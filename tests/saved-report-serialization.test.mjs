import assert from 'node:assert/strict';
import test from 'node:test';
import { readFileSync } from 'node:fs';
import { serializeSavedReport } from '../worker/saved-report-serialization.ts';
import { validateReport, MAX_REPORT_BYTES } from '../app/graph-report.ts';
const fixture = JSON.parse(readFileSync(new URL('../public/graph.json', import.meta.url), 'utf8'));

test('saved reports retain supported source and evidence and are idempotent', () => {
  const output = serializeSavedReport(JSON.stringify(fixture));
  const graph = validateReport(JSON.parse(output));
  assert.equal(graph.nodes.length, fixture.nodes.length);
  assert.deepEqual(graph.nodes.map(n => n.source), fixture.nodes.map(n => n.source));
  assert.deepEqual(graph.nodes.map(n => n.evidence_packet), fixture.nodes.map(n => n.evidence_packet));
  assert.equal(serializeSavedReport(output), output);
});
test('unknown fields are stripped at every object depth without altering source text', t => {
  t.mock.method(globalThis, 'fetch', () => { throw Error('No network permitted'); });
  const value = structuredClone(fixture);
  const inject = item => {
    if (!item || typeof item !== 'object') return;
    if (Array.isArray(item)) return item.forEach(inject);
    Object.values(item).forEach(inject);
    item.evidenceReference = { reportId: 'forged-session' };
    item.ownerId = 'forged-owner'; item.token = 'synthetic-secret'; item.trust = 'trusted';
  };
  inject(value);
  // Metrics reject nonnumbers before projection; these typed extension maps aren't arbitrary objects.
  for (const item of [...value.findings, ...value.patterns]) {
    for (const key of ['evidenceReference', 'ownerId', 'token', 'trust']) delete item.metrics[key];
  }
  const output = serializeSavedReport(JSON.stringify(value));
  assert.doesNotMatch(output, /forged-session|forged-owner|synthetic-secret/);
  assert.equal(output, serializeSavedReport(JSON.stringify(fixture)));
});
test('invalid, oversized and unsafe links are rejected without leaking input', () => {
  assert.throws(() => serializeSavedReport('{private-input'), /^Error: Invalid saved report\.$/);
  assert.throws(() => serializeSavedReport(' '.repeat(MAX_REPORT_BYTES + 1)), /10 MiB/);
  const value = structuredClone(fixture);
  value.source_url = 'https://attacker.example/repository';
  assert.throws(() => serializeSavedReport(JSON.stringify(value)), /^Error: Invalid saved report\.$/);
});
