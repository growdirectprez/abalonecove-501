# Deploy: abalonecove-chat Worker

## Prerequisites

- Cloudflare account with `abalonecove.org` zone
- `wrangler` CLI installed and authenticated
- Anthropic API key

## Steps

### 1. Create KV namespaces

```bash
wrangler kv:namespace create RATE_LIMIT
wrangler kv:namespace create KB
```

Copy the returned IDs into `wrangler.toml`:

```toml
[[kv_namespaces]]
binding = "RATE_LIMIT"
id = "<RATE_LIMIT id>"

[[kv_namespaces]]
binding = "KB"
id = "<KB id>"
```

### 2. Set API key as a secret

```bash
wrangler secret put ANTHROPIC_API_KEY
# paste key at prompt — never stored in wrangler.toml
```

### 3. Uncomment and set the route in wrangler.toml

```toml
[[routes]]
pattern = "501.abalonecove.org/api/chat"
zone_name = "abalonecove.org"
```

### 4. Deploy

```bash
cd workers/chat
wrangler deploy
```

### 5. Verify

```bash
curl -X POST https://501.abalonecove.org/api/chat \
  -H "Content-Type: application/json" \
  -d '{"message":"What is the Foundation?"}' \
  --no-buffer
```

You should see SSE lines streaming back (`data: {"text":"..."}` chunks, ending with `data: [DONE]`).

## Rate limiting

The worker uses a KV-backed counter: SHA-256(ip + date-hour-salt), capped at 30 requests/hour. Raw IPs are never stored. If the RATE_LIMIT binding is absent the cap is unenforced (rely on Cloudflare network-level rate limiting rules instead).

## Building and uploading the knowledge base

The RAG knowledge base is built from the verbatim document corpus:

```bash
# From the repo root (abalonecove/)
cd workers/chat
node build-kb.mjs
```

This writes `workers/chat/knowledge-base.json` (~50+ verbatim docs, section-chunked).

Then upload to KV (replace `<KB namespace id>` with the id from wrangler.toml):

```bash
wrangler kv:key put --binding KB "chunks" --path workers/chat/knowledge-base.json
```

The worker caches the loaded KB in-memory for the lifetime of the isolate, so the
first request after a cold start fetches from KV; subsequent requests use the cache.

Re-run `build-kb.mjs` and re-upload whenever verbatim transcriptions are added to
`abalonecove/docs/` or `GrowDirect/Cove/docs/archive/originals/transcriptions/`.

## Updating the system prompt

The baseline context (24-inch line position, 10 eras, evidence categories, gaps) is
in `index.ts` — the `SYSTEM_PROMPT` constant. Edit and redeploy.
