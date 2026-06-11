"""스크립트된 fake 어댑터 — API 키 없이 하네스 루프 자체를 결정론적으로 검증한다.

하네스의 테스트 대상은 모델 출력 품질이 아니라 **제어 흐름**(단계 전이·턴 상한·루프백)이다.
"""
from adapters.base import LLMClient, ModelResponse


class FakeClient(LLMClient):
    """미리 정의된 응답을 순서대로 반환. 호출 내역을 기록한다."""

    def __init__(self, responses: list[ModelResponse]):
        self.responses = list(responses)
        self.calls: list[dict] = []   # 각 호출의 (system, messages, tools) 기록

    async def run(self, system, messages, tools=None) -> ModelResponse:
        self.calls.append({"system": system, "messages": list(messages), "tools": tools})
        if not self.responses:
            raise AssertionError("FakeClient: 준비된 응답보다 많이 호출됨 — 턴 상한 검증 실패 신호")
        return self.responses.pop(0)

    def vendor_tools(self, tools):
        return tools
