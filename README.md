# Nous Community Expert

A Discord archive search platform for the Nous Research community.

Searchable archive of discussions from #hermes-agent, #developers, #community-projects-showcase, and #plugins-skills-and-skins.

## Quick Start

### Static Site (recommended)

```bash
# Install Python 3.11+, then:
python3 build_index.py /path/to/chunks.jsonl
python3 serve.py
# Opens at http://localhost:8080
```

### Docker

```bash
docker compose up -d
```

## How It Works

1. **`build_index.py`** — Reads Discord archive `chunks.jsonl`, builds a BM25 inverted index + pre-computed stats
2. **Output** — `search-data.json` (29MB), `search-index.json` (12MB), `metadata.json`
3. **`index.html`** — Static SPA that loads the JSON files and performs client-side BM25 search
4. **No server required** — deploy to Cloudflare Pages, GitHub Pages, or any static host

## Auto-Refresh Pipeline

A GitHub Actions workflow (`rebuild.yml`) runs every 6 hours:
- Clones the public [Nous Discord archive](https://github.com/teknium1/nous-discord-archive)
- Rebuilds the search index
- Uploads data as a build artifact
- Optionally deploys to Cloudflare Pages via deploy hook

## Build Pipeline

- **Every 6h**: Cloudflare Pages re-runs the build with the latest archive
- **Daily 04:00 UTC**: `auto-ingest.yml` re-parses the archive, rebuilds the
  index (including the rolling backups from issue #6), and — if the data
  shards changed — opens a PR to `main` with the new `search-data-*.json`,
  `search-index-*.json`, `metadata.json`, and `dist/` exports for review
- **On PR merge**: Cloudflare Pages auto-deploys

## Auto-Ingest

`.github/workflows/auto-ingest.yml` keeps the committed search index in
sync with the upstream Discord archive without ever pushing to `main`
directly:

1. Clones [teknium1/nous-discord-archive](https://github.com/teknium1/nous-discord-archive) (shallow, `main`)
2. Runs `parse_archive.py` to produce a fresh `chunks.jsonl`
3. Runs `build_index.py` to rebuild the search index, AI exports, and backups
4. Diffs the resulting data shards against what's committed
5. If anything changed, opens a PR (`auto/ingest-YYYY-MM-DD-NN` → `main`)
   titled `Auto-ingest YYYY-MM-DD: N chunks, M messages` with before/after
   stats, the list of changed files, and a link to the backup manifest
6. If nothing changed, the run exits cleanly with no PR

This keeps a human in the loop (PR review) before new data reaches
production, and gives every ingest a reviewable, revertible PR. A
`dist/last_ingest.txt` timestamp prevents redundant same-day runs, and a
`build_failed.json` marker (written if `build_index.py` raises) fails the
workflow loudly instead of opening a PR with partial data.

## Data Source

All message data comes from [teknium1/nous-discord-archive](https://github.com/teknium1/nous-discord-archive), which auto-updates every 6 hours. The archive contains public messages from four Nous Research Discord channels.

## License

MIT
