"""L1 반복 실험 드라이버 — 결함 주입 → 하네스 실행 → 리셋을 N회 반복하고 지표를 수집한다.

task-ladder.md 실행 프로토콜 구현: 일회용 harness-run 브랜치, 주입은 패치 적용으로
결정론적, 종료 후 diff는 runs/에만 보존하고 testbed는 리셋한다.

단일 결함:   python scripts/repeat_l1.py --defects L1-5 --repeat 5 --reset-infra
결함 매트릭스: python scripts/repeat_l1.py --repeat 5 --reset-infra   (기본 = 5종 전부)

게이트는 cleanTest로 강제 실행해야 한다(--reset-infra와 한 쌍):
  HARNESS_TEST_COMMAND='.\\gradlew.bat cleanTest test --console=plain'
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

# 결함별: 실패 테스트를 알리는 버그리포트(수정법은 노출 금지) + 정답 토큰(exact_fix 판정).
# task는 L1-5와 같은 결: "gradlew test 실패, 실패 테스트 X, 원인 찾아 최소 변경으로 고쳐라".
DEFECTS = {
    "L1-1": {
        "task": ("gradlew test가 실패한다. 실패 테스트: OrderAsyncApiTest "
                 "'같은 orderKey 이벤트가 두 번 와도 주문 1건, 후속 처리(알림)도 1회만 — 멱등성'. "
                 "원인을 찾아 최소 변경으로 고쳐라."),
        "ground_truth": "processed.add",          # 배치 내 중복 제거 검사 복원
    },
    "L1-2": {
        "task": ("gradlew test가 실패한다. 실패 테스트: ProductCacheTest "
                 "'수정하면 L1/L2가 모두 무효화되고, 다음 조회는 DB에서 새 값을 읽는다'. "
                 "원인을 찾아 최소 변경으로 고쳐라."),
        "ground_truth": "l1.invalidate(productId)",  # evict()의 동기 L1 무효화 복원
    },
    "L1-3": {
        "task": ("gradlew test가 실패한다. 실패 테스트: SettlementBatchTest "
                 "'정산 잡: 당월 주문을 상품별로 집계하고, 재실행해도 중복 없이 덮어쓴다'. "
                 "원인을 찾아 최소 변경으로 고쳐라."),
        "ground_truth": "* sales.totalQuantity()",   # 금액 계산 곱 복원 (+ → *)
    },
    "L1-4": {
        "task": ("gradlew test가 실패한다. 실패 테스트: AuthRbacTest "
                 "'관리자 API: 토큰 없으면 401'. 원인을 찾아 최소 변경으로 고쳐라."),
        "ground_truth": "authenticationEntryPoint",   # 401 entry point 복원
    },
    "L1-5": {
        "task": ("gradlew test가 실패한다. 실패 테스트: OrderAsyncApiTest "
                 "'주문 생성(비동기): 202 + orderKey 즉시 반환 후 여유롭게 처리하면 "
                 "COMPLETED로 조회된다'. 원인을 찾아 최소 변경으로 고쳐라."),
        "ground_truth": "order.complete()",
    },
}


def sh(args: list[str], check: bool = True, cwd: Path | None = None) -> str:
    r = subprocess.run(args, capture_output=True, text=True, encoding="utf-8",
                       cwd=str(cwd) if cwd else None)
    if check and r.returncode != 0:
        raise RuntimeError(f"{' '.join(args)} → {r.returncode}: {r.stderr[:300]}")
    return r.stdout


def reset_infra(repo: Path) -> None:
    """런 사이에 docker compose 볼륨을 비운다 — DB/Kafka 상태 누적이 게이트를 비결정적으로
    만든다(영속 볼륨에 주문·정산 행이 쌓이면 SettlementBatchTest가 거짓 red를 낸다).
    측정의 ground truth는 결정론적 게이트이므로, 매 런은 fresh 인프라에서 출발해야 한다."""
    sh(["docker", "compose", "down", "-v"], cwd=repo)
    sh(["docker", "compose", "up", "-d"], cwd=repo)
    for _ in range(20):  # mysql 헬스 대기 (최대 60s) — 게이트의 첫 DB 연결 실패 방지
        ping = subprocess.run(["docker", "exec", "tps-mysql", "mysqladmin",
                               "ping", "-h", "localhost", "--silent"],
                              capture_output=True)
        if ping.returncode == 0:
            return
        time.sleep(3)
    raise RuntimeError("mysql 헬스 대기 타임아웃 — 인프라 기동 실패")


def collect_metrics(run_dir: Path, exit_code: int, wall_s: float, diff: str,
                    ground_truth: str) -> dict:
    count = lambda pat: len(list(run_dir.glob(pat)))  # noqa: E731
    # final_report.json은 파이프라인이 끝까지 돈 경우에만 존재한다. 미처리 예외(API/인프라
    # 오류)는 Python을 종료 코드 1로 떨어뜨려 게이트 실패와 구분되지 않으므로, final_report의
    # 유무로 "하네스 중단"과 "게이트 판정"을 가른다 — 중단을 게이트 실패로 세면 통과율이 거짓이 된다.
    final = run_dir / "final_report.json"
    completed = final.exists()
    passed = json.loads(final.read_text(encoding="utf-8"))["passed"] if completed else False
    outcome = "pass" if passed else "gate_fail" if completed else "harness_abort"
    added = [l for l in diff.splitlines() if l.startswith("+") and not l.startswith("+++")]
    removed = [l for l in diff.splitlines() if l.startswith("-") and not l.startswith("---")]
    return {
        "run_dir": run_dir.name,
        "outcome": outcome,            # pass | gate_fail | harness_abort
        "gate_passed": passed,
        "exit_code": exit_code,
        "wall_s": round(wall_s, 1),
        "spec_revs": count("spec_rev*.md"),          # 기획 왕복 (생성 횟수)
        "critiques": count("critique_rev*.md"),       # 비평 횟수 (AGREE 전)
        "review_turns": count("review_turn*.md") + 1,  # REQUEST_CHANGES 수 + 최종 1턴
        "dev_loops": count("test_report_loop*.json"),  # 게이트 실행 횟수
        "diff_added": len(added),
        "diff_removed": len(removed),
        # 정답 복원 여부 — 결함별 ground truth 토큰이 모델 diff의 추가 라인에 있는가
        "exact_fix": any(ground_truth in l for l in added),
    }


def run_once(i: int, defect_id: str, repo: Path, runs_dir: Path,
             patch: Path, task: str, ground_truth: str) -> dict:
    if sh(["git", "-C", str(repo), "status", "--porcelain"]).strip():
        raise RuntimeError("testbed 작업 트리가 clean하지 않다 — 중단")
    # msa를 명시적 분기 기준으로 — 현재 HEAD가 어디든(전용 클론이라도 사람이 만질 수 있다) 항상 같은 베이스
    sh(["git", "-C", str(repo), "switch", "-c", "harness-run", "msa"])
    try:
        sh(["git", "-C", str(repo), "apply", str(patch)])
        sh(["git", "-C", str(repo), "commit", "-am", f"tasks: 결함 주입 ({defect_id} run {i})"])

        t0 = time.monotonic()
        r = subprocess.run([sys.executable, "-m", "orchestrator.runner", task],
                           cwd=HARNESS, capture_output=True, text=True, encoding="utf-8")
        wall = time.monotonic() - t0

        # 디렉터리만 후보로 — runs/에 섞인 파일(experiment json·로그)을 집지 않게 (이름순 'r'>'2')
        run_dir = max((p for p in runs_dir.iterdir() if p.is_dir()), key=lambda p: p.name)
        diff = sh(["git", "-C", str(repo), "diff"])
        (run_dir / "fix.patch").write_text(diff, encoding="utf-8")  # 리셋 전 보존
        m = collect_metrics(run_dir, r.returncode, wall, diff, ground_truth)
        if m["outcome"] == "harness_abort":  # final_report 없음 = 게이트 도달 전 크래시; 진단용 로그 보존
            (run_dir / "crash.log").write_text(r.stdout + "\n" + r.stderr, encoding="utf-8")
        return m
    finally:
        sh(["git", "-C", str(repo), "checkout", "--", "."])
        sh(["git", "-C", str(repo), "clean", "-fd"])           # 모델이 만든 신규 파일 제거
        sh(["git", "-C", str(repo), "switch", "msa"])
        sh(["git", "-C", str(repo), "branch", "-D", "harness-run"])


def tally_of(results: list[dict]) -> dict:
    t = {k: sum(1 for m in results if m.get("outcome") == k)
         for k in ("pass", "gate_fail", "harness_abort")}
    completed = t["pass"] + t["gate_fail"]
    done = [m for m in results if m.get("outcome") == "pass"]
    return {
        "n": len(results),
        "pass": t["pass"], "gate_fail": t["gate_fail"], "harness_abort": t["harness_abort"],
        "pass_rate": f"{t['pass']}/{completed}" if completed else "0/0",
        "exact_fix": sum(1 for m in results if m.get("exact_fix")),
        # 완주 런만 평균(중단은 부분 측정이라 제외)
        "avg_wall_s": round(sum(m["wall_s"] for m in done) / len(done), 1) if done else None,
        "avg_diff_added": round(sum(m["diff_added"] for m in done) / len(done), 1) if done else None,
    }


def run_defect(defect_id: str, repeat: int, cfg: HarnessConfig, reset: bool) -> list[dict]:
    d = DEFECTS[defect_id]
    patch = (HARNESS / "tasks/level1" / f"{defect_id}.patch").resolve()
    results = []
    for i in range(1, repeat + 1):
        print(f"[{defect_id}] run {i}/{repeat} 시작", flush=True)
        try:
            if reset:
                reset_infra(cfg.target_repo)
            m = run_once(i, defect_id, cfg.target_repo, cfg.runs_dir,
                         patch, d["task"], d["ground_truth"])
        except Exception as e:  # 한 런의 실패가 실험 전체를 죽이지 않게
            m = {"run_dir": "-", "gate_passed": False, "error": str(e)[:200]}
        results.append(m)
        print(f"[{defect_id}] run {i}: {m}", flush=True)
    out = cfg.runs_dir / f"experiment-{defect_id}.json"
    out.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[{defect_id}] 저장: {out} · {tally_of(results)}", flush=True)
    return results


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--defects", default=",".join(DEFECTS),
                        help="콤마 구분 결함 id (기본: 5종 전부)")
    parser.add_argument("--repeat", type=int, default=5)
    parser.add_argument("--reset-infra", action="store_true",
                        help="런 사이에 docker compose 볼륨을 비워 게이트 결정론 보장")
    args = parser.parse_args()

    ids = [d.strip() for d in args.defects.split(",") if d.strip()]
    unknown = [d for d in ids if d not in DEFECTS]
    if unknown:
        print(f"[matrix] 알 수 없는 결함 id: {unknown} (가능: {list(DEFECTS)})", flush=True)
        return 2

    cfg = HarnessConfig()
    # reset_infra ↔ 비캐시 게이트는 한 쌍이다: DB를 비워도 Gradle이 테스트를 up-to-date로
    # 스킵하면(수정이 msa와 동일할 때) 리셋이 무의미하고 거짓 green이 난다. cleanTest 강제 필요.
    if args.reset_infra and "cleanTest" not in cfg.test_command:
        print("[matrix] 경고: --reset-infra인데 게이트에 cleanTest가 없다 — Gradle 캐시가 "
              "테스트를 스킵할 수 있다. HARNESS_TEST_COMMAND에 cleanTest를 넣어라.", flush=True)

    summary = {}
    for defect_id in ids:
        print(f"\n[matrix] === {defect_id} ({args.repeat}회) ===", flush=True)
        results = run_defect(defect_id, args.repeat, cfg, args.reset_infra)
        summary[defect_id] = tally_of(results)

    out = cfg.runs_dir / "experiment-matrix.json"
    out.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print("\n[matrix] === 결함 유형별 요약 ===", flush=True)
    print(f"{'결함':6} {'통과':7} {'중단':5} {'정답':5} {'wall(s)':9} {'diff+':6}", flush=True)
    for defect_id, s in summary.items():
        print(f"{defect_id:6} {s['pass_rate']:7} {s['harness_abort']}/{s['n']:<3} "
              f"{s['exact_fix']}/{s['n']:<3} {str(s['avg_wall_s']):9} {str(s['avg_diff_added']):6}",
              flush=True)
    print(f"[matrix] 요약 저장: {out}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
