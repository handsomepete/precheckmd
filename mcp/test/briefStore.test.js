'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('fs');
const os = require('os');
const path = require('path');
const {
  detectBriefType,
  briefFilename,
  saveBriefFile,
  readBriefFile,
  listBriefFiles,
  maybePersistBriefFromDraft,
} = require('../lib/briefStore');

function tmpDir() {
  return fs.mkdtempSync(path.join(os.tmpdir(), 'briefs-test-'));
}

test('detectBriefType recognizes morning/evening subjects and ignores unrelated ones', () => {
  assert.equal(detectBriefType('Your Morning Brief — July 8'), 'morning');
  assert.equal(detectBriefType('Evening Brief: wrap-up'), 'evening');
  assert.equal(detectBriefType('Weekly brief'), 'brief');
  assert.equal(detectBriefType('Re: dinner tonight?'), null);
  assert.equal(detectBriefType(''), null);
});

test('saveBriefFile writes a dated markdown file and rejects bad types', () => {
  const dir = tmpDir();
  const { filename, path: filePath } = saveBriefFile(dir, {
    type: 'morning',
    date: '2026-07-08',
    body: '# Morning Brief\n\nHydro One: paid $454 (cleared).',
  });

  assert.equal(filename, '2026-07-08-morning.md');
  assert.equal(fs.readFileSync(filePath, 'utf8'), '# Morning Brief\n\nHydro One: paid $454 (cleared).');

  assert.throws(() => saveBriefFile(dir, { type: '../../etc', date: '2026-07-08', body: 'x' }));
});

test('readBriefFile round-trips and rejects path traversal filenames', () => {
  const dir = tmpDir();
  saveBriefFile(dir, { type: 'evening', date: '2026-07-08', body: 'evening content' });

  assert.equal(readBriefFile(dir, '2026-07-08-evening.md'), 'evening content');
  assert.throws(() => readBriefFile(dir, '../../../etc/passwd'));
  assert.throws(() => readBriefFile(dir, '2026-07-08-evening.md/../../secret'));
});

test('listBriefFiles returns only well-formed brief filenames, newest first', () => {
  const dir = tmpDir();
  saveBriefFile(dir, { type: 'morning', date: '2026-07-06', body: 'a' });
  saveBriefFile(dir, { type: 'morning', date: '2026-07-08', body: 'b' });
  fs.writeFileSync(path.join(dir, 'not-a-brief.txt'), 'ignore me');

  const files = listBriefFiles(dir);
  assert.deepEqual(files, ['2026-07-08-morning.md', '2026-07-06-morning.md']);
});

test('listBriefFiles returns empty array for a directory that does not exist yet', () => {
  const dir = path.join(tmpDir(), 'nested', 'missing');
  assert.deepEqual(listBriefFiles(dir), []);
});

test('maybePersistBriefFromDraft persists brief-looking drafts and skips everything else', () => {
  const dir = tmpDir();
  const now = new Date('2026-07-08T13:00:00Z');

  const result = maybePersistBriefFromDraft(
    dir,
    { subject: 'Your Morning Brief', body_text: 'Hydro One: paid $454 (cleared).' },
    now
  );
  assert.equal(result.filename, '2026-07-08-morning.md');
  assert.equal(readBriefFile(dir, '2026-07-08-morning.md'), 'Hydro One: paid $454 (cleared).');

  const skipped = maybePersistBriefFromDraft(
    dir,
    { subject: 'Interview follow-up', body_text: 'Thanks for chatting today.' },
    now
  );
  assert.equal(skipped, null);
});

test('maybePersistBriefFromDraft strips HTML when only body_html is provided', () => {
  const dir = tmpDir();
  const now = new Date('2026-07-08T13:00:00Z');

  maybePersistBriefFromDraft(
    dir,
    { subject: 'Evening Brief', body_html: '<p>All quiet <b>tonight</b>.</p>' },
    now
  );
  assert.equal(readBriefFile(dir, '2026-07-08-evening.md'), 'All quiet tonight .');
});
