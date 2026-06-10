#!/usr/bin/env python3
"""Build static JSON search index from Discord archive chunks.

Reads chunks.jsonl and produces:
  - search-data-0.json, search-data-1.json, ...  (sharded chunk data + envelope)
  - search-index-0.json, search-index-1.json, ... (sharded BM25 inverted index)
  - metadata.json     (build timestamp + counts + shard counts)

Usage: python3 build_index.py [chunks.jsonl_path]

Files are sharded so each on-disk JSON stays under Cloudflare Pages' 25 MiB
per-file limit. Shard counts are written into metadata.json so the client
knows how many files to fetch. Chunks are split by count (uniform size);
terms are split by total byte size (heavily skewed distribution).
"""
import json
import math
import os
import re
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_CHUNKS = Path("/mnt/homes/galileo/argo/Development/nous-discord-archive/tools/chunks.jsonl")
OUTPUT_DIR = SCRIPT_DIR

# Cloudflare Pages hard limit is 25 MiB per file. We target 24 MiB to leave
# a 1 MiB safety margin and account for any rounding / wrapper overhead.
MAX_SHARD_BYTES = 24 * 1024 * 1024


def now_iso():
    """Timezone-aware UTC timestamp, ISO 8601 with 'Z' suffix."""
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def estimate_bytes_per_item(items, sample_size=50, seed=0):
    """Average JSON-encoded size per item, sampled for speed.

    Terms/items are often heavily skewed (a few huge ones, many tiny ones),
    so we sample uniformly at random across the full list rather than taking
    the first N — that biases the estimate toward whichever items happened to
    be inserted first.
    """
    if not items:
        return 1024
    n = len(items)
    if n <= sample_size:
        sample = items
    else:
        import random
        rng = random.Random(seed)
        sample = [items[i] for i in rng.sample(range(n), sample_size)]
    encoded = len(json.dumps(sample, separators=(",", ":")).encode("utf-8"))
    return max(1, encoded / len(sample))


def pick_shard_count(items, max_bytes=MAX_SHARD_BYTES):
    """Pick the smallest N >= 1 such that N shards stay under max_bytes each.

    Note: this only uses a random-sample average. For heavily skewed item-size
    distributions (like the BM25 terms dict) prefer split_by_size() and
    compute the count from the resulting shard count.
    """
    avg = estimate_bytes_per_item(items)
    estimated_total = avg * len(items)
    return max(1, math.ceil(estimated_total / max_bytes))


def split_list(items, n):
    """Split a list into n contiguous shards of roughly equal size."""
    if n <= 1 or not items:
        return [items] if items else [[]]
    base, rem = divmod(len(items), n)
    shards, start = [], 0
    for i in range(n):
        end = start + base + (1 if i < rem else 0)
        shards.append(items[start:end])
        start = end
    return shards


def _item_byte_size(item):
    """Best-effort byte size of one item when serialized as JSON."""
    return len(json.dumps(item, separators=(",", ":")).encode("utf-8"))


def split_by_size(items, sizes, max_bytes):
    """Split items into shards, each <= max_bytes (sum of corresponding sizes).

    Items are kept in their original order; the cut points are chosen so that
    every shard's total size is bounded by max_bytes. This produces a number
    of shards that is sometimes slightly more than ceil(total/max_bytes) when
    one item alone exceeds max_bytes — those large items get their own shard.

    Use this when items are heavily skewed (e.g., the BM25 terms dict: a few
    common terms have thousands of postings; most have a handful). Plain
    count-based split would dump the giants into one shard and leave the
    rest nearly empty.
    """
    if not items:
        return [[]]
    assert len(items) == len(sizes), "items and sizes must be parallel"
    shards, current, current_bytes = [], [], 0
    for it, sz in zip(items, sizes):
        if current and current_bytes + sz > max_bytes:
            shards.append(current)
            current, current_bytes = [], 0
        current.append(it)
        current_bytes += sz
    if current:
        shards.append(current)
    return shards


def _max_shard_bytes(shards_items):
    """Byte size of the largest shard, summing the serialized size of its items."""
    return max(
        (sum(_item_byte_size(it) for it in shard) for shard in shards_items),
        default=0,
    )


BOT_NAMES = {
    "translator bot#1043", "translator bot", "fizbott", "mee6",
    "dyno", "carl-bot", "carl bot",
}

SAMPLE_QUERIES = [
    "hermes plugin setup", "GRPO training fine-tuning", "telegram bot issues",
    "memory system cortex", "computer use", "Axolotl Unsloth",
    "OBLITERATUS refusal removal", "dashboard configuration",
    "MCP server setup", "skill authoring", "Docker deployment",
    "WandB experiment tracking",
]

def is_bot(name):
    return name.lower() in BOT_NAMES

def tokenize(text):
    """Simple tokenizer: lowercase, split on non-alphanumeric, filter short."""
    return [w for w in re.split(r'[^a-z0-9]+', text.lower()) if len(w) > 1]

def tokenize_with_authors(chunk):
    """Tokenize chunk text + author names so searching a username finds their chunks."""
    text = chunk.get("text", "") or ""
    authors = chunk.get("authors", []) or []
    enriched = text + " " + " ".join(authors)
    return tokenize(enriched)


def build_inverted_index(chunks_data):
    """Build a simple inverted index for BM25 search."""
    terms = {}  # term -> {doc_idx: tf}
    doc_lengths = []

    for i, chunk in enumerate(chunks_data):
        tokens = tokenize_with_authors(chunk)
        doc_lengths.append(len(tokens))
        tf = Counter(tokens)
        for term, count in tf.items():
            if term not in terms:
                terms[term] = {}
            terms[term][i] = count

    avg_dl = sum(doc_lengths) / len(doc_lengths) if doc_lengths else 0
    return {
        "terms": terms,
        "doc_count": len(chunks_data),
        "avg_dl": avg_dl,
        "docs": [{"id": c["id"], "len": dl} for c, dl in zip(chunks_data, doc_lengths)],
    }

def extract_trending(chunks, count=12):
    """Extract trending terms from recent chunks using simple TF."""
    from collections import Counter

    if not chunks:
        return SAMPLE_QUERIES

    now = datetime.now(timezone.utc)
    recent_texts = []
    for c in chunks:
        et = c.get("end_time", "") or c.get("start_time", "")
        if et:
            try:
                d = datetime.fromisoformat(et.replace("Z", "+00:00"))
                if (now - d).days <= 7:
                    recent_texts.append(c.get("text", ""))
            except (ValueError, TypeError):
                recent_texts.append(c.get("text", ""))

    if len(recent_texts) < 50:
        sorted_chunks = sorted(
            chunks,
            key=lambda c: c.get("end_time", "") or c.get("start_time", "") or "",
            reverse=True
        )
        recent_texts = [c.get("text", "") for c in sorted_chunks[:200]]

    stopwords = {
        "the","and","for","with","that","this","from","have","will","just",
        "about","what","when","they","your","you","are","can","not","but","all","any",
        "our","has","had","was","were","been","into","out","like","make","get","use",
        "work","know","think","take","come","way","good","more","some","time","very",
        "now","than","then","only","also","could","should","would","thats","dont",
        "im","ive","hes","shes","lets","https","http","www","com","bot","user",
        "msg","id","num","val","etc","api","url","json"
    }

    word_counts = Counter()
    for text in recent_texts:
        words = [
            w for w in re.split(r"[^a-z0-9]+", text.lower())
            if len(w) > 3 and w not in stopwords and not w.isdigit()
        ]
        for w in words:
            word_counts[w] += 1

    top_words = [w for w, _ in word_counts.most_common(30)]
    phrases = []
    for text in recent_texts:
        tl = text.lower()
        for i in range(len(top_words) - 1):
            phrase = f"{top_words[i]} {top_words[i+1]}"
            if phrase in tl and phrase not in phrases:
                phrases.append(phrase)
                if len(phrases) >= count:
                    break
        if len(phrases) >= count:
            break

    return phrases[:count] if len(phrases) >= count else SAMPLE_QUERIES[:count]


def assign_medal(rank, name):
    """One medal per user based on rank. Founder gets Caduceus."""
    if name == "teknium":
        return ["caduceus", "Founder"]
    if rank is None:
        return ["ribbon", "Ribbon"]
    if rank <= 10:
        return ["gold", "Gold"]
    if rank <= 25:
        return ["silver", "Silver"]
    if rank <= 50:
        return ["bronze", "Bronze"]
    return ["ribbon", "Ribbon"]


def assign_awards(user_stats):
    """Stable contribution awards from lifetime stats. Only go up."""
    awards = []
    channels = len(user_stats.get("channels", set()) or set())
    first_seen = user_stats.get("first_seen", "") or ""
    max_streak = user_stats.get("max_streak", 0) or 0
    dates_active = user_stats.get("dates_active", set()) or set()
    msg_per_chunk = user_stats.get("msg_per_chunk", 0) or 0

    if channels >= 3:
        awards.append(["explorer", "Explorer"])
    if first_seen and first_seen <= "2025-01-01":
        awards.append(["pioneer", "Pioneer"])
    if max_streak >= 30:
        awards.append(["streak", "Streak"])
    if len(dates_active) >= 50:
        awards.append(["pillar", "Pillar"])
    if msg_per_chunk > 15:
        awards.append(["sage", "Sage"])
    if channels >= 4:
        awards.append(["diplomat", "Diplomat"])

    return awards[:2]


def main():
    chunks_path = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_CHUNKS
    start_time = time.time()

    print(f"Reading {chunks_path}...", file=sys.stderr)
    chunks = []
    with open(chunks_path) as f:
        for i, line in enumerate(f):
            if i % 2000 == 0:
                print(f"  {i} chunks...", file=sys.stderr)
            try:
                chunks.append(json.loads(line.strip()))
            except json.JSONDecodeError:
                pass
    print(f"  Loaded {len(chunks)} chunks.", file=sys.stderr)

    # Pre-compute stats
    date_counts = Counter()
    channel_counts = Counter()
    author_counts = Counter()
    channel_dates = {}
    author_dates = {}
    channel_author_counts = {}

    for c in chunks:
        st = c.get("start_time", "")
        ch = c.get("channel", "unknown")
        chunk_date = str(st)[:10] if st else ""

        if chunk_date:
            date_counts[chunk_date] += 1
        channel_counts[ch] += 1

        if ch not in channel_dates:
            channel_dates[ch] = Counter()
        if chunk_date:
            channel_dates[ch][chunk_date] += 1

        for author in c.get("authors", []):
            if is_bot(author):
                continue
            author_counts[author] += 1
            if ch not in channel_author_counts:
                channel_author_counts[ch] = Counter()
            channel_author_counts[ch][author] += 1
            if author not in author_dates:
                author_dates[author] = Counter()
            if chunk_date:
                author_dates[author][chunk_date] += 1

    # Build leaderboard
    leaderboard = []
    all_author_stats = {}
    for c in chunks:
        ch = c.get("channel", "")
        chunk_date = str(c.get("start_time", ""))[:10] or ""
        for author in c.get("authors", []):
            if is_bot(author):
                continue
            if author not in all_author_stats:
                all_author_stats[author] = {
                    "name": author, "total_chunks": 0, "channels": set(),
                    "first_seen": "9999", "last_seen": "", "total_messages": 0,
                    "dates_active": set(), "max_streak": 0, "msg_per_chunk": 0.0,
                }
            s = all_author_stats[author]
            s["total_chunks"] += 1
            s["channels"].add(ch)
            s["total_messages"] += len(c.get("messages", []))
            st = c.get("start_time", "")
            if st:
                if st < s["first_seen"]:
                    s["first_seen"] = st
                if st > s["last_seen"]:
                    s["last_seen"] = st
            if chunk_date:
                s["dates_active"].add(chunk_date)

    # Compute per-user max_streak and msg_per_chunk
    for _author, _s in all_author_stats.items():
        if _s["dates_active"]:
            sorted_dates = sorted(_s["dates_active"])
            max_streak = current = 1
            for i in range(1, len(sorted_dates)):
                try:
                    d1 = datetime.strptime(sorted_dates[i - 1], "%Y-%m-%d")
                    d2 = datetime.strptime(sorted_dates[i], "%Y-%m-%d")
                    if (d2 - d1).days == 1:
                        current += 1
                        max_streak = max(max_streak, current)
                    else:
                        current = 1
                except ValueError:
                    current = 1
            _s["max_streak"] = max_streak
        if _s["total_chunks"] > 0:
            _s["msg_per_chunk"] = _s["total_messages"] / _s["total_chunks"]

    # Build leaderboard with rank-aware medals + stable awards
    _ranked = sorted(all_author_stats.items(), key=lambda kv: kv[1]["total_chunks"], reverse=True)
    for rank_idx, (author, s) in enumerate(_ranked, start=1):
        tc = s["total_chunks"]
        medal = assign_medal(rank_idx, author)
        awards = assign_awards(s)
        leaderboard.append({
            "name": author, "total_chunks": tc,
            "total_messages": s["total_messages"],
            "channels_active": len(s["channels"]),
            "first_seen": s["first_seen"][:10] if s["first_seen"] else "",
            "last_seen": s["last_seen"][:10] if s["last_seen"] else "",
            "max_streak": s["max_streak"],
            "msg_per_chunk": round(s["msg_per_chunk"], 2),
            "active_days": len(s["dates_active"]),
            "medal": medal,
            "awards": awards,
        })

    # Per-user mood context (T14a) — last 20 chunks sorted by recency
    for entry in leaderboard:
        user_chunks_list = [c for c in chunks if entry["name"] in (c.get("authors") or [])]
        user_chunks_sorted = sorted(
            user_chunks_list,
            key=lambda c: c.get("start_time", ""),
            reverse=True
        )
        entry["mood_context_chunks"] = [
            {
                "id": c["id"],
                "channel": c.get("channel", ""),
                "start_time": c.get("start_time", ""),
                "text_preview": (c.get("text", "") or "")[:500],
            }
            for c in user_chunks_sorted[:20]
        ]

    # Pre-compute extra chart data (T8)
    _day_of_week = {0: 0, 1: 0, 2: 0, 3: 0, 4: 0, 5: 0, 6: 0}
    _hour_day_heatmap = [[0] * 7 for _ in range(24)]
    _channel_daily = {}
    _seen_author_dates = set()
    for c in chunks:
        st = c.get("start_time", "")
        if not st:
            continue
        d_date = str(st)[:10]
        try:
            d_obj = datetime.strptime(d_date, "%Y-%m-%d")
            _day_of_week[d_obj.weekday()] = _day_of_week.get(d_obj.weekday(), 0) + 1
            _hour_day_heatmap[d_obj.hour][d_obj.weekday()] += 1
        except ValueError:
            pass
        ch = c.get("channel", "unknown")
        if ch not in _channel_daily:
            _channel_daily[ch] = {}
        _channel_daily[ch][d_date] = _channel_daily[ch].get(d_date, 0) + 1
        for a in c.get("authors", []):
            if not is_bot(a):
                _seen_author_dates.add((a, d_date))

    # Cumulative contributor growth
    _sorted_dates = sorted({d for _, d in _seen_author_dates})
    _contributor_growth = {}
    _running = set()
    for d in _sorted_dates:
        for a, dd in _seen_author_dates:
            if dd <= d:
                _running.add(a)
        _contributor_growth[d] = len(_running)

    # Top 3 channels for the time series
    _top3 = sorted(channel_counts.items(), key=lambda x: x[1], reverse=True)[:3]
    _top3_channel_daily = {ch: _channel_daily.get(ch, {}) for ch, _ in _top3}
    _dow_labels = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']
    _day_of_week_named = {_dow_labels[k]: v for k, v in _day_of_week.items()}

    # Build streaks
    streaks = []
    for author, dates_count in author_dates.items():
        sorted_dates = sorted(dates_count.keys())
        if not sorted_dates:
            continue
        max_streak = 1
        current_streak = 1
        for i in range(1, len(sorted_dates)):
            from datetime import timedelta
            d1 = datetime.strptime(sorted_dates[i - 1], "%Y-%m-%d")
            d2 = datetime.strptime(sorted_dates[i], "%Y-%m-%d")
            if (d2 - d1).days == 1:
                current_streak += 1
                max_streak = max(max_streak, current_streak)
            else:
                current_streak = 1
        streaks.append({"name": author, "max_streak": max_streak, "total_days": len(sorted_dates)})
    streaks.sort(key=lambda x: x["max_streak"], reverse=True)

    # Top days
    top_days = [{"date": d, "count": c} for d, c in date_counts.most_common(30)]

    # Per-channel leaderboards
    channel_leaderboards = {}
    for ch, counter in channel_author_counts.items():
        channel_leaderboards[ch] = [{"name": a, "chunks": cnt} for a, cnt in counter.most_common(30)]

    # Prepare chunk data for JSON (limit size)
    chunks_data = []
    total_messages = 0
    for c in chunks:
        msgs = c.get("messages", [])
        total_messages += len(msgs)
        chunks_data.append({
            "id": c.get("id", ""),
            "text": c.get("text", "")[:2000],
            "channel": c.get("channel", ""),
            "authors": c.get("authors", []),
            "start_time": c.get("start_time", ""),
            "end_time": c.get("end_time", ""),
            "type": c.get("type", ""),
            "message_count": len(msgs),
        })

    print("Building search index...", file=sys.stderr)
    search_index = build_inverted_index(chunks_data)

    # Serialize search index terms efficiently
    # Convert dict keys to lists for JSON
    terms_list = {}
    for term, docs in search_index["terms"].items():
        terms_list[term] = [[idx, tf] for idx, tf in docs.items()]

    elapsed = time.time() - start_time

    # Pick shard counts so each file stays under MAX_SHARD_BYTES.
    # search-data sharding splits the chunks list. Each shard carries the full
    # stats/leaderboard envelope (~2 MB of metadata) plus its chunk subset, so
    # we budget for the wrapper explicitly. Chunks are roughly uniform in size
    # so count-based split is fine.
    # search-index sharding splits the terms dict. The terms dict is heavily
    # skewed (a few common terms have thousands of postings; most have a
    # handful), so we use a size-balanced split (linear pass, cumulative byte
    # total). doc_count/avg_dl/docs (~1 MB) only live in shard 0.
    WRAPPER_BUDGET_DATA = 4 * 1024 * 1024   # envelope: stats + leaderboard + …
    WRAPPER_BUDGET_INDEX = 2 * 1024 * 1024  # shard-0 extras: doc_count + docs
    # Terms and chunks are each constrained to (MAX - WRAPPER) bytes so that
    # the final on-disk file (terms + envelope) stays under MAX. Wrappers are
    # measured against the previous build; budgets have headroom for modest
    # data growth between builds.

    # Chunks: uniform, count-based split is fine. Estimate avg chunk size
    # from a uniform random sample, then pick N.
    avg_chunk_bytes = estimate_bytes_per_item(chunks_data, sample_size=200, seed=1)
    n_data_shards = max(1, math.ceil(
        (avg_chunk_bytes * len(chunks_data)) / (MAX_SHARD_BYTES - WRAPPER_BUDGET_DATA)
    ))
    chunks_shards = split_list(chunks_data, n_data_shards)
    print(f"Data shard plan: {n_data_shards} shards "
          f"(avg chunk {avg_chunk_bytes:.0f} B, {len(chunks_data)} chunks)",
          file=sys.stderr)

    # Terms: heavily skewed, must use size-balanced split. Compute per-term
    # serialized size, then cut shards at cumulative byte boundaries.
    terms_items = list(terms_list.items())
    # Each shard carries its own terms subset plus shard-0 carries `docs`. We
    # precompute per-term sizes once and feed them to split_by_size in one
    # linear pass.
    term_sizes = [_item_byte_size(tp) for tp in terms_items]
    terms_shards_items = split_by_size(
        terms_items, term_sizes, MAX_SHARD_BYTES - WRAPPER_BUDGET_INDEX
    )
    n_index_shards = len(terms_shards_items)
    terms_total_bytes = sum(term_sizes)
    print(f"Index shard plan: {n_index_shards} shards "
          f"(total {terms_total_bytes / 1024 / 1024:.1f} MB terms, "
          f"largest shard {_max_shard_bytes(terms_shards_items) / 1024 / 1024:.1f} MB)",
          file=sys.stderr)

    # Remove any stale monolithic outputs from previous (pre-shard) builds.
    for legacy in ("search-data.json", "search-index.json"):
        legacy_path = OUTPUT_DIR / legacy
        if legacy_path.exists():
            legacy_path.unlink()

    last_updated = now_iso()

    # Write sharded search-data files
    for i, shard_chunks in enumerate(chunks_shards):
        out = {
            "shard": i,
            "total_shards": n_data_shards,
            "last_updated": last_updated,
            "chunks": shard_chunks,
            "stats": {
                "total_chunks": len(chunks),
                "total_messages": total_messages,
                "channels": dict(channel_counts),
                "date_range": {
                    "from": min(date_counts.keys()) if date_counts else "",
                    "to": max(date_counts.keys()) if date_counts else "",
                },
                "total_dates": len(date_counts),
                "daily_counts": dict(date_counts),
                "day_of_week": _day_of_week_named,
                "hour_day_heatmap": _hour_day_heatmap,
                "contributor_growth": _contributor_growth,
                "channel_daily": _top3_channel_daily,
                "unique_authors": len(author_counts),
                "top_authors": [{"name": a, "count": c} for a, c in author_counts.most_common(20)],
            },
            "leaderboard": leaderboard[:100],
            "streaks": streaks[:30],
            "top_days": top_days,
            "channel_leaderboards": channel_leaderboards,
            "sample_queries": extract_trending(chunks),
        }
        path = OUTPUT_DIR / f"search-data-{i}.json"
        with open(path, "w") as f:
            json.dump(out, f, separators=(",", ":"))
        print(f"  {path.name}: {os.path.getsize(path) / 1024 / 1024:.1f} MB",
              file=sys.stderr)

    # Write sharded search-index files
    for i, shard_items in enumerate(terms_shards_items):
        out = {
            "shard": i,
            "total_shards": n_index_shards,
            "terms": dict(shard_items),
        }
        # Put doc_count/avg_dl/docs in shard 0 only (everything else reads them
        # from there during reassembly).
        if i == 0:
            out["doc_count"] = search_index["doc_count"]
            out["avg_dl"] = search_index["avg_dl"]
            out["docs"] = search_index["docs"]
        path = OUTPUT_DIR / f"search-index-{i}.json"
        with open(path, "w") as f:
            json.dump(out, f, separators=(",", ":"))
        print(f"  {path.name}: {os.path.getsize(path) / 1024 / 1024:.1f} MB",
              file=sys.stderr)

    # Write metadata.json with shard counts so the client knows how many
    # search-data-N.json / search-index-N.json files to fetch.
    print("Writing metadata.json...", file=sys.stderr)
    metadata = {
        "last_updated": last_updated,
        "build_time_seconds": round(elapsed, 1),
        "total_chunks": len(chunks),
        "total_messages": total_messages,
        "total_authors": len(author_counts),
        "date_range": {
            "from": min(date_counts.keys()) if date_counts else "",
            "to": max(date_counts.keys()) if date_counts else "",
        },
        "shards": {
            "data": n_data_shards,
            "index": n_index_shards,
        },
    }
    with open(OUTPUT_DIR / "metadata.json", "w") as f:
        json.dump(metadata, f, indent=2)
    print(f"  metadata.json: {os.path.getsize(OUTPUT_DIR / 'metadata.json') / 1024 / 1024:.2f} MB",
          file=sys.stderr)

    print(f"\nDone in {elapsed:.1f}s. {len(chunks)} chunks indexed.", file=sys.stderr)
    print(json.dumps(metadata))

if __name__ == "__main__":
    main()
