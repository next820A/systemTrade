# 2026년 10월 주문 추적 오류 수정 및 원장 정합화

사용자가 승인한 1차 범위인 주문별 추적, 거부·취소 동기화, 수량 구분, 확인된 과거 원장 정합화를 완료했다. 실주문·정정·취소 API는 호출하지 않았다.

## 적용 내용

- systemTrade: KIS의 체결 0주 전량 거부와 확정 취소를 반영한다. 부분체결 후 잔량 종료는 기존 체결량·평균가격을 보존한다.
- systemTrade: 취소 자식 주문을 원주문번호로 연결하고 취소 요청 자체의 거부와 원주문 거부를 구분한다. 조회 거래일과 종목도 확인한다.
- systemTrade: 동기화 상태와 감사 이벤트를 한 트랜잭션으로 저장한다. 동시 변경을 덮어쓰지 않으며, 정정 후 반복 조회의 누적 체결 중복 합산을 막는다.
- systemTrade: 조회 누락·불명확한 0주 체결은 장 종료만으로 취소 확정하지 않고 재확인 대상으로 남긴다.
- systemAlgo: 일부 주문 제출이 실패해도 나머지 주문의 상태를 끝까지 추적한다. 조회 실패 때는 후속 취소를 보내지 않으며, 정상 조회 후 시간 초과 시 열린 주문만 정리한다.
- systemAlgo: 목표·제출·체결 수량, 자금 부족 제외, 수량 축소, 브로커 거부를 분리해 출력한다. 후보 순위와 매수 직전 KIS 가능수량 기준은 유지한다. 자금화 예상 부족액도 별도 표시한다.

가격 추격 매수 +1%·매도 -1%, 매수 대기 15분과 기존 정정 횟수 정책을 유지했다. DB 스키마 변경과 의존성 변경은 없다.

## 확인된 과거 주문 보정

2026-10-18 KIS 일별 주문·체결을 다시 조회하고 변경 미리보기를 확인한 뒤, 주문 ID를 한정하여 적용했다.

| 주문 ID | 거래일 / 계좌 별칭 | 변경 | 근거 조회 ID |
| --- | --- | --- | --- |
| 83 | 10/14 / hagfish | `CANCELED`·체결 0주를 유지하고 미실행 수량 `NULL → 2`로 보완. 기존 취소 시각 보존 | 2463 |
| 91 | 10/16 / halfrise | `SENT → REJECTED`, 체결 0주·미실행 2주·`BROKER_REJECTED` 반영 | 2464 |

두 건 모두 수량과 평균 체결가격을 변경하지 않았다. `BROKER_STATE_SYNCED` 이벤트에 변경 전후 상태와 조회 ID를 남겼으며, 동일 응답을 반복 동기화했을 때 추가 변경이 없음을 확인했다. 주문 91의 상세 거부 사유는 KIS 조회에서 제공되지 않았다. 새로 기록한 종료 시각은 거부 상태를 확인·정합화한 시각이며 실제 거부 발생 시각을 추정한 값이 아니다.

계좌번호와 인증정보를 제외한 전후 비교 자료:
`/Volumes/InvestmentStore/aquma-finance/systemTrade/reconciliation/order_recovery_20261018.json`

## 검증

- systemTrade: `caffeinate -dimsu poetry run pytest -q` — 76개 통과.
- systemAlgo: `caffeinate -dimsu poetry run pytest -q tests/test_order_tracking_recovery.py tests/test_operations_system_trade_bridge.py tests/test_halfrise_operations.py` — 38개 통과. 기존 pandas 사용 중단 예정 경고 3건.
- 양쪽 저장소 `git diff --check` 통과.
- 실제 KIS 조회에 근거한 두 건의 원장 수정과 반복 실행 검증 완료.

배당·ETF 분배금 정산과 실행 비용의 성과 기여 분석은 별도 2차 범위로 남긴다.

## 배포

2026-10-18 사용자 배포 승인에 따라 systemTrade `main`과 systemAlgo `master`에 이번 수정만 커밋·원격 반영한다. systemAlgo의 별도 연구 커밋과 작업 파일은 보존하며 이번 배포로 함께 푸시하지 않는다.

운영은 Mac mini의 `/Users/aquma/repo/systemAlgo/scripts/run_daily_trade_signal_batch.sh`를 launchd가 평일 08:45 Asia/Seoul에 실행하는 구조다. 이 래퍼가 운영 체크아웃의 Python 코드를 매번 새로 읽고, systemTrade CLI도 `/Users/aquma/repo/systemTrade`에서 실행한다. 다음 예약 실행부터 배포 코드를 사용한다. `RunAtLoad=true`이므로 서비스 재등록·강제 시작 대신 기존 등록과 일정을 유지한다. 실제 주문을 발생시키는 배치 수동 실행은 배포 검증에 사용하지 않는다.
