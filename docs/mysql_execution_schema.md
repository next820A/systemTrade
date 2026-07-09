# MySQL execution schema

`systemTrade`의 MySQL은 시장 데이터 저장소가 아니라 실행 제어 원장이다.
가격, 시그널 연구, 백테스트 결과, 장기 분석용 복제본은 BigQuery 쪽에 둔다.

## 책임 경계

MySQL에 남기는 것:

- 거래 계좌 마스터와 전략-계좌 매핑
- 주문 의도와 KIS 주문번호 매핑
- 주문 상태 머신과 이벤트 감사 로그
- 체결 원장과 KIS 일별 체결조회 스냅샷
- 잔고/보유/매수가능/매도가능/정정취소가능 조회 스냅샷
- `systemAlgo`가 만든 매수/매도 조건의 참조값과 주문 시점 조건 스냅샷

MySQL에 남기지 않는 것:

- OHLCV, 종목 메타, 시총, 펀더멘털
- 전체 전략 신호 이력
- 백테스트 상세 산출물
- 장기 성과 분석용 대용량 fact table

## systemAlgo 조건 저장 연계

`systemAlgo`가 조건 원본을 저장하더라도 `systemTrade.trade_orders`에는 아래 값을 함께 남긴다.

- `source_system`: 보통 `systemAlgo`
- `source_run_id`: 오늘 주문 의도를 만든 workflow/run id
- `strategy_family`: `test`, `hagfish`, `halfrise`처럼 계좌 매핑에 쓰는 안정적인 알고리즘 family
- `strategy_name`, `strategy_id`, `strategy_version`
- `trade_date`: `T close` 신호를 실행할 `T+1` 세션 날짜
- `signal_id`: systemAlgo 신호/후보 row 참조
- `condition_id`, `condition_version`: 매수/매도 조건 정의 참조
- `condition_snapshot`: 주문 시점 조건 JSON 스냅샷
- `intent_metadata`: signal strength, feature score, portfolio context 같은 보조 JSON

이렇게 하면 BigQuery나 systemAlgo 산출물이 잠시 안 보여도 MySQL 원장만으로 주문 근거를 추적할 수 있다.

### 주문 유형과 가격/수량 조정

`systemAlgo`는 매수 주문 의도를 생성할 때 가능하면 `LIMIT` 주문으로 넘긴다. `MARKET`은 체결 편의성은 높지만, 계좌별 주문가능금액이 부족한 경우 KIS에서 `APBK0952` 같은 거절이 발생하기 쉽고 재전송 시에도 같은 문제가 반복된다.

권장 흐름:

1. systemAlgo가 기준가격을 정한다.
2. 기준가격 또는 buffer 적용 가격을 `trade_orders.price`로 넘긴다.
3. `order_type='LIMIT'`으로 넘긴다.
4. 주문가능금액/가능수량을 알 수 있으면 `quantity`를 가능수량 이하로 줄여 intent를 만든다.
5. 원래 목표수량, 기준가격, buffer, 잔여 현금 등은 `condition_snapshot.features`와 `intent_metadata`에 남긴다.

권장 JSON 키:

- `condition_snapshot.features.reference_price`
- `condition_snapshot.features.price_buffer_pct`
- `condition_snapshot.features.target_quantity`
- `condition_snapshot.features.adjusted_quantity`
- `condition_snapshot.features.residual_cash`
- `intent_metadata.reference_price`
- `intent_metadata.target_quantity`
- `intent_metadata.adjusted_quantity`

systemTrade의 현재 안전장치:

- `BUY + MARKET` 주문이라도 `intent_metadata` 또는 `condition_snapshot.features`에 `limit_price`, `reference_price`, `buy_threshold_price` 중 하나가 있으면 실주문 직전에 `LIMIT`으로 변환한다.
- 변환 가격으로 KIS 매수가능 조회를 수행하고, `max_buy_qty`가 원 주문수량보다 작으면 전송 수량을 줄인다.
- 가능수량이 0이면 KIS 주문을 보내지 않고 `BUYING_POWER_EXHAUSTED`로 로컬 거절 처리한다.
- 조정된 `order_type`, `quantity`, `price`는 `trade_orders`에 반영하고, 원본 주문 의도와 조정 내역은 `trade_order_events.request_payload.adjustment`에 남긴다.

이 안전장치는 마지막 방어선이다. systemAlgo가 처음부터 `LIMIT + price + 가능수량 이하 quantity`를 넘기면 주문 의도와 실제 전송값이 더 명확해진다.

## 알고리즘별 계좌 분리

초기 운영은 계좌 3개를 기준으로 둔다.

- `test`: 오래된 테스트 계좌. smoke test, 신규 주문/조회 로직 검증, hagfish 2.6 소액 검증용
- `hagfish`: 이번에 만든 hagfish 알고리즘 전용 계좌
- `halfrise`: 다음 주에 만들 halfrise 알고리즘 전용 예정 계좌

테이블 역할:

- `trade_accounts`: KIS 계좌 마스터. `account_alias`는 `test`, `hagfish`, `halfrise` 중 하나를 사용한다.
  - `account_role`: `TEST` 또는 `STRATEGY`
  - `account_status`: `PLANNED`, `ACTIVE`, `PAUSED`, `CLOSED`
  - `halfrise`처럼 아직 계좌번호가 없으면 `account_no=NULL`, `account_status='PLANNED'`, `is_active=0`으로 먼저 만든다.
- `account_capital_allocations`: 계좌별 목표 자금 배분. 실제 잔고는 KIS 조회 snapshot에 남기고, 이 테이블은 "이 계좌에 얼마/몇 %를 운용하기로 했는지" 계획값을 담는다.
- `strategy_account_allocations`: `strategy_family`와 `trade_accounts.id`의 유효기간 있는 매핑
- `trade_orders`: 주문 시점의 `account_no`, `account_product_code`, `account_alias`를 denormalize 해서 저장
- `account_query_requests`: 잔고/가능수량 조회도 어떤 알고리즘 계좌를 조회했는지 `account_alias`를 함께 저장

운영 이름은 버전마다 바뀔 수 있으므로 계좌 매핑은 `strategy_name`보다 `strategy_family`를 우선한다.
계좌별 실행은 명령의 `account_alias`를 기준으로 `trade_accounts`에서 KIS 계좌번호를 읽는다. `SYSTEM_TRADE_ACCOUNT_ALIAS`는 선택적 기본값으로만 사용하고, 명령의 `account_alias`와 env alias가 다르면 주문을 차단한다.
실주문 전에는 해당 alias의 `trade_accounts` 행에 KIS 계좌번호가 바인딩되어 있고 `ACTIVE/is_active=1`이어야 하며,
전략 계좌는 같은 `trade_account_id`에 active `strategy_account_allocations` 매핑이 있어야 한다.

기본 매핑은 아래처럼 둔다.

| account_alias | account_role | account_status | strategy_family | allocation active |
| --- | --- | --- | --- | --- |
| `test` | `TEST` | `ACTIVE` | `test` | yes |
| `test` | `TEST` | `ACTIVE` | `hagfish` | yes, hagfish 2.6 검증 기간용 |
| `hagfish` | `STRATEGY` | `ACTIVE` | `hagfish` | yes, 승격 후 운용 |
| `halfrise` | `STRATEGY` | `PLANNED` | `halfrise` | no, 계좌 개설 후 yes |

테스트 계좌에서 hagfish를 돌릴 때도 주문 요청의 `strategy_family`는 `hagfish`로 유지한다. 계좌 전환은 `SYSTEM_TRADE_ACCOUNT_ALIAS`와 주문 인자의 `account_alias`만 `test`에서 `hagfish`로 바꾸는 방식으로 처리한다.

`halfrise` 계좌가 만들어지면 계좌번호를 채우고 활성화한다.

```sql
UPDATE trade_accounts
SET account_no = '<HALFRISE_ACCOUNT_NO>',
    account_product_code = '01',
    account_status = 'ACTIVE',
    is_active = 1
WHERE broker = 'KIS'
  AND account_alias = 'halfrise';

UPDATE strategy_account_allocations saa
JOIN trade_accounts ta ON ta.id = saa.trade_account_id
SET saa.is_active = 1,
    saa.valid_from = CURRENT_DATE
WHERE ta.broker = 'KIS'
  AND ta.account_alias = 'halfrise'
  AND saa.strategy_family = 'halfrise'
  AND saa.allocation_role = 'PRIMARY'
  AND saa.valid_to IS NULL;
```

## 실제 KIS 조회 응답 기준

2026-04-26에 조회 API의 응답 구조를 확인했다.

- 잔고 조회: `output1`은 보유종목 list, `output2`는 계좌요약 list
- 매수가능 조회: `output` dict
- 매도가능 조회: `output` dict
- 정정취소 가능 주문 조회: `output` list
- 일별 주문/체결 조회: `output1` 체결/주문 list, `output2` summary dict

정규화 테이블은 위 구조를 기준으로 best-effort 필드만 빼고, 원본 row/payload는 JSON으로 같이 저장한다.
