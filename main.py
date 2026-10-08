import os
import uuid
import json
import time
import logging
import shutil
import threading
import subprocess
import tempfile
import sys
from functools import wraps
from collections import defaultdict, deque
from pathlib import Path
from datetime import datetime, timedelta, timezone

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
AVATARS_FOLDER = Path("uploads/avatars")
AVATARS_FOLDER.mkdir(parents=True, exist_ok=True)
ALLOWED_EXTENSIONS = {"pdf", "txt", "docx", "md"}
ALLOWED_IMAGE_EXTENSIONS = {"jpg", "jpeg", "png", "gif", "webp"}

client = OpenAI(
    api_key=os.getenv("GROQ_API_KEY"),
    base_url="https://api.groq.com/openai/v1",
)
DEFAULT_MODEL = os.getenv("GROQ_MODEL", "openai/gpt-oss-120b")

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_SERVICE_KEY = os.getenv("SUPABASE_SERVICE_KEY")
supabase = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)

_rate_buckets: dict[str, deque] = defaultdict(deque)
_rate_lock = threading.Lock()


def rate_limit(limit: int, window_seconds: int = 60, scope: str = ""):
    """Per-user sliding-window cap. Keeps one account from draining the API quota."""
    def decorator(f):
        @wraps(f)
        def wrapper(*args, **kwargs):
            user = getattr(request, "user", None)
            key = f"{scope or f.__name__}:{getattr(user, 'id', request.remote_addr)}"
            now = time.monotonic()
            with _rate_lock:
                bucket = _rate_buckets[key]
                while bucket and now - bucket[0] > window_seconds:
                    bucket.popleft()
                if len(bucket) >= limit:
                    retry_after = int(window_seconds - (now - bucket[0])) + 1
                    return jsonify({
                        "error": f"You are going a bit fast. Try again in {retry_after}s.",
                    }), 429
                bucket.append(now)
            return f(*args, **kwargs)
        return wrapper
    return decorator



def get_user_from_token():
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


class AIServiceError(Exception):
    """The AI provider call failed.

    `message` is safe to show the user, `status` is the HTTP code to return.
    The underlying provider error is always logged in full.
    """

    def __init__(self, message: str, status: int = 502):
        super().__init__(message)
        self.status = status


AI_UNAVAILABLE = "The assistant is temporarily unavailable. Please try again in a moment."


def _provider_error_detail(exc: Exception) -> tuple[str, str]:
    """Pull (code, message) out of an OpenAI-SDK style error body."""
    body = getattr(exc, "body", None)
    if isinstance(body, dict):
        err = body.get("error") or {}
        if isinstance(err, dict):
            return str(err.get("code") or ""), str(err.get("message") or "")
    return "", str(exc)


def _classify_ai_error(exc: Exception) -> AIServiceError:
    """Turn a provider exception into something the student can act on.

    Generic text is fine for transient faults, but a misconfigured model or a
    blown context window are both things the user can actually fix - saying
    'temporarily unavailable' for those just hides the problem.
    """
    status = getattr(exc, "status_code", None)
    code, detail = _provider_error_detail(exc)

    if code == "model_not_found" or status == 404:
        return AIServiceError(
            f"The AI model '{DEFAULT_MODEL}' is not available on this API key. "
            "Check the GROQ_MODEL setting on the server.",
            503,
        )

    # Groq returns 413 + code=rate_limit_exceeded when the prompt alone exceeds
    # the tokens-per-minute allowance.
    if status == 413 or (code == "rate_limit_exceeded" and "too large" in detail.lower()):
        return AIServiceError(
            "Those notes are too long to send in one go. Select fewer files, or "
            "split the document into smaller parts.",
            413,
        )

    if status == 429 or code == "rate_limit_exceeded":
        return AIServiceError(
            "The AI is rate-limited right now. Wait a few seconds and try again.",
            429,
        )

    if status == 401:
        return AIServiceError(
            "The server's AI credentials were rejected. Check the GROQ_API_KEY setting.",
            503,
        )

    if status is not None and 500 <= status < 600:
        return AIServiceError(
            "The AI provider is having problems. Please try again shortly.", 502)

    return AIServiceError(AI_UNAVAILABLE, 502)


def chat_completion(messages: list[dict], system: str = "") -> str:
    full_messages = []
    if system:
        full_messages.append({"role": "system", "content": system})
    full_messages.extend(messages)
    try:
        response = client.chat.completions.create(
            model=DEFAULT_MODEL,
            messages=full_messages,
        )
    except Exception as exc:
        app.logger.exception("Groq chat completion failed (model=%s)", DEFAULT_MODEL)
        raise _classify_ai_error(exc) from exc
    return response.choices[0].message.content


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
        "school_board": profile.get("school_board", ""),
        "grade_major": profile.get("grade_major", ""),
        "bio_message": profile.get("bio_message", ""),
    })


@app.route("/api/me", methods=["PUT"])
@require_auth
def update_me():
    user = request.user
    body = request.get_json(silent=True) or {}
    updates = {}
    for field in ("full_name", "school_board", "grade_major", "bio_message"):
        if field in body:
            updates[field] = body[field]
    if updates:
        existing = supabase.table("profiles").select("id").eq("id", user.id).execute()
        if existing.data:
            supabase.table("profiles").update(updates).eq("id", user.id).execute()
        else:
            updates["id"] = user.id
            supabase.table("profiles").insert(updates).execute()
    return jsonify({"message": "Profile updated"})


@app.route("/api/me/avatar", methods=["POST"])
@require_auth
def upload_avatar():
    user = request.user
    if "file" not in request.files:
        return jsonify({"error": "No file provided"}), 400
    file = request.files["file"]
    if not file.filename:
        return jsonify({"error": "No file selected"}), 400
    ext = file.filename.rsplit(".", 1)[-1].lower() if "." in file.filename else "jpg"
    if ext not in ALLOWED_IMAGE_EXTENSIONS:
        return jsonify({"error": "Invalid image type. Use JPG, PNG, GIF or WebP"}), 400
    filename = f"{user.id}.{ext}"
    filepath = AVATARS_FOLDER / filename
    file.save(filepath)
    avatar_url = f"/uploads/avatars/{filename}"
    existing = supabase.table("profiles").select("id").eq("id", user.id).execute()
    if existing.data:
        supabase.table("profiles").update({"avatar_url": avatar_url}).eq("id", user.id).execute()
    else:
        supabase.table("profiles").insert({"id": user.id, "avatar_url": avatar_url}).execute()
    return jsonify({"avatar_url": avatar_url})


@app.route("/api/files", methods=["GET"])
@require_auth
def list_files():
    user = request.user
    result = supabase.table("files").select("id, filename, size, uploaded_at").eq("user_id", user.id).order("uploaded_at", desc=True).execute()
    return jsonify({"files": result.data or []})


@app.route("/api/files/upload", methods=["POST"])
@require_auth
@rate_limit(20, 60)
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
    except Exception:
        app.logger.exception("Text extraction failed for %s", filename)
        save_path.unlink(missing_ok=True)
        return jsonify({"error": "Could not read that file. It may be corrupt, password-protected, or a scanned image with no selectable text."}), 422

    file_size = save_path.stat().st_size

    supabase.table("files").insert({
        "id": file_id,
        "user_id": user.id,
        "filename": filename,
        "size": file_size,
        "text_content": text,
        "storage_path": str(save_path),
    }).execute()

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
@rate_limit(30, 60)
def send_message(session_id: str):
    user = request.user
    body = request.get_json(silent=True) or {}
    user_message = (body.get("message") or "").strip()
    if not user_message:
        return jsonify({"error": "Message is required"}), 400

    session_result = supabase.table("chat_sessions").select("system_prompt").eq("id", session_id).eq("user_id", user.id).execute()
    if not session_result.data:
        return jsonify({"error": "Session not found"}), 404

    system_prompt = session_result.data[0]["system_prompt"]

    msgs_result = supabase.table("chat_messages").select("role, content").eq("session_id", session_id).order("created_at").execute()
    messages = [{"role": m["role"], "content": m["content"]} for m in (msgs_result.data or [])]
    messages.append({"role": "user", "content": user_message})

    supabase.table("chat_messages").insert({
        "session_id": session_id,
        "user_id": user.id,
        "role": "user",
        "content": user_message,
    }).execute()

    try:
        reply = chat_completion(messages, system=system_prompt)
    except AIServiceError as exc:
        return jsonify({"error": str(exc)}), exc.status

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
@rate_limit(15, 60)
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
    except AIServiceError as exc:
        return jsonify({"error": str(exc)}), exc.status

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
@rate_limit(15, 60)
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
    except AIServiceError as exc:
        return jsonify({"error": str(exc)}), exc.status

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


PLAYGROUND_SYSTEM = (
    "You are an expert coding and study assistant. "
    "Help the user understand concepts, debug code, explain algorithms, "
    "and answer any study-related questions. "
    "When writing code, prefer Python unless asked otherwise."
)


@app.route("/api/playground/ask", methods=["POST"])
@require_auth
@rate_limit(30, 60)
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
    except AIServiceError as exc:
        return jsonify({"error": str(exc)}), exc.status

    return jsonify({"response": reply})


CODE_TIMEOUT = int(os.getenv("CODE_TIMEOUT", "10"))
MAX_CODE_CHARS = 100_000
MAX_OUTPUT_CHARS = 20_000
# "docker" = real isolation (recommended for any public deploy).
# "subprocess" = hardened local fallback: secrets stripped, isolated cwd, no user site.
CODE_SANDBOX = os.getenv("CODE_SANDBOX", "subprocess").lower()
CODE_SANDBOX_IMAGE = os.getenv("CODE_SANDBOX_IMAGE", "python:3.12-alpine")


def _truncate_output(text: str) -> str:
    if text and len(text) > MAX_OUTPUT_CHARS:
        return text[:MAX_OUTPUT_CHARS] + '\n...[output truncated]'
    return text


def _sandbox_env(workdir: str) -> dict:
    """A minimal environment for untrusted code.

    The parent process holds GROQ_API_KEY and SUPABASE_SERVICE_KEY in os.environ
    (load_dotenv puts them there), and subprocess inherits the parent environment
    by default -- so user code could simply print them. Build the child's env from
    scratch instead of inheriting.
    """
    keep = ("PATH", "SYSTEMROOT", "WINDIR", "COMSPEC", "LANG", "LC_ALL", "TZ")
    env = {k: os.environ[k] for k in keep if k in os.environ}
    env.update({
        "HOME": workdir,
        "TMPDIR": workdir,
        "TEMP": workdir,
        "TMP": workdir,
        "PYTHONIOENCODING": "utf-8",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONUNBUFFERED": "1",
        "PYTHONNOUSERSITE": "1",
    })
    return env


def _run_docker(workdir: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [
            "docker", "run", "--rm",
            "--network", "none",
            "--memory", "256m", "--cpus", "0.5", "--pids-limit", "64",
            "--read-only",
            "--tmpfs", "/tmp:rw,size=16m,noexec,nosuid",
            "--security-opt", "no-new-privileges",
            "--cap-drop", "ALL",
            "-v", f"{workdir}:/sandbox:ro",
            "-w", "/sandbox",
            CODE_SANDBOX_IMAGE,
            "python", "-I", "-B", "main.py",
        ],
        capture_output=True, text=True, timeout=CODE_TIMEOUT + 10,
    )


def _run_subprocess(workdir: str, script: str) -> subprocess.CompletedProcess:
    kwargs = {}
    if os.name == "posix":
        kwargs["start_new_session"] = True  # so the timeout kill takes the whole group
    return subprocess.run(
        [sys.executable, "-I", "-B", script],
        capture_output=True, text=True, timeout=CODE_TIMEOUT,
        cwd=workdir, env=_sandbox_env(workdir), stdin=subprocess.DEVNULL,
        **kwargs,
    )


@app.route("/api/playground/run", methods=["POST"])
@require_auth
@rate_limit(20, 60)
def playground_run():
    body = request.get_json(silent=True) or {}
    code = body.get("code", "")
    if not code.strip():
        return jsonify({"error": "code is required"}), 400
    if len(code) > MAX_CODE_CHARS:
        return jsonify({"error": "That script is too large to run."}), 413

    workdir = tempfile.mkdtemp(prefix="tsw_run_")
    script = os.path.join(workdir, "main.py")
    try:
        with open(script, "w", encoding="utf-8") as fh:
            fh.write(code)

        if CODE_SANDBOX == "docker":
            proc = _run_docker(workdir)
        else:
            proc = _run_subprocess(workdir, script)

        return jsonify({
            "stdout": _truncate_output(proc.stdout),
            "stderr": _truncate_output(proc.stderr),
            "exit_code": proc.returncode,
            "sandbox": CODE_SANDBOX,
        })
    except subprocess.TimeoutExpired:
        return jsonify({"error": f"Execution timed out ({CODE_TIMEOUT}s limit)"}), 408
    except FileNotFoundError:
        app.logger.exception("Sandbox runtime missing (CODE_SANDBOX=%s)", CODE_SANDBOX)
        return jsonify({"error": "The code runner is not available right now."}), 503
    except Exception:
        app.logger.exception("Code execution failed")
        return jsonify({"error": "The code runner failed to start. Please try again."}), 500
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


@app.route("/api/playground/explain", methods=["POST"])
@require_auth
@rate_limit(30, 60)
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
    except AIServiceError as exc:
        return jsonify({"error": str(exc)}), exc.status

    return jsonify({"explanation": reply})


@app.route("/api/language/practice", methods=["POST"])
@require_auth
@rate_limit(40, 60)
def language_practice():
    body = request.get_json(silent=True) or {}
    language = (body.get("language") or "Spanish").strip()
    topic = (body.get("topic") or "").strip()
    history = body.get("history") or []
    user_message = (body.get("user_message") or "").strip()

    file_ids = body.get("file_ids") or []

    if not user_message:
        return jsonify({"error": "user_message is required"}), 400

    topic_context = f" The student is preparing for a speaking assignment about: {topic}." if topic else ""

    speaking_bank_context = ""
    if file_ids:
        notes = build_notes_context(request.user.id, file_ids)
        if notes.strip():
            speaking_bank_context = (
                f"\n\nThe student has provided the following speaking bank / reference material "
                f"(vocabulary lists, sentence starters, sample phrases, notes, etc.). "
                f"Use this to guide the conversation — encourage the student to use vocabulary "
                f"and phrases from these materials, and reference them when giving feedback:\n\n{notes}"
            )

    system = (
        f"You are a friendly and encouraging {language} language teacher helping a student "
        f"practice for a speaking assignment.{topic_context}\n\n"
        f"Your role:\n"
        f"1. Respond primarily in {language} to give the student real practice.\n"
        f"2. After the student speaks, gently point out any grammar or vocabulary errors "
        f"in a kind way — you may use English briefly for corrections.\n"
        f"3. Keep responses conversational and appropriate for a student.\n"
        f"4. If the student writes in English, gently encourage them to try in {language} "
        f"and model a helpful phrase they can use.\n"
        f"5. Keep responses concise (2-4 sentences) so the conversation flows naturally.\n"
        f"6. Ask a follow-up question to keep the conversation going.\n"
        f"7. Be warm and encouraging — celebrate their effort and progress!"
        f"{speaking_bank_context}"
    )

    messages = [{"role": "system", "content": system}]
    for msg in history[-12:]:
        if msg.get("role") in ("user", "assistant") and msg.get("content"):
            messages.append({"role": msg["role"], "content": msg["content"]})
    messages.append({"role": "user", "content": user_message})

    try:
        reply = chat_completion(messages[1:], system=system)
    except AIServiceError as exc:
        return jsonify({"error": str(exc)}), exc.status

    return jsonify({"reply": reply})


# =====================================================================
# Study loop: spaced repetition, quiz scoring, weak-topic detection
# =====================================================================

def parse_json_block(raw: str):
    """Parse model JSON that may be wrapped in a markdown code fence."""
    cleaned = (raw or "").strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("\n", 1)[1] if "\n" in cleaned else cleaned[3:]
        if cleaned.rstrip().endswith("```"):
            cleaned = cleaned.rstrip()[:-3]
    return json.loads(cleaned.strip())


MIN_EASE = 1.3
AGAIN_DELAY_MINUTES = 10


def sm2(ease: float, interval_days: float, repetitions: int, lapses: int, grade: int):
    """SuperMemo-2. grade: 0=again, 3=hard, 4=good, 5=easy.

    Returns (ease, interval_days, repetitions, lapses, due_at).
    """
    grade = max(0, min(5, int(grade)))

    if grade < 3:
        # Lapse: reset the repetition chain and show it again this session.
        repetitions = 0
        lapses += 1
        interval_days = 0.0
        due_at = datetime.now(timezone.utc) + timedelta(minutes=AGAIN_DELAY_MINUTES)
    else:
        if repetitions == 0:
            interval_days = 1.0
        elif repetitions == 1:
            interval_days = 6.0
        else:
            interval_days = round(interval_days * ease, 2)
        repetitions += 1
        due_at = datetime.now(timezone.utc) + timedelta(days=interval_days)

    # Ease only moves on graded recall, and never below the SM-2 floor.
    ease = ease + (0.1 - (5 - grade) * (0.08 + (5 - grade) * 0.02))
    ease = max(MIN_EASE, round(ease, 3))

    return ease, float(interval_days), repetitions, lapses, due_at


def humanize_interval(interval_days: float, grade: int) -> str:
    if grade < 3:
        return f"{AGAIN_DELAY_MINUTES} min"
    if interval_days < 1:
        return "today"
    if interval_days == 1:
        return "tomorrow"
    if interval_days < 30:
        return f"{int(round(interval_days))} days"
    if interval_days < 365:
        return f"{round(interval_days / 30, 1)} months"
    return f"{round(interval_days / 365, 1)} years"


@app.route("/api/flashcards/import", methods=["POST"])
@require_auth
def import_flashcards():
    """Turn a saved flashcards output into scheduled, reviewable cards."""
    user = request.user
    body = request.get_json(silent=True) or {}
    output_id = body.get("output_id")
    deck = (body.get("deck") or "").strip()
    cards_in = body.get("cards")

    if output_id and not cards_in:
        result = (supabase.table("saved_outputs").select("content, type")
                  .eq("id", output_id).eq("user_id", user.id).execute())
        if not result.data:
            return jsonify({"error": "Output not found"}), 404
        try:
            cards_in = parse_json_block(result.data[0]["content"])
        except Exception:
            return jsonify({"error": "That output is not a readable flashcard set."}), 422

    if not isinstance(cards_in, list) or not cards_in:
        return jsonify({"error": "No cards to import"}), 400

    rows = []
    for card in cards_in:
        if not isinstance(card, dict):
            continue
        front = str(card.get("front", "")).strip()
        back = str(card.get("back", "")).strip()
        if not front or not back:
            continue
        rows.append({
            "id": str(uuid.uuid4()),
            "user_id": user.id,
            "output_id": output_id,
            "deck": deck or "Default",
            "front": front[:2000],
            "back": back[:4000],
            "due_at": datetime.now(timezone.utc).isoformat(),
        })

    if not rows:
        return jsonify({"error": "No valid cards found"}), 422

    supabase.table("flashcards").insert(rows).execute()
    return jsonify({"imported": len(rows), "deck": deck or "Default"}), 201


@app.route("/api/flashcards/due", methods=["GET"])
@require_auth
def flashcards_due():
    user = request.user
    limit = min(int(request.args.get("limit", 40)), 100)
    deck = request.args.get("deck")

    query = (supabase.table("flashcards").select("*")
             .eq("user_id", user.id).eq("suspended", False)
             .lte("due_at", datetime.now(timezone.utc).isoformat())
             .order("due_at").limit(limit))
    if deck:
        query = query.eq("deck", deck)

    return jsonify({"cards": query.execute().data or []})


@app.route("/api/flashcards/<card_id>/review", methods=["POST"])
@require_auth
def review_flashcard(card_id: str):
    user = request.user
    body = request.get_json(silent=True) or {}
    try:
        grade = int(body.get("grade"))
    except (TypeError, ValueError):
        return jsonify({"error": "grade is required (0, 3, 4 or 5)"}), 400

    result = (supabase.table("flashcards").select("*")
              .eq("id", card_id).eq("user_id", user.id).execute())
    if not result.data:
        return jsonify({"error": "Card not found"}), 404
    card = result.data[0]

    ease, interval_days, repetitions, lapses, due_at = sm2(
        float(card.get("ease") or 2.5),
        float(card.get("interval_days") or 0),
        int(card.get("repetitions") or 0),
        int(card.get("lapses") or 0),
        grade,
    )

    now_iso = datetime.now(timezone.utc).isoformat()
    supabase.table("flashcards").update({
        "ease": ease,
        "interval_days": interval_days,
        "repetitions": repetitions,
        "lapses": lapses,
        "due_at": due_at.isoformat(),
        "last_grade": grade,
        "last_reviewed_at": now_iso,
    }).eq("id", card_id).eq("user_id", user.id).execute()

    supabase.table("card_reviews").insert({
        "id": str(uuid.uuid4()),
        "user_id": user.id,
        "card_id": card_id,
        "grade": grade,
        "interval_after": interval_days,
        "ease_after": ease,
    }).execute()

    return jsonify({
        "card_id": card_id,
        "ease": ease,
        "interval_days": interval_days,
        "repetitions": repetitions,
        "lapses": lapses,
        "due_at": due_at.isoformat(),
        "next_review": humanize_interval(interval_days, grade),
    })


@app.route("/api/flashcards/stats", methods=["GET"])
@require_auth
def flashcard_stats():
    user = request.user
    cards = (supabase.table("flashcards")
             .select("due_at, repetitions, lapses, deck, suspended")
             .eq("user_id", user.id).execute().data or [])

    now = datetime.now(timezone.utc)
    due = new = learning = mature = 0
    decks: dict[str, int] = {}

    for card in cards:
        if card.get("suspended"):
            continue
        name = card.get("deck") or "Default"
        decks[name] = decks.get(name, 0) + 1
        reps = int(card.get("repetitions") or 0)
        if reps == 0:
            new += 1
        elif reps < 3:
            learning += 1
        else:
            mature += 1
        try:
            if datetime.fromisoformat(str(card["due_at"]).replace("Z", "+00:00")) <= now:
                due += 1
        except (ValueError, KeyError):
            pass

    reviews = (supabase.table("card_reviews").select("reviewed_at, grade")
               .eq("user_id", user.id).order("reviewed_at", desc=True).limit(2000)
               .execute().data or [])

    review_days = set()
    for review in reviews:
        try:
            review_days.add(datetime.fromisoformat(
                str(review["reviewed_at"]).replace("Z", "+00:00")).date())
        except (ValueError, KeyError):
            pass

    # Count back from today, or yesterday so an unstarted day does not break it.
    streak = 0
    cursor = now.date()
    if cursor not in review_days:
        cursor -= timedelta(days=1)
    while cursor in review_days:
        streak += 1
        cursor -= timedelta(days=1)

    today_iso = now.date().isoformat()
    reviewed_today = sum(
        1 for review in reviews
        if str(review.get("reviewed_at", ""))[:10] == today_iso
    )

    return jsonify({
        "total": sum(decks.values()),
        "due": due,
        "new": new,
        "learning": learning,
        "mature": mature,
        "decks": decks,
        "streak_days": streak,
        "reviewed_today": reviewed_today,
        "total_reviews": len(reviews),
    })


@app.route("/api/flashcards/<card_id>", methods=["DELETE"])
@require_auth
def delete_flashcard(card_id: str):
    user = request.user
    supabase.table("flashcards").delete().eq("id", card_id).eq("user_id", user.id).execute()
    return jsonify({"deleted": card_id})


# -------------------------------------------------------------- quiz attempts

@app.route("/api/quiz/attempt", methods=["POST"])
@require_auth
def submit_quiz_attempt():
    """Grade a quiz server-side and store the attempt."""
    user = request.user
    body = request.get_json(silent=True) or {}
    questions = body.get("questions") or []
    chosen = body.get("answers") or {}
    output_id = body.get("output_id")
    file_ids = body.get("file_ids") or []

    if not questions:
        return jsonify({"error": "No questions supplied"}), 400

    graded = []
    score = 0
    for index, question in enumerate(questions):
        correct_index = question.get("answer")
        picked = chosen.get(str(index), chosen.get(index))
        is_correct = picked is not None and picked == correct_index
        if is_correct:
            score += 1
        graded.append({
            "q": question.get("q", ""),
            "options": question.get("options", []),
            "answer": correct_index,
            "chosen": picked,
            "correct": is_correct,
        })

    attempt_id = str(uuid.uuid4())
    supabase.table("quiz_attempts").insert({
        "id": attempt_id,
        "user_id": user.id,
        "output_id": output_id,
        "file_ids": file_ids,
        "score": score,
        "total": len(questions),
        "answers": graded,
    }).execute()

    return jsonify({
        "attempt_id": attempt_id,
        "score": score,
        "total": len(questions),
        "percent": round(100 * score / len(questions)),
        "missed": [g for g in graded if not g["correct"]],
    }), 201


@app.route("/api/quiz/attempts", methods=["GET"])
@require_auth
def list_quiz_attempts():
    user = request.user
    limit = min(int(request.args.get("limit", 50)), 200)
    attempts = (supabase.table("quiz_attempts")
                .select("id, output_id, score, total, created_at")
                .eq("user_id", user.id).order("created_at", desc=True)
                .limit(limit).execute().data or [])
    return jsonify({"attempts": attempts})


# ---------------------------------------------------------------- weak topics

WEAK_TOPICS_SYSTEM = (
    "You analyse a student's mistakes and name the underlying concepts they are "
    "struggling with. Return ONLY valid JSON, no markdown, no code fences, in this "
    "exact format:\n"
    '[{"topic":"Short concept name","why":"One sentence on what they are getting wrong",'
    '"evidence_count":2}]\n'
    "Group related mistakes into a single topic. Return at most 6 topics, most "
    "important first. If there is not enough evidence, return []."
)


@app.route("/api/study/weak-topics", methods=["GET"])
@require_auth
@rate_limit(10, 60)
def weak_topics():
    """Cluster missed quiz questions and lapsed cards into named weak concepts."""
    user = request.user

    attempts = (supabase.table("quiz_attempts").select("answers, created_at")
                .eq("user_id", user.id).order("created_at", desc=True)
                .limit(20).execute().data or [])
    missed = []
    for attempt in attempts:
        for answer in (attempt.get("answers") or []):
            if not answer.get("correct") and answer.get("q"):
                options = answer.get("options") or []
                correct_idx = answer.get("answer")
                correct_text = (options[correct_idx]
                                if isinstance(correct_idx, int) and 0 <= correct_idx < len(options)
                                else "")
                missed.append(f"Q: {answer['q']}\n   Correct answer: {correct_text}")

    lapsed = (supabase.table("flashcards").select("front, lapses")
              .eq("user_id", user.id).gte("lapses", 2)
              .order("lapses", desc=True).limit(25).execute().data or [])

    if not missed and not lapsed:
        return jsonify({
            "topics": [],
            "message": "Not enough data yet. Take a quiz or review some flashcards first.",
        })

    parts = []
    if missed:
        parts.append("Quiz questions the student got wrong:\n" + "\n".join(missed[:40]))
    if lapsed:
        parts.append("Flashcards the student repeatedly forgets:\n" + "\n".join(
            f"- {c['front']} (forgotten {c['lapses']}x)" for c in lapsed))

    try:
        raw = chat_completion([{"role": "user", "content": "\n\n".join(parts)}],
                              system=WEAK_TOPICS_SYSTEM)
        topics = parse_json_block(raw)
        if not isinstance(topics, list):
            topics = []
    except AIServiceError as exc:
        return jsonify({"error": str(exc)}), exc.status
    except Exception:
        app.logger.exception("Weak-topic analysis returned unparseable JSON")
        return jsonify({"error": "Could not analyse your results right now."}), 502

    return jsonify({
        "topics": topics,
        "missed_questions": len(missed),
        "lapsed_cards": len(lapsed),
    })


@app.route("/api/quiz/retry-weak", methods=["POST"])
@require_auth
@rate_limit(10, 60)
def retry_weak_quiz():
    """Build a fresh quiz aimed squarely at what the student keeps missing."""
    user = request.user
    body = request.get_json(silent=True) or {}
    file_ids = body.get("file_ids") or []

    attempts = (supabase.table("quiz_attempts").select("answers")
                .eq("user_id", user.id).order("created_at", desc=True)
                .limit(10).execute().data or [])
    missed = [a["q"] for attempt in attempts
              for a in (attempt.get("answers") or [])
              if not a.get("correct") and a.get("q")]

    if not missed:
        return jsonify({"error": "No missed questions to practise yet."}), 422

    notes = build_notes_context(user.id, file_ids) if file_ids else ""
    instruction = (
        "The student previously got these questions wrong:\n"
        + "\n".join(f"- {q}" for q in missed[:25])
        + "\n\nWrite a NEW 10-question multiple-choice quiz targeting the same "
          "underlying concepts. Do not reuse the exact wording above - test the "
          "concept from a different angle so they cannot pass by memorising.\n"
          "Return ONLY valid JSON, no markdown, no code fences:\n"
          '[{"q":"Question text?","options":["A) ...","B) ...","C) ...","D) ..."],"answer":0}]'
    )
    if notes:
        instruction += f"\n\nBase the questions on these notes:\n{notes}"

    try:
        result = chat_completion([{"role": "user", "content": instruction}])
    except AIServiceError as exc:
        return jsonify({"error": str(exc)}), exc.status

    output_id = str(uuid.uuid4())
    supabase.table("saved_outputs").insert({
        "id": output_id,
        "user_id": user.id,
        "type": "quiz",
        "file_ids": file_ids,
        "content": result,
    }).execute()

    return jsonify({"output_id": output_id, "type": "quiz", "content": result,
                    "targeted": len(missed[:25])}), 201


@app.route("/api/config", methods=["GET"])
def get_config():
    return jsonify({
        "supabase_url": SUPABASE_URL,
        "supabase_anon_key": os.getenv("SUPABASE_ANON_KEY"),
    })


def verify_model_available() -> dict:
    """Check the configured model at boot.

    A wrong GROQ_MODEL is invisible until the first chat request fails, and the
    env var overrides the code default - so a stale value in the deploy
    environment silently breaks the whole app. Surface it at startup instead.
    """
    try:
        available = {m.id for m in client.models.list().data}
    except Exception as exc:
        app.logger.warning("Could not verify model list at startup: %s", exc)
        return {"verified": False, "reason": "model list unavailable"}

    if DEFAULT_MODEL in available:
        app.logger.info("Model OK: %s", DEFAULT_MODEL)
        return {"verified": True}

    chat_models = sorted(
        m for m in available
        if not any(tag in m for tag in ("whisper", "tts", "guard", "orpheus"))
    )
    app.logger.error(
        "GROQ_MODEL=%r is NOT available on this API key. Chat will fail with 503. "
        "Available chat models: %s",
        DEFAULT_MODEL, ", ".join(chat_models) or "(none)",
    )
    return {"verified": False, "reason": "model not available",
            "available_chat_models": chat_models}


MODEL_STATUS = verify_model_available()


@app.route("/api/health", methods=["GET"])
def health():
    ok = MODEL_STATUS.get("verified", False)
    return jsonify({
        "status": "ok" if ok else "degraded",
        "model": DEFAULT_MODEL,
        "model_status": MODEL_STATUS,
    }), (200 if ok else 503)


@app.route("/")
def serve_index():
    return send_from_directory(".", "index.html")


@app.route("/<path:filename>")
def serve_static(filename):
    return send_from_directory(".", filename)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8100))
    app.run(host="0.0.0.0", port=port, debug=False)
