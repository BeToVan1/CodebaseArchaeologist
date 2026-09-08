import type { PendingReport } from './saved-report-policy.ts';

export type ReportState = 'pending' | 'ready' | 'deleting' | 'deleted';
export type ReportRecord = Omit<PendingReport, 'state'> & { state: ReportState; revision: number };
export type ReportEvent =
  | { kind: 'stored'; bytes: number; payloadSha256: string }
  | { kind: 'delete' }
  | { kind: 'storage-removed' };

/** Pure transition proposal, not authentication or an actual storage operation.
 * Adapter must compare-and-swap revision AND account quota in one transaction.
 * Only the storage adapter may issue stored/storage-removed confirmations.
 * Delete pending objects only after active writers are fenced/drained, otherwise
 * a late object write can recreate deleted bytes. Never expose events to clients.
 */
export function transitionReport(record: ReportRecord, event: ReportEvent,
  context: { ownerId: string; expectedRevision: number; now: number }): {
    record: ReportRecord; releaseBytes: number; releaseReports: number;
  } {
  if (!context.ownerId || record.ownerId !== context.ownerId) throw new Error('Report unavailable.');
  if (!Number.isSafeInteger(record.revision) || record.revision < 0
    || context.expectedRevision !== record.revision || record.revision === Number.MAX_SAFE_INTEGER
    || !Number.isSafeInteger(context.now) || context.now < 0
    || !Number.isSafeInteger(record.expiresAt) || record.expiresAt < 0
    || !Number.isSafeInteger(record.bytes) || record.bytes <= 0
    || !['pending', 'ready', 'deleting', 'deleted'].includes(record.state)) throw new Error('Invalid report transition.');

  let state = record.state;
  let releaseBytes = 0, releaseReports = 0;
  if (event.kind === 'stored') {
    if (state !== 'pending' || context.now >= record.expiresAt
      || event.bytes !== record.bytes || event.payloadSha256 !== record.payloadSha256)
      throw new Error('Invalid report transition.');
    state = 'ready';
  } else if (event.kind === 'delete') {
    if (state !== 'deleted') state = 'deleting';
  } else if (event.kind === 'storage-removed') {
    if (state !== 'deleting' && state !== 'deleted') throw new Error('Invalid report transition.');
    if (state === 'deleting') {
      state = 'deleted'; releaseBytes = record.bytes; releaseReports = 1;
    }
  } else throw new Error('Invalid report transition.');
  return { record: { ...record, state, revision: record.revision + Number(state !== record.state) }, releaseBytes, releaseReports };
}
