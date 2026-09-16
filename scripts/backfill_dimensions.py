"""One-time backfill of package dimensions for orders awaiting shipment.

The length_in / width_in / height_in columns on shipstation_orders were added on
2026-09-15 and deliberately not backfilled, so an empty value means "never synced
since then", not "no box in ShipStation". The cartonization queue reads those
columns, so it cannot tell the two apart. This fills them in for the orders that
are awaiting shipment, and only those.

Reads the ShipStation order list a page at a time, then updates matching rows.
Orders ShipStation reports without dimensions are left alone rather than being
written as zero.

Run with --dry-run first. Credentials come from Secret Manager; the database is
reached through cloud-sql-proxy.
"""

import argparse
import base64
import json
import logging
import os
import sys
import time
import urllib.error
import urllib.request

import pymysql

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

API = "https://ssapi.shipstation.com"
PAGE_SIZE = 100          # small pages, several calls: easier on the rate limit
PAUSE_SECONDS = 2.0      # ShipStation allows 40 calls/minute


def api_get(path, auth):
    """One GET against ShipStation, retrying once when rate limited."""
    req = urllib.request.Request(f"{API}{path}", headers={"Authorization": f"Basic {auth}"})
    for attempt in (1, 2):
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                remaining = resp.headers.get("X-Rate-Limit-Remaining")
                if remaining is not None and int(remaining) <= 3:
                    wait = int(resp.headers.get("X-Rate-Limit-Reset", "20"))
                    log.info("rate limit nearly used up; waiting %ss", wait)
                    time.sleep(wait)
                return json.loads(resp.read())
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt == 1:
                wait = int(e.headers.get("X-Rate-Limit-Reset", "20"))
                log.warning("rate limited; waiting %ss", wait)
                time.sleep(wait)
                continue
            raise


def to_inches(dims):
    """ShipStation dimensions object -> (length, width, height) in inches."""
    if not dims:
        return None
    try:
        l, w, h = float(dims["length"]), float(dims["width"]), float(dims["height"])
    except (KeyError, TypeError, ValueError):
        return None
    if not (l and w and h):
        return None
    if str(dims.get("units", "")).lower().startswith("cent"):
        l, w, h = l / 2.54, w / 2.54, h / 2.54
    return round(l, 2), round(w, 2), round(h, 2)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="report without writing")
    args = ap.parse_args()

    auth = base64.b64encode(
        f"{os.environ['SS_API_KEY']}:{os.environ['SS_API_SECRET']}".encode()
    ).decode()

    conn = pymysql.connect(
        unix_socket=os.environ["DB_SOCKET"],
        user=os.environ["DB_USER"],
        password=os.environ["DB_PASS"],
        database="shipstation",
        charset="utf8mb4",
        autocommit=False,
        cursorclass=pymysql.cursors.DictCursor,
    )

    with conn.cursor() as cur:
        cur.execute(
            "SELECT order_id FROM shipstation_orders "
            "WHERE order_status='awaiting_shipment' AND length_in IS NULL"
        )
        wanted = {r["order_id"] for r in cur.fetchall()}
    log.info("%d awaiting-shipment orders have no dimensions stored", len(wanted))

    page, pages, seen, updated, no_dims = 1, 1, 0, 0, 0
    try:
        while page <= pages:
            data = api_get(
                f"/orders?orderStatus=awaiting_shipment&page={page}&pageSize={PAGE_SIZE}",
                auth,
            )
            pages = data.get("pages", 1)
            orders = data.get("orders", [])
            log.info("page %d of %d: %d orders", page, pages, len(orders))

            for o in orders:
                oid = o.get("orderId")
                if oid not in wanted:
                    continue
                seen += 1
                dims = to_inches(o.get("dimensions"))
                if not dims:
                    no_dims += 1
                    continue
                updated += 1
                log.info("order %s (%s): %s x %s x %s in",
                         oid, o.get("orderNumber"), *dims)
                if not args.dry_run:
                    with conn.cursor() as cur:
                        cur.execute(
                            "UPDATE shipstation_orders "
                            "SET length_in=%s, width_in=%s, height_in=%s "
                            "WHERE order_id=%s AND order_status='awaiting_shipment'",
                            (*dims, oid),
                        )

            page += 1
            if page <= pages:
                time.sleep(PAUSE_SECONDS)

        if args.dry_run:
            conn.rollback()
            log.info("DRY RUN: nothing written")
        else:
            conn.commit()
    except Exception:
        conn.rollback()
        log.exception("backfill failed; nothing written")
        return 1
    finally:
        conn.close()

    log.info("matched %d of %d; %d had dimensions, %d had none in ShipStation",
             seen, len(wanted), updated, no_dims)
    return 0


if __name__ == "__main__":
    sys.exit(main())
