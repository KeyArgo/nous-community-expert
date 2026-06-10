# In-Progress Tasks

> Coordination file for multi-agent work in this repo. Read before starting.
> Update on every state change (claim, in-progress note, done, blocked).
>
> Convention: `[agent=<harness>] [branch=<branch>] [task=<one line>] [started=<ISO>] [status=<active|blocked|done>] [done=<ISO>] [merged-to=<branch>]`

## Active

  - [agent=claude-code] [branch=dev] [task=Talio multi-agent cleanup + OSS split (Phase 0: AGENTS.md hardening, Phase 1: sync main/dev to origin/github/keyargo, Phases 2-5: new repos KeyArgo/talaria + KeyArgo/nous-community-expert + Star-link update)] [started=2026-06-09] [status=active]

## Blocked

(none)

## Done (last 7 days)

  - [agent=hermes] [branch=main→dev→main] [task=feat(heatmap): show chunk count in each cell of the 24h×7d heatmap] [started=2026-06-09] [status=done] [done=2026-06-09] [merged-to=main]
  - [agent=hermes] [branch=feature/profile-leaderboard-ranking] [task=feat(profile): list user leaderboard ranking (rank + of N total + medal + awards listed explicitly; new "🏆 Leaderboard Standing" section; percentile "top X%" computed correctly)] [started=2026-06-10] [status=done] [done=2026-06-10] [merged-to=<awaiting-user-push>]
  - [agent=hermes] [branch=feature/cf-pages-25mb-shard] [task=fix(cf-pages): shard search-data.json + search-index.json (33.3 MiB / 28.1 MiB exceed 25 MiB limit; auto-pick N shards to stay under 24 MiB each, fetch metadata first then parallel shards in JS, fix datetime.utcnow deprecations)] [started=2026-06-10] [status=done] [done=2026-06-10] [merged-to=<awaiting-user-push>] [commit=01b1f98]
  - [agent=hermes] [branch=main] [task=fix(graphs): add dates, values, units, y-axis labels to all charts] [started=2026-06-09] [status=done] [done=2026-06-09]
  - [agent=hermes] [branch=main] [task=fix(graphDaily): scale to thousands with 'k msgs' label when > 1000] [started=2026-06-09] [status=done] [done=2026-06-09]
  - [agent=hermes] [branch=dev] [task=plan: v5 max-gamification design (Steam-style rankings, 30 badges, hidden achievements)] [started=2026-06-09] [status=done] [done=2026-06-09] [merged-to=dev] [note=plan only, not yet executed]
  - [agent=hermes] [branch=dev] [task=chore: governance setup — AGENTS.md contract + IN_PROGRESS.md coordination] [started=2026-06-09] [status=done] [done=2026-06-09] [merged-to=dev]

## Notes / Requests

  - **v5 plan ready on dev, awaiting execution.** `.hermes/plans/2026-06-09_143500-talio-v5-gamification.md` (726 lines, 9 sub-features). User has not yet given go-ahead to execute.
  - **CF Pages staging + Web Analytics setup pending.** Need CF access from user.
  - **Dead branches pending deletion (Phase 1 of OSS-split plan):** `test/logo-dev`, `test/logo-opencode`. Confirmed dead per prior subagent experiment.
  - **Active multi-phase plan (Claude Code, started 2026-06-09): Talio multi-agent cleanup + OSS split.**
    Phase 0 (this commit) hardens `AGENTS.md` with a hard confirmation rule + promotion model.
    Phase 1 syncs `main`/`dev` to `origin`/`github`/`keyargo` (fast-forward only).
    Phases 2-5 design+build a new generic engine repo `KeyArgo/talaria` and a Nous instance
    repo `KeyArgo/nous-community-expert` (no `-dev`), then repoint Star-on-GitHub links.
    Other harnesses: avoid touching `index.html` Star/Issues links, `build_index.py` topic
    seeds/teknium special-case, or repo-creation under `KeyArgo/` until this plan completes
    or is explicitly handed off.
