"""
Process manager for local `knowledge` MCP server.
"""
from __future__ import annotations

import logging
import socket
import subprocess
import sys
import time
from pathlib import Path

import config

logger = logging.getLogger("cafe_bot.local_mcp_manager")


class LocalMCPServerManager:
    def __init__(self):
        self.host = getattr(config, "LOCAL_MCP_HOST", "127.0.0.1")
        self.port = int(getattr(config, "LOCAL_MCP_PORT", 8765))
        self.path = getattr(config, "LOCAL_MCP_PATH", "/mcp")
        self.root = Path(getattr(config, "PRIORITY_KNOWLEDGE_PATH", config.BASE_DIR / "knowledge"))
        self.instruction_file = getattr(config, "PRIORITY_INSTRUCTION_FILE", "instruction.md")
        self.server_name = getattr(config, "CLAUDE_MCP_SERVER_NAME", "knowledge_mcp")
        self.auto_start = bool(getattr(config, "LOCAL_MCP_AUTO_START", True))
        self.mcp_enabled = bool(getattr(config, "CLAUDE_MCP_ENABLED", False))
        self.process: subprocess.Popen | None = None

    def should_start(self) -> bool:
        return self.mcp_enabled and self.auto_start

    def _is_port_open(self) -> bool:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(0.3)
            return sock.connect_ex((self.host, self.port)) == 0

    def start_if_needed(self) -> bool:
        if not self.should_start():
            return False

        if self._is_port_open():
            logger.info("Local MCP server already listening at %s:%s", self.host, self.port)
            return True

        cmd = [
            sys.executable,
            "-m",
            "modules.knowledge_mcp_server",
            "--root",
            str(self.root),
            "--instruction-file",
            self.instruction_file,
            "--host",
            self.host,
            "--port",
            str(self.port),
            "--path",
            self.path,
            "--server-name",
            self.server_name,
        ]
        logger.info("Starting local MCP server: %s", " ".join(cmd))
        try:
            self.process = subprocess.Popen(
                cmd,
                cwd=str(config.BASE_DIR),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except Exception as e:
            logger.error("Failed to start local MCP server: %s", e)
            return False

        for _ in range(50):
            if self._is_port_open():
                logger.info("Local MCP server started at %s:%s%s", self.host, self.port, self.path)
                return True
            if self.process and self.process.poll() is not None:
                logger.error("Local MCP server exited early with code %s", self.process.returncode)
                self.process = None
                return False
            time.sleep(0.2)

        logger.error("Timed out waiting for local MCP server to start")
        return False

    def stop(self) -> None:
        if not self.process:
            return
        if self.process.poll() is not None:
            self.process = None
            return

        logger.info("Stopping local MCP server...")
        try:
            self.process.terminate()
            self.process.wait(timeout=3)
        except Exception:
            try:
                self.process.kill()
            except Exception:
                pass
        finally:
            self.process = None

