# Shard inventory

The shard count the OpenSearch schema produces on each inventory, and the heap
budget it lands in. `deploy/test_provisioning.py` recomputes every number here
from the role defaults and both inventories, and fails if the two drift.

## Arithmetic

A rollover family holds `delete_days // rollover_days + 1` backing indices at
full retention, and each backing index costs `primaries x (1 + replicas)`
shards. The two date-named Templates-page bucket families follow the same
arithmetic with the index period in place of the rollover period: four day
indices of the five-minute series and three month indices of the hourly series
at full retention, one replica each.

An earlier plan quoted 45 shards. That figure is the two storage-tier log
families at one primary and full retention, and it counts nothing else. The test
suite reproduces it, and the 135 the same two families cost at three primaries.

## Totals

| | `inventory.yml` | `inventory.epn.yml` |
|---|---|---|
| Storage primaries | 1 | 3 |
| Workers | 2 | 3 |
| At first bootstrap, before the Templates page | 33 | 46 |
| At first bootstrap, now | **41** | **54** |
| At full retention, before the Templates page | 92 | 191 |
| At full retention, now | **112** | **211** |

The two fixed Templates-page indices add four shards; the bucket families add
four at first bootstrap and sixteen at full retention.

## Heap budget

On the farm only the shards pinned by `index.routing.allocation.require.role=storage`
consume storage-tier heap: 179 of the 211 at full retention. The three storage
nodes carry an 8 GB heap each, so at the repository's rule of roughly 20 shards
per gigabyte the budget is about 480 shards. The inventory uses 37 percent of
it and the Templates-page indices use 4 percent.

`inventory.yml` is tighter for a reason that predates the Templates page. Its
three storage nodes carry 1 GB heaps, a budget of about 60 shards, and the
storage-pinned inventory at full retention is 89, or 69 before the Templates
page. The staging cluster is over that guideline once the 56-day InfoLogger
retention fills, with or without the Templates page. At first bootstrap it is
38 shards and inside the budget.
