# StudyAI 📚⚡

> *"Because highlighting your textbook in three different colors clearly wasn't working."*

StudyAI is an AI-powered study companion that takes your notes, documents, and general academic suffering, and turns them into something actually useful. Upload a PDF, get flashcards. Upload lecture slides, get a quiz. Upload your existential dread — okay, that one's not supported yet.

---

## Features

### 🔐 Authentication
Login and signup powered by Supabase. Your study data is tied to your account, so unlike that one group project partner, it actually sticks around.

### 📁 File Upload
Upload your study materials in **PDF, DOCX, TXT, or Markdown** format (up to 16MB). The backend extracts the text so the AI can actually read it — which is more than can be said for most students the night before an exam.

### 💬 AI Chat
Chat with an AI study assistant that has *actually read your notes* (powered by Groq's Llama 3.3 70B model). Ask it to explain a concept, quiz you, or just reassure you that the mitochondria is indeed the powerhouse of the cell.

You can attach multiple files to a chat session, giving the AI full context over everything you've uploaded. It's like a tutor who did all the reading. Every time. Without complaining.

### 📄 Output Generation
Select your files and generate any of the following with one click:

| Output Type | What it does |
|---|---|
| **Summary** | Condenses your notes into a structured overview |
| **Flashcards** | Creates Q&A pairs in JSON, rendered interactively |
| **Quiz** | 10 multiple-choice questions to test yourself |
| **Key Points** | Numbered list of the most important concepts |
| **Explain** | Explains everything from scratch, like you've never seen the subject before (no judgment) |
| **Custom** | Provide your own prompt — go wild |

All outputs are saved so you can come back to them later. No more regenerating the same flashcard set for the fourth time because you forgot to copy it.

### 📝 Cornell Notes
Generates structured Cornell-format notes from your uploaded files, complete with cues, detailed notes per cue, and a summary paragraph. The AI ensures the cue and notes arrays always match up, which is already more organized than any notes you've taken in a lecture hall.

### 📅 Study Calendar
Plan your study sessions. It's there. Use it. You know you won't, but it's there.

### ⚡ Playground
A full coding + study scratch pad with three modes:

- **Ask** — Ask the AI anything. Code help, concept explanations, "why does this bug only appear at 2am" — all valid.
- **Run** — Write Python code and execute it directly in the browser (server-side, 10-second timeout, so don't try to mine crypto).
- **Explain** — Paste code and get a plain-English step-by-step breakdown. Great for understanding code you wrote three weeks ago.

---

## Tech Stack

| Layer | Technology |
|---|---|
| Frontend | Vanilla HTML, CSS, JavaScript |
| Backend | Python + Flask |
| AI | Groq API (Llama 3.3 70B via OpenAI-compatible SDK) |
| Auth & Database | Supabase |
| PDF Parsing | pdfplumber |
| DOCX Parsing | python-docx |

No React. No 47 npm packages. Just clean, honest HTML and a Python backend that does what it's told.

---

## Setup

### Prerequisites
- Python 3.10+
- A [Groq](https://console.groq.com) API key (free tier available)
- A [Supabase](https://supabase.com) project

### Install dependencies

```bash
pip install -r requirements.txt
```

### Configure environment variables

Create a `.env` file in the project root:

```env
GROQ_API_KEY=your_groq_api_key_here
GROQ_MODEL=llama-3.3-70b-versatile   # optional, this is the default

SUPABASE_URL=https://your-project.supabase.co
SUPABASE_ANON_KEY=your_anon_key_here
SUPABASE_SERVICE_KEY=your_service_role_key_here
```

> ⚠️ **Never commit your `.env` file.** Add it to `.gitignore`. Seriously.

### Supabase Database Schema

You'll need the following tables in your Supabase project:

- `profiles` — user profile info (`id`, `full_name`, `avatar_url`)
- `files` — uploaded file metadata and extracted text (`id`, `user_id`, `filename`, `size`, `text_content`, `storage_path`, `uploaded_at`)
- `chat_sessions` — chat session metadata (`id`, `user_id`, `name`, `file_ids`, `system_prompt`, `created_at`)
- `chat_messages` — individual messages (`session_id`, `user_id`, `role`, `content`, `created_at`)
- `saved_outputs` — generated outputs (`id`, `user_id`, `type`, `file_ids`, `content`, `cornell_data`, `created_at`)

Enable Row Level Security (RLS) and make sure users can only access their own data. This is important. Don't skip it.

### Run the server

```bash
python main.py
```

The app runs on **port 8100** by default. Visit `http://localhost:8100` in your browser.

To change the port:

```bash
PORT=3000 python main.py
```

---

## API Reference

All endpoints under `/api/` require a `Bearer` token in the `Authorization` header (obtained from Supabase auth), except for `/api/config` and `/api/health`.

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/api/health` | Health check — returns model name |
| `GET` | `/api/config` | Returns public Supabase config for frontend |
| `GET` | `/api/me` | Get current user's profile |
| `GET` | `/api/files` | List uploaded files |
| `POST` | `/api/files/upload` | Upload a file (multipart/form-data) |
| `DELETE` | `/api/files/<id>` | Delete a file |
| `POST` | `/api/chat/session` | Create a new chat session |
| `GET` | `/api/chat/sessions` | List all chat sessions |
| `GET` | `/api/chat/<session_id>` | Get messages in a session |
| `POST` | `/api/chat/<session_id>` | Send a message |
| `DELETE` | `/api/chat/<session_id>` | Clear chat history |
| `POST` | `/api/output/generate` | Generate output from files |
| `GET` | `/api/output` | List saved outputs |
| `GET` | `/api/output/<id>` | Get a specific output |
| `DELETE` | `/api/output/<id>` | Delete an output |
| `POST` | `/api/cornell/generate` | Generate Cornell Notes |
| `POST` | `/api/playground/ask` | Ask the AI a question |
| `POST` | `/api/playground/run` | Execute Python code |
| `POST` | `/api/playground/explain` | Explain code |

---

## Limitations

- Uploaded files are stored temporarily on the server during text extraction, then deleted. The extracted text is what gets saved to Supabase.
- The code runner executes Python only, with a hard 10-second timeout. It is not sandboxed beyond that, so don't deploy this publicly without adding proper isolation.
- Maximum upload size is 16MB. If your PDF is larger than that, consider that perhaps your professor assigned too much reading.

---

## License

MIT. Do whatever you want with it. Study hard. Or don't. StudyAI will be here either way.
