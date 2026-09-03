import assert from "node:assert/strict";
import test from "node:test";
import { changedFileSelection, selectionMetadata, evidenceLocation, currentReportLabel, explainFile } from "../app/graph-presentation.ts";

test("keyboard selection updates the inspector only for visible selected files", () => {
  const visible = new Set(["file:a.py", "file:b.py"]);
  assert.equal(changedFileSelection([{id:"file:a.py",type:"select",selected:false},{id:"file:b.py",type:"select",selected:true}], visible), "file:b.py");
  assert.equal(changedFileSelection([{id:"file:hidden.py",type:"select",selected:true}], visible), null);
  assert.equal(changedFileSelection([{id:"file:a.py",type:"position",position:{x:1,y:2}},{id:"file:a.py",type:"select",selected:false}], visible), null);
  assert.equal(changedFileSelection([], visible), null);
});

const file = {id:"file:models.py",kind:"file",path:"models.py",source:"class Item: pass"};
const model = {id:"symbol:models.py:Item:1",kind:"class",path:"models.py",qualified_name:"models.Item",start_line:1,end_line:3,sqlalchemy:{kind:"model",table_name:"items",columns:[],relationships:[],is_abstract:false}};
test("symbol metadata never substitutes its containing file identity", () => {
  const actual = selectionMetadata(file, model);
  assert.deepEqual(actual, {kind:"Python class",id:model.id,name:"models.Item",range:"1–3"});
  assert.equal(selectionMetadata(file,null).id,file.id);
  assert.equal(selectionMetadata(file,null).range,null);
});
test("flow evidence uses the call-site path, never the destination filename", () => {
  assert.equal(evidenceLocation({target:model.id,evidence:{path:"repository.py",line:6}}),"repository.py:6");
  assert.equal(evidenceLocation({target:model.id,evidence:{line:6}}),"Evidence location unavailable");
  assert.equal(evidenceLocation(undefined),"Evidence location unavailable");
});
test("current report label describes installed origin and tier independently of request mode", () => {
  assert.equal(currentReportLabel("example","deep"),"Bundled example · deep report");
  assert.equal(currentReportLabel("analysis","inventory"),"Analysis result · inventory report");
  assert.equal(currentReportLabel("imported","deep"),"Imported report · deep report");
});
test("file role prioritizes recorded ORM and route metadata over path guesses", () => {
  const route={id:"symbol:models.py:route:5",kind:"function",path:"models.py",entrypoint:{framework:"fastapi",label:"GET /items"}};
  const result=explainFile(file,[model,route],[],[],{key:"support",label:"Support"});
  assert.match(result.role,/SQLAlchemy model/);
  assert.match(result.role,/GET \/items/);
  assert.equal(result.grounding.role.classification,"fact");
  assert.ok(result.claims[1].evidence_refs.includes(model.id));
  assert.ok(result.claims[1].evidence_refs.includes(route.id));
  assert.doesNotMatch(result.role,/configuration|package setup/);
  assert.equal(result.grounding.rationale.confidence,0);
});
test("unclassified and unreadable files do not invent a purpose or architectural intent", () => {
  const unknown=explainFile(file,[],[],[],{key:"support",label:"Support"});
  assert.match(unknown.role,/not established/);
  assert.equal(unknown.grounding.role.confidence,0);
  const unreadable=explainFile({...file,source_error:"unreadable"},[],[],[],{key:"support",label:"Support"});
  assert.match(unreadable.summary,/could not be read/);
  const domain=explainFile(file,[],[],[],{key:"domain",label:"Domain"});
  assert.equal(domain.grounding.role.classification,"heuristic");
  assert.ok(domain.grounding.role.confidence < 0.9);
});
test("framework facts from another file cannot influence this file's explanation", () => {
  const result=explainFile(file,[{...model,path:"elsewhere.py"}],[],[],{key:"support",label:"Support"});
  assert.equal(result.grounding.role.confidence,0);
});
