# The radio's menu

The **Menu** tab shows the radio's own menu and lets you change it from the browser: every item the radio's CAT manual lists. It is the menu you reach with the MENU key on the radio, with the same names. The FT-991A has 153 items numbered 001 to 153 as on the radio; the FTDX10 (196 items), FTDX101D and FTDX101MP (192) and FT-710 (290) have sections, groups and items, shown as numbers like `01-01-04` (section, group, item) and chosen in the Group list by section.

## Using it

* Pick a **group** (AGC, Display, Keyer, CW mode, SSB mode, Equalizers, ...) or type in **Search** a name or number (`beep`, `026`, `ssb`). The values are read from the radio when you open a group or search: it takes a moment. **Read all** reads the whole menu (about 10 seconds).
* Each item has an editor that fits it: a drop-down for choices, a slider and a number box for ranges. Pressing or releasing the control writes the value to the radio, reads it back and checks it. If the radio does not take it you are told.
* Items you change are marked **changed**, with an **Undo** button that puts back the value this page first read.
* You need **control of the radio** to change anything (the same as for the other controls). Everybody can look.
* Some items affect the radio's menu only; others change how the radio sounds or behaves (the audio levels, the transmit power limits, the VOX). The ones that need care show a note under the name.

## Items that cannot be changed here

A few items are shown but read only, because changing them could cut the app's link with the radio or change how the radio is keyed, which the app relies on:

* the CAT and serial-socket speed, time-out and flow control;
* the PTT, keying and port selection items (PTT SELECT, RPTT SELECT, PC KEYING, REAR SELECT, port select);
* the radio ID, the time zone, text items (MY CALL, preset names) and the RTTY mark frequency, whose format the manual does not describe reliably, and any item the program does not understand well enough.

On the FT-991A these are 027 to 033, 047, 048, 060, 071, 072, 076, 077, 087, 096, 101, 108 and 109.

If you really need one of them, change it on the radio itself.

## Backup and restore

**Back up menu** reads the whole menu and saves it as a file (`menu-ft991a-DATE.json`). **Restore...** (administrators) writes such a file back: only the items that differ are written, the items that cannot be changed here are skipped, and a report lists what was changed. Keep a backup before trying new settings: the radio itself can only do this with an SD card.

Every change, backup and restore is written to the audit log (Admin) with the old and new value and who made it. Changes are refused while the radio is transmitting.

## About the table

The item numbers, names and allowed values come from the radio's CAT Operation Reference Book (the EX MENU table). The groups are ours. Where the manual has a mistake (for example the RTTY shift codes, or the digit counts of 068 and 069) the table follows what the radio does; if the radio ever answers with something the table does not expect, the item shows the raw answer with a `?` and is not edited. This is new: it has been tested against a simulated radio, not yet on a real one.

The tables of the FTDX10, FTDX101D/MP and FT-710 were built the same way from their CAT manuals (the FTDX101MP uses the FTDX101D's table). The addresses of the items the app already uses (the mic source, the rear port select, the out levels) agree with what was read on a real FTDX101D. None of these four radios has been tried with the Menu tab yet.
