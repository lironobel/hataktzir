"""
checkname.py - בודק אם שם פנוי ביוטיוב, בקיק ובטוויץ', בבת אחת

שימוש:
    python scripts\\checkname.py livon
    python scripts\\checkname.py livon klipo mehalive

מה כן ומה לא:
    יוטיוב   בדיקה אמינה. 404 = פנוי
    קיק      בדיקה אמינה דרך ה-API, כולל עוקבים
    טוויץ'   בדיקה אמינה דרך yt-dlp
    טיקטוק / אינסטגרם - חוסמים בדיקה אוטומטית. לבדוק ביד.

והכי חשוב: שם פנוי אינו שם טוב. תחפש אותו בגוגל לפני שאתה נועל -
ISRA היה פנוי בכיוון ורצוף בערוצים ספרדיים, ו-Livon הוא מותג סרום לשיער.
"""

import sys
import json
import shutil
import subprocess
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

try:
    import requests
except ImportError:
    raise SystemExit("חסר requests")


def youtube(name: str) -> str:
    try:
        r = requests.get(f"https://www.youtube.com/@{name}", timeout=20,
                         headers={"User-Agent": "Mozilla/5.0"}, allow_redirects=True)
    except Exception as exc:
        return f"שגיאה: {exc}"
    if r.status_code == 404:
        return "פנוי"
    if r.status_code == 200:
        return "תפוס"
    return f"לא ברור ({r.status_code})"


def twitch(name: str) -> str:
    if not shutil.which("yt-dlp"):
        return "אין yt-dlp"
    r = subprocess.run(["yt-dlp", "-J", "--no-warnings", "--ignore-config",
                        "--flat-playlist", "--playlist-end", "1",
                        f"https://www.twitch.tv/{name}/videos"],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    err = (r.stderr or "").lower()
    if r.stdout.strip():
        return "תפוס"
    if "does not exist" in err or "not found" in err or "404" in err:
        return "פנוי"
    return "לא ברור"


def kick(names: list) -> dict:
    try:
        from livecheck import verify_kick
    except ImportError:
        return {n: "אין livecheck" for n in names}
    res = verify_kick(names)
    out = {}
    for n, i in res.items():
        if i.get("error"):
            out[n] = f"שגיאה"
        elif not i.get("exists"):
            out[n] = "פנוי"
        else:
            out[n] = f"תפוס ({i.get('followers', 0)} עוקבים)"
    return out


def main() -> None:
    names = [n.strip().lstrip("@").lower() for n in sys.argv[1:] if n.strip()]
    if not names:
        print(__doc__)
        sys.exit(1)

    print("בודק בקיק...")
    k = kick(names)
    print()
    print(f"{'שם':14} {'יוטיוב':10} {'טוויץ':12} קיק")
    print("-" * 56)
    for n in names:
        print(f"{n:14} {youtube(n):10} {twitch(n):12} {k.get(n, '?')}")
    print()
    print("טיקטוק ואינסטגרם - לבדוק ביד: tiktok.com/@שם  instagram.com/שם")
    print("ואז לחפש את השם בגוגל. פנוי ≠ טוב.")


if __name__ == "__main__":
    main()
