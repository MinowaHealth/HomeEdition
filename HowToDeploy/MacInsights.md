# MacInsights.md — Notes on the Docker Desktop → OrbStack Swap

**Date**: 2026-04-09 00:00 UTC
**Context**: Companion notes to the string replacement of "Docker Desktop" → "OrbStack" across [MacDeploy.md](MacDeploy.md).

These are the judgment calls and caveats behind that swap. Read this before a developer on a fresh Mac wonders whether the old Docker Desktop install still works (it does).

---

## Licensing tailwind

OrbStack is free for personal use, but commercial use requires a paid license once a company grows past the free-tier thresholds. Docker Desktop has a similar commercial-license tier, so this isn't a regression — it's a lateral move on licensing.

**Action:** Before the switch becomes official policy (as opposed to "the maintainer uses OrbStack locally"), flag the licensing question to the team README so it doesn't get discovered at billing time.

**Why it matters:** A dev-tool license is a small line item, but it's the kind of thing that should be a deliberate decision, not a default inherited from "whatever the docs say."

---

## Networking nuance not captured in the docs

OrbStack's VM uses a slightly different bridge driver than Docker Desktop. In practice both runtimes expose `host.docker.internal` natively on macOS, so the `extra_hosts: ["host.docker.internal:host-gateway"]` Linux workaround mentioned in [MacDeploy.md:132](MacDeploy.md#L132) is still unnecessary under OrbStack — the "Linux vs Mac" section remains accurate as written.

**Watch for:** If anyone reports flaky `host.docker.internal` resolution after the switch, the bridge-driver difference is the first place to look. Symptoms would be intermittent "connection refused" from containers trying to reach a service running natively on the Mac (e.g., hybrid-mode Flask).

**Why it matters:** The failure mode is quiet — things *mostly* work, then break under load or after a sleep/wake cycle. If it happens, don't waste time on Flask or the DB; go straight to `docker network inspect` and check the bridge.

---

## What didn't change

Things that read like they might need updates but actually don't:

- **Port 80 handling** ([MacDeploy.md:283](MacDeploy.md#L283)) — OrbStack handles privileged ports on macOS the same way Docker Desktop does, via its VM. No `sudo`, no `authbind`, nothing.
- **Volume mounts** — Both runtimes use virtiofs/osxfs-style bind mounts for source code. Hot-reload on webapp code continues to work.
- **Cross-project container networking on Linux** ([MacDeploy.md:116-134](MacDeploy.md#L116-L134)) — The segmentation roadmap discusses Linux behavior, not Mac. Nothing there needed to change.

---

## Related docs

- [MacDeploy.md](MacDeploy.md) — running the appliance on a Mac
- [docker-compose.local.yml](docker-compose.local.yml) — the compose file MacDeploy.md drives
