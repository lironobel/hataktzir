"""
transcribe3.py - תמלול עברית עם חותמות זמן, על GPU, עמיד לקבצים ארוכים

מחלק אודיו ארוך לנתחים ומתמלל כל אחד בנפרד, כך שצריכת הזיכרון קבועה
בלי קשר לאורך השידור. גם נופל בחזרה לאצווה קטנה יותר אם נגמר זיכרון הכרטיס.

שימוש:
    python transcribe3.py audio.mp3
    python transcribe3.py audio.mp3 --chunk 30        (נתחים של 30 דקות)
    python transcribe3.py audio.mp3 --batch 4         (אצווה קטנה יותר)
    python transcribe3.py audio.mp3 --device cpu      (בלי כרטיס)
"""

import gc
import os
import sys
import json
import time
import shutil
import subprocess
import argparse
from pathlib import Path


def register_cuda_dlls() -> None:
    if os.name != "nt":
        return
    try:
        import nvidia
    except ImportError:
        return
    roots = [Path(p) for p in getattr(nvidia, "__path__", [])]
    added = 0
    for base in roots:
        for sub in ("cublas", "cudnn", "cuda_runtime", "cuda_nvrtc"):
            bin_dir = base / sub / "bin"
            if bin_dir.is_dir():
                os.add_dll_directory(str(bin_dir))
                os.environ["PATH"] = str(bin_dir) + os.pathsep + os.environ.get("PATH", "")
                added += 1
    print(f"ספריות CUDA שנרשמו: {added}", flush=True)


register_cuda_dlls()

from faster_whisper import WhisperModel  # noqa: E402

try:
    from faster_whisper import BatchedInferencePipeline
    HAVE_BATCHED = True
except ImportError:
    HAVE_BATCHED = False


def hms(seconds: float) -> str:
    s = int(seconds)
    return f"{s // 3600:02d}:{(s % 3600) // 60:02d}:{s % 60:02d}"


def audio_duration(path: Path) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "csv=p=0", str(path)],
        capture_output=True, text=True,
    )
    try:
        return float(out.stdout.strip())
    except ValueError:
        return 0.0


def split_audio(path: Path, chunk_s: int, work_dir: Path) -> tuple:
    """
    חותך לנתחים בלי קידוד מחדש. מחזיר ([(קובץ, היסט בשניות), ...], [היסטים שנכשלו]).
    67/2 (29.9): נתח ש-ffmpeg לא הצליח לחתוך נעלם עד היום בשקט - שעה חסרה.
    """
    work_dir.mkdir(exist_ok=True)
    total = audio_duration(path)
    pieces = []
    missing = []
    idx = 0
    offset = 0.0
    while offset < total:
        out = work_dir / f"part{idx:03d}.mp3"
        if not out.exists():
            subprocess.run(
                ["ffmpeg", "-v", "error", "-y",
                 "-ss", str(int(offset)), "-t", str(chunk_s),
                 "-i", str(path), "-c", "copy", str(out)],
                check=False,
            )
        if out.exists() and out.stat().st_size > 1000:
            pieces.append((out, offset))
        elif total - offset > 10:
            # זנב של שניות בודדות יכול לצאת ריק באמת. יותר מזה - חסר.
            missing.append(offset)
        idx += 1
        offset += chunk_s
    return pieces, missing


def transcribe_one(model, pipeline, path: Path, batch: int, beam: int):
    """
    מתמלל קובץ אחד. ה-pipeline נוצר פעם אחת בלבד ומשותף לכל הנתחים -
    יצירה מחדש לכל נתח משאירה זיכרון תפוס על הכרטיס ומפילה את הריצה.
    """
    common = dict(language="he", beam_size=beam)

    if pipeline is not None:
        for size in (batch, max(2, batch // 2), 1):
            try:
                segments, info = pipeline.transcribe(str(path), batch_size=size, **common)
                out = list(segments)
                gc.collect()
                return out, info, f"batch={size}"
            except Exception as exc:
                msg = str(exc).lower()
                gc.collect()
                if "memory" in msg or "cuda" in msg or "alloc" in msg:
                    print(f"    אין מספיק זיכרון ב-batch={size}, מנסה קטן יותר", flush=True)
                    continue
                raise

    segments, info = model.transcribe(
        str(path),
        vad_filter=True,
        vad_parameters={"min_silence_duration_ms": 500},
        **common,
    )
    out = list(segments)
    gc.collect()
    return out, info, "ללא אצוות"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("audio")
    ap.add_argument("--model", default="large-v3")
    ap.add_argument("--compute", default="int8_float16")
    ap.add_argument("--batch", type=int, default=8)
    ap.add_argument("--beam", type=int, default=1)
    ap.add_argument("--device", default="cuda")
    ap.add_argument("--chunk", type=int, default=60, help="אורך נתח בדקות")
    ap.add_argument("--keep-parts", action="store_true", help="לא למחוק את הנתחים הזמניים")
    args = ap.parse_args()

    audio_path = Path(args.audio)
    if not audio_path.exists():
        print(f"לא נמצא: {audio_path}")
        sys.exit(1)

    total_s = audio_duration(audio_path)
    print(f"אורך האודיו: {hms(total_s)}", flush=True)
    print(f"מודל={args.model}  compute={args.compute}  device={args.device}  beam={args.beam}", flush=True)

    try:
        model = WhisperModel(args.model, device=args.device, compute_type=args.compute)
    except Exception as exc:
        print(f"טעינה על {args.device} נכשלה ({exc}). עובר ל-CPU.", flush=True)
        model = WhisperModel(args.model, device="cpu", compute_type="int8")

    pipeline = None
    if HAVE_BATCHED and args.device == "cuda":
        try:
            pipeline = BatchedInferencePipeline(model=model)
        except Exception as exc:
            print(f"אצוות לא זמין ({exc}), ממשיך במצב רגיל.", flush=True)

    chunk_s = args.chunk * 60
    work_dir = audio_path.parent / "_parts"

    if total_s > chunk_s * 1.2:
        print(f"מחלק לנתחים של {args.chunk} דקות...", flush=True)
        pieces, missing = split_audio(audio_path, chunk_s, work_dir)
        print(f"{len(pieces)} נתחים.\n", flush=True)
    else:
        pieces, missing = [(audio_path, 0.0)], []

    started = time.time()
    rows = []
    # 67/2: נתח שנכשל = שעה שחסרה בתמלול. עד 29.9 זה היה continue, קוד יציאה 0,
    # ו-_parts נמחקה - השעה נעלמה לתמיד, והניתוח רץ כאילו כלום.
    failed = [f"{hms(o)} (החיתוך לנתחים נכשל)" for o in missing]

    for i, (piece, offset) in enumerate(pieces, 1):
        label = f"[{i}/{len(pieces)}] {hms(offset)}"
        cache = piece.with_suffix(".json")

        if cache.exists():
            cached = json.loads(cache.read_text(encoding="utf-8"))
            rows.extend(cached)
            print(f"{label} כבר תומלל ({len(cached)} מקטעים), מדלג.", flush=True)
            continue

        print(f"{label} מתמלל...", end=" ", flush=True)
        t0 = time.time()
        try:
            segments, info, mode = transcribe_one(
                model, pipeline, piece, args.batch, args.beam
            )
        except Exception as exc:
            print(f"נכשל: {exc}", flush=True)
            failed.append(f"{hms(offset)} ({str(exc)[:80]})")
            continue

        chunk_rows = []
        for seg in segments:
            text = seg.text.strip()
            if not text:
                continue
            chunk_rows.append({
                "start": seg.start + offset,
                "end": seg.end + offset,
                "text": text,
            })

        # נשמר מיד, כך שריצה חוזרת לא מתחילה מאפס
        cache.write_text(json.dumps(chunk_rows, ensure_ascii=False), encoding="utf-8")
        rows.extend(chunk_rows)

        took = time.time() - t0
        print(f"{len(chunk_rows)} מקטעים, {took / 60:.1f} דק\' ({mode})", flush=True)

    elapsed = time.time() - started
    ratio = total_s / elapsed if elapsed else 0

    print("-" * 60, flush=True)
    print(f"סיים ב-{elapsed / 60:.1f} דקות. {len(rows)} מקטעים.", flush=True)
    print(f"מהירות: פי {ratio:.1f} מזמן אמת.", flush=True)

    if not rows:
        print("לא הופק תמלול. לא כותב קבצים.", flush=True)
        sys.exit(1)

    if failed:
        # לא כותבים תמלול חלקי ולא מוחקים את _parts: הנתחים שהצליחו שמורים
        # כ-partNNN.json, וניסיון חוזר (המנטר / /retry) מתמלל רק את מה שחסר.
        print(f"⚠ {len(failed)} נתחים לא תומללו: {', '.join(failed)}", flush=True)
        print("לא כותב תמלול חלקי. ניסיון חוזר ימשיך מהנתחים שחסרים.", flush=True)
        sys.exit(3)

    rows.sort(key=lambda r: r["start"])
    stem = audio_path.stem

    with Path(f"{stem}_transcript.txt").open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(f"[{hms(r['start'])}] {r['text']}\n")
    with Path(f"{stem}_transcript.json").open("w", encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False, indent=1)

    print(f"נשמר: {stem}_transcript.txt / .json", flush=True)

    if work_dir.exists() and not args.keep_parts:
        shutil.rmtree(work_dir, ignore_errors=True)
        print("נתחים זמניים נמחקו.", flush=True)


if __name__ == "__main__":
    main()
