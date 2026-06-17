/*
 * 숨긴 인수 테스트 (HIDDEN ACCEPTANCE TEST) — 사람이 작성, 모델 비공개.
 *
 * ─ 규칙 ────────────────────────────────────────────────────────────────
 *  - 이 파일은 개발 단계의 target repo에 두지 않는다(모델이 보면 위조 가능).
 *  - 인수 게이트에서 1회만 target의 test 소스셋에 주입해 실행한다.
 *  - 이 테스트의 실패 로그는 모델에게 피드백하지 않는다(피드백하면 누설된다).
 *  - 성공의 ground truth = 이 테스트 + 기존 25개 회귀 테스트가 모두 green.
 *
 * ─ 시그니처 출처 ───────────────────────────────────────────────────────
 *  기존 OrderAsyncApiTest / CreateOrderRequest / IntegrationTestBase에 맞췄다:
 *   - 베이스: IntegrationTestBase (mockMvc, objectMapper 제공, @SpringBootTest)
 *   - 요청 DTO: CreateOrderRequest(Long productId, int quantity) — @Min(1) 기존 존재
 *   - 엔드포인트: POST /api/orders/async, 202 응답 바디 $.orderKey (string)
 *  경계 케이스(고정): quantity 0 → 400, 1 → 202, 100 → 202, 101 → 400.
 *  주의: 현재 코드엔 하한(@Min(1))만 있어 0은 이미 400이다. 상한(<=100)이 없어
 *  101은 지금 202다 — 이 테스트의 변별점은 100→202 / 101→400이다.
 */
package com.jypark.tps1000.order;

import com.jypark.tps1000.IntegrationTestBase;
import com.jypark.tps1000.order.dto.CreateOrderRequest;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.springframework.http.MediaType;
import org.springframework.test.web.servlet.ResultActions;

import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

/**
 * 비동기 주문 접수 수량 상한(1~100) 인수 테스트.
 */
class OrderQuantityCapAcceptanceTest extends IntegrationTestBase {

    /** 비동기 주문 생성 요청(quantity 파라미터화) — 기존 OrderAsyncApiTest와 동일한 DTO/경로/직렬화. */
    private ResultActions postAsync(int quantity) throws Exception {
        var request = new CreateOrderRequest(1L, quantity);
        return mockMvc.perform(post("/api/orders/async")
                .contentType(MediaType.APPLICATION_JSON)
                .content(objectMapper.writeValueAsString(request)));
    }

    // --- 유효: 경계 안 → 202 + orderKey ---

    @Test
    @DisplayName("quantity 1 → 202 접수 (orderKey 반환)")
    void quantity_1_isAccepted() throws Exception {
        postAsync(1)
                .andExpect(status().isAccepted())
                .andExpect(jsonPath("$.orderKey").isString());
    }

    @Test
    @DisplayName("quantity 100 → 202 접수 (상한 경계, orderKey 반환)")
    void quantity_100_isAccepted() throws Exception {
        postAsync(100)
                .andExpect(status().isAccepted())
                .andExpect(jsonPath("$.orderKey").isString());
    }

    // --- 위반: 경계 밖 → 400 (발행 전 검증, 접수 안 됨) ---

    @Test
    @DisplayName("quantity 0 → 400 거부 (하한 위반)")
    void quantity_0_isRejected() throws Exception {
        postAsync(0).andExpect(status().isBadRequest());
    }

    @Test
    @DisplayName("quantity 101 → 400 거부 (상한 위반)")
    void quantity_101_isRejected() throws Exception {
        postAsync(101).andExpect(status().isBadRequest());
    }
}
