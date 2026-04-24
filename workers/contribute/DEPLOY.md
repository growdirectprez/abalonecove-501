# Deploying the abalonecove contribute Worker

**Status:** scaffold only. Not deployed. The site form at `/position/contribute/`
is a stub that does NOT transmit anything on this run.

## Prerequisites

1. Cloudflare account with access to the `abalonecove.org` zone.
2. `wrangler` CLI authenticated: `wrangler login`
3. An `age` keypair generated on the Foundation's side. The private key is
   held as Shamir shares by two named editors; the public key goes into
   `wrangler.toml` as `AGE_PUBKEY`.
4. Cloudflare R2 bucket `abalonecove-contribute` created in the dashboard.
5. Cloudflare KV namespace for `SUBMISSIONS_META` created and its id pasted
   into `wrangler.toml`.
6. A real age implementation vendored into `index.ts`. The scaffold throws
   on encryption — do not deploy until this is done.

## One-time setup

```bash
# From ~/abalonecove/workers/contribute/

# 1. Create R2 bucket
wrangler r2 bucket create abalonecove-contribute

# 2. Create KV namespace for submission metadata
wrangler kv:namespace create SUBMISSIONS_META
# copy the id into wrangler.toml

# 3. Set the age public key as a var (or paste into wrangler.toml)
wrangler secret put AGE_PUBKEY
# paste the public key produced by `age-keygen` on the Foundation side

# 4. Route binding — in the Cloudflare dashboard, add a custom route:
#    contribute.abalonecove.org/*  →  this worker
```

## Deploy

```bash
wrangler deploy
```

## Verify

```bash
curl -X POST https://contribute.abalonecove.org \
  -H "Content-Type: application/json" \
  -d '{"kind":"eyewitness","body":"test"}'
# expect: {"ok":true,"ticket":"<12-hex>"}

# Confirm ciphertext landed in R2:
wrangler r2 object list abalonecove-contribute --prefix submissions/
```

## Decrypting a submission (editors only)

```bash
wrangler r2 object get abalonecove-contribute submissions/<ticket>.age \
  > /tmp/<ticket>.age

# Reassemble the age private key from the two editor shares (out of band)
# then decrypt:
age -d -i /tmp/private.key /tmp/<ticket>.age
```

## Things to decide before deploy

- **Rate limiting.** Cloudflare Turnstile at the edge is allowed (it is a
  first-party CF script). The site dispatch prohibits third-party scripts on
  `/contribute/` and `/position/contribute/` — Turnstile is a policy call.
  If Turnstile is not used, implement a sliding-window rate limit in the
  Worker keyed by `CF-Connecting-IP` WITHOUT storing the IP (hash + discard).
- **Payload size cap.** Default R2 object size is the cap; we probably want
  to reject bodies > 5 MB in the Worker before R2 hits.
- **Attachment upload.** Current scaffold accepts text body only. Media
  upload support requires either (a) multipart form handling in the Worker
  and a chunked R2 upload, or (b) a signed R2 pre-signed URL returned to
  the client. Second approach scales better.
- **Editor workflow.** Two-editor review is a policy, not a mechanism. The
  mechanism likely lives in a separate authenticated page on abalonecove.org
  that lists pending tickets and decrypts them client-side. Out of scope
  for this scaffold.

## Do not deploy until

- [ ] A real age implementation is vendored into `index.ts`
- [ ] `AGE_PUBKEY` secret is set in Cloudflare
- [ ] R2 bucket + KV namespace exist and are bound
- [ ] Rate-limiting decision is made and implemented
- [ ] Editor-side decrypt flow exists somewhere
- [ ] A privacy-policy page on abalonecove.org describes this system in
      plain language for submitters (it exists at `/position/contribute/how-it-works/`)
