# JS8

The **JS8** switch on the **Digital** tab is keyboard-to-keyboard messaging with JS8 (JS8Call's weak-signal mode, an FT8-style signal that carries free text): call CQ, send a heartbeat, talk to stations you hear, ask for signal reports, all from the browser. It receives and transmits.

## How it works (and why it needs an install)

JS8's message coding (a word dictionary and error-correcting tables) cannot be written into this MIT-licensed program, so the JS8 modem is **JS8Call-improved**, a separate free program (GPL-3.0, from <https://github.com/JS8Call-improved/JS8Call-improved>). It is **not** part of Radio Remote: an administrator downloads it from its own release page with a button. It then runs on the Pi without a screen, in its own service, and Radio Remote only talks to it through its documented network interface (on the Pi itself, localhost) and connects its audio to the radio:

* **Receive:** the radio's audio goes into a virtual sound card (the ALSA "loopback") that JS8Call listens to.
* **Transmit:** JS8Call's audio comes out of the other end of the loopback and is sent to the radio through the usual transmit protection (see below).
* JS8Call never touches the radio: it has no radio control, no PTT, and its own internet reporting (PSK Reporter, APRS) is switched off.

## First time (administrator)

1. Open **Digital**, then **JS8**. A box says what is missing.
2. **Prepare the system** (once): installs a few packages (the libraries JS8Call's toolkit needs even without a screen, and PulseAudio, started only privately for JS8Call) and loads the loopback sound card at every boot. It runs as root through a small helper, takes a few minutes, and its output is shown in the box.
3. **Install JS8Call** (once): downloads it (about 60 MB) from its release page and unpacks it into the Pi's data folder.
4. Set your callsign and grid in the *FT settings* of the FT8/FT4 screen (JS8 uses the same ones).

Supported: a Raspberry Pi 4 or newer, or a 64-bit Linux PC. The Pi uses about a quarter of one core while JS8 is on.

## Using it

* Entering the screen puts the radio on the last JS8 frequency (14.078 MHz the first time) in DATA-U and switches JS8 on when everything is ready; leaving switches it off and puts the radio back as it was. Pick a band from the list (the usual JS8 frequencies), type a frequency, or step with **-** and **+**.
* **Speed:** Slow (30 s), Normal (15 s), Fast (10 s), Turbo (6 s) and JS8 60 (4 s, experimental). Everybody on a frequency must use the same speed; Normal is the usual one.
* **TX offset:** where your signal sits in the radio's audio (300 to 2900 Hz). Pick a place where nobody else is: the **Band activity** list shows the offset of everything decoded.
* **Band activity** lists everything decoded; **Messages** lists the messages directed at someone (yours are highlighted); **heard stations** are buttons: tap one to talk to it (its callsign goes into *To* and into the log form).
* **Send:** type a message, with a station in *To* or not, and press **Send**. Buttons: **CQ**, **Heartbeat** (tells the stations listening that you are here), and for the selected station **SNR?**, **HW CPY?** and **73**. A message waits for the next time slot and long ones go out in several frames. **Halt TX** stops at once.
* **Log a contact** works like on the CW and RTTY screens (same log, ADIF mode MFSK with submode JS8).

## Transmit protection

JS8Call only transmits what you ask for: when JS8Call reports that it wants to key the radio, Radio Remote keys it **only if** you pressed Send a moment ago from a page that is still connected. Anything else (for example JS8Call answering by itself) is stopped at once and reported. As for every transmission you need control of the radio, transmitting must be allowed (`safety.allow_ptt`), and the transmit time limit, the heartbeat and the release when your page goes away all apply. The radio must be in USB, LSB or a data mode. FreeDV, SSTV, CW and RTTY cannot be used at the same time.

**TX level, dB** (JS8 settings, administrators) sets the level of JS8Call's audio sent to the radio; its output is at full scale, so the default is -14 dB. Keep the radio's ALC at zero.

## Limits

* Messages are the ones JS8Call can send (letters, digits, punctuation); automatic replies, heartbeat networking and relaying of other stations' messages are not used.
* JS8Call-improved version 3.0.3 is the one this was built and tested with. It needs glibc 2.38 or newer (Debian 13, Ubuntu 24.04); on older systems such as Debian 12 the Install button fetches version 3.0.2, which has the same network interface.
* New: tested with a simulated radio and with JS8Call's own audio through the loopback, not yet over the air.
