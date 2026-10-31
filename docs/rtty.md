# RTTY (radio teletype)

The **RTTY** switch on the **Digital** tab decodes radio teletype into text and sends what you type. RTTY is two audio tones (**mark** and **space**) a fixed **shift** apart, sending 5-bit Baudot characters at a fixed **baud** rate. The usual amateur setting is 45.45 baud and 170 Hz shift, which is what the screen starts with. It is sent and heard as audio in a USB (or data) mode, not with the radio's own RTTY modes.

## Using it

1. Open **Digital** and press **RTTY**. The radio is put in USB (or the mode and frequency you last used on this screen) and decoding starts. Leaving for another digital screen puts back what the radio had; leaving for the Radio tab or the Menu leaves RTTY running until you switch it off here.
2. Pick a band (the usual calling frequencies), type a frequency, or step with **-** and **+**.
3. The spectrum shows the audio from 300 to 3000 Hz. The decoder looks for two steady tones one shift apart: the **green** line is the mark tone and the **red** line the space tone. **Auto** keeps them tuned by itself; untick it to type the lower tone in Hz, or click the spectrum to put the lower tone there.
4. The decoded text is in the **Received** box. **Double-click a callsign** in the text to fill the *Call* box of the log below.

Settings on the screen: **Baud** (45.45, 50, 75, 100) and **Shift** (170, 200, 425, 850 Hz) must match the other station. **Reverse** swaps mark and space (needed in lower sideband, or when the other station sends the other way round). **USOS** (unshift on space) is what most stations use: after a space the letters shift is assumed.

## Sending

Type in the **Send** box and press **Send** (or Enter). The buttons **CQ**, **My call**, **RST** and **73** send the usual short texts with your callsign (set in the FT settings of the FT8/FT4 screen). **Halt TX** stops at once and drops what is waiting.

* The text is made into Baudot tones at the lower tone shown and transmitted like the FT8/FT4 and SSTV tones: you need control of the radio, transmitting must be allowed, and the usual time limit applies (a long text is cut into pieces at word boundaries, sent one after the other).
* The radio must be in **USB, LSB or a data mode** (DATA-U). Keep the radio's ALC at zero: set the **TX level, dB** under *RTTY settings* (administrators) so that the radio does not compress the tones.
* Only letters, digits and the usual teleprinter punctuation can be sent; other characters are dropped (the screen says which).
* The shift character is sent again after every space, so that stations with and without USOS both read the text correctly.

## Logging contacts

Under the text is **Log a contact**: callsign, the reports (599 by default), name and a comment. The date, UTC time, band and frequency come from the radio. The contact goes into the same log as FT8/FT4 and CW, which can be exported as one ADIF file and uploaded to QRZ and eQSL with the buttons under *Log* (set the accounts up on the FT8/FT4 screen). The same contact logged twice within a few minutes is kept once.

## How good is it?

It reads clean and moderately noisy signals at the usual speeds, follows a transmitter that is a little fast or slow, and prints nothing when there is only noise. Like any automatic decoder it makes mistakes on weak or fading signals, and it cannot read two stations at once. The first characters after tuning to a signal can be lost while the tones are being found.

Written from the published mode (ITA2 Baudot, 5 data bits, 1.5 stop bits); no code from any other program is used.
