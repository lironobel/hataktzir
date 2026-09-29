"""
livecheck.py - בודק מי משדר, בכל פלטפורמה

עד עכשיו המערכת ידעה רק קיק. כאן היא לומדת גם טוויץ' ויוטיוב.

הפרדוקס הנחמד: קיק היה הקשה מכולם. שם נאלצנו לפתוח דפדפן ברקע כדי
לעקוף 401, ואז ליירט m3u8. טוויץ' ויוטיוב נתמכים ב-yt-dlp מקורית -
שאילתה אחת מחזירה מצב שידור, ואת אותה כתובת אפשר להוריד ישירות.

שימוש:
    python scripts\\livecheck.py kick:ronengg twitch:forceee youtube:@thecohen
    python scripts\\livecheck.py --watchlist          כל מי שפעיל ברשימה
    python scripts\\livecheck.py twitch:forceee --vod --debug
    python scripts\\livecheck.py thecohen indegame --verify    האם הערוץ בקיק אמיתי

מבנה התשובה זהה לכל הפלטפורמות, כדי שהמנטר לא יצטרך לדעת מאיפה זה בא:
    {live, title, viewers, started, vods: [{id, url, title, start, duration}]}
"""

import re
import sys
import json
import shutil
import argparse
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

PLATFORMS = ("kick", "twitch", "youtube")


MIN_VOD_MINUTES = 20          # פחות מזה זה קליפ או היילייט, לא שידור
SKIP_TITLE_PREFIX = ("highlight:", "clip:", "קליפ:")


def to_iso(value) -> str:
    """
    כל פלטפורמה מחזירה זמן אחרת: טוויץ' ויוטיוב חותמת יוניקס,
    קיק מחרוזת ISO. מנרמלים לצורה אחת כדי שהמנטר לא יתבלבל.
    """
    if not value:
        return ""
    if isinstance(value, (int, float)) or str(value).isdigit():
        try:
            return datetime.fromtimestamp(int(value), tz=timezone.utc).isoformat(timespec="seconds")
        except Exception:
            return ""
    return str(value)


def to_secs(value, platform: str) -> float:
    """קיק מחזיר מילישניות, yt-dlp שניות."""
    try:
        v = float(value or 0)
    except Exception:
        return 0.0
    if platform == "kick" and v > 100000:
        v /= 1000.0
    return v


def real_broadcast(title: str, seconds: float) -> bool:
    """
    האם זה שידור מלא ולא קליפ. טוויץ' מגיש הכל באותה רשימה,
    ובלי הסינון הזה הצינור עלול לרוץ שעתיים על סרטון של דקה.
    """
    t = (title or "").strip().lower()
    if any(t.startswith(pfx) for pfx in SKIP_TITLE_PREFIX):
        return False
    return seconds >= MIN_VOD_MINUTES * 60


def blank(slug: str, platform: str) -> dict:
    return {"slug": slug, "platform": platform, "live": False, "title": "",
            "viewers": 0, "started": "", "vods": [], "url": "", "error": ""}


# ------------------------------------------------------------- yt-dlp

def ytdlp_json(url: str, flat: bool = False, limit: int = 5,
               debug: bool = False, timeout: int = 90):
    """
    מריץ yt-dlp ומחזיר (נתונים, שגיאה). yt-dlp נכשל כשערוץ לא משדר,
    וזו תשובה לגיטימית - לא תקלה.
    """
    if not shutil.which("yt-dlp"):
        return None, "yt-dlp לא מותקן"

    cmd = ["yt-dlp", "-J", "--no-warnings", "--ignore-config"]
    if flat:
        cmd += ["--flat-playlist", "--playlist-end", str(limit)]
    else:
        cmd += ["--no-playlist"]
    cmd.append(url)

    try:
        r = subprocess.run(cmd, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=timeout)
    except subprocess.TimeoutExpired:
        return None, "פסק זמן"
    except Exception as exc:
        return None, str(exc)

    if debug and r.stderr:
        print(f"    yt-dlp: {r.stderr.strip()[:300]}", file=sys.stderr)

    out = (r.stdout or "").strip()
    if out:
        try:
            return json.loads(out), ""
        except json.JSONDecodeError:
            return None, "פלט לא תקין"

    err = (r.stderr or "").strip()
    low = err.lower()
    if "not currently live" in low or "no video" in low or "not live" in low:
        return None, "offline"
    if "does not exist" in low or "not found" in low or "404" in low:
        return None, "הערוץ לא קיים"
    return None, (err.splitlines()[-1][:160] if err else "אין תשובה")


def is_live_payload(d: dict) -> bool:
    if not isinstance(d, dict):
        return False
    if d.get("is_live") is True:
        return True
    return d.get("live_status") in ("is_live", "is_upcoming_live")


def viewers_of(d: dict) -> int:
    for k in ("concurrent_view_count", "viewer_count", "view_count"):
        v = d.get(k)
        if isinstance(v, int) and v > 0:
            return v
    return 0


# ------------------------------------------------------------- טוויץ'

def check_twitch(name: str, want_vod: bool, debug: bool) -> dict:
    e = blank(name, "twitch")
    e["url"] = f"https://www.twitch.tv/{name}"

    d, err = ytdlp_json(e["url"], debug=debug)
    if d:
        e["live"] = is_live_payload(d)
        e["title"] = d.get("title") or d.get("description") or ""
        e["viewers"] = viewers_of(d)
        e["started"] = str(d.get("timestamp") or "")
    elif err and err != "offline":
        e["error"] = err

    if want_vod:
        # filter=archives מבקש רק שידורים מלאים. הסינון בקוד הוא
        # רשת ביטחון, כי טוויץ' לא תמיד מכבד את הפרמטר.
        d, err = ytdlp_json(f"{e['url']}/videos?filter=archives&sort=time",
                            flat=True, limit=8, debug=debug)
        if d:
            for it in (d.get("entries") or []):
                vid = str(it.get("id") or "")
                title = it.get("title") or ""
                secs = to_secs(it.get("duration"), "twitch")
                if not vid:
                    continue
                if not real_broadcast(title, secs):
                    if debug:
                        print(f"    דילגתי: {title[:40]} ({secs/60:.0f} דק')", file=sys.stderr)
                    continue
                e["vods"].append({
                    "id": vid,
                    "url": it.get("url") or f"https://www.twitch.tv/videos/{vid.lstrip('v')}",
                    "title": title,
                    "start": to_iso(it.get("timestamp")),
                    "duration": secs,
                })
                if len(e["vods"]) >= 5:
                    break
        elif err and err != "offline" and not e["error"]:
            e["error"] = err
    return e


# ------------------------------------------------------------- יוטיוב

def check_youtube(handle: str, want_vod: bool, debug: bool) -> dict:
    h = handle if handle.startswith("@") else f"@{handle}"
    e = blank(h, "youtube")
    e["url"] = f"https://www.youtube.com/{h}"

    # /live מוביל לשידור הפעיל. כשאין - יוטיוב מגיש משהו אחר,
    # ולכן בודקים את הדגל במפורש ולא מסתפקים בכך שהתקבלה תשובה.
    d, err = ytdlp_json(f"{e['url']}/live", debug=debug)
    if d:
        if is_live_payload(d):
            e["live"] = True
            e["title"] = d.get("title") or ""
            e["viewers"] = viewers_of(d)
            e["started"] = str(d.get("timestamp") or "")
    elif err and err != "offline":
        e["error"] = err

    if want_vod:
        d, err = ytdlp_json(f"{e['url']}/streams", flat=True, debug=debug)
        if d:
            for it in (d.get("entries") or []):
                vid = str(it.get("id") or "")
                title = it.get("title") or ""
                secs = to_secs(it.get("duration"), "youtube")
                if not vid or it.get("live_status") == "is_upcoming":
                    continue
                if not real_broadcast(title, secs):
                    if debug:
                        print(f"    דילגתי: {title[:40]} ({secs/60:.0f} דק')", file=sys.stderr)
                    continue
                e["vods"].append({
                    "id": vid,
                    "url": f"https://www.youtube.com/watch?v={vid}",
                    "title": title,
                    "start": to_iso(it.get("timestamp")),
                    "duration": secs,
                })
                if len(e["vods"]) >= 5:
                    break
        elif err and err != "offline" and not e["error"]:
            e["error"] = err
    return e


# ---------------------------------------------------------------- קיק

def check_kick(slugs: list, want_vod: bool, debug: bool) -> dict:
    """קיק דורש דפדפן, ולכן כל הערוצים נבדקים בהפעלה אחת."""
    if not slugs:
        return {}
    kc = None
    for mod in ("kicklive3", "kicklive2", "kicklive"):
        try:
            kc = __import__(mod).check
            break
        except ImportError:
            continue
    if kc is None:
        return {s: dict(blank(s, "kick"), error="kicklive לא נמצא") for s in slugs}

    try:
        raw = kc(slugs, want_vod=want_vod, debug=debug)
    except Exception as exc:
        return {s: dict(blank(s, "kick"), error=str(exc)[:160]) for s in slugs}

    out = {}
    for s in slugs:
        r = raw.get(s, {})
        e = blank(s, "kick")
        e.update({
            "live": bool(r.get("live")),
            "title": r.get("title", ""),
            "viewers": r.get("viewers", 0),
            "started": r.get("started", ""),
            "url": f"https://kick.com/{s}",
        })
        for v in (r.get("vods") or []):
            secs = to_secs(v.get("duration"), "kick")
            if not real_broadcast(v.get("title", ""), secs):
                continue
            e["vods"].append({
                "id": str(v.get("id", "")),
                "url": f"https://kick.com/{s}/videos/{v.get('id','')}",
                "title": v.get("title", ""),
                "start": to_iso(v.get("start", "")),
                "duration": secs,
                "m3u8": v.get("source", ""),
            })
        if not r.get("source") and not r.get("live"):
            e["error"] = "לא הצלחתי לקרוא את הערוץ"
        out[s] = e
    return out



# ------------------------------------------------------- אימות ערוץ בקיק

VERIFY_JS = """
async (url) => {
  try {
    const r = await fetch(url, {credentials:'include', headers:{'Accept':'application/json'}});
    if (!r.ok) return { __err: r.status };
    return await r.json();
  } catch (e) { return { __err: String(e) }; }
}
"""


def verify_kick(slugs: list, headless: bool = True) -> dict:
    """
    קיק מגישה עמוד גם לשם שנרשם ומעולם לא שודר בו. לכן "לא משדר"
    אינו הוכחה שהערוץ הנכון. כאן בודקים עוקבים ותוכן, וזה מה
    שמבדיל בין הערוץ האמיתי לבין שם תפוס.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return {s: {"error": "חסר playwright"} for s in slugs}

    out = {}
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        ctx = browser.new_context(
            user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"))
        page = ctx.new_page()
        try:
            page.goto("https://kick.com/", wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(2500)
        except Exception as exc:
            print(f"טעינת kick.com נכשלה: {exc}", file=sys.stderr)

        for slug in slugs:
            info = {"slug": slug, "exists": False, "followers": 0,
                    "vods": 0, "last": "", "verdict": "", "error": ""}
            try:
                d = page.evaluate(VERIFY_JS, f"https://kick.com/api/v2/channels/{slug}")
            except Exception as exc:
                info["error"] = str(exc)[:120]
                out[slug] = info
                continue

            if isinstance(d, dict) and "__err" in d:
                info["verdict"] = "לא קיים"
                out[slug] = info
                continue

            info["exists"] = True
            if isinstance(d, dict):
                info["followers"] = (d.get("followers_count")
                                     or d.get("followersCount") or 0)

            try:
                v = page.evaluate(VERIFY_JS, f"https://kick.com/api/v2/channels/{slug}/videos")
                items = v if isinstance(v, list) else (v or {}).get("data") or []
                info["vods"] = len(items)
                if items and isinstance(items[0], dict):
                    info["last"] = str(items[0].get("start_time")
                                       or items[0].get("created_at") or "")[:10]
            except Exception:
                pass

            if info["vods"] > 0:
                info["verdict"] = "ערוץ פעיל"
            elif info["followers"] >= 200:
                info["verdict"] = "יש קהל אבל אין שידורים - אולי עבר פלטפורמה"
            else:
                info["verdict"] = "שם תפוס, ריק. כמעט בטוח לא הערוץ שחיפשת"
            out[slug] = info

        browser.close()
    return out

# ------------------------------------------------------------- ניתוב

def parse_target(text: str) -> tuple:
    """מקבל 'twitch:forceee' או 'ronengg' ומחזיר (פלטפורמה, מזהה)."""
    text = text.strip()
    if ":" in text:
        p, _, rest = text.partition(":")
        if p.lower() in PLATFORMS:
            return p.lower(), rest.strip()
    m = re.match(r"https?://(?:www\.)?(kick|twitch)\.tv?/([^/?#]+)", text, re.I)
    if m:
        host = m.group(1).lower()
        return ("twitch" if host == "twitch" else "kick"), m.group(2)
    if "youtube.com" in text.lower():
        m = re.search(r"youtube\.com/(@[^/?#]+)", text, re.I)
        if m:
            return "youtube", m.group(1)
    if "kick.com" in text.lower():
        m = re.search(r"kick\.com/([^/?#]+)", text, re.I)
        if m:
            return "kick", m.group(1)
    if text.startswith("@"):
        return "youtube", text
    return "kick", text


def check(targets: list, want_vod: bool = False, debug: bool = False) -> dict:
    """
    targets: רשימה של 'platform:id' או של dict מה-watchlist.
    מחזיר מיפוי מהמזהה המקורי לתוצאה.
    """
    kick_slugs, others = [], []
    keys = {}

    for t in targets:
        if isinstance(t, dict):
            platform = (t.get("platform") or "kick").lower()
            ident = t.get("slug") or t.get("handle") or ""
            # אותו אדם יכול לשדר בשתי פלטפורמות עם אותו שם משתמש.
            # key מבדיל ביניהם, אחרת רשומה אחת דורסת את השנייה.
            key = t.get("key") or t.get("slug") or ident
        else:
            platform, ident = parse_target(str(t))
            key = str(t)
        if not ident:
            continue
        keys[(platform, ident)] = key
        if platform == "kick":
            kick_slugs.append(ident)
        else:
            others.append((platform, ident))

    results = {}

    for slug, e in check_kick(kick_slugs, want_vod, debug).items():
        results[keys.get(("kick", slug), slug)] = e

    for platform, ident in others:
        key = keys.get((platform, ident), ident)
        if debug:
            print(f"  בודק {platform}:{ident}", file=sys.stderr)
        if platform == "twitch":
            results[key] = check_twitch(ident, want_vod, debug)
        elif platform == "youtube":
            results[key] = check_youtube(ident, want_vod, debug)

    return results


# ------------------------------------------------------------------ ראשי

def from_watchlist() -> list:
    wl = {}
    p = ROOT / "watchlist.json"
    if p.exists():
        try:
            wl = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            pass
    return [s for s in wl.get("streamers", []) if s.get("active")]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("targets", nargs="*", help="platform:id או קישור מלא")
    ap.add_argument("--watchlist", action="store_true", help="כל הפעילים ברשימה")
    ap.add_argument("--vod", action="store_true", help="גם לאתר שידורים קודמים")
    ap.add_argument("--debug", action="store_true")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--verify", action="store_true",
                    help="לבדוק אם ערוצי קיק באמת קיימים ופעילים, ולא רק שם תפוס")
    ap.add_argument("--show", action="store_true", help="דפדפן גלוי")
    args = ap.parse_args()

    if args.verify:
        slugs = [parse_target(t)[1] for t in args.targets] or \
                [s["slug"] for s in from_watchlist()
                 if (s.get("platform") or "kick") == "kick" and s.get("slug")]
        if not slugs:
            print("תן שמות ערוצים בקיק לבדיקה.")
            sys.exit(1)
        print(f"בודק {len(slugs)} ערוצים בקיק...\n")
        res = verify_kick(slugs, headless=not args.show)
        print(f"{'ערוץ':18} {'עוקבים':>8} {'שידורים':>9} {'אחרון':12} מסקנה")
        print("-" * 92)
        for slug, i in res.items():
            if i.get("error"):
                print(f"{slug:18} {'—':>8} {'—':>9} {'—':12} שגיאה: {i['error']}")
                continue
            print(f"{slug:18} {i['followers']:>8} {i['vods']:>9} "
                  f"{(i['last'] or '—'):12} {i['verdict']}")
        return

    targets = from_watchlist() if args.watchlist else args.targets
    if not targets:
        print("תן יעדים, או --watchlist")
        sys.exit(1)

    names = {}
    if args.watchlist:
        names = {t["slug"]: t.get("name", t["slug"]) for t in targets}

    res = check(targets, want_vod=args.vod, debug=args.debug)

    if args.json:
        print(json.dumps(res, ensure_ascii=False, indent=1))
        return

    print(f"\n{'ערוץ':22} {'פלטפורמה':10} מצב")
    print("-" * 78)
    for key, e in res.items():
        label = names.get(key, key)
        if e.get("error"):
            state = f"שגיאה — {e['error']}"
        elif e["live"]:
            state = f"משדר · {e['viewers']} צופים · {e['title'][:40]}"
        else:
            state = "לא משדר"
        print(f"{label[:21]:22} {e['platform']:10} {state}")
        if e["vods"]:
            for v in e["vods"][:3]:
                mins = (v["duration"] or 0) / 60
                when = (v["start"] or "")[:10]
                print(f"    {mins:5.0f} דק'  {when:11} {v['title'][:38]}")
                print(f"           {v['url']}")


if __name__ == "__main__":
    main()
