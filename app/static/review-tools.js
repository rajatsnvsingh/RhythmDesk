'use strict';
const uploadPanel=document.createElement('section');uploadPanel.className='panel settings-panel';
uploadPanel.innerHTML='<div class="panel-heading"><h2>Drop music into staging</h2></div><p>Drop files or folders here, or choose them below. Uploads copy your files; originals on your computer stay untouched. Nothing is scanned or published automatically.</p><div class="header-tools"><label class="secondary">Choose files<input id="upload-files" type="file" multiple></label><label class="secondary">Choose folder<input id="upload-folder" type="file" webkitdirectory multiple></label></div><progress id="upload-progress" max="100" value="0" hidden></progress><p id="upload-status" role="status">Ready · maximum 2 GiB per file, 50 GiB per batch.</p>';
$('incoming').querySelector('.section-heading').after(uploadPanel);
let uploadBusy=false;
async function uploadBatch(items){
  if(uploadBusy){toast('An upload is already running');return;}
  if(!items.length)return;
  uploadBusy=true;uploadPanel.querySelectorAll('input').forEach(n=>n.disabled=true);
  const batch=crypto.randomUUID?crypto.randomUUID().replaceAll('-',''):Array.from(crypto.getRandomValues(new Uint8Array(16)),x=>x.toString(16).padStart(2,'0')).join('');
  const total=items.reduce((n,x)=>n+x.file.size,0);let done=0;
  const progress=$('upload-progress'),status=$('upload-status');progress.hidden=false;
  try{
    if(items.length>10000||total>50*1024**3||items.some(x=>!x.file.size||x.file.size>2*1024**3))throw new Error('Batch exceeds upload limits or contains an empty file.');
    for(let i=0;i<items.length;i++){
      const item=items[i];status.textContent=`Uploading ${i+1}/${items.length}: ${item.path}`;
      await new Promise((resolve,reject)=>{const xhr=new XMLHttpRequest();xhr.open('POST','/api/uploads/file?'+new URLSearchParams({batch,path:item.path}));xhr.setRequestHeader('X-CSRF-Token',csrf);xhr.setRequestHeader('Content-Type','application/octet-stream');xhr.timeout=30*60*1000;
        xhr.upload.onprogress=e=>{progress.value=total?100*(done+e.loaded)/total:0;};
        xhr.onload=()=>{if(xhr.status>=200&&xhr.status<300)resolve();else{let message='Upload failed ('+xhr.status+')';try{message=JSON.parse(xhr.responseText).error||message;}catch{}reject(new Error(message));}};
        xhr.onerror=()=>reject(new Error('Connection interrupted'));xhr.ontimeout=()=>reject(new Error('Upload timed out'));xhr.send(item.file);
      });done+=item.file.size;
    }
    const result=await api('/api/uploads/complete',{batch,count:items.length});progress.value=100;status.textContent=`${result.files} files · ${bytes(total)} copied to Incoming/${result.folder}. Click Scan when ready.`;await refresh();
  }catch(e){status.textContent=`${e.message} Completed uploads remain in temporary staging, not Incoming. Re-select the batch to retry; staging purge also removes abandoned uploads.`;}
  finally{uploadBusy=false;uploadPanel.querySelectorAll('input').forEach(n=>{n.disabled=false;n.value='';});}
}
for(const id of ['upload-files','upload-folder'])$(id).onchange=e=>uploadBatch(Array.from(e.target.files,file=>({file,path:file.webkitRelativePath||file.name})));
async function droppedEntry(entry,prefix=''){
  if(entry.isFile)return new Promise((resolve,reject)=>entry.file(file=>resolve([{file,path:prefix+entry.name}]),reject));
  const reader=entry.createReader();let entries=[],chunk;
  do{chunk=await new Promise((resolve,reject)=>reader.readEntries(resolve,reject));entries.push(...chunk);}while(chunk.length);
  const result=[];for(const child of entries)result.push(...await droppedEntry(child,prefix+entry.name+'/'));return result;
}
uploadPanel.ondragover=e=>{e.preventDefault();e.dataTransfer.dropEffect='copy';};
uploadPanel.ondrop=async e=>{e.preventDefault();if(uploadBusy)return;const entries=Array.from(e.dataTransfer.items||[],x=>x.webkitGetAsEntry?.()).filter(Boolean);const files=Array.from(e.dataTransfer.files);try{const items=[];if(entries.length){for(const entry of entries)items.push(...await droppedEntry(entry));}else items.push(...files.map(file=>({file,path:file.name})));await uploadBatch(items);}catch(err){$('upload-status').textContent=err.message;}};
const scanButton=document.createElement('button');scanButton.className='primary';scanButton.textContent='Scan & prepare incoming';$('incoming').querySelector('.section-heading').append(scanButton);scanButton.onclick=async()=>{try{await api('/api/intake/scan',{});toast('Scan requested. You control when incoming is ready.');}catch(e){toast(e.message);}};
const stagingPanel=document.createElement('section');stagingPanel.className='panel settings-panel';stagingPanel.innerHTML='<div class="panel-heading"><h2>Staging storage</h2><button id="refresh-staging" class="secondary">Refresh count</button></div><p id="staging-count" class="muted">Not yet counted</p><p class="muted">Counts all staging files: Incoming, Curated, needs-review, artwork and working copies. Purge permanently deletes all of them, including unresolved music. Library and state/processed archives are untouched. Stop the worker container first.</p><button id="purge-staging" class="secondary">Purge everything in staging…</button>';$('settings').append(stagingPanel);
const purgeDialog=document.createElement('dialog');purgeDialog.id='purge-dialog';purgeDialog.innerHTML='<h2>Permanently purge staging?</h2><p>This deletes all incoming originals and unpublished working copies. There is no undo. Published music and state archives are not deleted.</p><p>The worker pauses automatically during purge.</p><form id="purge-form"><label class="check-label"><input name="confirmation" type="checkbox" required> I understand all staging files will be permanently deleted.</label><div class="dialog-actions"><button type="button" id="cancel-purge" class="secondary">Cancel</button><button class="primary" disabled>Permanently delete staging</button></div></form>';document.body.append(purgeDialog);
async function stagingCount(){try{const s=await api('/api/staging');$('staging-count').textContent=`${number(s.files)} files · ${bytes(s.bytes)} · ${s.path}`;}catch(e){toast(e.message);}}
settingsNav.addEventListener('click',stagingCount);$('refresh-staging').onclick=stagingCount;$('purge-staging').onclick=()=>{$('purge-form').reset();purgeDialog.showModal();};$('cancel-purge').onclick=()=>purgeDialog.close();
$('purge-form').elements.confirmation.onchange=e=>{$('purge-form').querySelector('button.primary').disabled=!e.target.checked;};
$('purge-staging').onclick=()=>{$('purge-form').reset();$('purge-form').querySelector('button.primary').disabled=true;purgeDialog.showModal();};
$('purge-form').onsubmit=async e=>{e.preventDefault();const button=e.target.querySelector('button.primary');button.disabled=true;try{const s=await api('/api/staging/purge',{confirmation:e.target.elements.confirmation.checked});purgeDialog.close();selected.clear();toast(`Permanently removed ${number(s.files)} staging files. Library and state archives retained.`);await stagingCount();await refresh();}catch(err){toast(err.message);}finally{button.disabled=!e.target.elements.confirmation.checked;}};
const inspector=$('review-dialog');
let backdropDown=false;
function outsideInspector(e){const r=inspector.getBoundingClientRect();return e.target===inspector&&(e.clientX<r.left||e.clientX>r.right||e.clientY<r.top||e.clientY>r.bottom);}
inspector.addEventListener('pointerdown',e=>{backdropDown=outsideInspector(e);});
inspector.addEventListener('click',e=>{if(backdropDown&&outsideInspector(e))inspector.close();backdropDown=false;});
// Media elements stay attached to one parent for their entire lifetime.
const inspectorPlayer=$('player').cloneNode(true);
inspectorPlayer.id='modal-player';
inspectorPlayer.querySelectorAll('[id]').forEach(node=>node.id='modal-'+node.id);
inspector.append(inspectorPlayer);
$('modal-close-player').onclick=()=>{const audio=$('modal-audio');audio.pause();audio.removeAttribute('src');audio.load();inspectorPlayer.hidden=true;};
inspector.addEventListener('close',()=>{$('modal-audio').pause();inspectorPlayer.hidden=true;});
const labelsInspector=inspect;
const obsoleteSettling=$('runtime-form').elements.stable_seconds?.closest('label');if(obsoleteSettling)obsoleteSettling.hidden=true;
const overviewScan=document.createElement('button');overviewScan.className='primary';overviewScan.textContent='Checking incoming…';overviewScan.disabled=true;$('overview').querySelector('.section-heading').append(overviewScan);overviewScan.onclick=()=>scanButton.click();
const detectionRender=render;
render=function(){detectionRender();const s=snapshot.intake||{};const text=s.scan_pending?'Scan queued':s.changed_files?(s.ready?`Scan & prepare · ${number(s.changed_files)} new/changed files`:`${number(s.changed_files)} changes · settling ${s.settling_seconds}s`):'No new files to scan';for(const button of [scanButton,overviewScan]){button.textContent=text;button.disabled=!s.ready||s.scan_pending;}};
stagingPanel.querySelectorAll('p')[1].textContent='Counts all staging files: Incoming, Curated, needs-review, artwork and working copies. Purge permanently deletes all of them, including unresolved music. Library and state archives are untouched. The worker pauses automatically and waits for its current job to finish. Stop SMB uploads and avoid editing or approving releases during purge.';
purgeDialog.querySelectorAll('p')[1].textContent='The worker pauses automatically. If a release is processing, purge waits for it to finish, then clears staging and resumes the worker. Your existing pause setting is preserved.';
$('purge-form').addEventListener('submit',()=>toast('Waiting for current worker activity, then purging staging. Keep this page open.'),true);
inspect=async function(jobId){await labelsInspector(jobId);if(detail?.job.id!==jobId||!detail.review)return;
 const editor=[...document.querySelectorAll('#review-body section')].find(el=>el.querySelector('h3')?.textContent==='Genres & category tags');
 let disclosure=null;
 if(editor){disclosure=document.createElement('details');disclosure.className='label-disclosure';disclosure.open=Object.values(detail.unknown_labels||{}).some(values=>values.length);const summary=document.createElement('summary');summary.className='review-subheading';summary.textContent='Genres & category tags'+(disclosure.open?' · Unapproved values':'');editor.before(disclosure);disclosure.append(summary,editor);editor.querySelector('h3').remove();}
 if(detail.job.status!=='Curated')return;
 const box=document.createElement('section');box.className='settings-panel';box.innerHTML='<h3>Allow or create a label</h3><p class="muted">Allowing adds a value to the global list. It does not publish this release. Creating allows a new label; enter it in the track fields above and save corrections to apply it.</p>';
 for(const [kind,values] of Object.entries(detail.unknown_labels||{}))for(const value of values){const button=document.createElement('button');button.className='secondary';button.textContent=`Allow ${kind==='genres'?'genre':'tag'}: ${value}`;button.onclick=()=>allowFromReview(kind,value,jobId,button);box.append(button);}
 const form=document.createElement('form');form.className='settings-actions';form.innerHTML='<select name="kind" aria-label="New label type"><option value="genres">Genre</option><option value="tags">Category tag</option></select><input name="value" aria-label="New allowed label" placeholder="New label" maxlength="100" required><button class="secondary">Create & allow</button>';
 form.onsubmit=e=>{e.preventDefault();allowFromReview(form.elements.kind.value,form.elements.value.value,jobId,form.querySelector('button'));};box.append(form);(disclosure||$('review-body')).append(box);
};
async function allowFromReview(kind,value,jobId,button){const dirty=[...document.querySelectorAll('#review-body input')].some(el=>el.value!==el.defaultValue);if(dirty){toast('Save your current corrections before changing the allow list.');return;}button.disabled=true;try{await api('/api/taxonomy/allow',{kind,value});toast('Label allowed. Publication still requires your approval.');await inspect(jobId);await refresh();}catch(e){button.disabled=false;toast(e.message);}}
const coversRender=render;
render=function(){coversRender();for(const row of document.querySelectorAll('#decision-list .decision-row')){const id=row.querySelector('[data-job]')?.dataset.job;const job=snapshot.jobs.find(j=>j.id===id);const square=row.querySelector('.album-square');if(job?.cover_url&&square)square.outerHTML=`<img class="queue-cover" src="${esc(job.cover_url)}" alt="${esc(job.album)} cover" loading="lazy">`;}
for(const table of ['jobs-table','curated-table'])for(const row of $(table).querySelectorAll('tbody tr')){const id=row.querySelector('[data-job]')?.dataset.job;const job=snapshot.jobs.find(j=>j.id===id);if(job?.cover_url)row.cells[0].insertAdjacentHTML('afterbegin',`<img class="queue-cover" src="${esc(job.cover_url)}" alt="${esc(job.album)} cover" loading="lazy">`);}};
