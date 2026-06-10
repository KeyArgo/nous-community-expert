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

## Data Source

All message data comes from [teknium1/nous-discord-archive](https://github.com/teknium1/nous-discord-archive), which auto-updates every 6 hours. The archive contains public messages from four Nous Research Discord channels.

## License

MIT
