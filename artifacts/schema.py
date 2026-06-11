"""산출물 스키마 + 저장소 — 모델 간 핸드오프 인터페이스 (설계 문서 4절).

모델은 채팅하지 않는다. spec / diff / test report를 읽고 쓴다.
스키마가 곧 인터페이스 계약이므로 자유 텍스트 필드를 최소화한다.
"""
from datetime import datetime
from pathlib import Path

from pydantic import BaseModel


class Spec(BaseModel):
    """기획 단계 산출물. GPT 생성 ↔ Claude 비평을 거쳐 확정된다."""
    task: str                 # 원래 요청
    summary: str              # 무엇을 만들 것인가 (한 단락)
    requirements: list[str]   # 검증 가능한 요구사항 목록
    out_of_scope: list[str]   # 명시적 비목표 — 범위 폭발 방지
    revision: int = 1         # 비평 반영 횟수

    def to_markdown(self) -> str:
        reqs = "\n".join(f"- {r}" for r in self.requirements)
        oos = "\n".join(f"- {o}" for o in self.out_of_scope) or "- (없음)"
        return (f"# Spec (rev {self.revision})\n\n## Task\n{self.task}\n\n"
                f"## Summary\n{self.summary}\n\n## Requirements\n{reqs}\n\n"
                f"## Out of scope\n{oos}\n")


class DiffArtifact(BaseModel):
    """개발 단계 산출물. 구현 후 target repo의 git diff 스냅샷."""
    diff_text: str
    files_changed: list[str]
    implement_notes: str = ""  # 구현 모델이 남기는 리뷰어용 요약


class ReviewVerdict(BaseModel):
    """diff 리뷰 결과 — 구조화된 판정 (자유 채팅 금지)."""
    approved: bool
    comments: list[str] = []


class TestReport(BaseModel):
    """검증 게이트 산출물 — 결정론적 구간. 통과/실패가 ground truth."""
    __test__ = False  # 이름이 Test*라 pytest가 수집하려는 것 방지
    passed: bool
    exit_code: int
    command: str
    log_tail: str             # 실패 시 다음 개발 루프의 입력이 된다
    ran_at: str = ""


class ArtifactStore:
    """run 단위 파일 저장소. 산출물은 전부 디스크에 남아 run을 재구성할 수 있다."""

    def __init__(self, runs_dir: Path, run_id: str | None = None):
        self.run_dir = runs_dir / (run_id or datetime.now().strftime("%Y%m%d-%H%M%S"))
        self.run_dir.mkdir(parents=True, exist_ok=True)

    def save(self, name: str, content: str) -> Path:
        path = self.run_dir / name
        path.write_text(content, encoding="utf-8")
        return path

    def save_model(self, name: str, model: BaseModel) -> Path:
        return self.save(name, model.model_dump_json(indent=2))

    def read(self, name: str) -> str:
        return (self.run_dir / name).read_text(encoding="utf-8")
