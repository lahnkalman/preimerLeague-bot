import os, json, time, pathlib, sys
import requests
from requests.adapters import HTTPAdapter, Retry

# ---------- קונפיג ----------
LEAGUE_ID = int(os.getenv("LEAGUE_ID", "39"))
BASE = "https://v3.football.api-sports.io"

POLL_SECONDS      = int(os.getenv("POLL_SECONDS", "20"))
CORNERS_EVERY_N   = int(os.getenv("CORNERS_EVERY_N", "2"))
REQUEST_TIMEOUT   = int(os.getenv("REQUEST_TIMEOUT", "20"))
SIMULATE          = os.getenv("SIMULATE_ALERTS") == "1"
DIAG              = os.getenv("DIAG", "0") == "1"
GOAL_ALERTS       = os.getenv("GOAL_ALERTS", "1") == "1"
CORNER_ALERTS     = os.getenv("CORNER_ALERTS", "1") == "1"
PARSE_MODE        = os.getenv("PARSE_MODE", "")  # "Markdown"/"HTML"/""
TELEGRAM_SILENT   = os.getenv("TELEGRAM_SILENT", "0") == "1"

STATE_DIR = pathlib.Path(".state")
STATE_DIR.mkdir(exist_ok=True)
SEEN_GOALS_FILE   = STATE_DIR / "seen_goals.json"     # set of goal event ids
CORNERS_FILE      = STATE_DIR / "seen_corners.json"   # {fid:{home,away}}
SCORE_STATE_FILE  = STATE_DIR / "score_state.json"    # {fid:{home,away}}
META_FILE         = STATE_DIR / "meta.json"           # {"loop":int}

# ---------- HTTP Session עם ריטריי ----------
_session = requests.Session()
retries = Retry(
    total=4, backoff_factor=0.5,
    status_forcelist=[429, 500, 502, 503, 504],
    allowed_methods=["GET", "POST"]
)
_session.mount("https://", HTTPAdapter(max_retries=retries))

def _headers():
    key = os.getenv("API_FOOTBALL_KEY")
    if not key:
        print("[err] Missing API_FOOTBALL_KEY", file=sys.stderr)
        return None
    return {"x-apisports-key": key}

def _get(url, **params):
    h = _headers()
    if not h:
        return []
    try:
        r = _session.get(url, headers=h, params=params, timeout=REQUEST_TIMEOUT)
        if r.status_code == 429:
            # אם יש RateLimit header – נכבד אותו; אחרת נשן 5ש'
            wait = int(r.headers.get("Retry-After", "5"))
            print(f"[rate] 429; sleeping {wait}s", file=sys.stderr)
            time.sleep(wait)
            r = _session.get(url, headers=h, params=params, timeout=REQUEST_TIMEOUT)
        r.raise_for_status()
        return r.json().get("response", [])
    except Exception as e:
        print(f"[http] GET {url} err: {e}", file=sys.stderr)
        return []

# ---------- Telegram ----------
def tg_send(text: str):
    bot = os.getenv("TELEGRAM_BOT_TOKEN", "")
    chat = os.getenv("TELEGRAM_CHAT_ID", "")
    if not bot or not chat:
        print("ALERT:", text)
        return
    try:
        payload = {"chat_id": chat, "text": text}
        if PARSE_MODE:
            payload["parse_mode"] = PARSE_MODE
        if TELEGRAM_SILENT:
            payload["disable_notification"] = True
        r = _session.post(f"https://api.telegram.org/bot{bot}/sendMessage",
                          json=payload, timeout=REQUEST_TIMEOUT)
        if not r.ok:
            print("WARN TG:", r.status_code, r.text[:200], file=sys.stderr)
    except Exception as e:
        print("WARN TG EXC:", e, file=sys.stderr)

# ---------- IO helpers ----------
def _load_set(p: pathlib.Path):
    try:
        return set(json.loads(p.read_text(encoding="utf-8"))) if p.exists() else set()
    except Exception:
        return set()

def _load_dict(p: pathlib.Path):
    try:
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
    except Exception:
        return {}

def _save_set(p: pathlib.Path, s: set):
    p.write_text(json.dumps(sorted(list(s))), encoding="utf-8")

def _save_dict(p: pathlib.Path, d: dict):
    p.write_text(json.dumps(d), encoding="utf-8")

def _load_meta():
    d = _load_dict(META_FILE)
    d["loop"] = d.get("loop", 0) + 1
    _save_dict(META_FILE, d)
    return d

# ---------- Format helpers ----------
def _fmt_goal(ev, fx):
    hname = fx["teams"]["home"]["name"]; aname = fx["teams"]["away"]["name"]
    goals = fx.get("goals", {}); h, a = goals.get("home"), goals.get("away")
    player = (ev.get("player") or {}).get("name") or "Unknown"
    detail = ev.get("detail") or ev.get("type") or "Goal"
    t = ev.get("time",{})
    minute = f"{t.get('elapsed')}'" + (f"+{t.get('extra')}" if t.get("extra") else "")
    return f"GOAL! {hname} {h}–{a} {aname}\nScorer: {player} ({detail}) at {minute}"

def _fmt_goal_generic(fx):
    hname = fx["teams"]["home"]["name"]; aname = fx["teams"]["away"]["name"]
    h = fx.get("goals",{}).get("home"); a = fx.get("goals",{}).get("away")
    return f"GOAL! {hname} {h}–{a} {aname}"

def _fmt_corner(team_name, hc, ac, hname, aname):
    return f"CORNER! {hname} {hc}–{ac} {aname}\nCorner to {team_name}. Corners: {hname} {hc}–{ac} {aname}"

# ---------- API wrappers ----------
def get_live_fixtures():
    return _get(f"{BASE}/fixtures", live="all", league=LEAGUE_ID)  # EPL בלבד

def get_events(fid: int):
    return _get(f"{BASE}/fixtures/events", fixture=fid)

def get_stats(fid: int):
    return _get(f"{BASE}/fixtures/statistics", fixture=fid)

# ---------- Simulation ----------
def simulate_alerts_if_needed():
    if SIMULATE:
        tg_send("✅ TEST: GOAL! Arsenal 1–0 Chelsea (12')")
        tg_send("✅ TEST: CORNER! Arsenal 3–2 Chelsea (to Chelsea)")
        print("[run] simulation alerts sent")
        return True
    return False

# ---------- Main run ----------
def run_once():
    meta = _load_meta()
    if simulate_alerts_if_needed():
        return

    fixtures = get_live_fixtures()
    if DIAG:
        print(f"[diag] live fixtures: {len(fixtures)}")
        for fx in fixtures:
            fid = fx["fixture"]["id"]
            h = fx["teams"]["home"]["name"]; a = fx["teams"]["away"]["name"]
            st = fx["fixture"]["status"]["short"]; lg = fx["league"]["id"]
            print(f"[diag] fid={fid} lg={lg} {h} vs {a} status={st}")

    if not fixtures:
        print("[run] no live EPL matches")
        return

    seen_goals   = _load_set(SEEN_GOALS_FILE)
    corners_state= _load_dict(CORNERS_FILE)   # {fid:{home,away}}
    score_state  = _load_dict(SCORE_STATE_FILE) # {fid:{home,away}}

    for fx in fixtures:
        fid = str(fx["fixture"]["id"])
        status = fx["fixture"]["status"]["short"]  # 1H/2H/HT/FT/NS...
        if status in {"NS", "TBD", "PST", "FT"}:
            continue

        # ----- GOALS -----
        if GOAL_ALERTS:
            # 1) בדיקה מהירה אם התוצאה השתנתה (אמין ומהיר)
            cur_h = fx.get("goals",{}).get("home")
            cur_a = fx.get("goals",{}).get("away")
            prev = score_state.get(fid, {"home":cur_h, "away":cur_a})
            if (cur_h, cur_a) != (prev.get("home"), prev.get("away")):
                # תוצאה השתנתה → ננסה להביא אירוע כדי לדעת כובש; אם לא יימצא – נשלח גנרי
                events = get_events(int(fid)) or []
                goal_ev = None
                for ev in events:
                    if ev.get("type") == "Goal":
                        # מפתח ייחודי לאירוע
                        gid = f"{fid}:{(ev.get('team') or {}).get('id')}:{(ev.get('player') or {}).get('id')}:{(ev.get('time') or {}).get('elapsed')}:{(ev.get('time') or {}).get('extra')}:{ev.get('detail')}"
                        if gid not in seen_goals:
                            goal_ev = ev
                            seen_goals.add(gid)
                            break
                if goal_ev:
                    tg_send(_fmt_goal(goal_ev, fx))
                else:
                    tg_send(_fmt_goal_generic(fx))
                score_state[fid] = {"home": cur_h, "away": cur_a}

            # 2) גיבוי: אם יש events נוספים שלא נשלחו (מקרה נדיר)
            events = get_events(int(fid)) or []
            for ev in events:
                if ev.get("type") != "Goal":
                    continue
                gid = f"{fid}:{(ev.get('team') or {}).get('id')}:{(ev.get('player') or {}).get('id')}:{(ev.get('time') or {}).get('elapsed')}:{(ev.get('time') or {}).get('extra')}:{ev.get('detail')}"
                if gid in seen_goals:
                    continue
                tg_send(_fmt_goal(ev, fx))
                seen_goals.add(gid)

        # ----- CORNERS -----
        if CORNER_ALERTS and meta["loop"] % max(1, CORNERS_EVERY_N) == 0:
            stats = get_stats(int(fid))
            hname = fx["teams"]["home"]["name"]; aname = fx["teams"]["away"]["name"]
            hid = fx["teams"]["home"]["id"]; aid = fx["teams"]["away"]["id"]
            hc = ac = None
            for ts in stats:
                tid = ts["team"]["id"]
                for st in ts.get("statistics", []):
                    if (st.get("type") or "").lower() in {"corner kicks", "corners"}:
                        if tid == hid: hc = st.get("value") or 0
                        if tid == aid: ac = st.get("value") or 0
            if hc is None or ac is None:
                continue
            prev = corners_state.get(fid, {"home":0, "away":0})
            if hc > prev.get("home",0):
                tg_send(_fmt_corner(hname, hc, ac, hname, aname))
            if ac > prev.get("away",0):
                tg_send(_fmt_corner(aname, hc, ac, hname, aname))
            corners_state[fid] = {"home": hc, "away": ac}

    _save_set(SEEN_GOALS_FILE, seen_goals)
    _save_dict(CORNERS_FILE, corners_state)
    _save_dict(SCORE_STATE_FILE, score_state)
    print(f"[run] done at {time.strftime('%Y-%m-%d %H:%M:%S')} (poll={POLL_SECONDS}s)")

if __name__ == "__main__":
    try:
        run_once()
    except Exception as e:
        print("[fatal]", e, file=sys.stderr)
