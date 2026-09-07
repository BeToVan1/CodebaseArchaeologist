import assert from 'node:assert/strict';
import test from 'node:test';
import { localExecutionClaims } from '../app/local-execution.ts';

const id = 'symbol:example.py:example.f:1';
const claim = {id:`claim:${id}:local-execution:0`, classification:'fact', text:'Result ignored.', confidence:1, provenance:'AST', evidence_refs:[id]};
test('execution presentation selects owner-bound fact claims only', () => {
  const node = {id, kind:'function', evidence_packet:{claims:[claim,
    {...claim, classification:'heuristic'}, {...claim, id:'claim:other:local-execution:0'},
    {...claim, evidence_refs:['other']}, {...claim, id:`claim:${id}:local-execution:unknown`} ]}};
  assert.deepEqual(localExecutionClaims(node), [claim]);
  assert.equal(node.evidence_packet.claims.length, 5);
});
test('older reports and file selection do not fabricate facts', () => {
  assert.deepEqual(localExecutionClaims(null), []);
  assert.deepEqual(localExecutionClaims({id,kind:'function'}), []);
  assert.deepEqual(localExecutionClaims({id,kind:'file',evidence_packet:{claims:[claim]}}), []);
});
