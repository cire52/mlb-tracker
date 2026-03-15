from flask import Flask, jsonify, request, render_template
import json, os, concurrent.futures
from mlb_api import (search_players, get_game_log, get_player_info, clear_cache,
                     get_season_totals, get_player_transactions, get_xstats, get_pitch_mix,
                     get_statcast, get_nbc_news, get_career_stats, get_minor_league_stats,
                     get_schedule, get_splits, get_fangraphs_stats, import_fantrax_url)

app = Flask(__name__)
# Use /tmp for writable storage on cloud platforms, fallback to local
TRACKED_FILE = os.path.join(os.environ.get("STORAGE_DIR", "."), "tracked_players.json")


def load_tracked(uid=None):
    if os.path.exists(TRACKED_FILE):
        with open(TRACKED_FILE) as f:
            data = json.load(f)
        if isinstance(data, list):  # migrate old format
            data = {}
            _save_all(data)
        return data.get(uid, []) if uid else data
    return [] if uid else {}


def _save_all(data):
    with open(TRACKED_FILE, "w") as f:
        json.dump(data, f, indent=2)


def save_tracked(uid, players):
    data = load_tracked()
    data[uid] = players
    _save_all(data)


@app.route("/")
def index():
    resp = render_template("index.html")
    from flask import make_response
    r = make_response(resp)
    r.headers["Cache-Control"] = "no-store"
    return r


@app.route("/api/search")
def search():
    q = request.args.get("q", "").strip()
    if len(q) < 2:
        return jsonify([])
    return jsonify(search_players(q))


@app.route("/api/tracked")
def get_tracked():
    uid = request.args.get("uid")
    return jsonify(load_tracked(uid))


@app.route("/api/track", methods=["POST"])
def add_player():
    data = request.json
    uid = data.get("uid")
    players = load_tracked(uid)
    team = data.get("fantasy_team", "Main")
    if not any(p["id"] == data["id"] and (p.get("fantasy_team") or "Main") == team for p in players):
        players.append({k: v for k, v in data.items() if k != "uid"})
        save_tracked(uid, players)
    return jsonify({"ok": True})


@app.route("/api/track/<int:player_id>", methods=["DELETE"])
def remove_player(player_id):
    uid = request.args.get("uid")
    team = request.args.get("team")
    if team:
        players = [p for p in load_tracked(uid) if not (p["id"] == player_id and (p.get("fantasy_team") or "Main") == team)]
    else:
        players = [p for p in load_tracked(uid) if p["id"] != player_id]
    save_tracked(uid, players)
    return jsonify({"ok": True})


@app.route("/api/stats/<int:player_id>")
def get_stats(player_id):
    game_type = request.args.get("sport", "S")  # S=Spring, R=Regular
    hitting = get_game_log(player_id, "hitting", game_type)
    pitching = get_game_log(player_id, "pitching", game_type)
    return jsonify({"hitting": hitting, "pitching": pitching})


@app.route("/api/playerinfo/<int:player_id>")
def player_info(player_id):
    return jsonify(get_player_info(player_id))


@app.route("/api/season/<int:player_id>")
def season_totals(player_id):
    return jsonify(get_season_totals(player_id))


@app.route("/api/transactions/<int:player_id>")
def player_transactions(player_id):
    return jsonify(get_player_transactions(player_id))


@app.route("/api/news/<int:player_id>")
def player_nbc_news(player_id):
    return jsonify(get_nbc_news(player_id))


@app.route("/api/xstats/<int:player_id>")
def xstats(player_id):
    return jsonify(get_xstats(player_id) or {})


@app.route("/api/statcast/<int:player_id>")
def statcast(player_id):
    year = int(request.args.get('year', 2025))
    return jsonify(get_statcast(player_id, year=year) or {})


@app.route("/api/pitchmix/<int:player_id>")
def pitch_mix(player_id):
    year = int(request.args.get('year', 2025))
    return jsonify(get_pitch_mix(player_id, year=year))


@app.route("/api/track/<int:player_id>/note", methods=["POST"])
def update_note(player_id):
    uid = request.args.get("uid")
    note = request.json.get("note", "")
    players = load_tracked(uid)
    for p in players:
        if p["id"] == player_id:
            p["note"] = note
            break
    save_tracked(uid, players)
    return jsonify({"ok": True})


@app.route("/api/track/<int:player_id>/group", methods=["POST"])
def update_group(player_id):
    uid = request.args.get("uid")
    group = request.json.get("group", "")
    players = load_tracked(uid)
    for p in players:
        if p["id"] == player_id:
            p["group"] = group
            break
    save_tracked(uid, players)
    return jsonify({"ok": True})


@app.route("/api/track/<int:player_id>/fantasy_team", methods=["POST"])
def update_fantasy_team(player_id):
    uid = request.args.get("uid")
    fantasy_team = request.json.get("fantasy_team", "Main")
    players = load_tracked(uid)
    for p in players:
        if p["id"] == player_id:
            p["fantasy_team"] = fantasy_team
            break
    save_tracked(uid, players)
    return jsonify({"ok": True})


@app.route("/api/teams/rename", methods=["POST"])
def rename_team():
    uid = request.args.get("uid")
    old_name = request.json.get("old_name", "")
    new_name = request.json.get("new_name", "")
    if not old_name or not new_name:
        return jsonify({"ok": False}), 400
    players = load_tracked(uid)
    for p in players:
        if (p.get("fantasy_team") or "Main") == old_name:
            p["fantasy_team"] = new_name
    save_tracked(uid, players)
    return jsonify({"ok": True})


@app.route("/api/career/<int:player_id>")
def career_stats(player_id):
    return jsonify(get_career_stats(player_id))


@app.route("/api/milb/<int:player_id>")
def milb_stats(player_id):
    return jsonify(get_minor_league_stats(player_id))


@app.route("/api/schedule/<int:player_id>")
def player_schedule(player_id):
    info = get_player_info(player_id)
    team_id = info.get("teamId")
    if not team_id:
        return jsonify([])
    return jsonify(get_schedule(team_id))


@app.route("/api/splits/<int:player_id>")
def player_splits(player_id):
    year = int(request.args.get("year", 2026))
    return jsonify(get_splits(player_id, year))


@app.route("/api/fangraphs/<int:player_id>")
def fangraphs(player_id):
    year = int(request.args.get("year", 2025))
    return jsonify(get_fangraphs_stats(player_id, year) or {})


@app.route("/api/import/fantrax", methods=["POST"])
def import_fantrax():
    data = request.json or {}
    url = data.get("url", "").strip()
    names = data.get("names", [])

    if url:
        result = import_fantrax_url(url)
        if "error" in result:
            return jsonify({"error": result["error"]}), 400
        names = [p["name"] for p in result.get("players", [])]

    if not names:
        return jsonify({"error": "No player names provided"}), 400

    names = names[:75]

    def search_one(name):
        results = search_players(name)
        best = results[0] if results else None
        alternatives = results[1:4] if results else []
        return {"fantrax_name": name, "mlb": best, "alternatives": alternatives}

    name_order = {n: i for i, n in enumerate(names)}
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as ex:
        futures = {ex.submit(search_one, n): n for n in names}
        players = [f.result() for f in concurrent.futures.as_completed(futures)]

    players.sort(key=lambda x: name_order.get(x["fantrax_name"], 999))
    return jsonify({"players": players})


@app.route("/api/refresh", methods=["POST"])
def refresh():
    clear_cache()
    return jsonify({"ok": True})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(debug=False, host="0.0.0.0", port=port)
