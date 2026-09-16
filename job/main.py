"""Cartonization engine: pick a shipping box for each order awaiting shipment.

Reads the queue from inventory.v_cart_orders (orders that are awaiting shipment,
have no dimensions in ShipStation, and have not been boxed yet), matches each one
against the boxes in inventory.v_cart_boxes, and writes the result to
inventory.cart_order_box.

Writing a row to cart_order_box is what takes the order out of the queue, so a
re-run never boxes the same order twice. To send an order back through, delete
its row or set the row's status to 'void'.

Box rule: the smallest box whose volume holds the order volume AND whose every
side, compared longest to longest, holds each item on the order.

Environment:
    DB_HOST / DB_SOCKET   one or the other; DB_SOCKET is the Cloud SQL unix socket
    DB_USER, DB_PASS, DB_NAME
    DRY_RUN               "1" to calculate and log without writing (default "0")
    LIMIT                 max orders to process in one run (default 500)

    PUSH_TO_SHIPSTATION   "1" to write the chosen dimensions onto the live orders
                          (default "0" - nothing is sent)
    SS_API_KEY, SS_API_SECRET   ShipStation credentials, needed only to push
    PUSH_LIMIT            max orders to push in one run (default 25); small
                          batches every 15 min rather than one long sweep
    PUSH_PAUSE_SECONDS    pause between ShipStation calls (default 2.0)
    PUSH_ONLY_ORDER       push just this one order number, for testing
"""

import base64
import json
import logging
import os
import sys
import time
import urllib.error
import urllib.request
from decimal import Decimal

import pymysql

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [cartonization] %(message)s",
)
log = logging.getLogger(__name__)

DRY_RUN = os.environ.get("DRY_RUN", "0") == "1"
PUSH_TO_SHIPSTATION = os.environ.get("PUSH_TO_SHIPSTATION", "0") == "1"
LIMIT = int(os.environ.get("LIMIT", "500"))
PUSH_LIMIT = int(os.environ.get("PUSH_LIMIT", "25"))
PUSH_PAUSE_SECONDS = float(os.environ.get("PUSH_PAUSE_SECONDS", "2.0"))
PUSH_ONLY_ORDER = os.environ.get("PUSH_ONLY_ORDER", "").strip()

SS_API = "https://ssapi.shipstation.com"
MISS_SIZE_TAG_ID = int(os.environ.get("MISS_SIZE_TAG_ID", "108524"))  # the miss_size tag


def connect():
    """Open a connection to the inventory database."""
    kwargs = {
        "user": os.environ["DB_USER"],
        "password": os.environ["DB_PASS"],
        "database": os.environ.get("DB_NAME", "inventory"),
        "charset": "utf8mb4",
        "autocommit": False,
        "cursorclass": pymysql.cursors.DictCursor,
    }
    socket = os.environ.get("DB_SOCKET")
    if socket:
        kwargs["unix_socket"] = socket
    else:
        kwargs["host"] = os.environ["DB_HOST"]
        kwargs["port"] = int(os.environ.get("DB_PORT", "3306"))
    return pymysql.connect(**kwargs)


def load_boxes(conn):
    """Every usable box, smallest first, with its longest side."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT sku, box_dimensions, box_volume_cu_in,
                   box_length_in, box_width_in, box_height_in
            FROM v_cart_boxes
            WHERE box_volume_cu_in IS NOT NULL
            ORDER BY box_volume_cu_in ASC
            """
        )
        boxes = cur.fetchall()

    for b in boxes:  # longest to shortest, ready to compare against an item
        b["sides"] = tuple(
            sorted((b["box_length_in"], b["box_width_in"], b["box_height_in"]), reverse=True)
        )
    log.info("loaded %d boxes", len(boxes))
    return boxes


def load_item_sizes(conn, order_ids):
    """Each order's distinct item shapes, as sorted (long, mid, short) triples.

    A box has to hold every item on its own: compared side by side, longest to
    longest, middle to middle, shortest to shortest. Checking only the longest
    side lets a wide flat item through a box it cannot physically enter, which is
    how a 17x17x6 pillow insert was once assigned a 20x16x6 box.
    """
    if not order_ids:
        return {}
    placeholders = ",".join(["%s"] * len(order_ids))
    with conn.cursor() as cur:
        cur.execute(
            f"""
            SELECT DISTINCT order_id, sku,
                   product_length_in AS l, product_width_in AS w, product_height_in AS h
            FROM v_cart_items
            WHERE order_id IN ({placeholders})
            """,
            tuple(order_ids),
        )
        rows = cur.fetchall()

    sizes = {}
    for r in rows:
        if r["l"] is None or r["w"] is None or r["h"] is None:
            sizes.setdefault(r["order_id"], []).append((r["sku"], None))
            continue
        triple = tuple(sorted((r["l"], r["w"], r["h"]), reverse=True))
        sizes.setdefault(r["order_id"], []).append((r["sku"], triple))
    return sizes


def item_fits(item_triple, box_triple):
    """Does one item fit inside one box, turned any way up?"""
    return all(i <= b for i, b in zip(item_triple, box_triple))


def load_queue(conn):
    """Orders waiting to be boxed."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT order_id, order_number, customer_id,
                   total_order_volume_cu_in, max_item_side_in
            FROM v_cart_orders
            ORDER BY order_id
            LIMIT %s
            """,
            (LIMIT,),
        )
        queue = cur.fetchall()
    log.info("queue holds %d orders", len(queue))
    return queue


def choose_box(order, boxes, items):
    """Smallest box that holds the order's volume and every item's shape.

    Returns (box, note). box is None when nothing fits or the order cannot be
    measured; note explains why.
    """
    volume = order["total_order_volume_cu_in"]

    if volume is None:
        return None, "order has an item with no dimensions"

    unmeasured = [sku for sku, triple in items if triple is None]
    if unmeasured or not items:
        return None, f"no dimensions for {unmeasured[0] if unmeasured else 'any item'}"

    for box in boxes:  # already sorted smallest first
        if box["box_volume_cu_in"] < volume:
            continue
        if all(item_fits(triple, box["sides"]) for _, triple in items):
            return box, None

    biggest = max(items, key=lambda it: it[1])[1]
    return None, f"no box holds {volume} cu in and an item of {biggest[0]}x{biggest[1]}x{biggest[2]} in"


def save(conn, order, box, note):
    """Record the decision. The row is what keeps the order out of the queue."""
    status = "assigned" if box else "no_fit"
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO cart_order_box
                (order_id, order_number, customer_id, box_sku, box_dimensions,
                 box_volume_cu_in, order_volume_cu_in, max_item_side_in, status, note)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
                box_sku          = VALUES(box_sku),
                box_dimensions   = VALUES(box_dimensions),
                box_volume_cu_in = VALUES(box_volume_cu_in),
                order_volume_cu_in = VALUES(order_volume_cu_in),
                max_item_side_in = VALUES(max_item_side_in),
                status           = VALUES(status),
                note             = VALUES(note)
            """,
            (
                order["order_id"],
                order["order_number"],
                order["customer_id"],
                box["sku"] if box else None,
                box["box_dimensions"] if box else None,
                box["box_volume_cu_in"] if box else None,
                order["total_order_volume_cu_in"],
                order["max_item_side_in"],
                status,
                note,
            ),
        )


def ss_auth():
    return base64.b64encode(
        f"{os.environ['SS_API_KEY']}:{os.environ['SS_API_SECRET']}".encode()
    ).decode()


def ss_call(path, auth, payload=None):
    """One ShipStation call, waiting out a rate limit once if we hit it."""
    data = json.dumps(payload).encode() if payload is not None else None
    headers = {"Authorization": f"Basic {auth}"}
    if data:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(f"{SS_API}{path}", data=data, headers=headers,
                                 method="POST" if data else "GET")
    for attempt in (1, 2):
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                body = json.loads(resp.read())
                left = resp.headers.get("X-Rate-Limit-Remaining")
                if left is not None and int(left) <= 3:
                    wait = int(resp.headers.get("X-Rate-Limit-Reset", "20"))
                    log.info("rate limit nearly used up; waiting %ss", wait)
                    time.sleep(wait)
                return body
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt == 1:
                wait = int(e.headers.get("X-Rate-Limit-Reset", "20"))
                log.warning("rate limited; waiting %ss", wait)
                time.sleep(wait)
                continue
            raise


def push_one(row, auth):
    """Put the chosen dimensions on one live ShipStation order.

    ShipStation v1 has no partial update: an order is changed by posting the whole
    order back through /orders/createorder, and any field left out is wiped. So
    this reads the live order, changes nothing but the dimensions, and posts that
    same object back. It refuses to touch an order that is no longer awaiting
    shipment, or that already has dimensions, so it can never overwrite a choice
    a person made in the meantime.
    """
    order = ss_call(f"/orders/{row['order_id']}", auth)

    if order.get("orderStatus") != "awaiting_shipment":
        return False, f"order is {order.get('orderStatus')}, not awaiting_shipment"

    live = order.get("dimensions") or {}
    if live.get("length") and live.get("width") and live.get("height"):
        return False, (f"order already has {live['length']}x{live['width']}"
                       f"x{live['height']} in ShipStation")

    l, w, h = [float(x) for x in str(row["box_dimensions"]).lower().split("x")]
    order["dimensions"] = {"units": "inches", "length": l, "width": w, "height": h}

    if DRY_RUN:
        log.info("DRY RUN: would set %sx%sx%s on order %s",
                 l, w, h, row["order_number"])
        return False, "dry run"

    result = ss_call("/orders/createorder", auth, order)

    got = result.get("dimensions") or {}
    if not (got.get("length") == l and got.get("width") == w and got.get("height") == h):
        return False, f"ShipStation returned {got or 'no dimensions'}"
    return True, None


def load_tag_map(conn):
    """box SKU -> (tag id, tag name), from cart_box_tags."""
    with conn.cursor() as cur:
        cur.execute("SELECT box_sku, tag_id, tag_name FROM cart_box_tags")
        return {r["box_sku"]: (r["tag_id"], r["tag_name"]) for r in cur.fetchall()}


def tag_order(order_id, tag_id, auth):
    """Attach one tag to one order.

    /orders/addtag is a small call that touches nothing but the tag, unlike the
    dimensions write which has to repost the whole order.
    """
    ss_call("/orders/addtag", auth, {"orderId": order_id, "tagId": tag_id})


def run_tag_backlog(conn, auth, tags):
    """Tag orders that were pushed before tagging existed, or whose tag failed."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT order_id, order_number, box_sku, box_dimensions "
            "FROM cart_order_box WHERE status='pushed' AND tagged_at IS NULL "
            "ORDER BY pushed_at LIMIT %s",
            (PUSH_LIMIT,),
        )
        rows = cur.fetchall()
    if not rows:
        return

    log.info("tagging %d order(s) pushed earlier without a tag", len(rows))
    for n, row in enumerate(rows):
        tag_id, tag_name = tags.get(row["box_sku"], (MISS_SIZE_TAG_ID, "miss_size"))
        try:
            if not DRY_RUN:
                tag_order(row["order_id"], tag_id, auth)
                with conn.cursor() as cur:
                    cur.execute(
                        "UPDATE cart_order_box SET tag_id=%s, tag_name=%s, tagged_at=NOW() "
                        "WHERE order_id=%s",
                        (tag_id, tag_name, row["order_id"]),
                    )
            log.info("order %s: tagged %s", row["order_number"], tag_name)
        except Exception as e:
            log.error("order %s: tagging failed: %s", row["order_number"], e)
            break
        if n + 1 < len(rows):
            time.sleep(PUSH_PAUSE_SECONDS)


def run_push(conn, auth):
    """Push a small batch of assigned orders, oldest first."""
    tags = load_tag_map(conn)
    sql = """
        SELECT order_id, order_number, box_sku, box_dimensions
        FROM cart_order_box
        WHERE status = 'assigned' AND box_dimensions IS NOT NULL
    """
    params = []
    if PUSH_ONLY_ORDER:
        sql += " AND order_number = %s"
        params.append(PUSH_ONLY_ORDER)
    sql += " ORDER BY created_at LIMIT %s"
    params.append(1 if PUSH_ONLY_ORDER else PUSH_LIMIT)

    with conn.cursor() as cur:
        cur.execute(sql, tuple(params))
        rows = cur.fetchall()

    log.info("pushing %d order(s) to ShipStation", len(rows))
    pushed = skipped = tagged = 0

    failures = 0
    for n, row in enumerate(rows):
        try:
            ok, why = push_one(row, auth)
            failures = 0
        except urllib.error.HTTPError as e:
            # One bad order must not block the rest of the batch. A 404 means the
            # order is gone from ShipStation (deleted there, still awaiting here),
            # which is permanent: park the row so it is not retried every run.
            log.error("order %s: push failed: HTTP %s", row["order_number"], e.code)
            if not DRY_RUN:
                with conn.cursor() as cur:
                    cur.execute(
                        "UPDATE cart_order_box SET status='error', note=%s WHERE order_id=%s",
                        (f"ShipStation returned HTTP {e.code}"
                         + (" - order not found there" if e.code == 404 else ""),
                         row["order_id"]),
                    )
            failures += 1
            if failures >= 3:
                log.error("three failures in a row; leaving the rest for the next run")
                break
            time.sleep(PUSH_PAUSE_SECONDS)
            continue
        except Exception as e:
            log.error("order %s: push failed: %s", row["order_number"], e)
            break  # unknown trouble: stop rather than hammering the API

        if ok:
            pushed += 1
            log.info("order %s: set %s in ShipStation", row["order_number"],
                     row["box_dimensions"])

            # Tag the order with its box size. A box with no tag of its own gets
            # miss_size, because ShipStation's order API can list tags but not
            # create them - new size tags have to be made in the UI.
            tag_id, tag_name = tags.get(row["box_sku"], (MISS_SIZE_TAG_ID, "miss_size"))
            if tag_name == "miss_size":
                log.warning("order %s: no tag for %s; tagging miss_size",
                            row["order_number"], row["box_dimensions"])
            try:
                if not DRY_RUN:
                    tag_order(row["order_id"], tag_id, auth)
                tagged += 1
            except Exception as e:
                tag_id = tag_name = None
                log.error("order %s: tagging failed: %s", row["order_number"], e)

            if not DRY_RUN:
                with conn.cursor() as cur:
                    cur.execute(
                        "UPDATE cart_order_box SET status='pushed', pushed_at=NOW(), "
                        "tag_id=%s, tag_name=%s, tagged_at=IF(%s IS NULL, NULL, NOW()) "
                        "WHERE order_id=%s",
                        (tag_id, tag_name, tag_id, row["order_id"]),
                    )
        else:
            skipped += 1
            log.warning("order %s: not pushed - %s", row["order_number"], why)
            if not DRY_RUN:
                with conn.cursor() as cur:
                    cur.execute(
                        "UPDATE cart_order_box SET note=%s WHERE order_id=%s",
                        (why[:255], row["order_id"]),
                    )

        if n + 1 < len(rows):
            time.sleep(PUSH_PAUSE_SECONDS)

    log.info("push done: %d sent, %d tagged, %d skipped", pushed, tagged, skipped)
    run_tag_backlog(conn, auth, tags)


def main():
    conn = connect()
    assigned = no_fit = 0
    try:
        boxes = load_boxes(conn)
        if not boxes:
            log.error("no boxes available; stopping without writing")
            return 1

        queue = load_queue(conn)
        item_sizes = load_item_sizes(conn, [o["order_id"] for o in queue])

        for order in queue:
            box, note = choose_box(order, boxes, item_sizes.get(order["order_id"], []))
            if box:
                assigned += 1
                log.info(
                    "order %s (%s): %s  [order %s cu in, box %s cu in]",
                    order["order_id"], order["customer_id"], box["sku"],
                    order["total_order_volume_cu_in"], box["box_volume_cu_in"],
                )
            else:
                no_fit += 1
                log.warning("order %s (%s): no box - %s",
                            order["order_id"], order["customer_id"], note)

            if not DRY_RUN:
                save(conn, order, box, note)

        if DRY_RUN:
            conn.rollback()
            log.info("DRY RUN: nothing written")
        else:
            conn.commit()

        if PUSH_TO_SHIPSTATION:
            run_push(conn, ss_auth())
            if DRY_RUN:
                conn.rollback()
            else:
                conn.commit()
        else:
            log.info("PUSH_TO_SHIPSTATION is off; ShipStation untouched")
    except Exception:
        conn.rollback()
        log.exception("run failed; nothing written")
        return 1
    finally:
        conn.close()

    log.info("done: %d assigned, %d with no box", assigned, no_fit)
    return 0


if __name__ == "__main__":
    sys.exit(main())
