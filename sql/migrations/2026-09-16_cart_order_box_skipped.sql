-- 2026-09-16: 'skipped' status on cart_order_box.
-- A push that finds the order already shipped, or already carrying a package
-- size, used to leave the row 'assigned'. Those rows sat at the front of every
-- push batch and blocked newer orders. The job now marks them 'skipped'.
-- Like every non-void status, 'skipped' keeps the order out of v_cart_items.
ALTER TABLE inventory.cart_order_box
    MODIFY status ENUM('assigned','no_fit','pushed','void','error','skipped')
        NOT NULL DEFAULT 'assigned',
    ALGORITHM=INSTANT;

-- One-time cleanup of the rows that were already stuck.
UPDATE inventory.cart_order_box b
JOIN shipstation.shipstation_orders o ON o.order_id = b.order_id
SET b.status = 'skipped',
    b.note   = COALESCE(b.note, CONCAT('order is ', o.order_status, ', not awaiting_shipment'))
WHERE b.status = 'assigned'
  AND o.order_status <> 'awaiting_shipment';
