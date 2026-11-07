# FloBar v1 — מצב המימוש

**עודכן:** 2026-10-23  
**מצב:** חבילת בדיקה מקומית נבנתה; לא הושקה ולא נשלחו פקודות MIDI בזמן הבנייה.

## תוצרים

- אפליקציה מקומפלת: `FloBar_v1/dist/FloBar.app`
- מקור Swift ומבחנים: `FloBar_v1/`
- גיבוי הגדרות ראשוני מתוך `message_settings.json` שסופק: `FloBar_v1/Backups/FloBar_v1_initial_backup.json`. הוא מכיל את שני ה־Message Sets והגדרות הטיימר שנמצאו; קובץ המקור לא שונה.
- קובץ סטטוס זה, מסמך המשכיות, תוכנית הבנייה/בדיקות, ו־`FloBar_CHANGE_TRACKER.csv` הם ארבעת מסמכי המעקב.

## בנייה ובדיקות שבוצעו

- מערכת הבנייה: macOS 26.6.2, arm64, Swift/Xcode מקומי.
- יעד קומפילציה מוצהר: macOS 11 ומעלה, Apple Silicon בלבד. הקומפילציה אינה מוכיחה פעולה בכל גרסת macOS או בכל דגם M1 ישן.
- `swift test --scratch-path .build`: **7 בדיקות עברו, 0 נכשלו**. הן מכסות רצף סנכרון הפעלה, ערכי TT1 מאושרים, תוויות MIDI, מגבלת 40 grapheme clusters, כיוון שפה, הסרת CC0/57/60 ושימור נתוני Codable כולל `image`.
- חבילת `.app` נבנתה וחתימת ad-hoc אומתה (`codesign --verify --deep --strict` הצליח). קובצי FloBar ו־Guardian הם arm64.
- בדיקת ה־round-trip של קובץ הגיבוי הצליחה; נוצרו שני Sets מהקובץ שסופק.
- קובץ v99 נשאר ללא שינוי; SHA-256 שנבדק: `7e64e49aa0ba7f547312b5c38a68933630e209be40838f1a2dfba40de440b0ce`.

## מה v1 כולל בקוד

- Swift/AppKit/SwiftUI, סרגל ללא Title Bar, קליטה ראשונית בלי לדרוש קליק להפעלת החלון, ידית גרירה לארבע שפות, ופריסה אנכית בצדדים.
- פקודות/כינויים מאושרים, תצוגת MIDI גולמית, זיהוי HID כללי בתצוגת P23, feedback צבעוני רק מ־IAC Bus 2, סנכרון הפעלה של 16 אירועים בשני passes, 150ms מתוכנת בין אירועים.
- CC22 מפעיל את JamZone לפני P; CC100 מקבל Loop או `.A.S` לפי הקשר; CC101 מוגבל ל־JamZone בחזית ובמצב שאינו split.
- רצף TT1, חלון הסתעפות/חזרה שניתן להגדיר, quick fade/Down, CC43 v127 ולאחר 7s CC43 v0, ובונה הגדרות שמחובר למימוש הרצף.
- אשף עם checkpoints והיקף מלא/חלקי, Profiles, צבעים/סדר/מיפויים, MIDI Learn חד־פעמי לאירוע הבא, Message Sets, שמירה/דריסה עם אזהרה, snapshot קודם יחיד, Undo/Redo, גיבוי/ייבוא והודעות עד 40 תווים עם marquee.
- חיווי High/Critical של `ProcessInfo`, שמירת נתונים אטומית, קובצי לוג/הגדרות בתיקיות FloBar בלבד, וה־Guardian המופרד.

## מה עדיין לא אומת — לכן אין להשתמש עדיין בהופעה

1. לא פתחנו את האפליקציה: הפעלה מריצה סנכרון MIDI מאושר ועלולה לשנות מצב תוכנות ההופעה. לא נגעתי ב־MainStage, Jamzone, SongBookPro או בבקרים.
2. לא בוצעו capture של 16 אירועי MIDI על IAC, בדיקת קליק ראשון/חצים/double-click, בדיקות Automation/Accessibility/Input Monitoring, בדיקות מיקום/מסכים, או בדיקות HID של P23.
3. לא בוצעה הדמיית crash/hang או אימות restart של ה־Guardian; המימוש התקמפל בלבד. אירועי recovery לא נבדקו.
4. לא נמדדו CPU, RSS, wakeups, חום, latency או jitter, ולא נערך soak. יעד ≤7% רגיל ו־≤10% peak **אינו מוכח**.
5. ניתוקי AirStep Bluetooth עדיין ללא אבחון שורש. v1 משתמשת ב־CoreMIDI endpoint rediscovery/reconnect; יש להשוות Bluetooth/כבל ולתעד נתוני ניתוק לפני מסקנה.
6. תכונת ה־Login Item זמינה ב־macOS 13 ומעלה דרך API של Apple; ב־macOS 11–12 היא נשארת כבויה/לא נתמכת. תכונות אחרות ב־macOS 11 לא נבדקו על M1 ישן.
7. `.app` נחתמה ad-hoc בלבד, אינה notarized, ואינה הפצה מסחרית.

## הרשאות ש־macOS עשוי לבקש בשימוש

אין בקשה ל־Full Disk Access או לסריקת תיקיות. שליטה ב־Jamzone/SongBookPro עלולה להפעיל בקשת Automation/Apple Events כשהמשתמש מפעיל פקודה כזו; קיצור גלובלי Up/Down/§ דורש opt-in מפורש ו־Input Monitoring. אלה הרשאות מערכת נפרדות מאישור רישיון Xcode.

## קובצי הייחוס שלא נערכו

`mainstage_jamzone_fade_controller_FLOATING_BAR_v97.py`, קובץ v99, תיקיות בדיקה, וגיבוי המקור שסופק נשארו מחוץ לשכתוב. קובץ הגיבוי החדש נמצא רק תחת `FloBar_v1/Backups/`.
