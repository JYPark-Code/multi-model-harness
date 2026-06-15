# 태스크 사다리 (Task Ladder) — testbed 평가 태스크 묶음

> 하네스로 만들 산출물은 코드가 아니라 **측정 결과**다. 이 문서는 그 측정의 시험지 —
> testbed(event-driven-commerce)에서 돌릴 태스크를 난이도 사다리로 정의한다.
> 모든 태스크는 "결정론적 게이트가 성공/실패를 판정할 수 있는가"를 기준으로 선별했다.

## 원칙

1. **게이트가 판정 못 하는 태스크는 싣지 않는다** (문서 생성 등 — 모델 자기 평가를 믿지 않는 게 설계 원칙).
2. **게이트 2~3회 안에 수렴할 크기**로 자른다. 통합 테스트 1회 ≈ 30초, 루프백 폭발은 비용 폭발이다.
3. **레벨 1부터 순서대로.** 현재 MVP 게이트는 "기존 테스트가 깨지지 않았다"만 검증한다 —
   "새 기능이 됐다"를 검증하려면 레벨 3~4에서 하네스 자체의 진화(모델이 테스트 작성 → 게이트가 실행)가 필요하다.

## 실행 프로토콜

- testbed는 **`harness-run` 일회용 브랜치**(msa에서 분기)에서 돌린다 — msa와 절대 안 섞인다.
- 시작 전 작업 트리 clean 확인, 종료 후 diff는 산출물(runs/)로만 보존하고 브랜치는 리셋.
- 게이트: `.\gradlew.bat test` (통합 테스트 25개, docker compose 인프라 선행).
- 같은 태스크를 조건별 N회 반복한다 — 모델 출력은 비결정적이므로 1회 결과는 측정이 아니다
  (TPS 프로젝트의 측정 원칙과 동일: 런 간 편차 밴드를 먼저 안다).

---

## 레벨 1 — 고장 수리 (red → green)

> 사람이 결함을 주입해 테스트를 깨뜨려 두고, 하네스에 "테스트가 실패한다. 고쳐라"를 준다.
> ground truth가 가장 선명(red→green)해서 **첫 실전 런은 반드시 여기서 시작**한다.
> 게이트 한계(기존 테스트만 검증)가 없는 유일한 레벨이기도 하다.

| ID | 주입할 결함 | 깨지는 테스트 | 난이도 포인트 |
|---|---|---|---|
| L1-1 | `OrderEventConsumer.consume()`의 배치 내 중복 제거(`processed.add` 검사) 제거 | OrderAsyncApiTest 멱등성 | 실패 로그에서 중복 저장을 읽어내야 함 |
| L1-2 | `ProductCacheLayer.evict()`에서 로컬 `l1.invalidate()` 호출 제거 | ProductCacheTest 무효화 검증 | 캐시 계층 간 상호작용 이해 필요 |
| L1-3 | `settlementItemProcessor`의 금액 계산 `price * qty`를 `price + qty`로 변조 | SettlementBatchTest 금액 집계 | 연산자 한 글자 — 집계 정확성, L1-5와 같은 최소 수렴 결 |
| L1-4 | `SecurityConfig`의 authenticationEntryPoint(401) 제거 | AuthRbacTest 401/403 분리 | 프레임워크 기본 동작(403 뭉개짐) 지식 필요 |
| L1-5 | `Order.complete()` 호출을 컨슈머에서 제거 (상태가 CREATED로 남음) | OrderAsyncApiTest 상태 검증 | diff가 한 줄 — 최소 변경 수렴 측정용 |

> L1-3 정정: 원안(clearStep 제외)은 **무효 주입**이었다 — 검증 결과 green. Settlement에
> `(month, product_id)` unique 제약이 있어 clearStep 없이 재집계해도 2차 INSERT가 제약 위반으로
> 청크 롤백되고, `jobLauncher.run`이 잡 실패를 throw하지 않아 엔드포인트는 200을 반환한다. 테스트는
> 재실행 시 HTTP 200·행 수만 보므로 멱등성이 unique 제약으로 이미 보장돼 clearStep이 redundant였다.
> 그래서 실제로 테스트를 깨는 금액 계산 결함으로 교체했다.

주입 자동화: 결함별 `git apply` 가능한 역패치를 `tasks/level1/*.patch`로 보관한다 (L1-1~L1-5 작성 완료).
**전 패치 검증됨**: clean msa에 `git apply` 가능 + 대상 테스트를 실제로 red로 만드는 것까지 확인
(인프라 리셋 후 `:app:test --tests <대상>` 실행, exit≠0 + 단언 실패 위치 확인).

## 레벨 2 — 동작 보존 리팩터링

> 기존 테스트 25개가 완벽한 회귀 게이트로 동작한다. spec의 requirements가
> "동작 불변 + 구조 개선"이므로 리뷰어(GPT)의 diff 리뷰가 실질 역할을 하는 첫 레벨.

| ID | 태스크 | 검증 |
|---|---|---|
| L2-1 | `OrderEventConsumer.consume()`을 기처리 필터/알림/적재 책임으로 메서드 분해 | 테스트 전체 green + diff 리뷰(동작 변화 없음) |
| L2-2 | `ProductCacheLayer`의 L2 키 조립(`L2_KEY_PREFIX + id`) 3곳 중복을 메서드로 추출 | 동일 |
| L2-3 | `SettlementService.validateMonth()`의 YearMonth 파싱을 재사용 가능한 검증 유틸로 추출 | 동일 |
| L2-4 | 매직 넘버 정리: 캐시 TTL·청크 크기 등 이미 상수인 것 외 잔여 리터럴 탐색·승격 | 동일 — "탐색"이 포함돼 read_file/list_dir 도구 사용량 측정에 좋음 |

## 레벨 3 — 기존 테스트 체계 안의 작은 기능

> 신규 동작이지만 기존 테스트가 회귀를 막아주고, 신규 검증은 spec requirements로 명시한다.
> ⚠️ 게이트가 신규 요구를 직접 판정하지 못하므로, 이 레벨부터 **검증 격차**가 생긴다 —
> 격차를 메우는 방식(사람이 신규 테스트를 미리 작성해 두는 hidden test 방식)을 먼저 정한다.

| ID | 태스크 | hidden test (사람이 사전 작성) |
|---|---|---|
| L3-1 | `GET /api/admin/settlements`에 정렬 옵션(`sort=amount`) 추가 | 정렬 결과 검증 테스트 |
| L3-2 | 비동기 주문 접수에 quantity 상한(1~100) 검증 — 위반 시 400 | 경계값 테스트 |
| L3-3 | 상품 목록 조회 `GET /api/products?page=&size=` (단건 캐시와 무관, DB 직행 명시) | 페이징 테스트 |

hidden test는 주입 결함의 역방향이다: 태스크 시작 시엔 빠져 있고, 게이트 직전에 추가해서 돌린다 —
모델이 테스트를 보고 베끼는 것을 차단한다(SWE-bench와 같은 구조).

## 레벨 4 — 테스트까지 작성하는 기능

> spec requirements → 모델이 테스트 작성 → 게이트가 신규 테스트 포함 실행.
> 하네스 진화 필요: 게이트가 "신규 테스트가 실제로 추가됐고 의미 있는가"를 확인해야 한다
> (테스트 0개 추가로 green을 만드는 편법 차단 — 예: 변경 파일 목록에 src/test 포함 강제).

| ID | 태스크 | 비고 |
|---|---|---|
| L4-1 | DLQ 메시지 조회 어드민 API (`GET /api/admin/dlq`) | testbed decisions.md 9번의 미구현 지점 — 실제로 필요한 기능 |
| L4-2 | 주문 취소 API (CREATED → FAILED 전환) + 정산 제외 검증 | 도메인 규칙(FAILED 제외)과의 정합 필요 |
| L4-3 | 상품 삭제 API + 캐시 무효화 + 정산 스킵 동작 | 캐시·복제·배치 3개 축을 모두 건드리는 최고 난도 |

---

## 실험 설계 (태스크 사다리 위에서 측정할 것)

같은 태스크를 조건만 바꿔 N회(≥5) 반복하고 아래 지표를 비교한다:

| 실험 | 조건 A | 조건 B | 답하려는 질문 |
|---|---|---|---|
| E1 역할 교차 | GPT 기획 + Claude 구현 | 역할 교환 / 단일 모델 솔로 | 크로스 벤더 협업이 실제로 나은가? |
| E2 비평 ablation | critic 왕복 있음 | 기획 1샷 | 기획 비평이 게이트 통과율을 올리나? |
| E3 피드백 효과 | 루프백 시 실패 로그 전달 | "실패했다"만 전달 | 실패 로그가 수렴을 얼마나 앞당기나? |

**기록 지표** (runs/에 자동 적재되도록 하네스에 추가할 것): 게이트 통과율, 첫 green까지 루프백 횟수,
리뷰 왕복 횟수, tool call 수, 토큰 사용량(어댑터에서 수집), wall clock.

결과는 [`docs/experiments.md`](experiments.md)에 표로 누적한다 — testbed의 benchmarks.md와 같은 위상의 산출물.
E0(L1-5 베이스라인): 완주 2/2 통과, 중단 3/5가 지배적 실패 모드 → 재시도/백오프 적용 후
E1 재측정에서 5/5 통과·중단 0/5·전 런 최소 수렴(diff ≤3줄)으로 회복.

## 하네스 측 선행 작업 (코드 생성 전 준비)

- [ ] 토큰·비용 카운터를 어댑터에 추가 (응답 usage 수집 → run 메타에 기록)
- [ ] run 요약 리포트 생성 (`runs/<id>/summary.md` — 단계별 턴 수·게이트 결과 자동 정리)
- [x] 어댑터 재시도/백오프 — `adapters/retry.py` (지수 백오프+지터, 일시/영구 오류 분류).
      OpenAI·Claude(API/CLI) 전 어댑터 적용 → E0의 harness_abort 60% 제거 대상
- [x] 레벨 1 결함 주입 패치 5종 완성 (`tasks/level1/L1-1~L1-5.patch`) — 전부 red 검증
- [x] testbed `harness-run` 브랜치 운용 스크립트 — `scripts/repeat_l1.py` (분기→실행→diff 보존→리셋·지표 수집)
