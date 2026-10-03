# Operations: configuration, services, updating, backup

## Where things live (after `install.sh`)

| Path | What | Owner |
|---|---|---|
| `/opt/radio-remote/releases/<timestamp>/` | program releases (read-only to the service) | root |
| `/opt/radio-remote/current` | symlink to the active release | root |
| `/opt/radio-remote/venv/` | Python environment | root |
| `/etc/radio-remote/config.toml` | settings | radio-remote, mode 0600 |
| `/var/lib/radio-remote/radio-remote.db` | users, sessions, audit log | radio-remote |
| `/var/backups/radio-remote/` | backups (hold password hashes: mode 0600) | root |
| `/etc/systemd/system/radio-remote*.service/.timer` | units | root |
| `/etc/udev/rules.d/99-radio-remote.rules` | ModemManager exclusion for the radio's USB bridge | root |

## Configuration

Edit in the web UI (**Admin > Config**) or in the file. The file is the only place for the settings the UI refuses to
change on purpose: `safety.allow_ptt`, `server.*`, `storage.*`. Changes need `sudo systemctl restart radio-remote`
(the log level applies live). The UI's "Restart service now" button does the same.

| Key | Meaning | Default |
|---|---|---|
| `server.host` / `port` | where the app listens. **Keep `127.0.0.1`**; Caddy/Tailscale face the network | `127.0.0.1` / 8080 |
| `server.allowed_origins` | extra browser origins allowed for WebSockets (needed only if a proxy rewrites `Host`) | `[]` |
| `radio.model` | `mock` or `ft991a` | `mock` |
| `radio.serial_port` / `baud` | CAT port (`auto` probes) and speed | `auto` / 38400 |
| `radio.hamlib_model` | reserved (Hamlib backend is not implemented) | 0 |
| `safety.allow_ptt` | **file only.** Must be `true` before a real radio can be keyed | `false` |
| `safety.tx_timeout_s` | server-enforced maximum continuous transmission | 120 |
| `safety.ptt_heartbeat_timeout_s` | un-key if the PTT holder is silent this long | 1.0 |
| `safety.control_request_timeout_s` | an unanswered request for control passes after this long (trusted users skip the wait) | 10 |
| `audio.enabled` / `backend` | remote audio on/off; `auto` = alsa for a real radio, test tone for the mock | true / auto |
| `audio.input_device` / `output_device` | ALSA devices (`plughw:CARD=...,DEV=0`) | auto-detect |
| `audio.rx_gain_db` / `tx_gain_db` | -30..30 dB; a limiter always follows TX gain | 0 |
| `audio.opus_bitrate` / `max_peers` | codec rate; simultaneous listeners | 32000 / 3 |
| `ui.tuning_steps_hz` | step choices in the UI | 10..10000 |
| `logging.level` | DEBUG/INFO/WARNING/ERROR | INFO |
| `storage.data_dir` | database location | `/var/lib/radio-remote` |

## Services

```bash
sudo systemctl status radio-remote           # is it running?
sudo systemctl restart radio-remote          # restart (un-keys the transmitter first)
sudo systemctl stop radio-remote             # stop
sudo systemctl disable --now radio-remote    # stop and do not start at boot
journalctl -u radio-remote -f                # follow the log
journalctl -u radio-remote --since "1 hour ago" --no-pager
systemctl list-timers radio-remote-backup.timer
sudo systemctl status caddy                  # the HTTPS front end
```

The service restarts itself after a crash (`Restart=always`) and is restarted by systemd if it hangs (watchdog, 20 s).
A switched-off or unplugged radio does **not** restart the service: it reconnects by itself. Stopping or restarting the
service releases PTT before the process exits.

In the UI, **Admin > Diagnostics** shows version, CPU/RAM/temperature, radio and CAT counters, audio state, clients, the
control holder and the recent log. `sudo scripts/doctor.sh` prints similar information plus USB/serial/audio devices.

## Updating

```bash
cd radio-remote
git pull
sudo ./update.sh --dry-run        # optional preview
sudo ./update.sh
```

`update.sh`: backs up config and users, installs the new version **beside** the old one (including Python packages,
before anything is switched), switches, restarts, and checks health. If the new version is not healthy it switches back
to the previous release automatically and keeps the failed one for inspection. The three newest releases are kept.
Config and the user database are never modified by an update.

Manual rollback: `sudo ln -sfn /opt/radio-remote/releases/<older> /opt/radio-remote/current && sudo systemctl restart radio-remote`.
(If the newer release changed Python dependencies, the older code normally still runs; if not, re-run
`/opt/radio-remote/venv/bin/pip install -r /opt/radio-remote/releases/<older>/requirements.txt`.)

## Backup and restore

A timer runs a backup daily (14 kept). Manual:

```bash
sudo scripts/backup.sh                                   # -> /var/backups/radio-remote/radio-remote-<time>.tar.gz
sudo scripts/restore.sh /var/backups/radio-remote/radio-remote-20261001-030000.tar.gz
```

What is backed up: **`/etc/radio-remote/config.toml`** and **`/var/lib/radio-remote/radio-remote.db`** (a consistent
snapshot even while running). That is everything worth keeping; the program itself is reproducible from git.
Restore stops the service, keeps the replaced files as `*.before-restore`, restores, restarts and checks health.
Copy backups off the Pi occasionally: they are on the same SD card.

## Forgot the admin password

On the Pi: `sudo -u radio-remote /opt/radio-remote/venv/bin/python -m radio_remote.cli --config /etc/radio-remote/config.toml reset-password <name>`
(with `PYTHONPATH=/opt/radio-remote/current/backend`). It prompts for the new password and signs that user out everywhere.

## The manual on the Pi

The manual is installed with the program and served at `https://<pi>/docs/` (the **Help** button in the app). It is built from `docs/*.md`
by `python scripts/build_docs.py --pdf` (HTML + a PDF) and travels inside every release as `docs-html/`.
