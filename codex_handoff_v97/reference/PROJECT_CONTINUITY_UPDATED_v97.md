# NEW CHECKPOINT — 2026-10-18: v97 mapping cleanup and nine-hour test v5

## Approved mapping removal
- User explicitly approved removing the obsolete fade-cancellation mappings CH8 CC0 and CH1 CC57 from the controller and all test scenarios; they will not be used again.
- New controller source: `mainstage_jamzone_fade_controller_FLOATING_BAR_v97.py`. It is an exact v96 copy except for removing those two mapping constants/handlers/labels and updating the version title/documentation. v96 remains unchanged and preserved.
- v97 SHA-256: `f1c20f91ae5442f6981cbee750979cc958b1f50944d68801b7e8517e6e9d5a3e`.
- Original v96 SHA-256 confirmed unchanged: `8a21177f4af4cef1a2e46fc579cbb82bf02d750b77df8baeddf0465a70bef9f1`.

## Nine-hour diagnostic test v5
- Test runner: `floating_bar_v97_night_test_v5.py`. Place it in the same folder as v97, then run: `python3 "$HOME/Downloads/floating_bar_v97_night_test_v5.py"`.
- The runner performs preflight checks (apps, Accessibility permission, exact v97 hash, MIDI ports) before stopping any existing controller process. If a preflight fails, it aborts without stopping the current controller.
- It stops only known v96/v97 controller processes, runs a separate instrumented v97 diagnostic copy, and restores v97 after completion or on ordinary interruption. It does not edit v96 or v97 source files during the run.
- Duration is nine full hours after diagnostic startup and the 20-second manual mouse baseline. Random action gaps are 60–150 seconds.
- Keep AIRSTEP and SMC powered on and connected; keep JamZone's loop running, with MainStage and SBP open. Keep Spotify and Chrome closed. The test monitors connection changes but does not power-cycle physical controllers.
- The first 20 seconds ask the user to click only the timer text (10 single clicks, two double-clicks and two long presses). This records actual mouse input before synthetic Quartz clicks begin, helping distinguish physical input delivery from Tk hit-testing/event-loop problems.
- Mouse probes include timer hit-area points, double-click, long-press, reset, context menu, multiple points in all status/effect tiles, and probes during 1/2/4-worker CPU stress. Clicks/keyboard HID are sent only after the target process is verified frontmost.
- Scenario repetitions: 10 each G2M repeat, TalkBack repeat, quick fade, loop fade, full fade, state controls CC42/45/41/39/40, Note 22, Note 18, master-volume ramp, quick-volume ramp, CC60 sidechain pair, and mouse probe. Includes two controlled supervisor restarts, one existing restart-shortcut probe, internal watchdog freeze/recovery, and external-supervisor-only freeze/recovery.
- JamZone P/L HID commands are dispatched only after verifying JamZone is frontmost. After a successful loop-fade sequence, an extra L restores the loop, followed by P. If Down sequence is not confirmed, an extra L is sent only if the controller log confirms it already sent L.
- Timer phases use diagnostic-only durations: 3h30 starting at elapsed 00:30 and reset at 03:30; 2h30 starting at 05:00 and reset at 07:00. The saved user default is not changed.
- Report includes one-second UI heartbeat, heartbeat history every 5 seconds, resource samples every 10 seconds, `top` snapshots every 60 seconds, CPU/RSS for top processes, port/receiver/battery state, MIDI and Bus 2 logs, click-event geometry/timing, crash records created during the run, action counts, and `report_summary.json`. The ZIP is saved on Desktop.
- CPU stress workers are limited to 60 seconds and are terminated on schedule and during emergency cleanup. End-of-test restoration sends CC20=127 and CC21=127, then starts v97. If v96 unexpectedly remains running, the runner avoids launching a duplicate and records a recovery warning.

## Validation state and limitations
- Static AST/syntax checks: PASS for v97, runner v5, and the generated diagnostic copy.
- Verified the runner is pinned to the exact v97 SHA-256 and refuses to stop a controller if the source differs.
- Verified the retired cancellation mappings are absent from v97 controller logic and no test scenario sends them.
- No live Mac, CoreMIDI, MainStage, JamZone, Bluetooth or physical-mouse run has yet been performed by the assistant. Runtime success is not guaranteed until the Mac preflight passes and the test runs. The test is designed to abort before stopping the existing controller if its initial requirements are not met.

## Exact next step
1. Download `mainstage_jamzone_fade_controller_FLOATING_BAR_v97.py` and `floating_bar_v97_night_test_v5.py` to `~/Downloads`.
2. Start JamZone's loop, keep both controllers powered on, MainStage and SBP open, and quit Chrome/Spotify.
3. Run the command above. During the first 20 seconds, perform the requested manual clicks on the timer text.
4. After nine hours, upload the generated Desktop ZIP for analysis.

---

# CURRENT CHECKPOINT — 2026-10-17, Floating Bar v94 / Pending v95 Decision

Read this section first. It supersedes older “current version”, “next step”, and status statements elsewhere in this long continuity record when they conflict. Older historical sections remain preserved for context.

## Current state
- Latest generated script: `mainstage_jamzone_fade_controller_FLOATING_BAR_v94.py`. It exists in `/mnt/data`; v89–v94 files are preserved. v95 has NOT been created.
- P23 battery reporting remains unresolved. The user reported that it did not update in v92 and v93. v94 added broader JSON parsing and forced startup JSON fallback; synthetic tests passed, but the user has not yet reported a live v94 result. Do not claim P23 is fixed.
- User confirmed the Guitar Mute red-light behavior is working correctly. Do not change it.
- Keep IAC Bus 1 for existing control commands and IAC Bus 2 for status mirrors. Do not change mappings or established MIDI behavior without explicit authorization.
- Active-command display and disconnect display are intended to alternate every 3 seconds without redesigning either display. Recent live confirmation of this behavior is not documented.

## Recent versions and test record
- **v89:** Guitar Mute Note 18 press-edge toggle, release does not toggle; red override limited to DLY–IR; MIDI INPUT CC value on a separate lower line; expanded P23 aliases/parsing. Static checks passed. User's live log showed `ControllerBatteryScheduler` crashing with `AttributeError: 'Controller' object has no attribute '_update_controller_batteries'`, P23 battery `None`, and very repetitive MIDI/IAC logs. User later explicitly confirmed the red lights behave correctly.
- **v90:** Intended to reduce repetitive diagnostics, improve P23 handling, and alternate active-command/disconnect displays every 3 seconds. Static checks passed; no assistant-run live test.
- **v91:** UI refresh reduced from 20 to 10 Hz, skip repaint of unchanged tiles, reduce duplicate Bluetooth checks, JSON fallback limited to five minutes, raw MIDI output disabled by default. Static checks passed; CPU/RAM savings not measured.
- **v92:** UI tick 1 Hz; clock 30 seconds; startup battery refresh scheduled after 1 second; Mac battery update initially after 1 second and then every 60 seconds. Assistant removed the RESET overlay, describing it as a runtime-error banner rather than a true reset. User reported P23 battery did not update at startup or every 15 minutes. User has not confirmed that removing the overlay is harmless.
- **v93:** Coalesced UI refresh after 30 ms for discrete state changes; regular tick 1 Hz; clock 30 seconds; command-triggered JSON refresh capped at 30 seconds. Static/synthetic checks passed. User reported P23 battery still did not update.
- **v94:** Broader JSON parser, including device names used as dictionary keys; forced JSON fallback on initial battery scan; intended 15-minute schedule. Four synthetic parser tests and syntax checks were reported as passing. Live P23 result is pending.

## Approved v95 optimization work (do not re-ask approval)
User authorized implementing the following in a new version, while preserving all existing functional behavior unless a specific interval below is changed:
1. Reduce duplicate Bluetooth/system battery scans and avoid starting expensive `system_profiler` processes in response to every MIDI command.
2. Reduce duplicate CoreMIDI port enumeration while preserving fast-enough disconnect/reconnect recovery.
3. Optimize UI refresh so a MIDI state change is responsive without redrawing unrelated unchanged components.
4. Make blink scheduling event-driven where possible, preserving the current defined visual behavior/timing. Do not change blink behavior without first explaining the proposed behavior and getting approval.
5. Change UIWatchdog polling from every 0.5 seconds to every 5 seconds.
6. Change Mac system-battery polling from every 60 seconds to every 15 minutes.
7. Controller battery update: at startup and every 15 minutes.
8. Keep normal console output to important state changes, disconnect/reconnect events and errors; suppress repetitive raw MIDI diagnostics by default.
9. Preserve Guitar Mute red lights, IAC Bus 1/Bus 2 architecture, all existing mappings/button behavior, the existing display designs, and the intended 3-second alternation between active MIDI command and disconnect display.
10. Clock interval: latest explicit user instruction is update every 30 seconds. This supersedes the earlier assistant suggestion of once per minute.
11. Fix P23 battery percentage display and restore P23 disconnected indication in MIDI INPUT, which the user says worked in earlier versions but was lost in a later revision.

## Decision required before implementation
The user has not yet selected how to detect P23 disconnects while keeping expensive battery scans at 15-minute intervals:
- **Option 1 — maximum resource savings:** Bluetooth presence/battery scan at startup and every 15 minutes; P23 disconnect indication may be delayed until the next scan.
- **Option 2 — faster disconnect detection:** a separate more frequent presence check, with expensive battery scans only at startup and every 15 minutes; this uses some extra resources and must be implemented only with a verified available presence signal.
The assistant asked the user to choose 1 or 2. Do not create v95 until the user chooses.

## Resource analysis — evidence vs inference
Source review identified likely optimization opportunities: duplicate `mido.get_input_names()`/CoreMIDI enumeration in more than one monitoring loop; potentially expensive `system_profiler` text and JSON calls; command-triggered battery refresh; broad `tick()` after a discrete MIDI event; multiple scheduled UI/blink callbacks. `UIWatchdog` currently checks at 0.5 seconds and system battery is on a 60-second schedule in v94. These are source-level observations; no actual CPU/RAM benchmark has been run. The concern about consecutive `listener.start()` calls was noted but is not yet confirmed as a real runtime fault; inspect the exact execution path before changing it.

## Exact next action
Wait for the user's choice of P23 disconnect detection **option 1 or 2**. Then create v95 from the appropriate preserved source, perform syntax/AST/structural tests, document what was actually tested, update this continuity file and the current status file, and provide the exact run command. Do not overwrite earlier versions.

---

## 0. משימות שהושלמו ואושרו — אין להחזיר לרשימת המשימות

המשתמש אישר ב־2026-10-16 שכל שלוש המשימות הבאות **בוצעו, אושרו ונבדקו בפועל**:
1. תצוגת אחוזי הסוללה של הבקרים (SMC ו־P23).
2. גודל ומיקום הטקסט בתיבת ההודעות, כולל הגדלות הטקסט שבוצעו ב־v74–v76.
3. פקודת SMC Note 94 להסתרת הסרגל ולהחזרתו.
4. **הקמת פקודות מצב IAC Bus 2 ב־MainStage והניתוב שלהן לסרגל:** הושלמה, אושרה ונבדקה. CLOSED / USER-VERIFIED. אין להחזיר לרשימת המשימות.
5. **גובה הסרגל:** הופחת ב־25%; הושלם, אושר ונבדק. CLOSED / USER-VERIFIED. אין להחזיר לרשימת המשימות.
6. **הסרת המשבצת הריקה שאינה בשימוש:** המשתמש אישר ב־2026-10-17 שהוסרה מזמן, נבדקה ועובדת תקין. CLOSED / USER-VERIFIED. אין להחזיר לרשימת המשימות.

**כלל נגד חזרה בטעות:** כל שש המשימות המנויות לעיל הן CLOSED / USER-VERIFIED. אין להציגן כמשימות פתוחות, להוסיף אותן שוב לרשימת המשימות, או לבצע בהן שינוי נוסף. אם מסמך ישן מציג אחת מהן כ־pending, הרשומה הזו גוברת ויש לתקן את הרשימה הישנה במקום לפתוח את המשימה מחדש. אפשר להזכיר אותן רק כהיסטוריית שינויים שהושלמו. פתיחה מחדש מותרת רק אם המשתמש מדווח על תקלה חדשה ומבקש לטפל בה במפורש.

גרסה עובדת שאושרה לאחר השינויים: **v77** — `mainstage_jamzone_fade_controller_FLOATING_BAR_v77.py`. המשתמש אישר שההתנהגות הנוכחית עובדת.

4. **מעבר חלונות JamZone/SBP דרך SMC ו־AIRSTEP — הושלם, אושר ונבדק:** SMC כבר תמך במעבר. ב־v77 נוסף ל־AIRSTEP: במהלך 5 השניות הראשונות של TT1, לחיצת TB שולחת TB נוסף ו־Note 94; לחיצות נוספות בתוך חלון 2 השניות שולחות שוב את שתי הפקודות ומאפסות את הטיימר. המשתמש אישר ב־2026-10-17 שהכול עובד תקין. אין להחזיר למשימות פתוחות.

---

# PROJECT_CONTINUITY.md
# MainStage / JamZone Floating Bar — מסמך המשכיות מלא

**עודכן:** 2026-10-17  
**שפת עבודה מועדפת:** עברית; שמות תפריטים, פקודות MIDI וקוד נשמרים באנגלית.  
**מטרת המסמך:** לאפשר לשיחה חדשה להמשיך מהמצב המדויק בלי לבקש מהמשתמש לחזור על מידע, בלי לנחש ובלי לשנות התנהגות יציבה ללא אישור.

---

## 1. ניהול שיחות והיסטוריה

- השיחה המקורית נקראת לפי בקשת המשתמש: **`01 – Original – Full History`**.
- שיחות חדשות בפרויקט יקבלו מספור עולה: `02 – Continuation – Active Work`, אחריה `03 – ...` וכן הלאה.
- יש להתייחס לשיחה `01 – Original – Full History` כמקור ההיסטוריה המקורי, ולמסמך זה כנקודת המשכיות תפעולית מעודכנת.
- בתחילת כל שיחה חדשה יש לקרוא את `PROJECT_CONTINUITY.md` ואת הקבצים הרלוונטיים בפרויקט. אין להניח שניתן לראות את כל התמליל המקורי אם הוא לא נגיש בפועל.
- לאחר שינוי מהותי בהחלטות, גרסאות, בדיקות או תוכנית העבודה — לעדכן את המסמך רק לפי מידע מאומת.

## 2. כללי עבודה מחייבים

1. **אין לשנות קוד, ניתוב MIDI, פריסת סרגל או התנהגות קיימת ללא אישור מפורש.**
2. אין ליצור גרסה חדשה רק מפני שנמצא רעיון לפתרון. קודם מסבירים את השינוי המוצע ומקבלים אישור.
3. אין לשנות את הגרסה היציבה או לשבור התנהגות MIDI קיימת כדי לתקן את התצוגה.
4. `v34` היא גרסה שמורה שבה G2M עובד דרך IAC Bus 2. הקובץ הקיים במרחב העבודה הוא:
   `mainstage_jamzone_fade_controller_FLOATING_BAR_v34.py`
   נתיב בסביבת העבודה הנוכחית: `/mnt/data/mainstage_jamzone_fade_controller_FLOATING_BAR_v34.py`
5. אין להניח ש־v35 זמינה: זיכרון קודם הזכיר v35, אבל בבדיקת הקבצים הפעילים ב־`/mnt/data` נמצא v34 ולא נמצא קובץ v35. לפני שימוש בגרסה אחרת יש לאמת שהקובץ אכן קיים אצל המשתמש או בפרויקט.
6. לא לטעון שבדיקה עברה אם בוצעה רק בדיקת תחביר. יש להבחין בין: בדיקת תחביר, בדיקה אוטומטית, בדיקה ידנית, ובדיקת הופעה/זמן אמת.
7. יש לתת הוראת Terminal מדויקת בכל פעם שמבקשים מהמשתמש להריץ סקריפט. אין לבקש בדיקה שכבר בוצעה או לבקש שוב מידע שכבר נמסר.
8. לעבוד צעד אחד בכל פעם ולשאול לכל היותר שאלה ממוקדת אחת בכל הודעה.
9. תשובות קצרות וישירות; ללא הקדמות מיותרות. תמיד לסיים בצעד הבא המדויק.
10. אין להמציא אפשרויות UI או פקודות MIDI. אם צילום המסך לא מספיק, לשאול שאלה אחת ולחכות.
11. אין לשנות את ניתוב JamZone/SMC או את ההתנהגות הקיימת של MIDI Bus 1 בלי אישור מפורש.
12. המשתמש ביקש במפורש שלא לקבל עוד הנחיות שמסיטות מהיעד: **היעד הוא שהסרגל יקבל הודעות מצב באמצעות IAC Bus 2; לא לפתח כרגע פתרון CoreMIDI ולא להשתמש בכלי צד שלישי במקום זה.**

## 3. מטרת הפרויקט

Floating Bar לסביבת הופעה חיה, המשולבת עם MainStage ו־JamZone, שמציגה מצב של כפתורים ופקודות MIDI ומספקת כלים כגון טיימר, הודעות, BPM ומצבי mute. חשוב שהסרגל יהיה אמין ולא יפריע לאודיו, ל־MainStage, ל־JamZone או לבקרי MIDI.

המערכת כוללת:
- MacBook Pro M1, macOS.
- MainStage.
- JamZone.
- Behringer/SMC Mixer Bluetooth controller (נקרא גם SMC).
- AIRSTEP Bluetooth.
- IAC Driver Bus 1 ו־IAC Driver Bus 2.
- Python/Tkinter Floating Bar; MIDI דרך `mido`/`python-rtmidi` בגרסאות הרלוונטיות.
- MIDI Monitor של Snoize, גרסה 1.5.4, לצפייה בהודעות.

## 4. ארכיטקטורת MIDI — החלטות עדכניות

### 4.1 הפרדה קבועה בין בקרים

המשתמש התחיל סדר חדש כדי למנוע התנגשויות:

| מקור | ערוץ MIDI קבוע | כלל |
|---|---:|---|
| SMC | CH1 | כל כפתורי ה־SMC נשארים בערוץ 1; ה־CC הקיימים לא משתנים כרגע |
| AIRSTEP | CH16 | מעתה AIRSTEP בלבד משתמש בערוץ 16; המשתמש שינה גם את מספרי ה־CC לפי הסדר החדש שבתמונה |
| IAC Driver Bus 1 | CH15 | מעתה IAC Bus 1 בלבד משתמש בערוץ 15 |

חשוב: אלו החלטות מבניות חדשות שנמסרו ב־2026-10-15. יש לוודא שהגדרות MainStage והסקריפטים מתואמות לפני כל שינוי עתידי. לא לבצע שינויי קוד אוטומטיים על סמך ההחלטה בלבד.

### 4.2 פיידרים של SMC

המשתמש הסביר ש־SMC מוגדר אוטומטית:
- כל הכפתורים בערוץ 1.
- פיידרים משתמשים ב־CC0 בערוצים 1–8 לפי סדר:
  - פיידר 1 = CH1 CC0
  - פיידר 2 = CH2 CC0
  - ...
  - פיידר 8 = CH8 CC0

לכן המשתמש השאיר את מיפויי SMC כפי שהם. אין לשנותם בלי אישור.

### 4.3 Bus 1 לעומת Bus 2

- **IAC Bus 1:** מסלול פקודות הבקרה הקיים ל־MainStage/JamZone. יש לשמור על התנהגותו היציבה. לפי מבנה הערוצים החדש, הוחלט שהוא יפעל על CH15.
- **IAC Bus 2:** מסלול הודעות מצב/תצוגה לסרגל. G2M עובד בו לפי בדיקות קודמות; המשתמש אישר שפתרון Bus 2 כבר אינו גורם להאטות או תקיעות. יש להמשיך להשתמש ב־Bus 2 כמקור מצב לסרגל.
- המשתמש מעדיף את הודעות IAC הכפולות (פקודת SMC המקורית + הודעת מצב ל־IAC Bus 2), מפני שהפתרון כבר עובד ונתפס כאמין יותר מהוספת אפליקציה/כלי ניטור נוסף או פיתוח רכיב CoreMIDI.
- **אין להציע שוב מעבר ל־CoreMIDI או לכלי צד שלישי כפתרון המועדף**, אלא אם המשתמש יעלה זאת מחדש.

### 4.4 מדוע לא להאזין ל־SMC ישירות לצורך תצוגה

הוכח בבדיקה:
- MIDI Monitor מציג הודעות תחת `To SMC-Mixer Bluetooth`, לדוגמה:
  - Mute: `Note On`, channel 1, note `D-2`, velocity 127.
  - Unmute: `Note Off`, channel 1, note `D-2`, velocity 0.
- הסקריפט שמאזין לכניסת `SMC-Mixer Bluetooth` לא קיבל את ההודעות היוצאות האלה.
- הסיבה: MIDI Monitor מציג את זרם היציאה מהמחשב אל SMC; כניסת SMC שהסקריפט פותח מקבלת הודעות נכנסות מה־SMC. אלו כיוונים שונים.
- ב־MainStage, בשורת `SMC Guitar 2 Monitor ONLY`, הוגדר:
  - Device: Bluetooth SMC-Mixer
  - Channel: Channel 1
  - Type: Note On/Off
  - Number: D-2
  - MIDI Through: Do not pass through
  - Send Value to: Bluetooth SMC-Mixer בלבד
- תפריט `Send Value to` הציג רק SMC; לא נמצא שם יעד נוסף לשכפול.
- ההחלטה הסופית: לא לנסות ליירט את היציאה ישירות כרגע; להמשיך עם Bus 2 והודעות מצב ייעודיות לצורך התצוגה.

## 5. מצב קובץ וגרסה

### גרסה שמורה

`mainstage_jamzone_fade_controller_FLOATING_BAR_v34.py`

- הקובץ נמצא בפועל ב־`/mnt/data`.
- גודל שנצפה בבדיקת הקבצים: 85,350 bytes (הגודל עשוי להשתנות אם יוחלף הקובץ; אין לשנותו).
- v34 היא נקודת הייחוס השמורה. אין לדרוס אותה.
- G2M דרך IAC Bus 2 אושר כעובד; המשתמש אמר שהפתרון עובד ואין עוד האטות/תקיעות.
- בדיקת תחביר של v34 דווחה בעבר כעוברת, אבל אין להסיק מכך שכל התנהגות ה־UI נבדקה בזמן אמת.
- v34 מכילה לוגיקת קבלת SMC ו־AIRSTEP, וכן פתיחת כניסת `IAC Driver Bus 2` ויציאת `IAC Driver Bus 1`.
- בקוד v34 שנבדק קודם:
  - `SMC_STATE_NOTES = {2:'G2M',0:'T.B.',3:'Bvox',11:'Solos',14:'FX'}`
  - `GUITAR_MUTE_NOTE = 18`
  - `SMC` Note messages מסוימות נזנחות אם כניסת IAC state קיימת, כדי ש־IAC יהיה מקור המצב הסמכותי.
  - Bus 2 מטפל ב־G2M דרך CH14 CC55 (mido channel 13, CC55) כמקור מצב מוחלט: value 127 = active/red; value 0 = inactive/blue.
- יש לשמור את שם הקובץ `mainstage_jamzone_fade_controller_FLOATING_BAR_v34.py` לגרסה זו.

### גרסאות קודמות

קיימים קבצים v11–v34 בסביבת העבודה, כולל v26–v34. אל תשתמש בגרסה ישנה במקום v34 בלי סיבה ואישור.
- v29: דווח על קריסה בעת הפעלה כאשר AIRSTEP Bluetooth לא היה זמין.
- v30: שונה כדי לאפשר פתיחת UI גם כש־AIRSTEP לא זמין.
- v31 ואילך: המשתמש דיווח ש־G2M עובד דרך Bus 2.
- v34: נקודת ייחוס נוכחית שמורה.
- אזכור קודם של v35 בזיכרון לא אומת מול הקבצים הפעילים; אין להציג v35 כקובץ קיים עד אימות.

### נקודת יציבות היסטורית — v52 (2026-10-16; לא הגרסה הנוכחית)

- קובץ: `mainstage_jamzone_fade_controller_FLOATING_BAR_v52.py`
- המשתמש אישר במפורש: **v52 עובדת תקין; כל השאר עובד מושלם.** יש להתייחס אליה כגרסה היציבה הנוכחית ולשמור אותה ללא שינוי.
- הקובץ נמצא בסביבת העבודה הנוכחית ב־`/mnt/data/mainstage_jamzone_fade_controller_FLOATING_BAR_v52.py`.
- אתחול הסרגל בעת הפעלה שולח פקודות פעמיים דרך IAC Bus 1, בערוץ MIDI 15, כדי ש־MainStage ישלח עדכוני מצב ל־IAC Bus 2:
  - G2M: CC42
  - T.B.: CC45
  - MM: CC41
  - Bvox: CC39
  - Solos: CC40
  - FX Sidechain: CC43
  - FX Mute: Note 22
- FX Sidechain ו־FX Mute נשלחים בסדר הזה, אחרי Solos; כל פקודה נשלחת פעמיים, עם מרווח 150ms בין הפקודות. אין השהיית אתחול של 1.2 שניות.
- המשתמש אישר שהמערכת עובדת לאחר תיקון FX. אין לשנות את רצף האתחול, המיפויים או התנהגות MIDI של v52 בלי אישור מפורש.
- בדיקות קוד שנמסרו בעת יצירת v52: בדיקת תחביר ובדיקות מבניות עברו. בדיקת MIDI חיה לא בוצעה על ידינו; אישור התקינות בפועל מבוסס על דיווח המשתמש.

## 6. מיפויי AIRSTEP לפי התמונה מ־2026-10-15

המשתמש צירף תמונה של `Bluetooth AIRSTEP`, `Channel 16`. לפי התמונה כל הרשומות הבאות הן ב־CH16. שמות/CC כפי שמופיעים בתמונה:

| CH | CC | שם בתמונה | התאמה/הערה |
|---:|---:|---|---|
| 16 | 7 | `MATER Fader` | כנראה Master Fader; הכתיב בתמונה הוא MATER |
| 16 | 11 | `Guitar Vol 80-127` | Guitar Expression/Volume, טווח 80–127 |
| 16 | 38 | `old and empty` | ישן ופנוי |
| 16 | 39 | `BT Bvox mute` | Bvox mute |
| 16 | 40 | `BT SOLOs Mute Airstep` | Solos mute |
| 16 | 41 | `Airstep MuteGal-Mon` | **התאמה לא ברורה**; המשתמש נשאל אם זה G2M או פקודה נפרדת להשתקת Gal Monitor, ועדיין לא ענה |
| 16 | 42 | `Airstep Guitar 2 Monitor ONLY` | G2M |
| 16 | 43 | `Airstep FX side chain Bypassed` | FX Sidechain |
| 16 | 44 | `TAP TEMPO2` | Tap Tempo |
| 16 | 45 | `TalkBack Gal to Monitor` | TalkBack |

- ערכי MIDI Value לא נראו בתמונה, ואין להמציאם.
- המשתמש הדגיש ששינה ערוץ ו־CC **רק ב־AIRSTEP וב־IAC**; SMC נשאר כפי שהיה.
- השאלה הפתוחה האחרונה לפני יצירת מסמך זה: האם `CH16 CC41 — Airstep MuteGal-Mon` הוא G2M או פקודה נפרדת. יש לחזור לשאלה זו כשתוכנית העבודה תגיע למיפוי AIRSTEP.

## 7. החלטות ותוכנית עבודה מאושרת — לפי סדר עדיפות

### סטטוס נוכחי
- הגרסה העובדת האחרונה שאושרה ונבדקה היא **v76**. אין לשנות אותה או לדרוס אותה.
- סנכרון מצב בהפעלה והקמת פקודות/ניתוב מצב דרך IAC Bus 2 ב־MainStage הושלמו, אושרו ונבדקו על ידי המשתמש. אלה משימות סגורות, לא משימות פתוחות.
- כל שינוי עתידי מחייב בקשה מפורשת וגרסה חדשה בשם חדש.

### שלב 1 — IAC Bus 2 וניתוב מצב — הושלם ואושר
- המשתמש אישר ב־2026-10-17 שהקמת פקודות מצב IAC Bus 2 ב־MainStage והניתוב שלהן לסרגל בוצעו מזמן, נבדקו ועובדים תקין.
- **אין לחזור על שלב זה, להציגו כמשימה פתוחה או לבצעו שוב.** הרשומה הזו גוברת על כל אזכור ישן בהמשך המסמך.

### שלב 2 — עדכון התצוגה
- שלב עדכון התצוגה הקשור לפקודות IAC Bus 2 כבר מיושם בגרסה העובדת v76, לפי אישור המשתמש שהמערכת עובדת תקין. אין להציג את הקמת/חיבור Bus 2 כמשימה פתוחה.

### שלב 3 — תיקוני UI וטיימר (רשומים בלבד; לא לבצע עד אישור גרסה חדשה)

- [x] **גובה הסרגל — הושלם, אושר ונבדק:** הגובה הכולל הופחת ב־25%. CLOSED / USER-VERIFIED; אין להחזיר לרשימת המשימות אלא אם המשתמש מדווח על תקלה חדשה ומבקש לטפל בה במפורש.
- [x] **משבצת ריקה — הושלם, אושר ונבדק:** המשבצת שאינה בשימוש הוסרה. CLOSED / USER-VERIFIED; אין להחזיר לרשימת המשימות אלא אם המשתמש מדווח על תקלה חדשה ומבקש לטפל בה במפורש.
- [x] **סוללה — הושלם ואושר, לא לפתוח מחדש:** אחוזי הסוללה של SMC ו־P23 מוצגים בתיבת ההודעות. המשתמש בדק ואישר; ראו סעיף 0.
- [x] **טיימר — זמן שלילי — הושלם, אושר ונבדק:** התצוגה ממשיכה לזמן שלילי לפי הכלל שסוכם. CLOSED / USER-VERIFIED; אין להחזיר לרשימת המשימות אלא אם המשתמש מדווח על תקלה חדשה ומבקש לטפל בה במפורש.
- [ ] **מעבר בין חלונות:** להוסיף כפתור/קיצור דרך למעבר בין חלונות העבודה JamZone ו־SBP, ב־SMC וב־AIRSTEP בלבד. **לא להוסיף כפתור בסרגל.**
- [ ] **הודעות מתחלפות:** להמשיך להציג הודעות כל 10 שניות בצבעים שונים ובאיזון חזותי: `תציג עצמך`, `לחיים`, `אינסטה`, `משקה הבית`, `מאיפה אתם?`; לתקן RTL כך שסימן השאלה בסוף `מאיפה אתם?`; אייקון Instagram נכון אם זמין; אייקוני כוסות/יין ודגלי אירופה.
- [ ] **READY:** צבע הטקסט צהוב באופן קבוע; להקטין את רוחב READY ותיבת ההודעות ב־15% בלי להקטין את גודל הטקסט.
- [ ] **MIDI INPUT:** כשהוא ריק להציג `MIDI INPUT` ולא מקפים.
- [ ] **MIDI command display tile:** למקם בין Solos ל־DLY.
- [ ] **FX tile:** למקם בין READY לתיבת ההודעות; שינוי פריסה בלבד, ללא שינוי התנהגות.
- [ ] **דגלים:** להוסיף דגל אחד בצד שיש בו כרגע דגל יחיד כדי לאזן את קבוצת הדגלים.
- [ ] **BPM tile:** המיקום הקיים נשאר; להציג BPM רק כשאין ערך, להציג מספר בלבד כשיש ערך, 2–3 ספרות, גודל מספר מוגדל ב־15%; חיבור מקור BPM לא נעשה בכוונה עד החלטה.
- [ ] **אריח כהה לא מזוהה:** לזהות את האריח הכהה בין MIDI INPUT ל־BPM; לא להסיר בלי זיהוי מדויק ואישור.
- [ ] **FX states:** שלושה מצבים ל־FX עם עדיפות ל־global mute.
- [ ] **Guitar mute:** בעת mute של הגיטרות, ארבעת האריחים המתאימים יהיו צהוב־כתום; בעת unmute יש לשחזר את המצבים האישיים.
- [ ] **סנכרון מצב:** בעת פתיחת הסרגל יש לבצע סנכרון מצב מלא.
- [ ] **Timer controls:** לבדוק לחיצה, כפתורים, double-click/long press, reset בזמן ריצה ופורמט קלט מספרי בלבד.
- [ ] **TT1/Fade/Option+Tab:** להמשיך בדיקות; הבהוב כל האריח למשך 0.5 שניות בשלוש השניות האחרונות.
- [ ] **MIDI false alerts:** לתקן התראות MIDI שגויות והתרעת Tkinter בזמן סגירה.
- [ ] **Version label:** לוודא שמספר הגרסה שמוצג בסרגל תואם לקובץ (בעבר הוצג v26 בעת פתיחת v28).
- [ ] **הפעלה מחדש:** לאחר בדיקות שכבות הגנה 1+2, להוסיף קיצור ייעודי להפעלה מחדש מלאה של הסקריפט (לא רק reset פנימי); התהליך הישן חייב להסתיים לפני פתיחת חדש כדי למנוע כפילות של שולחי MIDI; להציג אישור/לוג ברור.
- [ ] **בדיקות יציבות:** להמשיך בדיקות כשל מבוקרות ובדיקות ממושכות לפני אריזת DMG.

## 8. מצבי SMC ידועים — שמות ופקודות שזוהו

הערכים הבאים הם תצפיות היסטוריות מ־MIDI Monitor או מהקוד, לא בהכרח המיפוי הסופי לאחר שינויי 2026-10-15:

| מצב | הודעת SMC שנצפתה | משמעות |
|---|---|---|
| G2M | CH1 Note 2 (`D-2`), ON velocity 127 / OFF velocity 0 | מצב Guitar 2 Monitor |
| T.B. | CH1 Note 0 (`C-2`), ON 127 / OFF 0 | TalkBack |
| Bvox | CH1 Note 3 (`D#-2`), ON 127 / OFF 0 | Bvox |
| Solos | CH1 Note 11 (`B-2`), ON 127 / OFF 0 | Solos |
| FX/NG | CH1 Note 14 (`D-1`), ON 127 / OFF 0 | FX/Sidechain-related; יש לאמת את המצב המדויק |
| Global FX mute | CH1 Note 22 (`A#-1`), ON 127 / OFF 0 | Global mute |
| Guitar mute | CH1 Note 18 (`F#-1`), ON 127 / OFF 0 | Guitar mute |

תצפיות נוספות מ־AIRSTEP לפני שינויי 2026-10-15:
- TalkBack: CH1 CC62 value 0 (MIDI Monitor מציג ערוץ 1; ב־mido channel 0).
- G2M: CH14 CC55 value 0 (mido channel 13).
- Bvox: CH2 CC39 value 0 (mido channel 1).
- Solos: CH2 CC40 value 0 (mido channel 1).
- N.G./Sidechain: CH1 CC60 value 0 (mido channel 0).
אל תעתיק מיפויים אלה למצב החדש בלי לבדוק את ערוץ 16 וה־CC החדשים.

## 9. בדיקות שעברו, נכשלו או טרם הושלמו

### עברו / אושרו על ידי המשתמש
- G2M עובד באמצעות הודעת מצב IAC Bus 2; המשתמש אישר שהפתרון עובד ואין עוד האטה/תקיעה לאחר המעבר ל־Bus 2.
- בדיקות v10 של סקריפט הבקרה הראשי: המשתמש דיווח שה־Full Fade (המתנה של 5 שניות ואז fade של 15 שניות; פקודת P עוצרת playback ב־JamZone) עבד. קיימים פרטי בדיקה נוספים בהיסטוריה המקורית.
- v34 דווח כעובר בדיקת תחביר בעבר.
- MIDI Monitor 1.5.4 רואה הודעות יציאה מ־MainStage אל SMC.
- צילום MainStage מ־2026-10-15 אישר את מיפוי `SMC Guitar 2 Monitor ONLY` ל־CH1 Note On/Off D-2, `Send Value to: Bluetooth SMC-Mixer`.

### נכשלו / בעיות שאובחנו
- v29: דווח על קריסה בעת ההפעלה כאשר AIRSTEP Bluetooth לא היה זמין.
- הסקריפט לא מקבל הודעות שמופיעות ב־MIDI Monitor תחת `To SMC-Mixer Bluetooth`; פתיחת כניסת SMC אינה שקולה לקריאת זרם היציאה.
- ניסיון ההנחה ש־`Send Value to` מאפשר יעד נוסף לא אושר: ברשימה הופיע רק Bluetooth SMC-Mixer.
- קיימת אי־בהירות בין AIRSTEP CC41 `Airstep MuteGal-Mon` לבין CC42 `Airstep Guitar 2 Monitor ONLY`.

### טרם אומתו במלואם
- צבעי כל הכפתורים (T.B., Bvox, Solos, FX) אחרי המיפויים החדשים.
- סנכרון מלא בעת פתיחת הסרגל.
- כל תרחישי TT1/Fade, טיימר, סגירה, התראות MIDI, הפעלה מחדש מלאה ובדיקות ממושכות.
- מדידות אמיתיות של CPU וזיכרון לאפשרויות השונות; כרגע לא רלוונטי לעבור מ־Bus 2 משום שהמשתמש אישר ש־Bus 2 יציב.

## 10. החלטה לגבי ניטור הודעות MIDI יוצאות

המשתמש ביקש לבדוק אם הסקריפט יכול לקרוא ישירות הודעות MainStage → SMC כפי שמופיעות ב־MIDI Monitor. לאחר בדיקה:
- אין להמשיך לנסות לפתוח את כניסת SMC כדי לקבל הודעות יציאה; זה לא עבד.
- אפשרות CoreMIDI/כלי צד שלישי נדונה, אך המשתמש החליט ש־IAC Bus 2 הוא הפתרון המעשי המועדף: הוא כבר עובד, לא גורם להאטות, ונמנע מהצורך להפעיל כלי נוסף או לפתח רכיב חדש.
- **החלטה פעילה:** להשתמש בפקודות מצב כפולות ל־SMC ול־IAC Bus 2. לא לפתח CoreMIDI ולא להכניס כלי ניטור צד שלישי לתוכנית הנוכחית.

## 11. השלב הבא המדויק

- **המשימה הבאה ברשימה: הודעות מתחלפות בתיבת ההודעות** — להציג כל 10 שניות את ההודעות `תציג עצמך`, `לחיים`, `אינסטה`, `משקה הבית`, `מאיפה אתם?`, בצבעים שונים ובאיזון חזותי; לתקן RTL לסימן השאלה; להשתמש באייקון Instagram מתאים אם זמין, באייקוני כוסות/יין ובדגלי אירופה. אין לשנות קוד או ליצור גרסה חדשה עד לאישור מפורש.
- מעבר החלונות ב־SMC וב־AIRSTEP, IAC Bus 2, הפחתת גובה הסרגל, הסרת המשבצת הריקה ותצוגת הזמן השלילי של הטיימר מסומנים כהושלמו ואסור להחזירם לרשימה.
- הגרסה העובדת האחרונה שאושרה היא v77; כל שינוי עתידי יתבצע רק בגרסה חדשה, בלי לדרוס גרסאות קודמות.
- לפני מיפוי AIRSTEP CC41, לשאול אם `Airstep MuteGal-Mon` הוא G2M או פעולה נפרדת של Gal Monitor, אם השאלה עדיין רלוונטית למשימה.
- עדכון מסמך זה ייעשה כשיש החלטה או תוצאה חדשה מאומתת.

---

## 12. הנחיית פתיחה לשיחה חדשה

קרא תחילה את `PROJECT_CONTINUITY_UPDATED.md` ואת `PROJECT_STATUS.md`; אלה משקפים את המצב העדכני יותר מההערות ההיסטוריות בהמשך. אין להחזיר משימות סגורות לרשימה ואין לשנות קוד או גרסה יציבה ללא אישור מפורש. לפני מיפוי AIRSTEP CC41, שאל אם `Airstep MuteGal-Mon` הוא G2M או פקודה נפרדת, רק אם המשימה עדיין רלוונטית. השיחה המקורית היא `01 – Original – Full History`; שיחות המשך יקבלו מספור עוקב. IAC Bus 2, הפחתת גובה הסרגל ב־25%, הסרת המשבצת הריקה, תצוגת הזמן השלילי של הטיימר ומעבר החלונות JamZone/SBP באמצעות SMC ו־AIRSTEP הושלמו, אושרו ונבדקו — אין להחזירם למשימות פתוחות. הגרסה העובדת האחרונה שאושרה היא v77. המשימה הבאה היא הודעות מתחלפות בתיבת ההודעות, בכפוף לאישור מפורש לפני יצירת גרסה חדשה.

## 13. Floating Bar v46 checkpoint (2026-10-16)

- User confirmed v45 works in live use. Preserve v45 as the known-good rollback version; do not overwrite it.
- Created `/mnt/data/mainstage_jamzone_fade_controller_FLOATING_BAR_v46.py` based on v45, implementing only the latest requested UI changes:
  - Final-three-second countdown flash changes the status tile background only, toggling every 0.5 seconds; foreground/text color is not changed by the flash.
  - Lockout label now reads `Cancel Lockout` instead of `Lockout`.
  - First quick-fade procedure (P) displays `Fast Fade` with a live one-decimal-second countdown and green background.
  - Second quick-fade procedure (Down/L) displays `Loop Fade` with a live one-decimal-second countdown and blue background.
  - G2M/Cmd+Tab repeat window displays `Cmd+Tab` with a live countdown based on its 2-second repeat-window deadline.
  - Added `quick_started_at` so quick-fade countdown measures from the actual quick-fade trigger, not the original TT1 start.
- Validation: Python AST parse and `py_compile` passed; structural checks passed for labels, timer state, and background-only flashing. No live Tk UI, MIDI, MainStage, or JamZone test performed for v46.
- Exact run command after placing the downloaded file in Downloads: `python3 "$HOME/Downloads/mainstage_jamzone_fade_controller_FLOATING_BAR_v46.py"`.
- Next step: user tests v46 in the live setup and reports whether the background-only blink, Fast Fade/Loop Fade colors/countdowns, Cancel Lockout label, and Cmd+Tab countdown behave as requested.

## 2026-10-16 — v47 UI corrections
- Created `/mnt/data/mainstage_jamzone_fade_controller_FLOATING_BAR_v47.py` from v46; v46 and v45 were preserved.
- Changes: keep red FADEOUT status/background while `scenario_busy` remains true, so READY appears only after the scenario worker has finished its volume restoration; lockout tile now displays `CANCEL` on the top line and `FADEOUT` on the bottom; Fast Fade and Loop Fader labels are on the top line with the timer below and no `s` suffix; Cmd+Alt is shown on the top line with timer below; FX sidechain `no_ng` color changed from orange to green.
- Preserved the existing 500 ms background-only flashing logic.
- Verification: `py_compile` passed; structural checks passed for the requested labels, lockout display, scenario-busy status, green FX sidechain, and 500 ms flash. Live UI/MIDI/MainStage/JamZone behavior has not been tested in this environment.

## Update 2026-10-16 — v49 startup state refresh
- Created `/mnt/data/mainstage_jamzone_fade_controller_FLOATING_BAR_v49.py` from v48; v48 remains unchanged.
- On startup, after the UI/Bus 2 receiver has had 1.2 seconds to initialize, the script sends two passes of the existing MainStage button trigger CCs on IAC Bus 1, MIDI CH15: CC42 (G2M), CC45 (T.B.), CC41 (MM), CC39 (Bvox), CC40 (Solos). Each message uses value 127; commands are spaced 150 ms apart so MainStage can process and emit status mirrors.
- The v48 script contains no command mappings for DLY, CHR, DIS, O.D., FLG, WHA, or IR; per user instruction, no commands were invented for those tiles. FX Sidechain CC43 was excluded because the requested range was G2M–Solos and DLY–IR.
- Verification: `py_compile` passed; structural checks confirmed command sequence, forced CH15 output, and startup scheduling. No live MainStage/MIDI test was available here.
- Rollback: run v48 instead; v48 was not modified.

## 2026-10-17 — v80 approved implementation from v79 (not live-tested)
- User explicitly authorized implementing the full 12-item message-editor change list, investigating/preventing the Python macOS crash, and correcting Bluetooth disconnect status colors.
- Created `/mnt/data/mainstage_jamzone_fade_controller_FLOATING_BAR_v80.py` from v79. v79 and all earlier versions remain unchanged.
- Message tile: left half advances, right half goes to previous; long press and double-click open editor; hover cursor becomes hand; hover-only × skips current row for this process only; adjacent arrow controls navigate. Runtime skips are not persisted and reset on next process launch.
- Message editor: positioned dynamically below the 50 px bar; made non-transient/raised/focused to avoid first-open inactive controls; removed timer-default controls and the redundant Global checkbox; retained Apply All; preserves `show` in settings and automatically checks Show for newly entered non-empty rows on save.
- Presets: added Load and Save Set selectors with existing-set list and typed-name support; saving a named set prompts in Hebrew to add new rows to other sets, with individual checkboxes and All/None toggle. Bottom Save persists current displayed message configuration for the next launch. No hard-coded preset count; each set is capped at 50 rows by the editor.
- Timer default stays in the timer editor only, where the current default is shown and Save as default remains available.
- Crash analysis of `Python-2026-10-17-013318(1).ips`: EXC_BREAKPOINT/SIGTRAP, triggered thread stack passes through `NSEvent eventWithCGEvent`, HIToolbox `ProcessCapsLockSequenceKeyUp`, and `_dispatch_assert_queue_fail`. v80 replaces the `pynput` global listener (which constructs NSEvent on its listener thread) with a raw Quartz CGEvent tap reading physical keycodes and modifier flags; it does not call NSEvent for keyboard events. This targets the observed crash path, but prevention is not confirmed until tested on the user's Mac.
- Bluetooth status: READY mode tile no longer turns red or displays MIDI error for connection loss. MIDI INPUT tile changes to steady red after the existing fault delay, then returns to its original color when connection recovers; no repeated blinking.
- Verification: `py_compile` and AST parsing passed; 13 structural checks passed for the requested UI/status behavior. No live macOS GUI, Quartz permission, MIDI, MainStage, JamZone, or crash-reproduction test was possible in this environment.
- Exact run command after downloading to Downloads: `python3 "$HOME/Downloads/mainstage_jamzone_fade_controller_FLOATING_BAR_v80.py"`.
- Current status: v80 is a candidate for user live testing, not a confirmed stable version. v77 remains the last version documented as user-confirmed stable; v79 is preserved as the immediate source/rollback for this batch.
