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


def _strip_fences(text: str) -> str:
    return re.sub(r"^```(json)?|```$", "", text.strip(), flags=re.MULTILINE).strip()


def _repair_json(text: str) -> str:
    """모델이 흔히 내는 JSON 오류를 결정론적으로 보수한다 (모델 호출 없음).
    1) object 경계만 추출 — 앞뒤 프로즈/잔여 펜스 제거.
    2) JSON에 없는 백슬래시 escape를 리터럴화 — `\\d`·경로·정규식 등이 'Invalid \\escape'
       크래시의 원인(E2 L1-3 중단). 이미 유효한 escape(`\\n`·`\\\\`·`\\"`·`\\uXXXX`)는 보존한다.
    3) 트레일링 콤마 제거.
    """
    s, e = text.find("{"), text.rfind("}")
    if 0 <= s < e:
        text = text[s:e + 1]
    # 유효 escape는 먼저 통째로 매칭해 그대로 두고(2글자 소비), 남은 단독 백슬래시만 이스케이프
    text = re.sub(r'\\(["\\/bfnrtu])|\\',
                  lambda m: m.group(0) if m.group(1) else "\\\\", text)
    text = re.sub(r",(\s*[}\]])", r"\1", text)
    return text


def _loads_lenient(text: str) -> dict:
    """엄격 파싱 우선, 실패 시 1회 보수 후 재시도. 그래도 실패하면 raise(호출부가 처리)."""
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return json.loads(_repair_json(text))


def _parse_spec_json(text: str, task: str, revision: int) -> Spec:
    data = _loads_lenient(_strip_fences(text))
    return Spec(task=task, summary=data["summary"], requirements=data["requirements"],
                out_of_scope=data.get("out_of_scope", []), revision=revision)


async def _generate_spec(generator: LLMClient, gen_messages: list[dict], task: str,
                         turn: int, store: ArtifactStore) -> Spec:
    """spec을 생성·파싱한다. 결정론적 보수로도 안 되면 오류를 모델에 돌려주고 JSON만 한 번
    다시 받는다 — 잘림·완전 비JSON 같은 구조적 깨짐의 안전망(흔적: *_badjson.md)."""
    resp = await generator.run(GENERATOR_SYSTEM, gen_messages)
    try:
        return _parse_spec_json(resp.text, task, turn)
    except (json.JSONDecodeError, KeyError, TypeError) as e:
        store.save(f"spec_rev{turn}_badjson.md", resp.text)
        retry = await generator.run(GENERATOR_SYSTEM, gen_messages + [
            {"role": "assistant", "content": resp.text},
            {"role": "user", "content":
                f"직전 출력이 유효한 JSON이 아니다 ({type(e).__name__}: {e}). "
                "설명·코드펜스 없이 JSON 객체만 다시 출력하라."}])
        return _parse_spec_json(retry.text, task, turn)


async def run_planning(generator: LLMClient, critic: LLMClient, task: str,
                       store: ArtifactStore, max_turns: int) -> Spec:
    gen_messages = [{"role": "user", "content": f"태스크: {task}"}]
    spec: Spec | None = None

    for turn in range(1, max_turns + 1):
        spec = await _generate_spec(generator, gen_messages, task, turn, store)
        store.save(f"spec_rev{turn}.md", spec.to_markdown())

        verdict = await critic.run(
            CRITIC_SYSTEM, [{"role": "user", "content": spec.to_markdown()}])
        if verdict.text.strip() == "AGREE":
            break
        store.save(f"critique_rev{turn}.md", verdict.text)
        # 비평을 생성기의 다음 입력으로 — raw 대화가 아니라 구조화된 산출물(현재 spec) + 비평만 전달
        gen_messages += [{"role": "assistant", "content": spec.to_markdown()},
                         {"role": "user", "content": f"비평을 반영해 spec JSON을 다시 작성하라:\n{verdict.text}"}]
    # 턴 상한 도달 시 마지막 spec으로 강제 진행 (수렴 실패를 숨기지 않고 산출물에 흔적이 남는다)

    store.save("spec.md", spec.to_markdown())
    return spec
