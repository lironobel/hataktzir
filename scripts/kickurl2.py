"""
kickurl2.py - מחלץ את כתובת ה-m3u8 של VOD בקיק, ומסביר כשזה לא מצליח

מה השתנה מ-kickurl:
    הגרסה הקודמת ידעה רק "מצאתי / לא מצאתי". כשחמישה לייבים נפלו
    בלילה אחד, לא היה שום רמז למה. עכשיו יש שתי דרכים ותשובה מנומקת:

    1. ה-API של קיק, דרך fetch מתוך העמוד (אותה טכניקה של kicklive2).
       מחזיר את שדה source של ה-VOD. אם ה-VOD קיים אבל source ריק -
       קיק עדיין מעבדת אותו, וזו סיבה שאפשר לחכות איתה.
    2. יירוט הנגן, כמו קודם - גיבוי למקרה שה-API השתנה.

שימוש:
    python scripts\\kickurl2.py https://kick.com/shonp/videos/126461367
    python scripts\\kickurl2.py <url> --debug
    python scripts\\kickurl2.py <url> --show

מתוך קוד:
    url, reason = grab_m3u8_ex(vod_url)
    reason אחד מ: api · player · processing · not_found · timeout · error
"""

import re
import sys
import time
import json
import argparse

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    print("חסר playwright. הרץ:  pip install playwright && playwright install chromium",
          file=sys.stderr)
    sys.exit(1)


UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")

FETCH_JS = """
async (url) => {
  try {
    const r = await fetch(url, {credentials:'include', headers:{'Accept':'application/json'}});
    if (!r.ok) return { __err: r.status };
    return await r.json();
  } catch (e) { return { __err: String(e) }; }
}
"""


def parse_vod_url(vod_url: str) -> tuple:
    """מחזיר (slug, video_id). video_id יכול להיות מספר או uuid."""
    m = re.search(r"kick\.com/([^/?#]+)/videos?/([^/?#]+)", vod_url, re.I)
    if m:
        return m.group(1), m.group(2)
    m = re.search(r"kick\.com/video/([^/?#]+)", vod_url, re.I)
    if m:
        return "", m.group(1)
    return "", ""


def walk(obj):
    if isinstance(obj, dict):
        yield obj
        for v in obj.values():
            yield from walk(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from walk(v)


def find_source(payload, video_id: str) -> tuple:
    """
    מחפש בתשובת ה-API את הרשומה של ה-VOD ואת שדה source שלה.
    מחזיר (source, found_record).
    """
    vid = str(video_id)
    for obj in walk(payload):
        if not isinstance(obj, dict):
            continue
        ids = {str(obj.get("id", "")), str(obj.get("uuid", ""))}
        inner_video = obj.get("video")
        if isinstance(inner_video, dict):
            ids.add(str(inner_video.get("uuid", "")))
            ids.add(str(inner_video.get("id", "")))
        if vid not in ids:
            continue
        # הרשומה נמצאה. source יכול לשבת בה ישירות או רמה אחת פנימה.
        for candidate in walk(obj):
            if isinstance(candidate, dict):
                s = candidate.get("source")
                if isinstance(s, str) and ".m3u8" in s:
                    return s, True
        return "", True
    return "", False


def find_uuid(payload, numeric_id: str) -> str:
    """מוצא את ה-uuid של VOD לפי ה-id המספרי שלו."""
    for obj in walk(payload):
        if not isinstance(obj, dict):
            continue
        if str(obj.get("id", "")) != str(numeric_id):
            continue
        video = obj.get("video")
        if isinstance(video, dict) and video.get("uuid"):
            return str(video["uuid"])
        if obj.get("uuid"):
            return str(obj["uuid"])
    return ""


def grab_m3u8_ex(vod_url: str, headless: bool = True, timeout_s: int = 45,
                 debug: bool = False) -> tuple:
    slug, vid = parse_vod_url(vod_url)
    found_player = []

    def dbg(msg):
        if debug:
            print(f"  {msg}", file=sys.stderr)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        ctx = browser.new_context(viewport={"width": 1280, "height": 720}, user_agent=UA)
        page = ctx.new_page()
        page.on("request", lambda r: found_player.append(r.url) if ".m3u8" in r.url else None)

        # ---------- דרך 1: ה-API ----------
        try:
            page.goto("https://kick.com/", wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(2000)
        except Exception as exc:
            dbg(f"טעינת kick.com נכשלה: {exc}")

        def api(url):
            try:
                d = page.evaluate(FETCH_JS, url)
            except Exception as exc:
                dbg(f"fetch נכשל: {exc}")
                return None
            if isinstance(d, dict) and "__err" in d:
                dbg(f"{d['__err']}  <- {url}")
                return None
            return d

        record_seen = False
        player_url = vod_url
        if vid:
            candidates = []
            if slug:
                candidates.append(f"https://kick.com/api/v2/channels/{slug}/videos")
            candidates.append(f"https://kick.com/api/v1/video/{vid}")
            for url in candidates:
                data = api(url)
                if data is None:
                    continue
                src, seen = find_source(data, vid)
                record_seen = record_seen or seen
                dbg(f"{'נמצא source' if src else ('רשומה בלי source' if seen else 'אין רשומה')}  <- {url}")
                if src:
                    browser.close()
                    return src, "api"
                # אין source, אבל אם הכתובת שקיבלנו מספרית - נמיר ל-uuid,
                # כי קיק מגישה נגן רק לכתובת עם uuid
                if seen and vid.isdigit() and slug:
                    uuid = find_uuid(data, vid)
                    if uuid:
                        player_url = f"https://kick.com/{slug}/videos/{uuid}"
                        dbg(f"כתובת מספרית -> uuid: {player_url}")

        # ---------- דרך 2: הנגן ----------
        try:
            page.goto(player_url, wait_until="domcontentloaded", timeout=30000)
        except Exception as exc:
            dbg(f"טעינת עמוד ה-VOD נכשלה: {exc}")

        body = ""
        try:
            body = (page.inner_text("body") or "")[:4000]
        except Exception:
            pass
        low = body.lower()
        if any(k in low for k in ("video not found", "this video is not available",
                                  "channel not found", "page not found")):
            browser.close()
            return "", "not_found"
        if any(k in low for k in ("processing", "being processed", "is still processing")):
            browser.close()
            return "", "processing"

        for selector in ("video", "button[aria-label*='lay']", ".vjs-big-play-button"):
            try:
                el = page.query_selector(selector)
                if el:
                    el.click(timeout=3000)
                    break
            except Exception:
                pass

        deadline = time.time() + timeout_s
        while time.time() < deadline:
            if any("master.m3u8" in u for u in found_player):
                break
            if found_player and time.time() > deadline - timeout_s + 15:
                break
            page.wait_for_timeout(500)

        browser.close()

    if found_player:
        master = [u for u in found_player if "master.m3u8" in u]
        return (master[0] if master else found_player[0]), "player"

    # ה-API ראה את ה-VOD אבל בלי source, והנגן לא הזרים - קיק עוד מעבדת
    if record_seen:
        return "", "processing"
    return "", "timeout"


def grab_m3u8(vod_url: str, headless: bool = True, timeout_s: int = 45) -> str:
    """תאימות לאחור - מחזיר רק את הכתובת."""
    url, _ = grab_m3u8_ex(vod_url, headless=headless, timeout_s=timeout_s)
    return url


REASONS = {
    "api": "התקבל מה-API",
    "player": "יורט מהנגן",
    "processing": "ה-VOD קיים אבל קיק עדיין מעבדת אותו. לחכות ולנסות שוב",
    "not_found": "ה-VOD לא קיים (נמחק, או שהקישור שגוי)",
    "timeout": "לא הגיעה בקשת m3u8 בזמן. אולי קיק שינתה את הנגן - נסה עם --show",
    "error": "שגיאה",
}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("url")
    ap.add_argument("--show", action="store_true")
    ap.add_argument("--timeout", type=int, default=45)
    ap.add_argument("--debug", action="store_true")
    args = ap.parse_args()

    url, reason = grab_m3u8_ex(args.url, headless=not args.show,
                               timeout_s=args.timeout, debug=args.debug)
    if not url:
        print(f"לא נמצאה כתובת. סיבה: {reason} — {REASONS.get(reason, '')}", file=sys.stderr)
        sys.exit(2)
    if args.debug:
        print(f"  מקור: {REASONS.get(reason, reason)}", file=sys.stderr)
    print(url)


if __name__ == "__main__":
    main()
