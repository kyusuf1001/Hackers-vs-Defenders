# app.py
from flask import Flask, render_template, request, session, jsonify, redirect, url_for
from flask_socketio import SocketIO, emit
import secrets

app = Flask(__name__)
app.config["SECRET_KEY"] = "dev-secret"  # replace with .env for production
socketio = SocketIO(app, cors_allowed_origins="*")

# In-memory lobby state (dev only)
# Maps socket id -> role (None, "hacker", "defender")
CONNECTED = {}   # sid -> {"role": None or "hacker"/"defender"}
# Role owner: 'hacker' -> sid or None, 'defender' -> sid or None
ROLE_OWNER = {"hacker": None, "defender": None}
MAX_PLAYERS = 2


# ---------- helper broadcasts ----------
def broadcast_player_count():
    count = len(CONNECTED)
    socketio.emit("player_count", {"count": count, "max": MAX_PLAYERS})


def broadcast_roles():
    # send who has taken which role (for UI)
    owners = {
        "hacker": True if ROLE_OWNER["hacker"] else False,
        "defender": True if ROLE_OWNER["defender"] else False
    }
    socketio.emit("roles_update", owners)


def all_roles_taken():
    return ROLE_OWNER["hacker"] is not None and ROLE_OWNER["defender"] is not None


# ---------- HTTP routes ----------
@app.route("/")
def index():
    # lobby
    return render_template("base.html")


@app.route("/set-role", methods=["POST"])
def set_role():
    """
    Called after a successful socket 'choose_role' lock.
    This saves the role in the Flask session so /hacker and /defender can
    protect pages (server-side).
    Request JSON: {"role": "hacker"|"defender"}
    """
    data = request.get_json(silent=True) or {}
    role = data.get("role")
    if role not in ("hacker", "defender"):
        return jsonify({"ok": False, "error": "invalid role"}), 400

    # ensure we actually have a session player id
    if "player_id" not in session:
        session["player_id"] = secrets.token_urlsafe(8)

    session["role"] = role
    # optional: persist more player info if needed

    # If both roles taken, emit roles_ready so clients can move on
    if all_roles_taken():
        socketio.emit("roles_ready", {"ok": True})

    return jsonify({"ok": True, "role": role})


@app.route("/hacker")
def hacker_page():
    # allow only if session role is hacker
    if session.get("role") != "hacker":
        return redirect(url_for("index"))
    return render_template("hacker.html")


@app.route("/defender")
def defender_page():
    if session.get("role") != "defender":
        return redirect(url_for("index"))
    return render_template("defender.html")


# ---------- Socket.IO event handlers ----------
@socketio.on("connect")
def handle_connect():
    sid = request.sid
    # new connection: no role assigned yet
    CONNECTED[sid] = {"role": None}
    broadcast_player_count()
    broadcast_roles()


@socketio.on("disconnect")
def handle_disconnect():
    sid = request.sid
    # If this sid owned a role, release it
    for role, owner in ROLE_OWNER.items():
        if owner == sid:
            ROLE_OWNER[role] = None
    # remove connection
    CONNECTED.pop(sid, None)
    broadcast_player_count()
    broadcast_roles()


@socketio.on("choose_role")
def handle_choose_role(data):
    """
    Client attempts to lock a role.
    data = {"role": "hacker"|"defender"}
    """
    sid = request.sid
    desired = (data or {}).get("role")
    count = len(CONNECTED)

    # Only allow choosing when there are exactly MAX_PLAYERS connected
    if count < MAX_PLAYERS:
        # not enough players yet
        emit("choose_role_failed", {"error": "waiting_for_players"})
        return

    if desired not in ("hacker", "defender"):
        emit("choose_role_failed", {"error": "invalid_role"})
        return

    # If role already taken by another sid, fail
    owner = ROLE_OWNER.get(desired)
    if owner and owner != sid:
        emit("choose_role_failed", {"error": "role_taken"})
        return

    # If this sid already owns another role, prevent (shouldn't normally happen)
    other_role = None
    for r, o in ROLE_OWNER.items():
        if o == sid and r != desired:
            other_role = r
            break
    if other_role:
        emit("choose_role_failed", {"error": "already_owns_other_role"})
        return

    # Assign role to this sid
    ROLE_OWNER[desired] = sid
    CONNECTED[sid]["role"] = desired

    # Notify the client who requested that they've successfully got the lock
    emit("choose_role_success", {"role": desired})

    # Broadcast updated roles to everyone so UIs gray the taken button
    broadcast_roles()

    # If both roles now assigned, notify everyone game can start (roles_ready)
    if all_roles_taken():
        socketio.emit("roles_ready", {"ok": True})


@socketio.on("release_role")
def handle_release_role(data):
    """Client voluntarily releases the role (if they want to): data={'role':'hacker'}"""
    sid = request.sid
    role = (data or {}).get("role")
    if role in ("hacker", "defender") and ROLE_OWNER.get(role) == sid:
        ROLE_OWNER[role] = None
        CONNECTED[sid]["role"] = None
        emit("role_released", {"role": role})
        broadcast_roles()


if __name__ == "__main__":
    # Use debug=True locally; in production choose suitable server
    socketio.run(app, debug=True)
