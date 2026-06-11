"""오케스트레이터 진입점 — 기획 → 개발 → 테스트 상태머신 (루프 소유자).

실행: python -m orchestrator.runner "태스크 설명"
"""
import argparse
import asyncio
import sys
from functools import partial

from adapters.base import LLMClient
from artifacts.schema import ArtifactStore, TestReport
from config import HarnessConfig
from evals.gate import run_gate
from orchestrator.phases.development import git_diff, run_development
from orchestrator.phases.planning import run_planning
from orchestrator.tools import ToolExecutor


async def run_pipeline(cfg: HarnessConfig, store: ArtifactStore, task: str,
                       planner: LLMClient, critic: LLMClient,
                       implementer: LLMClient, reviewer: LLMClient,
                       executor: ToolExecutor, diff_fn, gate_fn) -> TestReport:
    """단계 전이는 전부 여기서 — 모델은 단계를 모른다 (Decomposition)."""
    spec = await run_planning(planner, critic, task, store, cfg.max_planning_turns)

    feedback = None
    report = TestReport(passed=False, exit_code=-1, command=cfg.test_command, log_tail="")
    for loop in range(1, cfg.max_dev_loops + 1):
        await run_development(implementer, reviewer, spec, executor, store,
                              cfg.max_review_turns, test_feedback=feedback, diff_fn=diff_fn)
        report = gate_fn()
        store.save_model(f"test_report_loop{loop}.json", report)
        if report.passed:
            break
        feedback = report.log_tail   # 실패 로그 → 다음 개발 루프의 입력

    store.save_model("final_report.json", report)
    return report


async def main() -> int:
    parser = argparse.ArgumentParser(description="Multi-Model Orchestration Harness")
    parser.add_argument("task", help="대상 repo에 수행할 작업 설명")
    args = parser.parse_args()

    # 실제 어댑터는 여기서만 임포트 — 키 없이도 fake 기반 테스트는 돌게 (의존 격리)
    from adapters.claude_client import ClaudeClient
    from adapters.openai_client import OpenAIClient
    from orchestrator.tools import MCPToolExecutor

    cfg = HarnessConfig()
    store = ArtifactStore(cfg.runs_dir)
    print(f"[harness] run dir: {store.run_dir}")
    print(f"[harness] target:  {cfg.target_repo}")

    async with MCPToolExecutor(cfg.target_repo) as executor:
        report = await run_pipeline(
            cfg, store, args.task,
            planner=OpenAIClient(cfg.planning_model),
            critic=ClaudeClient(cfg.review_model),
            implementer=ClaudeClient(cfg.implement_model),
            reviewer=OpenAIClient(cfg.planning_model),
            executor=executor,
            diff_fn=partial(git_diff, cfg.target_repo),
            gate_fn=partial(run_gate, cfg.test_command, cfg.target_repo))

    print(f"[harness] gate: {'PASSED' if report.passed else 'FAILED'} (exit {report.exit_code})")
    return 0 if report.passed else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
