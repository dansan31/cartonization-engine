# cartonization-engine

Code to assign boxes to orders based on the boxes dimensions and the SKUs dimensions.

An order awaiting shipment with no package size gets one: the engine adds up what is
in the order, finds the smallest box that holds it, records the choice, and writes the
size back to ShipStation.

## How it runs

A Cloud Run job, `cartonization-engine` in `armbrust-3pl` / `us-central1`, every 5
minutes at :03, :08, :13 and so on. Those offsets keep it clear of the inventory
movements sync, which holds locks on the order items table for over a minute.

Run it by hand at any time:

```bash
gcloud run jobs execute cartonization-engine --region=us-central1
```

Stop it without deleting anything:

```bash
gcloud scheduler jobs pause cartonization-engine-job --location=us-central1
```

**Pause it before deploying.** A scheduled run once fired mid-deploy, and the old
image quietly redid the work the new one had just corrected.

## The pieces

### Views (`sql/views/`, in the `inventory` schema)

| View | One row per | Holds |
|---|---|---|
| `v_cart_items` | order item | SKU, quantity, product dimensions split into length/width/height, volume, padding per product, and the line total |
| `v_cart_orders` | order | customer, padding per order, total volume, and the longest side of any item on it |
| `v_cart_boxes` | box SKU | dimensions split out, plus volume |

`v_cart_items` is the queue. An item reaches it only if the order is awaiting
shipment, has no package size in ShipStation, and has not been boxed yet, and only if
the SKU exists in the SKU master. Blank and unknown SKUs are left out, so an order can
appear with fewer lines than it really has.

`v_cart_boxes` reads the SKUs classified as `Shipping Box` that are active and do not
have `avoid_box` set.

**`skus.avoid_box` means "not meant for fulfillment"**, not "out of stock". Those boxes
are stocked and used for other purposes, so the engine must never choose them. Setting
the flag takes a box out of play on the next run, with no deploy. 17 are flagged today,
including the three mailers and the sizes the engine once used most: 7X4X3, 5X5X3 and
9X6X2. The smallest box available is 6X4X4.

**`skus.is_mailer`** marks the mailers and bags. A mailer is judged on the goods alone,
without the customer's padding: that padding is void fill, which goes around goods
inside a box, and a mailer ships without it. Judging mailers on the padded volume made
a 1.8 cu in order look like 81.8 and pushed it into a box. Their third dimension is a
policy number - how thick you are willing to pack one, currently 1.5 in - not a
measurement, since a mailer bulges to fit. All three are flagged `avoid_box` today, so
this logic is dormant until one is put back in play.

### The job (`job/`)

`main.py` picks a box, records it, and optionally pushes it. `deploy.sh` ships it,
`run-local.sh` runs it from a laptop through cloud-sql-proxy.

**How a box is chosen.** The smallest box whose volume holds the order's total, and
whose three sides — compared longest to longest, middle to middle, shortest to
shortest — hold every item on the order. The second test matters: comparing only the
longest side once put a 17x17x6 pillow insert into a 20x16x6 box, which it cannot
physically enter.

This checks that each item fits **on its own**. It does not work out whether several
items fit together, so volume is still doing most of the work. Padding per product and
per order, on the customers table, is the allowance for that; both are currently 0 for
every customer, which means no slack at all.

**Writing back to ShipStation.** ShipStation's v1 API has no partial update, so the job
reads the whole order, changes nothing but the dimensions, and posts it back. It
refuses any order that is no longer awaiting shipment, or that already has dimensions,
so a size a packer set by hand is never overwritten. Ten orders per run, a pause
between calls, and an order that fails is parked rather than blocking the rest.

### Tags

After the dimensions are written, the order is tagged with its box size using
`/orders/addtag`, a small call that touches nothing else on the order.

`cart_box_tags` maps box SKU to ShipStation tag. It is a table rather than a name
match at run time, because a tag renamed in ShipStation would silently stop the
tagging, and the mailers need a tag whose name does not match their dimensions.

**A box with no row in that map is tagged `miss_size`.** ShipStation's order API can
list tags but not create them, so a new size tag has to be made in the UI first, then
added to `cart_box_tags`. **All 24 usable boxes are mapped today**, so `miss_size` only
appears when a new box size arrives without one.

### Results table (`inventory.cart_order_box`)

One row per order, and that row is what keeps the order out of the queue.

| status | Meaning |
|---|---|
| `assigned` | a box was chosen, not yet sent to ShipStation |
| `pushed` | the size is on the live order |
| `no_fit` | nothing fits, or an item has no dimensions; the reason is in `note` |
| `error` | ShipStation refused; the reason is in `note` |
| `void` | ignored, so the order goes back in the queue |

`tag_id`, `tag_name` and `tagged_at` record what was tagged. A pushed row with no
`tagged_at` is picked up by the catch-up pass on the next run.

To box an order again, delete its row or set the status to `void`.

### Settings

| Variable | Default | Does |
|---|---|---|
| `DRY_RUN` | `0` | `1` works everything out and writes nothing, to the database or ShipStation |
| `LIMIT` | `500` | orders boxed per run |
| `PUSH_TO_SHIPSTATION` | `0` in code, `1` deployed | whether to write sizes to live orders |
| `PUSH_LIMIT` | `25` in code, `10` deployed | orders pushed per run |
| `PUSH_PAUSE_SECONDS` | `2.0` | pause between ShipStation calls |
| `PUSH_ONLY_ORDER` | — | push one order number only, for testing |
| `REBOX` | `0` | `1` runs the rebox pass (below); leave off on ordinary runs |
| `MISS_SIZE_TAG_ID` | `108524` | the `miss_size` tag |

Credentials come from Secret Manager, never from the code.

### Reboxing after a size is retired

Flagging a box `avoid_box` stops it being chosen again, but orders already pushed keep
the size they were given. `REBOX=1` re-measures those orders, moves them to the best
box still available, and swaps the tag.

It overwrites an order **only when ShipStation's dimensions are still exactly the ones
we wrote**. If a person has touched the package since, the order is skipped - the same
rule that stops the ordinary push overwriting a packer's choice.

It measures from the order items rather than the stored row, because the queue views
exclude orders that already have a size, and a stored volume predates any padding or
SKU dimension changed since.

```bash
REBOX=1 DRY_RUN=1 ./run-local.sh   # see what would move
REBOX=1 ./run-local.sh             # do it
```

### `scripts/backfill_dimensions.py`

A one-off, kept because the problem can return. The dimension columns on
`shipstation_orders` were added without backfilling, so an empty value meant "not
synced since the columns were added", not "no box". The queue could not tell the
difference: of 197 orders it offered up, 172 already had a box in ShipStation. This
fills in the real values for orders awaiting shipment. Run it with `--dry-run` first.

## Everyday jobs

**A new box size arrives.** Add the SKU with `classification = 'Shipping Box'` and its
dimensions as `LxWxH`. Create the matching tag in the ShipStation UI, then add a row to
`cart_box_tags`. Until that row exists the box is used but tagged `miss_size`.

**A box should stop being used.** Set `skus.avoid_box = 1`. It leaves the running on the
next run, no deploy. Then run the rebox pass if orders already went out with it.

**An order needs boxing again.** Delete its `cart_order_box` row, or set the status to
`void`. It only returns to the queue if it still has no size in ShipStation.

**Stop the engine.** `gcloud scheduler jobs pause cartonization-engine-job
--location=us-central1`. Resume with `resume`.

**Change how tight the fit is.** `padding_per_product` and `padding_per_order` on the
customers table. Both feed the arithmetic live. BeardBrand carries 80 cu in per order
and BestSelf 1.75 per product; everyone else is 0, meaning no slack at all.

## Known gaps

- **Pillows are not vacuum-sealed yet.** Until they are, PILLOWD dimensions understate
  the real packed size and its boxes will run small. Updating the SKU dimensions when
  sealing starts is the whole fix.
- **Mailers are flagged out of use.** The logic for them is built - they are judged on
  the unpadded volume, and their depth is a policy number - but all three carry
  `avoid_box`, so it lies dormant until one is put back in play.
- **Every box belongs to `3PLFOUNDERS`**, so boxes cannot be matched to a customer.
- **Unknown SKUs are skipped silently.** An order is boxed on only the lines whose SKUs
  exist in the master, so an order can be measured short. This is deliberate.
- **Fit is per item, not a packing plan.** Each item is checked against the box on its
  own; nothing works out whether they all fit together. Padding is the allowance for
  that.
- **ShipStation fills in some sizes itself**, so orders often acquire a package between
  the engine choosing a box and pushing it. The push treats any existing size as
  someone else's decision and leaves it alone.
