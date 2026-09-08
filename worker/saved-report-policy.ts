/** Offline policy only. No routes, persistence, authentication, or AI authority. */
export const SAVED_REPORT_LIMITS = Object.freeze({
  maxReportBytes: 10 * 1024 * 1024,
  maxOwnerBytes: 50 * 1024 * 1024,
  maxOwnerReports: 10,
  retentionMs: 30 * 24 * 60 * 60 * 1000,
});

export type Usage = { bytes: number; reports: number };
export type SaveMetadata = { title: string; payloadSha256: string; bytes: number };
export type PendingReport = SaveMetadata & {
  id: string; ownerId: string; state: 'pending'; trust: 'uploaded-unverified';
  createdAt: number; expiresAt: number;
};

function nonnegative(value: unknown): value is number {
  return Number.isSafeInteger(value) && Number(value) >= 0;
}

/** These values must be derived from validated serialized bytes on the server. */
export function validateSaveMetadata(value: unknown): SaveMetadata {
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error('Invalid save metadata.');
  const v = value as Record<string, unknown>;
  if (Object.keys(v).sort().join(',') !== 'bytes,payloadSha256,title'
    || typeof v.title !== 'string' || !v.title.trim() || v.title.length > 120
    || /[\u0000-\u001f\u007f]/.test(v.title)
    || typeof v.payloadSha256 !== 'string' || !/^[a-f0-9]{64}$/.test(v.payloadSha256)
    || !nonnegative(v.bytes) || v.bytes === 0 || v.bytes > SAVED_REPORT_LIMITS.maxReportBytes) {
    throw new Error('Invalid save metadata.');
  }
  return { title: v.title.trim(), payloadSha256: v.payloadSha256, bytes: v.bytes };
}

/** Pure decision, NOT an atomic reservation. Future database adapter must serialize admission. */
export function admissionAllowed(bytes: number, owner: Usage, global: Usage, ceiling: Usage): boolean {
  const values = [bytes, owner.bytes, owner.reports, global.bytes, global.reports, ceiling.bytes, ceiling.reports];
  if (!values.every(nonnegative) || bytes === 0 || ceiling.bytes === 0 || ceiling.reports === 0) return false;
  return bytes <= SAVED_REPORT_LIMITS.maxReportBytes
    && owner.reports < SAVED_REPORT_LIMITS.maxOwnerReports
    && bytes <= SAVED_REPORT_LIMITS.maxOwnerBytes - owner.bytes
    && global.reports < ceiling.reports && bytes <= ceiling.bytes - global.bytes;
}

/** Identity and ID come from authenticated server context, never from uploaded metadata. */
export function pendingReport(metadata: unknown, context: { ownerId: string; id: string; now: number }): PendingReport {
  const clean = validateSaveMetadata(metadata);
  if (typeof context.ownerId !== 'string' || !context.ownerId.trim() || context.ownerId.length > 256
    || /[\u0000-\u001f\u007f]/.test(context.ownerId)
    || typeof context.id !== 'string' || !/^[a-f0-9]{32}$/.test(context.id)
    || !nonnegative(context.now) || !Number.isSafeInteger(context.now + SAVED_REPORT_LIMITS.retentionMs)) {
    throw new Error('Invalid server report context.');
  }
  return { ...clean, id: context.id, ownerId: context.ownerId, state: 'pending',
    trust: 'uploaded-unverified', createdAt: context.now, expiresAt: context.now + SAVED_REPORT_LIMITS.retentionMs };
}

/** Caller still needs authenticated identity and integrity-checked report bytes. */
export function mayReadReport(report: { ownerId: string; state: string; expiresAt: number }, ownerId: string, now: number): boolean {
  return typeof ownerId === 'string' && Boolean(ownerId.trim()) && report.ownerId === ownerId
    && report.state === 'ready' && nonnegative(now) && nonnegative(report.expiresAt) && now < report.expiresAt;
}
