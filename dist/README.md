# dist/

Generated at build time by `build_index.py`:

- `chunks-N.jsonl` — chunked shards of message content for embedding search
- `conversations-N.jsonl` — sharded conversation export
- `users.jsonl` — per-user summary
- `rag-corpus/chunks-N.jsonl` — per-chunk corpus for RAG ingestion
- `backups/` — rolling JSONL backups (weekly archive, 52-week retention)

Not in this repo by design. Re-generate with `python3 build_index.py`.
