'use strict';
const $ = id => document.getElementById(id);
const esc = v => String(v ?? '').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const number = v => Number(v || 0).toLocaleString();
const duration = v => {const s=Math.round(v||0);return `${Math.floor(s/60)}:${String(s%60).padStart(2,'0')}`;};
const bytes = v => v>=1024**3?`${(v/1024**3).toFixed(1)} GiB`:v>=1024**2?`${(v/1024**2).toFixed(1)} MiB`:`${(v/1024).toFixed(1)} KiB`;
const date = v => v?new Date(v*1000).toLocaleString():'Not yet scanned';
const badge = s => `<span class="badge ${esc(s.toLowerCase().replaceAll(' ','-'))}">${esc(s)}</span>`;
const empty = message => `<div class="empty">${esc(message)}</div>`;
const chips = (values,kind='') => values?.length?values.map(v=>`<span class="chip ${kind}">${esc(v)}</span>`).join(' '):'<span class="muted">—</span>';
const metric = (label,value,note,action='') => `<${action?'button':'div'} class="metric" ${action}><span class="metric-label">${esc(label)}</span><strong>${esc(value)}</strong><small>${esc(note)}</small></${action?'button':'div'}>`;
let csrf='', snapshot=null, detail=null, currentView='overview', offset=0, jobOffset=0, jobFilter='all', jobDirection=-1;
let selected=new Set(), refreshing=false, refreshQueued=false, storage=null, storageAt=0, importBusy=false;
const pageSize=50;
async function api(path,data) {
  const response=await fetch(path,data===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':csrf},body:JSON.stringify(data)});
  const result=await response.json();
  if(!response.ok){if(response.status===401&&path!=='/api/login')showLogin();throw new Error(result.error||'Request failed');}
  return result;
}
let toastTimer;
function feedback(message,kind='error',dialog=null) {
  const active=dialog||[...document.querySelectorAll('dialog[open]')].at(-1);
  if(active){let box=active.querySelector('.dialog-feedback');if(!box){box=document.createElement('div');box.className='dialog-feedback';box.setAttribute('role','alert');active.append(box);}box.textContent=message;box.classList.toggle('success',kind==='success');box.hidden=false;if(kind==='success')setTimeout(()=>{if(box.textContent===message&&box.classList.contains('success'))box.hidden=true;},6000);return;}
  if(kind==='error'){$('error').textContent=message;$('error').hidden=false;}
  else{clearTimeout(toastTimer);$('toast').textContent=message;$('toast').hidden=false;toastTimer=setTimeout(()=>$('toast').hidden=true,5000);}
}
const toast = message => feedback(message,'success');
function showLogin(){csrf='';$('desk').hidden=true;$('login').hidden=false;}
async function signIn(result){csrf=result.csrf;$('login').hidden=true;$('desk').hidden=false;$('logout').hidden=result.auth_required===false;$('mobile-logout').hidden=result.auth_required===false;await refresh();}
function view(name,historyMode='push'){
  if(name==='curated'){name='jobs';jobFilter='ready';}
  const go=()=>{const changed=currentView!==name;currentView=name;document.querySelectorAll('.view').forEach(el=>el.hidden=el.id!==name);document.querySelectorAll('.sidebar nav [data-view]').forEach(el=>{const exact=el.dataset.view===name;el.classList.toggle('active',RhythmModel.navActive(el.dataset.view,name));el.setAttribute('aria-current',exact?'page':RhythmModel.navActive(el.dataset.view,name)?'true':'false');});$('page-title').textContent=({overview:'Home',incoming:'Incoming',jobs:'Review',library:'Rhythm Attic',activity:'Activity',settings:'Settings',more:'More'})[name]||name;$('page-eyebrow').textContent=({overview:'THE WORKBENCH',incoming:'FROM THE MESSY DRAWER',jobs:'LISTEN. CHECK. KEEP.',library:'THE COLLECTION',activity:'THE WORKING RECORD',settings:'BEHIND THE DESK',more:'WORKSPACE TOOLS'})[name]||'THE WORKBENCH';if(name==='settings')loadSettings();if(historyMode==='replace')history.replaceState(null,'','#'+name);else if(location.hash!=='#'+name)history.pushState(null,'','#'+name);if(changed)window.scrollTo(0,0);};
  if($('review-dialog').open)guardDraft(()=>{$('review-dialog').close();go();});else go();
}
function reviewView(filter){jobFilter=filter;jobOffset=0;view('jobs');refresh();}
async function refresh(){
  if(!csrf)return;if(refreshing){refreshQueued=true;return;}
  refreshing=true;
  try{
    const params=new URLSearchParams({offset,limit:pageSize,q:$('track-search').value,status:$('track-filter').value,track_sort:$('track-sort').value,
      job_offset:jobOffset,job_limit:pageSize,job_filter:jobFilter,job_q:$('job-search').value,job_sort:$('job-sort').value,job_direction:jobDirection});
    snapshot=await api('/api/snapshot?'+params);render();
    if(typeof pollInspector==='function')await pollInspector();
    if(Date.now()-storageAt>60000)stagingCount();
  }catch(e){feedback(e.message);}finally{refreshing=false;if(refreshQueued){refreshQueued=false;refresh();}}
}
function encoding(t){return [t.format||t.codec?.toUpperCase()||'Unknown format',t.bitrate?`${Math.round(t.bitrate/1000)} kbps`:'',t.sample_rate?`${t.sample_rate/1000} kHz`:'',t.bitdepth?`${t.bitdepth}-bit`:''].filter(Boolean).join(' · ');}
function cover(job){return job.cover_url?`<img class="queue-cover" src="${esc(job.cover_url)}" alt="${esc(job.album)} cover" loading="lazy">`:'<span class="album-square art-placeholder" role="img" aria-label="No cover preview">NO ART</span>';}
function reasonFor(job){if(job.status==='Approved')return 'Published explicitly · read-only history';if(job.status==='Purged')return 'Staging was explicitly purged; library unchanged';if(job.status==='Regrouped')return 'Returned to Incoming; retained history';if(job.status==='Curated')return job.readiness.can_review?'Ready for your metadata and edition check':job.readiness.blockers.filter(b=>!['publisher','demo'].includes(b.code)).map(b=>b.message).join(' · ');return job.recovery?.title||job.progress?.phase||job.reason||job.status;}
function releaseName(job){return job.album||'Untitled release';}
function decisionRow(job){return `<div class="decision-row">${cover(job)}<div class="release-name"><strong>${esc(releaseName(job))}</strong><small>${esc(job.artist||'Unidentified artist')}${job.year?' · '+esc(job.year):''} · ${number(job.track_count)} tracks · ${esc(job.mode)}</small><span class="decision-reason">${esc(reasonFor(job))}</span></div>${badge(job.status)}<button class="secondary" data-job="${esc(job.id)}">${job.status==='Needs review'?'Resolve':'Review'}</button></div>`;}
function queueRow(j){return `<tr><td class="queue-name">${cover(j)}<strong>${esc(releaseName(j))}</strong></td><td class="queue-credit">${esc(j.artist||'Unidentified artist')}<small>${j.year||'Year unknown'}</small></td><td class="queue-count">${j.track_count} tracks<small>${esc(j.mode)}</small></td><td class="queue-state">${badge(j.status)}<small>${esc(reasonFor(j))}</small></td><td class="queue-destination"><code>${esc(j.destination||'Not prepared')}</code></td><td class="queue-date">${esc(date(j.updated))}</td><td class="queue-action"><button class="secondary" data-job="${esc(j.id)}">Inspect</button></td></tr>`;}
function navigationCount(id,value,label,description){const count=RhythmModel.navCount(value),el=$(id);el.hidden=!count;el.textContent=count?number(count):'';el.closest('button').setAttribute('aria-label',count?`${label}, ${number(count)} ${description}`:label);}
function render(){
  const s=snapshot,c=s.review_counts,t=s.track_counts,j=s.job_counts,lib=s.library;
  $('demo-banner').hidden=!s.demo;
  navigationCount('nav-incoming',(t.Incoming||0)+(t.Unresolved||0),'Incoming','tracks to organize');navigationCount('nav-jobs',c.attention+c.ready,'Review','releases awaiting your decision');
  const live=s.worker_heartbeat&&Date.now()/1000-s.worker_heartbeat<120;
  $('worker-status').textContent=s.demo?'Demo':s.runtime.paused?'Paused':live?'Worker active':'Worker not reporting';$('worker-status').className='status '+(live&&!s.runtime.paused?'live':'warning');
  $('publisher-status').textContent=s.approval_available?'Publisher available':s.demo?'Demo: no publication':'Publisher unavailable';
  const pending=s.intake.scan_pending,changed=s.intake.changed_files;
  document.querySelectorAll('[data-scan]').forEach(b=>{b.disabled=!s.intake.ready||pending||importBusy||s.runtime.paused;b.textContent=pending?'Scan queued':changed?`Scan ${number(changed)} new / changed files`:'Scan incoming';});
  $('incoming-state').textContent=importBusy?'Copy in progress · scan after completion':pending?'Scan requested':s.runtime.paused?'Processing paused in Settings':changed?'Changes detected · you decide when to scan':'Incoming is up to date';
  const actions=[];
  if(c.attention)actions.push(`<button class="secondary" data-review="attention">Resolve ${number(c.attention)} release${c.attention===1?'':'s'} needing attention →</button>`);
  if(c.ready)actions.push(`<button class="primary" data-review="ready">Review ${number(c.ready)} prepared release${c.ready===1?'':'s'} →</button>`);
  if((t.Incoming||0)+(t.Unresolved||0))actions.push(`<button class="secondary" data-view="incoming">Organize ${number((t.Incoming||0)+(t.Unresolved||0))} indexed tracks →</button>`);
  $('next-actions').innerHTML=actions.join('');
  $('pipeline-metrics').innerHTML=metric('To organize',number((t.Incoming||0)+(t.Unresolved||0)),'Indexed tracks','data-view="incoming"')+metric('Needs attention',number(c.attention),'Release decisions','data-review="attention"')+metric('Ready for review',number(c.ready),'Not automatically correct','data-review="ready"')+metric('Service activity',number(c.processing),'Queued / preparing / editing / publishing','data-review="processing"');
  $('last-scan').textContent=`Intake scan: ${date(s.last_scan)} · ${number(t.Ignored)} excluded · ${number(t.Duplicate)} duplicate indexed files · ${number(j.Approved)} published jobs in this workspace’s retained history`;
  const decisions=s.decisions||[];
  $('decision-list').innerHTML=decisions.length?decisions.map(decisionRow).join(''):'<div class="desk-empty"><strong>The desk is clear.</strong><p>No releases need a decision. Import music or copy files into Incoming, then scan when you’re ready. Every release waits for your approval.</p></div>';
  $('processing-panel').hidden=!s.active_jobs.length;$('processing-list').innerHTML=s.active_jobs.map(job=>`<div class="activity-job">${processingMarkup(job)}<button class="text-button" data-job="${esc(job.id)}">Inspect</button></div>`).join('');
  $('library-summary').innerHTML=lib.available?`<div class="summary-grid">${[['Albums',lib.albums],['Tracks',lib.tracks],['Artists',lib.artists],['Storage',bytes(lib.bytes)],['Genres',lib.genres],['Tags',lib.tags]].map(([k,v])=>`<div><strong>${typeof v==='string'?v.split(' ')[0]:number(v)}</strong><small>${k==='Storage'?v.split(' ')[1]+' storage':k}</small></div>`).join('')}</div><p class="compact-note">Read-only snapshot · ${esc(date(lib.scanned_at))}</p>`:empty(s.stats_refreshing?'Reading library inventory…':'Library is not mounted or readable.');
  renderStorage();renderTracks();renderJobs();renderLibrary();$('recent-activity').innerHTML=activity(s.events.slice(0,8));$('activity-list').innerHTML=activity(s.events);
}
function processingMarkup(job){const p=job.progress,active=['Queued','Processing','Editing','Publishing'].includes(job.status),elapsed=p?Math.max(0,Math.floor((active?Date.now()/1000:p.updated)-p.started)):0;return `<div class="processing-job"><strong>${esc(job.album)} · ${esc(active?p?.phase||job.status:job.status)}</strong>${active?`<progress aria-label="${esc(p?.phase||job.status)}" ${p?.total?`max="${p.total}" value="${p.completed||0}"`:''}></progress>`:''}<small>${p?.total?`${p.completed||0}/${p.total} tracks · `:''}${p?`${Math.floor(elapsed/60)}m ${elapsed%60}s · `:''}${esc(active?p?.message||'Waiting for worker':job.reason||'Prepared copy retained')}</small>${active&&p&&!p.total?'<small>Network matching has no reliable percentage.</small>':''}</div>`;}
function eligible(t){return ['Incoming','Unresolved'].includes(t.status)&&t.sha256&&t.seconds>=10;}
function renderTracks(){
  const tracks=snapshot.tracks;
  $('track-rows').innerHTML=tracks.length?tracks.map(t=>`<tr><td class="track-select"><label class="hit-target"><input type="checkbox" data-track="${esc(t.id)}" aria-label="Select ${esc(t.title||t.path)}" ${selected.has(t.id)?'checked':''} ${eligible(t)?'':'disabled'}></label></td><td class="track-title"><strong>${esc(t.title||t.path.split('/').pop())}</strong><small class="source-path">${esc(t.path)}</small>${t.reason?`<small class="warning">${esc(t.reason)}</small>`:''}</td><td class="track-credit">${esc(t.artist||'Unknown artist')}<small>${esc(t.album||'Unidentified album')}</small></td><td class="track-position">${t.track?`Disc ${t.disc||1} · #${t.track}`:'Unnumbered'}</td><td class="track-encoding">${duration(t.seconds)}<small>${esc(encoding(t))}</small></td><td class="track-action">${badge(t.status)}${t.sha256&&t.seconds?`<button class="quiet" data-play-track="${esc(t.id)}" aria-label="Play ${esc(t.title)}">▶</button>`:''}</td></tr>`).join(''):'<tr><td colspan="6">No indexed files match. New physical files appear here only after you scan.</td></tr>';
  $('track-page').textContent=`${tracks.length?offset+1:0}–${offset+tracks.length} of ${number(snapshot.track_total)} indexed files`;$('previous').disabled=!offset;$('next').disabled=offset+pageSize>=snapshot.track_total;
  $('intake-summary').textContent=`${number(snapshot.intake.changed_files)} physical files awaiting scan · ${number(snapshot.track_counts.Ignored)} excluded · ${number(snapshot.track_counts.Duplicate)} duplicates. Use All indexed files to inspect these outcomes.`;selectionLabel();
}
function selectionLabel(){const available=snapshot?.tracks.filter(eligible)||[];$('selection-count').textContent=`${selected.size} selected across pages`;$('group-selected').disabled=!selected.size;$('clear-selection').disabled=!selected.size;$('select-page').checked=!!available.length&&available.every(t=>selected.has(t.id));$('select-page').indeterminate=available.some(t=>selected.has(t.id))&&!$('select-page').checked;}
function renderJobs(){
  const c=snapshot.review_counts;for(const key of ['attention','ready','processing','history'])$('count-'+key).textContent=number(c[key]);$('count-decisions').textContent=number(c.attention+c.ready);
  document.querySelectorAll('#review-tabs button').forEach(b=>{b.classList.toggle('active',b.dataset.review===jobFilter);b.setAttribute('aria-pressed',b.dataset.review===jobFilter);});
  $('jobs-table').innerHTML=snapshot.jobs.length?`<div class="table-scroll"><table class="queue-table"><thead><tr><th>Release</th><th>Artist / year</th><th>Tracks / mode</th><th>Status / next decision</th><th>Destination</th><th>Updated</th><th></th></tr></thead><tbody>${snapshot.jobs.map(queueRow).join('')}</tbody></table></div>`:empty('No releases in this filter.');
  $('job-page').textContent=`${snapshot.jobs.length?jobOffset+1:0}–${jobOffset+snapshot.jobs.length} of ${number(snapshot.job_total)} releases`;$('job-previous').disabled=!jobOffset;$('job-next').disabled=jobOffset+pageSize>=snapshot.job_total;
}
function renderLibrary(){
  const lib=snapshot.library;$('library-freshness').textContent=`Read-only · ${snapshot.stats_refreshing?'Scan in progress':'Snapshot '+date(lib.scanned_at)} · successfully inspected audio unless stated otherwise`;$('rescan-stats').disabled=snapshot.stats_refreshing;
  $('library-metrics').innerHTML=[['Artists',lib.artists,'Album artists'],['Albums',lib.albums,'Current release folders'],['Tracks',lib.tracks,'Successfully inspected'],['Library size',bytes(lib.bytes),'Inspected audio'],['Genres',lib.genres,'Distinct embedded values'],['Tags',lib.tags,'Distinct category values']].map(([k,v,n])=>metric(k,typeof v==='string'?v:number(v),n)).join('');
  $('formats').innerHTML=Object.entries(lib.formats||{}).map(([name,count])=>`<div class="format-row"><span>${esc(name)}</span><progress value="${count}" max="${Math.max(lib.tracks,1)}"></progress><strong>${number(count)}</strong></div>`).join('')||empty('No encoding data.');
  const coverage=key=>lib[key]===undefined?'Rescan to measure':`${number(lib[key])} / ${number(lib.tracks)}`;
  $('quality').innerHTML=[['Tracks with genres',coverage('with_genres')],['Tracks with category tags',coverage('with_tags')],['Missing embedded artwork',number(lib.missing_art)],['Unreadable audio files',number(lib.unreadable)],['Lossless / lossy encoding',`${number(lib.lossless)} / ${number(lib.lossy)}`]].map(([k,v])=>`<div class="signal"><span>${esc(k)}</span><strong>${esc(v)}</strong></div>`).join('')+'<p class="compact-note">Artwork counts image presence, not image validity. Encoding does not certify source fidelity. Unreadable audio is excluded from inspected track totals.</p>'+(lib.errors||[]).map(e=>`<p class="compact-note warning">${esc(e)}</p>`).join('');
  const q=$('library-search').value.toLowerCase(),filter=$('library-filter').value,sort=$('library-sort').value;
  const releases=(lib.releases||[]).filter(r=>`${r.artist} ${r.album} ${r.year} ${(r.genres||[]).join(' ')} ${(r.tags||[]).join(' ')}`.toLowerCase().includes(q)&&(!filter||(filter==='art'?r.missing_art:r['with_'+filter]!==undefined&&r['with_'+filter]<r.tracks))).sort((a,b)=>typeof a[sort]==='number'?a[sort]-b[sort]:String(a[sort]||'').localeCompare(String(b[sort]||'')));
  $('library-table').innerHTML=releases.length?`<div class="table-scroll"><table class="library-table"><thead><tr><th>Artist / release</th><th>Year / tracks</th><th>Encoding / size</th><th>Genres / category tags</th><th>Artwork</th></tr></thead><tbody>${releases.map(r=>`<tr><td><strong>${esc(r.artist)} · ${esc(r.album)}</strong><small>${esc(r.path)}</small></td><td>${r.year||'—'} · ${r.tracks} tracks</td><td>${esc(r.formats.join(' / '))} · ${bytes(r.bytes)}</td><td><div>Genres: ${chips(r.genres)}</div><div>Tags: ${chips(r.tags)}</div></td><td>${r.missing_art?`${r.missing_art} missing`:'Present in all tracks'}</td></tr>`).join('')}</tbody></table></div>`:empty('No library releases match.');
}
function activity(events){return events.length?events.map(e=>`<div class="event"><time>${esc(date(e.at))}</time><span class="${esc(e.level)}">${esc(e.level.toUpperCase())}</span><div>${esc(e.message)}${e.job_id?` <button class="text-button" data-job="${esc(e.job_id)}">Inspect release</button>`:''}</div></div>`).join(''):empty('No activity yet.');}
function mediaUrl(job,file,art=false){return '/api/media?'+new URLSearchParams({job,file,...(art?{art:1}:{})});}
function play(url,title,subtitle){document.querySelectorAll('audio').forEach(a=>a.pause());const prefix=$('review-dialog').open?'modal-':'';$(prefix+'player-title').textContent=title;$(prefix+'player-subtitle').textContent=subtitle;$(prefix+'player').hidden=false;const audio=$(prefix+'audio');audio.src=url;audio.load();audio.onerror=()=>feedback('Audio preview failed: '+(audio.error?.message||'Unable to load or decode audio'));audio.play().catch(e=>feedback('Playback could not start: '+e.message));}
function closePlayer(prefix=''){const audio=$(prefix+'audio');audio.pause();audio.removeAttribute('src');audio.load();$(prefix+'player').hidden=true;}
async function requestScan(){try{await api('/api/intake/scan',{});toast('Scan requested. Nothing publishes automatically.');await refresh();}catch(e){feedback(e.message);}}
function openGroup(){const tracks=snapshot.tracks.filter(t=>selected.has(t.id)),f=$('group-form');f.reset();if(tracks.length){f.elements.artist.value=tracks[0].albumartist||tracks[0].artist||'';f.elements.album.value=tracks[0].album||'';f.elements.year.value=tracks[0].year||'';}$('group-count').textContent=`${selected.size} source tracks selected across pages`;groupMode();$('group-dialog').showModal();}
function groupMode(){
  const f=$('group-form'),mode=f.elements.mode.value,resolved=mode==='auto'?(selected.size===1?'single':'album'):mode;
  f.elements.mode.querySelector('[value=single]').disabled=selected.size!==1;
  $('group-catalogue-fields').hidden=mode==='manual';
  for(const name of ['artist','album','year','release_id'])f.elements[name].disabled=mode==='manual';
  $('group-mode-note').textContent=resolved==='manual'?'Next: enter metadata and artwork once, in the inspector. Untagged files are welcome; no catalogue lookup.':resolved==='album'?'Complete album: every submitted track and the intended full release must validate. Choose Partial if your collection is incomplete.':resolved==='partial'?'Partial album: every submitted recording must match; completeness is not certified.':'Single: match this recording and verify its release identity. Album fields are lookup hints, not a claim that the album is complete.';
  f.querySelector('button.primary').textContent=mode==='manual'?'Open manual editor →':'Queue preparation →';
}
document.addEventListener('click',async e=>{
  const b=e.target.closest('button');if(!b)return;
  if(b.dataset.close){if(b.dataset.close==='review-dialog')guardDraft(()=>$('review-dialog').close());else $(b.dataset.close).close();}
  if(b.dataset.view)view(b.dataset.view);if(b.dataset.review)reviewView(b.dataset.review);if(b.dataset.job)inspect(b.dataset.job);
  if(b.hasAttribute('data-import'))openImport();if(b.hasAttribute('data-scan'))requestScan();if(b.hasAttribute('data-storage')){view('settings');$('storage-panel').scrollIntoView({block:'start'});}
  if(b.dataset.playTrack){const t=snapshot.tracks.find(t=>t.id===b.dataset.playTrack);play('/api/media?track='+encodeURIComponent(t.id),t.title,t.artist);}
});
document.addEventListener('change',e=>{if(e.target.dataset.track){e.target.checked?selected.add(e.target.dataset.track):selected.delete(e.target.dataset.track);selectionLabel();}});
$('login-form').onsubmit=async e=>{e.preventDefault();try{await signIn(await api('/api/login',{token:$('login-token').value}));$('login-token').value='';}catch(err){$('login-error').textContent=err.message;}};
async function logout(){guardDraft(async()=>{try{await api('/api/logout',{});document.querySelectorAll('dialog[open]').forEach(d=>d.close());showLogin();}catch(e){feedback(e.message);}});}
$('logout').onclick=logout;$('mobile-logout').onclick=logout;$('refresh').onclick=()=>{storageAt=0;refresh();};
for(const id of ['track-filter','track-sort'])$(id).onchange=()=>{offset=0;refresh();};
function search(id,fn){let timer;$(id).oninput=()=>{clearTimeout(timer);timer=setTimeout(fn,250);};}
search('track-search',()=>{offset=0;refresh();});search('job-search',()=>{jobOffset=0;refresh();});$('job-sort').onchange=()=>{jobOffset=0;refresh();};$('job-direction').onclick=()=>{jobDirection=-jobDirection;$('job-direction').textContent=jobDirection===1?'Ascending ↑':'Descending ↓';refresh();};
for(const id of ['library-search','library-sort','library-filter'])$(id).addEventListener(id==='library-search'?'input':'change',renderLibrary);
$('previous').onclick=()=>{offset=Math.max(0,offset-pageSize);refresh();};$('next').onclick=()=>{offset+=pageSize;refresh();};$('job-previous').onclick=()=>{jobOffset=Math.max(0,jobOffset-pageSize);refresh();};$('job-next').onclick=()=>{jobOffset+=pageSize;refresh();};
$('select-page').onchange=e=>{snapshot.tracks.filter(eligible).forEach(t=>e.target.checked?selected.add(t.id):selected.delete(t.id));renderTracks();};$('clear-selection').onclick=()=>{selected.clear();renderTracks();};$('group-selected').onclick=openGroup;$('group-form').elements.mode.onchange=groupMode;
$('group-form').onsubmit=async e=>{e.preventDefault();const b=e.target.querySelector('button.primary');b.disabled=true;try{const result=await api('/api/group',{...Object.fromEntries(new FormData(e.target)),track_ids:[...selected]});const manual=e.target.elements.mode.value==='manual';selected.clear();$('group-dialog').close();await refresh();view('jobs');if(manual)await inspect(result.job_id);else toast('Preparation queued. Originals retained.');}catch(err){feedback(err.message);}finally{b.disabled=false;}};
$('rescan-stats').onclick=async()=>{try{await api('/api/stats/refresh',{});toast('Read-only library scan started');await refresh();}catch(e){feedback(e.message);}};
$('close-player').onclick=()=>closePlayer();$('modal-close-player').onclick=()=>closePlayer('modal-');
window.addEventListener('popstate',()=>{const name=location.hash.slice(1)||'overview';if(!$(name)?.classList.contains('view'))return;if($('review-dialog').open&&draftDirty()){history.replaceState(null,'','#'+currentView);view(name);}else view(name,'replace');});
window.addEventListener('beforeunload',e=>{if(typeof draftDirty==='function'&&draftDirty()){e.preventDefault();e.returnValue='';}});
(async()=>{try{await signIn(await api('/api/session'));const name=location.hash.slice(1)||'overview';if($(name)?.classList.contains('view'))view(name,'replace');}catch{showLogin();}})();
setInterval(()=>{if(csrf)refresh();},8000);
