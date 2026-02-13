"""
Local MCP server for serving `knowledge` folder assets.

Tools:
1) search_docs(query, top_k, max_chars)
2) read_doc(file_name, start_line, max_lines)
"""
from __future__ import annotations

import argparse
import logging
from pathlib import Path

from modules.priority_knowledge import PriorityKnowledge

logger = logging.getLogger("knowledge_mcp_server")


class KnowledgeService:
    def __init__(self, root: Path, instruction_file: str):
        self.root = root.resolve()
        self.instruction_file = instruction_file
        self.kb = PriorityKnowledge(source_path=self.root, instruction_file=instruction_file)
        self.refresh()

    def refresh(self) -> None:
        self.kb.load()

    def _safe_resolve(self, file_name: str) -> Path | None:
        if not file_name:
            return None
        candidate = (self.root / file_name).resolve()
        try:
            candidate.relative_to(self.root)
        except ValueError:
            return None
        if not candidate.is_file():
            return None
        if candidate.suffix.lower() not in {".md", ".js"}:
            return None
        return candidate

    def search(self, query: str, top_k: int = 5, max_chars: int = 12000) -> str:
        self.refresh()
        top_k = max(1, min(20, int(top_k)))
        max_chars = max(500, min(30000, int(max_chars)))
        context = self.kb.retrieve_context(query, top_k=top_k)
        if not context:
            return "No matching references found."
        return context[:max_chars]

    def read(self, file_name: str, start_line: int = 1, max_lines: int = 250) -> str:
        path = self._safe_resolve(file_name)
        if path is None:
            return (
                "Invalid file_name. Allowed files are `.md` and `.js` inside the `knowledge` folder only."
            )

        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except Exception as e:
            return f"Failed to read file: {e}"

        lines = text.splitlines()
        total = len(lines)
        if total == 0:
            return f"{path.name} is empty."

        start_line = max(1, int(start_line))
        max_lines = max(1, min(1000, int(max_lines)))
        start_idx = min(start_line - 1, total - 1)
        end_idx = min(start_idx + max_lines, total)

        numbered = "\n".join(
            f"{i + 1:4}: {lines[i]}"
            for i in range(start_idx, end_idx)
        )
        return f"# {path.name} ({start_idx + 1}-{end_idx}/{total})\n{numbered}"


def build_server(service: KnowledgeService, server_name: str):
    # Imported lazily so the bot can still run without MCP package installed.
    from mcp.server.fastmcp import FastMCP

    mcp = FastMCP(server_name)

    @mcp.tool()
    def search_docs(query: str, top_k: int = 5, max_chars: int = 12000) -> str:
        """Search references from the local `knowledge` folder."""
        return service.search(query=query, top_k=top_k, max_chars=max_chars)

    @mcp.tool()
    def read_doc(file_name: str, start_line: int = 1, max_lines: int = 250) -> str:
        """Read a specific `.md`/`.js` file from the local `knowledge` folder."""
        return service.read(file_name=file_name, start_line=start_line, max_lines=max_lines)

    return mcp


def run_server(root: Path, instruction_file: str, host: str, port: int, path: str, server_name: str) -> None:
    service = KnowledgeService(root=root, instruction_file=instruction_file)
    mcp = build_server(service, server_name=server_name)
    mcp.settings.host = host
    mcp.settings.port = port
    mcp.settings.streamable_http_path = path if path.startswith("/") else f"/{path}"
    mcp.settings.mount_path = "/"
    mcp.run(transport="streamable-http")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Local MCP server for `knowledge` docs")
    parser.add_argument("--root", required=True, help="Path to knowledge folder")
    parser.add_argument("--instruction-file", default="instruction.md", help="Instruction file name")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--path", default="/mcp")
    parser.add_argument("--server-name", default="knowledge_mcp")
    return parser.parse_args()


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="[%(asctime)s] %(levelname)s %(name)s: %(message)s",
    )
    args = parse_args()
    run_server(
        root=Path(args.root),
        instruction_file=args.instruction_file,
        host=args.host,
        port=args.port,
        path=args.path,
        server_name=args.server_name,
    )


if __name__ == "__main__":
    main()
