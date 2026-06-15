"""재시도/백오프 단위 테스트 — 실시간 대기·난수를 주입해 결정론적으로 검증한다."""
import asyncio

import pytest

from adapters.retry import Transient, with_retry


class Clock:
    """sleep 이음매 — 실제로 자지 않고 대기 시간만 기록한다."""
    def __init__(self):
        self.waits = []

    async def __call__(self, seconds):
        self.waits.append(seconds)


def run(thunk, **kw):
    clock = Clock()
    # rand=0 → 지터 제거, 백오프가 base·2^n으로 결정론적
    result = asyncio.run(with_retry(thunk, sleep=clock, rand=lambda: 0.0,
                                    on_retry=lambda *a: None, **kw))
    return result, clock


def test_succeeds_first_try_no_sleep():
    async def thunk():
        return "ok"
    result, clock = run(thunk, attempts=4)
    assert result == "ok"
    assert clock.waits == []          # 성공이면 백오프 없음


def test_retries_transient_then_succeeds():
    calls = {"n": 0}

    async def thunk():
        calls["n"] += 1
        if calls["n"] < 3:
            raise Transient("일시")
        return "ok"

    result, clock = run(thunk, attempts=4, base_delay=2.0)
    assert result == "ok"
    assert calls["n"] == 3
    assert clock.waits == [2.0, 4.0]  # 1·2회차 실패 후 백오프, 3회차 성공


def test_gives_up_after_attempts_reraises_transient():
    calls = {"n": 0}

    async def thunk():
        calls["n"] += 1
        raise Transient("계속 실패")

    clock = Clock()
    with pytest.raises(Transient):
        asyncio.run(with_retry(thunk, attempts=3, sleep=clock,
                               rand=lambda: 0.0, on_retry=lambda *a: None))
    assert calls["n"] == 3            # 정확히 attempts회 시도
    assert clock.waits == [2.0, 4.0]  # 마지막 시도 뒤엔 대기 없음


def test_permanent_error_not_retried():
    calls = {"n": 0}

    async def thunk():
        calls["n"] += 1
        raise ValueError("영구 오류")   # Transient가 아니면 즉시 전파

    clock = Clock()
    with pytest.raises(ValueError):
        asyncio.run(with_retry(thunk, attempts=4, sleep=clock,
                               rand=lambda: 0.0, on_retry=lambda *a: None))
    assert calls["n"] == 1            # 재시도 없음
    assert clock.waits == []


def test_backoff_caps_at_max_delay():
    async def thunk():
        raise Transient("일시")

    clock = Clock()
    with pytest.raises(Transient):
        asyncio.run(with_retry(thunk, attempts=6, base_delay=10.0, max_delay=30.0,
                               sleep=clock, rand=lambda: 0.0, on_retry=lambda *a: None))
    # 10, 20, 30(40→cap), 30, 30 — max_delay에서 평탄화
    assert clock.waits == [10.0, 20.0, 30.0, 30.0, 30.0]


def test_jitter_widens_wait_within_band():
    async def thunk():
        raise Transient("일시")

    clock = Clock()
    with pytest.raises(Transient):
        asyncio.run(with_retry(thunk, attempts=2, base_delay=2.0,
                               sleep=clock, rand=lambda: 1.0, on_retry=lambda *a: None))
    assert clock.waits == [3.0]       # 2.0 * (1 + 0.5*1.0) = 3.0
