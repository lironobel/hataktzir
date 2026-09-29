"""
run10.py - מריץ את כל הצינור על לייב אחד, מקישור VOD בלבד

    VOD -> כתובת -> אודיו -> תמלול -> זיהוי קטעים -> חיתוך -> תיאורים
    עובד על קיק, טוויץ' ויוטיוב.

מה חדש ב-run10:
    תהליכי הבת (תמלול, ניתוח, חיתוך) נפתחים בלי חלון.
    מאז ש-run רץ מנותק מהקונסולה, ווינדוס פתחה לכל תהליך בת חלון
    חדש משלו - וסגירת החלון הזה הרגה את התמלול. אותו באג, רמה אחת
    למטה.

מה היה ב-run9:
    כותב pipeline.pid בתיקיית העבודה, כדי שהמנטר ידע שהצינור עוד רץ
    ולא יפעיל אותו פעם שנייה במקביל.

מה היה ב-run8:
    שלב התמלול עומד בתור ל-GPU (gpulock) - תמלול אחד בכל רגע.
    ב-10.9 חמישה תמלולים רצו במקביל על כרטיס אחד וחנקו את המחשב.

    כל כישלון שולח לטלגרם: איזה שלב, מה הסיבה, ומה לעשות.
    שלב ה-m3u8 מנסה כמה פעמים עם המתנה, כי קיק מעבדת VOD כמה דקות
    אחרי שהוא מופיע ב-API. חמישה לייבים נפלו על זה ב-9.9.

מה תוקן לעומת run4:
    run4 קרא ל-analyze.py ול-cut.py - הגרסאות הראשונות, מלפני שבוע.
    כלומר כל ריצה אוטומטית של המנטר השתמשה בקוד ישן: בלי parts,
    בלי הפרופילים, ובלי ליטוש הגבולות. עכשיו הוא קורא ל-analyze11
    ול-cut3.

שימוש:
    python scripts\\run5.py ronengg 2026-09-05 --vod "https://kick.com/ronengg/videos/..."
    python scripts\\run5.py ronengg 2026-09-05 --resume
    python scripts\\run5.py ronengg 2026-09-05 --no-cut

כל שלב מדלג על עצמו אם הפלט שלו כבר קיים, כך שאפשר להריץ שוב בבטחה.
"""

import os
import sys
import json
import time
import subprocess
import argparse
from pathlib import Path
from datetime import datetime, timezone


def find_root(start: Path) -> Path:
    for candidate in [start, *start.parents]:
        if (candidate / "scripts").is_dir():
            return candidate
    return start


ROOT = find_root(Path(__file__).resolve().parent)
SCRIPTS = ROOT / "scripts"

ANALYZER = "analyze13.py"
CUTTER = "cut3.py"

sys.path.insert(0, str(SCRIPTS))
try:
    from tg import notify
except Exception:
    def notify(text, **kw):
        return False

# מה אומרים למשתמש על כל סוג כישלון. זה מה שמגיע לטלפון.
HINTS = {
    "no_m3u8":        "קיק לא מסרה כתובת הזרמה. לרוב ה-VOD עדיין בעיבוד - המנטר ינסה שוב לבד.\n"
                      "בדיקה ידנית: <code>python scripts\\kickurl2.py {vod} --debug</code>",
    "vod_processing": "ה-VOD קיים אבל קיק עדיין מעבדת אותו. ינוסה שוב אוטומטית.",
    "vod_not_found":  "ה-VOD לא קיים - נמחק או שהקישור שגוי. לא ינוסה שוב.",
    "audio_download": "yt-dlp לא הצליח להוריד את האודיו. אולי הכתובת פגה. <code>/retry {job}</code> יביא כתובת טרייה.",
    "transcribe":     "התמלול נפל. לבדוק: <code>nvidia-smi</code> (כרום פתוח? VRAM תפוס?), מקום בדיסק, וקובץ האודיו.",
    "analyze":        "הניתוח נפל. אם ההודעה למעלה מדברת על יתרה - "
                      "console.anthropic.com → Plans &amp; Billing.\n"
                      "אחרת: התשובות הגולמיות ב-<code>raw_responses</code>.",
    "cut":            "החיתוך נפל. לרוב הכתובת פגה - <code>/retry {job}</code>.",
    "describe":       "התיאורים נפלו. הקליפים מוכנים, רק בלי טקסט. אפשר להריץ describe.py ידנית.",
}

# תמלול הוא חולף: כל נתח שהושלם נשמר, וניסיון חוזר ממשיך מהמקום
# שנעצר. לכן גם הפסקה באמצע שווה ניסיון נוסף.
RETRYABLE = {"no_m3u8", "vod_processing", "audio_download", "cut", "transcribe"}


def api_precheck() -> str:
    """
    קריאה של טוקן אחד כדי לוודא שהמפתח תקף ושיש יתרה.
    מחזיר הסבר בעברית אם יש בעיה, ומחרוזת ריקה אם הכל בסדר.
    כשלון רשת מחזיר ריק בכוונה - לא עוצרים צינור בגלל רעד ברשת.
    """
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return ('חסר ANTHROPIC_API_KEY. setx ANTHROPIC_API_KEY "sk-ant-..." '
                "ואז חלון חדש.")
    try:
        import anthropic
        sys.path.insert(0, str(SCRIPTS))
        from analyze13 import fatal_api_error
    except ImportError:
        return ""
    try:
        from analyze13 import pick_model
        client = anthropic.Anthropic()
        # שם המודל בקוד מזדקן. לפתור אותו קודם, אחרת שגיאת "מודל לא
        # קיים" תסתיר בדיוק את מה שבאנו לבדוק. models.list לא עולה טוקנים.
        model = pick_model(client, "claude-haiku-4-5-20251001")
        client.messages.create(model=model, max_tokens=1,
                               messages=[{"role": "user", "content": "1"}])
    except Exception as exc:
        return fatal_api_error(str(exc))     # ריק = שגיאה חולפת, ממשיכים
    return ""


def fail(job_dir: Path, streamer: str, date: str, error: str, detail: str = "",
         vod: str = "") -> None:
    """כותב מצב כישלון ושולח הודעה מפורטת. יוצא מהתוכנית."""
    job = f"{streamer}_{date}"
    set_state(job_dir, "failed", error=error, detail=detail[:300],
              retryable=error in RETRYABLE)
    hint = HINTS.get(error, "").format(vod=vod, job=job)
    text = (f"<b>נכשל: {streamer} / {date}</b>\n"
            f"שלב: {error}\n")
    if detail:
        text += f"<code>{detail[:200]}</code>\n"
    if hint:
        text += f"\n{hint}\n"
    text += f"\nלוג: <code>jobs\\{job}_pipeline.log</code>"
    log(f"נכשל: {error} {detail[:120]}")
    # כשל שנוסה שוב לבד (המנטר) - שקט. כשל שדורש אותך - עם רטט.
    notify(text, important=error not in RETRYABLE)
    sys.exit(1)


def log(msg: str) -> None:
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


def set_state(job_dir: Path, stage: str, **extra) -> None:
    """כותב את מצב העבודה. זה מה שהמסך והבוט קוראים."""
    state_path = job_dir / "state.json"
    state = {}
    if state_path.exists():
        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
        except Exception:
            state = {}
    state["stage"] = stage
    state["updated"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    state.update(extra)
    state_path.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")


def run(cmd, cwd=None) -> int:
    """
    מריץ תהליך בת.

    CREATE_NO_WINDOW מסתיר את חלון הקונסולה, אבל הוא *לא* מנתק את
    התהליך מאירועי קונסולה. זה מה שהפיל את התמלול של ליאור ועודד
    ב-10.9 עם forrtl: error (200) - ספריית Fortran שבתוך ctranslate2
    רושמת console control handler ומפילה את התהליך בסגירת חלון.

    שני התיקונים:
      CREATE_NEW_PROCESS_GROUP        אירועי Ctrl-C/Break לא עוברים אלינו
      FOR_DISABLE_CONSOLE_CTRL_HANDLER  אומר ל-Fortran לא לרשום handler בכלל
    """
    log(" ".join(str(c) for c in cmd[:4]) + " ...")
    flags = 0
    env = None
    if os.name == "nt":
        flags = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
        env = os.environ.copy()
        env["FOR_DISABLE_CONSOLE_CTRL_HANDLER"] = "1"
    return subprocess.run([str(c) for c in cmd], cwd=cwd, env=env,
                          creationflags=flags).returncode


def watch_entry(slug: str) -> dict:
    """הרשומה של הסטרימר ב-watchlist.json, או {} אם אין."""
    try:
        wl = json.loads((ROOT / "watchlist.json").read_text(encoding="utf-8"))
        for s in wl.get("streamers", []):
            if str(s.get("slug", "")).lstrip("@").lower() == slug.lstrip("@").lower():
                return s
    except Exception:
        pass
    return {}


def audio_seconds(path: Path) -> float:
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    try:
        r = subprocess.run(["ffprobe", "-v", "error", "-show_entries",
                            "format=duration", "-of", "csv=p=0", str(path)],
                           capture_output=True, text=True, timeout=60,
                           creationflags=flags)
        return float(r.stdout.strip())
    except Exception:
        return 0.0


def trim_audio(job_dir: Path, audio: Path, cap_min: int, meta: dict,
               meta_path: Path) -> None:
    """מקצר את audio.mp3 ל-cap_min דקות. כישלון לא עוצר - ממשיכים עם המלא."""
    secs = audio_seconds(audio)
    if not secs:
        log("תקרת אורך: לא הצלחתי למדוד את האודיו, ממשיך עם המלא.")
        return
    if secs <= cap_min * 60 + 5:        # +5: מסגרת mp3 אחרונה עוברת את הסף
        return
    tmp = job_dir / "audio_trim.mp3"
    rc = run(["ffmpeg", "-y", "-v", "error", "-i", audio, "-t", str(cap_min * 60),
              "-c", "copy", tmp])
    if rc != 0 or not tmp.exists() or tmp.stat().st_size == 0:
        log(f"תקרת אורך: ffmpeg החזיר {rc}, ממשיך עם האודיו המלא.")
        tmp.unlink(missing_ok=True)
        return
    tmp.replace(audio)
    meta["trimmed_to_min"] = cap_min
    meta["full_minutes"] = round(secs / 60)
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
    log(f"תקרת אורך: הלייב {secs/60:.0f} דק', מנתח רק את {cap_min} הראשונות.")


def streamer_tier(slug: str):
    """
    (רמה, ציון מינימלי, חלון, חפיפה) לפי גודל הקהל ב-watchlist.json.

    סטרימר קטן מקבל רף גבוה יותר, וגם חלון גדול יותר עם פחות חפיפה -
    כלומר פחות קריאות ל-API. לייב של 9 שעות יורד מ-16 חלונות לכ-10.

    הספים עודכנו 14.9 יחד עם מקור ה-audience: הוא כבר לא עוקבי קיק אלא
    הגדול מבין מנויי היוטיוב לעוקבי הקיק, כלומר מספרים גדולים בסדר גודל.
    בספים הישנים (60K = גדול) כמעט כל הרשימה הייתה נופלת ל"גדול".

    ⚠ אותם ספים קיימים גם ב-analyze13.TIER_THRESHOLDS. כאן הם הקובעים
      בייצור כי run10 מעביר --tier מפורש, אבל הרצה ידנית של analyze13
      נופלת על שלו. לשנות בשניהם.
    """
    audience = None
    try:
        wl = json.loads((ROOT / "watchlist.json").read_text(encoding="utf-8"))
        for s in wl.get("streamers", []):
            if str(s.get("slug", "")).lstrip("@").lower() == slug.lstrip("@").lower():
                audience = s.get("audience")
                break
    except Exception:
        pass
    try:
        n = int(audience)
    except (TypeError, ValueError):
        n = 80_000                      # בלי מידע - מתייחסים כבינוני
    if n >= 250_000:
        return "גדול", 6, 45, 12
    if n >= 50_000:
        return "בינוני", 7, 45, 12
    return "קטן", 7, 60, 8


def pick(name: str) -> Path:
    """
    מוודא שהסקריפט המבוקש קיים. בלי נפילה לגרסה ישנה (29.9): גרסה ישנה
    שרצה בשקט היא מלכודת, והמיון היה שגוי (analyze9 לפני analyze13).
    """
    p = SCRIPTS / name
    if p.exists():
        return p
    log(f"לא נמצא {name}.")
    sys.exit(1)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("streamer")
    ap.add_argument("date", help="YYYY-MM-DD")
    ap.add_argument("--vod", default="", help="קישור עמוד ה-VOD בקיק")
    ap.add_argument("--m3u8", default="", help="כתובת הזרמה מוכנה, אם המנטר כבר השיג")
    ap.add_argument("--display", default="", help="שם הסטרימר בעברית לכותרות")
    ap.add_argument("--platform", default="", help="kick / twitch / youtube. מזוהה לבד מהקישור")
    ap.add_argument("--model", default="large-v3")
    ap.add_argument("--batch", type=int, default=4, help="גודל אצווה לתמלול")
    ap.add_argument("--min", type=int, default=6)
    ap.add_argument("--max", type=int, default=25)
    ap.add_argument("--min-score", type=int, default=None,
                    help="ברירת מחדל נגזרת מגודל הסטרימר: 6 לגדול, 7 לבינוני ולקטן")
    ap.add_argument("--llm", default="claude-sonnet-5")
    ap.add_argument("--no-cut", action="store_true")
    ap.add_argument("--no-snap", action="store_true", help="בלי ליטוש גבולות בחיתוך")
    ap.add_argument("--no-desc", action="store_true", help="בלי שלב התיאורים")
    ap.add_argument("--no-thumb", action="store_true", help="בלי שלב התמניילים")
    ap.add_argument("--no-wait", action="store_true", help="ניסיון m3u8 אחד בלבד, בלי המתנות")
    ap.add_argument("--no-llm-desc", action="store_true", help="תיאורים מתבנית, בלי עלות")
    ap.add_argument("--resume", action="store_true")
    ap.add_argument("--force-vod", action="store_true",
                    help="להחליף את ה-VOD של עבודה קיימת (בלי זה: עצירה, כדי לא לערבב שני לייבים)")
    ap.add_argument("--analysis", default="auto", choices=["auto", "realtime", "batch"],
                    help="auto = budget.py מחליט (דרמה חמה/גדולים בזמן אמת, השאר batch). "
                         "ידני עוקף את התקציב")
    args = ap.parse_args()

    try:
        from tg import paused
        if paused():
            log(f"המערכת מושהית (/pause מ-{paused().get('since', '?')}). לא מתחיל. /resume בטלגרם.")
            sys.exit(0)
    except ImportError:
        pass

    # גודל הסטרימר קובע את הרף ואת עומק הניתוח. דגל מפורש גובר.
    tier, tier_min_score, window, overlap = streamer_tier(args.streamer)
    if args.min_score is None:
        args.min_score = tier_min_score

    job_dir = ROOT / "jobs" / f"{args.streamer}_{args.date}"
    (job_dir / "clips").mkdir(parents=True, exist_ok=True)

    meta_path = job_dir / "meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}
    # 67/1 (29.9): לייב שני באותו יום קיבל את אותה תיקייה, --resume דילג על הכל
    # (האודיו והתמלול של הראשון כבר שם), ו-vod_url נדרס לכתובת של השני.
    # המנטר נותן עכשיו תיקייה נפרדת (-2), וכאן השומר לכל מסלול אחר.
    old_vod = meta.get("vod_url", "")
    if args.vod and old_vod and old_vod != args.vod and not args.force_vod:
        log(f"העבודה {job_dir.name} שייכת ל-VOD אחר:\n  קיים: {old_vod}\n  ביקשו: {args.vod}")
        notify(f"<b>לא הרצתי: {job_dir.name}</b>\n"
               f"התיקייה שייכת ל-VOD אחר, ולא דורסים לייב אחד בשני.\n"
               f"קיים: {old_vod}\nביקשו: {args.vod}\n"
               f"אם זה מכוון: <code>--force-vod</code>", important=True)
        sys.exit(1)
    if args.vod:
        meta["vod_url"] = args.vod
    if args.display:
        meta["display_name"] = args.display
    meta.setdefault("streamer", args.streamer)
    meta.setdefault("display_name", args.display or args.streamer)
    meta.setdefault("date", args.date)
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")

    vod = meta.get("vod_url", "")
    if not vod:
        log("חסר קישור VOD. תן --vod")
        sys.exit(1)

    started = time.time()

    # סימון "אני רץ", כדי שהמנטר לא יפעיל את אותה עבודה פעמיים
    pidfile = job_dir / "pipeline.pid"
    pidfile.write_text(str(os.getpid()), encoding="utf-8")
    import atexit
    atexit.register(lambda: pidfile.unlink(missing_ok=True))

    # רשת ביטחון: יציאה באמצע בלי fail() משאירה את העבודה ב"שלב פעיל",
    # והמנטר מגלה את זה רק אחרי stall_hours (5 שעות). ב-15.9 כך אבדו
    # כל הלייבים של היום. כאן: כל יציאה שלא הגיעה ל-done/failed נרשמת
    # מיד ככישלון עם השלב שבו נפלה, ומדווחת. לא retryable - קריסה היא
    # באג, וניסיון חוזר יקרוס באותו מקום. /retry ידני אחרי התיקון.
    def crash_guard():
        try:
            st = json.loads((job_dir / "state.json").read_text(encoding="utf-8"))
        except Exception:
            return
        stage = st.get("stage", "")
        if stage in ("done", "failed", "awaiting_cut", "waiting_budget"):
            return
        set_state(job_dir, "failed", error="crashed", retryable=False,
                  detail=f"הצינור יצא באמצע שלב {stage}", _failed_reported=True)
        log(f"נכשל: יציאה באמצע שלב {stage}")
        try:
            notify(f"<b>קרס: {args.streamer} / {args.date}</b>\n"
                   f"הצינור יצא באמצע שלב {stage}. זה באג, לא תקלה חולפת.\n"
                   f"לוג: <code>jobs\\{args.streamer}_{args.date}_pipeline.log</code>\n"
                   f"אחרי התיקון: <code>/retry {args.streamer}_{args.date}</code>",
                   important=True)
        except Exception:
            pass
    atexit.register(crash_guard)

    log(f"מתחיל: {args.streamer} / {args.date}")
    log(f"תיקייה: {job_dir}")

    # ---------- 0. בדיקת מפתח ויתרה, לפני שמשקיעים שעה וחצי ----------
    #
    # הסדר של הצינור הוא הורדה, תמלול, ורק אז ניתוח. אם היתרה ריקה,
    # הכישלון מגיע אחרי שה-GPU כבר טחן שעה וחצי - וזה קרה ב-14.9.
    # קריאה של טוקן אחד כאן עולה אפס ומגלה את זה מראש.
    problem = api_precheck()
    if problem:
        fail(job_dir, args.streamer, args.date, "analyze", detail=problem)

    # ---------- 1. כתובת להורדה ----------
    # קיק חוסם את yt-dlp, ולכן שם צריך לחלץ m3u8 דרך דפדפן.
    # טוויץ' ויוטיוב נתמכים מקורית - הכתובת של העמוד היא הכתובת.
    platform = (args.platform or meta.get("platform") or "").lower()
    if not platform:
        low = vod.lower()
        platform = ("twitch" if "twitch.tv" in low else
                    "youtube" if ("youtube.com" in low or "youtu.be" in low) else "kick")
    meta["platform"] = platform

    if platform == "kick":
        set_state(job_dir, "resolving_url")
        # 28.9: בלי נפילה ל-kickurl הישן - גרסה ישנה שרצה בשקט היא מלכודת.
        from kickurl2 import grab_m3u8_ex

        # קיק מעבדת VOD כמה דקות אחרי שהוא מופיע ב-API. מנסים כמה
        # פעמים עם המתנה ביניהן, במקום ליפול בניסיון הראשון.
        waits = [0, 5, 10, 15] if not args.no_wait else [0]
        m3u8, reason = "", ""
        if args.m3u8:
            m3u8, reason = args.m3u8, "monitor"
            log("כתובת הזרמה התקבלה מהמנטר, מדלג על הדפדפן.")
            waits = []
        for i, mins in enumerate(waits, 1):
            if mins:
                log(f"ממתין {mins} דקות לפני ניסיון {i}/{len(waits)}...")
                set_state(job_dir, "resolving_url", attempt=i, waiting_minutes=mins)
                time.sleep(mins * 60)
            log(f"קיק - מחלץ כתובת m3u8 (ניסיון {i}/{len(waits)})...")
            m3u8, reason = grab_m3u8_ex(vod)
            if m3u8:
                break
            log(f"  לא התקבלה כתובת: {reason}")
            if reason == "not_found":
                break

        if not m3u8:
            err = {"processing": "vod_processing", "not_found": "vod_not_found"}.get(reason, "no_m3u8")
            fail(job_dir, args.streamer, args.date, err,
                 detail=f"סיבה: {reason} אחרי {len(waits)} ניסיונות", vod=vod)
        meta["m3u8_url"] = m3u8
        meta["m3u8_source"] = reason
        meta["m3u8_fetched"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        log(f"יש כתובת ({reason}).")
    else:
        m3u8 = vod
        log(f"{platform} - yt-dlp מוריד ישירות, בלי שלב הדפדפן.")

    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")

    # ---------- 2. אודיו ----------
    audio = job_dir / "audio.mp3"
    if audio.exists() and args.resume:
        log(f"אודיו קיים ({audio.stat().st_size / 1e6:.0f}MB), מדלג.")
    else:
        set_state(job_dir, "downloading_audio")
        log("מוריד אודיו של כל השידור...")
        # bestaudio אם יש רצועה נפרדת, אחרת הרזולוציה הנמוכה ביותר.
        # האודיו זהה בכל הרזולוציות - אין הפסד, רק חיסכון עצום בנפח.
        rc = run([
            "yt-dlp",
            "-f", "bestaudio/worstvideo+bestaudio/worst",
            "-x", "--audio-format", "mp3",
            "-o", str(job_dir / "audio.%(ext)s"),
            m3u8,
        ])
        if rc != 0 or not audio.exists():
            fail(job_dir, args.streamer, args.date, "audio_download",
                 detail=f"yt-dlp החזיר קוד {rc}")
        log(f"אודיו: {audio.stat().st_size / 1e6:.0f}MB")

    # ---------- 2ב. תקרת אורך לסטרימר (max_minutes ב-watchlist) ----------
    # לסטרימרים קטנים עם לייבים של 10-14 שעות (מיכאל, פורסיי) - מנתחים
    # רק את N הדקות הראשונות. חותכים את הזנב בלבד, ולכן הזמנים בתמלול
    # לא זזים והחיתוך מה-m3u8 נשאר נכון. רק לפני תמלול: אחרי שיש תמלול
    # מלא, קיצור האודיו היה משאיר את cut3 בלי נתוני שקט לחלק מהקטעים.
    cap = watch_entry(args.streamer).get("max_minutes")
    if cap and not (job_dir / "audio_transcript.json").exists():
        trim_audio(job_dir, audio, int(cap), meta, meta_path)

    # ---------- 3. תמלול ----------
    transcript = job_dir / "audio_transcript.json"
    if transcript.exists() and args.resume:
        log("תמלול קיים, מדלג.")
    else:
        try:
            from gpulock import gpu_lock
        except ImportError:
            from contextlib import contextmanager
            @contextmanager
            def gpu_lock(label="", on_wait=None):
                yield

        def on_wait(mins, h):
            who = (h or {}).get("label", "?")
            set_state(job_dir, "waiting_gpu", behind=who)
            if mins == 0:
                log(f"ה-GPU תפוס ({who}), ממתין בתור...")

        with gpu_lock(label=f"{args.streamer}_{args.date}", on_wait=on_wait):
            set_state(job_dir, "transcribing")
            log("מתמלל. זה החלק הארוך.")
            rc = run([sys.executable, pick("transcribe5.py"), "audio.mp3",
                      "--model", args.model, "--batch", args.batch], cwd=job_dir)
        if rc != 0 or not transcript.exists():
            fail(job_dir, args.streamer, args.date, "transcribe",
                 detail=f"transcribe5 החזיר קוד {rc}")

    # ---------- 4. זיהוי קטעים ----------
    segments = job_dir / "audio_segments.json"
    if segments.exists() and args.resume:
        log("קטעים קיימים, מדלג.")
    else:
        # ---------- 4א. תקציב ועדיפות (משימות 57 + 46, 28.9) ----------
        # אחרי התמלול (חינם, על ה-GPU) ולפני הניתוח (הכסף). ההחלטה נגזרת
        # מהתמלול עצמו: דרמה חמה נכנסת מיד, השאר ב-batch בחצי מחיר, ומה
        # שאין לו תקציב מחכה ב-waiting_budget - המנטר ישחרר לפי עדיפות.
        mode, why, dec = args.analysis, "ידני", {}
        if mode == "auto":
            try:
                import budget
                rows = json.loads(transcript.read_text(encoding="utf-8"))
                dec = budget.decide(args.streamer, rows,
                                    args.display or meta.get("display_name", ""))
                mode, why = dec["mode"], dec["why"]
                budget.log_event("decide", job=job_dir.name, mode=mode, why=why,
                                 hot=dec["hot"], big=dec["big"],
                                 est=dec.get("est_batch" if mode != "realtime" else "est_realtime"),
                                 heat=dec["heat"])
            except Exception as exc:
                # בלי budget.py מנתחים כמו פעם, בזמן אמת - לא עוצרים את הצינור
                mode, why = "realtime", f"budget.py לא זמין ({exc})"
        log(f"ניתוח: {mode} - {why}")
        if dec:
            h = dec["heat"]
            log(f"  חום: {h['others_per_h']} אזכורי יוצרים/שעה, {h['drama_per_h']} מילות דרמה/שעה"
                f"{' · טריגר: ' + ', '.join(h['triggers']) if h['triggers'] else ''}"
                f"  ·  הוצאו ${dec['spent']} · חלק יומי ${dec['daily']}")
        if mode == "defer":
            prev = {}
            try:
                prev = json.loads((job_dir / "state.json").read_text(encoding="utf-8"))
            except Exception:
                pass
            set_state(job_dir, "waiting_budget", why=why, hot=dec.get("hot"),
                      big=dec.get("big"), est=dec.get("est_batch"),
                      waiting_since=prev.get("waiting_since")
                      or datetime.now(timezone.utc).isoformat(timespec="seconds"))
            if prev.get("stage") != "waiting_budget":
                try:
                    import budget
                    budget.log_event("defer", job=job_dir.name, why=why,
                                     est=dec.get("est_batch"), hot=dec.get("hot"))
                except Exception:
                    pass
                notify(f"<b>{args.display or args.streamer}: ממתין לתקציב</b>\n{why}\n"
                       f"התמלול מוכן. המנטר ינתח כשיתפנה"
                       f"{' - בעדיפות (דרמה חמה)' if dec.get('hot') else ''}.\n"
                       f"<code>/budget</code> · לעקוף: "
                       f"<code>/retry {job_dir.name}</code>", silent=True)
            log("ממתין לתקציב. יוצא בלי כישלון.")
            sys.exit(0)

        set_state(job_dir, "analyzing_batch" if mode == "batch" else "analyzing",
                  hot=dec.get("hot"), big=dec.get("big"))
        log("מזהה קטעים מעניינים..." + (" (batch - התשובה בד\"כ תוך שעה)" if mode == "batch" else ""))
        rc = run([sys.executable, pick(ANALYZER), "audio_transcript.json",
                  "--min", args.min, "--max", args.max,
                  "--model", args.llm,
                  # ⚠ השם לכותרות הוא שם בעברית, לא ה-slug. העברת
                  # args.streamer כאן הפילה את כל הכותרות של ניק ב-9.9
                  # ל-"pedrofederer מתפוצץ על...". meta.json הוא המקור.
                  "--streamer", (args.display
                                 or meta.get("display_name")
                                 or args.streamer),
                  "--tier", tier,
                  "--window", window, "--overlap", overlap,
                  "--save-raw"] + (["--batch"] if mode == "batch" else []), cwd=job_dir)
        if rc != 0 or not segments.exists():
            fail(job_dir, args.streamer, args.date, "analyze",
                 detail=f"{ANALYZER} החזיר קוד {rc}")

        # 67/3: חלונות שלא נותחו גם אחרי הניסיונות. הקטעים משאר הלייב ממשיכים.
        gaps_path = job_dir / "analysis_gaps.json"
        if gaps_path.exists():
            try:
                gaps = json.loads(gaps_path.read_text(encoding="utf-8"))
            except Exception:
                gaps = []
            if gaps:
                lines = [f"חלון {g.get('window')} מתוך {g.get('of')} ({g.get('span', '')})"
                         for g in gaps]
                notify(f"<b>חלק מהלייב לא נותח: {args.display or args.streamer} / {args.date}</b>\n"
                       + "\n".join(lines) + "\n"
                       f"סיבה: {str(gaps[0].get('why', ''))[:120]}\n"
                       "הקטעים משאר הלייב ממשיכים כרגיל.\n"
                       f"לנתח מחדש הכל: למחוק את <code>audio_segments.json</code> ואז "
                       f"<code>/retry {job_dir.name}</code>", important=True)

    found = json.loads(segments.read_text(encoding="utf-8"))
    good = [s for s in found if s.get("score", 0) >= args.min_score]
    log(f"נמצאו {len(found)} קטעים, {len(good)} מעל ציון {args.min_score} "
        f"(סטרימר {tier})")

    # ---------- 5. חיתוך ----------
    if args.no_cut:
        set_state(job_dir, "awaiting_cut", segments=len(found), good=len(good))
        log("עוצר לפני החיתוך (--no-cut).")
    else:
        set_state(job_dir, "cutting", segments=len(found), good=len(good))
        # 67/4: הכתובת מתחילת הריצה עברה תמלול של שעה-שעתיים ואולי batch של
        # עד 24 שעות. כתובות של קיק פגות - מביאים טרייה לפני החיתוך.
        if platform == "kick":
            fresh, why = "", ""
            try:
                from kickurl2 import grab_m3u8_ex
                log("מביא כתובת m3u8 טרייה לחיתוך...")
                fresh, why = grab_m3u8_ex(vod)
            except Exception as exc:
                why = str(exc)
            if fresh:
                m3u8 = fresh
                meta["m3u8_url"] = fresh
                meta["m3u8_fetched"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
                meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
            else:
                log(f"  לא התקבלה כתובת טרייה ({why}), חותך עם הקודמת.")
        log("חותך קטעים...")
        cmd = [sys.executable, pick(CUTTER), "audio_segments.json",
               "--url", m3u8, "--min-score", args.min_score]
        if args.no_snap:
            cmd.append("--no-snap")
        rc = run(cmd, cwd=job_dir)
        if rc != 0:
            # 67/4: cut3 יוצא עם 2 כשחלק מהקטעים נכשלו, ורושם אותם
            missing = []
            try:
                missing = json.loads((job_dir / "clips" / "cut_failures.json")
                                     .read_text(encoding="utf-8"))
            except Exception:
                pass
            what = ("\n".join(f"[{m.get('idx')}] {str(m.get('title', ''))[:50]} - {m.get('why', '')}"
                              for m in missing)
                    or "חלק מהקליפים אולי חסרים.")
            notify(f"<b>חיתוך חלקי: {args.display or args.streamer} / {args.date}</b>\n"
                   f"{len(missing) or '?'} קטעים לא נחתכו (קוד {rc}):\n{what}\n\n"
                   + HINTS["cut"].format(job=f"{args.streamer}_{args.date}", vod=vod)
                   + "\nמה שנחתך ממשיך לאישור.", important=True)

    # ---------- 6. תיאורים וקרדיטים ----------
    if not args.no_desc:
        set_state(job_dir, "describing", segments=len(found), good=len(good))
        log("כותב תיאורים וקרדיטים...")
        desc_cmd = [sys.executable, pick("describe.py"), "audio_segments.json",
                    "--min-score", args.min_score, "--model", args.llm]
        if args.no_llm_desc:
            desc_cmd.append("--no-llm")
        rc = run(desc_cmd, cwd=job_dir)
        if rc != 0:
            notify(f"<b>אזהרה: {args.streamer} / {args.date}</b>\n"
                   f"describe החזיר קוד {rc}. " + HINTS["describe"], important=True)

    # ---------- 7. תמניילים (משימה 4) ----------
    # לפני האישור, כדי שהתמנייל יגיע לטלגרם יחד עם הקטע ותראה אותו
    # לפני שהוא ביוטיוב. כישלון כאן לא עוצר כלום: upload.py ינסה
    # לבנות אותו שוב ברגע ההעלאה.
    if not args.no_cut and not args.no_thumb:
        set_state(job_dir, "thumbnails", segments=len(found), good=len(good))
        log("בונה תמניילים...")
        try:
            from thumb import make_thumbnail
            made = 0
            for i, seg in enumerate(found, 1):
                if seg.get("score", 0) < args.min_score:
                    continue
                try:
                    if make_thumbnail(job_dir, i):
                        made += 1
                except Exception as exc:
                    log(f"  תמנייל [{i}] נכשל: {exc}")
            log(f"תמניילים: {made}")
        except Exception as exc:
            # ממשיכים לאישור בלי תמנייל, אבל לא בשקט.
            log(f"שלב התמניילים דולג: {exc}")
            notify(f"<b>אזהרה: {args.streamer} / {args.date}</b>\n"
                   f"התמניילים דולגו: {exc}\nהקטעים ממשיכים לאישור בלעדיהם.", important=True)

    mins = (time.time() - started) / 60
    clips = sorted(p for p in (job_dir / "clips").glob("*.mp4") if "__p" not in p.stem)
    set_state(job_dir, "done", segments=len(found), good=len(good),
              clips=len(clips), minutes=round(mins, 1))

    log(f"סיים ב-{mins:.0f} דקות. {len(clips)} קליפים.")
    if not clips and not args.no_cut:
        notify(f"<b>הסתיים בלי קליפים: {args.streamer} / {args.date}</b>\n"
               f"נמצאו {len(found)} קטעים, {len(good)} מעל ציון {args.min_score}, "
               f"אבל אף קובץ לא נוצר. לבדוק את הלוג.", important=bool(good))
    log(f"קטעים: {job_dir / 'audio_segments.txt'}")
    log(f"וידאו: {job_dir / 'clips'}")


if __name__ == "__main__":
    main()
