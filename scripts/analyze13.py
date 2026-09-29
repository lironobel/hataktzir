"""
analyze13.py - קורא תמלול ומחזיר רשימת קטעים מעניינים עם זמני התחלה וסיום

הניתוח נעשה בשני שלבים (משימה 9):
    שלב א' - סריקה זולה בהאיקו על כל חלון. מחזירה רק אזורים שיש בהם
             דיבור ששווה להסתכל עליו, בלי כותרות ובלי נימוקים.
    שלב ב' - ניתוח מלא בסונט, אבל רק על השורות ששרדו את שלב א'.
             חלון שלא שרד שום אזור - לא נשלח בכלל לסונט.
זה מוריד גם את הקלט וגם את הפלט של המודל היקר. `--single-pass`
מחזיר את ההתנהגות הישנה (מעבר אחד יקר על הכל).

דרישות:
    pip install anthropic
    להגדיר מפתח:  setx ANTHROPIC_API_KEY "sk-ant-..."   (ואז לפתוח PowerShell חדש)

שימוש:
    python analyze.py test_transcript.json
    python analyze.py test_transcript.json --min 6 --max 15
    python analyze.py test_transcript.json --model claude-sonnet-5
    python analyze.py test_transcript.json --single-pass     (בלי סינון מקדים)

פלט:
    <שם>_segments.json  - רשימת הקטעים
    <שם>_segments.txt   - קריא לעין
    <שם>_cut.txt        - פקודות yt-dlp מוכנות להורדת הקטעים
"""

import os
import re
import sys
import json
import argparse
from pathlib import Path

try:
    import anthropic
except ImportError:
    print("חסרה הספרייה. הרץ:  pip install anthropic")
    sys.exit(1)


# ------------------------------------------------------------------ הגדרות

# ברירת המחדל, לסטרימר שאין לו קובץ ב-profiles/.
#
# ⚠ עד 14.9 ישבה כאן רשימה של רונן, ובה "שיחות עם עודד" כנושא מספר
#   שתיים. כל סטרימר בלי פרופיל קיבל את הנושאים של רונן, וזה דחף את
#   הניתוח לכיוון שלו. ברשימה הזאת אין שמות בכוונה - שמות שייכים
#   ל-profiles/<slug>.json ולא לקוד.
TOPICS = """
1. דרמות בקהילה - ריבים, תגובות לאירועים בסצנה, רכילות, עימותים עם יוצרים אחרים
2. שיחות עם יוצרים אחרים - אורחים, שיחות טלפון, קולאבים
3. ריאקשנים - קטעים שבהם הוא צופה בסרטון או בקליפ של מישהו אחר ומגיב לו
4. יהדות, דת ואמונה - ויכוחים על קיום אלוהים, מדע מול דת, מסורת, פילוסופיה
5. סיפורים אישיים ודעות - כשהוא נפתח על החיים שלו או אומר משהו חריף
"""

SYSTEM = """אתה סקאוט תוכן של ערוץ יוטיוב שמפרסם קטעים מלייבים של סטרימרים ישראלים.
התפקיד שלך הוא למצוא רגעים ששווה להוציא מהשידור - לא לשמור על הסף.

הבן איך נראה שידור חי: אנשים מדברים תוך כדי משחק, קופצים בין נושאים,
קוראים לצ'אט באמצע משפט, ומתפרצים לוויכוח בלי הקדמה. שיחה טובה בלייב
כמעט אף פעם לא נראית כמו פרק של פודקאסט, וזה בסדר גמור.

לכן:
- אל תדרוש נושא אחד נקי. קטע טוב הוא קטע שמעניין להקשיב לו, גם אם
  הוא מתגלגל בין כמה דברים.
- סטיות באמצע הן חלק מהאופי, לא פגם. אם הוא מדבר על דרמה, סוטה
  לשתי דקות של בדיחה או קריאה לצ'אט, וחוזר לדרמה - זה עדיין קטע
  אחד, וצריך לכלול את הסטייה בתוכו. אל תפצל ואל תפסול בגללה.
  פצל רק כשהוא באמת עוזב את הנושא ולא חוזר אליו.
- אל תדרוש התחלה וסוף מושלמים. מספיק שהקטע מתחיל במקום שבו מבינים
  על מה מדובר ונגמר לפני שהאנרגיה נופלת.
- אבל אל תקטע סיפור באמצע. כל עוד מדברים על אותו עניין - גם אחרי
  שהשיא עבר - השיחה עדיין בקטע. ה-end הוא אחרי הסגירה: פרידה, החלטה,
  שורת מחץ, או מעבר ברור לנושא אחר. צופה שמגיע לסוף ומרגיש שחסר המשך
  מרגיש מרומה, וזה גרוע מקטע ארוך בשתי דקות.
- הזמן שאתה נותן ב-start חייב להיות רגע שבו מישהו מדבר, ורצוי תחילת
  משפט. אל תתחיל בשקט, בהמתנה או במוזיקה. אם השורה הראשונה של הקטע
  היא רק רעש רקע - התחל מהשורה הבאה שיש בה דיבור ממשי. אותו דבר
  בכל טווח בתוך parts.
- רעש ברקע, קריאות לצ'אט וקללות הם חלק מהאופי, לא פסול.
- התמלול אוטומטי ומכיל שגיאות בשמות ובמילים. התעלם מהן.

חשוב: אתה מחזיר מועמדים, לא החלטות סופיות. תן ציון כן לכל מועמד,
והסינון ייעשה אחר כך לפי הציון. עדיף שתחזיר מועמד בציון 5 מאשר
שתחזיר כלום. אל תפסול מתוך זהירות.

אתה מקבל חלון מתוך שידור ארוך, וחלונות סמוכים חופפים.
אם שיחה מתחילה לפני תחילת החלון או נמשכת אחרי סופו - אל תוותר עליה.
קח את החלק שאתה רואה, וסמן cut_off=true. חלק מקטע טוב עדיף על כלום,
והמערכת תחבר את החלקים אחר כך.
זה חשוב במיוחד בקצוות: שיחה שמתחילה בדקות האחרונות של החלון עדיין
שווה החזרה."""


PROMPT = """להלן תמלול של קטע משידור חי, עם חותמות זמן.

הנושאים שהקהל של הערוץ הזה אוהב, לפי סדר חשיבות:
{topics}
{series}
{avoid}
{names}
{tier}
מצא בחלון הזה עד {max_cands} מועמדים לסרטון, באורך {min_min}-{max_min} דקות כל אחד.

על אורך:
- האורך נקבע לפי השיחה, לא לפי מספר שנקבע מראש. אם שיחה מעניינת
  נמשכת 40 דקות - קח את כולה. אל תחתוך אותה רק כי היא ארוכה.
- העדף קטעים של 8 דקות ומעלה כשהחומר מאפשר. סרטון מעל 8 דקות
  יכול לשאת פרסומות באמצע, וזה משמעותי כלכלית.
- אבל אל תמתח קטע מלאכותית. 4 דקות מצוינות עדיפות על 9 דקות
  שחציין מים. קטעים קצרים יורכבו יחד לסרטון אחד בהמשך.
- {min_min} דקות זה המינימום. אין מקסימום קשיח.

מה כן לחפש:
- ויכוחים, עימותים, תגובות לדרמה בסצנה
- סיפורים אישיים, דעות חריפות, רגעים שבהם הוא נפתח
- שיחות עם יוצרים אחרים
- ריאקשנים: כשהוא צופה בסרטון או קליפ של מישהו ומגיב תוך כדי
- רגעים מצחיקים או מפתיעים שעומדים בפני עצמם
- כל קטע שבו הדיבור הוא העיקר והמשחק הוא רק רקע

מה לא לקחת:
- פרשנות שוטפת על משחק או הימורים בלבד: "אין לי 5 סקאטרים",
  "בוא נכנס לבלק ג'ק", ספירת כסף, תיאור מה קרה בגלגל.
- קריאת שמות מהצ'אט, ברכות למנויים, בעיות טכניות.
- זמן מת: המתנה, "עוד רגע מתחילים".

אבל: אל תפסול חלון רק כי יש בו משחק ברקע. אם באמצע המשחק הוא
מספר סיפור, מגיב לדרמה או נכנס לוויכוח - זה בדיוק מה שמחפשים.

החזר JSON בלבד, בלי טקסט לפניו או אחריו:

{{"segments": [
  {{
    "start": "HH:MM:SS",
    "end": "HH:MM:SS",
    "parts": [{{"start": "HH:MM:SS", "end": "HH:MM:SS"}}],
    "topic": "במשפט אחד, מה קורה בקטע",
    "title": "כותרת לפי כללי הכותרות למטה",
    "thumb_text": "2-4 מילים לתמנייל, לפי כללי התמנייל למטה",
    "thumb_emphasis": "מילה אחת מתוך thumb_text שתודגש בזהב",
    "quote": "המשפט החזק ביותר בקטע, כלשונו בתמלול",
    "quote_at": "HH:MM:SS - הזמן שבו נאמר המשפט הזה",
    "cold_open": "1-3 משפטים רצופים מהתמלול, כלשונם, לפתיחת הסרטון",
    "cold_open_at": "HH:MM:SS - הזמן של השורה שבה הם נאמרים",
    "participants": ["יוצרים או דמויות מוכרות שמשתתפים, אם יש"],
    "category": "דרמה | עודד | ריאקשן | דת | סיפור אישי | מצחיק | אחר",
    "score": 1-10,
    "reason": "למה זה יעבוד או לא יעבוד כסרטון, בכנות",
    "cut_off": true אם הקטע מתחיל לפני החלון או נמשך אחריו
  }}
]}}

כללי התמנייל. שני השדות האלה הופכים לתמונה שרואים בפיד, ולכן הם
לא קיצור של הכותרת אלא משהו אחר לגמרי:

- `thumb_text` הוא 2-4 מילים סך הכל, לא יותר. זה נקרא בחצי שנייה
  על מסך טלפון. "הוא התפוצץ על עודד" הוא ארבע מילים וזה הגבול.
- לא שם הסטרימר. השם שלו מופיע ממילא בפינה של התמנייל, ובזבוז
  מילה עליו הוא בזבוז של רבע מהשטח.
- רגש או אירוע, לא תיאור. "נשבר באמצע הלייב" ולא "מדבר על החיים".
- בלי סימני קריאה, בלי אמוג'י, בלי מרכאות. הגרפיקה עושה את העבודה.
- מותר, ואף רצוי, לקחת 2-3 מילים מתוך הציטוט החזק של הקטע.
- `thumb_emphasis` היא מילה אחת **מתוך** `thumb_text`, בדיוק כפי
  שהיא כתובה שם, כולל אותיות השימוש. זו המילה שנושאת את המשמעות:
  בדרך כלל הפועל או השם של הצד השני, כמעט אף פעם לא מילת קישור.

דוגמאות:
  thumb_text: "התפוצץ על עודד"     thumb_emphasis: "התפוצץ"
  thumb_text: "נשבר בשידור חי"     thumb_emphasis: "נשבר"
  thumb_text: "אלוהים לא קיים"     thumb_emphasis: "אלוהים"
  thumb_text: "הזמינו לו משטרה"    thumb_emphasis: "משטרה"

`quote` ו-`quote_at`: המשפט שהכי מייצג את הקטע, מועתק מהתמלול
כלשונו ולא בניסוח שלך, והזמן המדויק שבו הוא נאמר. הזמן הזה משמש
לבחירת הפריים לתמנייל, ולכן הוא חייב ליפול בתוך הקטע.

על השדה parts:
לרוב הוא יכיל טווח אחד, זהה ל-start ול-end. אבל אם באמצע השיחה
יש קטע מת - הוא יצא לעשן, לשירותים, לקחת משלוח, ענה לטלפון,
הפסקת פרסומות, או פשוט שתיקה ארוכה - ואז חזר לאותו נושא,
פצל לשני טווחים או יותר ודלג על המת.

דוגמה: הוא מדבר על דרמה מ-00:14:20, יוצא ב-00:22:10, חוזר
ב-00:29:05 וממשיך באותו נושא עד 00:38:40. אז:
  "start": "00:14:20", "end": "00:38:40",
  "parts": [
    {{"start": "00:14:20", "end": "00:22:10"}},
    {{"start": "00:29:05", "end": "00:38:40"}}
  ]

פצל רק אם זו באמת אותה שיחה שנמשכת. אם אחרי ההפסקה הוא עבר
לנושא אחר - זה קטע נפרד, לא חלק שני.
אל תפצל בגלל הפסקות של שניות בודדות. רק קטעים מתים של דקה ומעלה.

`cold_open` ו-`cold_open_at`: הסרטון ייפתח ב-6-15 שניות מהרגע הזה, ורק
אחריהן יתחיל הקטע מההתחלה. 40% מהצופים עוזבים בחצי הדקה הראשונה - זה
מה שאמור להשאיר אותם. בחר את הרגע שגורם לשאול "רגע, מה קרה פה?":
- שיא רגשי, צעקה, פאנץ' או אמירה מזעזעת - לא הסבר ולא רקע.
- מובן בלי הקשר. צופה שלא יודע כלום צריך לקלוט שמשהו קורה.
- לא מתחילת הקטע: הוא צריך לבוא לפחות דקה אחרי ה-start, אחרת אין מה להקדים.
- עדיף שמעלה שאלה ולא סוגר אותה. אם הסוף הוא הפאנץ', קח את הרגע שלפניו.
- 1-3 משפטים רצופים, מועתקים מהתמלול כלשונם, 15-40 מילים בסך הכל.
  בלי "...", בלי השמטות ובלי ניסוח שלך - הטקסט מאותר בתמלול אות באות.
- מותר שיהיה אותו רגע כמו `quote`, אם הוא באמת החזק ביותר ולא בתחילת הקטע.

כללי הכותרת. אלה נגזרו מניתוח של ערוצים מובילים בסצנה הזו:

**בעל השידור הזה הוא "{streamer}". זו עובדה, לא ניחוש.**
התמלול כולו מוקלט מהשידור שלו, והוא הדובר הראשי בכל שורה שאין בה
ציון אחר. אל תסיק מי מדבר משמות שמוזכרים בתמלול - אנשים מזכירים
יוצרים אחרים כל הזמן, מגיבים לסרטונים שלהם ומדברים עליהם.
**לעולם אל תייחס את השידור ליוצר אחר, ואל תכתוב כותרת שמשתמעת
ממנה שמישהו אחר הוא זה שמשדר.** אם בקטע מוזכר יוצר אחר, הנוסח
הוא "{streamer} מגיב ל..." או "{streamer} ו..." - לא שמו של האחר
כנושא המשפט. טעות כזאת מייחסת תוכן לאדם הלא נכון, וזה הדבר החמור
ביותר שאפשר לעשות בערוץ הזה.

- שם של אדם מוכר חייב להופיע. אף פעם לא "סטרימר" או "מישהו".
  ברוב המקרים הכותרת פותחת בשם הסטרימר: "{streamer}".
  אם בקטע יש עימות ומישהו אחר הוא הצד התוקף, מותר לפתוח בו -
  אבל רק כשברור מהכותרת ש"{streamer}" הוא זה שמשדר.
- אם יש בקטע משפט חד ובלתי נשכח, פתח בו במרכאות.
- השתמש בפועל של אירוע או עימות: מתפוצץ, מגיב, נקם, חושף,
  מתווכח, הגיע, חטף, נכנס. לא "מדבר על" ולא "משוחח".
- סיים בשלוש נקודות, או בסוגריים שמוסיפים הבטחה:
  (אחד המצחיקים) · (מטורף!) · (הזמינו משטרה) · (לא מה שחשבתם)
- אל תחשוף את הפאנץ'. רמוז עליו. "וזה מה שקרה.." עדיף על לספר מה קרה.
- אם מעורב בקטע יוצר גדול מחוץ לסצנה - הבלט אותו. זה מכפיל קהל.
- אם משתתפים יוצרים מוכרים נוספים, הזכר גם אותם.
- אחרי השמות בא הנושא המרכזי, ואחריו עד שלושה תת-נושאים מופרדים
  בפסיקים - אבל רק כאלה שנדונו באמת. זה מרחיב את הקהל.
- בעברית, בלי קליקבייט זול, עד 90 תווים.

דוגמאות:
  "{streamer} מדבר על האם אלוהים קיים, מדע וחייזרים"
  "{streamer} ועודד מתווכחים על כסף בסצנה, חסויות ומה באמת מרוויחים"
  "{streamer} מגיב לדרמה של מיכאל"
  "{streamer} מגיב לסרטון של עודד"

בריאקשן, ציין בכותרת למה או למי הוא מגיב.

מדרג הציונים:
  9-10  קטע מצוין, הייתי מעלה מיד
  7-8   טוב, שווה העלאה
  5-6   בינוני, אפשרי אם אין משהו טוב יותר
  3-4   חלש
  1-2   לא רלוונטי

אם באמת אין בחלון שום שיחה - רק משחק, צ'אט וזמן מת - החזר {{"segments": []}}.

התמלול:
---
{transcript}
---"""


# ------------------------------------------------------------ שלב א': סינון
#
# המטרה כאן היא הפוכה מהמטרה של שלב ב'. שלב ב' מחפש איכות; שלב א'
# מחפש רק "יש כאן דיבור או שאין". הוא רץ על מודל זול, מקבל את אותו
# חלון, ומחזיר פלט זעיר - טווחים בלבד. מה שנחתך כאן לא יגיע למודל
# היקר לעולם, ולכן הוא חייב להיות רחב ידיים: עדיף להעביר עשר דקות
# מיותרות מאשר להפיל שיחה אחת טובה.

TRIAGE_SYSTEM = """אתה מסנן ראשוני של תמלולי שידורים חיים.
התפקיד שלך הוא לא לבחור קטעים ולא לתת ציונים לאיכות, אלא רק לסמן
איפה בתמלול יש דיבור של ממש ואיפה יש מילוי.

אתה שלב מקדים. מה שאתה מסמן יעבור לבדיקה מעמיקה אחר כך, ומה שלא
סימנת - נזרק ואף אחד לא יראה אותו שוב. לכן במקרה של ספק, סמן.
טעות של סימון מיותר עולה כמה סנטים. טעות של השמטה מאבדת סרטון."""


TRIAGE_PROMPT = """להלן תמלול של חלון משידור חי, עם חותמות זמן.

סמן את כל הטווחים שבהם מתנהל דיבור ששווה להסתכל עליו: סיפור, ויכוח,
דעה, תגובה לדרמה, ריאקשן לתוכן של מישהו, שיחה עם יוצר אחר, רגע
מצחיק או רגשי. גם אם משחק רץ ברקע.

מה לא לסמן:
- פרשנות על משחק או הימורים בלבד: ספירת כסף, "אין לי סקאטרים",
  תיאור מה קרה בגלגל.
- קריאת שמות מהצ'אט, ברכות למנויים, בעיות טכניות.
- זמן מת, שתיקות, "עוד רגע מתחילים", המתנה.

כללים:
- טווח מינימלי {min_span} דקות. אל תסמן טווחים של חצי דקה; אם שני
  אזורים קרובים, אחד אותם לטווח אחד ארוך.
- אל תקצץ בקצוות. תן לכל טווח דקה-שתיים לפני ואחרי, כדי שלא ייחתך
  פתיח או סיום.
- אם החלון כולו דיבור - החזר טווח אחד שמכסה את כולו. זה תקין.
- אם באמת אין בחלון שום דיבור - החזר רשימה ריקה. זה גם תקין.

`heat` הוא 1-10 וזו הערכה גסה בלבד של כמה "קורה" שם, לא ציון איכות.
משהו שנשמע כמו ויכוח או סיפור מקבל 7+. שיחה זורמת בלי אירוע מקבלת 5.
מילוי מקבל 3.

החזר JSON בלבד, בלי טקסט לפניו או אחריו, ובלי הסברים:

{{"regions": [
  {{"start": "HH:MM:SS", "end": "HH:MM:SS", "heat": 1-10, "what": "עד 6 מילים"}}
]}}

התמלול:
---
{transcript}
---"""


# ------------------------------------------------------- סף לפי גודל הסטרימר
#
# סטרימר גדול מביא קהל בזכות השם. סטרימר קטן לא - אצלו הקטע חייב
# לעמוד בזכות עצמו. לכן משתנים שלושה דברים לפי הגודל: מה נחשב מספיק
# טוב, כמה מועמדים מותר להחזיר בחלון, וכמה כסף שווה להשקיע בניתוח.
#
# הספים עודכנו 14.9 יחד עם מקור ה-audience. קודם הוא היה עוקבי קיק
# (60K = גדול), ועכשיו הוא הגדול מבין מנויי היוטיוב לעוקבי הקיק -
# מספרים גדולים בסדר גודל. בספים הישנים כמעט כל הרשימה הייתה נופלת
# ל"גדול", כלומר 4 מועמדים וחלון 45/12 לכולם, והעלות לכל הרצה קופצת.
#
# ⚠ אותם ספים מופיעים גם ב-run10.streamer_tier, ושם נקבע גם החלון.
#    run10 מעביר --tier מפורש, ולכן הוא הקובע בייצור. לשנות בשניהם.

TIER_LARGE, TIER_MID, TIER_SMALL = "גדול", "בינוני", "קטן"
TIER_THRESHOLDS = [(250_000, TIER_LARGE), (50_000, TIER_MID), (0, TIER_SMALL)]

TIER_RULES = {
    # (טקסט לפרומפט, מקסימום מועמדים בחלון, ציון מינימלי לחיתוך)
    TIER_LARGE: ("", 4, 6),
    TIER_MID: (
        "הסטרימר הזה בינוני בגודלו. השם שלו מושך קהל, אבל לא לבד.\n"
        "העדף קטעים שיש בהם אירוע - עימות, סיפור, תגובה לדרמה -\n"
        "על פני שיחה נעימה שסתם זורמת.",
        3, 7),
    TIER_SMALL: (
        "שים לב: הסטרימר הזה קטן. הקהל לא מכיר אותו, והשם שלו לא\n"
        "ימשוך אף אחד ללחוץ. לכן הקטע חייב לעמוד בזכות עצמו לגמרי.\n"
        "\n"
        "השאלה שאתה שואל על כל מועמד היא: האם מישהו שלא שמע על\n"
        "הסטרימר הזה מעולם היה נשאר לצפות? אם התשובה היא 'רק מי\n"
        "שכבר עוקב אחריו' - הציון לא יעלה על 5.\n"
        "\n"
        "מה כן עובר את הרף אצל סטרימר קטן:\n"
        "- מצחיק באמת, ברמה שמצחיקה גם מי שלא מכיר את ההקשר\n"
        "- רגע רגשי חזק: התרגשות, בכי, כעס אמיתי, וידוי\n"
        "- עימות או דרמה שמערבים מישהו מוכר יותר ממנו\n"
        "- סיפור עם התחלה, אמצע וסוף שאפשר לספר לזר\n"
        "- משהו מפתיע או חריג שעומד בפני עצמו\n"
        "\n"
        "מה לא עובר: דעות שגרתיות, שיחה נעימה, בדיחות פנימיות,\n"
        "פרשנות שוטפת, וכל דבר שמעניין רק בגלל מי שאומר אותו.\n"
        "\n"
        "עדיף להחזיר segments ריק מאשר להחזיר קטע בינוני.",
        2, 7),
}


# רף ה-heat של שלב א', לפי גודל הסטרימר. אצל סטרימר גדול כמעט הכל
# שווה בדיקה, אצל קטן רק מה שנשמע כמו אירוע. ברירת מחדל שמרנית -
# הרף נמוך בכוונה, כי הפסד של שיחה טובה יקר בהרבה מחיסכון של סנט.
TIER_TRIAGE_FLOOR = {TIER_LARGE: 3, TIER_MID: 4, TIER_SMALL: 5}


def tier_for(audience) -> str:
    """גדול / בינוני / קטן לפי גודל הקהל. בלי מידע - מניחים בינוני."""
    try:
        n = int(audience)
    except (TypeError, ValueError):
        return TIER_MID
    for floor, name in TIER_THRESHOLDS:
        if n >= floor:
            return name
    return TIER_SMALL


def tier_settings(tier: str):
    """מחזיר (טקסט לפרומפט, מקסימום מועמדים, ציון מינימלי)."""
    return TIER_RULES.get(tier, TIER_RULES[TIER_MID])


# ------------------------------------------------------------------ עזרים

def hms(seconds: float) -> str:
    s = int(seconds)
    return f"{s // 3600:02d}:{(s % 3600) // 60:02d}:{s % 60:02d}"


def to_seconds(t: str) -> int:
    # 67/12 (29.9): int(float()) ולא int() - "01:02:03.5" מהמודל הפיל את כל
    # הניתוח אחרי שכבר שולם. ערך לא תקין עדיין זורק ValueError - valid_segments מסנן.
    parts = [int(float(x)) for x in str(t).strip().split(":")]
    while len(parts) < 3:
        parts.insert(0, 0)
    return parts[0] * 3600 + parts[1] * 60 + parts[2]


def chunk_rows(rows, window_min: int, overlap_min: int):
    """חותך את התמלול לחלונות לפי זמן, עם חפיפה כדי לא לפספס שיחות על התפר."""
    if not rows:
        return []
    window = window_min * 60
    overlap = overlap_min * 60
    end_of_audio = rows[-1]["end"]

    chunks = []
    start = 0.0
    while start < end_of_audio:
        stop = start + window
        block = [r for r in rows if r["start"] >= start and r["start"] < stop]
        if block:
            chunks.append(block)
        if stop >= end_of_audio:
            break
        start = stop - overlap
    return chunks


def rows_to_text(rows) -> str:
    return "\n".join(f"[{hms(r['start'])}] {r['text']}" for r in rows)


def response_text(resp) -> str:
    """
    מודלים מסוימים מחזירים בלוקי חשיבה לפני הטקסט.
    אוסף רק את בלוקי הטקסט.
    """
    parts = []
    for block in resp.content:
        if getattr(block, "type", None) == "text":
            parts.append(block.text)
        elif hasattr(block, "text"):
            parts.append(block.text)
    return "\n".join(parts)


def extract_json_ok(text: str):
    """
    (נתונים, הצליח). המודל אמור להחזיר JSON נקי, אבל לפעמים עוטף אותו.
    הצליח=False כשאין JSON תקין בכלל - תשובה ריקה או קטועה. זה לא "אין קטעים",
    וחייב להיות מובחן ממנו (67/3, 29.9).
    """
    text = (text or "").strip()
    text = re.sub(r"^```(?:json)?|```$", "", text, flags=re.MULTILINE).strip()
    try:
        data = json.loads(text)
        return (data if isinstance(data, dict) else {"segments": []}), isinstance(data, dict)
    except json.JSONDecodeError:
        pass
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        try:
            data = json.loads(text[start:end + 1])
            if isinstance(data, dict):
                return data, True
        except json.JSONDecodeError:
            pass
    return {"segments": []}, False


def extract_json(text: str) -> dict:
    """כמו extract_json_ok, בלי הדגל. לשלב א' ולכלים אחרים."""
    return extract_json_ok(text)[0]


def valid_segments(found, window: int = 0) -> list:
    """
    67/12: קטע בלי start/end, או עם זמן שאי אפשר לקרוא, נזרק כאן - לפני
    merge_segments, שם הוא היה מפיל את כל הריצה אחרי שכבר שולם על כל החלונות.
    window = מספר החלון, נשמר בקטע (merge_segments משתמש בו - 67/9).
    """
    out = []
    for seg in found if isinstance(found, list) else []:
        if not isinstance(seg, dict):
            continue
        try:
            a, b = to_seconds(seg["start"]), to_seconds(seg["end"])
        except (KeyError, TypeError, ValueError, AttributeError):
            print(f"  ⚠ קטע עם זמן לא תקין נזרק: {str(seg.get('title', ''))[:50]}")
            continue
        if b <= a:
            print(f"  ⚠ קטע שנגמר לפני שהתחיל נזרק: {str(seg.get('title', ''))[:50]}")
            continue
        if window:
            seg["window"] = window
        out.append(seg)
    return out


def load_profile(root: Path, slug: str) -> dict:
    """טוען את profiles/<slug>.json אם קיים."""
    path = root / "profiles" / f"{slug}.json"
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def profile_topics(prof: dict) -> str:
    """בונה את קטע הנושאים מהפרופיל."""
    lines = []
    for i, t in enumerate(prof.get("topics", []), 1):
        lines.append(f"{i}. {t}")
    return "\n".join(lines)


def profile_series(prof: dict) -> str:
    """סדרות ודמויות חוזרות - פורמטים עם קהל קבוע."""
    series = prof.get("series", [])
    if not series:
        return ""
    out = ["", "סדרות ודמויות חוזרות אצל הסטרימר הזה.",
           "אלה פורמטים עם קהל קבוע שמחכה להם, ולכן קטע כזה שווה יותר",
           "מקטע רגיל באותה איכות. אם זיהית אחד מהם - העלה את הציון,",
           "וציין זאת בכותרת ובשדה category."]
    for s in series:
        out.append("")
        out.append(f"  * {s.get('name','')}")
        if s.get("what"):
            out.append(f"    מה זה: {s['what']}")
        if s.get("how_to_spot"):
            out.append(f"    איך לזהות: {s['how_to_spot']}")
        if s.get("title_style"):
            out.append(f"    כותרת: {s['title_style']}")
    return "\n".join(out)


def profile_avoid(prof: dict) -> str:
    items = prof.get("avoid", [])
    if not items:
        return ""
    return "\n".join(["", "מה לא לקחת אצל הסטרימר הזה:"] + [f"  - {a}" for a in items])


def load_names(root: Path) -> str:
    """
    טוען את names.json ומחזיר קטע טקסט לפרומפט.
    אם הקובץ לא קיים - מחזיר מחרוזת ריקה והכל ממשיך כרגיל.
    """
    path = root / "names.json"
    if not path.exists():
        return ""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return ""

    lines = []

    people = data.get("people", [])
    if people:
        lines.append("שמות מוכרים בסצנה. אם בתמלול מופיע שם שנשמע כמו אחד")
        lines.append("מהכתיבים החלופיים - זה כנראה אותו אדם, והשם הנכון הוא")
        lines.append("זה שמופיע ראשון. השתמש בשם הנכון בכותרת ובמשתתפים.")
        for p in people:
            aliases = [a for a in p.get("aliases", []) if a != p["name"]]
            line = f"  {p['name']}"
            if aliases:
                line += "  (מופיע בתמלול גם כ: " + ", ".join(aliases) + ")"
            if p.get("note"):
                line += f"  - {p['note']}"
            lines.append(line)

    unc = data.get("uncertain", [])
    if unc:
        lines.append("")
        lines.append("שמות שהתמלול משבש ולא ידוע מה הנכון. אם אתה נתקל בהם,")
        lines.append("כתוב אותם בכותרת בכתיב הנפוץ ביותר וציין זאת ב-reason:")
        for u in unc:
            lines.append("  " + " / ".join(u.get("heard_as", [])))

    # 25.9: "שון" היה כינוי של שון פי, והמודל ייחס לו את הדרמה של שון
    # שואו. שם שמתאים לכמה אנשים לא יכול להיות כינוי - הוא נשאר כאן.
    amb = data.get("ambiguous", [])
    if amb:
        lines.append("")
        lines.append("שמות דו-משמעיים. השם הזה לבד מתאים ליותר מאדם אחד - אל תניח.")
        lines.append("הכרע לפי ההקשר, ובכותרת ובמשתתפים כתוב את השם המלא:")
        for a in amb:
            cands = " / ".join(a.get("candidates", []))
            lines.append(f"  \"{a.get('heard_as', '')}\" = {cands}. {a.get('rule', '')}")

    viewers = data.get("viewers", [])
    if viewers:
        lines.append("")
        lines.append("צופים ודמויות משניות. אפשר להזכיר בתיאור, אבל לא לבנות")
        lines.append("עליהם כותרת - הקהל לא מחפש שמות של צופים:")
        for v in viewers:
            lines.append(f"  {v['name']}  ({', '.join(v.get('aliases', []))})")

    terms = data.get("terms", [])
    if terms:
        lines.append("")
        lines.append("מונחים שחוזרים: " + ", ".join(terms))

    fb = (data.get("feedback") or {}).get("entries", [])
    if fb:
        lines.append("")
        lines.append("משוב אמיתי של בעל הערוץ על קטעים קודמים. זה הטעם")
        lines.append("שאתה מכייל אליו - תן לו משקל גבוה:")
        for e in fb[-12:]:
            verdict = e.get("verdict", "")
            why = e.get("why", "")
            title = e.get("title", "")[:55]
            line = f"  [{verdict}] {title}"
            if why:
                line += f" — {why[:90]}"
            lines.append(line)

    trig = data.get("_טריגרים", {})
    if trig.get("שמות"):
        lines.append("")
        lines.append("טריגרים - שמות שההופעה שלהם היא סיבה בפני עצמה לקחת קטע:")
        for t in trig["שמות"]:
            lines.append(f"  {t.get('name','')} ({t.get('בונוס','')})")
            lines.append(f"    {t.get('why','')}")
        if trig.get("כלל"):
            lines.append(f"  {trig['כלל']}")

    win = data.get("_מה_מנצח", {})
    if win:
        lines.append("")
        lines.append("מה מוכח שמביא צפיות בסצנה הזאת. תן לזה משקל בציון,")
        lines.append("לא רק בכותרת:")
        for item in win.get("דרג_גבוה", []):
            lines.append(f"  ++ {item}")
        for item in win.get("דרג_נמוך", []):
            lines.append(f"  -- {item}")

    ex = data.get("title_examples", {})
    if ex.get("good"):
        lines.append("")
        lines.append("דוגמאות לכותרות שעובדות. חקה את הסגנון הזה:")
        for t in ex["good"]:
            lines.append(f"  {t}")
    if ex.get("weak"):
        lines.append("")
        lines.append("דוגמאות לכותרות חלשות. אל תכתוב כאלה:")
        for t in ex["weak"]:
            lines.append(f"  {t}")
    if ex.get("rules"):
        lines.append("")
        lines.append("כללי כותרות שנלמדו מניתוח ערוצי הקליפים הגדולים בסצנה:")
        for r in ex["rules"]:
            lines.append(f"  - {r}")
    if ex.get("_למה"):
        lines.append(f"  ({ex['_למה']})")

    return "\n".join(lines)


# ------------------------------------------------- קריאה למודל, בלי בזבוז
#
# שני דברים נלמדו ב-15.9, שניהם עלו כסף לפני שהתגלו:
#
# 1. **סונט 5 חושב כברירת מחדל.** הקוד נכתב ל-sonnet-4-5, ששם החשיבה
#    כבויה. המודל ההוא כבר לא קיים, pick_model נופל לסונט 5, ובלוקי
#    החשיבה נספרים כטוקני פלט במחיר מלא - ו-response_text אפילו לא
#    אוסף אותם. חלון שהחזיר {"segments": []} חויב ב-386 טוקני פלט.
#    **בשלב א' זה מכובה.** בשלב ב' זה נשאר: שם מתקבלת ההחלטה איזה
#    קטע שווה סרטון, וזו לא הוצאה שכדאי לחסוך עליה (החלטת לירון).
#
# 2. **הפרומפט הקבוע נשלח מחדש בכל חלון.** ~9,500 טוקנים של חוקים
#    ו-names.json, תשע פעמים בהרצה. עכשיו הם נשלחים עם cache_control,
#    וקריאה מהמטמון עולה עשירית. המודל מקבל בדיוק אותו טקסט.

CACHE = {"type": "ephemeral"}
NO_THINKING = {"type": "disabled"}
SPLIT = "«TRANSCRIPT»"    # סמן פנימי לחיתוך הפרומפט. לא מגיע למודל


def usage_of(resp):
    """
    (נכנס, יצא, נכתב_למטמון, נקרא_מהמטמון).
    שדות המטמון לא קיימים בכל גרסת SDK, ולכן getattr עם ברירת מחדל.
    """
    u = resp.usage
    return (getattr(u, "input_tokens", 0) or 0,
            getattr(u, "output_tokens", 0) or 0,
            getattr(u, "cache_creation_input_tokens", 0) or 0,
            getattr(u, "cache_read_input_tokens", 0) or 0)


def create_message(client, **kw):
    """
    עוטף messages.create. אם הדגם לא מכיר `thinking` - מנסה שוב בלעדיו
    במקום להיכשל. בלי זה, מודל ישן היה מפיל את כל ההרצה על פרמטר.
    """
    try:
        return client.messages.create(**kw)
    except Exception as exc:
        if "thinking" in kw and "thinking" in str(exc).lower():
            kw.pop("thinking", None)
            return client.messages.create(**kw)
        raise


def pick_model(client, requested: str) -> str:
    """אם שם המודל לא קיים, בוחר אוטומטית את הזמין העדכני ביותר."""
    try:
        available = [m.id for m in client.models.list(limit=50).data]
    except Exception:
        return requested
    if requested in available:
        return requested

    # קודם כל לחפש באותה משפחה שביקשו. בלי זה, בקשה להאיקו שלא נמצא
    # הייתה נופלת לסונט - כלומר שלב הסינון הזול היה רץ על המודל היקר
    # ומכפיל את העלות במקום להוריד אותה.
    families = ["sonnet", "opus", "haiku"]
    for fam in families:
        if fam in requested.lower():
            families.remove(fam)
            families.insert(0, fam)
            break

    for keyword in families:
        matches = [m for m in available if keyword in m.lower()]
        if matches:
            print(f"המודל '{requested}' לא זמין. משתמש ב-'{matches[0]}'.")
            return matches[0]
    return requested


# מחירים ל-1000 טוקנים, בדולרים. עדכן אם המחירון משתנה.
# ⚠ 28.9: עד היום כאן ישבו המחירים של סונט 4.5 ($3/$15) והאיקו 3.5.
#   סונט 5 עולה $2/$10 והאיקו 4.5 $1/$5 (anthropic.com/news/claude-sonnet-5).
#   לכן usage.jsonl הגזים ב-~40% מול החשבונית. budget.py מחשב מחדש מהטוקנים
#   ולא סומך על שדה usd הישן. אותו מחירון יושב גם שם - לעדכן בשניהם.
PRICES = {
    "sonnet": {"in": 0.002, "out": 0.010},
    "opus":   {"in": 0.005, "out": 0.025},
    "haiku":  {"in": 0.001, "out": 0.005},
}


# מטמון: כתיבה עולה פי 1.25 מקלט רגיל (פי 2 למטמון של שעה), קריאה עשירית.
CACHE_WRITE_MULT = 1.25
CACHE_WRITE_1H_MULT = 2.0
CACHE_READ_MULT = 0.10
BATCH_MULT = 0.5         # Message Batches: חצי מחיר על הכל, מצטבר עם המטמון


def estimate_cost(model: str, tokens_in: int, tokens_out: int,
                  cache_write: int = 0, cache_read: int = 0,
                  batch: bool = False, cache_ttl: str = "5m") -> float:
    cw_mult = CACHE_WRITE_1H_MULT if cache_ttl == "1h" else CACHE_WRITE_MULT
    for key, price in PRICES.items():
        if key in model.lower():
            usd = ((tokens_in / 1000) * price["in"]
                   + (cache_write / 1000) * price["in"] * cw_mult
                   + (cache_read / 1000) * price["in"] * CACHE_READ_MULT
                   + (tokens_out / 1000) * price["out"])
            return usd * (BATCH_MULT if batch else 1.0)
    return 0.0


def log_usage(root: Path, model: str, tokens_in: int, tokens_out: int, job: str,
              cache_write: int = 0, cache_read: int = 0,
              batch: bool = False, cache_ttl: str = "5m") -> None:
    """
    רושם כל קריאה לקובץ usage.jsonl בשורש הפרויקט.
    זה מה שיזין בהמשך את הדוח היומי בטלגרם.
    """
    from datetime import datetime, timezone

    record = {
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "job": job,
        "model": model,
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
        "cache_write": cache_write,
        "cache_read": cache_read,
        "usd": round(estimate_cost(model, tokens_in, tokens_out,
                                   cache_write, cache_read, batch, cache_ttl), 5),
    }
    if batch:
        record["batch"] = True           # budget.py מחשב חצי מחיר לפי זה
    if cache_ttl == "1h":
        record["cache_ttl"] = "1h"
    log_path = root / "usage.jsonl"
    with log_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def find_project_root(start: Path) -> Path:
    for candidate in [start, *start.parents]:
        if (candidate / "scripts").is_dir():
            return candidate
    return start


def seg_parts_sec(seg) -> list:
    """הטווחים של קטע בשניות. בלי parts תקין - הטווח start-end."""
    out = []
    for p in seg.get("parts") or []:
        try:
            a, b = to_seconds(str(p["start"])), to_seconds(str(p["end"]))
        except (KeyError, TypeError, ValueError, AttributeError):
            continue
        if b > a:
            out.append((a, b))
    if not out:
        out = [(to_seconds(seg["start"]), to_seconds(seg["end"]))]
    return sorted(out)


def merge_parts(keep: list, other: list, join: int = 1) -> list:
    """
    parts של קטע מאוחד (28.9). הבסיס הוא ה-parts של השומר, כולל הדילוגים
    שהוא בחר על זמן מת. מהקטע השני נכנס רק מה שחורג מהקצוות של השומר:
    חלון אחד ראה את תחילת השיחה, השני את הסוף, ושניהם נכנסים.
    """
    lo, hi = keep[0][0], keep[-1][1]
    before = [(a, min(b, lo)) for a, b in other if a < lo]
    after = [(max(a, hi), b) for a, b in other if b > hi]
    # הרווח בין הקטעים (עד gap_tolerance) הוא ניחוש גבול של חלון, לא דילוג
    # מכוון על זמן מת - לכן הטווח הסמוך לשומר נמתח עד אליו.
    if before:
        i = max(range(len(before)), key=lambda k: before[k][1])
        before[i] = (before[i][0], lo)
    if after:
        i = min(range(len(after)), key=lambda k: after[k][0])
        after[i] = (hi, after[i][1])
    ranges = list(keep) + before + after
    ranges.sort()
    out = [list(ranges[0])]
    for a, b in ranges[1:]:
        if a <= out[-1][1] + join:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return [(a, b) for a, b in out]


STAGE_B_RETRY_WAITS = [0, 30, 120, 300]   # ניסיון ראשון מיד, אחר כך המתנות (67/3)
SAME_CONTEXT_OVERLAP = 30   # שניות חפיפה בין קטעים מחלונות שונים = אותה שיחה


def same_context(last: dict, seg: dict, gap_tolerance: int = 90) -> bool:
    """
    67/9 (החלטת לירון 28.9): לאחד רק כשזה אותו הקשר - אותה שיחה שחלון אחר
    ראה - גם אם יש פער. שני קטעים שונים שסמוכים זה לזה - לא לאחד.

    אותו הקשר:
      - כפילות: חופפים בחצי מהקצר מביניהם או יותר (גם מאותו חלון).
      - מחלונות שונים, וגם חפיפה ממשית (30 שנ'+) או cut_off באחד מהם
        (המודל אמר "הסיפור ממשיך מעבר לקצה") עם פער עד gap_tolerance.
    קטעים מאותו חלון שלא חופפים - המודל פיצל אותם בכוונה. נפרדים.
    """
    a1, b1 = to_seconds(last["start"]), to_seconds(last["end"])
    a2, b2 = to_seconds(seg["start"]), to_seconds(seg["end"])
    ov = min(b1, b2) - max(a1, a2)          # שלילי = פער
    short = max(1, min(b1 - a1, b2 - a2))
    if ov >= 0.5 * short:
        return True
    w1, w2 = last.get("window"), seg.get("window")
    if w1 and w2 and w1 == w2:
        return False
    if ov >= SAME_CONTEXT_OVERLAP:
        return True
    if -ov <= gap_tolerance and (last.get("cut_off") or seg.get("cut_off")):
        return True
    return False


def merge_segments(segments, gap_tolerance: int = 90):
    """
    מאחד כפילויות שנוצרו מהחפיפה בין חלונות.

    תוקן 28.9: עד אז start/end הורחבו אבל parts נשאר של השומר בלבד, ו-cut3
    חותך לפי parts. התוצאה: ההמשך שחלון אחר ראה נזרק בשקט (אוהד 15.9 #2
    נגמר ב-02:56:38 באמצע השידוך, והסוף עד 02:59:34 היה בחלון הבא).
    עכשיו parts מתאחדים, ו-start/end נגזרים מהם.

    תוקן 29.9 (67/9): עד אז כל שני קטעים עם פער עד 90 שנ' אוחדו, גם כשהמודל
    פיצל אותם בכוונה כי הנושא התחלף. עכשיו רק same_context.
    """
    if not segments:
        return []
    segments = sorted(segments, key=lambda s: to_seconds(s["start"]))
    merged = [segments[0]]
    for seg in segments[1:]:
        last = merged[-1]
        if same_context(last, seg, gap_tolerance):
            # אותו הקשר - שומרים את זה עם הציון הגבוה, ומרחיבים את הגבולות
            keep_new = seg.get("score", 0) > last.get("score", 0)
            keeper = dict(seg if keep_new else last)
            other = last if keep_new else seg
            parts = merge_parts(seg_parts_sec(keeper), seg_parts_sec(other))
            keeper["parts"] = [{"start": hms(a), "end": hms(b)} for a, b in parts]
            keeper["start"] = hms(parts[0][0])
            keeper["end"] = hms(parts[-1][1])
            keeper["merged_windows"] = keeper.get("merged_windows", 1) + other.get("merged_windows", 1)
            merged[-1] = keeper
        else:
            merged.append(seg)
    return merged


# ------------------------------------------------------- עזרי השלב הראשון

REGION_PAD = 60      # שניות ריפוד לכל צד, שלא ייחתך פתיח או סיום
REGION_GLUE = 150    # פער קצר מזה בין אזורים - מאחדים אותם לאזור אחד


def merge_regions(regions, glue: int = REGION_GLUE):
    """מאחד אזורים חופפים או קרובים. עובד על זוגות (התחלה, סוף) בשניות."""
    if not regions:
        return []
    regions = sorted(regions)
    out = [list(regions[0])]
    for start, end in regions[1:]:
        if start <= out[-1][1] + glue:
            out[-1][1] = max(out[-1][1], end)
        else:
            out.append([start, end])
    return [tuple(r) for r in out]


def parse_regions(data, lo: float, hi: float, floor: int, min_span: int):
    """
    הופך את תשובת שלב א' לרשימת טווחים בשניות, מרופדים ומאוחדים.
    lo/hi הם גבולות החלון - אזור שחורג מהם נחתך אליהם.
    """
    raw = []
    for r in data.get("regions", []):
        try:
            start = to_seconds(str(r["start"]))
            end = to_seconds(str(r["end"]))
        except (KeyError, ValueError, AttributeError):
            continue
        if end <= start:
            continue
        try:
            heat = int(r.get("heat", 10))
        except (TypeError, ValueError):
            heat = 10
        if heat < floor:
            continue
        start = max(lo, start - REGION_PAD)
        end = min(hi, end + REGION_PAD)
        if end - start < min_span * 60 * 0.5:
            continue
        raw.append((start, end))
    return merge_regions(raw)


def rows_in_regions(rows, regions):
    """מחזיר רשימה של קבוצות שורות, קבוצה אחת לכל אזור ששרד."""
    groups = []
    for start, end in regions:
        block = [r for r in rows if r["start"] >= start and r["start"] < end]
        if block:
            groups.append(block)
    return groups


def groups_to_text(groups) -> str:
    """
    כמו rows_to_text, אבל עם סימון מפורש בין קבוצות. בלי הסימון הזה
    המודל היקר רואה קפיצה בזמן וחושב שהתמלול פגום, או גרוע מזה -
    מותח קטע אחד על פני חור של עשרים דקות.
    """
    blocks = []
    for i, g in enumerate(groups):
        if i:
            blocks.append("\n[... כאן הושמט חלק שאין בו דיבור רלוונטי ...]\n")
        blocks.append(rows_to_text(g))
    return "\n".join(blocks)


def fatal_api_error(message: str) -> str:
    """
    לא כל שגיאה שווה ניסיון נוסף. יתרה שנגמרה או מפתח פסול יחזרו
    בדיוק אותו דבר בכל אחד מתשעת החלונות, וכל מה שיוצא מזה הוא
    מסך מלא הודעות שגיאה שמסתיר את השורה החשובה. אלה נעצרים מיד.
    מחזיר הסבר בעברית, או מחרוזת ריקה אם השגיאה כן שווה המשך.
    """
    text = (message or "").lower()
    if "credit balance is too low" in text or "insufficient" in text:
        return ("נגמרה היתרה בחשבון ה-API של אנת'רופיק.\n"
                "  להיכנס ל-console.anthropic.com → Plans & Billing ולטעון.\n"
                "  שום חלון לא נותח, ולא חויבת על ההרצה הזאת.")
    if "authentication_error" in text or "invalid x-api-key" in text \
            or "invalid_api_key" in text:
        return ("המפתח ANTHROPIC_API_KEY לא תקף.\n"
                '  setx ANTHROPIC_API_KEY "sk-ant-..."  ואז חלון חדש.')
    if "permission_error" in text:
        return "לחשבון אין הרשאה למודל הזה."
    return ""


def triage_window(client, model: str, block, min_span: int):
    """
    שלב א' על חלון אחד. מחזיר (טקסט_התשובה, טוקנים_נכנסו, טוקנים_יצאו).
    שגיאה מחזירה טקסט ריק, והקורא מחליט מה לעשות.
    """
    prompt = TRIAGE_PROMPT.format(min_span=min_span, transcript=rows_to_text(block))
    try:
        resp = create_message(
            client,
            model=model,
            max_tokens=2000,
            system=TRIAGE_SYSTEM,
            # השאלה כאן היא "יש פה דיבור או אין". אין על מה להתלבט,
            # ואין סיבה לשלם על חשיבה שנזרקת ממילא.
            thinking=NO_THINKING,
            messages=[{"role": "user", "content": prompt}],
        )
    except Exception as exc:
        return "", 0, 0, 0, 0, str(exc)
    tok_in, tok_out, cw, cr = usage_of(resp)
    return response_text(resp), tok_in, tok_out, cw, cr, ""


# ------------------------------------------------ מדידת הסינון (משימה 25)
#
# כל הרצה עם שלב א' רושמת שורה ל-triage_log.jsonl בשורש. כך השאלה
# "האם הסינון משתלם" נענית מהריצות הרגילות, בלי הרצות בדיקה יקרות.
#   net_usd    כמה חסך בקלט של סונט פחות כמה עלה האיקו. שלילי = הפסד
#   audit_hits חלונות שנזרקו ובכל זאת היה בהם קטע - המחיר הנסתר
# הערכת החיסכון שמרנית: היא סופרת רק קלט. חלון שנזרק לגמרי חוסך גם
# את הפלט של סונט, ולכן החיסכון האמיתי שם גבוה מהמוצג.

def save_triage_stats(root: Path, job: str, stats: dict) -> None:
    from datetime import datetime, timezone
    record = {"ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
              "job": job, **stats}
    try:
        Path("triage_stats.json").write_text(
            json.dumps(record, ensure_ascii=False, indent=1), encoding="utf-8")
        with (root / "triage_log.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception as exc:
        print(f"(לא נשמרה מדידת הסינון: {exc})")


def triage_report(root: Path) -> str:
    path = root / "triage_log.jsonl"
    if not path.exists():
        return "אין עדיין מדידות. הן נאספות מכל הרצה רגילה עם שלב א'."
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            rows.append(json.loads(line))
        except Exception:
            pass
    if not rows:
        return "הקובץ ריק."
    out = ["הסינון הדו-שלבי - מה נמדד בהרצות אמיתיות", ""]
    for tier in (TIER_LARGE, TIER_MID, TIER_SMALL):
        rs = [r for r in rows if r.get("tier") == tier]
        if not rs:
            continue
        net = sum(r.get("net_usd", 0) for r in rs)
        skipped = sum(r.get("skipped", 0) for r in rs)
        windows = sum(r.get("windows", 0) for r in rs)
        audits = sum(r.get("audit_runs", 0) for r in rs)
        hits = sum(r.get("audit_hits", 0) for r in rs)
        kept = sum(r.get("kept_pct", 0) for r in rs) / len(rs)
        out.append(f"{tier}: {len(rs)} הרצות · עבר {kept:.0f}% · "
                   f"נזרקו {skipped}/{windows} חלונות · נטו ${net:+.3f}")
        if audits:
            out.append(f"   ביקורת: {hits} החטאות מתוך {audits} חלונות שנבדקו")
        if len(rs) >= 3:
            if hits:
                out.append("   ⚠ הסינון מפיל קטעים טובים. להוריד את הרף או לכבות.")
            elif net < 0:
                out.append("   → מפסיד כסף. כדאי --single-pass לרמה הזאת.")
            else:
                out.append("   → משתלם. להשאיר.")
        else:
            out.append("   (מוקדם להסיק - צריך לפחות 3 הרצות)")
    return "\n".join(out)


# ------------------------------------------------------------------ ראשי

# ----------------------------------------------- Message Batches (משימה 57)
#
# אותה קריאה בדיוק כמו בזמן אמת - אותו מודל, אותו פרומפט, אותה חשיבה -
# בחצי מחיר. המחיר: התשובה מגיעה בד"כ תוך שעה ועד 24 שעות. לכן batch
# רק ללייבים שאינם דרמה חמה (budget.decide מחליט, run10 מעביר --batch).
#
# המטמון ב-batch הוא "best effort": הבקשות רצות במקביל ולא לפי הסדר,
# ולכן מטמון של 5 דקות כמעט לא נתפס. שעה (ttl=1h) עולה פי 2 בכתיבה
# ועשירית בקריאה, ומשתלם כבר מהפגיעה הראשונה.
#
# batch_state.json בתיקיית העבודה שומר את מזהה ה-batch. אם המחשב נכבה
# באמצע ההמתנה, ה---resume מתחבר לאותו batch ולא משלם שוב.

CACHE_1H = {"type": "ephemeral", "ttl": "1h"}
BATCH_STATE = Path("batch_state.json")


def run_batch(client, jobs, build_params, poll_sec: int = 60):
    """
    jobs = [(i, body), ...]. מחזיר [(i, message או None), ...] לפי הסדר.
    None = החלון לא חזר (errored/expired/canceled) והקורא ישלח בזמן אמת.
    """
    import time
    ids = {f"w{i:02d}": i for i, _ in jobs}
    batch_id = ""
    if BATCH_STATE.exists():
        try:
            saved = json.loads(BATCH_STATE.read_text(encoding="utf-8"))
            if sorted(saved.get("windows", [])) == sorted(ids):
                batch_id = saved.get("id", "")
                print(f"ממשיך batch קיים {batch_id} (לא שולח שוב)")
        except Exception:
            batch_id = ""
    if not batch_id:
        requests = [{"custom_id": cid, "params": build_params(body, CACHE_1H)}
                    for cid, body in ((f"w{i:02d}", b) for i, b in jobs)]
        try:
            batch = client.messages.batches.create(requests=requests)
        except Exception as exc:
            stop_why = fatal_api_error(str(exc))
            if stop_why:
                print(f"\n\n{stop_why}")
                sys.exit(1)
            print(f"שליחת ה-batch נכשלה ({exc}). כל החלונות יישלחו בזמן אמת.")
            return [(i, None) for i, _ in jobs]
        batch_id = batch.id
        BATCH_STATE.write_text(json.dumps({
            "id": batch_id, "windows": list(ids),
            "created": time.strftime("%Y-%m-%dT%H:%M:%S"),
        }, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"\nנשלח batch {batch_id}: {len(requests)} חלונות. "
              f"ממתין לתשובה (בד\"כ פחות משעה, עד 24 שעות)...", flush=True)

    started = time.time()
    last_print = 0.0
    errors = 0
    while True:
        try:
            b = client.messages.batches.retrieve(batch_id)
            errors = 0
        except Exception as exc:
            errors += 1
            if errors >= 30:              # חצי שעה בלי תשובה מה-API
                print(f"אי אפשר לבדוק את ה-batch ({exc}). יוצא - --resume ימשיך.")
                sys.exit(1)
            time.sleep(poll_sec)
            continue
        if b.processing_status == "ended":
            break
        if time.time() - last_print > 600:
            c = b.request_counts
            print(f"  batch: {c.succeeded + c.errored}/{len(ids)} מוכנים, "
                  f"{(time.time() - started) / 60:.0f} דק'", flush=True)
            last_print = time.time()
        time.sleep(poll_sec)

    got = {}
    for res in client.messages.batches.results(batch_id):
        i = ids.get(res.custom_id)
        if i is None:
            continue
        if res.result.type == "succeeded":
            got[i] = res.result.message
        else:
            print(f"  [{i}] {res.result.type}")
    print(f"batch הסתיים אחרי {(time.time() - started) / 60:.0f} דק': "
          f"{len(got)}/{len(ids)} חלונות.", flush=True)
    # batch_state.json נשאר עד שהקטעים נשמרו (סוף main). קריסה באמצע
    # העיבוד = --resume קורא את אותן תוצאות שוב, בלי לשלם שוב.
    return [(i, got.get(i)) for i, _ in jobs]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("transcript", help="קובץ _transcript.json")
    ap.add_argument("--model", default="claude-sonnet-5")
    ap.add_argument("--min", type=int, default=3, help="אורך קטע מינימלי בדקות")
    ap.add_argument("--max", type=int, default=45, help="אורך קטע מקסימלי בדקות")
    ap.add_argument("--window", type=int, default=45, help="גודל חלון ניתוח בדקות")
    ap.add_argument("--overlap", type=int, default=12, help="חפיפה בין חלונות בדקות")
    ap.add_argument("--url", default="", help="כתובת m3u8 או VOD, לייצור פקודות החיתוך")
    ap.add_argument("--streamer", default="", help="שם הסטרימר לכותרות. אם לא ניתן, נקרא מ-meta.json")
    ap.add_argument("--tier", default="", choices=["", TIER_LARGE, TIER_MID, TIER_SMALL],
                    help="גודל הסטרימר. משנה את הרף ואת מספר המועמדים. "
                         "אם לא ניתן, נגזר מ-audience ב-watchlist.json")
    ap.add_argument("--triage-model", default="claude-haiku-4-5-20251001",
                    help="המודל הזול של שלב א'")
    ap.add_argument("--single-pass", action="store_true",
                    help="(ברירת המחדל מ-28.9, נשאר לתאימות)")
    ap.add_argument("--two-pass", action="store_true",
                    help="עם סינון Haiku מקדים. כבוי מ-28.9: עלה יותר ממה שחסך (משימה 57)")
    ap.add_argument("--batch", action="store_true",
                    help="שלב ב' דרך Message Batches: חצי מחיר, תשובה בד\"כ תוך שעה (עד 24)")
    ap.add_argument("--batch-poll", type=int, default=60, help="כל כמה שניות לבדוק את ה-batch")
    ap.add_argument("--triage-floor", type=int, default=0,
                    help="רף ה-heat בשלב א'. 0 = לפי גודל הסטרימר")
    ap.add_argument("--audit-skipped", type=int, default=1,
                    help="כמה חלונות שהסינון זרק לבדוק בכל זאת בסונט (משימה 25). "
                         "0 = בלי בדיקה")
    ap.add_argument("--triage-report", action="store_true",
                    help="סיכום המדידות מכל ההרצות, בלי לנתח כלום")
    ap.add_argument("--window-only", type=int, default=0, help="לנתח רק חלון מסוים, לבדיקה זולה")
    ap.add_argument("--save-raw", action="store_true", help="לשמור את התשובות הגולמיות לבדיקה")
    args = ap.parse_args()

    if args.triage_report:
        print(triage_report(find_project_root(Path(__file__).resolve().parent)))
        return

    # שם הסטרימר לכותרות. חייב להיות שם בעברית, לא slug.
    #
    # ⚠ הבאג של 14.9: run10 העביר את ה-slug ב---streamer, וכיוון שהוא
    #   לא ריק, meta.json לא נקרא בכלל. התוצאה הייתה כותרות כמו
    #   "pedrofederer מתפוצץ על..." - ובקטע שבו זה נראה למודל חסר
    #   פשר, הוא לקח שם עברי אחר מהתמלול ותלה בו את הקטע.
    #   לכן: slug באותיות לטיניות לעולם לא גובר על display_name.
    meta = {}
    meta_file = Path("meta.json")
    if meta_file.exists():
        try:
            meta = json.loads(meta_file.read_text(encoding="utf-8"))
        except Exception:
            meta = {}

    def looks_like_slug(name: str) -> bool:
        """שם בלי אות עברית אחת הוא slug, לא שם של אדם."""
        return bool(name) and not any("֐" <= c <= "׿" for c in name)

    streamer = args.streamer
    if not streamer or looks_like_slug(streamer):
        better = meta.get("display_name") or ""
        if better and not looks_like_slug(better):
            if streamer and streamer != better:
                print(f"שם לכותרות: '{streamer}' נראה כמו slug, "
                      f"משתמש ב-'{better}' מ-meta.json.")
            streamer = better
    if not streamer:
        streamer = meta.get("streamer") or Path.cwd().name.split("_")[0]
    if looks_like_slug(streamer):
        print(f"⚠ שם הסטרימר לכותרות הוא '{streamer}' - slug ולא שם בעברית.\n"
              "  הכותרות ייצאו עם ה-slug, ויש סיכוי שהמודל יתלה את הקטע\n"
              "  באדם אחר. מלא display_name ב-meta.json והרץ שוב.")

    # גודל הסטרימר: מהדגל, אחרת מ-watchlist לפי ה-slug של תיקיית העבודה
    tier = args.tier
    if not tier:
        slug = Path.cwd().name.rsplit("_", 1)[0]
        audience = None
        try:
            wl = json.loads((find_project_root(Path.cwd()) / "watchlist.json")
                            .read_text(encoding="utf-8"))
            for s in wl.get("streamers", []):
                if str(s.get("slug", "")).lstrip("@").lower() == slug.lstrip("@").lower():
                    audience = s.get("audience")
                    break
        except Exception:
            pass
        tier = tier_for(audience)
    tier_text, max_cands, _ = tier_settings(tier)
    print(f"סטרימר {tier}: עד {max_cands} מועמדים בחלון.")

    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("לא נמצא ANTHROPIC_API_KEY. הגדר אותו והרץ מחדש:")
        print('   setx ANTHROPIC_API_KEY "sk-ant-..."')
        print("   (ואז לפתוח חלון PowerShell חדש)")
        sys.exit(1)

    path = Path(args.transcript)
    if not path.exists():
        print(f"לא נמצא: {path}")
        sys.exit(1)

    rows = json.loads(path.read_text(encoding="utf-8"))
    total_min = rows[-1]["end"] / 60 if rows else 0
    print(f"תמלול: {len(rows)} מקטעים, {total_min:.0f} דקות")

    client = anthropic.Anthropic()
    model = pick_model(client, args.model)
    # 28.9 (משימה 57): מעבר אחד כברירת מחדל. ב-15.9 הסינון עלה $1.53
    # וחסך $0.70 - הפסד נטו, בלי שום תרומה לאיכות. --two-pass מחזיר אותו.
    triage_model = pick_model(client, args.triage_model) if args.two_pass else ""
    triage_floor = args.triage_floor or TIER_TRIAGE_FLOOR.get(tier, 4)

    proj = find_project_root(Path(__file__).resolve().parent)
    names_block = load_names(proj)

    slug = Path.cwd().name.split("_")[0]
    prof = load_profile(proj, slug)
    if prof:
        print(f"נטען פרופיל: {prof.get('display_name', slug)}"
              f"  ({len(prof.get('topics', []))} נושאים, {len(prof.get('series', []))} סדרות)")
        if prof.get("display_name"):
            streamer = prof["display_name"]
    topics_block = profile_topics(prof) or TOPICS
    series_block = profile_series(prof)
    avoid_block = profile_avoid(prof)
    if names_block:
        print("נטענה רשימת שמות מ-names.json")

    chunks = chunk_rows(rows, args.window, args.overlap)

    if args.window_only:
        if 1 <= args.window_only <= len(chunks):
            chunks = [chunks[args.window_only - 1]]
            print(f"בודק חלון {args.window_only} בלבד.")
        else:
            print(f"אין חלון {args.window_only}. יש {len(chunks)}.")
            sys.exit(1)

    raw_dir = Path("raw_responses")
    if args.save_raw:
        raw_dir.mkdir(exist_ok=True)

    # אם הדגם הזול לא היה זמין ו-pick_model נפל על היקר, אין טעם
    # בשלב א' - הוא רק יוסיף עלות. מוותרים עליו במקום להכפיל חשבון.
    if triage_model and triage_model == model:
        print(f"אזהרה: מודל הסינון זהה למודל הניתוח ({model}). "
              "מדלג על שלב א'.")
        triage_model = ""

    if triage_model:
        print(f"מנתח ב-{len(chunks)} חלונות. "
              f"שלב א': {triage_model} (רף heat {triage_floor}) · "
              f"שלב ב': {model}\n")
    else:
        print(f"מנתח ב-{len(chunks)} חלונות, מעבר אחד (מודל: {model})\n")

    project_root = find_project_root(Path(__file__).resolve().parent)
    job_name = Path.cwd().name

    usage_box = {"in": 0, "out": 0, "cw": 0, "cr": 0, "usd": 0.0}
    batch_jobs = []          # (i, body) - נשלחים יחד בסוף הלולאה כש---batch
    skipped_blocks = []

    def build_params(body: str, cache: dict = CACHE) -> dict:
        """הפרמטרים של קריאת שלב ב'. זהים בזמן אמת וב-batch - אותה איכות."""
        prompt = PROMPT.format(
            topics=topics_block,
            series=series_block,
            avoid=avoid_block,
            min_min=args.min,
            max_min=args.max,
            tier=("\n" + tier_text + "\n") if tier_text else "",
            max_cands=max_cands,
            streamer=streamer,
            names=("\n" + names_block + "\n") if names_block else "",
            transcript=SPLIT,
        )

        # חיתוך במקום שבו התמלול היה אמור לשבת. כל מה שלפניו זהה בכל
        # החלונות, ולכן הוא הולך למטמון; רק מה שאחריו משתנה.
        head, tail = prompt.split(SPLIT, 1)
        return dict(
            model=model,
            max_tokens=16000,
            system=[{"type": "text", "text": SYSTEM}],
            messages=[{"role": "user", "content": [
                {"type": "text", "text": head, "cache_control": cache},
                {"type": "text", "text": body + tail},
            ]}],
        )

    def stage_b(body: str, i: int):
        """
        שלב ב' על טקסט אחד, בזמן אמת. מחזיר רשימת קטעים, או None אם נכשל
        גם אחרי כל הניסיונות.

        67/3 (29.9): עד היום שגיאה אחת (עומס 529, רשת, תשובה קטועה) = החלון
        נזרק בשקט, 45 דקות של לייב בלי הודעה. עכשיו: עוד ניסיונות עם המתנה
        (מעבר ל-2 של ה-SDK), ומה שנכשל נרשם ב-failed_windows ומדווח.
        """
        import time
        last_err = ""
        for attempt, wait in enumerate(STAGE_B_RETRY_WAITS, 1):
            if wait:
                print(f"\n   ניסיון {attempt}/{len(STAGE_B_RETRY_WAITS)} לחלון {i} "
                      f"בעוד {wait} שנ' ({last_err[:60]}) ...", end=" ", flush=True)
                time.sleep(wait)
            try:
                resp = create_message(client, **build_params(body))
            except Exception as exc:
                stop_why = fatal_api_error(str(exc))
                if stop_why:
                    print(f"\n\n{stop_why}")
                    sys.exit(1)
                last_err = f"שגיאת API: {exc}"
                print(f"שגיאה: {exc}", flush=True)
                continue
            found = handle_b(resp, i)
            if found is not None:
                return found
            last_err = "תשובה ריקה/קטועה, בלי JSON תקין"
        failed_windows[i] = last_err
        return None

    def handle_b(resp, i: int, batch: bool = False):
        """תשובה של שלב ב' (מזמן אמת או מ-batch) -> רשימת קטעים."""
        tok_in, tok_out, cw, cr = usage_of(resp)
        usage_box["in"] += tok_in
        usage_box["out"] += tok_out
        usage_box["cw"] += cw
        usage_box["cr"] += cr
        usage_box["usd"] += estimate_cost(model, tok_in, tok_out, cw, cr, batch,
                                          "1h" if batch else "5m")
        log_usage(project_root, model, tok_in, tok_out, job_name, cw, cr,
                  batch=batch, cache_ttl="1h" if batch else "5m")

        text = response_text(resp)
        stop = getattr(resp, "stop_reason", "")

        if args.save_raw:
            (raw_dir / f"window{i:02d}.txt").write_text(text, encoding="utf-8")

        if not text.strip():
            print(f"אזהרה: תשובה ריקה (stop_reason={stop}). "
                  "כנראה תקציב הפלט נגמר לפני התשובה.", flush=True)
        elif stop == "max_tokens":
            print(f"אזהרה: התשובה נקטעה (max_tokens).", flush=True)

        data, ok = extract_json_ok(text)
        if not ok:
            # 67/3: לא "אין קטעים" אלא "לא התקבלה תשובה". הקורא ינסה שוב.
            print(f"אין JSON תקין בתשובה (stop_reason={stop}).", flush=True)
            return None
        found = valid_segments(data.get("segments", []), window=i)
        print(f"{len(found)} קטעים  ({tok_in:,} טוקנים)")
        return found

    all_segments = []
    failed_windows = {}      # i -> סיבה. 67/3: חלון שלא נותח לא נעלם בשקט
    spans = {}               # i -> "hh:mm:ss-hh:mm:ss", לדיווח
    total_in = total_out = 0
    cache_write = cache_read = 0
    triage_in = triage_out = 0
    kept_sec = seen_sec = 0
    skipped_windows = 0

    for i, block in enumerate(chunks, 1):
        span = f"{hms(block[0]['start'])}-{hms(block[-1]['end'])}"
        spans[i] = span
        print(f"[{i}/{len(chunks)}] {span} ...", end=" ", flush=True)

        # ---------------------------------------------------- שלב א'
        window_lo = block[0]["start"]
        window_hi = block[-1]["end"]
        seen_sec += window_hi - window_lo
        body = rows_to_text(block)

        if triage_model:
            text, t_in, t_out, t_cw, t_cr, err = triage_window(
                client, triage_model, block, args.min)
            triage_in += t_in
            triage_out += t_out
            if t_in or t_cr:
                log_usage(project_root, triage_model, t_in, t_out,
                          f"{job_name}/triage", t_cw, t_cr)

            if err:
                stop_why = fatal_api_error(err)
                if stop_why:
                    print(f"\n\n{stop_why}")
                    sys.exit(1)
                # סינון שנפל לא מצדיק ויתור על החלון. ממשיכים עליו
                # במלואו ומשלמים - עדיף יקר מאשר לאבד קטע.
                print(f"סינון נכשל ({err[:60]}), ממשיך על החלון המלא ...",
                      end=" ", flush=True)
            else:
                regions = parse_regions(extract_json(text), window_lo,
                                        window_hi, triage_floor, args.min)
                if not regions:
                    print("אין דיבור רלוונטי, מדלג")
                    skipped_windows += 1
                    skipped_blocks.append((i, block))
                    continue
                groups = rows_in_regions(block, regions)
                if not groups:
                    print("אין דיבור רלוונטי, מדלג")
                    skipped_windows += 1
                    skipped_blocks.append((i, block))
                    continue
                kept = sum(e - s for s, e in regions)
                kept_sec += kept
                body = groups_to_text(groups)
                print(f"[{kept/60:.0f}/{(window_hi-window_lo)/60:.0f} דק'] ...",
                      end=" ", flush=True)
        else:
            kept_sec += window_hi - window_lo

        if args.batch:
            batch_jobs.append((i, body))
            print("→ batch")
            continue
        found = stage_b(body, i)
        if found is None:
            continue
        all_segments.extend(found)

    # ---------------------------------------------- Batch (משימה 57, 28.9)
    if batch_jobs:
        for i, resp in run_batch(client, batch_jobs, build_params, args.batch_poll):
            if resp is None:
                # batch שנכשל/פג לחלון הזה: לא מוותרים על החלון - שולחים
                # אותו בזמן אמת ומשלמים מחיר מלא. עדיף מלאבד קטע.
                print(f"[{i}] לא חזר מה-batch - שולח בזמן אמת ...", end=" ", flush=True)
                found = stage_b(dict(batch_jobs)[i], i)
            else:
                print(f"[{i}/{len(chunks)}] (batch) ", end="", flush=True)
                found = handle_b(resp, i, batch=True)
                if found is None:
                    # תשובה קטועה מה-batch - שולחים את החלון שוב בזמן אמת
                    print(f"   [{i}] שולח שוב בזמן אמת ...", end=" ", flush=True)
                    found = stage_b(dict(batch_jobs)[i], i)
            if found:
                all_segments.extend(found)

    # ---------------------------------------------- ביקורת על הסינון (25)
    #
    # השאלה שלא נמדדה עד 15.9: האם שלב א' זורק חלונות שיש בהם קטע טוב?
    # כאן לוקחים חלון אחד (או --audit-skipped) מאלה שנזרקו ושולחים אותו
    # לסונט בכל זאת. אם סונט מוצא בו קטע מעל הרף - זו החטאה של הסינון,
    # והקטע **נכנס לתוצאות** ולא הולך לאיבוד. העלות: עד חלון אחד נוסף,
    # ורק בהרצה שבה הסינון באמת זרק משהו.
    audit_runs = audit_hits = 0
    if triage_model and skipped_blocks and args.audit_skipped > 0:
        import random
        pick = random.sample(skipped_blocks, min(args.audit_skipped, len(skipped_blocks)))
        _, _, min_score = tier_settings(tier)
        for wi, blk in pick:
            print(f"[ביקורת] חלון {wi} שהסינון זרק - בודק בסונט ...", end=" ", flush=True)
            found = stage_b(rows_to_text(blk), wi)
            audit_runs += 1
            if not found:
                continue
            strong = [x for x in found if x.get("score", 0) >= min_score]
            if strong:
                audit_hits += 1
                print(f"  ⚠ הסינון פספס {len(strong)} קטעים בציון {min_score}+")
            for x in found:
                x["audit"] = True
            all_segments.extend(found)

    merged = merge_segments(all_segments)
    merged.sort(key=lambda s: -s.get("score", 0))

    stem = path.stem.replace("_transcript", "")

    Path(f"{stem}_segments.json").write_text(
        json.dumps(merged, ensure_ascii=False, indent=1), encoding="utf-8"
    )

    lines = []
    for i, s in enumerate(merged, 1):
        dur = (to_seconds(s["end"]) - to_seconds(s["start"])) / 60
        lines.append(f"{i}. [{s.get('score','?')}/10] {s['start']} - {s['end']}  ({dur:.0f} דק')")
        lines.append(f"   כותרת: {s.get('title','')}")
        if s.get("thumb_text"):
            lines.append(f"   תמנייל: {s['thumb_text']}"
                         f"   (בזהב: {s.get('thumb_emphasis','—')})")
        if s.get("quote"):
            lines.append(f"   ציטוט: \"{s['quote']}\"  [{s.get('quote_at','')}]")
        if s.get("cold_open"):
            lines.append(f"   פתיחה: \"{s['cold_open']}\"  [{s.get('cold_open_at','')}]")
        lines.append(f"   קטגוריה: {s.get('category','')}")
        if s.get("participants"):
            lines.append(f"   משתתפים: {', '.join(s['participants'])}")
        lines.append(f"   נושא: {s.get('topic','')}")
        lines.append(f"   הערכה: {s.get('reason','')}")
        if s.get("cut_off"):
            lines.append("   שים לב: הקטע עלול להיחתך בקצה")
        lines.append("")
    Path(f"{stem}_segments.txt").write_text("\n".join(lines), encoding="utf-8")

    # 67/3: חלונות שלא נותחו גם אחרי כל הניסיונות. run10 קורא את הקובץ ומדווח
    # בטלגרם. הקטעים משאר החלונות נשמרו - לא זורקים עבודה ששולם עליה.
    gaps_path = Path("analysis_gaps.json")
    if failed_windows:
        gaps = [{"window": i, "of": len(chunks), "span": spans.get(i, ""), "why": why}
                for i, why in sorted(failed_windows.items())]
        gaps_path.write_text(json.dumps(gaps, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"\n⚠ {len(gaps)} מתוך {len(chunks)} חלונות לא נותחו:")
        for g in gaps:
            print(f"   חלון {g['window']} ({g['span']}): {g['why'][:100]}")
    elif gaps_path.exists():
        gaps_path.unlink()
    if BATCH_STATE.exists():
        try:
            BATCH_STATE.replace("batch_state.done.json")
        except OSError:
            pass

    if args.url:
        cmds = []
        for i, s in enumerate(merged, 1):
            name = f"{stem}_clip{i:02d}"
            cmds.append(
                f'yt-dlp --download-sections "*{s["start"]}-{s["end"]}" '
                f'-o "{name}.%(ext)s" "{args.url}"'
            )
        Path(f"{stem}_cut.txt").write_text("\n".join(cmds), encoding="utf-8")

    total_in, total_out = usage_box["in"], usage_box["out"]
    cache_write, cache_read = usage_box["cw"], usage_box["cr"]
    cost = usage_box["usd"]
    print(f"\nנמצאו {len(merged)} קטעים." + ("  (batch - חצי מחיר)" if batch_jobs else ""))

    if cache_read or cache_write:
        # מה אותם טוקנים היו עולים בלי מטמון, לעומת מה שבאמת שולם
        price = next((p for k, p in PRICES.items() if k in model.lower()), None)
        if price:
            wm = CACHE_WRITE_1H_MULT if batch_jobs else CACHE_WRITE_MULT
            plain = (cache_write + cache_read) / 1000 * price["in"]
            paid = (cache_write / 1000 * price["in"] * wm
                    + cache_read / 1000 * price["in"] * CACHE_READ_MULT)
            print(f"מטמון: {cache_read:,} טוקנים נקראו, {cache_write:,} נכתבו. "
                  f"חסך ${plain - paid:.3f}")
    elif args.two_pass:
        print("מטמון: לא נוצר. אם החלונות ארוכים מ-5 דקות זה צפוי "
              "(תוקף המטמון פג), אחרת כדאי לבדוק.")

    if triage_model:
        t_cost = estimate_cost(triage_model, triage_in, triage_out)
        pct = (kept_sec / seen_sec * 100) if seen_sec else 0
        # ⚠ עם מטמון, total_in הוא רק מה ש**לא** נקרא ממנו. להשוואה
        #   מול מעבר אחד צריך את כל הקלט שהמודל ראה בפועל.
        seen_in = total_in + cache_write + cache_read
        would_be = (seen_in / (kept_sec / seen_sec) if kept_sec else seen_in)
        naive = estimate_cost(model, would_be, total_out or 1)
        print(f"שלב א' ({triage_model}): ${t_cost:.3f}  "
              f"({triage_in:,} נכנס / {triage_out:,} יצא)")
        print(f"שלב ב' ({model}): ${cost:.3f}  "
              f"({seen_in:,} נכנס / {total_out:,} יצא)")
        print(f"עבר לשלב ב': {pct:.0f}% מהחומר. "
              f"{skipped_windows} מתוך {len(chunks)} חלונות נחסכו לגמרי.")
        total_line = f"סך הכל: ${cost + t_cost:.3f}"
        if seen_in:
            total_line += f"   (בלי סינון ובלי מטמון: ~${naive:.2f})"
        print(total_line)
        if audit_runs:
            print(f"ביקורת: {audit_runs} חלונות שנזרקו נבדקו, "
                  f"ב-{audit_hits} מהם הסינון פספס קטע.")

        # ההשוואה ההוגנת היא לאותה הרצה עם מטמון ובלי סינון: אותו קלט
        # קבוע (שכבר זול בזכות המטמון), ותמלול מלא במקום המסונן.
        price = next((p for k, p in PRICES.items() if k in model.lower()), {"in": 0})
        # רק הקלט שלא מהמטמון מתכווץ עם הסינון - הקבוע נשלח ממילא
        dropped_in = (total_in / (pct / 100) - total_in) if pct else 0
        saved_b = dropped_in / 1000 * price["in"]
        save_triage_stats(project_root, job_name, {
            "tier": tier, "floor": triage_floor,
            "windows": len(chunks), "skipped": skipped_windows,
            "kept_pct": round(pct, 1),
            "triage_usd": round(t_cost, 4), "stage_b_usd": round(cost, 4),
            "saved_input_usd": round(saved_b, 4),
            "net_usd": round(saved_b - t_cost, 4),
            "audit_runs": audit_runs, "audit_hits": audit_hits,
        })
    else:
        seen_in = total_in + cache_write + cache_read
        print(f"עלות הריצה: ${cost:.3f}  ({seen_in:,} נכנס / {total_out:,} יצא)")
    print(f"נשמר: {stem}_segments.txt / .json")
    if args.url:
        print(f"נשמר: {stem}_cut.txt")
    if failed_windows and len(failed_windows) == len(chunks):
        # אף חלון לא נותח - זה כישלון של הניתוח, לא "לייב בלי קטעים"
        print("\nאף חלון לא נותח. יוצא עם שגיאה.")
        sys.exit(1)


if __name__ == "__main__":
    main()
