# FT8 and FT4

The **FT8/FT4** tab (beside FreeDV on a phone or tablet; the **FT8/FT4** button in the bar under the title on a wide screen) lets you listen to, call and work FT8 and FT4 stations from the browser. The Pi decodes the radio's audio in the UTC time slots (15 s for FT8, 7.5 s for FT4) and makes the transmit tones; the page shows the stations, follows the standard QSO sequence, keeps a log and can upload it.

The codec is [ft8_lib](https://github.com/kgoba/ft8_lib) (MIT licence). FT2 is not supported.

## Time

Every FT8/FT4 slot starts on the UTC second, so the clock is the most important setting of all. The top bar of the tab shows the UTC time of the Pi and two indicators (hover over them for the explanation):

* **NTP**: whether the Pi's clock is synchronised by a time server and how far it is from it. Green = within 100 ms, yellow = more than that, red = not synchronised or more than half a second off. A Raspberry Pi with internet access does this by itself (systemd-timesyncd). Without internet access, or if the service is off, the clock drifts: `sudo timedatectl set-ntp true`, or install `chrony`.
* **DT**: the median time offset of the stations just decoded, that is how far *they* are from the start of *your* slot. It should stay within +-0.5 s (green); up to 1 s is yellow, more is red. It is the check that matters most: every other station's clock has been set by a time server too, so a steady DT away from zero means this Pi's clock, or the delay of the audio between the radio and the Pi, is wrong, and your own signal arrives just as early or late at the other end. The Pi's journal logs the median DT of every slot (`journalctl -u radio-remote | grep "median DT"`).

## Before the first use

1. **The Pi's clock must be right.** FT8 and FT4 are timed to the second: a clock that is more than about a second off decodes nothing and transmits in the wrong place. A Pi with internet access keeps its clock by itself; the tab shows *Clock not synchronised* when the Pi says it does not.
2. **Set your callsign and grid square** (*FT settings* at the bottom of the tab; administrators only).
3. **The radio must be in a data mode** (DATA-USB: *DATA-U* on the FT-991A). Choosing a band in the tab sets it. The radio's USB audio and data-port menus must be set as described in [Radio connection](radio-connection.md) (an FT-991A needs the data port menus on USB).
4. **Switch the speech processor off** and keep the radio's **ALC at zero** when you transmit: FT8 is a constant-envelope signal and compression only spreads it.

## The screen

* **Top bar:** the radio's dial frequency, the mode (FT8 or FT4), the band and its usual FT8/FT4 frequency (choosing one sets data-USB and the frequency, and switches reception on) and **RX on/off**. The bar below it shows where you are in the slot (even or odd, seconds).
* **All decodes:** every message decoded, newest at the bottom, with the signal-to-noise ratio (an estimate in the 2500 Hz convention, good to a few dB), the time offset (DT, seconds from the nominal start) and the audio frequency. Green = a CQ call, orange = a message to you. *CQ only* hides the rest. **Double-click** a CQ to answer it (message 1 is sent in the other slot, on that station's frequency unless *Hold TX frequency* is ticked); double-click a message to you to continue the contact.
* **Moving between screens:** opening this tab sets data-USB and the last frequency used for the mode (the current band if it is an FT8/FT4 band the first time, otherwise 20 m) and switches reception on. Switching FT8/FT4 goes to the same band on the other mode's frequency. Leaving the tab switches reception off and puts back the mode and frequency the radio had before. It happens only in the page that has control of the radio. The FreeDV tab remembers the mode and frequency on entry but leaves FreeDV switched on when you leave it (switch it off yourself); once it is off, leaving the tab puts the radio back as it was.
* **FreeDV:** FreeDV and FT8/FT4 cannot share the radio (the FT8 tones would go through the FreeDV modem), so opening this tab switches FreeDV off if it is on, and the Pi refuses to transmit FT8/FT4 while FreeDV is on.
* **Sound:** this device's sound is off while the tab is open and returns afterwards if it was on; *FT settings > Sound here* can set it to *Low* instead. Other devices are not affected.
* **Country:** on tablets and large screens each decode also shows the country of the station that sent it, taken from the callsign with AD1C's country files (a few long names are shortened). It is a table of prefixes, so a special-event or unusual call can show the wrong or no country. Refresh the table with `python scripts/build_cty.py cty.dat` when a new country file is out ([country-files.com](https://www.country-files.com/)).
* **Directed to me / worked:** the messages to you, what you transmit (red) and the contacts logged.
* **Waterfall:** the received audio, 0 to 3000 Hz, with the callsigns of the last two slots marked. The orange bar is your transmit frequency: click the waterfall to move it, or type it in *TX ... Hz*.
* **Comment:** every logged contact carries a comment, by default the mode and the reports as exchanged (`FT8 Sent: +06 Rcvd: +01`). Type your own in the *Comment* box (next to *Erase*) before the contact is logged and it replaces the automatic one in the log, the ADIF file and the QRZ / eQSL uploads; the box empties after each logged contact.
* **QRZ Lookup:** the button next to *Reset QSO* opens the QRZ.com page of the DX station (new tab; nothing is sent to QRZ before you click). On the FreeDV tab the callsigns in the station list work the same way.
* **QSO messages:** the six standard messages for the current DX station, editable, each with a **Send** button.

## Calling and answering

Each slot is decoded twice: an **early pass** about 1.5 s before the slot ends, so the CQ calls appear while you can still answer them in the next slot, and a **final pass** that adds the late and weak ones (shown as "more decoded"). Double-click a CQ and message 1 goes out in the next slot of the other parity, or still in the current one if you were quick enough (up to about 1.3 s after it started: the signal is then that much late, which receivers accept). Later than that it waits for the next slot of that parity, which for FT8 can be 30 s away; the line under the controls says how many seconds are left.

1. Press **Enable TX**. Nothing is transmitted until it is on, and it switches itself off when the page is reloaded.
2. **Call CQ** sends `CQ <callsign> <grid>` in the slots you choose (*TX even* or *odd*) and repeats it. When a station answers, the page follows the standard sequence by itself:

   | You call CQ | | You answer a CQ | |
   |---|---|---|---|
   | they | `<you> <them> <grid>` | you | `<them> <you> <grid>` |
   | you | `<them> <you> -12` (their SNR) | they | `<you> <them> -07` |
   | they | `<you> <them> R-08` | you | `<them> <you> R-09` |
   | you | `<them> <you> RR73` | they | `<you> <them> RR73` |
   | they | `<you> <them> 73` | you | `<them> <you> 73` |

3. The contact is logged when it is complete for you (you send RR73 or 73, or the last message arrives). **Halt TX** stops everything at once and switches *Enable TX* off.

The Pi repeats the current message in every slot of the chosen parity until the next one replaces it, the contact ends, you press Halt, or it has been sent *Repeats* times (default 6) without progress. **Log QSO** logs the current DX station by hand and **Erase** forgets it.

## Transmit safety

Transmitting goes through the same protection as the PTT button: the transmit permission in the administration page, the **control** of the radio (only the operator who has control can transmit), the owner heartbeat (the Pi stops transmitting at once if that page disconnects) and the transmit time limit. The tones are sent at the *TX level* of the settings (default -6 dB); the radio's ALC must stay at zero.

## The log, ADIF and uploading

Every logged contact is kept on the Pi (`ftx_log.jsonl` in the data folder). **Export ADIF** downloads all of it as a standard `.adi` file that any logging program imports (FT8 is its own ADIF mode; FT4 is the submode FT4 of MFSK). Under **Log** each contact shows what has been uploaded; **Upload to QRZ / eQSL** sends everything not yet sent to that service, one contact at a time. Tick *Upload ... as soon as a contact is logged* to do it automatically. There is no LoTW upload (it needs your signing certificate: export the ADIF and sign it with TQSL).

The accounts are entered under **FT settings** by an administrator and stay in the Pi's configuration file; they are never sent back to the page, and the settings backup never contains them.

| Service | What it needs |
|---|---|
| QRZ.com | the logbook's **API key** (QRZ logbook settings); the logbook owner needs an XML (or higher) subscription |
| eQSL | your eQSL user name and password |

These uploads were written from the services' published interfaces and tested only against stand-ins, not against the live services: the first real upload may need a small correction, so look at the message the page shows.

## What it does not do (yet)

* FT2, JS8 and other modes. No DXCC or "new one" marks, no multi-pass signal subtraction (a signal hidden under a stronger one may not be decoded) and no a-priori decoding: the decoder reads down to about -19 dB signal-to-noise ratio in white noise, about 2 dB less than WSJT-X.
* One signal at a time on the transmit side, and one audio frequency per transmission.

## If nothing is decoded

* *Clock not synchronised* or a DT far from zero on every station: the Pi's time is wrong.
* The radio is not in data-USB, or the USB audio level is far too low or too high (the waterfall shows noise but no signal columns). Set the radio's USB audio output level so that the waterfall shows the band clearly without turning white.
* Nobody is on the frequency: use a busy band (20 m, 40 m) and the standard frequency from the list.
