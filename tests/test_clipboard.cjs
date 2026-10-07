const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const source=fs.readFileSync(require('node:path').join(__dirname,'../app/static/review-tools.js'),'utf8');
const code=source.slice(source.indexOf('async function copyMatchingLog('),source.indexOf('async function loadArtwork('));

function setup(clipboard,successful=true){
  const state={removed:false,restored:false,appended:false};
  const area={style:{},focus(){},select(){},setSelectionRange(){},remove(){state.removed=true;}};
  const context=vm.createContext({navigator:{clipboard},inspector:{append(node){state.appended=true;assert.equal(node,area);}},document:{activeElement:{isConnected:true,focus(){state.restored=true;}},createElement(){return area;},execCommand(command){assert.equal(command,'copy');state.copied=area.value;return successful;}}});
  vm.runInContext(code,context);
  return {copy:text=>context.copyMatchingLog(text),state};
}
test('secure clipboard copies without creating a fallback',async()=>{
  let copied;const {copy,state}=setup({writeText:async text=>copied=text});
  await copy('Matching log');assert.equal(copied,'Matching log');assert.equal(state.appended,false);
});
test('HTTP fallback copies inside modal and restores focus',async()=>{
  const {copy,state}=setup(undefined);await copy('Candidate\nDistance: 0.04');
  assert.equal(state.copied,'Candidate\nDistance: 0.04');assert.ok(state.appended&&state.removed&&state.restored);
});
test('clipboard rejection falls back to selection',async()=>{
  const {copy,state}=setup({writeText:async()=>{throw Error('Denied');}});await copy('Log');assert.equal(state.copied,'Log');
});
test('failed copying reports a useful error and cleans up',async()=>{
  const {copy,state}=setup(undefined,false);await assert.rejects(copy('Log'),/copy it manually/);assert.ok(state.removed&&state.restored);
});
