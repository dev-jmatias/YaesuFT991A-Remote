# Radio Remote

Web remote control for Yaesu transceivers on a Raspberry Pi: frequency, mode, filters/DSP, meters, memory channels, PTT with server-side
safety, remote audio (WebRTC/Opus, Bluetooth headsets), multi-user with a control lease, admin tools. No cloud, no native app.
Works on phones, tablets and computers.

**Radios:** the **FT-991A** is complete and verified on a real radio. **FTDX10, FTDX101D/MP and FT-710** have profiles written from their
CAT manuals and tested only against a simulator (EXPERIMENTAL, see [docs/08-other-radios.md](docs/08-other-radios.md)). A built-in
simulator ("mock") lets you try everything without a radio.

## Install on a Raspberry Pi

**Easiest: the installer pack** (`radio-remote-installer`: program, Python libraries and manual in one folder):

```bash
scp -r radio-remote-installer <user>@radio.local:~          # from your PC
ssh <user>@radio.local
cd radio-remote-installer && sudo bash install-everything.sh
```

From the source code instead: `sudo bash install.sh --dry-run`, then `sudo bash install.sh --model ft991a`.
Then open `https://<pi>.local`, create the administrator, pick the radio in **Admin > Config**.
**Transmitting is disabled** until you set `allow_ptt = true` in the config file. Full guide: [docs/INSTALL.md](docs/INSTALL.md).

## Documentation

The manual is built to HTML and PDF (`python scripts/build_docs.py --pdf`), installed on the Pi and served at `/docs/` (Help button in the app).

| | |
|---|---|
| [INSTALL.md](docs/INSTALL.md) | Pi preparation, installer pack, first run, removal |
| [USER-GUIDE.md](docs/USER-GUIDE.md) | operating the radio: screens, control, audio, memories, transmitting |
| [operations.md](docs/operations.md) | configuration, services, logs, updating, backup/restore |
| [radio-connection.md](docs/radio-connection.md) | USB CAT and audio, radio menu settings |
| [troubleshooting.md](docs/troubleshooting.md) | symptom-by-symptom fixes |
| [tailscale.md](docs/tailscale.md), [06-security-remote.md](docs/06-security-remote.md) | remote access and security model |
| [08-other-radios.md](docs/08-other-radios.md), [01-capability-matrix.md](docs/01-capability-matrix.md) | other radios, verified capabilities |
| [02-architecture.md](docs/02-architecture.md), [04-audio.md](docs/04-audio.md), [05-ui.md](docs/05-ui.md) | design, audio, UI notes |
| [03-bench-checklist.md](docs/03-bench-checklist.md), [bench-results.md](docs/bench-results.md) | hardware tests and what was verified |

## Build the installer pack (on the development PC)

```powershell
pwsh scripts\build_pack.ps1          # tests + manual (HTML/PDF) + Python wheels for the Pi + dist\radio-remote-installer-<version>.zip
```

## Develop (Windows/Linux, no radio needed)

```powershell
python -m venv .venv
.\.venv\Scripts\pip install -r requirements.txt pytest pytest-asyncio markdown
$env:PYTHONPATH = "backend"
.\.venv\Scripts\python -m radio_remote --config config/radio-remote.toml   # mock radio; open http://localhost:8080
.\.venv\Scripts\python -m pytest -q                                         # tests never touch hardware
```

## Safety defaults

- `safety.allow_ptt = false` and it can only be enabled by editing the config file on the Pi.
- Hold-to-talk with a 250 ms heartbeat; the server un-keys on missed heartbeats, disconnect, lease change, time limit or shutdown.
- One client controls at a time; mic audio reaches the radio only while that client is keyed.
- Set the radio's own TX time-out as a last backstop.