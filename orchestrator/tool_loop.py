"""tool loop — 오케스트레이터가 소유하는 루프 (설계 문서 5절의 패턴을 그대로 구현).

모델이 tool_use로 멈추면 도구를 실행해 결과를 되먹이고, 텍스트로 끝나면 반환한다.
max_tool_calls가 폭주의 방파제다.
"""
from adapters.base import LLMClient, ModelResponse, ToolResult
from orchestrator.tools import ToolExecutor


async def run_tool_loop(client: LLMClient, system: str, messages: list[dict],
                        executor: ToolExecutor, max_tool_calls: int) -> ModelResponse:
    tools = executor.tool_defs()
    used = 0
    resp = await client.run(system, messages, tools=tools)

    while resp.stop_reason == "tool_use":
        if used + len(resp.tool_calls) > max_tool_calls:
            raise RuntimeError(f"tool loop 상한 초과 ({max_tool_calls}) — 발산 신호, 강제 종료")
        results = []
        for tc in resp.tool_calls:
            used += 1
            try:
                content = await executor.call(tc.name, tc.arguments)
                results.append(ToolResult(tool_call_id=tc.id, content=content))
            except Exception as e:  # 도구 실패는 모델에게 되먹여 스스로 고치게 한다
                results.append(ToolResult(tool_call_id=tc.id, content=f"ERROR: {e}", is_error=True))
        messages.append({"role": "assistant_tool_use", "content": resp.text,
                         "tool_calls": resp.tool_calls})
        messages.append({"role": "tool_results", "results": results})
        resp = await client.run(system, messages, tools=tools)

    return resp
