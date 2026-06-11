"""하네스 자체의 결정론적 테스트 — fake 어댑터로 제어 흐름을 검증한다.

검증 대상: 단계 전이, 비평 반영, tool loop, 리뷰 게이트, 테스트 실패 루프백, 턴 상한.
모델 출력 품질은 여기서 검증하지 않는다 (그건 결정론적이지 않다).
"""
import asyncio
import json

import pytest

from adapters.base import ModelResponse, ToolCall
from adapters.fake import FakeClient
from artifacts.schema import ArtifactStore, Spec, TestReport
from config import HarnessConfig
from orchestrator.phases.development import run_development
from orchestrator.phases.planning import run_planning
from orchestrator.runner import run_pipeline
from orchestrator.tools import LocalToolExecutor

SPEC_JSON_V1 = json.dumps({"summary": "주문 조회 페이징", "requirements": ["페이지 파라미터"],
                           "out_of_scope": ["정렬"]})
SPEC_JSON_V2 = json.dumps({"summary": "주문 조회 페이징 v2",
                           "requirements": ["페이지 파라미터", "기본 페이지 크기 20"],
                           "out_of_scope": ["정렬"]})

FS_TOOL_DEFS = [{"name": "write_file", "description": "write",
                 "input_schema": {"type": "object", "properties": {
                     "path": {"type": "string"}, "content": {"type": "string"}},
                     "required": ["path", "content"]}}]


def make_executor(files: dict) -> LocalToolExecutor:
    return LocalToolExecutor(
        tools={"write_file": lambda path, content: files.__setitem__(path, content) or f"written: {path}"},
        defs=FS_TOOL_DEFS)


def test_planning_converges_on_first_agree(tmp_path):
    generator = FakeClient([ModelResponse(text=SPEC_JSON_V1)])
    critic = FakeClient([ModelResponse(text="AGREE")])
    store = ArtifactStore(tmp_path)

    spec = asyncio.run(run_planning(generator, critic, "페이징 추가", store, max_turns=4))

    assert spec.revision == 1 and spec.requirements == ["페이지 파라미터"]
    assert (store.run_dir / "spec.md").exists()


def test_planning_incorporates_critique_then_agrees(tmp_path):
    generator = FakeClient([ModelResponse(text=SPEC_JSON_V1), ModelResponse(text=SPEC_JSON_V2)])
    critic = FakeClient([ModelResponse(text="- 기본 페이지 크기가 없다"),
                         ModelResponse(text="AGREE")])
    store = ArtifactStore(tmp_path)

    spec = asyncio.run(run_planning(generator, critic, "페이징 추가", store, max_turns=4))

    assert spec.revision == 2 and "기본 페이지 크기 20" in spec.requirements
    # 비평이 생성기의 다음 입력에 들어갔는지 — 핸드오프 검증
    assert "기본 페이지 크기가 없다" in generator.calls[1]["messages"][-1]["content"]
    assert (store.run_dir / "critique_rev1.md").exists()


def test_planning_stops_at_max_turns(tmp_path):
    generator = FakeClient([ModelResponse(text=SPEC_JSON_V1)] * 2)
    critic = FakeClient([ModelResponse(text="- 계속 불만")] * 2)  # 영원히 합의 안 함
    store = ArtifactStore(tmp_path)

    spec = asyncio.run(run_planning(generator, critic, "태스크", store, max_turns=2))

    assert spec is not None  # 상한에서 강제 진행 — FakeClient가 더 호출되면 AssertionError
    assert len(generator.calls) == 2


def test_development_runs_tools_and_passes_review(tmp_path):
    files: dict = {}
    executor = make_executor(files)
    implementer = FakeClient([
        ModelResponse(text="", stop_reason="tool_use",
                      tool_calls=[ToolCall(id="t1", name="write_file",
                                           arguments={"path": "a.java", "content": "code"})]),
        ModelResponse(text="a.java에 페이징을 구현했다"),
    ])
    reviewer = FakeClient([ModelResponse(text="APPROVE")])
    spec = Spec(task="t", summary="s", requirements=["r"], out_of_scope=[])
    store = ArtifactStore(tmp_path)

    artifact, verdict = asyncio.run(run_development(
        implementer, reviewer, spec, executor, store, max_turns=3,
        diff_fn=lambda: ("diff --git a/a.java", ["a.java"])))

    assert files["a.java"] == "code"            # 도구가 실제 실행됨
    assert verdict.approved
    assert artifact.files_changed == ["a.java"]
    assert "페이징" in artifact.implement_notes


def test_development_feeds_review_comments_back(tmp_path):
    files: dict = {}
    executor = make_executor(files)
    implementer = FakeClient([
        ModelResponse(text="1차 구현 완료"),                       # 1차: 도구 없이 종료
        ModelResponse(text="지적 반영해 수정 완료"),                # 2차: 리뷰 반영
    ])
    reviewer = FakeClient([ModelResponse(text="- 예외 처리가 없다"),
                           ModelResponse(text="APPROVE")])
    spec = Spec(task="t", summary="s", requirements=["r"], out_of_scope=[])
    store = ArtifactStore(tmp_path)

    artifact, verdict = asyncio.run(run_development(
        implementer, reviewer, spec, executor, store, max_turns=3,
        diff_fn=lambda: ("d", ["f"])))

    assert verdict.approved
    # 리뷰 지적이 구현 모델의 다음 입력에 들어갔는지
    assert "예외 처리가 없다" in implementer.calls[1]["messages"][-1]["content"]


def test_pipeline_loops_back_on_gate_failure(tmp_path):
    cfg = HarnessConfig(max_planning_turns=2, max_review_turns=2, max_dev_loops=2,
                        runs_dir=tmp_path)
    store = ArtifactStore(tmp_path)
    planner = FakeClient([ModelResponse(text=SPEC_JSON_V1)])
    critic = FakeClient([ModelResponse(text="AGREE")])
    implementer = FakeClient([ModelResponse(text="구현 1차"), ModelResponse(text="구현 2차")])
    reviewer = FakeClient([ModelResponse(text="APPROVE"), ModelResponse(text="APPROVE")])

    reports = [TestReport(passed=False, exit_code=1, command="t", log_tail="FAILED: NPE"),
               TestReport(passed=True, exit_code=0, command="t", log_tail="OK")]
    gate_fn = lambda: reports.pop(0)

    final = asyncio.run(run_pipeline(
        cfg, store, "태스크", planner, critic, implementer, reviewer,
        executor=make_executor({}), diff_fn=lambda: ("d", []), gate_fn=gate_fn))

    assert final.passed
    # 실패 로그가 2차 개발의 입력으로 들어갔는지 — 루프백의 핵심 검증
    assert "FAILED: NPE" in implementer.calls[1]["messages"][0]["content"]
    assert (store.run_dir / "test_report_loop2.json").exists()


def test_tool_loop_enforces_max_calls(tmp_path):
    executor = make_executor({})
    endless = ModelResponse(text="", stop_reason="tool_use",
                            tool_calls=[ToolCall(id="t", name="write_file",
                                                 arguments={"path": "x", "content": "y"})])
    implementer = FakeClient([endless] * 10)
    spec = Spec(task="t", summary="s", requirements=["r"], out_of_scope=[])

    from orchestrator.tool_loop import run_tool_loop
    with pytest.raises(RuntimeError, match="상한"):
        asyncio.run(run_tool_loop(implementer, "sys",
                                  [{"role": "user", "content": "go"}], executor, max_tool_calls=3))
