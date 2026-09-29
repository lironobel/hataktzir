"""
tgbot2.py - הצד השני של הטלגרם: מקשיב לפקודות ועונה

מה חדש מול tgbot.py:
    ✎ כותרת    כפתור ליד אישור/דחייה. שולחים כותרת חדשה בהודעה,
               והיא נכנסת ל-audio_segments.json, משנה את שם קובץ
               הקליפ ואת התיאור, ונרשמת גם בלולאת הלמידה - כך
               שהמנתח לומד מהתיקונים איך אתה רוצה שכותרת תיראה.
    📼 מלא     שולח את כל הקליפ מכווץ ל-45MB. הקידוד רץ ברקע כדי
               שהבוט לא ייתקע, ומגיע כשהוא מוכן.
    /pending   מה ממתין לאישור ברגע זה, בכל העבודות, עם אפשרות
               לשלוח את הקטעים שוב אם ההודעות נבלעו.

פקודות (אפשר גם בעברית חופשית):
    /status   מצב  מה קורה     תמונת מצב מהירה, מהקבצים. מיידי
    /pending  מה לאשר          קטעים שממתינים להחלטה שלך
    /live     מי בלייב          מי משדר לפי הבדיקה האחרונה
    /check    בדוק              בדיקה טרייה מול קיק. לוקח בערך חצי דקה
    /health   הכל עובד?         בדיקת בריאות של כל הרכיבים
    /jobs     עבודות            העבודות האחרונות והשלב שלהן
    /help     עזרה

הרצה:
    python scripts\\tgbot2.py            עצמאי
    python scripts\\tgbot2.py --once     סבב אחד, לבדיקה

בתוך המנטר הוא רץ כחוט רקע, כך שאין תהליך נוסף לתחזק.

הערה: רק תהליך אחד יכול לקרוא getUpdates. אם הבוט רץ גם בנפרד וגם
בתוך המנטר, הפקודות יתחלקו ביניהם באקראי. תריץ אחד.
"""

import os
import re
import sys
import json
import time
import shutil
import argparse
import threading
import subprocess
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

try:
    import requests
except ImportError:
    raise SystemExit("חסר requests. הרץ:  pip install requests")

from tg import load_config, save_config, notify, call, send_video  # noqa: E402
from tg import paused, set_paused  # noqa: E402

try:
    from preview import make_compact, clip_for, size_mb
except ImportError:                      # בלי preview.py הכפתור פשוט לא יופיע
    make_compact = clip_for = size_mb = None

try:
    import upload as yt_upload          # משימה 16
except Exception:                        # גם ImportError וגם ספריות גוגל חסרות
    yt_upload = None

try:
    import uploadq                       # תור ההעלאות המתוזמן
except Exception:
    uploadq = None

OFFSET_PATH = ROOT / "telegram_offset.json"
API = "https://api.telegram.org/bot{token}/{method}"


# ------------------------------------------------------------------ עזרים

def log(msg: str) -> None:
    print(f"[{datetime.now().strftime('%H:%M:%S')}] בוט: {msg}", flush=True)


def load_json(path: Path, default):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def ago(iso: str) -> str:
    """כמה זמן עבר מאז חותמת זמן. בשפה של בן אדם."""
    if not iso:
        return "לא ידוע"
    try:
        then = datetime.fromisoformat(iso)
        if then.tzinfo is None:
            then = then.replace(tzinfo=timezone.utc)
        secs = (datetime.now(timezone.utc) - then).total_seconds()
    except Exception:
        return iso
    if secs < 90:
        return f"לפני {int(secs)} שניות"
    if secs < 5400:
        return f"לפני {int(secs / 60)} דקות"
    if secs < 172800:
        return f"לפני {secs / 3600:.0f} שעות"
    return f"לפני {secs / 86400:.0f} ימים"


def have(cmd: str) -> bool:
    return shutil.which(cmd) is not None


# ------------------------------------------------------------- התשובות

def reply_status() -> str:
    state = load_json(ROOT / "monitor_state.json", {})
    wl = load_json(ROOT / "watchlist.json", {})
    active = [s for s in wl.get("streamers", []) if s.get("active")]

    checked = max((e.get("checked", "") for e in state.values()), default="")
    live = [s["name"] for s in active
            if state.get(s.get("key") or s.get("slug", ""), {}).get("live")]

    lines = ["<b>מצב המערכת</b>", ""]
    p = paused()
    if p:
        lines += [f"⏸ <b>מושהית מ-{p.get('since', '?')}</b> - שום דבר לא רץ. "
                  "<code>/resume</code> להחזיר.", ""]

    # האם המנטר בכלל חי
    fresh = False
    if checked:
        try:
            then = datetime.fromisoformat(checked)
            if then.tzinfo is None:
                then = then.replace(tzinfo=timezone.utc)
            fresh = (datetime.now(timezone.utc) - then).total_seconds() < 20 * 60
        except Exception:
            pass
    every = wl.get("check_every_minutes", 5)
    lines.append(("המנטר פועל" if fresh else "<b>המנטר לא בדק כבר הרבה זמן</b>")
                 + f" — בדיקה אחרונה {ago(checked)}, כל {every} דקות")
    lines.append(f"במעקב: {len(active)} סטרימרים")
    lines.append("")

    lines.append("<b>בלייב עכשיו:</b> " + (", ".join(live) if live else "אף אחד"))
    lines.append("")

    running = []
    jobs = ROOT / "jobs"
    if jobs.is_dir():
        for d in jobs.iterdir():
            if not d.is_dir():
                continue
            st = load_json(d / "state.json", {})
            stage = st.get("stage", "")
            if stage and stage not in ("done", "failed"):
                running.append(f"{d.name} — {stage_he(stage)} ({ago(st.get('updated',''))})")
    if running:
        lines.append("<b>בעבודה:</b>")
        lines.extend(running[:5])
    else:
        lines.append("<b>בעבודה:</b> כלום")

    waiting = pending_segments()
    if waiting:
        lines.append("")
        lines.append(f"<b>ממתינים לאישור: {len(waiting)}</b> — <code>/pending</code> לרשימה")

    failed = recent_failed(24)
    if failed:
        lines.append("")
        lines.append(f"<b>נכשלו ב-24 שעות: {len(failed)}</b> — <code>/failed</code> לפירוט")

    return "\n".join(lines)


def recent_failed(hours: int = 48) -> list:
    """עבודות שנכשלו לאחרונה, החדשה ראשונה."""
    out = []
    jobs = ROOT / "jobs"
    if not jobs.is_dir():
        return out
    for d in jobs.iterdir():
        if not d.is_dir():
            continue
        st = load_json(d / "state.json", {})
        if st.get("stage") != "failed":
            continue
        try:
            then = datetime.fromisoformat(st.get("updated", ""))
            if then.tzinfo is None:
                then = then.replace(tzinfo=timezone.utc)
            if (datetime.now(timezone.utc) - then).total_seconds() > hours * 3600:
                continue
        except Exception:
            pass
        out.append((d, st))
    out.sort(key=lambda x: x[1].get("updated", ""), reverse=True)
    return out


def find_job(name: str):
    """מאתר תיקיית עבודה לפי שם מלא או חלקי."""
    jobs = ROOT / "jobs"
    if not jobs.is_dir() or not name:
        return None
    name = name.strip()
    exact = jobs / name
    if exact.is_dir():
        return exact
    hits = [d for d in jobs.iterdir() if d.is_dir() and name.lower() in d.name.lower()]
    if len(hits) == 1:
        return hits[0]
    if hits:
        hits.sort(key=lambda d: d.stat().st_mtime, reverse=True)
        return hits[0]
    return None


def launch(job: Path) -> str:
    if paused():
        return "⏸ המערכת מושהית. קודם <code>/resume</code>, ואז שוב <code>/retry</code>."
    meta = load_json(job / "meta.json", {})
    slug = meta.get("streamer", "")
    vod = meta.get("vod_url", "")
    display = meta.get("display_name", slug)
    parts = job.name.rsplit("_", 1)
    date = parts[1] if len(parts) == 2 else ""
    if not (slug and vod and date):
        return "חסר מידע ב-meta.json, אי אפשר להפעיל."

    # 28.9: בלי נפילה ל-run9/run8. גרסה ישנה שרצה בשקט היא בדיוק המלכודת
    # של "איזו גרסה באמת רצה" (START_HERE). חסר run10 = שגיאה גלויה.
    runner = SCRIPTS / "run10.py"
    if not runner.exists():
        return "לא נמצא run10.py"

    st = load_json(job / "state.json", {})
    # /retry על עבודה שממתינה לתקציב = לירון מחליט לעקוף אותו
    bypass = st.get("stage") == "waiting_budget"
    st["stage"] = "retrying"
    st["retries"] = int(st.get("retries", 0)) + 1
    st["updated"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    st.pop("notified", None); st.pop("_failed_reported", None); st.pop("_gave_up", None)
    (job / "state.json").write_text(json.dumps(st, ensure_ascii=False, indent=1), encoding="utf-8")

    cmd = [sys.executable, str(runner), slug, date, "--vod", vod, "--display", display, "--resume"]
    if bypass:
        cmd += ["--analysis", "realtime"]
    env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
    flags = 0
    if os.name == "nt":
        flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    logfile = ROOT / "jobs" / f"{job.name}_pipeline.log"
    with logfile.open("a", encoding="utf-8") as f:
        f.write(f"\n===== הפעלה מהבוט {datetime.now().isoformat()} =====\n")
        subprocess.Popen(cmd, stdout=f, stderr=subprocess.STDOUT, cwd=str(ROOT),
                         env=env, creationflags=flags, close_fds=True)
    return (f"הופעל מחדש: <b>{display} / {date}</b>"
            + (" - עוקף את התקציב, ניתוח בזמן אמת" if bypass else "")
            + "\nאעדכן כשיסתיים או ייכשל.")


PENDING_PATH = ROOT / "pending_feedback.json"


def seg_title(job: Path, idx: int) -> str:
    segs = load_json(job / "audio_segments.json", [])
    if 1 <= idx <= len(segs):
        return segs[idx - 1].get("title", "")
    return ""


def clip_brief(job: Path, idx: int) -> str:
    """
    29.9 (בקשת לירון): בהודעת "עלה" - של מי, כמה זמן, ועל מה בשתי שורות,
    כדי לדעת איזה סרטון עלה בלי לפתוח אותו.
    """
    job = Path(job)
    segs = load_json(job / "audio_segments.json", [])
    seg = segs[idx - 1] if 1 <= idx <= len(segs) else {}
    desc = next((d for d in load_json(job / "clips" / "descriptions.json", [])
                 if isinstance(d, dict) and d.get("idx") == idx), {})
    who = load_json(job / "meta.json", {}).get("display_name", "")
    mins = desc.get("minutes")
    if not mins:
        try:
            ranges = load_json(job / "clips" / "ranges.json", [])
            r = next((x for x in ranges if x.get("idx") == idx), None)
            if r:
                mins = (sum(b - a for a, b in r.get("ranges", [])) + (r.get("lead") or 0)) / 60
        except Exception:
            mins = None
    about = (seg.get("topic") or seg.get("reason") or "").strip()
    head = " · ".join(x for x in [who, f"⏱ {float(mins):.0f} דק'" if mins else ""] if x)
    import html
    out = (head + ("\n" if head and about else "") + (about[:220] if about else "")).strip()
    return html.escape(out, quote=False)         # parse_mode=HTML: "<" בנושא שובר את ההודעה


def mark_segment(job: Path, idx: int, approved: bool, reason: str = "") -> None:
    path = job / "audio_segments.json"
    segs = load_json(path, [])
    if not (1 <= idx <= len(segs)):
        return
    segs[idx - 1]["approved"] = approved
    # תור ההעלאות מסודר לפי זה
    segs[idx - 1]["approved_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    if reason:
        segs[idx - 1]["reject_reason"] = reason
    path.write_text(json.dumps(segs, ensure_ascii=False, indent=1), encoding="utf-8")


def write_feedback(job: Path, idx: int, verdict: str, why: str) -> None:
    """הלב של הלמידה: כל החלטה נכנסת ל-names.json, והמנתח קורא משם."""
    path = ROOT / "names.json"
    data = load_json(path, {})
    fb = data.setdefault("feedback", {})
    entries = fb.setdefault("entries", [])
    segs = load_json(job / "audio_segments.json", [])
    seg = segs[idx - 1] if 1 <= idx <= len(segs) else {}
    entries.append({
        "job": job.name,
        "segment": idx,
        "title": seg.get("title", ""),
        "score_given": seg.get("score"),
        "verdict": verdict,
        "why": why,
    })
    fb["entries"] = entries[-40:]      # לא לנפח את הפרומפט לנצח
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")


def safe_name(text: str, limit: int = 60) -> str:
    """זהה ל-describe.py, כדי ששמות הקבצים יישארו עקביים."""
    text = re.sub(r'[<>:"/\\|?*]', "", str(text))
    text = re.sub(r"\s+", " ", text).strip()
    return text[:limit].strip() or "clip"


def rename_clip_files(job: Path, idx: int, new_title: str) -> list:
    """
    משנה את שם קובץ הקליפ ואת קובץ התיאור שלידו.
    שומר סיומות כמו __p1 שנוצרות כשקטע מורכב מכמה חלקים.
    """
    clips = job / "clips"
    if not clips.is_dir():
        return []
    renamed = []
    for p in sorted(clips.glob(f"{idx:02d} - *")):
        if p.is_dir():
            continue
        # 67/6 (29.9): גם __thumb. עד היום "01 - ישן__thumb.jpg" הפך ל-"01 - חדש.jpg",
        # thumb.py לא מצא אותו ובנה תמנייל אחר ברגע ההעלאה - תמנייל ידני אבד.
        m = re.search(r"(__p\d+|__thumb)$", p.stem)
        tail = m.group(1) if m else ""
        target = clips / f"{idx:02d} - {safe_name(new_title)}{tail}{p.suffix}"
        if target == p or target.exists():
            continue
        try:
            p.rename(target)
            renamed.append(target.name)
        except Exception as exc:
            log(f"לא הצלחתי לשנות שם ל-{p.name}: {exc}")
    return renamed


def set_title(job: Path, idx: int, new_title: str):
    """
    משנה כותרת בכל שלושת המקומות שמחזיקים אותה, ומחזיר (הישנה, קבצים ששונו).
    audio_segments.json הוא המקור, ואחריו שם הקובץ ו-descriptions.json.
    """
    path = job / "audio_segments.json"
    segs = load_json(path, [])
    if not (1 <= idx <= len(segs)):
        return None, []

    old = segs[idx - 1].get("title", "")
    segs[idx - 1]["title"] = new_title
    segs[idx - 1].setdefault("title_was", old)
    path.write_text(json.dumps(segs, ensure_ascii=False, indent=1), encoding="utf-8")

    renamed = rename_clip_files(job, idx, new_title)

    dpath = job / "clips" / "descriptions.json"
    rows = load_json(dpath, [])
    if isinstance(rows, list) and rows:
        for r in rows:
            if r.get("idx") == idx:
                r["title"] = new_title
                mp4 = next((n for n in renamed if n.lower().endswith(".mp4")), "")
                if mp4:
                    r["file"] = mp4
        try:
            dpath.write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
        except Exception as exc:
            log(f"descriptions.json לא עודכן: {exc}")

    return old, renamed


def stamp_message(chat_id: str, msg: dict, suffix: str, keep_buttons: bool = False) -> None:
    """
    מוסיף שורה להודעה שכבר נשלחה. הודעת וידאו נערכת דרך הכיתוב
    ולא דרך הטקסט, ולכן צריך לבחור את השיטה לפי סוג ההודעה.
    """
    mid = msg.get("message_id")
    if not mid:
        return
    params = {"chat_id": chat_id, "message_id": mid, "parse_mode": "HTML"}
    if keep_buttons and msg.get("reply_markup"):
        params["reply_markup"] = json.dumps(msg["reply_markup"])
    if msg.get("caption") is not None or msg.get("video"):
        call("editMessageCaption", caption=(msg.get("caption", "") + suffix)[:1000], **params)
    else:
        call("editMessageText", text=(msg.get("text", "") + suffix)[:4000], **params)


def send_full_clip(job: Path, idx: int) -> None:
    """
    שולח את הקליפ המלא. רץ בחוט נפרד כי הקידוד לוקח דקה-שתיים
    ואסור שהבוט יפסיק להקשיב בינתיים.
    """
    title = seg_title(job, idx)
    clip = clip_for(job, idx) if clip_for else None
    if not clip:
        notify(f"[{idx}] {title[:50]} — לא מצאתי את קובץ הקליפ.")
        return
    notify(f"מכין את <b>[{idx}] {title[:50]}</b> לשליחה, רגע...", silent=True)
    try:
        out, mb = make_compact(clip)
    except Exception as exc:
        notify(f"הכנת הקובץ נכשלה:\n<code>{exc}</code>")
        return
    if not out:
        notify(f"<b>[{idx}] {title[:50]}</b>\n"
               f"הקליפ ארוך מדי מכדי להיכנס למגבלת 50MB של טלגרם באיכות סבירה.\n"
               f"<code>{clip}</code>")
        return
    buttons = [[
        {"text": "✓ אשר", "callback_data": f"ap:{job.name}:{idx}"},
        {"text": "✗ דחה", "callback_data": f"rj:{job.name}:{idx}"},
    ]]
    if not send_video(out, caption=f"<b>[{idx}] {title}</b>\n<i>הקליפ המלא, {mb:.0f}MB</i>",
                      buttons=buttons):
        notify(f"השליחה נכשלה. הקובץ מוכן כאן:\n<code>{out}</code>")


# ------------------------------------------------------ העלאה ליוטיוב (16)
#
# ✓ לא מפרסם. הוא מעלה כ-unlisted ומחזיר קישור עם כפתור פרסום.
# ההפרדה הזאת היא הבלם היחיד בצינור שכולו אוטומטי: הכותרת, התיאור
# והתמנייל נבנים בלי אדם, ולכן חייבת להיות נקודה אחת שבה מסתכלים
# על התוצאה לפני שהיא באוויר.

def publish_buttons(job_name: str, idx: int) -> list:
    return [[{"text": "🌍 פרסם עכשיו", "callback_data": f"pb:{job_name}:{idx}"}]]


def enqueue(job: Path, idx: int) -> None:
    """
    מה שקורה אחרי ✓. לא מעלה ישירות: מוסר לתור.
    אם התור פנוי והשעה מתאימה, `tick` יעלה מיד ממילא.
    """
    title = seg_title(job, idx)
    if not uploadq:
        do_upload(job, idx)              # בלי התור, ההתנהגות הישנה
        return
    try:
        rows = uploadq.schedule()
        due_now = uploadq.due()
    except Exception as exc:
        notify(f"[{idx}] {title[:50]} — התור לא זמין:\n<code>{exc}</code>")
        do_upload(job, idx)
        return

    place = len(rows)
    if due_now and place <= 1:
        do_upload(job, idx)
        return

    # הזמן של **הקטע הזה**, לא של ראש התור. "מקום 3, עולה בעוד רגע"
    # זו הודעה שמשקרת, והיא תתגלה רק כשהסרטון לא יופיע.
    mine = next((r for r in rows
                 if r["job"] == job.name and r["idx"] == idx), None)
    eta = mine["eta"] if mine else uploadq.next_slot()

    notify(f"<b>[{idx}] {title[:50]}</b>\n"
           f"נכנס לתור, מקום {place}. צפוי לעלות {uploadq.hhmm(eta)}.\n"
           f"<code>/queue</code> מראה את כל התור. "
           f"<code>/upload {job.name} {idx}</code> עוקף אותו.", silent=True)


def do_upload(job: Path, idx: int) -> None:
    """רץ בחוט נפרד. העלאה של קליפ לוקחת דקות, והבוט חייב להמשיך להקשיב."""
    title = seg_title(job, idx)
    if paused():
        notify(f"<b>[{idx}] {title[:50]}</b>\n⏸ המערכת מושהית - לא מעלה. "
               "הקטע נשאר בתור ויעלה אחרי <code>/resume</code>.")
        return
    if not yt_upload:
        notify(f"[{idx}] {title[:50]} — אושר, אבל ההעלאה לא מוגדרת עדיין.\n"
               "חסר <code>upload.py</code> או ספריות גוגל. ראה YOUTUBE_SETUP.md.")
        return

    try:
        left = yt_upload.quota_left()
    except Exception:
        left = 1
    if left <= 0:
        notify(f"<b>[{idx}] {title[:50]}</b>\n"
               "המכסה היומית של יוטיוב נגמרה (6 העלאות ליום).\n"
               "הקטע נשאר בתור ויעלה מחר לבד.")
        return

    notify(f"מעלה את <b>[{idx}] {title[:50]}</b> ליוטיוב...", silent=True)
    try:
        res = yt_upload.upload_clip(job, idx)
    except Exception as exc:
        notify(f"<b>[{idx}] {title[:50]}</b>\nההעלאה נפלה:\n<code>{exc}</code>", important=True)
        log(f"העלאה נפלה: {job.name} [{idx}]: {exc}")
        return

    if res.get("busy"):
        notify(f"<b>[{idx}] {title[:50]}</b>\nכבר בהעלאה ממקום אחר - לא מעלה פעם שנייה.")
        return
    if not res.get("ok"):
        notify(f"<b>[{idx}] {title[:50]}</b>\n"
               f"ההעלאה נכשלה:\n<code>{res.get('error','')}</code>\n"
               f"<code>/upload {job.name} {idx}</code> ינסה שוב.", important=True)
        log(f"העלאה נכשלה: {job.name} [{idx}]: {res.get('error','')}")
        return

    if res.get("already"):
        notify(f"<b>[{idx}] {title[:50]}</b>\nכבר היה מועלה: {res['url']}",
               buttons=publish_buttons(job.name, idx))
        return

    brief = clip_brief(job, idx)
    head = f"<b>[{idx}] {title[:60]}</b>\n" + (f"{brief}\n" if brief else "") + res['url']
    if res.get("locked"):
        # יוטיוב נעל את הסרטון בסטטוס אחר ממה שביקשנו. זה כמעט תמיד
        # אומר שהפרויקט עוד לא עבר audit, ואז גם כפתור הפרסום ייכשל.
        notify(f"{head}\n\n⚠ ביקשנו <b>{res['asked']}</b> ויוטיוב קבע "
               f"<b>{res['privacy']}</b>.\nזה הסימן שהפרויקט עוד לא עבר audit - "
               f"ראה YOUTUBE_SETUP.md שלב 8.", important=True)
        log(f"הועלה נעול: {job.name} [{idx}] {res['id']}")
        return

    thumb = ("" if res.get("thumb_ok")
             else f"\n⚠ תמנייל לא עלה: {res.get('thumb_error','')[:120]}")
    if res.get("privacy") == "public":
        notify(f"{head}\n\nעלה ו<b>באוויר</b>.{thumb}", important=bool(thumb))
    else:
        notify(f"{head}\n\nהסרטון למעלה כ-<b>{res.get('privacy','unlisted')}</b> "
               f"ועוד לא באוויר. תסתכל עליו, ואם הוא בסדר - לחץ פרסם.{thumb}",
               buttons=publish_buttons(job.name, idx), important=True)
    log(f"הועלה: {job.name} [{idx}] {res['id']}")


def do_publish(job: Path, idx: int, chat_id: str, msg: dict) -> None:
    title = seg_title(job, idx)
    if not yt_upload:
        notify("ההעלאה לא מוגדרת. ראה YOUTUBE_SETUP.md.")
        return
    try:
        res = yt_upload.publish(job, idx)
    except Exception as exc:
        notify(f"<b>[{idx}] {title[:50]}</b>\nהפרסום נפל:\n<code>{exc}</code>", important=True)
        return
    if not res.get("ok"):
        notify(f"<b>[{idx}] {title[:50]}</b>\n"
               f"הפרסום נכשל:\n<code>{res.get('error','')}</code>", important=True)
        log(f"פרסום נכשל: {job.name} [{idx}]: {res.get('error','')}")
        return
    stamp_message(chat_id, msg, "\n\n🌍 באוויר")
    notify(f"<b>[{idx}] {title[:60]}</b>\nבאוויר: {res['url']}")
    log(f"פורסם: {job.name} [{idx}] {res['id']}")


def reply_unlisted(arg: str = "") -> str:
    """מה הועלה ועוד לא פורסם. זה הדוח שהתזכורת מסתמכת עליו."""
    if not yt_upload:
        return "ההעלאה ליוטיוב עוד לא מוגדרת. ראה YOUTUBE_SETUP.md."
    try:
        waiting = yt_upload.pending_publish()
    except Exception as exc:
        return f"לא הצלחתי לקרוא את מצב ההעלאות:\n<code>{exc}</code>"
    if not waiting:
        return "אין סרטונים שממתינים לפרסום. הכל או באוויר או עוד לא אושר."

    # הודעה נפרדת לכל סרטון, כדי שלכל אחד יהיה כפתור פרסום משלו.
    # רשימה אחת ארוכה נראית נוח אבל אז אין מה ללחוץ.
    for w in waiting[:10]:
        notify(f"<b>[{w['idx']}] {w['title'][:60]}</b>\n"
               f"{w['url']}\nהועלה {ago(w['uploaded_at'])} · {w['privacy']}",
               buttons=publish_buttons(w["job"], w["idx"]), silent=True)
    more = "  (מוצגים 10 הראשונים)" if len(waiting) > 10 else ""
    head = ("סרטון אחד למעלה ועוד לא באוויר." if len(waiting) == 1
            else f"{len(waiting)} סרטונים למעלה ועוד לא באוויר.")
    return f"<b>{head}</b>{more}"


def reply_upload(arg: str = "") -> str:
    """העלאה ידנית: /upload שם_עבודה מספר. בלי ארגומנטים - סטטוס."""
    if not yt_upload:
        return "ההעלאה ליוטיוב עוד לא מוגדרת. ראה YOUTUBE_SETUP.md."
    parts = (arg or "").split()
    if len(parts) < 2:
        try:
            return (f"ערוץ: {yt_upload.channel_name() or 'לא מחובר'}\n"
                    f"הועלו היום: {len(yt_upload.uploads_today())}  ·  "
                    f"נשארו בערך {yt_upload.quota_left()}\n\n"
                    f"<code>/upload שם_עבודה מספר</code> מעלה קטע.")
        except Exception as exc:
            return f"<code>{exc}</code>"
    job = find_job(parts[0])
    if not job:
        return f"לא מצאתי עבודה בשם {parts[0]}."
    try:
        idx = int(parts[1])
    except ValueError:
        return "מספר הקטע צריך להיות מספר."
    threading.Thread(target=do_upload, args=(job, idx),
                     daemon=True, name=f"upload-{idx}").start()
    return "מעלה..."


def reply_publish(arg: str = "") -> str:
    if not yt_upload:
        return "ההעלאה ליוטיוב עוד לא מוגדרת."
    parts = (arg or "").split()
    if len(parts) < 2:
        return "שימוש: <code>/publish שם_עבודה מספר</code>"
    job = find_job(parts[0])
    if not job:
        return f"לא מצאתי עבודה בשם {parts[0]}."
    try:
        idx = int(parts[1])
    except ValueError:
        return "מספר הקטע צריך להיות מספר."
    res = yt_upload.publish(job, idx)
    return (f"באוויר: {res['url']}" if res.get("ok")
            else f"נכשל:\n<code>{res.get('error','')}</code>")


def reply_queue(arg: str = "") -> str:
    """מה בתור ההעלאות ומתי כל אחד יעלה."""
    if not uploadq:
        return "תור ההעלאות לא זמין. חסר <code>uploadq.py</code>."
    try:
        return uploadq.report()
    except Exception as exc:
        return f"קריאת התור נכשלה:\n<code>{exc}</code>"


def reply_budget(arg: str = "") -> str:
    """כמה הוצאנו החודש, והאם התקציב עוצר את הערוץ (משימה 57, 28.9)."""
    try:
        import budget
        return budget.report()
    except Exception as exc:
        return f"דוח התקציב נכשל:\n<code>{exc}</code>"


QUEUE_TICK_SEC = 600         # כל כמה זמן החוט בודק אם הגיע הזמן להעלות


def queue_loop(stop: threading.Event = None) -> None:
    """
    חוט הרקע שמוציא פריט אחד מהתור כשמותר. כל ההחלטות - מכסה,
    פער, שעות - יושבות ב-`uploadq.next_slot`, וכאן רק מפעילים.
    """
    while not (stop and stop.is_set()):
        time.sleep(QUEUE_TICK_SEC)
        if not uploadq:
            continue
        try:                                  # דוח תקציב שבועי, ראשון בערב
            import budget
            if budget.weekly_due():
                notify(budget.report())
                budget.log_event("weekly_report")
        except Exception as exc:
            log(f"דוח תקציב שבועי נכשל: {exc}")
        try:
            res = uploadq.tick()
        except Exception as exc:
            log(f"תור ההעלאות נפל: {exc}")
            continue
        if not res.get("did"):
            continue

        title = (res.get("title") or "")[:60]
        if res.get("auth"):
            notify(f"<b>⚠ תור ההעלאות מושהה</b>\n{res.get('error','')}\n\n"
                   f"הקטע [{res['idx']}] {title} לא נספר ככישלון ונשאר ראשון בתור.",
                   important=True)
            log("תור: ההרשאה ליוטיוב פגה, התור מושהה")
            continue
        if not res.get("ok"):
            if res.get("gave_up"):
                tail = (f"נכשל {res.get('fails')} פעמים ויצא מהתור.\n"
                        f"<code>/upload {res['job']} {res['idx']}</code> אחרי שתתקן.")
            else:
                tail = f"אנסה שוב מאוחר יותר (ניסיון {res.get('fails', 1)})."
            notify(f"<b>[{res['idx']}] {title}</b>\n"
                   f"ההעלאה מהתור נכשלה:\n<code>{res.get('error','')}</code>\n"
                   f"{tail}", important=True)
            log(f"תור: העלאה נכשלה {res['job']} [{res['idx']}]")
            continue

        left = res.get("left", 0)
        tail = f"\nנשארו בתור: {left}" if left else "\nהתור ריק עכשיו."
        thumb = ("" if res.get("thumb_ok")
                 else f"\n⚠ תמנייל לא עלה: {res.get('thumb_error','')[:120]}")
        # 25.9: לירון - ✓ הוא האישור היחיד. youtube.json privacy=public,
        # ואז הסרטון באוויר מיד ואין כפתור פרסום.
        live = res.get("privacy") == "public"
        try:
            brief = clip_brief(ROOT / "jobs" / res["job"], res["idx"])
        except Exception:
            brief = ""
        notify(f"<b>[{res['idx']}] {title}</b>\n" + (f"{brief}\n" if brief else "")
               + f"{res['url']}\n\n"
               + (f"עלה מהתור ו<b>באוויר</b>.{thumb}{tail}" if live else
                  f"עלה מהתור כ-<b>{res.get('privacy','unlisted')}</b> "
                  f"ועוד לא באוויר.{thumb}{tail}"),
               buttons=None if live else publish_buttons(res["job"], res["idx"]),
               important=not live or bool(thumb))
        log(f"תור: הועלה {res['job']} [{res['idx']}] {res.get('id','')}")


REMIND_AFTER_HOURS = 6       # כמה זמן סרטון יושב unlisted לפני תזכורת
REMIND_EVERY_SEC = 3600      # כל כמה זמן החוט מתעורר ובודק


def reminder_loop(stop: threading.Event = None) -> None:
    """
    חוט רקע שמזכיר על סרטונים שהועלו ולא פורסמו. בלי זה, סרטון
    שהועלה ב-2 בלילה יכול לשבת unlisted שבוע בלי שאף אחד ישים לב.
    מזכיר פעם אחת לכל סרטון, לא בכל סבב.
    """
    from datetime import timedelta
    reminded = set()
    while not (stop and stop.is_set()):
        time.sleep(REMIND_EVERY_SEC)
        if not yt_upload:
            continue
        try:
            waiting = yt_upload.pending_publish()
        except Exception as exc:
            log(f"בדיקת ממתינים לפרסום נכשלה: {exc}")
            continue
        now = datetime.now(timezone.utc)
        due = []
        for w in waiting:
            key = (w["job"], w["idx"])
            if key in reminded:
                continue
            try:
                when = datetime.fromisoformat(w["uploaded_at"])
            except Exception:
                continue
            if now - when >= timedelta(hours=REMIND_AFTER_HOURS):
                due.append(w)
                reminded.add(key)
        if not due:
            continue
        head = ("סרטון אחד למעלה ועוד לא באוויר" if len(due) == 1
                else f"{len(due)} סרטונים למעלה ועוד לא באוויר")
        lines = [f"<b>תזכורת: {head}</b>", ""]
        for w in due:
            lines.append(f"[{w['idx']}] {w['title'][:55]}\n{w['url']}")
        lines.append("")
        lines.append("<code>/unlisted</code> מראה את הרשימה המלאה עם כפתורים.")
        notify("\n".join(lines), important=True)


def handle_callback(cb: dict) -> None:
    """לחיצה על אחד הכפתורים מתחת לקטע."""
    data = cb.get("data", "")
    cb_id = cb.get("id", "")
    msg = cb.get("message") or {}
    chat_id = str((msg.get("chat") or {}).get("id", ""))

    cfg = load_config()
    if str(cfg.get("chat_id", "")) not in ("", chat_id):
        call("answerCallbackQuery", callback_query_id=cb_id)
        return

    try:
        action, job_name, idx_s = data.split(":", 2)
        idx = int(idx_s)
    except ValueError:
        call("answerCallbackQuery", callback_query_id=cb_id, text="נתון לא מזוהה")
        return

    job = ROOT / "jobs" / job_name
    if not job.is_dir():
        call("answerCallbackQuery", callback_query_id=cb_id, text="העבודה לא נמצאה")
        return

    title = seg_title(job, idx)

    if action == "ap":
        mark_segment(job, idx, True)
        write_feedback(job, idx, "אושר להעלאה", "")
        call("answerCallbackQuery", callback_query_id=cb_id, text="אושר ✓, מעלה...")
        stamp_message(chat_id, msg, "\n\n✓ אושר")
        log(f"אושר: {job_name} [{idx}]")
        # דרך התור, לא ישירות. המכסה היא שש ביום, ולילה של שלושה
        # סטרימרים מייצר יותר מזה.
        threading.Thread(target=enqueue, args=(job, idx),
                         daemon=True, name=f"enqueue-{idx}").start()

    elif action == "pb":
        call("answerCallbackQuery", callback_query_id=cb_id, text="מפרסם...")
        threading.Thread(target=do_publish, args=(job, idx, chat_id, msg),
                         daemon=True, name=f"publish-{idx}").start()

    elif action == "rj":
        mark_segment(job, idx, False)
        set_pending("reject", job_name, idx, title)
        call("answerCallbackQuery", callback_query_id=cb_id, text="נדחה ✗")
        stamp_message(chat_id, msg, "\n\n✗ נדחה")
        notify(f"למה נדחה <b>[{idx}] {title[:50]}</b>?\n"
               f"כתוב סיבה קצרה - היא תלמד את המנתח. או שלח <b>דלג</b>.")
        log(f"נדחה: {job_name} [{idx}], ממתין לסיבה")

    elif action == "ti":
        set_pending("title", job_name, idx, title)
        call("answerCallbackQuery", callback_query_id=cb_id, text="שלח כותרת חדשה")
        notify(f"כותרת חדשה ל-<b>[{idx}]</b>?\n"
               f"הנוכחית: <i>{title}</i>\n\n"
               f"שלח את הכותרת בהודעה הבאה. <b>בטל</b> כדי להשאיר כמו שהיא.")
        log(f"ממתין לכותרת חדשה: {job_name} [{idx}]")

    elif action == "fl":
        if not make_compact:
            call("answerCallbackQuery", callback_query_id=cb_id, text="חסר preview.py")
            return
        call("answerCallbackQuery", callback_query_id=cb_id, text="מכין את הקובץ...")
        threading.Thread(target=send_full_clip, args=(job, idx),
                         daemon=True, name=f"full-{idx}").start()

    elif action == "rs":
        call("answerCallbackQuery", callback_query_id=cb_id, text="שולח שוב...")
        threading.Thread(target=lambda: notify(resend_job(job)),
                         daemon=True, name="resend").start()


PENDING_TTL_MINUTES = 45      # אחרי זה ההודעה הבאה כבר לא שייכת ללחיצה ההיא


def set_pending(mode: str, job_name: str, idx: int, title: str) -> None:
    PENDING_PATH.write_text(json.dumps({
        "mode": mode, "job": job_name, "idx": idx, "title": title,
        "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }, ensure_ascii=False), encoding="utf-8")


def resend_job(job: Path) -> str:
    """שולח שוב את הקטעים שעוד לא הוחלט עליהם בעבודה מסוימת."""
    from approve2 import send_for_approval
    try:
        n = send_for_approval(job)
    except Exception as exc:
        return f"השליחה נכשלה:\n<code>{exc}</code>"
    return f"נשלחו {n} קטעים." if n else "אין קטעים פתוחים בעבודה הזאת."


def maybe_pending_reply(text: str) -> bool:
    """
    ההודעה החופשית הבאה אחרי לחיצה על ✗ או ✎ שייכת לה.
    זה מה שמאפשר לתת סיבה או כותרת בלי לזכור שום תחביר.
    """
    if not PENDING_PATH.exists():
        return False
    pend = load_json(PENDING_PATH, {})

    # לחיצה מלפני שעתיים לא אמורה לחטוף הודעה אקראית
    try:
        then = datetime.fromisoformat(pend.get("at", ""))
        if then.tzinfo is None:
            then = then.replace(tzinfo=timezone.utc)
        if (datetime.now(timezone.utc) - then).total_seconds() > PENDING_TTL_MINUTES * 60:
            PENDING_PATH.unlink(missing_ok=True)
            return False
    except Exception:
        pass                          # בלי חותמת זמן - התנהגות ישנה

    PENDING_PATH.unlink(missing_ok=True)
    job = ROOT / "jobs" / pend.get("job", "")
    idx = int(pend.get("idx", 0))
    if not job.is_dir() or not idx:
        return False

    body = text.strip()
    mode = pend.get("mode", "reject")

    if mode == "title":
        if body in ("בטל", "cancel", "-", "לא"):
            notify("בסדר, הכותרת נשארה.")
            return True
        if len(body) < 4:
            notify("זה קצר מדי לכותרת. לחץ שוב על ✎ ונסה שוב.")
            return True
        old, renamed = set_title(job, idx, body)
        if old is None:
            notify("לא מצאתי את הקטע.")
            return True
        write_feedback(job, idx, "כותרת שונתה", f"מ: {old} → ל: {body}")
        note = f"\nשם הקובץ עודכן ({len(renamed)} קבצים)." if renamed else ""
        notify(f"<b>[{idx}]</b> הכותרת עודכנה:\n"
               f"<s>{old[:70]}</s>\n<b>{body[:90]}</b>{note}\n"
               f"<i>גם המנתח ילמד מהתיקון הזה.</i>")
        log(f"כותרת שונתה: {job.name} [{idx}]")
        return True

    if body in ("דלג", "skip", "לא", "-"):
        write_feedback(job, idx, "נדחה", "")
        notify("בסדר, נרשם בלי סיבה.")
        return True

    mark_segment(job, idx, False, reason=body)
    write_feedback(job, idx, "נדחה", body)
    notify(f"נרשם. המנתח יקרא את זה בלייב הבא:\n<i>{body[:120]}</i>")
    log(f"סיבת דחייה: {body[:60]}")
    return True


# שם ישן, כדי שקוד אחר שמייבא אותו לא יישבר
maybe_feedback_reason = maybe_pending_reply


STAGES = {
    "resolving_url": "מאתר כתובת",
    "downloading_audio": "מוריד אודיו",
    "transcribing": "מתמלל",
    "waiting_gpu": "בתור ל-GPU",
    "retrying": "ניסיון חוזר",
    "analyzing": "מנתח",
    "analyzing_batch": "מנתח ב-batch (עד שעה בד\"כ)",
    "waiting_budget": "ממתין לתקציב",
    "thumbnails": "בונה תמניילים",
    "cutting": "חותך",
    "describing": "כותב תיאורים",
    "awaiting_cut": "ממתין לחיתוך",
    "done": "הסתיים",
    "failed": "נכשל",
}


def stage_he(stage: str) -> str:
    return STAGES.get(stage, stage)


def reply_live() -> str:
    state = load_json(ROOT / "monitor_state.json", {})
    wl = load_json(ROOT / "watchlist.json", {})
    rows = []
    for s in wl.get("streamers", []):
        e = state.get(s.get("key") or s.get("slug", ""), {})
        if e.get("live"):
            rows.append(f"<b>{s['name']}</b> — {e.get('viewers',0)} צופים\n"
                        f"{(e.get('title') or '')[:70]}\n"
                        f"{channel_url(s)}")
    if not rows:
        checked = max((e.get("checked", "") for e in state.values()), default="")
        return f"אף אחד לא משדר.\nנבדק {ago(checked)}."
    return "<b>משדרים עכשיו</b>\n\n" + "\n\n".join(rows)


def channel_url(s: dict) -> str:
    slug = (s.get("slug") or "").strip()
    platform = (s.get("platform") or "kick").lower()
    if not slug:
        return ""
    if platform == "twitch":
        return f"https://www.twitch.tv/{slug}"
    if platform == "youtube":
        return f"https://www.youtube.com/{slug if slug.startswith('@') else '@' + slug}"
    return f"https://kick.com/{slug}"


def reply_check() -> str:
    """בדיקה טרייה בכל הפלטפורמות. איטית, ולכן מודיעים לפני."""
    wl = load_json(ROOT / "watchlist.json", {})
    active = [s for s in wl.get("streamers", [])
              if s.get("active") and (s.get("slug") or "").strip()]
    if not active:
        return "אין סטרימרים פעילים עם מזהה ב-watchlist.json"

    try:
        from livecheck import check as live_check
    except ImportError:
        return "לא נמצא livecheck.py"

    try:
        res = live_check(active, want_vod=False)
    except Exception as exc:
        return f"הבדיקה נכשלה: {exc}"

    names = {(s.get("key") or s["slug"]): s.get("name", s["slug"]) for s in active}
    live, off, broken = [], [], []
    for slug, e in res.items():
        label = names.get(slug, slug)
        if e.get("error"):
            broken.append(f"{label} ({e['platform']}) — {e['error']}")
        elif e.get("live"):
            live.append(f"<b>{label}</b> ({e['platform']}) — {e.get('viewers',0)} צופים\n"
                        f"{(e.get('title') or '')[:60]}")
        else:
            off.append(label)

    out = ["<b>בדיקה טרייה</b>", ""]
    if live:
        out.append("משדרים:")
        out.extend(live)
    else:
        out.append("אף אחד לא משדר.")
    if off:
        out.append("")
        out.append("לא משדרים: " + ", ".join(off))
    if broken:
        out.append("")
        out.append("<b>לא הצלחתי לבדוק:</b>")
        out.extend(broken)
    return "\n".join(out)


def reply_health() -> str:
    """זו התשובה ל'הכל עובד?'. בודקת כל רכיב בנפרד."""
    ok, bad = [], []

    def check(label: str, passed: bool, detail: str = ""):
        (ok if passed else bad).append(f"{label}{' — ' + detail if detail else ''}")

    check("ffmpeg", have("ffmpeg"), "" if have("ffmpeg") else "לא מותקן / לא ב-PATH")
    check("yt-dlp", have("yt-dlp"), "" if have("yt-dlp") else "לא מותקן / לא ב-PATH")

    key = os.environ.get("ANTHROPIC_API_KEY", "")
    check("מפתח Anthropic", bool(key), "" if key else "לא מוגדר, הניתוח לא ירוץ")

    try:
        import playwright  # noqa: F401
        check("playwright", True)
    except ImportError:
        check("playwright", False, "בלי זה אין גישה לקיק")

    try:
        import faster_whisper  # noqa: F401
        check("faster-whisper", True)
    except ImportError:
        check("faster-whisper", False, "בלי זה אין תמלול")

    # התמנייל. ב-15.9 חסר Pillow הרג את כל הצינור ו-/health אמר "הכל עובד".
    try:
        import PIL  # noqa: F401
        check("Pillow", True)
    except ImportError:
        check("Pillow", False, "בלי זה אין תמנייל. pip install pillow")
    # 27.9: `import cv2` עבר אבל בלי CascadeClassifier, ו-/health הראה ירוק
    # בזמן שכל התמניילים נפלו. בודקים שהמודול שלם, לא רק שהוא נטען.
    try:
        import cv2
        missing = [n for n in ("CascadeClassifier", "cvtColor", "Laplacian", "data")
                   if not hasattr(cv2, n)]
        if missing:
            # OpenCV 5 הוציא את CascadeClassifier מהחבילה הראשית (27.9)
            check("opencv", False, f"{getattr(cv2, '__version__', '?')} בלי "
                  + ", ".join(missing) + '. pip install "opencv-python<5"')
        else:
            check("opencv", True)
    except Exception:
        check("opencv", False, "התמנייל ייקח חיתוך מרכזי. pip install opencv-python")

    # ההרשאה ליוטיוב. 25.9: פגה אחרי 7 ימים (אפליקציה במצב Testing),
    # ו-/health לא ידע על זה - גילינו רק כשההעלאה נכשלה.
    if yt_upload:
        try:
            name = yt_upload.channel_name(raise_auth=True)
            check("הרשאת יוטיוב", bool(name),
                  name if name else "לא הצלחתי לקרוא את הערוץ")
        except Exception as exc:
            check("הרשאת יוטיוב", False,
                  "פגה - python scripts\\ytauth.py" if yt_upload.is_auth_error(exc)
                  else str(exc)[:120])

    # כרטיס מסך
    try:
        r = subprocess.run(["nvidia-smi", "--query-gpu=name,memory.used,memory.total",
                            "--format=csv,noheader"],
                           capture_output=True, text=True, timeout=15)
        if r.returncode == 0 and r.stdout.strip():
            check("GPU", True, r.stdout.strip().splitlines()[0])
        else:
            check("GPU", False, "nvidia-smi לא ענה")
    except Exception:
        check("GPU", False, "nvidia-smi לא נמצא")

    # מקום בדיסק
    try:
        total, used, free = shutil.disk_usage(str(ROOT))
        gb = free / 1e9
        check("מקום בדיסק", gb > 25, f"{gb:.0f}GB פנוי"
              + ("" if gb > 25 else " — צר, לייב שלם צורך בערך 20GB"))
    except Exception:
        pass

    # המנטר
    state = load_json(ROOT / "monitor_state.json", {})
    checked = max((e.get("checked", "") for e in state.values()), default="")
    fresh = False
    if checked:
        try:
            then = datetime.fromisoformat(checked)
            if then.tzinfo is None:
                then = then.replace(tzinfo=timezone.utc)
            fresh = (datetime.now(timezone.utc) - then).total_seconds() < 20 * 60
        except Exception:
            pass
    check("המנטר", fresh, f"בדיקה אחרונה {ago(checked)}")

    wl = load_json(ROOT / "watchlist.json", {})
    act = [s for s in wl.get("streamers", []) if s.get("active")]
    ready = [s for s in act if (s.get("slug") or "").strip()]
    by = {}
    for s in ready:
        p = (s.get("platform") or "kick").lower()
        by[p] = by.get(p, 0) + 1
    detail = ", ".join(f"{v} {k}" for k, v in sorted(by.items()))
    check("רשימת מעקב", bool(ready), f"{len(ready)} פעילים ({detail})")
    if len(act) > len(ready):
        missing = [s.get("name", "?") for s in act if not (s.get("slug") or "").strip()]
        check("רשומות בלי מזהה", False, ", ".join(missing) + " — לא ייבדקו")

    failed = recent_failed(24)
    check("עבודות ב-24 שעות", not failed,
          f"{len(failed)} נכשלו — /failed" if failed else "בלי כישלונות")

    lines = []
    if bad:
        lines.append("<b>יש בעיות</b>")
        lines.append("")
        lines.extend(f"✗ {b}" for b in bad)
        lines.append("")
    else:
        lines.append("<b>הכל עובד</b>")
        lines.append("")
    lines.extend(f"✓ {o}" for o in ok)
    return "\n".join(lines)


def reply_jobs() -> str:
    jobs = ROOT / "jobs"
    if not jobs.is_dir():
        return "אין תיקיית jobs."
    dirs = sorted([d for d in jobs.iterdir() if d.is_dir()],
                  key=lambda d: d.stat().st_mtime, reverse=True)[:6]
    if not dirs:
        return "אין עבודות עדיין."

    lines = ["<b>עבודות אחרונות</b>"]
    for d in dirs:
        st = load_json(d / "state.json", {})
        clips = len(list((d / "clips").glob("*.mp4"))) if (d / "clips").is_dir() else 0
        line = f"<b>{d.name}</b>\n{stage_he(st.get('stage','—'))}"
        if st.get("good") is not None:
            line += f" · {st.get('good')} קטעים טובים"
        if clips:
            line += f" · {clips} קליפים"
        line += f" · {ago(st.get('updated',''))}"
        lines.append(line)
    return "\n\n".join(lines)


def pending_segments(min_score: int = 6) -> list:
    """
    כל קטע שנחתך ועוד לא הוחלט עליו, בכל העבודות.
    מחזיר [(תיקייה, idx, seg, יש קובץ?)], החדש ראשון.
    """
    out = []
    jobs = ROOT / "jobs"
    if not jobs.is_dir():
        return out
    for d in sorted([x for x in jobs.iterdir() if x.is_dir()],
                    key=lambda x: x.stat().st_mtime, reverse=True):
        segs = load_json(d / "audio_segments.json", [])
        for idx, seg in enumerate(segs, 1):
            if seg.get("approved") is not None or seg.get("skip_upload"):
                continue
            if seg.get("score", 0) < min_score:
                continue
            has_file = bool(clip_for(d, idx)) if clip_for else False
            out.append((d, idx, seg, has_file))
    return out


def reply_pending(arg: str = "") -> str:
    """מה מחכה להחלטה שלי עכשיו."""
    rows = pending_segments()
    if not rows:
        return ("אין קטעים שממתינים לאישור.\n"
                "<i>כשעבודה תסתיים אקפוץ עם תצוגה מקדימה וכפתורים.</i>")

    by_job = {}
    for d, idx, seg, has_file in rows:
        by_job.setdefault(d, []).append((idx, seg, has_file))

    no_file = sum(1 for *_, has in rows if not has)
    lines = [f"<b>ממתינים לאישור: {len(rows)} קטעים</b>", ""]
    for d, items in list(by_job.items())[:6]:
        meta = load_json(d / "meta.json", {})
        display = meta.get("display_name", d.name)
        st = load_json(d / "state.json", {})
        lines.append(f"<b>{display}</b> — {len(items)} קטעים · {ago(st.get('updated',''))}")
        for idx, seg, has_file in items[:8]:
            mark = "" if has_file else "  <i>(אין קובץ)</i>"
            lines.append(f"  [{idx}] {seg.get('score','?')}/10  {seg.get('title','')[:55]}{mark}")
        lines.append(f"  <code>/resend {d.name}</code>")
        lines.append(f"  <code>/approve {d.name} </code>1,2 · <code>/skip {d.name} </code>3")
        lines.append("")

    if no_file:
        lines.append(f"<i>{no_file} מהם עוד בלי קובץ וידאו - כנראה החיתוך לא הסתיים.</i>")
    lines.append("<code>/resend</code> ישלח שוב את כולם עם הכפתורים.")
    return "\n".join(lines)


def reply_resend(arg: str = "") -> str:
    """שולח שוב את הקטעים הפתוחים, למקרה שההודעות נבלעו."""
    if arg:
        job = find_job(arg)
        if not job:
            return f"לא מצאתי עבודה בשם '{arg}'."
        return resend_job(job)

    rows = pending_segments()
    if not rows:
        return "אין מה לשלוח, הכל מוחלט."
    jobs = []
    for d, *_ in rows:
        if d not in jobs:
            jobs.append(d)
    total = 0
    for d in jobs[:4]:
        try:
            from approve2 import send_for_approval
            total += send_for_approval(d)
        except Exception as exc:
            log(f"שליחה חוזרת נכשלה ל-{d.name}: {exc}")
    return f"נשלחו {total} קטעים." if total else "לא הצלחתי לשלוח."


def reply_failed(arg: str = "") -> str:
    rows = recent_failed(48)
    if not rows:
        return "אין כישלונות ב-48 השעות האחרונות."
    lines = [f"<b>נכשלו ב-48 שעות: {len(rows)}</b>", ""]
    for d, st in rows[:8]:
        err = st.get("error", "?")
        lines.append(f"<b>{d.name}</b>")
        lines.append(f"{err} · {ago(st.get('updated',''))}"
                     + (f" · ניסיונות: {st.get('retries')}" if st.get("retries") else ""))
        if st.get("detail"):
            lines.append(f"<i>{str(st['detail'])[:120]}</i>")
        lines.append("")
    lines.append("<code>/retry שם</code> להפעיל מחדש · <code>/logs שם</code> ללוג")
    return "\n".join(lines)


# ------------------------------------------------ החלטה בפקודה (25.9)
#
# לירון: 30 קטעים ממתינים, ולחיצה על כל אחד בנפרד לא מחזיקה.
#   /approve masterohad_2026-09-15 1,3,5    ✓ לכמה קטעים בבת אחת
#   /reject  masterohad_2026-09-15 7 חלש     ✗ עם סיבה - נכנס ללמידה
#   /skip    masterohad_2026-09-15 4         לא מעלים, ובלי ללמד כלום
# skip קיים כי "ישן" או "כפול" הם לא סיבה שהמנתח צריך ללמוד ממנה - דחייה
# של דרמה בת 10 ימים הייתה מלמדת אותו לא לבחור דרמות.

def parse_decision(arg: str):
    """'שם 1,3 5 סיבה חופשית' -> (תיקייה, [1,3,5], 'סיבה חופשית', שגיאה)."""
    parts = (arg or "").split()
    if len(parts) < 2:
        return None, [], "", "שימוש: <code>שם_עבודה 1,3,5</code>"
    job = find_job(parts[0])
    if not job:
        return None, [], "", f"לא מצאתי עבודה בשם '{parts[0]}'. <code>/pending</code> לרשימה."
    nums, rest = [], []
    for tok in parts[1:]:
        bits = [b for b in tok.split(",") if b]
        if not rest and bits and all(b.isdigit() for b in bits):
            nums += [int(b) for b in bits]
        else:
            rest.append(tok)
    if not nums:
        return None, [], "", "חסרים מספרי קטעים, למשל <code>1,3,5</code>"
    segs = load_json(job / "audio_segments.json", [])
    bad = [n for n in nums if not 1 <= n <= len(segs)]
    if bad:
        return None, [], "", f"אין קטעים {bad} ב-{job.name} (יש {len(segs)})."
    return job, sorted(set(nums)), " ".join(rest), ""


def reply_approve(arg: str = "") -> str:
    job, nums, _, err = parse_decision(arg)
    if err:
        return err
    for n in nums:
        mark_segment(job, n, True)
        write_feedback(job, n, "אושר להעלאה", "")
        log(f"אושר בפקודה: {job.name} [{n}]")

    def go():
        for n in nums:                    # ברצף, לא במקביל - שני enqueue
            enqueue(job, n)               # בו-זמנית היו מעלים שניים יחד
    threading.Thread(target=go, daemon=True, name="approve-cmd").start()
    return (f"✓ אושרו {len(nums)} מ-{job.name}: {', '.join(map(str, nums))}\n"
            f"<code>/queue</code> מראה מתי כל אחד יעלה.")


def reply_reject(arg: str = "") -> str:
    job, nums, why, err = parse_decision(arg)
    if err:
        return err
    for n in nums:
        mark_segment(job, n, False, why)
        write_feedback(job, n, "נדחה", why)
        log(f"נדחה בפקודה: {job.name} [{n}] {why}")
    tail = "" if why else "\n<i>בלי סיבה. עם סיבה המנתח לומד: /reject שם 7 חלש מדי</i>"
    return f"✗ נדחו {len(nums)} מ-{job.name}: {', '.join(map(str, nums))}{tail}"


def reply_skip(arg: str = "") -> str:
    job, nums, why, err = parse_decision(arg)
    if err:
        return err
    path = job / "audio_segments.json"
    segs = load_json(path, [])
    for n in nums:
        segs[n - 1]["skip_upload"] = True
        segs[n - 1]["skip_reason"] = why or "דולג ידנית"
    path.write_text(json.dumps(segs, ensure_ascii=False, indent=1), encoding="utf-8")
    log(f"דולג: {job.name} {nums}")
    return (f"⏭ דולגו {len(nums)} מ-{job.name}: {', '.join(map(str, nums))}\n"
            "לא יעלו ולא ייספרו כדחייה.")


def reply_retry(arg: str = "") -> str:
    if not arg:
        rows = recent_failed(48)
        if not rows:
            return "אין מה להפעיל מחדש. שלח <code>/retry שם_העבודה</code>."
        return "איזו עבודה? למשל:\n" + "\n".join(f"<code>/retry {d.name}</code>" for d, _ in rows[:6])
    job = find_job(arg)
    if not job:
        return f"לא מצאתי עבודה בשם '{arg}'. <code>/jobs</code> לרשימה."
    return launch(job)


def reply_logs(arg: str = "") -> str:
    if not arg:
        rows = recent_failed(48)
        if rows:
            arg = rows[0][0].name
        else:
            return "איזו עבודה? <code>/logs שם_העבודה</code>"
    job = find_job(arg)
    if not job:
        return f"לא מצאתי עבודה בשם '{arg}'."
    logfile = ROOT / "jobs" / f"{job.name}_pipeline.log"
    if not logfile.exists():
        return f"אין לוג ל-{job.name}."
    try:
        text = logfile.read_text(encoding="utf-8", errors="replace")
    except Exception as exc:
        return f"לא הצלחתי לקרוא את הלוג: {exc}"
    tail = text.strip().splitlines()[-30:]
    body = "\n".join(line[:160] for line in tail)
    return f"<b>{job.name}</b> — 30 שורות אחרונות\n<pre>{body[:3500]}</pre>"


def running_pipelines() -> list:
    """עבודות שיש להן תהליך צינור חי עכשיו: [(תיקייה, pid)]."""
    out = []
    for pid_file in sorted((ROOT / "jobs").glob("*/pipeline.pid")):
        try:
            pid = int(pid_file.read_text(encoding="utf-8").strip())
        except Exception:
            continue
        alive = False
        if os.name == "nt":
            try:
                r = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"],
                                   capture_output=True, text=True, timeout=15)
                alive = str(pid) in (r.stdout or "")
            except Exception:
                alive = False
        else:
            try:
                os.kill(pid, 0)
                alive = True
            except OSError:
                alive = False
        if alive:
            out.append((pid_file.parent, pid))
    return out


def kill_tree(pid: int) -> bool:
    """הורג את הצינור וכל תהליכי הבת שלו (תמלול, ffmpeg, yt-dlp)."""
    try:
        if os.name == "nt":
            r = subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"],
                               capture_output=True, text=True, timeout=30)
            return r.returncode == 0
        import signal
        os.killpg(os.getpgid(pid), signal.SIGTERM)
        return True
    except Exception as exc:
        log(f"לא הצלחתי לעצור את {pid}: {exc}")
        return False


def reply_pause(arg: str = "") -> str:
    """
    עצירת חירום (29.9, בקשת לירון): כשרואים תקלה עמוקה - לעצור הכל.
    המנטר מפסיק לבדוק ולהפעיל, התור מפסיק להעלות, והצינורות שרצים עכשיו
    נעצרים. עבודה שנעצרה מסומנת "נכשלה - ניתן לנסות שוב", כך שאחרי
    /resume המנטר ימשיך אותה מהשלב שבו עצרה (התמלול שומר נתחים).
    """
    already = paused()
    set_paused(True, why=arg or "")
    stopped = []
    for job, pid in running_pipelines():
        if kill_tree(pid):
            st = load_json(job / "state.json", {})
            stage = st.get("stage", "?")
            st.update(stage="failed", error="paused", retryable=True,
                      detail=f"נעצר ב-/pause בשלב {stage}", _failed_reported=True,
                      updated=datetime.now(timezone.utc).isoformat(timespec="seconds"))
            (job / "state.json").write_text(json.dumps(st, ensure_ascii=False, indent=1),
                                            encoding="utf-8")
            (job / "pipeline.pid").unlink(missing_ok=True)
            stopped.append(f"{job.name} (בשלב {stage})")
    log(f"/pause: המערכת מושהית. נעצרו: {stopped or 'אין'}")
    head = "⏸ <b>הכל מושהה</b>" + (" (כבר היה)" if already else "")
    lines = [head, "",
             "• המנטר לא בודק לייבים ולא מפעיל עבודות",
             "• התור לא מעלה ליוטיוב (גם ✓ ו-/upload)",
             "• הבוט עצמו ממשיך לענות - אפשר לאשר, לבדוק, לקרוא לוגים",
             "• נשאר גם אחרי הפעלה מחדש של המחשב"]
    if stopped:
        lines += ["", "<b>נעצרו באמצע:</b>"] + [f"• {x}" for x in stopped]
        lines.append("ימשיכו מאותו שלב אחרי /resume.")
    else:
        lines += ["", "לא רץ אף צינור ברגע זה."]
    lines += ["", "<code>/resume</code> מחזיר הכל לפעולה."]
    return "\n".join(lines)


def reply_resume(arg: str = "") -> str:
    p = paused()
    if not p:
        return "▶ המערכת לא מושהית - הכל רץ."
    set_paused(False)
    log("/resume: המערכת חזרה לפעולה")
    waiting = sum(1 for j in (ROOT / "jobs").glob("*/state.json")
                  if load_json(j, {}).get("error") == "paused")
    return ("▶ <b>חזרנו לפעולה</b> (הייתה מושהית מ-" + str(p.get("since", "?")) + ")\n"
            "המנטר יבדוק בסבב הבא (עד 5 דק'), התור ימשיך כרגיל."
            + (f"\n{waiting} עבודות שנעצרו ימשיכו לבד תוך כחצי שעה (או מיד: /retry שם)." if waiting else ""))


def reply_help() -> str:
    return (
        "<b>מה אפשר לשאול אותי</b>\n\n"
        "<b>/status</b> — מה קורה עכשיו. מיידי\n"
        "<b>/pending</b> — מה ממתין לאישור שלך\n"
        "<b>/resend</b> — לשלוח שוב את הקטעים הפתוחים עם הכפתורים\n"
        "<b>/live</b> — מי משדר לפי הבדיקה האחרונה\n"
        "<b>/check</b> — בדיקה טרייה מול קיק. חצי דקה\n"
        "<b>/health</b> — האם כל הרכיבים עובדים\n"
        "<b>/jobs</b> — העבודות האחרונות\n"
        "<b>/failed</b> — מה נכשל ולמה\n"
        "<b>/retry שם</b> — להפעיל עבודה מחדש\n"
        "<b>/logs שם</b> — 30 השורות האחרונות בלוג\n"
        "<b>/pause</b> — ⏸ עצירת חירום: מנטר, צינורות ותור\n"
        "<b>/resume</b> — ▶ להחזיר הכל לפעולה\n\n"
        "<b>יוטיוב</b>\n"
        "<b>/queue</b> — תור ההעלאות ומתי כל אחד יעלה\n"
        "<b>/budget</b> — כמה הוצאנו החודש, והאם התקציב עוצר את הערוץ\n"
        "<b>/unlisted</b> — מה כבר למעלה ועוד לא באוויר\n"
        "<b>/upload שם מספר</b> — להעלות קטע ידנית\n"
        "<b>/publish שם מספר</b> — להעביר סרטון ל-public\n\n"
        "<b>החלטה בלי כפתורים</b>\n"
        "<b>/approve שם 1,3,5</b> — לאשר כמה קטעים בבת אחת\n"
        "<b>/reject שם 7 סיבה</b> — לדחות, והסיבה מלמדת את המנתח\n"
        "<b>/skip שם 4</b> — לא להעלות, בלי ללמד (ישן / כפול)\n\n"
        "<b>מתחת לכל קטע:</b>\n"
        "✓ אשר · ✗ דחה ואשאל למה · ✎ כותרת חדשה · 📼 הקליפ המלא\n\n"
        "✓ מכניס לתור ההעלאות (המכסה היא 6 ביום, ולכן יש תור).\n"
        "אחרי שהסרטון עולה מגיע קישור עם 🌍 <b>פרסם עכשיו</b>. "
        "עד שלא תלחץ - הוא לא באוויר.\n\n"
        "אפשר גם בעברית: <i>מצב</i>, <i>מי בלייב</i>, <i>הכל עובד?</i>, "
        "<i>בדוק</i>, <i>מה לאשר</i>"
    )


# ---------------------------------------------------------- ניתוב הודעות

SLOW = {"check"}

# פקודות שמקבלות ארגומנט (שם עבודה)
ARG_ROUTES = [
    (("failed", "נכשל", "מה נכשל", "כישלונות", "כשלים"), reply_failed),
    (("retry", "נסה שוב", "הפעל מחדש", "תנסה שוב"), reply_retry),
    (("logs", "log", "לוג", "לוגים"), reply_logs),
    (("resend", "שלח שוב", "תשלח שוב", "שלח לי שוב"), reply_resend),
    (("upload", "העלה", "תעלה", "העלאה"), reply_upload),
    (("approve", "אשר", "תאשר"), reply_approve),
    (("reject", "דחה", "תדחה"), reply_reject),
    (("skip", "דלג על", "תדלג"), reply_skip),
    (("publish", "פרסם", "תפרסם", "לאוויר"), reply_publish),
    (("pending", "ממתין", "ממתינים", "מה לאשר", "לאישור", "מה ממתין",
      "מה נשאר לאשר", "יש משהו לאשר", "מה פתוח"), reply_pending),
]

ROUTES = [
    # עצירת חירום - ראשונה, ובלי מילים קצרות שיופיעו סתם במשפט
    (("pause", "עצור הכל", "עצור הכול", "השהה הכל", "תעצור הכל"), reply_pause),
    (("resume", "המשך הכל", "תמשיך הכל", "חזור לפעולה"), reply_resume),
    (("queue", "תור", "מה בתור", "תור ההעלאות", "מתי יעלה",
      "מה מחכה להעלאה"), reply_queue),
    (("budget", "תקציב", "כמה הוצאנו", "כמה עלה", "הוצאות"), reply_budget),
    (("unlisted", "לא באוויר", "ממתין לפרסום", "מה לא פורסם", "לפרסם",
      "מה למעלה", "לא פורסם"), reply_unlisted),
    # "על מה אתה עובד" נשאל בפועל ולא זוהה. הרשימה הזאת גדלה
    # מכל שאלה שהבוט לא הבין - זה המקום להוסיף.
    (("pending", "ממתין לאישור", "מה לאשר", "מה מחכה", "לאישור",
      "יש משהו לאשר", "מה נשאר"), reply_pending),
    (("jobs", "עבודות", "על מה אתה עובד", "מה אתה עושה", "מה אתה עובד",
      "מה רץ", "במה אתה עסוק", "עבודה", "קטעים", "מה מתעבד"), reply_jobs),
    (("status", "מצב", "מה קורה", "מה המצב", "מה נשמע", "סטטוס"), reply_status),
    (("live", "מי בלייב", "מי משדר", "מי באוויר", "לייב"), reply_live),
    (("check", "בדוק", "בדיקה", "תבדוק", "רענן"), reply_check),
    (("health", "הכל עובד", "הכול עובד", "בריאות", "תקין", "הכל תקין",
      "יש בעיה", "משהו נשבר"), reply_health),
    (("help", "עזרה", "start", "מה אתה יודע", "פקודות"), reply_help),
]


def route(text: str):
    """מחזיר (פונקציה, איטי?). None אם לא זוהתה פקודה."""
    t = (text or "").strip()
    if t.startswith("/"):
        t = t[1:]
        # "/status@MyBot" -> "status"
        head, _, rest = t.partition(" ")
        t = head.split("@")[0] + (" " + rest if rest else "")
    t = t.strip().lower()
    if not t:
        return None, False

    # פקודות עם ארגומנט: "/retry shonp_2026-09-10", "לוג של שון"
    for keys, fn in ARG_ROUTES:
        for k in keys:
            if t == k or t.startswith(k + " ") or t.startswith(k + "_"):
                arg = t[len(k):].strip(" _")
                return (lambda a=arg, f=fn: f(a)), False

    for keys, fn in ROUTES:
        for k in keys:
            if t == k or t.startswith(k):
                return fn, k in SLOW
    # התאמה רכה, למי שכותב משפט שלם
    for keys, fn in ROUTES:
        for k in keys:
            if len(k) > 3 and k in t:
                return fn, k in SLOW
    return None, False


def describe_routes() -> str:
    """לבדיקה מהירה של מה הבוט מזהה."""
    out = []
    for keys, fn in ROUTES:
        out.append(f"{fn.__name__:16} {', '.join(keys)}")
    return "\n".join(out)


# ------------------------------------------------------------ לולאת קליטה

def get_offset() -> int:
    return load_json(OFFSET_PATH, {}).get("offset", 0)


def set_offset(v: int) -> None:
    OFFSET_PATH.write_text(json.dumps({"offset": v}), encoding="utf-8")


def poll(timeout: int = 25) -> list:
    cfg = load_config()
    token = cfg.get("token", "")
    if not token:
        return []
    try:
        r = requests.get(
            API.format(token=token, method="getUpdates"),
            params={"offset": get_offset() + 1, "timeout": timeout},
            timeout=timeout + 15,
        )
        data = r.json()
    except Exception:
        return []
    if not data.get("ok"):
        return []
    return data.get("result", [])


def handle(update: dict) -> None:
    if update.get("callback_query"):
        handle_callback(update["callback_query"])
        return

    msg = update.get("message") or update.get("edited_message") or {}
    text = msg.get("text", "")
    chat_id = str((msg.get("chat") or {}).get("id", ""))
    if not text or not chat_id:
        return

    cfg = load_config()
    owner = str(cfg.get("chat_id", ""))
    if owner and chat_id != owner:
        log(f"התעלמתי מהודעה מצ'אט {chat_id}")
        return

    # אם ממתינים לכותרת או לסיבת דחייה - כל הודעה שאינה פקודה היא התשובה.
    # זה חייב לקדום לניתוב: כותרת אמיתית עלולה להכיל "לייב" או "מצב"
    # ואז היא הייתה נבלעת כפקודה במקום להיכתב לקטע.
    if not text.strip().startswith("/") and maybe_pending_reply(text):
        return

    fn, slow = route(text)

    log(f'קיבלתי "{text[:40]}" -> {getattr(fn, "__name__", "λ") if fn else "לא זוהה"}')

    if not fn:
        # במקום "לא הבנתי" יבש - תשובה שימושית בכל מקרה
        notify("לא בטוח מה שאלת, אז הנה המצב:\n\n"
               + reply_status()
               + "\n\n<i>/help לרשימת הפקודות</i>")
        return

    if slow:
        notify("בודק מול קיק, רגע...")

    try:
        notify(fn())
    except Exception as exc:
        notify(f"משהו נשבר בזמן הבדיקה:\n<code>{exc}</code>")
        log(f"שגיאה: {exc}")


MENU_COMMANDS = [
    ("status",  "מה קורה עכשיו"),
    ("pause",   "⏸ עצירת חירום - עוצר הכל"),
    ("resume",  "▶ להחזיר הכל לפעולה"),
    ("pending", "מה ממתין לאישור שלך"),
    ("resend",  "לשלוח שוב את הקטעים הפתוחים"),
    ("queue",   "תור ההעלאות ומתי כל אחד יעלה"),
    ("budget",  "כמה הוצאנו החודש, והאם התקציב מגביל"),
    ("unlisted", "מה למעלה ועוד לא באוויר"),
    ("upload",  "להעלות קטע ידנית - /upload שם מספר"),
    ("approve", "לאשר קטעים - /approve שם 1,3,5"),
    ("reject",  "לדחות עם סיבה - /reject שם 7 סיבה"),
    ("skip",    "לא להעלות, בלי ללמד - /skip שם 4"),
    ("publish", "להעביר סרטון ל-public - /publish שם מספר"),
    ("live",    "מי משדר"),
    ("check",   "בדיקה טרייה מול קיק"),
    ("health",  "האם כל הרכיבים עובדים"),
    ("jobs",    "העבודות האחרונות"),
    ("failed",  "מה נכשל ולמה"),
    ("retry",   "להפעיל עבודה מחדש - /retry שם"),
    ("logs",    "השורות האחרונות בלוג - /logs שם"),
    ("help",    "רשימת הפקודות"),
]


def register_menu() -> bool:
    """
    רושם את הפקודות בתפריט ה-"/" של טלגרם.

    בלי זה הבוט עדיין עונה לכל פקודה, אבל טלגרם לא מציע אותן -
    התפריט מגיע מ-setMyCommands ולא מהקוד, וזה בלבל בפועל.
    """
    try:
        res = call("setMyCommands", commands=json.dumps(
            [{"command": c, "description": d} for c, d in MENU_COMMANDS],
            ensure_ascii=False))
        # call מחזיר את גוף התשובה. dict לא ריק הוא truthy גם כשהיא כישלון,
        # ולכן בודקים את השדה ok עצמו ולא את התשובה.
        ok = isinstance(res, dict) and res.get("ok") is True
        if ok:
            log(f"תפריט הפקודות נרשם ({len(MENU_COMMANDS)} פקודות).")
        else:
            log(f"רישום תפריט הפקודות נכשל: {res}")
        return ok
    except Exception as exc:
        log(f"רישום תפריט הפקודות נכשל: {exc}")
        return False


def serve(once: bool = False, stop: threading.Event = None) -> None:
    cfg = load_config()
    if not cfg.get("token") or not cfg.get("chat_id"):
        log("אין טוקן או chat_id. הרץ:  python scripts\\tg.py setup")
        return

    register_menu()
    threading.Thread(target=reminder_loop, args=(stop,),
                     daemon=True, name="publish-reminder").start()
    threading.Thread(target=queue_loop, args=(stop,),
                     daemon=True, name="upload-queue").start()
    log("מקשיב לפקודות.")
    while not (stop and stop.is_set()):
        updates = poll(2 if once else 25)
        for u in updates:
            set_offset(u.get("update_id", 0))
            try:
                handle(u)
            except Exception as exc:
                log(f"שגיאה בטיפול: {exc}")
        if once:
            log(f"סבב אחד, {len(updates)} הודעות.")
            return
        if not updates:
            time.sleep(1)


def start_thread() -> threading.Thread:
    """מפעיל את הבוט כחוט רקע. משמש את המנטר."""
    t = threading.Thread(target=serve, daemon=True, name="tgbot")
    t.start()
    return t


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true", help="סבב אחד ויציאה")
    ap.add_argument("--say", default="", help="להריץ פקודה מקומית בלי טלגרם")
    args = ap.parse_args()

    if args.say:
        fn, _ = route(args.say)
        if not fn:
            print("לא זוהתה פקודה.")
            return
        import re
        print(re.sub(r"<[^>]+>", "", fn()))
        return

    serve(once=args.once)


if __name__ == "__main__":
    main()
