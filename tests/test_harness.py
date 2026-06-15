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
from orchestrator.phases.planning import _parse_spec_json, _repair_json, run_planning
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


def test_parse_spec_repairs_invalid_escape():
    # E2 L1-3 중단 재현: requirement 문자열에 정규식 백슬래시(\d) — 엄격 파싱은 'Invalid \escape'
    bad = '{"summary": "정규식 검증", "requirements": ["\\d+ 형식만 허용"], "out_of_scope": []}'
    spec = _parse_spec_json(bad, "태스크", 1)
    assert spec.requirements == ["\\d+ 형식만 허용"]   # 보수 후 백슬래시가 값에 보존됨


def test_repair_preserves_valid_escapes():
    # 이미 유효한 escape(\n·\"·이스케이프된 \\)는 보수가 손상하지 않아야 한다
    valid = json.dumps({"summary": "줄1\n줄2 \"인용\" 경로 C:\\tmp\\d",
                        "requirements": ["r"], "out_of_scope": []})
    assert json.loads(_repair_json(valid)) == json.loads(valid)


def test_parse_spec_strips_trailing_comma():
    bad = '{"summary": "s", "requirements": ["a", "b",], "out_of_scope": [],}'
    assert _parse_spec_json(bad, "t", 1).requirements == ["a", "b"]


def test_parse_spec_extracts_json_from_prose():
    bad = '여기 spec입니다:\n{"summary": "s", "requirements": ["a"], "out_of_scope": []}\n끝.'
    assert _parse_spec_json(bad, "t", 1).summary == "s"


def test_planning_self_repairs_on_unparseable_json(tmp_path):
    # 보수로도 못 살리는 완전 비JSON → 자가 교정 재요청 → 유효 JSON으로 복구
    generator = FakeClient([ModelResponse(text="죄송하지만 JSON을 출력할 수 없습니다"),
                            ModelResponse(text=SPEC_JSON_V1)])
    critic = FakeClient([ModelResponse(text="AGREE")])
    store = ArtifactStore(tmp_path)

    spec = asyncio.run(run_planning(generator, critic, "태스크", store, max_turns=4))

    assert spec.requirements == ["페이지 파라미터"]
    assert len(generator.calls) == 2                       # 초기 + 재요청
    assert (store.run_dir / "spec_rev1_badjson.md").exists()
    assert "유효한 JSON이 아니다" in generator.calls[1]["messages"][-1]["content"]


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


# --- 하네스 보강: per-call 타임아웃 + infra_fail 분류 (E3 run2 ~32분·docker 다운 교훈) ---

def test_config_has_per_call_timeouts():
    # degraded API가 한 런을 무한정 끌지 않게 하는 방파제 — 노브가 존재하고 기본값이 합리적인가
    cfg = HarnessConfig()
    assert cfg.planner_timeout_s == 120.0
    assert cfg.critic_timeout_s == 300.0
    assert cfg.implement_timeout_s == 900.0   # 1800→900: degraded 런이 30분까지 부풀던 것 차단


def _load_repeat_l1():
    import pathlib
    import sys
    scripts = str(pathlib.Path(__file__).resolve().parent.parent / "scripts")
    if scripts not in sys.path:
        sys.path.insert(0, scripts)
    import repeat_l1
    return repeat_l1


def test_tally_classifies_infra_fail_separately():
    # 핵심 회귀 방지: docker 등 인프라 실패 런이 조용히 드롭돼 pass_rate가 거짓이 되던 버그
    rl1 = _load_repeat_l1()
    results = [
        {"outcome": "pass", "wall_s": 100.0, "diff_added": 1},
        {"outcome": "gate_fail", "wall_s": 200.0, "diff_added": 5},
        {"outcome": "infra_fail", "error": "docker compose down -v 실패"},
        {"outcome": "infra_fail", "error": "docker compose down -v 실패"},
        {"outcome": "harness_abort", "error": "git switch 실패"},
    ]
    t = rl1.tally_of(results)
    assert t["n"] == 5
    assert t["pass"] == 1 and t["gate_fail"] == 1 and t["harness_abort"] == 1
    assert t["infra_fail"] == 2                  # 별도 집계 — 더 이상 조용히 사라지지 않는다
    assert t["pass_rate"] == "1/2"               # 완주(pass+gate_fail)만 분모, infra_fail 제외
