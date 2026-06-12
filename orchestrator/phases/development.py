"""개발 단계 — Claude 구현(tool loop) → GPT diff 리뷰 (설계 문서 4절).

구현 모델은 도구로 파일을 만지고, 리뷰는 git diff 산출물만 본다.
리뷰 판정은 "APPROVE" 또는 "- " 목록(REQUEST_CHANGES)으로 강제한다.
"""
import subprocess
from pathlib import Path
from typing import Callable

from adapters.base import LLMClient
from artifacts.schema import ArtifactStore, DiffArtifact, ReviewVerdict, Spec
from orchestrator.tool_loop import run_tool_loop
from orchestrator.tools import ToolExecutor

IMPLEMENTER_SYSTEM = """너는 구현 담당이다. 주어진 도구로 대상 repo를 수정해
spec의 requirements를 구현한다. 기존 코드 스타일을 따른다.
구현이 끝나면 도구 호출 없이, 리뷰어를 위한 변경 요약만 텍스트로 출력하고 종료한다."""

REVIEWER_SYSTEM = """너는 diff 리뷰어다. spec 대비 (1) 요구사항 충족 (2) 명백한 버그만 본다.
통과면 정확히 "APPROVE"만 출력한다.
아니면 고칠 점을 "- "로 시작하는 목록으로만 출력한다 (그 외 텍스트 금지)."""


def git_diff(target_repo: Path) -> tuple[str, list[str]]:
    """대상 repo의 작업 트리 diff와 변경 파일 목록 (untracked 포함)."""
    diff = subprocess.run(["git", "-C", str(target_repo), "diff"],
                          capture_output=True, text=True, encoding="utf-8").stdout
    status = subprocess.run(["git", "-C", str(target_repo), "status", "--porcelain"],
                            capture_output=True, text=True, encoding="utf-8").stdout
    files = [line[3:].strip() for line in status.splitlines() if line.strip()]
    return diff, files


async def run_development(implementer: LLMClient, reviewer: LLMClient, spec: Spec,
                          executor: ToolExecutor, store: ArtifactStore, max_turns: int,
                          test_feedback: str | None = None,
                          diff_fn: Callable[[], tuple[str, list[str]]] | None = None,
                          ) -> tuple[DiffArtifact, ReviewVerdict]:
    diff_fn = diff_fn or (lambda: (_ for _ in ()).throw(RuntimeError("diff_fn 필요")))
    prompt = f"다음 spec을 구현하라.\n\n{spec.to_markdown()}"
    if test_feedback:  # 검증 게이트 실패 로그가 다음 입력이 된다 (Iteration model)
        prompt += f"\n\n직전 구현이 테스트에 실패했다. 실패 로그:\n```\n{test_feedback}\n```"
    messages = [{"role": "user", "content": prompt}]

    artifact: DiffArtifact | None = None
    verdict = ReviewVerdict(approved=False)

    for turn in range(1, max_turns + 1):
        final = await run_tool_loop(implementer, IMPLEMENTER_SYSTEM, messages,
                                    executor, max_tool_calls=50)
        diff_text, files = diff_fn()
        artifact = DiffArtifact(diff_text=diff_text, files_changed=files,
                                implement_notes=final.text)
        store.save(f"diff_turn{turn}.patch", diff_text)

        review = await reviewer.run(REVIEWER_SYSTEM, [{"role": "user", "content":
            f"## Spec\n{spec.to_markdown()}\n\n## 변경 요약\n{final.text}\n\n## Diff\n```diff\n{diff_text}\n```"}])
        if review.text.strip() == "APPROVE":
            verdict = ReviewVerdict(approved=True)
            break
        comments = [line[2:] for line in review.text.splitlines() if line.startswith("- ")]
        verdict = ReviewVerdict(approved=False, comments=comments)
        store.save(f"review_turn{turn}.md", review.text)
        messages.append({"role": "user", "content":
            "리뷰에서 다음 지적이 나왔다. 도구로 수정하라:\n" + review.text})

    store.save_model("diff_artifact.json", artifact)
    return artifact, verdict
