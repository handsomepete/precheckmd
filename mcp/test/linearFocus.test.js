'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const { resolveLinearFocus, pickHighestPriorityIssue } = require('../lib/linearFocus');

function fakeLogger() {
  const warnings = [];
  return { warnings, warn: (msg) => warnings.push(msg) };
}

test('pinned issue exists: returns it directly, no fallback, no dangling id', async () => {
  const issue = { id: 'HOME-100', title: 'Fix the fence' };
  const logger = fakeLogger();

  const result = await resolveLinearFocus({
    pinnedIssueId: 'HOME-100',
    fetchIssueById: async () => issue,
    getUrgentIssues: async () => { throw new Error('should not be called'); },
    logger,
  });

  assert.equal(result.source, 'pinned');
  assert.equal(result.issue, issue);
  assert.equal(result.dangling_issue_id, null);
  assert.equal(logger.warnings.length, 0);
});

test('pinned issue deleted (fetch returns null): falls back and logs the dangling id', async () => {
  const fallbackIssue = { id: 'HOME-448', title: 'Replace gutter', priority: 2 };
  const logger = fakeLogger();

  const result = await resolveLinearFocus({
    pinnedIssueId: 'HOME-447-stale',
    fetchIssueById: async () => null,
    getUrgentIssues: async () => [fallbackIssue],
    logger,
  });

  assert.equal(result.source, 'fallback');
  assert.equal(result.issue, fallbackIssue);
  assert.equal(result.dangling_issue_id, 'HOME-447-stale');
  assert.equal(logger.warnings.length, 1);
  assert.match(logger.warnings[0], /HOME-447-stale/);
});

test('pinned issue archived (fetch throws "Entity not found"): falls back and logs', async () => {
  const fallbackIssue = { id: 'HOME-449', title: 'Winterize sprinklers', priority: 1 };
  const logger = fakeLogger();

  const result = await resolveLinearFocus({
    pinnedIssueId: 'stale-uuid',
    fetchIssueById: async () => { throw new Error('Entity not found: Issue'); },
    getUrgentIssues: async () => [fallbackIssue],
    logger,
  });

  assert.equal(result.source, 'fallback');
  assert.equal(result.issue, fallbackIssue);
  assert.equal(result.dangling_issue_id, 'stale-uuid');
  assert.equal(logger.warnings.length, 1);
});

test('no pinned issue at all: goes straight to fallback without warning', async () => {
  const fallbackIssue = { id: 'HOME-450', title: 'Rake leaves', priority: 3 };
  const logger = fakeLogger();

  const result = await resolveLinearFocus({
    pinnedIssueId: null,
    fetchIssueById: async () => { throw new Error('should not be called'); },
    getUrgentIssues: async () => [fallbackIssue],
    logger,
  });

  assert.equal(result.source, 'fallback');
  assert.equal(result.issue, fallbackIssue);
  assert.equal(result.dangling_issue_id, null);
  assert.equal(logger.warnings.length, 0);
});

test('pinned issue missing and no urgent issues either: source is none', async () => {
  const logger = fakeLogger();

  const result = await resolveLinearFocus({
    pinnedIssueId: 'gone',
    fetchIssueById: async () => null,
    getUrgentIssues: async () => [],
    logger,
  });

  assert.equal(result.source, 'none');
  assert.equal(result.issue, null);
  assert.equal(result.dangling_issue_id, 'gone');
});

test('pickHighestPriorityIssue prefers lower priority number, then earlier due date', () => {
  const issues = [
    { id: 'a', priority: 3, due: '2026-07-20' },
    { id: 'b', priority: 1, due: '2026-07-15' },
    { id: 'c', priority: 1, due: '2026-07-09' },
    { id: 'd', priority: 0, due: '2026-07-01' },
  ];

  const best = pickHighestPriorityIssue(issues);
  assert.equal(best.id, 'c');
});

test('pickHighestPriorityIssue returns null when nothing has a priority', () => {
  assert.equal(pickHighestPriorityIssue([{ id: 'a', priority: 0 }, { id: 'b', priority: null }]), null);
  assert.equal(pickHighestPriorityIssue([]), null);
});
