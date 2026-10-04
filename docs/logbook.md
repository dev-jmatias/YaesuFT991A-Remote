# Logbook link (Hamlib rigctl)

Radio Remote can let a logbook program on another computer **follow the radio**: the logbook shows the frequency and mode, and fills them
into a new contact automatically. It can also **change the frequency and mode** when you click a spot or a bandmap entry.

It speaks Hamlib's "NET rigctl" protocol on TCP port 4532, which Log4OM, N1MM+, CQRLOG, HamRS, Ham Radio Deluxe and many others understand.
The program on the other computer does not need a COM port or a USB cable: it connects to the Pi over the network.

## What it can and cannot do

| | |
|---|---|
| Read frequency, mode, split, whether the radio is transmitting | yes |
| Set frequency and mode | yes (can be switched to read-only) |
| **Transmit (PTT)** | **never**: a PTT-on request is refused and logged, even when transmitting is enabled in Admin > Config |
| Switch the radio off | never |
| Change frequency or mode **while the radio transmits** | refused |

This is a deliberate limit: the link has no password (the protocol has none), so it can only do harmless things. WSJT-X and other programs
that need to **transmit** through the radio are not supported by this link.

## Switch it on

1. Sign in as an administrator, **Admin > Config > Logbook link**.
2. Tick **Switch the logbook link on** and press **Apply**. It starts at once (no restart).
3. The card now says *Listening on port 4532* and shows which computers are connected.

Settings in the card:

* **Let the logbook change frequency and mode**: untick it for a read-only link.
* **Port**: 4532 is the usual one. Change it only if something else uses it.
* **Allowed addresses**: `private` (the default) means this Pi and the usual home-network ranges (192.168.x.x, 10.x.x.x, 172.16-31.x.x).
  Computers anywhere else are dropped without an answer. To allow one computer or a Tailscale address use a list, for example
  `192.168.1.40, 100.64.0.0/10`.

**Never forward port 4532 on your router.** Anyone who could reach it could retune the radio. Keep it for the home network (or Tailscale).

## Log4OM

1. In Log4OM open **Settings > Program Configuration > Hardware Configuration** and add a new radio / CAT interface.
2. Choose **Hamlib** as the interface and, as the radio model, **NET rigctl** (Hamlib model 2, sometimes listed as "Hamlib NET rigctl").
3. As the port / address enter the Pi's name or address and the port: `ft991a.local:4532` or `192.168.1.50:4532`.
   (Use the name shown in your browser's address bar. If `.local` names do not work on that computer, use the Pi's IP address.)
4. Save, then connect. The frequency and mode in Log4OM should now follow the radio; clicking a spot should retune it.

The wording of the Log4OM menus changes between versions. What matters: Hamlib, model **NET rigctl**, address `host:4532`. Other logbooks use the
same three settings, sometimes under the name "rigctld" or "Hamlib NET rigctl".

## From the command line (to check it)

With Hamlib installed on any computer:

```bash
rigctl -m 2 -r ft991a.local:4532 f        # prints the frequency
rigctl -m 2 -r ft991a.local:4532 m        # mode and passband
rigctl -m 2 -r ft991a.local:4532 F 7074000
```

## If it does not work

| Symptom | Likely cause |
|---|---|
| The card says "Switched off" | tick the box and press Apply |
| The logbook cannot connect | wrong name/address, a firewall on the Pi, or the logbook's computer is outside the allowed addresses (the card counts "refused" connections, and the log says `rigctl: refused`) |
| It connects but the frequency never changes | the radio is off or disconnected (the link answers "I/O error" then); check the radio tab shows a green dot |
| Setting a frequency does nothing | "Let the logbook change frequency and mode" is off, the radio is transmitting, or the frequency is outside the radio's range |
| Mode shows wrongly | Hamlib has fewer mode names than the radio: DATA-U shows as PKTUSB, DATA-L as PKTLSB, narrow FM/AM as FM/AM |

Every frequency or mode change made through the link is written to the audit log (Admin > Audit) with the computer's address.
