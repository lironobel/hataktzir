"""
content.py - מדיניות תוכן (משימה 58, 28.9)

שני דברים, ושניהם בלי מודל ובלי עלות:

1. `soften(text)` - מילים קשות מקבלות כוכבית, כך שהצופה מבין והמערכות
   של יוטיוב לא. חל על כותרת, תיאור, תגיות, טקסט התמנייל והציטוט.
   "ביג פאקינג דיל" -> "ביג פ*קינג דיל". הקטע עצמו לא משתנה.
   למה: קללה חזקה או נושא רגיש בכותרת או בתמנייל הם הסיבה הנפוצה
   ל"הגבלת פרסומות" (דולר צהוב). בגוף הסרטון זה פחות קריטי.

2. `risk_flags(text)` - נושאים שכוכבית לא פותרת: הכללה על קבוצת מוצא,
   קידום הימורים, קטינים בהקשר מיני. לא משנה כלום - רק מחזיר רשימה
   שמוצגת כ-⚠ באישור ובתור, כדי שאדם יחליט.

להוסיף מילה = להוסיף שורה ל-SOFTEN או ל-FLAGS. אין צורך לגעת בשום
קובץ אחר.
"""

import re

HEB = "א-ת"
# אותיות שימוש שיכולות להידבק לפני מילה: ו-ה-ב-ל-מ-ש-כ ("והפאקינג", "לזונה")
PREFIX = f"(?<![{HEB}])([והבלמשכ]{{0,3}})"
END = f"(?![{HEB}])"

# (דפוס, החלפה). הדפוס בלי אותיות שימוש - הן נוספות לבד.
# `tail=True` = מותר המשך למילה (פאקינג, פאקינגג, זונות).
SOFTEN = [
    # קללות
    ("פאקינג", "פ*קינג", True),
    ("פאק", "פ*ק", False),
    ("פאקינג'", "פ*קינג'", True),
    ("בנזונה", "בנז*נה", True),
    ("בנזונות", "בנז*נות", True),
    ("בן זונה", "בן ז*נה", False),
    ("בני זונות", "בני ז*נות", False),
    ("זונה", "ז*נה", False),
    ("זונות", "ז*נות", False),
    ("שרמוטה", "שרמ*טה", True),
    ("שרמוטות", "שרמ*טות", True),
    ("כוס אמק", "כ*ס אמק", False),
    ("כוס אמא שלך", "כ*ס אמא שלך", False),
    ("כוסית", "כ*סית", True),
    ("מזדיין", "מזד*ין", True),
    ("מזדיינת", "מזד*ינת", True),
    ("לזיין", "לז*ין", True),
    ("זין", "ז*ן", False),
    ("חרא", "ח*א", False),
    ("מניאק", "מני*ק", True),
    ("סמרטוט", "סמרט*ט", False),
    # נושאים רגישים - הכוכבית משאירה את המשמעות ומורידה את המילה המדויקת
    ("היטלר", "היט*ר", True),
    ("נאצי", "נא*י", True),
    ("נאצים", "נא*ים", True),
    ("רצח", "ר*צח", True),
    ("רוצח", "רו*צח", True),
    ("אונס", "או*ס", False),
    ("אנס", "א*ס", False),
    ("התאבד", "הת*בד", True),
    ("התאבדות", "הת*בדות", True),
    ("סכין", "ס*כין", False),
    ("הטרדה מינית", "הטר*ה מינית", False),
    ("מטרידן", "מטר*דן", True),
]

# מילים רגילות שנראות כמו "אות שימוש + מילה קשה". נבדק על המילה המלאה.
# "מזונות" = מ + זונות. כל false positive שמתגלה - להוסיף כאן.
EXCEPT = {"מזונות", "מזון", "מזונה", "ומזונות", "המזונות", "במזונות", "למזונות",
          "שזין", "כזין"}

# באנגלית, בלי קשר לאותיות גדולות
SOFTEN_EN = [
    (r"\bfuck(ing|ed|er|s)?\b", lambda m: "f*ck" + (m.group(1) or "")),
    (r"\bshit\b", lambda m: "sh*t"),
    (r"\bbitch(es)?\b", lambda m: "b*tch" + (m.group(1) or "")),
    (r"\bhitler\b", lambda m: "H*tler"),
    (r"\bnazi(s)?\b", lambda m: "n*zi" + (m.group(1) or "")),
]

# נושאים שכוכבית לא פותרת. מחזירים סיבה, לא משנים טקסט.
FLAGS = [
    (r"מוסלמים|ערבים|שחורים|כושים|אתיופים|מזרחים|אשכנזים|רוסים|דתיים|חרדים",
     "הכללה על קבוצת מוצא/דת - סיכון לשיח שנאה"),
    (r"סטייק|stake|קזינו|casino|קוד הנחה|רולטה|בונוס באי|סלוטים",
     "הימורים - עלול להיחשב קידום"),
    (r"קטין|קטינה|בן 1[0-7]|בת 1[0-7]",
     "קטינים - לוודא שאין הקשר מיני או חשיפה"),
    (r"רצח|ר\*צח|התאבד|הת\*בד|אונס|או\*ס|טרור|פיגוע",
     "אלימות קשה - פרסומות מוגבלות גם עם כוכבית"),
    (r"היטלר|היט\*ר|נאצי|נא\*י|שואה|הייל",
     "נאציזם - פרסומות מוגבלות, להקפיד שזה לא נראה כהאדרה"),
]


def _compile():
    out = []
    for word, repl, tail in SOFTEN:
        pat = PREFIX + re.escape(word) + (f"([{HEB}']*)" if tail else "()") + END
        out.append((re.compile(pat), repl))
    # ארוך לפני קצר: "פאקינג" לפני "פאק", "בן זונה" לפני "זונה"
    out.sort(key=lambda p: -len(p[0].pattern))
    return out


_SOFTEN_RE = _compile()
_SOFTEN_EN_RE = [(re.compile(p, re.I), f) for p, f in SOFTEN_EN]
_FLAGS_RE = [(re.compile(p, re.I), why) for p, why in FLAGS]


def soften(text: str) -> str:
    """מילים קשות -> כוכבית. טקסט שאין בו כלום חוזר בדיוק כמו שהוא."""
    if not text:
        return text
    s = str(text)
    for rx, repl in _SOFTEN_RE:
        s = rx.sub(lambda m, r=repl: m.group(0) if m.group(0) in EXCEPT
                   else m.group(1) + r + m.group(2), s)
    for rx, fn in _SOFTEN_EN_RE:
        s = rx.sub(fn, s)
    return s


def soften_tags(tags) -> list:
    """תגית עם מילה קשה לא עוזרת לחיפוש אחרי שמרככים אותה - מורידים."""
    return [t for t in (tags or []) if soften(str(t)) == str(t)]


def risk_flags(*texts) -> list:
    """רשימת סיבות, בלי כפילויות. ריקה = אין מה לבדוק."""
    blob = " ".join(str(t or "") for t in texts)
    out = []
    for rx, why in _FLAGS_RE:
        if rx.search(blob) and why not in out:
            out.append(why)
    return out


def segment_flags(seg: dict, desc: dict = None) -> list:
    """כל מה שעולה לאוויר מהקטע: כותרת, תמנייל, ציטוט, תיאור."""
    desc = desc or {}
    return risk_flags(seg.get("title"), seg.get("thumb_text"), seg.get("quote"),
                      desc.get("description", "").split("──")[0])


if __name__ == "__main__":
    import sys
    for line in (sys.argv[1:] or sys.stdin.read().splitlines()):
        print(soften(line))
        for f in risk_flags(line):
            print("  ⚠", f)
