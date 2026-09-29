"""
scan_youtube.py - מאתר יוטיוברים ישראלים שמשדרים לייב, ובונה רשימת פנייה

הסורק של קיק נתקע כי אין נקודת קצה לגלישה בעברית. ביוטיוב זה קל:
חיפוש עם מסנן "לייב" מחזיר רק מי שמשדר עכשיו, ו-yt-dlp יודע לקרוא אותו.

מי שנתפס משדר מצטבר ב-prospects.json. ככל שמריצים יותר פעמים,
בשעות שונות, הרשימה מתמלאת. הערב הישראלי (20:00-01:00) הוא הזמן.

שימוש:
    python scripts\\scan_youtube.py                  סריקה אחת
    python scripts\\scan_youtube.py --watch 60       כל שעה, ברצף
    python scripts\\scan_youtube.py --report         להדפיס את הרשימה המצטברת
    python scripts\\scan_youtube.py --min-subs 1000  רק ערוצים גדולים יותר
    python scripts\\scan_youtube.py --debug

פלט:
    prospects.json      המאגר המצטבר
    prospects.txt       רשימה קריאה לפנייה, ממוינת לפי גודל
"""

import re
import sys
import json
import time
import shutil
import argparse
import subprocess
from pathlib import Path
from datetime import datetime, timezone
from urllib.parse import quote


def find_root(start: Path) -> Path:
    for c in [start, *start.parents]:
        if (c / "scripts").is_dir():
            return c
    return start


ROOT = find_root(Path(__file__).resolve().parent)
DB_PATH = ROOT / "prospects.json"
TXT_PATH = ROOT / "prospects.txt"

# מסנן "לייב" של יוטיוב. זה מה שהופך חיפוש רגיל לרשימת מי שמשדר עכשיו.
LIVE_FILTER = "EgJAAQ%3D%3D"

# שאילתות בעברית. כל אחת תופסת פינה אחרת של הסצנה.
QUERIES = [
    "לייב",
    "שידור חי",
    "סטרים",
    "לייב משחקים",
    "ג'אסט צ'אטינג",
    "מדברים עם הצאט",
    "לייב פורטנייט",
    "לייב GTA",
    "לייב מיינקראפט",
    "לייב ולורנט",
    "לייב פיפא",
    "לייב עד הבוקר",
]

HEBREW = re.compile(r"[֐-׿]")

# ערוצים שלא רלוונטיים גם אם הם בעברית: חדשות, רדיו, מוסדות
SKIP_WORDS = ("חדשות", "כאן ", "ערוץ 1", "ערוץ 12", "ערוץ 13", "ערוץ 14", "גלגלצ",
              "רדיו", "כנסת", "עיריית", "ישיבת", "בית כנסת", "שיעור", "הרב ",
              "קזינו", "casino", "news", "tv ", "וואלה", "ynet", "i24", "מכבי", "הפועל")


def log(msg: str) -> None:
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


def ytdlp(url: str, limit: int = 40, debug: bool = False):
    if not shutil.which("yt-dlp"):
        raise SystemExit("yt-dlp לא מותקן")
    cmd = ["yt-dlp", "-J", "--no-warnings", "--ignore-config",
           "--flat-playlist", "--playlist-end", str(limit), url]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=120)
    except subprocess.TimeoutExpired:
        return None
    if debug and r.stderr.strip():
        print("    " + r.stderr.strip().splitlines()[-1][:200], file=sys.stderr)
    out = (r.stdout or "").strip()
    if not out:
        return None
    try:
        return json.loads(out)
    except json.JSONDecodeError:
        return None


def is_relevant(name: str, title: str) -> bool:
    blob = f"{name} {title}".lower()
    if any(w.lower() in blob for w in SKIP_WORDS):
        return False
    return bool(HEBREW.search(name) or HEBREW.search(title))


def search_live(query: str, debug: bool) -> list:
    url = f"https://www.youtube.com/results?search_query={quote(query)}&sp={LIVE_FILTER}"
    data = ytdlp(url, debug=debug)
    if not data:
        return []
    found = []
    for it in (data.get("entries") or []):
        if not isinstance(it, dict):
            continue
        # במסנן לייב הכל אמור להיות חי, אבל בודקים בכל זאת
        status = it.get("live_status")
        if status and status not in ("is_live", "is_upcoming_live"):
            continue
        cid = it.get("channel_id") or it.get("uploader_id") or ""
        name = it.get("channel") or it.get("uploader") or ""
        if not cid or not name:
            continue
        found.append({
            "channel_id": cid,
            "name": name,
            "handle": it.get("uploader_id") if str(it.get("uploader_id", "")).startswith("@") else "",
            "channel_url": it.get("channel_url") or it.get("uploader_url") or "",
            "title": it.get("title") or "",
            "viewers": it.get("concurrent_view_count") or it.get("view_count") or 0,
            "video_id": it.get("id") or "",
            "query": query,
        })
    return found


def channel_info(url: str, debug: bool) -> dict:
    """מנויים ו-handle. קריאה אחת לערוץ, רק לחדשים."""
    data = ytdlp(url, limit=1, debug=debug)
    if not data:
        return {}
    return {
        "subs": data.get("channel_follower_count") or 0,
        "handle": data.get("uploader_id") if str(data.get("uploader_id", "")).startswith("@") else "",
        "channel_url": data.get("channel_url") or data.get("uploader_url") or url,
    }


def load_db() -> dict:
    if DB_PATH.exists():
        try:
            return json.loads(DB_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def save_db(db: dict) -> None:
    DB_PATH.write_text(json.dumps(db, ensure_ascii=False, indent=1), encoding="utf-8")


def scan(min_subs: int, debug: bool) -> tuple:
    db = load_db()
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    seen_now, new = {}, []

    for q in QUERIES:
        rows = search_live(q, debug)
        log(f"  {q:18} {len(rows):3} תוצאות")
        for r in rows:
            if not is_relevant(r["name"], r["title"]):
                continue
            cid = r["channel_id"]
            if cid in seen_now:
                if r["viewers"] > seen_now[cid]["viewers"]:
                    seen_now[cid] = r
                continue
            seen_now[cid] = r

    for cid, r in seen_now.items():
        e = db.get(cid)
        if not e:
            info = channel_info(r["channel_url"] or f"https://www.youtube.com/channel/{cid}", debug)
            e = {
                "channel_id": cid,
                "name": r["name"],
                "handle": info.get("handle") or r["handle"],
                "url": info.get("channel_url") or r["channel_url"],
                "subs": info.get("subs", 0),
                "first_seen": now,
                "sightings": 0,
                "max_viewers": 0,
                "titles": [],
                "queries": [],
                "contacted": False,
                "note": "",
            }
            new.append(e)
        e["last_seen"] = now
        e["sightings"] = e.get("sightings", 0) + 1
        e["max_viewers"] = max(e.get("max_viewers", 0), int(r["viewers"] or 0))
        e["last_viewers"] = int(r["viewers"] or 0)
        e["last_video"] = f"https://www.youtube.com/watch?v={r['video_id']}" if r["video_id"] else ""
        if r["title"] and r["title"] not in e["titles"]:
            e["titles"] = (e["titles"] + [r["title"]])[-5:]
        if r["query"] not in e["queries"]:
            e["queries"].append(r["query"])
        db[cid] = e

    save_db(db)
    write_report(db, min_subs)
    return seen_now, new, db


def write_report(db: dict, min_subs: int) -> None:
    rows = sorted(db.values(), key=lambda e: (-e.get("subs", 0), -e.get("max_viewers", 0)))
    lines = ["רשימת פנייה - יוטיוברים ישראלים שנתפסו משדרים לייב",
             f"עודכן {datetime.now().strftime('%d.%m.%Y %H:%M')}", ""]
    for e in rows:
        if e.get("subs", 0) < min_subs:
            continue
        mark = "✓ פנינו" if e.get("contacted") else "  "
        lines.append(f"{mark}  {e['name']}")
        lines.append(f"      {e.get('handle') or e.get('url')}")
        lines.append(f"      {e.get('subs', 0):,} מנויים · נראה {e.get('sightings', 0)} פעמים · שיא {e.get('max_viewers', 0)} צופים")
        if e.get("titles"):
            lines.append(f"      \"{e['titles'][-1][:60]}\"")
        if e.get("note"):
            lines.append(f"      הערה: {e['note']}")
        lines.append("")
    TXT_PATH.write_text("\n".join(lines), encoding="utf-8")


def print_report(db: dict, min_subs: int) -> None:
    rows = [e for e in db.values() if e.get("subs", 0) >= min_subs]
    rows.sort(key=lambda e: (-e.get("subs", 0), -e.get("max_viewers", 0)))
    if not rows:
        print("המאגר ריק. הרץ סריקה בערב, כשמשדרים.")
        return
    print(f"\n{'מנויים':>9}  {'שיא':>5}  {'פעמים':>5}  {'ערוץ':24} handle")
    print("-" * 80)
    for e in rows:
        c = "✓" if e.get("contacted") else " "
        print(f"{c}{e.get('subs',0):>8,}  {e.get('max_viewers',0):>5}  {e.get('sightings',0):>5}  "
              f"{e['name'][:23]:24} {e.get('handle') or e.get('url','')}")
    print(f"\n{len(rows)} ערוצים. הרשימה המלאה ב-{TXT_PATH.name}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--watch", type=int, default=0, help="לחזור כל X דקות")
    ap.add_argument("--report", action="store_true", help="רק להדפיס את המאגר")
    ap.add_argument("--min-subs", type=int, default=300)
    ap.add_argument("--debug", action="store_true")
    args = ap.parse_args()

    if args.report:
        print_report(load_db(), args.min_subs)
        return

    while True:
        log("סורק יוטיוב...")
        seen, new, db = scan(args.min_subs, args.debug)
        log(f"משדרים עכשיו: {len(seen)}  ·  חדשים: {len(new)}  ·  במאגר: {len(db)}")
        for e in sorted(new, key=lambda e: -e.get("subs", 0))[:10]:
            log(f"  חדש: {e['name']}  ({e.get('subs',0):,} מנויים)  {e.get('handle') or e.get('url')}")
        if not args.watch:
            print_report(db, args.min_subs)
            break
        time.sleep(args.watch * 60)


if __name__ == "__main__":
    main()
