# `main_trade` vs `systemTrade` (코드 기반 추정)

서버 DB 접속이 안 되는 상태라, `systemAlgo` 코드에서 사용된 컬럼 기준으로 `trade.main_trade`를 추정해 비교합니다.

## 1) 기존 `trade.main_trade` 추정 구조

`algo_buy_list.py`, `algo_sell_list.py`, `pytrader5_without_ui.py`, `get_algo_status.py`에서 반복적으로 사용된 컬럼:

- `id` (PK 추정)
- `gubun` (`매수`/`매도`)
- `code` (종목코드)
- `type` (`시장가`, `지정가` 등)
- `qty`
- `price`
- `status` (`매수전`, `매수중`, `매도전`, `매도중`, `주문완료`)
- `algo` (전략 번호: int)
- `date_cd` (거래 대상일)
- `reason`

특징:
- 주문 의도 큐 + 주문 상태 + 일부 체결 후처리 관리를 한 테이블에서 같이 수행.
- 체결 상세는 별도 `trade.trade_result`에서 조인/집계.

## 2) 신규 `systemTrade` 테이블 역할

### `trade_orders`
주문 의도/주문 요청 단위의 원장.

핵심 컬럼:
- `idempotency_key` (중복 주문 방지)
- `side`, `symbol`, `order_type`, `quantity`, `price`
- `status` (`CREATED` ~ `FAILED`)
- `strategy_id` (기존 `algo` 번호 호환)
- `strategy_name`
- `trade_date`, `reason`
- `broker_order_no`, `broker_org_order_no`

### `trade_order_events`
주문 이벤트 이력.
- CREATED/SUBMITTING/SENT/REJECTED 등 이벤트를 JSON payload와 함께 저장.

### `trade_fills`
체결 레코드 단위 저장.
- 체결 수량/가격/수수료/세금/체결시각.

### `trade_positions`
계좌-종목 포지션 스냅샷/집계.

### `trade_account_balances`
현금/평가금액 스냅샷.

## 3) 컬럼 매핑

- `main_trade.id` -> `trade_orders.id`
- `main_trade.gubun` -> `trade_orders.side`
- `main_trade.code` -> `trade_orders.symbol`
- `main_trade.type` -> `trade_orders.order_type`
- `main_trade.qty` -> `trade_orders.quantity`
- `main_trade.price` -> `trade_orders.price`
- `main_trade.status` -> `trade_orders.status` (영문 상태 머신)
- `main_trade.algo` -> `trade_orders.strategy_id`
- `main_trade.date_cd` -> `trade_orders.trade_date`
- `main_trade.reason` -> `trade_orders.reason`

## 4) 상태값 매핑(권장)

- `매수전` / `매도전` -> `CREATED`
- `매수중` / `매도중` -> `SUBMITTING` 또는 `SENT`
- `주문완료` -> `FILLED` (전량체결) 또는 `PARTIALLY_FILLED` (부분체결)
- 주문거부/실패 케이스(기존에 분산 처리) -> `REJECTED` / `FAILED`

## 5) 분리 효과

기존: `main_trade` 하나에 의도/실행/이력 혼합

신규:
- 의도/상태: `trade_orders`
- 이벤트로그: `trade_order_events`
- 체결: `trade_fills`
- 포지션/잔고: `trade_positions`, `trade_account_balances`

운영 관점에서 장애 원인 추적, 재처리, 정합성 검증이 훨씬 쉬워짐.

## 6) 다음 적용 순서

1. `strategy_id` 기준으로 기존 전략 번호를 그대로 연계.
2. 기존 스케줄러가 생성하던 buy/sell 리스트를 `trade_orders` insert로 변경.
3. 체결조회 동기화 작업에서 `trade_fills` 적재 + `trade_orders.status` 갱신.
4. 필요하면 `main_trade` 호환 조회용 View 제공.
