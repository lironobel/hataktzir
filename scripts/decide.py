"""
decide.py - אישור / דחייה / דילוג על קטעים מחלון cmd, בלי טלגרם (29.9, בקשת לירון)

אותה לוגיקה בדיוק כמו /approve /reject /skip בבוט, וכמה פקודות בהרצה אחת:

    python scripts\\decide.py "approve masterohad_2026-09-15 7" "skip ronengg_2026-09-05-full 7,10"
    python scripts\\decide.py "reject odedsvr_2026-09-15 4 חלש מדי"
    python scripts\\decide.py --file decisions.txt        שורה לכל פקודה, # = הערה
    python scripts\\decide.py --pending                   מה עוד ממתין להחלטה

approve  מסמן מאושר ומלמד את המנתח. **לא מעלה מכאן** - התור בבוט מעלה בזמן שלו.
reject   נדחה, והסיבה (אם יש) נכנסת ללמידה.
skip     לא מעלים ולא מלמדים (ישן / כפול / רגיש).

למה לא --say של tgbot2: שם אישור מפעיל חוט שמנסה להעלות מיד, והתהליך יוצא
לפני שהוא מסיים - העלאה שנקטעת באמצע. כאן רק מסמנים, והתור עושה את השאר.
"""

import re
import sys
import argparse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import tgbot2 as bot  # noqa: E402


def plain(text: str) -> str:
    return re.sub(r"<[^>]+>", "", text or "")


def do(line: str) -> bool:
    line = line.strip()
    if not line or line.startswith("#"):
        return True
    verb, _, arg = line.lstrip("/").partition(" ")
    verb = verb.lower()
    if verb in ("approve", "אשר"):
        job, nums, _, err = bot.parse_decision(arg)
        if err:
            print(f"✗ {line}\n  {plain(err)}")
            return False
        for n in nums:
            bot.mark_segment(job, n, True)
            bot.write_feedback(job, n, "אושר להעלאה", "")
        print(f"✓ אושרו מ-{job.name}: {', '.join(map(str, nums))}  (התור יעלה בזמן שלו)")
        return True
    if verb in ("reject", "דחה"):
        res = bot.reply_reject(arg)
    elif verb in ("skip", "דלג"):
        res = bot.reply_skip(arg)
    else:
        print(f"✗ לא מכיר '{verb}'. approve / reject / skip")
        return False
    ok = not res.startswith(("שימוש", "לא מצאתי", "חסרים", "אין קטעים"))
    print(("" if ok else "✗ ") + plain(res).replace("\n", "  "))
    return ok


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("commands", nargs="*", help='למשל "approve שם 1,3"')
    ap.add_argument("--file", default="", help="קובץ עם פקודה בכל שורה")
    ap.add_argument("--pending", action="store_true", help="מה ממתין להחלטה")
    args = ap.parse_args()

    if args.pending:
        print(plain(bot.reply_pending()))
        return
    lines = list(args.commands)
    if args.file:
        lines += Path(args.file).read_text(encoding="utf-8").splitlines()
    if not lines:
        ap.print_help()
        return
    bad = sum(0 if do(x) else 1 for x in lines)
    if bad:
        print(f"\n{bad} פקודות נכשלו - שום דבר לא השתנה בהן.")
        sys.exit(1)


if __name__ == "__main__":
    main()
