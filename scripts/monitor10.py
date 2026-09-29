"""
monitor10.py - הלולאה שמריצה את הכל לבד

בודק כל כמה דקות מי משדר. כשלייב נגמר, ממתין שה-VOD יופיע,
מפעיל את הצינור, ושולח לך את התוצאה בטלגרם.

זהה ל-monitor9 חוץ משני חיבורים: הקטעים נשלחים דרך approve2
(תצוגה מקדימה כווידאו + כפתור כותרת + כפתור קובץ מלא), והבוט
שרץ ברקע הוא tgbot2 (שיודע לטפל בכפתורים האלה ובפקודת /pending).

שימוש:
    python scripts\\monitor.py                 מתחיל לרוץ
    python scripts\\monitor.py --once          סבב בדיקה אחד ויציאה
    python scripts\\monitor.py --dry           בלי להפעיל את הצינור, רק התראות
    python scripts\\monitor.py --status        להציג את המצב הנוכחי ולצאת

קבצים:
    watchlist.json      מי במעקב
    monitor_state.json  מה היה המצב בבדיקה הקודמת
"""

import os
import sys
import json
import time
import subprocess
import argparse
import traceback
from pathlib import Path
from datetime import datetime, timezone


def find_root(start: Path) -> Path:
    for c in [start, *start.parents]:
        if (c / "scripts").is_dir():
            return c
    return start


ROOT = find_root(Path(__file__).resolve().parent)
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

from livecheck import check as live_check  # noqa: E402

try:
    from tg import notify
except ImportError:
    def notify(text, **kw):
        print(f"[טלגרם לא זמין] {text}")
        return False


STATE_PATH = ROOT / "monitor_state.json"
WATCHLIST_PATH = ROOT / "watchlist.json"


def log(msg: str) -> None:
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


def load_json(path: Path, default):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def save_json(path: Path, data) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")


def active_streamers() -> list:
    """
    פעיל וגם יש לו מזהה. רשומה בלי slug היא תזכורת למלא אותו,
    לא סטרימר שאפשר לבדוק.
    """
    wl = load_json(WATCHLIST_PATH, {})
    out = []
    for s in wl.get("streamers", []):
        if not s.get("active"):
            continue
        if not (s.get("slug") or "").strip():
            log(f"דילגתי על {s.get('name','?')} - חסר מזהה ב-watchlist.json")
            continue
        s.setdefault("platform", "kick")
        out.append(s)
    return out


def settings() -> dict:
    wl = load_json(WATCHLIST_PATH, {})
    return {
        "every": wl.get("check_every_minutes", 5),
        "vod_wait": wl.get("vod_wait_minutes", 20),
        # כמה דקות לתת ל-VOD "להתיישב" מרגע שהופיע ב-API ועד ההפעלה.
        # ב-9.9 חמישה לייבים נפלו כי הופעלו באותה שנייה שה-VOD הופיע.
        "vod_settle": wl.get("vod_settle_minutes", 5),
        "retry_max": wl.get("retry_max", 4),
        "retry_gap": wl.get("retry_gap_minutes", 30),
        "stall_hours": wl.get("stall_hours", 5),
    }


def job_alive(job: Path) -> bool:
    """האם יש צינור שכבר רץ על העבודה הזו."""
    pidfile = job / "pipeline.pid"
    if not pidfile.exists():
        return False
    try:
        pid = int(pidfile.read_text(encoding="utf-8").strip())
    except Exception:
        return False
    if pid <= 0:
        return False
    if os.name == "nt":
        try:
            r = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"],
                               capture_output=True, text=True, timeout=15)
            if str(pid) in (r.stdout or ""):
                return True
        except Exception:
            return True
        pidfile.unlink(missing_ok=True)
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        pidfile.unlink(missing_ok=True)
        return False


def run_job(slug: str, date: str, vod: str, display: str, header: str,
            m3u8: str = "") -> None:
    """
    מפעיל את הצינור ברקע.

    DETACHED_PROCESS הוא לב העניין: בלעדיו התהליך יורש את החלון של
    המנטר, וסגירת המנטר שולחת לו "החלון נסגר" והורגת אותו באמצע
    התמלול. זה מה שקטל את התמלול של ליאור ב-10.9.
    """
    job = ROOT / "jobs" / f"{slug}_{date}"
    if job_alive(job):
        log(f"{job.name} כבר רץ, לא מפעיל שוב")
        return

    cmd = [sys.executable, str(SCRIPTS / "run10.py"), slug, date,
           "--vod", vod, "--display", display, "--resume"]
    if m3u8:
        cmd += ["--m3u8", m3u8]
    env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
    flags = 0
    if os.name == "nt":
        flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    logfile = ROOT / "jobs" / f"{slug}_{date}_pipeline.log"
    logfile.parent.mkdir(exist_ok=True)
    with logfile.open("a", encoding="utf-8") as f:
        f.write(f"\n===== {header} {datetime.now().isoformat()} =====\n")
        subprocess.Popen(cmd, stdout=f, stderr=subprocess.STDOUT,
                         cwd=str(ROOT), env=env, creationflags=flags,
                         close_fds=True)


def hhmm(minutes: float) -> str:
    h, m = divmod(int(minutes), 60)
    return f"{h}:{m:02d}" if h else f"{m} דק'"


def newest_vod(entry: dict) -> dict:
    vods = entry.get("vods") or []
    return vods[0] if vods else {}


def launch_pipeline(slug: str, display: str, vod_url: str, dry: bool, m3u8: str = "") -> None:
    """מפעיל את run10.py ברקע. לא חוסם את הלולאה."""
    date = datetime.now().strftime("%Y-%m-%d")
    cmd = [
        sys.executable, str(SCRIPTS / "run10.py"),
        slug, date,
        "--vod", vod_url,
        "--display", display,
        "--resume",
    ]
    if dry:
        log(f"[יבש] הייתי מריץ: {' '.join(cmd[-6:])}")
        return

    log(f"מפעיל את הצינור על {slug}")
    run_job(slug, date, vod_url, display, "התחלה", m3u8=m3u8)

    notify(
        f"<b>מתחיל לעבד את הלייב של {display}</b>\n"
        f"זה ייקח בערך שעתיים. אעדכן כשיהיו קטעים."
    )


def report_finished(slug: str, display: str) -> bool:
    """
    בודק אם עבודה שהופעלה הסתיימה, ואם כן שולח את הקטעים.
    מחזיר True אם דיווח.
    """
    # לא לפי התאריך של היום. לייב שמתחיל בערב ונגמר אחרי חצות מקבל
    # תיקייה עם תאריך אתמול, ואז החיפוש לפי היום לא מוצא אותה לעולם -
    # ככה שלושת הקטעים של ניק מ-9.9 מעולם לא נשלחו. במקום זה: כל עבודה
    # של הסטרימר הזה שהסתיימה ועוד לא דווחה, מהחדשה לישנה.
    job = None
    state = {}
    for cand in sorted((ROOT / "jobs").glob(f"{slug}_*"), reverse=True):
        if not cand.is_dir():
            continue
        st = load_json(cand / "state.json", {})
        if st.get("stage") == "done" and not st.get("_reported"):
            job, state = cand, st
            break
    if job is None:
        return False

    # שולח כל קטע עם כפתורי אישור/דחייה. לחיצה מלמדת את המנתח.
    try:
        from approve2 import send_for_approval
        sent = send_for_approval(job)
        if not sent:
            notify(f"<b>{display}: העבודה הסתיימה</b>\nאין קטעים חדשים שממתינים לאישור.")
    except Exception as exc:
        log(f"שליחה לאישור נכשלה ({exc}), שולח סיכום רגיל")
        segs = load_json(job / "audio_segments.json", [])
        lines = [f"<b>הקטעים של {display} מוכנים</b>", ""]
        for i, s in enumerate(segs[:8], 1):
            lines.append(f"<b>{s.get('score','?')}/10</b>  {s.get('title','')}")
        notify("\n".join(lines))

    state["_reported"] = True
    save_json(job / "state.json", state)
    return True


def resume_stuck_jobs(dry: bool = False) -> None:
    """
    בהפעלה: מחפש עבודות שנתקעו באמצע - הפסקת חשמל, אתחול, קריסה -
    וממשיך אותן מהמקום שבו נעצרו.
    """
    jobs_dir = ROOT / "jobs"
    if not jobs_dir.is_dir():
        return

    stuck = []
    for job in jobs_dir.iterdir():
        if not job.is_dir():
            continue
        state = load_json(job / "state.json", {})
        stage = state.get("stage", "")
        if stage and stage not in ("done", "failed", "waiting_budget"):
            if job_alive(job):
                log(f"  {job.name} עדיין רץ ({stage}), משאיר אותו")
                continue
            stuck.append((job, stage))

    if not stuck:
        return

    log(f"נמצאו {len(stuck)} עבודות שנתקעו")
    if len(stuck) > 1:
        log("כולן יופעלו, והתמלולים יעמדו בתור ל-GPU אחד אחרי השני")
    for job, stage in stuck:
        meta = load_json(job / "meta.json", {})
        slug = meta.get("streamer", "")
        display = meta.get("display_name", slug)
        vod = meta.get("vod_url", "")
        parts = job.name.rsplit("_", 1)
        date = parts[1] if len(parts) == 2 else datetime.now().strftime("%Y-%m-%d")

        if not (slug and vod):
            log(f"  {job.name}: חסר מידע, מדלג")
            continue

        log(f"  ממשיך {job.name} משלב '{stage}'")
        notify(f"<b>ממשיך עבודה שנקטעה</b>\n{display} · {date}\nנעצר בשלב: {stage}")

        if dry:
            continue
        run_job(slug, date, vod, display, "התאוששות")


def job_date(job: Path) -> str:
    parts = job.name.rsplit("_", 1)
    return parts[1] if len(parts) == 2 else datetime.now().strftime("%Y-%m-%d")


def minutes_since(iso: str) -> float:
    try:
        return (datetime.now(timezone.utc) - datetime.fromisoformat(iso)).total_seconds() / 60
    except Exception:
        return 1e9


def tend_failed_jobs(dry: bool = False) -> None:
    """
    עובר על העבודות ומטפל בשני מצבים:
      נכשלה עם שגיאה חולפת  -> מנסה שוב, עד retry_max פעמים, במרווחים
      תקועה בלי עדכון שעות  -> מסמן ככישלון ומדווח
    גם מדווח על כישלון שהצינור לא הספיק לדווח עליו בעצמו.
    """
    cfg = settings()
    jobs_dir = ROOT / "jobs"
    if not jobs_dir.is_dir():
        return

    launched_this_round = 0

    for job in jobs_dir.iterdir():
        if not job.is_dir():
            continue
        state = load_json(job / "state.json", {})
        stage = state.get("stage", "")
        if not stage:
            continue
        meta = load_json(job / "meta.json", {})
        slug = meta.get("streamer", "")
        display = meta.get("display_name", slug)
        vod = meta.get("vod_url", "")
        date = job_date(job)
        age_min = minutes_since(state.get("updated", ""))

        # תקועה: שלב פעיל בלי עדכון הרבה זמן, ובלי תהליך חי
        if (stage not in ("done", "failed", "waiting_budget")
                and age_min > cfg["stall_hours"] * 60 and not job_alive(job)):
            state.update(stage="failed", error="stalled", retryable=True,
                         detail=f"נתקע בשלב {stage} במשך {age_min/60:.0f} שעות")
            save_json(job / "state.json", state)
            notify(f"<b>נתקע: {display} / {date}</b>\nבשלב {stage} כבר {age_min/60:.0f} שעות. "
                   f"מסמן ככישלון וינסה שוב.")
            log(f"{job.name} נתקע בשלב {stage}")

        if stage != "failed":
            continue

        # דיווח על כישלון שלא דווח (למשל קריסה קשה של הצינור)
        if not state.get("notified") and not state.get("_failed_reported"):
            notify(f"<b>נכשל: {display} / {date}</b>\n"
                   f"שגיאה: {state.get('error','?')}\n{state.get('detail','')}\n"
                   f"לוג: <code>jobs\\{job.name}_pipeline.log</code>")
            state["_failed_reported"] = True
            save_json(job / "state.json", state)

        # ניסיון חוזר. עבודות ישנות (לפני run7) לא סימנו retryable -
        # מזהים לפי סוג השגיאה.
        retries = int(state.get("retries", 0))
        retryable = state.get("retryable")
        if retryable is None:
            retryable = state.get("error") in ("no_m3u8", "vod_processing", "audio_download",
                                               "cut", "stalled", "transcribe")
        if not retryable:
            continue
        if retries >= cfg["retry_max"]:
            if not state.get("_gave_up"):
                notify(f"<b>ויתרתי: {display} / {date}</b>\n"
                       f"{retries} ניסיונות נכשלו ({state.get('error')}). "
                       f"<code>/retry {job.name}</code> ינסה ידנית.")
                state["_gave_up"] = True
                save_json(job / "state.json", state)
            continue
        if age_min < cfg["retry_gap"]:
            continue
        if not (slug and vod):
            continue
        if launched_this_round >= 1:
            continue    # אחת לסבב. השאר יחכו 5 דקות לסבב הבא

        state["retries"] = retries + 1
        state["stage"] = "retrying"
        state["updated"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        state.pop("notified", None)
        state.pop("_failed_reported", None)
        save_json(job / "state.json", state)
        log(f"מנסה שוב {job.name} ({retries + 1}/{cfg['retry_max']})")
        notify(f"<b>מנסה שוב: {display} / {date}</b>\nניסיון {retries + 1} מתוך {cfg['retry_max']}",
               silent=True)
        if not dry:
            run_job(slug, date, vod, display, f"ניסיון חוזר {retries + 1}")
            launched_this_round += 1


def tend_budget_waiting(dry: bool = False) -> None:
    """
    עבודות שהתמלול שלהן מוכן וממתינות לתקציב (משימות 57 + 46, 28.9).
    כל סבב: הכי חשובה (דרמה חמה, אחר כך גדול, אחר כך החדשה) - אם התקציב
    מרשה עכשיו, מופעלת. אחת לסבב. לייב שמחכה יותר מ-defer_max_days -
    הסיפור שלו כבר מת; מוותרים ורושמים, וזה מה שמזין את /budget.
    """
    try:
        import budget
    except Exception as exc:
        log(f"budget.py לא נטען ({exc}) - עבודות שממתינות לתקציב לא ישוחררו")
        return
    jobs_dir = ROOT / "jobs"
    if not jobs_dir.is_dir():
        return
    cfg = budget.config()
    waiting = []
    for job in jobs_dir.iterdir():
        if not job.is_dir():
            continue
        st = load_json(job / "state.json", {})
        if st.get("stage") != "waiting_budget" or job_alive(job):
            continue
        meta = load_json(job / "meta.json", {})
        if minutes_since(st.get("waiting_since", st.get("updated", ""))) \
                > float(cfg["defer_max_days"]) * 1440:
            st.update(stage="failed", error="budget", retryable=False,
                      detail="חיכה לתקציב יותר מדי זמן - הסיפור כבר לא רלוונטי",
                      _failed_reported=True)
            save_json(job / "state.json", st)
            budget.log_event("budget_drop", job=job.name, est=st.get("est"), hot=st.get("hot"))
            notify(f"<b>ויתרתי מחוסר תקציב: {meta.get('display_name', job.name)}</b>\n"
                   f"חיכה {cfg['defer_max_days']} ימים. נרשם ל-/budget.", silent=True)
            log(f"{job.name}: ויתור - תקציב")
            continue
        waiting.append(job)
    if not waiting:
        return
    waiting.sort(key=budget.waiting_priority)
    job = waiting[0]
    meta = load_json(job / "meta.json", {})
    rows = load_json(job / "audio_transcript.json", [])
    slug = meta.get("streamer", job.name.rsplit("_", 1)[0])
    dec = budget.decide(slug, rows, meta.get("display_name", ""))
    if dec["mode"] == "defer":
        return
    log(f"משחרר מהמתנה לתקציב: {job.name} ({dec['mode']}, {len(waiting) - 1} עוד ממתינים)")
    if not dry:
        run_job(slug, job_date(job), meta.get("vod_url", ""),
                meta.get("display_name", slug), "שחרור מהמתנה לתקציב")


def one_round(dry: bool = False) -> None:
    streamers = active_streamers()
    if not streamers:
        log("אין סטרימרים פעילים ב-watchlist.json")
        return

    cfg = settings()
    state = load_json(STATE_PATH, {})
    slugs = [s["slug"] for s in streamers]

    log(f"בודק: {', '.join(slugs)}")
    try:
        results = live_check(streamers, want_vod=True)
    except Exception as exc:
        log(f"בדיקה נכשלה: {exc}")
        return

    now = datetime.now(timezone.utc).isoformat(timespec="seconds")

    for s in streamers:
        slug = s["slug"]
        key = s.get("key") or slug          # מבדיל בין אותו שם בשתי פלטפורמות
        display = s.get("name", slug)
        cur = results.get(key, {})
        prev = state.get(key, {})

        was_live = bool(prev.get("live"))
        is_live = bool(cur.get("live"))

        entry = dict(prev)
        entry["live"] = is_live
        entry["checked"] = now
        if is_live:
            entry["title"] = cur.get("title", "")
            entry["viewers"] = cur.get("viewers", 0)
            entry.setdefault("went_live", now)

        # עלה לשידור
        if is_live and not was_live:
            log(f"{display} עלה ללייב")
            notify(
                f"<b>{display} עלה ללייב</b>\n"
                f"{cur.get('title','')}\n"
                f"{cur.get('viewers',0)} צופים\n"
                f"{cur.get('url') or 'https://kick.com/' + slug}"
            )
            entry["went_live"] = now

        # ירד מהשידור
        elif was_live and not is_live:
            log(f"{display} סיים לשדר")
            notify(f"<b>{display} סיים לשדר</b>\nממתין שה-VOD יופיע...")
            entry["ended"] = now
            entry["pending_vod"] = True
            entry.pop("went_live", None)

        # ממתין ל-VOD
        if entry.get("pending_vod"):
            vod = newest_vod(cur)
            ended = entry.get("ended")
            waited = 0
            if ended:
                try:
                    delta = datetime.now(timezone.utc) - datetime.fromisoformat(ended)
                    waited = delta.total_seconds() / 60
                except Exception:
                    waited = 0

            # ה-VOD הופיע. נותנים לו כמה דקות להתיישב לפני שמפעילים.
            if vod and vod.get("id") and vod["id"] != entry.get("last_processed_vod"):
                if entry.get("vod_seen_id") != vod["id"]:
                    entry["vod_seen_id"] = vod["id"]
                    entry["vod_seen_at"] = now
                    log(f"נמצא VOD ל-{display}, ממתין {cfg['vod_settle']} דק' שיתייצב")
                    state[key] = entry
                    continue
                try:
                    seen_min = (datetime.now(timezone.utc)
                                - datetime.fromisoformat(entry["vod_seen_at"])).total_seconds() / 60
                except Exception:
                    seen_min = 999
                if seen_min < cfg["vod_settle"]:
                    state[key] = entry
                    continue

                url = vod.get("url") or f"https://kick.com/{slug}/videos/{vod['id']}"
                mins = (vod.get("duration") or 0)
                mins = mins / 60000 if mins > 100000 else mins / 60
                log(f"נמצא VOD ל-{display}: {vod['id']}  ({mins:.0f} דק')")
                notify(
                    f"<b>נמצא ה-VOD של {display}</b>\n"
                    f"{vod.get('title','')[:80]}\n"
                    f"{hhmm(mins)}\n"
                    f"{url}"
                )
                launch_pipeline(slug, display, url, dry, m3u8=vod.get("m3u8", ""))
                entry["last_processed_vod"] = vod["id"]
                entry["pending_vod"] = False
                entry.pop("ended", None)

            elif waited > cfg["vod_wait"] * 3:
                log(f"לא נמצא VOD ל-{display} אחרי {waited:.0f} דקות. מוותר.")
                notify(f"לא נמצא VOD ל-{display} אחרי {waited:.0f} דקות.")
                entry["pending_vod"] = False
                entry.pop("ended", None)

        state[key] = entry

        # דיווח על עבודה שהסתיימה
        try:
            report_finished(slug, display)
        except Exception:
            pass

    save_json(STATE_PATH, state)

    try:
        tend_failed_jobs(dry=dry)
    except Exception:
        log("טיפול בכישלונות נכשל:")
        traceback.print_exc()

    try:
        tend_budget_waiting(dry=dry)
    except Exception:
        log("טיפול בעבודות שממתינות לתקציב נכשל:")
        traceback.print_exc()

    live_now = [s["name"] for s in streamers
                if state.get(s.get("key") or s["slug"], {}).get("live")]
    log("משדרים כרגע: " + (", ".join(live_now) if live_now else "אף אחד"))


def show_status() -> None:
    state = load_json(STATE_PATH, {})
    wl = load_json(WATCHLIST_PATH, {})
    print(f"{'ערוץ':16} {'שם':8} {'פעיל':6} {'משדר':7} נבדק לאחרונה")
    print("-" * 70)
    for s in wl.get("streamers", []):
        st = state.get(s.get("key") or s.get("slug", ""), {})
        print(f"{s['slug']:16} {s.get('name',''):8} "
              f"{'כן' if s.get('active') else 'לא':6} "
              f"{'כן' if st.get('live') else 'לא':7} "
              f"{st.get('checked','—')}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--dry", action="store_true", help="בלי להפעיל את הצינור")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--no-bot", action="store_true", help="בלי הבוט שמקשיב לפקודות")
    args = ap.parse_args()

    if args.status:
        show_status()
        return

    cfg = settings()
    log(f"מנטר פועל. בדיקה כל {cfg['every']} דקות.")

    # הבוט רץ כחוט רקע באותו תהליך, כדי שלא יהיה חלון שני לתחזק.
    if not (args.once or args.no_bot):
        try:
            import tgbot2 as tgbot
            tgbot.start_thread()
            log("הבוט מקשיב לפקודות בטלגרם.")
        except Exception as exc:
            log(f"הבוט לא עלה: {exc}")

    if not args.once:
        notify("<b>המנטר הופעל</b>\n"
               "מעכשיו אקבל התראות על לייבים.\n"
               "אפשר לכתוב לי <b>/status</b> או <b>/health</b> בכל רגע.")

    try:
        resume_stuck_jobs(dry=args.dry)
    except Exception:
        log("בדיקת עבודות תקועות נכשלה:")
        traceback.print_exc()

    while True:
        try:
            one_round(dry=args.dry)
        except KeyboardInterrupt:
            log("נעצר.")
            break
        except Exception:
            log("שגיאה בסבב:")
            traceback.print_exc()

        if args.once:
            break
        time.sleep(cfg["every"] * 60)


if __name__ == "__main__":
    main()
