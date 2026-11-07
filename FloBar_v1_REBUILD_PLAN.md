# FloBar v1 — תוכנית מימוש, החלטות ובדיקות

**מצב:** מימוש ראשוני נבנה לבדיקת משתמש ב־2026-10-23. תוכנית זו מתארת גם מה נבנה וגם שערי קבלה שטרם עברו. אין להסיק הצלחה תפעולית מבנייה/בדיקות ליבה בלבד.

## 1. מקור ואסטרטגיה

- בונים אפליקציה חדשה ב־Swift, AppKit לסרגל ו־SwiftUI להגדרות; הקוד החדש נמצא ב־`FloBar_v1/`. קובצי v97/v99 אינם נערכים.
- Apple Silicon בלבד, ללא Intel. יעד ה־Deployment בקוד הוא macOS 11 כדי לכוון ל־M1 הראשון; תאימות מעשית לכל מערכת 11–27 טעונה בדיקה. אין תמיכה ב־Windows/iPad/Android ב־v1.
- v99 נשאר מקור אמת להתנהגות המאושרת: SHA‑256 `7e64e49aa0ba7f547312b5c38a68933630e209be40838f1a2dfba40de440b0ce`.
- ניסוי v99: CPU חציון כ־43.92%, p95 כ־44.15%, peak כ־44.46%, RSS כ־3.0GiB; heartbeat נתקע 6 שניות והתהליך הוחלף. בדיקה קצרה ראתה צורך בקליק ראשון; חצים עבדו במגע ישיר, double-click נכשל ו־long press עבד. המשתמש אימת שה־X האדום ב־v99 מסתיר בלבד.
- בגרסת v99 נשמרו אותן הגדרות תוכן ו־MIDI בלי להעתיק ערכים חסרים. קובץ `message_settings.json` שסופק נקרא בלבד והומר לגיבוי v1 נפרד. אין כתיבה למקור.

## 2. MIDI, תצוגה ופקודות — מפרט מחייב

- `AIR`, `SMC`, `P23` מוצגים בשלישים שווים. צבע ניתוק כתום כהה חל רק על אזור שם הבקר; מצב שעדיין לא נבדק נשאר ניטרלי. פעולת בקר אחרונה נשארת באזור הבקר ששלח אותה עד אירוע חדש; כל HID מוצג בשדה P23 בלי לייחס סוג בקר מסוים.
- raw MIDI מוצג בלי ערוץ, למשל `cc15v0`. שמות מלאים וקיצורים מאושרים מוצגים בנפרד: `G2M`, `MM`, `bvM`, `slM`, `fxSC`, `TT`, `TB`, `L`, `FO-L`, `.A.S`, `GBk`, `gtM`, `fxM`, `fbH`, `j/sF`.
- שמות/קיצורים ידועים: AIRSTEP CH16 CC42 G2M, CC41 MM, CC39 bvM, CC40 slM, CC43 fxSC, CC44 TT, CC45 TB, CC100 L, CC101 GBk; Note18 gtM, Note22 fxM, Note93 fbH, Note94 j/sF. SMC aliases מבוססים על הקוד v99. P23 משדר HID בלבד; אחוז סוללה מופיע רק מנתון מזוהה. פקודה לא ידועה נשמרת כ־raw, אין להמציא שם.
- פלט IAC מוצג רק כשהגיע בעקבות מקור, ומתחלף עם המקור כל 3 שניות. צבע כפתור איננו משתנה על סמך שליחת פקודה: רק feedback אמין מ־IAC Bus 2 מעדכן אותו. בתחילת session ולאחר disconnect הצבע ניטרלי/Unknown.
- סנכרון startup נבנה לפי v99: שני passes זהים, 8 אירועים בכל pass, לפי CC42, CC45, CC41, CC39, CC40, Note22, Note18, CC43; 150ms מתוכנת בין אירועים. סך 16. שורת סיכום v99 שהחסירה Note18 תועדה, לא תוקנה. בדיקת התאימות הנדרשת היא capture MIDI בפועל, לא מחרוזת log.
- הסרת CC60, CC57 ו־CC0. CC43 v127 מפעיל bypass ו־v0 מבטל. במהלך quick fade/Down: v127 בתחילה ו־v0 אחרי 7s.

## 3. התנהגות פקודות ואפליקציות

- עצמאית: CC22 מביא Jamzone לחזית, מוודא focus, ממתין לפי ההגדרה הקצרה האמינה ושולח P יחיד. במסך מפוצל מחזירים את `SongBookPro` לחזית. בתוך TT1 נשמר מסלול quick fade נפרד.
- CC100: רק כאשר עצמאי, split ו־SBP בחזית — AutoScroll `.A.S`; לאחר Down או כש־Jamzone בחזית — Loop `L`. quick fade+L מציג `FO-L`. בדיקת הממשק נדרשת כדי לאמת מצב חזית אמיתי.
- CC101 הוא Option+Left רק כש־Jamzone בחזית ובמצב שאינו split. אין לבצע HID/keyboard automation כשהתנאי לא מתקיים.
- P/L restore (שתי לחיצות להחזרת Jamzone ל־Loop) שייך לנוהל בדיקה עם המשתמש בלבד. מוצר ו־Guardian לא משדרים P/L/CC20/CC21 כ־recovery.
- תיבות ההודעות/חצים הן חלק פנימי של FloBar; רק רמת הסוללה מגיעה ממערכת חיצונית. אין צלילי התרעה.

## 4. TT1 ובונה Secondary Actions

ה־UI משמר את ערכי v99 ומחבר את עריכתם למנוע רצפים: trigger CC44 CH16; חלון G2M/TB 5s; fade מלא 15s; quick fade 1s; CC22 P target 1s; Down P target 1.5s; זמן התייצבות split 0.5s/רגיל 0.05s; P→L, L→Up, Up→restore כל 0.5s; CC43 restore אחרי 7s; CC20 full-volume restore אחרי 30s; חלון חזרה 2s שמתחדש בפקודה תואמת; lockout ביטול 2.5s. G2M/TB/quickfade/Down ניתנים להפעלה/כיבוי בנפרד. שינוי הגדרות אינו משדר MIDI.

בונה ה־TT1 משפיע על אותם ערכים שה־runtime קורא; הוא אינו מציג UI שאינו מחובר. ההתנהגות נשמרת לפי המפרט המאושר. בדיקות רצף מול התקנים עדיין חסרות.

## 5. אשף, Profiles ו־Message Sets

- אשף ראשון כולל בקרי MIDI וערוצים, effects, I/O, טיימר עד 5 שעות, שתי הודעות ראשוניות, FX/Sidechain ו־High/Critical; כל checkpoint נשמר, והמשך מתבצע מהשלב שהופסק. לאחר הצלחה מוצעת פתיחת Builder מיד או מאוחר יותר.
- Settings מאפשר rerun מלא או בחירת תחומים; תחום שלא נבחר נשאר ללא שינוי. MIDI Learn מאזין לאירוע הבא שנכנס, מתעלם מ־IAC/note-off, ומשייך AIRSTEP לפקודה הראשית ו־SMC/P23/HID כ־alias. לא נשלחת בדיקת MIDI אוטומטית.
- Profiles נפרדים מאפשרים שמות, מיפויים, צבעים, סדר, הצגה והצמדה. Unmapped controls כבויים. הפקודה ל־Sidechain היא CC43; CC57/60/0 לא קיימות במיפוי ברירת מחדל.
- Message Sets: Sets נפרדים, Preview, Load, Enter לטעינת שם, Add new name, אישור דריסה בשפה האנגלית, אפשרות לבחור שם חדש, Save/Discard/Cancel, X זמני, Set Ready text/color, Set reorder. Snapshot קודם גלובלי אחד נשמר לפני דריסה, נשאר מעבר ל־Quit עד snapshot הבא. Undo/Redo עד 20 פעולות הגדרה במופע, בלי MIDI; `⌘Z` בשדה טקסט נועד לטקסט.
- מגבלת הודעה 40 grapheme clusters; marquee חד־כיווני לפי כיוון השפה הראשית, במהירות שמנסה לסיים בתוך משך התצוגה. אין צליל. בדיקות שפות/אימוג׳י ורינדור עדיין נדרשות.
- גיבוי/ייבוא: גיבוי חדש בתיקיית v1, ייצוא ידני, בחירת קובץ legacy מפורשת, schema validation ו־preview; לפני import של קובץ FloBar נשמר safety backup של ה־config הנוכחי. קובץ legacy המקורי לא נכתב.

## 6. ממשק והתראות

- סרגל borderless ו־non-activating; `acceptsFirstMouse` עבור קליק ראשון. drag handle ייעודי. Top/Bottom אופקיים; Left/Right אנכיים. drag דומיננטי בציר אופקי מצמיד לצד; אנכי מצמיד למעלה/למטה.
- חצים גדולים, חצי שקופים וללא מסגרת, מופיעים באזור 20% הקצה לפי צד הסמן; × נשאר בצד החיצוני שלו. לחיצה ארוכה/double-click על מלוא תיבת ההודעות פותחת Settings.
- הודעות נראות בשטח ההודעות; MIDI raw נמצא באריח `MIDI INPUT` הייעודי של הסרגל. פריסה זו שומרת על שתי הפונקציות הקיימות בתצוגות נפרדות.
- High/serious כתום בשטח 3% משמאל; Critical שני מדי חום בקצוות, מקטעים עד 5% מכל צד, אדום/צהוב מתחלף כל 5s. מתבסס על `ProcessInfo.thermalState`, לא מד מעלות ולא polling תכוף.
- Undo/Redo בתפריט וקיצור; restart תפריט דורש אישור; `§` מבקש restart מלא מה־Guardian; Hide הוא hide בלבד; Quit מפורש עוצר אפליקציה ו־Guardian.

## 7. Guardian ונתונים

- Guardian Swift נפרד; heartbeat UI פעם בשנייה, בדיקה פעם בשנייה, hang ב־5s, sleep suspends check, עד 15s grace אחרי wake; עד 3 automatic restarts בדקה, אחר כך עצירה. `§` ידני מחוץ למכסה; interrupted MIDI ננטש, אין replay. קובץ `guardian.lock` קבוע לנעילה וקובץ PID/uptime מתעדכן.
- Single instance באפליקציה וב־Guardian; Guardian מאמת bundle ID/path של FloBar לפני איתות; Quit command מפורש; לא רושמים helper בנפרד ל־login. Login Item opt-in, נתמך ב־API החל מ־macOS13.
- Settings: `~/Library/Application Support/FloBar`; logs: `~/Library/Logs/FloBar`. אין Full Disk Access, directory scanning או network requests. Input Monitoring opt-in בלבד; Apple Events/Automation לפי פעולה יזומה.
- הליבה שומרת JSON אטומית; כשל בטעינת קובץ קיים לא גורם לדריסתו, ועובר ל־read-only עד import מפורש. Snapshot import safety נעשה לפני החלפה.

## 8. תקלות המערכת והסטטוס

- **AirStep Bluetooth:** CoreMIDI notifications מפעילים endpoint rediscovery ו־reconnect. שורש התקלה לא ידוע ולא נטען שנפתר. צריך A/B חוזר BT מול כבל, עם זמני endpoint/MIDI events וגרסת firmware.
- **Mouse/focus:** מימוש non-activating ו־first-mouse קיים; לא אומת ב־UI אמיתי. חצים, double-click, long press, כיסוי כל hit area ו־click בלי שינוי front app הם שער קבלה.
- **Crash/hang:** Guardian קיים בקוד/חבילה; לא בוצע fault injection ולכן recovery אינו מאומת. נדרש לבדוק crash, UI freeze, restart limit, `§`, Hide/Quit, sleep/wake, והיעדר MIDI replay.
- **משאבים:** לא נמדדו. אין טענה שהיעדים 7% רגיל/10% peak הושגו. יש למדוד baseline, idle, MIDI/UI, wakeups, RSS, pressure, thermal ו־latency/jitter על M1. יציבות ותגובה בזמן אמת קודמות ליעד CPU.

## 9. תוכנית קבלה אחרי בדיקת המשתמש

1. לפתוח את האפליקציה רק כשהמשתמש ליד המחשב; לקיחת capture של startup sync; לא להשתמש ב־Spotify ולא באינטרנט.
2. בדיקה קצרה: first click בלי activation, חצים/X/long/double, Load/Save Set, preset Enter, MIDI Learn, feedback color, P23 HID/battery, ארבע פריסות, focus Jamzone→SBP, CC22/CC100/CC101.
3. המשתמש משיב ל־P או L ששלחה בדיקה באמצעות P או L נוסף להחזרת Jamzone ל־Loop; לא לבצע תוספות אוטומטיות מהמוצר.
4. הדמיות Guardian במעבדה: crash, hang>5s, recovery marker, restart limit, manual §, explicit Quit, sleep/wake; לא לנתק חשמל.
5. מדידת CPU: baseline 10 דקות, FloBar idle 15 דקות, פעילות MIDI/UI 15 דקות; דגימה 1Hz, להחריג 30 שניות של מעבר; mean rolling 5 דקות ≤7% וה־peak ≤10% יחד, לדווח גם כל process. מדוד latency/jitter בכל מצב.
6. רק אחרי תוצאות קצרות תקינות, לתכנן soak של עד 5 שעות (אורך הופעה מרבי שאושר) עם אישור המשתמש ונוכחות/הנחיות פעולה ברורות.

## 10. סטטוס סעיפים

החלטות המוצר נעולות לפי האישורים האחרונים. הטבלה `FloBar_CHANGE_TRACKER.csv` מסמנת לכל דרישה אם המימוש קיים, חלקי, עתידי או דורש בדיקה חיה. אין לסמן סעיף כ־Done רק מפני שהקוד התקמפל. v1 היא חבילת בדיקה מקומית ולא הפצה/notarized.
