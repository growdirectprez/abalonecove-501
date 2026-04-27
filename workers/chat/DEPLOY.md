# Deploy: abalonecove-chat Worker

## Prerequisites

- Cloudflare account with `abalonecove.org` zone
- `wrangler` CLI installed and authenticated
- Anthropic API key

## Steps

### 1. Create KV namespace (rate limiting)

```bash
wrangler kv:namespace create RATE_LIMIT
```

Copy the returned `id` into `wrangler.toml`:

```toml
[[kv_namespaces]]
binding = "RATE_LIMIT"
id = "<id from above>"
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

## Updating the knowledge base

The system prompt is in `index.ts` — the `SYSTEM_PROMPT` constant. Edit and redeploy. For a future version, replace the inline prompt with a fetch from an R2 object so the knowledge base can be updated without a full deploy.
