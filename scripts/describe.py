"""
describe.py - כותב תיאור יוטיוב מלא לכל קטע, עם קרדיט לכל ערוץ שמופיע בו

מה הוא מייצר לכל קליפ:
    כותרת, תיאור, תגיות, קרדיטים לכל מי שמופיע, הצהרת בעלות, ופרקים
    (אם הקטע מורכב מכמה חלקים).

הקרדיטים נבנים מ-channels.json. אם ערוץ מסומן שם verified=false,
הסקריפט יזכיר את השם אבל לא ימציא קישור. זה מכוון: קישור שגוי
בתיאור גרוע מקישור חסר.

שימוש:
    python ..\\..\\scripts\\describe.py audio_segments.json
    python ..\\..\\scripts\\describe.py audio_segments.json --no-llm     בלי עלות, מתבנית
    python ..\\..\\scripts\\describe.py audio_segments.json --only 2,5

פלט (בתיקיית clips):
    descriptions.json          הכל, מובנה
    NN - <כותרת>.txt           קובץ להעתקה ליוטיוב, ליד כל mp4
    upload_notes.txt           סיכום קריא
"""

import os
import re
import sys
import json
import argparse
from pathlib import Path


# ------------------------------------------------------------------ עזרים

def to_seconds(t) -> float:
    if isinstance(t, (int, float)):
        return float(t)
    parts = [float(x) for x in str(t).strip().split(":")]
    while len(parts) < 3:
        parts.insert(0, 0.0)
    return parts[0] * 3600 + parts[1] * 60 + parts[2]


def hms(total) -> str:
    total = max(0, int(round(total)))
    return f"{total // 3600:02d}:{(total % 3600) // 60:02d}:{total % 60:02d}"


def mmss(total) -> str:
    """פורמט הפרקים של יוטיוב. חייב להתחיל ב-00:00 בפרק הראשון."""
    total = max(0, int(round(total)))
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def safe_name(text: str, limit: int = 60) -> str:
    text = re.sub(r'[<>:"/\\|?*]', "", str(text))
    text = re.sub(r"\s+", " ", text).strip()
    return text[:limit].strip() or "clip"


def find_root(start: Path) -> Path:
    for c in [start, *start.parents]:
        if (c / "scripts").is_dir():
            return c
    return start


ROOT = find_root(Path(__file__).resolve().parent)


def load_json(path: Path, default):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def segment_parts(seg: dict) -> list:
    parts = seg.get("parts")
    if isinstance(parts, list) and parts:
        out = []
        for p in parts:
            try:
                out.append((to_seconds(p["start"]), to_seconds(p["end"])))
            except (KeyError, ValueError):
                continue
        if out:
            return out
    return [(to_seconds(seg["start"]), to_seconds(seg["end"]))]


# ----------------------------------------------------------- זיהוי אנשים

def ambiguous_names(names: dict) -> set:
    """שמות שמתאימים לכמה אנשים ("שון"). לא מקושרים לאף אחד אוטומטית."""
    return {str(a.get("heard_as", "")).strip()
            for a in names.get("ambiguous", []) if a.get("heard_as")}


def build_alias_map(names: dict) -> dict:
    """
    מיפוי מכל כינוי לשם הקנוני. כולל צופים, שמסומנים בנפרד.
    שם דו-משמעי לא נכנס, גם אם מישהו הוסיף אותו ככינוי בטעות - עד 25.9
    "שון" היה כינוי של שון פי, וכל אזכור של שון שואו קיבל את הקישור שלו.
    """
    amb = ambiguous_names(names)
    amap = {}
    for group, is_creator in (("people", True), ("viewers", False), ("mods", False)):
        for p in names.get(group, []):
            canon = p.get("name", "")
            for a in [canon, *p.get("aliases", [])]:
                if a and a.strip() not in amb:
                    amap[a.strip()] = (canon, is_creator)
    return amap


def clean_participant(raw: str) -> str:
    """המנתח מוסיף לפעמים הערות בסוגריים. מוריד אותן."""
    return re.sub(r"\(.*?\)", "", str(raw)).strip()


def participant_candidates(raw: str) -> list:
    """
    "שון (SHONP)" - הסוגריים הם בדיוק מה שמזהה אותו, וכשהורדנו אותם
    נשאר "שון" הדו-משמעי. לכן מנסים לפי הסדר: הכל, מה שבסוגריים,
    ובסוף בלי הסוגריים.
    """
    raw = str(raw).strip()
    inner = [x.strip() for x in re.findall(r"\((.*?)\)", raw)]
    out = []
    for c in [raw, *inner, clean_participant(raw)]:
        if c and c not in out:
            out.append(c)
    return out


def ambiguous_in_title(title: str, names: dict, amap: dict) -> list:
    """
    "שון" לבד בכותרת, בלי שום כינוי של אחד המועמדים לידו - אזהרה.
    "שון שואו", "שון פי", "שונפי", "SHONP" - בסדר.
    """
    out = []
    for a in names.get("ambiguous", []):
        word = str(a.get("heard_as", "")).strip()
        if not word or not re.search(rf"(?<!\w){re.escape(word)}(?!\w)", title):
            continue
        cands = set(a.get("candidates", []))
        known = [k for k, (canon, _) in amap.items() if canon in cands]
        if not any(re.search(rf"(?<!\w){re.escape(k)}(?!\w)", title) for k in known):
            out.append(f"בכותרת '{word}' לבד - {' או '.join(a.get('candidates', []))}?")
    return out


def names_without_evidence(seg: dict, text: str, names: dict, amap: dict, host: str) -> list:
    """
    אדם שנקרא בשמו בכותרת או במשתתפים, אבל אין לו זכר בתמלול של הקטע.

    28.9: רונן 5.9 #7 קיבל ב-retitle את הכותרת "הדרמה של שונפי (SHONP)".
    בתמלול יש רק "שון" והתמנייל עם המסור - כלומר שון שואו. ambiguous_in_title
    לא תפס, כי בכותרת היה שם מלא ולא "שון" לבד. הבדיקה כאן הפוכה: לא "האם
    הכותרת דו-משמעית" אלא "האם יש לשם הזה ראיה בתמלול".

    text הוא התמלול המלא של הקטע (לא הקטע המקוצר שהולך למודל).
    """
    if not text:
        return []

    def found(word: str, where: str) -> bool:
        # אותיות שימוש בעברית נצמדות לשם: "לדה כהן", "ושון", "מהשון"
        return bool(re.search(rf"(?<!\w)[ובלהמשכ]{{0,2}}{re.escape(word)}(?!\w)", where, re.I))

    # שם קנוני -> [(מילה דו-משמעית, ברירת המחדל שלה)]. ברירת המחדל היא
    # המועמד הראשון ב-names.json ("שון" לבד = שון שואו אם ההקשר לא מכריע)
    amb_of = {}
    for a in names.get("ambiguous", []):
        w = str(a.get("heard_as", "")).strip()
        cands = a.get("candidates", [])
        for c in cands:
            if w and cands:
                amb_of.setdefault(c, []).append((w, cands[0]))

    claimed = []
    title = seg.get("title", "")
    for alias, (canon, is_creator) in amap.items():
        if is_creator and canon != host and canon not in claimed and found(alias, title):
            claimed.append(canon)
    for raw in (seg.get("participants") or []):
        for c in participant_candidates(raw):
            if c in amap:
                canon, is_creator = amap[c]
                if is_creator and canon != host and canon not in claimed:
                    claimed.append(canon)
                break

    out = []
    for canon in claimed:
        aliases = [k for k, (c, _) in amap.items() if c == canon]
        if any(found(k, text) for k in aliases):
            continue
        hits = [(w, d) for w, d in amb_of.get(canon, []) if found(w, text)]
        if hits and hits[0][1] == canon:
            out.append(f"{canon}: בתמלול רק '{hits[0][0]}' - לוודא שזה הוא")
        elif hits:
            # "✗" = כמעט בטוח טעות, זה בדיוק הבאג של 25.9 ו-28.9.
            # retitle לא שומר כותרת כזאת.
            out.append(f"✗ {canon}: בתמלול רק '{hits[0][0]}', שבלי הקשר הוא {hits[0][1]}")
        else:
            # לא חוסם: התמלול משבש שמות ("מה היה לי לוי" = מאיה לי לוי).
            # נבדק 28.9 על 3 עבודות: חלק מהמקרים באמת טעות, חלק שיבוש.
            out.append(f"{canon}: לא נמצא בתמלול (טעות או שיבוש תמלול?)")
    return out


def people_in(seg: dict, text: str, amap: dict, host: str, amb: set = None) -> tuple:
    """
    מחזיר (מופיעים, מוזכרים, לא מזוהים).
    משתתף שנשאר רק כשם דו-משמעי ("שון") נכנס ללא מזוהים עם סימון,
    בלי קרדיט ובלי קישור - עדיף שם חסר מקישור לאדם הלא נכון.

    מופיעים  - המארח ומי שהמנתח סימן כמשתתף. הם מקבלים קרדיט עם קישור.
    מוזכרים  - שמות שעלו רק בטקסט. מקבלים שורת אזכור בלי קישור, כי
               הזכרה בשיחה היא לא הופעה.
    לא מזוהים - משתתף שאין לו רשומה ב-names.json. מודפס כדי שנוסיף אותו.
    """
    appear, unknown = [], []

    def add(lst, name):
        if name and name not in lst and name not in appear:
            lst.append(name)

    if host:
        add(appear, host)

    amb = amb or set()
    for raw in (seg.get("participants") or []):
        cands = participant_candidates(raw)
        if not cands:
            continue
        hit = next((amap[c] for c in cands if c in amap), None)
        if hit and hit[1]:
            add(appear, hit[0])
        elif not hit:
            name = clean_participant(raw) or cands[0]
            add(unknown, f"{name} (דו-משמעי)" if name in amb else name)

    mentioned = []
    for alias, (canon, is_creator) in amap.items():
        if not is_creator or canon in appear or canon in mentioned:
            continue
        if re.search(rf"(?<!\w){re.escape(alias)}(?!\w)", text):
            mentioned.append(canon)

    return appear, mentioned, unknown


# מילים שנכתבו ב-channels.json במשמעות "אין לו ערוץ שם". הן מחרוזות,
# כלומר truthy, ולכן בלי הנרמול הזה נוצר הקישור https://kick.com/none
NO_CHANNEL = {"none", "-", "אין", "null", "n/a"}

URL_OF = {
    "kick": "https://kick.com/{}",
    "youtube": "https://youtube.com/@{}",
    "twitch": "https://twitch.tv/{}",
}


def person_links(info: dict) -> list:
    """
    כל הקישורים המאומתים של אדם, בסדר שבו הוא באמת משדר.
    שדה `live` ב-channels.json קובע מה ראשון. בלי אימות - שום קישור.
    """
    if not info.get("verified"):
        return []

    def slug(platform: str) -> str:
        # channels.json נכתב ידנית, ולכן גם "Twitch" וגם "twitch" קיימים
        raw = info.get(platform) or info.get(platform.capitalize()) or ""
        raw = str(raw).strip().lstrip("@")
        return "" if raw.lower() in NO_CHANNEL else raw

    live = str(info.get("live") or "").strip().lower()
    links, seen = [], set()
    for platform in [live, "kick", "youtube", "twitch"]:
        if platform not in URL_OF or platform in seen:
            continue
        seen.add(platform)
        s = slug(platform)
        if s:
            links.append(URL_OF[platform].format(s))
    return links


def credit_block(people: list, channels: dict) -> list:
    """שורות קרדיט. בלי קישור מומצא - רק מה שאומת."""
    db = channels.get("people", {})
    lines = []
    for name in people:
        links = person_links(db.get(name, {}))
        if links:
            lines.append(f"{name} — " + "  |  ".join(links))
        else:
            lines.append(f"{name} — (חסר קישור, למלא ב-channels.json)")
    return lines


def rights_block(channels: dict, host: str) -> list:
    """
    בלוק הזכויות. הנוסח קבוע ויושב ב-channels.json תחת channel.rights.
    מה שמשתנה בין סרטון לסרטון הוא רק {host} ו-{host_links}.
    """
    ch = channels.get("channel", {})
    template = ch.get("rights") or ch.get("disclaimer") or ""
    if not template:
        return []
    links = person_links(channels.get("people", {}).get(host, {}))
    text = str(template).format(
        host=host or "היוצר",
        host_links="  |  ".join(links) if links
                   else "(חסר קישור, למלא ב-channels.json)",
        channel=ch.get("name", ""),
        contact=ch.get("contact", ""),
    )
    return text.strip().split("\n")


# ------------------------------------------------------- תמלול של הקטע

def transcript_excerpt(rows: list, ranges: list, budget: int = 5000) -> str:
    """הטקסט של הקטע עצמו. זה מה שהמודל כותב ממנו את התיאור."""
    picked = []
    for a, b in ranges:
        for r in rows:
            if r["end"] >= a and r["start"] <= b:
                picked.append(r.get("text", ""))
    text = " ".join(picked)
    if len(text) <= budget:
        return text
    # ההתחלה והסוף חשובים יותר מהאמצע
    head = text[: int(budget * 0.6)]
    tail = text[-int(budget * 0.4):]
    return head + "\n[...]\n" + tail


# ------------------------------------------------------------ קריאה למודל

PROMPT = """אתה כותב תיאורים לסרטוני יוטיוב בעברית, לערוץ שמעלה קטעים
משידורים חיים של סטרימרים ישראלים בקיק.

לכל קטע תקבל: כותרת, נושא, ותמלול אוטומטי של הקטע עצמו.

כתוב לכל קטע:
1. "description" - שתיים עד ארבע שורות שמספרות מה קורה בקטע ולמה
   שווה לצפות. בגוף שלישי, בשפה של הקהל, בלי התלהבות מזויפת ובלי
   סימני קריאה. שורה ראשונה היא החשובה - היא מה שנראה בתוצאות
   החיפוש לפני ה"עוד".
2. "tags" - שש עד עשר תגיות בעברית ובאנגלית, בלי סולמית.
3. "hook" - משפט אחד שאפשר לנעוץ כתגובה ראשונה.

חוקים שאסור להפר:
- כל עובדה בתיאור חייבת להופיע בתמלול. אל תמציא ציטוטים, שמות,
  מספרים או אירועים. אם התמלול לא ברור - כתוב כללי יותר.
- אל תשתמש בציטוט במרכאות אלא אם המילים מופיעות בתמלול כלשונן.
- אל תבטיח בתיאור משהו שלא קורה בקטע.
- התמלול אוטומטי ומכיל שגיאות בשמות. אל תתקן שמות שאתה לא בטוח בהם -
  פשוט אל תזכיר אותם.

החזר JSON בלבד, במבנה:
{{"items": [{{"idx": 1, "description": "...", "tags": ["..."], "hook": "..."}}]}}

הקטעים:
{blocks}
"""


def response_text(resp) -> str:
    parts = []
    for block in resp.content:
        if getattr(block, "type", None) == "text":
            parts.append(block.text)
        elif getattr(block, "type", None) != "thinking" and hasattr(block, "text"):
            parts.append(block.text)
    return "\n".join(parts)


def extract_json(text: str):
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        try:
            return json.loads(text[start:end + 1])
        except json.JSONDecodeError:
            pass
    return None


def ask_model(items: list, model: str, job_name: str = "") -> dict:
    try:
        import anthropic
    except ImportError:
        print("חסרה anthropic. מייצר מתבנית במקום.")
        return {}
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("אין ANTHROPIC_API_KEY. מייצר מתבנית במקום.")
        return {}

    blocks = []
    for it in items:
        blocks.append(
            f"--- קטע {it['idx']} ---\n"
            f"כותרת: {it['title']}\n"
            f"נושא: {it['topic']}\n"
            f"אורך: {it['minutes']:.0f} דקות\n"
            f"תמלול:\n{it['text']}\n"
        )

    client = anthropic.Anthropic()
    # ⚠ עד 15.9 שם המודל נשלח כמו שהוא. claude-sonnet-4-5 כבר לא קיים,
    #   הקריאה נכשלה, והתיאור נפל לתבנית - בשקט, כי זה רק print.
    #   analyze13 מתגונן מזה עם pick_model; עכשיו גם כאן.
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from analyze13 import pick_model, usage_of, log_usage
        model = pick_model(client, model)
    except Exception:
        usage_of = log_usage = None
    print(f"מבקש תיאורים ל-{len(items)} קטעים ({model})...")
    try:
        # max_tokens כולל את החשיבה בסונט 5. 8000 היה עלול לקטוע את
        # ה-JSON באמצע כשיש הרבה קטעים. החשיבה נשארת - זה טקסט שהצופה
        # קורא, ולא חוסכים עליו.
        resp = client.messages.create(
            model=model,
            max_tokens=16000,
            messages=[{"role": "user",
                       "content": PROMPT.format(blocks="\n".join(blocks))}],
        )
    except Exception as exc:
        print(f"⚠ הקריאה למודל נכשלה: {exc}")
        return {}

    if usage_of and log_usage:
        try:
            t_in, t_out, cw, cr = usage_of(resp)
            log_usage(ROOT, model, t_in, t_out, f"{job_name}/describe", cw, cr)
            print(f"טוקנים: {t_in} נכנס, {t_out} יוצא")
        except Exception:
            pass

    data = extract_json(response_text(resp))
    if not data:
        print("לא הצלחתי לפענח את התשובה. מייצר מתבנית.")
        return {}
    return {int(i["idx"]): i for i in data.get("items", []) if "idx" in i}


# ----------------------------------------------------------- בניית התיאור

PLATFORM_TAG = {"kick": "קיק", "youtube": "יוטיוב", "twitch": "טוויץ"}

# קו מפריד. באורך הזה בכוונה - קו ארוך יותר נשבר לשתי שורות
# בטלפון, וזה נראה כמו תקלה ולא כמו עיצוב.
RULE = "─" * 26


def compose(seg: dict, idx: int, ranges: list, people: list, mentioned: list,
            written: dict, channels: dict, host_display: str,
            lead: float = 0.0) -> str:
    """
    מבנה התיאור. הסדר לא שרירותי:

    1. הטקסט קודם. יוטיוב מציג רק שתיים-שלוש שורות לפני ה"עוד",
       וזה מה שנראה בתוצאות החיפוש. כל דבר אחר שיישב שם מבזבז אותן.
    2. פרקים מיד אחריו, כי יוטיוב סורק אותם מהתיאור ודורש שהראשון
       יהיה 00:00. שלושה פרקים ומעלה הופכים לסימונים על סרגל הזמן.
    3. קרדיטים. זה התנאי שעליו הערוץ עומד, ולכן הוא מעל הקיפול
       של הבלוק המשפטי ולא מתחתיו.
    4. זכויות, מ-channel.rights ב-channels.json.
    5. האשטאגים אחרונים. שלושת הראשונים מוצגים מעל הכותרת.
    """
    ch = channels.get("channel", {})
    out = []

    desc = (written.get("description") or "").strip()
    if not desc:
        topic = seg.get("topic", "").strip()
        desc = topic or seg.get("title", "")
    out.append(desc)
    out.append("")

    # פרקים - רק אם הקטע באמת מורכב מכמה חלקים
    if len(ranges) > 1:
        out.append(RULE)
        out.append("⏱️ פרקים")
        # lead = הטיזר בתחילת הקובץ (cut3, משימה 37). הוא לא פרק משלו -
        # יוטיוב דורש 10 שניות לפרק ומבטל את כל הפרקים אם אחד קצר יותר -
        # אז הוא נבלע בחלק 1, וכל שאר החלקים זזים בו.
        clock = 0.0
        for j, (a, b) in enumerate(ranges, 1):
            out.append(f"{mmss(clock)} חלק {j}")
            clock += (b - a) + (lead if j == 1 else 0.0)
        out.append("")

    # הכותרת קצרה בכוונה: "נלקח משידור חי" נאמר פעם אחת, בבלוק הזכויות
    credits = credit_block(people, channels)
    if credits:
        out.append(RULE)
        out.append("▶️ מי שמופיע בקטע")
        out.extend(credits)
        out.append("")

    rights = rights_block(channels, people[0] if people else host_display)
    if rights:
        out.append(RULE)
        out.extend(rights)
        out.append("")

    # רק אם בלוק הזכויות לא כבר נתן את הכתובת. אחרת אותו handle פעמיים
    contact = ch.get("contact", "").strip()
    if contact and not any(contact in line for line in rights):
        out.append(f"לפניות: {contact}")
        out.append("")

    # מוזכרים יושבים בתחתית, אחרי הזכויות ולפני ההאשטאגים. הם לא
    # מקבלים קישור בכוונה - אלה אנשים שהשם שלהם נאמר בקטע, לא
    # משתתפים בו, וקישור אליהם היה נראה כמו קרדיט שלא מגיע להם.
    if mentioned:
        out.append(RULE)
        out.append("מוזכרים בקטע: " + ", ".join(mentioned))
        out.append("")

    tags = written.get("tags") or []
    base = [t.lstrip("#") for t in ch.get("hashtags", [])]
    # "#קיק" בסרטון של מי שמשדר ביוטיוב הוא פשוט לא נכון. הפלטפורמה
    # נגזרת משדה live של המארח ב-channels.json.
    live = str((channels.get("people", {}).get(host_display, {}) or {})
               .get("live") or "kick").lower()
    platform_tag = PLATFORM_TAG.get(live, "")
    base = [platform_tag if t in PLATFORM_TAG.values() else t for t in base]
    base = [t for t in base if t]
    hashtags = []
    for t in [host_display, *base, *tags]:
        t = str(t).strip().lstrip("#").replace(" ", "")
        if t and f"#{t}" not in hashtags:
            hashtags.append(f"#{t}")
    if hashtags:
        out.append(RULE)
        out.append(" ".join(hashtags[:15]))

    return "\n".join(out).strip() + "\n"


# ------------------------------------------------------------------ ראשי

def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("segments")
    ap.add_argument("--out", default="clips")
    ap.add_argument("--transcript", default="audio_transcript.json")
    ap.add_argument("--min-score", type=int, default=6)
    ap.add_argument("--only", default="")
    ap.add_argument("--model", default="claude-sonnet-5")
    ap.add_argument("--no-llm", action="store_true", help="בלי עלות, תיאור מתבנית")
    args = ap.parse_args()

    seg_path = Path(args.segments)
    if not seg_path.exists():
        print(f"לא נמצא: {seg_path}")
        sys.exit(1)

    job_dir = seg_path.resolve().parent
    segments = load_json(seg_path, [])
    if not segments:
        print("אין קטעים.")
        sys.exit(0)

    meta = load_json(job_dir / "meta.json", {})
    host_display = meta.get("display_name") or meta.get("streamer") or ""
    names = load_json(ROOT / "names.json", {})
    channels = load_json(ROOT / "channels.json", {})
    if not channels:
        print("לא נמצא channels.json בשורש הפרויקט. אין ממה לבנות קרדיטים.")
        sys.exit(1)
    rows = load_json(job_dir / args.transcript, [])
    # הטווחים שבאמת נחתכו (cut3), כולל אורך הטיזר. לפרקים בלבד - הם
    # מדויקים יותר מ-parts של המנתח, שזזים עד 45 שניות בליטוש.
    clip_ranges = {r.get("idx"): r for r in load_json(job_dir / args.out / "ranges.json", [])
                   if isinstance(r, dict)}

    amap = build_alias_map(names)
    amb = ambiguous_names(names)

    if args.only:
        wanted = {int(x) for x in args.only.split(",") if x.strip().isdigit()}
        chosen = [(i, s) for i, s in enumerate(segments, 1) if i in wanted]
    else:
        chosen = [(i, s) for i, s in enumerate(segments, 1)
                  if s.get("score", 0) >= args.min_score]

    if not chosen:
        print("אף קטע לא עבר את הסינון.")
        sys.exit(0)

    items = []
    for idx, seg in chosen:
        ranges = segment_parts(seg)
        text = transcript_excerpt(rows, ranges) if rows else ""
        full = transcript_excerpt(rows, ranges, budget=10**9) if rows else ""
        items.append({
            "full_text": full,
            "idx": idx,
            "title": seg.get("title", ""),
            "topic": seg.get("topic", ""),
            "minutes": sum(b - a for a, b in ranges) / 60,
            "text": text,
            "ranges": ranges,
            "seg": seg,
        })

    if not rows:
        print("לא נמצא תמלול. התיאורים ייבנו מהכותרת והנושא בלבד.")

    written = {} if (args.no_llm or not rows) else ask_model(items, args.model, job_dir.name)
    if not written and not args.no_llm:
        print("\n⚠ התיאורים נבנו מתבנית ולא מהמודל. הם יסומנו כך בטלגרם.\n"
              "  להריץ שוב: python ..\\..\\scripts\\describe.py audio_segments.json\n")

    out_dir = job_dir / args.out
    out_dir.mkdir(parents=True, exist_ok=True)

    results = []
    missing_links = set()
    unknown_people = set()

    for it in items:
        idx, seg = it["idx"], it["seg"]
        people, mentioned, unknown = people_in(seg, it["text"], amap, host_display, amb)
        unknown_people.update(unknown)
        # שם בכותרת שהוא דו-משמעי או לא מוכר - approve2 מציג ⚠ (משימה 36)
        warn = ambiguous_in_title(seg.get("title", ""), names, amap) + list(unknown)
        warn += names_without_evidence(seg, it.get("full_text", ""), names, amap, host_display)
        for p in people:
            info = channels.get("people", {}).get(p, {})
            if not (info.get("verified") and info.get("kick")):
                missing_links.add(p)

        cut = clip_ranges.get(idx) or {}
        body = compose(seg, idx, cut.get("ranges") or it["ranges"], people, mentioned,
                       written.get(idx, {}), channels, host_display,
                       lead=float(cut.get("lead") or 0))

        name = f"{idx:02d} - {safe_name(seg.get('title', ''))}"
        (out_dir / f"{name}.txt").write_text(body, encoding="utf-8")

        results.append({
            "idx": idx,
            "file": f"{name}.mp4",
            "title": seg.get("title", ""),
            "score": seg.get("score"),
            "minutes": round(it["minutes"], 1),
            "people": people,
            "mentioned": mentioned,
            "name_warnings": warn,
            "source": [f"{hms(a)}-{hms(b)}" for a, b in it["ranges"]],
            "description": body,
            "tags": written.get(idx, {}).get("tags", []),
            "hook": written.get(idx, {}).get("hook", ""),
            # "template" = המודל לא ענה והתיאור הוא הנושא בלבד. approve2
            # מציג את זה, כדי שתיאור חלול לא יעלה בלי שמישהו ישים לב.
            "desc_source": "llm" if written.get(idx) else "template",
        })
        print(f"[{idx}] {seg.get('title','')[:55]}")
        if warn:
            print("     ⚠ שמות לבדיקה: " + " · ".join(warn))
        print(f"     קרדיט:  {', '.join(people) or '—'}")
        if mentioned:
            print(f"     מוזכרים: {', '.join(mentioned)}")

    # מיזוג עם מה שכבר קיים. עד 15.9, הרצה עם --only דרסה את
    # descriptions.json ומחקה ממנו את כל שאר הקטעים - והעלאה שלהם
    # הייתה נעצרת על "אין תיאור".
    merged = {r.get("idx"): r for r in load_json(out_dir / "descriptions.json", [])
              if isinstance(r, dict)}
    for r in results:
        merged[r["idx"]] = r
    results_all = [merged[k] for k in sorted(merged)]
    (out_dir / "descriptions.json").write_text(
        json.dumps(results_all, ensure_ascii=False, indent=1), encoding="utf-8")

    notes = []
    for r in results_all:
        notes.append("=" * 70)
        notes.append(f"קובץ: {r['file']}")
        notes.append(f"כותרת: {r['title']}")
        notes.append(f"מקור: {', '.join(r['source'])}   ({r['minutes']} דק')")
        if r["hook"]:
            notes.append(f"תגובה נעוצה: {r['hook']}")
        notes.append("")
        notes.append(r["description"])
        notes.append("")
    (out_dir / "upload_notes.txt").write_text("\n".join(notes), encoding="utf-8")

    print(f"\nנשמר: {out_dir / 'descriptions.json'}")
    print(f"       {out_dir / 'upload_notes.txt'}")
    print(f"       וקובץ txt ליד כל mp4")

    if missing_links:
        print("\nחסרים קישורים ל: " + ", ".join(sorted(missing_links)))
        print("מלא אותם ב-channels.json והרץ שוב. בינתיים הם מופיעים בשם בלבד.")

    if unknown_people:
        print("\nמשתתפים שאין להם רשומה ב-names.json: "
              + ", ".join(sorted(unknown_people)))
        print("אם הם יוצרי תוכן - כדאי להוסיף אותם, כדי שיקבלו קרדיט אמיתי.")


if __name__ == "__main__":
    main()
