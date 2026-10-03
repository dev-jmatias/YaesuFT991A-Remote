# Troubleshooting

First step for almost everything: `sudo /opt/radio-remote/current/scripts/doctor.sh` (or **Admin > Diagnostics**), then
`journalctl -u radio-remote -n 100 --no-pager`. Paste the doctor output when asking for help; it contains no passwords.

## Radio not detected ("radio offline", "no FT-991A found on any USB serial port")

1. `lsusb`: is there a Silicon Labs CP210x entry? No: cable, radio power, try another Pi USB port, avoid unpowered hubs.
2. `ls /dev/serial/by-id/`: do ports exist? No: kernel did not create them (check `dmesg | tail`).
3. Is the radio **on** (not standby)? CAT does not answer in standby.
4. `radio.model` must be `ft991a`, not `mock` (Admin > Config).
5. Detection tries 4800/9600/19200/38400. If it still fails, the radio's **menu 031 CAT RATE** may not match any, or another program holds the port.

## CAT not responding / timeouts

- Another program is using the port (`sudo fuser -v /dev/ttyUSB*`, check ModemManager: `systemctl status ModemManager`; the installer's udev rule tells it to ignore the radio, re-plug the cable after installing).
- Baud mismatch: set `radio.baud` to the radio's menu 031 value.
- Raise menu 032 CAT TOT. Check the cable and avoid long unshielded runs near the transmitter (RF in the USB cable can corrupt CAT; ferrites help).
- `journalctl -u radio-remote | grep -i cat` shows reasons ("radio stopped answering", "unexpected radio ID").

## Wrong serial device

The FT-991A exposes two serial ports; the app picks the one that answers `ID;` with `0670`. Force one with
`radio.serial_port = "/dev/serial/by-id/usb-Silicon_Labs_...-if00-port0"` (stable across reboots, unlike `ttyUSB0/1`).
"unexpected radio ID" means a different radio model answered on that port.

## USB audio missing

`arecord -l` / `aplay -l` must list the radio's codec. If not: cable/power as above; the audio device appears only while
the radio is on. Several codecs present: set `audio.input_device` and `audio.output_device` explicitly (the UI says
"several USB audio CODECs found"). Make sure the service user is in group `audio` (`id radio-remote`) and that you use
Raspberry Pi OS **Lite** (a desktop session's PipeWire can hold the device; check with `fuser -v /dev/snd/*`).

## No RX audio

- Click **Listen** (browsers need a click). Check volume slider and the "RX audio" meter: moving meter but silence = browser/volume; flat meter = nothing arriving from the radio.
- Server side: Admin > Diagnostics > Audio: `rx_capture`, `capture_error`.
- Test the sound card directly: `arecord -D plughw:CARD=CODEC,DEV=0 -f S16_LE -r 48000 -c 1 -d 5 /tmp/rx.wav && aplay /tmp/rx.wav` with a signal on the radio.
- The radio's menus decide whether USB audio carries receive audio ([radio-connection.md](radio-connection.md)).
- Plain `http://` pages can listen; if audio cannot connect at all, check browser console for WebRTC errors and that UDP is not blocked between you and the Pi (VPN relays can add delay).

## No TX audio

- Needs **HTTPS** (microphone is blocked on plain http), a microphone permission grant, an operator account, and **control** (the bar must say "You have control").
- Audio only reaches the radio **while you hold PTT**. Watch "TX audio (to radio)": it stays 0 until PTT is held and `allow_ptt` is true.
- Radio side: transmit audio source menus (070/072/106/109...), data/mic gain; with a dummy load adjust `audio.tx_gain_db` and the radio's out-level until ALC barely moves.

## PTT does not work

1. The button says "PTT DISABLED IN CONFIG": an administrator enables it in **Admin > Config > Transmitting (PTT)** (password
   required), or sets `allow_ptt = true` in `/etc/radio-remote/config.toml` and restarts.
2. "Request control first" / "X has control": take control (control bar).
3. Radio offline, or "radio did not key": check CAT first.
4. Released too soon: PTT is hold-to-talk; it un-keys if the page loses focus, the tab is hidden, the network drops (heartbeat 1 s), or after `safety.tx_timeout_s`.
5. The radio's own menu 036 TX TOT may also be cutting you off.

## CAT reconnect problems

Unplug/replug and radio power-cycle should recover within a few seconds. If the status stays offline: look for
"CAT session ended" in the log; confirm the port name did not change (use by-id); `sudo systemctl restart radio-remote`.
After a radio power cycle, the radio forgets auto-information; the app re-enables it on reconnect (log: "FT-991A connected").

## Browser cannot connect

- Is the service up? `systemctl status radio-remote`, `curl -s http://127.0.0.1:8080/api/status` on the Pi.
- Is Caddy up? `systemctl status caddy`, `journalctl -u caddy -n 30`. Does the name resolve (`ping radio.local`; some Windows/Android setups lack mDNS: use the Pi's IP and `--hostname`)?
- Certificate warning: trust Caddy's local CA once per device ([06-security-remote.md](06-security-remote.md)), or use Tailscale.
- Login page loops / "origin not allowed": you reached the app through a name the proxy did not pass on; add it to `server.allowed_origins`.
- "too many attempts": wait; lock-outs are per address and user.
- Port 8080 is loopback-only on purpose; use the HTTPS address.

## Tailscale connectivity

- The Pi shows up in Tailscale but the page does not open (no answer, any port): the HTTPS front end does not know the Tailscale
  name yet. Run `sudo /opt/radio-remote/current/scripts/tailscale_setup.sh` ([tailscale.md](tailscale.md)).
- `tailscale status` on both ends; `tailscale ping <pi>` shows direct vs relayed (DERP) path.
- HTTPS names need HTTPS certificates enabled in the Tailscale admin console.
- Audio over a relayed path is laggy or fails (WebRTC uses UDP); a direct path is needed for good audio.
- If the page loads but the session cookie is not `Secure` / mic is blocked, the proxy is not forwarding `X-Forwarded-Proto: https`: use the Caddy variant ([tailscale.md](tailscale.md)).

## Service will not start / keeps restarting

`journalctl -u radio-remote -n 100`. Common: a typo in `config.toml` (the error names the key); missing Python packages
after a failed update (`sudo /opt/radio-remote/venv/bin/pip install -r /opt/radio-remote/current/requirements.txt`);
`status=...WATCHDOG` means the app hung (please send the log).

## Start over

`sudo scripts/uninstall.sh` (keeps data) then `sudo ./install.sh`; or `--purge` for a completely clean slate.

## Mic armed but "To radio: 0 frames"; log says "Unknown PCM 6"

The server could not open the radio's sound card for transmit: `audio.output_device` holds something that is not an ALSA device name
(for example a bare card number `6`). Leave **Admin > Config > Pi -> radio (playback)** empty (auto-detect) or set
`plughw:CARD=CODEC,DEV=0` (see `aplay -l`), save, restart the service. A bare number is now rejected when saving, and the Audio tab shows
"Audio to the radio failed: ..." when the playback device cannot be opened.
