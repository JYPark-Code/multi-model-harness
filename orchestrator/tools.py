"""도구 실행 추상화 — 오케스트레이터는 ToolExecutor만 알고, MCP는 구현 세부다.

(Interface design: 테스트에서는 LocalToolExecutor로 같은 루프를 결정론적으로 돌린다.)
"""
import json
import os
import sys
from abc import ABC, abstractmethod
from contextlib import AsyncExitStack
from pathlib import Path
from typing import Callable

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


class ToolExecutor(ABC):

    @abstractmethod
    async def call(self, name: str, arguments: dict) -> str: ...

    @abstractmethod
    def tool_defs(self) -> list[dict]:
        """공통 tool 정의: {name, description, input_schema}"""


class MCPToolExecutor(ToolExecutor):
    """파일시스템 MCP 서버를 stdio 서브프로세스로 띄우고 세션을 유지한다."""

    def __init__(self, fs_root: Path):
        self.fs_root = fs_root
        self._stack = AsyncExitStack()
        self._session: ClientSession | None = None
        self._defs: list[dict] = []

    async def __aenter__(self):
        params = StdioServerParameters(
            command=sys.executable, args=["-m", "mcp_servers.filesystem"],
            cwd=str(Path(__file__).parent.parent),
            env={**os.environ, "HARNESS_FS_ROOT": str(self.fs_root)})
        read, write = await self._stack.enter_async_context(stdio_client(params))
        self._session = await self._stack.enter_async_context(ClientSession(read, write))
        await self._session.initialize()
        listed = await self._session.list_tools()
        self._defs = [{"name": t.name, "description": t.description or "",
                       "input_schema": t.inputSchema} for t in listed.tools]
        return self

    async def __aexit__(self, *exc):
        await self._stack.aclose()

    async def call(self, name, arguments) -> str:
        result = await self._session.call_tool(name, arguments)
        parts = [c.text for c in result.content if getattr(c, "type", "") == "text"]
        text = "\n".join(parts) if parts else json.dumps([c.model_dump() for c in result.content])
        if result.isError:
            # FastMCP는 툴 예외를 isError 결과로 돌려준다 — 정상 결과로 오인되지 않게 예외로 승격
            raise RuntimeError(f"{name} failed: {text}")
        return text

    def tool_defs(self):
        return self._defs


class LocalToolExecutor(ToolExecutor):
    """테스트용 — 콜러블 직접 실행 (MCP 왕복 없이 같은 루프 검증)."""

    def __init__(self, tools: dict[str, Callable[..., str]], defs: list[dict]):
        self.tools = tools
        self._defs = defs
        self.calls: list[tuple[str, dict]] = []

    async def call(self, name, arguments) -> str:
        self.calls.append((name, arguments))
        return self.tools[name](**arguments)

    def tool_defs(self):
        return self._defs
