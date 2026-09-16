-- 2026-09-15: mark the mailers and bags.
-- A mailer is not padded the way a box is: the void fill that justifies a
-- customer's padding_per_order goes inside a box, while a mailer ships on the
-- goods alone. The engine therefore tries mailers against the unpadded volume.
-- The name cannot be trusted for this - one of them is a SHIPPING-BAG, not an
-- ENVELOP.
ALTER TABLE inventory.skus
    ADD COLUMN is_mailer tinyint(1) NOT NULL DEFAULT 0 AFTER avoid_box;

UPDATE inventory.skus SET is_mailer = 1
 WHERE sku IN ('PACKAGING-BOX-BEARDBRAND-ENVELOP-BUBBLE-1X1-5X10',
               'PACKAGING-BOX-BEARDBRAND-ENVELOP-BUBBLE-1X1-7.25X12',
               'PACKAGING-BOX-SHIPPING-BAG-BROWN-1X1-9X11.5');
