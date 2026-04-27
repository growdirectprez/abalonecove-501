#!/usr/bin/env node
/**
 * build-kb.mjs — Build the Foundation knowledge base for RAG
 *
 * Reads all verbatim markdown docs from the abalonecove repo and the
 * Cove transcriptions archive, chunks them by section heading, and
 * writes knowledge-base.json for upload to Cloudflare KV.
 *
 * Usage:
 *   node build-kb.mjs
 *
 * Then upload:
 *   wrangler kv:key put --binding KB "chunks" --path knowledge-base.json
 *   (or set the KV namespace ID in wrangler.toml first)
 */

import { readFileSync, writeFileSync, readdirSync, statSync } from 'fs';
import { join, relative, basename } from 'path';

// ── Source paths ──────────────────────────────────────────────────────────────

const REPO_ROOT = new URL('../..', import.meta.url).pathname.replace(/\/$/, '');
const COVE_TRANSCRIPTIONS = join(
  REPO_ROOT, '..', 'GrowDirect', 'Cove',
  'docs', 'archive', 'originals', 'transcriptions'
);

// All abalonecove/docs/ markdown files
const ABALONECOVE_DOCS = join(REPO_ROOT, 'docs');

// Exclude these — internal or not verbatim primary source
const EXCLUDE_PATTERNS = [
  /DRAFT/i,
  /2008-easement-status-agreement/i,
  /2015-prelim-title-report-25-seacove/i,
];

// ── Helpers ───────────────────────────────────────────────────────────────────

function walkMd(dir) {
  const results = [];
  try {
    for (const entry of readdirSync(dir)) {
      const full = join(dir, entry);
      const stat = statSync(full);
      if (stat.isDirectory()) {
        results.push(...walkMd(full));
      } else if (entry.endsWith('.md') && !entry.startsWith('.')) {
        results.push(full);
      }
    }
  } catch { /* dir doesn't exist */ }
  return results;
}

function stripFrontmatter(text) {
  if (!text.startsWith('---')) return text;
  const end = text.indexOf('\n---', 3);
  if (end === -1) return text;
  return text.slice(end + 4).trimStart();
}

function cleanLabel(filename) {
  return basename(filename, '.md')
    .replace(/-verbatim$/i, '')
    .replace(/-Verbatim$/i, '');
}

/**
 * Split a markdown document into sections by ## headings.
 * Each section includes the heading text and the content beneath it.
 * Returns [{heading, text}].
 */
function splitIntoSections(markdown) {
  const lines = markdown.split('\n');
  const sections = [];
  let currentHeading = '(preamble)';
  let buffer = [];

  for (const line of lines) {
    if (line.startsWith('## ') || line.startsWith('# ')) {
      if (buffer.join('\n').trim().length > 50) {
        sections.push({ heading: currentHeading, text: buffer.join('\n').trim() });
      }
      currentHeading = line.replace(/^#+\s*/, '').trim();
      buffer = [];
    } else {
      buffer.push(line);
    }
  }
  if (buffer.join('\n').trim().length > 50) {
    sections.push({ heading: currentHeading, text: buffer.join('\n').trim() });
  }

  // If no sections found, treat entire doc as one chunk
  if (sections.length === 0 && markdown.trim().length > 50) {
    sections.push({ heading: '(full document)', text: markdown.trim() });
  }

  return sections;
}

/**
 * Further split large sections into ~1200 char chunks with 200 char overlap.
 */
function chunkSection(text, maxChars = 1200, overlap = 200) {
  if (text.length <= maxChars) return [text];
  const chunks = [];
  let start = 0;
  while (start < text.length) {
    const end = Math.min(start + maxChars, text.length);
    // Try to break at a newline near the end
    let breakAt = end;
    if (end < text.length) {
      const nl = text.lastIndexOf('\n', end);
      if (nl > start + maxChars * 0.5) breakAt = nl;
    }
    chunks.push(text.slice(start, breakAt).trim());
    start = breakAt - overlap;
    if (start >= text.length - overlap) break;
  }
  return chunks.filter(c => c.trim().length > 40);
}

// ── Main ──────────────────────────────────────────────────────────────────────

const allFiles = [
  ...walkMd(ABALONECOVE_DOCS),
  ...walkMd(COVE_TRANSCRIPTIONS),
];

const seen = new Set();
const chunks = [];
let docCount = 0;
let chunkCount = 0;

for (const filePath of allFiles) {
  const label = cleanLabel(filePath);

  // Skip excluded
  if (EXCLUDE_PATTERNS.some(p => p.test(label))) {
    console.log(`  SKIP  ${label}`);
    continue;
  }

  // Deduplicate by label (abalonecove/docs and Cove/transcriptions often have the same doc)
  if (seen.has(label.toLowerCase())) {
    console.log(`  DUP   ${label}`);
    continue;
  }
  seen.add(label.toLowerCase());

  let raw;
  try {
    raw = readFileSync(filePath, 'utf8');
  } catch {
    console.log(`  ERR   ${label}`);
    continue;
  }

  const body = stripFrontmatter(raw);
  const sections = splitIntoSections(body);

  docCount++;
  for (const { heading, text } of sections) {
    const subchunks = chunkSection(text);
    for (const chunk of subchunks) {
      chunks.push({
        id: `${label}::${heading}::${chunkCount}`,
        source: label,
        heading,
        text: chunk,
      });
      chunkCount++;
    }
  }
  console.log(`  OK    ${label} (${sections.length} sections → ${chunkCount} total chunks so far)`);
}

const output = JSON.stringify(chunks);
const outPath = new URL('knowledge-base.json', import.meta.url).pathname;
writeFileSync(outPath, output);

console.log(`\nDone. ${docCount} documents → ${chunks.length} chunks`);
console.log(`Output: ${outPath} (${(output.length / 1024).toFixed(0)} KB)`);
console.log(`\nUpload with:`);
console.log(`  wrangler kv:key put --binding KB "chunks" --path workers/chat/knowledge-base.json`);
