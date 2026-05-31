# systemTrade

한국투자증권(KIS) API를 이용한 실거래 실행 전용 저장소입니다.

## 확정된 방향

- DB: MySQL을 원장(System of Record)으로 사용
- 브로커 범위: 현재는 KIS만 지원
- 백테스팅: 이 저장소에서 분리

## 이 저장소가 하는 일

- KIS 현금 매수/매도 주문 실행
- 실행 플로우에 사용할 KIS TR ID 세트를 고정하여 사용 (`001x`, `008x`, `8434R`, `8908R`, `8408R`)
- MySQL에 실행 원장 저장 (`trade_orders`, `trade_order_events`, `trade_fills`, `account_query_requests` 계열)
- `idempotency_key`를 이용한 중복 주문 방지

## 이 저장소가 하지 않는 일

- 뉴스 크롤링/배치 ETL (`systemData`로 분리)
- 백테스팅/전략 시뮬레이션

## 로컬 산출물 저장

거래 원장과 계좌 스냅샷은 MySQL을 기준 저장소로 둔다. 파일 로그, export, reconciliation 결과를 남겨야 할 때는 `/Volumes/InvestmentStore/aquma-finance/systemTrade/` 아래 `logs/`, `exports/`, `reconciliation/`을 사용한다.
KIS 토큰 캐시는 민감 정보이고 SSD 미마운트 시 실거래 인증이 깨질 수 있어 기본 홈 디렉터리 캐시를 유지한다.

## 환경 설정 (`pyenv` + `Poetry`)

```bash
cd /Users/dongjun/repo/systemTrade
pyenv local 3.12.9
poetry env use "$(pyenv prefix 3.12.9)/bin/python"
poetry install --with dev
```

필요하면 `.env.example`를 참고하면 됩니다. 코드가 상위 디렉터리의 `.env`도 자동으로 탐색합니다.
계좌를 알고리즘별로 나눠 실행할 때는 계좌별 실행 환경에 `SYSTEM_TRADE_ACCOUNT_ALIAS=test`, `SYSTEM_TRADE_ACCOUNT_ALIAS=hagfish`, `SYSTEM_TRADE_ACCOUNT_ALIAS=halfrise` 중 하나를 함께 둡니다.

## 주요 명령어

```bash
# 토큰 발급과 현재가 조회 확인
poetry run python -m system_trade.main health-check --symbol 005930

# 현재 선택된 고정 TR ID 세트 확인
poetry run python -m system_trade.main tr-ids

# 보유 종목과 계좌 요약 조회
poetry run python -m system_trade.main balance

# 일자별 주문/체결 이력 조회
poetry run python -m system_trade.main daily-ccld --start-date 20260301 --end-date 20260307

# 특정 종목/가격 기준 매수 가능 수량 조회
poetry run python -m system_trade.main buying-power --symbol 005930 --price 70000 --order-type LIMIT

# 매도 가능 수량 조회
poetry run python -m system_trade.main sellable --symbol 005930

# MySQL 마이그레이션 적용
poetry run python -m system_trade.main init-db

# 주문 실행 (계좌 정보 필요)
poetry run python -m system_trade.main order \
  --side BUY --symbol 005930 --qty 1 --order-type LIMIT --price 70000 \
  --strategy-id 1 \
  --idempotency-key demo-20260302-1 --strategy algo1 --reason smoke \
  --allow-live

# MySQL에 저장된 최근 주문 목록 확인
poetry run python -m system_trade.main list-orders --limit 20
```

## 계좌 운용 기준

`init-db`는 기본 계좌 alias와 전략 매핑을 함께 준비합니다.

| alias | 목적 | 상태 |
| --- | --- | --- |
| `test` | 오래된 테스트 계좌. hagfish 2.6 검증 주문도 여기서 먼저 실행 | 즉시 사용 |
| `hagfish` | hagfish 알고리즘 전용 계좌 | 즉시 사용 |
| `halfrise` | halfrise_v2 legacy-close 알고리즘 전용 계좌 | 계좌번호 바인딩 후 사용 |

계좌번호는 저장소에 커밋하지 않고 DB나 실행 환경에만 둡니다. 현재 실행 환경의 KIS 계좌를 alias에 묶을 때는 `poetry run python -m system_trade.main bind-account --account-alias test`처럼 등록합니다. `halfrise` 계좌를 만든 뒤에는 같은 방식으로 alias를 묶으면 `halfrise_v2`/`2.0` 전략 매핑으로 주문 의도 기록이 가능합니다. 목표 자금 배분은 `account_capital_allocations`에 계좌별 금액 또는 비중으로 기록합니다.
테스트 기간에는 `SYSTEM_TRADE_ACCOUNT_ALIAS=test` 환경에서 `--account-alias test --strategy-family hagfish --strategy-version 2.6` 주문만 허용하고, 승격 후에는 환경과 주문 인자를 둘 다 `hagfish` alias로 바꿉니다.

## 백테스팅과의 경계

백테스팅 시스템은 아래와 같은 정규화된 주문 의도만 넘기도록 제한합니다.

- `strategy_name`
- `strategy_id` (기존 `algo` 번호 호환용)
- `strategy_family` (`test`, `hagfish`, `halfrise`처럼 계좌 매핑에 쓰는 이름)
- `side`
- `symbol`
- `quantity`
- `order_type`
- `price` (`LIMIT` 주문일 때 사용)
- `idempotency_key`
- `account_alias` (`test`, `hagfish`, `halfrise` 계좌 구분)
- `trade_date` (`T close` 신호가 실행될 `T+1` 세션 날짜)
- `condition_id` / `condition_version` / `condition_snapshot` (주문 근거 추적용)
- `intent_metadata` (signal strength, feature score 등 보조 정보)

이 저장소 안에는 백테스팅 로직을 구현하지 않습니다.

MySQL 스키마 기준은 [docs/mysql_execution_schema.md](/Users/dongjun/repo/systemTrade/docs/mysql_execution_schema.md)를 참고합니다.

## 고정 TR ID

- 주문: `TTTC0012U` 매수, `TTTC0011U` 매도, `TTTC0013U` 정정/취소
- 주문 이력 조회: `TTTC0081R` (`inner`), `CTSC9215R` (`before`)
- 잔고/가능수량 조회: `TTTC8434R`, `TTTC8908R`, `TTTC8408R`, `TTTC0084R`
- 실시간 체결통보 WebSocket: `H0STCNI0` (모의투자는 `H0STCNI9`)

## 안전장치

- 실전 계좌에서 `order` 명령은 기본적으로 차단되며, `--allow-live`를 명시한 경우에만 실행됩니다.
- 주문 전 `account_alias`, KIS 계좌번호, `trade_accounts` ACTIVE 상태, `strategy_account_allocations` active 매핑이 모두 맞는지 확인합니다.
- 오래된 테스트 계좌에서 hagfish를 검증할 수 있도록 `test` 계좌에도 active `strategy_family='hagfish'` 매핑을 둡니다. 이 매핑은 계좌번호와 env alias가 `test`로 맞을 때만 주문을 통과시킵니다.
