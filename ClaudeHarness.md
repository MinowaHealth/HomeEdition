# Claude Development Harness

**Date:** 2026-06-15 09:55 PDT

This repo is developed through a four-part harness that wraps Claude Code. The parts are **Serena**, the **LSP Enforcement Kit**, **CodeSight**, and **OptiVault**. None of them change the application; they change how the AI *reads* the application. The goal of all four is the same: give the model precise, symbol-level knowledge of the codebase while spending as few tokens as possible getting it.

This document explains how the pieces fit together first, then covers installation and verification for each.

---

## How the four pieces work together

The expensive, error-prone default for an AI coding agent is "read the whole file" and "grep for a name." A 600-line module costs thousands of tokens to ingest, most of which are irrelevant to the change at hand, and a text `grep` finds a *string* — not a *symbol* — so it misses call sites, hits comments, and invents references that aren't really there. The harness replaces both habits with cheaper, more accurate alternatives, and then enforces the replacement.

Think of the four tools in three layers:

```
                    ┌──────────────────────────────────────────┐
   REFEREE          │   LSP Enforcement Kit (hooks)             │
                    │   intercepts Read / Grep / Glob / Bash    │
                    │   and redirects to the layers below       │
                    └───────────────────┬──────────────────────┘
                                        │ redirects to
        ┌───────────────────────────────┼───────────────────────────────┐
        ▼                                ▼                               ▼
  ┌───────────┐                  ┌───────────────┐              ┌────────────────┐
  │  Serena   │                  │   OptiVault   │              │   CodeSight    │
  │ (live LSP │                  │ (AST shadow   │              │ (project-wide  │
  │ navigator │                  │  vault + MCP) │              │  context map)  │
  │  + edits) │                  └───────────────┘              └────────────────┘
  └───────────┘
   NAVIGATOR                          INDEXERS (continuous, file-watching)
```

### The indexers — CodeSight and OptiVault

These two run continuously in the background (file watchers) and keep a compressed, always-fresh view of the repo on disk.

- **CodeSight** writes a *project-wide* map to `.codesight/` — routes, models, middleware, library signatures, the dependency graph, and the env-var inventory. It also injects a short "AI Context" block into `CLAUDE.md`. Its job is to answer "what is this project, and where does the high-impact code live?" in one cheap read instead of a directory crawl. CodeSight reports its own savings: the context map is ~8.5k tokens versus ~61k tokens of blind exploration — **~52k tokens saved per conversation** on this repo.
- **OptiVault** writes a *per-file* shadow vault to `_optivault/`, with one "skeleton" note per source file: exported signatures plus that file's dependencies, and a top-level `_RepoMap.md` linking them. It is AST-driven, so the skeletons are structurally accurate, not regex guesses. OptiVault also exposes this vault over MCP with four tools — `read_repo_map`, `read_file_skeleton`, `read_function_code`, and `sync_file_context` — so the model can pull *just* a function body when it needs one, instead of the whole file.

CodeSight gives breadth (the map of the whole repo); OptiVault gives depth on demand (drill into one file's structure, then one function's body). Both are read-cheap because the expensive AST/graph work already happened in the watcher, out of band, not inside the conversation's token budget.

### The navigator — Serena

CodeSight and OptiVault are *snapshots*. **Serena** is *live*. It is an MCP server backed by a real Language Server, so it answers ground-truth semantic questions about the code as it exists right now:

- `find_symbol`, `get_symbols_overview` — locate definitions
- `find_referencing_symbols`, `find_implementations` — true call/impl sites
- `replace_symbol_body`, `insert_after_symbol`, `rename_symbol` — **edit at the symbol level**, so a change to one function can't accidentally clobber its neighbors

The references Serena returns are real because the LSP computed them — the model isn't pattern-matching a name and hoping. That is the *code-quality* half of the harness: edits land on the right symbol, and "find everywhere this is called" is exhaustive rather than approximate.

### The referee — the LSP Enforcement Kit

Indexers and a navigator only help if the model actually uses them. Left alone, an LLM will fall back to `Read` and `grep` out of habit. The **LSP Enforcement Kit** is a set of Claude Code hooks (`~/.claude/hooks/`) that intercept the brute-force tools *before* they run and redirect to the layers above:

- `bash-grep-block.js` — blocks `grep`/`rg`/`ag`/`ack` on **code symbols** in a Bash command and suggests the LSP equivalent. (It allows `git grep`, non-code paths, and non-code file types like `*.sql`/`*.md`.)
- `lsp-first-guard.js` / `lsp-first-glob-guard.js` — the same idea for the `Grep` and `Glob` tools.
- `lsp-first-read-guard.js` — an **escalating budget** on reading *code* files. The first couple of reads are free; past a threshold the hook warns and then requires an LSP navigation step, nudging the model toward skeletons and symbol lookups instead of slurping whole files. Non-code files (`.md`, `.json`, `.sql`, configs, tests, and the `_optivault`/`.codesight` artifacts themselves) are exempt.
- `read-before-edit-guard.sh` — blocks an `Edit`/`Write` to a file the model hasn't read, preventing blind edits.
- `lsp-pre-delegation.js`, `lsp-session-reset.js`, `lsp-usage-tracker.js` — carry the LSP-first posture across sub-agent delegation and session boundaries, and record LSP usage so the budget logic works.

The companion rule file `~/.claude/rules/lsp-first.md` is the standing instruction the hooks enforce: LSP over Grep for any *semantic* navigation; Grep/Glob only as a fallback when the LSP returns empty or the target isn't a symbol.

### Why this combination improves quality *and* cuts tokens

| Concern | Without the harness | With the harness |
|---------|--------------------|--------------------|
| "What is this project?" | crawl directories, read configs (~60k tok) | one read of `.codesight/CODESIGHT.md` (~8.5k tok) |
| "What's in this file?" | read all 600 lines | `read_file_skeleton` → signatures + deps only |
| "I need this one function" | read the file, scroll | `read_function_code` → just the body |
| "Where is X called?" | `grep X` → strings, misses, comments | `find_referencing_symbols` → real call sites |
| "Change this function" | edit a line range, risk clobbering | `replace_symbol_body` at the symbol boundary |
| Falling back to old habits | happens silently | hooks block it and suggest the LSP path |

The indexers cut the *cost* of context, Serena raises the *accuracy* of navigation and edits, and the Enforcement Kit makes sure the model takes the cheap, accurate path rather than the expensive, sloppy one.

---

## Installation

All four are installed at the **user level** (`~/.claude/` and `~/.npm-global/`), so they apply to every repo on this machine, not just this one. The repo-local footprint is the generated artifact directories (`.codesight/`, `_optivault/`, `.serena/`) plus the `Watchers.md` opt-in marker described below.

### Serena (MCP server)

Serena is registered as a stdio MCP server in `~/.claude.json`, launched with `uvx` straight from upstream:

```json
"serena": {
  "type": "stdio",
  "command": "uvx",
  "args": [
    "--from", "git+https://github.com/oraios/serena",
    "serena", "start-mcp-server",
    "--context", "ide-assistant"
  ]
}
```

Prerequisite: `uv`/`uvx` on `PATH` (Astral's Python runner). No separate global install — `uvx` fetches and caches Serena on first launch. The `ide-assistant` context is the right profile for editor-style symbol work.

To register it yourself: `claude mcp add` (or edit `~/.claude.json`) with the command above, then approve the `mcp__serena__*` tools (already allowlisted in `~/.claude/settings.json`).

The language-server backends Serena/LSP rely on are provided by the enabled plugins `pyright-lsp` (Python — what matters for this repo) and `typescript-lsp`.

### CodeSight (npm global CLI)

```bash
npm install -g codesight        # lands in ~/.npm-global/bin/codesight
cd <repo>
codesight --init                # scan + write .codesight/ and the CLAUDE.md block
```

`codesight --init` is the one-time setup per repo; the watcher (below) keeps it fresh after that. Optional MCP mode exists (`codesight --mcp`) but on this box CodeSight is used as a watcher + on-disk map, not an MCP server.

### OptiVault (npm global CLI)

```bash
npm install -g optivault        # lands in ~/.npm-global/bin/optivault
cd <repo>
optivault init                  # scan + write the _optivault/ shadow vault
```

OptiVault's MCP tools (`read_repo_map`, `read_file_skeleton`, `read_function_code`, `sync_file_context`) come from `optivault mcp`. The "OptiVault Protocol" block in `CLAUDE.md` is what instructs the model to prefer those tools over `cat`/`grep` for initial code understanding.

### LSP Enforcement Kit (hooks)

The Kit is not a package — it is the hook scripts in `~/.claude/hooks/` plus their wiring in `~/.claude/settings.json` and the rule file `~/.claude/rules/lsp-first.md`. The relevant `settings.json` blocks:

```jsonc
"env": { "LSP_ENFORCE_DEBUG": "1" },        // emit hook decision logs
"hooks": {
  "PreToolUse": [
    { "matcher": "Grep",  "hooks": [{ "command": "node ~/.claude/hooks/lsp-first-guard.js" }] },
    { "matcher": "Glob",  "hooks": [{ "command": "node ~/.claude/hooks/lsp-first-glob-guard.js" }] },
    { "matcher": "Bash",  "hooks": [{ "command": "node ~/.claude/hooks/bash-grep-block.js" }] },
    { "matcher": "Read",  "hooks": [{ "command": "node ~/.claude/hooks/lsp-first-read-guard.js" }] },
    { "matcher": "Agent", "hooks": [{ "command": "node ~/.claude/hooks/lsp-pre-delegation.js" }] },
    { "matcher": "Edit|Write|NotebookEdit",
                          "hooks": [{ "command": "$HOME/.claude/hooks/read-before-edit-guard.sh" }] }
  ],
  "PostToolUse": [
    { "matcher": "mcp__(?:plugin_[^_]+_)?(?:cclsp|serena)__",
                          "hooks": [{ "command": "node ~/.claude/hooks/lsp-usage-tracker.js" }] },
    { "matcher": "Read",  "hooks": [{ "command": "$HOME/.claude/hooks/read-tracker.sh" }] }
  ],
  "SessionStart": [
    { "matcher": "true",  "hooks": [{ "command": "node ~/.claude/hooks/lsp-session-reset.js" }] },
    { "matcher": "true",  "hooks": [{ "command": "$HOME/.claude/hooks/optivault-codesight-watchers.sh", "timeout": 10 }] }
  ]
}
```

Requires Node.js (the `.js` hooks) and a POSIX shell. Per-session/per-repo state lives under `~/.claude/state/` (read budgets, LSP-ready flags).

### The watchers — opt-in per repo

The CodeSight and OptiVault watchers are started automatically by the `SessionStart` hook `optivault-codesight-watchers.sh`, **but only for repos that opt in** by having a `Watchers.md` file at the repo root:

```bash
[[ -f "$REPO/Watchers.md" ]] || exit 0      # no marker → no automation
```

When the marker is present, the hook idempotently launches `optivault watch` and `codesight --watch` for the repo (tracked by PID files under `~/.claude/run/watchers/`, logs under `.../logs/`). This is what keeps `.codesight/` and `_optivault/` fresh as you edit, without re-running the CLIs by hand.

---

## Verification of operation

### Serena

```bash
# 1. MCP server is registered
python3 -c "import json; print('serena' in json.load(open('$HOME/.claude.json')).get('mcpServers',{}))"
# 2. Project is initialized for Serena
ls .serena/project.yml
```

In-session: the `mcp__serena__*` tools appear in the tool list, and `mcp__serena__get_current_config` / `mcp__serena__find_symbol` return results. Each successful Serena/LSP call is recorded by `lsp-usage-tracker.js`, which is what relaxes the read-budget guard.

### CodeSight

```bash
codesight --version                 # -> codesight v1.5.0
ls -l .codesight/CODESIGHT.md       # exists; mtime should be recent if watching
codesight --benchmark               # detailed token-savings breakdown
codesight --telemetry               # real before/after token measurement
```

The header of `.codesight/CODESIGHT.md` states the live token-savings figure for this repo. A recent mtime confirms the `--watch` watcher is actually writing.

### OptiVault

```bash
optivault --version                 # -> 0.1.0
ls -l _optivault/_RepoMap.md        # exists; mtime recent if watching
```

In-session: `read_repo_map` returns the linked file index, and `read_file_skeleton <file>` returns that file's signatures + deps. After any edit you make, calling `sync_file_context` on the changed file should update its note (the OptiVault Protocol requires this so the vault doesn't drift).

### LSP Enforcement Kit

The fastest functional check is to try the thing it's supposed to block:

```bash
# In-session, attempt to grep for a code symbol, e.g. Grep "def process_document_inline".
# Expectation: the PreToolUse hook blocks it and suggests the LSP/Serena equivalent.
```

Supporting checks:

```bash
# Hooks are present
ls ~/.claude/hooks/lsp-first-*.js ~/.claude/hooks/bash-grep-block.js
# Debug logging is on (set in settings.json env)
grep LSP_ENFORCE_DEBUG ~/.claude/settings.json
```

With `LSP_ENFORCE_DEBUG=1`, each hook logs its allow/block decision (via `hooks/lib/logger.js`), so you can confirm *why* a given Read/Grep was allowed or redirected. Note the deliberate exemptions: `git grep`, non-code file types, and the generated `_optivault`/`.codesight` artifacts are always allowed, so reading the harness's own output never trips the guard.

### Watchers

```bash
# List every tracked watcher across all opted-in repos (and their status)
~/.claude/hooks/watchers-status.sh
# Or check directly
ps aux | grep -E 'optivault watch|codesight --watch' | grep -v grep
```

`watchers-status.sh --cleanup` removes PID files whose process has died. A `stale` or absent entry for this repo means the watchers aren't running; starting a new Claude Code session re-spawns them (the `Watchers.md` marker is present), or launch them by hand with `optivault watch` and `codesight --watch`.

---

## Quick reference

| Tool | Role | Lives in | Install | Verify |
|------|------|----------|---------|--------|
| **Serena** | Live LSP navigator + symbol edits | `~/.claude.json` MCP, `.serena/` | `uvx` from `oraios/serena` | `mcp__serena__*` tools respond |
| **LSP Enforcement Kit** | Hooks that force LSP-first | `~/.claude/hooks/`, `settings.json`, `rules/lsp-first.md` | hook scripts + settings wiring | `grep <symbol>` gets blocked |
| **CodeSight** | Project-wide context map | `.codesight/`, `CLAUDE.md` block | `npm i -g codesight` | `codesight --version` → v1.5.0 |
| **OptiVault** | Per-file AST shadow vault + MCP | `_optivault/`, MCP tools | `npm i -g optivault` | `optivault --version` → 0.1.0 |