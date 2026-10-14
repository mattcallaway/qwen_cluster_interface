# Qwen Collective: Implementation Plan (PLAN.md)

## 1. Project Overview & Goals
Build the first working, durable version of **Qwen Collective** — a locally hosted coordination server for a private, heterogeneous AI system (Control Server, Linux laptop compute nodes with standalone/distributed llama.cpp/Ollama, internet-connected VPS worker, and local Markdown/FTS5 knowledge repository).

## 2. Key Architecture & Technology Decisions
- **Backend**: Python 3.12+ with FastAPI, Pydantic v2, SQLite (with WAL mode & FTS5 full-text search).
- **Frontend**: Server-rendered HTML with Jinja2, HTMX for interactive components without full page reloads, clean responsive custom CSS (dark charcoal/slate navigation, neutral content, forest-teal accents, high readability, no bulky JS frameworks).
- **Security & Auth**: Session-based auth with salted password hashing (pbkdf2_sha256/bcrypt), CSRF protection for mutations, secret redaction, audit logging, localhost binding by default.
- **Node Communication**: Defensively parsed SSH connectivity & hardware inspection (read-only commands like `uname`, `lscpu`, `free`, `lsblk`, `ip`); HTTP API for inference endpoints and VPS worker.
- **Inference**: Support standalone llama.cpp server and Ollama endpoints, plus distributed inference group configuration generation (llama.cpp RPC command builder & manual execution docs/benchmark recorder).
- **Knowledge**: Durable Obsidian-compatible Markdown directory structure (`knowledge/system`, `projects`, `research`, `sources`, `decisions`, `journal`, `inbox`) indexed into SQLite FTS5 for fast full-text searching, atomic file writes.
- **Task Queue**: SQLite-backed persistent task queue with bounded worker loop, task statuses (`draft`, `queued`, `running`, `awaiting_approval`, `completed`, `failed`, `cancelled`), approval gates.
- **VPS Integration**: Online worker registration, secure token auth, health check, capability exchange, test task execution.

## 3. Explicit Non-Goals for Initial Version
- No custom model runtimes or vector databases (use SQLite FTS5).
- No automatic remote software installation or destructive SSH executions.
- No public port exposure or automated cloud publishing.
- No Celery/Redis dependencies or microservices orchestration.
- No fake hardware stats or simulated metrics; report genuine state or unknown.

## 4. File Structure
```text
qwen-collective/
├── PLAN.md
├── ARCHITECTURE.md
├── STATUS.md
├── README.md
├── requirements.txt
├── .env.example
├── app/
│   ├── __init__.py
│   ├── main.py
│   ├── config.py
│   ├── database.py
│   ├── models.py
│   ├── auth.py
│   ├── routes/
│   │   ├── __init__.py
│   │   ├── auth.py
│   │   ├── dashboard.py
│   │   ├── machines.py
│   │   ├── inference.py
│   │   ├── chat.py
│   │   ├── knowledge.py
│   │   ├── tasks.py
│   │   └── settings.py
│   ├── services/
│   │   ├── __init__.py
│   │   ├── machine_registry.py
│   │   ├── hardware_probe.py
│   │   ├── inference.py
│   │   ├── knowledge.py
│   │   ├── tasks.py
│   │   └── vps.py
│   ├── templates/
│   │   ├── base.html
│   │   ├── login.html
│   │   ├── dashboard.html
│   │   ├── machines/
│   │   │   ├── list.html
│   │   │   ├── add_wizard.html
│   │   │   └── detail.html
│   │   ├── inference/
│   │   │   ├── index.html
│   │   │   └── group_detail.html
│   │   ├── chat.html
│   │   ├── knowledge/
│   │   │   ├── index.html
│   │   │   └── note.html
│   │   ├── tasks/
│   │   │   ├── index.html
│   │   │   └── detail.html
│   │   └── settings.html
│   └── static/
│       ├── css/style.css
│       └── js/htmx.min.js
├── worker/
│   └── vps_worker.py
├── tests/
│   ├── test_db.py
│   ├── test_auth.py
│   ├── test_machines.py
│   ├── test_probe_parser.py
│   ├── test_inference.py
│   ├── test_knowledge.py
│   ├── test_tasks.py
│   └── test_vps.py
└── data/ (configurable runtime directory with knowledge/ and sqlite db)
```

## 5. Development Phases
- **Phase 1**: Setup project skeleton, configuration, database schema, auth, base templates & CSS styling.
- **Phase 2**: Machine Registry, Add Machine Wizard, connectivity checker, defensive SSH hardware probe parser, role recommendation engine, machine detail & command instructions.
- **Phase 3**: Knowledge & Memory repository with Obsidian-compatible structure, safe file writer, SQLite FTS5 indexer and search UI.
- **Phase 4**: Inference Management: standalone endpoints (llama.cpp/Ollama) with genuine health check, distributed llama.cpp RPC group definitions, command generator, benchmark recorder, and Chat interface.
- **Phase 5**: Persistent Task Queue, bounded executor, approval states, VPS Worker registration and companion script (`worker/vps_worker.py`).
- **Phase 6**: Comprehensive automated test suite, verification, documentation (`README.md`, `STATUS.md`).
