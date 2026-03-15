CREATE TABLE IF NOT EXISTS trade_orders (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    idempotency_key VARCHAR(64) NOT NULL,
    broker VARCHAR(16) NOT NULL DEFAULT 'KIS',
    account_no VARCHAR(32) NOT NULL,
    side ENUM('BUY', 'SELL') NOT NULL,
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
    strategy_name VARCHAR(64) NULL,
    reason VARCHAR(128) NULL,
    trade_date DATE NOT NULL,
    broker_order_no VARCHAR(64) NULL,
    broker_org_order_no VARCHAR(64) NULL,
    requested_at DATETIME(6) NOT NULL,
    sent_at DATETIME(6) NULL,
    last_synced_at DATETIME(6) NULL,
    error_code VARCHAR(32) NULL,
    error_message VARCHAR(255) NULL,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
    PRIMARY KEY (id),
    UNIQUE KEY uq_trade_orders_idempotency (idempotency_key),
    UNIQUE KEY uq_trade_orders_broker_no (broker, broker_order_no),
    KEY idx_trade_orders_trade_date_status (trade_date, status),
    KEY idx_trade_orders_strategy_id (strategy_id),
    KEY idx_trade_orders_symbol (symbol)
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
    broker_fill_id VARCHAR(64) NULL,
    side ENUM('BUY', 'SELL') NOT NULL,
    symbol VARCHAR(16) NOT NULL,
    fill_quantity INT UNSIGNED NOT NULL,
    fill_price INT UNSIGNED NOT NULL,
    fee INT UNSIGNED NOT NULL DEFAULT 0,
    tax INT UNSIGNED NOT NULL DEFAULT 0,
    fill_time DATETIME(6) NOT NULL,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    PRIMARY KEY (id),
    UNIQUE KEY uq_trade_fills_order_fill (order_id, broker_fill_id),
    KEY idx_trade_fills_symbol_fill_time (symbol, fill_time),
    CONSTRAINT fk_trade_fills_order
      FOREIGN KEY (order_id) REFERENCES trade_orders(id)
      ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS trade_positions (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    account_no VARCHAR(32) NOT NULL,
    symbol VARCHAR(16) NOT NULL,
    quantity BIGINT NOT NULL DEFAULT 0,
    avg_price DECIMAL(20, 6) NOT NULL DEFAULT 0,
    realized_pnl DECIMAL(20, 6) NOT NULL DEFAULT 0,
    updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
    PRIMARY KEY (id),
    UNIQUE KEY uq_trade_positions_account_symbol (account_no, symbol),
    KEY idx_trade_positions_symbol (symbol)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS trade_account_balances (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    account_no VARCHAR(32) NOT NULL,
    cash_available DECIMAL(20, 2) NOT NULL,
    cash_withdrawable DECIMAL(20, 2) NULL,
    total_value DECIMAL(20, 2) NULL,
    as_of DATETIME(6) NOT NULL,
    source_payload JSON NULL,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    PRIMARY KEY (id),
    UNIQUE KEY uq_trade_account_balances_account_asof (account_no, as_of),
    KEY idx_trade_account_balances_asof (as_of)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
