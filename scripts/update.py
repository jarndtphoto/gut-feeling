#!/usr/bin/env python3
"""Gut Feeling data job.

Pulls this week's NFL games and writes data.json for the app:
  - win % and betting lines (The Odds API, ESPN as backup)
  - records, recent form, rest and travel (ESPN schedule and scores)
  - likely starting QBs and injuries (ESPN game summaries)
  - kickoff weather for outdoor stadiums (Open-Meteo)
  - team ratings and future projections for every remaining week
  - short + / - notes written by simple rules

Run kinds (RUN_KIND env var):
  daily / manual  -> always do a full update
  gameday         -> only update when a game kicks off in 35-85 minutes
                     (right after inactives are posted), otherwise exit
"""
import json
import math
import os
import statistics
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "data.json")
STATE = os.path.join(ROOT, "data", "state.json")
ESPN = "https://site.api.espn.com/apis/site/v2/sports/football/nfl"
ODDS = "https://api.the-odds-api.com/v4/sports/americanfootball_nfl/odds"
UA = {"User-Agent": "gut-feeling/1.0 (+https://github.com/jarndtphoto/gut-feeling)"}
HFA = 1.5          # home-field edge in points
LOGIT = 14.0       # 7-point favorite ~= 76%
NOW = datetime.now(timezone.utc)

# ---------- Teams ----------
TEAMS = {  # app abbreviation: (short name, full name)
    "ARI": ("Cardinals", "Arizona Cardinals"), "ATL": ("Falcons", "Atlanta Falcons"),
    "BAL": ("Ravens", "Baltimore Ravens"), "BUF": ("Bills", "Buffalo Bills"),
    "CAR": ("Panthers", "Carolina Panthers"), "CHI": ("Bears", "Chicago Bears"),
    "CIN": ("Bengals", "Cincinnati Bengals"), "CLE": ("Browns", "Cleveland Browns"),
    "DAL": ("Cowboys", "Dallas Cowboys"), "DEN": ("Broncos", "Denver Broncos"),
    "DET": ("Lions", "Detroit Lions"), "GB": ("Packers", "Green Bay Packers"),
    "HOU": ("Texans", "Houston Texans"), "IND": ("Colts", "Indianapolis Colts"),
    "JAX": ("Jaguars", "Jacksonville Jaguars"), "KC": ("Chiefs", "Kansas City Chiefs"),
    "LA": ("Rams", "Los Angeles Rams"), "LAC": ("Chargers", "Los Angeles Chargers"),
    "LV": ("Raiders", "Las Vegas Raiders"), "MIA": ("Dolphins", "Miami Dolphins"),
    "MIN": ("Vikings", "Minnesota Vikings"), "NE": ("Patriots", "New England Patriots"),
    "NO": ("Saints", "New Orleans Saints"), "NYG": ("Giants", "New York Giants"),
    "NYJ": ("Jets", "New York Jets"), "PHI": ("Eagles", "Philadelphia Eagles"),
    "PIT": ("Steelers", "Pittsburgh Steelers"), "SEA": ("Seahawks", "Seattle Seahawks"),
    "SF": ("49ers", "San Francisco 49ers"), "TB": ("Buccaneers", "Tampa Bay Buccaneers"),
    "TEN": ("Titans", "Tennessee Titans"), "WAS": ("Commanders", "Washington Commanders"),
}
FULL2AB = {v[1]: k for k, v in TEAMS.items()}
FULL2AB.update({"Washington Football Team": "WAS", "Washington Redskins": "WAS"})
ESPN2AB = {"WSH": "WAS", "LAR": "LA", "JAC": "JAX"}

DIVISIONS = [
    ["BUF", "MIA", "NE", "NYJ"], ["DAL", "NYG", "PHI", "WAS"], ["BAL", "CIN", "CLE", "PIT"],
    ["CHI", "DET", "GB", "MIN"], ["HOU", "IND", "JAX", "TEN"], ["ATL", "CAR", "NO", "TB"],
    ["DEN", "KC", "LV", "LAC"], ["ARI", "LA", "SF", "SEA"],
]
DIV = {t: i for i, d in enumerate(DIVISIONS) for t in d}
WEST = {"SEA", "SF", "LA", "LAC", "LV", "ARI"}
EAST = {"BUF", "MIA", "NE", "NYJ", "NYG", "PHI", "WAS", "BAL", "PIT", "CLE", "CIN",
        "DET", "ATL", "CAR", "TB", "JAX", "IND"}

# Home stadiums: (lat, lon, roof)  roof = open | dome | retractable | covered
STADIUMS = {
    "ARI": (33.5276, -112.2626, "retractable"), "ATL": (33.7554, -84.4008, "retractable"),
    "BAL": (39.2780, -76.6227, "open"), "BUF": (42.7738, -78.7870, "open"),
    "CAR": (35.2258, -80.8528, "open"), "CHI": (41.8623, -87.6167, "open"),
    "CIN": (39.0955, -84.5161, "open"), "CLE": (41.5061, -81.6995, "open"),
    "DAL": (32.7473, -97.0945, "retractable"), "DEN": (39.7439, -105.0201, "open"),
    "DET": (42.3400, -83.0456, "dome"), "GB": (44.5013, -88.0622, "open"),
    "HOU": (29.6847, -95.4107, "retractable"), "IND": (39.7601, -86.1639, "retractable"),
    "JAX": (30.3239, -81.6373, "open"), "KC": (39.0489, -94.4839, "open"),
    "LA": (33.9535, -118.3392, "covered"), "LAC": (33.9535, -118.3392, "covered"),
    "LV": (36.0909, -115.1833, "dome"), "MIA": (25.9580, -80.2389, "open"),
    "MIN": (44.9737, -93.2581, "dome"), "NE": (42.0909, -71.2643, "open"),
    "NO": (29.9511, -90.0812, "dome"), "NYG": (40.8135, -74.0745, "open"),
    "NYJ": (40.8135, -74.0745, "open"), "PHI": (39.9008, -75.1675, "open"),
    "PIT": (40.4468, -80.0158, "open"), "SEA": (47.5952, -122.3316, "open"),
    "SF": (37.4030, -121.9700, "open"), "TB": (27.9759, -82.5033, "open"),
    "TEN": (36.1665, -86.7713, "open"), "WAS": (38.9077, -76.8645, "open"),
}
# Neutral / international sites, matched by venue or city name
INTL = [
    ("tottenham", "London", 51.6043, -0.0664, "open"),
    ("wembley", "London", 51.5560, -0.2796, "open"),
    ("allianz", "Munich", 48.2188, 11.6247, "open"),
    ("deutsche bank park", "Frankfurt", 50.0686, 8.6455, "open"),
    ("olympiastadion", "Berlin", 52.5147, 13.2395, "open"),
    ("bernab", "Madrid", 40.4531, -3.6883, "retractable"),
    ("maracan", "Rio de Janeiro", -22.9121, -43.2302, "open"),
    ("corinthians", "São Paulo", -23.5453, -46.4742, "open"),
    ("melbourne", "Melbourne", -37.8200, 144.9834, "open"),
    ("mcg", "Melbourne", -37.8200, 144.9834, "open"),
    ("croke", "Dublin", 53.3607, -6.2512, "open"),
    ("azteca", "Mexico City", 19.3029, -99.1505, "open"),
    ("banorte", "Mexico City", 19.3029, -99.1505, "open"),
    ("stade de france", "Paris", 48.9245, 2.3602, "open"),
    ("london", "London", 51.6043, -0.0664, "open"),
]
ROOF_TEXT = {"dome": "Dome", "retractable": "Retractable roof", "covered": "Covered stadium"}


# ---------- Helpers ----------
def get(url, tries=3):
    last = None
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=25) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            last = e
            if 400 <= e.code < 500 and e.code != 429:
                break
        except Exception as e:  # noqa: BLE001
            last = e
        time.sleep(2 * (i + 1))
    raise last


def ab(espn_abbr):
    a = (espn_abbr or "").upper()
    return ESPN2AB.get(a, a)


def ptime(s):
    s = (s or "").replace("Z", "+00:00")
    try:
        t = datetime.fromisoformat(s)
    except ValueError:
        t = datetime.strptime(s[:16], "%Y-%m-%dT%H:%M")
    if t.tzinfo is None:
        t = t.replace(tzinfo=timezone.utc)
    return t


def num(x):
    try:
        return int(float(x))
    except (TypeError, ValueError):
        return None


def implied(ml):
    ml = float(ml)
    return 100 / (ml + 100) if ml > 0 else -ml / (-ml + 100)


def ml_prob(ml_a, ml_b):
    pa, pb = implied(ml_a), implied(ml_b)
    return pa / (pa + pb)


def med(v):
    return statistics.median(v) if v else None


def fmt_spread(x):
    if x is None:
        return None
    if abs(x) < 0.01:
        return "PK"
    s = f"{abs(x):g}"
    return ("–" if x < 0 else "+") + s


def fmt_ml(x):
    if x is None:
        return None
    x = int(round(x))
    return ("+" if x > 0 else "–") + str(abs(x))


def wp(r_team, r_opp, home, neutral):
    d = r_team - r_opp + (0 if neutral else (HFA if home else -HFA))
    return 1 / (1 + 10 ** (-d / LOGIT))


def load_json(path, default):
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:  # noqa: BLE001
        return default


def write_json(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def log(*a):
    print(*a, flush=True)


# ---------- ESPN ----------
def parse_event(ev):
    comp = (ev.get("competitions") or [{}])[0]
    side = {}
    for c in comp.get("competitors") or []:
        side[c.get("homeAway")] = {
            "a": ab((c.get("team") or {}).get("abbreviation")),
            "score": num(c.get("score")),
            "leaders": c.get("leaders") or [],
        }
    if "home" not in side or "away" not in side:
        return None
    st = ((comp.get("status") or ev.get("status") or {}).get("type") or {})
    venue = comp.get("venue") or {}
    addr = venue.get("address") or {}
    return {
        "id": ev.get("id"), "kick": ptime(ev.get("date")),
        "home": side["home"], "away": side["away"],
        "done": bool(st.get("completed")), "state": st.get("state") or "pre",
        "neutral": bool(comp.get("neutralSite")),
        "venue": venue.get("fullName") or "", "city": addr.get("city") or "",
        "country": addr.get("country") or "", "indoor": venue.get("indoor"),
        "odds": (comp.get("odds") or [None])[0],
    }


def scoreboard(season=None, week=None):
    if season is None:
        return get(f"{ESPN}/scoreboard")
    return get(f"{ESPN}/scoreboard?seasontype=2&week={week}&dates={season}&limit=50")


def week_games(season, week):
    try:
        data = scoreboard(season, week)
    except Exception as e:  # noqa: BLE001
        log(f"  week {week}: ESPN failed ({e})")
        return []
    return [g for g in (parse_event(e) for e in data.get("events") or []) if g]


def current_week():
    sb = scoreboard()
    season = (sb.get("season") or {}).get("year") or NOW.year
    stype = (sb.get("season") or {}).get("type") or 2
    wk = (sb.get("week") or {}).get("number") or 1
    if stype == 1:
        wk = 1
    elif stype != 2:
        return season, None, []
    games = week_games(season, wk)
    while wk < 18 and games and all(g["done"] for g in games):
        wk += 1
        games = week_games(season, wk)
    return season, wk, games


def norm_status(s):
    s = (s or "").lower()
    if "reserve" in s or s == "ir":
        return "IR"
    if "out" in s:
        return "Out"
    if "doubt" in s:
        return "Doubtful"
    if "quest" in s:
        return "Questionable"
    if "day" in s:
        return "Day-to-day"
    return s.title() if s else ""


def summary_bits(event_id):
    """Injuries and passing leaders per team, plus ESPN's own odds/projection."""
    out = {"inj": {}, "qb": {}, "proj": {}, "pick": None}
    try:
        s = get(f"{ESPN}/summary?event={event_id}")
    except Exception as e:  # noqa: BLE001
        log(f"  summary {event_id}: failed ({e})")
        return out
    for blk in s.get("injuries") or []:
        t = ab((blk.get("team") or {}).get("abbreviation"))
        lst = []
        for it in blk.get("injuries") or []:
            ath = it.get("athlete") or {}
            lst.append({
                "name": ath.get("displayName") or ath.get("shortName") or "?",
                "pos": ((ath.get("position") or {}).get("abbreviation") or "").upper(),
                "status": norm_status(it.get("status") or (it.get("type") or {}).get("description")),
            })
        out["inj"][t] = lst
    for blk in s.get("leaders") or []:
        t = ab((blk.get("team") or {}).get("abbreviation"))
        for cat in blk.get("leaders") or []:
            if cat.get("name") in ("passingYards", "passingLeader"):
                ls = cat.get("leaders") or []
                if ls:
                    out["qb"][t] = (ls[0].get("athlete") or {}).get("displayName")
    pred = s.get("predictor") or {}
    for side in ("homeTeam", "awayTeam"):
        v = (pred.get(side) or {}).get("gameProjection")
        try:
            out["proj"][side] = float(v)
        except (TypeError, ValueError):
            pass
    pc = s.get("pickcenter") or []
    if pc:
        out["pick"] = pc[0]
    return out


def league_injuries():
    """Backup injury source when a game summary has none."""
    res = {}
    try:
        data = get(f"{ESPN}/injuries")
    except Exception as e:  # noqa: BLE001
        log(f"  injuries endpoint failed ({e})")
        return res
    for blk in data.get("injuries") or []:
        t = FULL2AB.get(blk.get("displayName"))
        if not t:
            continue
        lst = []
        for it in blk.get("injuries") or []:
            ath = it.get("athlete") or {}
            lst.append({
                "name": ath.get("displayName") or "?",
                "pos": ((ath.get("position") or {}).get("abbreviation") or "").upper(),
                "status": norm_status(it.get("status")),
            })
        res[t] = lst
    return res


# ---------- Odds ----------
def odds_api():
    key = os.environ.get("ODDS_API_KEY", "").strip()
    if not key:
        log("  no ODDS_API_KEY, using ESPN odds")
        return {}
    q = urllib.parse.urlencode({"apiKey": key, "regions": "us", "markets": "h2h,spreads",
                                "oddsFormat": "american"})
    try:
        data = get(f"{ODDS}?{q}")
    except Exception as e:  # noqa: BLE001
        log(f"  Odds API failed ({e}), using ESPN odds")
        return {}
    out = {}
    for ev in data:
        h, a = FULL2AB.get(ev.get("home_team")), FULL2AB.get(ev.get("away_team"))
        if not h or not a:
            continue
        ph_list, mls, spr = [], {h: [], a: []}, {h: [], a: []}
        for bk in ev.get("bookmakers") or []:
            for m in bk.get("markets") or []:
                oc = {FULL2AB.get(x.get("name")): x for x in m.get("outcomes") or []}
                if m.get("key") == "h2h" and h in oc and a in oc:
                    try:
                        ph_list.append(ml_prob(oc[h]["price"], oc[a]["price"]))
                        mls[h].append(float(oc[h]["price"]))
                        mls[a].append(float(oc[a]["price"]))
                    except (TypeError, ValueError, ZeroDivisionError):
                        pass
                elif m.get("key") == "spreads":
                    for t in (h, a):
                        if t in oc and oc[t].get("point") is not None:
                            spr[t].append(float(oc[t]["point"]))
        if not ph_list:
            continue
        out.setdefault(frozenset((h, a)), []).append({
            "home": h, "kick": ptime(ev.get("commence_time")), "ph": statistics.mean(ph_list),
            "ml": {t: med(v) for t, v in mls.items()},
            "spread": {t: med(v) for t, v in spr.items()}, "books": len(ph_list)})
    log(f"  Odds API: {sum(len(v) for v in out.values())} games")
    return out


def espn_odds(g, bits):
    """(home win prob, {team: spread}, {team: ml}) from ESPN, or Nones."""
    h, a = g["home"]["a"], g["away"]["a"]
    o = bits.get("pick") or g.get("odds") or {}
    ml_h = ((o.get("homeTeamOdds") or {}).get("moneyLine"))
    ml_a = ((o.get("awayTeamOdds") or {}).get("moneyLine"))
    ph = None
    try:
        if ml_h is not None and ml_a is not None:
            ph = ml_prob(ml_h, ml_a)
    except (TypeError, ValueError, ZeroDivisionError):
        ph = None
    if ph is None and bits["proj"].get("homeTeam") is not None:
        ph = bits["proj"]["homeTeam"] / 100
    sp = o.get("spread")
    spreads = {}
    try:
        sp = float(sp)
        spreads = {h: sp, a: -sp}  # ESPN spread is from the home team's side
    except (TypeError, ValueError):
        pass
    mls = {h: num(ml_h), a: num(ml_a)}
    return ph, spreads, mls


# ---------- Weather ----------
def site_for(g):
    text = f"{g['venue']} {g['city']}".lower()
    if g["neutral"] or g["country"] not in ("", "USA", "United States"):
        for key, city, lat, lon, roof in INTL:
            if key in text:
                return city, lat, lon, roof
        return (g["city"] or "Neutral site"), None, None, "open"
    lat, lon, roof = STADIUMS.get(g["home"]["a"], (None, None, "open"))
    return "", lat, lon, roof


def forecast(lat, lon, kick):
    if lat is None or kick - NOW > timedelta(days=7) or kick < NOW - timedelta(hours=4):
        return None
    d = kick.strftime("%Y-%m-%d")
    q = urllib.parse.urlencode({
        "latitude": lat, "longitude": lon, "timezone": "UTC", "start_date": d, "end_date": d,
        "hourly": "temperature_2m,precipitation_probability,wind_speed_10m,wind_gusts_10m",
        "temperature_unit": "fahrenheit", "wind_speed_unit": "mph"})
    try:
        j = get(f"https://api.open-meteo.com/v1/forecast?{q}")
        hr = j["hourly"]
        times = [ptime(t + ":00+00:00" if len(t) == 16 else t) for t in hr["time"]]
        i = min(range(len(times)), key=lambda k: abs((times[k] - kick).total_seconds()))
        val = lambda k: (hr.get(k) or [None] * len(times))[i]  # noqa: E731
        return {"t": val("temperature_2m"), "w": val("wind_speed_10m"),
                "g": val("wind_gusts_10m"), "p": val("precipitation_probability")}
    except Exception as e:  # noqa: BLE001
        log(f"  weather failed ({e})")
        return None


def weather_text(roof, wx, indoor):
    if roof in ROOF_TEXT or indoor is True:
        return ROOF_TEXT.get(roof, "Indoors") + ", so weather is not a factor.", False
    if not wx or wx.get("t") is None:
        return "Outdoor. Forecast shows up within a week of kickoff.", False
    parts = [f"{round(wx['t'])}°F"]
    if wx.get("w") is not None:
        parts.append(f"wind {round(wx['w'])} mph" + (f" (gusts {round(wx['g'])})" if wx.get("g") and wx["g"] >= wx["w"] + 8 else ""))
    if wx.get("p") is not None:
        parts.append(f"{round(wx['p'])}% chance of rain")
    bad = (wx.get("w") or 0) >= 15 or (wx.get("g") or 0) >= 28 or (wx.get("p") or 0) >= 60 or wx["t"] <= 25
    return ", ".join(parts) + (". Could affect the game." if bad else "."), bad


# ---------- Main ----------
def main():
    kind = os.environ.get("RUN_KIND", "manual")
    state = load_json(STATE, {})
    log(f"Run kind: {kind}")

    season, wk, games = current_week()
    if wk is None:
        log("Not the regular season. Nothing to do.")
        return 0
    if not games:
        log(f"No games found for week {wk}. Keeping old data.")
        return 1

    if kind == "gameday":
        soon = [g for g in games if g["state"] == "pre"
                and timedelta(minutes=35) <= g["kick"] - NOW <= timedelta(minutes=85)]
        last = state.get("lastFull")
        recent = last and NOW - ptime(last) < timedelta(minutes=40)
        if not soon or recent:
            log("No kickoff in the 35-85 minute window (or just updated). Skipping.")
            return 0
        log(f"Game-day check for {len(soon)} upcoming game(s)")

    log(f"Season {season}, week {wk}: {len(games)} games")
    weeks = {wk: games}
    for n in range(1, 19):
        if n != wk:
            weeks[n] = week_games(season, n)

    # Results so far
    res = {t: [] for t in TEAMS}
    for n, gs in weeks.items():
        for g in gs:
            h, a = g["home"]["a"], g["away"]["a"]
            if h not in res or a not in res:
                continue
            if g["done"] and g["home"]["score"] is not None and g["away"]["score"] is not None:
                hs, as_ = g["home"]["score"], g["away"]["score"]
                res[h].append({"wk": n, "opp": a, "home": True, "neutral": g["neutral"], "pf": hs, "pa": as_})
                res[a].append({"wk": n, "opp": h, "home": False, "neutral": g["neutral"], "pf": as_, "pa": hs})
    for t in res:
        res[t].sort(key=lambda x: x["wk"])

    def record(t):
        w = sum(1 for x in res[t] if x["pf"] > x["pa"])
        l_ = sum(1 for x in res[t] if x["pf"] < x["pa"])
        ti = sum(1 for x in res[t] if x["pf"] == x["pa"])
        return f"{w}-{l_}" + (f"-{ti}" if ti else ""), w, l_

    # Ratings: margin-based, adjusted for opponents, shrunk early in the season
    r = {t: 0.0 for t in TEAMS}
    for _ in range(40):
        new = {}
        for t in TEAMS:
            gs = res[t]
            if not gs:
                new[t] = 0.0
                continue
            tot = 0.0
            for x in gs:
                m = max(-21, min(21, x["pf"] - x["pa"]))
                m -= 0 if x["neutral"] else (HFA if x["home"] else -HFA)
                tot += m + r[x["opp"]]
            new[t] = tot / len(gs)
        mean = sum(new.values()) / len(new)
        r = {t: v - mean for t, v in new.items()}
    rating = {t: r[t] * len(res[t]) / (len(res[t]) + 3) for t in TEAMS}

    # Future projections and schedule strength
    future = {t: [] for t in TEAMS}
    for n in range(wk + 1, 19):
        for g in weeks.get(n) or []:
            h, a = g["home"]["a"], g["away"]["a"]
            if h not in TEAMS or a not in TEAMS:
                continue
            ph = wp(rating[h], rating[a], True, g["neutral"])
            future[h].append({"wk": n, "opp": a, "home": True, "neutral": g["neutral"], "p": round(ph * 100)})
            future[a].append({"wk": n, "opp": h, "home": False, "neutral": g["neutral"], "p": round((1 - ph) * 100)})
    sos_val = {t: statistics.mean([rating[f["opp"]] for f in future[t]]) for t in TEAMS if future[t]}
    order = sorted(sos_val, key=lambda t: -sos_val[t])
    sos = {}
    for i, t in enumerate(order):
        sos[t] = {"grade": "Hard" if i < len(order) / 3 else ("Medium" if i < 2 * len(order) / 3 else "Easy"),
                  "rank": i + 1}

    # Odds, summaries, weather for this week
    odds = odds_api()
    backup_inj = None
    if state.get("season") != season or state.get("week") != wk:
        state = {"season": season, "week": wk, "open": {}}
    opens = state.setdefault("open", {})

    teams, sources = {}, set()
    playing = set()
    for g in sorted(games, key=lambda x: x["kick"]):
        h, a = g["home"]["a"], g["away"]["a"]
        if h not in TEAMS or a not in TEAMS:
            continue
        playing.update([h, a])
        bits = summary_bits(g["id"])
        if not bits["inj"]:
            if backup_inj is None:
                backup_inj = league_injuries()
            bits["inj"] = {t: backup_inj.get(t, []) for t in (h, a)}
        for side in ("home", "away"):
            t = g[side]["a"]
            if t not in bits["qb"]:
                for cat in g[side]["leaders"]:
                    if cat.get("name") in ("passingYards", "passingLeader") and cat.get("leaders"):
                        bits["qb"][t] = (cat["leaders"][0].get("athlete") or {}).get("displayName")

        # Same two teams can meet twice, so match on kickoff time too
        o = next((x for x in odds.get(frozenset((h, a)), [])
                  if abs((x["kick"] - g["kick"]).total_seconds()) < 36 * 3600), None)
        spreads, mls, ph = {}, {}, None
        if o:
            ph = o["ph"] if o["home"] == h else 1 - o["ph"]
            spreads, mls = o["spread"], o["ml"]
            sources.add(f"The Odds API ({o['books']} books)")
        else:
            ph, spreads, mls = espn_odds(g, bits)
            if ph is not None:
                sources.add("ESPN odds")
        if ph is None:
            ph = wp(rating[h], rating[a], True, g["neutral"])
            sources.add("team ratings")

        city, lat, lon, roof = site_for(g)
        wx = forecast(lat, lon, g["kick"]) if roof == "open" else None
        wx_text, wx_bad = weather_text(roof, wx, g["indoor"])

        for t, o_, home in ((h, a, True), (a, h, False)):
            p = ph if home else 1 - ph
            teams[t] = build_team(t, o_, home, g, p, spreads, mls, bits, res, weeks, wk,
                                  record, opens, city, wx_text, wx_bad, future, sos)

    if not teams:
        log("No teams built. Keeping old data.")
        return 1

    byes = sorted(t for t in TEAMS if t not in playing)
    data = {
        "v": 1, "season": season, "week": wk,
        "updated": NOW.isoformat(timespec="seconds"),
        "source": " + ".join(sorted(sources)) or "ESPN",
        "byes": byes, "teams": teams,
        # Every team's remaining schedule, so the app can judge future value
        "future": {t: [{"wk": f["wk"], "opp": f["opp"], "home": f["home"], "p": f["p"]} for f in future[t]]
                   for t in TEAMS},
        "sos": {t: sos[t]["grade"] for t in sos},
    }
    write_json(OUT, data)
    state["lastFull"] = NOW.isoformat(timespec="seconds")
    write_json(STATE, state)
    log(f"Wrote data.json: week {wk}, {len(teams)} teams, byes {byes}")
    return 0


def build_team(t, o, home, g, p, spreads, mls, bits, res, weeks, wk, record, opens,
               city, wx_text, wx_bad, future, sos):
    name, oname = TEAMS[t][0], TEAMS[o][0]
    neutral = g["neutral"]
    rec, w, l_ = record(t)
    orec, ow, ol = record(o)
    prob = round(p * 100, 1)

    # Line
    sp, ml = spreads.get(t), mls.get(t)
    line = None
    if sp is not None or ml is not None:
        line = " · ".join(x for x in [
            f"{t} {fmt_spread(sp)}" if sp is not None else None,
            f"ML {fmt_ml(ml)}" if ml is not None else None] if x)

    # Movement since first seen this week
    first = opens.get(t)
    if not first:
        first = opens[t] = {"prob": prob, "spread": sp, "at": NOW.isoformat(timespec="seconds")}
    day = ptime(first["at"]).astimezone(timezone(timedelta(hours=-5))).strftime("%a")
    diff = prob - first["prob"]
    if abs(diff) < 1:
        move = f"Steady since {day} ({prob:g}%)."
    else:
        move = f"{first['prob']:g}% on {day}, now {prob:g}% ({'+' if diff > 0 else ''}{diff:.1f})."

    # QBs and injuries
    inj, oinj = bits["inj"].get(t, []), bits["inj"].get(o, [])

    def qb_line(team, lst):
        q = bits["qb"].get(team)
        if not q:
            return None, None
        st = next((x["status"] for x in lst if x["name"] == q), "")
        if st in ("Out", "IR", "Doubtful", "Questionable"):
            return f"{q} ({st})", st
        return q, None
    qb, qst = qb_line(t, inj)
    oqb, oqst = qb_line(o, oinj)

    key_pos = {"WR", "RB", "TE", "OT", "T", "LT", "RT", "C", "G", "DE", "EDGE", "DT", "LB", "CB", "S", "OLB"}

    def missing(lst):
        return [x for x in lst if x["status"] in ("Out", "Doubtful") and x["pos"] in key_pos]

    # Rest and travel
    kick = g["kick"]
    prev = [x for n in range(1, wk) for x in (weeks.get(n) or []) if t in (x["home"]["a"], x["away"]["a"])]
    oprev = [x for n in range(1, wk) for x in (weeks.get(n) or []) if o in (x["home"]["a"], x["away"]["a"])]
    rest = []

    def days_rest(lst):
        return (kick - max(x["kick"] for x in lst)).total_seconds() / 86400 if lst else None

    dr, odr = days_rest(prev), days_rest(oprev)
    bye = wk > 1 and not any(t in (x["home"]["a"], x["away"]["a"]) for x in weeks.get(wk - 1) or [])
    obye = wk > 1 and not any(o in (x["home"]["a"], x["away"]["a"]) for x in weeks.get(wk - 1) or [])
    if bye:
        rest.append("Coming off a bye")
    elif dr is not None and dr < 5:
        rest.append(f"Short week ({int(dr)} days rest)")
    elif dr is not None and dr >= 9.5:
        rest.append(f"Extra rest ({int(dr)} days)")
    if obye and not bye:
        rest.append(f"{oname} are coming off a bye")
    elif odr is not None and odr < 5 and not (dr is not None and dr < 5):
        rest.append(f"{oname} are on a short week")
    if neutral and city:
        rest.append(f"Both teams travel to {city}")
    elif not home:
        streak = 1
        for x in reversed(sorted(prev, key=lambda x: x["kick"])):
            if x["away"]["a"] == t and not x["neutral"]:
                streak += 1
            else:
                break
        if streak >= 3:
            rest.append(f"{streak}rd straight road game" if streak == 3 else f"{streak}th straight road game")
        local_hour = kick.astimezone(timezone(timedelta(hours=-5))).hour
        if t in WEST and o in EAST and local_hour <= 12:
            rest.append("West coast team in an early East game")
    rest_text = ". ".join(rest) + "." if rest else "Normal week for both teams."

    # Form
    last = res[t][-3:]
    form = " · ".join(
        f"{'W' if x['pf'] > x['pa'] else ('L' if x['pf'] < x['pa'] else 'T')} {x['pf']}-{x['pa']} "
        f"{'vs' if x['home'] or x['neutral'] else 'at'} {x['opp']}" for x in reversed(last))
    pdiff = sum(x["pf"] - x["pa"] for x in res[t])
    form = (form + f" ({'+' if pdiff > 0 else ''}{pdiff} season)") if form else "No games yet."

    # Notes (+ good for picking this team, - bad, i info)
    notes = []
    if oqst in ("Out", "IR", "Doubtful"):
        notes.append(["plus", f"{oname} QB {bits['qb'].get(o)} is {oqst.lower() if oqst != 'IR' else 'on IR'}."])
    elif oqst == "Questionable":
        notes.append(["info", f"{oname} QB {bits['qb'].get(o)} is questionable."])
    if qst in ("Out", "IR", "Doubtful"):
        notes.append(["minus", f"{name} QB {bits['qb'].get(t)} is {qst.lower() if qst != 'IR' else 'on IR'}."])
    elif qst == "Questionable":
        notes.append(["minus", f"{name} QB {bits['qb'].get(t)} is questionable."])
    if ow == 0 and ol >= 2:
        notes.append(["plus", f"{oname} are winless ({orec})."])
    om, tm = missing(oinj), missing(inj)
    if om:
        notes.append(["plus", f"{oname} missing: " + ", ".join(f"{x['name']} ({x['pos']})" for x in om[:4])
                      + (f" +{len(om) - 4} more" if len(om) > 4 else "") + "."])
    if tm:
        notes.append(["minus", f"{name} missing: " + ", ".join(f"{x['name']} ({x['pos']})" for x in tm[:4])
                      + (f" +{len(tm) - 4} more" if len(tm) > 4 else "") + "."])
    same_div = DIV.get(t) == DIV.get(o)
    if neutral:
        notes.append(["info", f"Neutral site{(' in ' + city) if city else ''}, so neither team gets much home edge."])
    elif home:
        notes.append(["plus", "Home game" + (" against a division rival." if same_div else ".")])
    elif same_div:
        notes.append(["minus", "Road division game. These upset more often than the odds suggest."])
    else:
        notes.append(["info", "Road game."])
    if bye and not obye:
        notes.append(["plus", f"{name} are coming off a bye."])
    if obye and not bye:
        notes.append(["minus", f"{oname} are coming off a bye."])
    if wx_bad:
        notes.append(["minus" if prob >= 50 else "info", "Weather could make this sloppy, which helps underdogs."])
    if abs(diff) >= 4:
        notes.append(["plus" if diff > 0 else "minus",
                      f"Odds moved {'toward' if diff > 0 else 'away from'} {name} since {day}."])
    if kick.astimezone(timezone(timedelta(hours=-5))).weekday() == 3:
        notes.append(["info", "Thursday game. The pick locks early."])

    return {
        "n": name, "opp": o, "home": home, "neutral": neutral, "city": city,
        "kick": kick.isoformat(timespec="minutes"), "rec": rec, "orec": orec, "prob": prob,
        "line": line, "qb": qb, "oqb": oqb, "move": move, "rest": rest_text, "form": form,
        "wx": wx_text, "notes": notes[:7],
    }


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:  # noqa: BLE001
        log(f"Update failed: {e!r}. Keeping old data.")
        sys.exit(1)
