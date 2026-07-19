# systemTrade 작업 규칙

이 저장소는 KIS 주문 실행, 잔고, 체결, 실행 원장을 담당한다. 백테스트와 전략 연구는 `systemAlgo`, 데이터 수집과 백필은 `systemData`에서 처리한다.

## Python 실행 환경 (필수)

- 이 저장소의 기본이자 유일한 Python 실행 환경은 Poetry다.
- CLI와 모듈은 `poetry run python -m system_trade.main ...`, 테스트는 `poetry run pytest ...`로 실행한다.
- bare `python`, `python3`, `pip`, `pip3`, `pytest`를 직접 실행하거나 시스템/봇 가상환경으로 우회하지 않는다.
- 의존성 설치와 변경은 `poetry install --with dev`, `poetry add`, `poetry remove`를 사용하고 `poetry.lock`을 함께 유지한다.
- 환경 진단은 `command -v poetry`, `poetry env info`, `poetry run python -V` 순서로 한다. Poetry를 찾지 못해도 시스템 Python으로 대체 실행하지 않는다.
- 장기 작업은 `caffeinate -dimsu poetry run ...` 형태로 실행한다.

## 안전 원칙

- 사용자 요청 없이 실주문을 실행하지 않는다. 가능한 경우 조회 또는 dry-run으로 먼저 검증한다.
- `.env`, 계좌번호, 앱 키와 시크릿을 출력하거나 커밋하지 않는다.
- 작업 전 `git status --short`를 확인하고 기존 사용자 변경은 보존한다.
