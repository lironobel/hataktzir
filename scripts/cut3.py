"""
cut3.py - חותך את הקטעים, ודואג שכל קליפ ייפתח על דיבור ולא על שקט

מה חדש לעומת cut2:
    לפני שהוא מוריד חלק, הוא בודק את האודיו שכבר יושב בתיקיית העבודה
    (audio.mp3) סביב נקודת הפתיחה שהמודל ביקש, ומזיז אותה קדימה עד
    הרגע שבו באמת מתחיל קול. ככה לא נכנסים לסרטון לתוך שקט.

    בסוף הקטע הוא עושה את ההפך: מחפש נשימה טבעית כדי לא לחתוך
    באמצע מילה.

    ה-pad לאחור בוטל. הוא היה הגורם העיקרי לשקט בהתחלה.

שימוש:
    python ..\\..\\scripts\\cut3.py audio_segments.json --url <m3u8>
    python ..\\..\\scripts\\cut3.py audio_segments.json --min-score 6
    python ..\\..\\scripts\\cut3.py audio_segments.json --only 1,3,5
    python ..\\..\\scripts\\cut3.py audio_segments.json --plan     בלי להוריד, רק להראות מה יזוז
    python ..\\..\\scripts\\cut3.py audio_segments.json --join-leftovers   להדביק חלקים שנשארו

מה נוסף ב-15.9:
    - הפתיחה זזה קודם למשפט הראשון שיש בו דיבור ממשי לפי התמלול
      (משימה 12), ורק אז נבדק השקט. --no-speech מכבה.
    - הדבקת חלקים עם concat filter, והשגיאה של ffmpeg מודפסת.
    - חלקים שכבר ירדו לא יורדים שוב.
    - כותרת עם נקודה ("משליטה..") לא מאבדת יותר את הקליפ.
    - clips/ranges.json עם הטווחים המלוטשים, בשביל התמנייל.

מה נוסף ב-28.9 (משימה 37):
    - cold open: 6-15 שניות מהרגע החזק לפני תחילת הקטע (hook.py).
      יורד כחלק 0 (__p0), ומודבק עם מעבר קצר. --no-cold-open מכבה.
      ranges.json מקבל lead = אורך הטיזר, כדי שהתמנייל והפרקים יזוזו.
"""

import re
import sys
import json
import subprocess
import argparse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
try:
    import hook as cold_open
    HOOK_FADE = cold_open.FADE
except Exception as _exc:          # hook.py חסר - חותכים כרגיל, ומדווחים
    cold_open = None
    HOOK_IMPORT_ERROR = str(_exc)
    HOOK_FADE = 0.25

JUMP_FADE_GAP = 3.0   # פער בשידור בין parts (שנ') שממנו יש מעבר לשחור (67/9)

# ---------------------------------------------------------------- עזרי זמן

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


def safe_name(text: str, limit: int = 60) -> str:
    text = re.sub(r'[<>:"/\\|?*]', "", str(text))
    text = re.sub(r"\s+", " ", text).strip()
    return text[:limit].strip() or "clip"


def find_part(out_dir: Path, name: str, j: int):
    """קובץ חלק j שכבר ירד. מתעלם משאריות של yt-dlp באמצע הורדה."""
    for p in sorted(out_dir.glob(f"{glob_escape(name)}__p{j}.*")):
        if p.suffix.lower() in (".mp4", ".mkv", ".webm", ".ts", ".mov"):
            return p
    return None


def glob_escape(text: str) -> str:
    """כותרת עם [ או ] נקראת כ-glob. עוטפים אותם."""
    return re.sub(r"([\[\]*?])", r"[\1]", text)


# ------------------------------------------------------- זיהוי קול באודיו

SIL_START = re.compile(r"silence_start:\s*(-?[\d.]+)")
SIL_END = re.compile(r"silence_end:\s*(-?[\d.]+)")


def scan_silence(audio: Path, frm: float, to: float,
                 noise_db: int = 30, min_sil: float = 0.35) -> list:
    """
    מחזיר רשימת קטעי שקט [(start, end), ...] בזמנים מוחלטים של הקובץ.
    אם ffmpeg נכשל - מחזיר רשימה ריקה, והקורא ממשיך בלי השינוי.
    """
    frm = max(0.0, frm)
    if to <= frm:
        return []

    cmd = [
        "ffmpeg", "-hide_banner", "-nostats",
        "-ss", f"{frm:.2f}", "-to", f"{to:.2f}", "-copyts",
        "-i", str(audio),
        "-af", f"silencedetect=n=-{noise_db}dB:d={min_sil}",
        "-f", "null", "-",
    ]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True,
                             encoding="utf-8", errors="replace", timeout=180)
    except Exception:
        return []

    log = (res.stderr or "") + (res.stdout or "")
    starts = [float(m) for m in SIL_START.findall(log)]
    ends = [float(m) for m in SIL_END.findall(log)]
    if not starts and not ends:
        return []

    # הגנה: אם ffmpeg התעלם מ-copyts והזמנים יחסיים, מוסיפים את ההיסט
    sample = starts or ends
    if frm > 5 and max(sample) < frm - 1:
        starts = [s + frm for s in starts]
        ends = [e + frm for e in ends]

    spans = []
    for i, s in enumerate(starts):
        e = ends[i] if i < len(ends) else to
        if e > s:
            spans.append((s, e))
    # שקט שהתחיל לפני החלון ונגמר בתוכו
    if ends and (not starts or ends[0] < starts[0]):
        spans.insert(0, (frm, ends[0]))
    return spans


def in_silence(spans: list, t: float) -> tuple:
    for s, e in spans:
        if s - 0.05 <= t <= e + 0.05:
            return True, e
    return False, t


def sound_runs(spans: list, frm: float, to: float) -> list:
    """הופך רשימת שקטים לרשימת קטעי קול בתוך החלון."""
    runs = []
    cur = frm
    for s, e in sorted(spans):
        if s > cur:
            runs.append((cur, min(s, to)))
        cur = max(cur, e)
        if cur >= to:
            break
    if cur < to:
        runs.append((cur, to))
    return [(a, b) for a, b in runs if b - a > 0.05]


# ------------------------------------------------------------- תמלול לעזר

def load_transcript(job_dir: Path, explicit: str = "") -> list:
    names = [explicit] if explicit else ["audio_transcript.json", "transcript.json"]
    for n in names:
        if not n:
            continue
        p = Path(n) if Path(n).is_absolute() else job_dir / n
        if p.exists():
            try:
                rows = json.loads(p.read_text(encoding="utf-8"))
                if isinstance(rows, list) and rows and "start" in rows[0]:
                    return rows
            except Exception:
                pass
    return []


def row_at(rows: list, t: float) -> dict:
    for r in rows:
        if r["start"] - 0.5 <= t <= r["end"] + 0.5:
            return r
    return {}


def sentence_edge(rows: list, t: float, window: float = 6.0) -> float:
    """
    מחפש סוף משפט קרוב לזמן t, ומחזיר את הזמן המשוער של תחילת המשפט הבא.
    מבוסס על פריסה יחסית של הטקסט על פני משך השורה - קירוב, לא מדויק,
    ולכן נעשה בו שימוש רק אם הוא נופל בתוך חלון קטן.
    """
    r = row_at(rows, t)
    if not r:
        return t
    text = r.get("text") or ""
    dur = r["end"] - r["start"]
    if dur <= 0 or len(text) < 20:
        return t

    best = t
    best_gap = window
    for m in re.finditer(r"[.!?]\s+", text):
        pos = m.end()
        when = r["start"] + dur * (pos / len(text))
        gap = abs(when - t)
        if gap < best_gap:
            best_gap = gap
            best = when
    return best


# --------------------------------------------------- פתיחה על דיבור ממשי
#
# משימה 12. בדיקת השקט לבדה מבטיחה שיש *קול* בשנייה הראשונה, אבל
# קול יכול להיות "אה... רגע... יאללה", מוזיקה, או ההזיה הקבועה של
# whisper על שקט ("תודה רבה", "תודה שצפיתם"). צופה שנכנס לסרטון
# ושומע את זה - יוצא. לכן לפני השקט מזיזים את הפתיחה לתחילת השורה
# הראשונה בתמלול שיש בה משפט של ממש. שורות whisper מתחילות בדרך
# כלל בתחילת משפט, וזה בדיוק המקום שבו רוצים להיכנס.

FILLER_WORDS = {
    "אה", "אהה", "אמ", "אממ", "אמממ", "יאללה", "רגע", "אוקיי", "אוקי", "אוקיי.",
    "טוב", "כן", "לא", "וואו", "וואי", "מה", "אז", "נו", "שנייה", "אחי",
    "חח", "חחח", "חחחח", "הא", "או", "אוי", "יא", "סבבה", "רגע.",
}
HALLUCINATIONS = ("תודה רבה", "תודה שצפיתם", "כתוביות", "תודה על הצפייה",
                  "הירשמו לערוץ", "מוזיקה")
MIN_WORDS = 4
# כמה רחוק מותר לחפש משפט. קטן מ---max-shift בכוונה: בשיחה מהירה יש
# הרבה שורות קצרות ולגיטימיות ("מה?!", "באמת?"), ודילוג ארוך מדי
# היה מפיל את תחילת הוויכוח עצמו.
SPEECH_MAX_SHIFT = 20


def substantive(text: str) -> bool:
    """האם בשורה יש משפט שאפשר לפתוח בו סרטון."""
    clean = re.sub(r"[^\w\s']", " ", str(text or "")).strip()
    if not clean:
        return False
    if any(clean.startswith(h) and len(clean) <= len(h) + 6 for h in HALLUCINATIONS):
        return False
    words = clean.split()
    real = [w for w in words if w not in FILLER_WORDS]
    return len(words) >= MIN_WORDS and len(real) >= MIN_WORDS - 1


def speech_start(rows: list, t: float, max_shift: float, back: float = 4.0) -> tuple:
    """
    מחזיר (זמן, הסבר). הזמן הוא תחילת השורה הראשונה עם דיבור ממשי,
    בטווח [t-back, t+max_shift]. אם אין כזאת - t כמו שהוא.

    back קטן בכוונה: מותר לחזור כמה שניות כדי לא להיכנס באמצע
    משפט, אבל לא לגרור לתוך הקטע את הנושא הקודם.
    """
    if not rows:
        return t, ""
    for r in rows:
        start = float(r.get("start", 0))
        if start < t - back:
            continue
        if start > t + max_shift:
            break
        if substantive(r.get("text")):
            if abs(start - t) < 0.3:
                return t, ""
            direction = "אחורה" if start < t else "קדימה"
            return start, f"{direction} {abs(start - t):.1f}ש' למשפט"
    return t, "לא נמצא משפט ממשי בטווח"


# --------------------------------------------------------- ליטוש הגבולות

def refine_start(audio: Path, rows: list, t: float,
                 max_shift: float, noise_db: int) -> tuple:
    """
    מחזיר (זמן חדש, הסבר). התחלה תמיד נופלת על קול.
    """
    if not audio or not audio.exists():
        return t, "אין אודיו לבדיקה"

    frm = max(0.0, t - 8)
    to = t + max_shift + 4
    spans = scan_silence(audio, frm, to, noise_db=noise_db)
    if not spans:
        return t, "לא נמצאו קטעי שקט בסביבה"

    quiet, ends_at = in_silence(spans, t)

    if quiet:
        new = min(ends_at + 0.15, t + max_shift)
        new = sentence_edge(rows, new, window=3.0) if rows else new
        new = max(t, min(new, t + max_shift))
        return new, f"היה שקט, קדימה {new - t:.1f}ש'"

    # יש קול. נבדוק כמה זמן הוא כבר רץ, כדי לא להיכנס באמצע מילה.
    runs = sound_runs(spans, frm, to)
    onset = t
    for a, b in runs:
        if a <= t <= b:
            onset = a
            break

    # חלון קצר בכוונה. מטרתו לתפוס מילה שכבר התחילה, לא לגרור
    # אליי את הנושא הקודם.
    back = t - onset
    if 0.2 < back <= 2.5:
        return max(0.0, onset), f"אחורה {back:.1f}ש' לתחילת המילה"
    return t, "נופל על דיבור"


def refine_end(audio: Path, t: float, tail: float,
               look: float, noise_db: int) -> tuple:
    """מאריך עד נשימה טבעית, כדי לא לחתוך באמצע מילה או לסיים בדממה."""
    if not audio or not audio.exists():
        return t + tail, "בלי בדיקה"

    spans = scan_silence(audio, t - look - 2, t + look + 2, noise_db=noise_db)
    if not spans:
        return t + tail, "לא נמצא שקט קרוב"

    # אם הסוף נפל בתוך דממה - למשוך אחורה לרגע שבו הדממה התחילה
    quiet, _ = in_silence(spans, t)
    if quiet:
        for s, e in sorted(spans):
            if s - 0.05 <= t <= e + 0.05:
                if t - s <= look:
                    return s + 0.35, f"נגמר בדממה, אחורה {t - s:.1f}ש'"
                break

    for s, e in sorted(spans):
        if s >= t - 0.5:
            if s - t <= look:
                return s + 0.35, f"עד ההפסקה ({s - t:+.1f}ש')"
            break
    return t + tail, "אין הפסקה בטווח"


# ----------------------------------------------------------------- הורדה

def get_m3u8(vod_url: str) -> str:
    # kickurl2 ולא kickurl: הישן לא יודע לנסות שוב ולא מבחין בין
    # VOD בעיבוד ל-VOD שנמחק. run10 תמיד מעביר --url ולכן זה לא התפוצץ,
    # אבל הרצה ידנית בלי כתובת נפלה עד 15.9 על הקוד הישן.
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    try:
        from kickurl2 import grab_m3u8_ex
    except ImportError:
        print("לא נמצא kickurl2.py לצד cut3.py")
        return ""
    print("מביא כתובת m3u8 טרייה...")
    url, reason = grab_m3u8_ex(vod_url)
    if not url:
        print(f"לא התקבלה כתובת: {reason}")
    return url or ""


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


def download_part(url: str, start: float, end: float, target: Path,
                  precise: bool = False) -> bool:
    # ⚠ לא target.with_suffix: כותרת כמו "הדרמה יצאה משליטה.." או
    #   "אני נקניקיה." מכילה נקודה, ו-with_suffix מחליף את כל מה שאחריה -
    #   כולל ה-__p1. הקובץ ירד בשם אחר, ה-glob לא מצא אותו, והקליפ
    #   אבד בשקט. כללי הכותרות שלנו ממליצים על שלוש נקודות, אז זה
    #   היה קורה הרבה. (נמצא 15.9)
    cmd = [
        "yt-dlp",
        # hms מעגל לשניות שלמות - מספיק לקטע של 20 דקות, לא לטיזר של
        # 8 שניות שנחתך בין מילים. precise = שניות עם שבר.
        "--download-sections", (f"*{start:.2f}-{end:.2f}" if precise
                                else f"*{hms(start)}-{hms(end)}"),
        "--force-keyframes-at-cuts",
        "-o", str(target) + ".%(ext)s",
        url,
    ]
    return subprocess.run(cmd).returncode == 0


def concat(parts: list, target: Path, fade_first: bool = False,
           fade_after: set = None) -> bool:
    """
    מדביק חלקים לקובץ אחד, בקידוד מחדש כדי שהתפרים יהיו חלקים.

    עד 15.9 זה נעשה עם ה-concat demuxer וקובץ רשימה, ונכשל בשקט
    (קטע 3 של רונן נשאר בשני חלקים). שתי בעיות בשיטה ההיא: שם קובץ
    עם גרש - "ג'יג'י", "צ'אט" - שובר את קובץ הרשימה, והחלקים מ-yt-dlp
    מגיעים עם חותמות זמן שלא מתחילות באפס. ה-concat filter מקבל כל
    חלק כקלט נפרד, בלי רשימה ובלי ציטוט, ומאפס את הזמנים בעצמו.
    והשגיאה של ffmpeg מודפסת עכשיו, במקום להיבלע.

    fade_first: החלק הראשון הוא טיזר (cold open). יציאה קצרה לשחור
    בסופו וכניסה בתחילת הבא, כדי שהצופה יבין שהייתה קפיצה בזמן.

    fade_after (67/9, 29.9, החלטת לירון): אינדקסים של חלקים שאחריהם יש קפיצה
    בזמן בשידור (פער בין parts). אותו מעבר לשחור כמו אחרי הטיזר. עד היום
    ההדבקה של parts הייתה חיתוך חד, והצופה לא הבין שדילגנו.
    ה-fade לא משנה אורכים - רק מכהה את הקצוות - כך ש-lead, פרקים ותמנייל לא זזים.
    """
    parts = [Path(p) for p in parts]
    cmd = ["ffmpeg", "-hide_banner", "-nostdin", "-v", "error", "-y"]
    for p in parts:
        cmd += ["-i", str(p)]
    n = len(parts)
    # כל חלק מנורמל לאותה רזולוציה, קצב ודגימה. חלקים מאותו VOD
    # בדרך כלל זהים, אבל concat filter נכשל על ההבדל הקטן ביותר.
    first = probe_video(parts[0])
    w, h = first.get("width") or 1280, first.get("height") or 720
    fps = first.get("fps") or 30
    bounds = set(fade_after or ())
    if fade_first and n > 1:
        bounds.add(0)
    bounds = {b for b in bounds if 0 <= b < n - 1}
    durs = {}
    for b in sorted(bounds):
        # חלק קצר מדי לא מקבל fade (אותו כלל כמו לטיזר) - המעבר נשאר חד
        for k in (b, b + 1):
            if k not in durs:
                durs[k] = probe_duration(parts[k])
        if durs[b] <= HOOK_FADE * 3 or durs[b + 1] <= HOOK_FADE * 3:
            bounds.discard(b)
    fade = HOOK_FADE
    chains = []
    for i in range(n):
        vfx, afx = "", ""
        if i - 1 in bounds:             # כניסה מהשחור
            vfx += f",fade=t=in:st=0:d={fade}"
            afx += f",afade=t=in:st=0:d={fade * 0.6:.3f}"
        if i in bounds:                 # יציאה לשחור
            d = durs[i]
            vfx += f",fade=t=out:st={d - fade:.3f}:d={fade}"
            afx += f",afade=t=out:st={d - fade:.3f}:d={fade}"
        chains.append(
            f"[{i}:v:0]scale={w}:{h}:force_original_aspect_ratio=decrease,"
            f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps={fps},"
            f"setpts=PTS-STARTPTS{vfx}[v{i}];"
            f"[{i}:a:0]aresample=48000,aformat=channel_layouts=stereo,"
            f"asetpts=PTS-STARTPTS{afx}[a{i}];")
    joined = "".join(f"[v{i}][a{i}]" for i in range(n))
    graph = "".join(chains) + f"{joined}concat=n={n}:v=1:a=1[v][a]"
    cmd += ["-filter_complex", graph, "-map", "[v]", "-map", "[a]",
            "-c:v", "libx264", "-preset", "fast", "-crf", "20",
            "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart",
            str(target)]
    res = subprocess.run(cmd, capture_output=True, text=True,
                         encoding="utf-8", errors="replace")
    if res.returncode != 0:
        tail = " | ".join((res.stderr or "").strip().splitlines()[-3:])
        print(f"     ffmpeg: {tail or '(אין פלט שגיאה)'}")
        target.unlink(missing_ok=True)
        return False
    return target.exists() and target.stat().st_size > 0


def probe_video(path: Path) -> dict:
    """רוחב, גובה וקצב פריימים של הווידאו. ריק אם ffprobe לא ענה."""
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=width,height,r_frame_rate",
             "-of", "json", str(path)],
            capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=60)
        st = (json.loads(r.stdout or "{}").get("streams") or [{}])[0]
        num, _, den = str(st.get("r_frame_rate", "30/1")).partition("/")
        fps = float(num) / float(den or 1) if float(den or 1) else 30.0
        return {"width": int(st.get("width") or 0),
                "height": int(st.get("height") or 0),
                "fps": round(fps, 3) if 5 <= fps <= 120 else 30}
    except Exception:
        return {}


def probe_duration(path: Path) -> float:
    """אורך הקובץ בשניות. 0 אם ffprobe לא ענה."""
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=nw=1:nk=1", str(path)],
            capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=60)
        return float((r.stdout or "0").strip() or 0)
    except Exception:
        return 0.0


def join_leftovers(out_dir: Path) -> int:
    """
    מדביק חלקים שנשארו מהרצה שההדבקה שלה נכשלה. לא מוריד כלום.
    מחזיר כמה קליפים הורכבו.
    """
    groups = {}
    for p in sorted(out_dir.glob("*__p*.*")):
        m = re.match(r"^(.*)__p(\d+)$", p.stem)
        if not m or p.suffix.lower() not in (".mp4", ".mkv", ".webm", ".ts", ".mov"):
            continue
        groups.setdefault(m.group(1), []).append((int(m.group(2)), p))
    done = 0
    for name, items in groups.items():
        final = out_dir / f"{name}.mp4"
        if final.exists():
            print(f"[{name[:50]}] כבר קיים קובץ שלם, לא נוגע.")
            continue
        items.sort()
        nums = [n for n, _ in items]
        first = 0 if nums and nums[0] == 0 else 1     # 0 = טיזר (cold open)
        if nums != list(range(first, first + len(nums))):
            print(f"[{name[:50]}] חסר חלק (יש {nums}), מדלג.")
            continue
        if first == 0 and len(items) == 1:
            # רק הטיזר ירד והקטע עצמו לא - זה לא קליפ
            print(f"[{name[:50]}] יש רק טיזר בלי הקטע, מדלג.")
            continue
        if len(items) == 1:
            items[0][1].rename(final)
            done += 1
            continue
        print(f"[{name[:50]}] מדביק {len(items)} חלקים...")
        if concat([p for _, p in items], final, fade_first=(first == 0)):
            for _, p in items:
                p.unlink(missing_ok=True)
            done += 1
            print("     מוכן.")
        else:
            print("     ההדבקה נכשלה, החלקים נשארו.")
    return done


# ------------------------------------------------------------------ ראשי

def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("segments")
    ap.add_argument("--url", default="")
    ap.add_argument("--vod", default="")
    ap.add_argument("--out", default="clips")
    ap.add_argument("--audio", default="audio.mp3", help="האודיו של השידור, לבדיקת שקט")
    ap.add_argument("--transcript", default="", help="ברירת מחדל: audio_transcript.json")
    ap.add_argument("--min-score", type=int, default=6)
    ap.add_argument("--only", default="")
    ap.add_argument("--tail", type=float, default=1.5, help="שניות אחרי הסוף")
    ap.add_argument("--max-shift", type=float, default=45,
                    help="כמה שניות מותר להזיז את ההתחלה קדימה")
    ap.add_argument("--end-look", type=float, default=8,
                    help="כמה שניות לחפש נשימה בסוף")
    ap.add_argument("--noise-db", type=int, default=30,
                    help="סף שקט. גבוה יותר = מחמיר יותר")
    ap.add_argument("--no-snap", action="store_true", help="בלי ליטוש גבולות")
    ap.add_argument("--no-speech", action="store_true",
                    help="בלי הזזת הפתיחה למשפט ממשי לפי התמלול (משימה 12)")
    ap.add_argument("--no-cold-open", action="store_true",
                    help="בלי טיזר מהרגע החזק בתחילת הקליפ (משימה 37)")
    ap.add_argument("--cold-open-model", default="large-v3",
                    help="מודל whisper ליישור המילים של הטיזר")
    ap.add_argument("--plan", action="store_true", help="רק להראות מה יזוז, בלי להוריד")
    ap.add_argument("--join-leftovers", action="store_true",
                    help="רק להדביק חלקים שנשארו מהרצה קודמת, בלי להוריד כלום")
    args = ap.parse_args()

    seg_path = Path(args.segments)
    if not seg_path.exists():
        print(f"לא נמצא: {seg_path}")
        sys.exit(1)

    job_dir = seg_path.resolve().parent

    if args.join_leftovers:
        out_dir = Path(args.out)
        if not out_dir.is_absolute():
            out_dir = job_dir / args.out
        n = join_leftovers(out_dir)
        print(f"\nהורכבו {n} קליפים.")
        return

    segments = json.loads(seg_path.read_text(encoding="utf-8"))
    if not segments:
        print("אין קטעים בקובץ.")
        sys.exit(0)

    if args.only:
        wanted = {int(x) for x in args.only.split(",") if x.strip().isdigit()}
        chosen = [(i, s) for i, s in enumerate(segments, 1) if i in wanted]
    else:
        chosen = [(i, s) for i, s in enumerate(segments, 1)
                  if s.get("score", 0) >= args.min_score]

    if not chosen:
        print(f"אף קטע לא עבר את הסינון (ציון >= {args.min_score}).")
        sys.exit(0)

    audio = Path(args.audio)
    if not audio.is_absolute():
        audio = job_dir / args.audio
    rows = load_transcript(job_dir, args.transcript)

    if args.no_snap:
        print("ליטוש גבולות מכובה.")
    elif not audio.exists():
        print(f"לא נמצא {audio.name} - אי אפשר לבדוק שקט. חותך כמו שהוא.")
    else:
        print(f"בודק שקט מול {audio.name}"
              + (f", ועוזר בתמלול ({len(rows)} שורות)" if rows else ""))

    print(f"נבחרו {len(chosen)} מתוך {len(segments)} קטעים.\n")

    # ---------- שלב א: ליטוש כל הגבולות ----------
    plan = []
    for idx, seg in chosen:
        ranges = []
        for start, end in segment_parts(seg):
            if args.no_snap:
                ranges.append((start, end + args.tail, "", ""))
                continue
            if not audio.exists():
                # בלי אודיו אין בדיקת שקט, אבל התמלול עדיין יודע איפה
                # מתחיל משפט.
                ns, why_s = (start, "")
                if rows and not args.no_speech:
                    ns, why_s = speech_start(rows, start, SPEECH_MAX_SHIFT)
                ranges.append((ns, max(end, ns + 5) + args.tail, why_s, ""))
                continue
            speech_t, why_t = (start, "")
            if rows and not args.no_speech:
                speech_t, why_t = speech_start(rows, start, SPEECH_MAX_SHIFT)
            # השקט בודק רק את מה שנשאר מתקציב ההזזה, כדי ששני השלבים
            # יחד לא ידחפו את הפתיחה רחוק ממה שהמנתח ביקש.
            budget = max(3.0, args.max_shift - max(0.0, speech_t - start))
            ns, why_s = refine_start(audio, rows, speech_t, budget, args.noise_db)
            if why_t:
                why_s = f"{why_t}; {why_s}"
            ne, why_e = refine_end(audio, end, args.tail, args.end_look, args.noise_db)
            if ne <= ns + 5:
                ne = ns + max(5.0, end - start)
            ranges.append((ns, ne, why_s, why_e))
        plan.append((idx, seg, ranges))

    # ---------- שלב א2: cold open (משימה 37) ----------
    # אחרי הליטוש, כי הטיזר צריך ליפול בתוך מה שבאמת נחתך. ב---plan
    # בלי יישור מילים (טעינת מודל לוקחת זמן) - הזמנים שם משוערים.
    hooks = {}
    if args.no_cold_open:
        print("cold open מכובה.")
    elif cold_open is None:
        print(f"⚠ cold open לא זמין: hook.py לא נטען ({HOOK_IMPORT_ERROR[:80]})")
    elif not rows:
        print("⚠ cold open: אין תמלול, אי אפשר למצוא את הרגע.")
    else:
        aligner = None if args.plan else cold_open.Aligner(args.cold_open_model)
        sil = None
        if audio.exists() and not args.no_snap:
            sil = lambda a, b: scan_silence(audio, a, b, noise_db=args.noise_db)
        try:
            for idx, seg, ranges in plan:
                spans = [(a, b) for a, b, *_ in ranges]
                hooks[idx] = cold_open.find(seg, rows, spans,
                                            audio if audio.exists() else None,
                                            aligner, sil)
        finally:
            if aligner:
                aligner.close()     # משחרר את ה-GPU לפני ההורדות

    for idx, seg, ranges in plan:
        title = seg.get("title", "")
        total_min = sum(e - s for s, e, *_ in ranges) / 60
        tag = f"{len(ranges)} חלקים" if len(ranges) > 1 else "רציף"
        print(f"[{idx}] {total_min:.0f} דק' ({tag})  {title}")
        for s, e, why_s, why_e in ranges:
            line = f"     {hms(s)} - {hms(e)}"
            if why_s:
                line += f"   פתיחה: {why_s}"
            if why_e:
                line += f" | סיום: {why_e}"
            print(line)
        hk = hooks.get(idx) or {}
        if hk.get("start") is not None:
            print(f"     טיזר: {hms(hk['start'])} ({hk['end'] - hk['start']:.1f}ש', "
                  f"{hk['source']}, {hk['how']})  \"{str(hk.get('text',''))[:60]}\"")
        elif hk.get("why"):
            print(f"     טיזר: אין - {hk['why']}")

    if args.plan:
        print("\n--plan: לא הורדתי כלום.")
        return

    # ---------- שלב ב: הורדה ----------
    if not args.url:
        url = ""
        vod = args.vod
        if not vod:
            meta = job_dir / "meta.json"
            if meta.exists():
                vod = json.loads(meta.read_text(encoding="utf-8")).get("vod_url", "")
        if not vod:
            print("אין כתובת. תן --url או --vod.")
            sys.exit(1)
        url = get_m3u8(vod)
        if not url:
            print("לא הצלחתי להשיג כתובת m3u8.")
            sys.exit(1)
    else:
        url = args.url

    out_dir = Path(args.out)
    if not out_dir.is_absolute():
        out_dir = job_dir / args.out
    out_dir.mkdir(parents=True, exist_ok=True)

    ok = 0
    failures = []       # (idx, כותרת, סיבה) - 67/4: לא נעלמים בשקט
    leads = {}          # idx -> אורך הטיזר שבאמת נכנס לקובץ
    existed = set()     # קליפים שלא נחתכו עכשיו - לא נוגעים ברשומה שלהם
    print()
    for idx, seg, ranges in plan:
        title = safe_name(seg.get("title", f"clip{idx}"))
        name = f"{idx:02d} - {title}"
        final = out_dir / f"{name}.mp4"

        print(f"[{idx}] {title}")
        if final.exists():
            print("     קיים כבר, מדלג.")
            existed.add(idx)
            ok += 1
            continue

        made = []
        failed = False
        hk = hooks.get(idx) or {}
        if hk.get("start") is not None:
            # כשל בטיזר לא מפיל את הקליפ: ממשיכים בלעדיו ואומרים את זה
            p0 = find_part(out_dir, name, 0)
            if not p0:
                print(f"     טיזר: {hk['start']:.2f}-{hk['end']:.2f}")
                if download_part(url, hk["start"], hk["end"],
                                 out_dir / f"{name}__p0", precise=True):
                    p0 = find_part(out_dir, name, 0)
            if p0 and probe_duration(p0) >= 2:
                made.append(p0)
            else:
                print("     ⚠ הורדת הטיזר נכשלה - הקליפ ייחתך בלעדיו.")
                if p0:
                    p0.unlink(missing_ok=True)
        for j, (start, end, _, _) in enumerate(ranges, 1):
            part_target = out_dir / f"{name}__p{j}"
            have = find_part(out_dir, name, j)
            if have:
                # חלק שירד בהרצה קודמת שההדבקה שלה נכשלה. לא מורידים שוב.
                print(f"     חלק {j}: קיים כבר ({have.name[-30:]})")
                made.append(have)
                continue
            print(f"     חלק {j}: {hms(start)}-{hms(end)}")
            if not download_part(url, start, end, part_target):
                print("     הורדת החלק נכשלה.")
                failed = True
                break
            found = find_part(out_dir, name, j)
            if not found:
                print("     yt-dlp סיים, אבל הקובץ לא נמצא בשם הצפוי.")
                failed = True
                break
            made.append(found)

        if failed:
            # החלקים שכבר ירדו נשארים: הרצה חוזרת תשתמש בהם ולא תוריד שוב
            failures.append((idx, seg.get("title", ""), "ההורדה נכשלה"))
            continue

        has_hook = bool(made) and made[0].stem.endswith("__p0")
        lead = probe_duration(made[0]) if has_hook else 0.0
        # 67/9: קפיצה בזמן בין parts (דילוג על זמן מת) = מעבר לשחור קצר
        shift = 1 if has_hook else 0
        jumps = {j - 1 + shift for j in range(1, len(ranges))
                 if ranges[j][0] - ranges[j - 1][1] > JUMP_FADE_GAP}
        if len(made) == 1:
            made[0].rename(final)
        else:
            print(f"     מדביק {len(made)} חלקים"
                  + (f" ({len(jumps)} מעברים לשחור בקפיצות זמן)" if jumps else "") + "...")
            if concat(made, final, fade_first=has_hook, fade_after=jumps):
                for p in made:
                    p.unlink(missing_ok=True)
            else:
                print("     ההדבקה נכשלה, משאיר את החלקים בנפרד.")
                failures.append((idx, seg.get("title", ""), "ההדבקה נכשלה"))
                continue

        leads[idx] = round(lead, 2)
        ok += 1
        print("     מוכן.")

    print(f"\nהסתיים: {ok}/{len(chosen)} קטעים בתיקייה {out_dir}")

    # ---------- שלב ג: פתק להעלאה ----------
    lines = []
    for idx, seg, ranges in plan:
        lines.append(f"קובץ: {idx:02d} - {safe_name(seg.get('title',''))}.mp4")
        lines.append(f"כותרת: {seg.get('title','')}")
        if seg.get("participants"):
            lines.append(f"משתתפים: {', '.join(seg['participants'])}")
        lines.append(f"תיאור: {seg.get('topic','')}")
        lines.append("מקור: " + ", ".join(f"{hms(s)}-{hms(e)}" for s, e, *_ in ranges))
        lines.append("")
    (out_dir / "upload_notes.txt").write_text("\n".join(lines), encoding="utf-8")
    print(f"נשמר: {out_dir / 'upload_notes.txt'}")

    # הטווחים המלוטשים, במכונה. התמנייל (משימה 4) צריך לתרגם את
    # quote_at - זמן מוחלט בשידור - לזמן בתוך הקליפ, ובלי זה הוא
    # היה מחשב לפי הטווח של המנתח, שזז עד 45 שניות בליטוש.
    ranges_path = out_dir / "ranges.json"
    try:
        known = json.loads(ranges_path.read_text(encoding="utf-8"))
    except Exception:
        known = []
    by_idx = {r.get("idx"): r for r in known if isinstance(r, dict)}
    for idx, seg, ranges in plan:
        if idx in existed and idx in by_idx:
            # הקובץ נחתך בהרצה קודמת. הרשומה שלו מתארת אותו, לא את התוכנית
            # של עכשיו (שאולי כוללת טיזר שאין בקובץ).
            continue
        entry = {
            "idx": idx,
            "file": f"{idx:02d} - {safe_name(seg.get('title', ''))}.mp4",
            "ranges": [[round(s, 2), round(e, 2)] for s, e, *_ in ranges],
        }
        # lead = שניות הטיזר בתחילת הקובץ. thumb ו-describe מזיזים לפיו.
        hk = hooks.get(idx) or {}
        if leads.get(idx):
            entry["lead"] = leads[idx]
            entry["cold_open"] = [hk.get("start"), hk.get("end")]
            entry["cold_open_text"] = hk.get("text", "")
        by_idx[idx] = entry
    ranges_path.write_text(
        json.dumps([by_idx[k] for k in sorted(by_idx)], ensure_ascii=False, indent=1),
        encoding="utf-8")

    # 67/4 (29.9): עד היום יציאה עם 0 גם כש-3 מתוך 4 נחתכו, ו-run10 מתריע
    # רק על קוד שאינו 0. עכשיו: הרשימה בקובץ, וקוד 2 = "חלק נכשלו".
    fail_path = out_dir / "cut_failures.json"
    if failures:
        fail_path.write_text(json.dumps(
            [{"idx": i, "title": t, "why": w} for i, t, w in failures],
            ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"\n⚠ {len(failures)} קטעים לא נחתכו: "
              + ", ".join(f"[{i}]" for i, _, _ in failures))
        sys.exit(2)
    fail_path.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
