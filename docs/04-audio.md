# Remote audio

```
RX:  radio USB codec -> arecord -> gain -> shared track -> MediaRelay -> Opus (per listener) -> browser
TX:  browser mic -> Opus -> decode -> mono 48 kHz -> [SERVER GATE] -> gain + limiter -> aplay -> radio USB codec
```

Internal format: mono, s16, 48 kHz, 20 ms frames. Opus runs at 32 kbps (aiortc defaults to 96 kbps stereo); configurable with
`audio.opus_bitrate`. The audio runs in the same process as the rest of the server (a separate process was considered; the measured CPU cost on a Pi 4
makes it unnecessary).

## Using it

* **Speaker icon = Listen**, **microphone icon = arm the microphone**. Each is switched on and off only by you. The microphone needs **HTTPS**
  (browsers refuse it on plain `http://`): the installer sets that up, and your device must trust the Pi's certificate once
  ([security](06-security-remote.md)) or use the Tailscale name ([tailscale](tailscale.md)).
* The mic transmits only while you hold PTT and hold control. Set the radio to take its audio from the USB port: menu 106 = REAR
  (the REAR / MIC buttons in the app do this), see [radio connection](radio-connection.md).
* The audio session (one WebRTC connection) exists while Listen is on or the microphone is armed. Arming the mic alone connects with the radio
  audio muted; Listen only unmutes it; both off ends the session. The mic is never armed from an earlier visit.
* Several browsers can listen at once (`audio.max_peers`, default 3). Any operator may attach a microphone, but the server passes audio only from
  the connection that owns PTT.
* A Bluetooth headset can be chosen as the device on the Audio tab. Listening continues with the app in the background on phones.

## Safety design

- Mic audio is dropped on the **server** unless the PTT guard says this exact WebSocket connection owns an active
  transmission (`guard.keyed and guard.owner == conn_id`). The browser also mutes its mic track outside PTT, but that is a
  convenience, not the protection.
- Gate closing clears the jitter queue, so stale audio is never sent at the next PTT.
- A soft limiter (ceiling about -1 dBFS) always follows TX gain.
- Viewers can listen but never transmit audio; only one mic stream is accepted at a time.
- When no mic stream is connected the playback device is closed entirely; when one is connected, silence is fed to the
  radio between transmissions to keep its codec stable.
- Closing a WebSocket closes that connection's audio session. Audio sessions are tied to the logged-in user:
  `/api/audio/offer` rejects a `conn` that belongs to someone else.

## What has been verified

On a real FT-991A with a Raspberry Pi 4:

- receive audio in the browser, with the meter moving; the radio appears as one USB audio CODEC and is detected automatically;
- transmit audio into a dummy load, with ALC and COMP readings (the TX gain and the radio's data-out level set the ALC);
- background listening on a phone, and choosing a Bluetooth output and input;
- CPU cost measured with `tools/audio_spike.py` on a Pi 4 (aarch64, Python 3.13): Opus encode 3-4 % of one core per stream, decode below 1 %, one
  loopback listener about 21 % and three about 56 % of one core with both ends in one process (the real server-side cost is lower).
  A Pi 4 has ample headroom for the default `audio.max_peers = 3`; a Pi 3B+ is expected to be 2-3 times slower per core: use 1-2.

Automated tests (no radio) cover real WebRTC sessions in-process: RX audio and level meter, mic audio reaching the sink only while the gate is
open, wrong connection, viewer role, peer limit, device loss and recovery.

Not built: a **WebSocket-Opus fallback** for networks where WebRTC (UDP) is blocked, and a measured latency figure (budget: capture 20-80 ms + Opus
20 ms + network + the browser's jitter buffer).

## Pi setup notes

```bash
sudo apt install alsa-utils libopus0          # the installer does this
sudo usermod -aG audio radio-remote           # service user needs the audio group (the installer does this)
arecord -l ; aplay -l                          # note the CARD=<id> of the USB Audio CODEC
```

Use Raspberry Pi OS **Lite** (no PipeWire/PulseAudio grabbing the device). Set `audio.input_device` /
`audio.output_device` to `plughw:CARD=<id>,DEV=0` (a full ALSA name, never a bare number) only if auto-detection finds zero or several codecs.
The admin-only `GET /api/audio/devices` shows what the server sees.

## If there is no audio

1. `arecord -l` / `aplay -l`: confirm the codec appears when the radio is on, and what its card ID is.
2. `arecord -D plughw:CARD=<id>,DEV=0 -f S16_LE -r 48000 -c 1 -d 5 /tmp/rx.wav` with a signal on the radio: play it back. If that is silent the problem is
   on the radio side (menus, USB level), not in the app.
3. In the app: Listen. Check the RX meter and what you hear. Adjust `audio.rx_gain_db`.
4. For transmit, test **with a dummy load**: enable Mic, speak, watch "Your mic" move; then hold PTT and watch "TX audio". Tune
   `audio.tx_gain_db` and the radio's data-out level until the ALC is just touching.
5. The connection can drop and come back for a second when the browser renegotiates (reloading the page, toggling Listen, a network change): that is normal.

## Receive volume too low

Three places, in this order:
1. **Browser Volume slider** (Audio tab) at 100 %.
2. **The radio's own USB output level menus** (0-100, default 50): 107 SSB OUT LEVEL, 073 DATA OUT LEVEL, 046 AM OUT LEVEL, 075 FM OUT LEVEL,
   054 CW OUT LEVEL, 099 RTTY OUT LEVEL (CAT manual p.9). This is the cleanest fix because it improves the signal-to-noise ratio instead of
   just amplifying. (On the tested FT-991A, menu 107 = 90 fixed a quiet receive level.)
3. **Admin > Config > Audio > rx_gain_db**: boost in dB (-30..30, live, no restart). +6 dB doubles the amplitude, +12 dB is four times.
   A boost above 0 dB runs through the same soft limiter as the transmit path, so loud signals do not clip hard.

The radio's AF gain knob is believed not to affect the USB audio (not verified separately); the app therefore does not offer an AF gain slider.
