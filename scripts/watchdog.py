"""
watchdog.py - שומר חיצוני לניטור. רץ מתזמן המשימות של ווינדוס, לא מתוך המנטר.

למה: ב-10.9 בערב המנטר קפא (כנראה QuickEdit - לחיצה בחלון), ואיתו
הבוט, כי שניהם באותו תהליך. חמישה ימים עברו בלי לייב אחד שעובד,
ובלי שום הודעה - כי מי שהיה אמור לשלוח אותה הוא זה שקפא.
שומר שרץ בתוך אותו תהליך לא יכול לתפוס את זה. לכן תהליך נפרד.

מה הוא בודק: monitor_state.json נכתב בסוף כל סבב (כל 5 דקות). אם הוא
לא התעדכן STALE_MIN דקות - שולח התראה לטלגרם. פעם אחת לכל תקלה,
והודעה נוספת כשהמנטר חוזר.

התקנה, פעם אחת, בחלון cmd:
    schtasks /Create /SC MINUTE /MO 30 /TN HataktzirWatchdog /TR "pythonw C:\\Users\\97253\\Desktop\\Project-clips\\scripts\\watchdog.py"
בדיקה ידנית:
    python scripts\\watchdog.py --verbose
הסרה:
    schtasks /Delete /TN HataktzirWatchdog /F
"""

import sys
import json
import time
import argparse
from pathlib import Path
from datetime import datetime, timezone


def find_root(start: Path) -> Path:
    for c in [start, *start.parents]:
        if (c / "scripts").is_dir():
            return c
    return start


ROOT = find_root(Path(__file__).resolve().parent)
sys.path.insert(0, str(ROOT / "scripts"))

STATE = ROOT / "monitor_state.json"
MINE = ROOT / "watchdog_state.json"
STALE_MIN = 20          # 4 סבבים של המנטר


def load(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    try:
        from tg import notify
    except Exception:
        def notify(text, **kw):
            print(text)
            return False

    age_min = (time.time() - STATE.stat().st_mtime) / 60 if STATE.exists() else 1e9
    mine = load(MINE)
    alerted = mine.get("alerted", False)
    stale = age_min > STALE_MIN

    if args.verbose:
        print(f"monitor_state.json עודכן לפני {age_min:.0f} דקות. "
              f"{'תקוע' if stale else 'תקין'}.")

    if stale and not alerted:
        when = (datetime.fromtimestamp(STATE.stat().st_mtime).strftime("%d/%m %H:%M")
                if STATE.exists() else "אף פעם")
        ok = notify(
            "<b>⚠ המנטר לא רץ</b>\n"
            f"הסבב האחרון הסתיים ב-{when} ({age_min/60:.1f} שעות).\n"
            "לייבים לא נקלטים עכשיו.\n\n"
            "1. אם חלון Clips Monitor פתוח - ללחוץ בו Enter (קיפאון QuickEdit).\n"
            "2. אם אין חלון - להפעיל את start_monitor.bat.", important=True)
        if ok:
            mine = {"alerted": True,
                    "at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    elif not stale and alerted:
        notify("✓ המנטר חזר לרוץ.", silent=True)
        mine = {"alerted": False}
    MINE.write_text(json.dumps(mine, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    main()
