"""Claude CLI 어댑터 단위 테스트 — 서브프로세스 없이 직렬화·파싱·계약만 검증한다."""
import asyncio
import json
import shutil

import pytest

from adapters import claude_cli
from adapters.claude_cli import (AGENT_TOOLS, ClaudeCLIAgent, ClaudeCLIClient,
                                 parse_cli_json, transcript)
from adapters.retry import Transient

requires_cli = pytest.mark.skipif(shutil.which("claude") is None,
                                  reason="claude CLI 미설치")


class FakeProc:
    """서브프로세스 스텁 — _attempt의 실패 분류만 보기 위해 communicate 결과를 박아둔다."""
    def __init__(self, returncode, stdout=b"", stderr=b""):
        self.returncode = returncode
        self._out = (stdout, stderr)

    async def communicate(self, data=None):
        return self._out

    def kill(self):
        pass


def _patch_cli(monkeypatch, proc):
    monkeypatch.setattr(claude_cli.shutil, "which", lambda _: "claude")  # 생성자 통과
    async def fake_exec(*a, **k):
        return proc
    monkeypatch.setattr(claude_cli.asyncio, "create_subprocess_exec", fake_exec)


def test_transcript_serializes_user_assistant():
    text = transcript([{"role": "user", "content": "질문"},
                       {"role": "assistant", "content": "답"},
                       {"role": "user", "content": "후속"}])
    assert text == "[user]\n질문\n\n[assistant]\n답\n\n[user]\n후속"


def test_transcript_rejects_tool_roles():
    with pytest.raises(ValueError):
        transcript([{"role": "tool_results", "results": []}])


def test_parse_cli_json_returns_result():
    out = json.dumps({"type": "result", "subtype": "success",
                      "is_error": False, "result": "PONG", "session_id": "x"})
    assert parse_cli_json(out) == "PONG"


def test_parse_cli_json_raises_on_error():
    out = json.dumps({"type": "result", "subtype": "error_during_execution",
                      "is_error": True})
    with pytest.raises(RuntimeError):
        parse_cli_json(out)


def test_attempt_wraps_nonzero_exit_as_transient(monkeypatch):
    _patch_cli(monkeypatch, FakeProc(1, b"", b"boom"))
    client = ClaudeCLIClient()
    with pytest.raises(Transient):
        asyncio.run(client._attempt("sys", "p", ["--tools", ""]))


def test_attempt_wraps_is_error_response_as_transient(monkeypatch):
    body = json.dumps({"type": "result", "subtype": "error_during_execution",
                       "is_error": True}).encode("utf-8")
    _patch_cli(monkeypatch, FakeProc(0, body, b""))
    client = ClaudeCLIClient()
    with pytest.raises(Transient):
        asyncio.run(client._attempt("sys", "p", ["--tools", ""]))


def test_attempt_returns_result_on_success(monkeypatch):
    body = json.dumps({"type": "result", "is_error": False,
                       "result": "PONG"}).encode("utf-8")
    _patch_cli(monkeypatch, FakeProc(0, body, b""))
    client = ClaudeCLIClient()
    assert asyncio.run(client._attempt("sys", "p", ["--tools", ""])) == "PONG"


@requires_cli
def test_cli_client_rejects_tools():
    client = ClaudeCLIClient()
    with pytest.raises(NotImplementedError):
        asyncio.run(client.run("sys", [{"role": "user", "content": "x"}],
                               tools=[{"name": "t"}]))


@requires_cli
def test_cli_agent_ignores_tools_and_ends(tmp_path, monkeypatch):
    """위임 어댑터는 tool_use를 절대 반환하지 않는다 — run_tool_loop가 1회로 끝나는 근거."""
    agent = ClaudeCLIAgent(tmp_path)
    captured = {}

    async def fake_invoke(system, prompt, extra_args):
        captured["args"] = extra_args
        return "변경 요약"

    monkeypatch.setattr(agent, "_invoke", fake_invoke)
    resp = asyncio.run(agent.run("sys", [{"role": "user", "content": "구현하라"}],
                                 tools=[{"name": "read_file"}]))
    assert resp.stop_reason == "end"
    assert resp.text == "변경 요약"
    assert "--tools" in captured["args"] and AGENT_TOOLS in captured["args"]
