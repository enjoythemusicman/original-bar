# FloBar v1 — המשכיות

**עדכון אחרון:** 2026-10-23  
**היכנס ישר למצב הנוכחי:** v1 נבנתה כחבילת בדיקה. אין להפעיל אותה ללא המשתמש; startup sync שולח MIDI ל־IAC.

## קבצים

- תוכנית עבודה/בדיקות: `FloBar_v1_REBUILD_PLAN.md`
- מצב המימוש והבדיקות: `FloBar_v1_PROJECT_STATUS.md`
- מעקב פר סעיף: `FloBar_CHANGE_TRACKER.csv`
- קוד: `FloBar_v1/Sources/`
- בדיקות: `FloBar_v1/Tests/`
- אפליקציה: `FloBar_v1/dist/FloBar.app`
- גיבוי message settings שהומר: `FloBar_v1/Backups/FloBar_v1_initial_backup.json`

## מצב מדויק

- נבנתה אפליקציית macOS arm64 ב־ad-hoc signature; לא notarized.
- `swift test`: 7/7 עברו ב־macOS 26.6.2/arm64.
- `codesign --verify --deep --strict` עבר.
- האפליקציה וה־Guardian לא הושקו בזמן האימות. לא נשלחו MIDI, מקשים או Apple Events להופעה.
- אין נתוני v1 למדדי CPU, זיכרון, חום, קליטה/latency, reconnect או guardian recovery. אין להסיק שיעד 7% הושג.
- AirStep Bluetooth root cause, בדיקות קליקים/focus, תרחישי crash/hang ו־soak של עד 5 שעות נשארו לאימות פיזי.

## כללי המשך

1. לשמור את v97/v99, `message_settings.json`, תוצאות v99 והגיבויים ללא שינוי. גיבוי v1 נפרד נמצא תחת `FloBar_v1/Backups`.
2. לא להשיק/לבדוק MIDI בלי שהמשתמש נוכח, שמבין שכל launch שולח את startup sync ושיכול להשיב Jamzone ל־Loop לפי נוהל P/L שסוכם.
3. תחילה לבצע בדיקת קבלה קצרה, מודרכת ובנוכחות המשתמש: קליק ראשון בלי activation, צבעים לפי IAC feedback, שתי פעולות חצים, Message Sets, HID/P23, focus ופוקוס חוזר ל־SBP. אחר כך בלבד לקבוע soak.
4. למדוד FloBar ו־Guardian בנפרד וביחד, baseline באותו setup, וגם RSS/wakeups/thermal ו־MIDI latency/jitter. עד ≤7% רגיל ו־≤10% peak הם יעדי קבלה, לא תוצאות נוכחיות. לא להקריב יציבות/latency.
5. בדיקות Auto/Manual restart יבוצעו עם fault injection מבוקר, בלי replay של פעולות MIDI/TT1. לא לבדוק כשל באמצעות ניתוק חשמל.
6. בכל בעיה, לתעד OS/build, שעה, פקודה/מקור, מצב Jamzone/SBP, לוגים ו־Activity Monitor. אין לשלוח crash logs או פרטי Message Sets מחוץ למחשב בלי בקשת המשתמש.

## הרשאות

build/tests דורשים Xcode/SwiftPM ומטמון קומפילציה. בשימוש הממשי macOS עשוי לבקש Apple Events/Automation עבור שליטה יזומה ב־Jamzone/SongBookPro; Input Monitoring מבוקש רק אם המשתמש מפעיל את קיצור ה־HID הגלובלי. אין צורך ב־Full Disk Access. האחסון של האפליקציה מוגבל ל־`~/Library/Application Support/FloBar` ו־`~/Library/Logs/FloBar`; ייבוא legacy הוא בחירת קובץ מפורשת.
