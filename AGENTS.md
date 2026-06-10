# Agent Workspace Contract — nous-community-expert-dev (Talio)

This file is read by any AI agent (Hermes, Claude Code, OpenCode, Codex, subagents) before working in this repo.

## Branches

  - `main` — production. https://nous-community-expert-dev.pages.dev/. Only clean merges from `dev`. No direct commits.
  - `dev` — integration. All new work lands here first. CF Pages preview deploys from this branch.
  - `feature/<name>` — single-purpose work. Merged to `dev`, then deleted.
  - `agent/<harness>/<task>` — per-agent isolation. Use this for concurrent agent work.

## Commit format

`[harness] <type>(<scope>): <subject>`

  - `feat:` / `fix:` / `refactor:` / `chore:` / `plan:` / `docs:` / `test:` / `wip:`
  - Prefixes seen in this repo: `[hermes]`
  - Multi-agent commits add `Co-authored-by:` trailers

Examples from this repo's history:

```
[hermes] feat(graphs): add 8 charts (added day-of-week, heatmap, contributor growth, channels-time)
[hermes] fix(graphs): add dates, values, units, y-axis labels to all charts
[hermes] chore: remove graphs.html (now inline)
```

## Coordination

  - Read `IN_PROGRESS.md` before starting work
  - Claim with `chore: claim task <name>`
  - Release with `chore: release task <name>`
  - Never `git push --force` to `main` or `dev`
  - One worktree per concurrent agent

## Hard Confirmation Rule

  - No agent may execute `git push`, `git merge` into `main`/`dev`,
    `git branch -d/-D`, `git push --delete`, or any `--force`/`-f`
    git operation as part of a chained or batched command.
  - Each such operation requires its own explicit user confirmation
    in that session. An earlier "yes" for a different action does
    NOT carry over to push/merge/delete/force operations.
  - If another agent's commits appear on your branch unexpectedly,
    stop and report to the user — do not merge or push past them.

## Promotion Model

  - `dev` = active integration/experiments. Agents land work here
    freely (within the branch rules above).
  - `main` = curated, public-facing. This is what `KeyArgo/*` mirrors
    track. Promotion `dev` → `main` happens ONLY when the user reviews
    and approves specific commits — never automatic on merge to `dev`.
  - Public mirrors (`keyargo`, `github`/inovinlabs) are fast-forwarded
    from `main` only, never from `dev`.

## Identity

  - Hermes: `hermes-agent@nous.local`
  - Claude Code: `noreply@anthropic.com`
  - OpenCode: `opencode@nous.local`
  - Codex: `codex@nous.local`

Verify your author identity before committing: `git config user.email`

## File scope

  - Code, configs, scripts: repo root
  - Plans: `.hermes/plans/<ISO-timestamp>-<name>.md`
  - Coordination: `AGENTS.md`, `IN_PROGRESS.md` (this repo)
  - Handoffs: `~/Vaults/ai-context/` (Hermes writes, others read)
  - Skills: `~/.hermes/profiles/<profile>/skills/` (Hermes only — others hands off)
  - Memory: `~/.hermes/profiles/<profile>/memories/` (Hermes only — others hands off)

## Build / deploy pipeline

  - Data: `python3 build_index.py <chunks.jsonl>` regenerates `search-data.json` + `search-index.json`
  - QA: `python3 qa.py` validates JSON schema
  - Deploy: `gh workflow run "Trigger Cloudflare Rebuild" --repo inovinlabs/nous-community-expert-dev`
  - Auto-deploy cron: every 6h via GitHub Actions
  - Preview branches: `dev` branch gets `dev.nous-community-expert-dev.pages.dev` (CF Pages preview)

## Cleanup

  - Delete `feature/*` and `agent/*` branches after merge (local + remote)
  - Remove worktrees: `git worktree remove <path> --force`
  - Run `git worktree prune` periodically
  - Update `IN_PROGRESS.md` on every state change
