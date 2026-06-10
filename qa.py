#!/usr/bin/env python3
"""Validate generated sharded JSON has expected fields. Run after every build_index.py run.

Schema (post-2026-06-10 CF Pages 25 MiB sharding):
  metadata.json
    - last_updated, build_time_seconds, total_chunks, total_messages,
      total_authors, date_range, shards: { data: N, index: M }
  search-data-0.json ... search-data-(N-1).json
    - shard, total_shards, last_updated, chunks[], stats, leaderboard,
      streaks, top_days, channel_leaderboards, sample_queries
  search-index-0.json ... search-index-(M-1).json
    - shard, total_shards, terms
    - shard 0 also: doc_count, avg_dl, docs
"""
import json
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent
errors = []

def check(cond, msg):
    if not cond:
        errors.append(f"  X {msg}")
    else:
        print(f"  OK {msg}")

# metadata.json is required and gives us the shard counts
try:
    meta = json.load(open(BASE / "metadata.json"))
except Exception as e:
    print(f"FATAL: cannot load metadata.json: {e}")
    sys.exit(1)

print("== metadata.json ==")
check("last_updated" in meta, "has last_updated")
check("build_time_seconds" in meta, "has build_time_seconds")
check("total_chunks" in meta, "has total_chunks")
check("total_messages" in meta, "has total_messages")
check("total_authors" in meta, "has total_authors")
check("date_range" in meta, "has date_range")
check("shards" in meta and "data" in meta["shards"] and "index" in meta["shards"],
      "has shards.{data,index}")
n_data = meta.get("shards", {}).get("data", 0)
n_index = meta.get("shards", {}).get("index", 0)
check(n_data >= 1, f"shards.data >= 1 ({n_data})")
check(n_index >= 1, f"shards.index >= 1 ({n_index})")

# Search data shards
data_shards = []
for i in range(n_data):
    path = BASE / f"search-data-{i}.json"
    print(f"== search-data-{i}.json ==")
    try:
        shard = json.load(open(path))
    except Exception as e:
        check(False, f"loadable ({e})")
        continue
    data_shards.append(shard)
    check("shard" in shard and shard["shard"] == i, f"shard field == {i}")
    check("total_shards" in shard and shard["total_shards"] == n_data,
          f"total_shards == {n_data}")
    check("last_updated" in shard, "has last_updated")
    check("chunks" in shard and isinstance(shard["chunks"], list),
          "has chunks[]")
    if i == 0:
        check("stats" in shard, "shard 0 has stats (envelope)")
        check("stats" in shard and "channels" in shard["stats"],
              "stats has channels")
        check("sample_queries" in shard and len(shard["sample_queries"]) > 0,
              f"shard 0 has sample_queries ({len(shard.get('sample_queries', []))})")
        check("leaderboard" in shard and len(shard["leaderboard"]) > 0,
              f"shard 0 has leaderboard ({len(shard.get('leaderboard', []))})")
        check("streaks" in shard, "shard 0 has streaks")
        check("top_days" in shard, "shard 0 has top_days")
        check("channel_leaderboards" in shard, "shard 0 has channel_leaderboards")
        if "stats" in shard:
            s = shard["stats"]
            for f in ("daily_counts", "day_of_week", "channel_daily",
                      "contributor_growth", "hour_day_heatmap"):
                check(f in s, f"stats has {f}")
        if shard.get("leaderboard"):
            first = shard["leaderboard"][0]
            check("name" in first, "leaderboard[0] has name")
            check(isinstance(first.get("medal"), (list, tuple)) and len(first["medal"]) == 2,
                  "leaderboard[0].medal is [id, name]")
            check(isinstance(first.get("awards"), list) and len(first["awards"]) <= 2,
                  f"leaderboard[0].awards cap ok ({len(first.get('awards', []))} <= 2)")
            for f in ("max_streak", "msg_per_chunk", "active_days"):
                check(f in first, f"leaderboard[0] has {f}")

# Concatenate chunks across shards and verify total matches metadata
all_chunks = [c for s in data_shards for c in s.get("chunks", [])]
check(len(all_chunks) == meta.get("total_chunks", -1),
      f"sum(shards.chunks) == total_chunks ({len(all_chunks)} vs {meta.get('total_chunks')})")

# Search index shards
index_shards = []
for i in range(n_index):
    path = BASE / f"search-index-{i}.json"
    print(f"== search-index-{i}.json ==")
    try:
        shard = json.load(open(path))
    except Exception as e:
        check(False, f"loadable ({e})")
        continue
    index_shards.append(shard)
    check("shard" in shard and shard["shard"] == i, f"shard field == {i}")
    check("total_shards" in shard and shard["total_shards"] == n_index,
          f"total_shards == {n_index}")
    check("terms" in shard and isinstance(shard["terms"], dict) and len(shard["terms"]) > 0,
          f"has terms ({len(shard.get('terms', {}))})")
    if i == 0:
        check("doc_count" in shard, "shard 0 has doc_count")
        check("avg_dl" in shard, "shard 0 has avg_dl")
        check("docs" in shard and isinstance(shard["docs"], list),
              f"shard 0 has docs ({len(shard.get('docs', []))})")
        check(shard.get("doc_count") == meta.get("total_chunks"),
              f"shard 0 doc_count == total_chunks ({shard.get('doc_count')} vs {meta.get('total_chunks')})")

# Verify all terms merge to a single dict whose postings are internally consistent
merged_terms = {}
for s in index_shards:
    merged_terms.update(s.get("terms", {}))
print(f"== merged terms ==")
check(len(merged_terms) > 0, f"merged terms non-empty ({len(merged_terms)} terms)")
# Spot-check: a random term's postings should be a list of [doc_idx, tf] pairs
if merged_terms:
    sample_term = next(iter(merged_terms))
    postings = merged_terms[sample_term]
    check(isinstance(postings, list) and len(postings) > 0,
          f"sample term '{sample_term[:30]}' has {len(postings)} postings")
    if postings:
        check(isinstance(postings[0], list) and len(postings[0]) == 2,
              f"sample term '{sample_term[:30]}' postings are [idx, tf] pairs")
        # All doc_idx in postings should be < total_chunks
        ok = all(0 <= p[0] < meta.get("total_chunks", 0) for p in postings)
        check(ok, f"sample term '{sample_term[:30]}' doc_idx in valid range")

if errors:
    print(f"\nFAILED ({len(errors)} errors):")
    for e in errors:
        print(e)
    sys.exit(1)
print("\nALL OK")
