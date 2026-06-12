"""L1 반복 실험 드라이버 — 결함 주입 → 하네스 실행 → 리셋을 N회 반복하고 지표를 수집한다.

task-ladder.md 실행 프로토콜 구현: 일회용 harness-run 브랜치, 주입은 패치 적용으로
결정론적, 종료 후 diff는 runs/에만 보존하고 testbed는 리셋한다.

실행: python scripts/repeat_l1.py --patch tasks/level1/L1-5.patch --repeat 5
"""
import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

HARNESS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HARNESS))

from config import HarnessConfig  # noqa: E402  (.env 로드 포함)

L1_5_TASK = ("gradlew test가 실패한다. 실패 테스트: OrderAsyncApiTest "
             "'주문 생성(비동기): 202 + orderKey 즉시 반환 후 여유롭게 처리하면 "
             "COMPLETED로 조회된다'. 원인을 찾아 최소 변경으로 고쳐라.")


def sh(args: list[str], check: bool = True) -> str:
    r = subprocess.run(args, capture_output=True, text=True, encoding="utf-8")
    if check and r.returncode != 0:
        raise RuntimeError(f"{' '.join(args)} → {r.returncode}: {r.stderr[:300]}")
    return r.stdout


def collect_metrics(run_dir: Path, exit_code: int, wall_s: float, diff: str) -> dict:
    count = lambda pat: len(list(run_dir.glob(pat)))  # noqa: E731
    passed = False
    if (run_dir / "final_report.json").exists():
        passed = json.loads((run_dir / "final_report.json").read_text(encoding="utf-8"))["passed"]
    added = [l for l in diff.splitlines() if l.startswith("+") and not l.startswith("+++")]
    removed = [l for l in diff.splitlines() if l.startswith("-") and not l.startswith("---")]
    return {
        "run_dir": run_dir.name,
        "gate_passed": passed,
        "exit_code": exit_code,
        "wall_s": round(wall_s, 1),
        "spec_revs": count("spec_rev*.md"),          # 기획 왕복 (생성 횟수)
        "critiques": count("critique_rev*.md"),       # 비평 횟수 (AGREE 전)
        "review_turns": count("review_turn*.md") + 1,  # REQUEST_CHANGES 수 + 최종 1턴
        "dev_loops": count("test_report_loop*.json"),  # 게이트 실행 횟수
        "diff_added": len(added),
        "diff_removed": len(removed),
        # 정답 복원 여부 — L1-5의 ground truth는 order.complete() 한 줄
        "exact_fix": any("order.complete()" in l for l in added),
    }


def run_once(i: int, repo: Path, runs_dir: Path, patch: Path, task: str) -> dict:
    if sh(["git", "-C", str(repo), "status", "--porcelain"]).strip():
        raise RuntimeError("testbed 작업 트리가 clean하지 않다 — 중단")
    # msa를 명시적 분기 기준으로 — 현재 HEAD가 어디든(전용 클론이라도 사람이 만질 수 있다) 항상 같은 베이스
    sh(["git", "-C", str(repo), "switch", "-c", "harness-run", "msa"])
    try:
        sh(["git", "-C", str(repo), "apply", str(patch)])
        sh(["git", "-C", str(repo), "commit", "-am", f"tasks: L1-5 결함 주입 (run {i})"])

        t0 = time.monotonic()
        r = subprocess.run([sys.executable, "-m", "orchestrator.runner", task],
                           cwd=HARNESS, capture_output=True, text=True, encoding="utf-8")
        wall = time.monotonic() - t0

        run_dir = max(runs_dir.iterdir(), key=lambda p: p.name)
        diff = sh(["git", "-C", str(repo), "diff"])
        (run_dir / "fix.patch").write_text(diff, encoding="utf-8")  # 리셋 전 보존
        m = collect_metrics(run_dir, r.returncode, wall, diff)
        if r.returncode not in (0, 1):  # 0=게이트 통과, 1=게이트 실패, 그 외=하네스 크래시
            (run_dir / "crash.log").write_text(r.stdout + "\n" + r.stderr, encoding="utf-8")
        return m
    finally:
        sh(["git", "-C", str(repo), "checkout", "--", "."])
        sh(["git", "-C", str(repo), "clean", "-fd"])           # 모델이 만든 신규 파일 제거
        sh(["git", "-C", str(repo), "switch", "msa"])
        sh(["git", "-C", str(repo), "branch", "-D", "harness-run"])


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--patch", type=Path, default=HARNESS / "tasks/level1/L1-5.patch")
    parser.add_argument("--repeat", type=int, default=5)
    args = parser.parse_args()

    cfg = HarnessConfig()
    results = []
    for i in range(1, args.repeat + 1):
        print(f"[repeat] run {i}/{args.repeat} 시작", flush=True)
        try:
            m = run_once(i, cfg.target_repo, cfg.runs_dir, args.patch.resolve(), L1_5_TASK)
        except Exception as e:  # 한 런의 실패가 실험 전체를 죽이지 않게
            m = {"run_dir": "-", "gate_passed": False, "error": str(e)[:200]}
        results.append(m)
        print(f"[repeat] run {i}: {m}", flush=True)

    out = cfg.runs_dir / "experiment-L1-5.json"
    out.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[repeat] 결과 저장: {out}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
