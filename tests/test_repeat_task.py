"""L2/L3 드라이버(repeat_task)와 structural_guard의 결정론적 단위 테스트.

repeat_task는 docker/모델 없이도 검증 가능한 순수 로직이 핵심이다: 인수 결과 파싱,
outcome 분류, 지표 집계. 이 안전망이 콘솔 인코딩·분류 회귀(거짓 통과/거짓 오구현)를 잡는다.
모델 출력 품질이나 실제 게이트 실행은 여기서 검증하지 않는다(비결정적·인프라 의존).
"""
import importlib
import pathlib
import sys


def _load(mod_name: str):
    scripts = str(pathlib.Path(__file__).resolve().parent.parent / "scripts")
    if scripts not in sys.path:
        sys.path.insert(0, scripts)
    return importlib.import_module(mod_name)


def _write_junit(repo: pathlib.Path, fqcn: str, *, tests: int, failures: int = 0,
                 errors: int = 0, skipped: int = 0, module: str = "app") -> None:
    d = repo / module / "build" / "test-results" / "test"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"TEST-{fqcn}.xml").write_text(
        f'<testsuite name="{fqcn}" tests="{tests}" skipped="{skipped}" '
        f'failures="{failures}" errors="{errors}"></testsuite>', encoding="utf-8")


CLS = "OrderQuantityCapAcceptanceTest"
FQCN = f"com.jypark.tps1000.order.{CLS}"


# --- parse_acceptance_results: 결정론적 무결성 축 (거짓 통과 / flaky 오염 차단) ---

def test_acceptance_parse_pass(tmp_path):
    rt = _load("repeat_task")
    _write_junit(tmp_path, FQCN, tests=4, failures=0)
    acc = rt.parse_acceptance_results(tmp_path, CLS)
    assert acc["executed"] and acc["pass"]
    assert acc["tests"] == 4 and acc["failures"] == 0


def test_acceptance_parse_failure(tmp_path):
    rt = _load("repeat_task")
    _write_junit(tmp_path, FQCN, tests=4, failures=1)
    acc = rt.parse_acceptance_results(tmp_path, CLS)
    assert acc["executed"] and not acc["pass"]   # 실행됐고 실패 → 진짜 red


def test_acceptance_parse_error_counts_as_fail(tmp_path):
    rt = _load("repeat_task")
    _write_junit(tmp_path, FQCN, tests=4, errors=1)
    acc = rt.parse_acceptance_results(tmp_path, CLS)
    assert not acc["pass"]


def test_acceptance_parse_missing_is_not_executed(tmp_path):
    # XML이 아예 없음(컴파일 제외·패키지 오류 등) → 미실행 → pass 아님(거짓 통과 차단)
    rt = _load("repeat_task")
    acc = rt.parse_acceptance_results(tmp_path, CLS)
    assert acc["executed"] is False and acc["pass"] is False
    assert acc["report_files"] == []


def test_acceptance_parse_all_skipped_is_not_executed(tmp_path):
    # 4개 전부 skipped → 실행된 게 0 → 미실행으로 본다(green 위장 방지)
    rt = _load("repeat_task")
    _write_junit(tmp_path, FQCN, tests=4, skipped=4)
    acc = rt.parse_acceptance_results(tmp_path, CLS)
    assert acc["executed"] is False and acc["pass"] is False


# --- collect_feature_metrics: outcome·신호 분류 ---

def _acc(executed=True, failures=0, errors=0, tests=4):
    return {"executed": executed, "tests": tests, "failures": failures,
            "errors": errors, "skipped": 0,
            "pass": executed and failures == 0 and errors == 0, "report_files": []}


def test_metrics_feature_ok(tmp_path):
    rt = _load("repeat_task")
    m = rt.collect_feature_metrics(tmp_path, 10.0, "", "", regression_pass=True,
                                   completed=True, acc=_acc(), full_suite_pass=True)
    assert m["outcome"] == "regression_pass"
    assert m["acceptance_pass"] and m["feature_ok"]
    assert m["regress_green_accept_red"] is False
    assert m["acceptance_executed"] is True


def test_metrics_misimplementation_signal(tmp_path):
    # 회귀 green인데 인수가 실행됐고 red → 핵심 신호(오구현) True
    rt = _load("repeat_task")
    m = rt.collect_feature_metrics(tmp_path, 10.0, "", "", regression_pass=True,
                                   completed=True, acc=_acc(failures=1), full_suite_pass=False)
    assert m["regress_green_accept_red"] is True
    assert m["feature_ok"] is False


def test_metrics_not_executed_is_not_misimplementation(tmp_path):
    # 인수 미실행은 측정 무효지 모델 오구현이 아니다 → 오구현 신호 False
    rt = _load("repeat_task")
    m = rt.collect_feature_metrics(tmp_path, 10.0, "", "", regression_pass=True,
                                   completed=True, acc=_acc(executed=False, tests=0),
                                   full_suite_pass=True)
    assert m["acceptance_executed"] is False
    assert m["regress_green_accept_red"] is False
    assert m["feature_ok"] is False


def test_metrics_harness_abort_skips_acceptance(tmp_path):
    rt = _load("repeat_task")
    m = rt.collect_feature_metrics(tmp_path, 10.0, "", "", regression_pass=False,
                                   completed=False, acc=None, full_suite_pass=None)
    assert m["outcome"] == "harness_abort"
    assert m["acceptance_pass"] is None and m["acceptance_executed"] is None
    assert m["feature_ok"] is False and m["regress_green_accept_red"] is False


def test_metrics_model_wrote_tests_counts_test_paths(tmp_path):
    # 신규(??)·변경(M) 모두, src/test/ 경로만, 백슬래시 정규화 후 카운트
    rt = _load("repeat_task")
    status = ("?? app/src/test/java/com/x/FooTest.java\n"
              " M order/src/test/java/com/x/BarTest.java\n"
              " M order/src/main/java/com/x/Prod.java\n"           # main → 제외
              " M app\\src\\test\\java\\com\\x\\BazTest.java\n")    # 백슬래시 → 포함
    m = rt.collect_feature_metrics(tmp_path, 1.0, "", status, regression_pass=True,
                                   completed=True, acc=_acc(), full_suite_pass=True)
    assert m["model_wrote_tests"] == 3


def test_metrics_diff_counts(tmp_path):
    rt = _load("repeat_task")
    diff = "+++ b/x\n+added line\n+another\n--- a/x\n-removed line\n"
    m = rt.collect_feature_metrics(tmp_path, 1.0, diff, "", regression_pass=True,
                                   completed=True, acc=_acc(), full_suite_pass=True)
    assert m["diff_added"] == 2 and m["diff_removed"] == 1   # +++/--- 헤더 제외


# --- tally_feature: 집계가 중단/인프라/미실행을 조용히 삼키지 않는가 ---

def test_tally_feature_mixed():
    rt = _load("repeat_task")
    results = [
        {"outcome": "regression_pass", "regression_pass": True, "acceptance_pass": True,
         "feature_ok": True, "regress_green_accept_red": False, "acceptance_executed": True,
         "model_wrote_tests": 1, "dev_loops": 1},
        {"outcome": "regression_pass", "regression_pass": True, "acceptance_pass": False,
         "feature_ok": False, "regress_green_accept_red": True, "acceptance_executed": True,
         "model_wrote_tests": 0, "dev_loops": 2},
        {"outcome": "regression_pass", "regression_pass": True, "acceptance_pass": None,
         "feature_ok": False, "regress_green_accept_red": False, "acceptance_executed": False,
         "model_wrote_tests": 0, "dev_loops": 1},
        {"run_dir": "-", "outcome": "harness_abort"},
        {"run_dir": "-", "outcome": "infra_fail"},
    ]
    t = rt.tally_feature(results)
    assert t["n"] == 5
    assert t["regression_pass"] == 3
    assert t["acceptance_pass"] == 1
    assert t["feature_ok"] == 1
    assert t["regress_green_accept_red"] == 1     # 진짜 오구현 1건
    assert t["acceptance_not_executed"] == 1      # 측정 무효 1건 (별도 집계)
    assert t["harness_abort"] == 1 and t["infra_fail"] == 1
    assert t["avg_model_tests"] is not None       # 완주 런만 평균


def test_tally_feature_all_aborted_has_no_crash():
    rt = _load("repeat_task")
    t = rt.tally_feature([{"outcome": "harness_abort"}, {"outcome": "infra_fail"}])
    assert t["avg_model_tests"] is None and t["avg_dev_loops"] is None


# --- structural_guard: L2 축 (지금까지 테스트 없었음) ---

def test_dedup_guard_detects_extraction(tmp_path):
    sg = _load("evals.structural_guard")
    f = tmp_path / "Cache.java"
    f.write_text("X\nX\nX\n", encoding="utf-8")   # 인라인 3회
    g = sg.DedupGuard(file="Cache.java", pattern="X", max_after=1)
    before = g.measure(tmp_path)
    f.write_text("X\n", encoding="utf-8")          # 헬퍼 1곳으로 수렴
    after = g.measure(tmp_path)
    v = g.verdict(before, after)
    assert before == 3 and after == 1
    assert v["structure_changed"] is True and v["no_op"] is False


def test_dedup_guard_flags_noop(tmp_path):
    sg = _load("evals.structural_guard")
    f = tmp_path / "Cache.java"
    f.write_text("X\nX\n", encoding="utf-8")
    g = sg.DedupGuard(file="Cache.java", pattern="X")
    before = g.measure(tmp_path)
    after = g.measure(tmp_path)                    # 그대로 — 추출 안 일어남
    v = g.verdict(before, after)
    assert v["structure_changed"] is False and v["no_op"] is True


def test_dedup_guard_missing_file_returns_sentinel(tmp_path):
    sg = _load("evals.structural_guard")
    g = sg.DedupGuard(file="nope.java", pattern="X")
    assert g.measure(tmp_path) == -1


def test_build_guard_constructs_from_spec():
    sg = _load("evals.structural_guard")
    g = sg.build_guard({"kind": "dedup", "file": "F.java", "pattern": "P", "max_after": 2})
    assert isinstance(g, sg.DedupGuard) and g.file == "F.java" and g.max_after == 2
