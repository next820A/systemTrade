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

ALTER TABLE strategy_account_allocations
    ADD COLUMN strategy_version VARCHAR(64) NULL AFTER strategy_name;

ALTER TABLE strategy_account_allocations
    ADD KEY idx_strategy_account_allocations_family_validity (strategy_family, valid_from, valid_to);

ALTER TABLE trade_orders
    MODIFY idempotency_key VARCHAR(191) NOT NULL;

ALTER TABLE trade_orders
    ADD COLUMN source_system VARCHAR(32) NOT NULL DEFAULT 'systemTrade' AFTER idempotency_key;

ALTER TABLE trade_orders
    ADD COLUMN source_run_id VARCHAR(128) NULL AFTER source_system;

ALTER TABLE trade_orders
    ADD COLUMN trade_account_id BIGINT UNSIGNED NULL AFTER broker;

ALTER TABLE trade_orders
    ADD COLUMN account_product_code VARCHAR(8) NULL AFTER account_no;

ALTER TABLE trade_orders
    ADD COLUMN account_alias VARCHAR(64) NULL AFTER account_product_code;

ALTER TABLE trade_orders
    ADD COLUMN source_symbol VARCHAR(32) NULL AFTER side;

ALTER TABLE trade_orders
    ADD COLUMN strategy_family VARCHAR(64) NULL AFTER strategy_id;

ALTER TABLE trade_orders
    ADD COLUMN strategy_version VARCHAR(64) NULL AFTER strategy_name;

ALTER TABLE trade_orders
    ADD COLUMN signal_id VARCHAR(128) NULL AFTER strategy_version;

ALTER TABLE trade_orders
    ADD COLUMN condition_id VARCHAR(128) NULL AFTER signal_id;

ALTER TABLE trade_orders
    ADD COLUMN condition_version VARCHAR(64) NULL AFTER condition_id;

ALTER TABLE trade_orders
    ADD COLUMN condition_snapshot JSON NULL AFTER condition_version;

ALTER TABLE trade_orders
    ADD COLUMN intent_metadata JSON NULL AFTER condition_snapshot;

ALTER TABLE trade_orders
    MODIFY reason VARCHAR(255) NULL;

ALTER TABLE trade_orders
    ADD COLUMN filled_quantity INT UNSIGNED NOT NULL DEFAULT 0 AFTER broker_org_order_no;

ALTER TABLE trade_orders
    ADD COLUMN remaining_quantity INT UNSIGNED NULL AFTER filled_quantity;

ALTER TABLE trade_orders
    ADD COLUMN avg_fill_price DECIMAL(20, 6) NULL AFTER remaining_quantity;

ALTER TABLE trade_orders
    ADD COLUMN closed_at DATETIME(6) NULL AFTER sent_at;

ALTER TABLE trade_orders
    DROP INDEX uq_trade_orders_idempotency;

ALTER TABLE trade_orders
    DROP INDEX uq_trade_orders_broker_no;

ALTER TABLE trade_orders
    ADD UNIQUE KEY uq_trade_orders_account_idempotency (broker, account_no, idempotency_key);

ALTER TABLE trade_orders
    ADD UNIQUE KEY uq_trade_orders_broker_no (broker, account_no, broker_order_no);

ALTER TABLE trade_orders
    ADD KEY idx_trade_orders_strategy_family_date (strategy_family, trade_date);

ALTER TABLE trade_orders
    ADD KEY idx_trade_orders_strategy_date (strategy_name, trade_date);

ALTER TABLE trade_orders
    ADD KEY idx_trade_orders_source_run (source_system, source_run_id);

ALTER TABLE trade_orders
    ADD KEY idx_trade_orders_account_alias (account_alias);

ALTER TABLE trade_orders
    ADD KEY idx_trade_orders_condition (condition_id);

ALTER TABLE trade_fills
    ADD COLUMN broker VARCHAR(16) NOT NULL DEFAULT 'KIS' AFTER order_id;

ALTER TABLE trade_fills
    ADD COLUMN account_no VARCHAR(32) NULL AFTER broker;

ALTER TABLE trade_fills
    ADD COLUMN broker_order_no VARCHAR(64) NULL AFTER account_no;

ALTER TABLE trade_fills
    MODIFY fill_price DECIMAL(20, 6) NOT NULL;

ALTER TABLE trade_fills
    ADD COLUMN gross_amount DECIMAL(20, 2) NULL AFTER fill_price;

ALTER TABLE trade_fills
    MODIFY fee DECIMAL(20, 2) NOT NULL DEFAULT 0;

ALTER TABLE trade_fills
    MODIFY tax DECIMAL(20, 2) NOT NULL DEFAULT 0;

ALTER TABLE trade_fills
    ADD COLUMN settlement_date DATE NULL AFTER tax;

ALTER TABLE trade_fills
    ADD COLUMN raw_payload JSON NULL AFTER fill_time;

ALTER TABLE trade_fills
    ADD KEY idx_trade_fills_broker_order_no (broker, account_no, broker_order_no);

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

UPDATE strategy_account_allocations saa
JOIN trade_accounts ta ON ta.id = saa.trade_account_id
SET saa.strategy_name = 'hagfish_v2',
    saa.strategy_version = '2.6'
WHERE ta.broker = 'KIS'
  AND ta.account_alias IN ('test', 'hagfish')
  AND saa.strategy_family = 'hagfish'
  AND saa.allocation_role = 'PRIMARY'
  AND saa.valid_to IS NULL;
