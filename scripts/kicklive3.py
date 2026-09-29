"""
kicklive.py - בודק אם סטרימר משדר עכשיו, ומאתר את ה-VOD האחרון שלו

משתמש באותה טכניקה שעבדה: דפדפן ברקע שמריץ fetch מתוך העמוד,
כך שהבקשה נושאת את כל העוגיות והכותרות של האפליקציה.

שימוש:
    python scripts\\kicklive.py ronengg
    python scripts\\kicklive.py ronengg masterohad zigzagzong
    python scripts\\kicklive.py ronengg --debug        להראות איזו כתובת עבדה
    python scripts\\kicklive.py ronengg --vod          גם לאתר את ה-VOD האחרון
"""

import sys
import json
import argparse
from pathlib import Path


def _find_root(start: Path) -> Path:
    for c in [start, *start.parents]:
        if (c / 'scripts').is_dir():
            return c
    return start


ROOT = _find_root(Path(__file__).resolve().parent)

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    raise SystemExit("חסר playwright. הרץ:  pip install playwright && playwright install chromium")


CHANNEL_ENDPOINTS = [
    "https://web.kick.com/api/v1/channels/{slug}",
    "https://kick.com/api/v2/channels/{slug}",
    "https://kick.com/api/v1/channels/{slug}",
]

VOD_ENDPOINTS = [
    "https://web.kick.com/api/v1/channels/{slug}/videos",
    "https://kick.com/api/v2/channels/{slug}/videos",
]

FETCH_JS = """
async (url) => {
  try {
    const r = await fetch(url, {
      credentials: 'include',
      headers: { 'Accept': 'application/json' }
    });
    if (!r.ok) return { __err: r.status };
    return await r.json();
  } catch (e) { return { __err: String(e) }; }
}
"""


def walk(obj):
    if isinstance(obj, dict):
        yield obj
        for v in obj.values():
            yield from walk(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from walk(v)


def read_status(payload) -> dict:
    """מחלץ מצב שידור מתשובת ערוץ."""
    if not isinstance(payload, (dict, list)):
        return {}

    out = {"live": False, "title": "", "viewers": 0, "started": ""}

    for obj in walk(payload):
        if not isinstance(obj, dict):
            continue
        # אובייקט livestream פעיל
        if "is_live" in obj:
            if obj.get("is_live"):
                out["live"] = True
                out["title"] = obj.get("session_title") or obj.get("title") or out["title"]
                out["viewers"] = obj.get("viewer_count") or obj.get("viewers") or out["viewers"]
                out["started"] = obj.get("start_time") or obj.get("created_at") or out["started"]
        if obj.get("livestream") is None and "livestream" in obj:
            pass  # livestream=null פירושו לא משדר

    # מקרה נפוץ: {"livestream": {...}} או {"livestream": null}
    if isinstance(payload, dict):
        ls = payload.get("livestream")
        if isinstance(ls, dict):
            out["live"] = True
            out["title"] = ls.get("session_title") or out["title"]
            out["viewers"] = ls.get("viewer_count") or out["viewers"]
            out["started"] = ls.get("start_time") or ls.get("created_at") or out["started"]
        elif ls is None and "livestream" in payload:
            out["live"] = out["live"] or False

    return out


def read_vods(payload) -> list:
    """מחלץ רשימת VODs."""
    items = []
    if isinstance(payload, dict):
        items = payload.get("data") or payload.get("videos") or []
    elif isinstance(payload, list):
        items = payload

    out = []
    for it in items:
        if not isinstance(it, dict):
            continue
        video = it.get("video") if isinstance(it.get("video"), dict) else {}

        # הבאג של 9.9: הרשומה מכילה id מספרי של השידור וגם video.uuid.
        # קיק מגישה נגן רק לכתובת עם ה-uuid. הגרסה הקודמת העדיפה את
        # ה-id המספרי, בנתה כתובת שלא נפתחת, וחמישה לייבים נפלו.
        uuid = video.get("uuid") or it.get("uuid") or ""
        num = it.get("id") or video.get("id") or ""
        vid = uuid or num
        if not vid:
            continue

        # אם ה-API כבר נותן את כתובת ההזרמה - אין צורך בדפדפן בכלל
        source = it.get("source") or video.get("source") or ""
        if not (isinstance(source, str) and ".m3u8" in source):
            source = ""

        out.append({
            "id": str(vid),
            "numeric_id": str(num),
            "uuid": str(uuid),
            "title": it.get("session_title") or it.get("title") or "",
            "start": it.get("start_time") or it.get("created_at") or "",
            "duration": it.get("duration") or 0,
            "source": source,
        })
    return out


def check(slugs: list, want_vod: bool = False, debug: bool = False, headless: bool = True, raw: bool = False) -> dict:
    results = {}

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        ctx = browser.new_context(
            viewport={"width": 1280, "height": 800},
            user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"),
        )
        page = ctx.new_page()
        try:
            page.goto("https://kick.com/", wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(2500)
        except Exception as exc:
            print(f"טעינת kick.com נכשלה: {exc}", file=sys.stderr)

        def get(url):
            try:
                data = page.evaluate(FETCH_JS, url)
            except Exception as exc:
                return None, str(exc)
            if isinstance(data, dict) and "__err" in data:
                return None, data["__err"]
            return data, None

        for slug in slugs:
            entry = {"slug": slug, "live": False, "title": "", "viewers": 0,
                     "started": "", "vods": [], "source": ""}

            for tpl in CHANNEL_ENDPOINTS:
                url = tpl.format(slug=slug)
                data, err = get(url)
                if debug:
                    print(f"  {'OK ' if data else err}  {url}")
                if data:
                    if raw:
                        d = ROOT / "scan_debug"
                        d.mkdir(exist_ok=True)
                        (d / f"channel_{slug}.json").write_text(
                            json.dumps(data, ensure_ascii=False, indent=1)[:300000],
                            encoding="utf-8")
                        keys = list(data.keys()) if isinstance(data, dict) else "list"
                        print(f"     מפתחות: {keys}")
                        if isinstance(data, dict):
                            ls = data.get("livestream")
                            print(f"     livestream: {type(ls).__name__}"
                                  + (f"  מפתחות: {list(ls.keys())[:12]}" if isinstance(ls, dict) else ""))
                    st = read_status(data)
                    entry.update({k: st.get(k, entry[k]) for k in ("live", "title", "viewers", "started")})
                    entry["source"] = url
                    break

            if want_vod:
                for tpl in VOD_ENDPOINTS:
                    url = tpl.format(slug=slug)
                    data, err = get(url)
                    if debug:
                        print(f"  {'OK ' if data else err}  {url}")
                    if data:
                        entry["vods"] = read_vods(data)[:5]
                        break

            results[slug] = entry

        browser.close()

    return results


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("slugs", nargs="+")
    ap.add_argument("--vod", action="store_true", help="גם לאתר את ה-VODs האחרונים")
    ap.add_argument("--debug", action="store_true")
    ap.add_argument("--raw", action="store_true", help="לשמור את התשובה הגולמית")
    ap.add_argument("--show", action="store_true")
    ap.add_argument("--json", action="store_true", help="פלט JSON בלבד")
    args = ap.parse_args()

    res = check(args.slugs, want_vod=args.vod, debug=args.debug, headless=not args.show, raw=args.raw)

    if args.json:
        print(json.dumps(res, ensure_ascii=False, indent=1))
        return

    for slug, e in res.items():
        mark = "משדר" if e["live"] else "לא משדר"
        line = f"{slug:16} {mark}"
        if e["live"]:
            line += f"  {e['viewers']} צופים  |  {e['title'][:50]}"
        print(line)
        if e["vods"]:
            print("   VODs אחרונים:")
            for v in e["vods"]:
                mins = (v["duration"] or 0) / 60000 if v["duration"] > 100000 else (v["duration"] or 0) / 60
                print(f"     {v['start'][:16]}  {mins:5.0f} דק'  {v['title'][:40]}")
                print(f"       https://kick.com/{slug}/videos/{v['id']}")
        if not e["source"]:
            print("   לא הצלחתי לקרוא את מצב הערוץ. הרץ עם --debug")


if __name__ == "__main__":
    main()
