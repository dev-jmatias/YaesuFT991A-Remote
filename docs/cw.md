# CW (Morse)

The **CW** switch on the **Digital** tab decodes Morse from the radio's audio into text. It also sends what you type, through the radio's own keyer.

## Using it

1. Open **Digital** and press **CW**. The radio is put in a CW mode (CW-U, or the last CW mode and frequency you used on this screen) and decoding starts.
2. Pick a band, type a frequency (MHz, kHz or Hz), or step with **-** and **+**. Use the radio's own CW filter (narrow, 250-500 Hz) for the best result.
3. The decoded text appears in the **Received** box. **Copy** copies it; **Clear** empties it for everyone on this radio.

Leaving the screen switches decoding off and puts back the mode and frequency the radio had.

## What you see

* The **spectrum** shows the audio between 300 and 1100 Hz; the yellow line is the pitch the decoder is following. Click the spectrum to fix the pitch there.
* **Pitch** and **Speed** are found by themselves (*Auto*). Untick *Auto* to fix either one: a fixed speed helps with very weak or irregular signals. The chip at the top right shows the speed (WPM) while a steady tone is heard.
* **Sound here** (under CW settings) chooses what this device plays: *Off*, *Low* or *Normal*. The decoder works on the Pi's own audio, so it does not need any sound here.

## Sending

Under the received text is the **Send** box (it appears when the radio can send CW this way, as the FT-991A can). Type text and press **Send** (or Enter). The buttons **CQ**, **My call**, **599** and **73** send the usual short messages with your callsign (set in the FT settings of the FT8/FT4 screen). **Halt TX** stops at once and drops what is still waiting.

Before sending:

* The radio must be in **CW-U or CW-L** (the Mode box). The screen says so if it is not.
* **Break-in** must be on (tick it on the screen), or the radio will not transmit when it plays the text.
* You must have control of the radio, and transmitting must be allowed (`safety.allow_ptt`), as for any transmission. The usual transmit time limit applies to every piece.

How it works, and its limits: the FT-991A's CAT cannot send free text to its keyer, it can only play its stored keyer memories. So your text is cut into pieces (at most 24 characters, at word boundaries), each piece is written into **keyer memory 5** and played, and the next piece follows when the estimated sending time is over. The text that was in that memory is read first and **put back at the end**. The keyer speed (the box on the screen, in WPM) is the radio's own keyer speed. This is not live typing: text is sent when you press Send. Only letters, digits and `. , ? / = + - '` can be sent; other characters are dropped (the screen tells you which). The sending time is worked out from the speed, so if you turn the speed knob during a long text the pieces may overlap or have gaps: stop and send again.

An administrator can change which memory is used (`cw.keyer_slot`, 1 to 5) and the piece length (`cw.chunk_chars`, 10 to 50) in Admin > Config. This sending method has not yet been checked on a real radio: try it first into a dummy load. The same commands are used for the FTDX10, FTDX101D/MP and FT-710 profiles (their keyer commands are the same in the manuals that were read); none of them has been tried on a real radio yet.

## Logging contacts

Under the text is **Log a contact**: callsign, the reports (599 by default), name and a comment; **double-click a callsign** in the received text to fill the *Call* box. The date, UTC time, band and frequency come from the radio. The contact goes into the same log as FT8/FT4 and RTTY, which can be exported as one ADIF file (mode CW) and uploaded to QRZ and eQSL with the buttons under *Log*.

## How good is it?

The decoder follows any speed from about 5 to 50 WPM by itself, copes with some noise and with sending that is not perfectly even, and prints nothing when there is only noise. Like every automatic decoder it makes mistakes on weak, fading or badly timed signals (a `?` marks a letter it could not read), and it cannot read two stations at once. Your ears are still better, so use *Sound here* when it matters.

Written from the Morse timing rules; no code from any other program is used.
