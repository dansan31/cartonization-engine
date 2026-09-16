-- 2026-09-15: results table for the cartonization engine.
-- One row per order that has been boxed. Its presence is what keeps the order
-- out of v_cart_items on the next run, so the engine never boxes an order twice.
CREATE TABLE IF NOT EXISTS inventory.cart_order_box (
    id               bigint        NOT NULL AUTO_INCREMENT,
    order_id         bigint        NOT NULL,
    order_number     varchar(255)      NULL,
    customer_id      varchar(64)       NULL,
    box_sku          varchar(128)      NULL,
    box_dimensions   varchar(64)       NULL,
    box_volume_cu_in decimal(12,2)     NULL,
    order_volume_cu_in decimal(12,2)   NULL,
    max_item_side_in decimal(10,3)     NULL,
    status           enum('assigned','no_fit','pushed','void') NOT NULL DEFAULT 'assigned',
    note             varchar(255)      NULL,
    pushed_at        datetime          NULL,
    created_at       timestamp     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at       timestamp     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY ux_cart_order_box_order (order_id),
    KEY ix_cart_order_box_status (status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
