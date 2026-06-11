"""기획 단계 — GPT 생성 ↔ Claude 비평 (generator ↔ critic, 설계 문서 4절).

핸드오프는 Spec 산출물로만. 비평가는 "AGREE" 또는 비평 목록만 낼 수 있다(자유 채팅 금지).
합의 또는 턴 상한에서 종료한다.
"""
import json
import re

from adapters.base import LLMClient
from artifacts.schema import ArtifactStore, Spec

GENERATOR_SYSTEM = """너는 소프트웨어 기획자다. 주어진 태스크에 대한 spec을 작성한다.
반드시 아래 JSON만 출력한다 (코드펜스·설명 금지):
{"summary": "...", "requirements": ["검증 가능한 요구사항", ...], "out_of_scope": ["...", ...]}
requirements는 각각 테스트로 확인 가능한 문장이어야 한다."""

CRITIC_SYSTEM = """너는 spec 비평가다. 구현 가능성·검증 가능성·범위만 본다.
spec이 충분하면 정확히 "AGREE"만 출력한다.
아니면 고쳐야 할 점을 "- "로 시작하는 목록으로만 출력한다 (그 외 텍스트 금지)."""


def _parse_spec_json(text: str, task: str, revision: int) -> Spec:
    cleaned = re.sub(r"^```(json)?|```$", "", text.strip(), flags=re.MULTILINE).strip()
    data = json.loads(cleaned)
    return Spec(task=task, summary=data["summary"], requirements=data["requirements"],
                out_of_scope=data.get("out_of_scope", []), revision=revision)


async def run_planning(generator: LLMClient, critic: LLMClient, task: str,
                       store: ArtifactStore, max_turns: int) -> Spec:
    gen_messages = [{"role": "user", "content": f"태스크: {task}"}]
    spec: Spec | None = None

    for turn in range(1, max_turns + 1):
        resp = await generator.run(GENERATOR_SYSTEM, gen_messages)
        spec = _parse_spec_json(resp.text, task, revision=turn)
        store.save(f"spec_rev{turn}.md", spec.to_markdown())

        verdict = await critic.run(
            CRITIC_SYSTEM, [{"role": "user", "content": spec.to_markdown()}])
        if verdict.text.strip() == "AGREE":
            break
        store.save(f"critique_rev{turn}.md", verdict.text)
        # 비평을 생성기의 다음 입력으로 — raw 대화가 아니라 구조화된 산출물 + 비평만 전달
        gen_messages += [{"role": "assistant", "content": resp.text},
                         {"role": "user", "content": f"비평을 반영해 spec JSON을 다시 작성하라:\n{verdict.text}"}]
    # 턴 상한 도달 시 마지막 spec으로 강제 진행 (수렴 실패를 숨기지 않고 산출물에 흔적이 남는다)

    store.save("spec.md", spec.to_markdown())
    return spec
