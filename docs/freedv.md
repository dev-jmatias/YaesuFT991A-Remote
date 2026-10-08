# FreeDV digital voice

FreeDV sends speech as modem tones in an ordinary SSB channel. Radio Remote does the FreeDV work on the Pi, so you only need the browser:

* **Receive:** the Pi turns the radio's modem tones back into speech and plays that to every listener.
* **Transmit:** your voice (browser microphone) is turned into modem tones by the Pi and sent to the radio, while you hold PTT.

Modes: **RADE V1** (the neural mode most stations use today) and **RADE V2** (see below). Both come from one optional library, the same one that has always held RADE.
The older codec2 modes (1600, 700D, 700E) were removed from the program; a configuration file that still names one of them is read as RADE V1.

> **Status:** the modem round trip (voice to tones to voice, noise rejected) is covered by automated tests. How well it works over the air with
> your radio, antenna and band has to be found out by trying it: start with receive, then transmit into a dummy load.

## What you need

* The RADE library (see below). It is installed by the installer; without it the FreeDV tab says so and offers no mode.
* Remote audio working ([audio](04-audio.md)): FreeDV only decodes while someone is listening (speaker icon) and only transmits while
  the microphone is armed and you hold PTT.

## Using it

1. Open the **FreeDV** tab (beside Audio; on wide screens it is the card under Levels and Audio). **Switch FreeDV on** starts it; if the radio is
   not in USB or LSB it puts it in USB (LSB below 10 MHz), which is the FreeDV convention. The same button switches it off.
2. The tab shows the status (whether the modem is locked on a signal and its signal-to-noise ratio) and has the **preset channels**: one tap sets
   LSB or USB, the frequency and the FreeDV mode, and switches FreeDV on.
3. Switch **Listen** on (speaker icon). When a FreeDV signal is locked you hear the decoded speech. Without a signal you hear nothing: the Pi
   passes no noise on. Tune carefully (the modem finds the signal within about 100 Hz); watch the status.
4. **Transmit:** arm the microphone (microphone button, which also sets the radio's input to REAR), hold PTT (or slide up to lock it) and talk.
   The Pi encodes your voice for as long as you hold PTT.

Switching FreeDV off passes the radio's audio through as before (it does not change the radio's mode back).

### The tuning aid: the Pi finds the tuning for you

A FreeDV modem only locks when the signal is close to where it expects it, and a radio dial cannot be set that finely by hand. Measured with ideal
signals: RADE V1 locks only within about ±50 Hz, so the Pi does the fine tuning in software (RADE V2 locked across the whole ±300 Hz tried, so for V2 the search is rarely needed):

* While nothing is locked it shifts the received audio, step by step, within ±450 Hz around the dial frequency, until the modem locks, and then holds that shift.
  The strongest-looking spot in the spectrum is tried first. A short fade-out does not make it start again (it waits a few seconds).
* **The FreeDV tab shows** the spectrum of the received audio (0 to 4 kHz) with the band where the modem expects its signal shaded (it moves with the correction), a
  yellow line where the spectrum thinks the signal is, and a text line: the audio level (it warns when it clips or is very low), and either *Locked: the signal is
  120 Hz above its normal place; the Pi is correcting it by itself* or *No signal locked yet: searching*.
* **Centre the dial** (shown when the Pi is correcting 30 Hz or more) moves the radio's dial by exactly that offset, so the signal sits where the modem expects it.
  The dial buttons (−100, −10, +10, +100 Hz) are for fine steps. If you move the dial while locked, the Pi adjusts its correction by the same amount and keeps the lock.
  **Search again** forgets the tuning found so far.
* The Pi only analyses the audio while someone is listening (speaker icon). If nothing locks: check that the level line says "fine", that the radio's DSP
  (noise reduction, notch, contour, narrow filter) is off, that the sideband is right (LSB below 10 MHz) and that a FreeDV station is really on the air.

### Radio settings that matter for FreeDV

* USB or LSB mode (not DATA), **speech processor off**, no equaliser tricks: the radio must pass the modem tones unchanged.
* Set the transmit level so that the **ALC barely moves**: the tones are a continuous signal with high peaks, and an overdriven signal
  is worse than a weak one. Use the **Transmit level of the modem tones** slider on the FreeDV tab (administrators; default -6 dB) together
  with the radio's data-in level, and keep the power moderate (about half of what you would use for SSB is a good start).
* The receive level that goes to the Pi is the radio's menu 107 SSB OUT LEVEL ([radio connection](radio-connection.md)): the modem tones should
  be clearly present but never clipping.

## RADE (the neural modes)

### RADE V1

RADE V1 sends speech as an OFDM signal that is about 2.1 kHz wide and decodes at lower signal-to-noise ratios than 700D/700E (it still locks at about 0 dB
on a fading path). It is the mode most FreeDV activity uses. It comes from a separate library of about 24 MB.

**A new installation already has it, you do not need to do anything:** the ready-made image and the installer pack contain the RADE library, and `install.sh` (Raspberry Pi or
Debian PC) downloads and installs it as part of the install (`--no-rade` skips it). Ordinary updates do not carry it, so they stay small, and a library that is already installed is kept.

**If it is missing**, for example because the install had no internet: an administrator presses **Install RADE** in **Admin > Config > RADE** (or in the **FreeDV** tab, which shows the same
button; once RADE is installed it says **Reinstall RADE**). The Pi downloads the library from the project's release page, checks its SHA-256, puts it in `/var/lib/radio-remote/lib` and starts
using it at once, with no restart. It works on 64-bit Linux: a Raspberry Pi with the 64-bit OS, or Debian 12/13 on a 64-bit PC (see [installation](INSTALL.md), route D). The library is built on
Debian 12 and runs on Debian 12 and newer. From the command line:

```bash
sudo /opt/radio-remote/current/scripts/install_rade.sh            # downloads the matching release file, checks its SHA-256, installs it, restarts the service
sudo /opt/radio-remote/current/scripts/install_rade.sh --file F.tar.xz   # from a file you copied to the Pi (no internet)
sudo /opt/radio-remote/current/scripts/install_rade.sh --remove
```

Then choose **RADE** in the mode list of the FreeDV tab (or in a channel). Until it is installed the tab says so and offers only the codec2 modes.

* The library is built from the open-source `rade_c` C port (BSD licence, with the Opus sources it uses); the release includes the licence files.
  It runs in the same program as the rest of the server (about 3% of one fast PC core to decode, less to encode; expect roughly a quarter to a
  third of one Pi 4 core while receiving: not yet measured on your Pi).
* RADE V1 has no automatic level control on receive: if it does not lock on a signal you can hear clearly, try the radio's menu 107 or **Admin > Config >
  Audio > RX gain**. For transmit use the same **modem level** slider as the other modes and keep the ALC barely moving.
* The speech it plays is the neural vocoder's voice (clean, but not your exact voice). A few hundred milliseconds of delay is normal.

### RADE V2

Choose **RADE V2** in the mode list. It is a newer version of the same idea with a narrower signal (about 1.1 to 1.9 kHz), its own level control on receive, and it follows a mistuned
signal by itself. **A V1 station cannot decode a V2 station and the other way round**, so use the version the other station uses; most stations are on V1 today.

* It needs a library built with V2 support (the one that comes with this version). If you updated the program but kept an older RADE library, V1 still works and the tab says that V2 needs the library to be reinstalled:
  **Admin > Config > RADE > Reinstall RADE**.
* The FreeDV Reporter lists V2 stations as "RADE V2"; the site's mode name for V2 is `RADEV2` (as other open-source clients report it).

### When your transmission is unreadable at the other station

RADE needs a continuous stream of tones, with no holes in it. The Pi therefore keeps a small cushion (about 0.2 s) of modem tones queued before it starts sending them to the radio, so an uneven network (Wi-Fi, a remote connection)
cannot put gaps into the signal; when you release PTT the Pi first sends the last modem frame (silence completes it) and lets the queued tones reach the radio, so the end of the over is not cut off; the radio is unkeyed about half a second after you let go.
After every over the service log says whether the tones went out whole:

```bash
journalctl -u radio-remote --since "10 min ago" | grep "FreeDV transmit"
```

`no holes` is good. `the tones had N hole(s)` means the audio reached the server unevenly or the PC is too slow: use a wired connection, close other programs, and send the line to the developers.
**Admin > Diagnostics** (and `/api/diagnostics`) also shows the slowest processing step, which should stay well under 20 ms on the receive side. A PC that regularly needs longer than that for one 20 ms frame will stutter.

## FreeDV Reporter: be listed, and see who is on the air

[qso.freedv.org](https://qso.freedv.org/) is the live list of FreeDV stations: callsign, grid square, frequency, mode, whether the station is transmitting right now, and a short message.
Radio Remote can join it. **It is off by default.**

* **Switch it on** (administrators): *FreeDV tab > FreeDV Reporter settings*. Tick **Switch the link to FreeDV Reporter on**, and enter your **callsign** and **grid square**
  (for example `IO91wm`) to be listed, plus an optional short message. Choose whether to **announce** this station, to **show who is on the air**, or both. *Apply*.
* **What is sent, and when:** the Pi connects to qso.freedv.org **only while FreeDV is switched on** and disconnects when you switch it off. It sends your callsign, grid square and the program name
  ("Radio Remote 1.x") once, then your dial frequency whenever it changes, your FreeDV mode, whether you are transmitting (it flips when you hold PTT with FreeDV on, and back when you let go)
  and your message. **Your callsign and grid square are public on that site.** Nothing else is sent: no audio, no accounts, no settings.
* **Who is on the air:** the FreeDV tab lists the stations the site reports, those within 5 kHz of your frequency first (shaded), stations that are transmitting marked **TX**. **Tune** moves your
  radio to that station (sideband, frequency and FreeDV mode) and switches FreeDV on. Stations in a mode this program cannot decode (for example the old 700D) have no Tune button.
* **Limits:** your callsign is not sent inside the FreeDV signal itself (other stations see you in the web list, not in their decoder), so say it by voice when you transmit as usual.
  Reports of the stations *you* hear (their callsign and SNR) are not sent yet. The site's protocol was taken from open-source clients, not from official documentation, so if the site changes,
  the link simply shows "cannot reach FreeDV Reporter" and everything else keeps working.
* The settings are in the configuration under `[reporter]` (`enabled`, `announce`, `watch`, `callsign`, `grid_square`, `message`).

## The channels

The list on the FreeDV tab comes from the configuration (`freedv.channels`). The defaults are the usual FreeDV calling frequencies (160 m
1.997, 80 m 3.625 and 3.643, 40 m 7.177 and 7.197, 20 m 14.236 and 14.240, 17 m 18.118, 15 m 21.313, 12 m 24.933, 10 m 28.330 and 28.720 MHz, all in kHz
as dial frequencies). Check the current activity frequencies on the FreeDV website before relying on them.
Administrators can change the list in the tab (**Edit the channel list**): name, frequency in MHz, mode.

Settings (Admin > Config or the tab): `freedv.mode` (the mode used when FreeDV is switched on without choosing one), `freedv.tx_level_db` (-40..0), `freedv.channels`.

## Notes and limits

* One FreeDV setting for the whole radio: it applies to every listener, and changing it needs control of the radio, like changing the mode.
* It is refused while the radio is transmitting.
* Voice goes through the same server-side safeguards as ever (transmit permission, control, PTT heartbeat, time limit, lock). FreeDV changes what is
  sent to the radio, not who may transmit.
* Operating rules: FreeDV is plain unencrypted amateur digital voice; use it only where your licence and the band plan allow it.

## How much CPU does it need?

`python3 scripts/freedv_probe.py` (on the Pi: `/opt/radio-remote/venv/bin/python /opt/radio-remote/current/scripts/freedv_probe.py`) runs every installed mode as a
transmit-to-receive loopback and prints the share of one CPU core each needs and its slowest single step. On a PC RADE needs about 1% to transmit and 5% to receive; on a small Intel Atom PC receive was measured at about 40% of one core.

## If it does not work

| Symptom | Likely cause |
|---|---|
| The FreeDV tab offers no mode | the RADE library is not installed (Admin > Config > RADE > Install RADE) |
| Status stays at "no signal locked yet" | wrong frequency or sideband, signal too weak, RX level too low or clipping, or it is another mode (RADE V1 and V2 signals do not decode as each other) |
| "Tap the speaker icon" | the Pi only decodes while someone is listening |
| Locks, but the speech is garbled | an overloaded receive level (lower menu 107) or a very weak signal |
| Others cannot decode you | overdriven or too-weak transmit level, speech processor on, or the radio not in USB/LSB |
