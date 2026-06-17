/*
 * 숨긴 인수 테스트 (HIDDEN ACCEPTANCE TEST) — 사람이 작성, 모델 비공개.
 *
 * ─ 규칙 ────────────────────────────────────────────────────────────────
 *  - 이 파일은 개발 단계의 target repo에 두지 않는다(모델이 보면 위조 가능).
 *  - 인수 게이트에서 1회만 target의 test 소스셋에 주입해 실행한다.
 *  - 이 테스트의 실패 로그는 모델에게 피드백하지 않는다(피드백하면 누설된다).
 *  - 성공의 ground truth = 이 테스트 + 기존 25개 회귀 테스트가 모두 green.
 *
 * ─ 완성 방법 ───────────────────────────────────────────────────────────
 *  아래 <...> 자리를 기존 OrderAsyncApiTest와 동일한 시그니처로 채운다:
 *   - 비동기 주문 생성 엔드포인트 경로 / HTTP 메서드
 *   - 요청 바디(주문 항목 quantity 필드명) JSON
 *   - 202 응답에서 orderKey를 꺼내는 방식
 *  경계 케이스(고정): quantity 0 → 400, 1 → 202, 100 → 202, 101 → 400.
 */
package com.jypark.tps1000.order; // TODO: 실제 패키지(기존 OrderAsyncApiTest와 동일)

// TODO: 기존 통합 테스트와 동일한 베이스/임포트 (SpringBootTest, MockMvc 또는 TestRestTemplate)
// import ...;

@org.springframework.boot.test.context.SpringBootTest
@org.springframework.boot.test.autoconfigure.web.servlet.AutoConfigureMockMvc
class OrderQuantityCapAcceptanceTest {

    // @org.springframework.beans.factory.annotation.Autowired
    // org.springframework.test.web.servlet.MockMvc mvc;  // TODO: 기존 스타일에 맞춤

    // 비동기 주문 생성 요청 바디를 만든다(quantity 파라미터화).
    private String body(int quantity) {
        // TODO: 기존 테스트의 요청 DTO 형태로. 예시(필드명은 실제에 맞춰 교체):
        return """
            { "items": [ { "productId": 1, "quantity": %d } ] }
            """.formatted(quantity);
    }

    // --- 유효: 경계 안 → 202 + orderKey ---

    // @org.junit.jupiter.api.Test
    void quantity_1_은_202_접수() throws Exception {
        // TODO: POST <비동기 주문 생성 경로>, body(1) → status 202, orderKey 존재 단언
    }

    // @org.junit.jupiter.api.Test
    void quantity_100_은_202_접수() throws Exception {
        // TODO: body(100) → 202
    }

    // --- 위반: 경계 밖 → 400, 접수 안 됨 ---

    // @org.junit.jupiter.api.Test
    void quantity_0_은_400_거부() throws Exception {
        // TODO: body(0) → 400. 가능하면 후속 조회로 주문 미존재까지 단언.
    }

    // @org.junit.jupiter.api.Test
    void quantity_101_은_400_거부() throws Exception {
        // TODO: body(101) → 400
    }
}
