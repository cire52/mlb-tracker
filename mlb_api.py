import requests
import time

BASE = "https://statsapi.mlb.com/api/v1"
HEADSHOT_URL = "https://img.mlbstatic.com/mlb-photos/image/upload/d_people:generic:headshot:67:current.png/w_213,q_auto:best/v1/people/{id}/headshot/67/current"
TEAM_LOGO_URL = "https://www.mlbstatic.com/team-logos/{team_id}.svg"

_cache = {}
CACHE_TTL = 3600  # 1 hour

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
        data = _get(f"{BASE}/people/search", {"names": name, "sportId": 1})
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
        }
    except:
        return {}

def clear_cache():
    _cache.clear()
