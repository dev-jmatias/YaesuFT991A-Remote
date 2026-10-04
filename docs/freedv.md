# FreeDV digital voice

FreeDV sends speech as modem tones in an ordinary SSB channel. Radio Remote does the FreeDV work on the Pi, so you only need the browser:

* **Receive:** the Pi turns the radio's modem tones back into speech and plays that to every listener.
* **Transmit:** your voice (browser microphone) is turned into modem tones by the Pi and sent to the radio, while you hold PTT.

Modes: **700D** and **700E** (700E copes better with fast fading). The codec is the open-source **codec2** library (the same one the FreeDV program uses).
Not included: RADE and the other FreeDV modes.

> **Status:** the codec round trip (voice to tones to voice, noise rejected) is covered by automated tests. How well it works over the air with
> your radio, antenna and band has to be found out by trying it: start with receive, then transmit into a dummy load.

## What you need

* The Pi needs the codec2 library: `sudo apt install libcodec2-1.2` (older systems: `libcodec2-1.0`). The installer and `update.sh` /
  `self_update.sh` try to install it by themselves. If it is missing the FreeDV tab simply does not appear.
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

### Radio settings that matter for FreeDV

* USB or LSB mode (not DATA), **speech processor off**, no equaliser tricks: the radio must pass the modem tones unchanged.
* Set the transmit level so that the **ALC barely moves**: the tones are a continuous signal with high peaks, and an overdriven signal
  is worse than a weak one. Use the **Transmit level of the modem tones** slider on the FreeDV tab (administrators; default -6 dB) together
  with the radio's data-in level, and keep the power moderate (about half of what you would use for SSB is a good start).
* The receive level that goes to the Pi is the radio's menu 107 SSB OUT LEVEL ([radio connection](radio-connection.md)): the modem tones should
  be clearly present but never clipping.

## The channels

The list on the FreeDV tab comes from the configuration (`freedv.channels`). The defaults are the usual FreeDV calling frequencies (160 m
1.997, 80 m 3.625 and 3.643, 40 m 7.177 and 7.197, 20 m 14.236 and 14.240, 17 m 18.118, 15 m 21.313, 12 m 24.933, 10 m 28.330 and 28.720 MHz, all in kHz
as dial frequencies). Check the current activity frequencies on the FreeDV website before relying on them.
Administrators can change the list in the tab (**Edit the channel list**): name, frequency in MHz, mode.

Settings (Admin > Config or the tab): `freedv.mode` (the mode used by the FreeDV button), `freedv.tx_level_db` (-40..0), `freedv.channels`.

## Notes and limits

* One FreeDV setting for the whole radio: it applies to every listener, and changing it needs control of the radio, like changing the mode.
* It is refused while the radio is transmitting.
* Voice goes through the same server-side safeguards as ever (transmit permission, control, PTT heartbeat, time limit, lock). FreeDV changes what is
  sent to the radio, not who may transmit.
* CPU: decoding and encoding 700D/700E is light (a few percent of one core on a PC); not yet measured on a Pi.
* Operating rules: FreeDV is plain unencrypted amateur digital voice; use it only where your licence and the band plan allow it.

## If it does not work

| Symptom | Likely cause |
|---|---|
| No FreeDV tab | libcodec2 is not installed on the Pi (`sudo apt install libcodec2-1.2`, then restart the service) |
| Status stays at "no signal locked yet" | wrong frequency or sideband, signal too weak, RX level too low or clipping, or it is another mode (700D and 700E signals do not decode as each other) |
| "Tap the speaker icon" | the Pi only decodes while someone is listening |
| Locks, but the speech is garbled | an overloaded receive level (lower menu 107) or a very weak signal |
| Others cannot decode you | overdriven or too-weak transmit level, speech processor on, or the radio not in USB/LSB |
