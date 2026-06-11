"""Claude 어댑터 — 코어 Messages API (Agent SDK 비사용, 설계 문서 5절).

Agent SDK를 쓰면 Anthropic의 하네스를 쓰는 것이지 내가 하네스를 만드는 게 아니다.
루프는 오케스트레이터가 소유하고, 여기는 1회 호출 + 형식 변환만 한다.
"""
from anthropic import AsyncAnthropic

from adapters.base import LLMClient, ModelResponse, ToolCall


class ClaudeClient(LLMClient):

    def __init__(self, model: str, max_tokens: int = 4096):
        self.client = AsyncAnthropic()
        self.model = model
        self.max_tokens = max_tokens

    async def run(self, system, messages, tools=None) -> ModelResponse:
        resp = await self.client.messages.create(
            model=self.model, max_tokens=self.max_tokens, system=system,
            messages=self._to_anthropic(messages),
            tools=self.vendor_tools(tools) if tools else [])
        text = "".join(b.text for b in resp.content if b.type == "text")
        tool_calls = [ToolCall(id=b.id, name=b.name, arguments=b.input)
                      for b in resp.content if b.type == "tool_use"]
        stop = {"tool_use": "tool_use", "max_tokens": "max_tokens"}.get(resp.stop_reason, "end")
        return ModelResponse(text=text, tool_calls=tool_calls, stop_reason=stop, raw=resp)

    def vendor_tools(self, tools):
        # 공통 형식이 Anthropic 형식과 동일 (name/description/input_schema)
        return tools

    @staticmethod
    def _to_anthropic(messages: list[dict]) -> list[dict]:
        out = []
        for m in messages:
            if m["role"] == "assistant_tool_use":
                content = ([{"type": "text", "text": m["content"]}] if m.get("content") else [])
                content += [{"type": "tool_use", "id": tc.id, "name": tc.name, "input": tc.arguments}
                            for tc in m["tool_calls"]]
                out.append({"role": "assistant", "content": content})
            elif m["role"] == "tool_results":
                out.append({"role": "user", "content": [
                    {"type": "tool_result", "tool_use_id": r.tool_call_id,
                     "content": r.content, "is_error": r.is_error}
                    for r in m["results"]]})
            else:
                out.append({"role": m["role"], "content": m["content"]})
        return out
