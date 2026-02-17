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
from openai import OpenAI  # Groq uses the OpenAI-compatible SDK
import pdfplumber
from docx import Document
from werkzeug.utils import secure_filename

load_dotenv()

app = Flask(__name__)
CORS(app)

app.config["MAX_CONTENT_LENGTH"] = 16 * 1024 * 1024
UPLOAD_FOLDER = Path("uploads")
UPLOAD_FOLDER.mkdir(exist_ok=True)
ALLOWED_EXTENSIONS = {"pdf", "txt", "docx", "md"}

client = OpenAI(
    api_key=os.getenv("GROQ_API_KEY"),
    base_url="https://api.groq.com/openai/v1",
)
DEFAULT_MODEL = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")

uploaded_files: dict[str, dict] = {}
chat_sessions: dict[str, list] = {}
saved_outputs: dict[str, dict] = {}



def allowed_file(filename: str) -> bool:
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


def extract_text(filepath: Path, extension: str) -> str:
    """Extract plain text from uploaded file."""
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

    # txt / md
    return filepath.read_text(encoding="utf-8", errors="replace")


def build_notes_context(file_ids: list[str]) -> str:
    """Build a context block from selected uploaded notes."""
    parts = []
    for fid in file_ids:
        meta = uploaded_files.get(fid)
        if meta:
            parts.append(f"--- Notes: {meta['filename']} ---\n{meta['text']}\n")
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


@app.route("/api/files", methods=["GET"])
def list_files():
    """Return metadata for all uploaded files (no raw text)."""
    files = [
        {
            "id": fid,
            "filename": meta["filename"],
            "size": meta["size"],
            "uploaded_at": meta["uploaded_at"],
        }
        for fid, meta in uploaded_files.items()
    ]
    return jsonify({"files": files})


@app.route("/api/files/upload", methods=["POST"])
def upload_file():
    """Upload a notes file (PDF, DOCX, TXT, MD) and extract its text."""
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

    uploaded_files[file_id] = {
        "filename": filename,
        "path": str(save_path),
        "size": save_path.stat().st_size,
        "text": text,
        "uploaded_at": datetime.utcnow().isoformat(),
    }

    return jsonify({
        "id": file_id,
        "filename": filename,
        "size": uploaded_files[file_id]["size"],
        "preview": text[:300] + ("…" if len(text) > 300 else ""),
    }), 201


@app.route("/api/files/<file_id>", methods=["DELETE"])
def delete_file(file_id: str):
    """Remove an uploaded file."""
    meta = uploaded_files.pop(file_id, None)
    if not meta:
        return jsonify({"error": "File not found"}), 404
    Path(meta["path"]).unlink(missing_ok=True)
    return jsonify({"message": "File deleted"})



CHAT_SYSTEM = (
    "You are a helpful study assistant. "
    "When the user provides notes, use them to answer questions accurately. "
    "Be concise, clear, and educational."
)


@app.route("/api/chat/session", methods=["POST"])
def create_session():
    """Create a new chat session (optionally attach note file IDs)."""
    body = request.get_json(silent=True) or {}
    session_id = str(uuid.uuid4())
    file_ids = body.get("file_ids", [])
    system_prompt = CHAT_SYSTEM

    if file_ids:
        notes = build_notes_context(file_ids)
        system_prompt += f"\n\nThe user has shared the following study notes:\n{notes}"

    chat_sessions[session_id] = {
        "messages": [],
        "system": system_prompt,
        "file_ids": file_ids,
        "created_at": datetime.utcnow().isoformat(),
    }
    return jsonify({"session_id": session_id}), 201


@app.route("/api/chat/<session_id>", methods=["GET"])
def get_chat(session_id: str):
    """Return the message history for a session."""
    session = chat_sessions.get(session_id)
    if not session:
        return jsonify({"error": "Session not found"}), 404
    return jsonify({"messages": session["messages"]})


@app.route("/api/chat/<session_id>", methods=["POST"])
def send_message(session_id: str):
    """Send a user message and receive an AI reply."""
    session = chat_sessions.get(session_id)
    if not session:
        return jsonify({"error": "Session not found"}), 404

    body = request.get_json(silent=True) or {}
    user_message = (body.get("message") or "").strip()
    if not user_message:
        return jsonify({"error": "Message is required"}), 400

    session["messages"].append({"role": "user", "content": user_message})

    try:
        reply = chat_completion(session["messages"], system=session["system"])
    except Exception as exc:
        session["messages"].pop()
        return jsonify({"error": str(exc)}), 502

    session["messages"].append({"role": "assistant", "content": reply})

    return jsonify({
        "reply": reply,
        "message_count": len(session["messages"]),
    })


@app.route("/api/chat/<session_id>", methods=["DELETE"])
def clear_chat(session_id: str):
    """Clear the message history (keep session alive)."""
    session = chat_sessions.get(session_id)
    if not session:
        return jsonify({"error": "Session not found"}), 404
    session["messages"] = []
    return jsonify({"message": "Chat history cleared"})


OUTPUT_TYPES = {"summary", "flashcards", "quiz", "key_points", "explain"}

OUTPUT_PROMPTS = {
    "summary": (
        "Produce a thorough but concise summary of the following study notes. "
        "Use clear headings and bullet points where appropriate."
    ),
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
    "key_points": (
        "Extract the most important key points from the following study notes. "
        "Present them as a numbered list."
    ),
    "explain": (
        "Explain the main concepts in the following study notes as if teaching "
        "a beginner. Use simple language and examples."
    ),
}


@app.route("/api/output/generate", methods=["POST"])
def generate_output():
    """Generate structured study material from uploaded notes."""
    body = request.get_json(silent=True) or {}
    file_ids: list[str] = body.get("file_ids", [])
    output_type: str = body.get("type", "summary").lower()
    custom_prompt: str = body.get("custom_prompt", "").strip()

    if not file_ids:
        return jsonify({"error": "Provide at least one file_id"}), 400
    if output_type not in OUTPUT_TYPES and not custom_prompt:
        return jsonify({"error": f"type must be one of {OUTPUT_TYPES} or supply custom_prompt"}), 400

    notes = build_notes_context(file_ids)
    if not notes.strip():
        return jsonify({"error": "No text found in the selected files"}), 422

    instruction = custom_prompt if custom_prompt else OUTPUT_PROMPTS[output_type]
    messages = [
        {"role": "user", "content": f"{instruction}\n\n{notes}"}
    ]

    try:
        result = chat_completion(messages)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 502

    output_id = str(uuid.uuid4())
    saved_outputs[output_id] = {
        "type": output_type if not custom_prompt else "custom",
        "file_ids": file_ids,
        "content": result,
        "created_at": datetime.utcnow().isoformat(),
    }

    return jsonify({
        "output_id": output_id,
        "type": saved_outputs[output_id]["type"],
        "content": result,
    }), 201


@app.route("/api/output", methods=["GET"])
def list_outputs():
    """Return all previously generated outputs."""
    outputs = [
        {
            "output_id": oid,
            "type": data["type"],
            "file_ids": data["file_ids"],
            "created_at": data["created_at"],
            "preview": data["content"][:200] + ("…" if len(data["content"]) > 200 else ""),
        }
        for oid, data in saved_outputs.items()
    ]
    return jsonify({"outputs": outputs})


@app.route("/api/output/<output_id>", methods=["GET"])
def get_output(output_id: str):
    """Fetch a specific saved output."""
    data = saved_outputs.get(output_id)
    if not data:
        return jsonify({"error": "Output not found"}), 404
    return jsonify({"output_id": output_id, **data})


@app.route("/api/output/<output_id>", methods=["DELETE"])
def delete_output(output_id: str):
    saved_outputs.pop(output_id, None)
    return jsonify({"message": "Output deleted"})


# ════════════════════════════════════════════════════════════════════════
# 3b. CORNELL NOTES
# ════════════════════════════════════════════════════════════════════════

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
    "- cues and notes arrays MUST have the same length; each cue pairs with the note at the same index.\n"
    "- cues should be concise questions or keywords (left column).\n"
    "- notes should be detailed explanations or answers (right column).\n"
    "- summary should be 2-4 sentences capturing the big picture.\n"
    "- Generate at least 5 cue/note pairs.\n"
    "- Return ONLY the JSON object, nothing else."
)


@app.route("/api/cornell/generate", methods=["POST"])
def generate_cornell():
    """Generate Cornell Notes from uploaded study material."""
    body = request.get_json(silent=True) or {}
    file_ids: list[str] = body.get("file_ids", [])

    if not file_ids:
        return jsonify({"error": "Provide at least one file_id"}), 400

    notes = build_notes_context(file_ids)
    if not notes.strip():
        return jsonify({"error": "No text found in the selected files"}), 422

    messages = [
        {"role": "user", "content": f"Create Cornell Notes from the following study material:\n\n{notes}"}
    ]

    try:
        result = chat_completion(messages, system=CORNELL_SYSTEM)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 502

    # Parse the JSON response from the AI
    try:
        # Strip markdown fences if the model wraps them
        cleaned = result.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.split("\n", 1)[1] if "\n" in cleaned else cleaned[3:]
            if cleaned.endswith("```"):
                cleaned = cleaned[:-3]
            cleaned = cleaned.strip()

        cornell_data = json.loads(cleaned)

        # Validate required keys
        for key in ("title", "cues", "notes", "summary"):
            if key not in cornell_data:
                raise ValueError(f"Missing key: {key}")
        if len(cornell_data["cues"]) != len(cornell_data["notes"]):
            raise ValueError("cues and notes arrays must have the same length")

    except (json.JSONDecodeError, ValueError) as exc:
        # Fall back: return raw text so the frontend can still display something
        output_id = str(uuid.uuid4())
        saved_outputs[output_id] = {
            "type": "cornell",
            "file_ids": file_ids,
            "content": result,
            "cornell": None,
            "created_at": datetime.utcnow().isoformat(),
        }
        return jsonify({
            "output_id": output_id,
            "cornell": None,
            "raw": result,
            "parse_error": str(exc),
        }), 201

    output_id = str(uuid.uuid4())
    saved_outputs[output_id] = {
        "type": "cornell",
        "file_ids": file_ids,
        "content": result,
        "cornell": cornell_data,
        "created_at": datetime.utcnow().isoformat(),
    }

    return jsonify({
        "output_id": output_id,
        "cornell": cornell_data,
    }), 201


# ════════════════════════════════════════════════════════════════════════
# 4. PLAYGROUND  (free-form AI + sandboxed code execution)
# ════════════════════════════════════════════════════════════════════════

PLAYGROUND_SYSTEM = (
    "You are an expert coding and study assistant. "
    "Help the user understand concepts, debug code, explain algorithms, "
    "and answer any study-related questions. "
    "When writing code, prefer Python unless asked otherwise."
)


@app.route("/api/playground/ask", methods=["POST"])
def playground_ask():
    """
    Free-form prompt to the AI — no session history.
    Optionally attach note file IDs for context.
    """
    body = request.get_json(silent=True) or {}
    prompt: str = (body.get("prompt") or "").strip()
    file_ids: list[str] = body.get("file_ids", [])

    if not prompt:
        return jsonify({"error": "prompt is required"}), 400

    system = PLAYGROUND_SYSTEM
    if file_ids:
        notes = build_notes_context(file_ids)
        system += f"\n\nThe user has provided these study notes for reference:\n{notes}"

    try:
        reply = chat_completion([{"role": "user", "content": prompt}], system=system)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 502

    return jsonify({"response": reply})


@app.route("/api/playground/run", methods=["POST"])
def playground_run():
    """
    Execute a Python code snippet in a sandboxed subprocess.
    Returns stdout, stderr and exit code.
    WARNING: For production, replace with a proper sandbox (e.g. Docker / Pyodide).
    """
    body = request.get_json(silent=True) or {}
    code: str = body.get("code", "")

    if not code.strip():
        return jsonify({"error": "code is required"}), 400

    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".py", delete=False, encoding="utf-8"
    ) as tmp:
        tmp.write(code)
        tmp_path = tmp.name

    try:
        proc = subprocess.run(
            [sys.executable, tmp_path],
            capture_output=True,
            text=True,
            timeout=10,   # 10-second hard limit
        )
        return jsonify({
            "stdout": proc.stdout,
            "stderr": proc.stderr,
            "exit_code": proc.returncode,
        })
    except subprocess.TimeoutExpired:
        return jsonify({"error": "Execution timed out (10 s limit)"}), 408
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500
    finally:
        Path(tmp_path).unlink(missing_ok=True)


@app.route("/api/playground/explain", methods=["POST"])
def playground_explain():
    """Ask the AI to explain a piece of code."""
    body = request.get_json(silent=True) or {}
    code: str = (body.get("code") or "").strip()
    language: str = body.get("language", "Python")

    if not code:
        return jsonify({"error": "code is required"}), 400

    prompt = (
        f"Explain the following {language} code step-by-step in simple terms. "
        f"Mention what each section does and highlight any important concepts.\n\n"
        f"```{language.lower()}\n{code}\n```"
    )

    try:
        reply = chat_completion(
            [{"role": "user", "content": prompt}],
            system=PLAYGROUND_SYSTEM,
        )
    except Exception as exc:
        return jsonify({"error": str(exc)}), 502

    return jsonify({"explanation": reply})


# ════════════════════════════════════════════════════════════════════════
# Health check
# ════════════════════════════════════════════════════════════════════════

@app.route("/api/health", methods=["GET"])
def health():
    return jsonify({
        "status": "ok",
        "model": DEFAULT_MODEL,
        "uploaded_files": len(uploaded_files),
        "active_sessions": len(chat_sessions),
        "saved_outputs": len(saved_outputs),
    })


if __name__ == "__main__":
    app.run(debug=True, port=8100)
