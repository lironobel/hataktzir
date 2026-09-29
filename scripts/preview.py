"""
preview.py - מכין גרסה קטנה של קליפ כדי שאפשר יהיה לראות אותה בטלגרם

טלגרם מגבילה בוט ל-50MB בשליחת קובץ. קליפ של 20 דקות אצלנו שוקל
46-294MB, כלומר כמעט אף קליפ לא נכנס כמו שהוא. לכן שתי גרסאות:

    תצוגה מקדימה  - 75 השניות הראשונות, 640 רוחב. בערך 4-8MB.
                    זה מספיק כדי לראות אם הקטע מתחיל נכון על דיבור
                    ואם הפתיחה תופסת. זה מה שנשלח אוטומטית.

    גרסה מלאה     - כל הקליפ ב-480p עם ביטרייט מחושב כך שייצא
                    מתחת ל-45MB. איכות בינונית, מספיקה לצפייה בטלפון.
                    זה מה שנשלח כשלוחצים על "📼 מלא".

הקידוד מנסה קודם h264_nvenc (הכרטיס עושה את זה בשניות), ואם אין
כרטיס או שהוא תפוס בתמלול - נופל ל-libx264.

בדיקה ידנית:
    python scripts\\preview.py "jobs\\ronengg_2026-09-10\\clips\\01 - וכו.mp4"
    python scripts\\preview.py "...\\01 - וכו.mp4" --full
    python scripts\\preview.py "...\\01 - וכו.mp4" --plan     רק מראה מה יעשה
"""

import re
import sys
import json
import shutil
import argparse
import subprocess
from pathlib import Path


def find_root(start: Path) -> Path:
    for c in [start, *start.parents]:
        if (c / "scripts").is_dir():
            return c
    return start


ROOT = find_root(Path(__file__).resolve().parent)
CACHE = ROOT / "previews"

TG_LIMIT_MB = 49.0        # מה שטלגרם מרשה
TARGET_MB = 44.0          # לאן אנחנו מכוונים, עם מרווח ביטחון
PREVIEW_SECONDS = 75
PREVIEW_WIDTH = 640
FULL_HEIGHT = 480
AUDIO_KBPS = 64


# ------------------------------------------------------------------ עזרים

def have(cmd: str) -> bool:
    return shutil.which(cmd) is not None


def duration(path: Path) -> float:
    """אורך הקובץ בשניות. 0 אם לא הצלחנו."""
    if not have("ffprobe"):
        return 0.0
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
            capture_output=True, text=True, timeout=60,
            encoding="utf-8", errors="replace")
        return float((r.stdout or "0").strip() or 0)
    except Exception:
        return 0.0


def size_mb(path: Path) -> float:
    try:
        return path.stat().st_size / 1e6
    except Exception:
        return 0.0


_nvenc_broken = False       # נדלק אחרי כישלון אמיתי, ואז לא מנסים שוב


def has_nvenc() -> bool:
    """
    האם אפשר לקדד עם הכרטיס.

    שים לב: ffmpeg מפרסם את h264_nvenc גם במכונה בלי כרטיס, ולכן
    הרשימה לבדה לא מספיקה. גם כשיש כרטיס, הוא עשוי להיות תפוס
    בתמלול ואז הקידוד ייכשל באמצע. לכן כל קידוד שנכשל עם nvenc
    מנוסה שוב עם libx264, והדגל הזה מונע ניסיונות מיותרים.
    """
    if _nvenc_broken:
        return False
    if not hasattr(has_nvenc, "_cached"):
        ok = False
        try:
            r = subprocess.run(["ffmpeg", "-hide_banner", "-encoders"],
                               capture_output=True, text=True, timeout=30,
                               encoding="utf-8", errors="replace")
            ok = "h264_nvenc" in (r.stdout or "")
        except Exception:
            pass
        has_nvenc._cached = ok
    return has_nvenc._cached


def run_ffmpeg(args: list) -> tuple:
    """מחזיר (הצליח?, שגיאה). השגיאה משמשת להבחין בין תקלת כרטיס לקלט פגום."""
    cmd = ["ffmpeg", "-hide_banner", "-nostdin", "-y"] + [str(a) for a in args]
    flags = subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0
    try:
        # encoding מפורש: ffmpeg כותב UTF-8, ובלי זה פייתון מפענח לפי
        # קוד הדף של ווינדוס (cp1255 בעברית) והחוט הקורא קורס. התוצאה
        # הייתה שהודעת השגיאה של ffmpeg נבלעה בדיוק כשהיא הכי נחוצה.
        r = subprocess.run(cmd, capture_output=True, text=True,
                           timeout=60 * 30, creationflags=flags,
                           encoding="utf-8", errors="replace")
        err = (r.stderr or "").strip()
        if r.returncode != 0:
            tail = " | ".join(err.splitlines()[-2:]) or "(אין פלט שגיאה מ-ffmpeg)"
            print(f"ffmpeg נכשל (קוד {r.returncode}): {tail}", file=sys.stderr)
        return r.returncode == 0, err
    except Exception as exc:
        print(f"ffmpeg נכשל: {exc}", file=sys.stderr)
        return False, str(exc)


GPU_ERRORS = ("nvenc", "cuda", "no capable devices", "openencodesessionex",
              "out of memory", "driver does not support")


def encode(args_before: list, vargs_fn, args_after: list, out: Path) -> bool:
    """
    מריץ קידוד, ואם הכרטיס הוא זה שנפל - חוזר על עצמו במעבד.
    זה מה שמציל אותנו כשהתמלול תופס את ה-GPU.

    חשוב: קלט פגום נכשל גם הוא, וזו לא אשמת הכרטיס. אם נסמן אותו
    כשבור בגלל קליפ אחד פגום, כל שאר הקידודים בתהליך יעברו למעבד
    ויהיו איטיים בלי סיבה. לכן בודקים מה ffmpeg בעצם אמר.
    """
    global _nvenc_broken
    used_gpu = has_nvenc()
    ok, err = run_ffmpeg(args_before + vargs_fn(used_gpu) + args_after + [str(out)])
    if ok:
        return True
    if not used_gpu:
        return False
    if not any(k in err.lower() for k in GPU_ERRORS):
        return False                     # הקלט אשם, אין טעם לנסות שוב
    print("הקידוד עם הכרטיס נכשל, מנסה במעבד.", file=sys.stderr)
    _nvenc_broken = True
    out.unlink(missing_ok=True)
    ok, _ = run_ffmpeg(args_before + vargs_fn(False) + args_after + [str(out)])
    return ok


def out_path(clip: Path, kind: str) -> Path:
    """שם יציב בתיקיית previews, כדי לא לפזר קבצים ליד הקליפים."""
    CACHE.mkdir(exist_ok=True)
    stem = re.sub(r"[^A-Za-z0-9א-ת_-]+", "_", clip.stem)[:50]
    job = clip.parent.parent.name if clip.parent.name == "clips" else ""
    return CACHE / f"{job}__{stem}__{kind}.mp4"


# ------------------------------------------------------------- הקידודים

def _video_args(gpu: bool, bitrate_k: int = 0, crf: int = 28) -> list:
    """
    ביטרייט קבוע כשצריך לפגוע ביעד גודל, אחרת איכות קבועה.
    nvenc לא מכבד crf, אז שם משתמשים ב-cq.
    """
    if gpu:
        v = ["-c:v", "h264_nvenc", "-preset", "p4"]
        if bitrate_k:
            v += ["-b:v", f"{bitrate_k}k", "-maxrate", f"{int(bitrate_k * 1.3)}k",
                  "-bufsize", f"{bitrate_k * 2}k"]
        else:
            v += ["-rc", "vbr", "-cq", str(crf + 4)]
        return v
    v = ["-c:v", "libx264", "-preset", "veryfast"]
    if bitrate_k:
        v += ["-b:v", f"{bitrate_k}k", "-maxrate", f"{int(bitrate_k * 1.3)}k",
              "-bufsize", f"{bitrate_k * 2}k"]
    else:
        v += ["-crf", str(crf)]
    return v


def make_preview(clip, seconds: int = PREVIEW_SECONDS, force: bool = False):
    """
    קטע הפתיחה, קטן ומהיר. מחזיר (Path, שניות) או (None, 0).
    זה מה שנשלח אוטומטית עם הודעת האישור.
    """
    clip = Path(clip)
    if not clip.exists():
        return None, 0
    out = out_path(clip, f"pv{seconds}")
    if out.exists() and not force and size_mb(out) > 0.05:
        return out, seconds

    total = duration(clip)
    take = min(seconds, total) if total else seconds

    ok = encode(
        ["-t", f"{take:.2f}", "-i", str(clip), "-vf", f"scale={PREVIEW_WIDTH}:-2"],
        lambda gpu: _video_args(gpu, crf=28),
        ["-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", f"{AUDIO_KBPS}k", "-ac", "1",
         "-movflags", "+faststart"],
        out,
    )
    if not ok or not out.exists():
        return None, 0
    return out, int(take)


def make_compact(clip, target_mb: float = TARGET_MB, force: bool = False):
    """
    כל הקליפ, מכווץ כך שייכנס למגבלה. מחזיר (Path, MB) או (None, 0).

    החישוב: תקציב הביטים הכולל הוא target_mb, מורידים ממנו את האודיו,
    ומה שנשאר מתחלק באורך. בקליפ ארוך במיוחד זה יוצא נמוך מאוד -
    ואז עדיף להגיד את האמת מאשר לשלוח משהו לא צפייה.
    """
    clip = Path(clip)
    if not clip.exists():
        return None, 0

    # כלל אחד: אם הקובץ כבר מתחת ליעד - שולחים אותו כמו שהוא, בלי לפגוע
    # באיכות. אחרת מקודדים. (לא משווים למגבלת 49 אלא ליעד, כי קובץ
    # שיושב בדיוק על הגבול נוטה להיתקע בהעלאה.)
    if size_mb(clip) <= min(target_mb, TG_LIMIT_MB):
        return clip, size_mb(clip)

    out = out_path(clip, f"full{int(target_mb)}")
    if out.exists() and not force and 0.05 < size_mb(out) <= TG_LIMIT_MB:
        return out, size_mb(out)

    total = duration(clip)
    if total < 1:
        return None, 0

    total_kbits = target_mb * 8000
    video_k = int(total_kbits / total) - AUDIO_KBPS
    if video_k < 150:
        return None, 0                 # ארוך מדי, אין מה לשלוח בטלגרם

    ok = encode(
        ["-i", str(clip), "-vf", f"scale=-2:{FULL_HEIGHT}"],
        lambda gpu: _video_args(gpu, bitrate_k=video_k),
        ["-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", f"{AUDIO_KBPS}k", "-ac", "1",
         "-movflags", "+faststart"],
        out,
    )
    if not ok or not out.exists():
        return None, 0
    mb = size_mb(out)
    if mb > TG_LIMIT_MB:
        return None, mb                # יצא גדול בכל זאת, שהקורא יחליט
    return out, mb


def plan(clip) -> str:
    """מה יקרה, בלי לקדד. שימושי לבדוק לפני שמריצים על הכל."""
    clip = Path(clip)
    if not clip.exists():
        return f"לא נמצא: {clip}"
    total = duration(clip)
    mb = size_mb(clip)
    lines = [f"{clip.name}",
             f"  אורך {total/60:.1f} דק', {mb:.0f}MB",
             f"  מקודד: {'h264_nvenc' if has_nvenc() else 'libx264'}"]
    take = min(PREVIEW_SECONDS, total)
    lines.append(f"  תצוגה מקדימה: {take:.0f} שנ' ראשונות ב-{PREVIEW_WIDTH}px")
    if mb <= TG_LIMIT_MB:
        lines.append("  מלא: נשלח כמו שהוא, נכנס למגבלה")
    elif total > 0:
        v = int(TARGET_MB * 8000 / total) - AUDIO_KBPS
        if v < 150:
            lines.append(f"  מלא: {v}kbps - ארוך מדי, לא נשלח")
        else:
            lines.append(f"  מלא: {FULL_HEIGHT}p ב-{v}kbps כדי לפגוע ב-{TARGET_MB:.0f}MB")
    return "\n".join(lines)


# ------------------------------------------------------- איתור קליפ לפי אינדקס

def clip_for(job_dir, idx: int):
    """הקובץ של קטע מספר idx בעבודה. None אם עוד לא נחתך."""
    clips = Path(job_dir) / "clips"
    if not clips.is_dir():
        return None
    hits = sorted(clips.glob(f"{idx:02d} - *.mp4"))
    if hits:
        return hits[0]
    hits = sorted(clips.glob(f"{idx:02d}*.mp4"))    # __p1 וכאלה
    return hits[0] if hits else None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("clip", help="נתיב לקליפ, או תיקיית עבודה עם --idx")
    ap.add_argument("--idx", type=int, default=0, help="מספר קטע בתוך תיקיית עבודה")
    ap.add_argument("--full", action="store_true", help="גרסה מלאה מכווצת")
    ap.add_argument("--plan", action="store_true", help="רק להראות מה יקרה")
    ap.add_argument("--force", action="store_true", help="לקדד מחדש גם אם קיים")
    args = ap.parse_args()

    p = Path(args.clip)
    if args.idx:
        found = clip_for(p if p.is_dir() else ROOT / args.clip, args.idx)
        if not found:
            print(f"לא נמצא קליפ [{args.idx}]")
            sys.exit(1)
        p = found

    if not p.exists():
        p = ROOT / args.clip
    if not p.exists():
        print(f"לא נמצא: {args.clip}")
        sys.exit(1)

    if args.plan:
        print(plan(p))
        return

    if not have("ffmpeg"):
        print("חסר ffmpeg")
        sys.exit(1)

    if args.full:
        out, mb = make_compact(p, force=args.force)
        if not out:
            print(f"לא הצלחתי להכניס למגבלה ({mb:.0f}MB).")
            sys.exit(1)
        print(f"נוצר: {out}  ({mb:.0f}MB)")
    else:
        out, secs = make_preview(p, force=args.force)
        if not out:
            print("הקידוד נכשל.")
            sys.exit(1)
        print(f"נוצר: {out}  ({size_mb(out):.1f}MB, {secs} שניות)")


if __name__ == "__main__":
    main()
