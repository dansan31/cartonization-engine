CREATE OR REPLACE VIEW inventory.v_cart_items AS
SELECT
    oi.order_id,
    oi.id                AS item_id,
    oi.sku,
    oi.quantity,
    o.order_date,
    o.store_id,
    s.customer_id,
    s.dimensions         AS product_dimensions,
    p.l                  AS product_length_in,
    p.w                  AS product_width_in,
    p.h                  AS product_height_in,
    ROUND(p.l * p.w * p.h, 2)                                                      AS product_volume_cu_in,
    COALESCE(c.padding_per_product, 0)                                             AS padding_per_product,
    ROUND((p.l * p.w * p.h + COALESCE(c.padding_per_product, 0)) * oi.quantity, 2) AS total_volume_cu_in
FROM shipstation.shipstation_orders o
JOIN shipstation.shipstation_order_items oi ON oi.order_id = o.order_id
JOIN inventory.skus s                       ON s.sku = oi.sku
LEFT JOIN inventory.customers c             ON c.customer_id = s.customer_id
CROSS JOIN LATERAL (SELECT REPLACE(LOWER(s.dimensions), ' ', '') AS dims) d
CROSS JOIN LATERAL (
    SELECT
        CASE WHEN d.dims REGEXP '^[0-9.]+x[0-9.]+x[0-9.]+$'
             THEN CAST(SUBSTRING_INDEX(d.dims, 'x', 1) AS DECIMAL(10,3)) END                            AS l,
        CASE WHEN d.dims REGEXP '^[0-9.]+x[0-9.]+x[0-9.]+$'
             THEN CAST(SUBSTRING_INDEX(SUBSTRING_INDEX(d.dims, 'x', 2), 'x', -1) AS DECIMAL(10,3)) END  AS w,
        CASE WHEN d.dims REGEXP '^[0-9.]+x[0-9.]+x[0-9.]+$'
             THEN CAST(SUBSTRING_INDEX(d.dims, 'x', -1) AS DECIMAL(10,3)) END                           AS h
) p
WHERE o.order_status = 'awaiting_shipment'
  AND TRIM(oi.sku) <> ''
  AND o.length_in IS NULL
  AND o.width_in  IS NULL
  AND o.height_in IS NULL
  -- already boxed by the engine: a row in cart_order_box takes the order out of
  -- the queue. Delete the row, or set its status to 'void', to send it back.
  AND NOT EXISTS (
      SELECT 1 FROM inventory.cart_order_box b
      WHERE b.order_id = o.order_id
        AND b.status <> 'void'
  );
