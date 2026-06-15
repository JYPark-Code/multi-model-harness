"""Claude Code CLI 어댑터 — 구독 인증으로 API 크레딧 없이 Claude를 호출한다.

코어 API 어댑터(claude_client.py)와의 차이: CLI는 tool_use를 호출자에게 돌려주지
않고 자기 루프에서 직접 실행한다. 따라서 두 계열로 나눈다.
- ClaudeCLIClient: 텍스트 전용(critic 역할). 도구를 전부 끄고 1회 응답만 받는다.
  하네스의 tool loop 계약과 충돌하지 않는다.
- ClaudeCLIAgent: 구현 위임(implementer 역할). CLI가 target repo에서 자체
  도구(Read/Write/Edit/Glob/Grep)로 구현하고 변경 요약 텍스트만 반환한다.
  inner tool loop 소유권을 CLI에 양보하는 대신, 단계 전이·산출물·검증 게이트는
  하네스가 그대로 소유한다 (설계 원칙 1·2·3 유지, 원칙 5의 부분 타협).

폭주 방파제: CLI에 --max-turns가 없으므로(v2.1.174) 도구 화이트리스트(Bash 제외)와
하네스 외곽 상한(max_review_turns/max_dev_loops) + 프로세스 타임아웃으로 막는다.
"""
import asyncio
import json
import shutil
from pathlib import Path

from adapters.base import LLMClient, ModelResponse
from adapters.retry import Transient, with_retry

# 구현 위임 시 허용 도구 — Bash 제외(테스트 실행은 검증 게이트의 몫, 결정론 유지)
AGENT_TOOLS = "Read,Write,Edit,Glob,Grep"


def transcript(messages: list[dict]) -> str:
    """공통 메시지 형식을 단일 프롬프트로 직렬화 — CLI 호출은 stateless라서
    대화 이력을 매번 텍스트로 평탄화해 전달한다."""
    parts = []
    for m in messages:
        if m["role"] not in ("user", "assistant"):
            raise ValueError(f"CLI 어댑터가 지원하지 않는 role: {m['role']}")
        parts.append(f"[{m['role']}]\n{m['content']}")
    return "\n\n".join(parts)


def parse_cli_json(stdout: str) -> str:
    """`--output-format json` 결과에서 최종 텍스트를 꺼낸다."""
    data = json.loads(stdout)
    if data.get("is_error") or "result" not in data:
        raise RuntimeError(
            f"claude CLI 오류 응답: subtype={data.get('subtype')!r} — {stdout[:500]}")
    return data["result"]


class _ClaudeCLIBase(LLMClient):

    def __init__(self, model: str = "", timeout: float = 300.0,
                 cwd: Path | None = None, retries: int = 4):
        exe = shutil.which("claude")
        if not exe:
            raise FileNotFoundError(
                "claude CLI를 PATH에서 찾을 수 없다 — Claude Code 설치/로그인 필요")
        self.exe = exe
        self.model = model      # 빈 값이면 CLI 기본 모델 (구독 플랜이 결정)
        self.timeout = timeout
        self.cwd = cwd
        self.retries = retries

    def vendor_tools(self, tools):
        # CLI는 외부 tool 정의를 받지 않는다(자체 내장 도구만) — 변환할 것이 없다
        return tools

    async def _invoke(self, system: str, prompt: str, extra_args: list[str]) -> str:
        # CLI 호출 실패(타임아웃·비정상 종료·is_error)는 외부 프로세스 변덕이라 일시로 본다 —
        # 영구 오류(잘못된 인자 등)도 재시도 소진 후 같은 메시지로 표면화되므로 안전하다
        return await with_retry(lambda: self._attempt(system, prompt, extra_args),
                                attempts=self.retries)

    async def _attempt(self, system: str, prompt: str, extra_args: list[str]) -> str:
        args = [self.exe, "-p", "--output-format", "json",
                "--no-session-persistence", "--system-prompt", system, *extra_args]
        if self.model:
            args += ["--model", self.model]
        # 프롬프트는 stdin으로 — Windows argv 길이 제한(약 32K) 회피
        proc = await asyncio.create_subprocess_exec(
            *args, cwd=str(self.cwd) if self.cwd else None,
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE)
        try:
            out, err = await asyncio.wait_for(
                proc.communicate(prompt.encode("utf-8")), self.timeout)
        except asyncio.TimeoutError:
            proc.kill()
            raise Transient(f"claude CLI 타임아웃 ({self.timeout}s)")
        if proc.returncode != 0:
            raise Transient(
                f"claude CLI 종료 코드 {proc.returncode}: {err.decode('utf-8', 'replace')[:500]}")
        try:
            return parse_cli_json(out.decode("utf-8"))
        except RuntimeError as e:   # is_error 응답(overloaded 등) — 다시 시도해 볼 가치가 있다
            raise Transient(str(e)) from e


class ClaudeCLIClient(_ClaudeCLIBase):
    """텍스트 전용 — 도구를 전부 끄고 모델 응답 1회만 받는다 (critic 등)."""

    async def run(self, system, messages, tools=None) -> ModelResponse:
        if tools:
            raise NotImplementedError(
                "ClaudeCLIClient는 tool_use를 반환할 수 없다 — 도구가 필요하면 "
                "ClaudeClient(API) 또는 ClaudeCLIAgent(위임)를 사용")
        text = await self._invoke(system, transcript(messages), ["--tools", ""])
        return ModelResponse(text=text, stop_reason="end")


class ClaudeCLIAgent(_ClaudeCLIBase):
    """구현 위임 — CLI가 target repo에서 자체 도구로 작업을 끝내고 요약만 반환한다.

    tool_use를 절대 반환하지 않으므로 run_tool_loop에 그대로 끼워도 루프는
    1회 호출로 끝난다 (하네스의 tools 정의는 무시 — CLI 내장 도구를 쓴다).
    """

    def __init__(self, target_repo: Path, model: str = "", timeout: float = 1800.0,
                 retries: int = 4):
        super().__init__(model=model, timeout=timeout, cwd=target_repo, retries=retries)

    async def run(self, system, messages, tools=None) -> ModelResponse:
        text = await self._invoke(system, transcript(messages), [
            "--tools", AGENT_TOOLS,
            "--permission-mode", "acceptEdits",   # 헤드리스에선 묻지 못한다 — 파일 편집 자동 승인
        ])
        return ModelResponse(text=text, stop_reason="end")
