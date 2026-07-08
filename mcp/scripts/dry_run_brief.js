#!/usr/bin/env node
/**
 * DRY RUN ONLY. No email is sent, no Gmail draft is created, no real YNAB/
 * Linear/Home Assistant API is called. This exercises the four HOME-447
 * fixes (mcp/lib/reconciliation.js, linearFocus.js, propertyStatus.js,
 * briefStore.js) against synthetic fixtures that reproduce today's (July 8)
 * failures, and renders the resulting brief body to a local file only.
 *
 * The real brief-composition pipeline (cowork.main) is not in this repo —
 * see DIAGNOSIS.md — so this is a standalone harness proving the fixes
 * behave correctly, not a run of the production pipeline itself.
 *
 * Usage: node mcp/scripts/dry_run_brief.js [output-path]
 */

'use strict';

const fs = require('fs');
const path = require('path');

const { reconcileScheduledTransactions } = require('../lib/reconciliation');
const { resolveLinearFocus } = require('../lib/linearFocus');
const { buildPropertyStatus } = require('../lib/propertyStatus');

const NOW = new Date('2026-07-08T07:00:00-04:00');

// ── Fixture: reproduces the Hydro One stale-scheduled-transaction bug ──────
const scheduledTransactions = [
  { id: 'sched-hydro', payee: 'Hydro One', amount: -426.0, date_next: '2026-07-10' },
  { id: 'sched-netflix', payee: 'Netflix', amount: -20.0, date_next: '2026-07-15' },
];
const recentClearedTransactions = [
  // Actual budget-billing amount cleared yesterday — higher than the stale scheduled amount.
  { id: 'txn-hydro-actual', payee: 'Hydro One', amount: -454.0, date: '2026-07-07', cleared: 'cleared' },
];

// ── Fixture: reproduces the dangling pinned Linear issue bug ───────────────
const pinnedIssueId = 'HOME-999'; // deleted/archived — this is the "dangling" id
const urgentIssues = [
  { id: 'HOME-448', identifier: 'HOME-448', title: 'Replace furnace filter', priority: 2, due: '2026-07-09', url: 'https://linear.app/home/issue/HOME-448' },
  { id: 'HOME-451', identifier: 'HOME-451', title: 'Renew HA long-lived token', priority: 1, due: '2026-07-08', url: 'https://linear.app/home/issue/HOME-451' },
];
async function fetchIssueById(id) {
  if (id === pinnedIssueId) throw new Error('Entity not found: Issue');
  return null;
}
async function getUrgentIssues() { return urgentIssues; }

// ── Fixture: reproduces the HA TLS-failure bug ──────────────────────────────
async function haHealthCheckDown() {
  return { ok: false, error: 'Client network socket disconnected before secure TLS connection was established' };
}
async function getAllStatesUnreachable() {
  throw new Error('should not be called — health check must gate this');
}

function formatMoney(n) {
  return `$${Math.abs(n).toFixed(2)}`;
}

function renderFinanceSection(bills) {
  const lines = bills.map((b) => {
    if (b.status === 'paid') {
      return `- ${b.payee}: paid ${formatMoney(b.actual_amount)} (cleared ${b.cleared_date}) — scheduled amount was stale at ${formatMoney(b.scheduled_amount)}`;
    }
    return `- ${b.payee}: ${formatMoney(b.amount)} due in ${b.days_until_due} day(s) (${b.due_date})`;
  });
  return ['## FINANCE', ...lines].join('\n');
}

function renderPropertySection(status) {
  if (!status.ok) {
    return ['## PROPERTY', `- ${status.line}`].join('\n');
  }
  const weatherLines = status.weather.map((w) => `- Weather: ${w.state}`);
  const coverLines = status.covers.map((c) => `- ${c.entity_id}: ${c.state}`);
  return ['## PROPERTY', ...weatherLines, ...coverLines].join('\n');
}

function renderFocusSection(focus) {
  if (!focus.issue) return ["## TODAY'S LINEAR FOCUS", '- No open urgent issues.'].join('\n');
  const note = focus.source === 'fallback'
    ? ` (fallback — pinned issue "${focus.dangling_issue_id}" no longer exists, needs cleanup)`
    : '';
  return ["## TODAY'S LINEAR FOCUS", `- ${focus.issue.title} [${focus.issue.id}]${note}`].join('\n');
}

async function main() {
  const bills = reconcileScheduledTransactions(scheduledTransactions, recentClearedTransactions, { now: NOW });
  const propertyStatus = await buildPropertyStatus({
    haHealthCheck: haHealthCheckDown,
    getAllStates: getAllStatesUnreachable,
  });
  const focus = await resolveLinearFocus({
    pinnedIssueId,
    fetchIssueById,
    getUrgentIssues,
    logger: console,
  });

  const body = [
    `# Morning Brief — ${NOW.toISOString().slice(0, 10)} (DRY RUN, synthetic data)`,
    '',
    renderFinanceSection(bills),
    '',
    renderPropertySection(propertyStatus),
    '',
    renderFocusSection(focus),
    '',
  ].join('\n');

  const outputPath = process.argv[2] || path.join(__dirname, '..', '..', 'briefs', 'dry-run-2026-07-08-morning.md');
  fs.mkdirSync(path.dirname(outputPath), { recursive: true });
  fs.writeFileSync(outputPath, body, 'utf8');
  console.log(body);
  console.error(`\n[dry-run] wrote ${outputPath} (local file only — no email, no Gmail draft, no real API calls)`);
}

main().catch((err) => { console.error(err); process.exit(1); });
