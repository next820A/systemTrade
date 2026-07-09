# 경계: 실거래 실행 vs 백테스팅

실거래 실행 저장소(`systemTrade`)가 담당하는 범위:
- 브로커 연동(KIS)
- 주문 상태 머신과 감사 로그(audit trail)
- 체결, 포지션, 잔고 원장

백테스팅 저장소가 담당하는 범위:
- 시그널 생성
- 시뮬레이션 및 성과 분석
- 파라미터 탐색

연계 원칙: 백테스팅 쪽에서는 정규화된 주문 의도만 전달합니다.

## 주문 유형 연계

`systemAlgo`는 매수 주문을 가능하면 `LIMIT`으로 생성하고, 기준가격을 `price`와 `condition_snapshot.features.reference_price`에 함께 남긴다. 계좌별 매수가능금액/가능수량을 반영할 수 있으면 주문 의도 생성 시점에 `quantity`를 가능수량 이하로 줄인다.

systemTrade는 최종 안전장치로 `BUY + MARKET` 주문에 기준가격 메타데이터가 있을 때 실주문 직전에 `LIMIT`으로 변환하고 KIS 매수가능수량에 맞춰 수량을 줄인다. 가능수량이 0이면 주문을 보내지 않고 로컬에서 `BUYING_POWER_EXHAUSTED`로 거절한다.
