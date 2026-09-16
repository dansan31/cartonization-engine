-- 2026-09-15: box SKU -> ShipStation tag.
-- Kept as a table rather than matching tag names at run time: a tag renamed in
-- ShipStation would silently stop the tagging, and some boxes need a tag whose
-- name does not match their dimensions (the mailers). A box with no row here is
-- tagged miss_size instead.
CREATE TABLE IF NOT EXISTS inventory.cart_box_tags (
    box_sku     varchar(128) NOT NULL,
    tag_id      int          NOT NULL,
    tag_name    varchar(128)     NULL,
    created_at  timestamp    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (box_sku),
    KEY ix_cart_box_tags_tag (tag_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

ALTER TABLE inventory.cart_order_box
    ADD COLUMN tag_id    int          NULL AFTER box_volume_cu_in,
    ADD COLUMN tag_name  varchar(128) NULL AFTER tag_id,
    ADD COLUMN tagged_at datetime     NULL AFTER pushed_at;
