"""
tg.py - שכבת התקשורת עם טלגרם

משמש את שאר הסקריפטים לשליחת התראות, ומריץ גם פעולות משלו.

הרצה ראשונה, אחרי ששלחת הודעה לבוט:
    python scripts\\tg.py setup

בדיקה:
    python scripts\\tg.py test
    python scripts\\tg.py send "הודעת בדיקה"

שימוש מתוך סקריפטים אחרים:
    from tg import notify, send_video
    notify("רונן עלה ללייב")
"""

import sys
import json
import time
import argparse
from pathlib import Path

try:
    import requests
except ImportError:
    raise SystemExit("חסר requests. הרץ:  pip install requests")


def find_root(start: Path) -> Path:
    for c in [start, *start.parents]:
        if (c / "scripts").is_dir():
            return c
    return start


ROOT = find_root(Path(__file__).resolve().parent)
CONFIG_PATH = ROOT / "telegram_config.json"
API = "https://api.telegram.org/bot{token}/{method}"


def load_config() -> dict:
    if not CONFIG_PATH.exists():
        return {}
    try:
        return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_config(cfg: dict) -> None:
    CONFIG_PATH.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")


def call(method: str, **params):
    cfg = load_config()
    token = cfg.get("token", "")
    if not token:
        return None
    try:
        r = requests.post(API.format(token=token, method=method), data=params, timeout=25)
        return r.json()
    except Exception as exc:
        print(f"טלגרם: {exc}", file=sys.stderr)
        return None


IMPORTANT_MARK = "🔴 "

# ---------------------------------------------------- עצירת חירום (29.9)
# /pause בטלגרם יוצר את הקובץ, /resume מוחק. כל רכיב בודק אותו:
#   monitor10  - לא בודק לייבים, לא מפעיל, לא מנסה שוב (אבל כן כותב
#                monitor_state.json, כדי שה-watchdog לא יצעק "המנטר קפא")
#   run10      - לא מתחיל עבודה (גם /retry ידני)
#   uploadq / tgbot2 - לא מעלים ליוטיוב
# קובץ ולא משתנה בזיכרון: שורד הפעלה מחדש של המחשב, ורואים אותו מכל תהליך.
PAUSE_PATH = ROOT / "PAUSED.json"


def paused() -> dict:
    """{} אם הכל רץ. אחרת {"since": ..., "why": ...}."""
    if not PAUSE_PATH.exists():
        return {}
    try:
        data = json.loads(PAUSE_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) and data else {"since": "?"}
    except Exception:
        return {"since": "?"}          # קובץ פגום עדיין אומר "עצור"


def set_paused(on: bool, why: str = "") -> None:
    if on:
        from datetime import datetime
        PAUSE_PATH.write_text(json.dumps(
            {"since": datetime.now().strftime("%d/%m %H:%M"), "why": why},
            ensure_ascii=False), encoding="utf-8")
    else:
        PAUSE_PATH.unlink(missing_ok=True)


def notify(text: str, silent: bool = None, buttons: list = None,
           important: bool = False) -> bool:
    """
    שולח הודעה. buttons היא רשימה של רשימות:
        [[{"text": "אשר", "callback_data": "ok:1"}], [{"text": "דחה", "callback_data": "no:1"}]]

    צליל ורטט (29.9, בקשת לירון): **ברירת המחדל שקטה.** הודעה מגיעה לצ'אט
    בלי צליל ובלי רטט - עלה לשדר, סיים, התקדמות, העלאה שהצליחה.
    important=True = משהו שמחכה לך או נכשל ודורש תגובה: עם צליל ורטט,
    ו-🔴 בתחילת ההודעה כדי שיהיה קל לזהות אותה ברשימה.
    silent=False מפורש (קוד ישן) נחשב כ-important בלי הסימון.
    """
    cfg = load_config()
    if not cfg.get("enabled", True):
        return False
    chat_id = cfg.get("chat_id", "")
    if not chat_id:
        print("אין chat_id. הרץ:  python scripts\\tg.py setup", file=sys.stderr)
        return False

    loud = important or silent is False
    if important and not text.startswith(IMPORTANT_MARK):
        text = IMPORTANT_MARK + text
    params = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_notification": not loud,
    }
    if buttons:
        params["reply_markup"] = json.dumps({"inline_keyboard": buttons})

    res = call("sendMessage", **params)
    return bool(res and res.get("ok"))


def send_photo(path, caption: str = "", buttons: list = None, silent: bool = True) -> bool:
    """שולח תמונה - בשביל התמנייל, כדי לראות אותו לפני שהוא ביוטיוב."""
    cfg = load_config()
    chat_id, token = cfg.get("chat_id", ""), cfg.get("token", "")
    p = Path(path)
    if not (chat_id and token and p.exists()):
        return False
    data = {"chat_id": chat_id, "caption": caption[:1000], "parse_mode": "HTML",
            "disable_notification": "true" if silent else "false"}
    if buttons:
        data["reply_markup"] = json.dumps({"inline_keyboard": buttons})
    try:
        with p.open("rb") as f:
            r = requests.post(API.format(token=token, method="sendPhoto"),
                              data=data, files={"photo": f}, timeout=120)
        res = r.json()
        if not res.get("ok"):
            print(f"טלגרם דחה את התמונה: {res.get('description','')}", file=sys.stderr)
        return bool(res.get("ok"))
    except Exception as exc:
        print(f"שליחת תמונה נכשלה: {exc}", file=sys.stderr)
        return False


def send_video(path, caption: str = "", buttons: list = None,
               silent: bool = True, reply_to: int = 0) -> bool:
    """
    שולח וידאו עם כפתורים אופציונליים מתחתיו.
    טלגרם מגבילה בוט ל-50MB, ולכן קליפ שלם כמעט אף פעם לא נכנס -
    scripts\\preview.py מכין גרסה שכן. אם בכל זאת הגיע לכאן קובץ
    גדול מדי, שולחים את הטקסט לבד במקום להיכשל בשקט.
    """
    cfg = load_config()
    chat_id = cfg.get("chat_id", "")
    token = cfg.get("token", "")
    if not (chat_id and token):
        return False

    p = Path(path)
    if not p.exists():
        print(f"לא נמצא: {p}", file=sys.stderr)
        return False

    size_mb = p.stat().st_size / 1e6
    if size_mb > 49:
        notify(f"{caption}\n\n<i>הקובץ {size_mb:.0f}MB, גדול מדי לשליחה בטלגרם.</i>\n"
               f"<code>{p}</code>", buttons=buttons)
        return False

    data = {"chat_id": chat_id, "caption": caption[:1000], "parse_mode": "HTML",
            "supports_streaming": "true", "disable_notification": "true" if silent else "false"}
    if buttons:
        data["reply_markup"] = json.dumps({"inline_keyboard": buttons})
    if reply_to:
        data["reply_to_message_id"] = reply_to

    try:
        with p.open("rb") as f:
            r = requests.post(
                API.format(token=token, method="sendVideo"),
                data=data, files={"video": f}, timeout=600,
            )
        res = r.json()
        if not res.get("ok"):
            print(f"טלגרם דחה את הווידאו: {res.get('description','')}", file=sys.stderr)
        return res.get("ok", False)
    except Exception as exc:
        print(f"שליחת וידאו נכשלה: {exc}", file=sys.stderr)
        return False


def cmd_setup() -> None:
    """מאתר את ה-chat_id מתוך ההודעה ששלחת לבוט."""
    cfg = load_config()
    if not cfg.get("token"):
        print("אין טוקן ב-telegram_config.json")
        sys.exit(1)

    print("מחפש הודעות שנשלחו לבוט...")
    res = call("getUpdates")
    if not res or not res.get("ok"):
        print("לא הצלחתי לפנות לטלגרם. בדוק שהטוקן נכון.")
        sys.exit(1)

    updates = res.get("result", [])
    if not updates:
        print()
        print("לא נמצאו הודעות.")
        print("פתח את הבוט בטלגרם, לחץ Start, שלח לו הודעה כלשהי,")
        print("ואז הרץ שוב:  python scripts\\tg.py setup")
        sys.exit(1)

    chats = {}
    for u in updates:
        msg = u.get("message") or u.get("edited_message") or {}
        chat = msg.get("chat") or {}
        if chat.get("id"):
            name = chat.get("first_name") or chat.get("title") or chat.get("username") or "?"
            chats[str(chat["id"])] = name

    if len(chats) == 1:
        chat_id, name = next(iter(chats.items()))
    else:
        print("נמצאו כמה צ'אטים:")
        for cid, name in chats.items():
            print(f"  {cid}  {name}")
        chat_id = input("איזה chat_id לשמור? ").strip()
        name = chats.get(chat_id, "?")

    cfg["chat_id"] = chat_id
    save_config(cfg)
    print(f"נשמר chat_id={chat_id}  ({name})")

    if notify("<b>הבוט מחובר</b>\nמכאן והלאה תקבל כאן התראות על הלייבים והקטעים."):
        print("נשלחה הודעת אישור לטלגרם.")


def cmd_test() -> None:
    cfg = load_config()
    print(f"טוקן:   {'קיים' if cfg.get('token') else 'חסר'}")
    print(f"chat_id: {cfg.get('chat_id') or 'חסר'}")
    me = call("getMe")
    if me and me.get("ok"):
        u = me["result"]
        print(f"בוט:     @{u.get('username')}  ({u.get('first_name')})")
    else:
        print("בוט:     לא נגיש")
        return
    if cfg.get("chat_id"):
        ok = notify("בדיקה. אם אתה רואה את זה - הכל עובד.")
        print("שליחה:  " + ("הצליחה" if ok else "נכשלה"))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("command", choices=["setup", "test", "send"])
    ap.add_argument("text", nargs="?", default="")
    args = ap.parse_args()

    if args.command == "setup":
        cmd_setup()
    elif args.command == "test":
        cmd_test()
    elif args.command == "send":
        if not args.text:
            print("צריך טקסט")
            sys.exit(1)
        print("נשלח" if notify(args.text) else "נכשל")


if __name__ == "__main__":
    main()
