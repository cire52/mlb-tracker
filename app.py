from flask import Flask, jsonify, request, render_template, session
import json, os, concurrent.futures, threading, re, secrets, time
from werkzeug.security import generate_password_hash, check_password_hash
from mlb_api import (search_players, get_game_log, get_player_info, clear_cache,
                     get_season_totals, get_player_transactions, get_xstats, get_pitch_mix,
                     get_statcast, get_nbc_news, get_career_stats, get_minor_league_stats,
                     get_schedule, get_splits, get_fangraphs_stats, import_fantrax_url,
                     get_player_videos)

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "clubhouse-dev-key-please-set-SECRET_KEY-env")
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["SESSION_COOKIE_SECURE"] = os.environ.get("RAILWAY_ENVIRONMENT") is not None

# Use /tmp for writable storage on cloud platforms, fallback to local
STORAGE_DIR = os.environ.get("STORAGE_DIR", ".")
TRACKED_FILE = os.path.join(STORAGE_DIR, "tracked_players.json")
BACKUP_FILE = TRACKED_FILE + ".bak"
USERS_FILE = os.path.join(STORAGE_DIR, "users.json")
_save_lock = threading.Lock()
_users_lock = threading.Lock()

# Warn at startup if data won't persist across redeploys
if STORAGE_DIR == ".":
    print("WARNING: STORAGE_DIR not set — data stored in app directory and will be lost on redeploy. Set STORAGE_DIR to a mounted volume path.")
else:
    print(f"INFO: Storage directory: {STORAGE_DIR}")


# ── User storage ──────────────────────────────────────────────────────────────

def load_users():
    if os.path.exists(USERS_FILE):
        try:
            with open(USERS_FILE) as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def save_users(users):
    tmp = USERS_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(users, f, indent=2)
    os.replace(tmp, USERS_FILE)


# ── Auth routes ───────────────────────────────────────────────────────────────

def send_reset_email(to_email, reset_url):
    api_key = os.environ.get("RESEND_API_KEY")
    from_email = os.environ.get("FROM_EMAIL", "The Clubhouse <noreply@theclubhouse.app>")
    if not api_key:
        print(f"[reset] RESEND_API_KEY not set — reset URL: {reset_url}")
        return False, "RESEND_API_KEY not configured"
    try:
        import requests as req
        r = req.post(
            "https://api.resend.com/emails",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={
                "from": from_email,
                "to": [to_email],
                "subject": "Reset your Clubhouse password",
                "html": f"""
                <div style="font-family:sans-serif;max-width:480px;margin:0 auto;padding:32px">
                  <h2 style="color:#c8102e">⚾ The Clubhouse</h2>
                  <p>Someone requested a password reset for your account.</p>
                  <p>Click the button below to reset your password. This link expires in <strong>1 hour</strong>.</p>
                  <a href="{reset_url}" style="display:inline-block;margin:20px 0;padding:12px 28px;background:#c8102e;color:#fff;text-decoration:none;border-radius:8px;font-weight:700">Reset Password</a>
                  <p style="color:#888;font-size:0.85rem">If you didn't request this, you can ignore this email.</p>
                </div>"""
            },
            timeout=10
        )
        print(f"[reset] Resend status={r.status_code} body={r.text[:300]}")
        if not r.ok:
            return False, r.text
        return True, None
    except Exception as e:
        print(f"[reset] Email send failed: {e}")
        return False, str(e)


@app.route("/api/auth/register", methods=["POST"])
def auth_register():
    data = request.json or {}
    username = (data.get("username") or "").strip().lower()
    password = data.get("password", "")
    email = (data.get("email") or "").strip().lower()
    if not re.match(r'^[a-z0-9_]{3,20}$', username):
        return jsonify({"error": "Username must be 3–20 characters (letters, numbers, underscores)"}), 400
    if len(password) < 6:
        return jsonify({"error": "Password must be at least 6 characters"}), 400
    with _users_lock:
        users = load_users()
        if username in users:
            return jsonify({"error": "Username already taken"}), 409
        import uuid
        uid = "user_" + uuid.uuid4().hex[:16]
        users[username] = {"password_hash": generate_password_hash(password), "uid": uid, "email": email}
        save_users(users)
    session["username"] = username
    return jsonify({"ok": True, "username": username, "uid": uid})


@app.route("/api/auth/login", methods=["POST"])
def auth_login():
    data = request.json or {}
    username = (data.get("username") or "").strip().lower()
    password = data.get("password", "")
    users = load_users()
    user = users.get(username)
    if not user or not check_password_hash(user["password_hash"], password):
        return jsonify({"error": "Invalid username or password"}), 401
    session["username"] = username
    return jsonify({"ok": True, "username": username, "uid": user["uid"]})


@app.route("/api/auth/logout", methods=["POST"])
def auth_logout():
    session.pop("username", None)
    return jsonify({"ok": True})


@app.route("/api/auth/me")
def auth_me():
    username = session.get("username")
    if not username:
        return jsonify({})
    users = load_users()
    user = users.get(username)
    if not user:
        session.pop("username", None)
        return jsonify({})
    return jsonify({"username": username, "uid": user["uid"], "email": user.get("email", "")})


@app.route("/api/admin/reset-password", methods=["POST"])
def admin_reset_password():
    admin_key = os.environ.get("ADMIN_KEY")
    if not admin_key or request.json.get("admin_key") != admin_key:
        return jsonify({"error": "Unauthorized"}), 401
    username = (request.json.get("username") or "").strip().lower()
    new_password = request.json.get("password", "")
    if not username or len(new_password) < 6:
        return jsonify({"error": "Username and password (min 6 chars) required"}), 400
    with _users_lock:
        users = load_users()
        if username not in users:
            return jsonify({"error": f"User '{username}' not found"}), 404
        users[username]["password_hash"] = generate_password_hash(new_password)
        users[username].pop("reset_token", None)
        users[username].pop("reset_expires", None)
        save_users(users)
    return jsonify({"ok": True, "message": f"Password for '{username}' has been reset"})


@app.route("/api/auth/forgot", methods=["POST"])
def auth_forgot():
    email = (request.json or {}).get("email", "").strip().lower()
    if not email:
        return jsonify({"error": "Please enter your email address"}), 400
    with _users_lock:
        users = load_users()
        user_entry = next(((u, d) for u, d in users.items() if d.get("email", "").lower() == email), None)
        if not user_entry:
            # Don't reveal whether email exists
            return jsonify({"ok": True})
        username, user = user_entry
        token = secrets.token_urlsafe(32)
        user["reset_token"] = token
        user["reset_expires"] = time.time() + 3600  # 1 hour
        save_users(users)
    app_url = os.environ.get("APP_URL", request.host_url.rstrip("/"))
    reset_url = f"{app_url}/?reset={token}"
    ok, err = send_reset_email(email, reset_url)
    if not ok:
        print(f"[reset] Failed to send to {email}: {err}")
        return jsonify({"ok": False, "error": f"Email delivery failed: {err}"}), 500
    return jsonify({"ok": True})


@app.route("/api/auth/reset", methods=["POST"])
def auth_reset():
    data = request.json or {}
    token = data.get("token", "")
    password = data.get("password", "")
    if len(password) < 6:
        return jsonify({"error": "Password must be at least 6 characters"}), 400
    with _users_lock:
        users = load_users()
        user_entry = next(((u, d) for u, d in users.items()
                           if d.get("reset_token") == token and d.get("reset_expires", 0) > time.time()), None)
        if not user_entry:
            return jsonify({"error": "Reset link is invalid or has expired"}), 400
        username, user = user_entry
        user["password_hash"] = generate_password_hash(password)
        user.pop("reset_token", None)
        user.pop("reset_expires", None)
        save_users(users)
    session["username"] = username
    return jsonify({"ok": True, "username": username, "uid": users[username]["uid"]})


@app.route("/api/auth/change-password", methods=["POST"])
def auth_change_password():
    username = session.get("username")
    if not username:
        return jsonify({"error": "Not logged in"}), 401
    data = request.json or {}
    current = data.get("current_password", "")
    new_pw = data.get("new_password", "")
    if len(new_pw) < 6:
        return jsonify({"error": "New password must be at least 6 characters"}), 400
    with _users_lock:
        users = load_users()
        user = users.get(username)
        if not user or not check_password_hash(user["password_hash"], current):
            return jsonify({"error": "Current password is incorrect"}), 400
        user["password_hash"] = generate_password_hash(new_pw)
        save_users(users)
    return jsonify({"ok": True})


@app.route("/api/auth/update-email", methods=["POST"])
def auth_update_email():
    username = session.get("username")
    if not username:
        return jsonify({"error": "Not logged in"}), 401
    email = (request.json or {}).get("email", "").strip().lower()
    with _users_lock:
        users = load_users()
        if username not in users:
            return jsonify({"error": "User not found"}), 404
        users[username]["email"] = email
        save_users(users)
    return jsonify({"ok": True})


def load_tracked(uid=None):
    for path in (TRACKED_FILE, BACKUP_FILE):
        if path and os.path.exists(path):
            try:
                with open(path) as f:
                    data = json.load(f)
                if isinstance(data, list):
                    data = {}
                    _save_all(data)
                return data.get(uid, []) if uid else data
            except Exception:
                continue
    return [] if uid else {}


def _save_all(data):
    # Write to temp file first, then rename for atomicity
    tmp = TRACKED_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, TRACKED_FILE)
    # Keep a backup copy
    try:
        import shutil
        shutil.copy2(TRACKED_FILE, BACKUP_FILE)
    except Exception:
        pass


def save_tracked(uid, players):
    with _save_lock:
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


@app.route("/api/videos/<int:player_id>")
def player_videos(player_id):
    season = request.args.get("season")
    kwargs = {"season": int(season)} if season else {}
    return jsonify(get_player_videos(player_id, **kwargs))


@app.route("/api/refresh", methods=["POST"])
def refresh():
    clear_cache()
    return jsonify({"ok": True})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(debug=False, host="0.0.0.0", port=port)
