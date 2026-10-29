'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const crypto = require('node:crypto');
const {publish, classify, strictJSON, marker} = require('./mutation_alerts.cjs');
const policy = 'verdiclip-mutmut-3.8-full-v2';
function run(id=10, attempt=1) { return {id, workflow_id:123, run_attempt:attempt, run_number:id, head_sha:'a'.repeat(40), head_branch:'main', head_repository:{full_name:'mikejhill/verdiclip'}, status:'completed', conclusion:'success', event:'push', created_at:`2026-10-${String(id).padStart(2,'0')}T00:00:00Z`, html_url:`https://github.com/mikejhill/verdiclip/actions/runs/${id}`}; }
function evidence(r, killed=72, fresh=true, threshold=72) {return {schema:2,verdict_context:{schema:1,policy:'same-commit-os-arch-v1',source_sha:r.head_sha,os:'Linux',arch:'X64'}, repository:'mikejhill/verdiclip', source_sha:r.head_sha, run_id:r.id, run_attempt:r.run_attempt, policy, scope:'full', threshold, status:killed<threshold?'findings':'healthy', complete:true, fresh, historical_verdicts:fresh?0:5, counts:{killed,survived:100-killed}, total:100, detected:killed, undetected:100-killed, score:killed, outcomes:Object.fromEntries(['BASELINE_OUTCOME','PROBE_OUTCOME','MUTATION_OUTCOME','IDENTITY_OUTCOME','CACHE_CONTEXT_OUTCOME'].map(k=>[k,'success'])), comparable_key:crypto.createHash('sha256').update(`${policy}\nfull\n${threshold.toFixed(6)}`).digest('hex')}; }
function harness(seed=[]) {
 const issues=structuredClone(seed), writes=[], calls=[];
 const core={info:()=>{},summary:{addHeading(){return this;},addRaw(){return this;},async write(){}}};
 const github={rest:{actions:{getWorkflowRun:async()=>({data:current}),listWorkflowRuns:async()=>({data:{workflow_runs:[current]}})},issues:{
  listForRepo:async args=>{calls.push(args);return {data:issues.slice((args.page-1)*100,args.page*100)};},
  create:async args=>{writes.push({operation:'create',...args});issues.push({...args,number:issues.length+1,state:'open',user:{login:'github-actions[bot]'}});},
  update:async args=>{writes.push({operation:'update',...args});Object.assign(issues.find(i=>i.number===args.issue_number),args);}
 }}};
 let current;
 return {issues,writes,calls, github, async send(r,report=evidence(r)){current=r;await publish({github,core,report,context:{payload:{repository:{full_name:'mikejhill/verdiclip',default_branch:'main'},workflow_run:r}}});}};
}
for (const [score,status] of [[71,'findings'],[72,'healthy'],[73,'healthy']]) test(`threshold ${score}`,()=>assert.equal(classify(evidence(run(),score),run()).status,status));
test('above/equal survivors never create issues',async()=>{const h=harness();await h.send(run(),evidence(run()));await h.send(run(11),evidence(run(11),90));assert.equal(h.writes.length,0);});
test('findings create/update/recover/reopen without duplicates',async()=>{
 const h=harness();await h.send(run(),evidence(run(),60));assert.equal(h.issues.length,1);assert.deepEqual(h.writes[0].assignees,['mikejhill']);
 await h.send(run(11),evidence(run(11),65));assert.equal(h.issues.length,1);
 await h.send(run(12),evidence(run(12),72));assert.equal(h.issues[0].state,'closed');
 await h.send(run(13),evidence(run(13),60));assert.equal(h.issues[0].state,'open');assert.equal(h.issues.length,1);
});
test('incomplete/missing/cancelled have distinct tracker and cannot close findings',async()=>{
 const h=harness();await h.send(run(),evidence(run(),60));await h.send(run(11),null);
 assert.equal(h.issues.length,2);assert.equal(h.issues[0].state,'open');assert.ok(h.issues[1].body.includes(marker('execution')));
 const r=run(12);r.conclusion='cancelled';await h.send(r,evidence(r));assert.equal(h.issues[1].state,'open');
 await h.send(run(13),evidence(run(13),80));assert.equal(h.issues[0].state,'closed');assert.equal(h.issues[1].state,'closed');
});
test('incomplete newer evidence prevents delayed healthy recovery',async()=>{const h=harness();await h.send(run(),evidence(run(),60));await h.send(run(12),null);const n=h.writes.length;await h.send(run(11),evidence(run(11),90));assert.equal(h.writes.length,n);assert.equal(h.issues[0].state,'open');});
test('cached passing evidence and incomparable threshold preserve findings',async()=>{
 const h=harness();await h.send(run(),evidence(run(),60));await h.send(run(11),evidence(run(11),90,false));assert.equal(h.issues[0].state,'open');
 await h.send(run(12),evidence(run(12),90,true,71));assert.equal(h.issues[0].state,'open');
 await h.send(run(13),evidence(run(13),72));assert.equal(h.issues[0].state,'closed');
});
test('rerun retry finishes partially published update; stale attempt ignored',async()=>{
 const h=harness();await h.send(run(),evidence(run(),60));await h.send(run(),evidence(run(),60));assert.equal(h.issues.length,1);
 const r=run(10,2);await h.send(r,evidence(r));assert.equal(h.issues[0].state,'closed');const n=h.writes.length;await h.send(run(),evidence(run(),60));assert.equal(h.writes.length,n);
});
test('live newer attempt ignores stale completion event',async()=>{
 const h=harness();h.github.rest.actions.getWorkflowRun=async()=>({data:{...run(),run_attempt:2}});await h.send(run(),evidence(run(),60));assert.equal(h.writes.length,0);
});
test('pagination and unrelated/foreign-authored markers',async()=>{
 const seed=Array.from({length:100},(_,i)=>({number:i+1,user:{login:'outsider'},body:i===0?marker('findings'):'unrelated'}));
 const h=harness(seed);await h.send(run(),evidence(run(),60));assert.equal(h.calls.length,2);assert.equal(h.issues.length,101);
});
test('malformed managed state and duplicates fail without writes',async()=>{
 for(const seed of [[{number:1,user:{login:'github-actions[bot]'},body:marker('findings')}], [1,2].map(number=>({number,user:{login:'github-actions[bot]'},body:marker('findings')}))]) {
 const h=harness(seed);await assert.rejects(h.send(run(),evidence(run(),60)));assert.equal(h.writes.length,0);}
});
test('malformed/partial accounting cannot become a pass',()=>{
 for(const mutate of [r=>r.total++,r=>r.score=NaN,r=>r.counts.unknown=1,r=>r.outcomes.BASELINE_OUTCOME='failure',r=>r.source_sha='b'.repeat(40),r=>r.run_attempt++,r=>r.fresh=false,r=>r.policy='changed',r=>r.status='healthy']) {
 const r=run(), e=evidence(r,60);mutate(e);assert.equal(classify(e,r).status,'incomplete');}
});
test('below-threshold workflow failure remains complete findings',()=>{const r=run();r.conclusion='failure';assert.equal(classify(evidence(r,60),r,true).status,'findings');assert.equal(classify(evidence(r,60),r).status,'incomplete');assert.equal(classify(evidence(r,80),r).status,'incomplete');});
test('foreign/PR/non-default events never use API',async()=>{
 for(const mutate of [r=>r.head_branch='feature',r=>r.event='pull_request',r=>r.head_repository.full_name='other/repo',r=>r.status='in_progress']){const h=harness(),r=run();mutate(r);await h.send(r);assert.equal(h.calls.length,0);assert.equal(h.writes.length,0);}
});
test('strict parser rejects duplicate keys, trailing bytes, oversized/deep/malformed JSON',()=>{
 assert.deepEqual(strictJSON('{"a":[1,true,null,"text"]}'),{a:[1,true,null,'text']});
 for(const text of ['{"a":1,"a":2}','{"a":{"b":1,"b":2}}','{} true','[', ' '.repeat(1048577),'['.repeat(34)+'0'+']'.repeat(34)]) assert.throws(()=>strictJSON(text));
});

test('healthy-first history fences delayed old findings without healthy issue',async()=>{
 const h=harness();await h.send(run(12),evidence(run(12),90));assert.equal(h.issues.length,0);
 h.github.rest.actions.listWorkflowRuns=async()=>({data:{workflow_runs:[run(12),run(11)]}});
 await h.send(run(11),evidence(run(11),60));assert.equal(h.writes.length,0);
});
test('in-progress newer run does not erase completed findings',async()=>{
 const h=harness();h.github.rest.actions.listWorkflowRuns=async()=>({data:{workflow_runs:[{...run(12),status:'in_progress'},run(11)]}});
 await h.send(run(11),evidence(run(11),60));assert.equal(h.issues.length,1);
});

test('additional infrastructure failure is not hidden by a low score',async()=>{
 for (const extra of [false,true]) {
  const h=harness(),r=run();r.conclusion='failure';
  h.github.rest.actions.listJobsForWorkflowRunAttempt=async()=>({data:{total_count:1,jobs:[{conclusion:'failure',steps:[{name:'Report asynchronous score and findings',conclusion:'failure'},...(extra?[{name:'Save verdicts for later runs',conclusion:'failure'}]:[])]}]}});
  await h.send(r,evidence(r,60));assert.ok(h.issues[0].body.includes(marker(extra?'execution':'findings')));
 }
});
test('trusted workflow history pagination fences old initial healthy result',async()=>{
 const h=harness();let pages=0;
 h.github.rest.actions.listWorkflowRuns=async({page})=>{pages++;return {data:{workflow_runs:page===1?Array(100).fill(run(10)):[run(12)]}};};
 await h.send(run(11),evidence(run(11),60));assert.equal(pages,2);assert.equal(h.writes.length,0);
});
test('a second failed job without step evidence remains execution incomplete',async()=>{
 const h=harness(),r=run();r.conclusion='failure';
 h.github.rest.actions.listJobsForWorkflowRunAttempt=async()=>({data:{total_count:2,jobs:[{conclusion:'failure',steps:[{name:'Report asynchronous score and findings',conclusion:'failure'}]},{conclusion:'failure',steps:[]}]}});
 await h.send(r,evidence(r,60));assert.ok(h.issues[0].body.includes(marker('execution')));
});
test('publisher workflow retains reviewed security boundaries',()=>{
 const fs=require('node:fs'), text=fs.readFileSync('.github/workflows/mutation-alerts.yml','utf8');
 assert.match(text,/workflow_run: # zizmor: ignore\[dangerous-triggers\]/);
 assert.match(text,/head_repository.full_name == github.repository/);
 assert.match(text,/head_branch == github.event.repository.default_branch/);
 assert.match(text,/ref: \$\{\{ github.event.repository.default_branch \}\}/);
 assert.match(text,/persist-credentials: false/);
 assert.match(text,/MUTATION_EVIDENCE_DIR: \$\{\{ runner.temp \}\}/);
 assert.match(text,/path: \$\{\{ runner.temp \}\}/);
 assert.doesNotMatch(text,/ref:.*head_sha|id-token:|secrets\.|actions\/cache|run:.*mutation-evidence/);
 assert.match(text,/actions: read\s+contents: read\s+issues: write/);
});
test('legacy or cross-context evidence cannot recover execution or close findings',async()=>{
 for (const mutate of [e=>{e.schema=1;e.policy='verdiclip-mutmut-3.8-full-v1';delete e.verdict_context;e.fresh=false;e.historical_verdicts=5;},e=>delete e.verdict_context,e=>e.verdict_context.source_sha='b'.repeat(40),e=>e.verdict_context.os='Windows',e=>e.verdict_context.arch='ARM64',e=>e.outcomes.CACHE_CONTEXT_OUTCOME='failure']) {
  const h=harness();await h.send(run(),evidence(run(),60));await h.send(run(11),null);
  const r=run(12),e=evidence(r,90);mutate(e);await h.send(r,e);
  assert.equal(h.issues[0].state,'open');assert.equal(h.issues[1].state,'open');
  assert.ok(h.issues[1].body.includes('incomplete'));
 }
});
test('legacy cached pass creates execution alert rather than a current score finding',async()=>{
 const h=harness(),r=run(),e=evidence(r,90,false);e.schema=1;e.policy='verdiclip-mutmut-3.8-full-v1';delete e.verdict_context;
 await h.send(r,e);assert.equal(h.issues.length,1);assert.ok(h.issues[0].body.includes(marker('execution')));
 assert.ok(h.issues[0].body.includes('Score/completion not established'));
});
