"""검증 게이트 — 결정론적 구간 (설계 문서 4절). 통과/실패가 ground truth.

모델의 자기 평가를 믿지 않는다. 대상 repo의 실제 테스트 명령을 돌리고
exit code로 판정하며, 실패 로그 꼬리가 다음 개발 루프의 입력이 된다.
"""
import subprocess
from datetime import datetime
from pathlib import Path

from artifacts.schema import TestReport

LOG_TAIL_CHARS = 4000


def run_gate(test_command: str, target_repo: Path, timeout: int = 900) -> TestReport:
    proc = subprocess.run(test_command, shell=True, cwd=str(target_repo),
                          capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=timeout)
    log = (proc.stdout or "") + (proc.stderr or "")
    return TestReport(passed=proc.returncode == 0, exit_code=proc.returncode,
                      command=test_command, log_tail=log[-LOG_TAIL_CHARS:],
                      ran_at=datetime.now().isoformat(timespec="seconds"))
