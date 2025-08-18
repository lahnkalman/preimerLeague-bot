import os, json, time, requests, pathlib

LEAGUE_ID = 39
BASE = "https://v3.football.api-sports.io"

# שמירת מצב בריפו כדי למנוע כפילויות בין ריצות
STATE_DIR = pathlib.Path(".state")
STATE_DIR.mkdir(exist_ok=True)
GOALS_FILE = STATE_DIR / "seen_goals.json"
CORNERS_FILE = STATE_DIR / "seen_corners.json"

def headers():
    key = os.getenv("API_FOOTBALL_KEY")
    if not key:
        raise RuntimeError("Missing API_FOOTBALL_KEY")
    return {"x-apisports-key": key}

def tg_send(text: str):
    bot = os.getenv("TELEGRAM_BOT_TOKEN", "")
    chat = os.getenv("TELEGRAM_CHAT_ID", "")
    if not bot or not chat:
        print("ALERT:", text)   # fallback לקונסול
        return
    url = f"https://api.telegram.org/bot{bot}/sendMessage"
    r = requests.post(url, json={"chat_id": chat, "text": text}, timeout=20)
    if not r.ok:
        print("WARN TG:", r.status_code, r.text[:160])

def get_live_fixtures():
    r = requests.get(f"{BASE}/fixtures", headers=headers(), params={"live":"all","league":LEAGUE_ID}, timeout=20)
    r.raise_for_status()
    return r.json().get("response", [])

def get_events(fid: int):
    r = requests.get(f"{BASE}/fixtures/events", headers=headers(), params={"fixture":fid}, timeout=20)
    r.raise_for_status()
    return r.json().get("response", [])

def get_stats(fid: int):
    r = requests.get(f"{BASE}/fixtures/statistics", headers=headers(), params={"fixture":fid}, timeout=20)
    r.raise_for_status()
    return r.json().get("response", [])

def load_set(p: pathlib.Path):
    if p.exists():
        try:
            return set(json.loads(p.read_text(encoding="utf-8")))
        except Exception:
            return set()
    return set()

def load_dict(p: pathlib.Path):
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}

def save_set(p: pathlib.Path, s: set):
    p.write_text(json.dumps(sorted(list(s))), encoding="utf-8")

def save_dict(p: pathlib.Path, d: dict):
    p.write_text(json.dumps(d), encoding="utf-8")

def format_goal(ev, fx):
    hname = fx["teams"]["home"]["name"]; aname = fx["teams"]["away"]["name"]
    goals = fx.get("goals", {}); h, a = goals.get("home"), goals.get("away")
    player = (ev.get("player") or {}).get("name") or "Unknown"
    detail = ev.get("detail") or "Goal"
    t = ev.get("time",{}); minute = f"{t.get('elapsed')}'" + (f"+{t.get('extra')}" if t.get("extra") else "")
    return f"GOAL! {hname} {h}–{a} {aname}\nScorer: {player} ({detail}) at {minute}"

def format_corner(team_name, hc, ac, hname, aname):
    return f"CORNER! {hname} {hc}–{ac} {aname}\nCorner to {team_name}. Corners: {hname} {hc}–{ac} {aname}"

def run_once():
    fixtures = get_live_fixtures()
    if not fixtures:
        print("[run] no live EPL matches"); return

    seen_goals = load_set(GOALS_FILE)
    corner_state = load_dict(CORNERS_FILE)  # {"<fixture_id>": {"home": int, "away": int}}

    for fx in fixtures:
        fid = fx["fixture"]["id"]
        status = fx["fixture"]["status"]["short"]
        if status in {"NS","TBD","PST"}:
            continue

        # ------- Goals -------
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
            print("events err:", e)

        # ------- Corners -------
        try:
            stats = get_stats(fid)
            hname = fx["teams"]["home"]["name"]; aname = fx["teams"]["away"]["name"]
            hid = fx["teams"]["home"]["id"]; aid = fx["teams"]["away"]["id"]
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
            print("stats err:", e)

    save_set(GOALS_FILE, seen_goals)
    save_dict(CORNERS_FILE, corner_state)
    print("[run] done at", time.strftime("%Y-%m-%d %H:%M:%S"))

if __name__ == "__main__":
    run_once()
