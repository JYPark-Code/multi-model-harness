"""L2+ 태스크 반복 드라이버 — repeat_l1.py의 일반화 (결함 주입 → 리팩터링).

repeat_l1과 다른 점:
  - 입력이 patch가 아니라 spec(태스크 문자열)이다. 결함 주입 없음 — clean msa에서 출발한다.
  - 게이트(25 green)는 '동작 보존'만 증명한다. 모델이 no-op이어도 통과한다. 그래서
    evals/structural_guard.py로 '리팩터링이 실제로 일어났는가'를 결정론적으로 따로 판정한다.

L1 결함 매트릭스(E0~E2)는 기존 repeat_l1.py로 동결 보존한다 — 이 드라이버는 L2부터.
제네릭 프리미티브(sh / reset_infra)는 repeat_l1에서 그대로 재사용한다(원본 미변경).

실행: python scripts/repeat_task.py --tasks L2-2 --repeat 5 --reset-infra
게이트는 cleanTest로 (--reset-infra와 한 쌍): HARNESS_TEST_COMMAND='.\\gradlew.bat cleanTest test --console=plain'
"""
import argparse
import json
import shutil
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path

HARNESS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HARNESS))
sys.path.insert(0, str(Path(__file__).resolve().parent))  # repeat_l1 동일 디렉터리 import

from config import HarnessConfig            # noqa: E402  (.env 로드 포함)
from evals.gate import run_gate             # noqa: E402  (인수 게이트 = 코어와 동일한 게이트 재사용)
from evals.structural_guard import build_guard  # noqa: E402
from repeat_l1 import reset_infra, sh        # noqa: E402  (제네릭 프리미티브 재사용)

# 리팩터링 태스크: 주입 패치가 없고, spec(태스크 문자열) + 구조 가드로 정의된다.
TASKS = {
    "L2-2": {
        "kind": "refactor",
        "spec": "tasks/level2/L2-2.spec.md",   # 하네스에 줄 태스크(파일 내용 그대로)
        "guard": {                              # 결정론적 구조 단언
            "kind": "dedup",
            "file": "product/src/main/java/com/jypark/tps1000/product/cache/ProductCacheLayer.java",
            "pattern": r"L2_KEY_PREFIX",
            "max_after": 1,
        },
    },
    # 신규 기능 개발 측정. 회귀(동작 보존)와 분리해 '진짜 ground truth'는 숨긴 인수 테스트로
    # 잡는다. 핵심 불변: 모델은 자기 성공을 채점할 테스트를 작성하거나 보지 못한다 —
    # acceptance_src는 모델 종료 '후'에만 주입되고, 그 결과는 모델에게 피드백되지 않는다.
    "L3-quantity-cap": {
        "kind": "feature",
        "feature": "tasks/level3/quantity-cap.feature.md",       # 하네스 입력(모델용 요구사항)
        "acceptance_src": "tasks/level3/quantity-cap.acceptance.java",  # 숨긴 ground truth
        # 기존 OrderAsyncApiTest와 같은 소스셋에 주입한다.
        "acceptance_dest": "app/src/test/java/com/jypark/tps1000/order/"
                           "OrderQuantityCapAcceptanceTest.java",
        "acceptance_filter": "OrderQuantityCapAcceptanceTest",   # --acceptance-isolate용 클래스명
    },
    # 상태 기반 취소 — CREATED만 취소 가능(200), COMPLETED/FAILED/이미취소는 409, 미존재 404.
    # 409 의미론은 naive 구현이 500/400/200으로 틀리기 쉬워 '오구현' 변별력이 높다.
    "L3-order-cancel": {
        "kind": "feature",
        "feature": "tasks/level3/order-cancel.feature.md",
        "acceptance_src": "tasks/level3/order-cancel.acceptance.java",
        "acceptance_dest": "app/src/test/java/com/jypark/tps1000/order/"
                           "OrderCancelAcceptanceTest.java",
        "acceptance_filter": "OrderCancelAcceptanceTest",
    },
}


def collect_metrics(run_dir: Path, exit_code: int, wall_s: float, diff: str,
                    guard_verdict: dict) -> dict:
    count = lambda pat: len(list(run_dir.glob(pat)))  # noqa: E731
    # final_report.json 유무로 '하네스 중단'과 '게이트 판정'을 가른다(repeat_l1과 동일 원칙).
    final = run_dir / "final_report.json"
    completed = final.exists()
    passed = json.loads(final.read_text(encoding="utf-8"))["passed"] if completed else False
    outcome = "pass" if passed else "gate_fail" if completed else "harness_abort"
    added = [l for l in diff.splitlines() if l.startswith("+") and not l.startswith("+++")]
    removed = [l for l in diff.splitlines() if l.startswith("-") and not l.startswith("---")]
    sc = guard_verdict["structure_changed"]
    return {
        "run_dir": run_dir.name,
        "outcome": outcome,                 # pass(동작보존) | gate_fail | harness_abort
        "gate_passed": passed,
        "exit_code": exit_code,
        "wall_s": round(wall_s, 1),
        "spec_revs": count("spec_rev*.md"),
        "critiques": count("critique_rev*.md"),
        "review_turns": count("review_turn*.md") + 1,   # GPT REQUEST_CHANGES 수 + 최종 1턴
        "dev_loops": count("test_report_loop*.json"),
        "diff_added": len(added),
        "diff_removed": len(removed),
        # 리팩터링 전용: 동작(게이트)과 별개로 추출이 실제로 일어났나
        "structure_changed": sc,
        "no_op": guard_verdict["no_op"],    # green인데 구조 그대로 = 거짓 성공
        "guard": guard_verdict,
        # 진짜 성공 = 동작 보존 AND 구조 변경 (둘 다여야 리팩터링 성공)
        "refactor_ok": outcome == "pass" and sc,
    }


def run_once(i: int, task_id: str, task: dict, repo: Path, runs_dir: Path) -> dict:
    guard = build_guard(task["guard"])
    if sh(["git", "-C", str(repo), "status", "--porcelain"]).strip():
        raise RuntimeError("testbed 작업 트리가 clean하지 않다 — 중단")
    sh(["git", "-C", str(repo), "switch", "-c", "harness-run", "msa"])
    try:
        before = guard.measure(repo)        # baseline(clean msa) 인라인 카운트 — 추출 전
        task_text = (HARNESS / task["spec"]).read_text(encoding="utf-8")

        t0 = time.monotonic()
        r = subprocess.run([sys.executable, "-m", "orchestrator.runner", task_text],
                           cwd=HARNESS, capture_output=True, text=True, encoding="utf-8")
        wall = time.monotonic() - t0

        run_dir = max((p for p in runs_dir.iterdir() if p.is_dir()), key=lambda p: p.name)
        diff = sh(["git", "-C", str(repo), "diff"])
        (run_dir / "fix.patch").write_text(diff, encoding="utf-8")  # 리셋 전 보존
        after = guard.measure(repo)         # 변경 후 카운트 — 추출 후
        m = collect_metrics(run_dir, r.returncode, wall, diff, guard.verdict(before, after))
        if m["outcome"] == "harness_abort":
            (run_dir / "crash.log").write_text(r.stdout + "\n" + r.stderr, encoding="utf-8")
        return m
    finally:
        sh(["git", "-C", str(repo), "checkout", "--", "."])
        sh(["git", "-C", str(repo), "clean", "-fd"])
        sh(["git", "-C", str(repo), "switch", "msa"])
        sh(["git", "-C", str(repo), "branch", "-D", "harness-run"])


def parse_acceptance_results(repo: Path, class_simple_name: str) -> dict:
    """gradle JUnit XML에서 인수 클래스의 결과'만' 추출한다 (L3의 결정론적 무결성 축).

    전체 스위트 exit code에 acceptance_pass를 기대면 두 가지가 위조 가능해진다:
      1) 인수 테스트가 실제로 실행 안 됐는데(패키지 오류·필터·컴파일 제외) 스위트는 green
         → 거짓 통과. structural_guard가 L2의 no-op을 잡듯, 여기선 '인수가 실행됐나'를 본다.
      2) 무관한 다른 테스트가 flaky로 red → acceptance_pass=False로 오염
         → '회귀G·인수R'(오구현) 신호가 거짓 양성이 된다.
    그래서 인수 클래스의 JUnit XML(cleanTest가 매 게이트마다 새로 씀)만 보고 판정한다.
    """
    matches = list(repo.glob(f"**/build/test-results/test/TEST-*{class_simple_name}.xml"))
    agg = {"executed": False, "tests": 0, "failures": 0, "errors": 0, "skipped": 0,
           "report_files": [str(p.relative_to(repo)).replace("\\", "/") for p in matches]}
    for m in matches:
        root = ET.fromstring(m.read_text(encoding="utf-8"))
        for k in ("tests", "failures", "errors", "skipped"):
            agg[k] += int(root.get(k, "0"))
    agg["executed"] = (agg["tests"] - agg["skipped"]) > 0   # 실제로 1개 이상 돌았나
    agg["pass"] = agg["executed"] and agg["failures"] == 0 and agg["errors"] == 0
    return agg


def collect_feature_metrics(run_dir: Path, wall_s: float, diff: str, status: str,
                            regression_pass: bool, completed: bool,
                            acc: dict | None, full_suite_pass: bool | None) -> dict:
    count = lambda pat: len(list(run_dir.glob(pat)))  # noqa: E731
    added = [l for l in diff.splitlines() if l.startswith("+") and not l.startswith("+++")]
    removed = [l for l in diff.splitlines() if l.startswith("-") and not l.startswith("---")]
    # 모델이 자기 테스트를 얼마나 썼나 — 게이트 아님(별도 측정). status는 인수 테스트 주입 '전'에
    # 찍은 것이라 숨긴 인수 파일은 포함되지 않는다. 신규(??)·변경(M) 둘 다 센다.
    model_wrote_tests = sum(1 for l in status.splitlines()
                            if l.strip() and "src/test/" in l.replace("\\", "/"))
    # outcome: 회귀 게이트가 끝까지 돌았나(완주) 기준. harness_abort면 인수 게이트는 건너뛴다.
    outcome = ("regression_pass" if regression_pass else "gate_fail") if completed else "harness_abort"
    # 인수 판정은 클래스 XML에서만(전체 exit code 아님) — 거짓 통과·flaky 오염 차단.
    acceptance_executed = acc["executed"] if acc else None
    acceptance_pass = acc["pass"] if acc else None
    # 핵심 신호: 회귀 green인데 인수 red = "동작은 안 깨뜨렸지만 기능을 틀리게 구현".
    # 단, 인수가 '실제로 실행됐을 때만' 유효 — 미실행은 측정 무결성 문제지 모델 오구현이 아니다.
    regress_green_accept_red = bool(regression_pass and acceptance_executed and not acceptance_pass)
    return {
        "run_dir": run_dir.name,
        "outcome": outcome,                 # regression_pass | gate_fail | harness_abort
        "regression_pass": regression_pass,  # 3번: 25 회귀 green (모델이 반복한 게이트)
        "acceptance_pass": acceptance_pass,  # 5번: 인수 클래스 XML 기준 ← 진짜 ground truth (중단=None)
        # 인수가 실제로 실행됐나 — False면 거짓 통과 위험(측정 무효), None은 게이트 미도달(중단)
        "acceptance_executed": acceptance_executed,
        "acceptance_tests": acc["tests"] if acc else None,
        "acceptance_failures": (acc["failures"] + acc["errors"]) if acc else None,
        # 전체 스위트 결과(인수 주입 후 재실행) — 회귀 재확인용 별도 기록
        "full_suite_pass": full_suite_pass,
        "feature_ok": bool(regression_pass and acceptance_pass),
        "regress_green_accept_red": regress_green_accept_red,
        "model_wrote_tests": model_wrote_tests,
        "wall_s": round(wall_s, 1),
        "spec_revs": count("spec_rev*.md"),
        "critiques": count("critique_rev*.md"),
        "review_turns": count("review_turn*.md") + 1,
        "dev_loops": count("test_report_loop*.json"),
        "diff_added": len(added),
        "diff_removed": len(removed),
    }


def run_once_feature(i: int, task_id: str, task: dict, cfg: HarnessConfig,
                     isolate: bool) -> dict:
    repo, runs_dir = cfg.target_repo, cfg.runs_dir
    if sh(["git", "-C", str(repo), "status", "--porcelain"]).strip():
        raise RuntimeError("testbed 작업 트리가 clean하지 않다 — 중단")
    sh(["git", "-C", str(repo), "switch", "-c", "harness-run", "msa"])
    try:
        # 1) 요구사항(feature.md)만 태스크로 — 이 시점에 인수 테스트는 repo에 없다.
        task_text = (HARNESS / task["feature"]).read_text(encoding="utf-8")
        t0 = time.monotonic()
        r = subprocess.run([sys.executable, "-m", "orchestrator.runner", task_text],
                           cwd=HARNESS, capture_output=True, text=True, encoding="utf-8")
        wall = time.monotonic() - t0

        run_dir = max((p for p in runs_dir.iterdir() if p.is_dir()), key=lambda p: p.name)
        # 모델 산출물은 인수 테스트 주입 '전'에 확정한다(diff·자기테스트 수가 오염되지 않게).
        diff = sh(["git", "-C", str(repo), "diff"])
        (run_dir / "fix.patch").write_text(diff, encoding="utf-8")        # 리셋 전 보존
        status = sh(["git", "-C", str(repo), "status", "--porcelain"])    # 신규 파일까지 포착(read-only)

        # 2) 회귀 판정 — final_report 유무로 중단/게이트를 가른다(기존과 동일 원칙).
        final = run_dir / "final_report.json"
        completed = final.exists()
        regression_pass = json.loads(final.read_text(encoding="utf-8"))["passed"] if completed else False
        if not completed:
            (run_dir / "crash.log").write_text(r.stdout + "\n" + r.stderr, encoding="utf-8")

        # 3) 인수 게이트 — 모델 종료 '후'에만 주입, 1회, 피드백 없음. 중단 런은 건너뛴다.
        acc, full_suite_pass = None, None
        if completed:
            dest = repo / task["acceptance_dest"]
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(HARNESS / task["acceptance_src"], dest)  # 숨긴 ground truth 주입
            cmd = cfg.test_command
            if isolate:  # 인수 클래스만 격리 실행(회귀는 위 2번에서 별도로 이미 확인됨)
                cmd = f'{cmd} --tests {task["acceptance_filter"]}'
            report = run_gate(cmd, repo, timeout=900)
            (run_dir / "acceptance_report.json").write_text(
                report.model_dump_json(indent=2), encoding="utf-8")
            full_suite_pass = report.passed
            # exit code가 아니라 인수 클래스의 JUnit XML로 판정 (clean -fd 전에 읽어야 한다)
            acc = parse_acceptance_results(repo, task["acceptance_filter"])
            (run_dir / "acceptance_results.json").write_text(
                json.dumps(acc, ensure_ascii=False, indent=2), encoding="utf-8")

        return collect_feature_metrics(run_dir, wall, diff, status,
                                       regression_pass, completed, acc, full_suite_pass)
    finally:
        # clean -fd가 모델 신규 파일 + 주입한 인수 테스트를 함께 제거한다.
        sh(["git", "-C", str(repo), "checkout", "--", "."])
        sh(["git", "-C", str(repo), "clean", "-fd"])
        sh(["git", "-C", str(repo), "switch", "msa"])
        sh(["git", "-C", str(repo), "branch", "-D", "harness-run"])


def tally_feature(results: list[dict]) -> dict:
    n = lambda pred: sum(1 for m in results if pred(m))  # noqa: E731
    done = [m for m in results if m.get("outcome") in ("regression_pass", "gate_fail")]
    return {
        "n": len(results),
        "regression_pass": n(lambda m: m.get("regression_pass")),
        "acceptance_pass": n(lambda m: m.get("acceptance_pass") is True),
        "feature_ok": n(lambda m: m.get("feature_ok")),
        # 회귀 green · 인수 red — 이 모드가 잡으려는 핵심 신호(오구현)
        "regress_green_accept_red": n(lambda m: m.get("regress_green_accept_red")),
        # 인수 게이트는 돌았는데 인수 테스트가 실제로 실행 안 됨 = 측정 무효(거짓 통과 위험)
        "acceptance_not_executed": n(lambda m: m.get("acceptance_executed") is False),
        "harness_abort": n(lambda m: m.get("outcome") == "harness_abort"),
        "infra_fail": n(lambda m: m.get("outcome") == "infra_fail"),
        "avg_model_tests": round(sum(m["model_wrote_tests"] for m in done) / len(done), 1)
                           if done else None,
        "avg_dev_loops": round(sum(m["dev_loops"] for m in done) / len(done), 1) if done else None,
    }


def tally_of(results: list[dict]) -> dict:
    n = lambda pred: sum(1 for m in results if pred(m))  # noqa: E731
    completed = n(lambda m: m.get("outcome") in ("pass", "gate_fail"))
    passes = n(lambda m: m.get("outcome") == "pass")
    return {
        "n": len(results),
        "pass": passes,
        "gate_fail": n(lambda m: m.get("outcome") == "gate_fail"),
        "harness_abort": n(lambda m: m.get("outcome") == "harness_abort"),
        "infra_fail": n(lambda m: m.get("outcome") == "infra_fail"),  # 인프라 실패로 시작도 못한 런
        "behavior_preserved": f"{passes}/{completed}" if completed else "0/0",
        "structure_changed": n(lambda m: m.get("structure_changed")),
        # green인데 구조 변경 없음 = no-op 거짓 성공 (L2의 핵심 지표)
        "no_op_pass": n(lambda m: m.get("outcome") == "pass" and not m.get("structure_changed")),
        "refactor_ok": n(lambda m: m.get("refactor_ok")),
    }


def run_task(task_id: str, repeat: int, cfg: HarnessConfig, reset: bool,
             isolate: bool = False) -> list[dict]:
    task = TASKS[task_id]
    kind = task["kind"]
    if kind not in ("refactor", "feature"):
        raise NotImplementedError(f"{task_id}: 이 드라이버는 refactor/feature 태스크만. 결함은 repeat_l1.py.")
    results = []
    for i in range(1, repeat + 1):
        print(f"[{task_id}] run {i}/{repeat} 시작", flush=True)
        try:
            if reset:
                reset_infra(cfg.target_repo)
        except Exception as e:  # docker 등 인프라 기동 실패 — 게이트에 도달조차 못함(≠게이트실패·≠중단)
            m = {"run_dir": "-", "outcome": "infra_fail", "error": str(e)[:200]}
            results.append(m)
            print(f"[{task_id}] run {i}: {m}", flush=True)
            continue
        try:
            if kind == "feature":
                m = run_once_feature(i, task_id, task, cfg, isolate)
            else:
                m = run_once(i, task_id, task, cfg.target_repo, cfg.runs_dir)
        except Exception as e:  # setup/git 등 하네스 자체 오류 = 중단(한 런 실패가 실험 전체를 죽이지 않게)
            m = {"run_dir": "-", "outcome": "harness_abort", "error": str(e)[:200]}
        results.append(m)
        marker = ("  (!) 회귀green·인수red" if m.get("regress_green_accept_red")
                  else "  (!) 인수 미실행(측정무효)" if m.get("acceptance_executed") is False
                  else "")
        print(f"[{task_id}] run {i}: {m}{marker}", flush=True)
    out = cfg.runs_dir / f"experiment-{task_id}.json"
    out.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    tally = tally_feature(results) if kind == "feature" else tally_of(results)
    print(f"[{task_id}] 저장: {out} · {tally}", flush=True)
    return results


def main() -> int:
    # Windows 콘솔 기본 코드페이지(cp949)는 '—'·'⚠' 등 비한글 글리프를 인코딩 못해 print에서
    # 크래시한다. 하네스는 산출물을 전부 utf-8로 쓰므로 stdout/stderr도 utf-8로 맞춘다.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass  # 재구성 불가한 스트림(예: 파이프 래퍼)이면 그냥 둔다
    parser = argparse.ArgumentParser()
    parser.add_argument("--tasks", default=",".join(TASKS), help="콤마 구분 태스크 id")
    parser.add_argument("--repeat", type=int, default=5)
    parser.add_argument("--reset-infra", action="store_true",
                        help="런 사이에 docker compose 볼륨을 비워 게이트 결정론 보장")
    parser.add_argument("--acceptance-isolate", action="store_true",
                        help="feature: 인수 게이트를 --tests <클래스>로 격리 실행 "
                             "(회귀는 모델 게이트에서 별도 확인됨). 기본은 전체 스위트 실행.")
    args = parser.parse_args()

    ids = [t.strip() for t in args.tasks.split(",") if t.strip()]
    unknown = [t for t in ids if t not in TASKS]
    if unknown:
        print(f"[task] 알 수 없는 태스크 id: {unknown} (가능: {list(TASKS)})", flush=True)
        return 2

    cfg = HarnessConfig()
    if args.reset_infra and "cleanTest" not in cfg.test_command:
        print("[task] 경고: --reset-infra인데 게이트에 cleanTest가 없다 — Gradle 캐시가 "
              "테스트를 스킵할 수 있다. HARNESS_TEST_COMMAND에 cleanTest를 넣어라.", flush=True)

    summary = {}
    for task_id in ids:
        print(f"\n[task] === {task_id} ({args.repeat}회) ===", flush=True)
        results = run_task(task_id, args.repeat, cfg, args.reset_infra, args.acceptance_isolate)
        kind = TASKS[task_id]["kind"]
        summary[task_id] = {"kind": kind,
                            **(tally_feature(results) if kind == "feature" else tally_of(results))}

    refactor = {t: s for t, s in summary.items() if s["kind"] == "refactor"}
    feature = {t: s for t, s in summary.items() if s["kind"] == "feature"}

    if refactor:
        out = cfg.runs_dir / "experiment-l2.json"
        out.write_text(json.dumps(refactor, ensure_ascii=False, indent=2), encoding="utf-8")
        print("\n[task] === L2 요약 ===", flush=True)
        print(f"{'태스크':7} {'동작보존':9} {'구조변경':9} {'no-op':6} {'성공':6} {'중단':5} {'인프라':6}", flush=True)
        for task_id, s in refactor.items():
            print(f"{task_id:7} {s['behavior_preserved']:9} {s['structure_changed']}/{s['n']:<6} "
                  f"{s['no_op_pass']}/{s['n']:<3} {s['refactor_ok']}/{s['n']:<3} "
                  f"{s['harness_abort']}/{s['n']:<3} {s['infra_fail']}/{s['n']:<4}", flush=True)
        print(f"[task] 요약 저장: {out}", flush=True)

    if feature:
        out = cfg.runs_dir / "experiment-l3.json"
        out.write_text(json.dumps(feature, ensure_ascii=False, indent=2), encoding="utf-8")
        print("\n[task] === L3 feature 요약 ===", flush=True)
        print(f"{'태스크':16} {'회귀통과':9} {'인수통과':9} {'feature_ok':11} "
              f"{'모델테스트':9} {'회귀G·인수R':11} {'중단':5} {'인프라':6}", flush=True)
        for task_id, s in feature.items():
            flag = ("  (!)오구현" if s["regress_green_accept_red"]
                    else "  (!)인수미실행" if s["acceptance_not_executed"] else "")
            print(f"{task_id:16} {s['regression_pass']}/{s['n']:<7} {s['acceptance_pass']}/{s['n']:<7} "
                  f"{s['feature_ok']}/{s['n']:<9} {str(s['avg_model_tests']):<9} "
                  f"{s['regress_green_accept_red']}/{s['n']:<9}{flag} "
                  f"{s['harness_abort']}/{s['n']:<3} {s['infra_fail']}/{s['n']:<4}", flush=True)
        print(f"[task] 요약 저장: {out}", flush=True)
        print("[task] (!)오구현 = 회귀 green인데 인수 red — 동작은 보존, 기능은 오구현(이 모드의 핵심 신호).",
              flush=True)
        if any(s["acceptance_not_executed"] for s in feature.values()):
            print("[task] (!)인수미실행 = 인수 테스트가 실제로 안 돌았다(거짓 통과 위험) — 패키지/필터/컴파일 점검.",
                  flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
