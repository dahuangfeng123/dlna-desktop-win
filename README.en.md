# DLNA Screen Caster (Windows)

**English** | [简体中文](README.md)

Mirror your Windows desktop to a smart TV on the same LAN — **live**. No app on the TV, no Miracast, no HDMI cable, no streaming stick.

The program captures the screen and system audio with ffmpeg → encodes to H.264 → muxes into MPEG-TS → serves it over a built-in HTTP server → and uses UPnP `AVTransport` to make the TV pull the stream itself.

**Typical uses**: push slides / a browser / a video to the living-room TV; turn an old laptop into a temporary media centre; cast games (latency is roughly 1–2.5 s, so not for competitive titles).

![Screenshot](docs/screenshot.png)

---

## Contents

- [Features](#features)
- [Requirements](#requirements)
- [Quick start](#quick-start)
- [Quality presets](#quality-presets)
- [Playing on the TV: two routes](#playing-on-the-tv-two-routes)
- [How it works](#how-it-works)
- [Option reference](#option-reference)
- [Measured numbers and known limits](#measured-numbers-and-known-limits)
- [Troubleshooting](#troubleshooting)
- [Project layout](#project-layout)
- [Third-party components](#third-party-components)
- [License](#license)

---

## Features

| Capability | Detail |
|---|---|
| Zero install on TV | The TV needs nothing installed — any DLNA / UPnP renderer will do |
| Two forms, one binary | Double-click → GUI. Pass arguments → full command-line tool |
| Audio follows the TV | Mutes this PC by default so sound only comes out of the TV; restored on exit |
| Audio source | WASAPI loopback — **does not rely on "Stereo Mix"**, which is usually disabled |
| Hardware encoding | Prefers MediaFoundation (`h264_mf`) for near-zero CPU load; falls back to libx264 |
| No ffmpeg setup | If ffmpeg is missing it downloads one from GitHub (pulls only the single member it needs — about 67% less traffic) |
| Self-healing ports | If the preferred port is inside a Windows reserved range it picks the next free one and updates every URL — it never just fails to start |
| Proxy-proof discovery | When a TUN-mode proxy steals the route it falls back to the physical NIC; if multicast is blocked it sweeps the subnet |
| Low-latency route | A TV browser can open `/tv` and play raw MPEG-TS, with buffer depth controlled by the page instead of the TV vendor |
| Diagnosable | On-screen OSD shows fps / bitrate / latency; `--clock` serves a millisecond clock page for measuring end-to-end latency |

---

## Requirements

**Windows 10 / 11** (developed and measured on Win10 22H2, a 4-core / 8-thread laptop, 5 GHz Wi-Fi).

- **The TV and the PC must be on the same LAN.** 5 GHz Wi-Fi or Ethernet is recommended; 2.4 GHz usually cannot sustain 1080p bitrates.
- **ffmpeg**: nothing to prepare by hand. Lookup order is below; the last step downloads it automatically.
- **Python 3.8+**: only needed to run from source.

ffmpeg lookup order (defined once, in `screen-cast.py::find_ffmpeg()`):

```
FFMPEG_PATH env var
  → ffmpeg.exe next to the exe / script
  → %LOCALAPPDATA%\DlnaCast\bin\ffmpeg.exe
  → common install paths (D:\ffmpeg, ...)
  → PATH
  → download from GitHub into the exe's folder
```

To ignore a locally installed build and force a fresh download:

```bat
set DLNACAST_FORCE_DOWNLOAD=1
```

To change the download source (e.g. a mirror):

```bat
set DLNACAST_FFMPEG_URL=https://example.com/ffmpeg-release-essentials.zip
```

---

## Quick start

### Option 1: build a single exe

```bat
build_exe.bat
```

Produces `dist\DLNA投屏器.exe` (Chinese UI, ~12.7 MB). For the English build:

```bat
build_exe.bat --lang en
```

Produces `dist\DLNACast.exe` — same program, English UI by default.

**The exe does not bundle ffmpeg.** At runtime it walks the lookup order above and only downloads if nothing is found. That avoids writing 231 MB to disk on every launch, so cold start is noticeably faster.

- Package with a custom ffmpeg: `build_exe.bat --ffmpeg D:\ffmpeg\ffmpeg.exe`
- The old "one file contains everything" approach (exe grows to ~69 MB): `build_exe.bat --embed-ffmpeg`

Both builds are otherwise identical. Users of the English exe can still force Chinese at runtime with `--lang zh` or `DLNACAST_LANG=zh`.

### Option 2: run from source

```bat
pip install pyinstaller      :: only needed for packaging; running needs no third-party libraries
python screen-cast.py        :: interactive menu: scan for devices → pick quality → go
```

Common combinations:

```bat
python screen-cast.py --list                       :: list devices only, don't cast
python screen-cast.py --ip 192.168.1.100           :: specify the TV IP, skip discovery
python screen-cast.py --quality 1080hq --fps 30    :: recommended preset
python screen-cast.py --low-latency                :: smoothness-first preset
python screen-cast.py --no-push                    :: serve the stream only, for local testing
python screen-cast.py --unmute                     :: run this if the PC was left muted after a hard kill
```

Add `--lang en` to any of these for English output.

---

## Quality presets

The bitrate ceiling is not a guess — it is derived **backwards from the rate the TV can actually sustain** (see [Measured numbers](#measured-numbers-and-known-limits)).

| Preset | Target height | Target bitrate | Peak bitrate | Notes |
|---|---|---|---|---|
| `540` | 540 | 1200 | 1400 | Smooth (weak network / weak CPU) |
| `720` | 720 | 2500 | 2900 | Standard (default) |
| `1080` | 1080 | 4500 | 5200 | HD |
| **`1080hq`** | 1080 | 5500 | 6500 | **Recommended** — tuned to the TV's bandwidth ceiling |
| `1440` | 1440 | 14000 | 17000 | 2K; the screen itself must be ≥1440p |
| `2160` | 2160 | 28000 | 34000 | 4K; needs a 4K screen |

**Upscaling is disabled by default**: picking 2K/4K on a 1080p screen silently falls back to 1080p. Upscaling does not add detail — it only inflates the bitrate and saturates your LAN. Add `--allow-upscale` if you really want it.

**To improve picture quality, choose `1080hq`** (higher bitrate at the same resolution) rather than a higher-resolution preset.

---

## Playing on the TV: two routes

### Route A: DLNA push (default, most reliable)

The program treats the TV as a UPnP renderer and issues `SetAVTransportURI` + `Play` so the TV pulls the stream.

- **Pros**: best compatibility — nearly every smart TV supports it; the TV remote controls play/pause directly.
- **Cons**: 1–3 s end-to-end latency. **The bulk of that latency is the buffer depth of the TV's own player. Nothing on the PC side can change it.**

### Route B: TV browser plays `/tv` (low latency, experimental)

On start-up the program prints an address like `http://<PC-IP>:8899/tv`. Open it in the **TV's built-in browser**:

- Plays raw MPEG-TS in-page via [mpegts.js](https://github.com/xqq/mpegts.js), so **buffer depth is controlled by the page, not by the TV vendor**.
- Three buffer levels, switchable live: 0.5 s / **1.0 s (default)** / 2.0 s.
- A `/clock` millisecond clock page is served so you can photograph both screens in one frame and measure real end-to-end latency.

**Honest status**: this route depends on browser capabilities, and on some TV browsers it reports `NetworkError · Exception`. The reason is that this build of mpegts.js ships only four loaders, and all of them can fail against "an endless HTTP/1.0 response with no `Content-Length`":

| Loader | Depends on | Typical TV browser |
|---|---|---|
| `fetch-stream-loader` | `window.fetch` + `ReadableStream` | some models have no `fetch` |
| `xhr-moz-chunked-loader` | `moz-chunked-arraybuffer` (Firefox-only) | effectively unusable |
| `xhr-range-loader` | XHR + Range requests | ends up here, but **incompatible with an endless response** |
| `websocket-loader` | a `ws://` URL | works (needs server support) |

The repo therefore ships `tools/_tv_preview.py` (a preview server — see [Project layout](#project-layout)). Without touching the real server it reverse-proxies the page and `/live.ts` onto another port and additionally implements `/live-chunked` (HTTP/1.1 + chunked), `/probe` (a TV-browser capability probe page) and `/ws-live` (WebSocket streaming). `/ws-live` has been verified locally (3 s → 146 frames / 1.11 MB / 2.94 Mbps, first byte `0x47` — a valid TS sync byte).

**If the TV browser is unusable**, open the stream URL (the one ending in `/live.ts`) in **VLC for Android** and set *Network caching* to 300 ms.

---

## How it works

```
Screen ──ffmpeg/gdigrab│ddagrab──┐
                                 ├─→ H.264 (libx264 | h264_mf) ─→ MPEG-TS
Speakers ─WASAPI loopback────────┘         │
                                           ↓
                             built-in HTTP server (video/mp2t)
                                           │
                     ┌─────────────────────┴─────────────────────┐
                     ↓                                           ↓
       UPnP AVTransport push                       TV browser opens /tv
       (TV pulls; latency 1~3 s)                   (page controls buffer; lower latency)
```

Key design decisions:

1. **Audio does not depend on "Stereo Mix".** That device is usually disabled, and its `MMDevices` registry key is owned by `NT AUTHORITY\SYSTEM`. Instead we call WASAPI loopback directly via `ctypes` — zero dependencies, zero system changes. Audio goes into ffmpeg as 32-bit float PCM over a pipe.
2. **Audio hand-off does not switch devices or touch the registry.** It uses `IAudioEndpointVolume::SetMute`, which acts at the hardware output layer, i.e. **after** the loopback sampling point — so muting the PC still yields byte-identical loopback data. Cleanup is guarded three ways: `finally` → `atexit` → `SetConsoleCtrlHandler`.
3. **Cutting is only allowed on an IDR keyframe start packet.** Measured: only **0.22%** of packet positions are keyframe starts, so aligning to 188-byte boundaries alone lands inside a GOP 99.8% of the time — the client receives isolated P-frames and smears until the next keyframe. The code searches forward *and* backward; if it finds none it **declines to cut** rather than degrading to a blind truncation.
4. **Send pacing determines latency.** `--lead-ms` (default 800 ms) caps how far ahead of real time the server may run. It directly sets the TV's buffer depth and stops latency from growing without bound over a long session.
5. **Interleave interval tightened to 100 ms.** ffmpeg's default is 10 s, which lets audio and video packets drift far apart inside the TS; once backlog is dropped by byte count, the two are discarded over different time spans, and the mismatch accumulates into A/V desync.

---

## Option reference

Full list: `python screen-cast.py --help`.

| Option | Default | Meaning |
|---|---|---|
| `--quality` | `720` | Preset: 540/720/1080/1080hq/1440/2160 |
| `--fps` | `30` | Frame rate. gdigrab tops out at 30 fps for 1080p; go higher with `--capture ddagrab` |
| `--encoder` | `auto` | `auto` / `mf` (hardware) / `x264` (software) |
| `--capture` | `auto` | `auto` / `gdigrab` / `ddagrab` (Desktop Duplication, faster capture) |
| `--latency-ms` | `300` | Local stream buffer depth — the only source of PC-side latency |
| `--lead-ms` | `800` | Send pacing; sets the TV-side buffer depth |
| `--gop-ms` | `500` | Keyframe interval; smaller recovers faster after a drop, at a higher bitrate |
| `--buffer` | `384` | Stream buffer cap (KB) |
| `--bitrate` | `0` | Manual bitrate (kbps), overrides the preset default |
| `--audio` | `loopback` | `loopback` / `none` / `dshow:<device name>` |
| `--keep-local-audio` | off | Keep this PC's speakers unmuted (default is to mute them) |
| `--low-latency` | off | Smoothness-first preset: x264 + 250 ms buffer + 400 ms keyframes + 30 fps |
| `--lang` | `zh` | UI language: `zh` / `en` |
| `--clock` | off | Serve the millisecond clock page for latency measurement |
| `--no-osd` | off | Hide the on-screen info overlay (top-right) |
| `--push-retry` | `2` | Automatic re-push attempts when the TV has not connected |
| `--push-wait` | `18` | Seconds to wait for the TV to connect after each push |
| `--no-push` | off | Serve the stream only; don't push to the TV (local testing) |

---

## Measured numbers and known limits

Everything below was measured on this machine (a 2015 4-core / 8-thread laptop, Win10, 5 GHz Wi-Fi) — not inferred.

### Link throughput is a hard constraint

Playing a TV episode (1080p30) the encoder produced **7956 kbps** while the TV could only sustain **7039 kbps** — the 11.5% shortfall turned into continuous dropping → timeline jumps → **stutter**.

Every preset's peak bitrate therefore stays **under 6500 kbps** (about 18% headroom). When "dropped" keeps growing at >5% of the stream the log prints a `★ link throughput insufficient` warning. Fine-tuning knob: `--bitrate` (peak is automatically ×1.18).

### Frame-rate ceiling

- **gdigrab caps 1080p capture at 30 fps** (asking for 60 still yields 30); the bottleneck is BitBlt time.
- **ddagrab reaches 57.2 fps** in practice — the only way past 30.
- **`ddagrab` + `h264_mf` makes ffmpeg segfault (exit=139).** Three layers of protection exist in code: combo filtering / automatic switch to x264 / four-combination fallback.
- `h264_mf` emits Constrained Baseline, L4.0, no B-frames, and does not implement `-profile:v`; it produces about 7% fewer frames than x264.
- ddagrab's `output_idx=0` captures the primary monitor only; gdigrab captures the union of the virtual desktop.

### Encoders, measured

| Encoder | Result |
|---|---|
| Intel QSV | **Unavailable** — init fails with `-22 Invalid argument` |
| NVENC | **Unavailable** — Optimus restriction |
| `h264_mf` | **Preferred** — near-zero CPU (`-scenario display_remoting` + `-rate_control ld_vbr`) |
| `libx264 veryfast` | Fallback. Measured **3.09×** real-time headroom at 1080p30, about 3.2 cores |

> `ultrafast` was replaced by `veryfast`: both are among the weakest compression settings, and ultrafast costs roughly 10% more bitrate — which is the scarce resource here.

### End-to-end latency

| Stage | Time |
|---|---|
| Local capture + encode | on the order of 10 ms (hardware encoding) |
| Local stream buffer | controlled by `--latency-ms`, default 300 ms |
| **TV player buffer** | **1–3 s — outside this program's control** |

**Total measured latency is about 1–2.5 s.** The PC side (capture, encode, buffer) is down to roughly 50 ms + 0–2 ms; the rest is the TV vendor's player buffer. For lower latency, use [Route B](#route-b-tv-browser-plays-tv-low-latency-experimental).

### Other caveats

- **The TV shows a ~15 s start-up ad on power-on.** During it the player may "connect but not consume", so data piles up in the TV's memory and playback only starts once the ad ends — which looks like an absurdly large latency. The program defaults to `Stop` + re-push to force the TV to flush its buffer.
- **When the system is fully muted, WASAPI loopback emits no data at all** (measured: 0 frames in 3 seconds). The program synthesises silence at real-time rate; otherwise ffmpeg blocks forever waiting for audio input and takes the video down with it.
- The older approaches (Miracast / AirPlay / Chromecast) are all **unavailable** on the reference TV: no WFD advertisement; AirPlay is audio-only and `SETUP` returns `604 Internal Audio Server Error`; Chromecast's port 8009 is closed. DLNA is the only working route.

---

## Troubleshooting

**No sound when casting, or both the PC and TV play audio**
The default is to mute this PC. To have both, add `--keep-local-audio`. If a `taskkill /F` left the PC muted, run `python screen-cast.py --unmute` to restore.

**Picture stutters, start-stop**
First check the log for the "link throughput insufficient" warning. If present, lower the bitrate to about 15% below the measured delivery rate with something like `--bitrate 4000`. Failing that, drop to the `720` preset.

**Smearing / green blocks**
Usually the client received a non-keyframe from the middle of a GOP. Make sure no third-party program is competing for the same stream, then reconnect once so the program re-aligns to a keyframe.

**Latency keeps growing (not stuttering — a steady drift backwards)**
The TV player may be consuming its buffer at below 1.0×. Use `--clock`, then photograph both screens twice, 30 seconds apart, and compare.

**Start-up fails with `WinError 10013` ("An attempt was made to access a socket in a way forbidden by its access permissions")**
This is **not a port conflict** (that would be `10048`) — it means the port falls inside a Windows **reserved range**. Hyper-V / WSL2 / Docker reserve ports in dynamic blocks (on the reference machine the whole 8880–9079 range was taken) and **the range can change on every reboot** — so "it worked for ages" and "now it errors" are not contradictory. Firewall rules and running as administrator do not help.

The program **automatically walks forward to a free port** (candidates 9080 → 9081 → … → 18080 → …, and if all fail it lets the OS assign one) and rewrites the stream URL, the `/tv` page and the ffplay hint accordingly; the log prints `[port] preferred 8899 is reserved by the system, switched to 9080`. **Trust the address the program prints — do not hard-code 8899.**
To pin a port: `--port 9000`.

**With a proxy / VPN in TUN mode, the TV can't be discovered**
The proxy's TUN takes over the default route, and DLNA depends on SSDP multicast — multicast sent out of a virtual NIC is simply swallowed. Three built-in countermeasures:

1. Detect the virtual NIC and send from the physical NIC's local address instead;
2. If multicast goes unanswered, unicast `M-SEARCH` to every host in the local /24 (TUN does not affect intra-subnet unicast);
3. Just type the TV IP into the "TV IP" field — filling it in skips discovery entirely.

If all three fail, the definitive fix is to add the LAN to the proxy's **bypass list** (for Clash / Mihomo set `tun.route-exclude-address` to something like `192.168.1.0/24`; other clients have "Bypass LAN"), or temporarily switch to "System proxy / PAC" mode.

---

## Project layout

```
screen-cast.py          Core: discovery / capture+encode / HTTP stream server / UPnP push (also the CLI entry point)
cast_gui.py             GUI; also the PyInstaller entry point (no args → GUI, args → CLI)
i18n.py                 Chinese↔English translation table (keyed by the Chinese source string)
ffmpeg_fetch.py         Zero-config ffmpeg: lookup + ranged GitHub download + CRC check + capability probe
ffpayload.py            Append-style payload for the old "embedded ffmpeg" approach (only with --embed-ffmpeg)
build_exe.py            One-shot packaging (PyInstaller + verification); --lang en builds the English exe
screen-cast.spec        PyInstaller spec
build_exe.bat           Packaging entry point (pure ASCII, avoids GBK mojibake)
screen-cast.bat         Command-line launcher
dlna-probe.py           Standalone diagnostic: scan UPnP devices + log every HTTP request the TV makes
dlna-probe.bat          Launcher for the above
tcl-cast.ps1            Early v1.0 PowerShell prototype, superseded by screen-cast.py, kept for reference
cast.bat                Launcher for that early prototype

static/
  tv.html               TV browser player page (mpegts.js, three switchable buffer levels)
  probe.html            TV browser capability probe page (6 sections, ~18 s)
  mpegts.js             mpegts.js (self-hosted — the TV may not reach the internet, but it can reach the LAN)

assets/
  app.ico               Application icon
  version_info.txt      Windows version resource (generated at build time)

tools/                  Measurement and diagnostic scripts (not needed to run)
  _diag_fps.py          Capture frame-rate ceiling
  _diag_grab.py         Capture backend comparison
  _diag_burst.py        Burst throughput
  _diag_e2e.py          End-to-end link diagnosis
  _diag_sock.py         Raw-socket receive-pacing measurement
  _fetch_probe.py       Download-source resolution / zip central-directory lookup
  _fetch_small.py       Verify inflation + CRC using a small member
  _fetch_bench.py       Single vs. multi-connection throughput
  _fetch_e2e.py         End-to-end download (abort N → finish, verifies resume)
  _fetch_test.py        Tests both lookup and download paths
  _startup_time.py      A/B measure of "double-click → window appears"
  _gui_smoke.py         GUI smoke test
  _gui_flow.py          Full GUI flow test
  _gui_dl.py            GUI download-wiring test (with a stub)
  _tv_probe.py          Sample /live.ts directly, verify TS structure
  _tv_preview.py        TV preview server (static files from disk + reverse proxy + WebSocket streaming)
  _ws_live_check.py     WebSocket client: handshake → read frames → check 0x47
  _winshot.py           Screenshot by window handle (avoids occlusion)
  _exe_test.py          Run the real exe and screenshot it
  make_icon.py          Draws the application icon in code

docs/
  screenshot.png        UI screenshot
```

### Two traps when writing diagnostics

1. **Do not measure arrival pacing with `http.client` / `urllib.response.read(65536)`** — it **blocks until it has all 65536 bytes**, disguising a smooth stream as periodic stalls. Use a raw socket with small `recv()` calls.
2. **Do not treat the fps measured with `--no-push` as the TV-side fps** — the TV has its own decode and display chain on top.

---

## Third-party components

| Component | Purpose | License |
|---|---|---|
| [ffmpeg](https://ffmpeg.org/) | Capture, encode, mux | LGPL / GPL (depends on the build; not bundled — fetched at runtime) |
| [mpegts.js](https://github.com/xqq/mpegts.js) | MPEG-TS playback in the TV browser | Apache-2.0 |

---

## License

[MIT](LICENSE)
