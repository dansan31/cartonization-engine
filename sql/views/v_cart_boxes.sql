CREATE OR REPLACE VIEW inventory.v_cart_boxes AS
SELECT
    s.sku,
    s.customer_id,
    s.description,
    s.dimensions         AS box_dimensions,
    p.l                  AS box_length_in,
    p.w                  AS box_width_in,
    p.h                  AS box_height_in,
    ROUND(p.l * p.w * p.h, 2) AS box_volume_cu_in
FROM inventory.skus s
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
WHERE s.classification = 'Shipping Box'
  AND s.avoid_box = 0
  AND s.is_active = 1;
