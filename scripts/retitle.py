"""
retitle.py - כותב מחדש כותרת וטקסט תמנייל לקטעים שכבר נחתכו

למה זה קיים (משימה 22ב, 15.9): הקטעים של ניק נחתכו לפני תיקון באג
הייחוס, ואחד מהם יוחס לרונן. החיתוך עצמו טוב - רק הטקסט שגוי. חיתוך
מחדש עולה הורדה וניתוח מלא ומזיז את הגבולות; כאן משלמים קריאה אחת
קטנה לכל קטע, והקליפ נשאר כמו שהוא.

מה הוא עושה לכל קטע:
    1. שולח למודל רק את התמלול של הקטע, עם אותם כללי כותרת ותמנייל
       שיש ב-analyze13 (נחתכים משם, כדי שלא יתפצלו לשני נוסחים)
    2. מקבל title, thumb_text, thumb_emphasis, quote, quote_at
    3. מעדכן את audio_segments.json (הכותרת הישנה נשמרת ב-title_was),
       משנה את שמות הקבצים, ומריץ describe.py מחדש על אותם קטעים

שימוש:
    python scripts\\retitle.py jobs\\pedrofederer_2026-09-09 --dry        רק להראות
    python scripts\\retitle.py jobs\\pedrofederer_2026-09-09
    python scripts\\retitle.py jobs\\pedrofederer_2026-09-09 --only 1,3
    python scripts\\retitle.py jobs\\pedrofederer_2026-09-09 --reset-youtube 2 --send

    --reset-youtube N   לקטע שהסרטון שלו נמחק מיוטיוב ידנית. בלי זה
                        הצינור חושב שהוא עדיין למעלה ולא יעלה אותו שוב.
    --send              בסוף, לשלוח את הקטעים לאישור בטלגרם.

עלות: סנטים בודדים לקטע.
"""

import os
import re
import sys
import json
import argparse
import subprocess
from pathlib import Path
from datetime import datetime, timezone


def find_root(start: Path) -> Path:
    for c in [start, *start.parents]:
        if (c / "scripts").is_dir():
            return c
    return start


ROOT = find_root(Path(__file__).resolve().parent)
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

import analyze13 as az  # noqa: E402
import describe as ds   # noqa: E402  בדיקת השמות מול התמלול - אותה פונקציה כמו באישור


def load_json(path: Path, default):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return default


def save_json(path: Path, data) -> None:
    Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")


def between(text: str, start: str, end: str) -> str:
    """חותך קטע מתוך הפרומפט של analyze13. ריק אם הסמנים זזו."""
    i = text.find(start)
    j = text.find(end, i + 1) if i >= 0 else -1
    return text[i:j].strip() if i >= 0 and j > i else ""


# הכללים עצמם נלקחים מ-analyze13. אם מישהו ישנה שם את כללי הכותרת,
# השינוי יחול גם כאן בלי לגעת בקובץ הזה.
TITLE_RULES = between(az.PROMPT, "כללי הכותרת.", "מדרג הציונים:")
THUMB_RULES = between(az.PROMPT, "כללי התמנייל.", "על השדה parts:")

SYSTEM = """אתה עורך כותרות של ערוץ יוטיוב שמפרסם קטעים מלייבים של סטרימרים ישראלים.
הקטע כבר נבחר ונחתך. התפקיד שלך הוא רק לכתוב לו כותרת וטקסט לתמנייל
שמייצגים בדיוק את מה שקורה בו, לפי הכללים. אל תמציא דבר שלא מופיע
בתמלול. התמלול אוטומטי ומכיל שגיאות בשמות ובמילים."""

PROMPT = """{names}
{topics}

להלן התמלול של קטע אחד שכבר נבחר, עם חותמות זמן מתוך השידור.
הנושא כפי שסוכם בניתוח הראשון: {topic}
הסיכום הזה עלול להיות שגוי, במיוחד בשמות - retitle קיים בדיוק כדי לתקן
טעויות כאלה. אם הוא סותר את התמלול, התמלול קובע. אל תעתיק ממנו שם
שאין לו ראיה בתמלול עצמו.

{title_rules}

{thumb_rules}

`quote` הוא המשפט שהכי מייצג את הקטע, מועתק מהתמלול כלשונו.
`quote_at` הוא הזמן שבו נאמר, בפורמט HH:MM:SS, והוא חייב ליפול בין
{lo} ל-{hi}. אם הכותרת פותחת בציטוט במרכאות - הוא חייב להופיע מילה
במילה בתמלול הזה, לא בסרטון אחר.

החזר JSON בלבד:
{{"title": "...", "thumb_text": "...", "thumb_emphasis": "...",
  "quote": "...", "quote_at": "HH:MM:SS",
  "participants": ["..."], "why": "משפט אחד: למה הכותרת הזאת"}}

התמלול:
---
{transcript}
---"""


def segment_rows(rows: list, seg: dict) -> list:
    out = []
    for p in (seg.get("parts") or [{"start": seg["start"], "end": seg["end"]}]):
        a, b = az.to_seconds(p["start"]), az.to_seconds(p["end"])
        out.extend(r for r in rows if r["end"] >= a and r["start"] <= b)
    return out


def trim_text(rows: list, budget_chars: int = 60_000) -> str:
    """קטע של 40 דקות נכנס כמעט תמיד. אם לא - התחלה וסוף, כמו describe."""
    text = az.rows_to_text(rows)
    if len(text) <= budget_chars:
        return text
    head = text[: int(budget_chars * 0.6)]
    tail = text[-int(budget_chars * 0.4):]
    return head + "\n[...]\n" + tail


def streamer_name(job: Path) -> str:
    meta = load_json(job / "meta.json", {})
    name = meta.get("display_name") or ""
    prof = az.load_profile(ROOT, (meta.get("streamer") or job.name.rsplit("_", 1)[0]))
    return prof.get("display_name") or name or meta.get("streamer", "")


def check_result(res: dict, seg: dict, streamer: str) -> list:
    """מה לא בסדר בתשובה. רשימה ריקה = תקין."""
    problems = []
    title = (res.get("title") or "").strip()
    if not title:
        problems.append("אין כותרת")
    if streamer and streamer not in title:
        problems.append(f"הכותרת לא מזכירה את {streamer}")
    if re.search(r"[A-Za-z]{6,}", title):
        problems.append("יש בכותרת מילה לועזית ארוכה - אולי slug")
    if len(title) > 100:
        problems.append("כותרת ארוכה מ-100 תווים")
    tt, em = res.get("thumb_text") or "", res.get("thumb_emphasis") or ""
    if len(tt.split()) > 4:
        problems.append("thumb_text ארוך מ-4 מילים")
    if em and em not in tt.split():
        problems.append("thumb_emphasis לא מתוך thumb_text")
    try:
        q = az.to_seconds(str(res.get("quote_at", "")))
        lo, hi = az.to_seconds(seg["start"]), az.to_seconds(seg["end"])
        if not lo <= q <= hi:
            problems.append("quote_at מחוץ לקטע")
    except Exception:
        problems.append("quote_at לא תקין")
    return problems


def ask(client, model: str, prompt: str):
    resp = az.create_message(
        client, model=model, max_tokens=8000,
        system=SYSTEM,
        messages=[{"role": "user", "content": prompt}],
    )
    return resp


def safe_name(text: str, limit: int = 60) -> str:
    """זהה ל-cut3/describe/tgbot2, כדי ששמות הקבצים יישארו עקביים."""
    text = re.sub(r'[<>:"/\\|?*]', "", str(text))
    text = re.sub(r"\s+", " ", text).strip()
    return text[:limit].strip() or "clip"


def rename_files(job: Path, idx: int, new_title: str) -> list:
    """כל הקבצים של הקטע: mp4, txt, תמנייל. שומר סיומות כמו __p1."""
    clips = job / "clips"
    renamed = []
    if not clips.is_dir():
        return renamed
    new_base = f"{idx:02d} - {safe_name(new_title)}"
    for p in sorted(clips.glob(f"{idx:02d} - *")):
        if p.is_dir():
            continue
        m = re.search(r"((?:__p\d+)|(?:__thumb)|(?:__sheet))$", p.stem)
        tail = m.group(1) if m else ""
        target = clips / f"{new_base}{tail}{p.suffix}"
        if target == p:
            continue
        if target.exists():
            print(f"     קיים כבר: {target.name}, לא דורס")
            continue
        p.rename(target)
        renamed.append((p.name, target.name))
    return renamed


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("job", help="תיקיית העבודה")
    ap.add_argument("--only", default="", help="מספרי קטעים, מופרדים בפסיק")
    ap.add_argument("--min-score", type=int, default=6)
    ap.add_argument("--model", default="claude-sonnet-5")
    ap.add_argument("--dry", action="store_true", help="להראות בלבד, בלי לשנות קבצים")
    ap.add_argument("--force", action="store_true",
                    help="גם קטע שכבר עלה ליוטיוב (הכותרת שם לא תשתנה)")
    ap.add_argument("--reset-youtube", default="",
                    help="קטעים שהסרטון שלהם נמחק מיוטיוב ידנית, מופרדים בפסיק")
    ap.add_argument("--send", action="store_true", help="לשלוח לאישור בטלגרם בסוף")
    ap.add_argument("--no-describe", action="store_true")
    args = ap.parse_args()

    job = Path(args.job)
    if not job.is_dir():
        job = ROOT / args.job
    if not job.is_dir():
        sys.exit(f"לא נמצאה תיקייה: {args.job}")

    seg_path = job / "audio_segments.json"
    segs = load_json(seg_path, [])
    rows = load_json(job / "audio_transcript.json", [])
    if not segs or not rows:
        sys.exit("חסר audio_segments.json או audio_transcript.json.")

    # קודם איפוס היוטיוב, כי הוא משנה מה נחשב "כבר עלה"
    reset = {int(x) for x in args.reset_youtube.split(",") if x.strip().isdigit()}
    for i in sorted(reset):
        if not 1 <= i <= len(segs):
            continue
        yt = segs[i - 1].pop("youtube", None)
        if yt:
            hist = segs[i - 1].setdefault("youtube_history", [])
            hist.append({**yt, "removed_at": datetime.now(timezone.utc)
                         .isoformat(timespec="seconds"), "why": "נמחק ידנית"})
            print(f"[{i}] נשכחה ההעלאה {yt.get('id','')} (נשמרה ב-youtube_history)")
    if reset and not args.dry:
        save_json(seg_path, segs)

    wanted = {int(x) for x in args.only.split(",") if x.strip().isdigit()}
    chosen = []
    for i, seg in enumerate(segs, 1):
        if wanted and i not in wanted:
            continue
        if not wanted and seg.get("score", 0) < args.min_score:
            continue
        if (seg.get("youtube") or {}).get("id") and not args.force:
            print(f"[{i}] כבר ביוטיוב, מדלג (--force כדי בכל זאת)")
            continue
        chosen.append((i, seg))
    if not chosen:
        sys.exit("אין קטעים לעבוד עליהם.")

    if not os.environ.get("ANTHROPIC_API_KEY"):
        sys.exit('חסר ANTHROPIC_API_KEY.  setx ANTHROPIC_API_KEY "sk-ant-..."')
    if not TITLE_RULES or not THUMB_RULES:
        sys.exit("לא הצלחתי לחלץ את הכללים מ-analyze13. הפרומפט שם השתנה?")

    client = az.anthropic.Anthropic()
    model = az.pick_model(client, args.model)
    streamer = streamer_name(job)
    names = az.load_names(ROOT)
    names_data = load_json(ROOT / "names.json", {})
    amap = ds.build_alias_map(names_data)
    prof = az.load_profile(ROOT, job.name.rsplit("_", 1)[0])
    topics = az.profile_topics(prof)
    print(f"בעל השידור: {streamer} · מודל: {model} · {len(chosen)} קטעים\n")

    changed = []
    for i, seg in chosen:
        block = segment_rows(rows, seg)
        if not block:
            print(f"[{i}] אין תמלול בטווח, מדלג")
            continue
        prompt = PROMPT.format(
            names=names,
            topics=("נושאים שהקהל אוהב אצל הסטרימר הזה:\n" + topics) if topics else "",
            topic=seg.get("topic", ""),
            title_rules=TITLE_RULES.replace("{streamer}", streamer)
                                   .replace("{{", "{").replace("}}", "}"),
            thumb_rules=THUMB_RULES.replace("{{", "{").replace("}}", "}"),
            lo=seg["start"], hi=seg["end"],
            transcript=trim_text(block),
        )
        try:
            resp = ask(client, model, prompt)
        except Exception as exc:
            why = az.fatal_api_error(str(exc))
            if why:
                sys.exit(why)
            print(f"[{i}] הקריאה נכשלה: {exc}")
            continue
        t_in, t_out, cw, cr = az.usage_of(resp)
        az.log_usage(ROOT, model, t_in, t_out, f"{job.name}/retitle", cw, cr)
        res = az.extract_json(az.response_text(resp))
        if not res.get("title"):
            print(f"[{i}] לא התקבלה כותרת. התשובה:\n{az.response_text(resp)[:300]}")
            continue

        problems = check_result(res, seg, streamer)
        # 28.9: הכותרת החדשה של רונן #7 אמרה SHONP כשבתמלול יש רק "שון"
        # (ה-dry של אותו קטע יצא נכון - המודל לא עקבי). שם בלי ראיה בתמלול
        # מסומן ✗ ולא נשמר.
        proposed = {"title": res["title"],
                    "participants": res.get("participants") or seg.get("participants") or []}
        full = " ".join(r.get("text", "") for r in block)
        name_issues = ds.names_without_evidence(proposed, full, names_data, amap, streamer)
        blocked = [x for x in name_issues if x.startswith("✗")]
        problems += name_issues
        print(f"[{i}] {seg.get('title','')}")
        print(f"  → {res['title']}")
        print(f"     תמנייל: {res.get('thumb_text','')}  (זהב: {res.get('thumb_emphasis','')})")
        print(f"     ציטוט: \"{res.get('quote','')}\"  [{res.get('quote_at','')}]")
        if res.get("why"):
            print(f"     למה: {res['why']}")
        if problems:
            print("     ⚠ " + " · ".join(problems))
        print(f"     ${az.estimate_cost(model, t_in, t_out, cw, cr):.3f}\n")

        if args.dry:
            continue
        if blocked:
            print(f"     ✗ לא נשמר: שם בכותרת בלי ראיה בתמלול. להריץ שוב את הקטע,\n"
                  f"       או לתקן את הכותרת ידנית ב-✎ בטלגרם.\n")
            continue
        if "quote_at מחוץ לקטע" in problems or "quote_at לא תקין" in problems:
            res.pop("quote_at", None)       # התמנייל ייפול לאמצע הקליפ
        seg.setdefault("title_was", seg.get("title", ""))
        for key in ("title", "thumb_text", "thumb_emphasis", "quote", "quote_at"):
            if res.get(key):
                seg[key] = res[key]
        if res.get("participants"):
            seg["participants"] = res["participants"]
        seg["retitled_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        for old, new in rename_files(job, i, seg["title"]):
            print(f"     שם קובץ: {new}")
        changed.append(i)

    if args.dry or not changed:
        print("--dry: לא שונה כלום." if args.dry else "לא שונה כלום.")
        return
    save_json(seg_path, segs)

    # תיאור מחדש: הכותרת נכנסת לפרומפט שלו, והקובץ נקרא לפי הכותרת
    if not args.no_describe:
        only = ",".join(str(i) for i in changed)
        print(f"\nמריץ describe.py על {only} ...")
        rc = subprocess.run([sys.executable, str(SCRIPTS / "describe.py"),
                             "audio_segments.json", "--only", only],
                            cwd=job).returncode
        if rc != 0:
            print("⚠ describe.py נכשל. הכותרות עודכנו, התיאורים לא.")

    if args.send:
        from approve2 import send_for_approval
        n = send_for_approval(job, min_score=args.min_score)
        print(f"נשלחו {n} קטעים לאישור בטלגרם.")


if __name__ == "__main__":
    main()
