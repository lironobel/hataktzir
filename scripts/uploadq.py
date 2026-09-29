"""
uploadq.py - תור העלאות מתוזמן ליוטיוב

למה זה קיים: יוטיוב נותן 10,000 יחידות מכסה ביום והעלאה עולה 1,600,
כלומר **שש העלאות ביום ולא יותר**. לילה של שלושה סטרימרים יכול
לייצר עשרה קטעים מאושרים. בלי תור, השביעי נכשל וההודעה נבלעת.

וגם בלי קשר למכסה: ערוץ חדש ששופך שישה סרטונים בשעה אחת נראה
כמו ספאם. פיזור על פני היום עדיף גם לאלגוריתם וגם לקהל.

**אין כאן מקור אמת שני.** התור נגזר מ-`audio_segments.json`:
כל קטע עם `approved: true` ובלי `youtube.id` הוא בתור. אין קובץ
תור לתחזק, אין סנכרון לשבור. (אותו לקח כמו TASKS.md מול watchlist.)

הגדרות ב-`youtube.json`, תחת `queue`:
    per_day          כמה העלאות ביום (6 = כל המכסה, מ-28.9)
    min_gap_minutes  פער מינימלי בין העלאות
    hours            [מ, עד] בשעון מקומי
    fresh_days       קטע מלייב בן פחות מזה = "טרי" ועוקף את הישנים
    fresh_reserve    כמה מקומות ביום שמורים לטריים (הלייבים בערב)
    reserve_release_hours  כמה שעות לפני איפוס המכסה השמורים משתחררים לישנים
    priority         הסטרימרים שעוקפים בתוך הטריים (וגם כל מי שמעל 250K)

**הסדר (משימה 46, 28.9):**
    1. טרי + דרמה (או יוצר אחר בקטע)  - סיפור מת תוך 2-4 ימים
    2. טרי, אחר                         - הגדולים קודם, ואז ציון
    3. ישן                              - `queue_order` ידני, ואז ציון
    ישן לא חוסם: הוא לא לוקח את `fresh_reserve` המקומות האחרונים ביום,
    עד `reserve_release_hours` לפני האיפוס. אז הם משתחררים, כך שיום בלי
    לייב לא מבזבז מכסה.

שימוש ידני:
    python scripts\\uploadq.py              מה בתור ומתי כל אחד יעלה
    python scripts\\uploadq.py --tick       להעלות אחד אם מותר עכשיו
    python scripts\\uploadq.py --now        להעלות את הבא בתור, בלי להמתין
"""

import sys
import json
import argparse
from pathlib import Path
from datetime import datetime, timezone, timedelta


def find_root(start: Path) -> Path:
    for c in [start, *start.parents]:
        if (c / "scripts").is_dir():
            return c
    return start


ROOT = find_root(Path(__file__).resolve().parent)
sys.path.insert(0, str(ROOT / "scripts"))

import upload as yt  # noqa: E402

DEFAULTS = {
    "per_day": 6,
    "min_gap_minutes": 60,
    "hours": [0, 24],
    "fresh_days": 3,
    "fresh_reserve": 2,
    "reserve_release_hours": 4,
    "priority": ["ronengg", "odedsvr", "masterohad"],
    "big_audience": 250_000,
}


def qconfig() -> dict:
    cfg = dict(DEFAULTS)
    cfg.update((yt.config().get("queue") or {}))
    return cfg


# ------------------------------------------------- גבול היום של יוטיוב
#
# המכסה מתאפסת בחצות שעון האוקיינוס השקט, לא בחצות שלנו ולא ב-UTC.
# ספירה לפי תאריך UTC הייתה משחררת את המכסה בשעה הלא נכונה ונותנת
# להעלאה השביעית להיכשל. אם אין מאגר אזורי זמן - נופלים ל-PST קבוע,
# שהוא הצד הבטוח: הוא משחרר מאוחר יותר, לא מוקדם יותר.

pacific_day_start = yt.pacific_day_start


def quota_day_start(when: datetime) -> datetime:
    """תחילת יום המכסה שבו נופל `when` (לא רק היום). לסימולציית התור."""
    try:
        from zoneinfo import ZoneInfo
        tz = ZoneInfo("America/Los_Angeles")
    except Exception:
        tz = timezone(timedelta(hours=-8))
    local = when.astimezone(tz)
    return local.replace(hour=0, minute=0, second=0,
                         microsecond=0).astimezone(timezone.utc)


def parse_ts(value: str):
    try:
        dt = datetime.fromisoformat(str(value))
    except Exception:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def uploads_since(since: datetime) -> list:
    """כל ההעלאות מאז רגע מסוים, מהחדשה לישנה."""
    out = []
    jobs = ROOT / "jobs"
    if not jobs.is_dir():
        return out
    for job in jobs.iterdir():
        if not job.is_dir():
            continue
        for i, seg in enumerate(yt.load_json(job / "audio_segments.json", []), 1):
            info = seg.get("youtube") or {}
            when = parse_ts(info.get("uploaded_at", ""))
            if when and when >= since:
                out.append({"job": job.name, "idx": i, "at": when})
    return sorted(out, key=lambda r: r["at"], reverse=True)


def used_today() -> int:
    return len(uploads_since(pacific_day_start()))


def last_upload_at():
    recent = uploads_since(pacific_day_start() - timedelta(days=2))
    return recent[0]["at"] if recent else None


# ------------------------------------------------------------- התור

def live_date(job_name: str):
    """תאריך הלייב מתוך שם העבודה (slug_2026-09-15 / ..._2026-09-05-full)."""
    import re
    m = re.search(r"(\d{4})-(\d{2})-(\d{2})", job_name)
    if not m:
        return None
    try:
        return datetime(int(m[1]), int(m[2]), int(m[3])).date()
    except ValueError:
        return None


def audiences() -> dict:
    """slug -> audience מ-watchlist. גם כבויים - קטע ישן שלהם עדיין בתור."""
    wl = yt.load_json(ROOT / "watchlist.json", {})
    out = {}
    for s in wl.get("streamers", []) if isinstance(wl, dict) else []:
        slug = str(s.get("slug") or "").lower()
        if slug:
            out[slug] = max(out.get(slug, 0), int(s.get("audience") or 0))
    return out


def classify(job_name: str, seg: dict, cfg: dict, aud: dict, today=None) -> dict:
    """
    טרי/חם/גדול לקטע אחד. 'חם' = דרמה, או שמופיע בו יוצר אחר מלבד בעל
    השידור - זה מה שמתיישן תוך ימים (ראה TASKS → השוק).
    """
    today = today or datetime.now().astimezone().date()
    d = live_date(job_name)
    age = (today - d).days if d else 99
    fresh = age < int(cfg["fresh_days"])
    slug = job_name.rsplit("_", 1)[0].lower()
    parts = [p for p in (seg.get("participants") or []) if p]
    others = len(parts) > 1
    hot = fresh and (seg.get("category") == "דרמה" or others)
    big = (slug in [x.lower() for x in cfg["priority"]]
           or aud.get(slug, 0) >= int(cfg["big_audience"]))
    return {"fresh": fresh, "hot": hot, "big": big, "age": age}


def sort_key(r: dict):
    score = -(float(r["score"]) if isinstance(r["score"], (int, float)) else 0)
    if r["hot"]:
        return (0, 0 if r["big"] else 1, score, r["approved_at"] or "")
    if r["fresh"]:
        return (1, 0 if r["big"] else 1, score, r["approved_at"] or "")
    order = r.get("queue_order")
    order = order if isinstance(order, (int, float)) else 999
    return (2, order, score, r["approved_at"] or "")


def pending() -> list:
    """
    מה מאושר וממתין להעלאה, לפי סדר העדיפות (ראה למעלה).
    קטע שנדחה, שלא הוכרע, או שכבר הועלה - לא כאן.
    """
    out = []
    jobs = ROOT / "jobs"
    if not jobs.is_dir():
        return out
    cfg = qconfig()
    aud = audiences()
    for job in sorted(jobs.iterdir()):
        if not job.is_dir():
            continue
        segs = yt.load_json(job / "audio_segments.json", [])
        for i, seg in enumerate(segs, 1):
            if seg.get("approved") is not True:
                continue
            if (seg.get("youtube") or {}).get("id"):
                continue
            if seg.get("skip_upload"):
                continue
            if backing_off(seg.get("youtube") or {}):
                continue
            out.append({
                "job": job.name,
                "idx": i,
                "title": seg.get("title", ""),
                "score": seg.get("score"),
                "approved_at": seg.get("approved_at", ""),
                "queue_order": seg.get("queue_order"),
                **classify(job.name, seg, cfg, aud),
            })
    out.sort(key=sort_key)
    return out


# כשלון העלאה חוזר. עד 15.9 קטע שנכשל (למשל בלי תיאור) נשאר ראשון בתור,
# והחוט ניסה אותו שוב כל 10 דקות ושלח הודעת כישלון בכל פעם - וגם
# חסם את כל מי שמאחוריו. עכשיו: המתנה שגדלה, ואחרי 3 כישלונות הקטע
# יוצא מהתור עד שמישהו מעלה אותו ידנית (/upload).
BACKOFF_MIN = [30, 120]
MAX_FAILS = 3


def backing_off(yt: dict, now: datetime = None) -> bool:
    fails = int(yt.get("fails") or 0)
    if not fails:
        return False
    if fails >= MAX_FAILS:
        return True
    last = parse_ts(yt.get("failed_at", ""))
    if not last:
        return False
    wait = BACKOFF_MIN[min(fails, len(BACKOFF_MIN)) - 1]
    return (now or datetime.now(timezone.utc)) < last + timedelta(minutes=wait)


def record_failure(job: Path, idx: int, error: str) -> int:
    seg = yt.get_segment(job, idx)
    fails = int((seg.get("youtube") or {}).get("fails") or 0) + 1
    yt.mark_upload(job, idx, {
        "fails": fails, "last_error": str(error)[:300],
        "failed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    })
    return fails


# --------------------------------------------------------- התזמון

def slot_for(item: dict, after: datetime, used: int, last, cfg: dict) -> datetime:
    """
    הזמן המוקדם ביותר שבו `item` יכול לעלות, בהינתן כמה כבר עלו ביום
    המכסה של `after` ומתי עלה האחרון. ארבעה אילוצים, והמאוחר קובע:
    מכסה יומית, פער, שמירת מקום לטריים, וחלון השעות.
    """
    per_day = int(cfg["per_day"])
    reserve = int(cfg["fresh_reserve"])
    release = timedelta(hours=float(cfg["reserve_release_hours"]))
    gap = timedelta(minutes=int(cfg["min_gap_minutes"]))
    when = after
    if last:
        when = max(when, last + gap)
    for _ in range(10):
        day0 = quota_day_start(when)
        n = used if day0 == quota_day_start(after) else 0
        day_end = day0 + timedelta(days=1)
        if n >= per_day:                               # 1. המכסה נגמרה
            when = day_end
            continue
        stale = not item.get("fresh", True)
        if stale and n >= per_day - reserve and when < day_end - release:
            when = day_end - release                   # 2. המקום שמור לטרי
            continue
        clamped = clamp_to_hours(when, cfg)            # 3. חלון שעות
        if clamped != when:
            when = clamped
            continue
        return when
    return when


def next_slot(now: datetime = None, item: dict = None) -> datetime:
    """מתי מותר להעלות את הבא בתור (או את `item`)."""
    cfg = qconfig()
    now = now or datetime.now(timezone.utc)
    if item is None:
        items = pending()
        item = items[0] if items else {"fresh": True}
    return slot_for(item, now, used_today(), last_upload_at(), cfg)


def clamp_to_hours(when: datetime, cfg: dict = None) -> datetime:
    """
    דוחף זמן שנפל מחוץ לחלון השעות לתחילת החלון הבא.
    גם `schedule` משתמש בזה - בלעדיו הפריט השלישי בתור היה מוצג
    כ-"היום 23:40" בזמן שבפועל הוא יעלה מחר ב-9:00, וההודעה
    לטלגרם הייתה משקרת.
    """
    cfg = cfg or qconfig()
    lo, hi = (list(cfg["hours"]) + [0, 24])[:2]
    if int(lo) <= 0 and int(hi) >= 24:
        return when
    for _ in range(8):                       # לכל היותר כמה ימים קדימה
        local = when.astimezone()
        if int(lo) <= local.hour < int(hi):
            break
        if local.hour < int(lo):
            local = local.replace(hour=int(lo), minute=0, second=0, microsecond=0)
        else:
            local = (local + timedelta(days=1)).replace(
                hour=int(lo), minute=0, second=0, microsecond=0)
        when = local.astimezone(timezone.utc)
    return when


def schedule(now: datetime = None) -> list:
    """
    התור עם זמן משוער לכל פריט - בהנחה שלא יגיע קטע טרי חדש.
    אותה פונקציה (`slot_for`) כמו ההעלאה עצמה, כך שהתצוגה לא משקרת.
    """
    cfg = qconfig()
    items = pending()
    now = now or datetime.now(timezone.utc)
    used, last = used_today(), last_upload_at()
    day0 = quota_day_start(now)
    out = []
    for item in items:
        when = slot_for(item, now, used, last, cfg)
        d = quota_day_start(when)
        used = (used if d == day0 else 0) + 1
        day0, last, now = d, when, when
        out.append({**item, "eta": when})
    return out


def due(now: datetime = None) -> bool:
    now = now or datetime.now(timezone.utc)
    items = pending()
    return bool(items) and next_slot(now, items[0]) <= now


# הרשאה שפגה (25.9). היא לא כשל של קטע: עד עכשיו היא נספרה כך, ואחרי
# 3 סבבים כל הקטעים המאושרים היו נזרקים מהתור בשקט. עכשיו התור מושהה
# ולא נוגע בקטעים, עד שקובץ הטוקן משתנה - כלומר עד ש-ytauth רץ שוב.
_auth_broken_mtime = None


def token_mtime():
    try:
        return yt.TOKEN.stat().st_mtime
    except OSError:
        return 0


def auth_paused() -> bool:
    return (_auth_broken_mtime is not None
            and token_mtime() == _auth_broken_mtime)


def tick(force: bool = False, notify=None) -> dict:
    """
    מעלה פריט אחד אם מותר עכשיו. זו הפונקציה שחוט הרקע בבוט קורא לה.
    מחזירה dict עם `did` - האם באמת הועלה משהו.
    """
    global _auth_broken_mtime
    items = pending()
    if not items:
        # מעקב "האם התקציב עוצר את הערוץ" (57): שעה שבה התור ריק ויש מכסה.
        try:
            left = int(qconfig()["per_day"]) - used_today()
            if left > 0:
                import budget
                budget.note_idle_slot(left)
        except Exception:
            pass
        return {"did": False, "reason": "empty"}
    if auth_paused() and not force:
        return {"did": False, "reason": "auth", "queued": len(items)}
    if not force and not due():
        return {"did": False, "reason": "waiting", "eta": next_slot(),
                "queued": len(items)}

    item = items[0]
    job = ROOT / "jobs" / item["job"]
    res = yt.upload_clip(job, item["idx"])
    res.update({"did": True, "job": item["job"], "idx": item["idx"],
                "title": item["title"], "left": len(items) - 1})
    if res.get("auth"):
        # הודעה אחת, לא כל 10 דקות: הסבב הבא יחזיר reason=auth בשקט
        _auth_broken_mtime = token_mtime()
    elif not res.get("ok"):
        _auth_broken_mtime = None
        fails = record_failure(job, item["idx"], res.get("error", ""))
        res["fails"] = fails
        res["gave_up"] = fails >= MAX_FAILS
    else:
        _auth_broken_mtime = None
    if notify:
        notify(res)
    return res


# ------------------------------------------------------------ תצוגה

def hhmm(dt: datetime) -> str:
    local = dt.astimezone()
    today = datetime.now().date()
    if local.date() == today:
        return local.strftime("היום %H:%M")
    if local.date() == today + timedelta(days=1):
        return local.strftime("מחר %H:%M")
    return local.strftime("%d/%m %H:%M")


def report() -> str:
    cfg = qconfig()
    rows = schedule()
    head = (f"הועלו היום {used_today()} מתוך {cfg['per_day']}  ·  "
            f"פער {cfg['min_gap_minutes']} דק'  ·  "
            f"{cfg['fresh_reserve']} שמורים לטריים")
    if auth_paused():
        head += "\n⚠ התור מושהה: ההרשאה ליוטיוב פגה. python scripts\\ytauth.py"
    if not rows:
        return f"{head}\n\nהתור ריק. אין קטעים מאושרים שממתינים להעלאה."
    lines = [head, "", f"בתור: {len(rows)}  (🔥 דרמה טרייה · 🆕 טרי · השאר ישנים)"]
    for r in rows[:12]:
        tag = "🔥" if r.get("hot") else ("🆕" if r.get("fresh") else "")
        lines.append(f"  {hhmm(r['eta'])}  {tag}[{r['idx']}] {r['title'][:48]}")
        lines.append(f"           {r['job']}")
    if len(rows) > 12:
        lines.append(f"  ...ועוד {len(rows) - 12}")
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tick", action="store_true", help="להעלות אחד אם מותר עכשיו")
    ap.add_argument("--now", action="store_true", help="להעלות את הבא, בלי להמתין")
    args = ap.parse_args()

    if not (args.tick or args.now):
        print(report())
        return

    res = tick(force=args.now)
    if not res["did"]:
        if res["reason"] == "empty":
            print("התור ריק.")
        elif res["reason"] == "auth":
            print(yt.AUTH_EXPIRED_MSG)
        else:
            print(f"עוד לא. {res['queued']} בתור, הבא ב-{hhmm(res['eta'])}.")
        return
    if res.get("ok"):
        print(f"הועלה: {res['url']}  ({res.get('privacy','')})   "
              f"נשארו בתור: {res['left']}")
    else:
        print(f"נכשל: {res.get('error','')}")


if __name__ == "__main__":
    main()
