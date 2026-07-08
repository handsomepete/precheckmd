/**
 * HomeOS Personal MCP Server v1.3
 * Unified context layer for Claude — runs on Hetzner
 *
 * v1.1: Added Gmail IMAP, Airtable writes, Linear CRUD, fetch_url
 * v1.2: Added run_brief tool
 * v1.3: Expanded YNAB API — get_transaction, update_transaction,
 *       update_transactions_bulk, delete_transaction, get_month,
 *       get_budget_settings, update_category_budget
 *
 * Start: node server.js (via pm2 restart homeos-mcp)
 * Requires: .env (copy from .env.example)
 */

require('dotenv').config();
const { GoogleAuth } = require('google-auth-library');
const express = require('express');
const axios = require('axios');
const cors = require('cors');
const { ImapFlow } = require('imapflow');
const { spawn } = require('child_process');
const path = require('path');

const { reconcileScheduledTransactions } = require('./lib/reconciliation');
const { resolveLinearFocus } = require('./lib/linearFocus');
const { buildPropertyStatus, checkHaHealth } = require('./lib/propertyStatus');
const { maybePersistBriefFromDraft, listBriefFiles, readBriefFile } = require('./lib/briefStore');
const { sanitizeText } = require('./lib/sanitize');

const app = express();
app.use(express.json({ limit: '2mb' }));
app.use(cors());

const PORT = process.env.PORT || 3848;
const BEARER_TOKEN = process.env.BEARER_TOKEN;
const BRIEFS_DIR = process.env.BRIEFS_DIR || path.join(__dirname, '..', 'briefs');

// ─── Staleness tracking ───────────────────────────────────────────────────────
const sourceStatus = {
  airtable:        { ok: false, last_updated: null, error: null },
  linear:          { ok: false, last_updated: null, error: null },
  google_calendar: { ok: false, last_updated: null, error: null },
  ynab:            { ok: false, last_updated: null, error: null },
  gmail:           { ok: false, last_updated: null, error: null },
  home_assistant:  { ok: false, last_updated: null, error: null },
};

function markOk(source) {
  sourceStatus[source].ok = true;
  sourceStatus[source].last_updated = new Date().toISOString();
  sourceStatus[source].error = null;
}
function markFailed(source, err) {
  sourceStatus[source].ok = false;
  sourceStatus[source].error = err;
}
function isStale(source, thresholdSeconds = 86400) {
  const s = sourceStatus[source];
  if (!s.last_updated) return true;
  return (Date.now() - new Date(s.last_updated).getTime()) > thresholdSeconds * 1000;
}

// Maps tool name -> sourceStatus key, so a thrown error can be recorded via
// markFailed(). Previously markFailed() was defined but never called, so a
// source that failed every time (e.g. home_assistant during a TLS outage)
// stayed at its initial { ok: false, last_updated: null } forever instead of
// showing a real error/timestamp in get_health.
const TOOL_SOURCE = {
  get_pipeline: 'airtable', get_nox_leads: 'airtable', list_all_jobs: 'airtable',
  create_job: 'airtable', update_job: 'airtable',
  get_linear_urgent: 'linear', get_homestead: 'linear', close_linear_issue: 'linear',
  list_linear_issues: 'linear', create_linear_issue: 'linear', update_linear_issue: 'linear',
  add_linear_comment: 'linear', get_linear_focus: 'linear',
  get_calendar: 'google_calendar',
  get_finances: 'ynab', get_accounts: 'ynab', get_transactions: 'ynab',
  get_account_transactions: 'ynab', get_transaction: 'ynab', get_categories: 'ynab',
  get_payees: 'ynab', get_scheduled_transactions: 'ynab', get_months: 'ynab',
  get_month: 'ynab', get_budget_settings: 'ynab', create_transaction: 'ynab',
  update_transaction: 'ynab', update_transactions_bulk: 'ynab', delete_transaction: 'ynab',
  create_scheduled_transaction: 'ynab', update_category_budget: 'ynab', get_upcoming_bills: 'ynab',
  search_gmail: 'gmail', get_email: 'gmail', create_gmail_draft: 'gmail',
  get_home_state: 'home_assistant', control_home: 'home_assistant',
  get_home_automations: 'home_assistant', get_property_status: 'home_assistant',
};

// ─── Auth middleware ──────────────────────────────────────────────────────────
function auth(req, res, next) {
  if (!BEARER_TOKEN) return next();
  const header = req.headers['authorization'];
  if (!header || header !== `Bearer ${BEARER_TOKEN}`) {
    return res.status(401).json({ error: 'Unauthorized' });
  }
  next();
}

// ─── Airtable helpers ─────────────────────────────────────────────────────────
const AT_BASE = 'https://api.airtable.com/v0';
const atHeaders = () => ({ Authorization: `Bearer ${process.env.AIRTABLE_API_KEY}` });

async function fetchAirtableRecords(baseId, tableId, params = {}) {
  const url = `${AT_BASE}/${baseId}/${tableId}`;
  const records = [];
  let offset = null;
  do {
    const res = await axios.get(url, {
      headers: atHeaders(),
      params: { ...(offset ? { offset } : {}), ...params },
    });
    records.push(...res.data.records);
    offset = res.data.offset;
  } while (offset);
  return records;
}

async function airtableCreate(baseId, tableId, fields) {
  const res = await axios.post(
    `${AT_BASE}/${baseId}/${tableId}`,
    { fields },
    { headers: atHeaders() }
  );
  return res.data;
}

async function airtableUpdate(baseId, tableId, recordId, fields) {
  const res = await axios.patch(
    `${AT_BASE}/${baseId}/${tableId}/${recordId}`,
    { fields },
    { headers: atHeaders() }
  );
  return res.data;
}

// ─── Linear helpers ───────────────────────────────────────────────────────────
const LINEAR_API = 'https://api.linear.app/graphql';
const linearHeaders = () => ({
  Authorization: process.env.LINEAR_API_KEY,
  'Content-Type': 'application/json',
});

async function linearQuery(query, variables = {}) {
  const res = await axios.post(LINEAR_API, { query, variables }, { headers: linearHeaders() });
  if (res.data.errors) throw new Error(res.data.errors[0].message);
  return res.data.data;
}

async function resolveLinearId(identifierOrId) {
  if (/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(identifierOrId)) {
    return identifierOrId;
  }
  const data = await linearQuery(
    `query($id: String!) { issue(id: $id) { id identifier } }`,
    { id: identifierOrId }
  );
  if (!data.issue) throw new Error(`Linear issue not found: ${identifierOrId}`);
  return data.issue.id;
}

async function getLinearIssueById(identifierOrId) {
  const data = await linearQuery(
    `query($id: String!) { issue(id: $id) { id identifier title priority dueDate project { name } url } }`,
    { id: identifierOrId }
  );
  return data.issue || null;
}

async function getLinearFocus(pinnedIssueId) {
  const result = await resolveLinearFocus({
    pinnedIssueId,
    fetchIssueById: getLinearIssueById,
    getUrgentIssues: getLinearUrgentIssues,
    logger: console,
  });
  return {
    issue: result.issue
      ? { id: result.issue.identifier || result.issue.id, title: sanitizeText(result.issue.title), priority: result.issue.priority, due: result.issue.dueDate, url: result.issue.url }
      : null,
    source: result.source,
    dangling_issue_id: result.dangling_issue_id,
  };
}

async function getLinearWorkflowStates() {
  const data = await linearQuery(`
    query($teamId: ID!) {
      workflowStates(filter: { team: { id: { eq: $teamId } } }) {
        nodes { id name type }
      }
    }
  `, { teamId: process.env.LINEAR_TEAM_ID });
  return data.workflowStates.nodes;
}

async function getLinearUrgentIssues() {
  const data = await linearQuery(`
    query($teamId: ID!) {
      issues(filter: {
        team: { id: { eq: $teamId } }
        priority: { in: [1, 2] }
        state: { type: { nin: ["completed", "cancelled"] } }
      }, orderBy: updatedAt) {
        nodes { id identifier title priority dueDate project { name } url }
      }
    }
  `, { teamId: process.env.LINEAR_TEAM_ID });
  return data.issues.nodes;
}

async function getLinearProjectIssues(projectId) {
  const data = await linearQuery(`
    query($projectId: String!) {
      issues(filter: {
        project: { id: { eq: $projectId } }
        state: { type: { nin: ["completed", "cancelled"] } }
      }, orderBy: updatedAt) {
        nodes { id identifier title priority dueDate state { name type } url description }
      }
    }
  `, { projectId });
  return data.issues.nodes;
}

async function closeLinearIssue(issueId, comment) {
  const states = await getLinearWorkflowStates();
  const doneState = states.find(s => s.name === 'Done' && s.type === 'completed')
    || states.find(s => s.type === 'completed');
  if (!doneState) throw new Error('Could not find Done state');

  const id = await resolveLinearId(issueId);
  await linearQuery(
    `mutation($id: String!, $stateId: String!) { issueUpdate(id: $id, input: { stateId: $stateId }) { success } }`,
    { id, stateId: doneState.id }
  );

  if (comment) {
    await linearQuery(
      `mutation($issueId: String!, $body: String!) { commentCreate(input: { issueId: $issueId, body: $body }) { success } }`,
      { issueId: id, body: comment }
    );
  }
}

// ─── Google Calendar helpers ──────────────────────────────────────────────────
async function getCalendarAuth() {
  const keyPath = process.env.GOOGLE_SERVICE_ACCOUNT_PATH;
  if (!keyPath) throw new Error('GOOGLE_SERVICE_ACCOUNT_PATH not set in .env');
  const credentials = JSON.parse(require('fs').readFileSync(keyPath, 'utf8'));
  const auth = new GoogleAuth({
    credentials,
    scopes: ['https://www.googleapis.com/auth/calendar.readonly'],
  });
  return auth.getClient();
}

async function getCalendarEvents(calendarId, daysAhead = 30) {
  const client = await getCalendarAuth();
  const token = await client.getAccessToken();
  const now = new Date();
  const end = new Date(now);
  end.setDate(end.getDate() + daysAhead);
  const res = await axios.get(
    `https://www.googleapis.com/calendar/v3/calendars/${encodeURIComponent(calendarId)}/events`,
    {
      headers: { Authorization: `Bearer ${token.token}` },
      params: {
        timeMin: now.toISOString(),
        timeMax: end.toISOString(),
        singleEvents: true,
        orderBy: 'startTime',
      },
    }
  );
  return res.data.items || [];
}

// ─── YNAB helpers ─────────────────────────────────────────────────────────────
async function getYnabSnapshot() {
  const res = await axios.get(
    `https://api.ynab.com/v1/budgets/${process.env.YNAB_BUDGET_ID}/months/current`,
    { headers: { Authorization: `Bearer ${process.env.YNAB_API_KEY}` } }
  );
  const month = res.data.data.month;
  return {
    income: month.income / 1000,
    budgeted: month.budgeted / 1000,
    activity: month.activity / 1000,
    to_be_budgeted: month.to_be_budgeted / 1000,
    categories: month.categories || [],
  };
}

const YNAB_BASE = 'https://api.ynab.com/v1';
const ynabHeaders = () => ({ Authorization: `Bearer ${process.env.YNAB_API_KEY}` });
const ynabBudgetId = () => process.env.YNAB_BUDGET_ID;
const milliunits = (v) => (v || 0) / 1000;

async function ynabGet(path, params = {}) {
  const res = await axios.get(`${YNAB_BASE}/budgets/${ynabBudgetId()}${path}`, {
    headers: ynabHeaders(),
    params,
  });
  return res.data.data;
}

async function ynabPost(path, body) {
  try {
    const res = await axios.post(`${YNAB_BASE}/budgets/${ynabBudgetId()}${path}`, body, {
      headers: ynabHeaders(),
    });
    return res.data.data;
  } catch (err) {
    const status = err.response ? err.response.status : null;
    const detail = err.response ? JSON.stringify(err.response.data) : err.message;
    throw new Error('YNAB POST ' + path + ' failed (' + status + '): ' + detail);
  }
}

async function ynabPatch(path, body) {
  try {
    const res = await axios.patch(`${YNAB_BASE}/budgets/${ynabBudgetId()}${path}`, body, {
      headers: ynabHeaders(),
    });
    return res.data.data;
  } catch (err) {
    const status = err.response ? err.response.status : null;
    const detail = err.response ? JSON.stringify(err.response.data) : err.message;
    throw new Error('YNAB PATCH ' + path + ' failed (' + status + '): ' + detail);
  }
}

async function ynabDelete(path) {
  try {
    const res = await axios.delete(`${YNAB_BASE}/budgets/${ynabBudgetId()}${path}`, {
      headers: ynabHeaders(),
    });
    return res.data.data;
  } catch (err) {
    const status = err.response ? err.response.status : null;
    const detail = err.response ? JSON.stringify(err.response.data) : err.message;
    throw new Error('YNAB DELETE ' + path + ' failed (' + status + '): ' + detail);
  }
}

async function getYnabAccounts() {
  const data = await ynabGet('/accounts');
  return data.accounts.filter(a => !a.deleted && !a.closed).map(a => ({
    id: a.id, name: a.name, type: a.type,
    balance: milliunits(a.balance), cleared_balance: milliunits(a.cleared_balance),
    uncleared_balance: milliunits(a.uncleared_balance), on_budget: a.on_budget,
  }));
}

async function getYnabTransaction(transactionId) {
  const data = await ynabGet(`/transactions/${transactionId}`);
  const t = data.transaction;
  return {
    id: t.id, date: t.date, payee: t.payee_name, amount: milliunits(t.amount),
    account: t.account_name, account_id: t.account_id,
    category: t.category_name, category_id: t.category_id,
    memo: t.memo, cleared: t.cleared, approved: t.approved,
  };
}

async function getYnabTransactions(filters = {}) {
  const params = {};
  if (filters.since) params.since_date = filters.since;
  if (filters.before) params.before_date = filters.before;
  if (filters.category_id) params.category_id = filters.category_id;
  const data = await ynabGet('/transactions', params);
  let txns = data.transactions.filter(t => !t.deleted);
  if (filters.account_id) txns = txns.filter(t => t.account_id === filters.account_id);
  if (filters.payee_name) {
    const q = filters.payee_name.toLowerCase();
    txns = txns.filter(t => t.payee_name?.toLowerCase().includes(q));
  }
  return txns.map(t => ({
    id: t.id, date: t.date, payee: t.payee_name, amount: milliunits(t.amount),
    account: t.account_name, account_id: t.account_id,
    category: t.category_name, category_id: t.category_id, memo: t.memo, cleared: t.cleared,
  }));
}

async function getYnabAccountTransactions(accountId, filters = {}) {
  const params = {};
  if (filters.since) params.since_date = filters.since;
  if (filters.before) params.before_date = filters.before;
  const data = await ynabGet(`/accounts/${accountId}/transactions`, params);
  return data.transactions.filter(t => !t.deleted).map(t => ({
    id: t.id, date: t.date, payee: t.payee_name, amount: milliunits(t.amount),
    category: t.category_name, category_id: t.category_id, memo: t.memo, cleared: t.cleared,
  }));
}

async function updateYnabTransaction(transactionId, fields) {
  const txn = {};
  if (fields.cleared    !== undefined) txn.cleared     = fields.cleared;
  if (fields.amount     !== undefined) txn.amount      = Math.round(fields.amount * 1000);
  if (fields.date       !== undefined) txn.date        = fields.date;
  if (fields.payee_name !== undefined) txn.payee_name  = fields.payee_name;
  if (fields.memo       !== undefined) txn.memo        = fields.memo;
  if (fields.category_id !== undefined) txn.category_id = fields.category_id;
  const data = await ynabPatch(`/transactions/${transactionId}`, { transaction: txn });
  const t = data.transaction;
  return {
    id: t.id, date: t.date, payee: t.payee_name, amount: milliunits(t.amount),
    account: t.account_name, account_id: t.account_id,
    category: t.category_name, category_id: t.category_id, memo: t.memo, cleared: t.cleared,
  };
}

async function updateYnabTransactionsBulk(transactions) {
  const formatted = transactions.map(t => {
    const item = { id: t.id };
    if (t.cleared     !== undefined) item.cleared     = t.cleared;
    if (t.amount      !== undefined) item.amount      = Math.round(t.amount * 1000);
    if (t.date        !== undefined) item.date        = t.date;
    if (t.payee_name  !== undefined) item.payee_name  = t.payee_name;
    if (t.memo        !== undefined) item.memo        = t.memo;
    if (t.category_id !== undefined) item.category_id = t.category_id;
    return item;
  });
  const data = await ynabPatch('/transactions', { transactions: formatted });
  const bulk = data.bulk;
  return {
    transaction_ids_updated: bulk.transaction_ids || [],
    transaction_ids_added: bulk.transaction_ids_added || [],
  };
}

async function deleteYnabTransaction(transactionId) {
  const data = await ynabDelete(`/transactions/${transactionId}`);
  return { deleted: true, id: data.transaction.id };
}

async function getYnabCategories(month) {
  const monthStr = month ? `${month}-01` : 'current';
  const data = await ynabGet(`/months/${monthStr}`);
  return data.month.categories.filter(c => !c.deleted && !c.hidden).map(c => ({
    id: c.id, name: c.name, category_group: c.category_group_name,
    budgeted: milliunits(c.budgeted), activity: milliunits(c.activity),
    balance: milliunits(c.balance), goal_type: c.goal_type || null,
    goal_percentage_complete: c.goal_percentage_complete || null,
  }));
}

async function updateYnabCategoryBudget(month, categoryId, budgeted) {
  const monthStr = month.length === 7 ? `${month}-01` : month;
  const data = await ynabPatch(`/months/${monthStr}/categories/${categoryId}`, {
    category: { budgeted: Math.round(budgeted * 1000) },
  });
  const c = data.category;
  return {
    id: c.id, name: c.name,
    budgeted: milliunits(c.budgeted), activity: milliunits(c.activity), balance: milliunits(c.balance),
  };
}

async function getYnabPayees() {
  const data = await ynabGet('/payees');
  return data.payees.filter(p => !p.deleted).map(p => ({ id: p.id, name: p.name }));
}

async function getYnabScheduledTransactions() {
  const data = await ynabGet('/scheduled_transactions');
  return data.scheduled_transactions.filter(t => !t.deleted).map(t => ({
    id: t.id, date_first: t.date_first, date_next: t.date_next, frequency: t.frequency,
    payee: t.payee_name, amount: milliunits(t.amount),
    account: t.account_name, category: t.category_name, memo: t.memo,
  }));
}

async function getYnabUpcomingBills() {
  const scheduled = await getYnabScheduledTransactions();
  const since = new Date();
  since.setDate(since.getDate() - 10);
  const recentCleared = await getYnabTransactions({ since: since.toISOString().slice(0, 10) });
  return reconcileScheduledTransactions(scheduled, recentCleared, { windowDays: 10, toleranceRatio: 0.25 });
}

async function createYnabTransaction({ account_id, date, payee_name, amount, category_id, memo }) {
  const body = {
    transaction: {
      account_id, date, payee_name,
      amount: Math.round(amount * 1000),
      cleared: 'uncleared',
      ...(category_id ? { category_id } : {}),
      ...(memo ? { memo } : {}),
    },
  };
  const data = await ynabPost('/transactions', body);
  markOk('ynab');
  return {
    id: data.transaction.id, date: data.transaction.date,
    payee: data.transaction.payee_name, amount: milliunits(data.transaction.amount),
    account_id: data.transaction.account_id, category_id: data.transaction.category_id || null,
    memo: data.transaction.memo || null, cleared: data.transaction.cleared,
  };
}

const YNAB_FREQUENCY_CANONICAL = [
  'never','daily','weekly','everyOtherWeek','twiceAMonth','every4Weeks',
  'monthly','everyOtherMonth','every3Months','every4Months','twiceAYear','yearly',
];
const _freqLookup = Object.fromEntries(
  YNAB_FREQUENCY_CANONICAL.map(v => [v.toLowerCase().replace(/[^a-z0-9]/g,''), v])
);
function normalizeFrequency(f) {
  if (!f) return f;
  const key = f.toLowerCase().replace(/[^a-z0-9]/g, '');
  return _freqLookup[key] || f;
}

async function createYnabScheduledTransaction({ account_id, date, frequency, amount, payee_name, category_id, memo }) {
  const normalizedFrequency = normalizeFrequency(frequency);
  const body = {
    scheduled_transaction: {
      account_id, date, frequency: normalizedFrequency,
      amount: Math.round(amount * 1000),
      ...(payee_name  ? { payee_name }  : {}),
      ...(category_id ? { category_id } : {}),
      ...(memo        ? { memo }        : {}),
    },
  };
  const data = await ynabPost('/scheduled_transactions', body);
  markOk('ynab');
  const st = data.scheduled_transaction;
  return {
    id: st.id, date_first: st.date_first, date_next: st.date_next,
    frequency: st.frequency, payee: st.payee_name, amount: milliunits(st.amount),
    account_id: st.account_id, category_id: st.category_id || null, memo: st.memo || null,
  };
}

async function getYnabMonths() {
  const data = await ynabGet('/months');
  return data.months.map(m => ({
    month: m.month, income: milliunits(m.income), budgeted: milliunits(m.budgeted),
    activity: milliunits(m.activity), to_be_budgeted: milliunits(m.to_be_budgeted),
    age_of_money: m.age_of_money,
  }));
}

async function getYnabMonth(month) {
  const monthStr = month.length === 7 ? `${month}-01` : month;
  const data = await ynabGet(`/months/${monthStr}`);
  const m = data.month;
  return {
    month: m.month, income: milliunits(m.income), budgeted: milliunits(m.budgeted),
    activity: milliunits(m.activity), to_be_budgeted: milliunits(m.to_be_budgeted),
    note: m.note || null,
    categories: (m.categories || []).filter(c => !c.deleted && !c.hidden).map(c => ({
      id: c.id, name: c.name,
      budgeted: milliunits(c.budgeted), activity: milliunits(c.activity), balance: milliunits(c.balance),
    })),
  };
}

async function getYnabBudgetSettings() {
  const res = await axios.get(`${YNAB_BASE}/budgets/${ynabBudgetId()}/settings`, { headers: ynabHeaders() });
  const s = res.data.data.settings;
  return {
    date_format: s.date_format?.format || null,
    currency_format: {
      iso_code: s.currency_format?.iso_code || null,
      symbol: s.currency_format?.symbol || null,
      decimal_digits: s.currency_format?.decimal_digits || null,
    },
  };
}

// ─── Gmail IMAP helpers ───────────────────────────────────────────────────────
const GMAIL_USER = process.env.GMAIL_USER;
const GMAIL_PASS = process.env.GMAIL_APP_PASSWORD;

function makeImapClient() {
  return new ImapFlow({
    host: 'imap.gmail.com', port: 993, secure: true,
    auth: { user: GMAIL_USER, pass: GMAIL_PASS },
    logger: false, tls: { rejectUnauthorized: true },
  });
}

async function searchGmail({ since_iso, senders = [], max_results = 100 }) {
  if (!GMAIL_USER || !GMAIL_PASS) throw new Error('GMAIL_USER or GMAIL_APP_PASSWORD not set');
  const client = makeImapClient();
  await client.connect();
  const lock = await client.getMailboxLock('INBOX');
  try {
    const since = since_iso ? new Date(since_iso) : new Date(Date.now() - 24 * 3600 * 1000);
    const uids = await client.search({ since }, { uid: true });
    const fetchUids = uids.slice(-max_results);
    const results = [];
    for await (const msg of client.fetch(fetchUids, { envelope: true }, { uid: true })) {
      const from = msg.envelope.from?.[0];
      const fromAddr = from ? `${from.mailbox}@${from.host}`.toLowerCase() : '';
      const fromName = from?.name || '';
      if (senders.length > 0 && !senders.some(s => fromAddr.includes(s.toLowerCase()) || fromName.toLowerCase().includes(s.toLowerCase()))) continue;
      results.push({ uid: msg.uid, from: fromAddr, from_name: fromName, subject: msg.envelope.subject || '(no subject)', date: msg.envelope.date?.toISOString() || null });
    }
    markOk('gmail');
    return results;
  } finally { lock.release(); await client.logout(); }
}

async function getEmail({ uid }) {
  if (!GMAIL_USER || !GMAIL_PASS) throw new Error('GMAIL_USER or GMAIL_APP_PASSWORD not set');
  const client = makeImapClient();
  await client.connect();
  const lock = await client.getMailboxLock('INBOX');
  try {
    let bodyText = '', bodyHtml = '';
    for await (const msg of client.fetch([uid], { source: true }, { uid: true })) {
      const raw = msg.source.toString('utf8');
      const sepIdx = raw.search(/\r?\n\r?\n/);
      const headers = sepIdx >= 0 ? raw.slice(0, sepIdx) : '';
      const body = sepIdx >= 0 ? raw.slice(sepIdx + 2) : raw;
      const isHtml = /content-type:\s*text\/html/i.test(headers);
      const isBase64 = /content-transfer-encoding:\s*base64/i.test(headers);
      let decoded = body;
      if (isBase64) { try { decoded = Buffer.from(body.replace(/\s+/g, ''), 'base64').toString('utf8'); } catch (_) { decoded = body; } }
      if (isHtml || decoded.includes('<html') || decoded.includes('<body')) {
        bodyHtml = decoded.slice(0, 8000);
        bodyText = decoded.replace(/<[^>]+>/g, ' ').replace(/&[a-z]+;/gi, ' ').replace(/\s+/g, ' ').trim().slice(0, 4000);
      } else { bodyText = decoded.slice(0, 4000); }
    }
    markOk('gmail');
    return { uid, body_text: bodyText, body_html: bodyHtml };
  } finally { lock.release(); await client.logout(); }
}

async function createGmailDraft({ to, subject, body_html, body_text }) {
  if (!GMAIL_USER || !GMAIL_PASS) throw new Error('GMAIL_USER or GMAIL_APP_PASSWORD not set');
  const client = makeImapClient();
  await client.connect();
  try {
    const html = body_html || `<pre>${body_text || ''}</pre>`;
    const message = [`MIME-Version: 1.0`, `Date: ${new Date().toUTCString()}`, `From: ${GMAIL_USER}`, `To: ${to}`, `Subject: ${subject}`, `Content-Type: text/html; charset=UTF-8`, ``, html].join('\r\n');
    await client.append('[Google Mail]/Drafts', Buffer.from(message, 'utf8'), ['\\Draft', '\\Seen']);
    markOk('gmail');
    let briefFile = null;
    try {
      const persisted = maybePersistBriefFromDraft(BRIEFS_DIR, { subject, body_text, body_html });
      if (persisted) briefFile = persisted.filename;
    } catch (fileErr) {
      // Never let the file-persistence stopgap break the Gmail draft itself.
      console.error(`[briefStore] failed to persist brief file: ${fileErr.message}`);
    }
    return { ok: true, to, subject, ...(briefFile ? { brief_file: briefFile } : {}) };
  } finally { await client.logout(); }
}

// ─── Airtable write helpers ───────────────────────────────────────────────────
const JOB_BASE  = () => process.env.AIRTABLE_JOB_BASE_ID;
const JOB_TABLE = () => process.env.AIRTABLE_JOB_TABLE_ID;
const F_NAME   = 'fldYThNUtVY4dfxtd';
const F_STATUS = 'fldZLqA4c7eQkv6Op';
const F_NOTES  = 'fldhQBFx4zTVcbiiP';

async function getAllJobs() {
  const records = await fetchAirtableRecords(JOB_BASE(), JOB_TABLE(), {});
  const now = Date.now();
  return records.map(r => ({
    id: r.id, company: r.fields[F_NAME] || r.fields.Name || '',
    status: r.fields[F_STATUS] || r.fields.Status || '',
    notes: r.fields[F_NOTES] || r.fields.Notes || '',
    created: r.createdTime,
    days_since_created: Math.floor((now - new Date(r.createdTime).getTime()) / 86400000),
  }));
}

async function createJob({ company, status, notes }) {
  const fields = {};
  if (company) fields[F_NAME]   = company;
  if (status)  fields[F_STATUS] = status;
  if (notes)   fields[F_NOTES]  = `[${new Date().toISOString().split('T')[0]}] ${notes}`;
  const record = await airtableCreate(JOB_BASE(), JOB_TABLE(), fields);
  markOk('airtable');
  return { id: record.id, company, status, notes };
}

async function updateJob({ record_id, status, notes_append }) {
  const fields = {};
  if (status) fields[F_STATUS] = status;
  if (notes_append) {
    const existing = await fetchAirtableRecords(JOB_BASE(), JOB_TABLE(), { filterByFormula: `RECORD_ID()='${record_id}'` });
    const currentNotes = existing[0]?.fields[F_NOTES] || '';
    fields[F_NOTES] = `${currentNotes}\n[${new Date().toISOString().split('T')[0]}] ${notes_append}`.trim();
  }
  if (Object.keys(fields).length === 0) throw new Error('No fields to update');
  const record = await airtableUpdate(JOB_BASE(), JOB_TABLE(), record_id, fields);
  markOk('airtable');
  return { id: record_id, ...fields };
}

// ─── Linear write helpers ─────────────────────────────────────────────────────
async function listLinearIssues({ project_id, priority, state_types, parent_id, limit = 50 }) {
  const filters = [];
  if (project_id) filters.push(`project: { id: { eq: "${project_id}" } }`);
  if (priority !== undefined) { const ps = Array.isArray(priority) ? priority : [priority]; filters.push(`priority: { in: [${ps.join(',')}] }`); }
  if (state_types) { const sts = Array.isArray(state_types) ? state_types : [state_types]; filters.push(`state: { type: { in: [${sts.map(s => `"${s}"`).join(',')}] } }`); }
  if (parent_id) { const uuid = await resolveLinearId(parent_id); filters.push(`parent: { id: { eq: "${uuid}" } }`); }
  const filterStr = filters.length > 0 ? `(filter: { ${filters.join(', ')} }, first: ${limit}, orderBy: updatedAt)` : `(first: ${limit}, orderBy: updatedAt)`;
  const data = await linearQuery(`query { issues${filterStr} { nodes { id identifier title description priority dueDate state { name type } project { name id } parent { id identifier title } url createdAt updatedAt } } }`);
  markOk('linear');
  return data.issues.nodes;
}

async function createLinearIssue({ title, description, project_id, priority, parent_id }) {
  const input = { title, teamId: process.env.LINEAR_TEAM_ID, ...(description ? { description } : {}), ...(project_id ? { projectId: project_id } : {}), ...(priority !== undefined ? { priority } : {}) };
  if (parent_id) input.parentId = await resolveLinearId(parent_id);
  const data = await linearQuery(`mutation($input: IssueCreateInput!) { issueCreate(input: $input) { success issue { id identifier title url } } }`, { input });
  markOk('linear');
  return data.issueCreate.issue;
}

async function updateLinearIssue({ issue_id, state_type, comment }) {
  const states = await getLinearWorkflowStates();
  const state = states.find(s => s.type === state_type) || states.find(s => s.name.toLowerCase() === state_type?.toLowerCase());
  if (!state) throw new Error(`State type not found: ${state_type}`);
  const id = await resolveLinearId(issue_id);
  await linearQuery(`mutation($id: String!, $stateId: String!) { issueUpdate(id: $id, input: { stateId: $stateId }) { success } }`, { id, stateId: state.id });
  if (comment) await linearQuery(`mutation($issueId: String!, $body: String!) { commentCreate(input: { issueId: $issueId, body: $body }) { success } }`, { issueId: id, body: comment });
  markOk('linear');
  return { ok: true, issue_id, state_type };
}

async function addLinearComment({ issue_id, body }) {
  const id = await resolveLinearId(issue_id);
  await linearQuery(`mutation($issueId: String!, $body: String!) { commentCreate(input: { issueId: $issueId, body: $body }) { success } }`, { issueId: id, body });
  markOk('linear');
  return { ok: true };
}

// ─── Utility helpers ──────────────────────────────────────────────────────────
async function runBrief({ type = 'morning' }) {
  if (!['morning', 'evening', 'weekly'].includes(type)) throw new Error('type must be morning, evening, or weekly');
  const flag = '--' + (type === 'weekly' ? 'morning' : type);
  const proc = spawn('/var/www/cowork/venv/bin/python', ['-m', 'cowork.main', flag], { cwd: '/var/www/cowork', env: { ...process.env, PYTHONPATH: '/var/www' }, detached: true, stdio: 'ignore' });
  proc.unref();
  return { ok: true, type, message: type + ' brief started — draft appears in Gmail within 10-30 min' };
}

async function fetchUrl({ url }) {
  const isLinkedIn = /linkedin\.com/i.test(url);
  let fetchUrl = url;
  if (isLinkedIn) {
    const jobIdMatch = url.match(/\/jobs\/view\/(\d+)/);
    if (jobIdMatch) fetchUrl = `https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/${jobIdMatch[1]}`;
  }
  const headers = { 'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36', 'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8', 'Accept-Language': 'en-US,en;q=0.9' };
  if (isLinkedIn && process.env.LINKEDIN_LI_AT && fetchUrl === url) headers['Cookie'] = 'li_at=' + process.env.LINKEDIN_LI_AT;
  const res = await axios.get(fetchUrl, { timeout: 15000, maxRedirects: 5, headers });
  const raw = typeof res.data === 'string' ? res.data : JSON.stringify(res.data);
  const isGuestApi = fetchUrl.includes('/jobs-guest/');
  if (isLinkedIn && !isGuestApi && /LinkedIn Login|Sign in \| LinkedIn|authwall|uas\/login/i.test(raw)) throw new Error('LinkedIn auth wall — set LINKEDIN_LI_AT in /root/mcp/.env to fix.');
  const text = raw.replace(/<script[^>]*>[\s\S]*?<\/script>/gi, '').replace(/<style[^>]*>[\s\S]*?<\/style>/gi, '').replace(/<[^>]+>/g, ' ').replace(/&[a-z]+;/gi, ' ').replace(/\s+/g, ' ').trim().slice(0, 8000);
  return { url, text, length: text.length };
}

// ─── Home Assistant helpers ───────────────────────────────────────────────────
let _haToken = null, _haTokenExpiry = 0;

async function getHaToken() {
  if (_haToken && Date.now() < _haTokenExpiry - 60000) return _haToken;
  const params = new URLSearchParams({ grant_type: 'refresh_token', refresh_token: process.env.HA_REFRESH_TOKEN, client_id: process.env.HA_CLIENT_ID });
  const res = await axios.post(`${process.env.HA_URL}/auth/token`, params.toString(), { headers: { 'Content-Type': 'application/x-www-form-urlencoded' } });
  _haToken = res.data.access_token;
  _haTokenExpiry = Date.now() + res.data.expires_in * 1000;
  return _haToken;
}

async function haGet(path) {
  const token = await getHaToken();
  const res = await axios.get(`${process.env.HA_URL}${path}`, { headers: { Authorization: `Bearer ${token}` } });
  return res.data;
}

async function haPost(path, data) {
  const token = await getHaToken();
  const res = await axios.post(`${process.env.HA_URL}${path}`, data, { headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' } });
  return res.data;
}

async function haCallService(service, entityId, extraData = {}) {
  const [domain, svc] = service.split('.');
  return haPost(`/api/services/${domain}/${svc}`, { entity_id: entityId, ...extraData });
}

async function getPropertyStatus() {
  const result = await buildPropertyStatus({
    haHealthCheck: () => checkHaHealth(haGet),
    getAllStates: () => haGet('/api/states'),
  });
  if (result.ok) markOk('home_assistant');
  else markFailed('home_assistant', result.error);
  return result;
}

// ─── REST routes ──────────────────────────────────────────────────────────────
app.get('/health', auth, async (req, res) => {
  const anyDown = Object.values(sourceStatus).some(s => !s.ok);
  res.json({ status: anyDown ? 'degraded' : 'ok', sources: Object.fromEntries(Object.entries(sourceStatus).map(([k, v]) => [k, { ok: v.ok, last_updated: v.last_updated, stale: isStale(k), ...(v.error ? { error: v.error } : {}) }])) });
});

app.get('/', (req, res) => { res.json({ name: 'homeos-mcp', version: '1.3.0', tools: MCP_TOOLS.map(t => t.name) }); });

app.get('/briefs', auth, (req, res) => {
  res.json({ briefs: listBriefFiles(BRIEFS_DIR) });
});
app.get('/briefs/:filename', auth, (req, res) => {
  try {
    res.type('text/markdown').send(readBriefFile(BRIEFS_DIR, req.params.filename));
  } catch (err) {
    res.status(404).json({ error: 'brief not found' });
  }
});

let bankingCache = null;
app.get('/finances/banking', auth, (req, res) => {
  if (!bankingCache) return res.status(503).json({ error: 'No banking data cached.' });
  res.json(bankingCache);
});
app.post('/finances/banking', auth, (req, res) => {
  bankingCache = { ...req.body, cached_at: new Date().toISOString() };
  res.json({ ok: true });
});

// ─── MCP tool definitions ─────────────────────────────────────────────────────
const MCP_TOOLS = [
  // ── Read tools ───────────────────────────────────────────────────────────────
  { name: 'get_pipeline', description: 'Get active job application pipeline from Airtable', inputSchema: { type: 'object', properties: {} } },
  { name: 'get_linear_urgent', description: 'Get urgent/high-priority Linear issues (priority 1 and 2)', inputSchema: { type: 'object', properties: {} } },
  { name: 'get_homestead', description: 'Get Linear Homestead project issues', inputSchema: { type: 'object', properties: {} } },
  { name: 'get_calendar', description: 'Get "I love you" Google Calendar events for next 30 days', inputSchema: { type: 'object', properties: {} } },
  { name: 'get_finances', description: 'Get YNAB budget snapshot — monthly surplus and overspent categories', inputSchema: { type: 'object', properties: {} } },
  { name: 'get_nox_leads', description: 'Get Nox Security leads from Airtable', inputSchema: { type: 'object', properties: {} } },
  { name: 'get_health', description: 'Get health/staleness status of all data sources', inputSchema: { type: 'object', properties: {} } },
  // ── YNAB ─────────────────────────────────────────────────────────────────────
  { name: 'get_accounts', description: 'Get all YNAB accounts with current balances', inputSchema: { type: 'object', properties: {} } },
  { name: 'get_transactions', description: 'Get YNAB transactions with optional filters', inputSchema: { type: 'object', properties: { account_id: { type: 'string' }, since: { type: 'string', description: 'YYYY-MM-DD' }, before: { type: 'string', description: 'YYYY-MM-DD' }, category_id: { type: 'string' }, payee_name: { type: 'string' } } } },
  { name: 'get_account_transactions', description: 'Get transactions for a single YNAB account', inputSchema: { type: 'object', properties: { account_id: { type: 'string' }, since: { type: 'string', description: 'YYYY-MM-DD' }, before: { type: 'string', description: 'YYYY-MM-DD' } }, required: ['account_id'] } },
  { name: 'get_transaction', description: 'Get a single YNAB transaction by ID', inputSchema: { type: 'object', properties: { transaction_id: { type: 'string' } }, required: ['transaction_id'] } },
  { name: 'get_categories', description: 'Get YNAB categories with budgeted/activity/balance', inputSchema: { type: 'object', properties: { month: { type: 'string', description: 'YYYY-MM, defaults to current' } } } },
  { name: 'get_payees', description: 'Get all YNAB payees', inputSchema: { type: 'object', properties: {} } },
  { name: 'get_scheduled_transactions', description: 'Get upcoming YNAB scheduled transactions', inputSchema: { type: 'object', properties: {} } },
  { name: 'get_upcoming_bills', description: 'Get upcoming YNAB scheduled transactions reconciled against recently cleared transactions. A scheduled bill already cleared (same payee, amount within +/-25%, last 10 days) is reported as status "paid" with the actual amount instead of "due" with the stale scheduled amount.', inputSchema: { type: 'object', properties: {} } },
  { name: 'get_months', description: 'Get month-by-month YNAB budget summary history', inputSchema: { type: 'object', properties: {} } },
  { name: 'get_month', description: 'Get full detail for a single YNAB budget month including all category balances', inputSchema: { type: 'object', properties: { month: { type: 'string', description: 'YYYY-MM, e.g. "2025-06"' } }, required: ['month'] } },
  { name: 'get_budget_settings', description: 'Get YNAB budget settings: date format and currency format', inputSchema: { type: 'object', properties: {} } },
  { name: 'create_transaction', description: 'Create a new YNAB transaction. amount in dollars: negative = outflow, positive = inflow.', inputSchema: { type: 'object', properties: { account_id: { type: 'string', description: 'YNAB account UUID (from get_accounts)' }, date: { type: 'string', description: 'YYYY-MM-DD' }, payee_name: { type: 'string', description: 'Payee name (max 200 chars)' }, amount: { type: 'number', description: 'Dollar amount. Negative = outflow, positive = inflow.' }, category_id: { type: 'string', description: 'YNAB category UUID (optional)' }, memo: { type: 'string' } }, required: ['account_id', 'date', 'payee_name', 'amount'] } },
  { name: 'update_transaction', description: 'Update an existing YNAB transaction. Only provided fields are changed. cleared: "cleared"/"uncleared"/"reconciled". amount in dollars.', inputSchema: { type: 'object', properties: { transaction_id: { type: 'string' }, cleared: { type: 'string', enum: ['cleared', 'uncleared', 'reconciled'] }, amount: { type: 'number' }, date: { type: 'string', description: 'YYYY-MM-DD' }, payee_name: { type: 'string' }, memo: { type: 'string' }, category_id: { type: 'string' } }, required: ['transaction_id'] } },
  { name: 'update_transactions_bulk', description: 'Update multiple YNAB transactions in one call. Each item must have id; all other fields optional.', inputSchema: { type: 'object', properties: { transactions: { type: 'array', items: { type: 'object', properties: { id: { type: 'string' }, cleared: { type: 'string', enum: ['cleared', 'uncleared', 'reconciled'] }, amount: { type: 'number' }, date: { type: 'string' }, payee_name: { type: 'string' }, memo: { type: 'string' }, category_id: { type: 'string' } }, required: ['id'] } } }, required: ['transactions'] } },
  { name: 'delete_transaction', description: 'Delete a YNAB transaction permanently', inputSchema: { type: 'object', properties: { transaction_id: { type: 'string' } }, required: ['transaction_id'] } },
  { name: 'create_scheduled_transaction', description: 'Create a new YNAB scheduled (recurring) transaction. amount in dollars: negative = outflow, positive = inflow.', inputSchema: { type: 'object', properties: { account_id: { type: 'string' }, date: { type: 'string', description: 'First occurrence YYYY-MM-DD' }, frequency: { type: 'string', enum: ['never', 'daily', 'weekly', 'monthly', 'yearly'] }, amount: { type: 'number' }, payee_name: { type: 'string' }, category_id: { type: 'string' }, memo: { type: 'string' } }, required: ['account_id', 'date', 'frequency', 'amount'] } },
  { name: 'update_category_budget', description: 'Set the budgeted dollar amount for a YNAB category in a given month', inputSchema: { type: 'object', properties: { month: { type: 'string', description: 'YYYY-MM' }, category_id: { type: 'string', description: 'YNAB category UUID (from get_categories)' }, budgeted: { type: 'number', description: 'Dollar amount to budget' } }, required: ['month', 'category_id', 'budgeted'] } },
  // ── Gmail ─────────────────────────────────────────────────────────────────────
  { name: 'search_gmail', description: 'Search Gmail INBOX for emails since a date.', inputSchema: { type: 'object', properties: { since_iso: { type: 'string' }, senders: { type: 'array', items: { type: 'string' } }, max_results: { type: 'number' } } } },
  { name: 'get_email', description: 'Fetch the full body of an email by IMAP UID', inputSchema: { type: 'object', properties: { uid: { type: 'number' } }, required: ['uid'] } },
  { name: 'create_gmail_draft', description: 'Create a Gmail draft. Do NOT send — draft only.', inputSchema: { type: 'object', properties: { to: { type: 'string' }, subject: { type: 'string' }, body_html: { type: 'string' }, body_text: { type: 'string' } }, required: ['to', 'subject'] } },
  // ── Airtable writes ───────────────────────────────────────────────────────────
  { name: 'list_all_jobs', description: 'Get ALL Airtable job records including Skip, Rejected, Done', inputSchema: { type: 'object', properties: {} } },
  { name: 'create_job', description: 'Create a new job record in Airtable', inputSchema: { type: 'object', properties: { company: { type: 'string' }, status: { type: 'string' }, notes: { type: 'string' } }, required: ['company', 'status'] } },
  { name: 'update_job', description: 'Update an existing Airtable job record', inputSchema: { type: 'object', properties: { record_id: { type: 'string' }, status: { type: 'string' }, notes_append: { type: 'string' } }, required: ['record_id'] } },
  // ── Linear writes ─────────────────────────────────────────────────────────────
  { name: 'close_linear_issue', description: 'Mark a Linear issue as Done', inputSchema: { type: 'object', properties: { issueId: { type: 'string' }, comment: { type: 'string' } }, required: ['issueId'] } },
  { name: 'list_linear_issues', description: 'List Linear issues with filters', inputSchema: { type: 'object', properties: { project_id: { type: 'string' }, priority: { oneOf: [{ type: 'number' }, { type: 'array', items: { type: 'number' } }] }, state_types: { oneOf: [{ type: 'string' }, { type: 'array', items: { type: 'string' } }] }, parent_id: { type: 'string' }, limit: { type: 'number' } } } },
  { name: 'create_linear_issue', description: 'Create a new Linear issue', inputSchema: { type: 'object', properties: { title: { type: 'string' }, description: { type: 'string' }, project_id: { type: 'string' }, priority: { type: 'number' }, parent_id: { type: 'string' } }, required: ['title'] } },
  { name: 'update_linear_issue', description: 'Update the status of a Linear issue', inputSchema: { type: 'object', properties: { issue_id: { type: 'string' }, state_type: { type: 'string' }, comment: { type: 'string' } }, required: ['issue_id', 'state_type'] } },
  { name: 'add_linear_comment', description: 'Add a comment to a Linear issue', inputSchema: { type: 'object', properties: { issue_id: { type: 'string' }, body: { type: 'string' } }, required: ['issue_id', 'body'] } },
  { name: 'get_linear_focus', description: 'Get today\'s Linear focus issue. Validates the pinned issue still exists before returning it; if it was deleted/archived, falls back to the highest-priority open issue from get_linear_urgent and reports the dangling id for cleanup.', inputSchema: { type: 'object', properties: { pinned_issue_id: { type: 'string', description: 'Previously pinned/selected issue id or identifier (optional)' } } } },
  // ── Home Assistant ────────────────────────────────────────────────────────────
  { name: 'get_home_state', description: 'Get Home Assistant entity states', inputSchema: { type: 'object', properties: { domain: { type: 'string' } } } },
  { name: 'control_home', description: 'Control Home Assistant devices', inputSchema: { type: 'object', properties: { service: { type: 'string' }, entity_id: { type: 'string' }, extra_data: { type: 'object' } }, required: ['service', 'entity_id'] } },
  { name: 'get_home_automations', description: 'List all Home Assistant automations', inputSchema: { type: 'object', properties: {} } },
  { name: 'get_property_status', description: 'Get the PROPERTY section for the brief: one HA health check gates weather + garage/cover reporting. On failure returns a single consolidated line ("Property status unavailable (HA unreachable)") instead of per-entity socket errors.', inputSchema: { type: 'object', properties: {} } },
  // ── Utility ───────────────────────────────────────────────────────────────────
  { name: 'run_brief', description: 'Trigger a morning or evening brief pipeline', inputSchema: { type: 'object', properties: { type: { type: 'string', enum: ['morning', 'evening'] } }, required: ['type'] } },
  { name: 'fetch_url', description: 'Fetch the text content of a URL (strips HTML)', inputSchema: { type: 'object', properties: { url: { type: 'string' } }, required: ['url'] } },
];

// ─── MCP Streamable HTTP transport ───────────────────────────────────────────
const { Server: _McpServer } = require('@modelcontextprotocol/sdk/server');
const { StreamableHTTPServerTransport: _StreamableHTTP } = require('@modelcontextprotocol/sdk/server/streamableHttp.js');
const { ListToolsRequestSchema: _ListTools, CallToolRequestSchema: _CallTool } = require('@modelcontextprotocol/sdk/types.js');

function _createMcpServer() {
  const mcp = new _McpServer({ name: 'homeos-mcp', version: '1.3.0' }, { capabilities: { tools: {} } });
  mcp.setRequestHandler(_ListTools, async () => ({ tools: MCP_TOOLS }));

  mcp.setRequestHandler(_CallTool, async (request) => {
    const name = request.params.name;
    const args = request.params.arguments || {};
    try {
      let data;
      switch (name) {
        case 'get_pipeline': {
          const records = await fetchAirtableRecords(JOB_BASE(), JOB_TABLE(), { filterByFormula: "NOT(OR({Status}='Skip',{Status}='Done',{Status}='Rejected'))" });
          markOk('airtable');
          const ACTIVE = ['Applied', 'Recruiter screen', 'In process', 'In progress'];
          const active = records.filter(r => ACTIVE.includes(r.fields.Status));
          const todo   = records.filter(r => r.fields.Status === 'Todo');
          const now    = Date.now();
          const silent = active.filter(r => r.fields.Status === 'Applied' && (now - new Date(r.createdTime).getTime()) > 21 * 86400 * 1000);
          data = { applied: active.length, in_process: active.filter(r => ['In process', 'In progress'].includes(r.fields.Status)).length, warm_leads: todo.length, silent_applications: silent.map(r => ({ company: r.fields.Name, days: Math.floor((now - new Date(r.createdTime).getTime()) / 86400000) })), records: active.map(r => ({ id: r.id, company: r.fields.Name, status: r.fields.Status, notes: r.fields.Notes })) };
          break;
        }
        case 'get_linear_urgent': { const issues = await getLinearUrgentIssues(); markOk('linear'); data = { issues: issues.map(i => ({ id: i.identifier, title: i.title, priority: i.priority, due: i.dueDate, project: i.project?.name, url: i.url })) }; break; }
        case 'get_homestead': { const issues = await getLinearProjectIssues(process.env.LINEAR_HOMESTEAD_PROJECT_ID); markOk('linear'); data = { issues: issues.map(i => ({ id: i.identifier, title: i.title, state: i.state.name, due: i.dueDate })) }; break; }
        case 'get_calendar': { const events = await getCalendarEvents(process.env.GOOGLE_CALENDAR_ILOVEYOU_ID, 30); markOk('google_calendar'); const n = Date.now(); data = { events: events.map(e => ({ title: e.summary, date: e.start.date || e.start.dateTime, days_away: Math.floor((new Date(e.start.date || e.start.dateTime).getTime() - n) / 86400000), description: e.description })) }; break; }
        case 'get_finances': { const ynab = await getYnabSnapshot(); markOk('ynab'); data = { monthly_surplus: ynab.to_be_budgeted, income: ynab.income, budgeted: ynab.budgeted, activity: ynab.activity, overspent_categories: ynab.categories.filter(c => c.balance < 0).map(c => ({ name: c.name, overspent: Math.abs(c.balance / 1000) })) }; break; }
        case 'get_nox_leads': { const records = await fetchAirtableRecords(process.env.AIRTABLE_NOX_BASE_ID, process.env.AIRTABLE_NOX_TABLE_ID); markOk('airtable'); data = { leads: records.map(r => ({ id: r.id, ...r.fields })) }; break; }
        case 'get_health': { data = { status: Object.values(sourceStatus).some(s => !s.ok) ? 'degraded' : 'ok', sources: sourceStatus }; break; }
        case 'close_linear_issue': { if (!args.issueId) throw new Error('issueId required'); await closeLinearIssue(args.issueId, args.comment); markOk('linear'); data = { ok: true, issueId: args.issueId }; break; }
        // ── YNAB ──────────────────────────────────────────────────────────────
        case 'get_accounts':                { data = await getYnabAccounts(); markOk('ynab'); break; }
        case 'get_transactions':            { data = { transactions: await getYnabTransactions(args) }; markOk('ynab'); break; }
        case 'get_account_transactions':    { if (!args.account_id) throw new Error('account_id required'); data = { transactions: await getYnabAccountTransactions(args.account_id, args) }; markOk('ynab'); break; }
        case 'get_transaction':             { if (!args.transaction_id) throw new Error('transaction_id required'); data = await getYnabTransaction(args.transaction_id); markOk('ynab'); break; }
        case 'get_categories':              { data = { categories: await getYnabCategories(args.month) }; markOk('ynab'); break; }
        case 'get_payees':                  { data = { payees: await getYnabPayees() }; markOk('ynab'); break; }
        case 'get_scheduled_transactions':  { data = { scheduled: await getYnabScheduledTransactions() }; markOk('ynab'); break; }
        case 'get_upcoming_bills':          { data = { bills: await getYnabUpcomingBills() }; markOk('ynab'); break; }
        case 'get_months':                  { data = { months: await getYnabMonths() }; markOk('ynab'); break; }
        case 'get_month':                   { if (!args.month) throw new Error('month required'); data = await getYnabMonth(args.month); markOk('ynab'); break; }
        case 'get_budget_settings':         { data = await getYnabBudgetSettings(); markOk('ynab'); break; }
        case 'create_transaction':          { data = await createYnabTransaction(args); break; }
        case 'update_transaction':          { if (!args.transaction_id) throw new Error('transaction_id required'); data = await updateYnabTransaction(args.transaction_id, args); markOk('ynab'); break; }
        case 'update_transactions_bulk':    { if (!args.transactions) throw new Error('transactions required'); data = await updateYnabTransactionsBulk(args.transactions); markOk('ynab'); break; }
        case 'delete_transaction':          { if (!args.transaction_id) throw new Error('transaction_id required'); data = await deleteYnabTransaction(args.transaction_id); markOk('ynab'); break; }
        case 'create_scheduled_transaction':{ data = await createYnabScheduledTransaction(args); break; }
        case 'update_category_budget':      { if (!args.month || !args.category_id || args.budgeted === undefined) throw new Error('month, category_id, budgeted required'); data = await updateYnabCategoryBudget(args.month, args.category_id, args.budgeted); markOk('ynab'); break; }
        // ── Gmail ──────────────────────────────────────────────────────────────
        case 'search_gmail':       { data = await searchGmail(args); break; }
        case 'get_email':          { data = await getEmail(args); break; }
        case 'create_gmail_draft': { data = await createGmailDraft(args); break; }
        // ── Airtable ───────────────────────────────────────────────────────────
        case 'list_all_jobs':  { data = { records: await getAllJobs() }; markOk('airtable'); break; }
        case 'create_job':     { data = await createJob(args); break; }
        case 'update_job':     { data = await updateJob(args); break; }
        // ── Linear ─────────────────────────────────────────────────────────────
        case 'list_linear_issues':  { data = { issues: await listLinearIssues(args) }; break; }
        case 'create_linear_issue': { data = await createLinearIssue(args); break; }
        case 'update_linear_issue': { data = await updateLinearIssue(args); break; }
        case 'add_linear_comment':  { data = await addLinearComment(args); break; }
        case 'get_linear_focus':    { data = await getLinearFocus(args.pinned_issue_id); markOk('linear'); break; }
        // ── Home Assistant ─────────────────────────────────────────────────────
        case 'get_home_state': {
          const states = await haGet('/api/states');
          let filtered = states;
          if (args.domain) filtered = states.filter(s => s.entity_id.startsWith(args.domain + '.'));
          markOk('home_assistant');
          data = filtered.map(s => ({ entity_id: s.entity_id, state: s.state, friendly_name: s.attributes.friendly_name, last_changed: s.last_changed, ...(s.attributes.temperature !== undefined ? { temperature: s.attributes.temperature } : {}), ...(s.attributes.humidity !== undefined ? { humidity: s.attributes.humidity } : {}), ...(s.attributes.battery_level !== undefined ? { battery: s.attributes.battery_level } : {}) }));
          break;
        }
        case 'control_home': { if (!args.service || !args.entity_id) throw new Error('service and entity_id required'); const result = await haCallService(args.service, args.entity_id, args.extra_data || {}); markOk('home_assistant'); data = { ok: true, service: args.service, entity_id: args.entity_id, result }; break; }
        case 'get_home_automations': { const states = await haGet('/api/states'); const autos = states.filter(s => s.entity_id.startsWith('automation.')); markOk('home_assistant'); data = { automations: autos.map(a => ({ id: a.entity_id, name: a.attributes.friendly_name || a.entity_id.replace('automation.', ''), state: a.state, last_triggered: a.attributes.last_triggered || null })) }; break; }
        case 'get_property_status': { data = await getPropertyStatus(); break; }
        // ── Utility ────────────────────────────────────────────────────────────
        case 'fetch_url': { data = await fetchUrl(args); break; }
        case 'run_brief': { data = await runBrief(args); break; }
        default: throw new Error(`Unknown tool: ${name}`);
      }
      return { content: [{ type: 'text', text: JSON.stringify(data, null, 2) }] };
    } catch (err) {
      const source = TOOL_SOURCE[name];
      if (source) markFailed(source, err.message);
      return { content: [{ type: 'text', text: `Error: ${err.message}` }], isError: true };
    }
  });

  return mcp;
}

app.post('/mcp', auth, async (req, res) => {
  try {
    const mcp = _createMcpServer();
    const transport = new _StreamableHTTP({ sessionIdGenerator: undefined });
    await mcp.connect(transport);
    await transport.handleRequest(req, res, req.body);
  } catch (err) {
    if (!res.headersSent) res.status(500).json({ error: err.message });
  }
});

app.get('/mcp', (req, res) => res.status(405).json({ error: 'Use POST for MCP' }));

app.listen(PORT, '0.0.0.0', () => {
  console.log(`HomeOS MCP server v1.3 running on port ${PORT}`);
  console.log(`Bearer auth: ${BEARER_TOKEN ? 'enabled' : 'DISABLED'}`);
  console.log(`Tools: ${MCP_TOOLS.length}`);
});
