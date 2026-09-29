"""
hook.py - cold open: כמה שניות מהרגע הכי חזק, לפני תחילת הקטע (משימה 37)

למה: 40% מהצופים עוזבים ב-30 השניות הראשונות. הקטע עצמו חייב להתחיל
במקום מובן (אחרת לא מבינים על מה מדברים), אבל המקום המובן הוא כמעט
אף פעם לא המקום המעניין. אז פותחים ב-6-15 שניות מהשיא, ורק אז
הקטע מההתחלה - כמו כל ערוץ קליפים שעורך ביד.

מאיפה בא הרגע:
    1. `cold_open` - טקסט שהמנתח בחר במיוחד לזה (analyze13, מ-28.9).
       `cold_open_at` הוא הזמן המשוער.
    2. אחרת `quote` + `quote_at` - המשפט החזק שנבחר לתמנייל. כך גם
       קטעים שנותחו לפני 28.9 מקבלים פתיחה אם חותכים אותם מחדש.

איך מוצאים את השניות המדויקות:
    שורות התמלול ארוכות (30-110 שניות), ו-quote_at הוא בדרך כלל
    תחילת השורה ולא הרגע שבו המשפט נאמר. לכן שני שלבים:
    א. גס - מחפשים את הטקסט בתמלול סביב הזמן, ומעריכים את הזמן לפי
       המיקום היחסי של הטקסט בשורה. סטייה של כמה שניות.
    ב. מדויק - מתמללים מחדש ~40 שניות סביב המקום, עם זמן לכל מילה
       (כמו subtitle.py), ומוצאים את המשפט במילים. אם faster-whisper
       לא זמין - נשארים עם הגס, מרופד ומיושר לשקט, ומדווחים.

מתי לא עושים cold open (והקליפ נחתך כרגיל):
    - קליפ קצר מ-3 דקות.
    - הרגע נופל ב-40 השניות הראשונות של הקליפ - הוא ממילא בפתיחה.
    - הטקסט לא נמצא בתמלול, או שהוא לא בתוך הטווחים שנחתכים.
"""

import os
import re
import json
import tempfile
import subprocess
from difflib import SequenceMatcher
from pathlib import Path

MIN_LEN = 6.0          # שניות. פחות מזה - הצופה לא קולט מה שמע
MAX_LEN = 15.0         # יותר מזה - זה כבר לא טיזר, זה ספוילר
MIN_CLIP = 180.0       # קליפ קצר יותר לא צריך טיזר
MIN_OFFSET = 40.0      # הרגע כבר בפתיחה - אין מה להקדים
FADE = 0.25            # מעבר בין הטיזר לקטע (cut3.concat)
ROUGH_MIN_SCORE = 0.62
WORDS_MIN_SCORE = 0.55
ALIGN_PAD = 14.0       # כמה שניות לתמלל מכל צד של ההערכה הגסה


# ------------------------------------------------------------------ טקסט

FINALS = str.maketrans("ךםןףץ", "כמנפצ")


def norm(text: str) -> str:
    """להשוואה בלבד: בלי ניקוד, פיסוק ואותיות סופיות."""
    text = re.sub(r"[֑-ׇ]", "", str(text or ""))
    text = text.translate(FINALS)
    text = re.sub(r"[^\w\s]", " ", text)
    return re.sub(r"\s+", " ", text).strip().lower()


def to_seconds(t) -> float:
    if isinstance(t, (int, float)):
        return float(t)
    s = str(t).strip().split(" ")[0]
    parts = [float(x) for x in s.split(":")]
    while len(parts) < 3:
        parts.insert(0, 0.0)
    return parts[0] * 3600 + parts[1] * 60 + parts[2]


# ------------------------------------------------------ שלב א: חיפוש גס

def rough_locate(rows: list, text: str, anchor, lo: float, hi: float):
    """
    מחפש את text בתמלול, בחלון סביב anchor (או בכל [lo, hi] אם אין).
    מחזיר (התחלה, סוף, ציון) - הזמנים מוערכים לפי מיקום התו בשורה.
    """
    q = norm(text)
    if len(q) < 8 or not rows:
        return None
    if anchor is not None:
        frm, to = max(lo, anchor - 90), min(hi, anchor + 150)
        if to <= frm:
            frm, to = lo, hi
    else:
        frm, to = lo, hi

    stream, times = [], []
    for r in rows:
        a, b = float(r.get("start", 0)), float(r.get("end", 0))
        if b < frm - 5 or a > to + 5:
            continue
        t = norm(r.get("text"))
        if not t:
            continue
        dur = max(0.01, b - a)
        for k, ch in enumerate(t):
            stream.append(ch)
            times.append(a + dur * (k / len(t)))
        stream.append(" ")
        times.append(b)
    s = "".join(stream)
    n = len(q)
    if len(s) < n // 2:
        return None

    def score_at(i):
        return SequenceMatcher(None, s[i:i + n], q, autojunk=False).ratio()

    step = max(1, n // 8)
    best_i, best = 0, -1.0
    for i in range(0, max(1, len(s) - n + 1), step):
        sc = score_at(i)
        if sc > best:
            best_i, best = i, sc
    for i in range(max(0, best_i - step), min(len(s) - n + 1, best_i + step + 1)):
        sc = score_at(i)
        if sc > best:
            best_i, best = i, sc
    if best < ROUGH_MIN_SCORE:
        return None
    end_i = min(len(times) - 1, best_i + n - 1)
    return times[best_i], times[end_i], round(best, 2)


# --------------------------------------------------- שלב ב: מילים מדויקות

class Aligner:
    """
    תמלול קצר עם זמן לכל מילה. נטען פעם אחת לכל ריצה של cut3.

    GPU רק אם המנעול פנוי ברגע זה (gpulock). אם לייב אחר באמצע תמלול -
    לא מחכים לו (זה יכול להיות שעה, והזמן עד האוויר הוא מדד מרכזי),
    אלא עוברים ל-CPU. ~40 שניות אודיו לקטע - סביר גם שם.
    """

    def __init__(self, model_name: str = "large-v3", log=print):
        self.model_name = model_name
        self.log = log
        self.model = None
        self.failed = ""
        self.locked = False
        self.device = ""

    def _load(self) -> bool:
        if self.model is not None:
            return True
        if self.failed:
            return False
        try:
            register_cuda_dlls()
            from faster_whisper import WhisperModel
        except Exception as exc:
            self.failed = f"faster-whisper לא זמין ({str(exc)[:60]})"
            return False
        try:
            import gpulock
            self.locked = gpulock._try_acquire("cut3 cold open")
        except Exception:
            self.locked = False
        try:
            if self.locked:
                self.model = WhisperModel(self.model_name, device="cuda",
                                          compute_type="int8_float16")
                self.device = "GPU"
            else:
                raise RuntimeError("GPU תפוס")
        except Exception as exc:
            self.release()
            try:
                self.model = WhisperModel(self.model_name, device="cpu",
                                          compute_type="int8")
                self.device = "CPU"
                self.log(f"   cold open: מיישר מילים על CPU ({str(exc)[:40]})")
            except Exception as exc2:
                self.failed = f"לא נטען מודל ({str(exc2)[:60]})"
                return False
        return True

    def words(self, audio: Path, a: float, b: float, prompt: str = "") -> list:
        """[(start, end, word), ...] בזמנים מוחלטים של השידור."""
        if not self._load():
            return []
        tmp = Path(tempfile.gettempdir()) / f"cold_open_{os.getpid()}.wav"
        try:
            r = subprocess.run(
                ["ffmpeg", "-hide_banner", "-nostdin", "-v", "error", "-y",
                 "-ss", f"{max(0.0, a):.2f}", "-to", f"{b:.2f}", "-i", str(audio),
                 "-ac", "1", "-ar", "16000", str(tmp)],
                capture_output=True, text=True, encoding="utf-8", errors="replace")
            if r.returncode != 0 or not tmp.exists():
                return []
            try:
                return self._run(tmp, a, prompt)
            except Exception as exc:
                if self.device != "GPU":
                    raise
                # ה-GPU נטען אבל נופל בזמן ריצה (DLL חסר וכו') - עוברים ל-CPU
                # לכל שאר הריצה, במקום טיזר גס לכל הקטעים.
                self.log(f"   cold open: GPU נכשל ({str(exc)[:60]}), עובר ל-CPU")
                self.model = None
                self.release()
                from faster_whisper import WhisperModel
                self.model = WhisperModel(self.model_name, device="cpu", compute_type="int8")
                self.device = "CPU"
                return self._run(tmp, a, prompt)
        except Exception as exc:
            self.log(f"   cold open: התמלול הקצר נכשל ({str(exc)[:60]})")
            return []
        finally:
            tmp.unlink(missing_ok=True)

    def _run(self, tmp: Path, a: float, prompt: str) -> list:
        segs, _ = self.model.transcribe(
            str(tmp), language="he", word_timestamps=True,
            vad_filter=False, beam_size=1,
            # הטקסט הצפוי מטה את הזיהוי לכיוון הנכון. זה לא ממציא
            # מילים - הזמנים עדיין מהאודיו.
            initial_prompt=prompt[:200] or None)
        out = []
        base = max(0.0, a)
        for sg in segs:                  # גנרטור - השגיאה של cublas קופצת כאן
            for w in (getattr(sg, "words", None) or []):
                out.append((base + float(w.start), base + float(w.end), str(w.word)))
        return out

    def release(self):
        if self.locked:
            try:
                import gpulock
                if gpulock.read_lock().get("pid") == os.getpid():
                    gpulock.LOCK.unlink(missing_ok=True)
            except Exception:
                pass
            self.locked = False

    def close(self):
        self.model = None
        self.release()


def register_cuda_dlls() -> None:
    """כמו ב-transcribe5 ו-subtitle: בווינדוס ה-DLL של CUDA לא נמצאים לבד."""
    if os.name != "nt":
        return
    try:
        import nvidia
    except ImportError:
        return
    for base in [Path(p) for p in getattr(nvidia, "__path__", [])]:
        for sub in ("cublas", "cudnn", "cuda_runtime", "cuda_nvrtc"):
            d = base / sub / "bin"
            if d.is_dir():
                try:
                    os.add_dll_directory(str(d))
                except Exception:
                    pass
                # כמו transcribe5: add_dll_directory לבד לא מספיק ל-ctranslate2,
                # שטוען את cublas בזמן התמלול דרך PATH. בלי זה (28.9, אוהד #2):
                # "cublas64_12.dll is not found" והטיזר נחתך גס.
                if str(d) not in os.environ.get("PATH", ""):
                    os.environ["PATH"] = str(d) + os.pathsep + os.environ.get("PATH", "")


def match_words(words: list, text: str):
    """מוצא את text ברצף המילים. מחזיר (i, j, ציון) - אינדקסים כוללים."""
    target = norm(text).split()
    toks = [norm(w[2]) for w in words]
    if not target or not toks:
        return None
    q = " ".join(target)
    n = len(target)
    best = (0, 0, -1.0)
    for size in range(max(1, int(n * 0.7)), int(n * 1.3) + 2):
        for i in range(0, max(1, len(toks) - size + 1)):
            cand = " ".join(t for t in toks[i:i + size] if t)
            sc = SequenceMatcher(None, cand, q, autojunk=False).ratio()
            if sc > best[2]:
                best = (i, min(len(toks) - 1, i + size - 1), sc)
    if best[2] < WORDS_MIN_SCORE:
        return None
    return best[0], best[1], round(best[2], 2)


def shape_words(words: list, i: int, j: int):
    """
    גבולות לפי מילים: מאריך אחורה עד MIN_LEN (משפט שמתחיל לפני השיא
    נותן לו הקשר), מקצר עד MAX_LEN, ונחתך בין מילים ולא בתוכן.
    """
    while i > 0 and words[j][1] - words[i][0] < MIN_LEN:
        if words[j][1] - words[i - 1][0] > MAX_LEN:
            break
        i -= 1
        # הפסקה של חצי שנייה לפני המילה = כנראה תחילת משפט. אם כבר יש
        # 4.5 שניות, עוצרים שם ולא נכנסים לאמצע המשפט הקודם.
        if i > 0 and words[i][0] - words[i - 1][1] > 0.5 \
                and words[j][1] - words[i][0] >= MIN_LEN - 1.5:
            break
    while j > i and words[j][1] - words[i][0] > MAX_LEN:
        j -= 1
    start = words[i][0] - 0.25
    if i > 0:
        start = max(start, words[i - 1][1] + 0.02)
    end = words[j][1] + 0.45
    if j + 1 < len(words):
        end = min(end, max(words[j][1] + 0.1, words[j + 1][0] - 0.02))
    return start, end


def shape_rough(s: float, e: float, silences=None):
    """בלי מילים: ריפוד נדיב, ואז יישור לשקט כדי לא לחתוך באמצע מילה."""
    start, end = s - 1.2, e + 1.2
    if end - start < MIN_LEN:
        start = end - MIN_LEN
    if end - start > MAX_LEN:
        end = start + MAX_LEN
    if silences:
        spans = silences(start - 2.5, end + 3.0) or []
        # התחלה: סוף השקט האחרון שלפני start (עד 2.5 שניות אחורה)
        before = [b for a, b in spans if b <= start + 0.3 and b >= start - 2.5]
        if before:
            start = max(before) - 0.1
        # סוף: תחילת השקט הראשון אחרי end (עד 3 שניות קדימה)
        after = [a for a, b in spans if a >= end - 0.3 and a <= end + 3.0]
        if after:
            end = min(after) + 0.3
    return start, end


# --------------------------------------------------------------- ראשי

def clip_offset(ranges: list, t: float):
    """הזמן בתוך הקליפ של רגע t בשידור, או None אם הוא מחוץ לטווחים."""
    off = 0.0
    for a, b in ranges:
        if a - 0.5 <= t <= b + 0.5:
            return off + max(0.0, t - a)
        off += b - a
    return None


def inside(ranges: list, s: float, e: float) -> bool:
    return any(a - 0.5 <= s and e <= b + 0.5 for a, b in ranges)


def candidates(seg: dict) -> list:
    out = []
    if seg.get("cold_open"):
        out.append(("cold_open", seg["cold_open"], seg.get("cold_open_at")))
    if seg.get("quote"):
        out.append(("quote", seg["quote"], seg.get("quote_at")))
    return out


def find(seg: dict, rows: list, ranges: list, audio: Path = None,
         aligner: Aligner = None, silences=None) -> dict:
    """
    ranges = הטווחים המלוטשים של הקליפ [(s, e), ...] בזמני השידור.
    מחזיר {"start", "end", "text", "source", "how", "why"}; start=None = אין.
    """
    none = lambda why: {"start": None, "end": None, "why": why}
    if not ranges:
        return none("אין טווחים")
    total = sum(b - a for a, b in ranges)
    if total < MIN_CLIP:
        return none(f"קליפ קצר ({total / 60:.1f} דק')")
    cands = candidates(seg)
    if not cands:
        return none("אין cold_open ואין quote")

    lo, hi = ranges[0][0], ranges[-1][1]
    reasons = []
    for source, text, at in cands:
        try:
            anchor = to_seconds(at) if at else None
        except ValueError:
            anchor = None
        rough = rough_locate(rows, text, anchor, lo, hi)
        if not rough:
            reasons.append(f"{source}: לא נמצא בתמלול")
            continue
        rs, re_, rscore = rough

        start = end = None
        how = ""
        if aligner and audio and Path(audio).exists():
            words = aligner.words(Path(audio), rs - ALIGN_PAD, re_ + ALIGN_PAD, prompt=text)
            m = match_words(words, text) if words else None
            if m:
                start, end = shape_words(words, m[0], m[1])
                how = f"מילים ({aligner.device}, התאמה {m[2]})"
            elif aligner.failed:
                how = f"גס - {aligner.failed}"
            else:
                how = "גס - המשפט לא נמצא במילים"
        if start is None:
            start, end = shape_rough(rs, re_, silences)
            how = how or "גס"
            how += f" (התאמה {rscore})"

        if not inside(ranges, start, end):
            reasons.append(f"{source}: מחוץ לטווחי הקליפ")
            continue
        off = clip_offset(ranges, start)
        if off is not None and off < MIN_OFFSET:
            reasons.append(f"{source}: כבר בפתיחה ({off:.0f}ש')")
            continue
        return {"start": round(start, 2), "end": round(end, 2), "text": text,
                "source": source, "how": how, "why": ""}
    return none("; ".join(reasons))
