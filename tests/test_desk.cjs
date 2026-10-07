const {test}=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
const app=fs.readFileSync(path.join(__dirname,'../app/static/app.js'),'utf8');
const stable=app.slice(app.indexOf('const renderedHTML='),app.indexOf('function cover('));
function image(src){return {dataset:{artKey:'release'},alt:'Cover',getAttribute(){return src;},replaceWith(old){this.replacement=old;}};}
test('unchanged polling does not recreate cover DOM; changed neighbours reuse cover',()=>{
  let next=image('/cover?v=1'),replacements=0;const original=image('/cover?v=1');
  const element={querySelectorAll(){return [original];},replaceChildren(){replacements++;}};
  const template={content:{querySelectorAll(){return [next];}},set innerHTML(value){}};
  const context=vm.createContext({document:{createElement(){return template;}}});vm.runInContext(stable,context);
  context.stableHTML(element,'initial');assert.equal(next.replacement,original);
  context.stableHTML(element,'initial');assert.equal(replacements,1);
  next=image('/cover?v=1');context.stableHTML(element,'changed neighbour');assert.equal(next.replacement,original);
  next=image('/cover?v=2');context.stableHTML(element,'new revision');assert.equal(next.replacement,undefined);
});
const batch=fs.readFileSync(path.join(__dirname,'../app/static/batch.js'),'utf8');
function batchSetup(action,failId){
  const calls=[],selection=new Map([['one',{}],['two',{}]]),nodes={};
  const $=id=>nodes[id]||(nodes[id]={checked:true,disabled:false,textContent:''});
  const plan={action,ready:[{job_id:'one',album:'One',revision:'r1',token:'t1'},{job_id:'two',album:'Two',revision:'r2',token:'t2'}]};
  const context=vm.createContext({$,batchBusy:false,batchPlan:plan,releaseSelection:selection,storageAt:0,
    renderBatchSelection(){},toast(){},refresh:async()=>{},batchDialog:{close(){}},
    api:async(url,data)=>{calls.push({url,data});if(url.includes(failId||'NO_FAILURE'))throw Error('Revision changed');return {};}});
  vm.runInContext(batch.slice(batch.indexOf("$('release-batch-confirm').onclick=async()=>")),context);
  return {run:()=>$('release-batch-confirm').onclick(),calls,selection,context,nodes};
}
test('batch publish sends exact confirmed revisions and never automatically retries a failure',async()=>{
  const s=batchSetup('publish','two');await s.run();assert.equal(s.calls.length,2);
  assert.equal(s.calls[0].data.revision,'r1');assert.ok(s.calls[0].url.endsWith('/approve'));
  assert.equal(s.selection.has('one'),false);assert.equal(s.selection.has('two'),true);
  await s.run();assert.equal(s.calls.length,2);assert.match(s.nodes['release-batch-progress'].textContent,/Revision changed/);
});
test('batch delete sends checkbox confirmation and individual file tokens',async()=>{
  const s=batchSetup('delete');await s.run();assert.equal(s.calls[0].data.token,'t1');assert.equal(s.calls[0].data.confirmation,true);assert.equal(s.selection.size,0);
});
test('batch does nothing without explicit checkbox consent',async()=>{
  const s=batchSetup('publish');s.nodes['release-batch-check']={checked:false};await s.run();assert.equal(s.calls.length,0);
});
