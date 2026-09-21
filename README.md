# CloudOps Sentinel - Incident Response Self-RAG Copilot

CloudOps Sentinel is a copilot for cloud operations and incident response.
An on-call engineer asks a question about a production problem.
The copilot finds the answer in private runbooks and SOPs first.
It grades its own evidence and its own answer before it replies.
It searches the internet only when the private knowledge base does not have enough evidence.

Author: Muhammad Syakeer bin Abdul Rahman ([@SyakeerRahman](https://github.com/SyakeerRahman))

## Contents

1. [What the copilot does](#what-the-copilot-does)
2. [How it works](#how-it-works)
3. [Tech stack and the reason for each part](#tech-stack-and-the-reason-for-each-part)
4. [Prerequisites](#prerequisites)
5. [Setup from scratch, step by step](#setup-from-scratch-step-by-step)
6. [Use the copilot](#use-the-copilot)
7. [Run with Docker](#run-with-docker)
8. [Configuration reference](#configuration-reference)
9. [API reference](#api-reference)
10. [Project structure](#project-structure)
11. [Troubleshooting](#troubleshooting)
12. [Known limits](#known-limits)
13. [License and acknowledgements](#license-and-acknowledgements)

## What the copilot does

- It answers incident questions from your own runbooks, SOPs, and postmortems.
- It checks that each retrieved document is relevant before it uses the document.
- It checks that each claim in the answer has support in the evidence (IsSUP).
- It checks that the answer addresses the question (IsUSE).
- It rewrites a weak search query and tries again.
- It uses Tavily web search as a last resort, and it labels web evidence as external.
- It keeps conversation memory per session, so a follow-up question keeps the incident context.
- It accepts new documents from the web UI and adds them to the knowledge base immediately.
- It writes an audit record for each question and answer.

## How it works

### The Self-RAG workflow

A normal RAG system retrieves documents and answers from them without a check.
A Self-RAG system adds grading steps, so it can find and correct a bad retrieval or a bad answer.

```text
User question (with a thread_id)
        |
        v
Load memory for this thread (SQLite checkpointer)
        |
        v
Contextualize: rewrite a follow-up into a standalone question
        |
        v
Decide retrieval: does the question need evidence?
   |                      |
   no                     yes
   |                      v
   |              Retrieve from Pinecone (top K chunks)
   |                      |
   |                      v
   |              Grade relevance of each chunk
   |                 |                  |
   |              relevant          none relevant
   |                 |                  |
   |                 |                  v
   |                 |          Rewrite the query, retrieve again
   |                 |          (up to MAX_RETRIEVAL_REWRITES)
   |                 |                  |
   |                 |                  v
   |                 |          Still none: web search (Tavily)
   |                 |          (up to MAX_WEB_REWRITES), then grade again
   |                 v
   |              Generate the answer from the relevant evidence
   |                 |
   |                 v
   |              IsSUP: is each claim supported?  -- no --> Revise (up to MAX_SUPPORT_RETRIES)
   |                 |
   |                 v
   |              IsUSE: does the answer address the question?  -- no --> Rewrite and retry
   v                 |
Direct answer        |
   |                 |
   +--------+--------+
            v
Commit memory (SQLite checkpoint) -> return answer, route, sources, and trace
```

If all retries fail, the copilot returns a safe "no reliable evidence" answer.
It does not guess a troubleshooting action.

The code for this graph is in `src/self_rag.py`, in the function `build_graph()`.

### The two SQLite databases

| File | Written by | Contents |
|---|---|---|
| `data/langgraph_memory.sqlite` | The LangGraph checkpointer | The conversation memory for each `thread_id` |
| `data/audit.db` | `src/db.py` | One row per question: answer, route, grades, sources, and trace |

### The diagram in `docs/`

`docs/architecture_diagram.png` shows the target design for a full deployment.
Some parts of that diagram are not in this code yet: the `/ingest`, `/admin`, and `/history` endpoints, Nginx, Docker Compose, and DigitalOcean.
The text diagram above shows the code as it is now.

## Tech stack and the reason for each part

| Part | Technology | Reason |
|---|---|---|
| Workflow | LangGraph | Self-RAG needs loops and conditional routes. A graph shows these clearly. LangGraph also gives persistent state per thread. |
| LLM | OpenAI `gpt-5-mini` | It does routing, query rewrites, grading, and generation. A small model keeps the cost of the many grading calls low. |
| Embeddings | OpenAI `text-embedding-3-large` (3072 dimensions) | It gives high retrieval quality for technical text. |
| Vector store | Pinecone (serverless) | It is a managed service, so there is no database server to run. The free tier is enough for this project. |
| Web fallback | Tavily | Its search API returns clean text content that is ready for an LLM. |
| Memory and audit | SQLite | It needs no server. For production, replace it with PostgreSQL or another LangGraph checkpointer. |
| API | FastAPI | It validates requests with Pydantic and runs the blocking graph in a thread pool. |
| UI | HTML, CSS, and JavaScript | There is no build step. FastAPI serves the files directly. |
| Packaging | Docker | One image runs the same way on every machine. |

## Prerequisites

Get these before you start:

1. **Python 3.11 or later.** The Docker image uses Python 3.11.
2. **Git.**
3. **An OpenAI API key.** Get it at <https://platform.openai.com/api-keys>. The account must have billing enabled.
4. **A Pinecone API key.** Get it at <https://app.pinecone.io>. The free tier is enough.
5. **A Tavily API key.** Get it at <https://app.tavily.com>. The free tier is enough.
6. **Docker Desktop** (optional). You need it only for the Docker steps.

You do not need to create the Pinecone index yourself. The ingestion script creates it.

## Setup from scratch, step by step

Each step gives the reason, the command, and the output that you should see.
The commands show Windows PowerShell first, then macOS or Linux where they are different.

### Step 1 - Clone the repository

**Why:** You need a local copy of the code.

```bash
git clone https://github.com/SyakeerRahman/cloudops-sentinel-copilot.git
cd cloudops-sentinel-copilot
```

**Expected output:**

```text
Cloning into 'cloudops-sentinel-copilot'...
...
Resolving deltas: 100% (...), done.
```

### Step 2 - Create and activate a virtual environment

**Why:** A virtual environment keeps the project packages apart from other Python projects on your machine.

Windows (PowerShell):

```powershell
python -m venv venv
venv\Scripts\Activate.ps1
```

macOS or Linux:

```bash
python3 -m venv venv
source venv/bin/activate
```

**Expected output:** The command prints nothing. Your prompt starts with `(venv)`.

If PowerShell stops the script with a message about the execution policy, run this command one time, then try again:

```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
```

### Step 3 - Install the dependencies

**Why:** The app needs FastAPI, LangGraph, LangChain, the Pinecone and Tavily clients, and the document loaders.

```bash
pip install -r requirements.txt
```

**Expected output:** The last line is similar to this:

```text
Successfully installed fastapi-... langgraph-... pinecone-... tavily-python-... uvicorn-...
```

### Step 4 - Create the `.env` file

**Why:** The app reads all API keys and settings from `.env`. `src/config.py` loads this file.
Git ignores `.env`, so your keys stay out of the repository.

Windows (PowerShell):

```powershell
Copy-Item .env.example .env
```

macOS or Linux:

```bash
cp .env.example .env
```

Open `.env` and fill in the three required keys:

```env
OPENAI_API_KEY=sk-...
PINECONE_API_KEY=pcsk_...
TAVILY_API_KEY=tvly-...
```

Keep the other values as they are for the first run.
The [Configuration reference](#configuration-reference) explains each value.

### Step 5 - Check the configuration

**Why:** This step finds a missing key before you spend time on the next steps.

```bash
python test.py
```

**Expected output:**

```text
OpenAI API Key: set (...a1b2)
OpenAI Model: gpt-5-mini
```

If the first line shows `MISSING`, the app did not find `OPENAI_API_KEY`.
Make sure that `.env` is in the project root folder, and that the key name has no spaces.

### Step 6 - Build the knowledge base in Pinecone

**Why:** The copilot answers from the documents in Pinecone.
This step reads the files in `documents/`, cuts them into chunks, embeds the chunks, and stores them in Pinecone.
The repository includes three sample documents:

- `documents/checkout-api-runbook.md` - the Checkout API 502 runbook
- `documents/payments-high-cpu-runbook.md` - the Payments high CPU runbook
- `documents/deployment-rollback-sop.md` - the production deployment rollback SOP

You can add your own PDF, TXT, MD, or DOCX files to `documents/` before you run this step.

```bash
python data_ingestion.py
```

**Expected output** (with the three sample documents):

```text
====================================================================
CloudOps Sentinel - Pinecone Knowledge Base Ingestion
====================================================================
OpenAI embedding model : text-embedding-3-large
Embedding dimension    : 3072
Pinecone index         : cloudops-sentinel-openai-self-rag
Pinecone namespace     : incident-runbooks
Documents directory    : ...\cloudops-sentinel-copilot\documents

[1/2] Pinecone index is ready.
[2/2] Ingesting 3 document(s)...
      - checkout-api-runbook.md
      - deployment-rollback-sop.md
      - payments-high-cpu-runbook.md

Knowledge base is ready.
Indexed chunks: 8
Index: cloudops-sentinel-openai-self-rag
Namespace: incident-runbooks
```

On the first run, the script creates the index. This can take up to one minute.

**How the ingestion works:**

- The text splitter makes chunks of 800 characters with 160 characters of overlap.
  The overlap keeps a sentence at a chunk edge readable in both chunks.
- Each chunk gets a stable ID from the file name, the chunk position, and a hash of the text.
  If you run the script again, Pinecone updates the same vectors. It does not make duplicates.
- The script and the app both use `src/vectorstore.py`.
  Thus, ingestion and retrieval always use the same embedding model, index, and namespace.

### Step 7 - Start the app

**Why:** The app serves the web UI and the API.

```bash
python app.py
```

**Expected output:**

```text
INFO:     Will watch for changes in these directories: ['...\cloudops-sentinel-copilot']
INFO:     Uvicorn running on http://0.0.0.0:8080 (Press CTRL+C to quit)
INFO:     Started reloader process [...] using WatchFiles
INFO:     Started server process [...]
INFO:     Waiting for application startup.
INFO:     Application startup complete.
```

`python app.py` uses port **8080** and restarts when you change a file.
If you prefer port 8000, run `uvicorn app:app --reload` instead.

At startup, the app creates `data/audit.db` if the file does not exist.

### Step 8 - Check that the app runs

**Why:** The health endpoint confirms that the server answers, before you use the UI.

Windows (PowerShell):

```powershell
Invoke-RestMethod http://127.0.0.1:8080/api/health
```

macOS or Linux:

```bash
curl http://127.0.0.1:8080/api/health
```

**Expected output:**

```json
{"status": "ok", "service": "cloudops-sentinel-self-rag"}
```

### Step 9 - Open the UI

Open <http://127.0.0.1:8080> in your browser.
The page shows the chat panel, example incident prompts, and the **Runbook Vault** upload panel.

The browser keeps your session ID (`thread_id`) in local storage.
Thus, the conversation memory stays when you reload the page.

## Use the copilot

### Ask a question that the runbooks cover

Type this question in the UI:

> Our checkout API is returning 502 errors after deployment. What should the on-call engineer check first?

Or send it to the API.

Windows (PowerShell):

```powershell
$body = @{ question = "Our checkout API is returning 502 errors after deployment. What should the on-call engineer check first?"; thread_id = "demo-001" } | ConvertTo-Json
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8080/api/chat -ContentType "application/json" -Body $body
```

macOS or Linux:

```bash
curl -X POST http://127.0.0.1:8080/api/chat \
  -H "Content-Type: application/json" \
  -d '{"question": "Our checkout API is returning 502 errors after deployment. What should the on-call engineer check first?", "thread_id": "demo-001"}'
```

**Expected output:** The response has this shape.
The answer text, the counts, and the paths change from run to run.

```json
{
  "answer": "1. Check the load balancer target health ...",
  "route": "Private Runbooks",
  "used_web_search": false,
  "support_status": "fully_supported",
  "usefulness": "useful",
  "sources": [
    {"type": "internal", "title": "checkout-api-runbook.md", "source": "...\\documents\\checkout-api-runbook.md", "url": null, "page": null}
  ],
  "trace": [
    "Memory: new incident session",
    "Retrieval decision: True",
    "Internal retrieval: 5 chunks",
    "Relevance grade (internal): 3/5 relevant",
    "Generated answer from internal evidence",
    "Support check: fully_supported",
    "Usefulness check: useful",
    "SQLite memory checkpoint updated"
  ],
  "thread_id": "demo-001",
  "memory_turns": 1
}
```

**How to read the response:**

| Field | Meaning |
|---|---|
| `route` | The evidence source: `Private Runbooks`, `Internet Search`, `General Knowledge`, or `No Reliable Evidence`. |
| `support_status` | The IsSUP grade: `fully_supported`, `partially_supported`, or `no_support`. |
| `usefulness` | The IsUSE grade: `useful` or `not_useful`. |
| `sources` | The documents that passed the relevance grade. |
| `trace` | Each step that the graph ran, in order. Use it to see why the copilot chose a route. |
| `memory_turns` | The number of turns that this `thread_id` has in memory. |

### Ask a follow-up question

**Why:** This shows the persistent memory.

Send this question with the **same** `thread_id` (`demo-001`):

> What should I check next if that does not work?

**Expected output:** The first trace line changes to this:

```text
Memory contextualized question: <a standalone question about the checkout API 502 errors>
```

The `contextualize` step uses the earlier turn to rewrite the follow-up into a full question.
Then the normal Self-RAG flow runs on the rewritten question.
`memory_turns` is now `2`.

To start a new incident, click **New Session** in the UI, or send a new `thread_id`.

### Add a document from the UI

**Why:** You can add knowledge without a restart and without a new run of the ingestion script.

1. In the **Runbook Vault** panel, choose a PDF, TXT, MD, or DOCX file.
2. Click **Add to knowledge base**.
3. Ask a question that only the new document can answer.

The app saves the file in `uploads/`.
Then it uses the same ingestion code as `data_ingestion.py` to add the chunks to the same index and namespace.

To upload from the command line:

```bash
curl -X POST http://127.0.0.1:8080/api/upload -F "file=@my-runbook.md"
```

**Expected output:**

```json
{"filename": "my-runbook.md", "chunks_indexed": 4, "namespace": "incident-runbooks"}
```

The app rejects other file types with HTTP 400 and the message `Supported: PDF, TXT, MD, DOCX`.

### Trigger the web search fallback

**Why:** This shows how the copilot acts when the private knowledge base has no answer.

Ask a question that no runbook covers, for example:

> What are the common causes of a Kubernetes ImagePullBackOff error?

**Expected output:** The trace shows the private search, the failed grade, the query rewrites, and then the web search.
For example:

```text
Relevance grade (internal): 0/5 relevant
Rewrote internal query: ...
...
Prepared internet search query: ...
Internet search: 5 results
Relevance grade (web): 4/5 relevant
Generated answer from web evidence
```

`route` is `Internet Search` and `used_web_search` is `true`.
The answer labels the guidance as external.

The decide step can also choose `General Knowledge` for a generic concept question.
In that case, the trace shows `Retrieval decision: False` and `Generated direct answer`.

### Read the audit log

**Why:** The audit log shows each question, the route, the grades, and the sources.

```bash
curl "http://127.0.0.1:8080/api/audits?limit=5"
```

**Expected output:** A JSON list of the 5 most recent records, newest first.
Each record has `id`, `created_at`, `question`, `answer`, `route`, `used_web`, `support_status`, `usefulness`, `trace_json`, and `sources_json`.

## Run with Docker

**Why:** Docker runs the app without a local Python setup.

You must run Step 6 (ingestion) one time before you use the container.
The container does not run the ingestion script.

1. Build the image:

   ```bash
   docker build -t cloudops-sentinel .
   ```

2. Run the container with your `.env` file:

   ```bash
   docker run --rm --env-file .env -p 8080:8080 cloudops-sentinel
   ```

3. Open <http://127.0.0.1:8080>.

**Expected output** from step 2:

```text
INFO:     Uvicorn running on http://0.0.0.0:8080 (Press CTRL+C to quit)
INFO:     Application startup complete.
```

The container writes the SQLite files inside the container.
When the container stops, the memory and the audit log are gone.
To keep them, mount a volume: add `-v ${PWD}/data:/app/data` to the `docker run` command.

## Configuration reference

All values go in `.env`. Only the three API keys are required.

| Variable | Default | Purpose |
|---|---|---|
| `OPENAI_API_KEY` | (none) | OpenAI key for the LLM and the embeddings. |
| `PINECONE_API_KEY` | (none) | Pinecone key. |
| `TAVILY_API_KEY` | (none) | Tavily key. Without it, the web fallback returns no results. |
| `OPENAI_MODEL` | `gpt-5-mini` | The chat model for all LLM steps. |
| `EMBEDDING_MODEL` | `text-embedding-3-large` | The embedding model. |
| `EMBEDDING_DIMENSION` | `3072` | The vector size. It must match the Pinecone index dimension. |
| `PINECONE_INDEX_NAME` | `cloudops-sentinel-openai-self-rag` | The index name. The script creates the index if it does not exist. |
| `PINECONE_NAMESPACE` | `incident-runbooks` | The namespace for all chunks. |
| `PINECONE_CLOUD` | `aws` | The cloud for a new serverless index. |
| `PINECONE_REGION` | `us-east-1` | The region for a new serverless index. |
| `TOP_K` | `5` | The number of chunks for each retrieval. |
| `MAX_SUPPORT_RETRIES` | `2` | The maximum number of revisions after a failed IsSUP check. |
| `MAX_RETRIEVAL_REWRITES` | `2` | The maximum number of private query rewrites before the web fallback. |
| `MAX_WEB_REWRITES` | `2` | The maximum number of web query rewrites before the "no answer" result. |
| `DATABASE_PATH` | `data/audit.db` | The audit database path. The memory database path is fixed. |

Higher retry limits can improve the answer, but each retry adds LLM calls, time, and cost.

## API reference

| Method | Path | Body | Returns |
|---|---|---|---|
| `GET` | `/` | - | The web UI |
| `GET` | `/api/health` | - | `{"status": "ok", "service": "cloudops-sentinel-self-rag"}` |
| `POST` | `/api/chat` | JSON: `question` (2 to 2000 characters), `thread_id` (3 to 120 characters) | `ChatResponse` (see above) |
| `POST` | `/api/upload` | Form data: `file` (PDF, TXT, MD, or DOCX) | `{"filename", "chunks_indexed", "namespace"}` |
| `GET` | `/api/audits?limit=N` | - | The last N audit records (N is 1 to 100, default 20) |

FastAPI also serves interactive API documentation at <http://127.0.0.1:8080/docs>.

## Project structure

```text
cloudops-sentinel-copilot/
├── app.py                 # FastAPI app and endpoints
├── data_ingestion.py      # One command to build the Pinecone knowledge base
├── test.py                # Configuration check
├── src/
│   ├── config.py          # Loads .env into one Settings object
│   ├── vectorstore.py     # Embeddings, Pinecone index, retriever (shared by ingestion and app)
│   ├── ingestion.py       # File loaders, chunking, stable chunk IDs
│   ├── self_rag.py        # The LangGraph Self-RAG workflow and memory
│   ├── models.py          # API request and response models
│   └── db.py              # SQLite audit log
├── documents/             # Source documents for the initial knowledge base
├── uploads/               # Files uploaded from the UI
├── data/                  # SQLite files (created at run time, ignored by Git)
├── templates/index.html   # UI page
├── static/                # UI styles and JavaScript
├── docs/                  # Problem statement and architecture diagram
├── Self-Rag-Code/         # Standalone learning notebooks (the app does not use them)
├── .env.example           # Template for .env
├── Dockerfile
└── requirements.txt
```

## Troubleshooting

| Message or symptom | Cause | Fix |
|---|---|---|
| `OPENAI_API_KEY is not configured` or `OPENAI_API_KEY is missing` | `.env` has no OpenAI key, or the app cannot find `.env`. | Put `.env` in the project root and set the key. |
| `PINECONE_API_KEY is not configured` or `PINECONE_API_KEY is missing` | `.env` has no Pinecone key. | Set the key in `.env`. |
| `Pinecone index '...' has dimension ..., but ... is configured for ...` | The index was made with a different embedding size. Pinecone cannot change the dimension of an index. | Set a new `PINECONE_INDEX_NAME`, then run `python data_ingestion.py` again. |
| `[2/2] No supported documents found.` | `documents/` has no PDF, TXT, MD, or DOCX file. | Add a file, then run the script again. |
| The trace shows `Internet search unavailable: TAVILY_API_KEY missing` | `.env` has no Tavily key. | Set `TAVILY_API_KEY`. |
| Every answer has the route `No Reliable Evidence` | The knowledge base is empty, or the namespace in `.env` is different from the namespace used at ingestion. | Run Step 6 again with the same `.env`. |
| PowerShell does not activate the virtual environment | The execution policy blocks scripts. | Run `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`. |
| Port 8080 is already in use | Another program uses the port. | Stop the other program, or run `uvicorn app:app --reload --port 8001`. |

## Known limits

- There is no automated test suite yet. `test.py` checks the configuration only.
- SQLite supports one server process. For more than one instance, use a PostgreSQL checkpointer.
- The API has no authentication. Do not expose it to the internet as it is.
- If you change the chunk size in `src/ingestion.py`, the chunk IDs change.
  The old vectors stay in Pinecone. Delete the namespace, then run the ingestion again.

## License and acknowledgements

This project is released under the MIT License. See [LICENSE](LICENSE).

Acknowledgements: This project is based on the CloudOps Sentinel tutorial by Bappy Ahmed (MIT License).
