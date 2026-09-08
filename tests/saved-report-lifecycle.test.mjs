import assert from 'node:assert/strict';
import test from 'node:test';
import { pendingReport, mayReadReport } from '../worker/saved-report-policy.ts';
import { transitionReport } from '../worker/saved-report-lifecycle.ts';

const initial = () => ({ ...pendingReport({title:'Example', bytes:100, payloadSha256:'a'.repeat(64)},
  {ownerId:'owner', id:'b'.repeat(32), now:0}), revision:0 });
const stored = {kind:'stored', bytes:100, payloadSha256:'a'.repeat(64)};
const apply = (record, event, overrides={}) => transitionReport(record, event,
  {ownerId:'owner', expectedRevision:record.revision, now:1, ...overrides});

test('only verified stored metadata moves pending to ready', () => {
  const pending = initial();
  assert.equal(mayReadReport(pending, 'owner', 1), false);
  for (const event of [{...stored,bytes:99},{...stored,payloadSha256:'c'.repeat(64)}])
    assert.throws(() => apply(pending, event));
  const result = apply(pending, stored);
  assert.equal(result.record.state, 'ready');
  assert.equal(result.record.trust, 'uploaded-unverified');
  assert.equal(result.releaseBytes, 0);
  assert.equal(pending.state, 'pending');
});
test('failed or missing storage leaves pending report unreadable and charged', () => {
  // No confirmation event is issued when mock storage fails.
  const report = initial();
  assert.equal(mayReadReport(report, 'owner', 1), false);
  const deletion = apply(report, {kind:'delete'});
  assert.equal(deletion.releaseBytes, 0);
  assert.equal(deletion.releaseReports, 0);
  assert.equal(mayReadReport(deletion.record, 'owner', 1), false);
});
test('quota release occurs once after removal, never at tombstone', () => {
  const ready = apply(initial(), stored).record;
  const deleting = apply(ready, {kind:'delete'});
  assert.equal(deleting.releaseBytes, 0);
  const deleted = apply(deleting.record, {kind:'storage-removed'});
  assert.equal(deleted.releaseBytes, 100);
  assert.equal(deleted.releaseReports, 1);
  const duplicate = apply(deleted.record, {kind:'storage-removed'});
  assert.equal(duplicate.releaseBytes, 0);
  assert.equal(duplicate.releaseReports, 0);
  assert.throws(() => apply(deleted.record, stored));
});
test('stale confirmations cannot revive a tombstone and foreign owners cannot mutate', () => {
  const pending = initial();
  const deleting = apply(pending, {kind:'delete'}).record;
  assert.throws(() => apply(deleting, stored, {expectedRevision:0}));
  assert.throws(() => apply(deleting, stored));
  assert.throws(() => apply(pending, {kind:'delete'}, {ownerId:'other'}), /unavailable/);
  assert.throws(() => apply(pending, {kind:'storage-removed'}));
});
test('expired pending reports cannot become ready but remain deletable', () => {
  const report = initial();
  assert.throws(() => apply(report, stored, {now:report.expiresAt}));
  assert.equal(apply(report, {kind:'delete'}, {now:report.expiresAt}).record.state, 'deleting');
  assert.throws(() => apply(report, stored, {now:NaN}));
});
