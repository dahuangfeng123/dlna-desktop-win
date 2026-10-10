#Requires -Version 5.1
<#
    TCL 电视投屏助手  v1.0
    ------------------------------------------------------------
    启动后自动扫描局域网内的电视，列出设备供选择，然后：
      [1] Miracast 无线投屏  —— 投整个桌面（Windows 原生，画质实时）
      [2] DLNA 媒体推送      —— 把视频/图片/音乐直接推给电视播放
      [3] 播放控制           —— 暂停 / 停止 / 音量
      [4] 设备详情与诊断

    用法:
      .\tcl-cast.ps1            交互式菜单
      .\tcl-cast.ps1 -Scan      只扫描并列出设备（不做任何操作）
#>

param(
    [switch]$Scan,
    [int]$TimeoutMs = 4000,
    [int]$HttpPort  = 8899
)

$ErrorActionPreference = 'Continue'
$ProgressPreference    = 'SilentlyContinue'
$OutputEncoding        = [System.Text.Encoding]::UTF8

# ============================================================
#  输出辅助
# ============================================================
function Write-Head {
    param([string]$Text)
    Write-Host ''
    Write-Host ('  ' + ('-' * 58)) -ForegroundColor DarkGray
    Write-Host ('  ' + $Text) -ForegroundColor Cyan
    Write-Host ('  ' + ('-' * 58)) -ForegroundColor DarkGray
}
function Write-Ok    { param([string]$t) Write-Host ('  [OK]   ' + $t) -ForegroundColor Green }
function Write-Note  { param([string]$t) Write-Host ('  [注意] ' + $t) -ForegroundColor Yellow }
function Write-Bad   { param([string]$t) Write-Host ('  [错误] ' + $t) -ForegroundColor Red }
function Write-Line  { param([string]$t) Write-Host ('  ' + $t) -ForegroundColor Gray }
function Write-Dim   { param([string]$t) Write-Host ('  ' + $t) -ForegroundColor DarkGray }

# ============================================================
#  本机网络
# ============================================================
function Get-LocalIPv4 {
    try {
        $route = Get-NetRoute -DestinationPrefix '0.0.0.0/0' -ErrorAction Stop |
                 Sort-Object RouteMetric | Select-Object -First 1
        if ($route) {
            $addr = Get-NetIPAddress -InterfaceIndex $route.InterfaceIndex -AddressFamily IPv4 -ErrorAction Stop |
                    Select-Object -First 1
            if ($addr) { return $addr.IPAddress }
        }
    } catch { }
    $alt = Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue |
           Where-Object { $_.IPAddress -notlike '127.*' -and $_.IPAddress -notlike '169.254.*' } |
           Where-Object { $_.InterfaceAlias -notmatch 'vEthernet|VMware|Loopback|蓝牙|Bluetooth' } |
           Select-Object -First 1
    if ($alt) { return $alt.IPAddress }
    return $null
}

# ============================================================
#  SSDP / UPnP 设备发现
# ============================================================
function Invoke-SsdpSearch {
    param([int]$TimeoutMs = 4000)
    $found    = New-Object System.Collections.ArrayList
    $seenUrl  = @{}
    $udp      = New-Object System.Net.Sockets.UdpClient
    $udp.Client.ReceiveTimeout = 700
    $udp.EnableBroadcast       = $true

    $msg   = "M-SEARCH * HTTP/1.1`r`nHOST: 239.255.255.250:1900`r`nMAN: `"ssdp:discover`"`r`nMX: 2`r`nST: ssdp:all`r`n`r`n"
    $bytes = [System.Text.Encoding]::ASCII.GetBytes($msg)
    try {
        $mb = New-Object System.Net.IPEndPoint([System.Net.IPAddress]::Parse('239.255.255.250'), 1900)
        [void]$udp.Send($bytes, $bytes.Length, $mb)
    } catch { }

    $deadline = (Get-Date).AddMilliseconds($TimeoutMs)
    while ((Get-Date) -lt $deadline) {
        try {
            $remote = New-Object System.Net.IPEndPoint([System.Net.IPAddress]::Any, 0)
            $resp   = $udp.Receive([ref]$remote)
            $text   = [System.Text.Encoding]::ASCII.GetString($resp)
            $ip     = $remote.Address.ToString()
            $m      = [regex]::Match($text, '(?im)^LOCATION:\s*(\S+)')
            if ($m.Success) {
                $loc = $m.Groups[1].Value.Trim()
                $key = $loc.ToLower()
                if (-not $seenUrl.ContainsKey($key)) {
                    $seenUrl[$key] = $true
                    [void]$found.Add([pscustomobject]@{ IP = $ip; Location = $loc })
                }
            }
        } catch { }
    }
    $udp.Close()
    return $found
}

function Get-UpnpDevice {
    param([string]$Location, [string]$IP)
    try {
        $wc = New-Object System.Net.WebClient
        $wc.Proxy = $null
        $wc.Headers.Add('User-Agent', 'Windows/10.0 UPnP/1.0')
        $data    = $wc.DownloadData($Location)
        $xmlText = [System.Text.Encoding]::UTF8.GetString($data)
        [xml]$xml = $xmlText
        $dev = $xml.root.device
        if (-not $dev) { return $null }

        $baseUrl = [string]$xml.root.URLBase
        if ([string]::IsNullOrWhiteSpace($baseUrl)) {
            $u = [System.Uri]$Location
            $baseUrl = $u.Scheme + '://' + $u.Host + ':' + $u.Port
        }
        $baseUrl = $baseUrl.TrimEnd('/')

        $services = @()
        foreach ($s in $dev.serviceList.service) {
            $services += [pscustomobject]@{
                Type       = [string]$s.serviceType
                ControlUrl = $baseUrl + '/' + ([string]$s.controlURL).TrimStart('/')
            }
        }

        return [pscustomobject]@{
            IP           = $IP
            Location     = $Location
            FriendlyName = [string]$dev.friendlyName
            DeviceType   = [string]$dev.deviceType
            Manufacturer = [string]$dev.manufacturer
            ModelName    = [string]$dev.modelName
            UDN          = [string]$dev.UDN
            Services     = $services
        }
    } catch {
        return $null
    }
}

function Find-CastDevices {
    param([int]$TimeoutMs = 4000)
    $raw     = Invoke-SsdpSearch -TimeoutMs $TimeoutMs
    $devices = New-Object System.Collections.ArrayList
    $seenUdn = @{}

    foreach ($r in $raw) {
        $d = Get-UpnpDevice -Location $r.Location -IP $r.IP
        if ($null -eq $d) { continue }
        if ($d.DeviceType -notmatch 'MediaRenderer|MediaServer') { continue }

        $key = $d.UDN
        if ([string]::IsNullOrWhiteSpace($key)) { $key = $d.Location }
        $key = $key.ToLower()
        if ($seenUdn.ContainsKey($key)) { continue }
        $seenUdn[$key] = $true
        [void]$devices.Add($d)
    }
    return $devices
}

# ============================================================
#  UPnP SOAP 调用
# ============================================================
function Invoke-UpnpAction {
    param(
        [string]$ControlUrl,
        [string]$ServiceType,
        [string]$Action,
        [string]$InnerXml = ''
    )
    $envelope =
        '<?xml version="1.0" encoding="utf-8"?>' +
        '<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/" ' +
        's:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/"><s:Body>' +
        ('<u:' + $Action + ' xmlns:u="' + $ServiceType + '">' + $InnerXml + '</u:' + $Action + '>') +
        '</s:Body></s:Envelope>'

    $req = [System.Net.HttpWebRequest]::Create($ControlUrl)
    $req.Method        = 'POST'
    $req.Proxy         = $null
    $req.Timeout       = 8000
    $req.ContentType   = 'text/xml; charset="utf-8"'
    $req.Headers.Add('SOAPAction', '"' + $ServiceType + '#' + $Action + '"')

    $body = [System.Text.Encoding]::UTF8.GetBytes($envelope)
    $req.ContentLength = $body.Length
    $rs = $req.GetRequestStream()
    $rs.Write($body, 0, $body.Length)
    $rs.Close()

    $resp = $req.GetResponse()
    $sr   = New-Object System.IO.StreamReader($resp.GetResponseStream())
    $out  = $sr.ReadToEnd()
    $sr.Close()
    $resp.Close()
    return $out
}

function Get-ServiceOf {
    param($Device, [string]$Keyword)
    return ($Device.Services | Where-Object { $_.Type -match $Keyword } | Select-Object -First 1)
}

# ============================================================
#  本地媒体服务（供电视拉流）
# ============================================================
function Get-MimeType {
    param([string]$Path)
    switch ([System.IO.Path]::GetExtension($Path).ToLower()) {
        '.mp4'  { 'video/mp4' }
        '.m4v'  { 'video/mp4' }
        '.mkv'  { 'video/x-matroska' }
        '.avi'  { 'video/x-msvideo' }
        '.mov'  { 'video/quicktime' }
        '.ts'   { 'video/mp2t' }
        '.webm' { 'video/webm' }
        '.mp3'  { 'audio/mpeg' }
        '.flac' { 'audio/flac' }
        '.wav'  { 'audio/wav' }
        '.m4a'  { 'audio/mp4' }
        '.jpg'  { 'image/jpeg' }
        '.jpeg' { 'image/jpeg' }
        '.png'  { 'image/png' }
        '.gif'  { 'image/gif' }
        '.bmp'  { 'image/bmp' }
        default { 'application/octet-stream' }
    }
}

function Get-DidlClass {
    param([string]$Path)
    $ext = [System.IO.Path]::GetExtension($Path).ToLower()
    if (@('.mp4', '.m4v', '.mkv', '.avi', '.mov', '.ts', '.webm') -contains $ext) { return 'object.item.videoItem' }
    if (@('.mp3', '.flac', '.wav', '.m4a') -contains $ext)                        { return 'object.item.audioItem' }
    return 'object.item.imageItem'
}

function Test-PortFree {
    param([int]$Port)
    $used = Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue |
            Where-Object { $_.LocalPort -eq $Port }
    return (-not $used)
}

function Start-FileServer {
    param([string]$FilePath, [int]$Port)

    $mime = Get-MimeType -Path $FilePath
    $code = {
        param($Port, $FilePath, $MimeType)
        try {
            $all = [System.IO.File]::ReadAllBytes($FilePath)
        } catch { return }
        $total = $all.Length

        $listener = $null
        try {
            $listener = New-Object System.Net.Sockets.TcpListener([System.Net.IPAddress]::Any, $Port)
            $listener.Start()
        } catch { return }

        while ($true) {
            $client = $null
            try {
                $client = $listener.AcceptTcpClient()
                $client.ReceiveTimeout = 5000
                $ns  = $client.GetStream()
                $buf = New-Object byte[] 8192
                $n   = $ns.Read($buf, 0, $buf.Length)
                if ($n -le 0) { continue }
                $reqText = [System.Text.Encoding]::ASCII.GetString($buf, 0, $n)

                $start   = 0
                $end     = $total - 1
                $isRange = $false
                $rm = [regex]::Match($reqText, '(?i)Range:\s*bytes=(\d+)-(\d*)')
                if ($rm.Success) {
                    $start = [int]$rm.Groups[1].Value
                    if ($rm.Groups[2].Value -ne '') { $end = [int]$rm.Groups[2].Value }
                    if ($end -ge $total) { $end = $total - 1 }
                    if ($start -gt $end) { $start = 0; $end = $total - 1 }
                    $isRange = $true
                }
                $len = $end - $start + 1

                if ($isRange) { $head = 'HTTP/1.1 206 Partial Content' + "`r`n" }
                else          { $head = 'HTTP/1.1 200 OK' + "`r`n" }
                $head += 'Content-Type: ' + $MimeType + "`r`n"
                $head += 'Content-Length: ' + $len + "`r`n"
                $head += 'Accept-Ranges: bytes' + "`r`n"
                if ($isRange) {
                    $head += 'Content-Range: bytes ' + $start + '-' + $end + '/' + $total + "`r`n"
                }
                $head += "Connection: close`r`n`r`n"

                $hb = [System.Text.Encoding]::ASCII.GetBytes($head)
                $ns.Write($hb, 0, $hb.Length)
                $ns.Write($all, $start, $len)
                $ns.Flush()
                $ns.Close()
            } catch { }
            finally {
                if ($client) { $client.Close() }
            }
        }
    }
    return Start-Job -ScriptBlock $code -ArgumentList $Port, $FilePath, $mime
}

# ============================================================
#  动作：Miracast 无线投屏
# ============================================================
function Invoke-Miracast {
    param($Device)

    Write-Head 'Miracast 无线投屏'

    $miracastOK = $false
    try {
        $drv = netsh wlan show driver 2>&1 | Out-String
        if ($drv -match 'Wireless Display Supported:\s*Yes') { $miracastOK = $true }
    } catch { }

    if ($miracastOK) { Write-Ok '本机无线网卡支持 Miracast' }
    else             { Write-Note '本机驱动未报告 Miracast 支持，可能仍可尝试' }

    $wlan = ''
    try { $wlan = netsh wlan show interfaces 2>&1 | Out-String } catch { }
    $mm = [regex]::Match($wlan, '(?im)Channel\s*:\s*(\d+)')
    if ($mm.Success) {
        $ch = [int]$mm.Groups[1].Value
        if ($ch -ge 36) { Write-Ok ('当前 Wi-Fi 为 5 GHz 频段（信道 ' + $ch + '），投屏更稳定') }
        else            { Write-Note ('当前 Wi-Fi 为 2.4 GHz 频段（信道 ' + $ch + '），投屏可能卡顿，建议切 5 GHz') }
    }

    Write-Host ''
    Write-Host '  请先在电视上完成以下两步（关键）：' -ForegroundColor White
    Write-Line '1. 遥控器 -> 设置 -> 网络与连接 -> 无线投屏（有的机型叫「多屏互动」）'
    Write-Line '2. 把开关打开，并勾选「允许非认证设备连接」，然后停在该页面'
    Write-Dim  '   注：TCL 若不开启此项，Windows 电脑会搜不到电视'
    Write-Host ''
    Write-Line ('目标设备：' + $Device.FriendlyName + '  (' + $Device.IP + ')')
    Write-Host ''

    Write-Line '正在打开 Windows 连接面板...'
    try {
        Start-Process 'explorer.exe' -ArgumentList 'ms-actioncenter://Connect' -ErrorAction SilentlyContinue
    } catch { }
    Start-Sleep -Milliseconds 1200

    Write-Host ''
    Write-Host '  >> 若面板未弹出，请按 Win + K 手动调出' -ForegroundColor White
    Write-Host '  >> 在列表中点击你的电视名称即可开始投屏' -ForegroundColor White
    Write-Host '  >> 连上后按 Win + P 可切换「复制 / 扩展」模式' -ForegroundColor White
    Write-Host ''
    Write-Dim '提示：若搜不到电视，请退出向日葵等远程控制软件后重试'
    Write-Host ''
    [void](Read-Host '  按回车返回主菜单')
}

# ============================================================
#  动作：DLNA 推送
# ============================================================
function Select-MediaFile {
    try {
        Add-Type -AssemblyName System.Windows.Forms -ErrorAction Stop
        $dlg = New-Object System.Windows.Forms.OpenFileDialog
        $dlg.Title  = '选择要推送到电视的文件'
        $dlg.Filter = '媒体文件|*.mp4;*.mkv;*.avi;*.mov;*.ts;*.webm;*.mp3;*.flac;*.m4a;*.jpg;*.jpeg;*.png;*.gif;*.bmp|所有文件|*.*'
        if ($dlg.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK) { return $dlg.FileName }
        return $null
    } catch {
        $p = Read-Host '  请输入文件完整路径'
        if (Test-Path -LiteralPath $p) { return $p }
        return $null
    }
}

function Invoke-DlnaPush {
    param($Device, [int]$Port)

    Write-Head 'DLNA 媒体推送'

    $avt = Get-ServiceOf -Device $Device -Keyword 'AVTransport'
    if (-not $avt) {
        Write-Bad '该设备未提供 AVTransport 服务，无法推送媒体'
        Write-Dim '可改用 [1] Miracast 无线投屏'
        [void](Read-Host '  按回车返回')
        return
    }
    Write-Ok '电视支持 DLNA 接收（AVTransport 可用）'

    $localIp = Get-LocalIPv4
    if (-not $localIp) { Write-Bad '未取到本机局域网 IP'; [void](Read-Host '  按回车返回'); return }

    $file = Select-MediaFile
    if (-not $file) { Write-Note '未选择文件'; Start-Sleep -Milliseconds 800; return }
    Write-Line ('文件：' + $file)
    $sizeMB = [math]::Round((Get-Item -LiteralPath $file).Length / 1MB, 1)
    Write-Line ('大小：' + $sizeMB + ' MB')

    if (-not (Test-PortFree -Port $Port)) {
        Write-Note ('端口 ' + $Port + ' 被占用，改用 ' + ($Port + 1))
        $Port = $Port + 1
    }

    Write-Host ''
    Write-Line ('正在启动本地媒体服务  0.0.0.0:' + $Port)
    $job = Start-FileServer -FilePath $file -Port $Port
    Start-Sleep -Milliseconds 900

    if ($job.State -ne 'Running') {
        Write-Bad '媒体服务启动失败'
        Remove-Job $job -Force -ErrorAction SilentlyContinue
        [void](Read-Host '  按回车返回')
        return
    }
    Write-Ok '媒体服务已就绪'

    $title = [System.IO.Path]::GetFileName($file)
    $url   = 'http://' + $localIp + ':' + $Port + '/' + [System.Uri]::EscapeDataString($title)
    $mime  = Get-MimeType -Path $file
    $cls   = Get-DidlClass -Path $file

    $didl =
        '<DIDL-Lite xmlns="urn:schemas-upnp-org:metadata-1-0/DIDL-Lite/" ' +
        'xmlns:dc="http://purl.org/dc/elements/1.1/" ' +
        'xmlns:upnp="urn:schemas-upnp-org:metadata-1-0/upnp/">' +
        '<item id="0" parentID="-1" restricted="1">' +
        '<dc:title>' + $title + '</dc:title>' +
        '<upnp:class>' + $cls + '</upnp:class>' +
        '<res protocolInfo="http-get:*:' + $mime + ':*">' + $url + '</res>' +
        '</item></DIDL-Lite>'

    $metaEsc = [System.Security.SecurityElement]::Escape($didl)
    $svcType = 'urn:schemas-upnp-org:service:AVTransport:1'

    try {
        Write-Line '正在推送到电视...'
        [void](Invoke-UpnpAction -ControlUrl $avt.ControlUrl -ServiceType $svcType `
                -Action 'SetAVTransportURI' `
                -InnerXml ('<InstanceID>0</InstanceID><CurrentURI>' + [System.Security.SecurityElement]::Escape($url) +
                           '</CurrentURI><CurrentURIMetaData>' + $metaEsc + '</CurrentURIMetaData>'))
        [void](Invoke-UpnpAction -ControlUrl $avt.ControlUrl -ServiceType $svcType `
                -Action 'Play' -InnerXml '<InstanceID>0</InstanceID><Speed>1</Speed>')
        Write-Ok '推送成功，电视应已开始播放'
    } catch {
        Write-Bad ('推送失败：' + $_.Exception.Message)
        Write-Dim '（部分电视需要在待机或主界面才接受推送，请把电视停留在主界面重试）'
    }

    Write-Host ''
    Write-Dim '媒体服务运行中，播放期间请勿关闭本窗口。'
    Write-Host ''
    Write-Line '[P] 暂停/继续   [S] 停止播放   [Q] 结束并关闭服务'
    $key = (Read-Host '  请选择').Trim().ToUpper()

    try {
        if ($key -eq 'P') {
            [void](Invoke-UpnpAction -ControlUrl $avt.ControlUrl -ServiceType $svcType `
                    -Action 'Pause' -InnerXml '<InstanceID>0</InstanceID>')
            Write-Ok '已暂停'
        } elseif ($key -eq 'S') {
            [void](Invoke-UpnpAction -ControlUrl $avt.ControlUrl -ServiceType $svcType `
                    -Action 'Stop' -InnerXml '<InstanceID>0</InstanceID>')
            Write-Ok '已停止'
        }
    } catch { Write-Bad ('控制指令失败：' + $_.Exception.Message) }

    Stop-Job $job -ErrorAction SilentlyContinue
    Remove-Job $job -Force -ErrorAction SilentlyContinue
    Write-Dim '媒体服务已关闭'
    Start-Sleep -Milliseconds 700
}

# ============================================================
#  动作：播放控制
# ============================================================
function Invoke-PlaybackControl {
    param($Device)

    Write-Head '播放控制'
    $avt = Get-ServiceOf -Device $Device -Keyword 'AVTransport'
    $rc  = Get-ServiceOf -Device $Device -Keyword 'RenderingControl'
    if (-not $avt) { Write-Bad '设备不支持 AVTransport'; [void](Read-Host '  按回车返回'); return }

    $svcType = 'urn:schemas-upnp-org:service:AVTransport:1'
    $rcType  = 'urn:schemas-upnp-org:service:RenderingControl:1'

    while ($true) {
        Write-Host ''
        Write-Line '[1] 暂停  [2] 继续  [3] 停止  [4] 设置音量  [5] 静音  [0] 返回'
        $k = (Read-Host '  请选择').Trim()
        if ($k -eq '0') { return }
        try {
            switch ($k) {
                '1' { [void](Invoke-UpnpAction -ControlUrl $avt.ControlUrl -ServiceType $svcType -Action 'Pause' -InnerXml '<InstanceID>0</InstanceID>'); Write-Ok '已暂停' }
                '2' { [void](Invoke-UpnpAction -ControlUrl $avt.ControlUrl -ServiceType $svcType -Action 'Play'  -InnerXml '<InstanceID>0</InstanceID><Speed>1</Speed>'); Write-Ok '继续播放' }
                '3' { [void](Invoke-UpnpAction -ControlUrl $avt.ControlUrl -ServiceType $svcType -Action 'Stop'  -InnerXml '<InstanceID>0</InstanceID>'); Write-Ok '已停止' }
                '4' {
                    if ($rc) {
                        $vol = 0
                        $raw = Read-Host '  输入音量 0-100'
                        if ([int]::TryParse($raw, [ref]$vol) -and $vol -ge 0 -and $vol -le 100) {
                            [void](Invoke-UpnpAction -ControlUrl $rc.ControlUrl -ServiceType $rcType -Action 'SetVolume' -InnerXml ('<InstanceID>0</InstanceID><Channel>Master</Channel><DesiredVolume>' + $vol + '</DesiredVolume>'))
                            Write-Ok ('音量已设为 ' + $vol)
                        } else { Write-Note '音量数值无效' }
                    } else { Write-Note '该设备不支持音量控制' }
                }
                '5' {
                    if ($rc) {
                        [void](Invoke-UpnpAction -ControlUrl $rc.ControlUrl -ServiceType $rcType -Action 'SetMute' -InnerXml '<InstanceID>0</InstanceID><Channel>Master</Channel><DesiredMute>1</DesiredMute>')
                        Write-Ok '已发送静音指令'
                    } else { Write-Note '该设备不支持静音控制' }
                }
                default { Write-Note '无效选项' }
            }
        } catch { Write-Bad ('指令失败：' + $_.Exception.Message) }
    }
}

# ============================================================
#  动作：设备详情
# ============================================================
function Show-DeviceDetail {
    param($Device)

    Write-Head '设备详情'
    Write-Line ('名称     : ' + $Device.FriendlyName)
    Write-Line ('IP       : ' + $Device.IP)
    Write-Line ('设备类型 : ' + $Device.DeviceType)
    Write-Line ('厂商字段 : ' + $Device.Manufacturer + '   (红鲱鱼：Realtek 模板默认值)')
    Write-Line ('型号字段 : ' + $Device.ModelName + '   (同上，非真实品牌)')
    Write-Line ('UDN      : ' + $Device.UDN)
    Write-Host ''
    Write-Line '提供的服务：'
    foreach ($s in $Device.Services) {
        Write-Dim ('  - ' + $s.Type)
    }
    Write-Host ''
    $hasAvt = [bool](Get-ServiceOf -Device $Device -Keyword 'AVTransport')
    if ($hasAvt) { Write-Ok 'DLNA 推送：可用' } else { Write-Note 'DLNA 推送：不可用' }
    Write-Note 'Miracast：需电视端开启「无线投屏」后由 Windows 侧发现，无法远程探测'
    Write-Host ''
    [void](Read-Host '  按回车返回')
}

# ============================================================
#  主流程
# ============================================================
function Show-DevicePicker {
    param($Devices)
    Write-Host ''
    for ($i = 0; $i -lt $Devices.Count; $i++) {
        $d = $Devices[$i]
        $tag = ''
        if ($d.FriendlyName -match 'TCL') { $tag = '  <== TCL' }
        Write-Host ('  [' + ($i + 1) + '] ' + $d.FriendlyName + '   (' + $d.IP + ')' + $tag) -ForegroundColor White
        Write-Dim ('       ' + $d.DeviceType)
    }
    Write-Host '  [R] 重新扫描      [0] 退出' -ForegroundColor DarkGray
}

Clear-Host
Write-Host ''
Write-Host '  ============================================================' -ForegroundColor Cyan
Write-Host '              TCL 电视投屏助手   v1.0' -ForegroundColor Cyan
Write-Host '  ============================================================' -ForegroundColor Cyan
Write-Dim ('   本机 IP: ' + (Get-LocalIPv4))

while ($true) {
    Write-Host ''
    Write-Line ('正在扫描局域网投屏设备（约 ' + [math]::Round($TimeoutMs / 1000, 0) + ' 秒）...')
    $devices = @(Find-CastDevices -TimeoutMs $TimeoutMs)

    if ($Scan) {
        if ($devices.Count -eq 0) { Write-Note '未发现可用投屏设备' }
        else {
            Write-Host ''
            Write-Ok ('发现 ' + $devices.Count + ' 台设备：')
            foreach ($d in $devices) {
                Write-Line ('  - ' + $d.FriendlyName + ' | ' + $d.IP + ' | ' + $d.DeviceType)
            }
        }
        return
    }

    if ($devices.Count -eq 0) {
        Write-Host ''
        Write-Line '[R] 重新扫描      [0] 退出'
        $k = (Read-Host '  请选择').Trim().ToUpper()
        if ($k -eq '0') { return }
        Clear-Host
        continue
    }

    Show-DevicePicker -Devices $devices
    $sel = (Read-Host '  请选择设备').Trim()

    if ($sel -eq '0') { return }
    if ($sel.ToUpper() -eq 'R') { Clear-Host; continue }

    $idx = 0
    if (-not [int]::TryParse($sel, [ref]$idx)) { Write-Note '输入无效'; continue }
    if ($idx -lt 1 -or $idx -gt $devices.Count) { Write-Note '序号超出范围'; continue }

    $target = $devices[$idx - 1]

    while ($true) {
        Write-Head ('已选中：' + $target.FriendlyName + '  (' + $target.IP + ')')
        Write-Host '  [1] Miracast 无线投屏（投整个桌面，推荐）' -ForegroundColor White
        Write-Host '  [2] DLNA 推送媒体文件（视频/图片/音乐）' -ForegroundColor White
        Write-Host '  [3] 播放控制（暂停/停止/音量）' -ForegroundColor White
        Write-Host '  [4] 设备详情' -ForegroundColor White
        Write-Host '  [0] 返回设备列表' -ForegroundColor DarkGray
        $act = (Read-Host '  请选择操作').Trim()

        switch ($act) {
            '1' { Invoke-Miracast -Device $target }
            '2' { Invoke-DlnaPush -Device $target -Port $HttpPort }
            '3' { Invoke-PlaybackControl -Device $target }
            '4' { Show-DeviceDetail -Device $target }
            '0' { break }
            default { Write-Note '无效选项' }
        }
        if ($act -eq '0') { break }
    }
}
