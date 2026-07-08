'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const {
  reconcileScheduledTransaction,
  reconcileScheduledTransactions,
} = require('../lib/reconciliation');

const NOW = new Date('2026-07-08T12:00:00Z');

test('exact match: cleared transaction at the same amount suppresses the due line', () => {
  const scheduled = {
    id: 'sched-1',
    payee: 'Hydro One',
    amount: -426.0,
    date_next: '2026-07-10',
  };
  const cleared = [
    { id: 'txn-1', payee: 'Hydro One', amount: -426.0, date: '2026-07-07', cleared: 'cleared' },
  ];

  const result = reconcileScheduledTransaction(scheduled, cleared, { now: NOW });

  assert.equal(result.status, 'paid');
  assert.equal(result.actual_amount, -426.0);
  assert.equal(result.matched_transaction_id, 'txn-1');
});

test('amount-drift match: cleared amount differs but is within 25% tolerance', () => {
  // HOME-447 case: scheduled $426, actual cleared $454 (~6.6% drift).
  const scheduled = {
    id: 'sched-2',
    payee: 'Hydro One',
    amount: -426.0,
    date_next: '2026-07-10',
  };
  const cleared = [
    { id: 'txn-2', payee: 'Hydro One Networks Inc', amount: -454.0, date: '2026-07-07', cleared: 'cleared' },
  ];

  const result = reconcileScheduledTransaction(scheduled, cleared, { now: NOW });

  assert.equal(result.status, 'paid');
  assert.equal(result.actual_amount, -454.0);
  assert.equal(result.matched_transaction_id, 'txn-2');
});

test('no match: different payee is not reconciled and stays due', () => {
  const scheduled = {
    id: 'sched-3',
    payee: 'Hydro One',
    amount: -426.0,
    date_next: '2026-07-10',
  };
  const cleared = [
    { id: 'txn-3', payee: 'Rogers', amount: -120.0, date: '2026-07-07', cleared: 'cleared' },
  ];

  const result = reconcileScheduledTransaction(scheduled, cleared, { now: NOW });

  assert.equal(result.status, 'due');
  assert.equal(result.amount, -426.0);
  assert.equal(result.due_date, '2026-07-10');
});

test('no match: same payee but amount drift exceeds tolerance stays due', () => {
  const scheduled = {
    id: 'sched-4',
    payee: 'Hydro One',
    amount: -426.0,
    date_next: '2026-07-10',
  };
  const cleared = [
    // 50% higher than scheduled — outside the +/-25% band, e.g. a different bill cycle.
    { id: 'txn-4', payee: 'Hydro One', amount: -639.0, date: '2026-07-07', cleared: 'cleared' },
  ];

  const result = reconcileScheduledTransaction(scheduled, cleared, { now: NOW });

  assert.equal(result.status, 'due');
});

test('no match: matching payee/amount but outside the lookback window stays due', () => {
  const scheduled = {
    id: 'sched-5',
    payee: 'Hydro One',
    amount: -426.0,
    date_next: '2026-07-10',
  };
  const cleared = [
    { id: 'txn-5', payee: 'Hydro One', amount: -426.0, date: '2026-06-01', cleared: 'cleared' },
  ];

  const result = reconcileScheduledTransaction(scheduled, cleared, { now: NOW, windowDays: 10 });

  assert.equal(result.status, 'due');
});

test('uncleared transactions are ignored even if amount/payee match', () => {
  const scheduled = {
    id: 'sched-6',
    payee: 'Hydro One',
    amount: -426.0,
    date_next: '2026-07-10',
  };
  const cleared = [
    { id: 'txn-6', payee: 'Hydro One', amount: -426.0, date: '2026-07-07', cleared: 'uncleared' },
  ];

  const result = reconcileScheduledTransaction(scheduled, cleared, { now: NOW });

  assert.equal(result.status, 'due');
});

test('reconcileScheduledTransactions maps over a full list', () => {
  const scheduled = [
    { id: 'a', payee: 'Hydro One', amount: -426.0, date_next: '2026-07-10' },
    { id: 'b', payee: 'Netflix', amount: -20.0, date_next: '2026-07-12' },
  ];
  const cleared = [
    { id: 'txn-a', payee: 'Hydro One', amount: -454.0, date: '2026-07-07', cleared: 'cleared' },
  ];

  const results = reconcileScheduledTransactions(scheduled, cleared, { now: NOW });

  assert.equal(results.length, 2);
  assert.equal(results[0].status, 'paid');
  assert.equal(results[1].status, 'due');
});
