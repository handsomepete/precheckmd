/**
 * Hygiene for external free-text fields (YNAB payee names, Linear issue
 * titles) before they flow into brief output. See SECURITY.md for the
 * threat model and why this stops at hygiene rather than trying to detect
 * prompt injection.
 */

'use strict';

const DEFAULT_MAX_LENGTH = 300;

/**
 * Strips control characters (including newlines/tabs/NUL), collapses
 * whitespace, and bounds length. This defends against:
 *  - newline-based section smuggling (a payee/title with embedded "\n##
 *    FAKE SECTION" can't inject line breaks into a single-line field)
 *  - NUL-byte / control-character tricks
 *  - unbounded length (log flooding, layout breakage)
 *
 * It deliberately does NOT escape markdown-active characters like `[`, `]`,
 * `` ` ``, since legitimate payee/issue names can contain them (e.g.
 * "Bob's [Autobody]") and this repo doesn't control how the downstream
 * renderer (an LLM prompt in cowork.main, not present here) treats them.
 */
function sanitizeText(input, { maxLength = DEFAULT_MAX_LENGTH } = {}) {
  if (input == null) return input;
  const str = String(input);
  // eslint-disable-next-line no-control-regex
  const stripped = str.replace(/[\x00-\x1f\x7f]+/g, ' ').replace(/\s+/g, ' ').trim();
  return stripped.length > maxLength ? `${stripped.slice(0, maxLength)}…` : stripped;
}

module.exports = { sanitizeText, DEFAULT_MAX_LENGTH };
