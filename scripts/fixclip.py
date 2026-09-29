"""
fixclip.py - לתקן קליפ שכבר נחתך, בלי לחתוך אותו מחדש (משימה 64)

שני תיקונים, בקידוד אחד:
    1. טיזר (cold open, משימה 37) לקליפים שנחתכו לפני 28.9. המודל בוחר
       את הרגע מהתמלול של הקליפ (~$0.03 לקטע, בזמן אמת), hook.py מיישר
       אותו למילים, והשניות נלקחות *מהקובץ עצמו* - בלי הורדה.
    2. הארכה (באג 60): --start / --end מורידים רק את החלק החסר מה-VOD
       ומדביקים אותו לפני/אחרי הקליפ.

איך יודעים איפה הקליפ יושב בשידור:
    ranges.json מדויק בערך לשנייה, ולקליפים של 5.9 (cut2) אין אותו בכלל -
    שם הפתיחה זזה עד 45 שניות מ-parts. לכן לא סומכים על אף אחד מהם:
    משווים את עוצמת הקול (מעטפת, 5ms) של 20 שניות מהקליפ מול audio.mp3
    ומוצאים את ההיסט המדויק. אותה השוואה מיישרת את התפר בין הקליפ לחלק
    שהורד, ואת הטיזר בתוך הקובץ.

מה נשמר:
    - הקובץ הישן עובר ל-clips/_before_fix/ (לא נמחק). החדש באותו שם.
    - ranges.json: הטווחים המדויקים, lead, cold_open.
    - audio_segments.json: cold_open/cold_open_at, ו-parts אחרי הארכה.
    - התיאור: אחרי הארכה describe.py רץ שוב לקטע (פרקים, מקור, טקסט).
      בלי הארכה ועם כמה חלקים - רק הפרקים זזים ב-lead.
    - התמנייל הקיים נשאר.

שימוש (מתיקיית הפרויקט):
    python scripts\\fixclip.py                              מה בתור ומה חסר לכל קליפ
    python scripts\\fixclip.py --dry                        + מיקום מדויק ומה ייעשה, בלי לשנות כלום
    python scripts\\fixclip.py jobs\\masterohad_2026-09-15 2 --end 02:59:34
    python scripts\\fixclip.py jobs\\@TheCohen1_2026-09-15 1 --start 01:31:46 --hold-if-gone
    python scripts\\fixclip.py --all                        טיזר לכל התור (מדלג על מה שכבר יש לו)

    --no-cold-open    רק הארכה
    --repick          לבחור cold_open מחדש גם אם יש
    --hold-if-gone    אם החלק החסר לא ירד (VOD נמחק) - להוציא מהתור, לא להעלות חתוך
"""

import os
import re
import sys
import json
import time
import shutil
import argparse
import subprocess
from pathlib import Path
from datetime import datetime, timezone

sys.path.insert(0, str(Path(__file__).resolve().parent))
import cut3                     # noqa: E402  הורדה, ליטוש גבולות, probe
import hook                     # noqa: E402  מציאת הטיזר ויישור מילים

try:
    import numpy as np
except ImportError:             # מגיע עם faster-whisper, אבל שיהיה ברור
    np = None


def find_root(start: Path) -> Path:
    for c in [start, *start.parents]:
        if (c / "jobs").is_dir() and (c / "scripts").is_dir():
            return c
    return start.parent


ROOT = find_root(Path(__file__).resolve().parent)
VIDEO_EXT = (".mp4", ".mkv", ".webm", ".ts", ".mov")

NEEDLE = 20.0          # שניות קול שמשווים. פחות = פחות ייחודי
SR = 8000
HOP = SR // 200        # מעטפת ב-5ms
MIN_SCORE = 0.55       # מתחת לזה ההתאמה לא אמינה - לא נוגעים
SEARCH_RANGES = 12.0   # כשיש ranges.json (±שנייה בפועל)
SEARCH_PARTS = 75.0    # בלי ranges.json: cut2 הזיז עד 45 + משפט עד 20
OVERLAP = NEEDLE + 5   # כמה מהקליפ מורידים שוב, כדי למצוא את התפר בקול


# ------------------------------------------------------------ עזרי זמן/קבצים

def hms(t) -> str:
    return cut3.hms(t)


def to_seconds(t) -> float:
    return hook.to_seconds(t)


def load_json(path: Path, default):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return default


def save_json(path: Path, data) -> None:
    """כתיבה דרך קובץ זמני - קריסה באמצע לא משאירה JSON שבור."""
    tmp = Path(str(path) + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(path)


def patch_segment(job: Path, idx: int, fields: dict, drop=()) -> None:
    """
    טוען את audio_segments.json מחדש רגע לפני הכתיבה ומשנה רק את הקטע
    הזה. הבוט כותב לאותו קובץ (אישורים, תור) - לא דורסים לו.
    """
    path = job / "audio_segments.json"
    segs = load_json(path, [])
    if not 1 <= idx <= len(segs):
        raise RuntimeError(f"אין קטע {idx} ב-{path}")
    segs[idx - 1].update(fields)
    for k in drop:
        segs[idx - 1].pop(k, None)
    save_json(path, segs)


def find_clip(job: Path, idx: int):
    hits = [p for p in sorted((job / "clips").glob(f"{idx:02d} - *.mp4"))
            if "__p" not in p.stem]
    return hits[0] if hits else None


def seg_parts(seg: dict) -> list:
    return cut3.segment_parts(seg)


def log(msg: str = "") -> None:
    print(msg, flush=True)


# ----------------------------------------------------------------- התור

def queue_items() -> list:
    """כמו uploadq.pending, בלי לייבא את ספריות יוטיוב: מאושר, לא עלה, לא מוחזק."""
    out = []
    for job in sorted((ROOT / "jobs").iterdir()):
        if not job.is_dir():
            continue
        for i, seg in enumerate(load_json(job / "audio_segments.json", []), 1):
            if seg.get("approved") is not True or seg.get("skip_upload"):
                continue
            if (seg.get("youtube") or {}).get("id"):
                continue
            out.append((job, i))
    return out


def ranges_entry(job: Path, idx: int) -> dict:
    for r in load_json(job / "clips" / "ranges.json", []):
        if isinstance(r, dict) and r.get("idx") == idx:
            return r
    return {}


# ------------------------------------------------------ השוואת קול (סנכרון)

def pcm(path: Path, start: float, dur: float):
    """אודיו מונו 8kHz כ-float32, מ-start לאורך dur. ריק אם נכשל."""
    start = max(0.0, start)
    r = subprocess.run(
        ["ffmpeg", "-hide_banner", "-nostdin", "-v", "error",
         "-ss", f"{start:.3f}", "-t", f"{dur:.3f}", "-i", str(path),
         "-vn", "-ac", "1", "-ar", str(SR), "-f", "f32le", "-"],
        capture_output=True)
    if r.returncode != 0 or not r.stdout:
        return np.zeros(0, dtype=np.float32)
    return np.frombuffer(r.stdout, dtype=np.float32)


def envelope(x):
    """לוג-עוצמה בחלונות של 5ms. עמיד לקידוד מחדש (mp3 מול aac)."""
    n = len(x) // HOP
    if n < 10:
        return np.zeros(0)
    e = np.sqrt((x[:n * HOP].reshape(n, HOP).astype(np.float64) ** 2).mean(1) + 1e-10)
    return np.log(e)


def xcorr(needle, hay):
    """
    מיקום (בפריימים, עם שבר) שבו needle הכי דומה ל-hay, וציון 0-1.
    קורלציה מנורמלת בכל חלון - שקט ורעש רקע לא מטים אותה.
    """
    m = len(needle)
    if m < 20 or len(hay) < m:
        return None, 0.0
    nd = needle - needle.mean()
    nn = np.sqrt((nd ** 2).sum())
    if nn < 1e-6:
        return None, 0.0                    # קטע שקט - אין על מה להשוות
    num = np.correlate(hay, nd, mode="valid")
    cs = np.concatenate([[0.0], np.cumsum(hay)])
    cs2 = np.concatenate([[0.0], np.cumsum(hay ** 2)])
    s = cs[m:] - cs[:-m]
    s2 = cs2[m:] - cs2[:-m]
    var = np.maximum(s2 - s * s / m, 1e-12)
    ncc = num / (nn * np.sqrt(var))
    k = int(np.argmax(ncc))
    best = float(ncc[k])
    frac = 0.0
    if 0 < k < len(ncc) - 1:                # פרבולה סביב השיא - דיוק מתחת ל-5ms
        a, b, c = ncc[k - 1], ncc[k], ncc[k + 1]
        den = a - 2 * b + c
        if abs(den) > 1e-12:
            frac = max(-0.5, min(0.5, 0.5 * (a - c) / den))
    return k + frac, best


def locate(needle_path: Path, needle_t: float, hay_path: Path, hay_guess: float,
           search: float, dur: float = NEEDLE):
    """
    איפה ב-hay_path נמצא הקול שמתחיל ב-needle_t ב-needle_path.
    מחפש ב-hay_guess ±search. מחזיר (זמן ב-hay, ציון) או (None, ציון).
    """
    needle = envelope(pcm(needle_path, needle_t, dur))
    h0 = max(0.0, hay_guess - search)
    hay = envelope(pcm(hay_path, h0, (hay_guess + search + dur) - h0))
    pos, score = xcorr(needle, hay)
    if pos is None:
        return None, score
    return h0 + pos * HOP / SR, score


# ------------------------------------------------------ איפה הקליפ בשידור

def clip_anchor(clip: Path, audio: Path, dur: float, guess_start: float,
                guess_end: float, search: float) -> dict:
    """
    הזמנים המדויקים בשידור של תחילת הקליפ וסופו. בודק גם את האמצע
    הצפוי, כדי לתפוס קליפ שמורכב מחלקים שלא ידענו עליהם.
    """
    head_at = min(1.0, max(0.0, dur - NEEDLE - 1))
    s, sc1 = locate(clip, head_at, audio, guess_start + head_at, search)
    tail_at = max(0.0, dur - NEEDLE - 1.0)
    e, sc2 = locate(clip, tail_at, audio, guess_end - (dur - tail_at), search)
    out = {"score_start": round(sc1, 2), "score_end": round(sc2, 2)}
    if s is not None and sc1 >= MIN_SCORE:
        out["start"] = s - head_at
    if e is not None and sc2 >= MIN_SCORE:
        out["end"] = e + (dur - tail_at)
    return out


def exact_ranges(entry_ranges: list, anchor: dict, dur: float):
    """
    הטווחים של הקליפ בזמני השידור, מתוקנים לפי הסנכרון. חלק אחד: מדויק
    לגמרי. כמה חלקים: הקצוות מהסנכרון, התפרים הפנימיים מ-ranges.json.
    מחזיר (טווחים, הערה) או (None, סיבה).
    """
    if "start" not in anchor or "end" not in anchor:
        return None, (f"לא נמצא בשידור (התאמה {anchor.get('score_start')}"
                      f"/{anchor.get('score_end')})")
    s, e = anchor["start"], anchor["end"]
    rs = [list(r) for r in entry_ranges]
    if len(rs) == 1:
        if abs((e - s) - dur) > 1.0:
            return None, (f"הקליפ ({dur:.0f}ש') לא רצוף בשידור ({e - s:.0f}ש') - "
                          "כנראה חלקים שלא רשומים")
        return [[s, e]], "רציף, מדויק"
    inner = sum(b - a for a, b in rs[1:-1])
    rs[0][0], rs[-1][1] = s, e
    total = sum(b - a for a, b in rs)
    if abs(total - dur) > 2.0:
        return None, f"סכום החלקים ({total:.0f}ש') לא תואם את הקובץ ({dur:.0f}ש')"
    return rs, f"{len(rs)} חלקים, קצוות מדויקים" + (f" (+{inner:.0f}ש' באמצע)" if inner else "")


# ------------------------------------------------ המודל בוחר את הפתיחה

def cold_open_rules() -> str:
    """
    הכללים יושבים פעם אחת, בפרומפט של analyze13. קוראים אותם משם כדי
    ששינוי שם יחול גם כאן. אם המבנה שם השתנה - נכשלים בקול, לא בשקט.
    """
    src = (Path(__file__).resolve().parent / "analyze13.py").read_text(encoding="utf-8")
    a = src.find("`cold_open` ו-`cold_open_at`:")
    b = src.find("כללי הכותרת", a)
    if a < 0 or b < 0:
        raise RuntimeError("לא נמצאו כללי cold_open בפרומפט של analyze13.py")
    return src[a:b].replace("{{", "{").replace("}}", "}").strip()


def pick_cold_open(job: Path, idx: int, seg: dict, rows: list, ranges: list,
                   model: str) -> dict:
    """קריאה אחת בזמן אמת. מחזיר {"cold_open", "cold_open_at"} או {} ומדפיס למה."""
    import analyze13 as an           # מביא את anthropic, המחירון ו-usage.jsonl

    lo, hi = ranges[0][0], ranges[-1][1]
    inside = [r for r in rows
              if any(a - 2 <= float(r["start"]) <= b + 2 for a, b in ranges)]
    if not inside:
        log("     ⚠ אין תמלול בטווחי הקליפ")
        return {}
    text = an.rows_to_text(inside)
    skip = ""
    if len(ranges) > 1:
        skip = " (עם דילוגים: " + ", ".join(f"{hms(a)}-{hms(b)}" for a, b in ranges) + ")"
    meta = load_json(job / "meta.json", {})
    host = meta.get("display_name") or meta.get("streamer") or ""
    prompt = (
        f"זה קליפ שכבר נחתך מהשידור של {host}.\n"
        f"כותרת: {seg.get('title', '')}\n"
        f"נושא: {seg.get('topic', '')}\n"
        f"הקליפ מתחיל ב-{hms(lo)} ונגמר ב-{hms(hi)}{skip}.\n\n"
        f"בחר לו פתיחה לפי הכללים האלה:\n\n{cold_open_rules()}\n\n"
        "החזר JSON בלבד, בלי שום טקסט מסביב:\n"
        '{"cold_open": "המשפטים כלשונם", "cold_open_at": "HH:MM:SS"}\n'
        'אם אין בקליפ רגע שעומד בכללים: {"cold_open": "", "cold_open_at": ""}\n\n'
        f"התמלול של הקליפ:\n{text}"
    )
    client = an.anthropic.Anthropic()
    model = an.pick_model(client, model)
    try:
        # 29.9: היה 1500 - 11 קריאות נקטעו בדיוק שם (חשיבה של סונט 5 נספרת בפנים),
        # ו"אין רגע מתאים" היה בחלק מהמקרים תשובה קטועה, לא החלטה.
        resp = an.create_message(client, model=model, max_tokens=8000,
                                 messages=[{"role": "user", "content": prompt}])
    except Exception as exc:
        stop = an.fatal_api_error(str(exc))
        log(f"     ⚠ המודל לא ענה: {stop or str(exc)[:120]}")
        return {}
    tin, tout, cw, cr = an.usage_of(resp)
    an.log_usage(ROOT, model, tin, tout, job.name, cw, cr)
    usd = an.estimate_cost(model, tin, tout, cw, cr)
    data = an.extract_json(an.response_text(resp))
    co = str(data.get("cold_open") or "").strip()
    at = str(data.get("cold_open_at") or "").strip()
    log(f"     המודל (${usd:.3f}): " + (f"\"{co[:70]}\" [{at}]" if co else "אין רגע מתאים"))
    return {"cold_open": co, "cold_open_at": at} if co else {}


# ------------------------------------------------------------- הורדה

def source_url(job: Path) -> str:
    meta = load_json(job / "meta.json", {})
    vod = meta.get("vod_url", "")
    if not vod:
        return ""
    low = vod.lower()
    platform = (meta.get("platform") or
                ("twitch" if "twitch.tv" in low else
                 "youtube" if ("youtube.com" in low or "youtu.be" in low) else "kick"))
    if platform != "kick":
        return vod                               # yt-dlp מוריד ישירות
    return cut3.get_m3u8(vod)                    # קיק: כתובת טרייה דרך הדפדפן


def download(url: str, a: float, b: float, target: Path):
    for old in target.parent.glob(glob_escape(target.name) + ".*"):
        old.unlink(missing_ok=True)
    if not cut3.download_part(url, a, b, target, precise=True):
        return None
    hits = [p for p in target.parent.glob(glob_escape(target.name) + ".*")
            if p.suffix.lower() in VIDEO_EXT]
    return hits[0] if hits else None


def glob_escape(text: str) -> str:
    return cut3.glob_escape(text)


# -------------------------------------------------------------- הרכבה

def assemble(inputs: list, target: Path, teaser_n: int, like: Path) -> bool:
    """
    inputs = [(path, from, to|None), ...] בסדר ההדבקה. teaser_n = כמה
    מהראשונים הם הטיזר (fade בסוף שלהם ובתחילת הבא, כמו cut3.concat).
    קידוד אחד לכל הקובץ - אותן הגדרות כמו cut3.
    """
    first = cut3.probe_video(like)
    w, h = first.get("width") or 1280, first.get("height") or 720
    fps = first.get("fps") or 30
    fade = hook.FADE
    lens = []
    cmd = ["ffmpeg", "-hide_banner", "-nostdin", "-v", "error", "-y"]
    for path, a, b in inputs:
        if a:
            cmd += ["-ss", f"{a:.3f}"]
        if b is not None:
            cmd += ["-t", f"{b - (a or 0):.3f}"]
            lens.append(b - (a or 0))
        else:
            lens.append(cut3.probe_duration(path) - (a or 0))
        cmd += ["-i", str(path)]
    t_len = sum(lens[:teaser_n])
    if teaser_n and t_len <= fade * 3:
        fade = 0.0
    chains = []
    n = len(inputs)
    for i in range(n):
        vfx = afx = ""
        if teaser_n and fade and i == teaser_n - 1:
            st = lens[i] - fade
            vfx = f",fade=t=out:st={st:.3f}:d={fade}"
            afx = f",afade=t=out:st={st:.3f}:d={fade}"
        elif teaser_n and fade and i == teaser_n:
            vfx = f",fade=t=in:st=0:d={fade}"
            afx = f",afade=t=in:st=0:d={fade * 0.6:.3f}"
        chains.append(
            f"[{i}:v:0]scale={w}:{h}:force_original_aspect_ratio=decrease,"
            f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps={fps},"
            f"setpts=PTS-STARTPTS{vfx}[v{i}];"
            f"[{i}:a:0]aresample=48000,aformat=channel_layouts=stereo,"
            f"asetpts=PTS-STARTPTS{afx}[a{i}];")
    joined = "".join(f"[v{i}][a{i}]" for i in range(n))
    graph = "".join(chains) + f"{joined}concat=n={n}:v=1:a=1[v][a]"
    cmd += ["-filter_complex", graph, "-map", "[v]", "-map", "[a]",
            "-c:v", "libx264", "-preset", "fast", "-crf", "20", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", str(target)]
    res = subprocess.run(cmd, capture_output=True, text=True,
                         encoding="utf-8", errors="replace")
    if res.returncode != 0:
        tail = " | ".join((res.stderr or "").strip().splitlines()[-3:])
        log(f"     ffmpeg: {tail or '(אין פלט שגיאה)'}")
        target.unlink(missing_ok=True)
        return False
    got = cut3.probe_duration(target)
    want = sum(lens)
    if abs(got - want) > 1.5:
        log(f"     ⚠ האורך יצא {got:.1f}ש' במקום {want:.1f}ש' - לא מחליף את הקובץ")
        target.unlink(missing_ok=True)
        return False
    return True


# ---------------------------------------------------------- תיאור/פרקים

CHAPTER = re.compile(r"^(\d+):(\d{2})(?::(\d{2}))? (חלק (\d+))\s*$")


def shift_chapters(text: str, lead: float) -> str:
    """פרק 1 נשאר ב-0:00 (הטיזר נבלע בו), כל השאר זזים ב-lead."""
    out = []
    for line in text.split("\n"):
        m = CHAPTER.match(line)
        if m and int(m.group(5)) > 1:
            if m.group(3) is not None:
                t = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + int(m.group(3))
            else:
                t = int(m.group(1)) * 60 + int(m.group(2))
            t = int(round(t + lead))
            stamp = (f"{t // 3600}:{(t % 3600) // 60:02d}:{t % 60:02d}" if t >= 3600
                     else f"{t // 60}:{t % 60:02d}")
            line = f"{stamp} {m.group(4)}"
        out.append(line)
    return "\n".join(out)


def fix_description(job: Path, idx: int, clip: Path, lead: float, extended: bool,
                    model: str) -> str:
    if extended:
        # התוכן השתנה - התיאור, המקור והפרקים נבנים מחדש (המודל, ~$0.02)
        rc = subprocess.run([sys.executable, str(Path(__file__).resolve().parent / "describe.py"),
                             "audio_segments.json", "--only", str(idx), "--model", model],
                            cwd=str(job)).returncode
        return "נכתב מחדש" if rc == 0 else f"⚠ describe.py החזיר {rc} - התיאור הישן נשאר"
    path = job / "clips" / "descriptions.json"
    rows = load_json(path, [])
    changed = False
    for r in rows:
        if r.get("idx") == idx and "⏱️ פרקים" in (r.get("description") or ""):
            new = shift_chapters(r["description"], lead)
            if new != r["description"]:
                r["description"] = new
                changed = True
                txt = clip.parent / (clip.stem + ".txt")
                if txt.exists():
                    txt.write_text(shift_chapters(txt.read_text(encoding="utf-8"), lead),
                                   encoding="utf-8")
    if changed:
        save_json(path, rows)
        return f"הפרקים זזו ב-{lead:.1f}ש'"
    return "בלי פרקים, לא השתנה"


# ------------------------------------------------------------ קטע אחד

class Item:
    def __init__(self, job: Path, idx: int):
        self.job, self.idx = job, idx
        segs = load_json(job / "audio_segments.json", [])
        self.seg = segs[idx - 1] if 1 <= idx <= len(segs) else {}
        self.clip = find_clip(job, idx)
        self.entry = ranges_entry(job, idx)
        self.audio = job / "audio.mp3"
        self.label = f"{job.name} #{idx}"
        self.dur = cut3.probe_duration(self.clip) if self.clip else 0.0
        self.ranges = None       # הטווחים המדויקים של הקובץ הקיים
        self.why = ""

    def summary(self) -> str:
        s = self.seg
        parts = seg_parts(s)
        a, b = to_seconds(s.get("start", parts[0][0])), to_seconds(s.get("end", parts[-1][1]))
        miss_s = (parts[0][0] - a) / 60
        miss_e = (b - parts[-1][1]) / 60
        bits = [f"{self.dur / 60:.1f} דק'" if self.clip else "אין קובץ!"]
        if self.entry.get("lead"):
            bits.append(f"יש טיזר ({self.entry['lead']:.0f}ש')")
        else:
            bits.append("cold_open ✓" if s.get("cold_open") else "בלי טיזר")
        if miss_s > 0.5:
            bits.append(f"חסרות {miss_s:.1f} דק' בהתחלה ({s.get('start')})")
        if miss_e > 0.5:
            bits.append(f"חסרות {miss_e:.1f} דק' בסוף ({s.get('end')})")
        if not self.entry:
            bits.append("אין ranges.json")
        return " · ".join(bits)

    def locate(self) -> bool:
        if not self.clip:
            self.why = "אין קובץ mp4"
            return False
        if not self.audio.exists():
            self.why = "אין audio.mp3 - אי אפשר לסנכרן"
            return False
        base = self.entry.get("ranges") or [list(p) for p in seg_parts(self.seg)]
        search = SEARCH_RANGES if self.entry.get("ranges") else SEARCH_PARTS
        anchor = clip_anchor(self.clip, self.audio, self.dur,
                             base[0][0], base[-1][1], search)
        self.ranges, note = exact_ranges(base, anchor, self.dur)
        if not self.ranges:
            self.why = note
            return False
        drift = self.ranges[0][0] - base[0][0]
        self.why = (f"{note}; {hms(self.ranges[0][0])}-{hms(self.ranges[-1][1])}"
                    f" (סטייה {drift:+.1f}ש' מ-{'ranges.json' if self.entry.get('ranges') else 'parts'},"
                    f" התאמה {anchor['score_start']}/{anchor['score_end']})")
        return True


def refine_new_start(item: Item, t: float, rows: list) -> float:
    sp, _ = cut3.speech_start(rows, t, cut3.SPEECH_MAX_SHIFT) if rows else (t, "")
    ns, _ = cut3.refine_start(item.audio, rows, sp, 25.0, 30)
    return ns


def refine_new_end(item: Item, t: float) -> float:
    ne, _ = cut3.refine_end(item.audio, t, 1.5, 8, 30)
    return ne


def teaser_inputs(ts: float, te: float, sources: list) -> list:
    """
    sources = [(path, stream_a, stream_b, file_a), ...] - מה כל קובץ מכסה.
    מחזיר [(path, from, to)] לטיזר. אם הוא חוצה תפר - משני הקבצים.
    """
    out = []
    for path, sa, sb, fa in sources:
        a, b = max(ts, sa), min(te, sb)
        if b - a > 0.05:
            out.append([path, fa + (a - sa), fa + (b - sa), a])
    return out


def process(item: Item, args, aligner, rows: list, plan_only: bool) -> dict:
    """
    שלב א (בלי הורדה ובלי קידוד): סנכרון, בחירת פתיחה, יישור מילים.
    מחזיר תוכנית. שלב ב (build) מוריד ומרכיב.
    """
    log(f"\n[{item.label}] {item.seg.get('title', '')[:70]}")
    if item.entry.get("lead"):
        log("     כבר יש טיזר - מדלג. (לבנות מחדש: להחזיר את הקובץ מ-_before_fix)")
        return {}
    if not item.locate():
        log(f"     ✗ {item.why}")
        return {}
    log(f"     בשידור: {item.why}")

    plan = {"extend_start": None, "extend_end": None}
    final = [list(r) for r in item.ranges]
    if args.start:
        t = to_seconds(args.start)
        if t >= final[0][0] - 1:
            log(f"     ⚠ --start {args.start} לא לפני תחילת הקליפ ({hms(final[0][0])}) - מתעלם")
        else:
            ns = refine_new_start(item, t, rows)
            plan["extend_start"] = ns
            final[0][0] = ns
            log(f"     הארכה בהתחלה: {hms(ns)} (+{(item.ranges[0][0] - ns) / 60:.1f} דק')")
    if args.end:
        t = to_seconds(args.end)
        if t <= final[-1][1] + 1:
            log(f"     ⚠ --end {args.end} לא אחרי סוף הקליפ ({hms(final[-1][1])}) - מתעלם")
        else:
            ne = refine_new_end(item, t)
            plan["extend_end"] = ne
            final[-1][1] = ne
            log(f"     הארכה בסוף: {hms(ne)} (+{(ne - item.ranges[-1][1]) / 60:.1f} דק')")
    plan["final"] = final

    plan["hook"] = {}
    if not args.no_cold_open:
        seg = dict(item.seg)
        if (args.repick or not seg.get("cold_open")) and not plan_only:
            got = pick_cold_open(item.job, item.idx, seg, rows, final, args.model)
            if got:
                seg.update(got)
                patch_segment(item.job, item.idx, got)
        elif plan_only and not seg.get("cold_open"):
            log("     (--dry: המודל לא נקרא. בהרצה אמיתית הוא יבחר cold_open; עכשיו לפי quote)")
        sil = lambda a, b: cut3.scan_silence(item.audio, a, b, noise_db=30)
        hk = hook.find(seg, rows, final, item.audio, aligner, sil)
        if hk.get("start") is None:
            log(f"     טיזר: אין - {hk.get('why')}")
        else:
            log(f"     טיזר: {hms(hk['start'])} ({hk['end'] - hk['start']:.1f}ש', "
                f"{hk['source']}, {hk['how']})  \"{str(hk.get('text', ''))[:60]}\"")
            plan["hook"] = hk
    if not plan["hook"] and not plan["extend_start"] and not plan["extend_end"]:
        log("     אין מה לשנות.")
        return {}
    return plan


def build(item: Item, plan: dict, args, url_cache: dict) -> bool:
    """שלב ב: הורדת החלקים החסרים, הרכבה, החלפת הקובץ ועדכון הרשומות."""
    log(f"\n[{item.label}] בונה...")
    tmp = item.job / "clips" / "_fix_tmp"
    tmp.mkdir(parents=True, exist_ok=True)
    clip_s, clip_e = item.ranges[0][0], item.ranges[-1][1]

    # מה כל קובץ מכסה בזמני השידור. לקליפ עצמו: לפי הטווחים המדויקים
    sources = []
    off = 0.0
    clip_sources = []
    for a, b in item.ranges:
        clip_sources.append((item.clip, a, b, off))
        off += b - a
    pre = post = None
    pre_in = post_in = 0.0

    if plan["extend_start"] is not None or plan["extend_end"] is not None:
        key = str(item.job)
        if key not in url_cache:
            log("     מביא כתובת להורדה...")
            url_cache[key] = source_url(item.job)
        url = url_cache[key]
        if not url:
            return gone(item, args, "אין כתובת להורדה (VOD נמחק או לא נגיש)")
        if plan["extend_start"] is not None:
            a = plan["extend_start"]
            log(f"     מוריד {hms(a)}-{hms(clip_s + OVERLAP)}")
            pre = download(url, a, clip_s + OVERLAP, tmp / f"{item.idx:02d}_pre")
            if not pre:
                return gone(item, args, "הורדת ההתחלה החסרה נכשלה")
            # איפה בחלק שהורד מתחיל הקליפ - לפי הקול, לא לפי השעון של yt-dlp
            guess = clip_s - a
            at, sc = locate(item.clip, 1.0, pre, guess + 1.0, 8.0)
            if at is None or sc < MIN_SCORE:
                log(f"     ✗ התפר בהתחלה לא נמצא בקול (התאמה {sc:.2f}) - לא מחבר")
                return False
            pre_in = at - 1.0
            sources.append((pre, clip_s - pre_in, clip_s, 0.0))
        sources += clip_sources
        if plan["extend_end"] is not None:
            b = plan["extend_end"]
            log(f"     מוריד {hms(clip_e - OVERLAP)}-{hms(b)}")
            post = download(url, clip_e - OVERLAP, b, tmp / f"{item.idx:02d}_post")
            if not post:
                return gone(item, args, "הורדת הסוף החסר נכשלה")
            tail_at = max(0.0, item.dur - NEEDLE - 1.0)
            at, sc = locate(item.clip, tail_at, post, OVERLAP - (item.dur - tail_at), 8.0)
            if at is None or sc < MIN_SCORE:
                log(f"     ✗ התפר בסוף לא נמצא בקול (התאמה {sc:.2f}) - לא מחבר")
                return False
            post_in = at + (item.dur - tail_at)        # איפה בקובץ נגמר הקליפ
            post_len = cut3.probe_duration(post)
            if post_len - post_in < 1.0:
                log("     ✗ החלק שהורד לא ממשיך אחרי סוף הקליפ")
                return False
            sources.append((post, clip_e, clip_e + (post_len - post_in), post_in))
    else:
        sources += clip_sources

    # הטיזר: מהקובץ שמכסה את הרגע, ומיושר שוב בקול בתוך הקובץ עצמו
    teaser = []
    hk = plan.get("hook") or {}
    if hk.get("start") is not None:
        teaser = teaser_inputs(hk["start"], hk["end"], sources)
        for t in teaser:
            path, fa, fb, sa = t
            if fb - fa >= 4.0:
                at, sc = locate(item.audio, sa, path, fa, 3.0, dur=min(fb - fa, 12.0))
                if at is not None and sc >= MIN_SCORE:
                    t[2] = at + (fb - fa)
                    t[1] = at
        teaser = [(p, max(0.0, a), b) for p, a, b, _ in teaser]

    inputs = list(teaser)
    if pre:
        inputs.append((pre, 0.0, pre_in))
    inputs.append((item.clip, 0.0, None))
    if post:
        inputs.append((post, post_in, None))

    new = tmp / f"{item.idx:02d}_new.mp4"
    log(f"     מקודד ({len(inputs)} חלקים, {'עם' if teaser else 'בלי'} טיזר)...")
    t0 = time.time()
    if not assemble(inputs, new, len(teaser), item.clip):
        log("     ✗ ההרכבה נכשלה - הקובץ הישן לא נגע")
        return False
    log(f"     קודד ב-{(time.time() - t0) / 60:.1f} דק'")

    # החלפה: הישן ל-_before_fix, החדש באותו שם (התיאור והתמנייל מצביעים אליו)
    backup = item.job / "clips" / "_before_fix"
    backup.mkdir(exist_ok=True)
    dest = backup / item.clip.name
    if dest.exists():
        dest = backup / f"{item.clip.stem}.{datetime.now():%Y%m%d-%H%M}.mp4"
    shutil.move(str(item.clip), str(dest))
    shutil.move(str(new), str(item.clip))

    lead = sum(b - a for _, a, b in teaser)
    # הקצוות לפי מה שבאמת ירד (נמדד בקול בתפר), לא לפי מה שביקשנו מ-yt-dlp
    final = [list(r) for r in plan["final"]]
    if pre:
        final[0][0] = clip_s - pre_in
    if post:
        final[-1][1] = clip_e + (cut3.probe_duration(post) - post_in)
    extended = plan["extend_start"] is not None or plan["extend_end"] is not None

    # ranges.json - נטען מחדש רגע לפני הכתיבה
    rpath = item.job / "clips" / "ranges.json"
    known = [r for r in load_json(rpath, []) if isinstance(r, dict)]
    by_idx = {r.get("idx"): r for r in known}
    entry = dict(by_idx.get(item.idx) or {"idx": item.idx})
    entry.update({"file": item.clip.name,
                  "ranges": [[round(a, 2), round(b, 2)] for a, b in final],
                  "fixed": f"fixclip {datetime.now():%Y-%m-%d %H:%M}"})
    if lead:
        entry.update({"lead": round(lead, 2),
                      "cold_open": [round(hk["start"], 2), round(hk["end"], 2)],
                      "cold_open_text": hk.get("text", "")})
    by_idx[item.idx] = entry
    save_json(rpath, [by_idx[k] for k in sorted(by_idx)])

    if extended:
        segs_parts = seg_parts(item.seg)
        parts = [{"start": hms(a), "end": hms(b)} for a, b in segs_parts]
        if plan["extend_start"] is not None:
            parts[0]["start"] = hms(final[0][0])
        if plan["extend_end"] is not None:
            parts[-1]["end"] = hms(final[-1][1])
        patch_segment(item.job, item.idx, {
            "parts": parts,
            "fixed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "fix_note": "fixclip: " + ", ".join(
                x for x in (f"התחלה {hms(final[0][0])}" if plan["extend_start"] is not None else "",
                            f"סוף {hms(final[-1][1])}" if plan["extend_end"] is not None else "")
                if x)})

    for p in (pre, post):
        if p:
            p.unlink(missing_ok=True)

    desc = fix_description(item.job, item.idx, item.clip, lead, extended, args.model)
    log(f"     ✓ {cut3.probe_duration(item.clip) / 60:.1f} דק'"
        + (f", טיזר {lead:.1f}ש'" if lead else "")
        + f". תיאור: {desc}. הישן: _before_fix\\{dest.name[:40]}")
    return True


def gone(item: Item, args, why: str) -> bool:
    log(f"     ✗ {why}")
    if args.hold_if_gone:
        patch_segment(item.job, item.idx, {
            "skip_upload": True,
            "hold_reason": f"fixclip {datetime.now():%d.%m}: {why} - לא מעלים קליפ חתוך"})
        log("     הוצא מהתור (skip_upload). להחזיר: למחוק skip_upload ו-hold_reason.")
    else:
        log("     הקליפ לא השתנה ונשאר בתור. --hold-if-gone מוציא אותו.")
    return False


# ---------------------------------------------------------------- ראשי

def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("job", nargs="?", default="")
    ap.add_argument("idx", nargs="?", type=int, default=0)
    ap.add_argument("--all", action="store_true", help="כל התור")
    ap.add_argument("--dry", action="store_true",
                    help="סנכרון ותוכנית בלבד: בלי מודל, הורדה או שינוי קבצים")
    ap.add_argument("--start", default="", help="HH:MM:SS - להאריך את ההתחלה עד כאן")
    ap.add_argument("--end", default="", help="HH:MM:SS - להאריך את הסוף עד כאן")
    ap.add_argument("--no-cold-open", action="store_true")
    ap.add_argument("--repick", action="store_true")
    ap.add_argument("--hold-if-gone", action="store_true")
    ap.add_argument("--model", default="claude-sonnet-5")
    ap.add_argument("--whisper", default="large-v3")
    args = ap.parse_args()

    if args.job and not args.idx:
        ap.error("צריך גם מספר קטע: fixclip.py jobs\\<job> <n>")
    if (args.start or args.end) and not args.job:
        ap.error("--start/--end רק לקטע אחד")

    if args.job:
        job = Path(args.job)
        if not job.is_absolute():
            job = (Path.cwd() / job) if (Path.cwd() / job).exists() else ROOT / job
        targets = [(job.resolve(), args.idx)]
    else:
        targets = queue_items()

    if not args.all and not args.job:
        log(f"בתור {len(targets)} קטעים:\n")
        for job, idx in targets:
            it = Item(job, idx)
            log(f"  {it.label:34} {it.summary()}")
            log(f"  {'':34} {it.seg.get('title', '')[:60]}")
        log("\n--dry: מיקום מדויק ותוכנית · --all: טיזר לכולם · <job> <n> --start/--end: הארכה")
        if not args.dry:
            return

    if np is None:
        log("חסר numpy (מגיע עם faster-whisper). pip install numpy")
        sys.exit(1)

    items = [Item(j, i) for j, i in targets]
    aligner = None if (args.dry or args.no_cold_open) else hook.Aligner(args.whisper, log=log)
    plans = []
    try:
        for it in items:
            rows = cut3.load_transcript(it.job)
            p = process(it, args, aligner, rows, plan_only=args.dry)
            if p:
                plans.append((it, p))
    finally:
        if aligner:
            aligner.close()          # משחרר את ה-GPU לפני ההורדות והקידוד

    if args.dry:
        log(f"\n--dry: {len(plans)} קליפים ישתנו. לא נגעתי בכלום.")
        return

    ok = 0
    url_cache = {}
    for it, p in plans:
        try:
            if build(it, p, args, url_cache):
                ok += 1
        except Exception as exc:
            log(f"     ✗ {type(exc).__name__}: {exc}")
    log(f"\nהסתיים: {ok}/{len(plans)} קליפים תוקנו.")
    tmp_dirs = {it.job / "clips" / "_fix_tmp" for it, _ in plans}
    for d in tmp_dirs:
        try:
            d.rmdir()               # רק אם ריק - חלקים של כשלון נשארים לבדיקה
        except OSError:
            pass


if __name__ == "__main__":
    main()
