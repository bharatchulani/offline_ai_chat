# Windows-first offline AI agent

This project runs a private local AI stack that can be prepared on an internet-connected Windows machine and transferred to an offline machine.

```text
Browser -> Open WebUI (Docker) -> Ollama -> Qwen
Documents -> FastAPI -> Ollama embeddings -> FAISS -> Ollama chat
```

After the installers, container image, Python wheels, and models are collected, inference does not require an internet connection.

## Recommended daily workflow: one interface

Use **Open WebUI at <http://localhost:3000>** as the single interface for chat, model selection, file uploads, and reusable knowledge. Documents uploaded to Open WebUI remain in its own persistent Docker volume.

The FastAPI/FAISS service in `app/` is retained as an optional developer API and reference implementation, but it is not required for normal use. Avoid uploading the same documents to both systems unless you deliberately want two independent indexes.

Parquet analytics is the exception: Open WebUI calls the local FastAPI tool service to query Parquet files with DuckDB. The files remain local and are not embedded as documents.

## What is included

- `compose.yaml`: persistent Open WebUI connected to Windows-hosted Ollama.
- `scripts/Prepare-Airgap.ps1`: pulls models and Open WebUI, exports them, optionally downloads Python wheels, and creates SHA-256 checksums.
- `scripts/Install-Airgap.ps1`: restores the Docker image, complete Ollama model store, and optional Python wheels.
- `app/`: small FastAPI RAG service using the Ollama HTTP API and a persistent FAISS index.
- `tests/`: starter unit tests for document chunking.

The scripts default to `qwen3:8b` for chat and `nomic-embed-text` for embeddings. Both can be changed through parameters or environment variables.

## 1. Verify the Windows tools

Open PowerShell in this directory:

```powershell
git --version
python --version
docker --version
docker info
ollama --version
```

Start Docker Desktop and Ollama if either service is not running.

## 2. Pull and test the local models

```powershell
ollama pull qwen3:8b
ollama pull nomic-embed-text
ollama list
ollama run qwen3:8b "Reply with: local model working"
```

Model names are configurable; the chat and embedding model must exist in `ollama list` before the RAG service is used.

## 3. Run Open WebUI

```powershell
docker compose up -d
docker compose ps
```

Open <http://localhost:3000>. The compose file connects the container to Ollama at `http://host.docker.internal:11434` and stores UI data in a named Docker volume.

Stop the UI without deleting its data:

```powershell
docker compose down
```

## 4. Run the RAG API

Python 3.11 or 3.12 is recommended.

```powershell
python -m venv .venv
Set-ExecutionPolicy -Scope Process Bypass
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements-dev.txt
Copy-Item .env.example .env
```

The application reads environment variables directly. To use the `.env` values in the current PowerShell session:

```powershell
Get-Content .env | ForEach-Object {
    if ($_ -match '^[^#].*=') {
        $name, $value = $_ -split '=', 2
        Set-Item -Path "Env:$name" -Value $value
    }
}

python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

Interactive API documentation is at <http://127.0.0.1:8000/docs>.

### Index a document

Supported input types are TXT, Markdown, PDF, and DOCX. The upload limit is 50 MB.

```powershell
curl.exe -X POST http://127.0.0.1:8000/documents `
  -F "file=@C:\path\to\document.pdf"
```

### Ask a question

```powershell
$body = @{
    question = "What are the main findings?"
    top_k = 4
} | ConvertTo-Json

Invoke-RestMethod `
  -Method Post `
  -Uri http://127.0.0.1:8000/query `
  -ContentType "application/json" `
  -Body $body
```

Responses contain the answer plus the retrieved source chunks and similarity scores. The index persists under `data/`, which is deliberately excluded from Git.

If you change the embedding model after indexing documents, remove `data/index.faiss` and `data/metadata.json`, restart the API, and ingest the documents again.

### API endpoints

- `GET /health`: checks Ollama connectivity and reports configured models.
- `POST /documents`: extracts, chunks, embeds, and indexes one uploaded document.
- `POST /query`: retrieves relevant chunks and asks the local chat model to answer with citations.

## Local Parquet analytics

Place one or more `.parquet` files under `data/parquet/`. Subdirectories are supported and `data/` is excluded from Git.

```powershell
New-Item -ItemType Directory -Force data\parquet
Copy-Item "C:\path\to\*.parquet" data\parquet\
```

Start the API so Docker containers can reach it:

```powershell
.\.venv\Scripts\Activate.ps1
python -m uvicorn app.main:app --env-file .env --host 0.0.0.0 --port 8000
```

The analytics API deliberately does not accept arbitrary SQL. It exposes structured, read-only operations confined to `data/parquet/`:

- `GET /analytics/datasets`: list available files.
- `POST /analytics/schema`: inspect column names and types.
- `POST /analytics/summary`: calculate per-column DuckDB statistics.
- `POST /analytics/query`: select, filter, group, aggregate, order, and limit results.
- `POST /analytics/join-query`: join multiple Parquet datasets, filter, group, aggregate, order, limit, and return the generated SQL preview.

Use `dataset: "*"` to query all Parquet files together with schema unioning. Result sets are capped by `ANALYTICS_MAX_ROWS` (1,000 by default).

For joins, prefer qualified column names like `claims.patient_id`, `patients.patient_id`, or `providers.provider_npi`. The API rejects ambiguous unqualified columns instead of guessing. The endpoint returns `sql_preview` with the generated DuckDB SQL so you can inspect what ran.

### Connect the analytics tools to Open WebUI

1. Keep the FastAPI tool service running on port 8000.
2. In Open WebUI, open the admin/settings area for external tools or OpenAPI tool servers.
3. Add `http://host.docker.internal:8000/openapi.json` as the OpenAPI specification URL. If the form requests a base URL separately, use `http://host.docker.internal:8000`.
4. Enable the resulting analytics tools for your custom model.
5. Ask the model to list datasets before querying, then request a schema, summary, or grouped statistic.

Example prompts:

- `List the available Parquet datasets.`
- `Describe the schema of sales.parquet.`
- `Summarize every column in sales.parquet.`
- `Group sales.parquet by region and calculate count and average revenue.`
- `Join claims.parquet to providers.parquet on provider_npi. Group by providers.provider_specialty. Return count of claims and sum of claims.total_paid.`
- `Preview the SQL only for joining claims.parquet to patients.parquet on patient_id and counting claims by patient gender.`

For metric-style questions, put categories/dimensions in `group_by` and numeric measures in `metrics`. For example, use `providers.provider_specialty` in `group_by`, but use `sum(claims.total_paid)` as a metric. Do not group by `claims.total_paid` unless you literally want one group for each paid amount.

For the health-claims dummy data, useful join keys are likely:

| Left dataset | Right dataset | Join key |
| --- | --- | --- |
| `claims.parquet` | `patients.parquet` | `patient_id` |
| `claims.parquet` | `providers.parquet` | `provider_npi` |
| `claims.parquet` | `diagnosis_xwalk.parquet` | `diagnosis_code` |
| `claim_lines.parquet` | `claims.parquet` | `claim_id` |
| `enrollment.parquet` | `patients.parquet` | `patient_id` |

Open WebUI labels for adding an OpenAPI tool server can vary by release. The tool URL must use `host.docker.internal`, not `127.0.0.1`, because Open WebUI runs inside Docker.

## 5. Prepare an air-gap bundle

Run this on the internet-connected preparation machine:

```powershell
.\scripts\Prepare-Airgap.ps1 -IncludePythonWheels
```

The default bundle is written to `artifacts/` and contains:

- `docker/open-webui.tar`
- `ollama/models/` (the complete Ollama manifest/blob store)
- `ollama/models.txt`
- `wheels/`
- `SHA256SUMS.txt`

To select different models or output location:

```powershell
.\scripts\Prepare-Airgap.ps1 `
  -Models @("qwen3:4b", "nomic-embed-text") `
  -OutputDirectory "D:\offline-ai-bundle" `
  -IncludePythonWheels
```

Use `-SkipDownloads` to export already-downloaded models and the existing Docker image without pulling again.

Also copy these items to the transfer drive:

- This project directory.
- The Ollama Windows installer.
- Docker Desktop's installer if the target machine does not already have Docker.
- A compatible offline Python installer if Python is not installed on the target.

For stronger reproducibility, pass a versioned Open WebUI image tag instead of `main`:

```powershell
.\scripts\Prepare-Airgap.ps1 -OpenWebUIImage "ghcr.io/open-webui/open-webui:VERSION"
```

## 6. Restore on the offline machine

Install and start Ollama and Docker Desktop first. Then open PowerShell in the copied project:

```powershell
.\scripts\Install-Airgap.ps1 `
  -BundleDirectory "D:\offline-ai-bundle" `
  -InstallPythonWheels

ollama list
docker image ls
docker compose up -d
```

`Install-Airgap.ps1` honors `OLLAMA_MODELS` when it is set; otherwise it restores models beneath `%USERPROFILE%\.ollama\models`. Restart Ollama after restoration.

## 7. Validate the project

```powershell
python -m pytest
python -m compileall app tests
```

Test the live service after Ollama is running:

```powershell
Invoke-RestMethod http://127.0.0.1:8000/health
```

## Configuration

| Variable | Default | Purpose |
| --- | --- | --- |
| `OLLAMA_URL` | `http://127.0.0.1:11434` | Ollama server used by FastAPI |
| `OLLAMA_CHAT_MODEL` | `qwen3:8b` | Answer-generation model |
| `OLLAMA_EMBEDDING_MODEL` | `nomic-embed-text` | Document/query embedding model |
| `RAG_DATA_DIR` | `data` | Persistent FAISS and metadata directory |
| `RAG_CHUNK_SIZE` | `1000` | Approximate chunk size in characters |
| `RAG_CHUNK_OVERLAP` | `150` | Character overlap between chunks |
| `RAG_TOP_K` | `4` | Default retrieved chunk count |
| `PARQUET_DATA_DIR` | `data/parquet` | Only directory exposed to analytics tools |
| `ANALYTICS_MAX_ROWS` | `1000` | Maximum rows returned by a structured query |

## Current safety boundary

The service reads only explicitly uploaded documents and calls Ollama. It does not expose shell execution, arbitrary filesystem browsing, file modification tools, or autonomous actions. Uploaded filenames are reduced to their basename before being stored as metadata.
