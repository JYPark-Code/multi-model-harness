"""구조 가드 — 리팩터링이 실제로 일어났는지 결정론적으로 판정한다 (L2+의 새 검증 축).

L1까지는 게이트(red→green)가 성공을 완전히 정의했다. L2부터는 게이트(기존 25개 통과)가
**동작 보존만** 증명한다 — 모델이 아무것도 안 해도(no-op) 25개는 green이다. 그래서
"작업이 실제로 일어났는가"를 게이트와 별개로 단언해야 한다.

이 모듈은 그 단언을 **텍스트/카운트 기반(결정론적)으로만** 한다 — 모델 자기평가를 믿지 않는
설계 원칙(design 4절)의 연장이다. GUARDS 레지스트리에 가드 종류를 더하면 L3/L4의
'work-happened' 게이트로 일반화된다 (예: 신규 테스트가 실제로 추가됐는가).
"""
import re
from dataclasses import dataclass
from pathlib import Path


@dataclass
class DedupGuard:
    """인라인 패턴 중복이 줄었는가 — 메서드 추출의 결정론적 서명.

    baseline(clean msa)의 등장 횟수와 변경 후 횟수를 비교한다. 추출이 일어나면
    인라인 등장은 줄고(N→1) 단일 헬퍼로 수렴한다. 헬퍼 시그니처를 추측하지 않고
    '중복이 실제로 줄었나'만 보므로 구현 방식에 무관하게 견고하다.
    """
    file: str            # target_repo 기준 상대 경로
    pattern: str         # 인라인 중복 패턴 (regex)
    max_after: int = 1   # 추출 후 허용 인라인 등장 상한 (헬퍼 내부 1곳)

    def measure(self, repo: Path) -> int:
        path = repo / self.file
        if not path.exists():
            return -1
        return len(re.findall(self.pattern, path.read_text(encoding="utf-8")))

    def verdict(self, before: int, after: int) -> dict:
        # 구조 변경 = 등장이 줄었고(중복 제거) 단일 지점으로 수렴했다
        structure_changed = before > 0 and after < before and after <= self.max_after
        return {
            "guard": "dedup",
            "file": self.file,
            "before": before,
            "after": after,
            "structure_changed": structure_changed,
            "no_op": after == before,     # 등장 그대로 = 추출 안 일어남 (위험: green인데 no-op)
        }


GUARDS = {"dedup": DedupGuard}


def build_guard(spec: dict):
    """태스크 레지스트리의 plain dict → 가드 인스턴스. {"kind": "dedup", ...}."""
    spec = dict(spec)
    return GUARDS[spec.pop("kind")](**spec)
