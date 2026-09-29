"""
budget.py - תקציב הניתוח ועדיפות לדרמה חמה (משימות 57 + 46, 28.9)

שלוש שאלות, בלי מודל ובלי עלות:

1. **כמה באמת הוצאנו החודש?** `month_spend()` מחשב מחדש מהטוקנים שב-
   `usage.jsonl` לפי המחירון הנכון, ולא סוכם את שדה `usd` שנרשם בזמנו.
   עד 28.9 המחירון בקוד היה של סונט 4.5 ($3/$15), וסונט 5 עולה $2/$10.
   לכן הלוג רשם $20.19 כשהחשבונית הייתה $14.79. החישוב מחדש מתקן גם
   את ההיסטוריה.

2. **איך לנתח את הלייב הזה?** `decide()` אחרי התמלול:
     realtime  - דרמה חמה, או סטרימר בעדיפות. תשובה תוך דקות.
     batch     - כל השאר. אותו מודל ואותה איכות בחצי מחיר, תוך שעה בד"כ.
     defer     - אין תקציב. העבודה מחכה (`waiting_budget`) והמנטר מנסה
                 שוב כל סבב. אחרי `defer_max_days` - מוותר ורושם.
   "חם" נקבע מהתמלול עצמו: כמה פעמים בשעה מוזכר יוצר אחר מ-names.json,
   כמה מילות דרמה, ושמות טריגר (איתן ניסים). כויל על 8 הלייבים של 15.9:
   אוהד, עודד, ניק, ליאור וזיגי יצאו חמים; דה כהן, פרטיזן ורונן 15.9
   (שלושת הלייבים שנתנו 0-1 קטעים) יצאו קרים.

3. **האם התקציב עוצר את הערוץ?** `report()` - מה שלירון ביקש לעקוב אחריו.
   אם לייבים נדחו מחוסר תקציב **ובאותה תקופה** היו מקומות ריקים בתור
   ההעלאות, התקציב הוא מה שמגביל, והדוח אומר בכמה להגדיל. אם התור
   תמיד מלא - הגדלה לא תוסיף סרטונים, רק קטעים שלא יעלו.

הגדרות: `watchlist.json` → `budget`.
"""

import json
import re
import sys
from pathlib import Path
from datetime import datetime, timezone, timedelta


def find_root(start: Path) -> Path:
    for c in [start, *start.parents]:
        if (c / "scripts").is_dir():
            return c
    return start


ROOT = find_root(Path(__file__).resolve().parent)
LOG = ROOT / "budget_log.jsonl"

DEFAULTS = {
    "monthly_usd": 30.0,          # המגבלה ב-console.anthropic.com
    "reserve_usd": 3.0,           # לתיאורים, תמניילים ו-retitle אחרי הניתוח
    "realtime_usd_per_hour": 0.20,
    "batch_usd_per_hour": 0.10,
    "after_analysis_usd": 0.10,   # describe + thumb ללייב
    "big_realtime_until": 0.7,    # מעל 70% מהתקציב גם הגדולים עוברים ל-batch
    "pace_factor": 1.5,           # לייב רגיל: עד פי 1.5 מהחלק היומי
    "hot_pace_factor": 3.0,       # דרמה חמה: עד פי 3 - היא עוקפת, אבל לא שורפת את החודש
    "defer_max_days": 3,          # לייב שמחכה יותר מזה - הסיפור שלו כבר מת
    "priority": ["ronengg", "odedsvr", "masterohad"],
    "big_audience": 250_000,
    "hot_others_per_hour": 10,
    "hot_drama_per_hour": 10,
    "triggers": ["איתן ניסים"],
    "trigger_min": 3,
}

# מחיר לטוקן, דולרים למיליון. עודכן 28.9 מול anthropic.com/news/claude-sonnet-5
PRICES = {
    "sonnet": (2.0, 10.0),
    "haiku": (1.0, 5.0),
    "opus": (5.0, 25.0),
}
CACHE_WRITE_5M = 1.25
CACHE_WRITE_1H = 2.0
CACHE_READ = 0.10
BATCH = 0.5

DRAMA_WORDS = (r"דרמה|ריב|פתח עליי|פתח עליו|תביעה|עורך דין|עורכת דין|השמיץ|דיס|"
               r"חרם|מתנצל|להתנצל|שקרן|בוגד|הטריד|פוסט|סטורי|קורבן")


def load_json(path: Path, default):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return default


def config() -> dict:
    cfg = dict(DEFAULTS)
    wl = load_json(ROOT / "watchlist.json", {})
    if isinstance(wl, dict):
        cfg.update(wl.get("budget") or {})
    return cfg


# ------------------------------------------------------------- כסף

def record_cost(r: dict) -> float:
    """עלות רשומה אחת מ-usage.jsonl, מהטוקנים ולא מהשדה usd."""
    model = str(r.get("model", "")).lower()
    price = next((p for k, p in PRICES.items() if k in model), None)
    if not price:
        return float(r.get("usd") or 0)
    pin, pout = price[0] / 1e6, price[1] / 1e6
    cw_mult = CACHE_WRITE_1H if r.get("cache_ttl") == "1h" else CACHE_WRITE_5M
    usd = ((r.get("tokens_in") or 0) * pin
           + (r.get("cache_write") or 0) * pin * cw_mult
           + (r.get("cache_read") or 0) * pin * CACHE_READ
           + (r.get("tokens_out") or 0) * pout)
    return usd * (BATCH if r.get("batch") else 1.0)


def usage_rows():
    path = ROOT / "usage.jsonl"
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            out.append(json.loads(line))
        except Exception:
            pass
    return out


def parse_ts(v):
    try:
        dt = datetime.fromisoformat(str(v))
    except Exception:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def month_start(now: datetime = None) -> datetime:
    now = now or datetime.now(timezone.utc)
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def next_month(now: datetime = None) -> datetime:
    m = month_start(now)
    return (m + timedelta(days=32)).replace(day=1)


def spend_since(since: datetime) -> float:
    total = 0.0
    for r in usage_rows():
        ts = parse_ts(r.get("ts"))
        if ts and ts >= since:
            total += record_cost(r)
    return total


def month_spend(now: datetime = None) -> float:
    return spend_since(month_start(now))


def today_spend(now: datetime = None) -> float:
    now = now or datetime.now(timezone.utc)
    local = now.astimezone()
    start = local.replace(hour=0, minute=0, second=0, microsecond=0)
    return spend_since(start.astimezone(timezone.utc))


# ---------------------------------------------------------- חום

def people():
    return load_json(ROOT / "names.json", {}).get("people", [])


def heat(rows: list, host: str = "", cfg: dict = None) -> dict:
    """
    כמה הלייב "חם": אזכורים של יוצרים אחרים ומילות דרמה, לשעה.
    host = השם בעברית של בעל השידור (לא סופרים אותו).
    """
    cfg = cfg or config()
    text = " ".join(str(r.get("text", "")) for r in rows)
    minutes = max(1.0, (rows[-1]["end"] - rows[0]["start"]) / 60) if rows else 1.0
    hits = {}
    for p in people():
        if p.get("name") == host:
            continue
        # כינויים עבריים של 3+ אותיות. "ניסים" לבד הוא גם שם פרטי נפוץ.
        aliases = [a for a in p.get("aliases", [])
                   if re.search("[א-ת]", a) and len(a) >= 3 and a != "ניסים"]
        n = 0
        for a in set(aliases):
            n += len(re.findall(r"(?<![א-ת])[והבלמשכ]?" + re.escape(a) + r"(?![א-ת])", text))
        if n:
            hits[p["name"]] = n
    others = sum(hits.values())
    drama = len(re.findall(DRAMA_WORDS, text))
    trig = {t: hits.get(t, 0) for t in cfg["triggers"] if hits.get(t, 0) >= cfg["trigger_min"]}
    per_h = lambda n: n / minutes * 60
    hot = (per_h(others) >= cfg["hot_others_per_hour"]
           or per_h(drama) >= cfg["hot_drama_per_hour"]
           or bool(trig))
    top = sorted(hits.items(), key=lambda x: -x[1])[:5]
    return {"hot": hot, "minutes": round(minutes),
            "others_per_h": round(per_h(others), 1),
            "drama_per_h": round(per_h(drama), 1),
            "triggers": list(trig), "top": top}


def is_big(slug: str, cfg: dict = None) -> bool:
    cfg = cfg or config()
    if slug.lower() in [s.lower() for s in cfg["priority"]]:
        return True
    wl = load_json(ROOT / "watchlist.json", {})
    for s in wl.get("streamers", []) if isinstance(wl, dict) else []:
        if str(s.get("slug", "")).lower() == slug.lower():
            if int(s.get("audience") or 0) >= int(cfg["big_audience"]):
                return True
    return False


# ---------------------------------------------------------- החלטה

def decide(slug: str, rows: list, host: str = "", now: datetime = None) -> dict:
    """
    realtime / batch / defer, עם הסבר. לא כותב כלום - רק מחליט.
    """
    cfg = config()
    now = now or datetime.now(timezone.utc)
    h = heat(rows, host, cfg)
    big = is_big(slug, cfg)
    hours = h["minutes"] / 60
    est_rt = hours * cfg["realtime_usd_per_hour"] + cfg["after_analysis_usd"]
    est_b = hours * cfg["batch_usd_per_hour"] + cfg["after_analysis_usd"]

    spent = month_spend(now)
    limit = float(cfg["monthly_usd"])
    left = limit - float(cfg["reserve_usd"]) - spent
    days_left = max(1, (next_month(now) - now).days + 1)
    daily = max(0.0, left) / days_left
    today = today_spend(now)

    base = {"hot": h["hot"], "big": big, "heat": h, "spent": round(spent, 2),
            "left": round(left, 2), "daily": round(daily, 2),
            "est_realtime": round(est_rt, 2), "est_batch": round(est_b, 2)}

    if left < est_b:
        return {**base, "mode": "defer",
                "why": f"נשארו ${max(left, 0):.2f} מתוך ${limit:.0f} החודש, הלייב צפוי ${est_b:.2f}"}

    if h["hot"]:
        mode, why = "realtime", "דרמה חמה - בזמן אמת"
        if left < est_rt:
            mode, why = "batch", "דרמה חמה, אבל התקציב צפוף - batch"
    elif big and spent < limit * float(cfg["big_realtime_until"]):
        mode, why = "realtime", "סטרימר בעדיפות"
    else:
        mode, why = "batch", "לא חם - batch בחצי מחיר"
    est = est_rt if mode == "realtime" else est_b

    # קצב: החלק היומי = מה שנשאר חלקי הימים שנשארו. כך יום עמוס בתחילת
    # החודש מקטין לבד את החלק של הימים הבאים, והחודש לא נגמר ב-10 בו.
    # הלייב הראשון של היום עובר תמיד. השאר מחכים בתור (הדרמה החמה
    # והגדולים ראשונים), ומחר החלק מתחדש.
    pace = daily * float(cfg["hot_pace_factor"] if h["hot"] else cfg["pace_factor"])
    if today > 0 and today + est > pace:
        return {**base, "mode": "defer", "wanted": mode,
                "why": f"החלק היומי (${daily:.2f}) נוצל היום (${today:.2f}). "
                       f"ממתין בתור{' בעדיפות' if h['hot'] or big else ''}"}
    return {**base, "mode": mode, "why": why}


def waiting_priority(job: Path) -> tuple:
    """סדר ההוצאה מ-waiting_budget: חם, אחר כך גדול, אחר כך החדש."""
    st = load_json(job / "state.json", {})
    return (0 if st.get("hot") else 1, 0 if st.get("big") else 1,
            "".join(chr(255 - ord(c)) for c in job.name.rsplit("_", 1)[-1]))


# ------------------------------------------ מעקב: האם התקציב עוצר את הערוץ

def log_event(kind: str, **data) -> None:
    rec = {"ts": datetime.now(timezone.utc).isoformat(timespec="seconds"), "kind": kind, **data}
    try:
        with LOG.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except OSError:
        pass


def events(since: datetime) -> list:
    if not LOG.exists():
        return []
    out = []
    for line in LOG.read_text(encoding="utf-8").splitlines():
        try:
            r = json.loads(line)
        except Exception:
            continue
        ts = parse_ts(r.get("ts"))
        if ts and ts >= since:
            out.append(r)
    return out


_last_idle = None


def note_idle_slot(free_slots: int) -> None:
    """
    נקרא מהתור כשהוא ריק ויש עוד מכסה. פעם בשעה לכל היותר - שעה ריקה
    היא היחידה, לא סבב של 10 דקות.
    """
    global _last_idle
    now = datetime.now(timezone.utc)
    if _last_idle and now - _last_idle < timedelta(minutes=59):
        return
    _last_idle = now
    log_event("idle_slot", free=free_slots)


def weekly_due(now: datetime = None) -> bool:
    """ראשון אחרי 20:00, ולא נשלח בששת הימים האחרונים."""
    now = now or datetime.now(timezone.utc)
    local = now.astimezone()
    if local.weekday() != 6 or local.hour < 20:       # 6 = ראשון
        return False
    return not any(e["kind"] == "weekly_report" for e in events(now - timedelta(days=6)))


def report(now: datetime = None) -> str:
    cfg = config()
    now = now or datetime.now(timezone.utc)
    m0 = month_start(now)
    spent = month_spend(now)
    limit = float(cfg["monthly_usd"])
    elapsed = max(1.0, (now - m0).total_seconds() / 86400)
    days_in = (next_month(now) - m0).days
    forecast = spent / elapsed * days_in
    ev = events(m0)
    deferred = [e for e in ev if e["kind"] == "defer"]
    dropped = [e for e in ev if e["kind"] == "budget_drop"]
    idle = [e for e in ev if e["kind"] == "idle_slot"]
    modes = {}
    for e in ev:
        if e["kind"] == "decide":
            modes[e.get("mode")] = modes.get(e.get("mode"), 0) + 1

    lines = [f"<b>תקציב {now.strftime('%m/%Y')}</b>",
             f"הוצאו ${spent:.2f} מתוך ${limit:.0f}  ·  צפי לסוף החודש ${forecast:.2f}",
             f"היום: ${today_spend(now):.2f}"]
    if modes:
        lines.append("ניתוחים: " + "  ".join(f"{k} {v}" for k, v in modes.items()))
    lines.append(f"נדחו מחוסר תקציב: {len({e.get('job') for e in deferred})} לייבים"
                 f"  ·  ויתרנו לגמרי: {len(dropped)}")
    lines.append(f"שעות שבהן תור ההעלאות היה ריק ונשארה מכסה: {len(idle)}")

    missing = sum(float(e.get("est") or 0) for e in dropped)
    if dropped and idle:
        lines.append(f"\n⚠ <b>התקציב עוצר את הערוץ.</b> {len(dropped)} לייבים לא נותחו, "
                     f"ובאותו חודש התור עמד ריק {len(idle)} שעות. "
                     f"הגדלה של כ-${max(5, round(missing + 2)):.0f} הייתה מכסה אותם.")
    elif forecast > limit and not idle:
        lines.append("\nהצפי מעל המגבלה, אבל התור לא עמד ריק - "
                     "הגדלה תוסיף קטעים, לא סרטונים. לא צריך.")
    elif dropped:
        lines.append("\nלייבים נדחו, אבל התור אף פעם לא עמד ריק - "
                     "התקציב לא עוצר את הקצב כרגע.")
    else:
        lines.append("\nהתקציב לא מגביל כרגע.")
    return "\n".join(lines)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--heat":
        # python scripts\budget.py --heat jobs\odedsvr_2026-09-15
        for j in sys.argv[2:]:
            job = Path(j)
            rows = load_json(job / "audio_transcript.json", [])
            meta = load_json(job / "meta.json", {})
            slug = job.name.rsplit("_", 1)[0]
            d = decide(slug, rows, meta.get("display_name", ""))
            print(f"{job.name}: {d['mode']} - {d['why']}  heat={d['heat']}")
    else:
        print(re.sub(r"</?b>", "", report()))
