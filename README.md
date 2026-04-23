# OpenViking

A multi-instance [OpenViking](https://github.com/jadenmaciel/openviking) deployment with a unified MCP server and nginx reverse proxy. Designed as a local RAG knowledge base for AI-assisted development workflows.

Each instance maintains an isolated vector database and content store, accessible through a single MCP endpoint that AI agents (Claude Code, etc.) connect to.

## Prerequisites

- [Nix](https://nixos.org/) with flakes enabled
- [direnv](https://direnv.net/) (recommended)
- An OpenAI API key (for embeddings and content summarization)

## Setup

```bash
# Clone and enter the directory
cd OpenViking

# Allow direnv (sets up flake, venv, and env vars automatically)
direnv allow

# Or manually: enter the Nix dev shell and sync dependencies
nix develop
uv sync

# Add your OpenAI API key
echo 'OPENAI_API_KEY=sk-...' > .env
```

## Usage

### Starting and stopping

```bash
# Start everything: OpenViking instances, nginx proxy, MCP server
ov-manager start

# Check what's running
ov-manager status

# Stop everything
ov-manager stop
```

Output from `ov-manager status`:

```
OpenViking Manager
  nginx                    :1933  ● running  (PID 12345)
  work                     :1940  ● running  (PID 12346)  → http://localhost:1933/work/
  personal                 :1941  ● running  (PID 12347)  → http://localhost:1933/personal/
  openviking-mcp           :2033  ● running  (PID 12348)  → http://localhost:2033/mcp
  deep-researcher          :8001  ● running  (PID 12349)  → http://localhost:8001/mcp
```

### Managing individual components

```bash
ov-manager start work         # Start a single instance
ov-manager stop personal      # Stop a single instance
ov-manager restart work       # Restart a single instance
ov-manager start services     # Start only the MCP server and services
ov-manager stop services      # Stop only services
```

### Using the OpenViking CLI

The `openviking` CLI interacts with whichever instance `OPENVIKING_CONFIG_FILE` points to (defaults to `ov.conf` / the work instance via `.envrc`):

```bash
# Add a resource
openviking add-resource ./path/to/document.pdf

# Semantic search
openviking search "how does the ETL pipeline work"

# Browse stored content
openviking ls

# Read a resource
openviking read viking://resources/research/some-topic.md
```

## Architecture

```
Clients (Claude Code, etc.)
        │
        ▼
┌──────────────────────────────────┐
│  MCP Server (mcp-server.py)      │
│  :2033                           │
│  /mcp/work    /mcp/personal      │
│  12 tools per instance           │
└───────────────┬──────────────────┘
                │ HTTP
┌───────────────▼──────────────────┐
│  nginx reverse proxy             │
│  :1933                           │
│  /work/  →  :1940                │
│  /personal/  →  :1941            │
└──┬───────────────────────────┬───┘
   │                           │
┌──▼───────────────┐  ┌───────▼──────────┐
│ openviking-server│  │ openviking-server │
│ :1940 (work)     │  │ :1941 (personal)  │
│ data/work/       │  │ data/personal/    │
│  vectordb/       │  │  vectordb/        │
│  viking/         │  │  viking/          │
└──────────────────┘  └───────────────────┘
```

**Instances** are isolated OpenViking servers, each with their own data directory, vector database, and content store.

**nginx** provides a unified HTTP entry point. Requests to `/work/...` are proxied to the work instance, `/personal/...` to the personal instance.

**MCP server** (`mcp-server.py`) is a single Python process using [FastMCP](https://github.com/jlowin/fastmcp) and Starlette. It creates one MCP endpoint per instance, mounting them at `/mcp/<name>`. Each tool call is forwarded as an HTTP request through the nginx proxy to the correct backend.

### Content tiers (L0/L1/L2)

When a resource is ingested, OpenViking generates three representations:

| Tier | Name | Description | Use case |
|------|------|-------------|----------|
| L0 | Abstract | One-line summary | Scanning, filtering |
| L1 | Overview | Multi-paragraph structured summary | Understanding before deep dive |
| L2 | Full content | Complete document text | Detailed reading, extraction |

These are generated automatically by the configured VLM (GPT-5.4) and stored alongside the original content.

### Vector search

Content is embedded using OpenAI's `text-embedding-3-large` model (3072 dimensions) and stored in a Milvus-compatible vector database with flat indexing and int8 quantization. Both dense (semantic) and sparse (keyword/BM25) vectors are maintained.

## Configuration

### instances.json

Central configuration defining all instances, services, and the proxy port:

```json
{
  "proxy_port": 1933,
  "instances": {
    "work": {
      "port": 1940,
      "config": "ov-work.conf",
      "data": "data-work"
    },
    "personal": {
      "port": 1941,
      "config": "ov-personal.conf",
      "data": "data-personal"
    }
  },
  "services": {
    "openviking-mcp": {
      "port": 2033,
      "command": ".venv/bin/python3",
      "args": ["mcp-server.py", "--port", "2033"]
    }
  }
}
```

### Instance config (ov-*.conf)

Each instance has a JSON config file specifying storage, embedding, and VLM settings:

```json
{
  "storage": {
    "workspace": "./data/work"
  },
  "embedding": {
    "dense": {
      "provider": "openai",
      "model": "text-embedding-3-large",
      "api_key": "$OPENAI_API_KEY",
      "dimension": 3072
    }
  },
  "vlm": {
    "provider": "openai",
    "model": "gpt-5.4",
    "api_key": "$OPENAI_API_KEY"
  }
}
```

### Adding a new instance

1. Add the instance to `instances.json` with a unique port
2. Create an `ov-<name>.conf` with `storage.workspace` pointing to `./data/<name>`
3. Run `ov-manager restart` — nginx config and MCP server are regenerated automatically

## MCP Integration

The MCP server exposes 12 tools per instance:

| Category | Tools |
|----------|-------|
| Search | `search`, `grep_content`, `find_by_pattern` |
| Read | `read_content`, `get_overview`, `get_abstract` |
| Navigate | `list_contents`, `get_resource_info`, `get_relations` |
| Manage | `add_resource`, `add_memory`, `health_check` |

Connect your MCP client to `http://localhost:2033/mcp/<instance_name>` using Streamable HTTP transport.

## File Structure

```
.
├── mcp-server.py        # Multi-instance MCP server
├── ov-manager           # Process lifecycle manager (bash)
├── instances.json       # Instance and service definitions
├── ov.conf              # Default OpenViking config (CLI)
├── ov-work.conf         # Work instance config
├── ov-personal.conf     # Personal instance config
├── flake.nix            # Nix dev shell
├── pyproject.toml       # Python dependencies
├── .envrc               # direnv: flake + venv + env vars
├── .env                 # API keys (gitignored)
├── data/                # Instance data (gitignored)
│   ├── work/            # Work instance storage
│   └── personal/        # Personal instance storage
├── run/                 # PID files and logs (gitignored)
└── nginx.conf           # Auto-generated (gitignored)
```

## Logs

| Log | Location |
|-----|----------|
| Instance server logs | `data/<instance>/server.log` |
| MCP server log | `run/openviking-mcp.log` |
| nginx access log | `run/nginx/access.log` |
| nginx error log | `run/nginx/error.log` |
