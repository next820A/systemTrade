CREATE TABLE IF NOT EXISTS trade_accounts (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    broker VARCHAR(16) NOT NULL DEFAULT 'KIS',
    account_alias VARCHAR(64) NOT NULL,
    account_no VARCHAR(32) NOT NULL,
    account_product_code VARCHAR(8) NOT NULL,
    purpose VARCHAR(128) NULL,
    is_active TINYINT(1) NOT NULL DEFAULT 1,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
    PRIMARY KEY (id),
    UNIQUE KEY uq_trade_accounts_alias (broker, account_alias),
    UNIQUE KEY uq_trade_accounts_account (broker, account_no, account_product_code)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS strategy_account_allocations (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    strategy_family VARCHAR(64) NOT NULL,
    strategy_id INT NULL,
    strategy_name VARCHAR(64) NULL,
    strategy_version VARCHAR(64) NULL,
    trade_account_id BIGINT UNSIGNED NOT NULL,
    allocation_role ENUM('PRIMARY', 'BUY', 'SELL') NOT NULL DEFAULT 'PRIMARY',
    is_active TINYINT(1) NOT NULL DEFAULT 1,
    valid_from DATE NULL,
    valid_to DATE NULL,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
    PRIMARY KEY (id),
    KEY idx_strategy_account_allocations_family_active (strategy_family, is_active),
    KEY idx_strategy_account_allocations_account (trade_account_id),
    CONSTRAINT fk_strategy_account_allocations_account
      FOREIGN KEY (trade_account_id) REFERENCES trade_accounts(id)
      ON DELETE RESTRICT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS trade_orders (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    idempotency_key VARCHAR(191) NOT NULL,
    source_system VARCHAR(32) NOT NULL DEFAULT 'systemTrade',
    source_run_id VARCHAR(128) NULL,
    broker VARCHAR(16) NOT NULL DEFAULT 'KIS',
    trade_account_id BIGINT UNSIGNED NULL,
    account_no VARCHAR(32) NOT NULL,
    account_product_code VARCHAR(8) NULL,
    account_alias VARCHAR(64) NULL,
    side ENUM('BUY', 'SELL') NOT NULL,
    source_symbol VARCHAR(32) NULL,
    symbol VARCHAR(16) NOT NULL,
    order_type ENUM('MARKET', 'LIMIT') NOT NULL,
    quantity INT UNSIGNED NOT NULL,
    price INT UNSIGNED NULL,
    status ENUM(
        'CREATED',
        'SUBMITTING',
        'SENT',
        'PARTIALLY_FILLED',
        'FILLED',
        'CANCEL_REQUESTED',
        'CANCELED',
        'REJECTED',
        'FAILED'
    ) NOT NULL DEFAULT 'CREATED',
    strategy_id INT NULL,
    strategy_family VARCHAR(64) NULL,
    strategy_name VARCHAR(64) NULL,
    strategy_version VARCHAR(64) NULL,
    signal_id VARCHAR(128) NULL,
    condition_id VARCHAR(128) NULL,
    condition_version VARCHAR(64) NULL,
    condition_snapshot JSON NULL,
    intent_metadata JSON NULL,
    reason VARCHAR(255) NULL,
    trade_date DATE NOT NULL,
    broker_order_no VARCHAR(64) NULL,
    broker_org_order_no VARCHAR(64) NULL,
    filled_quantity INT UNSIGNED NOT NULL DEFAULT 0,
    remaining_quantity INT UNSIGNED NULL,
    avg_fill_price DECIMAL(20, 6) NULL,
    requested_at DATETIME(6) NOT NULL,
    sent_at DATETIME(6) NULL,
    closed_at DATETIME(6) NULL,
    last_synced_at DATETIME(6) NULL,
    error_code VARCHAR(32) NULL,
    error_message VARCHAR(255) NULL,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
    PRIMARY KEY (id),
    UNIQUE KEY uq_trade_orders_account_idempotency (broker, account_no, idempotency_key),
    UNIQUE KEY uq_trade_orders_broker_no (broker, account_no, broker_order_no),
    KEY idx_trade_orders_trade_date_status (trade_date, status),
    KEY idx_trade_orders_strategy_id (strategy_id),
    KEY idx_trade_orders_strategy_family_date (strategy_family, trade_date),
    KEY idx_trade_orders_strategy_date (strategy_name, trade_date),
    KEY idx_trade_orders_source_run (source_system, source_run_id),
    KEY idx_trade_orders_account_alias (account_alias),
    KEY idx_trade_orders_condition (condition_id),
    KEY idx_trade_orders_symbol (symbol),
    CONSTRAINT fk_trade_orders_trade_account
      FOREIGN KEY (trade_account_id) REFERENCES trade_accounts(id)
      ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS trade_order_events (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    order_id BIGINT UNSIGNED NOT NULL,
    event_type VARCHAR(32) NOT NULL,
    status ENUM(
        'CREATED',
        'SUBMITTING',
        'SENT',
        'PARTIALLY_FILLED',
        'FILLED',
        'CANCEL_REQUESTED',
        'CANCELED',
        'REJECTED',
        'FAILED'
    ) NULL,
    note VARCHAR(255) NULL,
    request_payload JSON NULL,
    response_payload JSON NULL,
    event_at DATETIME(6) NOT NULL,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    PRIMARY KEY (id),
    KEY idx_trade_order_events_order_id (order_id),
    KEY idx_trade_order_events_event_at (event_at),
    CONSTRAINT fk_trade_order_events_order
      FOREIGN KEY (order_id) REFERENCES trade_orders(id)
      ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS trade_fills (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    order_id BIGINT UNSIGNED NOT NULL,
    broker VARCHAR(16) NOT NULL DEFAULT 'KIS',
    account_no VARCHAR(32) NOT NULL,
    broker_order_no VARCHAR(64) NULL,
    broker_fill_id VARCHAR(64) NULL,
    side ENUM('BUY', 'SELL') NOT NULL,
    symbol VARCHAR(16) NOT NULL,
    fill_quantity INT UNSIGNED NOT NULL,
    fill_price DECIMAL(20, 6) NOT NULL,
    gross_amount DECIMAL(20, 2) NULL,
    fee DECIMAL(20, 2) NOT NULL DEFAULT 0,
    tax DECIMAL(20, 2) NOT NULL DEFAULT 0,
    settlement_date DATE NULL,
    fill_time DATETIME(6) NOT NULL,
    raw_payload JSON NULL,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    PRIMARY KEY (id),
    UNIQUE KEY uq_trade_fills_order_fill (order_id, broker_fill_id),
    KEY idx_trade_fills_broker_order_no (broker, account_no, broker_order_no),
    KEY idx_trade_fills_symbol_fill_time (symbol, fill_time),
    CONSTRAINT fk_trade_fills_order
      FOREIGN KEY (order_id) REFERENCES trade_orders(id)
      ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS account_query_requests (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    broker VARCHAR(16) NOT NULL DEFAULT 'KIS',
    trade_account_id BIGINT UNSIGNED NULL,
    account_no VARCHAR(32) NOT NULL,
    account_product_code VARCHAR(8) NULL,
    account_alias VARCHAR(64) NULL,
    query_type ENUM(
        'BALANCE',
        'BUYING_POWER',
        'SELLABLE',
        'CANCELABLE_ORDERS',
        'DAILY_CCLD'
    ) NOT NULL,
    symbol VARCHAR(16) NULL,
    side ENUM('BUY', 'SELL') NULL,
    order_type ENUM('MARKET', 'LIMIT') NULL,
    price INT UNSIGNED NULL,
    start_date DATE NULL,
    end_date DATE NULL,
    status ENUM('REQUESTED', 'SUCCEEDED', 'FAILED') NOT NULL DEFAULT 'REQUESTED',
    rt_cd VARCHAR(16) NULL,
    msg_cd VARCHAR(32) NULL,
    msg1 VARCHAR(255) NULL,
    request_payload JSON NULL,
    response_payload JSON NULL,
    requested_at DATETIME(6) NOT NULL,
    responded_at DATETIME(6) NULL,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    PRIMARY KEY (id),
    KEY idx_account_query_requests_account_type_time (account_no, query_type, requested_at),
    KEY idx_account_query_requests_account_alias_time (account_alias, requested_at),
    KEY idx_account_query_requests_symbol_time (symbol, requested_at),
    CONSTRAINT fk_account_query_requests_trade_account
      FOREIGN KEY (trade_account_id) REFERENCES trade_accounts(id)
      ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS account_balance_summaries (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    query_request_id BIGINT UNSIGNED NOT NULL,
    account_no VARCHAR(32) NOT NULL,
    cash_total DECIMAL(20, 2) NULL,
    cash_available DECIMAL(20, 2) NULL,
    cash_withdrawable DECIMAL(20, 2) NULL,
    purchase_amount DECIMAL(20, 2) NULL,
    securities_value DECIMAL(20, 2) NULL,
    total_value DECIMAL(20, 2) NULL,
    eval_pnl DECIMAL(20, 2) NULL,
    as_of DATETIME(6) NOT NULL,
    raw_payload JSON NULL,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    PRIMARY KEY (id),
    UNIQUE KEY uq_account_balance_summary_query (query_request_id),
    KEY idx_account_balance_summaries_account_asof (account_no, as_of),
    CONSTRAINT fk_account_balance_summaries_query
      FOREIGN KEY (query_request_id) REFERENCES account_query_requests(id)
      ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS account_holding_snapshots (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    query_request_id BIGINT UNSIGNED NOT NULL,
    account_no VARCHAR(32) NOT NULL,
    symbol VARCHAR(16) NOT NULL,
    product_name VARCHAR(128) NULL,
    quantity BIGINT NOT NULL DEFAULT 0,
    available_quantity BIGINT NULL,
    avg_price DECIMAL(20, 6) NULL,
    current_price DECIMAL(20, 6) NULL,
    purchase_amount DECIMAL(20, 2) NULL,
    evaluation_amount DECIMAL(20, 2) NULL,
    pnl DECIMAL(20, 2) NULL,
    pnl_rate DECIMAL(12, 6) NULL,
    as_of DATETIME(6) NOT NULL,
    raw_payload JSON NULL,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    PRIMARY KEY (id),
    UNIQUE KEY uq_account_holding_query_symbol (query_request_id, symbol),
    KEY idx_account_holding_snapshots_account_symbol_asof (account_no, symbol, as_of),
    CONSTRAINT fk_account_holding_snapshots_query
      FOREIGN KEY (query_request_id) REFERENCES account_query_requests(id)
      ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS order_capacity_snapshots (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    query_request_id BIGINT UNSIGNED NOT NULL,
    account_no VARCHAR(32) NOT NULL,
    capacity_type ENUM('BUYING_POWER', 'SELLABLE') NOT NULL,
    symbol VARCHAR(16) NOT NULL,
    order_type ENUM('MARKET', 'LIMIT') NULL,
    price INT UNSIGNED NULL,
    max_buy_amount DECIMAL(20, 2) NULL,
    max_buy_quantity BIGINT NULL,
    order_possible_cash DECIMAL(20, 2) NULL,
    order_possible_quantity BIGINT NULL,
    sellable_quantity BIGINT NULL,
    holding_quantity BIGINT NULL,
    current_price DECIMAL(20, 6) NULL,
    as_of DATETIME(6) NOT NULL,
    raw_payload JSON NULL,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    PRIMARY KEY (id),
    UNIQUE KEY uq_order_capacity_query (query_request_id),
    KEY idx_order_capacity_account_symbol_asof (account_no, symbol, as_of),
    CONSTRAINT fk_order_capacity_snapshots_query
      FOREIGN KEY (query_request_id) REFERENCES account_query_requests(id)
      ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS cancelable_order_snapshots (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    query_request_id BIGINT UNSIGNED NOT NULL,
    account_no VARCHAR(32) NOT NULL,
    broker_order_no VARCHAR(64) NULL,
    broker_org_order_no VARCHAR(64) NULL,
    side ENUM('BUY', 'SELL') NULL,
    symbol VARCHAR(16) NULL,
    order_type VARCHAR(32) NULL,
    order_quantity BIGINT NULL,
    unfilled_quantity BIGINT NULL,
    order_price DECIMAL(20, 6) NULL,
    ordered_at DATETIME(6) NULL,
    raw_payload JSON NULL,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    PRIMARY KEY (id),
    KEY idx_cancelable_order_snapshots_query (query_request_id),
    KEY idx_cancelable_order_snapshots_broker_no (account_no, broker_order_no),
    CONSTRAINT fk_cancelable_order_snapshots_query
      FOREIGN KEY (query_request_id) REFERENCES account_query_requests(id)
      ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS broker_execution_snapshots (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    query_request_id BIGINT UNSIGNED NOT NULL,
    account_no VARCHAR(32) NOT NULL,
    broker_order_no VARCHAR(64) NULL,
    broker_fill_id VARCHAR(64) NULL,
    side ENUM('BUY', 'SELL') NULL,
    symbol VARCHAR(16) NULL,
    order_quantity BIGINT NULL,
    filled_quantity BIGINT NULL,
    fill_price DECIMAL(20, 6) NULL,
    fill_amount DECIMAL(20, 2) NULL,
    ordered_at DATETIME(6) NULL,
    filled_at DATETIME(6) NULL,
    raw_payload JSON NULL,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    PRIMARY KEY (id),
    KEY idx_broker_execution_snapshots_query (query_request_id),
    KEY idx_broker_execution_snapshots_broker_no (account_no, broker_order_no),
    KEY idx_broker_execution_snapshots_symbol_time (symbol, filled_at),
    CONSTRAINT fk_broker_execution_snapshots_query
      FOREIGN KEY (query_request_id) REFERENCES account_query_requests(id)
      ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS account_position_cache (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    trade_account_id BIGINT UNSIGNED NULL,
    account_no VARCHAR(32) NOT NULL,
    account_alias VARCHAR(64) NULL,
    symbol VARCHAR(16) NOT NULL,
    quantity BIGINT NOT NULL DEFAULT 0,
    available_quantity BIGINT NULL,
    avg_price DECIMAL(20, 6) NULL,
    updated_from_query_id BIGINT UNSIGNED NULL,
    updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
    PRIMARY KEY (id),
    UNIQUE KEY uq_account_position_cache_account_symbol (account_no, symbol),
    KEY idx_account_position_cache_account_alias (account_alias),
    KEY idx_account_position_cache_symbol (symbol),
    CONSTRAINT fk_account_position_cache_account
      FOREIGN KEY (trade_account_id) REFERENCES trade_accounts(id)
      ON DELETE SET NULL,
    CONSTRAINT fk_account_position_cache_query
      FOREIGN KEY (updated_from_query_id) REFERENCES account_query_requests(id)
      ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
