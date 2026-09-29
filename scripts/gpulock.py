"""
gpulock.py - תור ל-GPU: תמלול אחד בכל רגע

יש כרטיס אחד עם 4GB. שני תמלולים במקביל נלחמים על הזיכרון ועל
המעבד, וביחד איטיים יותר משניהם ברצף. כל צינור שמגיע לשלב התמלול
תופס את המנעול; מי שמגיע כשהוא תפוס - ממתין בתור.

המנעול הוא קובץ transcribe.lock בשורש הפרויקט, עם ה-PID של המחזיק.
אם התהליך המחזיק כבר לא קיים (קריסה, ריסטרט) - המנעול נשבר אוטומטית,
אז אין מצב של תור תקוע לנצח.

שימוש:
    from gpulock import gpu_lock
    with gpu_lock(on_wait=lambda mins: print(f"בתור {mins} דק'")):
        ...תמלול...

בדיקה ידנית:
    python scripts\\gpulock.py           מי מחזיק עכשיו
    python scripts\\gpulock.py --clear   לשחרר בכוח
"""

import os
import sys
import json
import time
import subprocess
from pathlib import Path
from contextlib import contextmanager
from datetime import datetime, timezone


def find_root(start: Path) -> Path:
    for c in [start, *start.parents]:
        if (c / "scripts").is_dir():
            return c
    return start


ROOT = find_root(Path(__file__).resolve().parent)
LOCK = ROOT / "transcribe.lock"

STALE_HOURS = 6      # תמלול לא לוקח יותר. מעבר לזה המנעול נחשב נטוש
POLL_SECONDS = 90


def pid_alive(pid: int) -> bool:
    """האם התהליך קיים. עובד גם בווינדוס וגם בלינוקס."""
    if pid <= 0:
        return False
    if os.name == "nt":
        try:
            r = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"],
                               capture_output=True, text=True, timeout=15)
            return str(pid) in (r.stdout or "")
        except Exception:
            return True   # ספק - לא שוברים מנעול של תהליך חי
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def read_lock() -> dict:
    try:
        return json.loads(LOCK.read_text(encoding="utf-8"))
    except Exception:
        return {}


def holder() -> dict:
    """מי מחזיק את המנעול עכשיו. {} אם פנוי או נטוש."""
    if not LOCK.exists():
        return {}
    info = read_lock()
    pid = int(info.get("pid", 0))
    started = info.get("started", "")
    stale = True
    try:
        then = datetime.fromisoformat(started)
        if then.tzinfo is None:
            then = then.replace(tzinfo=timezone.utc)
        stale = (datetime.now(timezone.utc) - then).total_seconds() > STALE_HOURS * 3600
    except Exception:
        pass
    if stale or not pid_alive(pid):
        return {}
    return info


def _try_acquire(label: str) -> bool:
    """ניסיון אטומי: יצירת הקובץ נכשלת אם הוא קיים."""
    if holder():
        return False
    LOCK.unlink(missing_ok=True)   # מנעול נטוש
    try:
        fd = os.open(str(LOCK), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        return False
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump({"pid": os.getpid(), "label": label,
                   "started": datetime.now(timezone.utc).isoformat(timespec="seconds")}, f)
    return True


@contextmanager
def gpu_lock(label: str = "", on_wait=None):
    waited = 0.0
    while not _try_acquire(label):
        if on_wait:
            try:
                on_wait(waited / 60, holder())
            except Exception:
                pass
        time.sleep(POLL_SECONDS)
        waited += POLL_SECONDS
    try:
        yield
    finally:
        # משחררים רק אם המנעול עדיין שלנו
        if read_lock().get("pid") == os.getpid():
            LOCK.unlink(missing_ok=True)


def main() -> None:
    if "--clear" in sys.argv:
        LOCK.unlink(missing_ok=True)
        print("המנעול שוחרר.")
        return
    h = holder()
    if not h:
        print("ה-GPU פנוי.")
    else:
        print(f"תפוס על ידי {h.get('label') or '?'} (PID {h.get('pid')}) מאז {h.get('started')}")


if __name__ == "__main__":
    main()
