# FloBar v2 Fixes

## Message Settings

## macOS, Dock, Menu & Quit

## READY

## Clock, Timer & Restart

## Controllers & Disconnect

## Bar Size Not Persisted

Resizing the bar (making it bigger or smaller) does not work — the enlargement and reduction return to the original position/size it was in; the new size is not saved. The bar size should persist after resizing.

Goal: the size change applies to the bar permanently — the resized size should stick, not revert.

It is not possible to move the bar away from the screen edges — this was one of the defined fixes between version V1 and V2 and it does not work. Dragging the bar outward always snaps it to one of the screen edges. It needs to be fixed so the bar can also be a floating window that moves wherever the user wants it on the screen.

Because there were many buttons, the bar's length exceeded the screen bounds. It is important that the bar does not go outside the screen bounds. Define the bar so that if there are too many buttons, the bar still stays within the screen bounds, but the buttons become proportionally smaller to fit inside the screen.

For clarity: all buttons and button sections — including the message box, the MIDI box, and all other boxes — change proportionally to the number of buttons and sections within the bar.

## Buttons and Sections

1. Remove the drag handle/marker on the bar buttons and restore the buttons to their original size, because the marker takes up space and does not work. There was supposed to be an option on the bar itself (not in settings) to hold the top part of a button and drag it — this was defined between V1 and V2 fixes and did not happen.

2. Dragging does not work at all — not of sections, not within sections, not outside sections. Investigate why dragging does not work.

3. Add immediate saving the moment the user finishes dragging something, so it is saved no matter what.

## FX / FXSC

## Setup Wizard

## Bar & MIDI

## Message Sets

## Actions / TT1

## Data & Access

## Save & Exit

## Guardian & Performance

## Other
