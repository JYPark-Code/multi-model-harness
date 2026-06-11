"""GPT 어댑터 — Chat Completions API. ClaudeClient와 대칭 (설계 문서 5절)."""
import json

from openai import AsyncOpenAI

from adapters.base import LLMClient, ModelResponse, ToolCall


class OpenAIClient(LLMClient):

    def __init__(self, model: str, max_tokens: int = 4096):
        self.client = AsyncOpenAI()
        self.model = model
        self.max_tokens = max_tokens

    async def run(self, system, messages, tools=None) -> ModelResponse:
        oai_messages = [{"role": "system", "content": system}] + self._to_openai(messages)
        resp = await self.client.chat.completions.create(
            model=self.model, max_completion_tokens=self.max_tokens,
            messages=oai_messages,
            **({"tools": self.vendor_tools(tools)} if tools else {}))
        choice = resp.choices[0]
        tool_calls = [ToolCall(id=tc.id, name=tc.function.name,
                               arguments=json.loads(tc.function.arguments or "{}"))
                      for tc in (choice.message.tool_calls or [])]
        stop = {"tool_calls": "tool_use", "length": "max_tokens"}.get(choice.finish_reason, "end")
        return ModelResponse(text=choice.message.content or "", tool_calls=tool_calls,
                             stop_reason=stop, raw=resp)

    def vendor_tools(self, tools):
        return [{"type": "function",
                 "function": {"name": t["name"], "description": t["description"],
                              "parameters": t["input_schema"]}}
                for t in tools]

    @staticmethod
    def _to_openai(messages: list[dict]) -> list[dict]:
        out = []
        for m in messages:
            if m["role"] == "assistant_tool_use":
                out.append({"role": "assistant", "content": m.get("content") or None,
                            "tool_calls": [{"id": tc.id, "type": "function",
                                            "function": {"name": tc.name,
                                                         "arguments": json.dumps(tc.arguments)}}
                                           for tc in m["tool_calls"]]})
            elif m["role"] == "tool_results":
                out += [{"role": "tool", "tool_call_id": r.tool_call_id, "content": r.content}
                        for r in m["results"]]
            else:
                out.append({"role": m["role"], "content": m["content"]})
        return out
