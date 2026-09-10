import assert from 'node:assert/strict';
import test from 'node:test';
import { entrypointFlows, flowEvidenceTarget } from '../app/flow-presentation.ts';

const flows = [{id:'one',entrypoint_id:'a',label:'GET /',unresolved_steps:[{reason:'flow-path-budget-reached'}]},
  {id:'two',entrypoint_id:'b',label:'GET /'}, {id:'three',entrypoint_id:'a',label:'GET /'}];
test('entrypoint identity keeps same-labelled routes separate', () => {
  const result = entrypointFlows(flows, 'b');
  assert.deepEqual(result.entrypointIds, ['a','b']);
  assert.deepEqual(result.paths, [flows[1]]);
});

const sourceNodes = [{id:'file',kind:'file',path:'source.py'},
  {id:'caller',kind:'function',path:'source.py',start_line:10,end_line:20},
  {id:'callee',kind:'function',path:'source.py',start_line:30,end_line:40}];
test('evidence navigation uses the caller location, not the destination', () => {
  assert.deepEqual(flowEvidenceTarget(sourceNodes, 'caller', {path:'source.py',line:15}),
    {nodeId:'caller',path:'source.py',line:15});
  assert.equal(flowEvidenceTarget(sourceNodes, 'callee', {path:'source.py',line:15}), null);
});
for (const [name,evidence] of Object.entries({missing:{path:'source.py'}, zero:{path:'source.py',line:0},
  negative:{path:'source.py',line:-1}, fractional:{path:'source.py',line:12.5},
  outside:{path:'source.py',line:21}, wrongFile:{path:'other.py',line:15},
  text:{path:'source.py',line:'15'}, infinite:{path:'source.py',line:Infinity}})) {
  test(`invalid evidence location is not navigable: ${name}`, () => {
    assert.equal(flowEvidenceTarget(sourceNodes,'caller',evidence),null);
  });
}
test('missing symbols, owning files and range metadata are not guessed', () => {
  assert.equal(flowEvidenceTarget(sourceNodes,'unknown',{path:'source.py',line:15}),null);
  assert.equal(flowEvidenceTarget(sourceNodes.slice(1),'caller',{path:'source.py',line:15}),null);
  assert.equal(flowEvidenceTarget([{...sourceNodes[1],start_line:undefined},sourceNodes[0]],'caller',{path:'source.py',line:15}),null);
});
test('missing captured text still permits opening the correct source-unavailable panel', () => {
  assert.equal(flowEvidenceTarget(sourceNodes,'caller',{path:'source.py',line:10}).line,10);
  assert.equal(flowEvidenceTarget(sourceNodes,'caller',{path:'source.py',line:20}).line,20);
});
test('multiple paths keep original uncertainty and are not mutated', () => {
  const before = structuredClone(flows);
  assert.deepEqual(entrypointFlows(flows, 'a').paths, [flows[0],flows[2]]);
  assert.deepEqual(flows, before);
});
test('report replacement and legacy reports fall back to an available entrypoint', () => {
  assert.equal(entrypointFlows(flows, 'old-report-node').entrypointId, 'a');
  assert.equal(entrypointFlows(flows, null).entrypointId, 'a');
  assert.deepEqual(entrypointFlows([], 'b'), {entrypointIds:[],entrypointId:null,paths:[]});
});
