"""LLM 어댑터 공통 인터페이스 — GPT·Claude를 같은 모양으로 (설계 문서 5절).

벤더별 응답(OpenAI tool_calls vs Anthropic tool_use 블록)을 여기서 정규화한다.
오케스트레이터는 ModelResponse만 알고, 벤더 SDK 타입은 어댑터 밖으로 새지 않는다.
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class ModelResponse:
    text: str                          # 텍스트 응답 (tool 호출만 있으면 빈 문자열)
    tool_calls: list[ToolCall] = field(default_factory=list)
    stop_reason: str = "end"           # "end" | "tool_use" | "max_tokens"
    raw: Any = None                    # 디버깅용 원본 (오케스트레이터는 사용 금지)


@dataclass
class ToolResult:
    tool_call_id: str
    content: str
    is_error: bool = False


class LLMClient(ABC):
    """messages in → ModelResponse out. 대화 상태는 호출자가 소유한다 (stateless)."""

    @abstractmethod
    async def run(self, system: str, messages: list[dict],
                  tools: list[dict] | None = None) -> ModelResponse:
        """messages: [{"role": "user"|"assistant", "content": str}] 공통 형식.

        tool 결과 전달은 [{"role": "tool_results", "results": [ToolResult...]}] 항목으로 —
        벤더별 변환(어시스턴트 tool_use 블록 재구성 등)은 어댑터가 책임진다.
        """

    @abstractmethod
    def vendor_tools(self, tools: list[dict]) -> list[dict]:
        """공통 tool 정의({name, description, input_schema})를 벤더 형식으로 변환."""
