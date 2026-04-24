// abalonecove.org — anonymous contribute Worker
//
// This is a SCAFFOLD. It is not deployed. See DEPLOY.md.
//
// Design constraints from the site-regen dispatch:
//   - No IP logs
//   - Payload encrypted at rest with an age public key; only two named
//     editors hold fragments of the private key
//   - Submissions are unpublished by default; two-editor review before
//     anything appears on the public site
//   - Contributor-provided rebuttal mechanism honored
//   - No third-party scripts, no hCaptcha, no analytics — rate limiting
//     is done at the CF edge via Turnstile OR by IP heuristics (without
//     logging the IPs). TBD at deploy time.
//
// This scaffold handles the happy path (POST JSON, encrypt, store, return
// a ticket id) and a minimal OPTIONS preflight. It deliberately omits
// hardening that must be re-decided at deploy time (rate limiting,
// Turnstile, schema enforcement, payload size caps beyond the defaults).

export interface Env {
  SUBMISSIONS: R2Bucket;
  SUBMISSIONS_META: KVNamespace;
  AGE_PUBKEY: string;
}

interface Submission {
  kind: "eyewitness" | "document-pointer" | "media-upload" | "historical-knowledge";
  body: string;
  url?: string;
  contact_optional?: string;
  ts: string;
  ticket: string;
}

function hexTicket(): string {
  const buf = new Uint8Array(12);
  crypto.getRandomValues(buf);
  return Array.from(buf, b => b.toString(16).padStart(2, "0")).join("");
}

async function encryptWithAge(_pubkey: string, plaintext: Uint8Array): Promise<Uint8Array> {
  // TODO(deploy): replace with a real age implementation. Options:
  //   - https://github.com/FiloSottile/age (reference; Go)
  //   - https://github.com/hashgraph/hedera-sdk-js bundled libsodium
  //   - https://github.com/mozilla/rage compiled to WASM
  //
  // Until an implementation is vendored, this scaffold refuses to store
  // anything — we will not accept plaintext submissions.
  throw new Error("age encryption not implemented in scaffold; see DEPLOY.md");
}

const CORS_HEADERS = {
  "Access-Control-Allow-Origin": "https://abalonecove.org",
  "Access-Control-Allow-Methods": "POST, OPTIONS",
  "Access-Control-Allow-Headers": "Content-Type",
  "Access-Control-Max-Age": "86400",
};

export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    if (request.method === "OPTIONS") {
      return new Response(null, { status: 204, headers: CORS_HEADERS });
    }

    if (request.method !== "POST") {
      return new Response("method not allowed", { status: 405, headers: CORS_HEADERS });
    }

    let body: Partial<Submission>;
    try {
      body = await request.json();
    } catch {
      return new Response("bad json", { status: 400, headers: CORS_HEADERS });
    }

    if (!body.kind || !body.body) {
      return new Response("missing fields", { status: 400, headers: CORS_HEADERS });
    }

    const ticket = hexTicket();
    const submission: Submission = {
      kind: body.kind as Submission["kind"],
      body: body.body,
      url: body.url,
      contact_optional: body.contact_optional,
      ts: new Date().toISOString(),
      ticket,
    };

    const plaintext = new TextEncoder().encode(JSON.stringify(submission));
    const ciphertext = await encryptWithAge(env.AGE_PUBKEY, plaintext);

    await env.SUBMISSIONS.put(`submissions/${ticket}.age`, ciphertext);
    await env.SUBMISSIONS_META.put(ticket, JSON.stringify({
      kind: submission.kind,
      ts: submission.ts,
      status: "pending-review",
    }));

    return new Response(JSON.stringify({ ok: true, ticket }), {
      status: 200,
      headers: { ...CORS_HEADERS, "Content-Type": "application/json" },
    });
  },
};
