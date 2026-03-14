import requests
from datetime import datetime

BASE = "https://statsapi.mlb.com/api/v1"
_cache = {}

def _get(url, params=None):
    key = url + str(params)
    if key in _cache:
        return _cache[key]
    r = requests.get(url, params=params, timeout=10)
    r.raise_for_status()
    data = r.json()
    _cache[key] = data
    return data

def search_players(name):
    try:
        data = _get(f"{BASE}/people/search", {"names": name, "sportId": 1})
        results = []
        for p in data.get("people", []):
            results.append({
                "id": p["id"],
                "fullName": p.get("fullName", ""),
                "position": p.get("primaryPosition", {}).get("abbreviation", ""),
                "currentTeam": p.get("currentTeam", {}).get("name", ""),
            })
        return results[:8]
    except Exception as e:
        return []

def get_game_log(player_id, group, game_type, season=2026):
    """group = 'hitting' or 'pitching', game_type = 'S' (Spring) or 'R' (Regular)"""
    try:
        key = f"gamelog_{player_id}_{group}_{game_type}_{season}"
        if key in _cache:
            return _cache[key]
        data = _get(f"{BASE}/people/{player_id}/stats", {
            "stats": "gameLog",
            "season": season,
            "group": group,
            "gameType": game_type,
        })
        games = []
        for stat_group in data.get("stats", []):
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
                    })
                else:
                    ip = s.get("inningsPitched", "0.0")
                    decision = ""
                    if s.get("wins", 0): decision = "W"
                    elif s.get("losses", 0): decision = "L"
                    elif s.get("saves", 0): decision = "SV"
                    elif s.get("holds", 0): decision = "HLD"
                    games.append({
                        "date": date,
                        "opponent": opp_str,
                        "ip": ip,
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
        _cache[key] = result
        return result
    except Exception as e:
        return []

def get_player_info(player_id):
    try:
        data = _get(f"{BASE}/people/{player_id}", {"hydrate": "currentTeam"})
        p = data.get("people", [{}])[0]
        return {
            "id": player_id,
            "fullName": p.get("fullName", ""),
            "position": p.get("primaryPosition", {}).get("abbreviation", ""),
            "currentTeam": p.get("currentTeam", {}).get("abbreviation", ""),
        }
    except:
        return {}

def clear_cache():
    _cache.clear()
