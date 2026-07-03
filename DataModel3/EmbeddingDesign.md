# pgvector Embedding Design — healthv10 (Home Edition)

**Date:** 2026-06-18 11:00 PDT

Home Edition does semantic search and retrieval over free text with pgvector. This page records the one thing that matters operationally: **which embedding model we use, why, and the rules that keep the stored vectors coherent.**

## The model

| Setting | Value |
|---------|-------|
| Model | `nomic-embed-text-v2-moe` (env `EMBEDDING_MODEL`, default `nomic-embed-text-v2-moe:latest`) |
| Languages | ~100 — multilingual mixture-of-experts (475M total / 305M active params) |
| Dimensions | 768 — column type `VECTOR(768)` (pgvector) |
| **Max input** | **512 tokens** (~350–400 words of English; fewer for some languages). Text beyond this is truncated — see "Context window" below. |
| Distance | cosine (`vector_cosine_ops`, the `<=>` operator) |
| Index | IVFFlat, `lists = 100` |
| Served by | **Ollama on the host** (`OLLAMA_URL`, default `http://host.docker.internal:11434`) |

Embedding is **best-effort**: an unreachable or slow Ollama never blocks a write. The helper (`UserApp/webapp/embedding_utils.py`) has a short deadline (`EMBEDDING_DEADLINE`, default 20s); on timeout or error it returns `None` and the row is stored with a `NULL` vector, to be re-embedded later. Records and documents always complete regardless of embedding status.

## Why v2-moe (we moved off v1.5)

Earlier drafts of this design used `nomic-embed-text-v1.5`, an English-first model. We switched to **`nomic-embed-text-v2-moe` for multilingual support** — it's a mixture-of-experts model trained across ~100 languages, so a household whose members write notes, food entries, or document annotations in more than one language gets meaningful semantic matches instead of English-only behavior.

The switch is **dimension-compatible**: v2-moe also produces 768-dim vectors, so no column types changed. Only the vector *contents* differ — which is exactly why the rules below matter.

It is **not** context-compatible, and that's the deliberate tradeoff: v1.5 accepted up to **8,192 tokens**, but v2-moe accepts only **512**. We chose multilingual reach over long-context capacity. For a household tracker that's the right call — the things we embed are mostly short (names, allergens, conditions, a few sentences of observation) — but it does change how long text behaves (next section).

## The rules (don't break these)

- **One model per column, everywhere.** Vectors from different models live in different vector spaces; cosine similarity between a v1.5 vector and a v2-moe vector is meaningless. Every value in a given `embedding_*` column must come from the same model.
- **No on-device embedding.** v2-moe is too large to run on phone hardware, and a smaller on-device model would write vectors in a *different* space into the same column — silently corrupting similarity. All embedding happens server-side via the host Ollama. Mobile clients send text; the server embeds it.
- **If the model ever changes, re-embed everything.** Swapping `EMBEDDING_MODEL` invalidates every stored vector. Regenerate all `embedding_*` columns (set them `NULL` and re-run the embedding pass) before trusting search again.
- **768 is fixed.** Because the dimension stays 768 across model changes, the schema is unaffected — only data is regenerated.

## What gets embedded

Eight `VECTOR(768)` columns in the running schema (`Infrastructure/init/docker-init-home/02-home_schema.sql`), each with a matching IVFFlat index:

| Table | Column | Embedded text |
|-------|--------|---------------|
| `health_observations` | `embedding_content` | Free-text observations — primary semantic-search / RAG source |
| `health_inputs` | `embedding_name` | Med / supplement name — freeform-log dedup matching |
| `health_food_itemsv2` | `embedding_name` | Food item name — freeform-log dedup matching |
| `health_conditions` | `embedding_condition` | Condition name / description |
| `health_allergies` | `embedding_allergy_full` | Allergen + reaction + notes |
| `documents` | `embedding_content` | OCR'd document text (in-process OCR pipeline) |
| `document_annotations` | `embedding_body` | Annotation body |
| `mobile_events` | `embedding_event_text` | Mobile event text |

Columns are nullable so rows can exist before their vector is computed and an embedding failure never blocks the write. Re-embed on edit when the source text changes.

## Context window — handling long text

v2-moe embeds at most **512 tokens** of input. We do **no chunking and no truncation in the app** — `get_embedding()` sends the full string to Ollama, and the model truncates anything past 512 tokens itself. So a vector only ever reflects roughly the **first ~350–400 words** of its source text.

What that means per column:

- **Short fields are unaffected.** Names, allergens, conditions, food items, and most observations fit comfortably under 512 tokens — the whole text is embedded.
- **Long free text is embedded by its opening only.** A long `documents.embedding_content` (OCR'd multi-page document) or a long `health_observations` entry is represented by its first ~512 tokens; later content does not influence the vector. Semantic search will match on the opening, not the body.

This is acceptable for the household use cases here (short notes and catalog matching), and it is a deliberate consequence of choosing the multilingual model. If whole-document semantic search over long OCR'd documents becomes important, the fix is **chunking** — split long text into ≤512-token passages, embed each, and store/search them per-passage — which is future work, not part of the current single-vector-per-row design. Until then, don't assume a document's vector represents the entire document.

## Query shape

Search is a cosine-distance order-by, always carrying the explicit per-user predicate (privacy is enforced in the app via `user_id`):

```sql
SELECT id, content,
       1 - (embedding_content <=> $query_embedding) AS similarity
FROM health_observations
WHERE tenant_id = 1 AND user_id = $user_id      -- app-level scoping
  AND embedding_content IS NOT NULL
ORDER BY embedding_content <=> $query_embedding
LIMIT 5;
```

## Scope note

This is the Home Edition design: one box, one household, host Ollama, no cloud embedding services. Embedding is best-effort — an unreachable Ollama never blocks a write; documents and records still complete, just without a vector until re-embedded.
