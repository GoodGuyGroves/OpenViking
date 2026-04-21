# Multi-instance OpenViking MCP Server
#
# Serves multiple OpenViking instances from a single MCP server process.
# Each instance is mounted at /mcp/<instance_name> and routes all tool calls
# to the corresponding OpenViking backend via nginx proxy.
#
# Inspired by jadenmaciel/openviking-mcp (MIT license)
# https://github.com/jadenmaciel/openviking-mcp

import argparse
import contextlib
import json
import logging
import re
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx
import uvicorn
from mcp.server.fastmcp import FastMCP
from starlette.applications import Starlette
from starlette.routing import Mount

logger = logging.getLogger("openviking-mcp")


# ---------------------------------------------------------------------------
# HTTP client helpers (ported from openviking_mcp.client)
# ---------------------------------------------------------------------------

def _normalize_uri(uri: str) -> str:
    """Ensure URI has viking:// prefix and no trailing slash."""
    if not uri:
        return "viking://"
    if not re.match(r"^viking://", uri):
        uri = "viking://" + uri.lstrip("/")
    return uri.rstrip("/") or "viking://"


class OpenVikingError(Exception):
    """Error from the OpenViking server."""

    def __init__(self, message: str, code: str = "UNKNOWN"):
        self.code = code
        super().__init__(message)


def _handle_response(response: httpx.Response) -> Any:
    """Parse an OpenViking API response envelope."""
    try:
        data = response.json()
    except Exception:
        if not response.is_success:
            raise OpenVikingError(
                f"HTTP {response.status_code}: {response.text or 'empty response'}",
                code="INTERNAL",
            )
        return {}

    if data.get("status") == "error":
        error = data.get("error", {})
        raise OpenVikingError(
            error.get("message", "Unknown error"),
            code=error.get("code", "UNKNOWN"),
        )

    if not response.is_success:
        raise OpenVikingError(
            data.get("detail", f"HTTP {response.status_code}"),
            code="UNKNOWN",
        )

    return data.get("result")


# ---------------------------------------------------------------------------
# Formatting helpers (ported from openviking_mcp.formatting)
# ---------------------------------------------------------------------------

def _truncate(text: str, max_len: int = 200) -> str:
    if len(text) <= max_len:
        return text
    return text[:max_len] + "..."


def _format_search_results(result: Dict[str, Any]) -> str:
    items = result.get("results", result.get("items", []))
    if not items:
        return "No results found."
    lines = [f"Found {len(items)} result(s):\n"]
    for i, item in enumerate(items, 1):
        uri = item.get("uri", "?")
        score = item.get("score", 0)
        snippet = item.get("snippet", item.get("text", ""))
        lines.append(f"{i}. [{score:.2f}] {uri}")
        if snippet:
            lines.append(f"   {_truncate(snippet)}")
    return "\n".join(lines)


def _format_grep(result: Dict[str, Any]) -> str:
    matches = result.get("matches", result.get("results", []))
    if not matches:
        return "No matches found."
    lines = [f"Found {len(matches)} match(es):\n"]
    for m in matches:
        uri = m.get("uri", "?")
        line_num = m.get("line", "")
        text = m.get("text", m.get("line_text", ""))
        prefix = f"{uri}:{line_num}" if line_num else uri
        lines.append(f"  {prefix}: {_truncate(text)}")
    return "\n".join(lines)


def _format_glob(result: Dict[str, Any]) -> str:
    items = result.get("matches", result.get("results", []))
    if not items:
        return "No matches found."
    lines = [f"Found {len(items)} match(es):\n"]
    for item in items:
        if isinstance(item, str):
            lines.append(f"  {item}")
        else:
            lines.append(f"  {item.get('uri', item.get('path', str(item)))}")
    return "\n".join(lines)


def _format_ls(items: List[Any]) -> str:
    if not items:
        return "Directory is empty."
    lines = [f"{len(items)} item(s):\n"]
    for item in items:
        if isinstance(item, str):
            lines.append(f"  {item}")
        else:
            kind = item.get("type", "?")
            uri = item.get("uri", item.get("name", str(item)))
            size = item.get("size", "")
            suffix = f" ({size} bytes)" if size else ""
            lines.append(f"  [{kind}] {uri}{suffix}")
    return "\n".join(lines)


def _format_stat(data: Dict[str, Any]) -> str:
    if not data:
        return "No metadata available."
    lines = []
    for key in ("uri", "type", "size", "status", "created", "modified", "content_type"):
        val = data.get(key)
        if val is not None:
            lines.append(f"  {key}: {val}")
    # Include any remaining keys
    shown = {"uri", "type", "size", "status", "created", "modified", "content_type"}
    for key, val in data.items():
        if key not in shown and val is not None:
            lines.append(f"  {key}: {val}")
    return "\n".join(lines) if lines else str(data)


def _format_relations(items: List[Dict[str, Any]]) -> str:
    if not items:
        return "No relations found."
    lines = [f"{len(items)} relation(s):\n"]
    for item in items:
        rel_type = item.get("type", item.get("relation", "?"))
        target = item.get("target", item.get("uri", str(item)))
        lines.append(f"  [{rel_type}] {target}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Per-instance MCP factory
# ---------------------------------------------------------------------------

def create_mcp_for_instance(instance_name: str, backend_url: str) -> FastMCP:
    """Create a FastMCP server wired to a specific OpenViking backend.

    The returned server has 12 tools matching the jadenmaciel/openviking-mcp
    interface. The streamable_http_path is set to "/" so the app can be mounted
    under an arbitrary prefix by the caller.
    """

    mcp = FastMCP(
        name=f"openviking-{instance_name}",
        instructions=(
            "OpenViking is a context database for AI agents. "
            "Use these tools to search, read, and manage memories, documents, and resources "
            "stored in OpenViking. Start with 'search' to find relevant content, "
            "then 'read_content' to get full text. Use 'add_memory' to store information "
            "for future recall."
        ),
        streamable_http_path="/",
        stateless_http=True,
    )

    _base_url = backend_url.rstrip("/")
    _client: Optional[httpx.AsyncClient] = None

    async def _get_client() -> httpx.AsyncClient:
        nonlocal _client
        if _client is None or _client.is_closed:
            _client = httpx.AsyncClient(
                base_url=_base_url,
                headers={
                    "X-OpenViking-Account": "default",
                    "X-OpenViking-User": "default",
                    "X-OpenViking-Agent": "default",
                },
                timeout=60.0,
            )
        return _client

    # ── Search & Retrieval ────────────────────────────────────────────────

    @mcp.tool()
    async def search(
        query: str,
        target_uri: str = "",
        limit: int = 5,
        score_threshold: float = 0.1,
    ) -> str:
        """Search OpenViking for relevant memories, documents, and resources.

        Use this to find information the user has previously stored or ingested.
        Returns results ranked by semantic relevance.

        Args:
            query: Natural language search query.
            target_uri: Optional Viking URI to scope search to a specific directory.
            limit: Maximum number of results (1-20).
            score_threshold: Minimum relevance score (0.0-1.0).
        """
        c = await _get_client()
        try:
            if target_uri:
                target_uri = _normalize_uri(target_uri)
            resp = await c.post("/api/v1/search/find", json={
                "query": query,
                "target_uri": target_uri,
                "limit": limit,
                "score_threshold": score_threshold,
            })
            return _format_search_results(_handle_response(resp) or {})
        except OpenVikingError as e:
            return f"Search error ({e.code}): {e}"

    @mcp.tool()
    async def grep_content(
        pattern: str,
        uri: str = "viking://",
        case_insensitive: bool = False,
    ) -> str:
        """Search for exact text patterns within resources.

        Use this for exact string or regex matching (complements semantic search).

        Args:
            pattern: Text pattern or regex to search for.
            uri: Viking URI scope to search within.
            case_insensitive: Whether to ignore case.
        """
        c = await _get_client()
        try:
            uri_n = _normalize_uri(uri)
            resp = await c.post("/api/v1/search/grep", json={
                "uri": uri_n,
                "pattern": pattern,
                "case_insensitive": case_insensitive,
            })
            return _format_grep(_handle_response(resp) or {})
        except OpenVikingError as e:
            return f"Grep error ({e.code}): {e}"

    @mcp.tool()
    async def find_by_pattern(
        pattern: str,
        uri: str = "viking://",
    ) -> str:
        """Find resources by filename glob pattern.

        Examples: '*.pdf', 'docs/**/*.md', 'report*'

        Args:
            pattern: Glob pattern to match filenames against.
            uri: Viking URI scope to search within.
        """
        c = await _get_client()
        try:
            uri_n = _normalize_uri(uri)
            resp = await c.post("/api/v1/search/glob", json={
                "pattern": pattern,
                "uri": uri_n,
            })
            return _format_glob(_handle_response(resp) or {})
        except OpenVikingError as e:
            return f"Glob error ({e.code}): {e}"

    # ── Content Reading ───────────────────────────────────────────────────

    @mcp.tool()
    async def read_content(
        uri: str,
        offset: int = 0,
        limit: int = -1,
    ) -> str:
        """Read the full content of a resource by its Viking URI.

        Use after search to get the complete text of a matched document.

        Args:
            uri: Viking URI of the resource (e.g., viking://user/docs/readme.md).
            offset: Starting line number (0-indexed).
            limit: Number of lines to read (-1 for all).
        """
        c = await _get_client()
        try:
            uri_n = _normalize_uri(uri)
            resp = await c.get("/api/v1/content/read", params={
                "uri": uri_n,
                "offset": offset,
                "limit": limit,
            })
            content = _handle_response(resp)
            if isinstance(content, str):
                return content or "(empty)"
            return str(content)
        except OpenVikingError as e:
            return f"Read error ({e.code}): {e}"

    @mcp.tool()
    async def get_overview(uri: str) -> str:
        """Get a structured overview/summary of a resource (L1).

        Cheaper than reading full content for large documents. Provides a
        multi-paragraph summary with key sections and topics.

        Args:
            uri: Viking URI of the resource.
        """
        c = await _get_client()
        try:
            uri_n = _normalize_uri(uri)
            resp = await c.get("/api/v1/content/overview", params={"uri": uri_n})
            content = _handle_response(resp)
            return content or "(no overview available)"
        except OpenVikingError as e:
            return f"Overview error ({e.code}): {e}"

    @mcp.tool()
    async def get_abstract(uri: str) -> str:
        """Get a one-line abstract/summary of a resource (L0).

        The quickest way to understand what a resource contains.

        Args:
            uri: Viking URI of the resource.
        """
        c = await _get_client()
        try:
            uri_n = _normalize_uri(uri)
            resp = await c.get("/api/v1/content/abstract", params={"uri": uri_n})
            content = _handle_response(resp)
            return content or "(no abstract available)"
        except OpenVikingError as e:
            return f"Abstract error ({e.code}): {e}"

    # ── Navigation ────────────────────────────────────────────────────────

    @mcp.tool()
    async def list_contents(
        uri: str = "viking://",
        recursive: bool = False,
    ) -> str:
        """Browse the contents of OpenViking at a given URI path.

        Use this to explore what is stored in OpenViking.

        Args:
            uri: Viking URI to list (default: root).
            recursive: Whether to list all subdirectories recursively.
        """
        c = await _get_client()
        try:
            uri_n = _normalize_uri(uri)
            resp = await c.get("/api/v1/fs/ls", params={
                "uri": uri_n,
                "recursive": recursive,
                "simple": False,
                "output": "original",
            })
            return _format_ls(_handle_response(resp) or [])
        except OpenVikingError as e:
            return f"List error ({e.code}): {e}"

    @mcp.tool()
    async def get_resource_info(uri: str) -> str:
        """Get metadata about a resource (type, size, status, timestamps).

        Args:
            uri: Viking URI of the resource.
        """
        c = await _get_client()
        try:
            uri_n = _normalize_uri(uri)
            resp = await c.get("/api/v1/fs/stat", params={"uri": uri_n})
            return _format_stat(_handle_response(resp) or {})
        except OpenVikingError as e:
            return f"Stat error ({e.code}): {e}"

    @mcp.tool()
    async def get_relations(uri: str) -> str:
        """View relations/links between a resource and other resources.

        Args:
            uri: Viking URI of the resource.
        """
        c = await _get_client()
        try:
            uri_n = _normalize_uri(uri)
            resp = await c.get("/api/v1/relations", params={"uri": uri_n})
            return _format_relations(_handle_response(resp) or [])
        except OpenVikingError as e:
            return f"Relations error ({e.code}): {e}"

    # ── Resource Management ───────────────────────────────────────────────

    @mcp.tool()
    async def add_resource(
        path: str,
        reason: str = "",
        instruction: str = "",
    ) -> str:
        """Add a file, directory, or URL to OpenViking for indexing.

        The resource will be parsed, chunked, and made searchable.
        Supported: PDF, Markdown, Text, HTML, Word, images, code files, URLs.

        Args:
            path: Local file/directory path, or a URL to ingest.
            reason: Why this resource is being added (helps with retrieval context).
            instruction: Processing instruction for how to interpret the content.
        """
        c = await _get_client()
        try:
            resp = await c.post("/api/v1/resources", json={
                "path": path,
                "reason": reason,
                "instruction": instruction,
                "wait": True,
                "timeout": 300,
            }, timeout=310.0)
            result = _handle_response(resp) or {}
            root_uri = result.get("root_uri", "")
            if root_uri:
                return f"Resource added and indexed: {root_uri}"
            status = result.get("status", "")
            if status == "error":
                errors = result.get("errors", [])[:3]
                error_msg = "\n".join(f"  - {e}" for e in errors)
                return f"Resource had issues:\n{error_msg}"
            return f"Resource added: {json.dumps(result)}"
        except OpenVikingError as e:
            return f"Add resource error ({e.code}): {e}"

    @mcp.tool()
    async def add_memory(content: str) -> str:
        """Store a piece of information as a memory in OpenViking.

        Use this to remember facts, preferences, decisions, or context
        that should be recalled in future conversations.

        Args:
            content: The information to remember.
        """
        c = await _get_client()
        session_id: Optional[str] = None
        try:
            # Create session, add memory as a message, commit to extract
            resp = await c.post("/api/v1/sessions", json={})
            session_result = _handle_response(resp) or {}
            session_id = session_result.get("session_id", "")
            if not session_id:
                return "Error: failed to create session for memory storage."

            resp = await c.post(
                f"/api/v1/sessions/{session_id}/messages",
                json={"role": "user", "content": content},
            )
            _handle_response(resp)

            resp = await c.post(
                f"/api/v1/sessions/{session_id}/commit",
                json={},
            )
            commit_result = _handle_response(resp) or {}
            memories_extracted = commit_result.get("memories_extracted", 0)
            return f"Memory stored (session {session_id}, {memories_extracted} memories extracted)."
        except OpenVikingError as e:
            return f"Add memory error ({e.code}): {e}"
        except Exception as e:
            return f"Add memory error: {e}"
        finally:
            if session_id:
                try:
                    c_cleanup = await _get_client()
                    await c_cleanup.delete(f"/api/v1/sessions/{session_id}")
                except Exception:
                    pass

    # ── System ────────────────────────────────────────────────────────────

    @mcp.tool()
    async def health_check() -> str:
        """Check if the OpenViking server is running and healthy."""
        c = await _get_client()
        try:
            resp = await c.get("/health")
            data = resp.json()
            if data.get("status") == "ok":
                return f"OpenViking server is healthy ({_base_url})"
            return f"OpenViking server is not healthy ({_base_url})"
        except Exception as e:
            return f"Cannot reach OpenViking server at {_base_url}: {e}"

    return mcp


# ---------------------------------------------------------------------------
# Composite ASGI application
# ---------------------------------------------------------------------------

def build_app(config_path: str = "instances.json") -> Starlette:
    """Read instances.json and build a Starlette app that mounts one MCP
    endpoint per OpenViking instance.

    URL layout:
        /mcp/<instance_name>   ->  streamable-http MCP for that instance
    """
    config_file = Path(config_path)
    if not config_file.is_file():
        raise FileNotFoundError(f"Config not found: {config_file.resolve()}")

    config = json.loads(config_file.read_text())
    proxy_port = config["proxy_port"]
    instances = config["instances"]

    if not instances:
        raise ValueError("No instances defined in config")

    routes: list[Mount] = []
    mcp_apps: dict[str, FastMCP] = {}

    for name in instances:
        backend_url = f"http://localhost:{proxy_port}/{name}"
        mcp_instance = create_mcp_for_instance(name, backend_url)
        mcp_apps[name] = mcp_instance

        # Calling streamable_http_app() creates the session manager lazily.
        # The returned Starlette app has its own lifespan that calls
        # session_manager.run(), but when mounted as a sub-app that inner
        # lifespan is NOT invoked by Starlette. We manage it ourselves in
        # the outer lifespan below.
        inner_app = mcp_instance.streamable_http_app()
        routes.append(Mount(f"/mcp/{name}", app=inner_app))
        logger.info("Mounted /mcp/%s -> %s", name, backend_url)

    @asynccontextmanager
    async def lifespan(app: Starlette):
        # Start each instance's session manager. The session manager
        # owns the task group that serves MCP sessions — without this,
        # incoming requests would fail.
        async with contextlib.AsyncExitStack() as stack:
            for inst_name, mcp_inst in mcp_apps.items():
                if mcp_inst._session_manager is not None:
                    await stack.enter_async_context(
                        mcp_inst._session_manager.run()
                    )
                    logger.info("Session manager started for %s", inst_name)
            logger.info(
                "OpenViking multi-instance MCP server ready: %s",
                ", ".join(f"/mcp/{n}" for n in instances),
            )
            yield
        logger.info("All session managers shut down")

    return Starlette(routes=routes, lifespan=lifespan)


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Multi-instance OpenViking MCP server",
    )
    parser.add_argument(
        "--port", type=int, default=2033,
        help="Port to listen on (default: 2033)",
    )
    parser.add_argument(
        "--config", type=str, default="instances.json",
        help="Path to instances.json (default: instances.json)",
    )
    parser.add_argument(
        "--log-level", type=str, default="warning",
        choices=["debug", "info", "warning", "error", "critical"],
        help="Log level (default: warning)",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level.upper()),
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )

    app = build_app(args.config)

    uvicorn.run(
        app,
        host="127.0.0.1",
        port=args.port,
        log_level=args.log_level,
    )
