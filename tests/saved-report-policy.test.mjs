import assert from 'node:assert/strict';
import test from 'node:test';
import { SAVED_REPORT_LIMITS as limits, validateSaveMetadata, admissionAllowed, pendingReport, mayReadReport } from '../worker/saved-report-policy.ts';

const metadata = { title: ' Example ', payloadSha256: 'a'.repeat(64), bytes: 100 };
const context = { ownerId: 'account-a', id: 'b'.repeat(32), now: 1000 };

test('metadata excludes caller ownership, trust and evidence capabilities', () => {
  assert.equal(validateSaveMetadata(metadata).title, 'Example');
  for (const key of ['ownerId', 'trust', 'evidenceReference', 'reportId', 'token']) {
    assert.throws(() => validateSaveMetadata({ ...metadata, [key]: 'forged' }), /Invalid/);
  }
});
test('metadata rejects invalid or oversized values', () => {
  for (const bytes of [0, -1, 0.5, NaN, Infinity, limits.maxReportBytes + 1])
    assert.throws(() => validateSaveMetadata({ ...metadata, bytes }));
  for (const title of ['', ' ', 'a'.repeat(121), 'line\nbreak'])
    assert.throws(() => validateSaveMetadata({ ...metadata, title }));
  assert.throws(() => validateSaveMetadata({ ...metadata, payloadSha256: 'wrong' }));
});
test('pending record is immutable by input mutation and never AI-trusted', () => {
  const input = { ...metadata };
  const result = pendingReport(input, context);
  input.title = 'changed';
  assert.equal(result.title, 'Example');
  assert.equal(result.trust, 'uploaded-unverified');
  assert.equal(result.state, 'pending');
  assert.equal(result.expiresAt, context.now + limits.retentionMs);
  assert.equal(result.ownerId, context.ownerId);
});
test('invalid server context fails closed', () => {
  for (const patch of [{ ownerId: '' }, { id: '../report' }, { now: NaN }, { now: Number.MAX_SAFE_INTEGER }])
    assert.throws(() => pendingReport(metadata, { ...context, ...patch }));
});
test('admission respects exact byte boundary, counts and global ceilings', () => {
  const global = { bytes: 0, reports: 0 }, ceiling = { bytes: 1000, reports: 20 };
  assert.equal(admissionAllowed(100, { bytes: limits.maxOwnerBytes - 100, reports: 9 }, global, ceiling), true);
  assert.equal(admissionAllowed(100, { bytes: 0, reports: 10 }, global, ceiling), false);
  assert.equal(admissionAllowed(100, { bytes: limits.maxOwnerBytes - 99, reports: 0 }, global, ceiling), false);
  assert.equal(admissionAllowed(100, global, { bytes: 901, reports: 0 }, ceiling), false);
  assert.equal(admissionAllowed(100, global, { bytes: 0, reports: 20 }, ceiling), false);
  assert.equal(admissionAllowed(100, global, global, { bytes: 0, reports: 0 }), false);
  assert.equal(admissionAllowed(100, { bytes: -1, reports: 0 }, global, ceiling), false);
});
test('read policy denies other owners, expired reports and nonready states', () => {
  const report = { ...pendingReport(metadata, context), state: 'ready' };
  assert.equal(mayReadReport(report, 'account-a', report.expiresAt - 1), true);
  assert.equal(mayReadReport(report, 'account-b', context.now), false);
  assert.equal(mayReadReport(report, 'account-a', report.expiresAt), false);
  assert.equal(mayReadReport(report, 'account-a', NaN), false);
  for (const state of ['pending', 'deleting', 'deleted', 'unknown'])
    assert.equal(mayReadReport({ ...report, state }, 'account-a', context.now), false);
});
