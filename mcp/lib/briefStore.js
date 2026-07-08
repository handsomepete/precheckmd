/**
 * Dual-writes the rendered brief body next to the Gmail draft.
 *
 * HOME-447: the brief is only ever written as a Gmail draft, which the
 * Claude chat integration can't read (permission errors on draft threads).
 * This persists the same body to a plain dated file the MCP server can
 * serve over HTTP, e.g. briefs/2026-07-08-morning.md.
 *
 * create_gmail_draft now accepts an optional explicit `brief_type` argument
 * ('morning'|'evening'). When cowork.main passes it, that's used directly
 * and the subject-line heuristic below is skipped entirely. The heuristic
 * remains as a fallback for callers that don't pass brief_type yet (i.e.
 * cowork.main hasn't been updated — see INTEGRATION.md). Once cowork.main
 * passes brief_type on every brief-creating call, detectBriefType() and its
 * subject-matching can be deleted in a follow-up.
 */

'use strict';

const fs = require('fs');
const path = require('path');

// Strict allowlist, not a denylist: the whole string must match exactly
// this shape, so no "../", encoded traversal (e.g. Express decoding a
// %2F-in-segment into a literal "/"), absolute path, or null byte can ever
// satisfy it — there's no character class here that permits anything but
// digits, a literal "-", "morning"/"evening", and ".md". See SECURITY.md.
const FILENAME_PATTERN = /^\d{4}-\d{2}-\d{2}-(morning|evening)\.md$/;

function detectBriefType(subject) {
  if (!subject) return null;
  if (/morning brief/i.test(subject)) return 'morning';
  if (/evening brief/i.test(subject)) return 'evening';
  // Deliberately no generic "brief" fallback: run_brief only ever produces
  // 'morning' or 'evening' (see mcp/server.js runBrief), so a looser match
  // like /\bbrief\b/i just widened the false-positive surface for
  // persisting non-brief emails without enabling any real caller. See
  // SECURITY.md for the full reasoning.
  return null;
}

function briefFilename(type, date) {
  return `${date}-${type}.md`;
}

function todayIsoDate(now = new Date()) {
  return now.toISOString().slice(0, 10);
}

// Belt-and-suspenders on top of the allowlist above: even if FILENAME_PATTERN
// were ever loosened, resolve both paths and require the result to still be
// directly inside briefsDir before touching the filesystem.
function assertInsideBriefsDir(briefsDir, filePath) {
  const resolvedDir = path.resolve(briefsDir) + path.sep;
  const resolvedFile = path.resolve(filePath);
  if (!resolvedFile.startsWith(resolvedDir)) {
    throw new Error('resolved path escapes briefs directory');
  }
}

function saveBriefFile(briefsDir, { type, date, body }) {
  const filename = briefFilename(type, date);
  if (!FILENAME_PATTERN.test(filename)) {
    throw new Error(`invalid brief type/date: ${type} ${date}`);
  }
  const filePath = path.join(briefsDir, filename);
  assertInsideBriefsDir(briefsDir, filePath);
  fs.mkdirSync(briefsDir, { recursive: true });
  fs.writeFileSync(filePath, body, 'utf8');
  return { filename, path: filePath };
}

function readBriefFile(briefsDir, filename) {
  if (!FILENAME_PATTERN.test(filename)) {
    throw new Error('invalid brief filename');
  }
  const filePath = path.join(briefsDir, filename);
  assertInsideBriefsDir(briefsDir, filePath);
  return fs.readFileSync(filePath, 'utf8');
}

function listBriefFiles(briefsDir) {
  if (!fs.existsSync(briefsDir)) return [];
  return fs.readdirSync(briefsDir).filter((f) => FILENAME_PATTERN.test(f)).sort().reverse();
}

const EXPLICIT_TYPES = new Set(['morning', 'evening']);

/**
 * Given the same args passed to create_gmail_draft, decide whether (and
 * where) to also persist a file copy. Returns null when this doesn't look
 * like a brief email.
 *
 * If `brief_type` is passed and is 'morning' or 'evening', it's used
 * directly and the subject-line heuristic is skipped. Otherwise falls back
 * to detectBriefType(subject).
 */
function maybePersistBriefFromDraft(briefsDir, { subject, body_text, body_html, brief_type }, now = new Date()) {
  const type = EXPLICIT_TYPES.has(brief_type) ? brief_type : detectBriefType(subject);
  if (!type) return null;
  const body = body_text || (body_html || '').replace(/<[^>]+>/g, ' ').replace(/\s+/g, ' ').trim();
  return saveBriefFile(briefsDir, { type, date: todayIsoDate(now), body });
}

module.exports = {
  detectBriefType,
  briefFilename,
  saveBriefFile,
  readBriefFile,
  listBriefFiles,
  maybePersistBriefFromDraft,
  FILENAME_PATTERN,
};
