import os, json, time, requests, pathlib, sys

LEAGUE_ID = 39
BASE = "https://v3.football.api-sports.io"

# ==== קונפיגורציה מה-ENV (עם ברירות מחדל טובות) ====
POLL_SECONDS = int(os.getenv("POLL_SECONDS", "20"))         # רק למידע בלוג; השינה מתבצעת ב-workflow
CORNERS_EVERY_N = int(os.getenv("CORNERS_EVERY_N", "1"))    # בדיקת קרנות כל N איטרציות (1=כל פעם)
REQUEST_TIMEOUT = int(os.getenv("REQUEST_TIMEOUT", "20"))   # timeout לקריאות HTTP
SIMULATE = os.getenv("SIMULATE_ALERTS") == "1"

STATE_DIR = pathlib.Path(".state")
STATE_DIR.mkdir(exist_ok=True)
GOALS_FILE = STATE_DIR / "seen_goals.json"
CORNERS_FILE = STATE_DIR / "seen_corners.json"
META_FILE = STATE_DIR / "meta.json"  # נשמור כאן מונה איטרציות

def headers():
    key = os.getenv("API_FOOTBALL_KEY")
    if not key:
        print("[err] Missing API_FOOTBALL_KEY", file=sys.stderr)
        return None
    return {"x-apisports-key": key}

def tg_send(text: str):
    bot = os.getenv("TELEGRAM_BOT_TOKEN", "")
    chat = os.getenv("TELEGRAM_CHAT_ID", "")
    if not bot or not chat:
        print("ALERT:", text)   # fallback לקונסול
        return
    try:
        url = f"https://api.telegram.org/bot{bot}/sendMessage"
        r = requests.post(url, json={"chat_id": chat, "text": text}, timeout=REQUEST_TIMEOUT)
        if not r.ok:
            print("WARN TG:", r.status_code, r.text[:200], file=sys.stderr)
    except Exception as e:
        print("WARN TG EXC:", e, file=sys.stderr)

def _get(url, **params):
    h = headers()
    if not h:
        return []
    r = requests.get(url, headers=h, params=params, timeout=REQUEST_TIMEOUT)
    r.raise_for_status()
    return r.json().get("response", [])

def get_live_fixtures():
    return _get(f"{BASE}/fixtures", live="all", league=LEAGUE_ID)

def get_events(fid: int):
    return _get(f"{BASE}/fixtures/events", fixture=fid)

def get_stats(fid: int):
    return _get(f"{BASE}/fixtures/statistics", fixture=fid)

def load_set(p: pathlib.Path):
    if p.exists():
        try:
            return set(json.loads(p.read_text(encoding="utf-8")))
        except Exception:
            pass
    return set()

def load_dict(p: pathlib.Path):
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}

def save_set(p: pathlib.Path, s: set):
    p.write_text(json.dumps(sorted(list(s))), encoding="utf-8")

def save_dict(p: pathlib.Path, d: dict):
    p.write_text(json.dumps(d), encoding="utf-8")

def load_meta():
    d = load_dict(META_FILE)
    if "loop" not in d:
        d["loop"] = 0
    return d

def save_meta(d: dict):
    save_dict(META_FILE, d)

def format_goal(ev, fx):
    hname = fx["teams"]["home"]["name"]; aname = fx["teams"]["away"]["name"]
    goals = fx.get("goals", {}); h, a = goals.get("home"), goals.get("away")
    player = (ev.get("player") or {}).get("name") or "Unknown"
    detail = ev.get("detail") or "Goal"
    t = ev.get("time",{}); minute = f"{t.get('elapsed')}'" + (f"+{t.get('extra')}" if t.get("extra") else "")
    return f"GOAL! {hname} {h}–{a} {aname}\nScorer: {player} ({detail}) at {minute}"

def format_corner(team_name, hc, ac, hname, aname):
    return f"CORNER! {hname} {hc}–{ac} {aname}\nCorner to {team_name}. Corners: {hname} {hc}–{ac} {aname}"

def simulate_alerts_if_needed():
    if SIMULATE:
        tg_send("✅ TEST: GOAL! Arsenal 1–0 Chelsea (12')")
        tg_send("✅ TEST: CORNER! Arsenal 3–2 Chelsea (to Chelsea)")
        print("[run] simulation alerts sent")
        return True
    return False

def run_once():
    meta = load_meta()
    meta["loop"] = meta.get("loop", 0) + 1
    save_meta(meta)

    # סימולציה לפי דגל
    if simulate_alerts_if_needed():
        return

    try:
        fixtures = get_live_fixtures()
    except Exception as e:
        print("fixtures err:", e, file=sys.stderr)
        return

    if not fixtures:
        print("[run] no live EPL matches")
        return

    seen_goals = load_set(GOALS_FILE)
    corner_state = load_dict(CORNERS_FILE)  # {"<fid>":{"home":int,"away":int}}

    for fx in fixtures:
        fid = fx["fixture"]["id"]
        status = fx["fixture"]["status"]["short"]
        if status in {"NS","TBD","PST"}:
            continue

        # -------- Goals --------
        try:
            for ev in get_events(fid):
                if ev.get("type") != "Goal":
                    continue
                gid = f"{fid}:{(ev.get('team') or {}).get('id')}:{(ev.get('player') or {}).get('id')}:{(ev.get('time') or {}).get('elapsed')}:{(ev.get('time') or {}).get('extra')}:{ev.get('detail')}"
                if gid in seen_goals:
                    continue
                tg_send(format_goal(ev, fx))
                seen_goals.add(gid)
        except Exception as e:
            print("events err:", e, file=sys.stderr)

        # -------- Corners (בדיקה כל N איטרציות) --------
        try:
            if meta["loop"] % max(1, CORNERS_EVERY_N) == 0:
                stats = get_stats(fid)
                hname = fx["teams"]["home"]["name"]; aname = fx["teams"]["away"]["name"]
                hid = fx["teams"]["home"]["id"];     aid = fx["teams"]["away"]["id"]
                hc = ac = None
                for ts in stats:
                    tid = ts["team"]["id"]
                    for st in ts.get("statistics", []):
                        if (st.get("type") or "").lower() in {"corner kicks","corners"}:
                            if tid == hid: hc = st.get("value") or 0
                            if tid == aid: ac = st.get("value") or 0
                if hc is None or ac is None:
                    continue
                prev = corner_state.get(str(fid), {"home":0,"away":0})
                if hc > prev.get("home",0):
                    tg_send(format_corner(hname, hc, ac, hname, aname))
                if ac > prev.get("away",0):
                    tg_send(format_corner(aname, hc, ac, hname, aname))
                corner_state[str(fid)] = {"home": hc, "away": ac}
        except Exception as e:
            print("stats err:", e, file=sys.stderr)

    save_set(GOALS_FILE, seen_goals)
    save_dict(CORNERS_FILE, corner_state)
    print(f"[run] done at {time.strftime('%Y-%m-%d %H:%M:%S')} (loop={meta['loop']}, poll={POLL_SECONDS}s)")
    

if __name__ == "__main__":
    try:
        run_once()
    except Exception as e:
        print("[fatal]", e, file=sys.stderr)
