"""
dedup.py - מזהה קולאבים: אותו קטע שהוקלט אצל כמה סטרימרים באותו ערב

כשרונן ועודד מדברים יחד, הצינור של כל אחד מזהה את אותה שיחה.
להעלות את שתיהן זה תוכן כפול - גם ליוטיוב זה נראה רע, וגם לצופה.
הכלל של לירון: מעלים את הגרסה של הערוץ הגדול, ומדלגים על השאר.

איך זה מזהה: משווה את מילות התמלול של כל זוג קטעים מסטרימרים
שונים. שתי הקלטות של אותה שיחה חולקות את רוב המילים, גם אם
התמלול שלהן שונה בפרטים.

מי גדול ממי: שדה audience ב-watchlist.json.

שימוש:
    python scripts\\dedup.py 2026-09-10           דוח בלבד
    python scripts\\dedup.py 2026-09-10 --apply   גם מסמן skip_upload בקטעים
    python scripts\\dedup.py 2026-09-10 --threshold 0.5
"""

import re
import sys
import json
import argparse
from pathlib import Path
from itertools import combinations


def find_root(start: Path) -> Path:
    for c in [start, *start.parents]:
        if (c / "scripts").is_dir():
            return c
    return start


ROOT = find_root(Path(__file__).resolve().parent)

WORD = re.compile(r"[א-ת]{2,}|[a-zA-Z]{3,}")

# מילים שמופיעות בכל שיחה ולא מעידות על כלום
STOP = set("""אני אתה היא הוא אנחנו אתם זה זאת לא כן מה איך למה כאילו יאללה
אחי אוקיי טוב רגע בסדר עכשיו פשוט ממש הזה הזאת אבל אז גם רק עוד כל יש אין
היה הייתי אמרתי אומר להיות עושה עשיתי יודע יודעת חושב תודה שלום צאט""".split())


def load_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def to_seconds(t) -> float:
    if isinstance(t, (int, float)):
        return float(t)
    parts = [float(x) for x in str(t).strip().split(":")]
    while len(parts) < 3:
        parts.insert(0, 0.0)
    return parts[0] * 3600 + parts[1] * 60 + parts[2]


def seg_ranges(seg: dict) -> list:
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


def words_of(rows: list, ranges: list) -> set:
    bag = set()
    for a, b in ranges:
        for r in rows:
            if r["end"] >= a and r["start"] <= b:
                for w in WORD.findall(r.get("text", "")):
                    w = w.lower()
                    if w not in STOP:
                        bag.add(w)
    return bag


def similarity(a: set, b: set) -> float:
    """containment: כמה מהקטע הקטן מוכל בגדול. עמיד להבדלי אורך."""
    if not a or not b:
        return 0.0
    inter = len(a & b)
    return inter / min(len(a), len(b))


def audience_of(slug: str, wl: dict) -> int:
    for s in wl.get("streamers", []):
        if s.get("slug") == slug or s.get("key") == slug:
            return int(s.get("audience", 0))
    return 0


def collect(date: str) -> list:
    """כל הקטעים מכל העבודות של התאריך."""
    out = []
    jobs = ROOT / "jobs"
    if not jobs.is_dir():
        return out
    for d in sorted(jobs.iterdir()):
        if not d.is_dir() or not d.name.endswith(f"_{date}"):
            continue
        segs = load_json(d / "audio_segments.json", [])
        rows = load_json(d / "audio_transcript.json", [])
        if not (segs and rows):
            continue
        meta = load_json(d / "meta.json", {})
        slug = meta.get("streamer", d.name.rsplit("_", 1)[0])
        for idx, seg in enumerate(segs, 1):
            ranges = seg_ranges(seg)
            out.append({
                "job": d, "slug": slug,
                "display": meta.get("display_name", slug),
                "idx": idx, "seg": seg,
                "minutes": sum(b - a for a, b in ranges) / 60,
                "words": words_of(rows, ranges),
            })
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("date", help="YYYY-MM-DD")
    ap.add_argument("--threshold", type=float, default=0.45,
                    help="מעל זה = אותו קטע. 0.45 שמרני, להעלות אם יש התרעות שווא")
    ap.add_argument("--apply", action="store_true",
                    help="לסמן skip_upload בקבצי הקטעים, לא רק לדווח")
    args = ap.parse_args()

    wl = load_json(ROOT / "watchlist.json", {})
    items = collect(args.date)
    streamers = sorted({i["slug"] for i in items})
    print(f"{len(items)} קטעים מ-{len(streamers)} סטרימרים: {', '.join(streamers)}")
    if len(streamers) < 2:
        print("צריך לפחות שני סטרימרים באותו ערב כדי שיהיה מה להשוות.")
        return

    pairs = []
    for a, b in combinations(items, 2):
        if a["slug"] == b["slug"]:
            continue
        sim = similarity(a["words"], b["words"])
        if sim >= args.threshold:
            pairs.append((sim, a, b))
    pairs.sort(reverse=True, key=lambda x: x[0])

    if not pairs:
        print(f"לא נמצאו כפילויות (סף {args.threshold}).")
        return

    to_skip = {}   # (job, idx) -> מידע
    lines = [f"דוח כפילויות ל-{args.date}  (סף {args.threshold})", ""]
    for sim, a, b in pairs:
        aud_a = audience_of(a["slug"], wl)
        aud_b = audience_of(b["slug"], wl)
        winner, loser = (a, b) if aud_a >= aud_b else (b, a)
        lines.append(f"דמיון {sim:.0%}:")
        lines.append(f"  נשאר   {winner['display']:8} [{winner['idx']}] {winner['seg'].get('title','')[:55]}")
        lines.append(f"  מדולג  {loser['display']:8} [{loser['idx']}] {loser['seg'].get('title','')[:55]}")
        lines.append("")
        key = (loser["job"], loser["idx"])
        if key not in to_skip:
            to_skip[key] = f"קולאב - עולה אצל {winner['display']} (דמיון {sim:.0%})"

    report = "\n".join(lines)
    print(report)
    report_path = ROOT / "jobs" / f"dedup_{args.date}.txt"
    report_path.write_text(report, encoding="utf-8")
    print(f"נשמר: {report_path}")

    if not args.apply:
        print("\nזה דוח בלבד. --apply יסמן את המדולגים בקבצי הקטעים.")
        return

    by_job = {}
    for (job, idx), why in to_skip.items():
        by_job.setdefault(job, {})[idx] = why
    for job, marks in by_job.items():
        path = job / "audio_segments.json"
        segs = load_json(path, [])
        for idx, why in marks.items():
            if 1 <= idx <= len(segs):
                segs[idx - 1]["skip_upload"] = True
                segs[idx - 1]["skip_reason"] = why
        path.write_text(json.dumps(segs, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"סומנו {len(marks)} קטעים ב-{job.name}")


if __name__ == "__main__":
    main()
