# Watchers — automated OptiVault + CodeSight refresh

This file is **both documentation and an opt-in marker.** When it exists at the
repo root, the Claude Code `SessionStart` hook spawns `optivault watch` and
`codesight --watch` daemons scoped to this repo, so the AST shadow vault and
codebase map stay fresh without manual intervention.

Delete this file to disable the watchers for this repo.

---

## What you get

| Tool | Output | What it gives Claude |
|---|---|---|
| OptiVault | `_optivault/` AST skeletons + compressed function bodies | Cheap reads of file structure, exports, signatures without loading whole files |
| CodeSight | `.codesight/` route map, schema map, high-impact-file index | Project-wide overview injected at session start |

Both watch the working tree and update incrementally when files change.

## Heads-up: OptiVault appends to `CLAUDE.md`

On its first run in a repo, OptiVault appends a marked section to `CLAUDE.md`:

```
<!-- optivault-protocol -->
# OptiVault Protocol Active
...
```

Existing `CLAUDE.md` content is preserved. The append is reversible — delete
the block (including the `<!-- optivault-protocol -->` marker) if you don't
want it tracked in git. OptiVault will not re-append on subsequent runs as long
as the marker line remains, so you can keep the marker in a `.gitignore`-d
shadow or just delete the entire block and accept that future `optivault init`
runs will re-add it.

**Decide before the first session:** commit the addition (team uses it), revert
it (kept local), or leave it as a working-tree change for now.

## Files & locations

| Path | Purpose |
|---|---|
| `~/.claude/hooks/optivault-codesight-watchers.sh` | The `SessionStart` hook |
| `~/.claude/hooks/watchers-status.sh` | Inspection + cleanup utility |
| `~/.claude/run/watchers/<tool>-<repohash>.pid` | Repo-scoped PID files |
| `~/.claude/run/watchers/logs/<tool>-<repohash>.log` | Watcher stdout/stderr |
| `~/.claude/settings.json` → `hooks.SessionStart` | Hook registration |
| `<repo>/Watchers.md` | Opt-in marker (this file) |
| `<repo>/_optivault/` | OptiVault shadow vault (generated) |
| `<repo>/.codesight/` | CodeSight context map (generated) |

`<repohash>` is the first 8 chars of `shasum` of the absolute repo path, so
multiple repos never collide on PID files.

## Daily operations

```bash
# What's running across all repos?
~/.claude/hooks/watchers-status.sh

# Remove PID files for watchers that died (and re-spawn next session)
~/.claude/hooks/watchers-status.sh --cleanup

# What did a watcher log?
ls ~/.claude/run/watchers/logs/
tail -f ~/.claude/run/watchers/logs/optivault-<repohash>.log

# Manually kill a watcher for this repo (next session will respawn it)
pkill -f 'optivault watch.*HealthAI'
pkill -f 'codesight.*--watch.*HealthAI'
```

## Propagating to another repo

On the same machine:

1. Confirm CLIs are installed: `which optivault codesight` (both should resolve
   under `~/.npm-global/bin/`; if not, install with `npm install -g optivault codesight`).
2. Copy `Watchers.md` to the new repo's root.
3. Start a Claude Code session in that repo. The hook fires, watchers spawn,
   shadow files appear.
4. Decide what to do with the `CLAUDE.md` append (see "Heads-up" above).

On a fresh machine (or if the global hook isn't installed yet):

1. Install the CLIs: `npm install -g optivault codesight`.
2. Copy `~/.claude/hooks/optivault-codesight-watchers.sh` and
   `~/.claude/hooks/watchers-status.sh` from a machine that has them, and
   `chmod +x` both.
3. Add this entry to `~/.claude/settings.json` under `hooks.SessionStart`:
   ```json
   {
     "matcher": "true",
     "hooks": [
       {
         "type": "command",
         "command": "$HOME/.claude/hooks/optivault-codesight-watchers.sh",
         "timeout": 10
       }
     ]
   }
   ```
4. Drop `Watchers.md` into any repo you want covered.

### About the PATH line in the hook

The hook starts with:

```bash
export PATH="$HOME/.npm-global/bin:$HOME/.npm/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"
```

This exists because GUI-launched apps on macOS (Antigravity, VSCode opened
from Finder, anything spawned by `launchd`) inherit a minimal PATH —
`/usr/bin:/bin:/usr/sbin:/sbin` — that doesn't include where `npm install -g`
puts binaries. Without this line, `command -v optivault` returns false inside
the hook, both watchers are silently skipped, and the hook exits 0 looking
like a success.

If your `optivault`/`codesight` live somewhere else (custom `npm prefix`,
`fnm`/`volta`/`nvm`-managed Node, pnpm global bin), add that path to the
front of the line. Confirm with `which optivault` from your interactive
shell, then prepend that directory.

## Limitations & caveats

- **No teardown on session end.** Watchers run until the machine reboots or you
  `pkill` them. This is intentional — they're cheap idle, and a watcher that's
  still alive when you start your next Claude session saves a respawn cycle.
  Run `watchers-status.sh` periodically if you want to audit.
- **Race on simultaneous session starts.** If you open two Claude sessions in
  the same repo at the same instant, both can pass the "is a watcher running?"
  check and both spawn. Result: duplicate watchers. Not catastrophic — both
  write to the same vault dir — but `watchers-status.sh --cleanup` won't see
  the duplicates since their PID files are simply overwritten. Detect by
  running `pgrep -af 'optivault watch.*<repo>'`.
- **Mid-session edits from outside Claude.** As long as a watcher is alive, it
  picks up changes from any editor (VS Code without Claude, terminal `vim`,
  etc.). If you killed Claude long ago, the watcher likely died too and
  shadows are stale until next session.
- **`CLAUDE.md` modification.** Already covered above. Most surprising side
  effect on first run.
- **HealthAI's `CLAUDE.md` is checked in.** A clean `git status` after enabling
  watchers here will show `CLAUDE.md` modified. Review the diff before staging.

## Reverting everything

```bash
# Stop all watchers
~/.claude/hooks/watchers-status.sh --cleanup
pkill -f 'optivault watch'
pkill -f 'codesight.*--watch'

# Remove the hook from settings.json — open in editor and delete the entry
# Or: remove the script and the hook becomes a no-op (it'll fail silently)

# Remove this repo's shadow artifacts (optional)
rm -rf _optivault .codesight

# Remove this file to make sure the hook doesn't reactivate
rm Watchers.md
```
