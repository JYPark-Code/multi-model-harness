"""하네스 설정 — 모델명은 설정값일 뿐, 교체해도 오케스트레이터는 불변 (설계 문서 3절)."""
from dataclasses import dataclass, field
from pathlib import Path
import os

from dotenv import load_dotenv

# .env의 API 키·오버라이드를 모듈 임포트 시점에 환경 변수로 — 아래 os.environ.get과
# SDK 기본 생성자(ANTHROPIC_API_KEY/OPENAI_API_KEY 자동 탐색)보다 먼저 실행돼야 한다
load_dotenv(Path(__file__).parent / ".env")


@dataclass
class HarnessConfig:
    # 모델 역할 분리 (설계 문서 5절): 구현=Fable(장시간 자율), 리뷰·기획 보조=Opus(빠른 동기)
    implement_model: str = "claude-fable-5"
    review_model: str = "claude-opus-4-8"
    planning_model: str = "gpt-5.1"  # GPT 플래그십 — OpenAIClient 설정값

    # Claude 백엔드: "cli" = Claude Code 구독 인증(API 크레딧 불필요, 기본),
    #                "api" = ANTHROPIC_API_KEY 필요 (implement/review_model 사용)
    claude_backend: str = os.environ.get("HARNESS_CLAUDE_BACKEND", "cli")
    cli_model: str = os.environ.get("HARNESS_CLI_MODEL", "")  # 빈 값 = CLI 기본 모델

    # 티키타카가 안 새게 막는 방파제 (Constraint design)
    max_planning_turns: int = int(os.environ.get("HARNESS_MAX_PLANNING_TURNS", "4"))  # 생성↔비평 왕복 상한 (E3 ablation: 1=기획 1샷)
    max_review_turns: int = 3        # 구현↔리뷰 왕복 상한
    max_dev_loops: int = 2           # 테스트 실패 → 개발 루프백 상한
    max_tool_calls: int = 50         # tool loop 폭주 방지

    # 모델 호출 per-call 타임아웃(초) — degraded API가 한 런을 무한정 끌지 않게(E3 run2 ~32분 교훈).
    # SDK 클라이언트엔 생성자 timeout으로, CLI엔 프로세스 wait_for로 전달된다. env로 조정.
    planner_timeout_s: float = float(os.environ.get("HARNESS_PLANNER_TIMEOUT_S", "120"))
    critic_timeout_s: float = float(os.environ.get("HARNESS_CRITIC_TIMEOUT_S", "300"))
    implement_timeout_s: float = float(os.environ.get("HARNESS_IMPLEMENT_TIMEOUT_S", "900"))

    # 작업 대상 (첫 testbed = TPS repo)
    target_repo: Path = field(default_factory=lambda: Path(
        os.environ.get("HARNESS_TARGET_REPO", r"C:\project\event-driven-commerce")))
    # 검증 게이트 명령 — 결정론적이어야 한다 (통과/실패가 ground truth)
    test_command: str = os.environ.get(
        "HARNESS_TEST_COMMAND", r".\gradlew.bat test --console=plain")

    # 산출물 저장 루트 (run 단위 하위 디렉터리 생성)
    runs_dir: Path = field(default_factory=lambda: Path(__file__).parent / "runs")
