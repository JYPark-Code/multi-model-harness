"""파일시스템 MCP 서버 (직접 작성, 설계 문서 6절) — stdio로 동작.

모델이 target repo를 만지는 유일한 통로. root 밖 경로는 차단한다.
실행: HARNESS_FS_ROOT=<target repo> python -m mcp_servers.filesystem
"""
import os
from pathlib import Path

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("filesystem")

ROOT = Path(os.environ.get("HARNESS_FS_ROOT", ".")).resolve()


def _safe(path: str) -> Path:
    resolved = (ROOT / path).resolve()
    if not resolved.is_relative_to(ROOT):
        raise ValueError(f"root 밖 경로 접근 거부: {path}")
    return resolved


@mcp.tool()
def read_file(path: str) -> str:
    """root 기준 상대 경로의 텍스트 파일을 읽는다."""
    return _safe(path).read_text(encoding="utf-8")


@mcp.tool()
def write_file(path: str, content: str) -> str:
    """root 기준 상대 경로에 텍스트 파일을 쓴다 (부모 디렉터리 자동 생성)."""
    target = _safe(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return f"written: {path} ({len(content)} chars)"


@mcp.tool()
def list_dir(path: str = ".") -> str:
    """root 기준 상대 경로의 디렉터리 내용을 나열한다."""
    entries = sorted(_safe(path).iterdir(), key=lambda p: (p.is_file(), p.name))
    return "\n".join(f"{'[d]' if e.is_dir() else '[f]'} {e.name}" for e in entries)


if __name__ == "__main__":
    mcp.run()  # stdio transport
