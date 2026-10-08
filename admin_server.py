"""
Admin dashboard server - localhost only, do NOT push to GitHub.
Runs on port 5001 separately from the main app.
"""

from flask import Flask, jsonify, send_from_directory, request
from flask_cors import CORS
from supabase import create_client
from dotenv import load_dotenv
from functools import wraps
import os, time, hashlib, secrets

load_dotenv()

app = Flask(__name__, static_folder=".", static_url_path="")
CORS(app)

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_SERVICE_KEY = os.getenv("SUPABASE_SERVICE_KEY")
supabase = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)

# ── Auth ───────────────────────────────────────────────────────────────────────
PASSWORD_HASH = hashlib.sha256("2Xchange!".encode()).hexdigest()
_sessions = set()   # valid tokens in memory

def require_admin(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        token = request.headers.get("X-Admin-Token", "")
        if token not in _sessions:
            return jsonify({"error": "Unauthorized"}), 401
        return f(*args, **kwargs)
    return wrapper

@app.route("/api/admin/login", methods=["POST"])
def login():
    data = request.get_json(silent=True) or {}
    pw = data.get("password", "")
    if hashlib.sha256(pw.encode()).hexdigest() != PASSWORD_HASH:
        return jsonify({"error": "Wrong password"}), 403
    token = secrets.token_hex(32)
    _sessions.add(token)
    return jsonify({"token": token})

@app.route("/api/admin/logout", methods=["POST"])
def logout():
    token = request.headers.get("X-Admin-Token", "")
    _sessions.discard(token)
    return jsonify({"ok": True})

# ── Cache ──────────────────────────────────────────────────────────────────────
_cache = {}
CACHE_TTL = 60

def cache_get(key):
    e = _cache.get(key)
    if e and time.time() - e["ts"] < CACHE_TTL:
        return e["data"]
    return None

def cache_set(key, data):
    _cache[key] = {"data": data, "ts": time.time()}

def cache_bust(key):
    _cache.pop(key, None)

def fetch_all_auth_users():
    cached = cache_get("auth_users")
    if cached is not None:
        return cached
    all_users, page = [], 1
    while True:
        batch = supabase.auth.admin.list_users(page=page, per_page=1000)
        batch = batch if isinstance(batch, list) else list(batch)
        if not batch:
            break
        all_users.extend(batch)
        if len(batch) < 1000:
            break
        page += 1
    cache_set("auth_users", all_users)
    return all_users

# ── Static ─────────────────────────────────────────────────────────────────────
@app.route("/")
def index():
    return send_from_directory(".", "admin.html")

# ── Users ──────────────────────────────────────────────────────────────────────
@app.route("/api/admin/users")
@require_admin
def list_users():
    try:
        users = fetch_all_auth_users()
        profiles_res = supabase.table("profiles").select("*").execute()
        profiles = {p["id"]: p for p in (profiles_res.data or [])}
        result = []
        for u in users:
            uid = u.id
            p = profiles.get(uid, {})
            result.append({
                "id": uid,
                "email": u.email,
                "created_at": str(u.created_at),
                "last_sign_in_at": str(u.last_sign_in_at) if u.last_sign_in_at else None,
                "full_name": p.get("full_name", ""),
                "school_board": p.get("school_board", ""),
                "grade_major": p.get("grade_major", ""),
                "bio_message": p.get("bio_message", ""),
                "avatar_url": p.get("avatar_url", ""),
            })
        result.sort(key=lambda x: x["created_at"], reverse=True)
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/admin/users/<user_id>", methods=["DELETE"])
@require_admin
def delete_user(user_id):
    try:
        supabase.auth.admin.delete_user(user_id)
        cache_bust("auth_users")
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ── Files ──────────────────────────────────────────────────────────────────────
@app.route("/api/admin/users/<user_id>/files")
@require_admin
def user_files(user_id):
    try:
        res = supabase.table("files").select("id,filename,size,uploaded_at,text_content") \
            .eq("user_id", user_id).order("uploaded_at", desc=True).execute()
        return jsonify(res.data or [])
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/admin/files/<file_id>", methods=["DELETE"])
@require_admin
def delete_file(file_id):
    try:
        supabase.table("files").delete().eq("id", file_id).execute()
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ── Chats ──────────────────────────────────────────────────────────────────────
@app.route("/api/admin/users/<user_id>/chats")
@require_admin
def user_chat_sessions(user_id):
    try:
        sessions_res = supabase.table("chat_sessions").select("id,name,file_ids,created_at") \
            .eq("user_id", user_id).order("created_at", desc=True).execute()
        sessions = sessions_res.data or []
        if not sessions:
            return jsonify([])
        session_ids = [s["id"] for s in sessions]
        msgs_res = supabase.table("chat_messages").select("session_id") \
            .in_("session_id", session_ids).execute()
        counts = {}
        for m in (msgs_res.data or []):
            counts[m["session_id"]] = counts.get(m["session_id"], 0) + 1
        for s in sessions:
            s["message_count"] = counts.get(s["id"], 0)
        return jsonify(sessions)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/admin/users/<user_id>/chats/<session_id>")
@require_admin
def chat_messages(user_id, session_id):
    try:
        res = supabase.table("chat_messages").select("role,content,created_at") \
            .eq("session_id", session_id).order("created_at").execute()
        return jsonify(res.data or [])
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/admin/chats/<session_id>", methods=["DELETE"])
@require_admin
def delete_chat_session(session_id):
    try:
        supabase.table("chat_messages").delete().eq("session_id", session_id).execute()
        supabase.table("chat_sessions").delete().eq("id", session_id).execute()
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ── Outputs ────────────────────────────────────────────────────────────────────
@app.route("/api/admin/users/<user_id>/outputs")
@require_admin
def user_outputs(user_id):
    try:
        res = supabase.table("saved_outputs").select("id,type,content,created_at") \
            .eq("user_id", user_id).order("created_at", desc=True).execute()
        return jsonify(res.data or [])
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/admin/outputs/<output_id>", methods=["DELETE"])
@require_admin
def delete_output(output_id):
    try:
        supabase.table("saved_outputs").delete().eq("id", output_id).execute()
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ── Stats ──────────────────────────────────────────────────────────────────────
@app.route("/api/admin/stats")
@require_admin
def stats():
    try:
        total_users = len(fetch_all_auth_users())
        files_res = supabase.table("files").select("id", count="exact").execute()
        sessions_res = supabase.table("chat_sessions").select("id", count="exact").execute()
        messages_res = supabase.table("chat_messages").select("id", count="exact").execute()
        return jsonify({
            "total_users": total_users,
            "total_files": files_res.count or 0,
            "total_sessions": sessions_res.count or 0,
            "total_messages": messages_res.count or 0,
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    print("Admin dashboard running at http://localhost:5001")
    print("WARNING: Keep this server local — never expose publicly.")
    app.run(port=5001, debug=True)
