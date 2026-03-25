import requests
import time
import csv
import io
import re
import datetime
import concurrent.futures

BASE = "https://statsapi.mlb.com/api/v1"
HEADSHOT_URL = "https://img.mlbstatic.com/mlb-photos/image/upload/d_people:generic:headshot:67:current.png/w_213,q_auto:best/v1/people/{id}/headshot/67/current"
TEAM_LOGO_URL = "https://www.mlbstatic.com/team-logos/{team_id}.svg"

_cache = {}
CACHE_TTL = 3600  # 1 hour

SAVANT_CACHE_TTL = 21600  # 6 hours
_savant_xstats_cache = {}   # {"batter"|"pitcher": (indexed_dict, ts)}
_savant_pitcharsenal_cache = {}  # {year: (indexed_dict, ts)}
_savant_leaderboard_cache = {}  # {"batter"|"pitcher": (indexed_dict, ts)}
_bat_tracking_cache = {}        # {year: (indexed_dict, ts)}
_sprint_speed_cache = {}        # {year: (indexed_dict, ts)}
_fb_velo_cache = {}             # {year: (indexed_dict, ts)}
_video_cache = {}               # {player_id: (videos_list, ts)}
VIDEO_CACHE_TTL = 3600
_savant_percentile_cache = {}  # {"batter"|"pitcher": (indexed_dict, ts)}
_nbc_playerurl_cache = {}  # {player_id: (url, ts)}
_nbc_news_cache = {}       # {player_id: (news_list, ts)}
_fangraphs_cache = {}      # {("bat"|"pit", year): (indexed_dict, ts)}
_milb_cache = {}           # {str(player_id): (data, ts)}
NBC_URL_CACHE_TTL = 86400  # 24h
NBC_NEWS_CACHE_TTL = 1800  # 30 min
NBC_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}


def _col(row, *names, default=''):
    """Try multiple CSV column name variants, return first non-empty value."""
    for name in names:
        val = str(row.get(name, '')).strip()
        if val:
            return val
    return default
SAVANT_HEADERS = {"User-Agent": "Mozilla/5.0"}
FG_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.fangraphs.com/leaders/major-league",
    "Origin": "https://www.fangraphs.com",
    "Sec-Fetch-Dest": "empty",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Site": "same-origin",
}
FG_BASE = "https://www.fangraphs.com/api/leaders/major-league/data"
SPORT_LEVELS = {11: "Triple-A", 12: "Double-A", 13: "High-A", 14: "Single-A", 17: "Rookie"}

PITCH_TYPE_NAMES = {
    "FF": "4-Seam FB", "SI": "Sinker", "SL": "Slider", "CH": "Changeup",
    "CU": "Curveball", "KC": "Knuckle Curve", "FC": "Cutter", "FS": "Splitter",
    "ST": "Sweeper", "SV": "Slurve", "KN": "Knuckleball", "EP": "Eephus",
    "FO": "Forkball", "SC": "Screwball", "CS": "Slow Curve",
}


def _get(url, params=None):
    key = url + str(params)
    if key in _cache:
        data, ts = _cache[key]
        if time.time() - ts < CACHE_TTL:
            return data
    r = requests.get(url, params=params, timeout=10)
    r.raise_for_status()
    data = r.json()
    _cache[key] = (data, time.time())
    return data


def headshot_url(player_id):
    return HEADSHOT_URL.format(id=player_id)


def team_logo_url(team_id):
    return TEAM_LOGO_URL.format(team_id=team_id) if team_id else ""


def search_players(name):
    try:
        data = _get(f"{BASE}/people/search", {"names": name, "sportId": 1, "hydrate": "currentTeam"})
        results = []
        for p in data.get("people", []):
            team = p.get("currentTeam", {})
            results.append({
                "id": p["id"],
                "fullName": p.get("fullName", ""),
                "position": p.get("primaryPosition", {}).get("abbreviation", ""),
                "currentTeam": team.get("name", ""),
                "currentTeamAbbrev": team.get("abbreviation", ""),
                "teamId": team.get("id", ""),
                "headshotUrl": headshot_url(p["id"]),
                "teamLogoUrl": team_logo_url(team.get("id", "")),
            })
        return results[:8]
    except Exception:
        return []


def get_game_log(player_id, group, game_type, season=2026):
    """group = 'hitting' or 'pitching', game_type = 'S' (Spring) or 'R' (Regular)"""
    try:
        key = f"gamelog_{player_id}_{group}_{game_type}_{season}"
        if key in _cache:
            data, ts = _cache[key]
            if time.time() - ts < CACHE_TTL:
                return data
        raw = _get(f"{BASE}/people/{player_id}/stats", {
            "stats": "gameLog",
            "season": season,
            "group": group,
            "gameType": game_type,
            "hydrate": "team",
        })
        games = []
        for stat_group in raw.get("stats", []):
            for split in stat_group.get("splits", []):
                s = split.get("stat", {})
                date = split.get("date", "")
                opponent = split.get("opponent", {}).get("abbreviation", "")
                is_home = split.get("isHome", True)
                opp_str = f"vs {opponent}" if is_home else f"@ {opponent}"

                if group == "hitting":
                    games.append({
                        "date": date,
                        "opponent": opp_str,
                        "ab": s.get("atBats", 0),
                        "h": s.get("hits", 0),
                        "hr": s.get("homeRuns", 0),
                        "rbi": s.get("rbi", 0),
                        "r": s.get("runs", 0),
                        "sb": s.get("stolenBases", 0),
                        "bb": s.get("baseOnBalls", 0),
                        "k": s.get("strikeOuts", 0),
                        "avg": s.get("avg", ".000"),
                        "obp": s.get("obp", ".000"),
                        "slg": s.get("slg", ".000"),
                    })
                else:
                    decision = ""
                    if s.get("wins", 0): decision = "W"
                    elif s.get("losses", 0): decision = "L"
                    elif s.get("saves", 0): decision = "SV"
                    elif s.get("holds", 0): decision = "HLD"
                    games.append({
                        "date": date,
                        "opponent": opp_str,
                        "ip": s.get("inningsPitched", "0.0"),
                        "h": s.get("hits", 0),
                        "er": s.get("earnedRuns", 0),
                        "bb": s.get("baseOnBalls", 0),
                        "k": s.get("strikeOuts", 0),
                        "hr": s.get("homeRuns", 0),
                        "decision": decision,
                        "era": s.get("era", "-"),
                    })

        games.sort(key=lambda x: x["date"], reverse=True)
        result = games[:15]
        _cache[key] = (result, time.time())
        return result
    except Exception:
        return []


def get_player_info(player_id):
    try:
        data = _get(f"{BASE}/people/{player_id}", {"hydrate": "currentTeam"})
        p = data.get("people", [{}])[0]
        team = p.get("currentTeam", {})
        return {
            "id": player_id,
            "fullName": p.get("fullName", ""),
            "position": p.get("primaryPosition", {}).get("abbreviation", ""),
            "currentTeam": team.get("name", ""),
            "currentTeamAbbrev": team.get("abbreviation", ""),
            "teamId": team.get("id", ""),
            "headshotUrl": headshot_url(player_id),
            "teamLogoUrl": team_logo_url(team.get("id", "")),
            "currentAge": p.get("currentAge"),
            "mlbDebutDate": p.get("mlbDebutDate"),
            "birthDate": p.get("birthDate"),
            "height": p.get("height"),
            "weight": p.get("weight"),
            "active": p.get("active", True),
        }
    except Exception:
        return {}


def get_season_totals(player_id, season=2026):
    try:
        hitting_raw = _get(f"{BASE}/people/{player_id}/stats", {
            "stats": "season",
            "group": "hitting",
            "season": season,
            "gameType": "R",
        })
        pitching_raw = _get(f"{BASE}/people/{player_id}/stats", {
            "stats": "season",
            "group": "pitching",
            "season": season,
            "gameType": "R",
        })

        hitting = {}
        for sg in hitting_raw.get("stats", []):
            splits = sg.get("splits", [])
            if splits:
                s = splits[0].get("stat", {})
                hitting = {
                    "gamesPlayed": s.get("gamesPlayed", 0),
                    "ab": s.get("atBats", 0),
                    "hits": s.get("hits", 0),
                    "hr": s.get("homeRuns", 0),
                    "rbi": s.get("rbi", 0),
                    "runs": s.get("runs", 0),
                    "sb": s.get("stolenBases", 0),
                    "bb": s.get("baseOnBalls", 0),
                    "k": s.get("strikeOuts", 0),
                    "avg": s.get("avg", ".000"),
                    "obp": s.get("obp", ".000"),
                    "slg": s.get("slg", ".000"),
                    "ops": s.get("ops", ".000"),
                }

        pitching = {}
        for sg in pitching_raw.get("stats", []):
            splits = sg.get("splits", [])
            if splits:
                s = splits[0].get("stat", {})
                pitching = {
                    "gamesPlayed": s.get("gamesPlayed", 0),
                    "gamesStarted": s.get("gamesStarted", 0),
                    "w": s.get("wins", 0),
                    "l": s.get("losses", 0),
                    "era": s.get("era", "-"),
                    "ip": s.get("inningsPitched", "0.0"),
                    "k": s.get("strikeOuts", 0),
                    "bb": s.get("baseOnBalls", 0),
                    "whip": s.get("whip", "-"),
                    "sv": s.get("saves", 0),
                    "hld": s.get("holds", 0),
                }

        return {"hitting": hitting, "pitching": pitching}
    except Exception:
        return {"hitting": {}, "pitching": {}}


def get_player_transactions(player_id, days=30):
    try:
        end = datetime.date.today()
        start = end - datetime.timedelta(days=days)
        data = _get(f"{BASE}/transactions", {
            "playerId": player_id,
            "startDate": start.strftime("%Y-%m-%d"),
            "endDate": end.strftime("%Y-%m-%d"),
        })

        all_txns = data.get("transactions", [])
        # Sort ascending to determine final IL status chronologically
        all_txns.sort(key=lambda x: x.get("date", ""))

        il_status = None
        transactions = []
        for t in all_txns:
            desc = t.get("description", "").lower()
            transactions.append({
                "date": t.get("date", ""),
                "typeDesc": t.get("typeDesc", ""),
                "description": t.get("description", ""),
            })
            if "injured list" in desc:
                if "activated" in desc:
                    il_status = None
                elif "placed" in desc:
                    if "60-day" in desc:
                        il_status = "IL-60"
                    elif "15-day" in desc:
                        il_status = "IL-15"
                    elif "10-day" in desc:
                        il_status = "IL-10"
                    else:
                        il_status = "IL"
            elif "day-to-day" in desc:
                il_status = "DTD"

        # Return most recent first
        transactions.sort(key=lambda x: x["date"], reverse=True)
        return {"transactions": transactions, "ilStatus": il_status}
    except Exception:
        return {"transactions": [], "ilStatus": None}


def _load_savant_xstats(type_, year=2025):
    """Load bulk xStats CSV from Baseball Savant, cached 6h."""
    now = time.time()
    key = (type_, year)
    if key in _savant_xstats_cache:
        data, ts = _savant_xstats_cache[key]
        if now - ts < SAVANT_CACHE_TTL:
            return data

    url = f"https://baseballsavant.mlb.com/expected_statistics?type={type_}&year={year}&min=0&csv=true"
    try:
        r = requests.get(url, headers=SAVANT_HEADERS, timeout=30)
        r.raise_for_status()
        reader = csv.DictReader(io.StringIO(r.content.decode('utf-8-sig')))
        indexed = {}
        for row in reader:
            pid = row.get("player_id", "").strip()
            if pid:
                indexed[pid] = row
        _savant_xstats_cache[key] = (indexed, now)
        return indexed
    except Exception:
        return {}


def get_xstats(player_id):
    """Return xStats for player_id or None if not found."""
    pid_str = str(player_id)
    for type_ in ("batter", "pitcher"):
        data = _load_savant_xstats(type_)
        if pid_str in data:
            row = data[pid_str]
            return {
                "pa": row.get("pa", ""),
                "ba": row.get("ba", ""),
                "xba": row.get("est_ba", ""),
                "xba_diff": row.get("est_ba_minus_ba_diff", ""),
                "slg": row.get("slg", ""),
                "xslg": row.get("est_slg", ""),
                "woba": row.get("woba", ""),
                "xwoba": row.get("est_woba", ""),
                "player_name": row.get("last_name, first_name", row.get("player_name", "")),
            }
    return None


def _load_savant_pitch_arsenal(year=2025):
    """Load bulk pitch arsenal leaderboard CSV from Baseball Savant, cached 6h."""
    now = time.time()
    if year in _savant_pitcharsenal_cache:
        data, ts = _savant_pitcharsenal_cache[year]
        if now - ts < SAVANT_CACHE_TTL:
            return data

    url = (f"https://baseballsavant.mlb.com/leaderboard/pitch-arsenal-stats"
           f"?type=pitcher&pitchType=&year={year}&min=0&csv=true")
    try:
        r = requests.get(url, headers=SAVANT_HEADERS, timeout=30)
        r.raise_for_status()
        reader = csv.DictReader(io.StringIO(r.content.decode('utf-8-sig')))
        indexed = {}
        for row in reader:
            pid = row.get("player_id", "").strip()
            if pid:
                if pid not in indexed:
                    indexed[pid] = []
                indexed[pid].append(row)
        _savant_pitcharsenal_cache[year] = (indexed, now)
        return indexed
    except Exception:
        return {}


def get_pitch_mix(player_id, year=2025):
    """Return pitch mix for a pitcher from the arsenal leaderboard CSV."""
    pid_str = str(player_id)
    data = _load_savant_pitch_arsenal(year)
    if pid_str not in data:
        return []

    pitches = []
    for row in data[pid_str]:
        pt = row.get("pitch_type", "").strip()
        try:
            pct = float(row.get("pitch_usage", 0) or 0)
        except (ValueError, TypeError):
            pct = 0
        try:
            cnt = int(float(row.get("pitches", 0) or 0))
        except (ValueError, TypeError):
            cnt = 0
        try:
            whiff = float(row.get("whiff_percent", 0) or 0)
        except (ValueError, TypeError):
            whiff = None
        try:
            rv = float(row.get("run_value_per_100", 0) or 0)
        except (ValueError, TypeError):
            rv = None
        pitches.append({
            "pitch_type": pt,
            "pitch_name": PITCH_TYPE_NAMES.get(pt, row.get("pitch_name", pt)),
            "count": cnt,
            "percent": round(pct, 1),
            "whiff_pct": round(whiff, 1) if whiff is not None else None,
            "run_value": round(rv, 1) if rv is not None else None,
        })

    pitches.sort(key=lambda x: x["percent"], reverse=True)
    return pitches


def _load_savant_leaderboard(type_, year=2025):
    """Load bulk Statcast leaderboard CSV from Baseball Savant, cached 6h."""
    now = time.time()
    key = (type_, year)
    if key in _savant_leaderboard_cache:
        data, ts = _savant_leaderboard_cache[key]
        if now - ts < SAVANT_CACHE_TTL:
            return data

    url = (f"https://baseballsavant.mlb.com/leaderboard/statcast"
           f"?abs=0&player_type={type_}&year={year}&position=&team=&min=0&csv=true")
    try:
        r = requests.get(url, headers=SAVANT_HEADERS, timeout=30)
        r.raise_for_status()
        reader = csv.DictReader(io.StringIO(r.content.decode('utf-8-sig')))
        indexed = {}
        for row in reader:
            pid = row.get("player_id", "").strip()
            if pid:
                indexed[pid] = row
        _savant_leaderboard_cache[key] = (indexed, now)
        return indexed
    except Exception:
        return {}


def _load_fb_velo(year=2025):
    """Load pitcher fastball velocity from pitch movement leaderboard, cached 6h."""
    now = time.time()
    if year in _fb_velo_cache:
        data, ts = _fb_velo_cache[year]
        if now - ts < SAVANT_CACHE_TTL:
            return data
    url = (f"https://baseballsavant.mlb.com/leaderboard/pitch-movement"
           f"?pitcher_throws=&year={year}&team=&pitchType=FF&min=0&csv=true")
    try:
        r = requests.get(url, headers=SAVANT_HEADERS, timeout=30)
        r.raise_for_status()
        reader = csv.DictReader(io.StringIO(r.content.decode('utf-8-sig')))
        indexed = {row["pitcher_id"].strip(): row for row in reader if row.get("pitcher_id", "").strip()}
        _fb_velo_cache[year] = (indexed, now)
        return indexed
    except Exception:
        return {}


def _load_bat_tracking(year=2025):
    """Load bat-tracking CSV (bat speed + whiff rate), cached 6h."""
    now = time.time()
    if year in _bat_tracking_cache:
        data, ts = _bat_tracking_cache[year]
        if now - ts < SAVANT_CACHE_TTL:
            return data
    url = f"https://baseballsavant.mlb.com/leaderboard/bat-tracking?year={year}&min=0&csv=true"
    try:
        r = requests.get(url, headers=SAVANT_HEADERS, timeout=30)
        r.raise_for_status()
        reader = csv.DictReader(io.StringIO(r.content.decode('utf-8-sig')))
        indexed = {row["id"].strip(): row for row in reader if row.get("id", "").strip()}
        _bat_tracking_cache[year] = (indexed, now)
        return indexed
    except Exception:
        return {}


def _load_sprint_speed(year=2025):
    """Load sprint speed leaderboard CSV, cached 6h."""
    now = time.time()
    if year in _sprint_speed_cache:
        data, ts = _sprint_speed_cache[year]
        if now - ts < SAVANT_CACHE_TTL:
            return data
    url = f"https://baseballsavant.mlb.com/leaderboard/sprint_speed?year={year}&position=&team=&min=0&csv=true"
    try:
        r = requests.get(url, headers=SAVANT_HEADERS, timeout=30)
        r.raise_for_status()
        reader = csv.DictReader(io.StringIO(r.content.decode('utf-8-sig')))
        indexed = {row["player_id"].strip(): row for row in reader if row.get("player_id", "").strip()}
        _sprint_speed_cache[year] = (indexed, now)
        return indexed
    except Exception:
        return {}


def get_statcast(player_id, year=2025):
    """Return comprehensive Statcast data combining leaderboard + xStats."""
    pid_str = str(player_id)

    xstats_row = None
    xstats_type = None
    for type_ in ("batter", "pitcher"):
        data = _load_savant_xstats(type_, year)
        if pid_str in data:
            xstats_row = data[pid_str]
            xstats_type = type_
            break

    lb_row = None
    lb_type = None
    for type_ in ("batter", "pitcher"):
        data = _load_savant_leaderboard(type_, year)
        if pid_str in data:
            lb_row = data[pid_str]
            lb_type = type_
            break

    pct_row = None
    pct_type = None
    for type_ in ("batter", "pitcher"):
        data = _load_savant_percentiles(type_, year)
        if pid_str in data:
            pct_row = data[pid_str]
            pct_type = type_
            break

    if lb_row is None and xstats_row is None and pct_row is None:
        return None

    player_type = lb_type or xstats_type or pct_type
    result = {"type": player_type}

    if xstats_row:
        result["pa"] = xstats_row.get("pa", "")
        result["ba"] = xstats_row.get("ba", "")
        result["xba"] = xstats_row.get("est_ba", "")
        result["slg"] = xstats_row.get("slg", "")
        result["xslg"] = xstats_row.get("est_slg", "")
        result["woba"] = xstats_row.get("woba", "")
        result["xwoba"] = xstats_row.get("est_woba", "")
        # xera is available directly in the pitcher xstats CSV
        if xstats_row.get("xera"):
            result.setdefault("xera", xstats_row.get("xera", ""))

    if lb_row:
        if player_type == "batter":
            result["avg_ev"] = _col(lb_row, "avg_hit_speed", "launch_speed_avg")
            result["max_ev"] = _col(lb_row, "max_hit_speed", "launch_speed_max")
            result["avg_la"] = _col(lb_row, "avg_hit_angle", "launch_angle_avg")
            result["sweet_spot_pct"] = _col(lb_row, "anglesweetspotpercent", "sweet_spot_percent")
            result["barrel_pct"] = _col(lb_row, "brl_percent", "bbarrel_batted_rate", "barrel_batted_rate")
            result["barrel_pa"] = _col(lb_row, "brl_pa", "bbarrel_pa", "barrel_pa")
            result["hard_hit_pct"] = _col(lb_row, "ev95percent", "hard_hit_percent")
            result["k_pct"] = _col(lb_row, "k_percent")
            result["bb_pct"] = _col(lb_row, "bb_percent")
            result["whiff_pct"] = _col(lb_row, "whiff_percent")
            result["chase_pct"] = _col(lb_row, "chase_percent")
            result["sprint_speed"] = _col(lb_row, "sprint_speed")
            result["bat_speed"] = _col(lb_row, "bat_speed")
        else:
            result["avg_ev_against"] = _col(lb_row, "avg_hit_speed", "launch_speed_avg")
            result["barrel_pct_against"] = _col(lb_row, "brl_percent", "bbarrel_batted_rate", "barrel_batted_rate")
            result["hard_hit_pct_against"] = _col(lb_row, "ev95percent", "hard_hit_percent")
            result["k_pct"] = _col(lb_row, "k_percent")
            result["bb_pct"] = _col(lb_row, "bb_percent")
            result["gb_pct"] = _col(lb_row, "groundballs_percent", "gb_percent")
            result["whiff_pct"] = _col(lb_row, "whiff_percent")
            result["chase_pct"] = _col(lb_row, "chase_percent")
            result["csw_pct"] = _col(lb_row, "csw")
            result["xera"] = _col(lb_row, "xera", "p_era")
            result["fb_velo"] = _col(lb_row, "fastball_avg_speed", "p_fastball", "ff_avg_speed", "fastball_speed_avg")

    if pct_row:
        skip = {"player_name", "player_id", "year"}
        percentiles = {}
        for k, v in pct_row.items():
            if k in skip:
                continue
            try:
                percentiles[k] = int(float(v)) if v and v.strip() else None
            except (ValueError, TypeError):
                percentiles[k] = None
        result["percentiles"] = {k: v for k, v in percentiles.items() if v is not None}

    # Compute K% and BB% from MLB Stats API season stats (leaderboard CSVs don't have these)
    try:
        stat_group = "hitting" if player_type == "batter" else "pitching"
        season_data = _get(f"{BASE}/people/{player_id}/stats", {
            "stats": "season", "group": stat_group, "season": year, "gameType": "R",
        })
        splits = season_data.get("stats", [{}])[0].get("splits", [])
        if splits:
            s = splits[0].get("stat", {})
            if player_type == "pitcher":
                bf = float(s.get("battersFaced") or 0)
                k  = float(s.get("strikeOuts") or 0)
                bb = float(s.get("baseOnBalls") or 0)
                if bf > 0:
                    result["k_pct"]  = f"{k/bf*100:.1f}"
                    result["bb_pct"] = f"{bb/bf*100:.1f}"
            else:
                pa = float(s.get("plateAppearances") or 0)
                k  = float(s.get("strikeOuts") or 0)
                bb = float(s.get("baseOnBalls") or 0)
                if pa > 0:
                    result["k_pct"]  = f"{k/pa*100:.1f}"
                    result["bb_pct"] = f"{bb/pa*100:.1f}"
    except Exception:
        pass

    # Compute overall whiff% for pitchers from pitch arsenal weighted average
    if player_type == "pitcher" and not result.get("whiff_pct"):
        try:
            arsenal = _load_savant_pitch_arsenal(year)
            if pid_str in arsenal:
                rows = arsenal[pid_str]
                total = sum(float(r.get("pitches") or 0) for r in rows)
                if total > 0:
                    wtd = sum(float(r.get("pitches") or 0) * float(r.get("whiff_percent") or 0)
                              for r in rows)
                    result["whiff_pct"] = f"{wtd/total:.1f}"
        except Exception:
            pass

    # Fastball velo for pitchers (not in standard leaderboard CSV)
    if player_type == "pitcher":
        try:
            fv_row = _load_fb_velo(year).get(pid_str)
            if fv_row and not result.get("fb_velo") and fv_row.get("avg_speed"):
                result["fb_velo"] = fv_row["avg_speed"]
        except Exception:
            pass

    # Bat tracking: bat speed + batter whiff% (not in standard leaderboard CSV)
    if player_type == "batter":
        try:
            bt_row = _load_bat_tracking(year).get(pid_str)
            if bt_row:
                if not result.get("bat_speed") and bt_row.get("avg_bat_speed"):
                    result["bat_speed"] = bt_row["avg_bat_speed"]
                if not result.get("whiff_pct") and bt_row.get("whiff_per_swing"):
                    result["whiff_pct"] = f"{float(bt_row['whiff_per_swing']) * 100:.1f}"
        except Exception:
            pass

    # Sprint speed (all player types)
    try:
        ss_row = _load_sprint_speed(year).get(pid_str)
        if ss_row and not result.get("sprint_speed") and ss_row.get("sprint_speed"):
            result["sprint_speed"] = ss_row["sprint_speed"]
    except Exception:
        pass

    return result


def _get_nbc_player_url(player_id, player_name):
    """Search NBC Sports to find the player's news URL, cached 24h."""
    now = time.time()
    if player_id in _nbc_playerurl_cache:
        url, ts = _nbc_playerurl_cache[player_id]
        if now - ts < NBC_URL_CACHE_TTL:
            return url

    query = player_name.lower().replace(' ', '+')
    search_url = f"https://www.nbcsports.com/search?q={query}&sport=mlb"
    try:
        r = requests.get(search_url, headers=NBC_HEADERS, timeout=15)
        r.raise_for_status()
        # Find first /mlb/{name-slug}/{id} link (no trailing path segments)
        matches = re.findall(
            r'href="(https://www\.nbcsports\.com/mlb/[^/"]+/(?:\d+|[0-9a-f-]{36}))"',
            r.text
        )
        if matches:
            news_url = matches[0] + '/news'
            _nbc_playerurl_cache[player_id] = (news_url, now)
            return news_url
    except Exception:
        pass
    return None


def get_nbc_news(player_id):
    """Scrape player news articles from NBC Sports / Rotoworld."""
    now = time.time()
    if player_id in _nbc_news_cache:
        news, ts = _nbc_news_cache[player_id]
        if now - ts < NBC_NEWS_CACHE_TTL:
            return news

    info = get_player_info(player_id)
    player_name = info.get("fullName", "")
    if not player_name:
        return []

    url = _get_nbc_player_url(player_id, player_name)
    if not url:
        return []

    try:
        r = requests.get(url, headers=NBC_HEADERS, timeout=15)
        r.raise_for_status()
        text = r.text

        def _clean(s):
            return re.sub(r'<[^>]+>', '', s).strip()

        news = []
        for m in re.finditer(r'class="PlayerNewsPost-content"', text):
            chunk = text[m.start():m.start() + 4000]
            headline_m = re.search(r'PlayerNewsPost-headline[^>]*>(.*?)</div>', chunk, re.DOTALL)
            if not headline_m:
                continue
            analysis_m = re.search(r'PlayerNewsPost-analysis[^>]*>(.*?)</div>', chunk, re.DOTALL)
            type_m = re.search(r'PlayerNewsPost-type[^>]*>(.*?)</div>', chunk, re.DOTALL)
            date_m = re.search(r'data-date="([^"]+)"', chunk)
            news.append({
                "headline": _clean(headline_m.group(1)),
                "analysis": _clean(analysis_m.group(1)) if analysis_m else "",
                "type": _clean(type_m.group(1)) if type_m else "",
                "date": date_m.group(1) if date_m else "",
            })
            if len(news) >= 15:
                break

        _nbc_news_cache[player_id] = (news, now)
        return news
    except Exception:
        return []


def _load_savant_percentiles(type_, year=2025):
    """Load bulk percentile rankings CSV from Baseball Savant, cached 6h."""
    now = time.time()
    key = (type_, year)
    if key in _savant_percentile_cache:
        data, ts = _savant_percentile_cache[key]
        if now - ts < SAVANT_CACHE_TTL:
            return data

    url = f"https://baseballsavant.mlb.com/leaderboard/percentile-rankings?type={type_}&year={year}&csv=true"
    try:
        r = requests.get(url, headers=SAVANT_HEADERS, timeout=30)
        r.raise_for_status()
        reader = csv.DictReader(io.StringIO(r.content.decode('utf-8-sig')))
        indexed = {}
        for row in reader:
            pid = row.get("player_id", "").strip()
            if pid:
                indexed[pid] = row
        _savant_percentile_cache[key] = (indexed, now)
        return indexed
    except Exception:
        return {}


def get_career_stats(player_id):
    """Return year-by-year career stats (hitting + pitching) for a player."""
    try:
        h = _get(f"{BASE}/people/{player_id}/stats",
                 {"stats": "yearByYear", "group": "hitting", "sportId": "1", "gameType": "R"})
        p = _get(f"{BASE}/people/{player_id}/stats",
                 {"stats": "yearByYear", "group": "pitching", "sportId": "1", "gameType": "R"})

        def parse(data, group):
            rows = []
            for split in data.get("stats", [{}])[0].get("splits", []):
                s = split.get("stat", {})
                row = {"season": split.get("season"), "team": split.get("team", {}).get("abbreviation", "")}
                if group == "hitting":
                    row.update({k: s.get(k) for k in
                        ["gamesPlayed", "atBats", "hits", "homeRuns", "rbi", "stolenBases",
                         "avg", "obp", "slg", "ops", "strikeOuts", "baseOnBalls"]})
                else:
                    row.update({k: s.get(k) for k in
                        ["gamesPlayed", "gamesStarted", "inningsPitched", "wins", "losses",
                         "saves", "era", "strikeOuts", "baseOnBalls", "hits", "homeRuns", "whip"]})
                rows.append(row)
            return list(reversed(rows))  # most recent first
        return {"hitting": parse(h, "hitting"), "pitching": parse(p, "pitching")}
    except Exception:
        return {"hitting": [], "pitching": []}


def get_minor_league_stats(player_id):
    """Return minor league year-by-year stats across levels, cached 1h. Uses parallel fetches."""
    pid_str = str(player_id)
    now = time.time()
    if pid_str in _milb_cache:
        data, ts = _milb_cache[pid_str]
        if now - ts < CACHE_TTL:
            return data

    def fetch_level(sport_id, level_name, group):
        try:
            data = _get(f"{BASE}/people/{player_id}/stats",
                        {"stats": "yearByYear", "group": group, "sportId": sport_id})
            rows = []
            for split in data.get("stats", [{}])[0].get("splits", []):
                s = split.get("stat", {})
                rows.append({"level": level_name, "year": int(split.get("season", 0)),
                              "group": group, "team": split.get("team", {}).get("name", ""),
                              "stat": s})
            return rows
        except Exception:
            return []

    tasks = [(sid, lvl, grp) for sid, lvl in SPORT_LEVELS.items() for grp in ("hitting", "pitching")]
    result = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as ex:
        futures = [ex.submit(fetch_level, *t) for t in tasks]
        for f in concurrent.futures.as_completed(futures):
            result.extend(f.result())
    result.sort(key=lambda x: -x["year"])
    _milb_cache[pid_str] = (result, now)
    return result


def get_schedule(team_id):
    """Return next 14 days of games for a team."""
    try:
        today = datetime.date.today().strftime("%Y-%m-%d")
        end = (datetime.date.today() + datetime.timedelta(days=14)).strftime("%Y-%m-%d")
        data = _get(f"{BASE}/schedule",
                    {"teamId": team_id, "startDate": today, "endDate": end,
                     "sportId": 1, "hydrate": "team"})
        games = []
        for day in data.get("dates", []):
            for g in day.get("games", []):
                away = g["teams"]["away"]["team"]
                home = g["teams"]["home"]["team"]
                is_home = home["id"] == team_id
                opp = away if is_home else home
                games.append({
                    "date": day["date"],
                    "opponent": opp.get("abbreviation") or opp.get("teamCode", opp.get("name", "")),
                    "home": is_home,
                    "status": g.get("status", {}).get("abstractGameState", ""),
                    "score_us": g["teams"]["home" if is_home else "away"].get("score"),
                    "score_opp": g["teams"]["away" if is_home else "home"].get("score"),
                })
        return games
    except Exception:
        return []


def get_splits(player_id, year=2026):
    """Return vs LHP/RHP + Home/Away splits. Uses parallel fetches with prior-year fallback."""
    result = {"hitting": {}, "pitching": {}}
    sit_map = [("vL", "vs LHP"), ("vR", "vs RHP"), ("h", "Home"), ("a", "Away")]

    def fetch_split(group, sit_code, label):
        for y in (year, year - 1):
            try:
                data = _get(f"{BASE}/people/{player_id}/stats",
                            {"stats": "season", "group": group, "season": y,
                             "sportId": 1, "gameType": "R", "sitCodes": sit_code})
                splits = data.get("stats", [{}])[0].get("splits", [])
                if splits:
                    return (group, label, {"stat": splits[0].get("stat", {}), "year": y})
            except Exception:
                pass
        return None

    tasks = [(grp, sc, lbl) for grp in ("hitting", "pitching") for sc, lbl in sit_map]
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as ex:
        futures = [ex.submit(fetch_split, *t) for t in tasks]
        for f in concurrent.futures.as_completed(futures):
            res = f.result()
            if res:
                group, label, val = res
                result[group][label] = val
    return result


def _load_fangraphs(type_, year=2025):
    """Bulk FanGraphs leaderboard indexed by xMLBAMID, cached 6h."""
    key = (type_, year)
    now = time.time()
    if key in _fangraphs_cache:
        data, ts = _fangraphs_cache[key]
        if now - ts < SAVANT_CACHE_TTL:
            return data
    try:
        r = requests.get(FG_BASE, params={
            "age": 0, "pos": "all", "stats": type_, "lg": 2, "qual": 0,
            "season": year, "season1": year, "pageitems": 2000, "pagenum": 1,
            "ind": 0, "rost": 0, "type": 8,
        }, headers=FG_HEADERS, timeout=5)
        r.raise_for_status()
        rows = r.json().get("data", [])
        indexed = {str(row.get("xMLBAMID", "")).strip(): row
                   for row in rows if row.get("xMLBAMID")}
        print(f"[FG] Loaded {len(indexed)} {type_} players for {year}")
        _fangraphs_cache[key] = (indexed, now)
        return indexed
    except Exception as e:
        print(f"[FG] Error loading {type_} {year}: {e}")
        return {}


def get_fangraphs_stats(player_id, year=2025):
    """Return FanGraphs dashboard stats (WAR, FIP, wRC+, etc.) for a player."""
    pid = str(player_id)
    # Fetch pitcher and batter leaderboards in parallel (both are large bulk downloads)
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as ex:
        f_pit = ex.submit(_load_fangraphs, "pit", year)
        f_bat = ex.submit(_load_fangraphs, "bat", year)
        pit = f_pit.result()
        bat = f_bat.result()
    row = pit.get(pid) or bat.get(pid)
    if not row:
        return None
    is_pitcher = bool(pit.get(pid))
    result = {"type": "pitcher" if is_pitcher else "batter", "year": year}
    if is_pitcher:
        result["war"]     = row.get("WAR")
        result["fip"]     = row.get("FIP")
        result["xfip"]    = row.get("xFIP")
        result["babip"]   = row.get("BABIP")
        result["lob_pct"] = row.get("LOB%")
        result["k_9"]     = row.get("K/9")
        result["bb_9"]    = row.get("BB/9")
        result["era"]     = row.get("ERA")
        result["whip"]    = row.get("WHIP")
    else:
        result["war"]      = row.get("WAR")
        result["wrc_plus"] = row.get("wRC+")
        result["babip"]    = row.get("BABIP")
        result["iso"]      = row.get("ISO")
        result["obp"]      = row.get("OBP")
        result["slg"]      = row.get("SLG")
        result["ops"]      = row.get("OPS")
        result["woba"]     = row.get("wOBA")
    return result


def import_fantrax_url(url):
    """Parse leagueId + teamId from Fantrax URL and fetch roster via unofficial API."""
    m = re.search(r'/fantasy/league/([^/;?]+)/(?:players|team/roster)[^?]*[;?]teamId=([^&;]+)', url)
    if not m:
        return {"error": "Could not parse league/team ID from URL. Expected format: fantrax.com/fantasy/league/{leagueId}/players;teamId={teamId}"}

    league_id = m.group(1)
    team_id = m.group(2)

    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
        "Accept": "application/json, text/plain, */*",
        "Content-Type": "application/json",
        "Origin": "https://www.fantrax.com",
        "Referer": url,
    }

    try:
        r = requests.post(
            "https://www.fantrax.com/fxea/general/getTeamRoster",
            json={"leagueId": league_id, "teamId": team_id},
            headers=headers,
            timeout=15,
        )
        if r.status_code in (401, 403):
            return {"error": "Fantrax requires login for this league. Use the 'Paste Names' tab instead."}
        r.raise_for_status()
        data = r.json()

        # Try multiple response shapes
        items = (data.get("rosterItems") or data.get("items") or data.get("players") or [])
        players = []
        for item in items:
            name = (item.get("name") or item.get("playerName") or
                    (item.get("player") or {}).get("name") or "").strip()
            pos = (item.get("position") or item.get("pos") or
                   (item.get("player") or {}).get("position") or "").strip()
            team = (item.get("team") or item.get("teamAbbrev") or
                    (item.get("player") or {}).get("team") or "").strip()
            if name:
                players.append({"name": name, "pos": pos, "team": team})

        if not players:
            return {"error": f"No players found. Response keys: {list(data.keys())}. First item sample: {str(items[0])[:200] if items else 'no items'}"}

        return {"players": players}
    except requests.HTTPError as e:
        return {"error": f"Fantrax API error: {e.response.status_code}. The league may be private — use 'Paste Names' tab instead."}
    except Exception as e:
        return {"error": f"Failed to fetch roster: {str(e)}"}


def get_player_videos(player_id, season=None, limit=5):
    """Return up to `limit` recent MLB highlight videos for a player, cached 1h."""
    if season is None:
        season = datetime.date.today().year

    now = time.time()
    cache_key = (player_id, season)
    if cache_key in _video_cache:
        videos, ts = _video_cache[cache_key]
        if now - ts < VIDEO_CACHE_TTL:
            return videos

    pid_str = str(player_id)

    # Get recent game PKs — try spring training + regular season for current year,
    # fall back to prior year regular season if nothing found
    game_pks = []
    def _collect_pks(yr, game_type):
        for group in ("hitting", "pitching"):
            try:
                data = _get(f"{BASE}/people/{player_id}/stats",
                            {"stats": "gameLog", "group": group, "season": yr,
                             "sportId": 1, "gameType": game_type})
                for split in data.get("stats", [{}])[0].get("splits", []):
                    gp = split.get("game", {}).get("gamePk")
                    if gp and gp not in game_pks:
                        game_pks.append(gp)
            except Exception:
                pass

    _collect_pks(season, "S")
    _collect_pks(season, "R")
    if not game_pks:
        _collect_pks(season - 1, "R")

    if not game_pks:
        _video_cache[cache_key] = ([], now)
        return []

    # Sort descending (most recent first), check up to 20 games
    game_pks = list(reversed(game_pks))[:20]

    CONTENT_BASE = "https://statsapi.mlb.com/api/v1"
    videos = []

    def fetch_game_highlights(gp):
        try:
            r = requests.get(f"{CONTENT_BASE}/game/{gp}/content",
                             params={"highlightLimit": 20},
                             headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
            if not r.ok:
                return []
            data = r.json()
            # Try several known response shapes
            hl = data.get("highlights") or {}
            items = (
                hl.get("highlights", {}).get("items") or
                data.get("media", {}).get("highlights", {}).get("highlights", {}).get("items") or
                hl.get("items") or
                []
            )
            results = []
            for item in items:
                # Filter to this player's highlights
                keywords = item.get("keywordsAll", [])
                player_ids = [kw.get("value") for kw in keywords
                              if kw.get("type") == "player_id"]
                if pid_str not in player_ids:
                    continue
                # Extract mp4 URL
                mp4_url = None
                for pb in item.get("playbacks", []):
                    if "mp4" in pb.get("name", "").lower() or "mp4" in pb.get("url", "").lower():
                        mp4_url = pb.get("url")
                        break
                if not mp4_url:
                    continue
                # Thumbnail
                thumb_template = item.get("image", {}).get("templateUrl", "")
                thumb = thumb_template.replace("{formatInstructions}", "w_480,h_270,f_jpg,c_fill,g_auto") if thumb_template else ""
                slug = item.get("slug", "")
                date = item.get("date", "")[:10] if item.get("date") else ""
                results.append({
                    "title": item.get("title", ""),
                    "date": date,
                    "duration": item.get("duration", ""),
                    "thumb": thumb,
                    "mp4": mp4_url,
                    "url": f"https://www.mlb.com/video/{slug}" if slug else "",
                })
            return results
        except Exception:
            return []

    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as ex:
        futures = [ex.submit(fetch_game_highlights, gp) for gp in game_pks]
        for f in concurrent.futures.as_completed(futures):
            videos.extend(f.result())
            if len(videos) >= limit:
                break

    # Sort by date desc, cap at limit
    videos.sort(key=lambda v: v.get("date", ""), reverse=True)
    videos = videos[:limit]

    _video_cache[cache_key] = (videos, now)
    return videos


_probable_cache = {}
PROBABLE_CACHE_TTL = 1800  # 30 min

def get_probable_pitchers(days=16):
    """Fetch probable pitchers for the next `days` days from the MLB schedule API."""
    now = time.time()
    if _probable_cache.get("data") and now - _probable_cache["ts"] < PROBABLE_CACHE_TTL:
        return _probable_cache["data"]

    today = datetime.date.today()
    end = today + datetime.timedelta(days=days - 1)
    url = (f"{BASE}/schedule?sportId=1&startDate={today}&endDate={end}"
           f"&hydrate=probablePitcher(note),team&gameType=R,S")
    try:
        r = requests.get(url, timeout=10)
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        print(f"[probable] fetch error: {e}")
        return []

    games = []
    for date_entry in data.get("dates", []):
        date_str = date_entry.get("date", "")
        for game in date_entry.get("games", []):
            status = game.get("status", {}).get("abstractGameState", "")
            if status == "Final":
                continue
            away = game.get("teams", {}).get("away", {})
            home = game.get("teams", {}).get("home", {})
            away_pp = away.get("probablePitcher")
            home_pp = home.get("probablePitcher")
            games.append({
                "date": date_str,
                "game_pk": game.get("gamePk"),
                "game_type": game.get("gameType", "R"),
                "away_team": away.get("team", {}).get("name", ""),
                "away_team_id": away.get("team", {}).get("id"),
                "home_team": home.get("team", {}).get("name", ""),
                "home_team_id": home.get("team", {}).get("id"),
                "away_pitcher": {
                    "id": away_pp.get("id"),
                    "name": away_pp.get("fullName", ""),
                    "note": away_pp.get("note", ""),
                } if away_pp else None,
                "home_pitcher": {
                    "id": home_pp.get("id"),
                    "name": home_pp.get("fullName", ""),
                    "note": home_pp.get("note", ""),
                } if home_pp else None,
            })

    _probable_cache["data"] = games
    _probable_cache["ts"] = now
    return games


_hot_cold_cache = {}
HOT_COLD_CACHE_TTL = 1800  # 30 min


def get_hot_cold(days=14):
    """Return hot/cold hitters and pitchers over the last N days (regular season only)."""
    now = time.time()
    if _hot_cold_cache.get(days) and now - _hot_cold_cache[days]["ts"] < HOT_COLD_CACHE_TTL:
        return _hot_cold_cache[days]["data"]

    season = datetime.date.today().year
    result = {"hot_hitters": [], "cold_hitters": [], "hot_pitchers": [], "cold_pitchers": [], "days": days}

    def fetch(group, sort_stat, order, limit=250):
        try:
            r = requests.get(f"{BASE}/stats", params={
                "stats": "lastXDays",
                "lastXDays": days,
                "group": group,
                "gameType": "R",
                "playerPool": "All",
                "limit": limit,
                "sortStat": sort_stat,
                "order": order,
                "season": season,
                "hydrate": "person,team",
            }, timeout=12)
            r.raise_for_status()
            return r.json().get("stats", [{}])[0].get("splits", [])
        except Exception as e:
            print(f"[hot_cold] fetch error group={group} sort={sort_stat}: {e}")
            return []

    def _f(val, default=0):
        try:
            return float(val)
        except (TypeError, ValueError):
            return default

    def parse_ip(ip_str):
        """Convert '6.2' (6⅔ innings) to true float 6.667."""
        try:
            ip = float(ip_str or 0)
            whole = int(ip)
            thirds = round((ip - whole) * 10)
            return whole + thirds / 3.0
        except (TypeError, ValueError):
            return 0.0

    def fmt(split):
        p = split.get("player", {})
        t = split.get("team", {})
        s = split.get("stat", {})

        ab   = s.get("atBats", 0)
        bb   = s.get("baseOnBalls", 0)
        so   = s.get("strikeOuts", 0)
        slg  = _f(s.get("slg"))
        avg  = _f(s.get("avg"))
        obp  = _f(s.get("obp"))
        pa   = ab + bb
        xbh  = s.get("doubles", 0) + s.get("triples", 0) + s.get("homeRuns", 0)
        iso  = round(slg - avg, 3)
        bb_pct = round(bb / pa, 3) if pa >= 10 else None
        k_pct  = round(so / pa, 3) if pa >= 10 else None

        ip_true = parse_ip(s.get("inningsPitched"))
        pit_k   = s.get("strikeOuts", 0)
        pit_bb  = s.get("baseOnBalls", 0)
        pit_h   = s.get("hits", 0)
        pit_hr  = s.get("homeRuns", 0)
        pit_er  = s.get("earnedRuns", 0)
        k9   = round(pit_k  / ip_true * 9, 2) if ip_true > 0 else None
        bb9  = round(pit_bb / ip_true * 9, 2) if ip_true > 0 else None
        h9   = round(pit_h  / ip_true * 9, 2) if ip_true > 0 else None
        hr9  = round(pit_hr / ip_true * 9, 2) if ip_true > 0 else None
        kbb  = round(pit_k  / pit_bb, 2) if pit_bb > 0 else None
        fip_const = 3.10
        fip  = round((13 * pit_hr + 3 * pit_bb - 2 * pit_k) / ip_true + fip_const, 2) if ip_true >= 3 else None

        return {
            "id": p.get("id"),
            "name": p.get("fullName", ""),
            "team": t.get("name", ""),
            "team_id": t.get("id"),
            "stat": {
                # hitting raw
                "avg": s.get("avg"), "obp": s.get("obp"),
                "slg": s.get("slg"), "ops": s.get("ops"),
                "pa": pa, "atBats": ab, "hits": s.get("hits", 0),
                "doubles": s.get("doubles", 0), "triples": s.get("triples", 0),
                "homeRuns": s.get("homeRuns", 0), "rbi": s.get("rbi", 0),
                "runs": s.get("runs", 0), "stolenBases": s.get("stolenBases", 0),
                "baseOnBalls": bb, "strikeOuts": so,
                "gamesPlayed": s.get("gamesPlayed", 0),
                # hitting computed
                "xbh": xbh, "iso": iso,
                "bbPct": bb_pct, "kPct": k_pct,
                # pitching raw
                "era": s.get("era"), "inningsPitched": s.get("inningsPitched"),
                "ipTrue": ip_true,
                "wins": s.get("wins", 0), "losses": s.get("losses", 0),
                "saves": s.get("saves", 0), "blownSaves": s.get("blownSaves", 0),
                "pitcherStrikeOuts": pit_k, "walks": pit_bb,
                "whip": s.get("whip"), "earnedRuns": pit_er,
                "hitsAllowed": pit_h, "homeRunsAllowed": pit_hr,
                "hitBatsmen": s.get("hitBatsmen", 0),
                "battersFaced": s.get("battersFaced", 0),
                # pitching computed
                "k9": k9, "bb9": bb9, "h9": h9, "hr9": hr9,
                "kbb": kbb, "fip": fip,
            },
            "reasons": [],
        }

    # ── Parallel fetches ──
    fetch_jobs = [
        # hitting
        ("hitting", "homeRuns",    "desc"),
        ("hitting", "avg",         "desc"),
        ("hitting", "rbi",         "desc"),
        ("hitting", "stolenBases", "desc"),
        ("hitting", "ops",         "desc"),
        ("hitting", "slg",         "desc"),
        ("hitting", "obp",         "desc"),
        ("hitting", "hits",        "desc"),
        ("hitting", "runs",        "desc"),
        ("hitting", "doubles",     "desc"),
        ("hitting", "avg",         "asc"),
        ("hitting", "strikeOuts",  "desc"),
        ("hitting", "ops",         "asc"),
        ("hitting", "obp",         "asc"),
        # pitching
        ("pitching", "era",         "asc"),
        ("pitching", "strikeOuts",  "desc"),
        ("pitching", "whip",        "asc"),
        ("pitching", "saves",       "desc"),
        ("pitching", "era",         "desc"),
        ("pitching", "whip",        "desc"),
        ("pitching", "baseOnBalls", "desc"),
        ("pitching", "hits",        "desc"),
        ("pitching", "homeRuns",    "desc"),
    ]

    with concurrent.futures.ThreadPoolExecutor(max_workers=12) as ex:
        futures = {ex.submit(fetch, g, s, o): (g, s, o) for g, s, o in fetch_jobs}
        fetched = {}
        for fut in concurrent.futures.as_completed(futures):
            fetched[futures[fut]] = fut.result()

    def sp(group, sort_stat, order):
        return fetched.get((group, sort_stat, order), [])

    def apply_criteria(criteria_list):
        seen = {}
        for split_list, check, label_fn in criteria_list:
            for split in split_list:
                p = fmt(split)
                if not p["id"] or not check(p):
                    continue
                label = label_fn(p)
                if p["id"] in seen:
                    if label not in seen[p["id"]]["reasons"]:
                        seen[p["id"]]["reasons"].append(label)
                else:
                    p["reasons"] = [label]
                    seen[p["id"]] = p
        return seen

    # Global minimums (14-day window)
    HIT_MIN_PA  = 25   # plate appearances (ab + bb proxy)
    HIT_MIN_PA_STRICT = 35  # for cold / small-sample checks
    PIT_MIN_IP  = 7    # innings pitched for starters
    PIT_MIN_IP_REL = 4  # relievers / closers

    # ─────────────────────────────────────────────
    # HOT HITTERS — 15 criteria (14-day thresholds)
    # ─────────────────────────────────────────────
    hot_hit_criteria = [
        # Power
        (sp("hitting","homeRuns","desc"),   lambda p: p["stat"]["homeRuns"] >= 5 and p["stat"]["pa"] >= HIT_MIN_PA,
         lambda p: f"{p['stat']['homeRuns']} HR"),
        (sp("hitting","slg","desc"),        lambda p: _f(p["stat"]["slg"]) >= 0.650 and p["stat"]["pa"] >= HIT_MIN_PA,
         lambda p: f".{int(_f(p['stat']['slg'])*1000):03d} SLG"),
        (sp("hitting","avg","desc"),        lambda p: p["stat"]["iso"] >= 0.280 and p["stat"]["pa"] >= HIT_MIN_PA,
         lambda p: f".{int(p['stat']['iso']*1000):03d} ISO"),
        (sp("hitting","doubles","desc"),    lambda p: p["stat"]["doubles"] >= 5 and p["stat"]["pa"] >= HIT_MIN_PA,
         lambda p: f"{p['stat']['doubles']} 2B"),
        (sp("hitting","hits","desc"),       lambda p: p["stat"]["triples"] >= 2 and p["stat"]["pa"] >= HIT_MIN_PA,
         lambda p: f"{p['stat']['triples']} 3B"),
        # Contact / Average
        (sp("hitting","avg","desc"),        lambda p: _f(p["stat"]["avg"]) >= 0.350 and p["stat"]["pa"] >= HIT_MIN_PA,
         lambda p: f".{int(_f(p['stat']['avg'])*1000):03d} AVG"),
        (sp("hitting","hits","desc"),       lambda p: p["stat"]["hits"] >= 20 and p["stat"]["pa"] >= HIT_MIN_PA,
         lambda p: f"{p['stat']['hits']} H"),
        # Production
        (sp("hitting","rbi","desc"),        lambda p: p["stat"]["rbi"] >= 14 and p["stat"]["pa"] >= HIT_MIN_PA,
         lambda p: f"{p['stat']['rbi']} RBI"),
        (sp("hitting","runs","desc"),       lambda p: p["stat"]["runs"] >= 10 and p["stat"]["pa"] >= HIT_MIN_PA,
         lambda p: f"{p['stat']['runs']} R"),
        (sp("hitting","hits","desc"),       lambda p: p["stat"]["xbh"] >= 8 and p["stat"]["pa"] >= HIT_MIN_PA,
         lambda p: f"{p['stat']['xbh']} XBH"),
        # Speed
        (sp("hitting","stolenBases","desc"),lambda p: p["stat"]["stolenBases"] >= 5 and p["stat"]["pa"] >= HIT_MIN_PA,
         lambda p: f"{p['stat']['stolenBases']} SB"),
        # Advanced / On-base
        (sp("hitting","ops","desc"),        lambda p: _f(p["stat"]["ops"]) >= 0.950 and p["stat"]["pa"] >= HIT_MIN_PA,
         lambda p: f"{_f(p['stat']['ops']):.3f} OPS"),
        (sp("hitting","obp","desc"),        lambda p: _f(p["stat"]["obp"]) >= 0.430 and p["stat"]["pa"] >= HIT_MIN_PA,
         lambda p: f".{int(_f(p['stat']['obp'])*1000):03d} OBP"),
        (sp("hitting","obp","desc"),        lambda p: p["stat"]["bbPct"] is not None and p["stat"]["bbPct"] >= 0.180 and p["stat"]["pa"] >= HIT_MIN_PA,
         lambda p: f"{int(p['stat']['bbPct']*100)}% BB%"),
        (sp("hitting","avg","desc"),        lambda p: p["stat"]["kPct"] is not None and p["stat"]["kPct"] <= 0.100 and p["stat"]["pa"] >= HIT_MIN_PA_STRICT,
         lambda p: f"{int(p['stat']['kPct']*100)}% K%"),
    ]

    hot_hit_seen = apply_criteria(hot_hit_criteria)
    result["hot_hitters"] = sorted(
        hot_hit_seen.values(),
        key=lambda x: (-len(x["reasons"]), -x["stat"]["homeRuns"], -_f(x["stat"]["ops"]))
    )

    # ─────────────────────────────────────────────
    # COLD HITTERS — 9 criteria (14-day thresholds)
    # ─────────────────────────────────────────────
    cold_hit_criteria = [
        (sp("hitting","avg","asc"),         lambda p: _f(p["stat"]["avg"],1) < 0.180 and p["stat"]["pa"] >= HIT_MIN_PA_STRICT,
         lambda p: f".{int(_f(p['stat']['avg'])*1000):03d} AVG"),
        (sp("hitting","strikeOuts","desc"), lambda p: p["stat"]["strikeOuts"] >= 20 and p["stat"]["pa"] >= HIT_MIN_PA_STRICT,
         lambda p: f"{p['stat']['strikeOuts']} K"),
        (sp("hitting","ops","asc"),         lambda p: _f(p["stat"]["ops"],1) < 0.500 and p["stat"]["pa"] >= HIT_MIN_PA_STRICT,
         lambda p: f"{_f(p['stat']['ops']):.3f} OPS"),
        (sp("hitting","avg","asc"),         lambda p: _f(p["stat"]["slg"],1) < 0.250 and p["stat"]["pa"] >= HIT_MIN_PA_STRICT,
         lambda p: f".{int(_f(p['stat']['slg'])*1000):03d} SLG"),
        (sp("hitting","avg","asc"),         lambda p: p["stat"]["xbh"] <= 1 and p["stat"]["pa"] >= HIT_MIN_PA_STRICT,
         lambda p: "0 XBH"),
        (sp("hitting","obp","asc"),         lambda p: _f(p["stat"]["obp"],1) < 0.220 and p["stat"]["pa"] >= HIT_MIN_PA_STRICT,
         lambda p: f".{int(_f(p['stat']['obp'])*1000):03d} OBP"),
        (sp("hitting","avg","asc"),         lambda p: p["stat"]["iso"] < 0.080 and p["stat"]["pa"] >= HIT_MIN_PA_STRICT,
         lambda p: "0 Power"),
        (sp("hitting","strikeOuts","desc"), lambda p: p["stat"]["kPct"] is not None and p["stat"]["kPct"] >= 0.350 and p["stat"]["pa"] >= HIT_MIN_PA_STRICT,
         lambda p: f"{int(p['stat']['kPct']*100)}% K%"),
        (sp("hitting","avg","asc"),         lambda p: p["stat"]["baseOnBalls"] <= 1 and p["stat"]["pa"] >= 40,
         lambda p: "0 BB"),
    ]

    cold_hit_seen = apply_criteria(cold_hit_criteria)
    result["cold_hitters"] = sorted(
        cold_hit_seen.values(),
        key=lambda x: (-len(x["reasons"]), _f(x["stat"]["avg"],1), -x["stat"]["strikeOuts"])
    )[:35]

    # ─────────────────────────────────────────────
    # HOT PITCHERS — 12 criteria (14-day thresholds)
    # ─────────────────────────────────────────────
    def ipv(p):
        return p["stat"]["ipTrue"]

    hot_pit_criteria = [
        # ERA / runs
        (sp("pitching","era","asc"),        lambda p: _f(p["stat"]["era"],99) <= 2.50 and ipv(p) >= PIT_MIN_IP,
         lambda p: f"{_f(p['stat']['era']):.2f} ERA"),
        (sp("pitching","era","asc"),        lambda p: p["stat"]["earnedRuns"] == 0 and ipv(p) >= PIT_MIN_IP,
         lambda p: "0 ER"),
        (sp("pitching","era","asc"),        lambda p: p["stat"]["fip"] is not None and p["stat"]["fip"] <= 2.50 and ipv(p) >= PIT_MIN_IP,
         lambda p: f"{p['stat']['fip']:.2f} FIP"),
        # WHIP / baserunners
        (sp("pitching","whip","asc"),       lambda p: _f(p["stat"]["whip"],99) <= 0.90 and ipv(p) >= PIT_MIN_IP,
         lambda p: f"{_f(p['stat']['whip']):.2f} WHIP"),
        (sp("pitching","whip","asc"),       lambda p: p["stat"]["h9"] is not None and p["stat"]["h9"] <= 5.5 and ipv(p) >= PIT_MIN_IP,
         lambda p: f"{p['stat']['h9']:.1f} H/9"),
        (sp("pitching","whip","asc"),       lambda p: p["stat"]["homeRunsAllowed"] == 0 and ipv(p) >= PIT_MIN_IP,
         lambda p: "0 HR allowed"),
        # Strikeouts
        (sp("pitching","strikeOuts","desc"),lambda p: p["stat"]["pitcherStrikeOuts"] >= 18 and ipv(p) >= PIT_MIN_IP_REL,
         lambda p: f"{p['stat']['pitcherStrikeOuts']} K"),
        (sp("pitching","strikeOuts","desc"),lambda p: p["stat"]["k9"] is not None and p["stat"]["k9"] >= 12.0 and ipv(p) >= PIT_MIN_IP,
         lambda p: f"{p['stat']['k9']:.1f} K/9"),
        (sp("pitching","strikeOuts","desc"),lambda p: p["stat"]["kbb"] is not None and p["stat"]["kbb"] >= 4.5 and ipv(p) >= PIT_MIN_IP,
         lambda p: f"{p['stat']['kbb']:.1f} K/BB"),
        # Control
        (sp("pitching","whip","asc"),       lambda p: p["stat"]["bb9"] is not None and p["stat"]["bb9"] <= 1.5 and ipv(p) >= PIT_MIN_IP,
         lambda p: f"{p['stat']['bb9']:.1f} BB/9"),
        # Saves
        (sp("pitching","saves","desc"),     lambda p: p["stat"]["saves"] >= 3 and ipv(p) >= PIT_MIN_IP_REL,
         lambda p: f"{p['stat']['saves']} SV"),
        (sp("pitching","saves","desc"),     lambda p: p["stat"]["saves"] >= 2 and _f(p["stat"]["era"],99) == 0 and ipv(p) >= PIT_MIN_IP_REL,
         lambda p: "Perfect closer"),
    ]

    hot_pit_seen = apply_criteria(hot_pit_criteria)
    result["hot_pitchers"] = sorted(
        hot_pit_seen.values(),
        key=lambda x: (-len(x["reasons"]), _f(x["stat"]["era"],99), -x["stat"]["pitcherStrikeOuts"])
    )

    # ─────────────────────────────────────────────
    # COLD PITCHERS — 10 criteria (14-day thresholds)
    # ─────────────────────────────────────────────
    cold_pit_criteria = [
        (sp("pitching","era","desc"),         lambda p: _f(p["stat"]["era"]) >= 6.00 and ipv(p) >= PIT_MIN_IP,
         lambda p: f"{_f(p['stat']['era']):.2f} ERA"),
        (sp("pitching","whip","desc"),        lambda p: _f(p["stat"]["whip"]) >= 1.90 and ipv(p) >= PIT_MIN_IP,
         lambda p: f"{_f(p['stat']['whip']):.2f} WHIP"),
        (sp("pitching","baseOnBalls","desc"), lambda p: p["stat"]["walks"] >= 10 and ipv(p) >= PIT_MIN_IP_REL,
         lambda p: f"{p['stat']['walks']} BB"),
        (sp("pitching","hits","desc"),        lambda p: p["stat"]["hitsAllowed"] >= 25 and ipv(p) >= PIT_MIN_IP,
         lambda p: f"{p['stat']['hitsAllowed']} H allowed"),
        (sp("pitching","homeRuns","desc"),    lambda p: p["stat"]["homeRunsAllowed"] >= 4 and ipv(p) >= PIT_MIN_IP,
         lambda p: f"{p['stat']['homeRunsAllowed']} HR allowed"),
        (sp("pitching","era","desc"),         lambda p: p["stat"]["blownSaves"] >= 2 and ipv(p) >= PIT_MIN_IP_REL,
         lambda p: f"{p['stat']['blownSaves']} BS"),
        (sp("pitching","era","desc"),         lambda p: p["stat"]["hr9"] is not None and p["stat"]["hr9"] >= 2.0 and ipv(p) >= PIT_MIN_IP,
         lambda p: f"{p['stat']['hr9']:.1f} HR/9"),
        (sp("pitching","whip","desc"),        lambda p: p["stat"]["bb9"] is not None and p["stat"]["bb9"] >= 4.5 and ipv(p) >= PIT_MIN_IP,
         lambda p: f"{p['stat']['bb9']:.1f} BB/9"),
        (sp("pitching","whip","desc"),        lambda p: p["stat"]["h9"] is not None and p["stat"]["h9"] >= 11.0 and ipv(p) >= PIT_MIN_IP,
         lambda p: f"{p['stat']['h9']:.1f} H/9"),
        (sp("pitching","era","desc"),         lambda p: p["stat"]["fip"] is not None and p["stat"]["fip"] >= 6.0 and ipv(p) >= PIT_MIN_IP,
         lambda p: f"{p['stat']['fip']:.2f} FIP"),
    ]

    cold_pit_seen = apply_criteria(cold_pit_criteria)
    result["cold_pitchers"] = sorted(
        cold_pit_seen.values(),
        key=lambda x: (-len(x["reasons"]), -_f(x["stat"]["era"]), -x["stat"]["walks"])
    )[:35]

    _hot_cold_cache[days] = {"data": result, "ts": now}
    return result


def clear_cache():
    _cache.clear()
    _savant_xstats_cache.clear()
    _savant_pitcharsenal_cache.clear()
    _savant_leaderboard_cache.clear()
    _savant_percentile_cache.clear()
    _nbc_playerurl_cache.clear()
    _nbc_news_cache.clear()
    _fangraphs_cache.clear()
    _milb_cache.clear()
    _video_cache.clear()
    _probable_cache.clear()
