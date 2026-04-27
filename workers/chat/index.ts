// abalonecove.org — QA chat Worker
//
// Route: POST 501.abalonecove.org/api/chat
// Body:  { message: string, history?: {role: "user"|"assistant", content: string}[] }
// Returns: streaming text/event-stream (SSE)
//
// Rate limiting: hashed IP → KV counter, 30 req/hour. Disabled if RATE_LIMIT not bound.
// Privacy: raw IPs are never stored. Hash is SHA-256(ip + date-hour-salt).

export interface Env {
  ANTHROPIC_API_KEY: string;
  RATE_LIMIT?: KVNamespace;
  KB?: KVNamespace;
}

// ── RAG ──────────────────────────────────────────────────────────────────────

interface Chunk {
  id: string;
  source: string;
  heading: string;
  text: string;
}

// Module-level cache — survives for the lifetime of the Worker isolate
let kbCache: Chunk[] | null = null;

async function loadKB(env: Env): Promise<Chunk[]> {
  if (kbCache) return kbCache;
  if (!env.KB) return [];
  const raw = await env.KB.get("chunks");
  if (!raw) return [];
  try {
    kbCache = JSON.parse(raw) as Chunk[];
    return kbCache;
  } catch {
    return [];
  }
}

function tokenize(text: string): Set<string> {
  return new Set(
    text.toLowerCase()
      .replace(/[^a-z0-9\s-]/g, " ")
      .split(/\s+/)
      .filter(t => t.length > 2)
  );
}

function scoreChunk(queryTokens: Set<string>, chunk: Chunk): number {
  const bodyTokens = tokenize(chunk.text);
  const headingTokens = tokenize(chunk.heading);
  let score = 0;
  for (const qt of queryTokens) {
    if (bodyTokens.has(qt)) score += 1;
    if (headingTokens.has(qt)) score += 2; // heading match worth more
  }
  return score;
}

async function retrieveChunks(env: Env, query: string, topK = 5): Promise<Chunk[]> {
  const chunks = await loadKB(env);
  if (!chunks.length) return [];
  const queryTokens = tokenize(query);
  if (!queryTokens.size) return [];

  const scored = chunks
    .map(c => ({ chunk: c, score: scoreChunk(queryTokens, c) }))
    .filter(x => x.score > 0)
    .sort((a, b) => b.score - a.score)
    .slice(0, topK);

  return scored.map(x => x.chunk);
}

const ALLOWED_ORIGINS = [
  "https://501.abalonecove.org",
  "https://abalonecove.org",
];

const CORS_HEADERS = (origin: string) => ({
  "Access-Control-Allow-Origin": ALLOWED_ORIGINS.includes(origin) ? origin : ALLOWED_ORIGINS[0],
  "Access-Control-Allow-Methods": "POST, OPTIONS",
  "Access-Control-Allow-Headers": "Content-Type",
  "Access-Control-Max-Age": "86400",
});

const SYSTEM_PROMPT = `You are a research assistant for the Abalone Cove Foundation — a small California nonprofit that collects, transcribes, and maintains primary-source documentation about the Abalone Cove area of Rancho Palos Verdes, California.

## What the Foundation is

The Foundation does three things: (a) collects and maintains primary sources relevant to the coastal strip around Abalone Cove; (b) writes factual narrative around those sources, cited and dated; and (c) occasionally publishes a position paper when a public record is owed on a specific question. The Foundation does not represent any party in any litigation. It does not take a position on SB 9 or SB 330. It does not oppose development of the Palos Verdes Peninsula.

## The central question: The 24-inch line

The Foundation's active position concerns a 24-inch diameter line on or adjacent to two parcels at the inland edge of Abalone Cove:
- **APN 7573-006-024** (0 Clipper Road) — a vacant 1.56-acre lot owned by Clipper Development LLC since 2021, rezoned for high-density residential. An active $2.1M commercial loan from Hankey Capital was recorded Nov 2025.
- **APN 7573-007-900** — an unbuilt strip retained by the former RPV Redevelopment Agency successor under the Long Range Property Management Plan (2014).

Both parcels sit inside the Portuguese Bend / Abalone Cove landslide complex, moving continuously since 1956. The City's own 2018 Zone 2 EIR states: *"groundwater is the controlling factor causing slide movement."*

**The hypothesis**: The 24-inch line may be a remnant of the pre-1956 Palos Verdes Coast Road right-of-way (recorded Book 6059 p. 178), disconnected from the modern sewer system, and potentially exfiltrating water into the lower Filiorum canyon. Residential plumbing is named in the EIR as the likely 1956 catalyst for slide onset.

**This is a hypothesis, not a finding.** The Foundation states it to prompt five investigative steps:
1. Title search on the PV Coast Road ROW (Book 6059 p. 178) — abandonment date; infrastructure disposition.
2. 1956 reparceling map overlay — did Parcel 106's boundary move across the line?
3. LA County Sanitation District + RPV Public Works inventory — is the line maintained? Last inspection?
4. Dye trace or CCTV inspection of the line.
5. ACLAD dewatering well hydrology — signature consistent with an anthropogenic source?

If investigation shows the line is maintained, inspected, and dry — the Foundation will retract the position paper and say so.

## The site's ten historical eras

1. **The land** — Tongva village at Chowigna; Spanish/Mexican land grant era; the Rancho de los Palos Verdes partition (1882 Bixby et al. v. Bent et al.).
2. **Vanderlip** — Frank Vanderlip's acquisition of the peninsula; the Olmsted Brothers landscape plan; the pre-development era.
3. **PV Corp** — Palos Verdes Corporation's subdivision scheme; the Declaration system (Declaration No. One, 1923; Declaration One-A oceanfront overlay; Local Declarations including Tract 14649).
4. **The covenants** — the CC&R framework, the Art Jury, the covenant enforcement mechanism, and Tract 14649's founding (1949).
5. **Cityhood** — Rancho Palos Verdes incorporation (1973); Coastal Act (1976); the Coastal Specific Plan.
6. **Portuguese Bend slides** — 1956 slide onset; ACLAD formation; the groundwater/plumbing causation record; the Abalone Cove Landslide reactivation (1974).
7. **Shore Club fight** — the 1972 Abalone Shore Club vote rejecting high-density condo development in favor of coastal park preservation. Precedent for the 0 Clipper fight.
8. **Sister plots** — Lot H boundary history; the 1949/1957/1980/1986 tract map sequence; Parcel 106 and the reparceling question.
9. **Development tries** — multiple failed attempts to develop 0 Clipper Road; 2024 writ petition (No. 24TRCP00352) challenging RPV's rezoning.
10. **The conclusion** — open questions and the Foundation's forward program.

## Evidence library categories

- **PV Corp Declarations** (1923–1952): Declaration No. One, Declaration One-A, local declarations for Tract 14649
- **WPBCA Governance** (1949–2009): founding and governing instruments of the HOA
- **Parcel History & Conveyances** (1880–2026): chain of title for Lot H, Tract 14649, Parcel 106, and 0 Clipper Road
- **Geological Studies** (1935–2012): federal, state, and commissioned studies on the Portuguese Bend / Abalone Cove landslide complex
- **Case Law**: decisions bearing on the declaration scheme, landslide liability, covenant enforceability, and the Coastal Act
- **RPV Regulatory Record** (1975–2025): City of RPV planning framework
- **State & Federal Framework**: Coastal Act, SB 9, SB 330, housing element statutes
- **Landslide & Hazard Documentation**: active hazard documentation, moratorium record, monitoring data
- **Historical & Promotional Records**: Vanderlip-era materials, pre-development aerial photos
- **Newspaper Record**: press documentation of development proposals and community history
- **Shore Club Record** (1971–1972): the 1972 vote documentation
- **Active Litigation**: filings in No. 24TRCP00352

## Documented gaps we are still trying to close

- **Declaration 100, Book 9436 page 155** — the property-description page. LA County Recorder microfilm is illegible.
- **Pre-1956 Coast Road right-of-way, Book 6059 page 178** — date and manner of abandonment; infrastructure disposition.
- **The 24-inch line itself** — any inspection logs, asbuilts, dye-trace/CCTV reports, correspondence between LA County Sanitation Districts and the City of RPV.
- **Eyewitness memory** of the corridor from the 1950s–1980s.

## How to contribute

The Foundation's contribute page (/position/contribute/) accepts eyewitness observations, document pointers, media uploads, and historical knowledge. Two-editor review. No IP logs. Encrypted at rest. Rebuttal offered to any named party before publication.

## Tone and posture

Answer factually, precisely, and neutrally — in the same voice as the Foundation's published materials. Cite specific documents, APNs, book/page references, and dates where relevant. When you don't know something, say so. When a question is outside the Foundation's documented record, direct the user to the contribute page or note what primary source would answer the question. Never speculate beyond what the Foundation's documents support. Never assert the hypothesis as a finding.`;

async function hashIP(ip: string): Promise<string> {
  const now = new Date();
  const salt = `${now.getUTCFullYear()}-${now.getUTCMonth()}-${now.getUTCDate()}-${now.getUTCHours()}`;
  const data = new TextEncoder().encode(ip + salt);
  const hash = await crypto.subtle.digest("SHA-256", data);
  return Array.from(new Uint8Array(hash)).slice(0, 8).map(b => b.toString(16).padStart(2, "0")).join("");
}

async function checkRateLimit(env: Env, ip: string): Promise<boolean> {
  if (!env.RATE_LIMIT) return true;
  const key = "rl:" + await hashIP(ip);
  const val = await env.RATE_LIMIT.get(key);
  const count = val ? parseInt(val, 10) : 0;
  if (count >= 30) return false;
  await env.RATE_LIMIT.put(key, String(count + 1), { expirationTtl: 3600 });
  return true;
}

export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    const origin = request.headers.get("Origin") ?? "";
    const corsHeaders = CORS_HEADERS(origin);

    if (request.method === "OPTIONS") {
      return new Response(null, { status: 204, headers: corsHeaders });
    }

    if (request.method !== "POST") {
      return new Response("method not allowed", { status: 405, headers: corsHeaders });
    }

    const ip = request.headers.get("CF-Connecting-IP") ?? "unknown";
    const allowed = await checkRateLimit(env, ip);
    if (!allowed) {
      return new Response(
        JSON.stringify({ error: "Rate limit reached. Please try again in an hour." }),
        { status: 429, headers: { ...corsHeaders, "Content-Type": "application/json" } }
      );
    }

    let body: { message?: string; history?: { role: string; content: string }[] };
    try {
      body = await request.json();
    } catch {
      return new Response("bad json", { status: 400, headers: corsHeaders });
    }

    if (!body.message?.trim()) {
      return new Response("missing message", { status: 400, headers: corsHeaders });
    }

    const userMessage = body.message.trim().slice(0, 2000);

    // RAG: retrieve relevant chunks and prepend as context
    const relevantChunks = await retrieveChunks(env, userMessage);
    let contextBlock = "";
    if (relevantChunks.length > 0) {
      contextBlock = "RETRIEVED DOCUMENT EXCERPTS (use these to answer if relevant):\n\n" +
        relevantChunks.map(c =>
          `[Source: ${c.source} — ${c.heading}]\n${c.text}`
        ).join("\n\n---\n\n") +
        "\n\n---\n\nUser question: ";
    }

    const messages = [
      ...(body.history ?? []).slice(-6).map(m => ({
        role: m.role as "user" | "assistant",
        content: m.content,
      })),
      { role: "user" as const, content: contextBlock + userMessage },
    ];

    const anthropicResp = await fetch("https://api.anthropic.com/v1/messages", {
      method: "POST",
      headers: {
        "x-api-key": env.ANTHROPIC_API_KEY,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
      },
      body: JSON.stringify({
        model: "claude-haiku-4-5-20251001",
        max_tokens: 1024,
        stream: true,
        system: SYSTEM_PROMPT,
        messages,
      }),
    });

    if (!anthropicResp.ok) {
      return new Response(
        JSON.stringify({ error: "Upstream error" }),
        { status: 502, headers: { ...corsHeaders, "Content-Type": "application/json" } }
      );
    }

    // Pass the Anthropic SSE stream through, translated to our own simpler SSE format
    const { readable, writable } = new TransformStream<Uint8Array, Uint8Array>();
    const writer = writable.getWriter();
    const encoder = new TextEncoder();
    const decoder = new TextDecoder();

    (async () => {
      const reader = anthropicResp.body!.getReader();
      let buffer = "";
      try {
        while (true) {
          const { done, value } = await reader.read();
          if (done) break;
          buffer += decoder.decode(value, { stream: true });
          const lines = buffer.split("\n");
          buffer = lines.pop() ?? "";
          for (const line of lines) {
            if (!line.startsWith("data: ")) continue;
            const data = line.slice(6);
            if (data === "[DONE]") continue;
            try {
              const event = JSON.parse(data);
              if (event.type === "content_block_delta" && event.delta?.type === "text_delta") {
                const chunk = event.delta.text;
                await writer.write(encoder.encode(`data: ${JSON.stringify({ text: chunk })}\n\n`));
              }
              if (event.type === "message_stop") {
                await writer.write(encoder.encode("data: [DONE]\n\n"));
              }
            } catch {
              // skip malformed events
            }
          }
        }
      } finally {
        await writer.close();
      }
    })();

    return new Response(readable, {
      headers: {
        ...corsHeaders,
        "Content-Type": "text/event-stream",
        "Cache-Control": "no-cache",
        "X-Accel-Buffering": "no",
      },
    });
  },
};
