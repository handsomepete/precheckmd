/**
 * Reconciles YNAB scheduled ("upcoming/due") transactions against recently
 * cleared transactions, so the brief doesn't flag a bill as "due" when it
 * has already cleared under a different (e.g. budget-billing) amount.
 *
 * HOME-447: brief reported "Hydro One bill ($426.00) due in 2 days" when the
 * actual payment had already cleared YNAB the day before at $454 — the
 * scheduled transaction amount was stale and nothing checked cleared txns
 * before flagging it as due.
 */

'use strict';

const { sanitizeText } = require('./sanitize');

const DEFAULT_WINDOW_DAYS = 10;
const DEFAULT_TOLERANCE_RATIO = 0.25;

function normalizePayee(name) {
  return (name || '').trim().toLowerCase();
}

function payeesMatch(a, b) {
  const na = normalizePayee(a);
  const nb = normalizePayee(b);
  if (!na || !nb) return false;
  return na === nb || na.includes(nb) || nb.includes(na);
}

function amountWithinTolerance(scheduledAmount, clearedAmount, toleranceRatio) {
  const scheduled = Math.abs(scheduledAmount);
  const cleared = Math.abs(clearedAmount);
  if (scheduled === 0) return cleared === 0;
  return Math.abs(cleared - scheduled) / scheduled <= toleranceRatio;
}

function daysBetween(fromDate, toDate) {
  const MS_PER_DAY = 24 * 60 * 60 * 1000;
  return Math.round((toDate.getTime() - fromDate.getTime()) / MS_PER_DAY);
}

/**
 * @param {object} scheduled - one item from getYnabScheduledTransactions()
 * @param {object[]} clearedTransactions - recent transactions (any payee/date range)
 * @param {object} opts
 * @param {number} [opts.windowDays=10] - how many days back counts as "recent"
 * @param {number} [opts.toleranceRatio=0.25] - amount drift allowed, e.g. 0.25 = +/-25%
 * @param {Date} [opts.now] - injectable for tests
 */
function reconcileScheduledTransaction(scheduled, clearedTransactions, opts = {}) {
  const windowDays = opts.windowDays ?? DEFAULT_WINDOW_DAYS;
  const toleranceRatio = opts.toleranceRatio ?? DEFAULT_TOLERANCE_RATIO;
  const now = opts.now ?? new Date();
  const cutoff = new Date(now.getTime() - windowDays * 24 * 60 * 60 * 1000);

  const candidates = (clearedTransactions || [])
    .filter((t) => t.cleared === 'cleared' || t.cleared === 'reconciled')
    .filter((t) => payeesMatch(t.payee, scheduled.payee))
    .filter((t) => {
      const d = new Date(t.date);
      return d >= cutoff && d <= now;
    })
    .filter((t) => amountWithinTolerance(scheduled.amount, t.amount, toleranceRatio));

  if (candidates.length === 0) {
    const dueDate = new Date(scheduled.date_next);
    return {
      id: scheduled.id,
      payee: sanitizeText(scheduled.payee),
      status: 'due',
      amount: scheduled.amount,
      due_date: scheduled.date_next,
      days_until_due: daysBetween(now, dueDate),
    };
  }

  // Prefer the closest amount match; tie-break on most recent date.
  candidates.sort((a, b) => {
    const diffA = Math.abs(Math.abs(a.amount) - Math.abs(scheduled.amount));
    const diffB = Math.abs(Math.abs(b.amount) - Math.abs(scheduled.amount));
    if (diffA !== diffB) return diffA - diffB;
    return new Date(b.date) - new Date(a.date);
  });
  const match = candidates[0];

  return {
    id: scheduled.id,
    payee: sanitizeText(scheduled.payee),
    status: 'paid',
    scheduled_amount: scheduled.amount,
    actual_amount: match.amount,
    cleared_date: match.date,
    matched_transaction_id: match.id,
    due_date: scheduled.date_next,
  };
}

function reconcileScheduledTransactions(scheduledList, clearedTransactions, opts = {}) {
  return (scheduledList || []).map((s) => reconcileScheduledTransaction(s, clearedTransactions, opts));
}

module.exports = {
  reconcileScheduledTransaction,
  reconcileScheduledTransactions,
  payeesMatch,
  amountWithinTolerance,
};
