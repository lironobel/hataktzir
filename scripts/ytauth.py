"""
ytauth.py - הרשאה חד-פעמית של הערוץ מול YouTube Data API

מריצים את זה פעם אחת, ידנית, מהמחשב שיש בו דפדפן:

    python scripts\\ytauth.py

מה קורה: נפתח דפדפן, מתחברים לחשבון של הערוץ, מאשרים, והסקריפט
שומר `youtube_token.json` בשורש הפרויקט. מרגע זה `upload.py` עובד
לבד ולא צריך דפדפן שוב - הרענון אוטומטי.

דרישות:
    pip install google-api-python-client google-auth-oauthlib google-auth-httplib2

לפני ההרצה צריך `client_secret.json` בשורש הפרויקט.
איך משיגים אותו - ראה YOUTUBE_SETUP.md.

⚠ להתחבר לחשבון שמנהל את "התקציר" (@hataktzir), לא לחשבון אישי.
   הסקריפט מדפיס בסוף את שם הערוץ שחובר - לקרוא ולוודא.

לביטול ההרשאה או החלפת חשבון: למחוק את youtube_token.json ולהריץ שוב.
"""

import sys
import json
from pathlib import Path

SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",     # העלאה
    "https://www.googleapis.com/auth/youtube.force-ssl",  # שינוי סטטוס ותמנייל
]


def find_root(start: Path) -> Path:
    for c in [start, *start.parents]:
        if (c / "scripts").is_dir():
            return c
    return start


ROOT = find_root(Path(__file__).resolve().parent)
TOKEN = ROOT / "youtube_token.json"


def find_secret(root: Path):
    """
    גוגל מורידה את הקובץ בשם ארוך ומכוער
    (`client_secret_49166...apps.googleusercontent.com.json`).
    אין סיבה להכריח שינוי שם ידני, ובטח לא להיכשל בשקט אם שכחו.
    """
    exact = root / "client_secret.json"
    if exact.exists():
        return exact
    matches = sorted(root.glob("client_secret*.json"))
    return matches[0] if matches else None


SECRET = find_secret(ROOT) or (ROOT / "client_secret.json")


def main() -> None:
    try:
        from google_auth_oauthlib.flow import InstalledAppFlow
        from googleapiclient.discovery import build
    except ImportError:
        print("חסרות ספריות. הרץ:")
        print("  pip install google-api-python-client google-auth-oauthlib "
              "google-auth-httplib2")
        sys.exit(1)

    if not SECRET.exists():
        print("לא נמצא קובץ client_secret בשורש הפרויקט.")
        print("ראה YOUTUBE_SETUP.md - שלבים 1 עד 5.")
        sys.exit(1)
    print(f"משתמש ב: {SECRET.name}")

    if TOKEN.exists():
        print(f"כבר קיים {TOKEN.name}.")
        ans = input("לחדש את ההרשאה / להחליף חשבון? (כן/לא) ").strip()
        if ans not in ("כן", "y", "yes"):
            print("בוטל.")
            return

    flow = InstalledAppFlow.from_client_secrets_file(str(SECRET), SCOPES)
    # port=0 בוחר פורט פנוי. אם החומה של ווינדוס חוסמת, להחליף למספר קבוע
    # ולהוסיף אותו כ-redirect URI ב-Google Cloud Console.
    creds = flow.run_local_server(port=0, prompt="consent",
                                  authorization_prompt_message="")

    TOKEN.write_text(creds.to_json(), encoding="utf-8")
    print(f"\nנשמר: {TOKEN}")

    # לאמת לאיזה ערוץ באמת התחברנו. זה הרגע היחיד שבו קל לתפוס
    # שהתחברנו בטעות לחשבון האישי במקום לחשבון המותג.
    try:
        yt = build("youtube", "v3", credentials=creds, cache_discovery=False)
        resp = yt.channels().list(part="snippet", mine=True).execute()
        items = resp.get("items", [])
        if not items:
            print("\n⚠ החשבון הזה לא מנהל שום ערוץ יוטיוב.")
            print("  כנראה התחברת לחשבון הפרטי ולא לחשבון המותג.")
            print("  מחק את youtube_token.json והרץ שוב.")
            return
        ch = items[0]["snippet"]
        print(f"\nחובר לערוץ: {ch.get('title','')}  "
              f"({ch.get('customUrl','אין handle')})")
        print("אם זה לא 'התקציר' - מחק את youtube_token.json והרץ שוב.")
    except Exception as exc:
        print(f"\nההרשאה נשמרה, אבל בדיקת הערוץ נכשלה: {exc}")


if __name__ == "__main__":
    main()
