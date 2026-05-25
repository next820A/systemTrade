ALTER TABLE trade_accounts
    MODIFY account_no VARCHAR(32) NULL;

ALTER TABLE trade_accounts
    MODIFY account_product_code VARCHAR(8) NULL DEFAULT '01';

ALTER TABLE trade_accounts
    ADD COLUMN account_role ENUM('TEST', 'STRATEGY') NOT NULL DEFAULT 'STRATEGY' AFTER account_product_code;

ALTER TABLE trade_accounts
    ADD COLUMN account_status ENUM('PLANNED', 'ACTIVE', 'PAUSED', 'CLOSED') NOT NULL DEFAULT 'ACTIVE' AFTER account_role;

ALTER TABLE trade_accounts
    ADD KEY idx_trade_accounts_role_status (account_role, account_status, is_active);

CREATE TABLE IF NOT EXISTS account_capital_allocations (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    trade_account_id BIGINT UNSIGNED NOT NULL,
    currency CHAR(3) NOT NULL DEFAULT 'KRW',
    target_cash_amount DECIMAL(20, 2) NULL,
    target_weight DECIMAL(10, 8) NULL,
    cash_buffer_amount DECIMAL(20, 2) NULL,
    allocation_status ENUM('PLANNED', 'ACTIVE', 'RETIRED') NOT NULL DEFAULT 'PLANNED',
    valid_from DATE NULL,
    valid_to DATE NULL,
    note VARCHAR(255) NULL,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
    PRIMARY KEY (id),
    KEY idx_account_capital_allocations_account_status (trade_account_id, allocation_status),
    KEY idx_account_capital_allocations_validity (valid_from, valid_to),
    CONSTRAINT fk_account_capital_allocations_account
      FOREIGN KEY (trade_account_id) REFERENCES trade_accounts(id)
      ON DELETE RESTRICT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

ALTER TABLE strategy_account_allocations
    ADD KEY idx_strategy_account_allocations_family_validity (strategy_family, valid_from, valid_to);

INSERT INTO trade_accounts (
    broker,
    account_alias,
    account_no,
    account_product_code,
    account_role,
    account_status,
    purpose,
    is_active
) VALUES
    ('KIS', 'test', NULL, '01', 'TEST', 'ACTIVE', '오래된 테스트 계좌. 주문/조회 smoke test와 신규 로직 검증용.', 1),
    ('KIS', 'hagfish', NULL, '01', 'STRATEGY', 'ACTIVE', 'hagfish 알고리즘 전용 운용 계좌.', 1),
    ('KIS', 'halfrise', NULL, '01', 'STRATEGY', 'PLANNED', 'halfrise 알고리즘 전용 예정 계좌. 계좌 개설 후 account_no를 입력하고 ACTIVE로 전환.', 0)
ON DUPLICATE KEY UPDATE
    account_role = VALUES(account_role),
    purpose = VALUES(purpose),
    account_product_code = COALESCE(account_product_code, VALUES(account_product_code));

INSERT INTO strategy_account_allocations (
    strategy_family,
    strategy_name,
    trade_account_id,
    allocation_role,
    is_active,
    valid_from
)
SELECT 'test', 'test', ta.id, 'PRIMARY', 1, CURRENT_DATE
FROM trade_accounts ta
WHERE ta.broker = 'KIS'
  AND ta.account_alias = 'test'
  AND NOT EXISTS (
      SELECT 1
      FROM strategy_account_allocations saa
      WHERE saa.strategy_family = 'test'
        AND saa.trade_account_id = ta.id
        AND saa.allocation_role = 'PRIMARY'
        AND saa.valid_to IS NULL
  );

INSERT INTO strategy_account_allocations (
    strategy_family,
    strategy_name,
    strategy_version,
    trade_account_id,
    allocation_role,
    is_active,
    valid_from
)
SELECT 'hagfish', 'hagfish_v2', '2.6', ta.id, 'PRIMARY', 1, CURRENT_DATE
FROM trade_accounts ta
WHERE ta.broker = 'KIS'
  AND ta.account_alias = 'hagfish'
  AND NOT EXISTS (
      SELECT 1
      FROM strategy_account_allocations saa
      WHERE saa.strategy_family = 'hagfish'
        AND saa.trade_account_id = ta.id
        AND saa.allocation_role = 'PRIMARY'
        AND saa.valid_to IS NULL
  );

INSERT INTO strategy_account_allocations (
    strategy_family,
    strategy_name,
    strategy_version,
    trade_account_id,
    allocation_role,
    is_active,
    valid_from
)
SELECT 'hagfish', 'hagfish_v2', '2.6', ta.id, 'PRIMARY', 1, CURRENT_DATE
FROM trade_accounts ta
WHERE ta.broker = 'KIS'
  AND ta.account_alias = 'test'
  AND NOT EXISTS (
      SELECT 1
      FROM strategy_account_allocations saa
      WHERE saa.strategy_family = 'hagfish'
        AND saa.trade_account_id = ta.id
        AND saa.allocation_role = 'PRIMARY'
        AND saa.valid_to IS NULL
  );

INSERT INTO strategy_account_allocations (
    strategy_family,
    strategy_name,
    trade_account_id,
    allocation_role,
    is_active,
    valid_from
)
SELECT 'halfrise', 'halfrise', ta.id, 'PRIMARY', 0, NULL
FROM trade_accounts ta
WHERE ta.broker = 'KIS'
  AND ta.account_alias = 'halfrise'
  AND NOT EXISTS (
      SELECT 1
      FROM strategy_account_allocations saa
      WHERE saa.strategy_family = 'halfrise'
        AND saa.trade_account_id = ta.id
        AND saa.allocation_role = 'PRIMARY'
        AND saa.valid_to IS NULL
  );
