# 토스증권 Open API 연동 가능성 조사

조사일: 2026-09-13 (KST)  
대상 명세: 토스증권 Open API REST v1.2.15

## 결론

**가능하다.** 현재 systemTrade가 KIS로 수행하는 국내주식 현금 주문 실행, 주문 정정·취소, 잔고·주문 가능 수량 조회, 주문/체결 상태 동기화는 토스증권 Open API로 구현할 수 있다. 토스는 국내(KR)와 미국(US) 주식을 같은 REST API로 제공하므로, 현 KIS 국내주식 범위를 충족할 뿐 아니라 해외주식까지 확장할 수 있다.

다만 KIS 클라이언트를 토스 호출로 단순 치환하는 작업은 아니다. 인증·계좌 식별·주문 ID·응답 형식·실시간 구독 프로토콜이 다르므로 `BrokerClient` 같은 브로커 추상화 뒤에 `TossInvestClient`를 추가하는 방식이 안전하다.

## 현재 systemTrade 기능과의 대응

| systemTrade/KIS 기능 | 토스증권 Open API | 판정 | 이식 시 유의점 |
| --- | --- | --- | --- |
| 액세스 토큰 | `POST /oauth2/token` (OAuth 2.0 client credentials) | 가능 | 토큰 재발급 시 이전 토큰이 즉시 무효화되므로 공유 캐시와 단일 발급 락이 필요하다. |
| 계좌 선택 | `GET /api/v1/accounts`, 이후 `X-Tossinvest-Account: accountSeq` 헤더 | 가능 | KIS의 계좌번호+상품코드 대신 서버가 준 `accountSeq`를 저장·사용한다. |
| 현재가 / 최우선 호가 | `GET /api/v1/prices`, `/orderbook` | 가능 | 기존 매수 안전장치와 재호가 계산에 사용할 수 있다. |
| 보유종목·자산 요약 | `GET /api/v1/holdings` | 가능 | KR·US 주식만 포함한다. |
| 매수 가능 금액 | `GET /api/v1/buying-power?currency=KRW|USD` | 가능 | 현금 기반 금액이며, 기존처럼 주문 직전에 조회한다. |
| 매도 가능 수량 | `GET /api/v1/sellable-quantity?symbol=...` | 가능 | 기존 사전 검증에 대응한다. |
| 시장가·지정가 매수/매도 | `POST /api/v1/orders` | 가능 | KR/US 지원. US 시장가 주문은 금액(`orderAmount`) 기반도 지원한다. |
| 중복 주문 방지 | 요청 본문의 `clientOrderId` | 가능 | 36자 이하 영숫자/`-`/`_`; 동일 키 멱등성 보존 기간은 **10분**이므로 DB의 영구 idempotency 제약을 계속 기준으로 둔다. |
| 주문 정정 | `POST /api/v1/orders/{orderId}/modify` | 가능 | KR은 수량 필수, US는 수량 정정을 지원하지 않고 가격만 변경 가능하다. |
| 주문 취소 | `POST /api/v1/orders/{orderId}/cancel` | 가능 | 이미 체결 완료된 주문은 취소할 수 없다. |
| 미체결/종료 주문·체결 동기화 | `GET /api/v1/orders?status=OPEN|CLOSED`, `GET /api/v1/orders/{orderId}` | 가능 | 주문 상세에 부분체결 내역이 포함된다. `CLOSED` 목록은 페이지 처리해야 한다. |
| 실시간 체결통보 | `wss://openapi-ws.tossinvest.com/ws/v1` (별도 AsyncAPI) | 가능 | REST 폴링 대신 주문 이벤트를 구독해 원장을 갱신할 수 있다. 재연결·누락분 REST 재조정이 필요하다. |
| 조건주문 | `POST /api/v1/conditional-orders` 등 | 추가 기능 | SINGLE/OCO/OTO를 지원한다. 현 시스템에는 별도 모델·원장이 없으므로 후속 범위다. |

## 현 시스템 대비 차이와 제약

- 토스 인증은 `client_id`/`client_secret`을 OAuth 토큰으로 교환한다. 토큰은 24시간 예시가 문서에 제시되어 있으며 refresh token은 없고, 새 토큰 발급은 기존 토큰을 즉시 무효화한다.
- 주문·자산 API는 Bearer 토큰 외에 `X-Tossinvest-Account` 헤더가 필수다. 계좌 목록 API가 있으므로 KIS처럼 계좌번호를 수동 바인딩할 필요는 없지만, `trade_accounts`에는 토스 계좌의 `accountSeq`를 보관해야 한다.
- 토스 `orderId`는 서버 생성 식별자이고 정정/취소에 사용한다. `trade_orders.broker_order_no`에 저장해 현재 원장 구조를 재사용할 수 있다.
- 주문 생성은 `clientOrderId`를 제공할 때만 API 멱등성이 적용되고, 기간은 10분이다. 따라서 systemTrade의 DB 수준 `idempotency_key` 중복 차단을 제거하면 안 된다.
- 주문 목록은 Open API가 지원하는 호가 유형의 주문만 반환한다. 앱에서 낸 시간외 주문 등은 목록·상세에 보이지 않을 수 있으므로, 토스 계좌를 자동매매 전용으로 운영하거나 외부 주문과의 조정 정책을 정해야 한다.
- 공개 REST 명세에는 KIS와 같은 모의투자/샌드박스 엔드포인트나 별도 테스트 환경이 기재되어 있지 않다. 문서만으로 "없다"고 단정할 수는 없지만, 현재 구현 검증은 **실계좌 주문을 전제하지 않도록** HTTP mock·record-only·아주 작은 수량의 명시적 승인 절차로 설계해야 한다.
- 토큰 발급은 WTS Open API 설정의 허용 IP에 등록되지 않은 IP에서 403으로 차단된다. 고정 egress IP 또는 실행 환경의 IP 변경 절차가 필요하다.

## 권장 구현 순서

1. `BrokerClient` 프로토콜을 도입하고 현 `KISClient`를 어댑터로 감싼다. 주문, 정정/취소, 잔고, 매수 가능 금액, 매도 가능 수량, 주문 조회, 호가, 실시간 이벤트를 인터페이스에 둔다.
2. `trade_accounts`에 토스 계좌 식별자(`accountSeq`)와 브로커별 credential 참조를 추가한다. 계좌번호·키·시크릿은 원장이나 저장소에 기록하지 않는다.
3. `TossInvestClient`를 REST부터 구현한다. `clientOrderId`에는 기존 `idempotency_key`를 토스 허용 문자/길이에 맞게 안정적으로 변환해 넣고, 토스 `orderId`를 `broker_order_no`로 저장한다.
4. HTTP mock 기반 단위 테스트로 주문 생성, 10분 멱등성 창 밖 재시도, 429, 토큰 재발급, 부분체결, 정정/취소 제한을 검증한다. 공개 명세에서 샌드박스가 확인되기 전에는 실주문 테스트를 자동화하지 않는다.
5. REST 주문 이력 동기화를 먼저 운영한 뒤, AsyncAPI 명세를 기준으로 WebSocket 주문 이벤트 구독과 재연결 후 REST reconciliation을 추가한다.

## 공식 근거

- [토스증권 Open API 가이드](https://developers.tossinvest.com/)
- [REST OpenAPI 명세 v1.2.15](https://openapi.tossinvest.com/openapi-docs/latest/openapi.json) — 주문 생성·정정·취소, 계좌·보유자산, 주문 이력, 거래 가능 정보, OAuth, rate limit, `clientOrderId` 스키마의 기준 문서
- [실시간 AsyncAPI 명세](https://openapi.tossinvest.com/openapi-docs/latest/asyncapi.json) — 실시간 체결·호가·본인 주문 이벤트 WebSocket의 기준 문서
- [토스증권 Open API 소개](https://corp.tossinvest.com/ko/open-api) — KR/US 통합 주문과 API Key 발급 대상 안내

문서의 API는 변경될 수 있으므로 실제 구현을 시작할 때에는 위 OpenAPI/AsyncAPI의 최신 버전과 토스증권 WTS의 Open API 키 발급·허용 IP·테스트 환경 정책을 다시 확인한다.
