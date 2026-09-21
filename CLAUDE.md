# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

CloudOps Sentinel: a FastAPI + LangGraph Self-RAG copilot for incident response. It answers from a private Pinecone knowledge base (runbooks, SOPs), self-grades the evidence and the answer, and falls back to Tavily web search only when private evidence is insufficient. Conversation memory persists per `thread_id` through a LangGraph SQLite checkpointer.

## Commands

Windows, PowerShell 5.1. Python 3.11 (matches the Dockerfile).

| Task | Command |
|---|---|
| Install | `pip install -r requirements.txt` |
| Build / refresh the Pinecone KB from `documents/` | `python data_ingestion.py` |
| Run the app (port 8080, reload on) | `python app.py` |
| Run the app (port 8000) | `uvicorn app:app --reload` |
| Docker build and run | `docker build -t cloudops-sentinel .` then `docker run --env-file .env -p 8080:8080 cloudops-sentinel` |

- There is no test suite and no linter config. `test.py` is only a settings smoke check (`python test.py`). It prints a masked key.
- Required `.env` keys: `OPENAI_API_KEY`, `PINECONE_API_KEY`, `TAVILY_API_KEY`. All other settings have defaults in `src/config.py`.
- `.env.example` lists every setting. There is no `docker-compose.yml`.
- `README.md` is written to ASD-STE100 (short sentences, active voice, no em-dashes). Its "Expected output" blocks come from the code's print and trace strings. If you change those strings, update the README too.

## Architecture

### Request flow

`POST /api/chat` in `app.py` calls `run_self_rag(question, thread_id)` in `src/self_rag.py` in a threadpool, then writes an audit row via `src/db.py`. `POST /api/upload` saves the file to `uploads/` and calls `ingest_file`, which adds chunks to the same index and namespace as offline ingestion.

### The Self-RAG graph (`src/self_rag.py`)

All logic lives in `build_graph()`. The graph is compiled once, lazily, into the module-level `_graph`.

- Path: `contextualize` -> `decide_retrieval` -> (`direct` | `retrieve`) -> `grade` -> `generate` -> `support` (IsSUP) <-> `revise` -> `usefulness` (IsUSE) -> `commit_memory`.
- Fallback: `grade` or `usefulness` failures route to `rewrite_internal` (loops to `retrieve`) until `MAX_RETRIEVAL_REWRITES`. After that they route to `rewrite_web` -> `web_search` -> `grade`, until `MAX_WEB_REWRITES`. After that they route to `no_answer`.
- The routing functions (`route_after_*`) branch on `state["source_mode"]` (`internal` / `web` / `direct` / `none`). A new retrieval source must set `source_mode`, or the routing breaks.
- Every node returns `trace` via `_trace()`, which copies the list and appends one item. The UI and the audit table show this trace.
- Each LLM judgment uses `with_structured_output` with a small Pydantic model defined at the top of the file.

### Memory semantics

- `memory` is the only state field with a reducer (`operator.add`). It is the only field that accumulates across turns in a thread.
- `run_self_rag` passes a full `initial` state on every call. This resets all other fields per turn, including rewrite counters and trace.
- `commit_memory` appends one `User/Assistant` entry. `contextualize` reads the last 4 entries to rewrite follow-ups into standalone questions.
- The checkpointer path is hard-coded to `data/langgraph_memory.sqlite`. It does not use `DATABASE_PATH`, which only controls the audit DB (`data/audit.db`).

### Ingestion and vector store invariant

- `src/vectorstore.py` is the single source of the embedding model, dimension, index, and namespace. Both `data_ingestion.py` and the upload endpoint go through it. Keep it that way, or ingestion and retrieval will diverge.
- `ensure_index()` creates the index if it is missing. It raises an error if the existing index dimension differs from `EMBEDDING_DIMENSION`. To change embedding models, use a new index name.
- Chunk IDs from `_stable_chunk_id` (filename + position + content hash) make re-ingestion idempotent. Changing `chunk_size` or `chunk_overlap` in `ingest_file` changes the IDs and leaves stale vectors in Pinecone.

### Other folders

- `templates/index.html` + `static/app.js` + `static/styles.css`: the single-page UI. It stores `thread_id` in the browser and renders `trace`, `sources`, and `route` from `ChatResponse` (`src/models.py`). If you change the response fields, update `app.js` too.
- `Self-Rag-Code/`: standalone tutorial notebooks, with their own `requirements.txt`. The app does not import them.
- `template.py`: the one-time scaffold script. `steps.md`: the build order the project followed.
- `data/*.sqlite*` and `data/*.db` are local runtime state and are gitignored. The app recreates them on startup.
