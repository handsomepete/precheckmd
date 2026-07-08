/**
 * Dual-writes the rendered brief body next to the Gmail draft.
 *
 * HOME-447: the brief is only ever written as a Gmail draft, which the
 * Claude chat integration can't read (permission errors on draft threads).
 * This persists the same body to a plain dated file the MCP server can
 * serve over HTTP, e.g. briefs/2026-07-08-morning.md.
 *
 * TODO(HOME-447): the actual brief pipeline (cowork.main) lives outside
 * this repo and isn't reachable from here, so there's no way to have it
 * call save_brief_file directly with an explicit brief type. As a
 * conservative stopgap, create_gmail_draft (the one call every brief run
 * makes) detects "is this a brief" from the subject line below. If
 * cowork.main's source becomes available, replace this heuristic with an
 * explicit call/parameter from that pipeline instead.
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

/**
 * Given the same args passed to create_gmail_draft, decide whether (and
 * where) to also persist a file copy. Returns null when this doesn't look
 * like a brief email.
 */
function maybePersistBriefFromDraft(briefsDir, { subject, body_text, body_html }, now = new Date()) {
  const type = detectBriefType(subject);
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
