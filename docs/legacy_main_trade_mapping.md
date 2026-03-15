# 레거시 매핑 (`trade.main_trade` -> `systemTrade`)

기존 코드에서 자주 사용된 컬럼:
- `gubun` (매수/매도 의도)
- `code`
- `type` (시장가/지정가 등 주문 방식)
- `qty`
- `price`
- `status` (`매수전`, `매수중`, `매도전`, `매도중`, `주문완료`)
- `algo`
- `date_cd`
- `reason`

신규 정규화 매핑:
- `gubun` -> `side`
- `code` -> `symbol`
- `type` -> `order_type`
- `qty` -> `quantity`
- `price` -> `price`
- `algo` -> `strategy_id`
- `date_cd` -> `trade_date`
- `reason` -> `reason`
- `status` -> `status` (`CREATED`, `SUBMITTING`, `SENT`, `PARTIALLY_FILLED`, `FILLED`, `REJECTED` 등)
