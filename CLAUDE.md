# OpenViking

Local context database (RAG knowledge base) for the Oaasis workspace. Stores and semantically retrieves research, documentation, and architectural knowledge using Mistral embeddings.

## Quick Reference

| Component | Container | Port (host) | Description |
|---|---|---|---|
| Traefik | `traefik` | 1933, 8080 | Reverse proxy with Docker label discovery |
| work instance | `ov-work` | (internal) | Work/Oaasis knowledge base |
| personal instance | `ov-personal` | (internal) | Personal knowledge base |
| MCP server | `ov-mcp` | 2033 | Multi-instance MCP endpoint |

## Running

All services run via Docker Compose:

```bash
# Build and start everything
docker compose up -d

# Check status
docker compose ps

# Follow logs
docker compose logs -f

# Stop everything
docker compose down

# Rebuild after code changes
docker compose up -d --build
```

Selective operations:
```bash
docker compose restart ov-work       # restart one instance
docker compose logs -f ov-mcp        # follow MCP server logs
docker compose up -d --build ov-mcp  # rebuild and restart MCP server
```

Traefik dashboard is available at `http://localhost:8080` (dev only — disable or add auth in production).

## Architecture

```
                    ┌─────────────────────────────┐
                    │  MCP Server (ov-mcp:2033)    │
                    │  mcp-server.py               │
                    │  /mcp/work, /mcp/personal    │
                    └──────────┬──────────────────┘
                               │ discovers instances
                               │ via Traefik API
                    ┌──────────▼──────────────────┐
                    │  Traefik (traefik:1933)      │
                    │  /work/ → ov-work:1940       │
                    │  /personal/ → ov-personal:1940│
                    │  auto-discovers via Docker   │
                    └──┬──────────────────────┬───┘
                       │                      │
          ┌────────────▼───────┐  ┌───────────▼────────┐
          │ ov-work            │  │ ov-personal         │
          │ openviking-server  │  │ openviking-server   │
          │ :1940              │  │ :1940               │
          │ data/work/         │  │ data/personal/      │
          └────────────────────┘  └─────────────────────┘
```

### How discovery works

1. Each OpenViking instance has **Traefik labels** in `docker-compose.yml` that define its router name (`ov-<name>`) and path prefix (`/<name>`)
2. **Traefik** watches the Docker socket and auto-discovers services with `traefik.enable=true`
3. The **MCP server** queries Traefik's REST API (`/api/http/routers`) at startup, finds all routers prefixed with `ov-`, and creates an MCP endpoint for each
4. MCP tool calls are routed through Traefik to the correct instance

No config files to maintain — Docker labels are the single source of truth.

## Key Files

| File | Purpose |
|---|---|
| `docker-compose.yml` | Service orchestration with Traefik labels |
| `Dockerfile` | Single image for both openviking-server and MCP server |
| `mcp-server.py` | Multi-instance MCP server (discovers instances via Traefik API) |
| `ov.container.conf` | OpenViking config for containers (workspace at `/data`) |
| `ov-work.conf` | Local dev config for the work instance |
| `ov-personal.conf` | Local dev config for the personal instance |
| `ov.conf` | Default local config (used by CLI tools) |
| `flake.nix` | Nix dev shell (python3, uv, git) |
| `.envrc` | direnv setup: activates flake, syncs venv, loads `.env` |

## Configuration

**Adding a new instance** requires only two things in `docker-compose.yml`:

1. Add a new service based on the same image with Traefik labels:
   ```yaml
   ov-myinstance:
     build: .
     labels:
       traefik.enable: "true"
       traefik.http.routers.ov-myinstance.rule: "PathPrefix(`/myinstance`)"
       traefik.http.routers.ov-myinstance.entrypoints: "web"
       traefik.http.services.ov-myinstance.loadbalancer.server.port: "1940"
       traefik.http.middlewares.ov-myinstance-strip.stripprefix.prefixes: "/myinstance"
       traefik.http.routers.ov-myinstance.middlewares: "ov-myinstance-strip"
     volumes:
       - ./data/myinstance:/data
       - ./ov.container.conf:/config/ov.conf:ro
     env_file: .env
   ```
2. Run `docker compose up -d` — Traefik discovers it, MCP server picks it up on next restart

**Container config** (`ov.container.conf`): shared by all containers, uses `/data` as workspace.

**OpenViking config** defines:
- `storage.workspace` — path to the instance's data directory
- `embedding.dense` — Mistral embedding model (mistral-embed via litellm, 1024-dim)
- `vlm` — Vision/language model for generating summaries
- `server.auth_mode` — set to `api_key` for container networking

**Secrets**: `MISTRAL_API_KEY`, `ANTHROPIC_API_KEY`, and `OPENVIKING_API_KEY` must be in `.env` (gitignored). Docker compose passes them via `env_file: .env`.

**Agent identity**: the MCP server sends `X-OpenViking-Agent` on every backend request so OV's audit trail can attribute reads/writes. Defaults to `ov-mcp`; override via `OPENVIKING_AGENT` env var if running multiple MCP server instances.

## Data Model

Each instance stores data in its own directory (`data/work/`, `data/personal/`), mounted into the container at `/data`:

```
data/<instance>/
  _system/queue/     # Processing queue
  temp/upload/       # Upload staging
  vectordb/context/  # Milvus vector embeddings
  viking/default/    # Content filesystem
    resources/       # Ingested documents and research
    agent/           # Agent data
    session/         # Session data
    user/            # User data
```

### Tiered Content (L0/L1/L2)

Resources are stored with three levels of detail, auto-generated by the VLM:
- **L0 (Abstract)** — one-line summary, cheapest to retrieve
- **L1 (Overview)** — multi-paragraph structured summary
- **L2 (Full Content)** — complete document text

## MCP Tools

The MCP server exposes 12 tools per instance:

**Search**: `search`, `grep_content`, `find_by_pattern`
**Read**: `read_content`, `get_overview`, `get_abstract`
**Navigate**: `list_contents`, `get_resource_info`, `get_relations`
**Manage**: `add_resource`, `add_memory`, `health_check`

## Development Notes

- The `Dockerfile` is shared: openviking-server instances use the default CMD, the MCP server overrides it via `command` in compose
- The MCP server discovers instances by querying Traefik's `/api/http/routers` endpoint at startup, with retry logic for boot ordering
- Traefik router names must start with `ov-` to be discovered (convention set in `mcp-server.py` as `ROUTER_PREFIX`)
- The MCP server routes requests through Traefik (which handles path stripping and forwarding)
- Instance data is persisted on the host via volume mounts (`./data/<name>:/data`)
- Traefik dashboard at `:8080` is insecure by default — add auth or disable for production
