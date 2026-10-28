# Temporary memory research

Research date: 2026-10-28. Sources inspected from upstream `main`; behavior may change.

## Hermes Agent

- [Initialization](https://github.com/NousResearch/hermes-agent/blob/main/agent/agent_init.py) loads disk-backed `MEMORY.md` and `USER.md` into persistent prompt memory, with default character budgets of 2,200 and 1,375 respectively. This is a small curated layer, not the full conversation archive.
- [Memory tool](https://github.com/NousResearch/hermes-agent/blob/main/tools/memory_tool.py) exposes add, patch and remove (including batch operations), with capacity checking. Preserve explicit operations and evidence rather than silently treating every utterance as a durable fact.
- [Session search](https://github.com/NousResearch/hermes-agent/blob/main/tools/session_search_tool.py) retrieves historical messages using FTS5 and can locate the relevant surrounding session. The [FTS implementation](https://github.com/NousResearch/hermes-agent/blob/main/hermes_state_fts.py) describes optional CJK bigram tokenization and notes trigram fails for one- and two-character Chinese terms, falling back to a scan. Search and context injection should be bounded and retain source references.

## OpenClaw comparison

[Official memory documentation](https://github.com/openclaw/openclaw/blob/main/docs/concepts/memory.md) describes curated profile and long-term Markdown, plus dated detailed notes indexed for search. It preserves source files when importing from other assistants. Keyword-only search works without an embedding provider; vector and keyword search can be combined when one is configured. These ideas fit a temporary standalone plugin with independently exportable source records.

## Implementation implications

Keep original context in an append-only, timestamped archive with session/message provenance for later migration. Maintain a small separately editable curated profile and notes, index the archive and retrieve bounded excerpts on demand. For Chinese, use overlapping character bigrams and a substring fallback for short queries; verify retrieval with Chinese examples, including two-character terms. Do not claim semantic retrieval when only lexical search is active. Any optional embedding provider should have clear configuration, index model/version metadata, and a working lexical fallback when unavailable.

[FastEmbed model definitions](https://github.com/qdrant/fastembed/blob/main/fastembed/text/onnx_embedding.py) list `BAAI/bge-small-zh-v1.5` as Chinese, 512-dimensional, MIT, roughly 0.09 GB, with an ONNX model from `Qdrant/bge-small-zh-v1.5` on Hugging Face. The enforced cloud egress policy allows GitHub/raw and PyPI but not Hugging Face or OpenAI API, and reports no credential bindings. The model and Windows/Python 3.12 execution were not downloaded or tested; PyPI availability alone does not supply model weights. A user-provided local model or explicitly configured OpenAI-compatible embeddings endpoint may be viable in another environment, but requires separately verifying model files, dependencies, credentials, and network access.
