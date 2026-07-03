# `updated_at` policy

**Date**: 2026-07-03 04:00 PDT

## TL;DR

`updated_at` is a **sync cursor, not an audit artifact.** It exists schema-wide as the cursor for the Last-Write-Wins (LWW) sync protocol between the mobile client and the appliance. Apply triggers (or route-side `SET updated_at = NOW()`) only where the column is actually load-bearing. Don't blanket-trigger every table.

## The rule

For any user-owned table:

1. **If the table has UPDATE routes that bump `updated_at = NOW()` explicitly:** no trigger needed. The route is responsible for the bump.
2. **If the table has UPDATE routes that DON'T bump `updated_at`:** add either a `BEFORE UPDATE` trigger calling `public.update_updated_at()` OR amend the route to bump `updated_at`. The trigger is the belt; the route bump is the suspenders — pick one and be consistent within a table.
3. **If the table has no UPDATE paths in the application:** no trigger needed until the first UPDATE path is added. Whoever adds it chooses option 1 or 2 before merging. CI doesn't enforce this today; reviewer attention does.

## Current trigger inventory

The schema (`Infrastructure/init/docker-init-home/02-home_schema.sql`) declares `update_updated_at()` triggers on exactly four tables:

| Table | Why it has a trigger |
|-------|----------------------|
| `health_family_history` | Defensive — no UPDATE path today, but an edit-of-life surface likely to gain `PUT` routes |
| `health_social_history` | Same |
| `health_surgical_history` | Same |
| `dietary_settings` | Mixed app + trigger maintenance |

Every other table with an `updated_at` column either bumps it in its UPDATE routes or has no UPDATE path.

## Verifying

```bash
# Drift check: regenerate the reference snapshot, compare against a live snapshot.
bash DataModel3/generate_reference_snapshot.sh
python DataModel3/compare_full.py <live-snapshot.txt>
```

The TRIGGERS section of the diff should show 0 differences.

## See also

- [TIMESTAMPS.md](TIMESTAMPS.md) — UTC-at-rest and per-user timezone handling; `synced_at` and `updated_at` become load-bearing when mobile sync is active.
