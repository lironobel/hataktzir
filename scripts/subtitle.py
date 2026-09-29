"""
subtitle.py - כתוביות בעברית לקליפ חתוך: קובץ SRT, ואפשר גם לצרוב

למה לא משתמשים בתמלול שכבר יש: השורות שלו ארוכות (30-110 שניות) -
טובות לניתוח, חסרות תועלת ככתוביות. כאן מתמללים את הקליפ עצמו מחדש
עם חותמות זמן לכל מילה. קליפ של 20 דקות לוקח כ-3 דקות על ה-GPU.

שימוש (מתוך תיקיית העבודה או עם נתיב מלא):
    python ..\\..\\scripts\\subtitle.py "clips\\01 - שם הקליפ.mp4"
    python ..\\..\\scripts\\subtitle.py "clips\\01 - שם הקליפ.mp4" --burn
    python ..\\..\\scripts\\subtitle.py clips --all            כל תיקיית הקליפים
    python ..\\..\\scripts\\subtitle.py clips --all --burn

פלט:
    <קליפ>.srt                 ליד הווידאו. יוטיוב מקבל אותו כקובץ כתוביות
    <קליפ>__sub.mp4            עם --burn: עותק עם הכתוביות צרובות

הערה ליוטיוב: בדרך כלל עדיף להעלות את ה-SRT כקובץ כתוביות ולא לצרוב -
הצופה יכול לכבות, ויוטיוב מאנדקס את הטקסט. צריבה שווה לטיקטוק/שורטס.
"""

import os
import sys
import argparse
import subprocess
from pathlib import Path

# ---------------------------------------------------- CUDA כמו ב-transcribe5

def register_cuda_dlls() -> None:
    if os.name != "nt":
        return
    try:
        import nvidia
    except ImportError:
        return
    roots = [Path(p) for p in getattr(nvidia, "__path__", [])]
    for base in roots:
        for sub in ("cublas", "cudnn", "cuda_runtime", "cuda_nvrtc"):
            bin_dir = base / sub / "bin"
            if bin_dir.is_dir():
                try:
                    os.add_dll_directory(str(bin_dir))
                except Exception:
                    pass


# --------------------------------------------------------------- בניית SRT

MAX_CHARS = 34        # לשורה. קצר, כי עברית על מסך טלפון
MAX_SECONDS = 4.0     # אורך תצוגה מרבי לשורה
GAP_BREAK = 0.7       # שתיקה שמתחילה שורה חדשה


def stamp(t: float) -> str:
    ms = int(round(t * 1000))
    h, ms = divmod(ms, 3600000)
    m, ms = divmod(ms, 60000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def words_to_cues(words: list) -> list:
    """מקבץ מילים לשורות כתוביות. words: [(start, end, text), ...]"""
    cues = []
    cur, cur_start, cur_end = [], None, None

    def flush():
        nonlocal cur, cur_start, cur_end
        if cur:
            cues.append((cur_start, cur_end, " ".join(cur)))
        cur, cur_start, cur_end = [], None, None

    for start, end, text in words:
        text = text.strip()
        if not text:
            continue
        if cur:
            too_long = len(" ".join(cur)) + 1 + len(text) > MAX_CHARS
            too_slow = end - cur_start > MAX_SECONDS
            gap = start - cur_end > GAP_BREAK
            ends_sentence = cur[-1][-1:] in ".?!"
            if too_long or too_slow or gap or (ends_sentence and len(" ".join(cur)) > 12):
                flush()
        if not cur:
            cur_start = start
        cur.append(text)
        cur_end = end
    flush()

    # שורה לא תיעלם לפני שאפשר לקרוא אותה
    fixed = []
    for i, (a, b, t) in enumerate(cues):
        min_dur = max(0.8, len(t) * 0.045)
        b = max(b, a + min_dur)
        if i + 1 < len(cues):
            b = min(b, cues[i + 1][0] - 0.05)
        fixed.append((a, max(b, a + 0.5), t))
    return fixed


def write_srt(cues: list, path: Path) -> None:
    lines = []
    for i, (a, b, t) in enumerate(cues, 1):
        lines += [str(i), f"{stamp(a)} --> {stamp(b)}", t, ""]
    path.write_text("\n".join(lines), encoding="utf-8-sig")  # BOM עוזר לנגנים ישנים


# ------------------------------------------------------------------ תמלול

def transcribe_words(video: Path, model_name: str) -> list:
    register_cuda_dlls()
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        raise SystemExit("חסר faster-whisper. הרץ:  pip install faster-whisper")

    try:
        model = WhisperModel(model_name, device="cuda", compute_type="int8_float16")
    except Exception as exc:
        print(f"CUDA לא זמין ({str(exc)[:60]}), עובר ל-CPU. יהיה איטי.")
        model = WhisperModel(model_name, device="cpu", compute_type="int8")

    segments, info = model.transcribe(
        str(video), language="he",
        word_timestamps=True,
        vad_filter=True,
        beam_size=1,
    )
    words = []
    for seg in segments:
        for w in (seg.words or []):
            words.append((w.start, w.end, w.word))
    print(f"  {len(words)} מילים, {info.duration/60:.0f} דקות")
    return words


# ------------------------------------------------------------------ צריבה

def burn(video: Path, srt: Path) -> Path:
    """צורב כתוביות. עובד דרך שם זמני פשוט כי מסנני ffmpeg נשברים על עברית בנתיב."""
    import shutil as sh
    workdir = video.parent
    tmp_srt = workdir / "_sub_tmp.srt"
    sh.copyfile(srt, tmp_srt)
    out = video.with_name(video.stem + "__sub.mp4")

    style = ("FontName=Segoe UI,FontSize=15,Bold=1,PrimaryColour=&H00FFFFFF,"
             "OutlineColour=&H99000000,BorderStyle=1,Outline=2,Shadow=0,MarginV=28")
    common = ["-i", video.name, "-vf", f"subtitles=_sub_tmp.srt:force_style='{style}'",
              "-c:a", "copy", out.name]

    for vcodec in (["-c:v", "h264_nvenc", "-preset", "p4", "-cq", "23"],
                   ["-c:v", "libx264", "-preset", "fast", "-crf", "21"]):
        cmd = ["ffmpeg", "-v", "error", "-y"] + common[:4] + vcodec + ["-c:a", "copy", out.name]
        rc = subprocess.run(cmd, cwd=workdir).returncode
        if rc == 0:
            break
        print(f"  {vcodec[1]} נכשל, מנסה מקודד אחר..." if vcodec[1] == "h264_nvenc"
              else "  הצריבה נכשלה.")
    tmp_srt.unlink(missing_ok=True)
    return out if out.exists() else None


# ------------------------------------------------------------------ ראשי

def process(video: Path, model_name: str, do_burn: bool) -> None:
    srt = video.with_suffix(".srt")
    print(f"[{video.name}]")
    if srt.exists():
        print("  SRT קיים, מדלג על התמלול.")
    else:
        words = transcribe_words(video, model_name)
        if not words:
            print("  לא זוהה דיבור.")
            return
        cues = words_to_cues(words)
        write_srt(cues, srt)
        print(f"  נשמר: {srt.name}  ({len(cues)} שורות)")
    if do_burn:
        out = burn(video, srt)
        if out:
            print(f"  נצרב: {out.name}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("target", help="קובץ mp4, או תיקייה עם --all")
    ap.add_argument("--all", action="store_true", help="כל קובצי ה-mp4 בתיקייה")
    ap.add_argument("--burn", action="store_true", help="גם לצרוב על הווידאו")
    ap.add_argument("--model", default="large-v3")
    args = ap.parse_args()

    target = Path(args.target)
    if args.all:
        if not target.is_dir():
            print(f"לא תיקייה: {target}")
            sys.exit(1)
        videos = sorted(p for p in target.glob("*.mp4") if "__sub" not in p.name)
        if not videos:
            print("אין קובצי mp4.")
            return
        for v in videos:
            process(v, args.model, args.burn)
    else:
        if not target.exists():
            print(f"לא נמצא: {target}")
            sys.exit(1)
        process(target, args.model, args.burn)


if __name__ == "__main__":
    main()
