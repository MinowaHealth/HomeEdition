# Dependency Management

How Minowa.ai Home Edition handles third-party package updates across its two service backends. Written for reviewers who know GitHub but don't read Python daily.

---

## TL;DR

- **Dependabot** opens pull requests when our pinned dependencies have newer versions available. We don't update by hand.
- We **group related updates** so one weekly run produces a handful of PRs, not 50+.
- **Major-version bumps on critical packages are blocked.** They require deliberate planning and a human-driven PR, not a Tuesday-morning auto-PR.
- **Patch bumps auto-merge** when CI is green. Minor and major still need a human reviewer.
- **Python runtime is on 3.13 in production.** 3.14 is available as an opt-in build flag for the testing role only.

The full configuration lives in [.github/dependabot.yml](.github/dependabot.yml) and [.github/workflows/dependabot-auto-merge.yml](.github/workflows/dependabot-auto-merge.yml).

---

## What problem does this solve

Home Edition ships two backend services — **UserApp** (the household Flask API, with in-process OCR) and **UserMCP** (the Python MCP server) — plus a small `DataModel3` schema-audit tooling directory. Each has its own list of third-party Python packages pinned at known-good versions. Without Dependabot, those pins drift further out of date every week, and security advisories on those packages go un-actioned. With naive dependabot configuration, you get the opposite problem: dozens of PRs per week per service, each touching one line. We just lived through a week where dependabot opened **58 pull requests in 4 days**, and one of them snuck a Python 3.12 → 3.14 runtime jump past us. This config is the response.

---

## What we changed (three pieces)

### 1. Grouping related updates

Without grouping, dependabot opens a separate PR for every single package version change. With grouping, related changes bundle into one PR.

| Group | What it bundles |
|---|---|
| `patches` | All patch bumps (X.Y.Z → X.Y.Z+1) for any package in that service. One PR per service per week. |
| `minors` | All minor bumps (X.Y → X.Y+1). One PR per service per week. |
| `pytest-stack` | `pytest`, `pytest-asyncio`, `pytest-cov`, etc. The test framework moves as a unit. |
| `docker-base` | All Docker base-image bumps across the service Dockerfiles. The Python 3.12→3.14 wave that produced multiple PRs would now be one. |
| `actions` | All GitHub Actions version bumps, bundled into one PR. |

**Effect:** A typical week's dependabot output drops from dozens of PRs to a handful.

### 2. Major-version caps on critical packages

For packages where a major bump (X.Y → X+1.0) is likely to break our code, we tell dependabot to ignore major bumps entirely. The package can still update for security or minor releases, but a major version cutover requires a human writing a deliberate PR.

| Service | Capped at major |
|---|---|
| UserApp/webapp | flask, gunicorn |
| UserMCP | mcp, pydantic, starlette, uvicorn |
| All Docker | python (capped at <3.14 for now) |

The reasoning per package is in [.github/dependabot.yml](.github/dependabot.yml) comments. Drop a cap when you're ready to plan that specific upgrade.

### 3. Auto-merge for patch updates

Patch bumps (X.Y.Z → X.Y.Z+1) are the safe-by-convention release type — bug fixes and security patches only, no API changes. The workflow at [.github/workflows/dependabot-auto-merge.yml](.github/workflows/dependabot-auto-merge.yml) tells GitHub: when dependabot opens a patch-only PR and CI passes, merge it without waiting for a human.

**This is not a CI bypass.** The workflow uses `gh pr merge --auto`, which queues the merge for whenever the required checks turn green. If pip-audit detects a CVE or pyright complains, the PR sits unmerged until a human investigates.

Minor and major bumps never auto-merge. They land in the queue for human review.

---

## What a reviewer should do when a dependabot PR shows up

| PR type | Action |
|---|---|
| `patches` group | Usually nothing — auto-merge handles it once CI is green. Glance at the diff if you're curious. |
| `minors` group | Read the changelog links dependabot puts in the PR description. Look for behavior changes. Merge if nothing alarming. |
| `pytest-stack` group | Run the test suite locally if any of your branches are mid-development. Otherwise merge. |
| `docker-base` group | Treat as a runtime change. Verify the Build Docker Image CI job ran. Confirm the Python version is what you intended. |
| `actions` group | GitHub Actions version bumps. Skim for anything that changes a workflow's behavior; otherwise merge. |
| Single-package PR (no group label) | This is a major version bump that escaped the caps, OR a security update. Read the changelog carefully. |

CI checks must pass before merge regardless of update size. We never override a failing test or a CVE finding.

---

## The Python version situation (current state)

We deliberately rolled production back from `python:3.14-slim` to `python:3.13-slim` after the dependabot wave bumped both services' Dockerfiles in one motion. 3.14 only shipped in October 2025 and the C-extension wheel ecosystem hasn't fully caught up; 3.13 has 18 months of patch releases and full coverage. For an I/O-bound health appliance, 3.14 doesn't deliver any measurable performance win, and stacking a runtime jump on top of the Home Edition conversion was risk-on-risk.

To still let you **experiment with 3.14 on the testing role**, every Dockerfile is parameterized:

```dockerfile
ARG PYTHON_VERSION=3.13-slim
FROM python:${PYTHON_VERSION}
```

Production builds use the default. Testing-role builds opt in via:

```bash
docker build --build-arg PYTHON_VERSION=3.14-slim ...
```

Or in compose:

```yaml
services:
  webapp:
    build:
      args:
        PYTHON_VERSION: 3.14-slim
```

When you're ready to plan a coordinated 3.14 cutover, drop the `versions: [">=3.14"]` ignore rule in `dependabot.yml` and let it propose the bump again.

---

## Knobs to adjust if circumstances change

- **Too few PRs / want more freshness:** Drop the major-version caps for specific packages, or remove the `patches`/`minors` groupings.
- **Too many PRs / weekly is overwhelming:** Change `schedule.interval` from `weekly` to `monthly` for service entries that aren't mission-critical.
- **A package keeps showing up that shouldn't:** Add it to the `ignore` list under that service's entry.
- **Want auto-merge for minors too:** Change `version-update:semver-patch` to `version-update:semver-minor` in the auto-merge workflow. Higher risk; do this only if you trust the test suite to catch breaks.

---

## Why we didn't do other things

- **Removing dependabot entirely** — security advisories arrive via dependabot. Disabling it means manual CVE tracking, which doesn't scale.
- **Pinning all dependencies hard** — too brittle. The pip-audit CVE scans need newer versions to flag known-vulnerable code, and exact pins everywhere would make CVE response slower.
- **Squash-merging everything** — we use true merge commits for substantive PRs (so commit history is preserved) and squash-merges only for trivial dependabot patches. Nothing in this config forces that choice; it's a convention.

---

## Where to look in the future

- **Configuration:** [.github/dependabot.yml](.github/dependabot.yml) is the source of truth for everything described above.
- **Auto-merge logic:** [.github/workflows/dependabot-auto-merge.yml](.github/workflows/dependabot-auto-merge.yml).
- **Test gates:** [.github/workflows/ci.yml](.github/workflows/ci.yml) — these are the checks that must pass before any merge happens.

---

*Last updated: 2026-06-12T00:00:00Z.*
