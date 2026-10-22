## Radio Remote

Control a Yaesu radio from a phone, tablet or computer: frequency, mode, filters, meters, memories, transmit with server-side safety and
remote audio, from a Raspberry Pi next to the radio. The **FT-991A** and the **FTDX101D** are verified on real radios, the **FTDX101MP** has the same commands as the D;
FTDX10 and FT-710 are included but **experimental** (written from the manuals, simulator-tested only).

### Get started: pick one

**A. Ready-made image (easiest).**
*With Raspberry Pi Imager 2.x:* in its application options choose **Content Repository**, enter a custom repository and paste
`https://github.com/dev-jmatias/YaesuFT991A-Remote/releases/latest/download/os-list.json`. Then choose your Pi and **Radio Remote for Yaesu**,
fill in Imager's own screens (hostname, user, password, Wi-Fi) and write the card. Put it in the Pi, power on, wait two minutes,
open `https://<hostname>.local`.
*If that does not work for you* (older Imager, or no repository option): download `image_…-radio-remote.img.xz` and
`first-boot-settings.ps1` below, write the image with Imager (*Choose OS > Use custom*; do not unzip it), then run
`pwsh -ExecutionPolicy Bypass -File first-boot-settings.ps1` (Windows, PowerShell 7) while the card's small *bootfs* volume is visible: it
asks for the hostname, user, password and Wi-Fi. Run it on a freshly written card, before its first boot.

**B. On top of Raspberry Pi OS Lite (64-bit).** Write the official OS with Imager (set user, password, Wi-Fi and SSH in its settings),
download `radio-remote-installer-….zip` below, unzip it, copy the folder to the Pi and run `sudo bash install-everything.sh`.

Then create the administrator (use your callsign), choose the radio and the serial port in **Admin > Config**, and (with a dummy load
first) switch transmitting on in **Admin > Config > Transmitting (PTT)**. Guide: [docs/INSTALL.md](https://github.com/dev-jmatias/YaesuFT991A-Remote/blob/main/docs/INSTALL.md).

### Updating

Administrators see a notice in the page when a newer release exists. On the Pi run
`sudo /opt/radio-remote/current/scripts/self_update.sh`: it downloads the new release, verifies the checksum, makes a backup and installs it,
and goes back by itself if the new version does not start. Settings and accounts are kept.
*Coming from 1.0.x?* Those versions have no update command: download `radio-remote-v….tar.gz` below, copy it to the Pi, then
`tar xzf radio-remote-v*.tar.gz && cd radio-remote && sudo ./update.sh` (or write the new image).

### The files on this page

| File | What it is |
|---|---|
| `image_…-radio-remote.img.xz` | the Raspberry Pi image (route A) |
| `os-list.json` | the repository file for Raspberry Pi Imager 2.x (route A; you paste its link, you do not download it) |
| `first-boot-settings.ps1` | sets hostname, user, password and Wi-Fi on a freshly written card (route A, without the repository) |
| `radio-remote-installer-….zip` | installer pack with the program, the manual and offline libraries (route B) |
| `radio-remote-v….tar.gz` | the program only: used by `self_update.sh`, or by hand to update a running Pi |
| `radio-remote-rade-linux-aarch64.tar.xz` | the optional RADE library (FreeDV neural modes V1 and V2) for a Raspberry Pi, installed with `install_rade.sh` (the PC build is `…-x86_64.tar.xz`) |
| `SHA256SUMS` | checksums of all of the above |

### What is new in 1.1.48

- **Looks.** Pick the colours of the app under **Account > Look**: **Slate** (the usual look), **Yaesu** (black body, white digits, blue labels and buttons, red mode badge and lamps, like the radio's own display) or **FT-710** (near-black with amber digits and highlights). The choice is remembered on that device only. The meters and the tuning dial keep their own colours.

### What is new in 1.1.47

- **TX power on the FT8/FT4 and SSTV screens.** Those screens hide the Levels panel, so the radio's transmit power could not be set while on them. A **TX Power** slider is now on both (the radio keeps its power for each band).

### What is new in 1.1.46

- **Free tuning on the SSTV screen** (for the ISS SSTV events and any other frequency): a **Frequency** box (MHz, kHz or Hz), **-** and **+** buttons with a step from 100 Hz to 10 kHz (taps in a row add up, so a Doppler shift can be followed by tapping), a **Mode** list (USB, LSB, FM, FM-N, AM and the data modes) and a **Squelch** slider in the FM modes. A new **70cm ISS 437.550 FM** preset joins the list. The screen remembers the frequency, mode and step of your last visit (also when you tuned with the radio's own knob) and returns to them; leaving it still puts the radio back as it was. SSTV is received in any mode, not only a data mode. The guide has a section for the ISS passes (start about 437.560, end about 437.540, FM mode, squelch open, step 2.5 or 5 kHz).

### What is new in 1.1.45

- **SSTV (slow-scan television).** A new **SSTV** screen receives pictures from the radio's audio, shows them line by line as they arrive, and saves them on the Pi (the last 60; open, download or delete them from the page). It also transmits a picture of your own: choose a photo, choose the mode, print your callsign and a message on it, and send it. Modes: Robot 36 and 72, Martin M1 and M2, Scottie S1, S2 and DX, PD50, PD90, PD120, PD160, PD180 and PD240. The codec is written from the published mode descriptions and checked against two independent SSTV programs in both directions. The receiver follows the transmitter's timing line by line (a sound card clock that runs a little fast or slow does no harm), corrects for a radio tuned up to about 100 Hz off, and needs roughly 12 dB signal-to-noise ratio to find the header. Sending goes through the usual protection (transmit permission, control of the radio, heartbeat, transmit time limit); a mode longer than the transmit time limit is refused before anything is keyed (the default of 120 s allows Robot, Martin, Scottie S1/S2 and PD50/PD90; an administrator can raise it). Opening the screen puts the radio on the last SSTV frequency in its data mode and switches reception on, leaving it switches reception off and puts the radio back; this device's sound is off or low meanwhile. Not tried on the air before this release: please report what you see. Details: [SSTV](https://github.com/dev-jmatias/YaesuFT991A-Remote/blob/main/docs/sstv.md).
- **Fewer tabs.** The phone and tablet tab bar is now **Radio / Filters / Audio & Levels / Digital**. Levels and Audio share one tab, and FreeDV, FT8/FT4 and SSTV share the **Digital** tab, chosen with a switch: a slim row at the top of the tab on a phone, and in the radio bar beside the radio's name on a tablet or computer. Retuning when entering or leaving a digital screen works as before, also when you go from one digital screen straight to another.

### What is new in 1.1.44

- **FreeDV stays on when you leave its tab.** Leaving the FreeDV tab no longer switches FreeDV off or retunes the radio (1.1.41 did): FreeDV stays on until you switch it off. Once it is off, leaving the tab puts the radio back on the mode and frequency it had when you opened the tab.
- **FT8/FT4 and FreeDV no longer share the radio.** Opening the FT8/FT4 tab switches FreeDV off if it is on (the FT8 tones would otherwise go through the FreeDV modem and be sent as garbage), and the Pi refuses to transmit FT8/FT4 while FreeDV is on.

### What is new in 1.1.43

- **Comment on every logged FT8/FT4 contact.** The log, the ADIF file and the QRZ.com and eQSL uploads now carry a comment with the mode and the reports exchanged, for example `FT8 Sent: +06 Rcvd: +01` (`FT4 ...` for FT4). Contacts logged earlier get it too when they are exported or uploaded. A new **Comment** box next to *Erase* lets you type your own for the next contact; it replaces the automatic one and empties after the contact is logged.

### What is new in 1.1.42

- **QRZ.com lookup.** On the **FreeDV** tab a callsign in the "On the air now" list (and "Last heard") is now a link that opens its QRZ.com page in a new tab. On the **FT8/FT4** tab the new **QRZ Lookup** button, next to *Reset QSO*, opens the page of the DX station (the one you selected by tapping a decode or a callsign on the waterfall, or typed). Nothing is sent to QRZ until you click. A portable or mobile suffix (`/P`, `/QRP`, `/M`) is dropped and for `EA8/DL1ABC` the real callsign is looked up.
- **Club Log removed from the FT8/FT4 upload accounts** (fields, automatic upload and the *Upload to Club Log* button): it needed an application key that was not available to this program. Uploads go to QRZ.com and eQSL; the ADIF export is unchanged.

### What is new in 1.1.41

- **FT8/FT4 and FreeDV tabs retune the radio.** Opening the **FT8/FT4** tab puts the radio in data-USB on the last frequency you used for that mode (the first time, the current band if it is an FT8/FT4 band, otherwise 20 m) and switches decoding on; leaving it switches decoding off and puts back the mode and frequency the radio had. Switching between **FT8** and **FT4** moves to the same band on the other mode's frequency. Leaving the **FreeDV** tab, if FreeDV was used, switches FreeDV off and puts back the mode and frequency from before. This happens only in the page that has control of the radio (on a wide screen FreeDV is a card, not a tab, so nothing changes there).
- **Sound on the FT8/FT4 tab.** This device's sound is switched off while the tab is open and back on afterwards if it was on (the decoder works on the Pi's own audio). **FT settings > Sound here** can make it *Low* instead.

### What is new in 1.1.40

- **FT8 and FT4.** A new **FT8/FT4** tab (beside FreeDV; on a wide screen the **FT8/FT4** button under the title) decodes the band, calls CQ and works stations from the browser, with the radio on the Pi. The Pi decodes every UTC slot twice (an early pass so you can answer a CQ before the next slot starts, then a final pass for the weak ones) and transmits through the usual PTT protection (control of the radio, transmit permission, heartbeat, time limit). The page shows the decodes with their SNR, DT and audio frequency, the country of each station (tablets and large screens), a waterfall with the callsigns marked (tap one to select it, again to call it), and follows the standard QSO sequence by itself: message 1 to 5 as the station answers, with the message being sent highlighted. Double-click or tap twice on any message to call its sender. The top bar shows the UTC time, whether the Pi's clock is synchronised and its offset, and the median DT of the stations heard (the check that your clock and audio delay are right). The codec is ft8_lib (MIT licence). Details: [FT8 and FT4](https://github.com/dev-jmatias/YaesuFT991A-Remote/blob/main/docs/ft8.md).
- **Log, ADIF and uploads.** Every contact is logged on the Pi (once, even with two pages open: only the page that has control logs) and exported as an ADIF file for any logging program. It can be uploaded to **QRZ.com, eQSL and Club Log**, by hand or as soon as a contact is logged; the accounts are kept on the Pi and never shown again or put in the settings backup. **Not yet checked against the live services** (needs real accounts), and Club Log needs an application key issued to this program. No LoTW. FT2 is not supported.
- **Needs** the Pi's clock to be right (a Raspberry Pi with internet access keeps it by itself) and the radio in data-USB (choosing a band in the tab sets it). Not tried on the air before this release: please report what you see (the DT shown on the tab, the decodes, the audio level).

### What is new in 1.1.39

- **Clear message in Safari, Firefox and on iPhone/iPad.** Those browsers cannot read a Bluetooth serial PTT button (such as the B01PTT) and no helper can change that, so Bind PTT now says so and names what works (Chrome or Edge on Windows, Linux or Mac, Chrome on Android) instead of "Nothing received", and no longer offers a helper download there. Keyboard-type and headset media-button devices still work in those browsers.

### What is new in 1.1.38

- **Ready-made helpers for external Bluetooth PTT buttons on Mac and Android.** No compiling any more: download **`radio-remote-ptt-mac.zip`** (a menu-bar app for macOS 13 or later, Apple silicon and Intel: unzip, open it once with right-click > Open because it is not notarised, allow Bluetooth, tick *Start at login*) or **`radio-remote-ptt-android.apk`** (install it, allow Bluetooth and notifications, choose the paired device, press Start; it starts again by itself after the phone restarts) from this release. Pair the speaker-mic or PTT button with the computer or phone first. In Radio Remote press **Bind PTT** and then the device's button. If the page cannot find the helper it now shows a download link for it. Windows and Linux in Chrome or Edge need no helper. Only for buttons that send their press as Bluetooth serial text (the B01PTT type): headset media buttons and keys work without any helper. Not possible on iPhone or iPad. If you tried the 1.1.37 test APK, uninstall it before installing this one (it was signed with another key).

### What is new in 1.1.37

- **One Bind PTT button for every kind of external PTT.** While you bind, the page listens to keys, headset media buttons, game controllers and Bluetooth serial buttons at once. Speaker-mics and PTT buttons that send their button as text over Bluetooth serial (the B01PTT type: `+PTT=P` pressed, `+PTT=R` released, true hold to talk) work on **Windows and Linux in Chrome or Edge** (press Bind PTT, then **Add Bluetooth serial device** once and pick the device), and on a **Mac** or an **Android phone** through a small helper that passes the button to the page over `127.0.0.1`: `tools/ptt-bridge-mac` (build with `swiftc`, see its README) and `tools/ptt-bridge-android` (`radio-remote-ptt.apk` attached to this release; pair the device, choose it, Start). The helper listens on this computer only and sends button state one way. A press never leaves the radio keyed if the device goes away. Not possible on iPhone or iPad. Tested with a B01PTT on Windows, Mac and Android.

### What is new in 1.1.36

- **Your callsign in every RADE V1 transmission.** The end-of-over frame now carries your callsign (up to 8 letters, digits or `/`), the same text channel the FreeDV program uses, so other stations' programs can show who was calling. Set it on the FreeDV tab (*FreeDV Reporter settings > Callsign sent with every transmission*); left empty, the Reporter callsign is used. When a received over ends with a callsign, the FreeDV tab shows **Last heard**. It needs the RADE library of this version: after updating, an administrator runs **Reinstall RADE** (the FreeDV tab says so when the library is older). The callsign is a single short frame, so it is read only on reasonably strong signals (reliable from about 12 dB SNR in 3 kHz), and RADE V2 has no text channel here. Checked against the original code of the FreeDV program (identical symbols and decoding) and with the real modem; not yet checked against the official FreeDV program on air. The code is a C port of the BSD-2 licensed text channel of Mooneer Salem's FreeDV code.

### What is new in 1.1.35

- **FreeDV (RADE) transmit fix.** The transmit chain handed the modem's output to a resampler even in the 10 ms blocks where the modem returned nothing (11 blocks out of 12 for RADE V1), and the resampler turned each of those empty calls into 2 stray samples. That added about 22 samples per 120 ms modem frame: repeated small time slips in the transmitted waveform, a likely cause of splatter and of receivers losing sync. Checked offline against the unmodified upstream RADE receiver on a perfect channel: the speech decoded from our transmit signal matched the original with similarity 0.39 before and 0.88 after (the upstream transmitter's own signal: 0.89), with 112 of 117 modem symbols valid instead of 104. The receive side decodes the same as the upstream receiver. Not yet tested on air.

### What is new in 1.1.34

- **PTT button for a Bluetooth headset, speaker-mic or keyboard.** On the **Audio** tab press **Bind PTT**, then press the button on your device; it is remembered in this browser. A key such as Space works as hold to talk; a media button (play/pause, next track...) works as press to start, press again to stop. It works with Listen on or off (the page plays an inaudible tone so the browser passes the media buttons to it). With a Bluetooth headset use another microphone as the input: when the headset's own microphone is in use the headset switches to call mode and its buttons are hidden from the browser. The screen lock, the PTT permission and the transmit time limit apply as for the on-screen button. Tested with a Beats Studio Pro on Windows Chrome (double press of the "b" button).
- **The tuning scale uses the app colours by default** (dark navy, blue-grey scale, yellow needle), same size and style as 1.1.33. Double-click the scale to cycle through the other looks (paper, white, green, amber).

### What is new in 1.1.33

- **New tuning scale.** A taller scale (92 px on large screens, 56 px on phones and tablets) in the style of an old meter face: cream paper with a lamp behind it, dark printed numbers (larger than before), a red needle with a shadow, a recessed frame with a contrasting border. It is still a straight horizontal scale. **Double-click it** to change the look: paper (default), old white, green or amber; each browser remembers its choice.
- **Scan only on the front panel.** The Filters tab no longer has the "Tuning" group with the radio's own Scan switch; use the **Scan** button next to Tune.

### What is new in 1.1.32

- **FreeDV transmit: end-of-over frame, and two queue fixes found in a code review. After updating, press Reinstall RADE (FreeDV tab, administrators) on every station that transmits.** Every over now ends with the end-of-over frame that the reference RADE transmitter sends (V1 and V2), so a receiver can tell that the transmission has ended; it needs the new RADE library, an older one sends none and works as before. The PTT-release drain no longer evicts queued tones when the queue is full (the flushed frames wait and join as it empties), and an over that lost frames to a full queue is no longer reported as "no holes": the log line counts them.

### What is new in 1.1.31

- **FreeDV now always uses USB (LSB below 10 MHz), like every other station.** The "use the radio's DATA-USB / DATA-LSB mode" option is gone from the FreeDV tab: a DATA mode starts with a 500 Hz filter and may put the signal away from where other stations expect it. Received in plain LSB, RADE V1 held sync for a whole recording on the Pi. The manual (`docs/freedv.md`) now recommends USB/LSB with a settings table.

### What is new in 1.1.30

- **The RADE V1 transmit signal is now band-limited. After updating, press Reinstall RADE (FreeDV tab, administrators) on every station that transmits.** The library has a transmit band-pass filter for V1 but leaves it off (RADE V2 has it on). Without it about **7 % of the V1 transmit power falls outside 700-2300 Hz**, up past 3 kHz, so the signal looked wider than it should on other stations' waterfalls and the radio's ALC and filters saw the skirts; with it 1.6 % is outside the band, the peak-to-average ratio is lower and the loopback decode locks better (98 % against 94 % in a test). The RADE library built from this release switches it on; the FreeDV tab says when the installed library is the older kind.
- **The radio-filter rule is now what RADE really needs.** RADE V1 occupies about 1500 Hz (audio 750-2250 Hz), V2 about 900 Hz. The DATA filter is centred on 1500 Hz, so 1700 Hz is the least that carries V1 and about 2000 Hz is comfortable; in USB/LSB about 2100 Hz is needed (the default 2400 Hz is fine). 1.1.27's "at least 2400 Hz, widen to 3000 Hz" asked for more than that. The FreeDV tab's warning and its Widen button, and the automatic widening (on by default), now open a too-narrow filter to the narrowest comfortable value (from 2000 Hz in DATA modes, 2400 Hz in USB/LSB) instead of the widest.
- **FreeDV manual:** check first that both stations use the **same RADE version**: V1 and V2 are different waveforms and do not decode each other at all (a recording of a V2 station gave 0 % lock with V1 and the other way round); and a section on the transmit band limit.

### What is new in 1.1.29

- **ALC warning on the FreeDV tab.** While you transmit with FreeDV on and the radio's ALC is above its mark (about 55 % of the meter), a message says the radio is compressing the FreeDV signal and tells you to lower the transmit level of the modem tones (or the radio's power) until the ALC just touches. RADE carries information in both amplitude and phase: a signal compressed by the radio's ALC cannot be decoded at the other station.
- **Decoder log for recordings.** A recording made with FreeDV on now has a **Decoder log** (Recordings > Decoder log, JSON): what the live FreeDV decoder believed second by second (share of the second in sync, SNR, tuning offset, slowest frame in ms). Send it with the raw radio audio when a station decodes badly: it shows whether the live decoder lost lock where the reference decoder does not.

### What is new in 1.1.28

- **Noise gate, on both sides** (Audio tab, under each equalizer graph; off until ticked): Thresh., Range, Hold and Rel. It turns the audio down by Range dB when the level stays under the threshold, waits the hold time before closing and opens again at once, with hysteresis so it does not flutter and word endings are not chopped. Receive takes the hiss out of the pauses between words (also decoded FreeDV speech); Transmit keeps the microphone's room noise out of the signal and out of the leveler. A readout shows how many dB it is taking off.
- **Saved equalizer sets:** **My sets…** saves the current bands (frequency, gain, Q) and the low-cut and high-cut under a name; Use puts a set on Receive or Transmit, Delete removes it, and your sets appear in the Preset list under *My sets*. Up to 20, shared by Receive and Transmit and by everyone connected, kept across restarts.

### What is new in 1.1.27

- **FreeDV in the DATA modes: the radio's filter width (important).** An FT-991A in DATA-L / DATA-U starts with a **500 Hz** receive filter, which passes only a third of RADE's carriers (about 750 to 2250 Hz): nothing could decode. Found by analysing raw recordings with the reference RADE decoder; after widening the filter to 3000 Hz the same radio locked on a station (42 of 49 seconds, SNR up to 8 dB) and the app's live decoding matched the reference.
  - The Filters tab now has a **Width** list for DATA-U and DATA-L (500 Hz up to 3000 Hz; code 10 = 500 Hz was measured, the others are from the manual).
  - The FreeDV tab shows a warning with a **Widen** button while FreeDV is on and the radio's filter is under 2400 Hz.
  - **Open the radio's filter automatically** (FreeDV tab, on by default, administrators can switch it off): a moment after FreeDV is switched on or the mode changes, a narrower filter is opened to the widest value up to 3000 Hz.
- **Equalizer:** a **FreeDV speech** preset (less bass, more presence at 1 to 4 kHz: decoded RADE speech is bass-heavy), and the **low-cut and high-cut now on the Receive graph too** (defaults 200 Hz and 6000 Hz, off until ticked).
- Recordings: **Download raw radio audio** (with FreeDV on) is how the width problem was found.
- Manual: the FreeDV chapter has a section on the radio's filter width.

### What is new in 1.1.26

- **Parametric equalizer** (Audio tab, Receive and Transmit): 5, 10 or 18 bands, each with its own frequency, gain and Q, like the parametric EQ of Thetis. Drag a node on the graph to set its frequency and gain, turn the mouse wheel over the graph to change the selected band's Q (or type values: Freq, Gain and Q are on one line), double-click a node to zero it. Presets (Voice, Bass cut, Treble cut, Bass boost, Treble boost) and Reset. It replaces the 8 fixed sliders of 1.1.25; saved settings are converted. The RX EQ and TX EQ buttons still switch each one on and off.
- **Transmit low-cut and high-cut** (24 dB per octave, 30 Hz to 1 kHz and 1 kHz to 12 kHz, off until ticked): type a frequency or drag the dashed L and H markers on the graph.
- **Transmit leveler and limiter** (off until ticked): the leveler brings your voice toward a target level (it only adds gain, up to a maximum, follows a louder voice at once and a quieter one slowly, and holds in pauses); the limiter keeps the peaks under a ceiling. They work on the speech only (a FreeDV signal is made from the leveled speech; a recording sent with Send is not touched), add no delay, and show the gain and the reduction while you transmit. Set the radio's own speech processor to off and watch the ALC.
- **Recordings keep the radio's raw audio when FreeDV is on** (Recordings > Download raw radio audio): the signal before decoding, to analyse why a station decodes badly.

### What is new in 1.1.25

- **8-band equalizer for what you hear (RX) and what you send (TX).** On the **Audio** tab: 63 Hz, 125, 250, 500, 1k, 2k, 4k and 8k, each -12 to +12 dB, with presets (Voice, Bass cut, Treble cut, Bass boost, Treble boost) and Reset. Two buttons on the Radio tab after Memories, **RX EQ** and **TX EQ**, switch each one on and off. One setting for everyone connected, kept across restarts. FreeDV's modem tones are never equalized: the receive equalizer works on the decoded speech and the transmit one on your voice before the modem. It adds about 10 ms of delay while on.
- **Recorder** (a block under *Mode* on the Radio tab, four buttons of one size): **REC** records what you hear (up to 2 minutes, before the equalizer, nothing while you transmit), **Play** plays the last recording in the browser, **Send** sends the last recording over the air like a **voice keyer** (PTT is keyed, the clip plays on the server's own clock, PTT is released by itself when it ends; the usual safeguards apply), and **Recordings** opens the last five with player, download, send and delete. A new recording replaces the oldest. With FreeDV on, each recording also keeps the **radio's own audio before decoding** (16 kHz): the file to analyse why a station decodes badly.
- **The lock switch beside PTT now locks the whole screen** of the device: every control, the top bar, the tabs and the keyboard shortcuts stop responding and only the padlock stays live (scrolling still works). Locking while a Tune is running stops the tune first.
- **Tuning step block:** the buttons (- step +, Tune, Scan, Memories, RX EQ, TX EQ) are on one line and one size on wide screens (VFO tools gives way), get a full-width row on tablets and a tidy grid on phones.

### What is new in 1.1.24

- **FreeDV level test.** A **Level test** checkbox on the FreeDV tab makes the PTT button send a steady 1500 Hz tone at the modem level (up to 15 seconds) instead of your voice, so the transmit level can be set by the radio's ALC and power meters without another station listening. Hold PTT and raise the level until the ALC just starts to move; the manual has the steps and a measured example.
- **The FreeDV transmit level can go up to +3 dB** (it stopped at 0 dB), and the **default for new installations is -3 dB** (it was -6). RADE's transmit signal is nearly constant in amplitude and is meant to be sent at close to the radio's full set power: a radio whose ALC barely moves is underdriven. Existing settings are not changed.
- Manual: advice from the FreeDV GUI manual and the RADE paper (radio processing off, wide filters, receive level not clipping, RADE V2 is experimental and few stations use it).

### What is new in 1.1.23

- **Scan between two frequencies (FT-991A).** The **Scan** button now opens a sheet: *Scan the memories* (the radio's own scan, as before) or a **range scan** with a start and a stop frequency, a step, the signal level to stop on (S1 to S9+20), how long to wait after a signal ends, and whether to carry on or stop the scan on a signal. The server steps the VFO from the start to the stop frequency, parks on a signal and loops back to the start. The radio only receives; the scan ends on PTT or Tune, on any change of frequency, mode, band or memory, when the operator leaves or loses control, or when the radio disconnects. While scanning the button reads **Stop scan**.
- **Manual:** the FT-991A menu chapter now says menu 033 CAT RTS must be **DISABLE** (with ENABLE the radio stays silent and the app shows "radio offline", seen after a factory reset), and lists the port menus 109, 048, 077 and 072 (all USB): with them on DATA the app keys the radio but no audio reaches the transmitter.

### What is new in 1.1.22

- **Scan button (FT-991A).** A **Scan** / **Stop scan** button beside Tune on the Radio tab starts and stops the radio's own scan (CAT `SC`): in memory mode it steps through the stored memory channels, otherwise it scans up from the VFO frequency. It is greyed out while transmitting, and follows the radio if the scan is ended on the radio itself. Verified on a real FT-991A.
- **Tune sends a carrier only.** With FreeDV on, the microphone and the modem tones were also sent to the radio while the antenna tuner transmitted; now nothing but the tuner's carrier goes out.
- **No more false "radio is no longer keyed" warnings.** The PTT watchdog could see the radio's TX state change during a normal release (before the release had finished) and log a warning on about a quarter to 40% of releases. It now waits for a key or un-key in progress.

### What is new in 1.1.21

- **FreeDV transmit: a second browser no longer restarts the modem.** A browser that is connected but muted still sends silent microphone frames; they closed the transmit gate every 20 ms, and the next frame of the operator transmitting cleared the queue and built a new modem. Now only the browser that started the transmission can end it.
- **FreeDV receive: no limiter in front of the RADE decoder.** With a receive boost (`audio.rx_gain_db` above 0) the soft limiter squashed the modem signal and lowered the decoder's signal-to-noise ratio. Level the audio with the radio's menu 073 (DATA OUT LEVEL) instead of a boost.
- **Very short FreeDV overs** now send their last tones when PTT is released (they were held back waiting for the 200 ms cushion, and the release waited in vain).
- **Dropped tone frames are counted** (`tx_dropped` in Admin > Diagnostics, and a warning in the log), to show if the sending side ever stalls for more than half a second.

### What is new in 1.1.20

- **FreeDV receive timing is split in two** (Admin > Diagnostics and the log): the time a 20 ms audio frame takes in total, and the time spent inside the decoder itself. On a small PC the total was far above the decoder's own time,
  and until now nothing said whether the decoder or the busy server was the slow part. The warning in the log now names both.
- **Advice for small PCs** in the FreeDV chapter of the manual: an Intel Atom changes its CPU clock down to 480 MHz when it looks idle, and RADE's short bursts of work then run slowly; the "performance" governor, set permanently with a small
  systemd unit (the exact commands are in the manual), helps. RADE V2 is lighter for the decoder on such a PC.

### What is new in 1.1.19

- **Squelch.** A **Squelch** slider under the mode buttons on the Radio tab, shown in FM, AM and C4FM modes and on the AIR band, and always in the Levels tab. It is the radio's own squelch level (`SQ`, 0 to 100, 0 = open).
  FT-991A: tested on a real radio. FTDX101D and FTDX101MP: MAIN and SUB sliders, written from the CAT manual and simulator-tested (not yet tried on a real FTDX101).
- The manual's radio-menu chapter (docs/radio-connection.md) now has the whole table for the FT-991A: the basics, the microphone-source menus the app manages (106, 045, 074 and 070), the receive-level menus (107 and 073) and the FreeDV DATA-mode menus.

### What is new in 1.1.18

- **FT-991A: choosing a memory after a band key now really retunes the radio.** Found on a real radio: pressing a band key while a memory is recalled puts the radio in *Memory Tune* (IF state 2), and an `MC`
  sent from there changes the channel number (and even reports "memory") without retuning, so the display showed the new channel while the audio stayed on the band key's frequency. Before every recall the app now checks the
  radio's state and, if it is in such a special memory state, presses V/M first (the Win4Yaesu manual names the same way out). The recall is also checked afterwards and retried once.
- **FreeDV on the FT-991A: an option to use the radio's DATA-USB / DATA-LSB mode** (FreeDV tab, administrators; off by default). FreeDV's own guide for the FT-991A uses the DATA modes, which bypass the radio's microphone chain (speech processor,
  equaliser, SSB transmit filter) that distorts RADE; the manual has the table of menus to set once on the radio (062, 064/065, 066/068, 070, 071, 072, 059). With the option on, FreeDV puts the radio in DATA-USB (DATA-LSB below 10 MHz).
- **The microphone button sets the right input in every mode** (FT-991A): REAR on menus 106 (SSB), 045 (AM), 074 (FM) and 070 (DATA IN SELECT) when you arm it; when you release it (or the last operator has left) 106, 045 and 074 go back to
  **MIC**, the front microphone, so local operation works in SSB, AM and FM. Menu 070 is left on REAR (it only matters in the DATA modes). A menu left on REAR by hand is also put back.
- The page asks for the memory's tone before editing (1.1.16) and the band / memory state is refreshed after a band key.

### What is new in 1.1.17

- **FreeDV receive no longer freezes the server on a small PC.** The RADE decoder takes 60 to 100 ms for every modem frame on an Intel Atom (about 64% of a core in the live service), and it ran inside the single loop that also serves
  the web page, the audio to the listeners and PTT, so all of those stalled for that long eight times a second. The decoding now runs in a worker thread (the library releases the Python lock), and switching FreeDV off or changing mode while a frame is being
  decoded is guarded, so the modem is never closed underneath a running decode. On a PC like that this should make the received audio and the page much smoother.
- Tested with a decoder that takes 120 ms per frame: the loop keeps running while it works.

### What is new in 1.1.16

- **Fix in the 1.1.15 memory editor: editing a channel that uses a tone no longer resets its tone frequency.** The radio's `MT` write copies the tone number VFO-A holds at that moment into the channel (found on a real FT-991A), so the
  1.1.15 editor changed the tone of a channel whenever its name or frequency was edited. If you edited tone channels with 1.1.15, check their tone frequency on the radio.
- **Tone frequency and DCS code per memory channel (FT-991A).** The editor now has a **tone frequency** list (the 50 CTCSS tones) and a **DCS code** list. Opening **Edit** on a channel with a tone reads the channel's own tone from the radio first
  (it is recalled for a moment and the radio put back). To store a tone, VFO-A is borrowed: it is set to the channel's mode, frequency, shift and tone, the channel is written, read back by recalling it, and VFO-A with its own settings and the V/M state
  are put back. A save takes about 30 seconds. Tested on a real FT-991A (two channels with different tones, rename keeps the tone, VFO-A and its 2 m settings restored).
- **Delete a memory channel (FT-991A).** **Delete channel** in the editor empties it: the channel is recalled (and checked) and then `AM;` empties it (found on a real FT-991A). Proved like a write: the channel must read as empty and no other channel may change.
- **AIR band (FT-991A).** The band buttons include **AIR** (the radio's AIR key, `BS14`): the radio goes to AM by itself and the page's tuning step becomes 25 kHz, going back to the previous step when you leave the band.
- **The microphone button works in AM and FM too.** It set only menu 106 (SSB MIC SELECT); it now sets 106, 045 (AM MIC SELECT) and 074 (FM MIC SELECT) together (FT-991A). DATA modes (menu 070) are untouched.
- **FreeDV: the end of an over is no longer cut off.** When you release PTT the last modem frame is completed with silence and the queued tones reach the radio before it is unkeyed (about half a second later). The FreeDV Reporter name for RADE V2 (`RADEV2`) is confirmed by another open-source client.

### What is new in 1.1.15

- **Memory editor (FT-991A, administrators).** **Memories > Add** stores a new channel and **Edit** changes one: name (up to 12 characters), frequency, mode, shift direction (simplex, plus, minus) and tone mode (off, CTCSS encode,
  CTCSS encode + decode, DCS). **Export** saves the list as a CSV file first (a backup). Every save asks for confirmation and is **proved**: the whole list is read before and after, the channel must read back as written, and if a write ever
  changed another channel, editing switches itself off and the message names the channels. It takes about 20 seconds. Tested on a real FT-991A. Not offered: the tone **frequency** (e.g. 71.9 Hz) and the repeater offset, which the radio's CAT
  commands cannot store per channel (set them in the radio's menus), and deleting a channel (do it on the radio).
- **Memory recall is checked.** Some radios sometimes fall into a memory check / memory tune state where selecting a memory changes nothing (the Win4Yaesu manual describes it; two friends had to repeat the change until it took). The recall is now checked
  against the radio's answer and, if it did not take, retried once (the V/M key is pressed first only when the radio reports that special state). What happened is written to the log (`memory recall: ...`).
- **A reinstalled RADE library is used.** The copy installed from the page (Admin > Config > RADE) lives in the data folder and now wins over the one in `/opt`, and the service finds it the next time FreeDV is switched on, with no restart. Before, a PC kept loading the
  older V1-only library, so RADE V2 never appeared in the mode list.
- **The FreeDV tab stays visible while RADE can still be installed**, so its **Install RADE** button is reachable (the tab used to vanish on a machine without the library).
- RADE V2 is listed as **RADE V2**; the "experimental" wording is gone from the page.
- **Safari before version 16:** the frequency readout now has a fallback size for browsers that do not understand container query units (a phone showed the frequency falling apart and the page shaking). This is the likely cause and could not be tested on such a phone.
- Admin > Diagnostics shows the new FreeDV timing counters from 1.1.14 (slowest step, holes in the transmitted tones); `freedv_probe.py` prints the slowest step too.

### What is new in 1.1.14

- **FreeDV is now RADE only: RADE V1 and the experimental RADE V2.** 1600, 700D and 700E (and the libcodec2 package) are removed. A configuration that still names one of them is read as RADE V1, so nothing needs editing.
  **RADE V2 is experimental upstream**: its signal may still change, V1 and V2 stations cannot decode each other, and most stations are on V1. It needs the RADE library from this release:
  if you update the program but keep an older library, V1 keeps working and the FreeDV tab tells you to press **Admin > Config > RADE > Reinstall RADE** to get V2. V2 locked across the whole ±300 Hz tried on ideal signals (V1 only within about ±50 Hz).
- **Transmit tones no longer get holes in them** (a likely cause of RADE transmissions that other stations could not decode): the modem delivers its tones in bursts and the sender wrote 20 ms of silence whenever its queue ran dry,
  so a burst that was a few milliseconds late punched a hole into the signal, which RADE cannot follow. The Pi now keeps about 0.2 s of tones queued before it starts (and again after a gap). The price: the last fraction of a second
  of an over may not be sent, so hold PTT a moment after the last word. After every over the log says `FreeDV transmit: … no holes` or `the tones had N hole(s)`, and **Admin > Diagnostics** shows the slowest receive and transmit step
  (it should stay well under 20 ms) and the number of holes.
- Power on from the page (**Power on radio**, PS1) is now verified on a real FT-991A.
- `scripts/freedv_probe.py` also prints the slowest single step of each mode.

### What is new in 1.1.13

- **FreeDV no longer freezes after PTT** (fix for a problem seen over WebSocket audio): the page sends microphone audio only while PTT is held and nothing at all after it is released, so the server never learned
  that the transmission had ended; the receiver stayed muted, the tuning scope frozen, and FreeDV had to be switched off and on. The server now ends the transmit state by itself when no microphone frame has
  arrived for half a second (and the receiver restarts, as in 1.1.12). The FreeDV Reporter's "transmitting" flag ends the same way.
- **System card in Admin > Config**: **Restart the service…** (the page reloads by itself when it is back) and **Reboot the system…** (asks for your password again, is refused while the radio transmits, and is
  written to the audit log). The page only drops a request file; a small root helper started by systemd (`radio-remote-power.path`, installed by `install.sh` and `update.sh`) reboots the machine and does nothing else.

### What is new in 1.1.12

- **FreeDV receives again by itself after every transmission.** After you released PTT the receiver went on with a decoder that had seen a gap, and on one PC it never found the signal again until FreeDV
  was switched off and on. The receiver now restarts its modem about 0.3 s after each transmission (keeping the tuning it had found), for all FreeDV modes.
- **Memories on an FT-991A that answers `IF` with the VFO frequency in memory mode**: the page kept showing the VFO frequency after a recall (only the MEM label changed). While in memory mode, the stored
  frequency and mode of the recalled channel (read from the radio's own memory list) are now shown when the radio only repeats the VFO. A radio that reports the channel's own frequency is trusted as before.
- **Installer for a Debian PC**: it installs `libnss-mdns` and `avahi-utils` (so the PC resolves `.local` names itself) and, at the end, warns when the name the web page answers to does not match the PC's own hostname,
  which is what other devices find on the network, with the commands to fix it. `docs/INSTALL.md` has a new section, "If the page does not open" (name problems, routers holding old names, the hosts file, changing the name later).
- No crash when the RADE library folder is not readable by the user who runs `freedv_probe.py`; it says RADE is not installed.

### What is new in 1.1.11

- The *experimental* banner is gone for the **FTDX101D** (tested on a real radio) and the **FTDX101MP** (the same commands as the D; its ID and its 5 to 200 W power range are the only differences, and no MP has been
  tried here: please report anything that differs). The FTDX10 and FT-710 keep the banner. Docs and the capability matrix say the same.

### What is new in 1.1.10

- **Yaesu FTDX101D tested on a real radio** (Raspberry Pi 4, USB). The FTDX101MP uses the same commands and is enabled the same way (transmit power 5 to 200 W); no MP has been available, so both still show the
  *experimental* banner. What works on the FTDX101D:
  - **MAIN and SUB receivers** shown as two panels, with **RX** (listen, on / off), **TX** (transmit on MAIN or SUB, so split works) and **MAIN | SUB** selector buttons that follow the radio's own keys, a
    **MAIN ↔ SUB** frequency swap and **Set MAIN / Set SUB frequency** side by side. The USB audio carries one receiver at a time and follows the receiver you listen to.
  - **Filter, noise and receiver controls for each receiver** (a MAIN | SUB switch in the Filters & DSP panel), RF and AF gain per receiver, **speech processor** (the radio uses 0 = off / 1 = on; the manual says 1 / 2), **AMC output level**, and a
    **CW** tab (keyer speed, pitch, keyer, break-in), an **Audio** tab (audio out level, audio in level, **transmit audio source DATA / USB**), **Tune** and **Memories** (read and recall only, nothing is ever written).
  - If the radio does not answer at all, the log now says which USB ports and rates were tried; the usual cause is the radio's **CAT RTS** menu being ON (set it to OFF). The notes for this radio are in `docs/08-other-radios.md`.
- **Gain bars show 0 to 100 %** instead of 0 to 255, on all radios.
- **The Memories button sits beside Tune**, and the VFO tools and tuning rows wrap and are centred, so nothing runs off a small screen (all radios).
- The COMP meter shows "off" on the FTDX101 while the speech processor is off (the radio reports a meaningless value then).

### What is new in 1.1.9

- **RADE is now part of the installations**: the ready-made image and the installer pack contain the RADE library, and `install.sh` downloads and installs it by itself
  (`--no-rade` skips it, `--rade-file FILE` uses a local copy). A new installation has the neural FreeDV mode ready; ordinary updates stay small and keep an installed library.
- **Debian 12 / 13 on a PC**: Radio Remote can be installed on a normal 64-bit Debian PC, not only on a Raspberry Pi (installation guide, route D). The RADE library is now also built for the PC
  (`radio-remote-rade-linux-x86_64.tar.xz`), inside a Debian 12 container so it runs on Debian 12 and newer; the **Install RADE** button works on both kinds of machine.
- **FreeDV Reporter** (qso.freedv.org), off by default: the FreeDV tab can announce your station while FreeDV is on (callsign, grid square, frequency, mode, whether you are transmitting; this
  is public on that site) and lists who is on the air, with a **Tune** button for each station. Settings in the FreeDV tab (administrators).

### What is new in 1.1.8

- **FreeDV tuning aid**: FreeDV only locks when the signal is close to where the modem expects it (about +-50 Hz for RADE and 700E, about +-150 Hz for 700D and 1600), which a radio dial
  cannot be set to by hand. The Pi now searches the tuning in software (up to +-450 Hz around the dial), holds it once the modem locks, and keeps it when you move the dial.
  The FreeDV tab shows a spectrum of the received audio with the modem's expected band, a level check (too loud / very low), whether it is locked and how far it is correcting,
  fine dial buttons (+-10 / +-100 Hz), **Centre the dial** and **Search again**. Needs someone listening (speaker icon). Tested against the real codec2 and RADE libraries.

### What is new in 1.1.7

- **Update now button** (Admin > Config > Updates): when a newer version is known, an administrator presses **Update now…**, types their password, and the Pi updates itself
  (download, SHA-256 check, backup, install, health check, automatic rollback), shows the log and reloads the page when it is done. It works through a small root helper
  (`radio-remote-update.path`) that `update.sh` installs, so **this first update to 1.1.7 still has to be done the usual way** (`self_update.sh`); the button is there for later versions.
- **Install RADE button** (Admin > Config > RADE, and the FreeDV tab): administrators install the RADE library from the web page on a 64-bit ARM Pi, with no command line and no restart
  (it shows **Reinstall RADE** once installed, and says why when it cannot be installed on a system).
- The radio goes back to its own microphone by itself when the last operator leaves was already in 1.1.6; nothing else changed there.

### What is new in 1.1.6

- **The radio goes back to its own microphone by itself**: about 15 seconds after the last operator connection has gone, the Pi sets menu 106 back to MIC, so the radio is
  not left on REAR (USB audio) after you close the page. A page reload or a short network drop does not trigger it; it is left alone while the radio transmits.
- **MIT licence** (Copyright 2026 Jorge Matias) added to the project, with notes on the third-party software it uses.

### What is new in 1.1.5

- **FreeDV RADE** (the newest, neural FreeDV mode) as an optional extra, verified receiving and transmitting on a real radio. It is a separate 24 MB library
  that is not part of normal updates: install it on the Pi with `sudo /opt/radio-remote/current/scripts/install_rade.sh` (it downloads the matching
  `radio-remote-rade-linux-aarch64.tar.xz` from this page, checks its checksum and restarts the service), then choose **RADE** in the FreeDV tab.
  On a Raspberry Pi 4 it needs about 20% of one core to receive and 8% to transmit. Built from the open-source `rade_c` (BSD licence).
- **FreeDV 1600** joins 700D and 700E in the mode list.
- A **FreeDV indicator** under the frequency (before the MEM tag): dark while FreeDV is on, green while it is locked on a signal.
- **Phones**: the channel cards are all the same size, and the channel editor no longer cuts off text.
- New tool: `scripts/freedv_probe.py` measures the CPU each FreeDV mode needs on your Pi.

### What is new in 1.1.4

- **FreeDV digital voice (700D and 700E)**, receive and transmit, done on the Pi with the codec2 library: a **FreeDV tab** beside Audio switches it on and
  off, shows whether the modem is locked on a signal (with its SNR), has editable **preset channels** (the usual FreeDV calling frequencies; LSB
  below 10 MHz, USB above) and a transmit-level setting. Listeners hear the decoded speech; with the microphone armed, your voice is sent as modem
  tones while you hold PTT, through the same server-side transmit safeguards. The installer and the update scripts install `libcodec2` for you;
  without it the tab simply does not appear. The codec is covered by automated tests; try receive first and transmit into a dummy load. Guide:
  [docs/freedv.md](https://github.com/dev-jmatias/YaesuFT991A-Remote/blob/main/docs/freedv.md).

### What is new in 1.1.3

- **WebSocket audio**: if WebRTC (UDP) cannot connect (hotel, office or mobile networks, some relayed paths), the page now switches to the same
  Opus audio over its normal HTTPS/WebSocket connection after about 9 seconds, listening and microphone included. Audio tab > Connection lets you
  force WebRTC or WebSocket. The server-side PTT gate is unchanged. Needs a browser with WebCodecs (current Chrome, Edge, Firefox, Safari 17+).
  Verified on a real FT-991A with UDP blocked.
- **Hands-free transmit**: hold HOLD TO TRANSMIT, slide up until it says "Locked on", let go; tap the button to stop. The time limit, the heartbeat and
  "page hidden" still end it.

### What is new in 1.1.2

- **Logbook link (Hamlib rigctl)**: switch it on in Admin > Config > Logbook link and a logbook on your home network (tested design for Log4OM:
  Hamlib, model *NET rigctl*, `<pi>.local:4532`) follows the radio and can change frequency and mode. It can never transmit or switch the
  radio off, only answers home-network addresses by default, and is off until you switch it on. Guide:
  [docs/logbook.md](https://github.com/dev-jmatias/YaesuFT991A-Remote/blob/main/docs/logbook.md).
- **The microphone button now also sets the radio's input**: armed = REAR (USB audio), off = back to MIC. The separate REAR/MIC buttons are gone.
- **Lock PTT** is a round padlock beside HOLD TO TRANSMIT.
- **Phones**: the top menu shows icons, RX/TX and the status lights fit on one line, VFO-A is on the frequency line.
- **Connection dot** replaces the "radio online" text (green online, yellow reconnecting, red offline); the MEM channel tag sits under the frequency.
- Documentation and install guide reorganised; the "what each radio supports" page is generated from the radio profiles.

### What is new in 1.1.1

- **Remote access with Tailscale now works out of the box**: after `sudo tailscale up`, run
  `sudo /opt/radio-remote/current/scripts/tailscale_setup.sh`. Until now the HTTPS front end answered only to `<hostname>.local`, so the
  Tailscale name (`<pi>.<tailnet>.ts.net`), the `100.x` address and any port number gave no answer. The script adds the Tailscale name,
  gets a real certificate for it, checks the configuration before using it, and keeps it across updates and reboots. Switch on *MagicDNS* and
  *HTTPS Certificates* in the Tailscale admin console first. Guide: [docs/tailscale.md](https://github.com/dev-jmatias/YaesuFT991A-Remote/blob/main/docs/tailscale.md).
- Raspberry Pi Imager's own screens (hostname, user, password, Wi-Fi) are confirmed to work with the image through the content repository
  link above.

### What is new in 1.1.0

- **Status lights** next to RX/TX: the preamp setting (IPO / AMP1 / AMP2), ATT, AGC (FAST / MID / SLOW / AUTO) and the antenna tuner; each can
  be hidden per device (Account > Display).
- **Lock PTT**: one switch that blocks the PTT button and TUNE on that device, so a stray touch cannot transmit.
- **SWR warning**: the SWR bar turns red and a message appears while transmitting above a limit you set (default 3:1).
- **Transmit switch in the page**: an administrator enables or disables transmitting in Admin > Config (password asked again, audited).
- **Callsign as user name** (including portable callsigns such as G4XYZ/P), and a *Show password* option on the sign-in page.
- **Settings backup and restore** in Admin > Config; a restore never changes the listener address, the storage folder or the transmit permission.
- **Memory channels**: fixed for radios that answer every channel read as "001" (found on a real FT-991A); the list is read in the background
  after connecting so it opens at once.
- **Update notice** and **`self_update.sh`** (see above), with checksums verified.
- Fixes: the settings script no longer needs Git for Windows; the DG-ID answer no longer fills the log with warnings.

### Safe by default

- **Transmitting is off** until an administrator switches it on. The image contains **no password, key or login**: the account stays locked
  until you set one in Imager's screens (or with the settings script). Root login stays disabled; your user has `sudo`.
- HTTPS uses a local certificate (Caddy): your browser asks you to trust it once.
- Remote operation must be allowed by your licence; set the radio's own TX time-out as a last backstop.

### Known limits

- Tested on a Raspberry Pi 4 with an FT-991A. The other radios are experimental. Prefer route B if you hit a problem with the image.
- The settings script's choices apply on a card's **first boot** only; to change them later write the card again (or change them over SSH).
- The image is not signed; check the SHA-256 in `SHA256SUMS`.
