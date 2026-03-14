from flask import Flask, jsonify, request, render_template
import json, os
from mlb_api import search_players, get_game_log, get_player_info, clear_cache

app = Flask(__name__)
# Use /tmp for writable storage on cloud platforms, fallback to local
TRACKED_FILE = os.path.join(os.environ.get("STORAGE_DIR", "."), "tracked_players.json")

def load_tracked():
    if os.path.exists(TRACKED_FILE):
        with open(TRACKED_FILE) as f:
            return json.load(f)
    return []

def save_tracked(players):
    with open(TRACKED_FILE, "w") as f:
        json.dump(players, f, indent=2)

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/api/search")
def search():
    q = request.args.get("q", "").strip()
    if len(q) < 2:
        return jsonify([])
    return jsonify(search_players(q))

@app.route("/api/tracked")
def get_tracked():
    return jsonify(load_tracked())

@app.route("/api/track", methods=["POST"])
def add_player():
    data = request.json
    players = load_tracked()
    if not any(p["id"] == data["id"] for p in players):
        players.append(data)
        save_tracked(players)
    return jsonify({"ok": True})

@app.route("/api/track/<int:player_id>", methods=["DELETE"])
def remove_player(player_id):
    players = [p for p in load_tracked() if p["id"] != player_id]
    save_tracked(players)
    return jsonify({"ok": True})

@app.route("/api/stats/<int:player_id>")
def get_stats(player_id):
    game_type = request.args.get("sport", "S")  # S=Spring, R=Regular
    hitting = get_game_log(player_id, "hitting", game_type)
    pitching = get_game_log(player_id, "pitching", game_type)
    return jsonify({"hitting": hitting, "pitching": pitching})

@app.route("/api/refresh", methods=["POST"])
def refresh():
    clear_cache()
    return jsonify({"ok": True})

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(debug=False, host="0.0.0.0", port=port)
