# Architecture

## As built (version 1.1)

The design below was written before the program existed. This is how it turned out; where the two differ, **this section is right**.

* **One process.** The audio service runs inside the core process (the design proposed a separate `radio-audio` process). It is a self-contained
  class with a narrow interface, and the measured CPU cost on a Pi 4 (see [audio](04-audio.md)) made a second process unnecessary.
* **Native Yaesu CAT only.** Every radio is driven through a Yaesu CAT protocol object (`Ft991aProto` for the FT-991A, `HfProto` for the FTDX10,
  FTDX101D/MP and FT-710) and one `YaesuCatDriver`. Hamlib is not used. A Hamlib "NET rigctl" *server* (so a logbook can follow the radio) is planned.
* **Frontend: plain ES modules, no framework and no build step** (the design proposed Preact + htm). The page is a PWA with a service worker.
* **Python 3.11+, aiohttp, pyserial, aiortc, numpy, sqlite3 (accounts and audit), scrypt for passwords, TOML for the configuration.**
* **Capability files** (`backend/radio_remote/radio/capabilities/*.toml`) describe each radio; the page's controls are generated from them.
  [What each radio supports](01-capability-matrix.md) is generated from the same files.
* **Safety:** `TxGuard` (the only thing that can key the transmitter: permission, one owner, heartbeat, time limit, un-key on disconnect or lease
  change) and `ControlLease` (who controls the radio). The transmit permission is off by default and is switched on by an administrator.
* **Added since the design:** roles and trusted users, the audit log, remote audio with a server-side gate, Bluetooth device choice, memory channels, status
  lights, Lock PTT, the SWR warning, settings backup and restore, the update notice and `self_update.sh`, `tailscale_setup.sh`, the installer pack,
  and the ready-made Raspberry Pi image with its Imager repository.
* **Audio over a WebSocket** (`/ws/audio`, binary Opus packets both ways, WebCodecs in the browser) is built as the fallback for networks that block WebRTC/UDP.
* **Not built:** a separate audio process, Hamlib as a backend.

---

# Original design (kept for the reasoning behind the decisions)

Written as a proposal; the **[DECISION]** marks were the points confirmed before building.

## 1. System overview

```
 Browser (tablet / phone / desktop)
   │  HTTPS (443)           WebSocket (state, control)       WebRTC/Opus (audio, UDP)
   ▼
 Caddy  (TLS, optional)  ──► 127.0.0.1:8080  radio-core  (single owner of CAT)
                                  │  ├─ auth / sessions / REST
                                  │  ├─ WebSocket hub  ◄── fan-out of RadioState
                                  │  ├─ Radio State Engine (authoritative copy of the rig)
                                  │  ├─ TX Safety Controller (PTT lease, watchdog, timeout)
                                  │  └─ Radio Abstraction: RadioDriver ─► CatTransport
                                  │                                          ├─ serial (pyserial)
                                  │                                          ├─ mock (no hardware)
                                  │                                          └─ hamlib (optional)
                                  │  unix socket (control + tx-permit)
                                  ▼
                            radio-audio  (separate process)
                              ALSA capture/playback ◄─► aiortc WebRTC (Opus)
                                  ▲
                                  └── USB audio codec of the radio
 USB: CAT serial ──► radio       USB: audio codec ──► radio
```

Principles: the Pi owns the CAT port (no browser ever talks to the radio); the radio is the source of truth; the browser is a view plus an intent sender; safety logic lives in the backend; audio is isolated from CAT so audio problems cannot stall PTT release.

## 2. Backend technology **[DECISION]**

**Python 3.11+ (asyncio)**, packaged in a venv.

| Why | Detail |
|---|---|
| Fits the Pi 3B+ | `asyncio` is enough for ~5–15 CAT transactions/s and a few WebSocket clients. CAT is I/O-bound. |
| Library fit | `pyserial` (+ async wrapper), `aiohttp` (HTTP+WS in one small dependency), `aiortc` (WebRTC/Opus in Python), `numpy`, stdlib `sqlite3`, `hashlib.scrypt`, `tomllib`. Few deps = fewer install failures on Pi. |
| Testability | Easy mock transport; `pytest` + `pytest-asyncio`; develops and tests on Windows (Python 3.14 already present here) with no Pi. |
| Hamlib | `rigctl`/`rigctld` as a subprocess, or Hamlib's own Python binding; either is optional. |

Rejected: Node (not installed here, no better audio story), Go/Rust (excellent performance, but a bigger build/learning surface; revisit only if the aiortc spike fails — see §5.3), Docker (adds a layer to USB device passthrough/udev/ALSA and ~100 MB; systemd is simpler on a dedicated Pi. A Dockerfile can be added later for dev).

Two processes, one repo:
- `radio-core` — HTTP, WebSocket, CAT, state, auth, TX safety.
- `radio-audio` — ALSA ↔ WebRTC only. Talks to core over a Unix domain socket. It **cannot** key the radio; it only streams mic audio to the radio's codec while core says "TX permitted".

## 3. Frontend technology **[DECISION]**

**No-build ES modules + Preact + htm** (vendored, ~12 KB gzip), plain CSS (custom properties, container queries), SVG/Canvas for dial and meters, a service worker + web manifest for PWA.

| Why | Detail |
|---|---|
| No toolchain on the Pi | Node is not needed to run or install. Repo contains the final static files. |
| Light | Single-digit-KB app code; no React/Angular. |
| Maintainable | Components in `frontend/src/components/*.js`, typed via JSDoc + `tsc --checkJs` optionally. |
| Touch/PWA | Pointer Events (one code path for mouse/touch/pen), `wheel` for the dial, `screen.wakeLock`, installable PWA. |

Trade-off: no TypeScript compile step. If you prefer TypeScript + Vite we can adopt it; it needs Node on the developer machine only, and output is committed/released as static `dist/`.

## 4. CAT / Hamlib architecture **[DECISION]**

**Hybrid, native-first.** One Python `CatTransport` per radio; drivers speak Yaesu CAT directly. Hamlib is an optional second backend.

```
RadioDriver (abstract)                  capabilities: machine-readable, per model
 ├─ FT991ADriver     (native CAT, verified from the official manual)
 ├─ FTDX101Driver    (declared, disabled until manual verified)
 ├─ FTDX10Driver     (declared, disabled)
 ├─ FT710Driver      (declared, disabled)
 └─ HamlibDriver     (generic fallback; capability set derived from `--dump-caps`)
CatTransport
 ├─ SerialTransport  (pyserial; exclusive lock file; reconnect with backoff)
 ├─ MockTransport    (simulated radio; default for development; refuses PTT-to-hardware)
 └─ ReplayTransport  (tests: recorded CAT transcripts)
```

Core CAT mechanics (all model-agnostic, in `radio/cat/`):

- **Command pipeline**: priority queue (PTT-off > PTT-on > user sets > fast meters > periodic polls). One command in flight; `;`-framed parser tolerant of interleaved auto-information frames. Per-command timeout; limited retries; `?;` handling.
- **State sync**: on connect → `ID;` check → `AI1;` → full read-out → steady state. AI push updates keep latency low and detect front-panel changes. If AI isn't reliable, driver falls back to `IF;` poll (~2/s).
- **Polling tiers** (driver-tunable): TX meters 8–10 Hz while keyed; S-meter 5–8 Hz while receiving; `IF;`/`TX;` 2 Hz as AI backstop; slow settings round-robin one command every ~250 ms; paused for the user's own command bursts.
  Throughput check: at 38400 baud an `SM0;`→`SM0xxx;` pair is ~11 bytes ≈ 3 ms on the wire; the radio's own latency dominates, so a budget of ~15 transactions/s is realistic and leaves headroom.
- **Single owner**: lock file + `fcntl.flock` (Linux) / named mutex (Windows dev); udev symlinks `/dev/yaesu-cat` by USB serial/interface number. Port auto-detection: enumerate `pyserial.tools.list_ports` by Yaesu/Silicon Labs VID:PID, probe candidate ports with `ID;` (read-only, never a set), pick the one that answers with a known ID. Two-port radios (the 991A) are disambiguated by this probe, not by order.
- **Reconnect**: any read timeout burst/serial exception → state `connected=false` (UI banner), backoff 1→10 s, re-detect port, replay init sequence, then *read* the radio and overwrite the state (physical radio wins). Pending set-commands queued during disconnect are **dropped, never replayed** (could surprise the operator).
- **Radio authoritative**: after each user "set", the optimistic UI value is replaced by the radio's answer; rejected/clamped values show up automatically.

## 5. Audio architecture **[DECISION]**

### 5.1 Transport

WebRTC with **Opus** (mono, 20 ms frames, 16–24 kHz wideband-speech profile, ~24–32 kbps, in-band FEC enabled). Chosen because it is the only browser-native low-latency real-time audio stack that works on iOS Safari, Android Chrome and desktop without plugins, includes jitter buffer/loss concealment, and the same peer connection carries the microphone upstream.

Library: **aiortc** (Python, ctypes libopus). *Risk to verify, not assume:* aiortc's Opus path is hard-wired to 48 kHz and its CPU cost on a Pi 3B+ with an ARM64 build has not been measured by me. Plan a **Phase 6 spike**: 2-way stream, measure CPU/latency/jitter on a real Pi 3B+. **Fallback A**: GStreamer `webrtcbin` (C, lower CPU, heavier install). **Fallback B**: WebSocket carrying Opus frames decoded in the browser with WebCodecs (not available on older iOS) — used also when UDP is blocked.

### 5.2 Local capture/playback

ALSA only (Raspberry Pi OS **Lite** 64-bit; no PulseAudio/PipeWire). Devices referenced by stable names (`hw:CARD=CODEC,DEV=0`), never numeric index. `radio-audio` opens the radio's codec exclusively: RX = capture, TX = playback.
Detection layer: `/proc/asound/cards` + `aplay -L` → candidate list, "looks like Yaesu codec" heuristic, user override in the admin UI, hot-plug via udev/inotify with reopen backoff.

### 5.3 Levels and echo/AGC

- RX path: capture → gain → (optional) high-pass → Opus → browser. Gain/volume: server-side `rx_gain_db`, browser `<audio>` volume.
- TX path: browser mic (`getUserMedia` constraints for `echoCancellation`/`noiseSuppression`/`autoGainControl` are *user settings*, default AGC off, echo-cancel on for speaker use) → Opus → server decode → `tx_gain_db` → limiter → ALSA playback to the radio. **Limiter on the TX path is mandatory** to prevent over-deviation/ALC pumping.
- Level meters: RMS/peak computed in `radio-audio`, sent to core at ~10 Hz and fanned out through the WS; the browser also meters its own mic locally.

### 5.4 **Important constraint: HTTPS is required for the microphone**

Browsers only expose `getUserMedia` on a *secure context* (HTTPS, or `localhost`). A plain `http://raspberrypi.local/` page cannot capture the microphone on any modern browser, iOS included. So:

- LAN: Caddy with its **internal CA** (user installs the root cert once per device) or Tailscale-issued certs on the `*.ts.net` name.
- Remote: **Tailscale** (`tailscale serve`/`tailscale cert`) gives real publicly-trusted certificates for the Pi's tailnet hostname — the recommended default for tablets/phones.
- RX-only listening works over HTTP; TX audio does not.

This is why the installer sets up Caddy by default **[DECISION]**: the app listens on `127.0.0.1:8080` only; Caddy exposes 443/80. Without HTTPS, the UI disables the microphone button and says why.

### 5.5 WebRTC and Tailscale

No STUN/TURN is needed on LAN or tailnet: ICE host candidates on the LAN/`tailscale0` interface suffice. If Tailscale falls back to a DERP relay (TCP/HTTPS), WebRTC UDP latency increases; the UI shows the ICE path and the user can force the WebSocket-Opus transport.

## 6. Real-time protocol

Single WebSocket `/ws` (JSON; msgpack optional later).

- Server → client: `state` (full snapshot on connect), `patch` (changed fields only, coalesced at ≤20 Hz), `meters` (high-rate, separate small message), `event` (errors, warnings), `lease` (who controls), `audio` (stream status).
- Client → server: `cmd` messages `{id, type, args}` with ack `{id, ok|error}`; `hello` with a short-lived ticket; `ptt` heartbeat.
- REST (`/api/…`): config, users, diagnostics, logs, auth. Never used for high-rate data.
- WS auth: session cookie + `Origin` allowlist check + one-time ticket issued by REST after login (prevents cross-site WS hijack).

## 7. Radio State Engine

A single server-side `RadioState` (dataclass → dict). Sources: driver events (AI push, poll answers). Sinks: WS hub. Rules:

- **Writes go to the radio first**; state changes only when the radio confirms (or a short optimistic window for sliders, reconciled after ≤500 ms).
- Fields carry `{value, ts, source}`; per-field "unsupported" simply isn't in the snapshot.
- State persistence on disk: only last-known config, never TX state.

## 8. Capability model

Machine-readable `radio/capabilities/<model>.toml`, e.g.

```toml
[model]  id="ft991a" name="Yaesu FT-991A" cat_id="0670"
[verified] source="FT-991A_CAT_OM_ENG_1711-D" bench="pending"
[features]
vhf=true uhf=true antenna_switching=false  # no CAT opcode (verified absent)
swr_meter=true alc_meter=true comp_meter=true
power_off_cat=true  power_on_cat="unverified"
```

Each field is `true | false | "unverified"`. The UI builds itself from the capability snapshot sent at connect; `"unverified"` is treated as `false` for display. Ranges (`rf_gain 0..255`, `power 5..100`) come from the same file. A test asserts every UI control maps to a capability key.

## 9. TX safety (server-side, authoritative)

```
PTT request ──► [auth] ──► [ALLOW_PTT?] ──► [lease holder?] ──► [rate limit] ──► driver.tx(True)
                                   (default false)      (one owner)
      TxGuard watchdog (core, independent asyncio task + monotonic clock):
        • heartbeat: owner's WS must send a ptt-hold ping ≥ every 300 ms; miss 1.0 s ⇒ TX0
        • WS close / error / lease loss / auth expiry ⇒ TX0
        • max TX duration (default 120 s, config) ⇒ TX0, lock PTT until released
        • CAT disconnect while keyed ⇒ attempt TX0 repeatedly; radio TOT (`EX036`) is the final backstop
        • SIGTERM / shutdown ⇒ TX0 before exit
        • TX0 retried until the radio acknowledges (`TX;` → 0)
      Radio-keyed externally (`TX;`=2): shown as TX; software never claims control.
```

PTT is momentary (hold-to-talk) by default; latching requires an explicit setting + confirm. Test suite: transports are mocks; a `RealSerialTransport` refuses to open when `RADIO_REMOTE_TESTING=1` and PTT code paths require `ALLOW_PTT=true` in the *config file*, not the environment, so tests cannot enable it. Tuner start (`AC`) and any other TX-causing command go through the same guard.

## 10. Multi-client control

- Roles: **admin**, **operator**, **viewer**.
- Everyone with a session sees state/meters/audio-status.
- **Control lease**: at most one operator holds it; `lease.request` → current holder gets a prompt (accept/deny/timeout 10 s); admin can force-release; lease expires with heartbeat loss (5 s).
- Setting commands, PTT, and TX-audio require the lease. PTT additionally requires the holder's own *active* connection (not just the lease).
- RX audio can be listened to by multiple viewers (separate peer connections; Pi 3B+ limit configurable, default 3).

## 11. Security

| Concern | Measure |
|---|---|
| Authentication | Local accounts, `hashlib.scrypt` (N=2¹⁴/r=8/p=1 tuned for Pi 3B+, ~100–250 ms), constant-time compare, forced password change on first login (first-run wizard creates admin; no default password). |
| Sessions | Random 256-bit server-side session id in `HttpOnly; Secure; SameSite=Strict` cookie; idle + absolute expiry; revoke on logout/password change. |
| CSRF | `SameSite=Strict` plus a per-session CSRF token header on all non-GET REST; WS requires Origin allowlist and ticket. |
| Rate limiting | Login attempts per IP+user with exponential backoff; command rate limits per connection; PTT edge limit. |
| Input validation | Command catalogue: browser sends *intents* (`set_frequency{hz}`) validated by schema/range per capability; **no raw CAT, no shell, no paths** from the browser. Serial/Hamlib args come only from config validated against enumerated devices. |
| Subprocesses | Fixed argv lists, never `shell=True`; config values never concatenated into commands. |
| Network | Bind `127.0.0.1`; Caddy provides TLS. Install script does not touch the firewall unless asked, but docs show `ufw` allowing LAN + `tailscale0` only. No port forwarding; Hamlib rigctld (if enabled) bound to localhost. |
| Tailscale | App-level auth stays on regardless; optional "trust Tailscale identity" header mode is **off** by default (documented, not enabled). |
| Secrets/logs | Passwords never logged; config stored `0600` owned by service user; log scrubber for `Cookie`/`Authorization`. |
| Audit | auth events, PTT on/off with user/IP, config changes, power-off. |

## 12. Configuration & storage

- `/etc/radio-remote/config.toml` (admin-edited through the UI; atomic write + backup `.bak`).
- `/var/lib/radio-remote/radio-remote.db` (SQLite: users, sessions, audit).
- Logs: journald via systemd + rotating `/var/log/radio-remote/*.log` at configurable level.
- Defaults are safe: `allow_ptt=false`, `radio.model=mock`, mock transport.

## 13. Deployment

**systemd** (no Docker): `radio-remote-core.service`, `radio-remote-audio.service`, `caddy` (stock package), udev rule for stable device names and `plugdev/dialout/audio` groups, `Restart=always`, `WatchdogSec` using `sd_notify` from core (health), `TimeoutStopSec` large enough for TX0. `install.sh` is idempotent; `update.sh` does `git pull`, venv update, migrate, restart. Debian packages: `python3-venv libopus0 libasound2 libportaudio2 hamlib-utils caddy` (Hamlib from Debian may be older than 4.7 — the installer reports the version and can build 4.7+ from source on request).

## 14. Raspberry Pi 3B+ budget

| Item | Expected | Mitigation |
|---|---|---|
| CAT + state + WS | low CPU | single poll loop, patch coalescing |
| Opus encode+decode mono wideband | single-digit–low-teens % of a core (**REQUIRES MEASUREMENT**) | 16 kHz, 20 ms, complexity ≤5; audio in its own process |
| WebRTC (SRTP/DTLS) in Python | **REQUIRES MEASUREMENT** | spike in Phase 6; fallbacks in §5.1 |
| RAM | tens of MB (Python core ~60–80 MB, audio ~80 MB) **estimated** | few deps; no ML/numpy-heavy ops |
| USB | 2.0 shared with ethernet on 3B+ | not a problem at CAT/48 kHz audio bitrates |
| Network | Ethernet ≤ 300 Mbit/s effective on 3B+; Wi-Fi is fine for audio ~50 kbps | prefer wired |

## 15. Logging & diagnostics

Structured logs (JSON optional), per-subsystem loggers (`cat`, `audio`, `auth`, `ptt`, `web`, `config`). Diagnostics page: Pi model, uptime, CPU, RAM, temperature (`/sys/class/thermal`), version, radio model/ID, port, CAT counters (tx/rx/timeouts/errors), Hamlib version, audio devices & stream state, clients, lease holder, last 200 log lines (scrubbed).

## 16. Testing strategy

- Unit: CAT frame parser/generator, frequency/mode/width tables (from the manual), capability loader, state merge, lease logic, TX guard (with fake clock), auth/session/CSRF, WS protocol.
- Integration: MockRadio over a pty-pair "serial port" so the real `SerialTransport` code is exercised without hardware.
- Safety tests assert **no real transport can ever be keyed in the test environment**.
- Replay tests from recorded transcripts once real hardware traffic is captured.

## 17. Proposed project structure

```
radio-remote/
  README.md
  install.sh  update.sh
  pyproject.toml  requirements.txt
  backend/radio_remote/
    app.py  config/  auth/  api/  ws/  state/  safety/  services/
    radio/
      base.py  registry.py
      cat/ (frame.py transport_serial.py transport_mock.py hamlib_backend.py)
      drivers/ ft991a.py ftdx101.py ftdx10.py ft710.py hamlib_generic.py
      capabilities/ ft991a.toml ftdx101d.toml …
    audio/ (devices.py worker.py webrtc.py levels.py)
  frontend/ (index.html manifest.webmanifest sw.js  src/ components/ radio/ meters/ controls/ pages/ styles/ vendor/)
  config/  radio-remote.example.toml  udev/  systemd/  Caddyfile.example
  scripts/  detect-radio.py  probe-hamlib.sh
  docs/  01-capability-matrix.md 02-architecture.md installation.md …
  tests/
```

## 18. Roadmap (each phase ends runnable)

| Phase | Deliverable | Gate |
|---|---|---|
| 3 | Skeleton: core + mock radio + config + auth + WS + basic dashboard | UI shows mock radio, login works, tests green |
| 4 | CAT layer (frame parser, serial transport, FT-991A frequency/mode/band/meters, reconnect, AI sync) tested on mock + pty | Bench test #1 with real 991A, **PTT disabled** |
| 5 | FT-991A DSP/filters/antenna(n/a)/power/VHF-UHF/C4FM; bench tests of power-ON, AGC map, meters calibration | Needs real radio |
| 6 | Audio spike then full RX/TX audio | Pi 3B+ measurements |
| 7 | Production UI | Tablet first |
| 8 | Security hardening, PTT lease/guard tests, Caddy+Tailscale docs | Safety review |
| 9 | Installer + docs | Clean-Pi install |
| later | FTDX101/10/FT-710 drivers once manuals provided | per-model verification |

## 19. What I need from you

1. **Approve or change** the decisions: Python backend, no-build Preact frontend, native-CAT-first with optional Hamlib, aiortc (with spike), systemd + Caddy HTTPS.
2. **Provide** the FTDX101D/MP, FTDX10 and FT-710 CAT manuals (and the FT-991A operating manual) so those drivers can be verified. Until then I'll build FT-991A + mock only.
3. Confirm you have an FT-991A + Pi available for the bench tests in Phases 4–6 (power-ON via `PS1;`, audio-route menus, meter calibration). I will give a checklist; PTT stays disabled until you say otherwise.
