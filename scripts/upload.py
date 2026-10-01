"""
upload.py - מעלה קליפ מאושר ליוטיוב, ומפרסם אותו כשמחליטים

הזרימה שנבנתה (החלטת לירון, 14.9):
    ✓ בטלגרם  →  העלאה כ-unlisted  →  קישור חוזר לטלגרם עם כפתור "פרסם"
    כפתור פרסם  →  הסרטון עובר ל-public

הסרטון אף פעם לא הופך לפומבי מעצמו. זה מכוון: הכותרת, התיאור
והתמנייל נבנים אוטומטית, ועדיף לתפוס טעות בסרטון שאף אחד עוד לא ראה.

⚠ מגבלה של יוטיוב שחייבים להכיר: פרויקט API שלא עבר audit מעלה
  סרטונים שנעולים ל-private, ולא תמיד אפשר לשחרר אותם אחר כך.
  לכן הסרטון הראשון הוא סרטון בדיקה. ראה YOUTUBE_SETUP.md, שלב 8.

מכסה (1.10, נבדק ב-Console של hataktzir-upload ובדף העלויות של גוגל מ-15.9.2026):
`videos.insert` יושב בדלי נפרד - **100 העלאות ביום**, 1 לכל קריאה - ולא יורד מה-10,000.
ה-10,000 יחידות הן לכל השאר: thumbnails.set 50, videos.update 50, list 1.
(עד 1.10 הקוד הניח 1,600 להעלאה = 6 ביום. זה כבר לא נכון.)
`quota_left()` מעריך כמה נשארו לפי מה שהועלה היום בפועל. הקצב בפועל נקבע ב-youtube.json → queue.
יוטיוב מגביל גם את הערוץ עצמו (uploadLimitExceeded), ואת המספר הזה הם לא מפרסמים.

שימוש ידני:
    python scripts\\upload.py jobs\\ronengg_2026-09-10 3
    python scripts\\upload.py jobs\\ronengg_2026-09-10 3 --privacy private
    python scripts\\upload.py --publish jobs\\ronengg_2026-09-10 3
    python scripts\\upload.py --check          בדיקת חיבור בלי להעלות כלום
    python scripts\\upload.py --thumb jobs\\ronengg_2026-09-10 3   רק תמנייל
    python scripts\\upload.py --refresh-desc jobs\\ronengg_2026-09-10 3   רק תיאור, לסרטון שבאוויר
    python scripts\\upload.py --refresh-meta jobs\\ronengg_2026-09-10 3   כותרת+תיאור+תגיות מהקבצים
"""

import os
import re
import sys
import threading
import json
import time
import random
import argparse
from pathlib import Path
from datetime import datetime, timezone


def find_root(start: Path) -> Path:
    for c in [start, *start.parents]:
        if (c / "scripts").is_dir():
            return c
    return start


ROOT = find_root(Path(__file__).resolve().parent)
TOKEN = ROOT / "youtube_token.json"
CONFIG = ROOT / "youtube.json"

# גוגל מורידה את המפתח בשם ארוך. ytauth מוצא אותו לבד, וכאן רק
# מדווחים עליו ב---check, ולכן די באותה לוגיקה.
_secret_matches = sorted(ROOT.glob("client_secret*.json"))
SECRET = ((ROOT / "client_secret.json") if (ROOT / "client_secret.json").exists()
          else (_secret_matches[0] if _secret_matches else ROOT / "client_secret.json"))

# מגבלות יוטיוב. לא להמציא כאן מספרים - אלה מהתיעוד.
TITLE_MAX = 100
DESC_MAX = 5000
TAGS_CHARS_MAX = 500
# 1.10: videos.insert בדלי משלו (100 ביום). כל העלאה מוציאה מה-10,000 רק את התמנייל (50).
UPLOADS_PER_DAY = 100
UNITS_PER_UPLOAD = 50
DAILY_QUOTA = 10_000

DEFAULTS = {
    "category_id": "20",        # 20 = Gaming. 24 = Entertainment
    "privacy": "unlisted",
    "language": "he",
    "made_for_kids": False,
    "chunk_mb": 8,
}


def load_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def config() -> dict:
    cfg = dict(DEFAULTS)
    cfg.update(load_json(CONFIG, {}))
    return cfg


# ------------------------------------------------------------- הרשאה

class AuthExpired(RuntimeError):
    """ההרשאה מול גוגל בוטלה או פגה. לא תקלה של קטע - תקלה של החשבון."""


# 25.9: כל ההעלאות נכשלו עם invalid_grant. הסיבה: אפליקציית ה-OAuth
# ב-Google Cloud במצב Testing, ושם גוגל מבטלת את ה-refresh token אחרי
# 7 ימים. התיקון הקבוע הוא Publish app (ראה YOUTUBE_SETUP.md, שלב 9).
AUTH_EXPIRED_MSG = (
    "ההרשאה ליוטיוב פגה (invalid_grant). זו לא בעיה בקטע.\n"
    "1. Google Cloud → OAuth consent screen → Publish app (אחרת זה יחזור כל 7 ימים)\n"
    "2. בחלון cmd: python scripts\\ytauth.py  (ולענות 'כן')\n"
    "התור מושהה עד שההרשאה מתחדשת. הקטעים נשארים בו.")


def is_auth_error(exc) -> bool:
    """RefreshError של google-auth, או כל שגיאה שנושאת invalid_grant."""
    if isinstance(exc, AuthExpired):
        return True
    text = f"{type(exc).__name__} {exc}"
    return "invalid_grant" in text or "RefreshError" in text


def creds():
    """
    מחזיר credentials תקפים, ומרענן אותם אם פג תוקף.
    מחזיר None אם עוד לא הורשה - הקורא אחראי להודיע.
    זורק AuthExpired אם גוגל ביטלה את ההרשאה.
    """
    try:
        from google.oauth2.credentials import Credentials
        from google.auth.transport.requests import Request
    except ImportError:
        raise RuntimeError(
            "חסרות ספריות גוגל. הרץ: pip install google-api-python-client "
            "google-auth-oauthlib google-auth-httplib2")

    if not TOKEN.exists():
        return None

    from ytauth import SCOPES
    c = Credentials.from_authorized_user_file(str(TOKEN), SCOPES)
    if c and c.expired and c.refresh_token:
        try:
            c.refresh(Request())
        except Exception as exc:
            if is_auth_error(exc):
                raise AuthExpired(AUTH_EXPIRED_MSG) from exc
            raise
        TOKEN.write_text(c.to_json(), encoding="utf-8")
    return c if c and c.valid else None


def service():
    from googleapiclient.discovery import build
    c = creds()
    if not c:
        raise RuntimeError("אין הרשאה ליוטיוב. הרץ פעם אחת: "
                           "python scripts\\ytauth.py")
    return build("youtube", "v3", credentials=c, cache_discovery=False)


def channel_name(raise_auth: bool = False) -> str:
    try:
        yt = service()
        items = yt.channels().list(part="snippet", mine=True).execute().get("items", [])
        return items[0]["snippet"]["title"] if items else ""
    except Exception as exc:
        if raise_auth and is_auth_error(exc):
            raise AuthExpired(AUTH_EXPIRED_MSG) from exc
        return ""


# ------------------------------------------------------- מצב ההעלאות

def seg_path(job: Path) -> Path:
    return Path(job) / "audio_segments.json"


def get_segment(job: Path, idx: int) -> dict:
    segs = load_json(seg_path(job), [])
    return segs[idx - 1] if 1 <= idx <= len(segs) else {}


def mark_upload(job: Path, idx: int, info: dict) -> None:
    """שומר את מצב ההעלאה בתוך הקטע עצמו. אין קובץ מצב נוסף לתחזק."""
    path = seg_path(job)
    segs = load_json(path, [])
    if not (1 <= idx <= len(segs)):
        return
    yt = segs[idx - 1].get("youtube") or {}
    yt.update(info)
    segs[idx - 1]["youtube"] = yt
    path.write_text(json.dumps(segs, ensure_ascii=False, indent=1), encoding="utf-8")


def pacific_day_start() -> datetime:
    """
    המכסה של יוטיוב מתאפסת בחצות שעון האוקיינוס השקט - לא בחצות
    שלנו ולא ב-UTC. ספירה לפי תאריך UTC משחררת את המכסה בשעה
    הלא נכונה, וההעלאה השביעית נכשלת. בלי מאגר אזורי זמן נופלים
    ל-PST קבוע, שהוא הצד הבטוח (משחרר מאוחר יותר, לא מוקדם).
    """
    from datetime import timedelta
    try:
        from zoneinfo import ZoneInfo
        tz = ZoneInfo("America/Los_Angeles")
    except Exception:
        tz = timezone(timedelta(hours=-8))
    local = datetime.now(tz)
    return local.replace(hour=0, minute=0, second=0,
                         microsecond=0).astimezone(timezone.utc)


def uploads_today() -> list:
    """כל הקטעים שהועלו מאז איפוס המכסה האחרון. משמש להערכת המכסה."""
    since = pacific_day_start()
    out = []
    jobs = ROOT / "jobs"
    if not jobs.is_dir():
        return out
    for job in jobs.iterdir():
        if not job.is_dir():
            continue
        for i, seg in enumerate(load_json(seg_path(job), []), 1):
            yt = seg.get("youtube") or {}
            raw = yt.get("uploaded_at", "")
            try:
                when = datetime.fromisoformat(str(raw))
            except Exception:
                continue
            if not when.tzinfo:
                when = when.replace(tzinfo=timezone.utc)
            if when >= since:
                out.append((job.name, i, yt))
    return out


def quota_left() -> int:
    """כמה העלאות נשארו היום, לפי הערכה. לא מדויק, אבל מספיק כדי להזהיר."""
    n = len(uploads_today())
    by_inserts = UPLOADS_PER_DAY - n
    by_units = (DAILY_QUOTA - n * UNITS_PER_UPLOAD) // UNITS_PER_UPLOAD
    return max(0, min(by_inserts, by_units))


def pending_publish() -> list:
    """
    מה הועלה ועוד לא פורסם. זה מה שמזין את התזכורת בטלגרם.
    30.9: סרטון שעלה ישר כ-public כבר באוויר. published_at נכתב רק בכפתור
    הפרסום, ולכן כל העלאה מ-25.9 (privacy=public) נספרה כ"לא באוויר"
    והבוט שלח תזכורות על סרטונים שכבר היו פתוחים לכולם.
    """
    out = []
    jobs = ROOT / "jobs"
    if not jobs.is_dir():
        return out
    for job in sorted(jobs.iterdir()):
        if not job.is_dir():
            continue
        for i, seg in enumerate(load_json(seg_path(job), []), 1):
            yt = seg.get("youtube") or {}
            # קטע שלא אושר (approved=False) הוא סרטון בדיקה ידני, לא משהו
            # שמחכה לפרסום - כמו fmQklrQPMpY מ-14.9.
            if (yt.get("id") and not yt.get("published_at")
                    and yt.get("privacy") != "public"
                    and seg.get("approved") is not False):
                out.append({
                    "job": job.name, "idx": i,
                    "title": seg.get("title", ""),
                    "url": yt.get("url", ""),
                    "uploaded_at": yt.get("uploaded_at", ""),
                    "privacy": yt.get("privacy", ""),
                })
    return out


# --------------------------------------------------- בניית מטא-דאטה

def clip_file(job: Path, idx: int, desc: dict):
    """
    מוצא את קובץ ה-mp4. descriptions.json מחזיק את השם, אבל הוא
    משתנה כששולחים ✎ כותרת, ולכן יש נפילה לאחור לחיפוש לפי מספר.
    """
    clips = Path(job) / "clips"
    name = desc.get("file", "")
    if name and (clips / name).exists():
        return clips / name
    matches = [p for p in sorted(clips.glob(f"{idx:02d} - *.mp4")) if p.is_file()]
    return matches[0] if matches else None


# הסימן ש-describe כותב כשאין קישור לאדם. הוא סימון פנימי ולעולם לא יוצא
# לאוויר (30.9: הוא הופיע ב-3 תיאורים ציבוריים, כי מ-25.9 אין עצירה לפני פרסום).
MISSING_LINK = "(חסר קישור, למלא ב-channels.json)"


def repair_credits(text: str) -> tuple:
    """
    מחזיר (טקסט, משתתפים_בלי_קישור, מארח_בלי_קישור).
    ברגע ההעלאה ממלאים מחדש מ-channels.json - כך קישור שנוסף אחרי describe
    נכנס בלי להריץ אותו שוב. משתתף שעדיין אין לו קישור נשאר בשם בלבד.
    מארח בלי קישור = לא מעלים (הקרדיט למארח הוא התנאי).
    """
    if MISSING_LINK not in text:
        return text, [], ""
    try:
        import describe as _d
        amap = _d.build_alias_map(load_json(ROOT / "names.json", {}))
        db = load_json(ROOT / "channels.json", {}).get("people", {})
        links_of = _d.person_links
    except Exception:
        amap, db = {}, {}

        def links_of(_info):
            return []

    def links_for(name: str) -> str:
        canon = (amap.get(name) or (name,))[0]
        return "  |  ".join(links_of(db.get(canon) or db.get(name) or {}))

    missing, host_missing, out = [], "", []
    for line in text.split("\n"):
        if MISSING_LINK not in line:
            out.append(line)
            continue
        m = re.match(r"^\s*(.+?)\s+—\s+" + re.escape(MISSING_LINK), line)
        if m:                                           # שורת משתתף
            name = m.group(1).strip()
            links = links_for(name)
            if links:
                out.append(line.replace(MISSING_LINK, links))
            else:
                missing.append(name)
                out.append(name)
            continue
        m = re.search(r"של\s+(.+?):\s*" + re.escape(MISSING_LINK), line)
        name = m.group(1).strip() if m else ""        # שורת הזכויות (המארח)
        links = links_for(name) if name else ""
        if links:
            out.append(line.replace(MISSING_LINK, links))
        else:
            host_missing = name or "?"
            out.append(line)
    return "\n".join(out), missing, host_missing


def metadata(job: Path, idx: int, cfg: dict):
    """מחזיר (body ליוטיוב, נתיב הקובץ, שגיאה)."""
    seg = get_segment(job, idx)
    if not seg:
        return None, None, f"אין קטע {idx} ב-{Path(job).name}"

    rows = load_json(Path(job) / "clips" / "descriptions.json", [])
    desc = next((r for r in rows if r.get("idx") == idx), {})

    path = clip_file(job, idx, desc)
    if not path:
        return None, None, f"לא נמצא קובץ mp4 לקטע {idx}"

    # מדיניות תוכן (58): כוכבית על מילים קשות בכל מה שיוטיוב קורא.
    # הקבצים עצמם לא משתנים - הריכוך קורה רק ברגע ההעלאה.
    try:
        from content import soften, soften_tags
    except ImportError:
        def soften(t):
            return t

        def soften_tags(t):
            return list(t or [])

    title = soften((seg.get("title") or desc.get("title") or "").strip())
    if not title:
        return None, None, "אין כותרת לקטע"
    if len(title) > TITLE_MAX:
        title = title[:TITLE_MAX - 1].rstrip() + "…"

    raw_desc, credit_missing, host_missing = repair_credits(
        (desc.get("description") or "").strip())
    if host_missing:
        return None, None, (f"אין קישור ל-{host_missing} (בעל השידור) ב-channels.json. "
                            "בלי קרדיט למארח לא מעלים - למלא ולנסות שוב (/upload).")
    body_text = soften(raw_desc)[:DESC_MAX]
    if not body_text:
        return None, None, ("אין תיאור לקטע. בלי תיאור אין קרדיט ואין "
                            "קישור, וזה התנאי שעליו הכל עומד - לא מעלה.")

    # יוטיוב סופר את התגיות יחד, 500 תווים. חותכים ולא נותנים לו לדחות.
    tags, used = [], 0
    for t in soften_tags(desc.get("tags", [])):
        t = str(t).strip().lstrip("#")
        if not t:
            continue
        if used + len(t) + 1 > TAGS_CHARS_MAX:
            break
        tags.append(t)
        used += len(t) + 1

    return {
        "snippet": {
            "title": title,
            "description": body_text,
            "tags": tags,
            "categoryId": str(cfg["category_id"]),
            "defaultLanguage": cfg["language"],
            "defaultAudioLanguage": cfg["language"],
        },
        "status": {
            "privacyStatus": cfg["privacy"],
            "selfDeclaredMadeForKids": bool(cfg["made_for_kids"]),
            "madeForKids": bool(cfg["made_for_kids"]),
        },
        # לא נשלח ליוטיוב - _upload_clip מוציא אותו לפני insert
        "_credit_missing": credit_missing,
    }, path, ""


# ------------------------------------------------------------ העלאה

RETRIABLE = (500, 502, 503, 504)


# 67/5 (29.9): נעילה נגד העלאה כפולה. שלושה מסלולים מעלים - ✓ כשהתור ריק,
# /upload ידני, וחוט התור - ו-youtube.id נכתב רק בסוף ההעלאה. חוט שהתעורר
# באמצע ראה את הקטע "ממתין" והעלה אותו שוב: שני סרטונים public, מכסה כפולה.
#   _UPLOAD_LOCK      - בתוך תהליך הבוט: העלאה אחת בכל רגע, והבדיקה אחרי הנעילה.
#   uploading_at      - בין תהליכים (upload.py ידני, fixclip): סימון בקטע עצמו
#                       לפני תחילת ההעלאה. ישן מ-UPLOADING_STALE_H = קריסה, מותר שוב.
_UPLOAD_LOCK = threading.Lock()
UPLOADING_STALE_H = 3


def is_uploading(yt_info: dict, now: datetime = None) -> bool:
    """הקטע באמצע העלאה עכשיו (בתהליך כלשהו), לפי הסימון בקטע."""
    raw = (yt_info or {}).get("uploading_at", "")
    if not raw:
        return False
    try:
        when = datetime.fromisoformat(str(raw))
    except Exception:
        return False
    if not when.tzinfo:
        when = when.replace(tzinfo=timezone.utc)
    age_h = ((now or datetime.now(timezone.utc)) - when).total_seconds() / 3600
    return 0 <= age_h < UPLOADING_STALE_H


def clear_uploading(job: Path, idx: int) -> None:
    path = seg_path(job)
    segs = load_json(path, [])
    if not (1 <= idx <= len(segs)):
        return
    yt = segs[idx - 1].get("youtube") or {}
    if "uploading_at" in yt or "uploading_pid" in yt:
        yt.pop("uploading_at", None)
        yt.pop("uploading_pid", None)
        segs[idx - 1]["youtube"] = yt
        path.write_text(json.dumps(segs, ensure_ascii=False, indent=1), encoding="utf-8")


def upload_clip(job, idx: int, privacy: str = "", progress=None) -> dict:
    """
    מעלה קטע אחד. מחזיר dict עם ok / id / url / error.
    `progress` היא פונקציה אופציונלית שמקבלת אחוזים, לדיווח לטלגרם.
    busy=True = הקטע כבר בהעלאה ממקום אחר. זה לא כישלון ולא נספר.
    """
    job = Path(job)
    with _UPLOAD_LOCK:
        # הבדיקה אחרי הנעילה: מי שחיכה לה רואה את ה-id שהקודם כתב
        seg = get_segment(job, idx)
        yt_info = seg.get("youtube") or {}
        already = yt_info.get("id")
        if already:
            return {"ok": True, "id": already, "already": True,
                    "url": f"https://youtu.be/{already}",
                    "error": ""}
        if is_uploading(yt_info):
            return {"ok": False, "busy": True,
                    "error": f"הקטע כבר בהעלאה (התחיל {yt_info.get('uploading_at')}, "
                             f"תהליך {yt_info.get('uploading_pid', '?')})"}
        mark_upload(job, idx, {
            "uploading_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "uploading_pid": os.getpid()})
        try:
            return _upload_clip(job, idx, privacy, progress)
        finally:
            clear_uploading(job, idx)


def _upload_clip(job: Path, idx: int, privacy: str, progress) -> dict:
    cfg = config()
    if privacy:
        cfg["privacy"] = privacy

    body, path, err = metadata(job, idx, cfg)
    if err:
        return {"ok": False, "error": err}
    credit_missing = body.pop("_credit_missing", [])

    try:
        from googleapiclient.http import MediaFileUpload
        from googleapiclient.errors import HttpError
        yt = service()
    except Exception as exc:
        return {"ok": False, "error": str(exc), "auth": is_auth_error(exc)}

    media = MediaFileUpload(str(path), chunksize=int(cfg["chunk_mb"]) * 1024 * 1024,
                            resumable=True, mimetype="video/mp4")
    request = yt.videos().insert(part="snippet,status", body=body, media_body=media)

    response, tries, last = None, 0, ""
    while response is None:
        try:
            status, response = request.next_chunk()
            if status and progress:
                progress(int(status.progress() * 100))
            tries = 0
        except HttpError as exc:
            if exc.resp.status in RETRIABLE:
                tries += 1
                last = f"HTTP {exc.resp.status}"
            else:
                return {"ok": False, "error": http_error_text(exc)}
        except Exception as exc:
            # טוקן שבוטל לא יתוקן בניסיון חוזר. בלי זה - 6 ניסיונות ודקות של המתנה.
            if is_auth_error(exc):
                return {"ok": False, "error": AUTH_EXPIRED_MSG, "auth": True}
            tries += 1
            last = str(exc)

        if tries:
            if tries > 6:
                return {"ok": False,
                        "error": f"ההעלאה נכשלה אחרי 6 ניסיונות. אחרון: {last}"}
            # השהיה מתרחבת עם רעש, כדי ששני קליפים לא ינסו יחד
            time.sleep(min(60, 2 ** tries) + random.random())

    vid = response.get("id", "")
    got = (response.get("status") or {}).get("privacyStatus", "")
    info = {
        "fails": 0, "last_error": "",
        "id": vid,
        "url": f"https://youtu.be/{vid}",
        "privacy": got or cfg["privacy"],
        "uploaded_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "file": path.name,
    }
    # עלה ישר ל-public = פורסם ברגע ההעלאה. בלי זה published_at חסר
    # והקטע נראה כמו unlisted שמחכה לכפתור (30.9).
    if info["privacy"] == "public":
        info["published_at"] = info["uploaded_at"]
    mark_upload(job, idx, info)

    # התמנייל (משימה 4). כישלון כאן לא מבטל העלאה שהצליחה - הסרטון
    # למעלה ו-unlisted, ואפשר לנסות שוב עם --thumb.
    thumb = apply_thumbnail(job, idx)

    locked = bool(got) and got != cfg["privacy"]
    return {"ok": True, "id": vid, "url": info["url"], "privacy": info["privacy"],
            "locked": locked, "asked": cfg["privacy"], "error": "",
            "credit_missing": credit_missing,
            "thumb_ok": thumb.get("ok", False), "thumb_error": thumb.get("error", "")}


def apply_thumbnail(job, idx: int) -> dict:
    """בונה תמנייל אם אין, ומעלה אותו לסרטון. 50 יחידות מכסה."""
    try:
        from thumb import make_thumbnail
    except (Exception, SystemExit) as exc:
        return {"ok": False, "error": f"thumb.py לא נטען: {exc}"}
    try:
        image = make_thumbnail(Path(job), idx)
    except (Exception, SystemExit) as exc:     # לא להפיל את חוט ההעלאה בבוט
        return {"ok": False, "error": f"בניית התמנייל נכשלה: {exc}"}
    if not image:
        return {"ok": False, "error": "לא נבנה תמנייל"}
    res = set_thumbnail(job, idx, image)
    if res.get("ok"):
        mark_upload(Path(job), idx, {"thumb": Path(image).name,
                                     "thumb_at": datetime.now(timezone.utc)
                                     .isoformat(timespec="seconds")})
    return res


def http_error_text(exc) -> str:
    """הודעת שגיאה של גוגל היא JSON. מחלץ ממנה משפט קריא."""
    try:
        data = json.loads(exc.content.decode("utf-8"))
        err = data.get("error", {})
        msg = err.get("message", "")
        reason = ""
        for e in err.get("errors", []):
            reason = e.get("reason", "")
            break
        if reason == "quotaExceeded":
            return ("נגמרה מכסה יומית של יוטיוב (100 העלאות, או 10,000 יחידות לשאר הקריאות). "
                    "מתאפסת בחצות לפי שעון האוקיינוס השקט.")
        if reason == "uploadLimitExceeded":
            return "הערוץ חרג ממגבלת ההעלאות היומית של יוטיוב."
        if reason in ("forbidden", "youtubeSignupRequired"):
            return f"יוטיוב סירב: {msg}"
        return f"{reason or exc.resp.status}: {msg}"
    except Exception:
        return str(exc)


# ------------------------------------------------------------ פרסום

def publish(job, idx: int, privacy: str = "public") -> dict:
    """מעביר סרטון שכבר הועלה לסטטוס אחר, בדרך כלל public."""
    job = Path(job)
    seg = get_segment(job, idx)
    yt_info = seg.get("youtube") or {}
    vid = yt_info.get("id")
    if not vid:
        return {"ok": False, "error": "הקטע הזה עוד לא הועלה."}

    try:
        from googleapiclient.errors import HttpError
        yt = service()
        # videos.update דורס את כל החלק שמעדכנים, ולכן שולחים status שלם
        cfg = config()
        yt.videos().update(part="status", body={
            "id": vid,
            "status": {
                "privacyStatus": privacy,
                "selfDeclaredMadeForKids": bool(cfg["made_for_kids"]),
                "madeForKids": bool(cfg["made_for_kids"]),
            },
        }).execute()
    except HttpError as exc:
        return {"ok": False, "error": http_error_text(exc)}
    except Exception as exc:
        return {"ok": False, "error": str(exc), "auth": is_auth_error(exc)}

    mark_upload(job, idx, {
        "privacy": privacy,
        "published_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    })
    return {"ok": True, "id": vid, "url": f"https://youtu.be/{vid}", "error": ""}


def refresh_description(job, idx: int, with_title: bool = False) -> dict:
    """
    מעדכן רק את התיאור של סרטון שכבר באוויר, מ-descriptions.json אחרי
    repair_credits (30.9: קישורי פרנזי/עידן שנוספו אחרי ההעלאה).
    הכותרת, התגיות והקטגוריה נקראות מיוטיוב ונשלחות כמו שהן - כך שינוי
    ידני שנעשה ב-Studio לא נדרס. עלות: ~51 יחידות מכסה.
    with_title=True (1.10, --refresh-meta): גם הכותרת והתגיות נשלחות, מהקבצים בתיקיית
    העבודה - אותה כותרת ואותן תגיות ש-metadata בונה בהעלאה (content.soften, 100 תווים,
    500 תווי תגיות). זו הדרך לתקן כותרת שכבר באוויר בלי Studio ("pedrofederer מתפוצץ...",
    80ב). ⚠ שינוי שנעשה ב-Studio נדרס - לעדכן קודם את הקבצים. עלות: ~51 יחידות.
    """
    job = Path(job)
    vid = (get_segment(job, idx).get("youtube") or {}).get("id")
    if not vid:
        return {"ok": False, "error": "הקטע לא הועלה"}
    body, _path, err = metadata(job, idx, config())
    if err:
        return {"ok": False, "error": err}
    new_desc = body["snippet"]["description"]
    new_title = body["snippet"]["title"]
    try:
        from googleapiclient.errors import HttpError
        yt = service()
        items = yt.videos().list(part="snippet", id=vid).execute().get("items") or []
        if not items:
            return {"ok": False, "error": f"יוטיוב לא מצא את {vid}"}
        snip = items[0]["snippet"]
        old_title = snip.get("title", "")
        same_desc = snip.get("description", "") == new_desc
        new_tags = body["snippet"].get("tags", [])
        same_title = (not with_title) or (old_title == new_title
                                          and list(snip.get("tags", [])) == new_tags)
        if same_desc and same_title:
            return {"ok": True, "id": vid, "changed": False, "error": "",
                    "title": old_title}
        keep = {k: snip[k] for k in ("title", "categoryId", "tags", "defaultLanguage",
                                     "defaultAudioLanguage") if k in snip}
        keep["description"] = new_desc
        if with_title:
            keep["title"] = new_title
            keep["tags"] = new_tags
        yt.videos().update(part="snippet", body={"id": vid, "snippet": keep}).execute()
    except HttpError as exc:
        return {"ok": False, "error": http_error_text(exc)}
    except Exception as exc:
        return {"ok": False, "error": str(exc), "auth": is_auth_error(exc)}
    return {"ok": True, "id": vid, "changed": True, "error": "",
            "title_changed": with_title and old_title != new_title,
            "old_title": old_title, "title": keep.get("title", old_title),
            "credit_missing": body.get("_credit_missing", [])}


def set_thumbnail(job, idx: int, image) -> dict:
    """
    מעלה תמנייל לסרטון שכבר למעלה. דורש ערוץ מאומת בטלפון (בוצע 10.9).
    יוטיוב מקבל עד 2MB; ה-jpg שלנו ב-1280x720 הוא ~150-300KB.
    """
    from googleapiclient.http import MediaFileUpload
    if Path(image).stat().st_size > 2 * 1024 * 1024:
        return {"ok": False, "error": "התמנייל גדול מ-2MB"}
    seg = get_segment(Path(job), idx)
    vid = (seg.get("youtube") or {}).get("id")
    if not vid:
        return {"ok": False, "error": "הקטע לא הועלה"}
    try:
        service().thumbnails().set(
            videoId=vid,
            media_body=MediaFileUpload(str(image), mimetype="image/jpeg"),
        ).execute()
    except Exception as exc:
        try:
            from googleapiclient.errors import HttpError
            if isinstance(exc, HttpError):
                return {"ok": False, "error": http_error_text(exc)}
        except Exception:
            pass
        return {"ok": False, "error": str(exc)}
    return {"ok": True, "error": ""}


# ------------------------------------------------------------- ראשי

def check() -> int:
    print(f"שורש הפרויקט: {ROOT}")
    print(f"מפתח client_secret: {SECRET.name if SECRET.exists() else 'חסר'}")
    print(f"youtube_token.json: {'קיים' if TOKEN.exists() else 'חסר'}")
    if not TOKEN.exists():
        print("\nעוד לא הורשה. הרץ פעם אחת: python scripts\\ytauth.py")
        return 1
    try:
        name = channel_name(raise_auth=True)
    except AuthExpired:
        print("\n" + AUTH_EXPIRED_MSG)
        return 1
    if not name:
        print("\nההרשאה קיימת אבל לא הצלחתי לקרוא את הערוץ. "
              "אולי פג תוקף - הרץ python scripts\\ytauth.py שוב.")
        return 1
    cfg = config()
    print(f"ערוץ מחובר: {name}")
    print(f"סטטוס העלאה: {cfg['privacy']}   קטגוריה: {cfg['category_id']}")
    print(f"הועלו היום: {len(uploads_today())}   נשארו בערך: {quota_left()}")
    waiting = pending_publish()
    if waiting:
        print(f"\nממתינים לפרסום ({len(waiting)}):")
        for w in waiting:
            print(f"  [{w['idx']}] {w['title'][:55]}  {w['url']}")
    return 0


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("job", nargs="?", help="תיקיית העבודה")
    ap.add_argument("idx", nargs="?", type=int, help="מספר הקטע")
    ap.add_argument("--privacy", default="", choices=["", "private", "unlisted", "public"])
    ap.add_argument("--publish", action="store_true", help="לפרסם קטע שכבר הועלה")
    ap.add_argument("--check", action="store_true", help="בדיקת חיבור בלבד")
    ap.add_argument("--thumb", action="store_true",
                    help="רק להעלות תמנייל לקטע שכבר למעלה")
    ap.add_argument("--refresh-desc", action="store_true",
                    help="לעדכן רק את התיאור של סרטון שכבר באוויר (קרדיטים)")
    ap.add_argument("--refresh-meta", action="store_true",
                    help="לעדכן כותרת, תיאור ותגיות של סרטון שכבר באוויר, מהקבצים בתיקיית העבודה")
    args = ap.parse_args()

    if args.check or not args.job:
        sys.exit(check())

    job = Path(args.job)
    if not job.is_dir():
        job = ROOT / args.job
    if not job.is_dir():
        print(f"לא נמצאה תיקייה: {args.job}")
        sys.exit(1)
    if not args.idx:
        print("צריך מספר קטע.")
        sys.exit(1)

    if args.thumb:
        res = apply_thumbnail(job, args.idx)
        print("התמנייל עלה." if res.get("ok") else f"נכשל: {res.get('error','')}")
        sys.exit(0 if res.get("ok") else 1)

    if args.refresh_desc or args.refresh_meta:
        res = refresh_description(job, args.idx, with_title=args.refresh_meta)
        what = "הכותרת, התיאור והתגיות" if args.refresh_meta else "התיאור"
        if not res["ok"]:
            print(f"נכשל: {res['error']}")
        elif not res.get("changed"):
            print(f"[{args.idx}] {what} ביוטיוב כבר זהים. לא נשלח כלום.")
        else:
            print(f"[{args.idx}] {what} עודכנו: https://youtu.be/{res['id']}")
            if res.get("title_changed"):
                print(f"  כותרת לפני: {res['old_title']}")
                print(f"  כותרת אחרי: {res['title']}")
            if res.get("credit_missing"):
                print("  ⚠ עדיין בלי קישור: " + ", ".join(res["credit_missing"]))
        sys.exit(0 if res["ok"] else 1)

    if args.publish:
        res = publish(job, args.idx, args.privacy or "public")
        print(res["url"] if res["ok"] else f"נכשל: {res['error']}")
        sys.exit(0 if res["ok"] else 1)

    if quota_left() <= 0:
        print("המכסה היומית של יוטיוב נגמרה (100 העלאות). נסה מחר.")
        sys.exit(1)

    res = upload_clip(job, args.idx, args.privacy,
                      progress=lambda p: print(f"\r  {p}%", end="", flush=True))
    print()
    if not res["ok"]:
        print(f"נכשל: {res['error']}")
        sys.exit(1)
    print(f"הועלה: {res['url']}  ({res.get('privacy','')})")
    if res.get("thumb_ok"):
        print("תמנייל: עלה.")
    else:
        print(f"⚠ תמנייל: {res.get('thumb_error','')}  (לנסות שוב: --thumb)")
    if res.get("locked"):
        print(f"⚠ ביקשנו {res['asked']} ויוטיוב קבע {res['privacy']}.")
        print("  זה הסימן שהפרויקט עוד לא עבר audit. ראה YOUTUBE_SETUP.md שלב 8.")


if __name__ == "__main__":
    main()
