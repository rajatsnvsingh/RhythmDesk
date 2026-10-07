/* One inspector, one explicit draft, one revision-producing save operation. */
'use strict';
let editor=null, inspectSequence=0, discardAction=null, approvalRevision=null, lastPublished=null, songRemoval=null;
function draftDirty(){return !!editor&&RhythmModel.dirty(editor.draft,editor.baseline);}
function guardDraft(action){
  if(editor?.busy){feedback('An operation is in progress. Wait for it to finish.','error',$('review-dialog'));return;}
  if(!draftDirty()){action();return;}
  discardAction=action;if(!$('discard-dialog').open)$('discard-dialog').showModal();
}
$('keep-editing').onclick=()=>{discardAction=null;$('discard-dialog').close();};
$('discard-edits').onclick=()=>{if(editor)editor.draft=RhythmModel.copy(editor.baseline);const action=discardAction;discardAction=null;$('discard-dialog').close();action?.();};
$('discard-dialog').addEventListener('cancel',()=>{discardAction=null;});
let backdropDown=false;
const inspector=$('review-dialog');
function outside(e){const r=inspector.getBoundingClientRect();return e.target===inspector&&(e.clientX<r.left||e.clientX>r.right||e.clientY<r.top||e.clientY>r.bottom);}
inspector.addEventListener('pointerdown',e=>{backdropDown=outside(e);});inspector.addEventListener('click',e=>{if(backdropDown&&outside(e))guardDraft(()=>inspector.close());backdropDown=false;});
inspector.addEventListener('cancel',e=>{e.preventDefault();guardDraft(()=>inspector.close());});
inspector.addEventListener('close',()=>{++inspectSequence;closePlayer('modal-');editor=null;detail=null;});
async function inspect(id,force=false){
  if(editor&&editor.id!==id&&!force){guardDraft(()=>inspect(id,true));return;}
  if(editor&&editor.id===id&&draftDirty()&&!force)return;
  const seq=++inspectSequence;
  try{
    const d=await api('/api/jobs/'+encodeURIComponent(id));if(seq!==inspectSequence)return;
    const scroll=editor?.id===id?$('review-body').scrollTop:0,old=editor;
    detail=d;const manual=!d.review&&d.job.mode==='manual';
    editor={id,draft:RhythmModel.draft(d,manual),baseline:null,manual,editing:manual,rows:new Set(),selected:new Set(),busy:false,stale:false};
    editor.baseline=RhythmModel.copy(editor.draft);
    if(old?.id===id){editor.editing=old.editing;editor.rows=old.rows;editor.selected=old.selected;}
    renderInspector();if(!inspector.open)inspector.showModal();$('review-body').scrollTop=scroll;return true;
  }catch(e){feedback(e.message);return false;}
}
async function pollInspector(){
  if(!inspector.open||!editor||editor.busy)return;
  const id=editor.id,d=await api('/api/jobs/'+encodeURIComponent(id));if(editor?.id!==id||editor.busy)return;
  const changed=d.job.status!==detail.job.status||d.job.revision!==detail.job.revision;
  if(changed&&draftDirty()){
    editor.stale=true;feedback('The server version changed. Your draft is preserved. Copy any edits you need, then discard/reload before saving or approving.','error',inspector);updateEditorState();
  }else if(changed){await inspect(id,true);}
  else{const changedPolicy=JSON.stringify(detail.taxonomy)!==JSON.stringify(d.taxonomy);detail.readiness=d.readiness;detail.taxonomy=d.taxonomy;detail.unknown_labels=d.unknown_labels;detail.job.progress=d.job.progress;detail.destination_exists=d.destination_exists;
    if($('inspector-progress'))$('inspector-progress').innerHTML=processingMarkup(d.job);if(changedPolicy)renderUnknown();updateEditorState();}
}
function field(key,value,label,type='text',i=null){return `<span class="field-value">${esc(value||'—')}</span><input class="edit-field" ${i===null?`data-album="${key}"`:`data-field="${key}" data-index="${i}"`} aria-label="${esc(label)}" type="${type}" value="${esc(value)}" ${['disc','track'].includes(key)?'min="1"':''} ${key==='year'?'min="1000" max="9999"':''} ${['artist','title','album','year','disc','track'].includes(key)?'required':''} ${editable()?'':'disabled'}>`;}
function editable(){return (detail.job.status==='Curated'&&!!detail.review)||(detail.job.status==='Needs review'&&editor.manual);}
function labelEditor(t,i){
  return `<div class="row-label-editor edit-field"><div class="editable-chips">${['genres','tags'].map(kind=>`<div><small>${kind==='genres'?'Genres':'Category tags'}</small> ${t[kind].map(value=>`<button class="chip" data-chip-remove="${i}" data-kind="${kind}" data-value="${esc(value)}" aria-label="Remove ${esc(value)} from ${esc(t.title)}">${esc(value)} ×</button>`).join(' ')||'<span class="muted">—</span>'}</div>`).join('')}</div><div class="label-add"><select data-label-kind="${i}" aria-label="Label type for ${esc(t.title)}"><option value="genres">Genre</option><option value="tags">Category tag</option></select><input data-label-value="${i}" list="allowed-genres" placeholder="Search / enter label" aria-label="Add label to ${esc(t.title)}"><button class="quiet" data-chip-add="${i}">Add</button></div><input data-field="genres" data-index="${i}" aria-label="Genres for ${esc(t.title)}" value="${esc(t.genres.join('; '))}" placeholder="Expert: genres separated by semicolons"><input data-field="tags" data-index="${i}" aria-label="Category tags for ${esc(t.title)}" value="${esc(t.tags.join('; '))}" placeholder="Expert: category tags"></div>`;
}
function trackRow(t,i,media,allowEdit){
  const evidence=media[i]||{};
  return `<tr class="review-track ${editor.rows.has(i)?'row-editing':''}" data-row="${i}">
    <td class="review-position">${allowEdit?`<label class="hit-target"><input type="checkbox" data-review-select="${i}" aria-label="Select ${esc(t.title)} for batch edit" ${editor.selected.has(i)?'checked':''}></label>`:''}<span class="read-position">${t.disc}.${t.track}</span><div class="position-inputs">${field('disc',t.disc,'Disc for '+t.title,'number',i)}${field('track',t.track,'Track number for '+t.title,'number',i)}</div></td>
    <td class="review-name">${field('title',t.title,'Title for track '+(i+1),'text',i)}<small class="source-path">${esc((evidence.file||evidence.path||'').split('/').pop())}</small>${t.match_status==='unverified'?`<div class="unverified-track"><strong>Unverified · original track metadata</strong><small>${esc(evidence.match_note)}</small>${allowEdit?`<label class="check-label"><input type="checkbox" data-track-reviewed="${i}" ${t.reviewed?'checked':''}> I checked this song’s title, artist and disc/track position</label>`:''}</div>`:t.match_status?`<small>${t.match_status==='catalogue'?'Catalogue mapped':'User-confirmed metadata'}</small>`:''}</td>
    <td class="review-artist">${field('artist',t.artist,'Artist for '+t.title,'text',i)}</td>
    <td class="review-quality"><span>${duration(evidence.seconds)}</span><small>${esc(encoding(evidence))} · Art ${evidence.artwork?'✓':'missing'}</small></td>
    <td class="review-labels"><div class="field-value"><span class="label-prefix">Genres</span> ${chips(t.genres)}<br><span class="label-prefix">Tags</span> ${chips(t.tags)}</div>${allowEdit?labelEditor(t,i):''}</td>
    <td class="review-controls"><button class="quiet" data-preview="${i}" aria-label="Play ${esc(t.title)}">▶</button>${allowEdit?`<button class="quiet" data-edit-row="${i}" aria-label="Edit ${esc(t.title)}">Edit</button>`:''}${detail.job.status==='Curated'?`<button class="quiet" data-remove-song="${i}" aria-label="Remove ${esc(t.title)} from prepared album">Remove</button>`:''}</td></tr>`;
}
function renderInspector(){
  const d=detail,j=d.job,tracks=editor.draft.tracks,media=d.review?.tracks||d.sources,allowEdit=editable();
  $('review-title').textContent=editor.draft.album||'Untitled release';$('review-feedback').hidden=true;
  const first=d.review?.tracks.find(t=>t.artwork),source=d.sources.find(t=>t.artwork);
  const art=first?mediaUrl(j.id,first.file,true):source?'/api/media?'+new URLSearchParams({track:source.id,art:1}):'';
  $('review-body').classList.toggle('editing',editor.editing);
  $('review-body').innerHTML=`<div class="review-top">${art?`<img class="cover" src="${esc(art)}" alt="${esc(j.album)} artwork">`:'<div class="cover album-square art-placeholder" role="img" aria-label="No cover preview">NO ART</div>'}<div class="review-meta"><p><strong id="draft-artist">${esc(editor.draft.artist)}</strong> · <span id="draft-year">${editor.draft.year||'Year unknown'}</span> · ${j.track_count} tracks ${badge(j.status)}</p><p>${esc(d.identity.provenance)} · ${esc(j.mode)} · ${esc(d.identity.completeness)}</p>${d.identity.release_ids.map(id=>`<a href="https://musicbrainz.org/release/${id}" target="_blank" rel="noopener noreferrer">Inspect selected MusicBrainz edition ↗</a>`).join(' ')}${d.review?`<p>Destination: <code>rhythm-attic/${esc(d.review.destination)}</code></p>`:''}<p class="muted">Originals retained · No automatic publication</p></div></div>
    <div id="readiness" class="readiness" aria-live="polite"></div>
    ${d.recovery?`<section class="recovery"><h3>${esc(d.recovery.title)}</h3><p>${esc(d.recovery.evidence)}</p><p>${esc(d.recovery.action)}</p></section>`:''}
    ${(j.reason||'').startsWith('Publication failed:')?`<section class="recovery"><h3>Publication failed</h3><p>${esc(j.reason)}</p><p>The Curated copy is retained. Resolve the error and review publication again; existing destinations are never replaced.</p></section>`:''}
    ${['Queued','Processing','Editing','Publishing'].includes(j.status)?`<section id="inspector-progress">${processingMarkup(j)}</section>`:''}
    ${allowEdit?`<div class="editor-heading"><h3>Release metadata & artwork</h3><button id="show-artwork" class="secondary">Replace artwork</button><button id="toggle-edit" class="secondary">${editor.editing?'Read compact view':'Edit metadata'}</button></div>`:''}
    <div class="album-fields"><label>Album artist${field('artist',editor.draft.artist,'Album artist')}</label><label>Album title${field('album',editor.draft.album,'Album title')}</label><label>Year${field('year',editor.draft.year,'Release year','number')}</label></div>
    <div class="label-summary"><span>Genres: <span id="album-genres">${chips(RhythmModel.labels(tracks.flatMap(t=>t.genres)))}</span></span><span>Category tags: <span id="album-tags">${chips(RhythmModel.labels(tracks.flatMap(t=>t.tags)))}</span></span></div>
    <p class="art-coverage compact-note">Artwork: ${number(media.filter(t=>t.artwork).length)} / ${tracks.length} present${d.sources.some(t=>t.artwork_invalid)?` · ${d.sources.filter(t=>t.artwork_invalid).length} unreadable original cover(s)`:''}. ${d.review?'Output image presence, not a validity certificate.':''}</p>
    ${allowEdit?`<div class="artwork-tools" hidden><label class="secondary">Choose replacement cover<input id="art-file" type="file" accept="image/*"></label><div id="art-paste" tabindex="0" role="textbox" aria-label="Paste album artwork">Paste a copied image here</div><button id="art-clear" class="quiet" disabled>Clear replacement</button><img id="art-preview" class="queue-cover" alt="Replacement cover" hidden><small id="art-status">Replacements apply to every output track when saved.</small></div>`:''}
    <section class="labels-section"><div class="editor-heading"><h3>Genres & category tags</h3>${allowEdit?'<button id="show-label-create" class="text-button">Create allowed label</button>':''}</div><div id="unknown-labels"></div>${allowEdit?`<div class="label-create" hidden><label>Label type<select id="new-label-kind"><option value="genres">Genre</option><option value="tags">Category tag</option></select></label><label>Create an allowed label<input id="new-label-value" maxlength="100" placeholder="New allowed label"></label><button id="create-label" class="secondary">Create & allow globally</button></div><datalist id="allowed-genres">${d.taxonomy.genres.map(v=>`<option value="${esc(v)}"></option>`).join('')}</datalist><datalist id="allowed-tags">${d.taxonomy.tags.map(v=>`<option value="${esc(v)}"></option>`).join('')}</datalist>`:''}</section>
    ${allowEdit?`<div class="batch-bar"><label class="check-label"><input id="review-select-all" type="checkbox"> Select all ${tracks.length} tracks</label><span id="review-selection">${editor.selected.size} selected</span><div id="batch-controls" ${editor.selected.size?'':'hidden'}><label>Batch field<select id="batch-field"><option value="genres">Genres</option><option value="tags">Category tags</option><option value="artist">Track artist</option></select></label><label>Operation<select id="batch-operation"><option value="replace">Replace field</option><option value="add">Add labels · keep existing</option></select></label><label>Value<input id="batch-value" list="allowed-genres" placeholder="Search label / semicolon values / artist"></label><button id="apply-batch" class="secondary">Apply to selected draft</button><small>Only the selected field on selected tracks changes. Results appear in the rows; Save applies them to working copies.</small></div></div>`:''}
    ${j.status==='Curated'?'<button id="remove-selected-songs" class="secondary">Remove selected songs…</button>':''}
    ${d.review?.excluded_tracks?.length?`<p class="compact-note">Removed from this prepared album: ${d.review.excluded_tracks.map(t=>esc(t.title)).join(' · ')}. Incoming originals retained.</p>`:''}
    <div class="table-scroll review-table-wrap"><table class="review-table"><thead><tr><th>Select / position</th><th>Title / filename</th><th>Track artist</th><th>Length / encoding / artwork</th><th>Genres / category tags</th><th>Preview / edit / remove</th></tr></thead><tbody>${tracks.map((t,i)=>trackRow(t,i,media,allowEdit)).join('')}</tbody></table></div>
    ${j.status==='Needs review'&&d.candidates?.length?`<section class="candidate-picker"><h3>Review catalogue candidates</h3><p>Lower distance is closer. Suggestions are not verified editions. Partial mode allows catalogue tracks you do not own, but never silently drops a submitted track.</p>${d.candidates.map((c,i)=>candidateCard(c,i,j.mode)).join('')}</section>`:''}
    ${j.status==='Needs review'?`<section class="retry-tools"><h3>Catalogue recovery</h3><label>Retry mode<select id="retry-mode"><option value="album">Complete album</option><option value="single" ${j.track_count===1?'':'disabled'}>Single track</option><option value="partial">Partial album</option></select></label><label>Exact MusicBrainz release UUID (optional)<input id="retry-release" value="${esc(j.release_id||'')}" placeholder="Choose the intended edition on MusicBrainz"></label><a target="_blank" rel="noopener noreferrer" href="https://musicbrainz.org/search?${esc(new URLSearchParams({query:j.artist+' '+j.album,type:'release',method:'indexed'}))}">Search MusicBrainz releases ↗</a><button id="retry-job" class="secondary">Retry matching</button><button id="force-artwork" class="secondary">Retry & fetch new artwork</button><button id="manual-mode" class="secondary">${editor.manual?'Manual editor active':'Use manual metadata instead'}</button><p class="muted">Retry keeps the inspector open. Exact editions still need your verification. Matching thresholds are unchanged.</p></section>`:''}
    ${['Curated','Needs review','Queued'].includes(j.status)?'<button id="ungroup-job" class="quiet">Return tracks to Incoming for regrouping…</button>':''}
    ${['Curated','Needs review','Queued'].includes(j.status)?'<button id="delete-staged-release" class="quiet">Delete release from staging…</button>':''}
    <details><summary>Source tracks & original artwork (${d.sources.length})</summary><div class="source-evidence">${d.sources.map(s=>`<div>${s.artwork?`<img class="queue-cover" src="/api/media?${new URLSearchParams({track:s.id,art:1})}" alt="Source artwork for ${esc(s.title)}" loading="lazy">`:''}<strong>${esc(s.title)}</strong><small>${esc(s.path)} · ${duration(s.seconds)} · ${esc(s.codec)}${s.artwork_invalid?' · Unreadable original artwork (not usable)':''}</small></div>`).join('')}</div></details>
    <details><summary>Matching log & technical record</summary><div class="editor-heading"><p>Job: ${esc(j.id)} · Revision: ${esc(d.review?.revision||'No reviewed revision')}</p><button id="copy-matching-log" class="secondary" ${d.log?'':'disabled'}>Copy log</button></div>${d.log?`<pre class="review-log">${esc(d.log.replace(/\x1b\[[0-9;]*m/g,''))}</pre>`:'<p>No matching log yet.</p>'}</details>`;
  if($('retry-mode'))$('retry-mode').value=j.mode==='manual'?'album':j.mode;
  renderUnknown();updateEditorState();
}
function localBlockers(){
  const blockers=[...(detail.readiness?.blockers||[])].filter(b=>!['genres','tags'].includes(b.code));
  const unknown=RhythmModel.unknown(editor.draft,detail.taxonomy);if(unknown.length)blockers.push({code:'labels',message:unknown.map(v=>`${v.kind}: ${v.value}`).join(' · ')});
  if(draftDirty())blockers.unshift({code:'draft',message:'Unsaved changes. Save and inspect the new revision before approval.'});
  if(editor.stale)blockers.unshift({code:'stale',message:'Server revision changed. Reload before continuing.'});
  if(editor.busy)blockers.unshift({code:'busy',message:'Operation in progress.'});
  return blockers;
}
function updateEditorState(){
  if(!editor||!detail)return;
  // A save snapshots the draft. Lock controls until it finishes rather than
  // accepting keystrokes that the input handler must ignore while busy.
  for(const control of $('review-body').querySelectorAll('input,select,button')){
    if(editor.busy){if(!control.hasAttribute('data-busy-disabled'))control.dataset.busyDisabled=String(control.disabled);control.disabled=true;}
    else if(control.hasAttribute('data-busy-disabled')){control.disabled=control.dataset.busyDisabled==='true';delete control.dataset.busyDisabled;}
  }
  const blockers=localBlockers(),canPublish=!blockers.length&&!!detail.review&&detail.job.status==='Curated';
  // Presentation follows the existing blockers; colour never grants authority.
  $('readiness').dataset.state=detail.job.status==='Approved'?'published':draftDirty()?'draft':blockers.length?'blocked':'ready';
  $('readiness').innerHTML=detail.job.status==='Approved'?'<strong>Published revision · read-only history</strong><p>This release was explicitly approved. This view cannot edit or republish the library. Labels are compared with the current policy for reference.</p>':`<strong>${draftDirty()?'Unsaved draft':detail.readiness.can_review?'Prepared for your review':'Not ready for publication'}</strong>${blockers.length?`<ul>${blockers.map(b=>`<li>${esc(b.message)}</li>`).join('')}</ul>`:'<p>All preparation checks pass. You still need to verify metadata, edition and artwork, then explicitly approve.</p>'}`;
  const oldFocus=document.activeElement?.id;
  $('review-actions').innerHTML=`<span id="draft-state" class="muted">${editor.busy?'Working…':editor.stale?'Draft preserved · server changed':draftDirty()?'Unsaved changes':'Saved / unchanged'}</span>${editable()?`<button id="save-changes" class="${draftDirty()||editor.manual?'primary':'secondary'}" ${editor.busy||editor.stale||(!draftDirty()&&!editor.manual)?'disabled':''}>${editor.manual?'Prepare manual copy':'Save changes'}</button>`:''}${detail.job.status==='Curated'?`<button id="request-approval" class="primary" ${canPublish?'':'disabled'}>Review publication →</button>`:detail.job.status==='Approved'?'<button id="review-next" class="primary">Review next →</button>':''}<button data-close="review-dialog" class="secondary">Close</button>`;
  $('draft-state').dataset.state=editor.stale?'stale':draftDirty()?'dirty':'saved';
  if(oldFocus&&$(oldFocus))$(oldFocus).focus({preventScroll:true});
  $('album-genres').innerHTML=chips(RhythmModel.labels(editor.draft.tracks.flatMap(t=>t.genres)));$('album-tags').innerHTML=chips(RhythmModel.labels(editor.draft.tracks.flatMap(t=>t.tags)));
}
function renderUnknown(){if(!editor||!$('unknown-labels'))return;const unknown=RhythmModel.unknown(editor.draft,detail.taxonomy);
  $('unknown-labels').innerHTML=unknown.length?unknown.map((v,i)=>`<div class="unknown-label"><span><strong>${esc(v.value)}</strong> · ${v.kind==='genres'?'Genre':'Category tag'} · ${v.count} affected tracks</span>${editable()?`<button class="secondary" data-allow="${i}">Allow globally</button><label>Map to allowed value<select data-map-value="${i}"><option value="">Choose ${v.kind==='genres'?'genre':'tag'}</option>${detail.taxonomy[v.kind].map(x=>`<option>${esc(x)}</option>`).join('')}</select></label><button class="secondary" data-map="${i}">Map in draft</button><button class="quiet" data-remove="${i}">Remove from draft</button>`:''}</div>`).join(''):'<p class="compact-note">All present genres and category tags are allowed. Empty values are permitted.</p>';
}
function syncReadValues(){editor.draft.tracks.forEach((t,i)=>{const row=document.querySelector(`[data-row="${i}"]`);if(!row)return;for(const input of row.querySelectorAll('[data-field]')){input.value=['genres','tags'].includes(input.dataset.field)?t[input.dataset.field].join('; '):t[input.dataset.field];}row.querySelector('.read-position').textContent=`${t.disc}.${t.track}`;row.querySelector('.review-name .field-value').textContent=t.title;row.querySelector('.review-artist .field-value').textContent=t.artist;row.querySelector('.review-labels .field-value').innerHTML=`<span class="label-prefix">Genres</span> ${chips(t.genres)}<br><span class="label-prefix">Tags</span> ${chips(t.tags)}`;const labels=row.querySelector('.row-label-editor');if(labels)labels.outerHTML=labelEditor(t,i);});renderUnknown();updateEditorState();}
inspector.addEventListener('input',e=>{
  const n=e.target;if(!editor||editor.busy)return;
  if(n.dataset.album){editor.draft[n.dataset.album]=n.type==='number'?Number(n.value)||'':n.value;n.previousElementSibling.textContent=n.value||'—';$('draft-artist').textContent=editor.draft.artist;$('draft-year').textContent=editor.draft.year||'Year unknown';$('review-title').textContent=editor.draft.album;}
  if(n.dataset.field){const key=n.dataset.field;editor.draft.tracks[Number(n.dataset.index)][key]=['genres','tags'].includes(key)?RhythmModel.labels(n.value):['disc','track'].includes(key)?Number(n.value):n.value;}
  if(n.dataset.album||n.dataset.field){renderUnknown();updateEditorState();}
});
inspector.addEventListener('change',e=>{
  if(e.target.dataset.trackReviewed!==undefined){editor.draft.tracks[Number(e.target.dataset.trackReviewed)].reviewed=e.target.checked;updateEditorState();}
  if(e.target.dataset.reviewSelect!==undefined){const i=Number(e.target.dataset.reviewSelect);e.target.checked?editor.selected.add(i):editor.selected.delete(i);$('review-selection').textContent=`${editor.selected.size} selected`;$('batch-controls').hidden=!editor.selected.size;}
  if(e.target.dataset.labelKind!==undefined){const i=e.target.dataset.labelKind;inspector.querySelector(`[data-label-value="${i}"]`).setAttribute('list','allowed-'+e.target.value);}
  if(e.target.id==='batch-field'){const kind=e.target.value;$('batch-value').setAttribute('list','allowed-'+kind);$('batch-operation').querySelector('[value=add]').disabled=kind==='artist';if(kind==='artist')$('batch-operation').value='replace';}
});
inspector.addEventListener('click',async e=>{
  const b=e.target.closest('button');if(!b||!editor)return;
  if(b.id==='show-artwork'){$('review-body').querySelector('.artwork-tools').hidden=false;$('art-file').focus({preventScroll:true});}
  if(b.id==='show-label-create'){$('review-body').querySelector('.label-create').hidden=false;$('new-label-value').focus({preventScroll:true});}
  if(b.dataset.editRow!==undefined){const i=Number(b.dataset.editRow);editor.rows.has(i)?editor.rows.delete(i):editor.rows.add(i);document.querySelector(`[data-row="${i}"]`).classList.toggle('row-editing',editor.rows.has(i));if(!editor.rows.has(i))syncReadValues();}
  if(b.dataset.removeSong!==undefined||b.id==='remove-selected-songs'){
    const indexes=b.dataset.removeSong!==undefined?[Number(b.dataset.removeSong)]:[...editor.selected];
    guardDraft(()=>confirmSongRemoval(indexes));
  }
  if(b.id==='cancel-remove-songs'){songRemoval=null;$('review-feedback').hidden=true;}
  if(b.id==='confirm-remove-songs'&&$('remove-songs-check')?.checked&&songRemoval){
    const request=songRemoval;
    guardDraft(async()=>{songRemoval=null;closePlayer('modal-');editor.selected=new Set();editor.rows=new Set();
      await runJobAction('remove-tracks',{revision:request.revision,files:request.files,confirmation:true});});
  }
  if(b.dataset.preview!==undefined){const i=Number(b.dataset.preview),t=detail.review?.tracks[i],s=detail.sources[i];if(t)play(mediaUrl(editor.id,t.file),t.title,t.artist);else if(s)play('/api/media?track='+encodeURIComponent(s.id),s.title,s.artist);}
  if(b.id==='toggle-edit'){editor.editing=!editor.editing;$('review-body').classList.toggle('editing',editor.editing);b.textContent=editor.editing?'Read compact view':'Edit metadata';if(!editor.editing)syncReadValues();}
  if(b.id==='apply-batch'){if(!editor.selected.size){feedback('Select tracks before applying a batch change.');return;}RhythmModel.batch(editor.draft,[...editor.selected],$('batch-field').value,$('batch-value').value,$('batch-operation').value);syncReadValues();toast(`Draft updated: ${editor.selected.size} selected tracks. Save to apply to audio.`);}
  if(b.dataset.chipAdd!==undefined){const i=Number(b.dataset.chipAdd),kind=inspector.querySelector(`[data-label-kind="${i}"]`).value,value=inspector.querySelector(`[data-label-value="${i}"]`).value;if(!value.trim()){feedback('Choose or enter a label first.');return;}RhythmModel.batch(editor.draft,[i],kind,value,'add');syncReadValues();}
  if(b.dataset.chipRemove!==undefined){const i=Number(b.dataset.chipRemove);editor.draft.tracks[i][b.dataset.kind]=editor.draft.tracks[i][b.dataset.kind].filter(v=>v.toLocaleLowerCase()!==b.dataset.value.toLocaleLowerCase());syncReadValues();}
  if(b.dataset.allow!==undefined||b.id==='create-label'){
    const item=b.id==='create-label'?{kind:$('new-label-kind').value,value:$('new-label-value').value}:RhythmModel.unknown(editor.draft,detail.taxonomy)[Number(b.dataset.allow)];
    b.disabled=true;try{detail.taxonomy=await api('/api/taxonomy/allow',item);if(b.id==='create-label')$('new-label-value').value='';renderUnknown();updateEditorState();toast('Allowed globally. Your draft is preserved; no audio or publication changed.');await refresh();}catch(err){feedback(err.message);}finally{b.disabled=false;}
  }
  if(b.dataset.map!==undefined||b.dataset.remove!==undefined){const index=Number(b.dataset.map??b.dataset.remove),v=RhythmModel.unknown(editor.draft,detail.taxonomy)[index],replacement=b.dataset.map!==undefined?document.querySelector(`[data-map-value="${index}"]`).value:'';if(b.dataset.map!==undefined&&!replacement){feedback('Choose an allowed value before mapping.');return;}RhythmModel.replaceLabel(editor.draft,v.kind,v.value,replacement);syncReadValues();toast(`Draft ${replacement?'mapped':'removed'} ${v.value} on ${v.count} tracks. Save to apply.`);}
  if(b.id==='save-changes')await saveDraft();
  if(b.id==='manual-mode'&&!editor.manual)guardDraft(()=>{editor.manual=true;editor.editing=true;editor.draft=RhythmModel.draft(detail,true);editor.baseline=RhythmModel.copy(editor.draft);renderInspector();});
  if(['retry-job','force-artwork'].includes(b.id))guardDraft(()=>runJobAction('retry',{mode:$('retry-mode').value,release_id:$('retry-release').value.trim(),force_artwork:b.id==='force-artwork'}));
  if(b.id==='copy-matching-log'){try{await copyMatchingLog(detail.log.replace(/\x1b\[[0-9;]*m/g,''));toast('Matching log copied.');}catch(err){feedback(err.message);}}
  if(b.dataset.chooseCandidate!==undefined){const index=Number(b.dataset.chooseCandidate),c=detail.candidates[index],checked=inspector.querySelector(`[data-candidate-check="${index}"]`),mode=inspector.querySelector(`[data-candidate-mode="${index}"]`).value;if(c&&checked?.checked)guardDraft(()=>runJobAction('retry',{mode,release_id:c.release_id,selected_candidate:c.release_id}));}
  if(b.id==='ungroup-job')guardDraft(()=>{const box=$('review-feedback');box.innerHTML='Return every source track to Incoming? Working copies remain retained. <button id="confirm-regroup" class="secondary">Confirm regroup</button>';box.hidden=false;});
  if(b.id==='confirm-regroup')await runJobAction('ungroup',{});
  if(b.id==='delete-staged-release')guardDraft(async()=>{
    const id=editor.id;editor.busy=true;updateEditorState();
    try{const plan=await api(`/api/jobs/${encodeURIComponent(id)}/delete-preview`,{});if(editor?.id!==id)return;
      const box=$('review-feedback');box.innerHTML=`<strong>Permanently delete ${esc(plan.album)} from staging?</strong><p>${number(plan.files)} files · ${bytes(plan.bytes)}, including ${number(plan.originals)} Incoming originals and this release’s working copies. Unrelated files and published music are not removed. This cannot be undone.</p><label class="check-label"><input id="delete-release-check" type="checkbox"> I understand these staging files will be permanently deleted</label><button id="confirm-delete-release" class="secondary" data-delete-token="${esc(plan.token)}" disabled>Delete from staging</button><button id="cancel-delete-release" class="quiet">Cancel</button>`;box.hidden=false;box.scrollIntoView({block:'nearest'});
    }catch(err){feedback(err.message);}finally{if(editor?.id===id){editor.busy=false;updateEditorState();}}
  });
  if(b.id==='cancel-delete-release')$('review-feedback').hidden=true;
  if(b.id==='confirm-delete-release'&&$('delete-release-check')?.checked){closePlayer('modal-');await runJobAction('delete',{confirmation:true,token:b.dataset.deleteToken});}
  if(b.id==='request-approval'){if(localBlockers().length){feedback('Resolve the readiness checklist before approval.');return;}approvalRevision=detail.review.revision;$('approval-summary').textContent=`${detail.review.destination} · ${detail.review.tracks.length} tracks · Exact revision ${approvalRevision.slice(0,16)}`;$('approval-check').checked=false;$('confirm-approve').disabled=true;$('approve-dialog').querySelector('.dialog-feedback').hidden=true;$('approve-dialog').showModal();}
  if(b.id==='review-next'){const next=snapshot.decisions.find(j=>j.id!==editor.id);if(next)inspect(next.id);else{inspector.close();reviewView('all');}}
});
inspector.addEventListener('change',e=>{if(e.target.id==='remove-songs-check')$('confirm-remove-songs').disabled=!e.target.checked;if(e.target.id==='delete-release-check')$('confirm-delete-release').disabled=!e.target.checked;if(e.target.dataset.candidateCheck!==undefined){inspector.querySelector(`[data-choose-candidate="${e.target.dataset.candidateCheck}"]`).disabled=!e.target.checked;}if(e.target.id==='review-select-all'){editor.selected=e.target.checked?new Set(editor.draft.tracks.map((_,i)=>i)):new Set();inspector.querySelectorAll('[data-review-select]').forEach(n=>n.checked=e.target.checked);$('review-selection').textContent=`${editor.selected.size} selected`;$('batch-controls').hidden=!editor.selected.size;}});
function confirmSongRemoval(indexes){
  const tracks=detail.review?.tracks||[],selected=[...new Set(indexes)].map(i=>tracks[i]).filter(Boolean);
  if(detail.job.status!=='Curated'||!selected.length){feedback('Select prepared songs to remove first.');return;}
  if(selected.length>=tracks.length){feedback('Keep at least one song. Use Delete release from staging to remove the entire album.');return;}
  songRemoval={revision:detail.review.revision,files:selected.map(t=>t.file)};
  const box=$('review-feedback');box.innerHTML=`<strong>Remove ${selected.length} song(s) from the prepared album?</strong><ul>${selected.map(t=>`<li>${esc(t.disc)}.${esc(t.track)} · ${esc(t.title)}</li>`).join('')}</ul><p>${tracks.length-selected.length} songs remain, with their existing numbering. Incoming originals and published music stay untouched. Complete albums become partial; fresh publication approval is required.</p><label class="check-label"><input id="remove-songs-check" type="checkbox"> Remove these songs from this prepared copy</label><button id="confirm-remove-songs" class="secondary" disabled>Remove songs</button><button id="cancel-remove-songs" class="quiet">Cancel</button>`;box.hidden=false;box.scrollIntoView({block:'nearest'});
}
function candidateCard(c,i,mode){
  const partial=!!c.missing||!!c.unmatched||mode==='partial';
  return `<article class="candidate-card"><strong>${esc(c.artist)} — ${esc(c.album)}</strong><p>${esc([c.year,c.country,c.media,c.label,c.catalognum,c.disambiguation].filter(Boolean).join(' · ')||'Edition details on MusicBrainz')} · Distance ${Number(c.distance).toFixed(4)} · ${c.tracks.length} catalogue tracks</p><p>${c.missing==null?'Legacy log: mapping will be rechecked.':`${c.missing} catalogue tracks not in this collection · ${c.unmatched} unresolved source tracks`}</p><a href="https://musicbrainz.org/release/${esc(c.release_id)}" target="_blank" rel="noopener noreferrer">Inspect edition ↗</a><details><summary>Catalogue track list</summary><ol>${c.tracks.map(t=>`<li>${esc(t.disc||1)}.${esc(t.track)} · ${esc(t.title)}${t.seconds?` · ${duration(t.seconds)}`:''}</li>`).join('')}</ol></details><label>Preparation mode<select data-candidate-mode="${i}"><option value="album" ${c.missing||c.unmatched?'disabled':''} ${partial?'':'selected'}>Complete album</option><option value="partial" ${partial?'selected':''}>Partial album · retain unresolved songs for review</option></select></label><label class="check-label"><input type="checkbox" data-candidate-check="${i}"> I checked this edition and preparation mode; prepare a Curated copy only</label><button class="secondary" data-choose-candidate="${i}" disabled>Use this edition</button>${c.unmatched?'<p class="warning">Partial mode keeps these songs with original track metadata, marked unverified. Check and confirm them individually before publication; successful matches are retained.</p>':''}</article>`;
}
async function copyMatchingLog(text){
  if(navigator.clipboard?.writeText){try{await navigator.clipboard.writeText(text);return;}catch(err){/* Local HTTP / denied clipboard: use selection fallback. */}}
  const previous=document.activeElement,area=document.createElement('textarea');
  area.value=text;area.readOnly=true;area.style.cssText='position:fixed;left:0;top:0;opacity:0;pointer-events:none';
  // Keep selection inside the modal's focus boundary.
  inspector.append(area);
  try{area.focus();area.select();area.setSelectionRange(0,text.length);if(!document.execCommand('copy'))throw new Error('Clipboard unavailable. Select the log text and copy it manually.');}
  finally{area.remove();if(previous?.isConnected)previous.focus();}
}
async function loadArtwork(file){if(!file)return;try{if(!file.type.startsWith('image/')||file.size>10*1024**2)throw new Error('Choose an image smaller than 10 MiB.');const data=await new Promise((resolve,reject)=>{const r=new FileReader();r.onload=()=>resolve(r.result);r.onerror=()=>reject(new Error('Image could not be read'));r.readAsDataURL(file);});editor.draft.artwork=data.split(',')[1];$('art-preview').src=data;$('art-preview').hidden=false;$('art-clear').disabled=false;$('art-status').textContent='Replacement selected · will update every output track when saved. Server validates the image.';updateEditorState();}catch(e){feedback(e.message);}}
inspector.addEventListener('change',e=>{if(e.target.id==='art-file')loadArtwork(e.target.files[0]);});
inspector.addEventListener('paste',e=>{if(!e.target.closest('#art-paste'))return;const image=[...e.clipboardData.items].find(x=>x.type.startsWith('image/'));if(image){e.preventDefault();loadArtwork(image.getAsFile());}else feedback('Copy the image itself, not its URL, or choose an image file.');});
inspector.addEventListener('click',e=>{if(e.target.closest('#art-clear')){editor.draft.artwork='';$('art-file').value='';$('art-preview').hidden=true;$('art-clear').disabled=true;$('art-status').textContent='No replacement selected; existing artwork retained.';updateEditorState();}});
async function saveDraft(){
  if(!editor||editor.busy||editor.stale)return;
  const invalid=[...inspector.querySelectorAll('input.edit-field,.edit-field input')].find(n=>!n.disabled&&!n.checkValidity());if(invalid){editor.editing=true;$('review-body').classList.add('editing');invalid.reportValidity();feedback('Complete the highlighted metadata before saving.');return;}
  const id=editor.id,manual=editor.manual;editor.busy=true;updateEditorState();
  try{await api(`/api/jobs/${id}/${manual?'manual':'edit'}`,RhythmModel.payload(editor.draft,detail.job.revision,manual));editor.baseline=RhythmModel.copy(editor.draft);if(!await inspect(id,true)){editor.stale=true;throw new Error('Saved on the server, but the current revision could not reload. Close and refresh before further edits or approval.');}toast(manual?'Manual preparation queued. Nothing publishes automatically.':'Changes saved as a new revision. Inspect it before approval.');await refresh();}
  catch(e){if(editor?.id===id){editor.busy=false;updateEditorState();}feedback(e.message);}
}
async function runJobAction(action,data){const id=editor.id;editor.busy=true;updateEditorState();try{await api(`/api/jobs/${id}/${action}`,data);editor.busy=false;if(action==='ungroup'||action==='delete'){inspector.close();if(action==='ungroup')view('incoming');storageAt=0;}else await inspect(id,true);await refresh();toast(action==='retry'?'Retry queued; inspector stays open.':action==='remove-tracks'?'Songs removed from prepared copy. Originals retained; review before publishing.':action==='delete'?'Release permanently deleted from staging. Published music untouched.':'Tracks returned to Incoming.');}catch(e){if(editor?.id===id){editor.busy=false;updateEditorState();}feedback(e.message);}}
$('approval-check').onchange=e=>$('confirm-approve').disabled=!e.target.checked;
$('confirm-approve').onclick=async()=>{const b=$('confirm-approve');b.disabled=true;try{if(!editor||draftDirty()||editor.stale||detail.review.revision!==approvalRevision)throw new Error('Review changed. Close this confirmation and inspect the current revision.');const id=editor.id;editor.busy=true;await api(`/api/jobs/${id}/approve`,{revision:approvalRevision});if(editor?.id===id)editor.busy=false;$('approve-dialog').close();await inspect(id,true);toast('Publication started in the background. You can close this inspector and keep curating.');await refresh();}catch(e){if(editor)editor.busy=false;feedback(e.message,'error',$('approve-dialog'));b.disabled=!$('approval-check').checked;}};
