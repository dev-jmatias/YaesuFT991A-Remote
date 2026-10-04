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

Phones and tablets/computers share the same layout idea: a radio panel on top, four tabs at the bottom.

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
  off the lights you do not need in **Account > Display** (this device only).
  **SWR warning:** while you transmit at or above the SWR limit (default 3:1, set by an administrator in Config > Meters, 0 = off) the
  SWR bar turns red and a line under the meters says *High SWR: 3.4:1 - check the antenna and the cable*; it stays for 5 seconds after
  the transmission. The FT-991A reports only a raw SWR value, so its ratio is an estimate ("about").
* **VFO-B** is the attached row under VFO-A: its mode, band and frequency. In split operation the transmit VFO gets the red TX badge.
  A purple **MEM 005** badge means the radio is in memory mode.
* **Tuning scale.** Swipe or drag sideways (a quick flick keeps gliding). The numbers on the scale follow the tuning step; **x10**
  makes every move ten times larger.
* **Tuning step** (-, list, +) and **Tune** (see 5): the antenna tuner.
* **VFO tools:** *A -> B*, *B -> A*, *A <-> B* (swap), *Split* (receive on A, transmit on B), *Quick split*, **Set B** (type a frequency in MHz),
  and **Memories**.
* **Band select** and **Set frequency** (type MHz, e.g. `14.195`, or Hz, e.g. `14195000`), **Mode** buttons.
* **PTT** (hold to transmit), always at the bottom, with the round **Lock PTT** button beside it.

### Filters, Levels, Audio tabs

* **Filters & DSP** is grouped in tabs: *Filter* (width, narrow, IF shift, contour), *Noise* (manual notch, auto notch, DNR, noise blanker),
  *Receiver* (IPO/preamp, attenuator, AGC, RIT/XIT with the clarifier offset bar), *Transmit* (processor, monitor, tuner).
  Each row is a button, plus a bar where the function has a value. Double-click the clarifier bar to clear the offset.
* **Levels:** RF gain, microphone gain, transmit power.
* **Audio:** volume, meters, device choice, and the raw radio meter values (see 6).

PTT stays at the bottom on every tab so you can adjust a level while transmitting.

## 4. Memory channels

**Memories** (VFO tools) lists the channels stored in the radio (001-099): number, name, frequency, mode. Type in the box to filter. Tap a
channel to recall it. **Back to VFO** returns to the VFO. **Re-read** reads the list again after you changed memories on the radio.
Memories are never created, changed or deleted from here; do that on the radio. The list is read in the background a few seconds after the
radio connects, so it opens at once; it is read again whenever the radio reconnects.

## 5. Transmitting

**Transmitting is disabled until the owner enables it**: an administrator opens **Admin > Config > Transmitting (PTT)**, chooses
*Enable transmitting…* and types their password again (or sets `allow_ptt = true` in the configuration file on the Pi). The button then
says *Hold to transmit*. An administrator can switch it off again at any time, which also stops a transmission in progress.
Rules that always apply:

* **Hold** the button to transmit; let go to stop. The server un-keys on any hiccup: lost connection, lost control, time limit, shutdown.
* Only the client with control can transmit; microphone audio reaches the radio only while that client is keyed.
* **The microphone button** (top right of the frequency panel) does two things at once: it arms your microphone in the browser and sets the radio's input (menu 106) to **REAR**, so the radio takes its audio from the USB port. Switching it off puts the radio back to **MIC** (the front microphone). It is locked while the radio is transmitting. If you close the page without switching it off, the radio stays on REAR until you next use the microphone button or change menu 106 on the radio.
* **Tune** makes the radio transmit a carrier for a few seconds while the antenna tuner matches. Use an antenna or dummy load.
* **Lock PTT** (the round padlock beside *Hold to transmit*) prevents accidental transmissions from **this device**:
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
* **Too quiet?** Raise the radio's USB output level menu (FT-991A: menu 107 SSB OUT LEVEL, 073 DATA, 046 AM, 075 FM, 054 CW, 099 RTTY) rather
  than boosting in software; see [audio](04-audio.md).
* **Radio meters (raw)** shows the radio's raw 0-255 ALC, COMP, power, SWR and S values and the ALC/COMP peak of the last transmission,
  which is what to read when calibrating the percentage scales.

## 7. Remote access

* At home: `https://<pi-name>.local`. Your device must trust the Pi's certificate once (see [security and remote access](06-security-remote.md)).
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
