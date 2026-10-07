'use strict';
const releaseSelection=new Map();
let batchPlan=null,batchBusy=false;
const batchDialog=document.createElement('dialog');
batchDialog.id='release-batch-dialog';batchDialog.setAttribute('aria-labelledby','release-batch-title');
batchDialog.innerHTML='<h2 id="release-batch-title"></h2><div id="release-batch-summary"></div><label class="check-label"><input id="release-batch-check" type="checkbox"><span id="release-batch-consent"></span></label><p id="release-batch-progress" role="status"></p><div class="dialog-actions"><button id="release-batch-cancel" class="secondary">Cancel</button><button id="release-batch-confirm" disabled></button></div>';
document.body.append(batchDialog);
for(const [container,scope] of [[$('decision-list'),'home'],[$('jobs-table'),'review']]){
  const bar=document.createElement('div');bar.className='selection-bar release-batch-bar';bar.dataset.batchScope=scope;
  bar.innerHTML=`<label class="check-label"><input data-release-page="${scope}" type="checkbox"> Select visible</label><span data-release-count aria-live="polite">0 selected</span><button data-release-clear class="quiet">Clear</button><button data-release-batch="publish" class="primary" disabled>Publish ready…</button><button data-release-batch="delete" class="danger" disabled>Delete selected…</button>`;
  container.before(bar);
}
function idleRelease(job){return ['Needs review','Curated','Queued'].includes(job.status);}
function visibleReleases(scope){return (scope==='home'?snapshot.decisions:snapshot.jobs).filter(idleRelease);}
function renderBatchSelection(){
  for(const job of [...snapshot.jobs,...snapshot.decisions])if(releaseSelection.has(job.id)){if(idleRelease(job))releaseSelection.set(job.id,job);else releaseSelection.delete(job.id);}
  document.querySelectorAll('[data-release-select]').forEach(n=>{n.checked=releaseSelection.has(n.dataset.releaseSelect);n.disabled=batchBusy;});
  document.querySelectorAll('[data-batch-scope]').forEach(bar=>{
    const jobs=visibleReleases(bar.dataset.batchScope),page=bar.querySelector('[data-release-page]');
    page.checked=!!jobs.length&&jobs.every(j=>releaseSelection.has(j.id));page.indeterminate=jobs.some(j=>releaseSelection.has(j.id))&&!page.checked;page.disabled=batchBusy||!jobs.length;
    bar.querySelector('[data-release-count]').textContent=`${releaseSelection.size} selected across pages (max 50)`;
    bar.querySelectorAll('button').forEach(b=>b.disabled=batchBusy||!releaseSelection.size);
  });
}
function selectRelease(job,checked){if(checked){if(releaseSelection.size>=50&&!releaseSelection.has(job.id)){feedback('Select at most 50 releases per batch.');return;}releaseSelection.set(job.id,job);}else releaseSelection.delete(job.id);}
document.addEventListener('change',e=>{
  if(e.target.dataset.releaseSelect){const job=[...snapshot.jobs,...snapshot.decisions].find(j=>j.id===e.target.dataset.releaseSelect);if(job&&!batchBusy)selectRelease(job,e.target.checked);renderBatchSelection();}
  if(e.target.dataset.releasePage){if(!batchBusy)visibleReleases(e.target.dataset.releasePage).forEach(j=>selectRelease(j,e.target.checked));renderBatchSelection();}
});
document.addEventListener('click',async e=>{
  const b=e.target.closest('button');if(!b)return;
  if(b.hasAttribute('data-release-clear')&&!batchBusy){releaseSelection.clear();renderBatchSelection();}
  if(b.dataset.releaseBatch&&!batchBusy){
    batchBusy=true;renderBatchSelection();
    try{
      batchPlan=await api('/api/jobs/batch-preview',{action:b.dataset.releaseBatch,job_ids:[...releaseSelection.keys()]});
      const publishing=batchPlan.action==='publish';
      $('release-batch-title').textContent=publishing?'Approve selected reviewed revisions?':'Permanently delete selected staging releases?';
      $('release-batch-summary').innerHTML=`<p>${batchPlan.ready.length} eligible · ${batchPlan.blocked.length} blocked. Only the eligible releases below will be ${publishing?'queued for publication':'deleted'}. ${publishing?'Readiness does not certify correct metadata. No existing album will be replaced.':'Published library music and unrelated files stay untouched. Stop external transfers first.'}</p><ul>${batchPlan.ready.map(j=>`<li>${esc(j.artist||'')} ${esc(j.album||j.job_id)} — ${publishing?`${esc(j.destination)} · revision ${esc(j.revision)}`:`${number(j.files)} files · ${bytes(j.bytes)} · ${number(j.originals)} originals`}</li>`).join('')}</ul>${batchPlan.blocked.length?`<h3>Blocked / not included</h3><ul>${batchPlan.blocked.map(j=>`<li>${esc(releaseSelection.get(j.job_id)?.album||j.job_id)}: ${esc(j.reason)}</li>`).join('')}</ul>`:''}`;
      $('release-batch-check').checked=false;$('release-batch-check').disabled=!batchPlan.ready.length;
      $('release-batch-consent').textContent=publishing?'I reviewed the metadata, edition and artwork of every eligible release and approve these exact revisions.':'I understand this permanently deletes the listed staging copies and incoming originals. No undo.';
      $('release-batch-confirm').textContent=publishing?'Approve & publish eligible':'Permanently delete eligible';$('release-batch-confirm').className=publishing?'primary':'danger';$('release-batch-confirm').disabled=true;$('release-batch-cancel').textContent='Cancel';$('release-batch-progress').textContent='';batchDialog.showModal();
    }catch(err){feedback(err.message);}finally{batchBusy=false;renderBatchSelection();}
  }
});
$('release-batch-check').onchange=e=>$('release-batch-confirm').disabled=batchBusy||!e.target.checked||!batchPlan?.ready.length;
$('release-batch-cancel').onclick=()=>{if(!batchBusy)batchDialog.close();};
batchDialog.addEventListener('cancel',e=>{if(batchBusy)e.preventDefault();});
$('release-batch-confirm').onclick=async()=>{
  if(batchBusy||!batchPlan?.ready.length||!$('release-batch-check').checked)return;
  batchBusy=true;renderBatchSelection();$('release-batch-confirm').disabled=true;$('release-batch-cancel').disabled=true;$('release-batch-check').disabled=true;
  const plan=batchPlan,failures=[];let completed=0;
  try{
    for(const job of plan.ready){
      $('release-batch-progress').textContent=`${completed}/${plan.ready.length} ${plan.action==='publish'?'queued':'deleted'} · ${job.album||job.job_id}`;
      try{await api(`/api/jobs/${encodeURIComponent(job.job_id)}/${plan.action==='publish'?'approve':'delete'}`,plan.action==='publish'?{revision:job.revision}:{confirmation:true,token:job.token});releaseSelection.delete(job.job_id);completed++;}
      catch(err){failures.push(`${job.album||job.job_id}: ${err.message}`);}
    }
    // Never retry a stale plan: obtain a fresh preview and confirmation instead.
    batchPlan=null;$('release-batch-progress').textContent=`${completed} ${plan.action==='publish'?'queued; publication continues in the background':'deleted'}.${failures.length?' Not completed: '+failures.join(' · '):''}`;
    if(!failures.length){batchDialog.close();toast(`${completed} releases ${plan.action==='publish'?'queued for explicit publication':'deleted from staging'}.`);}
    storageAt=0;await refresh();
  }finally{batchBusy=false;$('release-batch-cancel').disabled=false;$('release-batch-cancel').textContent='Close';renderBatchSelection();}
};
