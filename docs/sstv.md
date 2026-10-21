# SSTV (slow-scan television)

The **SSTV** screen (the **Digital** tab, then **SSTV** in its switch; on the all-in-one desktop layout the **SSTV** button in the bar under the title) receives pictures from the radio's audio and
transmits a picture of your own. The Pi does the decoding and the encoding; the page only shows the picture as it arrives and prepares the one to send.

SSTV sends a still picture as a sound: the pitch of the tone is the brightness of the picture (1500 Hz black, 2300 Hz white, 1200 Hz the sync at the start of each line). A transmission starts with a
short calibration header that names the mode, so the receiver knows by itself what is coming. The software is written from the published mode descriptions; nothing from other SSTV programs is used.

## Modes

Robot 36 and Robot 72, Martin M1 and M2, Scottie S1, S2 and DX, PD50, PD90, PD120, PD160, PD180 and PD240, for receiving and for sending. Checked against two independent SSTV programs in both directions
(their pictures decode here, and ours decode there). The time a mode takes is shown in the mode list; the usual ones are Robot 36 (37 s), Martin M1 (115 s), Scottie S1 (111 s) and PD120 (127 s).

## Receiving

1. Open the screen. The radio is put in the data mode of the band you last used (USB above 10 MHz, LSB below, FM for the ISS) on that SSTV frequency, and reception is switched on. Choose another band in the
   list when you want one; the list has the usual calling frequencies (80 m 3.845 LSB, 40 m 7.171 LSB, 20 m 14.230, 15 m 21.340, 10 m 28.680 USB, and 2 m 145.800 FM for the ISS SSTV events).
2. When a header is heard the picture area starts and fills line by line, with the mode, the line count and the time left. A finished picture is saved on the Pi and appears under **Received pictures**: tap it
   to see it large, download it as a PNG, or delete it. A picture that is cut short is kept as *partial* when it got at least 10 % of the way.
3. Leaving the screen switches reception off and puts the radio back on the mode and frequency it had; **Clear** only empties the picture area.

The receiver follows the transmitter's timing line by line, so a sound card that runs a little fast or slow does no harm, and a picture right behind another one is picked up too. Signals down to about
12 dB signal-to-noise ratio (in 3 kHz) give a picture (noisy at that level); weaker ones are not recognised. The radio's filter must pass 1100 to 2300 Hz: if it is narrower, the Pi opens it (the same automatic widening as FreeDV,
`freedv.auto_width`).

## Sending

1. Press **Choose a picture** and pick a photo or drawing from the device (any format the browser can read). Choose the **mode**; the preview shows exactly what will be sent, at the mode's size. *Fit* decides
   whether the picture fills the frame (and is cropped) or is shown whole with bars.
2. **My callsign** prints your callsign on the picture (the callsign set under FT settings in the FT8/FT4 screen), and **Message** adds a line of text (a report, your name, "CQ SSTV"). *Text at* puts them at the
   bottom or the top.
3. **Send picture** asks for confirmation and sends the picture to the Pi, which transmits it. A bar shows the progress; **Halt TX** stops it at once.

Sending goes through the same protection as every transmission: the transmit permission in the administration page, the **control** of the radio (only the page that has control can send), the heartbeat (the Pi
stops at once if the page disconnects) and the **transmit time limit**. A mode that takes longer than the time limit is refused before anything is keyed, and the page says so (an administrator can raise
`safety.tx_timeout_s` in Admin > Config: the default of 120 s allows Robot, Martin and Scottie S1/S2 and PD50/PD90, but not PD120 and the longer ones). The radio's ALC must stay at zero: the tones are sent at the **TX level** under *SSTV settings* (default -6 dB), and the
radio's speech processor must be off. SSTV and FreeDV cannot share the radio: opening the SSTV screen switches FreeDV off.

## Sound

This device's sound is off while the screen is open (the decoder works on the Pi's own audio and the tones are not nice to listen to); *SSTV settings > Sound here* can make it *Low* instead. It comes back when
you leave the screen if it was on.

## If no picture appears

* The radio is not on an SSTV signal, or its USB audio level is far too low or high (see [Radio connection](radio-connection.md)).
* The signal is too weak or has fading deep enough to lose the header; the header is the one part that must be heard cleanly.
* A header for a mode this program does not know shows a short message with its code.
* A picture is kept only if the Pi's storage works: `data/sstv` on the Pi holds the pictures (the last 60 by default, `sstv.keep`).
