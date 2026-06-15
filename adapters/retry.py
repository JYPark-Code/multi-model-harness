"""모델 호출 재시도/백오프 — 일시적 오류로 인한 하네스 중단을 흡수한다.

E0 배치에서 중단(harness_abort)이 5회 중 3회로 지배적 실패 모드였다(docs/experiments.md).
전부 게이트 red가 아니라 모델 호출(critic·implementer) 단계의 일시적 외부 오류
(레이트리밋·5xx·네트워크·CLI 비정상 종료)였다. 영구 오류(400/401, 잘못된 요청)는
재시도해도 같은 결과이므로 즉시 전파한다 — 어댑터가 일시/영구를 분류해 일시만 Transient로 감싼다.
"""
import asyncio
import random
import sys


class Transient(Exception):
    """재시도 가치가 있는 일시적 실패. 어댑터가 벤더 예외를 이 타입으로 감싸 던진다.
    원본은 `raise Transient(...) from e`로 __cause__에 보존한다."""


def log_retry(attempt: int, exc: BaseException, wait: float) -> None:
    """기본 on_retry — 재시도를 run 로그에 남긴다(중단과 달리 회복됐음을 추적)."""
    print(f"[retry] {attempt}회차 일시 오류, {wait:.1f}s 후 재시도: {exc}",
          file=sys.stderr, flush=True)


async def with_retry(thunk, *, attempts: int = 4, base_delay: float = 2.0,
                     max_delay: float = 30.0, sleep=asyncio.sleep,
                     rand=random.random, on_retry=log_retry):
    """thunk()를 호출하고 Transient면 지수 백오프 + 지터로 재시도한다.

    Transient가 아닌 예외는 즉시 전파(영구 오류). 마지막 시도의 Transient도 전파한다.
    sleep/rand는 테스트에서 시계·난수를 주입하기 위한 이음매다(실시간 대기·비결정성 제거)."""
    if attempts < 1:
        raise ValueError("attempts는 1 이상이어야 한다")
    for attempt in range(1, attempts + 1):
        try:
            return await thunk()
        except Transient as exc:
            if attempt == attempts:
                raise
            # 지수 백오프(base·2^n)에 0~50% 지터 — 동시 재시도가 겹쳐 다시 레이트리밋되는 것 방지
            backoff = min(base_delay * 2 ** (attempt - 1), max_delay)
            wait = backoff * (1 + 0.5 * rand())
            if on_retry:
                on_retry(attempt, exc, wait)
            await sleep(wait)
