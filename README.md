# systemTrade

한국투자증권(KIS) API를 이용한 실거래 실행 전용 저장소입니다.

## 확정된 방향

- DB: MySQL을 원장(System of Record)으로 사용
- 브로커 범위: 현재는 KIS만 지원
- 백테스팅: 이 저장소에서 분리

## 이 저장소가 하는 일

- KIS 현금 매수/매도 주문 실행
- 실행 플로우에 사용할 KIS TR ID 세트를 고정하여 사용 (`001x`, `008x`, `8434R`, `8908R`, `8408R`)
- MySQL에 거래 원장 저장 (`trade_orders`, `trade_order_events`, `trade_fills`, `trade_positions`, `trade_account_balances`)
- `idempotency_key`를 이용한 중복 주문 방지

## 이 저장소가 하지 않는 일

- 뉴스 크롤링/배치 ETL (`systemData`로 분리)
- 백테스팅/전략 시뮬레이션

## 환경 설정 (`pyenv` + `venv`)

```bash
cd /Users/dongjun/repo/systemTrade
pyenv local 3.12.9
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

필요하면 `.env.example`를 참고하면 됩니다. 코드가 상위 디렉터리의 `.env`도 자동으로 탐색합니다.

## 주요 명령어

```bash
# 토큰 발급과 현재가 조회 확인 (계좌 정보 불필요)
python -m system_trade.main health-check --symbol 005930

# 현재 선택된 고정 TR ID 세트 확인
python -m system_trade.main tr-ids

# 보유 종목과 계좌 요약 조회
python -m system_trade.main balance

# 일자별 주문/체결 이력 조회
python -m system_trade.main daily-ccld --start-date 20260301 --end-date 20260307

# 특정 종목/가격 기준 매수 가능 수량 조회
python -m system_trade.main buying-power --symbol 005930 --price 70000 --order-type LIMIT

# 매도 가능 수량 조회
python -m system_trade.main sellable --symbol 005930

# MySQL 마이그레이션 적용
python -m system_trade.main init-db

# 주문 실행 (계좌 정보 필요)
python -m system_trade.main order \
  --side BUY --symbol 005930 --qty 1 --order-type LIMIT --price 70000 \
  --strategy-id 1 \
  --idempotency-key demo-20260302-1 --strategy algo1 --reason smoke \
  --allow-live

# MySQL에 저장된 최근 주문 목록 확인
python -m system_trade.main list-orders --limit 20
```

## 백테스팅과의 경계

백테스팅 시스템은 아래와 같은 정규화된 주문 의도만 넘기도록 제한합니다.

- `strategy_name`
- `strategy_id` (기존 `algo` 번호 호환용)
- `side`
- `symbol`
- `quantity`
- `order_type`
- `price` (`LIMIT` 주문일 때 사용)
- `idempotency_key`

이 저장소 안에는 백테스팅 로직을 구현하지 않습니다.

## 고정 TR ID

- 주문: `TTTC0012U` 매수, `TTTC0011U` 매도, `TTTC0013U` 정정/취소
- 주문 이력 조회: `TTTC0081R` (`inner`), `CTSC9215R` (`before`)
- 잔고/가능수량 조회: `TTTC8434R`, `TTTC8908R`, `TTTC8408R`, `TTTC0084R`
- 실시간 체결통보 WebSocket: `H0STCNI0` (모의투자는 `H0STCNI9`)

## 안전장치

- 실전 계좌에서 `order` 명령은 기본적으로 차단되며, `--allow-live`를 명시한 경우에만 실행됩니다.
