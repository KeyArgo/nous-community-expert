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
import base64
import gzip
import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tarfile
import time
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_CHUNKS = Path(os.environ.get("ARCHIVE_CHUNKS", ""))
if not str(DEFAULT_CHUNKS):
    # Fallback: archive is a sibling of this repo, under ../nous-discord-archive
    DEFAULT_CHUNKS = (SCRIPT_DIR.parent / "nous-discord-archive" / "tools" / "chunks.jsonl")
OUTPUT_DIR = SCRIPT_DIR

# ─── Version + build metadata ──────────────────────────────────────
# VERSION file holds the semver (manually bumped for minor/major releases).
# Build number is auto-derived from git commit count (auto-increments per commit).
# Combined display: "0.2.0 (build 42)" or PEP 440 "0.2.0+42".
def _read_version():
    version_path = SCRIPT_DIR / "VERSION"
    if version_path.exists():
        return version_path.read_text().strip()
    return "0.0.0+unknown"

def _read_git_meta():
    """Return (commit_sha, commit_count, current_branch) or safe defaults."""
    import subprocess
    def _run(args):
        try:
            r = subprocess.run(["git", "-C", str(SCRIPT_DIR)] + args,
                              capture_output=True, text=True, timeout=5)
            return r.stdout.strip() if r.returncode == 0 else ""
        except Exception:
            return ""
    sha = _run(["rev-parse", "HEAD"])
    count = _run(["rev-list", "--count", "HEAD"])
    branch = _run(["rev-parse", "--abbrev-ref", "HEAD"])
    return (sha[:7] if sha else "unknown", int(count) if count else 0, branch or "unknown")

VERSION = _read_version()
GIT_SHA, BUILD_NUMBER, GIT_BRANCH = _read_git_meta()

# Cloudflare Pages hard limit is 25 MiB per file. We target 24 MiB to leave
# a 1 MiB safety margin and account for any rounding / wrapper overhead.
MAX_SHARD_BYTES = 24 * 1024 * 1024

# ─── ISSUE #6: Rolling backup constants ─────────────────────────────
BACKUP_ENABLED = True
BACKUP_DIR = "dist/backups"
BACKUP_RETENTION_WEEKS = 52       # delete archived weeks older than this
BACKUP_COMPRESS_AFTER_WEEKS = 4   # gzip individual .jsonl in archived weeks older than this
BACKUP_TARBALL_WEEKLY = True      # tar.gz a week's archive once that week is over

# ─── T9/T10: Brand & sentiment constants ────────────────────────────
POSITIVE_WORDS = {"good", "great", "love", "best", "amazing", "awesome", "excellent",
                  "favorite", "top tier", "sota", "insane", "impressive", "beautiful",
                  "fast", "correct", "smart", "elegant", "robust"}
NEGATIVE_WORDS = {"bad", "sucks", "worst", "overrated", "trash", "garbage", "cope",
                  "biased", "lying", "dying", "dead", "shit", "cringe", "mediocre",
                  "lazy", "woke", "broken", "wrong", "terrible", "awful"}

BRAND_KEYWORDS = {
    "claude":   ["claude", "anthropic"],
    "gpt":      ["gpt", "openai", "chatgpt", "closedai"],
    "gemini":   ["gemini", "bard", "deepmind"],
    "llama":    ["llama", "meta-llama"],
    "grok":     ["grok", "xai"],
    "mistral":  ["mistral", "mixtral", "mistral.ai"],
    "cohere":   ["cohere", "command-r"],
    "qwen":     ["qwen", "tongyi", "dashscope", "alibaba"],
    "deepseek": ["deepseek"],
    "mimo":     ["mimo", "xiaomi"],
    "glm":      ["glm", "zhipu", "chatglm"],
    "kimi":     ["kimi", "moonshot"],
    "yi":       ["yi\\b", "01-ai"],
    "baichuan": ["baichuan"],
    "internlm": ["internlm"],
    "hermes":   ["hermes", "hermes-agent", "teknium"],
    "nous":     ["nous research", "nousresearch", "nousresearch/hermes-agent"],
    "capybara": ["capybara"],
    "slerp":    ["slerp", "model merging", "mergekit"],
    "dare":     ["\\bdare\\b", "dare merge", "drop and rescale"],
    "distro":   ["distro", "model stock", "model soup"],
    "grpo":     ["grpo", "group relative"],
    "dpo":      ["\\bdpo\\b", "direct preference"],
    "moe":      ["mixture of experts", "\\bmoe\\b", "sparse expert"],
    "rlhf":     ["\\brlhf\\b"],
    "quant":    ["q4_k_m", "q5_k_m", "gguf", "awq", "gptq", "quantization"],
    "vllm":     ["vllm", "pagedattention"],
    "llamacpp": ["llama.cpp", "ollama", "lm studio"],
    "arxiv":    ["arxiv"],
    "cuda":     ["\\bcuda\\b", "cudnn"],
    "rocm":     ["rocm", "\\bhip\\b", "amd gpu"],
    "kvcache":  ["kv cache", "paged attention"],
    "python":   ["import torch", "from transformers"],
    "huggingface": ["huggingface", "hugging face", "\\bhf\\b"],
    "axolotl":  ["axolotl", "winglian"],
    "unsloth":  ["unsloth"],
    "sillytavern": ["sillytavern", "st\\b"],
    "oobabooga": ["oobabooga", "text-generation-webui", "text gen webui"],
    "koboldai": ["koboldai", "koboldcpp", "kobold.cpp", "kobold"],
}

BRAND_META = {
    "claude":      ("🤖", "Claude",      "Frontier"),
    "gpt":         ("⚡", "GPT/ClosedAI", "Frontier"),
    "gemini":      ("🔮", "Gemini",      "Frontier"),
    "llama":       ("🦙", "Llama",       "Frontier"),
    "grok":        ("⚡", "Grok",        "Frontier"),
    "mistral":     ("🌀", "Mistral",     "Frontier"),
    "cohere":      ("🟪", "Cohere",      "Frontier"),
    "qwen":        ("🐉", "Qwen",        "Chinese"),
    "deepseek":    ("🐳", "DeepSeek",    "Chinese"),
    "mimo":        ("🦾", "MiMo",        "Chinese"),
    "glm":         ("🦄", "GLM",         "Chinese"),
    "kimi":        ("🌙", "Kimi",        "Chinese"),
    "yi":          ("🌐", "Yi",          "Chinese"),
    "baichuan":    ("📚", "Baichuan",    "Chinese"),
    "internlm":    ("🏯", "InternLM",    "Chinese"),
    "hermes":      ("🌶️", "Hermes",      "On-brand"),
    "nous":        ("🧠", "Nous",        "On-brand"),
    "capybara":    ("🦫", "Capybara",    "On-brand"),
    "slerp":       ("🌀", "SLERP",       "On-brand"),
    "dare":        ("🎲", "DARE",        "On-brand"),
    "distro":      ("🍲", "Distro/Soup", "On-brand"),
    "grpo":        ("📜", "GRPO",        "Techniques"),
    "dpo":         ("🔥", "DPO",         "Techniques"),
    "moe":         ("🧪", "MoE",         "Techniques"),
    "rlhf":        ("🎓", "RLHF",        "Techniques"),
    "quant":       ("💧", "Quant",       "Tooling"),
    "vllm":        ("⚡", "vLLM",        "Tooling"),
    "llamacpp":    ("🦀", "llama.cpp",   "Tooling"),
    "arxiv":       ("📑", "ArXiv",       "Tooling"),
    "cuda":        ("🌊", "CUDA",        "Tooling"),
    "rocm":        ("💀", "ROCm",        "Tooling"),
    "kvcache":     ("📐", "KV Cache",    "Tooling"),
    "python":      ("🐍", "Python",      "Tooling"),
    "huggingface": ("🤗", "HuggingFace", "Tooling"),
    "axolotl":     ("🦎", "Axolotl",     "Tooling"),
    "unsloth":     ("⚡", "Unsloth",     "Tooling"),
    "sillytavern": ("🎭", "SillyTavern", "Tooling"),
    "oobabooga":   ("📜", "Oobabooga",   "Tooling"),
    "koboldai":    ("🐲", "KoboldAI",    "Tooling"),
}

# ─── v0.3.0: Brand detection constants ──────────────────────────────
# ISSUE #5: All thresholds are named constants for easy tuning.
BRAND_MENTION_THRESHOLD = 10   # Minimum brand mentions before evaluating shill/hater (was 5)
SHILL_MIN_POS_RATIO = 5.0     # Min positive:negative ratio to qualify as shill (was 3:1)
HATER_MIN_NEG_RATIO = 5.0     # Min negative:positive ratio to qualify as hater (was 1:3)
USE_WORD_BOUNDARY = True       # Use word-boundary regex in brand keyword matching

# ─── v0.4.0: Title assignment rules (ISSUE #1) ─────────────────────
def _peak_hour_bucket(s):
    """Return time-of-day bucket name or None."""
    hour_dist = s.get("hour_dist", {}) or {}
    if not hour_dist:
        return None
    peak = max(hour_dist, key=hour_dist.get)
    if peak < 6:
        return "early_bird"
    if peak < 12:
        return "morning"
    if peak < 14:
        return "lunch"
    if peak < 18:
        return "afternoon"
    return "night_owl"

TITLE_RULES = [
    # (priority, name, emoji, check_fn(stats_dict, rank_idx))
    # Lower number = more specific, checked first
    (1, "Foundation", "\U0001F451",
     lambda s, r: r is not None and r <= TITLE_FOUNDATION_RANK),
    (2, "OG", "\U0001F3DB\uFE0F",
     lambda s, r: ((s.get("first_seen") or "9999")[:10] < TITLE_OG_FIRST_SEEN
                   and (s.get("total_chunks") or 0) >= TITLE_OG_MIN_CHUNKS)),
    (3, "Veteran", "\U0001F396\uFE0F",
     lambda s, r: ((s.get("first_seen") or "9999")[:10] < TITLE_VETERAN_FIRST_SEEN
                   and (s.get("max_streak") or 0) >= TITLE_VETERAN_MIN_STREAK)),
    (4, "Pioneer", "\U0001F48E",
     lambda s, r: ((s.get("first_seen") or "9999")[:10] < TITLE_PIONEER_FIRST_SEEN)),
    (5, "Globetrotter", "\U0001F30D",
     lambda s, r: (len(s.get("channels", set()) or set()) >= TITLE_GLOBETROTTER_MIN_CHANNELS
                   and (s.get("total_chunks") or 0) >= TITLE_GLOBETROTTER_MIN_CHUNKS)),
    (6, "Trailblazer", "\U0001F680",
     lambda s, r: (s.get("total_chunks") or 0) >= TITLE_TRAILBLAZER_MIN_CHUNKS),
    (7, "Discord Whisperer", "\U0001F5E3\uFE0F",
     lambda s, r: ((s.get("msg_per_chunk") or 0) > TITLE_WHISPERER_MIN_MSG_RATIO
                   and (s.get("total_chunks") or 0) >= TITLE_WHISPERER_MIN_CHUNKS)),
    (8, "Polymath", "\U0001F52C",
     lambda s, r: len(s.get("channels", set()) or set()) >= TITLE_POLYMATH_MIN_CHANNELS),
    (9, "Sage", "\U0001F9E0",
     lambda s, r: ((s.get("msg_per_chunk") or 0) > TITLE_SAGE_MIN_MSG_PER_CHUNK
                   and (s.get("total_chunks") or 0) >= TITLE_SAGE_MIN_CHUNKS)),
    (10, "Night Owl", "\U0001F989",
     lambda s, r: _peak_hour_bucket(s) == "night_owl"),
    (11, "Early Bird", "\U0001F305",
     lambda s, r: _peak_hour_bucket(s) == "early_bird"),
    (12, "Morning Poster", "\u2615",
     lambda s, r: _peak_hour_bucket(s) == "morning"),
    (13, "Lunch Break", "\U0001F96A",
     lambda s, r: _peak_hour_bucket(s) == "lunch"),
    (14, "Afternoon Poster", "\U0001F31E",
     lambda s, r: _peak_hour_bucket(s) == "afternoon"),
    (15, "Lurker", "\U0001F575\uFE0F",
     lambda s, r: ((s.get("total_chunks") or 0) >= TITLE_LURKER_MIN_CHUNKS
                   and (s.get("msg_per_chunk") or 0) < TITLE_LURKER_MAX_MSG_RATIO)),
    (16, "Linker", "\U0001F517",
     lambda s, r: (s.get("link_rate") or 0) > TITLE_LINKER_MIN_RATE),
    (17, "Code Wizard", "\U0001F9D9",
     lambda s, r: (s.get("code_rate") or 0) > TITLE_CODE_WIZARD_MIN_RATE),
    (18, "Builder", "\U0001F3D7\uFE0F",
     lambda s, r: (s.get("total_chunks") or 0) >= TITLE_BUILDER_MIN_CHUNKS),
]

# ─── v0.3.0: GPU tier emojis (ISSUE #8, #17) ───────────────────────
GPU_TIER_EMOJI = {
    "frontier": "\U0001F3C6",
    "tpu": "\U0001F525",
    "datacenter": "\U0001F5A5\uFE0F",
    "workstation": "\u26A1",
    "consumer": "\U0001F3AE",
    "multi": "\U0001F517",
    "edge": "\U0001F4E1",
    "integrated": "\U0001F4BB",
}

# ─── v0.4.0: Title rule thresholds (ISSUE #1) ────────────
TITLE_FOUNDATION_RANK = 5
TITLE_OG_FIRST_SEEN = "2025-01-01"
TITLE_OG_MIN_CHUNKS = 50
TITLE_PIONEER_FIRST_SEEN = "2025-01-01"
TITLE_VETERAN_FIRST_SEEN = "2025-06-01"
TITLE_VETERAN_MIN_STREAK = 30
TITLE_TRAILBLAZER_MIN_CHUNKS = 100
TITLE_BUILDER_MIN_CHUNKS = 50
TITLE_POLYMATH_MIN_CHANNELS = 4
TITLE_SAGE_MIN_MSG_PER_CHUNK = 15
TITLE_SAGE_MIN_CHUNKS = 10
TITLE_WHISPERER_MIN_MSG_RATIO = 20
TITLE_WHISPERER_MIN_CHUNKS = 20
TITLE_LURKER_MIN_CHUNKS = 50
TITLE_LURKER_MAX_MSG_RATIO = 3
TITLE_LINKER_MIN_RATE = 0.15
TITLE_CODE_WIZARD_MIN_RATE = 0.3
TITLE_EMOJI_MASTER_MIN_RATE = 0.4
TITLE_QUESTIONER_MIN_RATE = 0.4
TITLE_GLOBETROTTER_MIN_CHANNELS = 4
TITLE_GLOBETROTTER_MIN_CHUNKS = 50

# ─── v0.5.0: New-user handling thresholds (ISSUE #7) ───────
# Users below this many chunks get no rank-based medal/title (ribbon only,
# no title) — keeps brand-new arrivals from landing on gold/silver/bronze
# in small communities just because few other users exist yet.
MIN_CHUNKS_FOR_RANK = 5

# ─── v0.4.0: Brand lover/hater thresholds (ISSUE #13) ──────
BRAND_LOVER_AWARD_MIN_TOTAL = 10
BRAND_LOVER_AWARD_MIN_POS_RATIO = 3.0
BRAND_HATER_AWARD_MIN_TOTAL = 10
BRAND_HATER_AWARD_MIN_NEG_RATIO = 3.0

# ─── v0.4.0: Known community roles (ISSUE #15) ─────────────
MOD_USERS = {"teknium", "promptsiren", ".s0uthpaw"}  # v0.2.1: removed 4rgo (user is not a mod)
DEVELOPER_USERS = {"4rgo", "teknium"}

# ─── v0.5.0 backlog: Channel for developed repos, points system ──
CHANNEL_DEVELOPED = "plugins-skills-and-skins"
POINTS_PER_CHUNK = 1

# ─── T11: GPU power ranking list ────────────────────────────────────
GPU_LIST = [
    (  1, "GB200 Grace+Blackwell",  "384GB (2x B200)",  "frontier", "#facc15"),
    (  2, "B200 Blackwell",  "192GB HBM3e",  "frontier", "#facc15"),
    (  3, "B100 Blackwell",  "192GB",  "frontier", "#facc15"),
    (  4, "MI325X",  "256GB HBM3e",  "frontier", "#facc15"),
    (  5, "MI300X",  "192GB, 5.3TB/s",  "frontier", "#facc15"),
    (  6, "H200 SXM",  "141GB, 4.8TB/s",  "frontier", "#facc15"),
    (  7, "H100 SXM 80GB",  "80GB, 3.35TB/s",  "frontier", "#facc15"),
    (  8, "A100 SXM 80GB",  "80GB, 2.0TB/s",  "frontier", "#facc15"),
    (  9, "H800 SXM 80GB",  "80GB",  "frontier", "#facc15"),
    ( 10, "A800 80GB",  "80GB",  "frontier", "#facc15"),
    ( 11, "H20 96GB",  "96GB",  "frontier", "#facc15"),
    ( 12, "MI250X",  "128GB",  "frontier", "#facc15"),
    ( 13, "TPU v7 Ironwood",  "256GB+/pod, 7.4TB/s",  "tpu", "#ec4899"),
    ( 14, "TPU v6 Trillium",  "per-chip HBM",  "tpu", "#ec4899"),
    ( 15, "TPU v5p",  "per-chip HBM",  "tpu", "#ec4899"),
    ( 16, "TPU v5e",  "per-chip HBM",  "tpu", "#ec4899"),
    ( 17, "Trainium 2 (AWS)",  "96GB",  "tpu", "#ec4899"),
    ( 18, "Trainium 1 (AWS)",  "32GB",  "tpu", "#ec4899"),
    ( 19, "Gaudi 3 (Intel)",  "128GB",  "tpu", "#ec4899"),
    ( 20, "Gaudi 2 (Intel)",  "96GB",  "tpu", "#ec4899"),
    ( 21, "V100 SXM 32GB",  "32GB, 900GB/s",  "datacenter", "#a78bfa"),
    ( 22, "B200 Ultra Blackwell",  "288GB HBM3e",  "frontier", "#facc15"),
    ( 23, "Cerebras CS-3 (WSE-3)",  "44GB SRAM-on-chip",  "frontier", "#facc15"),
    ( 24, "Groq LPU (LPUv5)",  "230GB HBM",  "frontier", "#facc15"),
    ( 25, "RTX 6000 Ada",  "48GB ECC",  "workstation", "#a78bfa"),
    ( 26, "Mac Pro M2 Ultra",  "192GB unified",  "workstation", "#a78bfa"),
    ( 27, "RTX 5090",  "32GB, 1.79TB/s",  "consumer", "#10b981"),
    ( 28, "RTX 4090",  "24GB, 1.0TB/s",  "consumer", "#10b981"),
    ( 29, "RX 7900 XTX",  "24GB, 960GB/s",  "consumer", "#ef4444"),
    ( 30, "RTX 5880 Ada",  "48GB ECC",  "workstation", "#a78bfa"),
    ( 31, "RTX 5000 Ada",  "32GB ECC",  "workstation", "#a78bfa"),
    ( 32, "RTX 4500 Ada",  "24GB ECC",  "workstation", "#a78bfa"),
    ( 33, "RTX 4080 SUPER",  "16GB",  "consumer", "#10b981"),
    ( 34, "RX 7900 XT",  "20GB",  "consumer", "#ef4444"),
    ( 35, "RTX 4070 Ti SUPER",  "16GB",  "consumer", "#10b981"),
    ( 36, "L40S (datacenter)",  "48GB",  "datacenter", "#a78bfa"),
    ( 37, "L4 (datacenter)",  "24GB",  "datacenter", "#a78bfa"),
    ( 38, "L40 (datacenter)",  "48GB",  "datacenter", "#a78bfa"),
    ( 39, "RTX 3090 Ti",  "24GB",  "consumer", "#10b981"),
    ( 40, "Jetson Orin AGX 64GB",  "64GB unified",  "edge", "#3b82f6"),
    ( 41, "2x RTX 5090",  "64GB (NVLink)",  "multi", "#10b981"),
    ( 42, "2x RTX 4090",  "48GB (NVLink)",  "multi", "#10b981"),
    ( 43, "RTX 5090 + RTX 4090",  "56GB mixed",  "multi", "#10b981"),
    ( 44, "2x RTX 4080 SUPER",  "32GB",  "multi", "#10b981"),
    ( 45, "2x RTX 3090",  "48GB",  "multi", "#10b981"),
    ( 46, "2x RTX 4070 Ti SUPER",  "32GB",  "multi", "#10b981"),
    ( 47, "2x RTX 3090 Ti",  "48GB",  "multi", "#10b981"),
    ( 48, "2x RTX 4070 Ti",  "24GB",  "multi", "#10b981"),
    ( 49, "RTX 5080",  "16GB GDDR7",  "consumer", "#10b981"),
    ( 50, "RTX 4080",  "16GB",  "consumer", "#10b981"),
    ( 51, "RTX 4070 Ti",  "12GB",  "consumer", "#10b981"),
    ( 52, "RTX 3090",  "24GB",  "consumer", "#10b981"),
    ( 53, "RX 7800 XT",  "16GB",  "consumer", "#ef4444"),
    ( 54, "RTX 3080 Ti",  "12GB",  "consumer", "#10b981"),
    ( 55, "RTX 3080",  "10GB",  "consumer", "#10b981"),
    ( 56, "RTX 4070 SUPER",  "12GB",  "consumer", "#10b981"),
    ( 57, "RTX 4070",  "12GB",  "consumer", "#10b981"),
    ( 58, "Intel Arc A770 16GB",  "16GB",  "consumer", "#3b82f6"),
    ( 59, "RTX 4060 Ti 16GB",  "16GB",  "consumer", "#10b981"),
    ( 60, "2x RTX 3080 Ti",  "24GB",  "multi", "#10b981"),
    ( 61, "2x RTX 3080",  "20GB",  "multi", "#10b981"),
    ( 62, "2x Intel Arc A770",  "32GB",  "multi", "#3b82f6"),
    ( 63, "2x RTX 3070 Ti",  "16GB",  "multi", "#10b981"),
    ( 64, "2x RTX 3070",  "16GB",  "multi", "#10b981"),
    ( 65, "2x RTX 3060 12GB",  "24GB",  "multi", "#10b981"),
    ( 66, "2x RX 6700 XT",  "24GB",  "multi", "#ef4444"),
    ( 67, "RTX 3070 Ti",  "8GB",  "consumer", "#10b981"),
    ( 68, "RTX 3070",  "8GB",  "consumer", "#10b981"),
    ( 69, "RTX 3060 12GB",  "12GB",  "consumer", "#10b981"),
    ( 70, "RX 6700 XT 12GB",  "12GB",  "consumer", "#ef4444"),
    ( 71, "RTX 3060 Ti",  "8GB",  "consumer", "#10b981"),
    ( 72, "RTX 3050",  "8GB",  "consumer", "#10b981"),
    ( 73, "2x RTX 2080 Ti",  "22GB",  "multi", "#10b981"),
    ( 74, "RTX 2080 Ti",  "11GB",  "consumer", "#10b981"),
    ( 75, "2x RTX 2060 SUPER",  "16GB",  "multi", "#10b981"),
    ( 76, "RTX 2060 SUPER",  "8GB",  "consumer", "#10b981"),
    ( 77, "RTX 2070 SUPER",  "8GB",  "consumer", "#10b981"),
    ( 78, "RTX 2060",  "6GB",  "consumer", "#10b981"),
    ( 79, "RTX 2080",  "8GB",  "consumer", "#10b981"),
    ( 80, "RTX 2070",  "8GB",  "consumer", "#10b981"),
    ( 81, "2x GTX 1080 Ti",  "22GB",  "multi", "#10b981"),
    ( 82, "GTX 1080 Ti",  "11GB",  "consumer", "#10b981"),
    ( 83, "2x GTX 1080",  "16GB",  "multi", "#10b981"),
    ( 84, "GTX 1080",  "8GB",  "consumer", "#10b981"),
    ( 85, "RX 6800",  "16GB",  "consumer", "#ef4444"),
    ( 86, "2x RX 580 8GB",  "16GB",  "multi", "#ef4444"),
    ( 87, "RX 580 8GB",  "8GB",  "consumer", "#ef4444"),
    ( 88, "RX 570 8GB",  "8GB",  "consumer", "#ef4444"),
    ( 89, "2x RX 480 8GB",  "16GB",  "multi", "#ef4444"),
    ( 90, "RX 480 8GB",  "8GB",  "consumer", "#ef4444"),
    ( 91, "2x RX 570 4GB",  "8GB",  "multi", "#ef4444"),
    ( 92, "RX 570 4GB",  "4GB",  "consumer", "#ef4444"),
    ( 93, "GTX 1070 Ti",  "8GB",  "consumer", "#10b981"),
    ( 94, "GTX 1070",  "8GB",  "consumer", "#10b981"),
    ( 95, "GTX 1060 6GB",  "6GB",  "consumer", "#10b981"),
    ( 96, "GTX 980 Ti",  "6GB",  "consumer", "#10b981"),
    ( 97, "GTX 980",  "4GB",  "consumer", "#10b981"),
    ( 98, "GTX 970",  "4GB",  "consumer", "#10b981"),
    ( 99, "GTX 1050 Ti",  "4GB",  "consumer", "#10b981"),
    (100, "GTX 1050",  "2GB",  "consumer", "#10b981"),
    (101, "R9 380 4GB",  "4GB",  "consumer", "#ef4444"),
    (102, "Intel Iris Xe (96 EU)",  "~8GB shared",  "integrated", "#3b82f6"),
    (103, "Intel UHD 770",  "~8GB shared",  "integrated", "#3b82f6"),
    (104, "Intel UHD 630",  "~4GB shared",  "integrated", "#3b82f6"),
    (105, "Intel HD 4000 (Ivy Br.)",  "2GB shared",  "integrated", "#3b82f6"),
]
GPU_BY_RANK = {entry[0]: entry for entry in GPU_LIST}

# ─── v0.5.0: Client-side BM25 search constants (mirrored in index.html) ──
SEARCH_RESULT_CAP = 100
BM25_B = 0.95
PROXIMITY_BOOST = 0.5


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


def assign_awards(user_stats, author_name=None):
    """Stable contribution awards from lifetime stats. Only go up.

    ISSUE #4: 8 new awards added (Linker, Convo-starter, Emoji Master,
    Questioner, Helper, Mentor, OG, Resurrected). Existing awards have
    priority (checked first). Cap at 2 awards per user.
    ISSUE #13: Brand lover/hater awards added.
    ISSUE #15: Mod/Developer/Contributor role pills added (separate field).
    ISSUE #7: New users with no chunks get no awards.
    """
    if not user_stats.get("total_chunks"):
        return []

    awards = []
    channels = len(user_stats.get("channels", set()) or set())
    first_seen = user_stats.get("first_seen", "") or ""
    max_streak = user_stats.get("max_streak", 0) or 0
    dates_active = user_stats.get("dates_active", set()) or set()
    msg_per_chunk = user_stats.get("msg_per_chunk", 0) or 0
    total_chunks = user_stats.get("total_chunks", 0) or 0
    total_msgs = user_stats.get("total_messages", 0) or 0
    last_seen = user_stats.get("last_seen", "") or ""
    max_gap_days = user_stats.get("max_gap_days", 0) or 0

    # Existing awards (higher priority — checked first)
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

    # ISSUE #4: NEW awards (lower priority, checked after existing)
    if len(awards) < 2:
        link_rate = user_stats.get("link_rate", 0) or 0
        if link_rate > 0.15 and total_chunks > 20:
            awards.append(["linker", "Linker"])

    if len(awards) < 2:
        avg_msg_len = user_stats.get("avg_msg_len", 0) or 0
        if avg_msg_len > 300 and total_msgs > 50 and msg_per_chunk > 8:
            awards.append(["convo-starter", "Convo-starter"])

    if len(awards) < 2:
        emoji_rate = user_stats.get("emoji_rate", 0) or 0
        if emoji_rate > 0.4:
            awards.append(["emoji-master", "Emoji Master"])

    if len(awards) < 2:
        question_rate = user_stats.get("question_rate", 0) or 0
        if question_rate > 0.4:
            awards.append(["questioner", "Questioner"])

    if len(awards) < 2:
        helper_rate = user_stats.get("helper_rate", 0) or 0
        if helper_rate > 0.1 and total_chunks > 10:
            awards.append(["helper", "Helper"])

    if len(awards) < 2:
        if msg_per_chunk > 10 and max_streak > 14:
            awards.append(["mentor", "Mentor"])

    if len(awards) < 2:
        if first_seen and first_seen <= "2025-01-01" and total_chunks > 20:
            awards.append(["og", "OG"])

    if len(awards) < 2:
        if max_gap_days > 120:
            awards.append(["resurrected", "Resurrected"])

    # ISSUE #13: Brand lover/hater awards (checked after standard awards)
    if len(awards) < 2:
        _shills = user_stats.get("shill_brands", []) or []
        for _s in _shills:
            _bm = BRAND_META.get(_s["brand"])
            if _bm and (_s.get("count") or 0) >= BRAND_LOVER_AWARD_MIN_TOTAL:
                awards.append(["lover", f"{_bm[1]} lover"])
                break

    if len(awards) < 2:
        _haters = user_stats.get("hater_brands", []) or []
        for _h in _haters:
            _bm = BRAND_META.get(_h["brand"])
            if _bm and (_h.get("count") or 0) >= BRAND_HATER_AWARD_MIN_TOTAL:
                awards.append(["hater", f"{_bm[1]} hater"])
                break

    return awards[:2]


def assign_title(s, rank_idx=None):
    """Evaluate all TITLE_RULES and return the best match (highest priority).

    ISSUE #7: New users with no chunks/messages get no title.
    """
    if not s.get("total_chunks") or not s.get("total_messages"):
        return None
    for _priority, _name, _emoji, _check_fn in TITLE_RULES:
        if _check_fn(s, rank_idx):
            return f"{_emoji} {_name}"
    return None


def extract_developed(chunks, channel_name, author=None):
    """Scan a specific channel for github.com URLs by a given author.

    ISSUE #5 (v0.5.0): Only scans CHANNEL_DEVELOPED channel, not all messages.
    Deduplicates by owner/repo path. Caps at 5 per user.
    Returns empty list if no repos found (caller skips section).
    """
    if not chunks:
        return []
    repos = []
    seen = set()
    repo_pattern = r'https?://github\.com/([a-zA-Z0-9_.-]+/[a-zA-Z0-9_.-]+)'
    for c in chunks:
        if c.get("channel", "") != channel_name:
            continue
        for m in c.get("messages", []):
            if author is not None and m.get("author") != author:
                continue
            content = m.get("content", "") or ""
            for match in re.finditer(repo_pattern, content):
                full_path = match.group(1).rstrip("/")
                parts = full_path.split("/")
                if len(parts) >= 2:
                    repo_key = f"{parts[0]}/{parts[1]}"
                    if repo_key not in seen:
                        seen.add(repo_key)
                        repos.append({
                            "repo": repo_key,
                            "url": f"https://github.com/{repo_key}",
                        })
    return repos[:5]


# ─── T9: Style heuristic summary (v0.3.0 pill format) ──────────────
def compute_style_heuristic(s):
    """Return a short deterministic posting-style pill string.

    ISSUE #6: Pill format — channel/time/length only. No "short and
    punchy" filler. Low-activity fallback preserved. Format:
    "active in #ch · morning poster (UTC)"
    """
    if not s.get("total_chunks"):
        return None
    chunks = s.get("total_chunks", 0)
    msgs = s.get("total_messages", 0)
    chans = sorted(s.get("channels", set()) or set())
    chan_short = {"hermes-agent": "hermes", "community-projects-showcase": "projects",
                  "plugins-skills-and-skins": "plugins", "developers": "devs"}
    chans_disp = [chan_short.get(c, c) for c in chans]

    # Low-activity fallback (preserved from v2-review)
    if chunks <= 3:
        if not chans_disp:
            if msgs == 0:
                return None
            return None  # New/brief contributors get no style pill
        chan_str = f"in #{chans_disp[0]}" if len(chans_disp) == 1 else f"in #{' + #'.join(chans_disp)}"
        return f"Brief contributor {chan_str}"

    parts = []

    # Channels
    if len(chans_disp) == 1:
        parts.append(f"active in #{chans_disp[0]}")
    elif len(chans_disp) <= 3:
        parts.append(f"active in #{' + #'.join(chans_disp)}")
    else:
        parts.append(f"polymath across all {len(chans_disp)} channels")

    # Time of day
    hour_dist = s.get("hour_dist", {})
    if hour_dist:
        peak_hour = max(hour_dist, key=hour_dist.get)
        if peak_hour in range(0, 6):
            parts.append("night owl (UTC)")
        elif peak_hour in range(6, 12):
            parts.append("morning poster (UTC)")
        elif peak_hour in range(12, 18):
            parts.append("afternoon poster (UTC)")
        else:
            parts.append("evening poster (UTC)")

    # Length pill removed (v0.2.1) — average can be misleading on
    # link/sticker-dominated users. Add back later if total_msg_len
    # math is verified clean.

    if parts:
        return " · ".join(parts)
    return None


def compute_style_pills(s):
    """Return a list of individual style pill strings (ISSUE #10).

    Each pill is one trait: channel activity, time of day, message length,
    linker, code wizard, emoji master, questioner.
    """
    if not s.get("total_chunks"):
        return []
    pills = []
    chunks = s.get("total_chunks", 0)
    msgs = s.get("total_messages", 0)
    chans = sorted(s.get("channels", set()) or set())
    chan_short = {"hermes-agent": "hermes", "community-projects-showcase": "projects",
                  "plugins-skills-and-skins": "plugins", "developers": "devs"}
    chans_disp = [chan_short.get(c, c) for c in chans]

    # Channel activity
    if chunks <= 3:
        if chans_disp:
            chan_str = f"in #{chans_disp[0]}" if len(chans_disp) == 1 else f"in #{' + #'.join(chans_disp)}"
            pills.append(f"Brief contributor {chan_str}")
        return pills
    if len(chans_disp) == 1:
        pills.append(f"active in #{chans_disp[0]}")
    elif len(chans_disp) <= 3:
        pills.append(f"active in #{' + #'.join(chans_disp)}")
    else:
        pills.append(f"polymath across all {len(chans_disp)} channels")

    # Time of day
    hour_dist = s.get("hour_dist", {}) or {}
    if hour_dist:
        peak_hour = max(hour_dist, key=hour_dist.get)
        if peak_hour < 6:
            pills.append("night owl (UTC)")
        elif peak_hour < 12:
            pills.append("morning poster (UTC)")
        elif peak_hour < 18:
            pills.append("afternoon poster (UTC)")
        else:
            pills.append("evening poster (UTC)")

    # Length pill removed (v0.2.1) — same rationale as compute_style_heuristic.

    # Style traits
    link_rate = s.get("link_rate", 0) or 0
    if link_rate > 0.15:
        pills.append("linker")
    code_rate = s.get("code_rate", 0) or 0
    if code_rate > 0.3:
        pills.append("code wizard")
    emoji_rate = s.get("emoji_rate", 0) or 0
    if emoji_rate > 0.4:
        pills.append("emoji master")
    q_rate = s.get("question_rate", 0) or 0
    if q_rate > 0.4:
        pills.append("questioner")

    return pills


# ─── v0.5.0 backlog: USER-SPEC-SHEET integration (#12, #17) ────
def load_user_spec_sheet():
    """Load data/USER-SPEC-SHEET.yaml into {discord_username: record} dict.

    Tries yaml module first; falls back to simple text parser.
    Returns empty dict if file doesn't exist or is unparseable.
    """
    path = SCRIPT_DIR / "data" / "USER-SPEC-SHEET.yaml"
    if not path.exists():
        return {}
    try:
        import yaml
        with open(path) as f:
            records = yaml.safe_load(f)
        if isinstance(records, list):
            return {r.get("discord_username", "").lower(): r for r in records if r.get("discord_username")}
        return {}
    except ImportError:
        pass
    # Fallback: simple line-by-line parser for basic YAML list of dicts
    records = []
    current = None
    text = path.read_text()
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("- "):
            if current:
                records.append(current)
            current = {}
            remainder = stripped[2:].strip()
            if ":" in remainder:
                key, val = remainder.split(":", 1)
                current[key.strip()] = val.strip().strip('"').strip("'").strip('"')
        elif current and ":" in line:
            key, val = line.split(":", 1)
            current[key.strip()] = val.strip().strip('"').strip("'").strip('"')
    if current:
        records.append(current)
    result = {}
    for r in records:
        uname = r.get("discord_username", "").lower()
        if uname:
            result[uname] = r
    return result


# ─── T10: Brand detection ─────────────────────────────────────────
def _match_brand_kw(content, kw):
    """Match a keyword against content — supports \\b regex patterns."""
    if "\\b" in kw:
        return bool(re.search(kw, content))
    return kw in content


def detect_brands(messages):
    """For one user, return dict of {brand: {positive: N, negative: M, total: K}}.

    Uses BRAND_MENTION_THRESHOLD from constants (ISSUE #5).
    """
    if not messages:
        return {}
    result = {}
    for brand, kws in BRAND_KEYWORDS.items():
        pos, neg, total = 0, 0, 0
        for m in messages:
            content = (m.get("content") or "").lower()
            if not any(_match_brand_kw(content, kw) for kw in kws):
                continue
            total += 1
            has_pos = any(w in content for w in POSITIVE_WORDS)
            has_neg = any(w in content for w in NEGATIVE_WORDS)
            if has_pos and not has_neg:
                pos += 1
            elif has_neg and not has_pos:
                neg += 1
        if total >= BRAND_MENTION_THRESHOLD:
            result[brand] = {"positive": pos, "negative": neg, "total": total}
    return result


def assign_shill_hater(brand_sentiment):
    """Return (shill_brands, hater_brands) for one user.

    Uses SHILL_MIN_POS_RATIO, HATER_MIN_NEG_RATIO, BRAND_MENTION_THRESHOLD
    from constants (ISSUE #5). All-positive or all-negative simplified paths.
    ISSUE #7: New users with no brand mentions get no shill/hater entries.
    """
    if not brand_sentiment:
        return [], []
    shill, hater = [], []
    for brand, counts in brand_sentiment.items():
        pos, neg, total = counts["positive"], counts["negative"], counts["total"]
        if total < BRAND_MENTION_THRESHOLD:
            continue
        # All positive (no negative mentions ever)
        if pos > 0 and neg == 0 and pos >= BRAND_MENTION_THRESHOLD:
            shill.append({"brand": brand, "count": pos, "ratio": 9999.99, "total": total})
        # All negative (no positive mentions ever)
        elif neg > 0 and pos == 0 and neg >= BRAND_MENTION_THRESHOLD:
            hater.append({"brand": brand, "count": neg, "ratio": 0.0, "total": total})
        # Mixed: check ratios
        elif pos + neg >= BRAND_MENTION_THRESHOLD:
            ratio = pos / max(neg, 1)
            if ratio >= SHILL_MIN_POS_RATIO:
                shill.append({"brand": brand, "count": pos, "ratio": round(ratio, 2), "total": total})
            elif ratio <= (1.0 / HATER_MIN_NEG_RATIO):
                hater.append({"brand": brand, "count": neg, "ratio": round(ratio, 2), "total": total})
    return shill, hater


def get_user_messages(user_name, chunks):
    """Pull all messages for a user, sorted by timestamp."""
    msgs = []
    for c in chunks:
        for m in c.get("messages", []):
            if m.get("author") == user_name:
                msgs.append(m)
    msgs.sort(key=lambda m: m.get("timestamp", ""))
    return msgs


def compute_chunk_embeddings(chunks, model="qwen3-embedding:0.6b", batch_size=500):
    """Generate per-chunk embeddings via local Ollama. Returns list of 1024-dim int8 byte strings.

    Batches requests in groups of 500 to keep Ollama's context bounded.
    Quantizes float32 vectors to int8 (preserves ~99% of semantic info for
    cosine similarity). Each result is a `bytes` object of length 1024.

    T13 from v5.5 v3 plan. Validated against the Flask prototype (RAG/CFC4819)
    which uses the same model + cosine 0.3 threshold + RRF fusion.

    Implementation note: uses urllib.request rather than subprocess+curl
    because a 500-text payload (250KB+) exceeds the OS command-line arg
    length limit (~128KB on Linux). urllib streams the body cleanly.
    """
    import numpy as np
    import urllib.request
    import urllib.error

    texts = [c.get("text", "") or "" for c in chunks]
    all_vec_bytes = []

    for batch_start in range(0, len(texts), batch_size):
        batch = texts[batch_start:batch_start + batch_size]
        payload = json.dumps({"model": model, "input": batch}).encode("utf-8")
        req = urllib.request.Request(
            "http://localhost:11434/v1/embeddings",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=600) as resp:
                body = resp.read()
        except urllib.error.URLError as e:
            raise RuntimeError(
                f"Ollama embeddings request failed for batch "
                f"{batch_start}-{batch_start+len(batch)}: {e}"
            ) from e

        try:
            data = json.loads(body).get("data", [])
        except json.JSONDecodeError as e:
            raise RuntimeError(
                f"Could not parse Ollama response: {body[:500]!r}"
            ) from e

        if len(data) != len(batch):
            raise RuntimeError(
                f"Expected {len(batch)} embeddings, got {len(data)} from Ollama"
            )

        for item in data:
            vec = np.array(item["embedding"], dtype=np.float32)
            scale = 127.0 / max(abs(vec.max()), abs(vec.min()), 1e-9)
            vec_q = np.clip(np.round(vec * scale), -127, 127).astype(np.int8)
            all_vec_bytes.append(vec_q.tobytes())

    return all_vec_bytes


def compute_ai_exports(chunks, leaderboard):
    """Produce AI-friendly export files in dist/. All in standard formats, no
    custom schemas. Chunk and conversation exports are SHARDED to stay
    under the 25 MiB CF Pages per-file limit.

    Files written:
    - dist/chunks-{N}.jsonl         — one chunk per line per shard
    - dist/conversations-{N}.jsonl  — OpenAI/ShareGPT format, sharded
    - dist/users.jsonl              — per-user summary, single file (small)
    - dist/rag-corpus/manifest.json + chunks/  — RAG-ready corpus
    """
    import json, os
    from pathlib import Path
    Path("dist").mkdir(exist_ok=True)
    Path("dist/rag-corpus/chunks").mkdir(parents=True, exist_ok=True)

    EXPORT_TEXT_MAX = 1000
    EXPORT_SHARD_BYTES = 20 * 1024 * 1024  # 20 MiB target per shard (well under 25 MiB limit)

    def _open_shard(prefix, idx):
        return open(str(OUTPUT_DIR / f"{prefix}-{idx}.jsonl"), "w")

    def _rotate_shard(fh, files_list, prefix, idx, size, new_fh=None):
        fh.close()
        files_list.append(f"{prefix}-{idx}.jsonl")
        return _open_shard(prefix, idx + 1), 0

    # 1. chunks-{N}.jsonl — every chunk as a line per shard
    chunks_files = []
    n_chunks = len(chunks)
    chunks_fh = _open_shard("chunks", 0)
    chunks_size = 0
    try:
        for c in chunks:
            entry = dict(c)
            entry["text"] = (entry.get("text") or "")[:EXPORT_TEXT_MAX]
            line = json.dumps(entry, ensure_ascii=False) + "\n"
            if chunks_size + len(line) > EXPORT_SHARD_BYTES and chunks_size > 0:
                chunks_fh.close()
                chunks_files.append("chunks-0.jsonl")
                chunks_fh = _open_shard("chunks", len(chunks_files))
                chunks_size = 0
            chunks_fh.write(line)
            chunks_size += len(line)
        chunks_fh.close()
        if chunks_size > 0:
            chunks_files.append(f"chunks-{len(chunks_files)}.jsonl")
    except Exception:
        try: chunks_fh.close()
        except: pass
        raise
    n_chunk_shards = len(chunks_files)
    print(f"  chunks sharded: {n_chunk_shards} files", file=sys.stderr)

    # 2. conversations-{N}.jsonl — group chunks by channel+date, sharded
    by_conv = {}
    for c in chunks:
        key = (c.get("channel", ""), c.get("start_time", "")[:10])
        by_conv.setdefault(key, []).append(c)
    n_convs = 0
    conv_files = []
    conv_fh = _open_shard("conversations", 0)
    conv_size = 0
    try:
        for (ch, dt), group in by_conv.items():
            group_sorted = sorted(group, key=lambda c: c.get("start_time", ""))
            for c in group_sorted:
                text = (c.get("text", "") or "").strip()
                if len(text) < 50:
                    continue
                line = json.dumps({
                    "messages": [
                        {"role": "user", "content": f"[{ch} on {dt}] Discussion continues…"},
                        {"role": "assistant", "content": text[:EXPORT_TEXT_MAX]},
                    ],
                    "channel": ch,
                    "date": dt,
                    "chunk_id": c.get("id", ""),
                }, ensure_ascii=False) + "\n"
                if conv_size + len(line) > EXPORT_SHARD_BYTES and conv_size > 0:
                    conv_fh.close()
                    conv_files.append(f"conversations-{len(conv_files)}.jsonl")
                    conv_fh = _open_shard("conversations", len(conv_files))
                    conv_size = 0
                conv_fh.write(line)
                conv_size += len(line)
                n_convs += 1
        conv_fh.close()
        if conv_size > 0:
            conv_files.append(f"conversations-{len(conv_files)}.jsonl")
    except Exception:
        try: conv_fh.close()
        except: pass
        raise
    n_conv_shards = len(conv_files)
    print(f"  conversations sharded: {n_conv_shards} files", file=sys.stderr)

    # 3. users.jsonl — per-user summary (small, single file)
    with open("dist/users.jsonl", "w") as f:
        for u in leaderboard:
            f.write(json.dumps({
                "name": u["name"],
                "total_chunks": u.get("total_chunks", 0),
                "total_messages": u.get("total_messages", 0),
                "channels_active": u.get("channels_active", 0),
                "first_seen": u.get("first_seen", ""),
                "last_seen": u.get("last_seen", ""),
                "medal": u.get("medal", None),
                "awards": u.get("awards", []),
                "style_heuristic": u.get("style_heuristic", ""),
                "shill": u.get("shill", []),
                "hater": u.get("hater", []),
            }, ensure_ascii=False) + "\n")

    # 4. rag-corpus/ — RAG-ready, sharded jsonl (chunks-{N}.jsonl) to keep
    # total file count under CF Pages' 20,000-file deployment limit.
    # Each shard has RAG_CHUNKS_PER_SHARD chunks; ~21 shards for 20K chunks.
    RAG_CHUNKS_PER_SHARD = 1000
    rag_shard_files = []
    rag_shard_idx = 0
    rag_in_shard = 0
    # Note: relative to OUTPUT_DIR, so writes to OUTPUT_DIR/dist/rag-corpus/chunks-N.jsonl
    rag_path_template = "dist/rag-corpus/chunks-{shard}.jsonl"
    rag_fh = open(rag_path_template.format(shard=0), "w")
    try:
        for c in chunks:
            cid = c.get("id", "")
            if not cid:
                continue
            line = json.dumps({
                "id": cid,
                "channel": c.get("channel", ""),
                "start_time": c.get("start_time", ""),
                "end_time": c.get("end_time", ""),
                "text": (c.get("text", "") or "")[:EXPORT_TEXT_MAX],
                "authors": c.get("authors", []),
                "message_count": c.get("message_count", 0),
            }, ensure_ascii=False) + "\n"
            if rag_in_shard >= RAG_CHUNKS_PER_SHARD:
                rag_fh.close()
                rag_shard_files.append(f"chunks-{rag_shard_idx}.jsonl")
                rag_shard_idx += 1
                rag_fh = open(rag_path_template.format(shard=rag_shard_idx), "w")
                rag_in_shard = 0
            rag_fh.write(line)
            rag_in_shard += 1
        rag_fh.close()
        if rag_in_shard > 0:
            rag_shard_files.append(f"chunks-{rag_shard_idx}.jsonl")
    except Exception:
        try: rag_fh.close()
        except: pass
        raise
    n_rag_shards = len(rag_shard_files)
    print(f"  rag-corpus sharded: {n_rag_shards} files", file=sys.stderr)

    with open("dist/rag-corpus/manifest.json", "w") as f:
        json.dump({
            "version": "1.0",
            "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "total_chunks": len(chunks),
            "schema": "talio-rag-corpus-v1",
            "fields_per_chunk": [
                "id", "channel", "start_time", "end_time", "text",
                "authors", "message_count",
            ],
            "chunks_dir": "chunks",
            "chunks_files": rag_shard_files,
            "chunks_per_shard": RAG_CHUNKS_PER_SHARD,
        }, f, indent=2)

    return {
        "chunks": n_chunks,
        "chunk_shards": n_chunk_shards,
        "chunk_files": chunks_files,
        "conversations": n_convs,
        "conversation_shards": n_conv_shards,
        "conversation_files": conv_files,
        "users": len(leaderboard),
        "rag_chunks": len(chunks),
    }


# ─── v0.5.0 backlog: Plugins/skills/skins catalog (#18) ───────────
def compute_plugins_skills_skins_catalog(chunks):
    """Extract all github.com links from the CHANNEL_DEVELOPED channel.

    ISSUE #18 (v0.5.0 backlog). Writes to data/plugins_skills_skins_catalog.json.
    """
    catalog = {}
    repo_pattern = re.compile(r'https?://github\.com/([a-zA-Z0-9_.-]+/[a-zA-Z0-9_.-]+)')
    for c in chunks:
        if c.get("channel", "") != CHANNEL_DEVELOPED:
            continue
        for m in c.get("messages", []):
            content = m.get("content", "") or ""
            author = m.get("author", "")
            for match in repo_pattern.finditer(content):
                full_path = match.group(1).rstrip("/")
                parts = full_path.split("/")
                if len(parts) < 2:
                    continue
                repo_key = f"{parts[0]}/{parts[1]}"
                if repo_key not in catalog:
                    catalog[repo_key] = {
                        "repo": repo_key,
                        "owner": parts[0],
                        "mentioned_by": set(),
                        "mention_count": 0,
                    }
                catalog[repo_key]["mention_count"] += 1
                if author and not is_bot(author):
                    catalog[repo_key]["mentioned_by"].add(author)

    out_dir = SCRIPT_DIR / "data"
    out_dir.mkdir(exist_ok=True)

    result = []
    for repo_key, entry in catalog.items():
        result.append({
            "repo": entry["repo"],
            "owner": entry["owner"],
            "mentioned_by": sorted(entry["mentioned_by"]),
            "mention_count": entry["mention_count"],
        })
    result.sort(key=lambda x: -x["mention_count"])

    path = out_dir / "plugins_skills_skins_catalog.json"
    with open(path, "w") as f:
        json.dump(result, f, indent=2)
    print(f"  plugins/skills/skins catalog: {len(result)} repos written to {path}", file=sys.stderr)


# ─── ISSUE #6: Rolling backups ──────────────────────────────────────
# Short, human-readable reasons for each award id (assign_awards above).
# Used only for the awards.jsonl backup export.
AWARD_REASONS = {
    "explorer": "Active in 3+ channels",
    "pioneer": "Joined on or before 2025-01-01",
    "streak": "30+ day posting streak",
    "pillar": "Active on 50+ distinct days",
    "sage": "Averages 15+ messages per chunk",
    "diplomat": "Active in 4+ channels",
    "linker": "Shares links in 15%+ of messages (20+ chunks)",
    "convo-starter": "Long, frequent messages that spark discussion",
    "emoji-master": "Uses emoji in 40%+ of messages",
    "questioner": "Asks questions in 40%+ of messages",
    "helper": "Helps others in 10%+ of messages (10+ chunks)",
    "mentor": "High engagement with a 14+ day streak",
    "og": "Joined on or before 2025-01-01 with 20+ chunks",
    "resurrected": "Returned after a 120+ day absence",
    "lover": "Strongly positive about a tracked brand",
    "hater": "Strongly negative about a tracked brand",
}


def _write_jsonl(path, records, ts_field=None):
    """Write one JSON object per line. Returns manifest stats for the file."""
    oldest = None
    with open(path, "w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
            if ts_field:
                v = r.get(ts_field)
                if v and (oldest is None or v < oldest):
                    oldest = v
    return {
        "record_count": len(records),
        "byte_size": path.stat().st_size,
        "oldest_record_ts": oldest,
    }


def _iso_week_id(d):
    iso = d.isocalendar()
    return f"{iso[0]}-W{iso[1]:02d}"


def _backup_archive_week(backup_root, archive_root, files):
    """Copy this build's backup files into the current ISO week's archive
    directory (no-op after the first build of the week). If
    BACKUP_TARBALL_WEEKLY is set, finalize any prior week directories into
    tar.gz archives once a new week has started.
    """
    today = datetime.now(timezone.utc).date()
    week_id = _iso_week_id(today)
    week_dir = archive_root / week_id
    if not week_dir.exists():
        week_dir.mkdir(parents=True, exist_ok=True)
        for name in files:
            src = backup_root / name
            if src.exists():
                shutil.copy2(src, week_dir / name)

    if BACKUP_TARBALL_WEEKLY:
        for prev in sorted(archive_root.iterdir()):
            if not prev.is_dir() or prev.name == week_id:
                continue
            if not re.match(r"^\d{4}-W\d{2}$", prev.name):
                continue
            tar_path = archive_root / f"{prev.name}.tar.gz"
            if tar_path.exists():
                continue
            with tarfile.open(tar_path, "w:gz") as tf:
                tf.add(prev, arcname=prev.name)
            shutil.rmtree(prev)


def _backup_cleanup(archive_root):
    """Delete archived weeks older than BACKUP_RETENTION_WEEKS. Gzip loose
    .jsonl files inside week directories older than
    BACKUP_COMPRESS_AFTER_WEEKS (only relevant if BACKUP_TARBALL_WEEKLY is
    off, since tarballing already compresses everything).
    """
    today = datetime.now(timezone.utc).date()
    current_monday = today - timedelta(days=today.isoweekday() - 1)

    for entry in sorted(archive_root.iterdir()):
        m = re.match(r"^(\d{4})-W(\d{2})", entry.name)
        if not m:
            continue
        try:
            entry_monday = datetime.strptime(
                f"{m.group(1)}-W{m.group(2)}-1", "%G-W%V-%u"
            ).date()
        except ValueError:
            continue
        age_weeks = (current_monday - entry_monday).days // 7

        if age_weeks > BACKUP_RETENTION_WEEKS:
            if entry.is_dir():
                shutil.rmtree(entry)
            else:
                entry.unlink()
            continue

        if entry.is_dir() and age_weeks > BACKUP_COMPRESS_AFTER_WEEKS:
            for f in entry.iterdir():
                if f.suffix == ".jsonl":
                    with open(f, "rb") as fin, gzip.open(f"{f}.gz", "wb") as fout:
                        shutil.copyfileobj(fin, fout)
                    f.unlink()


def backup_assets(leaderboard, metadata, chunks):
    """ISSUE #6: Write rolling JSONL/JSON backups of build state to
    dist/backups/. Runs after the main build + AI exports. Never raises —
    a backup failure must not break the site build.
    """
    if not BACKUP_ENABLED:
        return
    try:
        backup_root = OUTPUT_DIR / BACKUP_DIR
        backup_root.mkdir(parents=True, exist_ok=True)
        archive_root = backup_root / "archive"
        archive_root.mkdir(parents=True, exist_ok=True)

        manifest_files = {}

        # users.jsonl — one record per leaderboard user, sorted by name.
        users_records = []
        for rank, u in enumerate(leaderboard, start=1):
            users_records.append({
                "name": u.get("name", ""),
                "rank": rank,
                "total_chunks": u.get("total_chunks", 0),
                "total_messages": u.get("total_messages", 0),
                "channels_active": u.get("channels_active", 0),
                "first_seen": u.get("first_seen", ""),
                "last_seen": u.get("last_seen", ""),
                "title": u.get("title"),
                "medal": u.get("medal"),
                "awards": u.get("awards", []),
                "shill": u.get("shill", []),
                "hater": u.get("hater", []),
                "max_streak": u.get("max_streak", 0),
                "msg_per_chunk": u.get("msg_per_chunk", 0),
                "active_days": u.get("active_days", 0),
                "developed_repos": u.get("developed", []),
            })
        users_records.sort(key=lambda r: r["name"])
        manifest_files["users.jsonl"] = _write_jsonl(
            backup_root / "users.jsonl", users_records, ts_field="first_seen"
        )

        # awards.jsonl — one record per (user, award) pair.
        award_records = []
        for u in leaderboard:
            for award in (u.get("awards") or [])[:2]:
                if not award or len(award) < 2:
                    continue
                award_id, award_name = award[0], award[1]
                award_records.append({
                    "user": u.get("name", ""),
                    "award_id": award_id,
                    "award_name": award_name,
                    "reason": AWARD_REASONS.get(award_id, award_name),
                })
        award_records.sort(key=lambda r: (r["user"], r["award_id"]))
        manifest_files["awards.jsonl"] = _write_jsonl(backup_root / "awards.jsonl", award_records)

        # brand_affinities.jsonl — one record per (user, brand) shill/hater pair.
        brand_records = []
        for u in leaderboard:
            name = u.get("name", "")
            for item in (u.get("shill") or []):
                brand_records.append({
                    "user": name,
                    "brand": item.get("brand", ""),
                    "sentiment": "positive",
                    "count": item.get("count", 0),
                    "ratio": item.get("ratio", 0),
                    "is_shill": True,
                    "is_hater": False,
                })
            for item in (u.get("hater") or []):
                brand_records.append({
                    "user": name,
                    "brand": item.get("brand", ""),
                    "sentiment": "negative",
                    "count": item.get("count", 0),
                    "ratio": item.get("ratio", 0),
                    "is_shill": False,
                    "is_hater": True,
                })
        brand_records.sort(key=lambda r: (r["user"], r["brand"]))
        manifest_files["brand_affinities.jsonl"] = _write_jsonl(
            backup_root / "brand_affinities.jsonl", brand_records
        )

        # projects.jsonl — deduplicated github.com/owner/repo links seen in messages.
        repo_pattern = re.compile(r'https?://github\.com/([a-zA-Z0-9_.-]+/[a-zA-Z0-9_.-]+)')
        developer_lower = {d.lower() for d in DEVELOPER_USERS}
        project_map = {}
        for c in chunks:
            for m in c.get("messages", []):
                content = m.get("content", "") or ""
                if "github.com" not in content:
                    continue
                author = m.get("author", "")
                for match in repo_pattern.finditer(content):
                    full_path = match.group(1).rstrip("/")
                    parts = full_path.split("/")
                    if len(parts) < 2:
                        continue
                    repo_key = f"{parts[0]}/{parts[1]}"
                    entry = project_map.setdefault(repo_key, {
                        "repo": repo_key,
                        "owner": parts[0],
                        "mentioned_by_users": set(),
                        "mention_count": 0,
                    })
                    entry["mention_count"] += 1
                    if author and not is_bot(author):
                        entry["mentioned_by_users"].add(author)
        project_records = []
        for repo_key, entry in project_map.items():
            project_records.append({
                "repo": entry["repo"],
                "owner": entry["owner"],
                "mentioned_by_users": sorted(entry["mentioned_by_users"]),
                "mention_count": entry["mention_count"],
                "pinned": entry["owner"].lower() in developer_lower,
            })
        project_records.sort(key=lambda r: r["repo"])
        manifest_files["projects.jsonl"] = _write_jsonl(backup_root / "projects.jsonl", project_records)

        # leaderboard_snapshot-N.jsonl — full leaderboard state, sharded
        # to stay under CF Pages 25 MiB per-file limit (was a single 27.3
        # MB file before; same bug pattern as chunks/conversations earlier).
        # Each line is one user record (NDJSON, compact). Shard at ~3,000
        # users to leave headroom for future growth.
        LB_SNAPSHOT_SHARD_USERS = 3000
        lb_shard_files = []
        lb_shard_fh = None
        lb_shard_idx = 0
        lb_shard_size = 0
        lb_shard_count = 0
        try:
            for u in leaderboard:
                line = json.dumps(u, ensure_ascii=False, sort_keys=True) + "\n"
                if lb_shard_count >= LB_SNAPSHOT_SHARD_USERS and lb_shard_size > 0:
                    lb_shard_fh.close()
                    lb_shard_files.append(f"leaderboard_snapshot-{lb_shard_idx}.jsonl")
                    lb_shard_idx += 1
                    lb_shard_fh = open(str(backup_root / f"leaderboard_snapshot-{lb_shard_idx}.jsonl"), "w")
                    lb_shard_size = 0
                    lb_shard_count = 0
                lb_shard_fh.write(line)
                lb_shard_size += len(line)
                lb_shard_count += 1
            lb_shard_fh.close()
            if lb_shard_size > 0:
                lb_shard_files.append(f"leaderboard_snapshot-{lb_shard_idx}.jsonl")
        except Exception:
            try: lb_shard_fh.close()
            except: pass
            raise
        for shard_file in lb_shard_files:
            shard_path = backup_root / shard_file
            manifest_files[shard_file] = {
                "record_count": LB_SNAPSHOT_SHARD_USERS if shard_file != lb_shard_files[-1] else (lb_shard_count - (lb_shard_idx - len(lb_shard_files)) * LB_SNAPSHOT_SHARD_USERS),
                "byte_size": shard_path.stat().st_size,
                "oldest_record_ts": metadata.get("last_updated"),
            }

        # metadata_snapshot.json — copy of the metadata.json just written.
        meta_path = backup_root / "metadata_snapshot.json"
        meta_path.write_text(
            json.dumps(metadata, ensure_ascii=False, sort_keys=True, indent=2), encoding="utf-8"
        )
        manifest_files["metadata_snapshot.json"] = {
            "record_count": 1,
            "byte_size": meta_path.stat().st_size,
            "oldest_record_ts": metadata.get("last_updated"),
        }

        # manifest.json — index of everything written above.
        manifest = {
            "generated_at": now_iso(),
            "build_number": BUILD_NUMBER,
            "commit_sha": GIT_SHA,
            "files": manifest_files,
        }
        (backup_root / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
        )

        # Weekly archive snapshot + retention/compression cleanup.
        archive_files = list(manifest_files.keys()) + ["manifest.json"]
        _backup_archive_week(backup_root, archive_root, archive_files)
        _backup_cleanup(archive_root)

        print(f"  backups: {sum(f['record_count'] for f in manifest_files.values())} "
              f"records across {len(manifest_files)} files", file=sys.stderr)
    except Exception as e:
        print(f"  WARNING: backup_assets failed: {type(e).__name__}: {e}", file=sys.stderr)


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
    _user_msgs = {}  # T10: collect all messages per author for brand detection
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
                    "emoji_total": 0, "code_total": 0, "link_total": 0, "question_total": 0,
                    "hour_dist": {}, "total_msg_len": 0, "helper_total": 0,
                }
            s = all_author_stats[author]
            s["total_chunks"] += 1
            s["channels"].add(ch)
            # Per-author message counter (v0.2.1): incremented inside
            # the per-author loop below. The previous line counted every
            # chunk message for every author in the chunk, inflating totals
            # 3-12x for chatty users. Don't restore the old shape.
            # T9: Collect style signals per-author-message
            for _m in c.get("messages", []):
                if _m.get("author") != author:
                    continue
                s["total_messages"] += 1
                _mc = _m.get("content", "") or ""
                if any(ord(ch) > 0x1F000 for ch in _mc):
                    s["emoji_total"] += 1
                if "```" in _mc or _mc.startswith("    "):
                    s["code_total"] += 1
                if "http://" in _mc or "https://" in _mc:
                    s["link_total"] += 1
                    if _mc.strip().endswith("?"):
                        s["question_total"] += 1
                # total_msg_len counted on EVERY message (v0.2.1).
                # Previously it was inside the link check, so non-linkers
                # got only their linked messages counted and a misleading
                # "avg 1 char" reading.
                s["total_msg_len"] = s.get("total_msg_len", 0) + len(_mc)
                _mcl = _mc.lower()
                if any(w in _mcl for w in ["help", "thanks", "thank you", "appreciate", "guide me"]):
                    s["helper_total"] = s.get("helper_total", 0) + 1
                    try:
                        _ts = _m.get("timestamp", "")
                        if _ts:
                            _h = datetime.fromisoformat(_ts.replace("Z", "+00:00")).hour
                            s["hour_dist"][_h] = s["hour_dist"].get(_h, 0) + 1
                    except (KeyError, ValueError, TypeError):
                        pass
            # T10: Collect messages for brand detection
            for _m in c.get("messages", []):
                if _m.get("author") == author:
                    _user_msgs.setdefault(author, []).append(_m)
            st = c.get("start_time", "")
            if st:
                if st < s["first_seen"]:
                    s["first_seen"] = st
                if st > s["last_seen"]:
                    s["last_seen"] = st
            if chunk_date:
                s["dates_active"].add(chunk_date)

    # Compute per-user max_streak, max_gap_days, and msg_per_chunk
    for _author, _s in all_author_stats.items():
        if _s["dates_active"]:
            sorted_dates = sorted(_s["dates_active"])
            max_streak = current = 1
            max_gap = 0
            for i in range(1, len(sorted_dates)):
                try:
                    d1 = datetime.strptime(sorted_dates[i - 1], "%Y-%m-%d")
                    d2 = datetime.strptime(sorted_dates[i], "%Y-%m-%d")
                    gap = (d2 - d1).days
                    if gap == 1:
                        current += 1
                        max_streak = max(max_streak, current)
                    else:
                        current = 1
                    if gap > max_gap:
                        max_gap = gap
                except ValueError:
                    current = 1
            _s["max_streak"] = max_streak
            _s["max_gap_days"] = max_gap
        if _s["total_chunks"] > 0:
            _s["msg_per_chunk"] = _s["total_messages"] / _s["total_chunks"]
        _tm = _s.get("total_messages", 0)
        _s["avg_msg_len"] = round(_s.get("total_msg_len", 0) / max(_tm, 1))

    # T9: Compute style rates
    for _author, _s in all_author_stats.items():
        _tm = _s.get("total_messages", 0)
        _s["emoji_rate"] = _s.get("emoji_total", 0) / max(_tm, 1)
        _s["code_rate"] = _s.get("code_total", 0) / max(_tm, 1)
        _s["link_rate"] = _s.get("link_total", 0) / max(_tm, 1)
        _s["question_rate"] = _s.get("question_total", 0) / max(_tm, 1)
        _s["helper_rate"] = _s.get("helper_total", 0) / max(_tm, 1)

    # T10: Brand detection — only for users with at least 3 messages
    for _author, _s in all_author_stats.items():
        if _s.get("total_messages", 0) < 3:
            continue
        _msgs = _user_msgs.get(_author, [])
        if len(_msgs) < 3:
            continue
        brand_sent = detect_brands(_msgs)
        _s["shill_brands"], _s["hater_brands"] = assign_shill_hater(brand_sent)

    # ISSUE #13 + #5 (v0.5.0): Extract developed repos, filtered to CHANNEL_DEVELOPED
    for _author, _s in all_author_stats.items():
        _s["developed_repos"] = extract_developed(chunks, CHANNEL_DEVELOPED, author=_author)

    # T9b: Load style cache (optional, created by separate summarize.py)
    STYLE_CACHE = {}
    if (Path(__file__).resolve().parent / "style_cache.json").exists():
        STYLE_CACHE = json.loads((Path(__file__).resolve().parent / "style_cache.json").read_text())

    # v0.5.0 backlog #12, #17: Load USER-SPEC data for github_handle + roles
    _user_spec = load_user_spec_sheet()
    if _user_spec:
        print(f"  loaded USER-SPEC data for {len(_user_spec)} users", file=sys.stderr)

    # Build leaderboard with rank-aware medals + stable awards
    _ranked = sorted(all_author_stats.items(), key=lambda kv: kv[1]["total_chunks"], reverse=True)
    for rank_idx, (author, s) in enumerate(_ranked, start=1):
        tc = s.get("total_chunks", 0) or 0
        # ISSUE #7: users below MIN_CHUNKS_FOR_RANK get no rank-based
        # medal/title — ribbon + no title, regardless of leaderboard position.
        rank_for_assignment = rank_idx if tc >= MIN_CHUNKS_FOR_RANK else None
        medal = assign_medal(rank_for_assignment, author)
        awards = assign_awards(s, author)
        title = assign_title(s, rank_for_assignment)
        # Cap shill/hater at top-2 by total mention count (ISSUE #9)
        _shills = sorted(s.get("shill_brands", []) or [], key=lambda x: x.get("total", 0), reverse=True)[:2]
        _haters = sorted(s.get("hater_brands", []) or [], key=lambda x: x.get("total", 0), reverse=True)[:2]
        _first_seen = s.get("first_seen") or ""
        _last_seen = s.get("last_seen") or ""
        _spec = _user_spec.get(author.lower(), {})
        leaderboard.append({
            "name": author, "total_chunks": tc,
            "points": tc * POINTS_PER_CHUNK,
            "total_messages": s.get("total_messages", 0) or 0,
            "channels_active": len(s.get("channels", set()) or set()),
            "first_seen": _first_seen[:10] if _first_seen else "",
            "last_seen": _last_seen[:10] if _last_seen else "",
            "max_streak": s.get("max_streak", 0) or 0,
            "msg_per_chunk": round(s.get("msg_per_chunk", 0) or 0, 2),
            "active_days": len(s.get("dates_active", set()) or set()),
            "medal": medal,
            "awards": awards,
            "title": title,
            "style_heuristic": compute_style_heuristic(s),
            "style_pills": compute_style_pills(s),
            "style_summary": STYLE_CACHE.get(author, {}).get("summary"),
            "shill": _shills,
            "hater": _haters,
            "developed": s.get("developed_repos", []),
            "mod": author in MOD_USERS,
            "developer": author in DEVELOPER_USERS,
            "github_handle": _spec.get("github_handle", ""),
            "one_liner": _spec.get("one_liner", ""),
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
    # T11: GPU rank assignment — dense ranking, cursor advances on rank transitions
    _ranked_for_gpu = sorted(
        leaderboard,
        key=lambda x: (-x["total_chunks"], x.get("first_seen", "9999")),
    )
    _assigned_ranks = {}
    _prev_chunks = None
    _gpu_cursor = 1
    for _e in _ranked_for_gpu:
        _c = _e["total_chunks"]
        if _prev_chunks is not None and _c == _prev_chunks:
            _assigned_ranks[_e["name"]] = _gpu_cursor - 1
        else:
            _assigned_ranks[_e["name"]] = _gpu_cursor
            _gpu_cursor += 1
        _prev_chunks = _c
    for _e in leaderboard:
        _r = _assigned_ranks.get(_e["name"])
        if _r is not None and _r <= 105:
            _ge = GPU_BY_RANK.get(_r)
            if _ge:
                _e["gpu"] = {"rank": _ge[0], "name": _ge[1], "vram": _ge[2],
                             "category": _ge[3], "color": _ge[4],
                             "emoji": GPU_TIER_EMOJI.get(_ge[3], "\U0001F3AE")}
            else:
                _e["gpu"] = None
        else:
            _e["gpu"] = None

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
            # Parse full timestamp for hour distribution (ISSUE #3b)
            actual_hour = 0
            if "T" in st:
                try:
                    dt = datetime.fromisoformat(st.replace("Z", "+00:00"))
                    actual_hour = dt.hour
                except (ValueError, TypeError):
                    pass
            d_obj = datetime.strptime(d_date, "%Y-%m-%d")
            _day_of_week[d_obj.weekday()] = _day_of_week.get(d_obj.weekday(), 0) + 1
            _hour_day_heatmap[actual_hour][d_obj.weekday()] += 1
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

    # Compute + write chunk embeddings for hybrid retrieval (T13) -- optional.
    # Requires numpy + a reachable Ollama at localhost:11434. If either is
    # unavailable (e.g., CF Pages build sandbox, or no local Ollama), skip
    # silently and the site falls back to BM25-only search. This keeps the
    # build from being BLOCKED by an environment-dependent feature.
    emb_shard_meta = []
    n_emb_shards = 0
    try:
        print("Computing chunk embeddings via qwen3-embedding:0.6b (Ollama)...", file=sys.stderr)
        embedding_t0 = time.time()
        chunk_embeddings = compute_chunk_embeddings(chunks)
        embedding_secs = time.time() - embedding_t0
        print(f"  embeddings computed in {embedding_secs:.1f}s", file=sys.stderr)

        chunk_ids = [c.get("id", "") for c in chunks]
        n_emb = len(chunk_ids)
        n_emb_shards = 2
        emb_mid = n_emb // n_emb_shards

        for shard_idx in range(n_emb_shards):
            s_start = shard_idx * emb_mid
            s_end = s_start + emb_mid if shard_idx == 0 else n_emb
            shard_bytes = b"".join(chunk_embeddings[s_start:s_end])
            emb_path = OUTPUT_DIR / f"search-embeddings-{shard_idx}.json"
            with open(emb_path, "w") as f:
                json.dump({
                    "model": "qwen3-embedding:0.6b",
                    "dim": 1024,
                    "quantization": "int8",
                    "chunk_ids": chunk_ids[s_start:s_end],
                    "vectors_b64": base64.b64encode(shard_bytes).decode("ascii"),
                }, f, separators=(",", ":"))
            size_mb = os.path.getsize(emb_path) / 1024 / 1024
            print(f"  {emb_path.name}: {size_mb:.1f} MB", file=sys.stderr)
            emb_shard_meta.append({
                "file": emb_path.name,
                "chunks": s_end - s_start,
            })
    except Exception as e:
        print(f"  embeddings unavailable ({type(e).__name__}: {e}); skipping hybrid retrieval shards", file=sys.stderr)
        # Clean up any partial shards
        for s in range(2):
            stale = OUTPUT_DIR / f"search-embeddings-{s}.json"
            if stale.exists():
                stale.unlink()

    # ─── v0.5.0: Project of the day (ISSUE #7) ─────────────────────────
    _repo_pat = re.compile(r'https?://github\.com/([a-zA-Z0-9_.-]+/[a-zA-Z0-9_.-]+)')
    _dev_lower = {d.lower() for d in DEVELOPER_USERS}
    _project_map = {}
    for c in chunks:
        for m in c.get("messages", []):
            _content = m.get("content", "") or ""
            if "github.com" not in _content:
                continue
            _author = m.get("author", "")
            for _match in _repo_pat.finditer(_content):
                _full_path = _match.group(1).rstrip("/")
                _parts = _full_path.split("/")
                if len(_parts) < 2:
                    continue
                _key = f"{_parts[0]}/{_parts[1]}"
                _pe = _project_map.setdefault(_key, {"repo": _key, "mention_count": 0, "unique_users": set()})
                _pe["mention_count"] += 1
                if _author and not is_bot(_author):
                    _pe["unique_users"].add(_author)
    _project_list = []
    for _key, _pe in _project_map.items():
        _project_list.append({
            "repo": _pe["repo"],
            "mention_count": _pe["mention_count"],
            "unique_users": sorted(_pe["unique_users"]),
            "pinned": _pe["repo"].split("/")[0].lower() in _dev_lower,
        })
    _project_list.sort(key=lambda r: r["mention_count"], reverse=True)
    _project_of_the_day = None
    if _project_list:
        _today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        _idx = int(hashlib.sha256(_today_str.encode()).hexdigest(), 16) % len(_project_list)
        _project_of_the_day = _project_list[_idx]

    # Write metadata.json with shard counts so the client knows how many
    # search-data-N.json / search-index-N.json files to fetch.
    print("Writing metadata.json...", file=sys.stderr)
    print(f"  version: {VERSION} (build {BUILD_NUMBER}, commit {GIT_SHA})", file=sys.stderr)
    metadata = {
        "version": VERSION,
        "build": BUILD_NUMBER,
        "commit": GIT_SHA,
        "branch": GIT_BRANCH,
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
            "embeddings": n_emb_shards,
        },
    }
    if emb_shard_meta:
        metadata["embeddings"] = {
            "model": "qwen3-embedding:0.6b",
            "dim": 1024,
            "quantization": "int8",
            "files": [m["file"] for m in emb_shard_meta],
        }
    if _project_of_the_day:
        metadata["project_of_the_day"] = _project_of_the_day
    with open(OUTPUT_DIR / "metadata.json", "w") as f:
        json.dump(metadata, f, indent=2)
    print(f"  metadata.json: {os.path.getsize(OUTPUT_DIR / 'metadata.json') / 1024 / 1024:.2f} MB",
          file=sys.stderr)

    # T15: AI-friendly data exports
    print("Writing AI-friendly data exports...", file=sys.stderr)
    export_counts = compute_ai_exports(chunks_data, leaderboard)
    print(f"  chunks: {export_counts['chunks']}, conversations: {export_counts['conversations']}, "
          f"users: {export_counts['users']}, rag_chunks: {export_counts['rag_chunks']}",
          file=sys.stderr)

    # ISSUE #6: Rolling backups of build state
    print("Writing backups...", file=sys.stderr)
    backup_assets(leaderboard, metadata, chunks)

    # ISSUE #18 (v0.5.0): Plugins/skills/skins catalog
    print("Writing plugins/skills/skins catalog...", file=sys.stderr)
    compute_plugins_skills_skins_catalog(chunks)

    print(f"\nDone in {elapsed:.1f}s. {len(chunks)} chunks indexed.", file=sys.stderr)
    print(json.dumps(metadata))

if __name__ == "__main__":
    # ISSUE #5: idempotency hook — never let a bad archive/parse leave the
    # repo in a half-built state. Record the failure so the auto-ingest
    # workflow can detect it and fail loudly instead of opening a PR with
    # partial/missing data shards.
    failure_marker = OUTPUT_DIR / "build_failed.json"
    try:
        main()
    except Exception as e:
        import traceback
        with open(failure_marker, "w") as f:
            json.dump({
                "error": f"{type(e).__name__}: {e}",
                "traceback": traceback.format_exc(),
                "timestamp": now_iso(),
            }, f, indent=2)
        print(f"BUILD FAILED: {type(e).__name__}: {e}", file=sys.stderr)
        sys.exit(1)
    else:
        if failure_marker.exists():
            failure_marker.unlink()
