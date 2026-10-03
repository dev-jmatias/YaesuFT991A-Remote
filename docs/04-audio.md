# Remote audio (Phase 6)

```
RX:  radio USB codec -> arecord -> gain -> shared track -> MediaRelay -> Opus (per listener) -> browser
TX:  browser mic -> Opus -> decode -> mono 48 kHz -> [SERVER GATE] -> gain + limiter -> aplay -> radio USB codec
```

Internal format: mono, s16, 48 kHz, 20 ms frames. Opus is patched to 32 kbps (aiortc defaults to 96 kbps stereo);
configurable with `audio.opus_bitrate`.

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

## What was verified (automated, no radio)

- Real WebRTC sessions in-process (aiortc client vs service): RX audio arrives and the level meter moves; mic audio reaches
  the sink only while the gate is open; wrong connection, viewer role, peer limit, device loss + recovery.
- Real browser (the Browser pane against the mock radio): connect, RX tone, meter, stop/cleanup.
- Spike on a 20-core Windows desktop: 1 listener about 6% of one core, 3 listeners about 15% (encode + decode + SRTP in one process).

## NOT verified yet (needs your hardware)

1. **Pi CPU**: run `python tools/audio_spike.py` on the Pi 4 (and a 3B+ if you have one) and paste the output.
2. **Real ALSA devices**: capture/playback through the radio's USB codec.
3. **Browser microphone**: the preview pane blocks microphone access, so only a synthetic mic track has been tested.
   Needs HTTPS or localhost (see architecture doc, section 5.4). Phone/tablet over plain `http://raspberrypi.local` cannot use the mic.
4. **Radio-side routing menus** for sending USB audio into the transmitter (menus 070/072/106/109 and the matching
   PTT-select menus). The software never changes menus; you set them on the radio.
5. **Latency**: not measured. Budget: capture 20-80 ms + Opus 20 ms + network + browser jitter buffer.

## Deviations from the architecture document

- The audio service runs **inside the core process** for now (the document proposed a separate process). It is a
  self-contained class with a narrow interface (`tx_gate`, `on_levels`), so moving it to its own process later is
  mechanical. Decide after the Pi spike: if CPU is comfortable, keep it in-process.
- WebSocket-Opus fallback (for networks where WebRTC UDP is blocked) is not built.

## Pi setup notes

```bash
sudo apt install alsa-utils libopus0
sudo usermod -aG audio radio-remote          # service user needs the audio group
arecord -l ; aplay -l                         # note the CARD=<id> of the USB Audio CODEC
```

Use Raspberry Pi OS **Lite** (no PipeWire/PulseAudio grabbing the device). Set `audio.input_device` /
`audio.output_device` to `plughw:CARD=<id>,DEV=0` if auto-detection finds zero or several codecs.
The admin-only `GET /api/audio/devices` shows what the server sees.

## Audio bench (after Bench 2)

1. `arecord -l`/`aplay -l`: confirm the codec appears when the radio is on, and what its card ID is.
2. `arecord -D plughw:CARD=<id>,DEV=0 -f S16_LE -r 48000 -c 1 -d 5 /tmp/rx.wav` with a signal on the radio: play it back.
3. In the app: Listen. Check the RX meter and what you hear. Adjust `audio.rx_gain_db`.
4. Mic test **with a dummy load and PTT still disabled**: enable Mic, speak, watch "Your mic" move and "TX audio" stay at 0.
5. Only after Bench 4 in the checklist (PTT with dummy load): hold PTT and watch "TX audio"; set radio menus so USB audio
   feeds the transmitter, then tune `audio.tx_gain_db` and the radio's data-out level until ALC is just touching.

## Receive volume too low

Three places, in this order:
1. **Browser Volume slider** (Audio tab) at 100 %.
2. **Admin > Config > Audio > rx_gain_db**: boost in dB (-30..30, live, no restart). +6 dB doubles the amplitude, +12 dB is four times.
   A boost above 0 dB runs through the same soft limiter as the transmit path, so loud signals do not clip hard.
3. **The radio's own USB output level menus** (0-100, default 50): 107 SSB OUT LEVEL, 073 DATA OUT LEVEL, 046 AM OUT LEVEL, 075 FM OUT LEVEL,
   054 CW OUT LEVEL, 099 RTTY OUT LEVEL (CAT manual p.9). This is the cleanest fix because it improves the signal-to-noise ratio instead of
   just amplifying; the radio's AF gain knob is believed not to affect the USB audio (not verified on the bench).

## Several browsers at once

Any operator connection may attach a microphone track; the server only passes audio from the connection that owns PTT (the one with
control), so a second open device, or an old session that has not timed out yet, can no longer take the microphone away from the real one
(before, only the first connection got the mic and the others silently had "server refused mic"). The Audio tab status line shows
"(server refused mic)" if the server still refuses it (viewer account, or no radio sound card).

## Mic icon and Listen icon are independent

The audio session (one WebRTC connection) exists while Listen is on OR the microphone is armed, because sending needs it too. Arming the mic
alone connects with the radio audio muted ("connected, not listening (mic armed)"); Listen only unmutes it; both off ends the session.
Before this change the microphone was only opened when Listen was on, so a teal mic icon with Listen off showed no mic level and no
"To radio" frames. The mic is never armed from an earlier visit (a tap is needed for the permission anyway).
