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
 *  기존 OrderAsyncApiTest / IntegrationTestBase / OrderResponse에 맞췄다:
 *   - 베이스: IntegrationTestBase (mockMvc, objectMapper 제공)
 *   - 동기 주문: POST /api/orders → 201, OrderResponse($.orderId,$.status="CREATED")
 *   - 비동기: POST /api/orders/async → 202 $.orderKey, GET /async/{key} → COMPLETED
 *   - 취소(신규): POST /api/orders/{orderId}/cancel
 *  순수 블랙박스 HTTP만 사용 — OrderStatus.CANCELED 등 심볼에 컴파일 의존하지 않는다
 *  (msa에 cancel 엔드포인트·CANCELED가 없어도 컴파일은 되고, 동작에서 red가 난다).
 *  변별점: 409(상태 충돌) 의미론 — naive 구현이 500/400/200으로 틀리기 쉽다.
 */
package com.jypark.tps1000.order;

import com.jypark.tps1000.IntegrationTestBase;
import com.jypark.tps1000.order.dto.CreateOrderRequest;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.springframework.http.MediaType;
import org.springframework.test.web.servlet.MvcResult;

import java.time.Duration;

import static org.awaitility.Awaitility.await;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

/**
 * 주문 취소 상태 머신 인수 테스트.
 */
class OrderCancelAcceptanceTest extends IntegrationTestBase {

    /** 동기 주문 1건 생성(상태 CREATED) → orderId 반환. */
    private long createdOrderId() throws Exception {
        MvcResult r = mockMvc.perform(post("/api/orders")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(objectMapper.writeValueAsString(new CreateOrderRequest(1L, 2))))
                .andExpect(status().isCreated())
                .andExpect(jsonPath("$.status").value("CREATED"))
                .andReturn();
        return objectMapper.readTree(r.getResponse().getContentAsString()).get("orderId").asLong();
    }

    @Test
    @DisplayName("CREATED 주문 취소 → 200 + status CANCELED")
    void cancel_created_returns200() throws Exception {
        long id = createdOrderId();
        mockMvc.perform(post("/api/orders/" + id + "/cancel"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.status").value("CANCELED"));
    }

    @Test
    @DisplayName("이미 취소된 주문 재취소 → 409 (멱등 아님, 충돌)")
    void cancel_alreadyCanceled_returns409() throws Exception {
        long id = createdOrderId();
        mockMvc.perform(post("/api/orders/" + id + "/cancel")).andExpect(status().isOk());
        mockMvc.perform(post("/api/orders/" + id + "/cancel")).andExpect(status().isConflict());
    }

    @Test
    @DisplayName("COMPLETED 주문 취소 → 409 (완료 후 취소 불가)")
    void cancel_completed_returns409() throws Exception {
        MvcResult accepted = mockMvc.perform(post("/api/orders/async")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(objectMapper.writeValueAsString(new CreateOrderRequest(1L, 2))))
                .andExpect(status().isAccepted())
                .andReturn();
        String orderKey = objectMapper.readTree(accepted.getResponse().getContentAsString())
                .get("orderKey").asText();

        long[] id = new long[1];
        await().atMost(Duration.ofSeconds(10)).untilAsserted(() -> {
            MvcResult q = mockMvc.perform(get("/api/orders/async/" + orderKey))
                    .andExpect(status().isOk())
                    .andExpect(jsonPath("$.status").value("COMPLETED"))
                    .andReturn();
            id[0] = objectMapper.readTree(q.getResponse().getContentAsString()).get("orderId").asLong();
        });

        mockMvc.perform(post("/api/orders/" + id[0] + "/cancel"))
                .andExpect(status().isConflict());
    }

    @Test
    @DisplayName("존재하지 않는 주문 취소 → 404")
    void cancel_nonexistent_returns404() throws Exception {
        mockMvc.perform(post("/api/orders/999999999/cancel"))
                .andExpect(status().isNotFound());
    }
}
