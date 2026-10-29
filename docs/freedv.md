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

1. Open the **Digital** tab and choose **FreeDV** in its switch (on the all-in-one desktop layout it is the card under Levels and Audio). **Switch FreeDV on** starts it; if the radio is
   not in USB or LSB it puts it in USB (LSB below 10 MHz), which is the FreeDV convention. The same button switches it off.
   The **Radio mode** box next to the FreeDV mode chooses the mode the radio is put in: **Automatic** (the default: LSB below 10 MHz, USB above, the FreeDV convention) or one of **USB, LSB, DATA-U, DATA-L, FM**, as on the other digital screens. It is remembered in this browser; picking a channel and the *Tune* buttons of the FreeDV Reporter list use it, and choosing it changes the radio's mode at once. Other stations are on USB/LSB: with a DATA mode or FM your signal may sit somewhere else in the audio than theirs (see the DATA mode section below), so keep Automatic unless you know why you want another.
2. The tab shows the status (whether the modem is locked on a signal and its signal-to-noise ratio) and has the **preset channels**: one tap sets
   LSB or USB, the frequency and the FreeDV mode, and switches FreeDV on.
3. Switch **Listen** on (speaker icon). When a FreeDV signal is locked you hear the decoded speech. Without a signal you hear nothing: the Pi
   passes no noise on. Tune carefully (the modem finds the signal within about 100 Hz); watch the status.
4. **Transmit:** arm the microphone (microphone button, which also sets the radio's input to REAR), hold PTT (or tap TX LOCK to transmit hands-free and tap it again to stop) and talk.
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

**FT-991A: use plain USB/LSB, like the other stations (recommended, and the default).** Below 10 MHz FreeDV uses LSB, above it USB, with the same dial frequencies as the channel list and the FreeDV Reporter. Every
station you meet is on its radio's ordinary SSB mode, and a DATA mode can put your signal somewhere else (see below). In USB/LSB the radio's filter is 2400 Hz, which carries RADE V1 (audio 750 to 2250 Hz) and V2 (about 1000 to 1900 Hz), and
the radio's own SSB transmit filter (menu 110, for example 400-2600 Hz) trims the edges of the signal. What the radio must **not** do is process the audio:

| Menu / control | Value |
|---|---|
| Speech processor | **OFF** (also **RF/AF compression**, noise reduction, notch and contour on receive) |
| Parametric microphone equalizer (menus for the mic EQ) | **OFF** |
| 110 SSB TX BPF | at least 400-2600 Hz (100-2900 Hz is the widest of the usual choices) |
| 106 SSB MIC SELECT, 109 SSB PORT SELECT | REAR, USB (the page's microphone button sets 106) |
| 107 SSB OUT LEVEL | the receive level to the Pi: the tones clearly present, never clipping |

Set the transmit level with the **Level test** (see below): the radio's **ALC must stay under its mark** and the power should be close to what you set; a compressed RADE signal cannot be decoded.

**Alternative: the radio's DATA mode (the way the FreeDV guide for the FT-991A does it).** The DATA modes bypass the radio's microphone chain, but they have traps of their own: the receive filter starts at **500 Hz**
(see the filter width below), the transmit band is not trimmed by menu 110, and the OTHER DISP / OTHER SHIFT menus (064, 065) change the relation between the displayed frequency and the carrier, so
at the same displayed frequency your signal may sit up to 1500 Hz away from where a USB/LSB station expects it (V1 only locks within about 50 Hz: nothing would decode). Use it only if you have checked all three. FreeDV's own guide
sets these menus once on the radio:

| Menu | Name | Value |
|---|---|---|
| 031 | CAT RATE | 38400 |
| 059 | CW FREQ DISPLAY | DIRECT FREQ |
| 062 | DATA MODE | OTHERS |
| 064, 065 | OTHER DISP (SSB), OTHER SHIFT (SSB) | 1500 Hz |
| 066, 068 | DATA LCUT FREQ, DATA HCUT FREQ | OFF |
| 070 | DATA IN SELECT | REAR (the page's microphone button sets it) |
| 071 | DATA PTT SELECT | DAKY |
| 072 | DATA PORT SELECT | USB |

Then switch on **Use the radio's DATA-USB / DATA-LSB mode** in the FreeDV tab (administrators): FreeDV then puts the radio in DATA-USB (DATA-LSB below 10 MHz) instead of USB (LSB).
It is off by default because it needs the menus above. The receive level that goes to the Pi is then menu **073 DATA OUT LEVEL** (in USB/LSB it is 107 SSB OUT LEVEL).

**The radio's filter width: RADE V1 needs about 1500 Hz of the audio, and the DATA modes start too narrow (BENCH-FOUND).** RADE V1 occupies about **1500 Hz: the audio from about 750 to 2250 Hz** (V2 about 900 Hz, around 1000 to 1900 Hz).
An FT-991A in a DATA mode starts with a **500 Hz** receive filter (width code 10 in the radio's table): a recording of the noise in DATA-L spanned exactly 1225 to 1750 Hz, and the reference RADE decoder found nothing in it,
because only a third of the carriers get through. The DATA filter is centred on 1500 Hz, so **1700 Hz is the least that carries V1 and about 2000 Hz is comfortable**; in USB/LSB the passband starts near 300 Hz, so about 2100 Hz is needed
and the default 2400 Hz is fine. More than that only lets in more noise and neighbouring stations (it does not help RADE). The width is kept per mode (USB and DATA-L have their own), and the DATA modes have no width key on the
radio's front panel, so the Filters tab shows a **Width** list for DATA-U and DATA-L as well, and the FreeDV tab shows a warning with a **Widen** button when the filter is too narrow while FreeDV is on. With
**Open the radio's filter automatically** ticked (the default; administrators can change it), the app opens a too-narrow filter by itself a moment after FreeDV is switched on or the mode changes, to the narrowest value from 2000 Hz
(DATA) or 2400 Hz (USB/LSB) up. Check it with a recording of the noise (REC on the Radio tab, then *Download raw radio audio*): it should span roughly 750 to 2250 Hz or more.

**Always:** speech processor off, no equaliser tricks (the radio must pass the modem tones unchanged).

* Set the transmit level so that the **ALC barely moves**: the tones are a continuous signal with high peaks, and an overdriven signal
  is worse than a weak one. Use the **Transmit level of the modem tones** slider on the FreeDV tab (administrators; default -6 dB) together
  with the radio's data-in level. Watch the power meter as well: a level so low that the radio makes only a few watts reaches fewer stations.
* The receive level that goes to the Pi is the radio's menu 107 SSB OUT LEVEL in USB/LSB, 073 DATA OUT LEVEL in the DATA modes ([radio connection](radio-connection.md)): the modem tones should
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

### Your callsign in the transmission (RADE V1)

At the end of every RADE V1 transmission the Pi sends your callsign inside the signal (in the end-of-over frame), the way the FreeDV program does, so other stations' programs can show who was calling.

* **Set it** (administrators): *FreeDV tab > FreeDV Reporter settings > Callsign sent with every transmission*. Up to 8 letters, digits or `/`. Left empty, the **Callsign** of the Reporter settings is used
  (its first 8 characters). The configuration key is `[freedv] callsign`.
* **Needs the RADE library of 1.1.36 or later.** An older one sends no callsign; the RADE line on the FreeDV tab then says so, and *Reinstall RADE* fixes it.
* **What you see:** when a received over ends with a callsign, **Last heard: CALLSIGN, SNR +6.0 dB (time)** appears (the SNR is the average over the received over) under the FreeDV status line. It is read from the end-of-over frame only, so it appears after the over, and
  not at all if the signal was too weak for the error correction to recover it (the speech may still have been readable).
* RADE V2 has no such text channel here: it sends and shows nothing.

### RADE V2

Choose **RADE V2** in the mode list. It is a newer version of the same idea with a narrower signal (about 1.1 to 1.9 kHz), its own level control on receive, and it follows a mistuned
signal by itself. **A V1 station cannot decode a V2 station and the other way round**, so use the version the other station uses; most stations are on V1 today.

* It needs a library built with V2 support (the one that comes with this version). If you updated the program but kept an older RADE library, V1 still works and the tab says that V2 needs the library to be reinstalled:
  **Admin > Config > RADE > Reinstall RADE**.
* The FreeDV Reporter lists V2 stations as "RADE V2"; the site's mode name for V2 is `RADEV2` (as other open-source clients report it).

### When your transmission is unreadable at the other station

**First check that you and the other station use the same RADE version.** V1 and V2 are different waveforms and do not decode each other at all (the reference decoder gave 0 % lock on a V2 recording with V1 and
the other way round). Many stations on the air use V2 now (about 900 Hz wide, around 1000 to 1900 Hz of the audio) while others use V1 (about 1500 Hz, 750 to 2250 Hz). Ask the station, look at the mode on the
FreeDV Reporter list, and set the same one on the FreeDV tab; a station that "cannot understand anything" is very often on the other version.

**Check that the transmitted signal stays in its band.** The V1 modem's output is made of OFDM carriers with sharp edges; without a band-pass filter about **7 % of its power falls outside 700 to 2300 Hz**
(skirts down to the audio floor and up past 3 kHz), so on someone's waterfall it looks wider than it should and the radio's ALC and filters see it. RADE V2 has its filter on by default; for V1 the library has it
but leaves it off. Libraries installed from **1.1.30** on have it switched on (1.6 % outside the band, a lower peak-to-average ratio): if the FreeDV tab says the installed RADE library is older, press **Reinstall RADE**.


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
* **Limits:** with RADE V1 your callsign is also sent inside the signal (see *Your callsign in the transmission* above); say it by voice as usual anyway. While you are announced, every station whose callsign you read from the end of an over is reported to the site (their callsign, the mode and the SNR you received them at, the average over that over), so a contact on qso.freedv.org carries the SNR you heard them at. The site's protocol was taken from open-source clients, not from official documentation, so if the site changes,
  the link simply shows "cannot reach FreeDV Reporter" and everything else keeps working.
* The settings are in the configuration under `[reporter]` (`enabled`, `announce`, `watch`, `callsign`, `grid_square`, `message`).

## The channels

The list on the FreeDV tab comes from the configuration (`freedv.channels`). The defaults are the usual FreeDV calling frequencies (160 m
1.997, 80 m 3.625 and 3.643, 40 m 7.177 and 7.197, 20 m 14.236 and 14.240, 17 m 18.118, 15 m 21.313, 12 m 24.933, 10 m 28.330 and 28.720 MHz, all in kHz
as dial frequencies). Check the current activity frequencies on the FreeDV website before relying on them.
Administrators can change the list in the tab (**Edit the channel list**): name, frequency in MHz, mode.

Settings (Admin > Config or the tab): `freedv.mode` (the mode used when FreeDV is switched on without choosing one), `freedv.tx_level_db` (-40..+3), `freedv.channels`.

## Setting the transmit level (the level test)

RADE's transmit signal has an almost constant amplitude (the paper reports a peak-to-average ratio under 1 dB before the radio). It is meant to be sent at close to the radio's full
rated power, and **"more is not better"** only applies beyond that: an *overdriven* signal is distorted and decodes badly, but an *underdriven* one is simply weak at the other
station. A radio whose ALC barely moves (one or two bars at every setting) is underdriven.

1. Switch FreeDV on, put the radio in its FreeDV mode (DATA-U / DATA-L, or USB / LSB), and connect a dummy load (or use a free frequency). Set the radio's power to the level you want to use.
2. On the FreeDV tab tick **Level test**. The PTT button now sends a steady 1500 Hz tone at the modem level (it stops by itself after 15 seconds; hold again for more).
3. Hold PTT and raise **Transmit level** until the **ALC** just starts to move, then go down one step. The **PO** meter should be close to the radio's set power (the tone reads about 2 dB
   higher than the voice signal, because a steady tone has less peak than the modem's signal for the same power).
4. Untick **Level test**. Do not leave it on: the PTT button would keep sending the tone.

A measured example (FT-991A in DATA-USB, set to 50 W, an external power meter, the test tone): -6 dB gave 32 W and the ALC barely moved; 0 dB gave 41 W with the ALC
just below its limit; +3 dB gave 42 W with the ALC at its limit. So 0 dB was the right level for that radio: the last 3 dB bought 1 W and started the overdrive. The default for a new
installation is -3 dB, a safe starting point; every radio and cable is different, so run the level test. Read the **external meter** if you have one: the app's PO bar saturates at the set power.

The level can go up to +3 dB. If the ALC is still not moving at +3 dB, raise the radio's own input level for the USB audio instead (on an FT-991A the sound-card playback volume on the
computer, or the radio's DATA/USB input gain menus) rather than turning up the level beyond that.

## Notes and limits

* One FreeDV setting for the whole radio: it applies to every listener, and changing it needs control of the radio, like changing the mode.
* It is refused while the radio is transmitting.
* Voice goes through the same server-side safeguards as ever (transmit permission, control, PTT heartbeat, time limit, lock). FreeDV changes what is
  sent to the radio, not who may transmit.
* Operating rules: FreeDV is plain unencrypted amateur digital voice; use it only where your licence and the band plan allow it.

## How much CPU does it need?

**Small PCs (an Intel Atom mini PC, for example):** RADE V1 receive needs about 40% of one core and the decoder has single steps of 60 to 100 ms. Such a PC also changes its CPU clock all the time (down to 480 MHz when it looks
idle), and RADE's short bursts of work then run slowly. Keep the clock up with the "performance" governor, permanently:

```bash
sudo tee /etc/systemd/system/radio-remote-cpu-performance.service >/dev/null <<'EOF'
[Unit]
Description=Radio Remote: CPU governor performance
After=sysinit.target

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/bin/sh -c "for g in /sys/devices/system/cpu/cpu*/cpufreq/scaling_governor; do echo performance > $g; done"

[Install]
WantedBy=multi-user.target
EOF
sudo systemctl daemon-reload && sudo systemctl enable --now radio-remote-cpu-performance.service
```

(undo: `sudo systemctl disable --now radio-remote-cpu-performance.service`). On the Atom tested the clock still swung between 1.0 and 1.9 GHz (its power limit), but the worst decoder step fell from 107 to 68 ms.
RADE V2 is lighter for the decoder on such a PC (about 30% of a core, worst step 19 ms). **Admin > Diagnostics** shows the receive timing: the time per 20 ms frame in total and the time inside the decoder; the total must stay under 20 ms on average.

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
