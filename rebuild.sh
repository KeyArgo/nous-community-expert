#!/bin/bash
# Rebuild the Nous Community Expert static site
# Pulls latest archive, rebuilds JSON index, optionally pushes to git for Cloudflare deploy
#
# Required environment:
#   ARCHIVE_DIR  - path to the Discord archive clone (e.g. ~/nous-discord-archive)
#   WEB_DIR      - path to this web repo checkout
#
# Optional:
#   PUSH_TO_GIT  - set to "push" to commit and push the rebuilt artifacts
set -e

: "${ARCHIVE_DIR:?ARCHIVE_DIR must be set to the Discord archive path}"
: "${WEB_DIR:?WEB_DIR must be set to this repo path}"

LOG_FILE="$WEB_DIR/rebuild.log"
PUSH_TO_GIT="${PUSH_TO_GIT:-}"

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" | tee -a "$LOG_FILE"
}

log "=== Starting rebuild ==="

# Pull latest archive
log "Pulling latest archive..."
cd "$ARCHIVE_DIR"
git pull origin main 2>&1 | tee -a "$LOG_FILE" || log "WARNING: git pull failed (offline?)"

# Rebuild search index
log "Building search index..."
cd "$WEB_DIR"
python3 build_index.py 2>&1 | tee -a "$LOG_FILE"

# Verify outputs exist
if [ ! -f "metadata.json" ]; then
    log "ERROR: metadata.json not created!"
    exit 1
fi
n_data=$(python3 -c 'import json; print(json.load(open("metadata.json"))["shards"]["data"])')
n_index=$(python3 -c 'import json; print(json.load(open("metadata.json"))["shards"]["index"])')
log "  metadata.json: $(du -h metadata.json | cut -f1) (data=$n_data shards, index=$n_index shards)"

for i in $(seq 0 $((n_data - 1))); do
    f="search-data-${i}.json"
    if [ ! -f "$f" ]; then
        log "ERROR: $f not created!"
        exit 1
    fi
    log "  $f: $(du -h "$f" | cut -f1)"
done

for i in $(seq 0 $((n_index - 1))); do
    f="search-index-${i}.json"
    if [ ! -f "$f" ]; then
        log "ERROR: $f not created!"
        exit 1
    fi
    log "  $f: $(du -h "$f" | cut -f1)"
done

# Run schema QA
log "Running qa.py..."
python3 qa.py 2>&1 | tee -a "$LOG_FILE" || { log "ERROR: qa.py failed"; exit 1; }

# Optionally push to git (triggers Cloudflare Pages deploy)
if [ "$PUSH_TO_GIT" = "push" ]; then
    log "Committing and pushing..."
    cd "$WEB_DIR"
    git add search-data.json search-index.json metadata.json index.html build_index.py rebuild.sh
    git commit -m "Rebuild: $(date '+%Y-%m-%d %H:%M') - $(python3 -c 'import json; m=json.load(open("metadata.json")); print(f"{m[\"total_chunks\"]} chunks, {m[\"total_messages\"]} msgs")')" 2>&1 | tee -a "$LOG_FILE" || log "Nothing to commit"
    git push 2>&1 | tee -a "$LOG_FILE" || log "WARNING: git push failed"
fi

log "=== Rebuild complete ==="
