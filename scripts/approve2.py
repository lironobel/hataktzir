"""
approve2.py - שולח כל קטע מוכן לטלגרם, כווידאו, עם כפתורי החלטה

מה השתנה מ-approve.py: קודם הגיעה הודעת טקסט ובשביל לראות את הקטע
היה צריך לגשת למחשב. עכשיו מגיעה תצוגה מקדימה של הפתיחה ישר לטלפון,
עם ארבעה כפתורים מתחתיה:

    ✓ אשר        מסמן להעלאה, נכנס ללולאת הלמידה
    ✗ דחה        מסמן ושואל למה. הסיבה נכנסת לפרומפט של המנתח
    ✎ כותרת      לשנות את הכותרת בלי לגעת בקבצים
    📼 מלא       לשלוח את כל הקליפ מכווץ, אם רוצים לראות הכל

התצוגה המקדימה היא 75 השניות הראשונות. זה בכוונה - הדבר שהכי
נוטה להישבר הוא נקודת ההתחלה, וזה בדיוק מה שרואים שם. הקידוד לוקח
כמה שניות עם הכרטיס, ואם הוא תפוס בתמלול - ffmpeg יעשה את זה במעבד.

החצי המקבל של הלולאה יושב ב-tgbot2.py.

הרצה ידנית:
    python scripts\\approve2.py jobs\\ronengg_2026-09-10
    python scripts\\approve2.py jobs\\ronengg_2026-09-10 --no-video
    python scripts\\approve2.py jobs\\ronengg_2026-09-10 --min-score 7
"""

import sys
import json
import argparse
from pathlib import Path


def find_root(start: Path) -> Path:
    for c in [start, *start.parents]:
        if (c / "scripts").is_dir():
            return c
    return start


ROOT = find_root(Path(__file__).resolve().parent)
sys.path.insert(0, str(ROOT / "scripts"))

from tg import notify, send_video  # noqa: E402

try:
    from tg import send_photo
except ImportError:
    send_photo = None

try:
    from preview import make_preview, clip_for, size_mb
except ImportError:                                    # בלי preview.py עדיין עובד
    make_preview = clip_for = size_mb = None


def load_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def hhmm(minutes: float) -> str:
    h, m = divmod(int(minutes), 60)
    return f"{h}:{m:02d} שע'" if h else f"{m} דק'"


def buttons_for(job_name: str, idx: int, has_file: bool) -> list:
    """
    שתי שורות. אישור ודחייה למעלה כי הן ההחלטה,
    כותרת וקובץ מלא למטה כי הן פעולות עזר.
    """
    rows = [[
        {"text": "✓ אשר", "callback_data": f"ap:{job_name}:{idx}"},
        {"text": "✗ דחה", "callback_data": f"rj:{job_name}:{idx}"},
    ]]
    second = [{"text": "✎ כותרת", "callback_data": f"ti:{job_name}:{idx}"}]
    if has_file:
        second.append({"text": "📼 מלא", "callback_data": f"fl:{job_name}:{idx}"})
    rows.append(second)
    return rows


def esc(text) -> str:
    """טקסט של המודל נכנס להודעת HTML. < או & בודד שוברים את השליחה."""
    return (str(text or "").replace("&", "&amp;")
            .replace("<", "&lt;").replace(">", "&gt;"))


def segment_text(idx: int, seg: dict, minutes, note: str = "", desc: dict = None) -> str:
    """
    ההודעה צריכה להספיק כדי להחליט בלי לצפות (הכיוון של לירון, 15.9):
    מה קורה, למה המנתח חושב שזה יעבוד, ומה הצופה יקרא בתיאור.
    """
    desc = desc or {}
    dur = f" · {hhmm(minutes)}" if minutes else ""
    lines = [f"<b>[{idx}] {esc(seg.get('title',''))}</b>",
             f"ציון {seg.get('score','?')}/10{dur}",
             esc((seg.get('topic') or '')[:180])]
    if seg.get("reason"):
        lines.append(f"<i>למה: {esc(seg['reason'][:220])}</i>")
    first = (desc.get("description") or "").strip().split("\n")[0]
    if first and first != seg.get("topic"):
        lines.append(f"תיאור: {esc(first[:200])}")
    if desc.get("desc_source") == "template":
        lines.append("⚠ התיאור מתבנית, המודל לא ענה. כדאי להריץ describe.py שוב.")
    if desc.get("name_warnings"):
        # 25.9: "שון" יוחס לשון פי במקום לשון שואו. לבדוק את השם לפני ✓
        lines.append("⚠ שמות לבדיקה: " + esc(", ".join(desc["name_warnings"])[:200]))
    try:                                      # מדיניות תוכן (58)
        from content import segment_flags, soften
        for why in segment_flags(seg, desc):
            lines.append("⚠ תוכן: " + esc(why))
        soft = soften(seg.get("title", ""))
        if soft != seg.get("title", ""):
            lines.append("✱ ביוטיוב: " + esc(soft))
    except ImportError:
        pass
    return "\n".join(lines) + note


def send_for_approval(job_dir: Path, min_score: int = 6, with_video: bool = True) -> int:
    """שולח את הקטעים לאישור. מחזיר כמה נשלחו."""
    job_dir = Path(job_dir)
    segs = load_json(job_dir / "audio_segments.json", [])
    meta = load_json(job_dir / "meta.json", {})
    desc = {d.get("idx"): d for d in load_json(job_dir / "clips" / "descriptions.json", [])}
    display = meta.get("display_name", meta.get("streamer", job_dir.name))

    chosen = []
    for idx, seg in enumerate(segs, 1):
        if seg.get("score", 0) < min_score:
            continue
        if seg.get("skip_upload"):
            continue
        if seg.get("approved") is not None:      # כבר הוחלט
            continue
        chosen.append((idx, seg))

    if not chosen:
        return 0

    notify(f"<b>{display} — {len(chosen)} קטעים ממתינים לאישור</b>\n"
           f"מגיעה תצוגה מקדימה של הפתיחה. ✓ מאשר · ✗ דוחה ואשאל למה · "
           f"✎ משנה כותרת · 📼 שולח את כל הקליפ.\n"
           f"<code>/pending</code> יראה תמיד מה עוד פתוח.")

    sent = 0
    for idx, seg in chosen:
        d = desc.get(idx, {})
        minutes = d.get("minutes")

        clip = clip_for(job_dir, idx) if clip_for else None
        text = segment_text(idx, seg, minutes,
                            "" if clip else "\n<i>(קובץ הווידאו עוד לא נוצר)</i>", d)

        # התמנייל לפני הווידאו, כדי לראות אותו לפני שהוא ביוטיוב
        thumb = clip.with_name(clip.stem + "__thumb.jpg") if clip else None
        if thumb and thumb.exists() and send_photo:
            send_photo(thumb, caption=f"תמנייל [{idx}]", silent=True)
        buttons = buttons_for(job_dir.name, idx, bool(clip))

        ok = False
        if clip and with_video and make_preview:
            try:
                pv, secs = make_preview(clip)
            except Exception as exc:
                pv, secs = None, 0
                print(f"[{idx}] הכנת תצוגה מקדימה נכשלה: {exc}", file=sys.stderr)
            if pv:
                cap = text + f"\n\n<i>{secs} השניות הראשונות מתוך {hhmm(minutes or 0)}</i>"
                ok = send_video(pv, caption=cap, buttons=buttons, silent=True)

        if not ok:                                # אין וידאו, או שהשליחה נכשלה
            ok = notify(text, buttons=buttons, silent=True)

        if ok:
            sent += 1
    return sent


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("job", help="תיקיית העבודה")
    ap.add_argument("--min-score", type=int, default=6)
    ap.add_argument("--no-video", action="store_true", help="טקסט בלבד, בלי תצוגה מקדימה")
    args = ap.parse_args()

    job = Path(args.job)
    if not job.is_dir():
        job = ROOT / args.job
    if not job.is_dir():
        print(f"לא נמצאה תיקייה: {args.job}")
        sys.exit(1)

    n = send_for_approval(job, args.min_score, with_video=not args.no_video)
    print(f"נשלחו {n} קטעים לאישור." if n else "אין קטעים חדשים לאישור.")


if __name__ == "__main__":
    main()
