#!/usr/bin/env bash
set -euo pipefail

# ---------------------------------------------------------------------------
# OpenViking Docker Entrypoint
#
# Handles secret injection (_FILE pattern), config generation from env vars,
# validation of required API keys, and data directory setup before handing
# off to the container CMD.
# ---------------------------------------------------------------------------

# --- 1. file_env: Docker-style secret loading --------------------------------
# Usage: file_env VAR [DEFAULT]
# If VAR_FILE is set, read file contents into VAR. Errors if both VAR and
# VAR_FILE are set simultaneously.
file_env() {
    local var="$1"
    local file_var="${var}_FILE"
    local default="${2:-}"

    local val="${!var:-}"
    local file_val="${!file_var:-}"

    if [ -n "$val" ] && [ -n "$file_val" ]; then
        echo "ERROR: both $var and $file_var are set — use one or the other" >&2
        exit 1
    fi

    if [ -n "$file_val" ]; then
        if [ ! -f "$file_val" ]; then
            echo "ERROR: $file_var points to '$file_val' but the file does not exist" >&2
            exit 1
        fi
        val="$(< "$file_val")"
    fi

    export "$var"="${val:-$default}"
}

# --- 2. Load sensitive variables via file_env --------------------------------
file_env OPENVIKING_API_KEY
file_env MISTRAL_API_KEY
file_env ANTHROPIC_API_KEY
file_env OPENAI_API_KEY

# --- 3. Validate required API keys ------------------------------------------
# Determine which keys are needed based on the configured models.
OPENVIKING_EMBEDDING_MODEL="${OPENVIKING_EMBEDDING_MODEL:-mistral/mistral-embed}"
OPENVIKING_VLM_MODEL="${OPENVIKING_VLM_MODEL:-anthropic/claude-sonnet-4-6}"

missing=()

# Helper: check that a variable is non-empty, or record it as missing.
require_key() {
    local var="$1"
    local reason="$2"
    if [ -z "${!var:-}" ]; then
        missing+=("$var ($reason)")
    fi
}

# Embedding model key requirement
case "$OPENVIKING_EMBEDDING_MODEL" in
    mistral/*)  require_key MISTRAL_API_KEY   "needed by embedding model $OPENVIKING_EMBEDDING_MODEL" ;;
    openai/*)   require_key OPENAI_API_KEY    "needed by embedding model $OPENVIKING_EMBEDDING_MODEL" ;;
    anthropic/*) require_key ANTHROPIC_API_KEY "needed by embedding model $OPENVIKING_EMBEDDING_MODEL" ;;
esac

# VLM model key requirement
case "$OPENVIKING_VLM_MODEL" in
    anthropic/*) require_key ANTHROPIC_API_KEY "needed by VLM model $OPENVIKING_VLM_MODEL" ;;
    openai/*)    require_key OPENAI_API_KEY    "needed by VLM model $OPENVIKING_VLM_MODEL" ;;
    mistral/*)   require_key MISTRAL_API_KEY   "needed by VLM model $OPENVIKING_VLM_MODEL" ;;
esac

# Deduplicate and report all missing keys at once
if [ ${#missing[@]} -gt 0 ]; then
    # Deduplicate (a key may be required by both embedding and VLM)
    printf -v joined '%s\n' "${missing[@]}"
    deduped="$(echo "$joined" | sort -u)"
    echo "ERROR: the following required environment variables are not set:" >&2
    echo "$deduped" | while IFS= read -r line; do
        [ -n "$line" ] && echo "  - $line" >&2
    done
    exit 1
fi

# --- 4. Generate config if none is mounted ----------------------------------
CONFIG_PATH="/config/ov.conf"

if [ -f "$CONFIG_PATH" ]; then
    echo "OpenViking: using mounted config at $CONFIG_PATH"
else
    echo "OpenViking: generating config from environment"

    OPENVIKING_AUTH_MODE="${OPENVIKING_AUTH_MODE:-api_key}"
    OPENVIKING_EMBEDDING_PROVIDER="${OPENVIKING_EMBEDDING_PROVIDER:-litellm}"
    OPENVIKING_EMBEDDING_DIMENSION="${OPENVIKING_EMBEDDING_DIMENSION:-1024}"
    OPENVIKING_VLM_PROVIDER="${OPENVIKING_VLM_PROVIDER:-litellm}"
    OPENVIKING_DATA_DIR="${OPENVIKING_DATA_DIR:-/data}"

    # Auto-detect embedding API key var and base URL from model prefix
    if [ -z "${OPENVIKING_EMBEDDING_API_KEY_VAR:-}" ]; then
        case "$OPENVIKING_EMBEDDING_MODEL" in
            mistral/*)   OPENVIKING_EMBEDDING_API_KEY_VAR='$MISTRAL_API_KEY' ;;
            openai/*)    OPENVIKING_EMBEDDING_API_KEY_VAR='$OPENAI_API_KEY' ;;
            anthropic/*) OPENVIKING_EMBEDDING_API_KEY_VAR='$ANTHROPIC_API_KEY' ;;
        esac
    fi

    if [ -z "${OPENVIKING_EMBEDDING_API_BASE:-}" ]; then
        case "$OPENVIKING_EMBEDDING_MODEL" in
            mistral/*)  OPENVIKING_EMBEDDING_API_BASE="https://api.mistral.ai/v1" ;;
            openai/*)   OPENVIKING_EMBEDDING_API_BASE="https://api.openai.com/v1" ;;
            anthropic/*) OPENVIKING_EMBEDDING_API_BASE="" ;;  # not needed
        esac
    fi

    # Auto-detect VLM API key var from model prefix
    if [ -z "${OPENVIKING_VLM_API_KEY_VAR:-}" ]; then
        case "$OPENVIKING_VLM_MODEL" in
            anthropic/*) OPENVIKING_VLM_API_KEY_VAR='$ANTHROPIC_API_KEY' ;;
            openai/*)    OPENVIKING_VLM_API_KEY_VAR='$OPENAI_API_KEY' ;;
            mistral/*)   OPENVIKING_VLM_API_KEY_VAR='$MISTRAL_API_KEY' ;;
        esac
    fi

    # Build the embedding block — conditionally include api_base
    embedding_block=""
    if [ -n "${OPENVIKING_EMBEDDING_API_BASE:-}" ]; then
        embedding_block=$(cat <<EMBED_EOF
    "dense": {
      "provider": "${OPENVIKING_EMBEDDING_PROVIDER}",
      "model": "${OPENVIKING_EMBEDDING_MODEL}",
      "api_key": "${OPENVIKING_EMBEDDING_API_KEY_VAR}",
      "api_base": "${OPENVIKING_EMBEDDING_API_BASE}",
      "dimension": ${OPENVIKING_EMBEDDING_DIMENSION}
    }
EMBED_EOF
)
    else
        embedding_block=$(cat <<EMBED_EOF
    "dense": {
      "provider": "${OPENVIKING_EMBEDDING_PROVIDER}",
      "model": "${OPENVIKING_EMBEDDING_MODEL}",
      "api_key": "${OPENVIKING_EMBEDDING_API_KEY_VAR}",
      "dimension": ${OPENVIKING_EMBEDDING_DIMENSION}
    }
EMBED_EOF
)
    fi

    # Build the VLM block
    vlm_block=$(cat <<VLM_EOF
  "vlm": {
    "provider": "${OPENVIKING_VLM_PROVIDER}",
    "model": "${OPENVIKING_VLM_MODEL}",
    "api_key": "${OPENVIKING_VLM_API_KEY_VAR}"
  }
VLM_EOF
)

    # Assemble the full config
    mkdir -p "$(dirname "$CONFIG_PATH")"

    if [ "$OPENVIKING_AUTH_MODE" = "none" ]; then
        # Omit the server block entirely
        cat > "$CONFIG_PATH" <<CONF_EOF
{
  "storage": {
    "workspace": "${OPENVIKING_DATA_DIR}"
  },
  "embedding": {
${embedding_block}
  },
${vlm_block}
}
CONF_EOF
    else
        cat > "$CONFIG_PATH" <<CONF_EOF
{
  "server": {
    "auth_mode": "${OPENVIKING_AUTH_MODE}",
    "root_api_key": "\$OPENVIKING_API_KEY"
  },
  "storage": {
    "workspace": "${OPENVIKING_DATA_DIR}"
  },
  "embedding": {
${embedding_block}
  },
${vlm_block}
}
CONF_EOF
    fi
fi

# --- 5. Ensure data directory exists -----------------------------------------
OPENVIKING_DATA_DIR="${OPENVIKING_DATA_DIR:-/data}"
mkdir -p "$OPENVIKING_DATA_DIR"

# --- 6. Hand off to CMD ------------------------------------------------------
exec "$@"
