"""
thumb.py - תמנייל אוטומטי לכל קליפ, לפי המפרט שנסגר ב-14.9 (משימה 4)

    ┌──────────────────────────────────────────┐
    │                     ╱        שם הסטרימר  │
    │   ░░ הפרצוף ░░      ╱                     │
    │   ░░ מהקטע  ░░→נמוג   מילה  מילה         │
    │   ░░░░░░░░░░░       ╱   **זהב**           │
    └──────────────────────────────────────────┘
    נייבי #151B25 · שמנת #F7F4ED · זהב #CE8B3C · Heebo 900

הטקסט מימין (נקרא ראשון בעברית), הפרצוף משמאל ונמוג לתוך הנייבי,
בלי חיתוך רקע - ה-GPU נעול לתמלול. הלוכסן הזהוב מהלוגו יושב באותו
מקום ובאותה זווית בכל תמנייל, וזה מה שיגרום לפרק 40 להיראות שייך
לפרק 1.

בחירת הפריים, שחשובה יותר מכל הגרפיקה:
    1. analyze13 מחזיר quote_at - הרגע של המשפט החזק. clips/ranges.json
       (מ-cut3) מתרגם אותו לזמן בתוך הקליפ.
    2. ~20 פריימים בחלון של ±15 שניות סביבו.
    3. סינון מקומי: גודל פנים וחדות (OpenCV אם מותקן, אחרת חדות בלבד).
    4. קריאת ראייה אחת שבוחרת מבין 5 הניצולים את ההבעה החזקה.
       בלי מפתח API - הניצול הראשון.

מ-27.9 (בקשת לירון): שתי תמונות כשאפשר - משמאל מי שהסטרימר מדבר איתו
או עליו (או אזור המסך שמחוץ למצלמה), וליד הטקסט הסטרימר עצמו, עם קו זהב
ביניהם באותה זווית של החתימה. מתחת לטקסט - ציטוט קצר (עד 5 מילים).
קריאת הראייה מקבלת פריימים מלאים עם אות לכל פנים ומחליטה: איזה פריים,
מי הסטרימר, מי מולו, ואיזה ציטוט. מיקום המצלמה של כל סטרימר נשמר
ב-cam_positions.json ומשמש כשאין מודל. לא ידוע מי הסטרימר - תמונה אחת.

הטקסט לא נכתב כאן. analyze13 כבר מחזיר thumb_text ו-thumb_emphasis.
קטע ישן בלי השדות האלה - retitle.py משלים אותם.

שימוש:
    python scripts\\thumb.py jobs\\pedrofederer_2026-09-09 1
    python scripts\\thumb.py jobs\\pedrofederer_2026-09-09 --all
    python scripts\\thumb.py jobs\\pedrofederer_2026-09-09 1 --no-llm
    python scripts\\thumb.py jobs\\pedrofederer_2026-09-09 1 --at 00:21:10    לעקוף את הרגע

פלט: clips\\NN - <כותרת>__thumb.jpg, ובתיקייה clips\\_thumbwork הפריימים
שנבחנו (נמחקת בסוף, --keep משאיר לבדיקה).

תלויות: pillow (חובה). opencv-python (מומלץ, לזיהוי פנים).
עברית: אם Pillow בנוי עם raqm - הוא מסדר לבד. אם לא (ברוב ההתקנות
בווינדוס) - הסדר ההפוך נעשה כאן, מילה-מילה, בלי python-bidi.
"""

import io
import os
import re
import sys
import json
import base64
import shutil
import argparse
import subprocess
from pathlib import Path

try:
    from PIL import Image, ImageDraw, ImageFont, ImageFilter, features
except ImportError:
    # ⚠ ImportError ולא SystemExit. thumb מיובא מתוך run10 ומתוך upload,
    # ושם `except Exception` לא תופס SystemExit. ב-15.9 זה הרג את run10
    # באמצע, העבודה נשארה "thumbnails", ואף קטע לא הגיע לאישור - 10 לייבים.
    raise ImportError("חסר Pillow. הרץ:  pip install pillow") from None

CV2_PROBLEM = ""                                  # למה אין OpenCV, להודעות ול-/health


def _cv2_problem(mod) -> str:
    """ריק אם OpenCV מתאים. 27.9: `import cv2` עבר אבל בלי CascadeClassifier.
    הסיבה: OpenCV 5 הוציא את ה-Haar cascades מהחבילה הראשית (ל-contrib).
    כל התמניילים של 26.9 נפלו על זה, ו-/health הראה ירוק.
    התיקון: pip install "opencv-python<5"."""
    need = ("CascadeClassifier", "cvtColor", "resize", "Laplacian", "data")
    missing = [n for n in need if not hasattr(mod, n)]
    if missing:
        ver = getattr(mod, "__version__", "?")
        return (f"OpenCV {ver} בלי {', '.join(missing)}"
                + (' - בגרסה 5 זה הוצא. pip install "opencv-python<5"'
                   if str(ver).startswith("5") else ""))
    return ""


try:
    import cv2                                   # לא חובה
    import numpy as np
    CV2_PROBLEM = _cv2_problem(cv2)
    if CV2_PROBLEM:
        print(f"⚠ {CV2_PROBLEM}. ממשיך בלי זיהוי פנים (חיתוך מרכזי).", file=sys.stderr)
        cv2 = None
except Exception as exc:
    CV2_PROBLEM = f"אין OpenCV ({type(exc).__name__})"
    cv2 = None
    np = None


def find_root(start: Path) -> Path:
    for c in [start, *start.parents]:
        if (c / "scripts").is_dir():
            return c
    return start


ROOT = find_root(Path(__file__).resolve().parent)
sys.path.insert(0, str(ROOT / "scripts"))

W, H = 1280, 720
NAVY = (0x15, 0x1B, 0x25)
CREAM = (0xF7, 0xF4, 0xED)
GOLD = (0xCE, 0x8B, 0x3C)

# ערכות צבע (27.9). "brand" = המפרט של 14.9 (צבעי האווטאר). האחרות הן
# ניסיון להגדיל הקלקות - צבעים חמים וקו מתאר, כמו בערוצי קליפים גדולים.
# ברירת המחדל: youtube.json -> "thumb_theme", או --theme בשורת הפקודה.
THEMES = {
    "brand": {"bg": NAVY, "bg2": NAVY, "text": CREAM, "accent": GOLD,
              "quote": (0xC9, 0xC5, 0xBC), "line": GOLD, "stroke": 0,
              "name_bg": GOLD, "name_fg": NAVY},
    "red":   {"bg": (0x7A, 0x00, 0x0E), "bg2": (0xD4, 0x10, 0x26), "text": (255, 255, 255),
              "accent": (0xFF, 0xD6, 0x0A), "quote": (0xFF, 0xF1, 0xB8),
              "line": (0xFF, 0xD6, 0x0A), "stroke": 7,
              "name_bg": (0xFF, 0xD6, 0x0A), "name_fg": (0x14, 0x14, 0x14)},
    "navy_yellow": {"bg": (0x0B, 0x10, 0x1C), "bg2": (0x1B, 0x26, 0x3D),
                    "text": (255, 255, 255), "accent": (0xFF, 0xD6, 0x0A),
                    "quote": (0xDD, 0xE3, 0xEE), "line": (0xFF, 0xD6, 0x0A), "stroke": 6,
                    "name_bg": (0xE0, 0x1B, 0x2F), "name_fg": (255, 255, 255)},
}
FONT_PATH = ROOT / "brand" / "Heebo.ttf"

PHOTO_W = 720            # רוחב אזור הפרצוף, משמאל
FADE_FROM = 0.55         # מאיפה ברוחב התמונה מתחיל המעבר לנייבי
TEXT_RIGHT = 70          # שוליים מימין
TEXT_LEFT = 640          # הטקסט לא נכנס שמאלה מזה
WINDOW_SEC = 15          # ±15 שניות סביב הציטוט
N_FRAMES = 20
N_FINALISTS = 5
N_SPREAD = 10            # 27.9: עוד פריימים לאורך כל הקטע - צחוק אמיתי לא תמיד ליד הציטוט
N_SPREAD_KEEP = 3

# חתימת הסדרה: הקו האלכסוני של ה-Z בלוגו. אותה זווית, אותו מקום.
SLASH_TOP = (668, 64)
SLASH_BOTTOM = (646, 190)
SLASH_WIDTH = 9

RAQM = features.check("raqm")


def load_json(path: Path, default):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return default


def to_seconds(t) -> float:
    if isinstance(t, (int, float)):
        return float(t)
    parts = [float(x) for x in str(t).strip().split(":")]
    while len(parts) < 3:
        parts.insert(0, 0.0)
    return parts[0] * 3600 + parts[1] * 60 + parts[2]


def duration(video: Path) -> float:
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                        "-of", "csv=p=0", str(video)],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    try:
        return float(r.stdout.strip())
    except ValueError:
        return 0.0


# ------------------------------------------------------------ עברית בציור

MIRROR = str.maketrans("()[]{}<>", ")(][}{><")
LTR_RUN = re.compile(r"[A-Za-z0-9#@%$&+=/:.,'\"-]*[A-Za-z0-9][A-Za-z0-9#@%$&+=/:.,'\"-]*")


def visual(word: str) -> str:
    """
    מילה אחת בסדר שבו צריך לצייר אותה משמאל לימין.
    עם raqm - Pillow מסדר לבד, ולכן מחזירים כמו שהיא.
    בלי raqm - הופכים את האותיות, אבל רצף לועזי/מספרי נשאר בסדרו
    ("#21" לא הופך ל-"12#"), וסוגריים מתחלפים כדי שיפנו נכון.
    """
    if RAQM or not re.search(r"[\u0590-\u05FF]", word):
        return word
    pieces, pos = [], 0
    for m in LTR_RUN.finditer(word):
        if m.start() > pos:
            pieces.append(("rtl", word[pos:m.start()]))
        pieces.append(("ltr", m.group(0)))
        pos = m.end()
    if pos < len(word):
        pieces.append(("rtl", word[pos:]))
    out = []
    for kind, text in reversed(pieces):
        out.append(text if kind == "ltr" else text[::-1].translate(MIRROR))
    return "".join(out)


def font(size: int, weight: str = "Black"):
    if not FONT_PATH.exists():
        for alt in ("C:/Windows/Fonts/arialbd.ttf",
                    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"):
            if Path(alt).exists():
                return ImageFont.truetype(alt, size)
        return ImageFont.load_default()
    # בלי raqm מבקשים במפורש פריסה בסיסית. אחרת, במכונה שכן יש בה
    # raqm אבל RAQM כובה ידנית, העברית הייתה מתהפכת פעמיים.
    engine = ImageFont.Layout.RAQM if RAQM else ImageFont.Layout.BASIC
    f = ImageFont.truetype(str(FONT_PATH), size, layout_engine=engine)
    try:
        f.set_variation_by_name(weight)
    except Exception:
        try:
            f.set_variation_by_axes([900 if weight == "Black" else 500])
        except Exception:
            pass
    return f


def draw_word(d, x_right: float, y: float, word: str, f, fill, stroke: int = 0) -> float:
    """מצייר מילה כך שהקצה הימני שלה ב-x_right. מחזיר את הרוחב."""
    text = visual(word)
    kw = {"direction": "rtl", "language": "he"} if RAQM else {}
    w = d.textlength(text, font=f, **kw)
    if stroke:
        kw.update(stroke_width=stroke, stroke_fill=(0, 0, 0))
    d.text((x_right - w, y), text, font=f, fill=fill, **kw)
    return w


def word_w(d, word: str, f) -> float:
    kw = {"direction": "rtl", "language": "he"} if RAQM else {}
    return d.textlength(visual(word), font=f, **kw)


# ----------------------------------------------------------- פריסת טקסט

def split_lines(words: list, n: int) -> list:
    """כל החלוקות של המילים ל-n שורות לא ריקות, לפי הסדר."""
    if n == 1:
        return [[words]]
    out = []
    for i in range(1, len(words) - n + 2):
        for rest in split_lines(words[i:], n - 1):
            out.append([words[:i]] + rest)
    return out


def layout(d, text: str, max_w: int, max_h: int):
    """
    הגופן הגדול ביותר שבו הטקסט נכנס, עד 3 שורות.
    מעדיף שורות מאוזנות - שורה של מילה אחת מעל שורה של שלוש נראית
    כמו טעות.
    """
    words = text.split()[:6]
    if not words:
        return None
    for size in range(190, 60, -6):
        f = font(size)
        space = word_w(d, " ", f) or size * 0.25
        line_h = int(size * 1.02)                  # מרווח שורות צפוף
        best = None
        for n in range(1, min(3, len(words)) + 1):
            if n * line_h > max_h:
                break
            for lines in split_lines(words, n):
                widths = [sum(word_w(d, w, f) for w in ln) + space * (len(ln) - 1)
                          for ln in lines]
                if max(widths) > max_w:
                    continue
                spread = max(widths) - min(widths)
                key = (spread, n)
                if best is None or key < best[0]:
                    best = (key, lines, widths)
            if best:
                break              # הכי מעט שורות שנכנסות בגודל הזה
        if best:
            return {"font": f, "size": size, "lines": best[1],
                    "widths": best[2], "line_h": line_h, "space": space}
    return None


# ------------------------------------------------------ זמן בתוך הקליפ

def clip_time(job: Path, idx: int, seg: dict, clip_len: float, at: str = ""):
    """
    הרגע שסביבו מחפשים פריים, בשניות מתחילת הקליפ.
    סדר העדיפויות: --at, quote_at דרך ranges.json, quote_at דרך parts
    של המנתח (לא מלוטש, עד 45 שניות סטייה), ואמצע הקליפ.
    """
    target = at or seg.get("quote_at") or ""
    if not target:
        return clip_len * 0.5, "אין quote_at, אמצע הקליפ"
    try:
        t_abs = to_seconds(target)
    except ValueError:
        return clip_len * 0.5, "quote_at לא תקין, אמצע הקליפ"

    ranges, source, lead = None, "", 0.0
    for r in load_json(job / "clips" / "ranges.json", []):
        if r.get("idx") == idx:
            ranges, source = r.get("ranges"), "ranges.json"
            # cold open (משימה 37): הטיזר יושב לפני הקטע, והכל זז בו
            lead = float(r.get("lead") or 0)
            break
    if not ranges:
        parts = seg.get("parts") or [{"start": seg.get("start"), "end": seg.get("end")}]
        try:
            ranges = [[to_seconds(p["start"]), to_seconds(p["end"])] for p in parts]
            source = "parts (לא מלוטש)"
        except Exception:
            return clip_len * 0.5, "אין טווחים, אמצע הקליפ"

    offset = lead
    for a, b in ranges:
        if a <= t_abs <= b:
            t = offset + (t_abs - a)
            return max(0.0, min(clip_len - 1, t)), f"quote_at לפי {source}"
        offset += b - a
    return clip_len * 0.5, "quote_at מחוץ לטווחים, אמצע הקליפ"


# --------------------------------------------------------- שליפת פריימים

def grab_frames(video: Path, center: float, workdir: Path, clip_len: float) -> list:
    """~20 פריימים בחלון סביב center. קריאה אחת ל-ffmpeg."""
    workdir.mkdir(parents=True, exist_ok=True)
    for old in workdir.glob("f_*.jpg"):
        old.unlink()
    start = max(0.0, center - WINDOW_SEC)
    span = min(2 * WINDOW_SEC, max(1.0, clip_len - start))
    rate = N_FRAMES / span
    cmd = ["ffmpeg", "-hide_banner", "-nostdin", "-v", "error", "-y",
           "-ss", f"{start:.2f}", "-t", f"{span:.2f}", "-i", str(video),
           "-vf", f"fps={rate:.4f},scale='min(1920,iw)':-2",
           "-q:v", "2", str(workdir / "f_%03d.jpg")]
    res = subprocess.run(cmd, capture_output=True, text=True,
                         encoding="utf-8", errors="replace")
    if res.returncode != 0:
        print(f"  ffmpeg: {(res.stderr or '').strip()[-200:]}")
    frames = sorted(workdir.glob("f_*.jpg"))
    return [(p, start + i / rate) for i, p in enumerate(frames)]


def grab_spread(video: Path, workdir: Path, clip_len: float) -> list:
    """N_SPREAD פריימים בפיזור שווה על כל הקליפ (seek מהיר לכל אחד)."""
    workdir.mkdir(parents=True, exist_ok=True)
    for old in workdir.glob("g_*.jpg"):
        old.unlink()
    out = []
    for i in range(N_SPREAD):
        t = clip_len * (i + 0.5) / N_SPREAD
        dst = workdir / f"g_{i:02d}.jpg"
        subprocess.run(["ffmpeg", "-hide_banner", "-nostdin", "-v", "error", "-y",
                        "-ss", f"{t:.2f}", "-i", str(video), "-frames:v", "1",
                        "-vf", "scale='min(1920,iw)':-2", "-q:v", "2", str(dst)],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
        if dst.exists():
            out.append((dst, t))
    return out


_cascade = None


def _overlap(a, b) -> float:
    """כמה מהמסגרת הקטנה מבין השתיים נמצאת בתוך השנייה (0-1)."""
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    ix = max(0, min(ax + aw, bx + bw) - max(ax, bx))
    iy = max(0, min(ay + ah, by + bh) - max(ay, by))
    return (ix * iy) / max(1, min(aw * ah, bw * bh))


def detect_faces(img: Image.Image) -> list:
    """
    כל הפנים בפריים, [(x, y, w, h), ...], הגדולות קודם. בלי OpenCV - [].
    27.9: עד היום נלקחו רק הפנים הגדולות. אצל אוהד באומיגל אלה פני
    הבחורה, והסטרימר עצמו נעלם מהתמנייל. עכשיו מחזירים את כולן, והבחירה
    מי הסטרימר ומי "התוכן" נעשית למעלה (ask_vision / זיכרון מצלמה).
    Haar מחזיר לפעמים את אותן פנים פעמיים בקנה מידה אחר - משאירים את
    זו עם המשקל הגבוה (הזיהוי הבטוח יותר).
    """
    global _cascade
    if cv2 is None:
        return []
    if _cascade is None:
        path = os.path.join(cv2.data.haarcascades, "haarcascade_frontalface_default.xml")
        _cascade = cv2.CascadeClassifier(path)
    gray = cv2.cvtColor(np.array(img.convert("RGB")), cv2.COLOR_RGB2GRAY)
    small = max(1, gray.shape[0] // 540)
    if small > 1:
        gray = cv2.resize(gray, (gray.shape[1] // small, gray.shape[0] // small))
    min_side = max(24, gray.shape[0] // 18)
    try:
        rects, _levels, weights = _cascade.detectMultiScale3(
            gray, scaleFactor=1.1, minNeighbors=6, minSize=(min_side, min_side),
            outputRejectLevels=True)
        weights = [float(np.ravel(w)[0]) if np.size(w) else 0.0 for w in weights]
    except Exception:
        rects = _cascade.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=6,
                                          minSize=(min_side, min_side))
        weights = [0.0] * len(rects)
    cands = sorted(zip([tuple(int(v) for v in r) for r in rects], weights),
                   key=lambda c: -c[1])
    kept = []
    for box, _w in cands:
        if all(_overlap(box, k) < 0.35 for k in kept):
            kept.append(box)
    kept = [(x * small, y * small, w * small, h * small) for x, y, w, h in kept]
    return sorted(kept, key=lambda f: -(f[2] * f[3]))


def detect_face(img: Image.Image):
    """(x, y, w, h) של הפנים הגדולות, או None. בלי OpenCV - תמיד None."""
    faces = detect_faces(img)
    return faces[0] if faces else None


def sharpness(img: Image.Image, box=None) -> float:
    region = img.crop(box) if box else img
    region = region.convert("L").resize((256, 256))
    if cv2 is not None:
        return float(cv2.Laplacian(np.array(region), cv2.CV_64F).var())
    edges = region.filter(ImageFilter.FIND_EDGES)
    px = list(edges.getdata())
    mean = sum(px) / len(px)
    return sum((p - mean) ** 2 for p in px) / len(px)


def score_frames(frames: list, n: int = N_FINALISTS) -> list:
    """מדרג פריימים: פנים גדולות וחדות קודם. מחזיר רשימה ממוינת."""
    scored = []
    for path, t in frames:
        try:
            img = Image.open(path).convert("RGB")
        except Exception:
            continue
        faces = detect_faces(img)
        face = faces[0] if faces else None
        if face:
            x, y, w, h = face
            area = (w * h) / (img.width * img.height)
            sharp = sharpness(img, (x, y, x + w, y + h))
            # פריים עם פנים תמיד גובר על פריים בלי. אצל רוב הסטרימרים
            # המצלמה היא ריבוע קטן בפינה, והפנים תופסות אחוז מהמסך -
            # בלי הבונוס, מסך צ'אט חד היה מנצח אותן.
            score = 100 + area * 2000 + min(sharp, 800) / 20
            if len(faces) >= 2:              # 27.9: סטרימר + מי שמולו
                score += 25
        else:
            sharp = sharpness(img)
            score = min(sharp, 800) / 80
        scored.append({"path": path, "t": t, "face": face, "faces": faces,
                       "score": score})
    scored.sort(key=lambda s: -s["score"])

    # לא חמישה פריימים צמודים מאותה שנייה
    picked = []
    for s in scored:
        if all(abs(s["t"] - p["t"]) >= 2.0 for p in picked):
            picked.append(s)
        if len(picked) >= n:
            break
    return picked


def mood(seg: dict) -> str:
    """איזו הבעה לחפש, לפי הקטגוריה והכותרת (27.9, בקשת לירון)."""
    cat = seg.get("category") or ""
    t = " ".join([seg.get("title") or "", seg.get("thumb_text") or ""])
    if re.search(r"מצחיק|צוחק|צחוק|קורע|הזוי", cat + " " + t):
        return "צחוק אמיתי: פה פתוח בצחוק, חיוך רחב, עיניים מצטמצמות מצחוק"
    if re.search(r"עצבני|מתפוצץ|מתעצבן|כועס|זועם|מתעמת|צועק|רב |ריב|נגד", t):
        return "עצבים: כעס, מצח מכווץ, צעקה, מבט חד"
    if cat == "דרמה":
        return "תגובה חזקה: הלם או כעס על מה שהוא שומע"
    if cat == "רונאלד":
        return "שובבות: חיוך ממזרי, הפתעה, צחוק"
    if cat == "סיפור אישי":
        return "רגש ורצינות: מבט ישיר וכנה"
    return "הבעה חזקה: הלם, צחוק, פליאה"


# --------------------------------------------------------- חיתוך הפרצוף

def cam_bounds(img: Image.Image, face):
    """
    גבולות ריבוע המצלמה סביב הפנים, בקירוב. אצל רוב הסטרימרים המצלמה
    היא חלון קטן בפינה, ומתחתיו צ'אט או משחק. חיתוך שחוצה את הגבול
    נראה כמו טעות. מחפש קו חד (שינוי בהירות לאורך כל הרוחב) מתחת
    לפנים ומימין להן. בלי OpenCV או בלי קו ברור - כל התמונה.
    """
    if cv2 is None or not face:
        return (0, 0, img.width, img.height)
    x, y, w, h = face
    g = np.asarray(img.convert("L"), dtype=np.float32)

    def edge_after(profile, start, stop):
        if stop - start < 4:
            return None
        seg = profile[start:stop]
        base = float(np.median(profile)) + 1e-3
        hits = np.where(seg > max(25.0, base * 4))[0]
        return start + int(hits[0]) if len(hits) else None

    x0, x1 = max(0, x - w), min(img.width, x + 2 * w)
    rows = np.abs(np.diff(g[:, x0:x1], axis=0)).mean(axis=1)
    # 27.9: היה y + h*1.6. בפנים גדולות (אומיגל, מצלמה קרובה) זה התחיל
    # לחפש מתחת לקו של ה-overlay, ופס ה-Discord/קוד ההנחה נכנס לתמנייל.
    bottom = edge_after(rows, int(y + h * 1.1), img.height - 1) or img.height

    y0, y1 = max(0, y - h // 2), min(img.height, y + 2 * h)
    cols = np.abs(np.diff(g[y0:y1, :], axis=1)).mean(axis=0)
    right = edge_after(cols, int(x + w * 1.5), img.width - 1) or img.width
    left_edges = cols[: max(0, int(x - w * 0.5))][::-1]
    lhit = edge_after(left_edges, 0, len(left_edges))
    left = (int(x - w * 0.5) - lhit) if lhit is not None else 0

    up = rows[: max(0, int(y - h * 0.3))][::-1]
    uhit = edge_after(up, 0, len(up))
    top = (int(y - h * 0.3) - uhit) if uhit is not None else 0
    return (max(0, left), max(0, top), right, bottom)


def face_crop(img: Image.Image, face, out_w: int, out_h: int) -> Image.Image:
    """
    חותך סביב הפנים כך שהראש ייכנס עם כתפיים ויצא מהקצה התחתון.
    בלי פנים - חיתוך מרכזי, שיהיה לפחות משהו.
    """
    ratio = out_w / out_h
    if face:
        x, y, w, h = face
        bl, bt, br, bb = cam_bounds(img, face)
        bw, bh = br - bl, bb - bt
        ch = min(bh, h * 3.3)
        cw = ch * ratio
        if cw > bw:                               # צר מדי - מקטינים את שניהם
            cw = bw
            ch = cw / ratio
        ch = max(ch, h * 1.6)                     # אבל לא עד שהראש נחתך
        cw = ch * ratio
        cx = x + w / 2
        cy = y + h * 0.5
        left = cx - cw / 2
        top = cy - ch * 0.42                      # הפנים בשליש העליון
        left = max(bl, min(br - cw, left))
        top = max(bt, min(bb - ch, top))
    else:
        ch = img.height
        cw = min(img.width, ch * ratio)
        ch = cw / ratio
        left = (img.width - cw) / 2
        top = (img.height - ch) / 2
    left = max(0, min(img.width - cw, left))
    top = max(0, min(img.height - ch, top))
    box = (int(left), int(top), int(left + cw), int(top + ch))
    crop = img.crop(box).resize((out_w, out_h), Image.LANCZOS)
    return crop.filter(ImageFilter.UnsharpMask(radius=2, percent=70, threshold=3))


# ------------------------------------------------------------ ההרכבה

def content_region(img: Image.Image, streamer_face):
    """
    כשאין פנים של מישהו אחר (משחק, סרטון, צ'אט) - החלק של המסך שמחוץ
    למצלמה של הסטרימר. מחזיר (l, t, r, b), או None אם המצלמה היא כל
    המסך (לייב IRL / רק מדבר) ואין "תוכן" נפרד להראות.
    """
    if not streamer_face:
        return None
    # 27.9 (זיגי #3): מצלמה במסך מלא - "התוכן" יצא הרקע של הסטודיו.
    # פנים ברוחב של יותר מ-15% מהמסך = המצלמה היא המסך, אין תוכן נפרד.
    if streamer_face[2] > 0.15 * img.width:
        return None
    cl, ct, cr, cb = cam_bounds(img, streamer_face)
    iw, ih = img.width, img.height
    rects = [(0, 0, cl, ih), (cr, 0, iw, ih), (0, 0, iw, ct), (0, cb, iw, ih)]
    best = max(rects, key=lambda r: (r[2] - r[0]) * (r[3] - r[1]))
    if (best[2] - best[0]) * (best[3] - best[1]) < 0.25 * iw * ih:
        return None
    return best


def region_crop(img: Image.Image, box, out_w: int, out_h: int) -> Image.Image:
    """החיתוך הגדול ביותר ביחס out_w:out_h, במרכז של box."""
    l, t, r, b = box
    bw, bh = r - l, b - t
    ratio = out_w / out_h
    cw, ch = (bh * ratio, bh) if bw / bh > ratio else (bw, bw / ratio)
    left = l + (bw - cw) / 2
    top = t + (bh - ch) / 2
    crop = img.crop((int(left), int(top), int(left + cw), int(top + ch)))
    return crop.resize((out_w, out_h), Image.LANCZOS)


# שתי תמונות (27.9): התוכן משמאל, הסטרימר ליד הטקסט, וביניהם קו זהב
# באותה זווית של חתימת הסדרה. הקו עובר מ-(DIV_TOP, 0) ל-(DIV_BOTTOM, H).
SLOPE = (SLASH_TOP[0] - SLASH_BOTTOM[0]) / (SLASH_BOTTOM[1] - SLASH_TOP[1])
DIV_TOP = 420
DIV_BOTTOM = int(DIV_TOP - SLOPE * H)
DUO_FADE_FROM = 600          # בשתי תמונות הנמוג מתחיל מאוחר, שהסטרימר לא ייבלע


def paste_photo(canvas, photo, x0: int, fade_from: int, left_edge=None) -> None:
    """מדביק תמונה מ-x0, נמוגה לנייבי מ-fade_from עד PHOTO_W.
    left_edge(y) - אם ניתן, מה שמשמאל לו לא מודבק (החיתוך האלכסוני)."""
    pw = photo.width
    mask = Image.new("L", (pw, H), 255)
    px = mask.load()
    for y in range(H):
        cut = (left_edge(y) - x0) if left_edge else -1
        for x in range(pw):
            ax = x0 + x
            if x < cut:
                px[x, y] = 0
            elif ax >= fade_from:
                k = min(1.0, (ax - fade_from) / max(1, PHOTO_W - fade_from))
                px[x, y] = int(255 * (1 - k) ** 1.6)
    canvas.paste(photo, (x0, 0), mask)


def compose(frame: Image.Image, face, text: str, emphasis: str,
            streamer: str, out_path: Path, other=None, content_box=None,
            quote: str = "", theme: str = "", other_frame=None) -> None:
    """
    face = הפנים של הסטרימר (או הפנים הטובות ביותר, כשלא ידוע מי הוא).
    other / content_box - מה שהוא מגיב אליו: פנים של מי שמולו, או אזור
    המסך שמחוץ למצלמה. בלי אף אחד מהם - תמונה אחת, כמו במפרט של 14.9.
    """
    # מדיניות תוכן (58): התמנייל הוא המקום הראשון שיוטיוב בודק.
    try:
        from content import soften
        text, emphasis, quote = soften(text), soften(emphasis), soften(quote)
    except ImportError:
        pass
    th = THEMES.get(theme or default_theme(), THEMES["brand"])
    bg = background(th)
    canvas = bg.copy()

    duo = bool(face) and (bool(other) or bool(content_box))
    if duo:
        left_w = DIV_TOP
        right_x0 = DIV_BOTTOM
        right_w = PHOTO_W - right_x0
        if other:
            # 27.9: מי שמולו יכול לבוא מפריים אחר - כל צד ברגע הכי טוב שלו
            left = face_crop(other_frame or frame, other, left_w, H)
        else:
            left = region_crop(frame, content_box, left_w, H)
            left = left.filter(ImageFilter.UnsharpMask(radius=2, percent=60, threshold=3))
        canvas.paste(left, (0, 0))
        right = face_crop(frame, face, right_w, H)
        edge = lambda y: DIV_TOP - SLOPE * y
        paste_photo(canvas, right, right_x0, DUO_FADE_FROM, edge)
        d0 = ImageDraw.Draw(canvas)
        d0.line([(DIV_TOP, -10), (DIV_BOTTOM, H + 10)], fill=th["line"], width=8)
    else:
        photo = face_crop(frame, face, PHOTO_W, H)
        paste_photo(canvas, photo, 0, int(PHOTO_W * FADE_FROM))

    # הצללה עדינה בתחתית, שהקצה לא ייראה חתוך
    shade = Image.new("L", (1, H), 0)
    for y in range(H):
        shade.putpixel((0, y), int(max(0, (y - H * 0.8) / (H * 0.2)) * 110))
    canvas = Image.composite(bg, canvas, shade.resize((W, H)))

    d = ImageDraw.Draw(canvas)

    # חתימת הסדרה
    d.line([SLASH_TOP, SLASH_BOTTOM], fill=th["line"], width=SLASH_WIDTH)

    # שם הסטרימר, פינה ימנית עליונה (לא למטה: שם יושב תג המשך).
    # 27.9: היה 38 פיקסל בלי רקע - קטן מדי בפיד. עכשיו תג צבעוני בולט.
    if streamer:
        nf = font(64, "Black")
        sp = word_w(d, " ", nf)
        words = streamer.split()
        tw = sum(word_w(d, w, nf) for w in words) + sp * (len(words) - 1)
        pad_x, top, h_tag = 26, 36, 92
        x1 = W - TEXT_RIGHT + 10
        d.rounded_rectangle([x1 - tw - 2 * pad_x, top, x1, top + h_tag], radius=16,
                            fill=th["name_bg"])
        draw_word_line(d, x1 - pad_x, top + 2, words, nf,
                       lambda w: th["name_fg"], sp)

    # הטקסט הראשי. עם ציטוט - הוא מקבל פחות גובה, והציטוט יושב מתחתיו.
    max_w = W - TEXT_RIGHT - TEXT_LEFT
    qlay = quote_layout(d, quote, max_w) if quote else None
    box_h = 420 if not qlay else 310
    lay = layout(d, text, max_w, box_h)
    y_end = 175
    if lay:
        total_h = lay["line_h"] * len(lay["lines"])
        y = 175 + (box_h - total_h) / 2
        emph = (emphasis or "").strip()
        for ln in lay["lines"]:
            draw_word_line(d, W - TEXT_RIGHT, y, ln, lay["font"],
                           lambda w: th["accent"] if emph and w == emph else th["text"],
                           lay["space"], th["stroke"])
            y += lay["line_h"]
        y_end = y
    if qlay:
        y = max(y_end + 28, 175 + box_h + 10)
        for ln in qlay["lines"]:
            draw_word_line(d, W - TEXT_RIGHT, y, ln, qlay["font"],
                           lambda w: th["quote"], qlay["space"],
                           min(th["stroke"], 4))
            y += qlay["line_h"]

    canvas.save(out_path, quality=92)


def default_theme() -> str:
    return str(load_json(ROOT / "youtube.json", {}).get("thumb_theme") or "brand")


def background(th: dict) -> Image.Image:
    """רקע: מעבר אלכסוני מ-bg (שמאל למטה) ל-bg2 (ימין למעלה)."""
    a, b = th["bg"], th["bg2"]
    if a == b:
        return Image.new("RGB", (W, H), a)
    small = Image.new("RGB", (64, 36))
    px = small.load()
    for y in range(36):
        for x in range(64):
            k = min(1.0, max(0.0, (x / 63) * 0.7 + (1 - y / 35) * 0.3))
            px[x, y] = tuple(int(a[i] + (b[i] - a[i]) * k) for i in range(3))
    return small.resize((W, H), Image.BILINEAR)


def quote_layout(d, quote: str, max_w: int):
    """ציטוט קצר במירכאות, עד 2 שורות. לא נכנס - לא מציירים (לא מקטינים לאין-סוף)."""
    words = quote.split()
    if not words:
        return None
    words[0] = '"' + words[0]
    words[-1] = words[-1] + '"'
    for size in (50, 44, 38):
        f = font(size, "Bold")
        space = word_w(d, " ", f) or size * 0.25
        lines, cur = [], []
        for w in words:
            trial = cur + [w]
            width = sum(word_w(d, x, f) for x in trial) + space * (len(trial) - 1)
            if width <= max_w or not cur:
                cur = trial
            else:
                lines.append(cur)
                cur = [w]
        lines.append(cur)
        too_wide = any(sum(word_w(d, x, f) for x in ln) + space * (len(ln) - 1) > max_w
                       for ln in lines)
        if len(lines) <= 2 and not too_wide:
            return {"font": f, "lines": lines, "space": space, "line_h": int(size * 1.2)}
    return None


def short_quote(quote: str, max_words: int = 6) -> str:
    """בלי מודל: המשפט הראשון של הציטוט, אם הוא קצר מספיק. אחרת כלום."""
    first = re.split(r"[,?!.;:]", (quote or "").strip())[0].strip()
    first = re.sub(r"[\"'״“”]", "", first)
    return first if 2 <= len(first.split()) <= max_words else ""


def draw_word_line(d, x_right, y, words, f, color_of, space, stroke: int = 0) -> None:
    """שורה בעברית: המילה הראשונה בימין, וכל אחת אחריה משמאלה."""
    x = x_right
    for w in words:
        width = draw_word(d, x, y, w, f, color_of(w), stroke)
        x -= width + space


# -------------------------------------------------------- בחירה במודל

LETTERS = "ABCDEFGH"
CAM_FILE = ROOT / "cam_positions.json"


def face_sheet(finalists: list):
    """הניצולים כפריימים מלאים ברשת 3 עמודות, עם מספר לכל פריים ואות
    לכל פנים. מלא ולא חיתוך - כדי שהמודל יראה מי יושב מול המצלמה
    עם אוזניות ומי בחלון השני."""
    cw, ch = 640, 360
    cols = 3
    rows = (len(finalists) + cols - 1) // cols
    sheet = Image.new("RGB", (cw * cols, ch * rows), (0, 0, 0))
    d = ImageDraw.Draw(sheet)
    lf = font(40, "Bold")
    for i, fr in enumerate(finalists):
        img = Image.open(fr["path"]).convert("RGB")
        sx, sy = cw / img.width, ch / img.height
        ox, oy = (i % cols) * cw, (i // cols) * ch
        sheet.paste(img.resize((cw, ch)), (ox, oy))
        for j, (x, y, w, h) in enumerate(fr.get("faces") or []):
            if j >= len(LETTERS):
                break
            box = [ox + x * sx, oy + y * sy, ox + (x + w) * sx, oy + (y + h) * sy]
            d.rectangle(box, outline=(0, 255, 255), width=4)
            d.rectangle([box[0], box[1] - 44, box[0] + 40, box[1]], fill=(0, 0, 0))
            d.text((box[0] + 8, box[1] - 46), LETTERS[j], font=lf, fill=(0, 255, 255))
        d.rectangle([ox + 6, oy + 6, ox + 62, oy + 64], fill=(0, 0, 0))
        d.text((ox + 18, oy + 6), str(i + 1), font=font(46, "Bold"), fill=GOLD)
    return sheet


def ask_vision(finalists: list, text: str, title: str, streamer: str = "",
               quote: str = "", feel: str = ""):
    """
    קריאת ראייה אחת שמחליטה הכל. מחזיר dict:
        frame     אינדקס הפריים (0-based)
        streamer  אינדקס הפנים של הסטרימר בפריים הזה, או None
        other     אינדקס הפנים של מי שמולו / עליו מדברים, או None
        quote     ציטוט קצר (עד 5 מילים) מתוך quote, או ""
    או None אם אין מודל / התשובה לא שמישה.
    """
    if not os.environ.get("ANTHROPIC_API_KEY") or not finalists:
        return None
    try:
        import anthropic
        import analyze13 as az
    except (Exception, SystemExit):       # analyze13 עושה sys.exit בלי anthropic
        return None

    sheet = face_sheet(finalists)
    buf = io.BytesIO()
    sheet.save(buf, format="JPEG", quality=80)
    b64 = base64.standard_b64encode(buf.getvalue()).decode()

    who = streamer or "הסטרימר"
    feel = feel or "הבעה חזקה: הלם, צחוק, פליאה"
    prompt = (
        f"אלה {len(finalists)} פריימים ממוספרים מקטע בלייב של {who}. "
        "על כל פנים שזוהו יש מסגרת עם אות (A, B...). האותיות נפרדות בכל פריים.\n"
        f"כותרת הסרטון: \"{title}\"\n"
        f"טקסט התמנייל: \"{text}\"\n"
        + (f"הציטוט מהרגע הזה: \"{quote}\"\n" if quote else "")
        + f"אופי הקטע: {feel}\n"
        + "\nהתמנייל מורכב משתי תמונות נפרדות: " + who + " בצד אחד, ומי שהוא מדבר "
        "איתו או עליו בצד השני. **כל צד יכול לבוא מפריים אחר** - בחר לכל אחד את "
        "הרגע הכי טוב שלו.\n"
        f"1. streamer_frame + streamer - הפריים והאות של {who} עצמו (מי שמשדר: בדרך "
        "כלל עם אוזניות או מיקרופון, מול המצלמה שלו, באותו מקום בכל הפריימים). "
        f"{who} חייב להיראות טוב: עיניים פקוחות, לא באמצע מילה עם פה עקום, לא עיניים "
        f"חצי סגורות, לא מבט למטה. מבין אלה - ההבעה שהכי מתאימה לאופי הקטע ({feel}). "
        "צחוק עם פה פתוח זה מצוין; פה עקום באמצע משפט זה לא.\n"
        "2. other_frame + other - הפריים והאות של מי שהוא מדבר איתו או עליו (שיחת "
        "וידאו, אורח, מי שמופיע בסרטון שהוא מגיב אליו), ברגע הכי חזק שלו. null לשניהם "
        "אם אין כזה באף פריים, או שזה סתם מישהו ברקע, ציור או לוגו.\n"
        + ("3. short_quote - עד 5 מילים מתוך הציטוט, מילה במילה, החלק הכי חזק "
           "ומסקרן. \"\" אם אין בו משהו שעובד לבד.\n" if quote else "")
        + 'החזר JSON בלבד: {"streamer_frame": <מספר>, "streamer": "A", '
        '"other_frame": <מספר> או null, "other": "B" או null, '
        '"short_quote": "...", "why": "<קצר>"}'
    )
    client = anthropic.Anthropic()
    model = az.pick_model(client, "claude-sonnet-5")
    try:
        resp = az.create_message(
            client, model=model, max_tokens=3000,
            messages=[{"role": "user", "content": [
                {"type": "image", "source": {"type": "base64",
                                             "media_type": "image/jpeg", "data": b64}},
                {"type": "text", "text": prompt},
            ]}])
    except Exception as exc:
        print(f"  מודל הראייה נכשל: {exc}")
        return None
    try:
        t_in, t_out, cw, cr = az.usage_of(resp)
        az.log_usage(ROOT, model, t_in, t_out, "thumb", cw, cr)
    except Exception:
        pass
    return parse_vision(az.extract_json(az.response_text(resp)), finalists, quote)


def parse_vision(data, finalists: list, quote: str = ""):
    """
    בודק את תשובת המודל מול מה שבאמת זוהה. אות או פריים שלא קיימים = None.
    מחזיר {"frame", "streamer", "other_frame", "other", "quote"} (אינדקסים 0-based).
    מקבל גם את הפורמט הישן ({"frame": n, ...}) - אז שני הצדדים מאותו פריים.
    """
    if not isinstance(data, dict):        # תשובה בלי JSON - לא סיבה להפיל תמנייל
        return None

    def frame_of(key, fallback=None):
        v = data.get(key, fallback)
        try:
            n = int(v)
        except (TypeError, ValueError):
            return None
        return n - 1 if 1 <= n <= len(finalists) else None

    def letter(key, fi):
        v = data.get(key)
        if fi is None or not isinstance(v, str) or len(v.strip()) != 1:
            return None
        faces = finalists[fi].get("faces") or []
        i = LETTERS.find(v.strip().upper())
        return i if 0 <= i < len(faces) else None

    sf = frame_of("streamer_frame", data.get("frame", data.get("pick")))
    if sf is None:
        return None
    of = frame_of("other_frame", data.get("frame")) if data.get("other") else None
    s, o = letter("streamer", sf), letter("other", of)
    if o is not None and of == sf and o == s:
        o = None
    if o is None:
        of = None
    sq = str(data.get("short_quote") or "").strip().strip('"״“”')
    # רק מילים שבאמת נאמרו - המודל לא ממציא ציטוט
    if sq and (len(sq.split()) > 6 or not all(w in (quote or "") for w in sq.split())):
        sq = ""
    print(f"  המודל: סטרימר={data.get('streamer')} בפריים {sf + 1}"
          + (f", מולו={data.get('other')} בפריים {of + 1}" if of is not None else ", אין מולו")
          + f" · {str(data.get('why', ''))[:60]}")
    return {"frame": sf, "streamer": s, "other_frame": of, "other": o, "quote": sq}


# -------------------------------------------------- זיכרון מצלמה לסטרימר
# המצלמה של סטרימר יושבת בדרך כלל באותו מקום בכל לייב. כשהמודל מזהה
# אותו, שומרים את מרכז הפנים (יחסית לגודל המסך). בלי מודל (אין מפתח,
# תקלה) - הפנים הכי קרובות למקום השמור הן הסטרימר. בלי זיכרון - כמו
# פעם: הפנים הגדולות, תמונה אחת.

def remember_cam(slug: str, face, iw: int, ih: int) -> None:
    if not slug or not face:
        return
    x, y, w, h = face
    mem = load_json(CAM_FILE, {})
    mem[slug] = {"cx": round((x + w / 2) / iw, 3), "cy": round((y + h / 2) / ih, 3),
                 "w": round(w / iw, 3)}
    try:
        CAM_FILE.write_text(json.dumps(mem, ensure_ascii=False, indent=1), encoding="utf-8")
    except OSError:
        pass


def match_cam(slug: str, faces: list, iw: int, ih: int):
    """אינדקס הפנים שהכי קרובות למקום השמור של הסטרימר, או None."""
    m = load_json(CAM_FILE, {}).get(slug or "")
    if not m or not faces:
        return None
    def dist(f):
        x, y, w, h = f
        return ((x + w / 2) / iw - m["cx"]) ** 2 + ((y + h / 2) / ih - m["cy"]) ** 2
    i = min(range(len(faces)), key=lambda k: dist(faces[k]))
    return i if dist(faces[i]) ** 0.5 < 0.12 else None


# ------------------------------------------------------------------ ראשי

def find_clip(job: Path, idx: int):
    hits = [p for p in sorted((job / "clips").glob(f"{idx:02d} - *.mp4"))
            if "__p" not in p.stem]
    return hits[0] if hits else None


def thumb_path(clip: Path) -> Path:
    return clip.with_name(clip.stem + "__thumb.jpg")


def make_thumbnail(job, idx: int, use_llm: bool = True, at: str = "",
                   keep: bool = False, force: bool = False, theme: str = ""):
    """
    בונה תמנייל לקטע idx. מחזיר את הנתיב, או None.
    אם כבר קיים - מחזיר אותו בלי לבנות מחדש (אלא אם force).
    """
    job = Path(job)
    segs = load_json(job / "audio_segments.json", [])
    if not 1 <= idx <= len(segs):
        print(f"[{idx}] אין קטע כזה")
        return None
    seg = segs[idx - 1]
    clip = find_clip(job, idx)
    if not clip:
        print(f"[{idx}] אין קובץ mp4")
        return None
    out = thumb_path(clip)
    if out.exists() and not force:
        return out

    text = (seg.get("thumb_text") or "").strip()
    if not text:
        # קטע מלפני 14.9. עדיף טקסט קצר מהכותרת מאשר כלום, אבל כדאי
        # להריץ retitle.py שיכתוב טקסט אמיתי.
        words = re.sub(r"[\"'״“”]", "", seg.get("title", "")).split()
        text = " ".join(words[1:4] if len(words) > 3 else words)
        print(f"[{idx}] ⚠ אין thumb_text - לוקח מהכותרת. retitle.py ישפר.")
    meta = load_json(job / "meta.json", {})
    streamer = meta.get("display_name") or ""
    if re.fullmatch(r"[\x00-\x7F]*", streamer or ""):
        streamer = ""                        # slug לא נכתב על תמנייל

    length = duration(clip)
    if length <= 0:
        print(f"[{idx}] ffprobe לא הצליח לקרוא את {clip.name}")
        return None
    center, why = clip_time(job, idx, seg, length, at)
    print(f"[{idx}] {clip.name[:50]}\n  רגע: {center:.0f}ש' ({why})")

    work = job / "clips" / "_thumbwork"
    frames = grab_frames(clip, center, work, length)
    if not frames:
        print("  לא נשלפו פריימים.")
        return None
    finalists = score_frames(frames)
    if not finalists:
        return None
    # 27.9: עוד כמה פריימים עם פנים מכל אורך הקטע, שהמודל ימצא צחוק/עצבים אמיתיים
    if use_llm:
        extra = [f for f in score_frames(grab_spread(clip, work, length), N_SPREAD)
                 if f["face"] and all(abs(f["t"] - x["t"]) >= 2.0 for x in finalists)]
        finalists += extra[:N_SPREAD_KEEP]
    faces = sum(1 for f in finalists if f["face"])
    print(f"  {len(frames)} פריימים, {len(finalists)} ניצולים, {faces} עם פנים"
          + ("" if cv2 is not None else "  (אין OpenCV - בלי זיהוי פנים)"))

    slug = meta.get("streamer") or ""
    full_quote = (seg.get("quote") or "").strip()
    pick = (ask_vision(finalists, text, seg.get("title", ""), streamer, full_quote,
                       mood(seg)) if use_llm else None)
    best = finalists[pick["frame"] if pick else 0]
    frame = Image.open(best["path"]).convert("RGB")
    faces = best.get("faces") or ([best["face"]] if best["face"] else [])

    # מי הסטרימר ומי מולו. 1) המודל  2) זיכרון המצלמה  3) כמו פעם
    s_i = pick["streamer"] if pick else None
    o_i = pick["other"] if pick else None
    how = "מודל"
    if s_i is None:
        s_i = match_cam(slug, faces, frame.width, frame.height)
        how = "זיכרון מצלמה" if s_i is not None else ""
        if s_i is not None and o_i is None:
            others = [k for k in range(len(faces)) if k != s_i
                      and faces[k][2] * faces[k][3] >= 0.015 * frame.width * frame.height]
            o_i = others[0] if others else None
    elif slug:
        remember_cam(slug, faces[s_i], frame.width, frame.height)

    if s_i is not None:
        face = faces[s_i]
        other = None
        other_img = None
        if o_i is not None:
            of = pick.get("other_frame") if pick else None
            if of is not None and of != pick["frame"]:
                ofr = finalists[of]
                other_img = Image.open(ofr["path"]).convert("RGB")
                other = ofr["faces"][o_i]
            else:
                other = faces[o_i]
        content = None if other else content_region(frame, face)
    else:                                    # לא יודעים מי הסטרימר - תמונה אחת
        face, other, content, other_img = best["face"], None, None, None

    quote = (pick or {}).get("quote") or short_quote(full_quote)
    kind = ("סטרימר + " + ("פנים שמולו" if other else "תוכן מהמסך")) if (other or content) \
        else "תמונה אחת"
    print(f"  פריסה: {kind}" + (f" ({how})" if how else "")
          + (f"  ·  ציטוט: {quote}" if quote else ""))
    compose(frame, face, text, seg.get("thumb_emphasis", ""), streamer, out,
            other=other, content_box=content, quote=quote, theme=theme,
            other_frame=other_img)
    if not keep:
        shutil.rmtree(work, ignore_errors=True)
    print(f"  נשמר: {out.name}")
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("job", help="תיקיית העבודה")
    ap.add_argument("idx", nargs="?", type=int, default=0)
    ap.add_argument("--all", action="store_true", help="כל הקטעים שיש להם קליפ")
    ap.add_argument("--no-llm", action="store_true", help="בלי קריאת ראייה")
    ap.add_argument("--at", default="", help="HH:MM:SS בשידור, במקום quote_at")
    ap.add_argument("--keep", action="store_true", help="להשאיר את הפריימים שנבחנו")
    ap.add_argument("--force", action="store_true", help="לבנות מחדש גם אם קיים")
    ap.add_argument("--theme", default="", choices=[""] + list(THEMES),
                    help="ערכת צבע. ברירת מחדל: thumb_theme ב-youtube.json, אחרת brand")
    args = ap.parse_args()

    job = Path(args.job)
    if not job.is_dir():
        job = ROOT / args.job
    if not job.is_dir():
        sys.exit(f"לא נמצאה תיקייה: {args.job}")

    if args.all:
        segs = load_json(job / "audio_segments.json", [])
        targets = [i for i in range(1, len(segs) + 1) if find_clip(job, i)]
    elif args.idx:
        targets = [args.idx]
    else:
        sys.exit("צריך מספר קטע או --all")

    if not RAQM:
        print("(Pillow בלי raqm - העברית מסודרת כאן, מילה-מילה)")
    # 25.9: send_backlog הריץ --all על 5 עבודות ולא נוצר אף תמנייל. בכל
    # עבודה נשלפו פריימים לקליפ הראשון ואז השקט - חריגה אחת עצרה את כל
    # הלולאה, והשגיאה נשארה רק בחלון ה-cmd. עכשיו כל קליפ לבד, והשגיאה
    # נכתבת גם ל-clips\_thumb_errors.log כדי שאפשר יהיה לקרוא אותה אחר כך.
    made, failed = 0, 0
    for i in targets:
        try:
            if make_thumbnail(job, i, use_llm=not args.no_llm, at=args.at,
                              keep=args.keep, force=args.force or not args.all,
                              theme=args.theme):
                made += 1
            else:
                failed += 1
        except Exception:
            import traceback
            from datetime import datetime
            failed += 1
            tb = traceback.format_exc()
            print(f"[{i}] ✗ נכשל:\n{tb}")
            try:
                with open(job / "clips" / "_thumb_errors.log", "a", encoding="utf-8") as fh:
                    fh.write(f"=== {datetime.now():%Y-%m-%d %H:%M:%S}  קטע {i}\n{tb}\n")
            except OSError:
                pass
    print(f"\nתמניילים: {made} נוצרו, {failed} נכשלו"
          + ("  (פירוט: clips\\_thumb_errors.log)" if failed else ""))


if __name__ == "__main__":
    main()
