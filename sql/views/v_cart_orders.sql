CREATE OR REPLACE VIEW inventory.v_cart_orders AS
SELECT
    o.order_number,
    i.order_id,
    i.customer_id,
    COALESCE(c.padding_per_order, 0)  AS padding_per_order,
    SUM(i.line_volume_cu_in)                                           AS raw_volume_cu_in,
    SUM(i.total_volume_cu_in)                                          AS total_volume_cu_in,
    MAX(GREATEST(i.product_length_in, i.product_width_in, i.product_height_in)) AS max_item_side_in,
    ROUND(SUM(i.total_volume_cu_in) + COALESCE(c.padding_per_order, 0), 2) AS total_order_volume_cu_in
FROM inventory.v_cart_items i
JOIN shipstation.shipstation_orders o ON o.order_id = i.order_id
LEFT JOIN inventory.customers c       ON c.customer_id = i.customer_id
GROUP BY o.order_number, i.order_id, i.customer_id, c.padding_per_order;
