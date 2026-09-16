-- 2026-09-15: flag SKUs that ship without a box (cartonization engine)
ALTER TABLE inventory.skus
    ADD COLUMN avoid_box tinyint(1) NOT NULL DEFAULT 0 AFTER dimensions;
