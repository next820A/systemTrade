UPDATE trade_accounts
SET account_role = 'STRATEGY',
    purpose = 'halfrise_v2 legacy-close 알고리즘 전용 운용 계좌. 계좌번호 바인딩 후 즉시 주문 의도 기록 가능.'
WHERE broker = 'KIS'
  AND account_alias = 'halfrise';

UPDATE trade_accounts
SET account_status = 'ACTIVE',
    is_active = 1
WHERE broker = 'KIS'
  AND account_alias = 'halfrise'
  AND account_no IS NOT NULL
  AND account_product_code IS NOT NULL;

INSERT INTO strategy_account_allocations (
    strategy_family,
    strategy_name,
    strategy_version,
    trade_account_id,
    allocation_role,
    is_active,
    valid_from
)
SELECT 'halfrise', 'halfrise_v2', '2.0', ta.id, 'PRIMARY', 1, CURRENT_DATE
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

UPDATE strategy_account_allocations saa
JOIN trade_accounts ta ON ta.id = saa.trade_account_id
SET saa.strategy_name = 'halfrise_v2',
    saa.strategy_version = '2.0',
    saa.is_active = 1,
    saa.valid_from = COALESCE(saa.valid_from, CURRENT_DATE)
WHERE ta.broker = 'KIS'
  AND ta.account_alias = 'halfrise'
  AND saa.strategy_family = 'halfrise'
  AND saa.allocation_role = 'PRIMARY'
  AND saa.valid_to IS NULL;
