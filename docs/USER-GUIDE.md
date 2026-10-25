# User guide

How to operate the radio from a phone, tablet or computer. For installing the system see the [installation guide](INSTALL.md);
for administering it (users, configuration, backups) see [operations](operations.md).

## 1. First visit

Open the address the installer printed (for example `https://radio.local`, or your Tailscale address) and sign in.
The very first visit asks you to create the **administrator** account. The administrator then creates a user for everyone else
under **Admin > Users** (roles below).

| Role | Can |
|---|---|
| **viewer** | watch the radio and listen; cannot control it or transmit |
| **operator** | take control, tune, change settings, use the microphone and transmit (if transmitting is enabled) |
| **admin** | everything, plus users, configuration, diagnostics, power on/off |

Tip: add the page to your phone's home screen ("Add to Home Screen" / "Install app") to get a full-screen app.
The **microphone only works over HTTPS** (a secure address). Listening works either way.

## 2. Who is in control

Many people can watch at once, but **only one controls the radio**. The top bar shows your state:

* **You have control** + *Release*: you are in charge. Press *Release* when you are done.
* **Name has control. You are watching** + *Request control*: ask for it. The holder has 10 s to hand it over or keep it; if they
  do not answer, control passes to you. **Trusted** users (set by the admin) take control immediately. An admin can force control
  (*Force*), which also un-keys the transmitter.
* Nobody can take control **while the radio is transmitting**.

## 3. The screen

Phones and tablets/computers share the same layout idea: a radio panel on top, four tabs at the bottom: **Radio**, **Filters**, **Audio & Levels** and **Digital** (FreeDV, FT8/FT4 and SSTV, chosen with a switch: a slim row at the top of the tab on a phone, and in the bar beside the radio's name on a tablet or computer).

**Top bar:** *Full* (full screen), *Help* (this manual), *Admin* (administrators), *Account* (your password), *Sign out*, and for administrators
*Power off*. **Radio bar:** a green dot = the radio answers, the radio name, and the control status. The **Help** button opens this manual
from the Pi itself (no internet needed).

### Radio tab

* **VFO-A panel.** Big frequency (tap a digit to make it the tuning step; on a computer the mouse wheel over a digit tunes by that digit),
  mode and band, the **signal (S) meter**, and below it the small **power, SWR, ALC and COMP** meters. ALC and COMP are shown as 0-100 %,
  red above 50 %. A **TX** badge appears while transmitting. Top right: the **speaker** and **microphone** switches (see Audio).
  Next to RX / TX / MEM, small **status lights** show the radio's receive path: one light for the **preamp** that names the setting in use
  (**IPO**, **AMP1** or **AMP2**), **ATT** (attenuator), one light for the **AGC** that names its setting (**AGC FAST**, **AGC MID**,
  **AGC SLOW** or **AGC AUTO**; it is dark and reads **AGC OFF** when the AGC is off) and **TUNER** (antenna tuner).
  **Green = active, dark = inactive**; TUNER blinks amber while a tune runs. They
  only show what the radio reports (change the settings in *Filters & DSP > Receiver* and *Transmit*). On a phone they sit on a row of
  their own under VFO-A. A radio profile without one of these functions simply does not show that light. For a cleaner header, switch
  off the lights you do not need in **Account > Display** (this device only). **Account > Look** changes the colours of the whole app: *Slate* (the default), *Yaesu* (black body, white digits, blue labels, red mode badge, like the radio's own display) or *FT-710* (near-black with amber digits). **Lines** sets how bright the borders around panels, tabs and buttons are (Soft, Normal or Bright). Both are remembered on that device only.
  **SWR warning:** while you transmit at or above the SWR limit (default 3:1, set by an administrator in Config > Meters, 0 = off) the
  SWR bar turns red and a line under the meters says *High SWR: 3.4:1 - check the antenna and the cable*; it stays for 5 seconds after
  the transmission. The FT-991A reports only a raw SWR value, so its ratio is an estimate ("about").
* **VFO-B** is the attached row under VFO-A: its mode, band and frequency. In split operation the transmit VFO gets the red TX badge.
  A purple **MEM 005** badge means the radio is in memory mode.
* **Tuning scale.** Swipe or drag sideways (a quick flick keeps gliding). The numbers on the scale follow the tuning step; **x10**
  makes every move ten times larger. Double-click the scale to change its look (the app colours, old paper, white, green, amber); the browser remembers it.
* **Tuning step** (-, list, +), **Tune** (see 5): the antenna tuner, and **Memories** (see 4), side by side on one centred row.
* **VFO tools:** *A -> B*, *B -> A*, *A <-> B* (swap), *Split* (receive on A, transmit on B), *Quick split* and **Set B** (type a frequency in MHz).
  The rows wrap and are centred, so they fit a small screen.
* **FTDX101D / MP (two receivers):** the panels are **MAIN** and **SUB**. The VFO tools row is **MAIN ↔ SUB** (swaps the two frequencies) and, for each receiver,
  its name button (makes the radio's dial and keys operate it, like the radio's MAIN / SUB keys), **RX** (listen to it, on / off, at least one stays on) and **TX** (transmit
  on it). Listen to one and transmit on the other for split. **Set MAIN frequency** and **Set SUB frequency** sit side by side in Band select. The audio you hear in the browser
  is one receiver at a time: with only one receiver listening it follows that one.
* **Band select** and **Set frequency** (type MHz, e.g. `14.195`, or Hz, e.g. `14195000`), **Mode** buttons.
* **PTT** (hold to transmit), always at the bottom, with the round **Lock PTT** button beside it.

### Filters, Audio & Levels tabs

* **Filters & DSP** is grouped in tabs: *Filter* (width, narrow, IF shift, contour), *Noise* (manual notch, auto notch, DNR, noise blanker),
  *Receiver* (IPO/preamp, attenuator, AGC, RIT/XIT with the clarifier offset bar), *Transmit* (processor, monitor, tuner).
  Each row is a button, plus a bar where the function has a value. Double-click the clarifier bar to clear the offset.
  On the **FTDX101D** the panel starts with a **MAIN | SUB** switch (the filter, noise and receiver controls then act on that receiver), *Transmit* also has the
  processor level and the **AMC output level** (the radio's PROC / PITCH knob), and there are two more tabs: *Audio* (audio out level to the app, audio in level
  from the app) and *CW* (keyer speed, pitch, keyer, break-in).
* **Levels:** RF gain, microphone gain, transmit power; the gain bars show 0-100 %. The FTDX101D also has AF gain and RF gain for MAIN and SUB.
* **Audio:** volume, meters, device choice, and the raw radio meter values (see 6).

PTT stays at the bottom on every tab so you can adjust a level while transmitting.

## 4. Memory channels

**Squelch (FT-991A, FTDX101D and MP):** a **Squelch** slider appears under the mode buttons in FM, AM and C4FM and on the AIR band (and is always in the Audio & Levels tab); on the FTDX101 there is one for MAIN and one for SUB. 0 is open (no squelch); raise it until the noise just closes. It is the radio's own squelch level (`SQ`), the same as its SQL knob. On the FTDX101 it is written from the manual and simulator-tested; the FT-991A one was tested on a real radio.

**AIR band (FT-991A):** the band buttons include **AIR** (the radio's AIR key, `BS14`): the radio goes to the airband in AM by itself and the page's tuning step switches to 25 kHz, the airband channel
spacing, and goes back to the previous step when you leave the band.

**Memories** (beside **Tune**; FT-991A and FTDX101D) lists the channels stored in the radio (001-099): number, name, frequency, mode. Type in the box to filter. Tap a
channel to recall it. **Back to VFO** returns to the VFO. **Re-read** reads the list again after you changed memories on the radio.
**Export** saves the list as a CSV file (a backup, or to open in a spreadsheet). The list is read in the background a few seconds after the
radio connects, so it opens at once; it is read again whenever the radio reconnects.

**Adding, editing and deleting a channel (FT-991A, administrators):** **Add** stores a new channel and **Edit** (beside each row) changes one: name (up to 12 characters), frequency, mode,
**shift direction** (simplex, plus, minus), **tone mode** (off, CTCSS encode, CTCSS encode + decode, DCS) and the **tone frequency** (the 50 CTCSS tones) or **DCS code**. Opening **Edit** on a channel
with a tone reads that channel's own tone from the radio first (the radio is recalled to that channel for a moment and put back), so changing a name does not change its tone. **Delete channel** empties it.
Saving or deleting asks for confirmation and takes 20 to 30 seconds: the program writes the channel and then reads **all** channels back, and tells you if the radio did not store it exactly as sent.
To store a tone the radio's VFO-A is borrowed for a moment (it is set to the channel's frequency, mode, shift and tone, the channel is written, and VFO-A, its own tone settings and the V/M state are put
back as they were). If a write or a delete ever changed a channel other than the one you chose, editing switches itself off (until the service is restarted) and the message names the channels, so put
them back from your **Export** file. **Export first** if the list matters to you. One limit remains: the **repeater offset** (for example 600 kHz) is a radio menu setting, not stored per channel, so set it
in the radio's menus; the page only chooses the direction.

## 5. Transmitting

**Transmitting is disabled until the owner enables it**: an administrator opens **Admin > Config > Transmitting (PTT)**, chooses
*Enable transmitting…* and types their password again (or sets `allow_ptt = true` in the configuration file on the Pi). The button then
says *Hold to transmit*. An administrator can switch it off again at any time, which also stops a transmission in progress.
Rules that always apply:

* **Hold** the button to transmit; let go to stop. The server un-keys on any hiccup: lost connection, lost control, time limit, shutdown.
* **Hands-free transmit:** while you hold the button, slide your finger (or the mouse) **up** until the message says "Locked on", then let go.
  The radio keeps transmitting; the button shows a dashed outline and "TRANSMITTING - TAP TO STOP". Tap it once to stop. The safeguards still
  apply: the time limit ends it, and it also ends when the page is hidden (screen locked, another app or tab), when control is lost or the
  connection drops. Use it only when you are sure of the band, the antenna and your power.
* Only the client with control can transmit; microphone audio reaches the radio only while that client is keyed.
* **The microphone button** (top right of the frequency panel) does two things at once: it arms your microphone in the browser and sets the radio's input to **REAR**, so the radio takes its audio from the USB port. On an FT-991A that is menu 106 (SSB MIC SELECT), 045 (AM MIC SELECT), 074 (FM MIC SELECT) and 070 (DATA IN SELECT), all together, so it works in every mode. Switching it off puts 106, 045 and 074 back to **MIC** (the front microphone), which is what local operation needs; so does the automatic return when the last operator has left (a menu left on REAR by hand is put back too). Menu 070 is left on REAR: it only matters in the DATA modes, and REAR is how digital programs and the FreeDV guide use it. It is locked while the radio is transmitting. If you close the page without switching it off, the Pi puts the radio back on **MIC** by itself about 15 seconds after the last operator (or administrator) connection has gone; a page reload or a short network drop does not trigger it, and a listen-only account does not keep it on REAR. It is left alone while the radio is transmitting.
* **Tune** makes the radio transmit a carrier for a few seconds while the antenna tuner matches. Use an antenna or dummy load.
* **Lock** (the round padlock beside *Hold to transmit*) locks the **whole screen** of **this device**, so nothing can be changed or transmitted by accident: the frequency, the buttons, the tabs and the keyboard shortcuts stop responding, and only the padlock stays live (scrolling still works). It also prevents accidental transmissions:
  while it is on (amber) the PTT button and **Tune** cannot be used. A transmission that is already running can still be
  released, and switching the lock on while holding PTT lets go at once. The choice is remembered in this browser; other devices have
  their own switch. It is a convenience, not a security setting: the server's own safeguards (permission, control, heartbeat, time
  limit) work as before.
* A time limit (default 120 s) stops over-long transmissions.

## 6. Audio

* **Speaker icon = Listen** (teal = on). **Microphone icon = arm the microphone** (teal = armed; it transmits only while PTT is held).
  Each is switched on and off **only by you**: changing tabs, locking the screen or using another app does not stop them, and the
  connection re-establishes itself if it drops. (Some phone browsers still suspend a page in the background; keep the screen on with
  *Awake* if yours does.)
* **Audio tab:** *Volume*; the **RX audio**, **TX audio** and **Your mic** meters; **Speaker / output** and **Microphone / input**: pick a
  Bluetooth headset, car hands-free kit or any other device (remembered per browser; Safari/iPhone cannot choose the output, use the
  system sound settings there); **To radio** counts audio frames delivered to the radio while you transmit.
* **PTT button** (Bluetooth headset or speaker-mic, or a key): press **Bind PTT**, then press the button on your device; it is
  remembered in this browser. A key such as Space works as **hold to talk**. A media button (play/pause, next track and similar) sends one
  event per press, so it works as **press to start, press again to stop**; some headsets only send one kind of press (Beats: a **double
  press** is "next track"), so press the button the way you will use it while learning. It works with Listen on or off: the page plays a
  silent sound so the browser keeps the media buttons for it, and after reopening the page the first tap or key press re-arms it.
  **Bluetooth headsets:** use the computer's own microphone (or any microphone that is not the headset's) as the **Microphone / input**
  and the headset only as the speaker. When the headset's own microphone is in use, Windows switches the headset to its phone-call mode, the
  button becomes a call button and the browser never receives it (tested with a Beats Studio Pro on Windows Chrome: the double press of the
  "b" button works with another microphone and not with the headset's). After an update the page can take a moment, or one extra reload, to use the new version. If nothing is received within 15 seconds the page says so; check that the headset is the connected device and close other media players. The screen lock,
  the PTT permission and the transmit time limit apply to it as to the screen button.
* **What Bind PTT can pick up, and devices that do not work.** A web page only receives what the browser passes on: ordinary keys (also
  Android hardware keys that have only a number), headset media buttons (play/pause, next track...), **game-controller buttons** and, in Chrome or
  Edge on a computer, **Bluetooth serial buttons** (see the next point). While you bind, the line **Received:** shows what arrived, even if it
  cannot be used.
* **Bluetooth speaker-mics and PTT buttons that are not keyboards.** Many of them (the kind sold for Zello and similar apps) show up on the
  computer as a Bluetooth headset for the audio, and send the PTT button over a separate **serial** connection: the B01 type sends the text
  `+PTT=P` when the button goes down and `+PTT=R` when it goes up. Pair the device with the computer first, then on the Audio tab press
  **Bind PTT**, then **Add Bluetooth serial device** (it appears while binding), pick the device's port in Chrome's list (named after the device, for example *B01PTT-F5D0*) and press its PTT button
  within 30 seconds. Once the port has been picked, a plain **Bind PTT** hears it too. The button then gives true **hold to talk**, the page reopens the port by itself when it is loaded or the device is switched
  on again, and a device that disappears while the button is down releases the radio. Needs Chrome or Edge 117 or later (not Safari or Firefox) on
  Windows or Linux, and only one program can have the port open. **On a Mac Chrome cannot open such a port**: use the small **Radio Remote PTT** helper instead: download `radio-remote-ptt-mac.zip` from the latest release, unzip it, open it once (right-click > Open, allow Bluetooth) and tick *Start at login* in its menu-bar icon. On **Android** install `radio-remote-ptt-android.apk` from the same release, choose the paired device and press Start. Pair the device with the computer or phone first, then press **Bind PTT** on the Audio tab and the device's button: the page finds the helper by itself (and shows a download link if it cannot). The helper listens on this computer or phone only, and the page reconnects to it by itself. iPhone and iPad cannot do this. If the port is read but the messages are not `+PTT=P` / `+PTT=R`, the page shows what
  it received: send that line to the developer and it can often be added.
* **Android PoC radios and some Android speaker-mics** send the PTT as a private Android signal that no browser receives, so **Nothing received**
  is the answer for them. Things to try: a setting of the device that makes its button act as a standard one (a headset or media button, or a keyboard
  mode), or on Android an app that remaps a hardware button to a media button.
* **Too quiet?** Raise the radio's USB output level menu (FT-991A: menu 107 SSB OUT LEVEL, 073 DATA, 046 AM, 075 FM, 054 CW, 099 RTTY) rather
  than boosting in software; see [audio](04-audio.md).
* **Radio meters (raw)** shows the radio's raw 0-255 ALC, COMP, power, SWR and S values and the ALC/COMP peak of the last transmission,
  which is what to read when calibrating the percentage scales.

### Equalizer

The **Audio** tab has a **parametric equalizer** for what you hear (**Receive**) and for what you send (**Transmit**), like the one in Thetis: **5, 10 or 18 bands**, each with its own **frequency**, **gain** (-12 to +12 dB) and **Q** (width, 0.3 to 10).
**Drag a node** on the graph to set its frequency (left/right) and gain (up/down); the **mouse wheel** over the graph changes the selected band's Q (on a phone use the Q box); **double-click** a node to zero it; the boxes under the graph give exact values
for the selected band. The curve shows the combined response. **Bands** changes the number of bands (it starts again flat), **Preset** (Voice, Bass cut, Treble cut, Bass boost, Treble boost, and **FreeDV speech**: less bass, more presence at 1 to 4 kHz, which makes decoded RADE speech clearer) sets the gains at your band frequencies, **Reset** zeroes all gains.
It is one setting for the whole radio, shared by everyone connected, and it is kept across restarts. Both sides have a **Low-cut** and a **High-cut** (24 dB per octave, off until you tick them; defaults 150 Hz and 2800 Hz for Transmit, 200 Hz and 6000 Hz for Receive): tick the box and type a frequency, or drag the dashed **L** and **H** markers on the graph. They keep unwanted rumble and hiss out of the signal and out of the radio's own filter edges. The selected band's Freq, Gain and Q are on one line under the graph. Two buttons on the Radio tab, **RX EQ** and **TX EQ** (after Memories), switch each equalizer (and the cuts) on and off so you can compare.
It adds about 10 ms of delay while on; very narrow bands below about 150 Hz come out a little wider and weaker than drawn. With FreeDV the equalizer works on the speech only: the modem tones are never equalized.

#### Noise gate (Receive and Transmit)

Each side has a **Noise gate** under the graph (off until ticked). It turns the audio down by **Range** dB (default 30) when the level stays under **Thresh.** (default -50 dB), waits **Hold** ms (default 150) before it starts to close and closes over **Rel.** ms (default 250), and opens again at once when the level comes back,
so word endings are not chopped. **Receive** takes the hiss out of the pauses between words (it also works on decoded FreeDV speech); **Transmit** keeps the microphone's room noise out of the signal and out of the leveler, which would otherwise raise it. A readout shows how many dB the gate is taking off while it is closed.
Set **Thresh.** just above the noise in the pauses and below your quiet words, then listen for chopped word beginnings and lower it if you hear them.

#### Saved equalizer sets

**My sets…** (beside Reset) saves the current bands (frequency, gain and Q) and the low-cut and high-cut under a name; **Use** puts a saved set on the Receive or the Transmit equalizer, and **Delete** removes it. Your sets also appear in the **Preset** list under *My sets*. Up to 20 sets are kept; they are shared by Receive and Transmit and by everyone connected, and kept across restarts.

#### Transmit leveler and limiter

Under the Transmit graph, two more stages for your voice, after the noise gate (they are shared and kept like the equalizer, and they work on the speech only: a FreeDV signal is made from the leveled speech, and a recording sent with *Send* is not touched):

* **Leveler** brings your voice toward a **Target** level (default -18 dB). It only adds gain, up to **Max** (default 12 dB), follows a louder voice at once and a quieter one slowly (**Decay**, default 300 ms), and holds its gain in pauses instead of boosting the room noise.
  It evens out a voice that moves around the microphone, so the average power is higher for the same peaks. The readout beside it shows the gain it is adding while you transmit.
* **Limiter** keeps the peaks under a **Ceiling** (default -3 dB) and lets go with the **Rel.**(ease) time (default 100 ms); the readout shows how many dB it is reducing. Together with the leveler it is the ALC of a good SSB transmitter: set the radio's own
  speech processor to **off**, watch the radio's **ALC** and the power on a dummy load, and lower the Target or the Ceiling if the ALC goes over its mark.

Both are off until you tick them. Start with the leveler's defaults and the limiter at about -3 dB.

### Recorder (REC, Play, Send, Recordings)

A **Recorder** block under *Mode* on the Radio tab has four buttons of one size:

* **REC** records what you hear (the decoded speech when FreeDV is on, otherwise the radio's audio, before the equalizer; nothing is recorded while you transmit), up to two minutes. The button shows the elapsed time; press it again to stop.
  The Pi keeps the **last five** clips: a new recording replaces the oldest.
* **Play** plays the last recording here, in your browser (it is not sent to the radio).
* **Send** sends the last recording over the air like a **voice keyer**: PTT is keyed, the clip plays, and PTT is released by itself when it ends. It uses the same safeguards as any transmission (control, heartbeat,
  time limit, the screen lock) and does not need your microphone. Tap the PTT button or **Stop** to end it earlier. Use it to give another station an audio report: send only audio you may retransmit, such as
  their own signal back to them, and give your callsign.
* **Recordings** opens the last five, each with a player, **Download** (a WAV file), **Send** and **Delete**.
  When FreeDV is on, each recording also keeps the **radio's own audio before decoding** (the modem signal, 16 kHz): **Download raw radio audio** gives that file. It is what to analyse when a station decodes
  badly (sync, SNR, frequency offset, level, gaps): the speech alone cannot show why.

## 7. Remote access

* At home: `https://<pi-name>.local`. Your device must trust the Pi's certificate once (see [security and remote access](06-security-remote.md)).
* **FreeDV** digital voice (RADE V1 and RADE V2): the **Digital** tab, **FreeDV** switch, switches it on and off and has preset channels; the Pi does the encoding and decoding. See [FreeDV](freedv.md).
* **FT8 and FT4**: the **Digital** tab, **FT8/FT4** switch (on the all-in-one desktop layout the FT8/FT4 button under the title) decodes the band, calls and answers with the standard sequence, keeps an ADIF log and can upload it to QRZ, eQSL and Club Log. See [FT8 and FT4](ft8.md).
* **CW** (Morse): the **Digital** tab, **CW** switch, decodes the Morse in the radio's audio into text, with a spectrum, the pitch and the speed it found and sends what you type through the radio's keyer (see `cw.md`).
* **Menu** (the last tab): the radio's own menu, every item the CAT manual lists (the FT-991A: 001 to 153), with search, groups, undo, and backup / restore of the whole menu (see `menu.md`). Items that could cut the link or change the keying are read only.
* **RTTY** (radio teletype): the **Digital** tab, **RTTY** switch, decodes the two tones into text, sends what you type as Baudot audio tones (45.45 baud / 170 Hz by default) and logs contacts (see `rtty.md`).
* **SSTV** (slow-scan television): the **Digital** tab, **SSTV** switch, receives pictures from the radio's audio line by line and transmits a picture of your own with your callsign printed on it. See [SSTV](sstv.md).
* A logbook on another computer (Log4OM and others) can follow and tune the radio over the home network: [logbook link](logbook.md).
* Away from home: **Tailscale** ([guide](tailscale.md)). No port is opened on your router.

## 8. Administrators

**Admin** has these tabs: **Users** (create, roles, trusted, reset password), **Clients** (connected devices; disconnect one), **Config** (radio model,
serial port, audio devices and gains, meter calibration, SWR warning limit, time limits, the transmit switch and **Settings backup**:
download the settings to a file and restore them later; **Updates**: whether a newer release exists and how to install it; some changes need a
restart), **Diagnostics** (system, radio link, audio and the recent log)
and **Audit** (who did what). *Power off* switches the radio to
standby; when the radio is off, the offline banner shows **Power on radio**. Details: [operations](operations.md).

## 9. If something is wrong

| Symptom | Try |
|---|---|
| Radio offline | cable, radio on, **Admin > Diagnostics**; see [troubleshooting](troubleshooting.md) |
| No sound | tap the speaker icon; Audio tab status; the radio's USB OUT LEVEL menu |
| Microphone silent | tap the microphone icon and allow it in the browser; HTTPS needed; the microphone button is on (radio menu 106 = REAR); check **To radio** while keyed |
| Cannot transmit | transmitting enabled? you have control? not a viewer account? |
| Page looks old after an update | press Ctrl+F5, or the **Reload now** bar |
