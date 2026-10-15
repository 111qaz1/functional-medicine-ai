import { readFileSync } from "node:fs";
import vm from "node:vm";
import ts from "typescript";
import { expect, it } from "vitest";

// Execute the real component handler with synthetic network/DOM adapters.
const source=readFileSync(new URL("./integration-case-workbench.tsx",import.meta.url),"utf8");
const ast=ts.createSourceFile("workbench.tsx",source,ts.ScriptTarget.Latest,true,ts.ScriptKind.TSX);
let handler="";
function visit(node: ts.Node) {
  if(ts.isFunctionDeclaration(node)&&node.name?.text==="downloadReportFile") handler=node.getText(ast);
  ts.forEachChild(node,visit);
}
visit(ast);
const code=ts.transpileModule(handler,{compilerOptions:{target:ts.ScriptTarget.ES2022}}).outputText;

async function run(embedded=true, mismatched=false, readOnly=false) {
  const events:string[]=[];
  const context={embedded,readOnly,caseId:"case",embedContext:{external_encounter_id:"encounter",parent_origin:"https://partner.example"},
    postedRecommendationRevision:{current:""},setRecommendationSyncError:(error:unknown)=>{if(error)events.push("sync-error");},
    fetchApprovedRecommendationsWithRetry:async()=>{events.push("sync-request");return {version:2,case_id:mismatched?"other":"case",external_encounter_id:"encounter",draft_id:"draft",revision:1};},
    gateway:{downloadReport:async()=>{events.push("pdf-request");return {blob:{},filename:"report.pdf"};}},
    window:{parent:{postMessage:()=>events.push("sync-posted")},setTimeout:()=>0},
    URL:{createObjectURL:()=>"blob:synthetic",revokeObjectURL:()=>{}},
    document:{createElement:()=>({click:()=>{},remove:()=>{},href:"",download:""}),body:{appendChild:()=>{}}},
  };
  const fn=vm.runInNewContext(code+"\ndownloadReportFile",context);
  await fn("draft");return events;
}
it("posts approved recommendations before the PDF response can end protection",async()=>{
  expect(await run()).toEqual(["sync-request","sync-posted","pdf-request"]);
});
it("does not post another case's recommendation and still permits the report",async()=>{
  expect(await run(true,true)).toEqual(["sync-request","sync-error","pdf-request"]);
});
it("native internal reports do not call the embedded recommendation endpoint",async()=>{
  expect(await run(false)).toEqual(["pdf-request"]);
});

it("reopened approved cases download only, without replaying historical recommendations",async()=>{
  expect(await run(true,false,true)).toEqual(["pdf-request"]);
});

let syncEffect = "";
function findSyncEffect(node: ts.Node) {
  if (ts.isCallExpression(node) && node.expression.getText(ast) === "useEffect" &&
      node.arguments[0]?.getText(ast).includes("postedRecommendationRevision") &&
      node.arguments[0]?.getText(ast).includes("fetchApprovedRecommendationsWithRetry")) {
    syncEffect = node.arguments[0].getText(ast);
  }
  ts.forEachChild(node, findSyncEffect);
}
findSyncEffect(ast);
it("approved-history startup does not fetch or post historical recommendations", async () => {
  const events: string[] = [];
  const effectCode = ts.transpileModule("const effect = " + syncEffect,
    { compilerOptions: { target: ts.ScriptTarget.ES2022 } }).outputText;
  const context = { embedded: true, readOnly: true, embedContext: {external_encounter_id:"encounter"},
    draft: {id:"draft",status:"approved",revision:1},
    fetchApprovedRecommendationsWithRetry: () => { events.push("fetch"); throw new Error("unexpected sync"); },
    window: {parent: {postMessage: () => events.push("post")}} };
  const effect = vm.runInNewContext(effectCode + "\neffect", context);
  effect();
  await Promise.resolve();
  expect(events).toEqual([]);
});
