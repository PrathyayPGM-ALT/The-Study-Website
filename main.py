import os
import uuid
import json
import subprocess
import tempfile
import sys
from pathlib import Path
from datetime import datetime

from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
from dotenv import load_dotenv
from openai import OpenAI
import pdfplumber
from docx import Document
from werkzeug.utils import secure_filename
from supabase import create_client

load_dotenv()

app = Flask(__name__)
CORS(app)

app.config["MAX_CONTENT_LENGTH"] = 16 * 1024 * 1024
UPLOAD_FOLDER = Path("uploads")
UPLOAD_FOLDER.mkdir(exist_ok=True)
ALLOWED_EXTENSIONS = {"pdf", "txt", "docx", "md"}

# Groq AI client
client = OpenAI(
    api_key=os.getenv("GROQ_API_KEY"),
    base_url="https://api.groq.com/openai/v1",
)
DEFAULT_MODEL = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")

# Supabase admin client (uses service role key for server-side operations)
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_SERVICE_KEY = os.getenv("SUPABASE_SERVICE_KEY")
supabase = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)


def get_user_from_token():
    """Extract and verify the user from the Authorization header."""
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        return None
    token = auth_header.split("Bearer ")[1]
    try:
        user_response = supabase.auth.get_user(token)
        return user_response.user
    except Exception:
        return None


def require_auth(f):
    """Decorator that requires a valid Supabase JWT."""
    from functools import wraps
    @wraps(f)
    def decorated(*args, **kwargs):
        user = get_user_from_token()
        if not user:
            return jsonify({"error": "Unauthorized"}), 401
        request.user = user
        return f(*args, **kwargs)
    return decorated


def allowed_file(filename: str) -> bool:
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


def extract_text(filepath: Path, extension: str) -> str:
    if extension == "pdf":
        text_parts = []
        with pdfplumber.open(filepath) as pdf:
            for page in pdf.pages:
                page_text = page.extract_text()
                if page_text:
                    text_parts.append(page_text)
        return "\n".join(text_parts)
    if extension == "docx":
        doc = Document(filepath)
        return "\n".join(p.text for p in doc.paragraphs if p.text.strip())
    return filepath.read_text(encoding="utf-8", errors="replace")


def build_notes_context(user_id: str, file_ids: list[str]) -> str:
    parts = []
    for fid in file_ids:
        result = supabase.table("files").select("filename, text_content").eq("id", fid).eq("user_id", user_id).execute()
        if result.data:
            row = result.data[0]
            parts.append(f"--- Notes: {row['filename']} ---\n{row['text_content']}\n")
    return "\n".join(parts)


def chat_completion(messages: list[dict], system: str = "") -> str:
    full_messages = []
    if system:
        full_messages.append({"role": "system", "content": system})
    full_messages.extend(messages)
    response = client.chat.completions.create(
        model=DEFAULT_MODEL,
        messages=full_messages,
    )
    return response.choices[0].message.content


# ════════════════════════════════════════════════════════════════════════
# USER PROFILE
# ════════════════════════════════════════════════════════════════════════

@app.route("/api/me", methods=["GET"])
@require_auth
def get_me():
    user = request.user
    result = supabase.table("profiles").select("*").eq("id", user.id).execute()
    profile = result.data[0] if result.data else {}
    return jsonify({
        "id": user.id,
        "email": user.email,
        "full_name": profile.get("full_name", ""),
        "avatar_url": profile.get("avatar_url", ""),
    })


# ════════════════════════════════════════════════════════════════════════
# FILES
# ════════════════════════════════════════════════════════════════════════

@app.route("/api/files", methods=["GET"])
@require_auth
def list_files():
    user = request.user
    result = supabase.table("files").select("id, filename, size, uploaded_at").eq("user_id", user.id).order("uploaded_at", desc=True).execute()
    return jsonify({"files": result.data or []})


@app.route("/api/files/upload", methods=["POST"])
@require_auth
def upload_file():
    user = request.user
    if "file" not in request.files:
        return jsonify({"error": "No file part in request"}), 400

    file = request.files["file"]
    if file.filename == "":
        return jsonify({"error": "No file selected"}), 400

    if not allowed_file(file.filename):
        return jsonify({"error": f"Unsupported file type. Allowed: {ALLOWED_EXTENSIONS}"}), 400

    filename = secure_filename(file.filename)
    extension = filename.rsplit(".", 1)[1].lower()
    file_id = str(uuid.uuid4())
    save_path = UPLOAD_FOLDER / f"{file_id}_{filename}"

    file.save(save_path)

    try:
        text = extract_text(save_path, extension)
    except Exception as exc:
        save_path.unlink(missing_ok=True)
        return jsonify({"error": f"Could not extract text: {exc}"}), 422

    file_size = save_path.stat().st_size

    # Store in Supabase
    supabase.table("files").insert({
        "id": file_id,
        "user_id": user.id,
        "filename": filename,
        "size": file_size,
        "text_content": text,
        "storage_path": str(save_path),
    }).execute()

    # Clean up local file after extracting text
    save_path.unlink(missing_ok=True)

    return jsonify({
        "id": file_id,
        "filename": filename,
        "size": file_size,
        "preview": text[:300] + ("..." if len(text) > 300 else ""),
    }), 201


@app.route("/api/files/<file_id>", methods=["DELETE"])
@require_auth
def delete_file(file_id: str):
    user = request.user
    supabase.table("files").delete().eq("id", file_id).eq("user_id", user.id).execute()
    return jsonify({"message": "File deleted"})


# ════════════════════════════════════════════════════════════════════════
# CHAT
# ════════════════════════════════════════════════════════════════════════

CHAT_SYSTEM = (
    "You are a helpful study assistant. "
    "When the user provides notes, use them to answer questions accurately. "
    "Be concise, clear, and educational."
)


@app.route("/api/chat/session", methods=["POST"])
@require_auth
def create_session():
    user = request.user
    body = request.get_json(silent=True) or {}
    file_ids = body.get("file_ids", [])
    system_prompt = CHAT_SYSTEM

    if file_ids:
        notes = build_notes_context(user.id, file_ids)
        system_prompt += f"\n\nThe user has shared the following study notes:\n{notes}"

    session_id = str(uuid.uuid4())
    supabase.table("chat_sessions").insert({
        "id": session_id,
        "user_id": user.id,
        "name": body.get("name", "New Chat"),
        "file_ids": file_ids,
        "system_prompt": system_prompt,
    }).execute()

    return jsonify({"session_id": session_id}), 201


@app.route("/api/chat/sessions", methods=["GET"])
@require_auth
def list_sessions():
    user = request.user
    result = supabase.table("chat_sessions").select("id, name, created_at").eq("user_id", user.id).order("created_at", desc=True).execute()
    return jsonify({"sessions": result.data or []})


@app.route("/api/chat/<session_id>", methods=["GET"])
@require_auth
def get_chat(session_id: str):
    user = request.user
    result = supabase.table("chat_messages").select("role, content, created_at").eq("session_id", session_id).eq("user_id", user.id).order("created_at").execute()
    return jsonify({"messages": result.data or []})


@app.route("/api/chat/<session_id>", methods=["POST"])
@require_auth
def send_message(session_id: str):
    user = request.user
    body = request.get_json(silent=True) or {}
    user_message = (body.get("message") or "").strip()
    if not user_message:
        return jsonify({"error": "Message is required"}), 400

    # Get session
    session_result = supabase.table("chat_sessions").select("system_prompt").eq("id", session_id).eq("user_id", user.id).execute()
    if not session_result.data:
        return jsonify({"error": "Session not found"}), 404

    system_prompt = session_result.data[0]["system_prompt"]

    # Get existing messages
    msgs_result = supabase.table("chat_messages").select("role, content").eq("session_id", session_id).order("created_at").execute()
    messages = [{"role": m["role"], "content": m["content"]} for m in (msgs_result.data or [])]
    messages.append({"role": "user", "content": user_message})

    # Save user message
    supabase.table("chat_messages").insert({
        "session_id": session_id,
        "user_id": user.id,
        "role": "user",
        "content": user_message,
    }).execute()

    try:
        reply = chat_completion(messages, system=system_prompt)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 502

    # Save assistant message
    supabase.table("chat_messages").insert({
        "session_id": session_id,
        "user_id": user.id,
        "role": "assistant",
        "content": reply,
    }).execute()

    return jsonify({"reply": reply, "message_count": len(messages) + 1})


@app.route("/api/chat/<session_id>", methods=["DELETE"])
@require_auth
def clear_chat(session_id: str):
    user = request.user
    supabase.table("chat_messages").delete().eq("session_id", session_id).eq("user_id", user.id).execute()
    return jsonify({"message": "Chat history cleared"})


# ════════════════════════════════════════════════════════════════════════
# OUTPUT GENERATION
# ════════════════════════════════════════════════════════════════════════

OUTPUT_TYPES = {"summary", "flashcards", "quiz", "key_points", "explain"}

OUTPUT_PROMPTS = {
    "summary": "Produce a thorough but concise summary of the following study notes. Use clear headings and bullet points where appropriate.",
    "flashcards": (
        "Create a set of flashcards from the following study notes. "
        "Return ONLY valid JSON, no markdown, no code fences, no extra text. "
        "Use this exact format:\n"
        '[{"front":"Question or term","back":"Answer or definition"}]\n'
        "Generate at least 10 cards."
    ),
    "quiz": (
        "Generate a multiple-choice quiz (10 questions) based on the study notes. "
        "Return ONLY valid JSON, no markdown, no code fences, no extra text. "
        "Use this exact format:\n"
        '[{"q":"Question text?","options":["A) ...","B) ...","C) ...","D) ..."],"answer":0}]\n'
        "where \"answer\" is the zero-based index of the correct option."
    ),
    "key_points": "Extract the most important key points from the following study notes. Present them as a numbered list.",
    "explain": "Explain the main concepts in the following study notes as if teaching a beginner. Use simple language and examples.",
}


@app.route("/api/output/generate", methods=["POST"])
@require_auth
def generate_output():
    user = request.user
    body = request.get_json(silent=True) or {}
    file_ids = body.get("file_ids", [])
    output_type = body.get("type", "summary").lower()
    custom_prompt = body.get("custom_prompt", "").strip()

    if not file_ids:
        return jsonify({"error": "Provide at least one file_id"}), 400
    if output_type not in OUTPUT_TYPES and not custom_prompt:
        return jsonify({"error": f"type must be one of {OUTPUT_TYPES} or supply custom_prompt"}), 400

    notes = build_notes_context(user.id, file_ids)
    if not notes.strip():
        return jsonify({"error": "No text found in the selected files"}), 422

    instruction = custom_prompt if custom_prompt else OUTPUT_PROMPTS[output_type]
    messages = [{"role": "user", "content": f"{instruction}\n\n{notes}"}]

    try:
        result = chat_completion(messages)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 502

    output_id = str(uuid.uuid4())
    supabase.table("saved_outputs").insert({
        "id": output_id,
        "user_id": user.id,
        "type": output_type if not custom_prompt else "custom",
        "file_ids": file_ids,
        "content": result,
    }).execute()

    return jsonify({
        "output_id": output_id,
        "type": output_type if not custom_prompt else "custom",
        "content": result,
    }), 201


@app.route("/api/output", methods=["GET"])
@require_auth
def list_outputs():
    user = request.user
    result = supabase.table("saved_outputs").select("id, type, file_ids, content, created_at").eq("user_id", user.id).order("created_at", desc=True).execute()
    outputs = []
    for row in (result.data or []):
        outputs.append({
            "output_id": row["id"],
            "type": row["type"],
            "file_ids": row["file_ids"],
            "created_at": row["created_at"],
            "preview": row["content"][:200] + ("..." if len(row["content"]) > 200 else ""),
        })
    return jsonify({"outputs": outputs})


@app.route("/api/output/<output_id>", methods=["GET"])
@require_auth
def get_output(output_id: str):
    user = request.user
    result = supabase.table("saved_outputs").select("*").eq("id", output_id).eq("user_id", user.id).execute()
    if not result.data:
        return jsonify({"error": "Output not found"}), 404
    row = result.data[0]
    return jsonify({"output_id": row["id"], "type": row["type"], "content": row["content"], "created_at": row["created_at"]})


@app.route("/api/output/<output_id>", methods=["DELETE"])
@require_auth
def delete_output(output_id: str):
    user = request.user
    supabase.table("saved_outputs").delete().eq("id", output_id).eq("user_id", user.id).execute()
    return jsonify({"message": "Output deleted"})



CORNELL_SYSTEM = (
    "You are a study assistant that creates Cornell Notes. "
    "Given study material, produce structured notes in the Cornell Note-Taking Method. "
    "Return ONLY valid JSON (no markdown fences, no extra text) with this exact schema:\n"
    '{\n'
    '  "title": "Topic or subject title",\n'
    '  "cues": ["question or keyword 1", "question or keyword 2", ...],\n'
    '  "notes": ["detailed notes for cue 1", "detailed notes for cue 2", ...],\n'
    '  "summary": "A concise summary paragraph covering the main ideas."\n'
    '}\n\n'
    "Rules:\n"
    "- cues and notes arrays MUST have the same length.\n"
    "- Generate at least 5 cue/note pairs.\n"
    "- Return ONLY the JSON object, nothing else."
)


@app.route("/api/cornell/generate", methods=["POST"])
@require_auth
def generate_cornell():
    user = request.user
    body = request.get_json(silent=True) or {}
    file_ids = body.get("file_ids", [])

    if not file_ids:
        return jsonify({"error": "Provide at least one file_id"}), 400

    notes = build_notes_context(user.id, file_ids)
    if not notes.strip():
        return jsonify({"error": "No text found in the selected files"}), 422

    messages = [{"role": "user", "content": f"Create Cornell Notes from the following study material:\n\n{notes}"}]

    try:
        result = chat_completion(messages, system=CORNELL_SYSTEM)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 502

    try:
        cleaned = result.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.split("\n", 1)[1] if "\n" in cleaned else cleaned[3:]
            if cleaned.endswith("```"):
                cleaned = cleaned[:-3]
            cleaned = cleaned.strip()
        cornell_data = json.loads(cleaned)
        for key in ("title", "cues", "notes", "summary"):
            if key not in cornell_data:
                raise ValueError(f"Missing key: {key}")
    except (json.JSONDecodeError, ValueError) as exc:
        output_id = str(uuid.uuid4())
        supabase.table("saved_outputs").insert({
            "id": output_id, "user_id": user.id, "type": "cornell",
            "file_ids": file_ids, "content": result, "cornell_data": None,
        }).execute()
        return jsonify({"output_id": output_id, "cornell": None, "raw": result, "parse_error": str(exc)}), 201

    output_id = str(uuid.uuid4())
    supabase.table("saved_outputs").insert({
        "id": output_id, "user_id": user.id, "type": "cornell",
        "file_ids": file_ids, "content": result, "cornell_data": cornell_data,
    }).execute()

    return jsonify({"output_id": output_id, "cornell": cornell_data}), 201


# ════════════════════════════════════════════════════════════════════════
# PLAYGROUND
# ════════════════════════════════════════════════════════════════════════

PLAYGROUND_SYSTEM = (
    "You are an expert coding and study assistant. "
    "Help the user understand concepts, debug code, explain algorithms, "
    "and answer any study-related questions. "
    "When writing code, prefer Python unless asked otherwise."
)


@app.route("/api/playground/ask", methods=["POST"])
@require_auth
def playground_ask():
    user = request.user
    body = request.get_json(silent=True) or {}
    prompt = (body.get("prompt") or "").strip()
    file_ids = body.get("file_ids", [])

    if not prompt:
        return jsonify({"error": "prompt is required"}), 400

    system = PLAYGROUND_SYSTEM
    if file_ids:
        notes = build_notes_context(user.id, file_ids)
        system += f"\n\nThe user has provided these study notes for reference:\n{notes}"

    try:
        reply = chat_completion([{"role": "user", "content": prompt}], system=system)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 502

    return jsonify({"response": reply})


@app.route("/api/playground/run", methods=["POST"])
@require_auth
def playground_run():
    body = request.get_json(silent=True) or {}
    code = body.get("code", "")
    if not code.strip():
        return jsonify({"error": "code is required"}), 400

    with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False, encoding="utf-8") as tmp:
        tmp.write(code)
        tmp_path = tmp.name

    try:
        proc = subprocess.run(
            [sys.executable, tmp_path],
            capture_output=True, text=True, timeout=10,
        )
        return jsonify({"stdout": proc.stdout, "stderr": proc.stderr, "exit_code": proc.returncode})
    except subprocess.TimeoutExpired:
        return jsonify({"error": "Execution timed out (10 s limit)"}), 408
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500
    finally:
        Path(tmp_path).unlink(missing_ok=True)


@app.route("/api/playground/explain", methods=["POST"])
@require_auth
def playground_explain():
    body = request.get_json(silent=True) or {}
    code = (body.get("code") or "").strip()
    language = body.get("language", "Python")

    if not code:
        return jsonify({"error": "code is required"}), 400

    prompt = (
        f"Explain the following {language} code step-by-step in simple terms. "
        f"Mention what each section does and highlight any important concepts.\n\n"
        f"```{language.lower()}\n{code}\n```"
    )

    try:
        reply = chat_completion([{"role": "user", "content": prompt}], system=PLAYGROUND_SYSTEM)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 502

    return jsonify({"explanation": reply})



@app.route("/api/config", methods=["GET"])
def get_config():
    """Return public Supabase config for the frontend."""
    return jsonify({
        "supabase_url": SUPABASE_URL,
        "supabase_anon_key": os.getenv("SUPABASE_ANON_KEY"),
    })


@app.route("/api/health", methods=["GET"])
def health():
    return jsonify({"status": "ok", "model": DEFAULT_MODEL})


# ════════════════════════════════════════════════════════════════════════
# Serve frontend files
# ════════════════════════════════════════════════════════════════════════

@app.route("/")
def serve_index():
    return send_from_directory(".", "index.html")


@app.route("/<path:filename>")
def serve_static(filename):
    return send_from_directory(".", filename)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8100))
    app.run(host="0.0.0.0", port=port, debug=False)
