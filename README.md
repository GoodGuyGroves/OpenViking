# OpenViking

Context database for AI agents with semantic search, tiered summaries, and MCP integration.

## Quick Start

### Docker Run

```bash
# 1. Create your environment file
cp .env.example .env
# Edit .env and add your MISTRAL_API_KEY and ANTHROPIC_API_KEY

# 2. Start OpenViking
docker run -d \
  --name openviking \
  -p 1940:1940 \
  -v ./data:/data \
  --env-file .env \
  ghcr.io/goodguygroves/openviking:latest

# 3. Verify it's running
curl http://localhost:1940/health
```

### Docker Compose

```bash
cp docker-compose.example.yml docker-compose.yml
cp .env.example .env
# Edit .env with your API keys
docker compose up -d
```

## Configuration

### Environment Variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `MISTRAL_API_KEY` | Yes* | -- | Mistral API key (*required when using default embedding model) |
| `ANTHROPIC_API_KEY` | Yes* | -- | Anthropic API key (*required when using default VLM model) |
| `OPENAI_API_KEY` | No | -- | OpenAI API key (when using OpenAI models) |
| `OPENVIKING_API_KEY` | No | -- | API key for authenticating requests to OpenViking |
| `OPENVIKING_AGENT` | No | `ov-mcp` | Identifies this MCP server in OV's audit trail; sent as `X-OpenViking-Agent` header on backend requests |
| `OPENVIKING_AUTH_MODE` | No | `api_key` | Authentication mode: `api_key` or `none` |
| `OPENVIKING_EMBEDDING_PROVIDER` | No | `litellm` | Embedding provider backend |
| `OPENVIKING_EMBEDDING_MODEL` | No | `mistral/mistral-embed` | Embedding model (format: `provider/model`) |
| `OPENVIKING_EMBEDDING_DIMENSION` | No | `1024` | Embedding vector dimension (must match model output) |
| `OPENVIKING_EMBEDDING_API_BASE` | No | Auto-detected | Embedding API base URL |
| `OPENVIKING_VLM_PROVIDER` | No | `litellm` | VLM provider backend |
| `OPENVIKING_VLM_MODEL` | No | `anthropic/claude-sonnet-4-6` | VLM model for generating summaries |
| `OPENVIKING_DATA_DIR` | No | `/data` | Data directory inside the container |
| `OPENVIKING_INSTANCES` | No | -- | MCP server only: explicit instance list (`name=url[,name=url]`) |
| `TRAEFIK_API_URL` | No | -- | MCP server only: Traefik API URL for auto-discovery |
| `TRAEFIK_ENTRYPOINT_URL` | No | `http://localhost:1933` | MCP server only: Traefik entrypoint for routing |

All sensitive variables support the `_FILE` suffix for Docker/Kubernetes secrets (e.g., `MISTRAL_API_KEY_FILE=/run/secrets/mistral`).

### Config File

By default, the entrypoint generates configuration from environment variables. Advanced users can mount a custom config file at `/config/ov.conf` to override everything.

Minimal example:

```json
{
  "storage": {
    "workspace": "/data"
  },
  "embedding": {
    "dense": {
      "provider": "litellm",
      "model": "mistral/mistral-embed",
      "api_key": "$MISTRAL_API_KEY",
      "api_base": "https://api.mistral.ai/v1",
      "dimension": 1024
    }
  },
  "vlm": {
    "provider": "litellm",
    "model": "anthropic/claude-sonnet-4-6",
    "api_key": "$ANTHROPIC_API_KEY"
  }
}
```

`$VAR` references in the config file are resolved by OpenViking at runtime from the container's environment.

## Volumes

| Path | Purpose |
|---|---|
| `/data` | Persistent storage for documents, embeddings, and indexes |
| `/config/ov.conf` | Optional: mount a custom config file |

## Ports

| Port | Purpose |
|---|---|
| `1940` | OpenViking HTTP API |
| `2033` | MCP server (multi-instance mode only) |

## Multi-Instance Setup

OpenViking supports running multiple isolated instances, each with its own data directory. There are two ways to wire them up:

### Direct connection (simple)

Set the `OPENVIKING_INSTANCES` env var on the MCP server with explicit URLs:

```bash
OPENVIKING_INSTANCES=docs=http://ov-docs:1940,research=http://ov-research:1940
```

### Traefik auto-discovery (dynamic)

For setups where instances are added/removed frequently, use Traefik for automatic discovery. See `docker-compose.example.yml` (Pattern B) for the full setup.

- **Traefik** watches the Docker socket and auto-discovers services with the appropriate labels.
- Each instance is a separate container with Traefik labels defining its router name and path prefix (e.g., `/docs/`, `/research/`).
- The **MCP server** queries Traefik's REST API at startup, discovers all OpenViking instances by router prefix, and creates an MCP endpoint for each.
- Adding a new instance is just adding a new service to `docker-compose.yml` with the correct Traefik labels and running `docker compose up -d`.

## MCP Integration

OpenViking exposes 12 tools per instance for use by AI agents via MCP (Model Context Protocol):

| Category | Tools |
|----------|-------|
| **Search** | `search`, `grep_content`, `find_by_pattern` |
| **Read** | `read_content`, `get_overview`, `get_abstract` |
| **Navigate** | `list_contents`, `get_resource_info`, `get_relations` |
| **Manage** | `add_resource`, `add_memory`, `health_check` |

**Single-instance mode:** Connect your MCP client to `http://localhost:1940` using Streamable HTTP transport. Or run the MCP server alongside it — without `TRAEFIK_API_URL` set, it defaults to connecting to `http://localhost:1940` as a single instance named `default`.

**Multi-instance mode:** Connect to the MCP server at `http://localhost:2033/mcp/<instance>` (e.g., `http://localhost:2033/mcp/docs`). The MCP server discovers instances in priority order:
1. `OPENVIKING_INSTANCES` env var — explicit `name=url` pairs (e.g., `docs=http://ov-docs:1940,research=http://ov-research:1940`)
2. `TRAEFIK_API_URL` env var — auto-discover via Traefik router labels
3. Default — single instance at `http://localhost:1940`

## Data Model

When a resource is ingested, OpenViking generates three tiered representations:

| Tier | Name | Description | Use case |
|------|------|-------------|----------|
| **L0** | Abstract | One-line summary | Scanning, filtering, cheap retrieval |
| **L1** | Overview | Structured multi-paragraph summary | Understanding context before a deep dive |
| **L2** | Full Content | Complete document text | Detailed reading and extraction |

These tiers are generated automatically by the configured VLM and stored alongside the original content. Semantic search uses vector embeddings (dense) to match queries against all stored content.

## Building from Source

```bash
git clone https://github.com/GoodGuyGroves/OpenViking.git
cd openviking
docker build -t openviking .
```

## License

Apache-2.0
