'use strict';
const $ = id => document.getElementById(id);
const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
let csrf = '', snapshot = null, currentView = 'overview', offset = 0, selected = new Set(), detail = null, refreshing = false;
let sorts = {jobs: ['updated', -1], library: ['artist', 1]};
const number = value => Number(value || 0).toLocaleString();
const duration = seconds => `${Math.floor((seconds || 0) / 60)}:${String(Math.round((seconds || 0) % 60)).padStart(2, '0')}`;
const bytes = value => value > 1024 ** 3 ? `${(value / 1024 ** 3).toFixed(1)} GB` : `${(value / 1024 ** 2).toFixed(1)} MB`;
const date = value => value ? new Date(value * 1000).toLocaleString() : 'Not yet scanned';
const badge = status => `<span class="badge ${esc(status.toLowerCase().replaceAll(' ', '-'))}">${esc(status)}</span>`;
const empty = message => `<div class="empty">${esc(message)}</div>`;
const metric = (label, value, note, style = '') => `<div class="metric ${style}"><div class="metric-label">${esc(label)}</div><div class="metric-value">${esc(value)}</div><div class="metric-note">${esc(note)}</div></div>`;

async function api(path, data) {
  const response = await fetch(path, data === undefined ? {} : {method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':csrf},body:JSON.stringify(data)});
  const result = await response.json();
  if (!response.ok) {
    if (response.status === 401 && path !== '/api/login') showLogin();
    throw new Error(result.error || 'Request failed');
  }
  return result;
}
function error(message) { $('error').textContent = message; $('error').hidden = false; }
function toast(message) { $('toast').textContent = message; $('toast').hidden = false; setTimeout(() => $('toast').hidden = true, 4500); }
function showLogin() { csrf = ''; $('desk').hidden = true; $('login').hidden = false; }
async function signIn(result) { csrf = result.csrf; $('login').hidden = true; $('logout').hidden=result.auth_required===false; $('desk').hidden = false; await refresh(); }
function view(name) { currentView = name; document.querySelectorAll('.view').forEach(el => el.hidden = el.id !== name); document.querySelectorAll('nav button').forEach(el => el.classList.toggle('active', el.dataset.view === name)); $('page-title').textContent = ({overview:'Overview',incoming:'Incoming',jobs:'Curation queue',curated:'Curated',library:'Rhythm Attic',activity:'Activity'})[name]; }

async function refresh() {
  if (!csrf || refreshing) return;
  refreshing = true;
  try {
    const params = new URLSearchParams({offset, limit:200, q:$('track-search').value, status:$('track-filter').value});
    snapshot = await api('/api/snapshot?' + params);
    render();
    $('error').hidden = true;
  } catch (e) { error(e.message); }
  finally { refreshing = false; }
}
function render() {
  const s = snapshot, jc = s.job_counts, tc = s.track_counts, lib = s.library;
  $('demo-banner').hidden = !s.demo;
  $('nav-incoming').textContent = number((tc.Incoming || 0) + (tc.Unresolved || 0));
  $('nav-jobs').textContent = number((jc.Queued || 0) + (jc.Processing || 0) + (jc['Needs review'] || 0));
  $('nav-curated').textContent = number(jc.Curated);
  const live = s.worker_heartbeat && Date.now() / 1000 - s.worker_heartbeat < 120;
  $('worker-status').textContent = s.demo ? '● Demo workspace' : live ? '● Worker active' : '○ Worker not reporting';
  $('worker-status').className = 'status ' + (live || s.demo ? 'live' : 'warning');
  $('last-scan').textContent = 'Last intake scan · ' + date(s.last_scan);
  $('publisher-status').textContent = s.approval_available ? '● Publisher available' : s.demo ? 'Demo: publishing disabled' : 'Publisher unavailable';
  $('publisher-status').className = 'status ' + (s.approval_available ? 'live' : 'warning');
  $('pipeline-metrics').innerHTML = metric('INCOMING TRACKS', number((tc.Incoming || 0) + (tc.Unresolved || 0)), `${number(tc.Unresolved)} need grouping`) + metric('CURATION IN PROGRESS', number((jc.Queued || 0) + (jc.Processing || 0)), `${number(jc['Needs review'])} releases need review`) + metric('CURATED · AWAITING APPROVAL', number(jc.Curated), 'Nothing publishes automatically', 'green') + metric('APPROVED RELEASES', number(jc.Approved), `${number(tc.Ignored)} files excluded · ${number(tc.Duplicate)} duplicates`);
  const decisions = s.jobs.filter(j => ['Curated','Needs review'].includes(j.status)).slice(0, 5);
  $('decision-list').innerHTML = decisions.length ? decisions.map(j => `<div class="decision-row"><span class="album-square">♫</span><div class="release-name"><strong>${esc(j.album)}</strong><small>${esc(j.artist)} · ${j.track_count} tracks</small></div>${badge(j.status)}<button class="secondary" data-job="${esc(j.id)}">Inspect</button></div>`).join('') : empty('No releases waiting. Drop music into incoming to begin.');
  $('library-summary').innerHTML = lib.available ? `<div class="summary-grid"><div><strong>${number(lib.albums)}</strong><small>ALBUMS</small></div><div><strong>${number(lib.tracks)}</strong><small>TRACKS</small></div><div><strong>${number(lib.artists)}</strong><small>ARTISTS</small></div><div><strong>${bytes(lib.bytes)}</strong><small>STORAGE USED</small></div></div><div class="signal"><span class="muted">Listening time</span><strong>${(lib.seconds / 3600).toFixed(1)} hours</strong></div>` : empty(s.stats_refreshing ? 'Reading library inventory…' : 'Library not mounted or readable. Statistics will appear when connected.');
  renderTracks(); renderJobs(); renderLibrary();
  $('recent-activity').innerHTML = activity(s.events.slice(0, 8));
  $('activity-list').innerHTML = activity(s.events);
}
function activity(events) { return events.length ? events.map(e => `<div class="event"><time>${esc(date(e.at))}</time><span class="event-level ${esc(e.level)}">${esc(e.level.toUpperCase())}</span><div>${esc(e.message)}${e.job_id ? `<small><button class="text-button" data-job="${esc(e.job_id)}">${esc(e.job_id)}</button></small>` : ''}</div></div>`).join('') : empty('No actions recorded yet.'); }
function eligible(track) { return ['Incoming','Unresolved'].includes(track.status) && track.sha256 && track.seconds >= 10; }
function renderTracks() {
  const tracks = snapshot.tracks;
  $('track-rows').innerHTML = tracks.length ? tracks.map(t => `<tr><td><input type="checkbox" data-track="${esc(t.id)}" aria-label="Select ${esc(t.title || t.path)}" ${selected.has(t.id) ? 'checked' : ''} ${eligible(t) ? '' : 'disabled'}></td><td class="source"><strong>${esc(t.title || t.path.split('/').pop())}</strong><small title="${esc(t.path)}">${esc(t.path)}</small>${t.reason ? `<small title="${esc(t.reason)}">${esc(t.reason)}</small>` : ''}</td><td>${esc(t.artist || '—')}</td><td>${esc(t.album || 'Unidentified')}</td><td>${t.track || '—'}</td><td>${t.seconds ? duration(t.seconds) : '—'}</td><td>${esc(t.codec || t.path.split('.').pop().toUpperCase())}</td><td>${t.bitrate ? Math.round(t.bitrate / 1000) + ' kbps' : '—'}<small>${t.sample_rate ? (t.sample_rate / 1000) + ' kHz' : ''}${t.bitdepth ? ' · ' + t.bitdepth + '-bit' : ''}</small></td><td>${badge(t.status)}</td><td>${t.sha256 && t.seconds ? `<button class="quiet" data-play-track="${esc(t.id)}" aria-label="Play ${esc(t.title)}">▶</button>` : ''}</td></tr>`).join('') : '<tr><td colspan="10">No tracks match this filter.</td></tr>';
  $('track-page').textContent = `${tracks.length ? offset + 1 : 0}–${Math.min(offset + tracks.length, snapshot.track_total)} of ${number(snapshot.track_total)} files`;
  $('previous').disabled = offset === 0;
  $('next').disabled = offset + 200 >= snapshot.track_total;
  selectionLabel();
}
function selectionLabel() { $('selection-count').textContent = `${selected.size} selected`; $('group-selected').disabled = !selected.size; }
function sortItems(items, type) { const [field, direction] = sorts[type]; return [...items].sort((a, b) => (typeof a[field] === 'number' ? a[field] - b[field] : String(a[field] || '').localeCompare(String(b[field] || ''))) * direction); }
function jobTable(jobs) {
  if (!jobs.length) return empty('No releases in this view.');
  return `<div class="table-scroll"><table><thead><tr><th data-sort="jobs:album">Release ↕</th><th data-sort="jobs:artist">Album artist ↕</th><th data-sort="jobs:year">Year ↕</th><th data-sort="jobs:track_count">Tracks ↕</th><th>Status</th><th>Destination / review signal</th><th data-sort="jobs:updated">Updated ↕</th><th></th></tr></thead><tbody>${sortItems(jobs, 'jobs').map(j => `<tr><td><strong>${esc(j.album)}</strong><small>${esc(j.id)}</small></td><td>${esc(j.artist)}</td><td>${j.year || '—'}</td><td>${j.track_count}</td><td>${badge(j.status)}</td><td>${esc(j.destination || j.reason || 'Matching pending')}${j.destination_exists && j.status !== 'Approved' ? '<small class="warning">Existing album: publication blocked</small>' : ''}</td><td>${esc(date(j.updated))}</td><td><button class="secondary" data-job="${esc(j.id)}">Inspect →</button></td></tr>`).join('')}</tbody></table></div>`;
}
function renderJobs() { const filter = $('job-filter').value; $('jobs-table').innerHTML = jobTable(snapshot.jobs.filter(j => !filter || j.status === filter)); $('curated-table').innerHTML = jobTable(snapshot.jobs.filter(j => j.status === 'Curated')); }
function renderLibrary() {
  const lib = snapshot.library;
  $('library-freshness').textContent = `Read-only · ${snapshot.stats_refreshing ? 'Scan in progress' : 'Snapshot ' + date(lib.scanned_at)}`;
  $('rescan-stats').disabled = snapshot.stats_refreshing;
  $('library-metrics').innerHTML = metric('ARTISTS', number(lib.artists), 'Album artists') + metric('ALBUMS', number(lib.albums), 'Release folders') + metric('TRACKS', number(lib.tracks), `${((lib.seconds || 0) / 3600).toFixed(1)} listening hours`) + metric('LIBRARY SIZE', bytes(lib.bytes || 0), 'Audio files only');
  const formats = Object.entries(lib.formats || {}).sort((a,b) => b[1] - a[1]);
  $('formats').innerHTML = formats.length ? formats.map(([name, count]) => `<div class="format-row"><span>${esc(name)}</span><progress class="format-track" value="${count}" max="${Math.max(lib.tracks,1)}"></progress><span>${number(count)}</span></div>`).join('') : empty('No format data available.');
  $('quality').innerHTML = [['Lossless tracks',lib.lossless],['Lossy tracks',lib.lossy],['Missing embedded artwork',lib.missing_art],['Unreadable tracks',lib.unreadable]].map(([key,value]) => `<div class="signal"><span class="muted">${esc(key)}</span><strong>${number(value)}</strong></div>`).join('') + (lib.errors || []).map(e => `<div class="signal"><span>${esc(e)}</span></div>`).join('');
  const query = $('library-search').value.toLowerCase();
  const releases = sortItems((lib.releases || []).filter(r => `${r.artist} ${r.album} ${r.year}`.toLowerCase().includes(query)), 'library');
  $('library-table').innerHTML = releases.length ? `<div class="table-scroll"><table><thead><tr><th data-sort="library:artist">Artist ↕</th><th data-sort="library:album">Album ↕</th><th data-sort="library:year">Year ↕</th><th data-sort="library:tracks">Tracks ↕</th><th>Formats</th><th>Duration</th><th data-sort="library:bytes">Size ↕</th><th>Embedded art</th></tr></thead><tbody>${releases.map(r => `<tr><td>${esc(r.artist)}</td><td>${esc(r.album)}<small>${esc(r.path)}</small></td><td>${r.year || '—'}</td><td>${r.tracks}</td><td>${esc(r.formats.join(' / '))}</td><td>${duration(r.seconds)}</td><td>${bytes(r.bytes)}</td><td>${r.missing_art ? `${r.missing_art} missing` : 'Complete'}</td></tr>`).join('')}</tbody></table></div>` : empty('No releases match this filter.');
}

async function inspect(jobId) {
  try {
    detail = await api('/api/jobs/' + encodeURIComponent(jobId));
    const {job, review, sources, log} = detail, editable = job.status === 'Curated';
    $('review-title').textContent = job.album;
    const first = review?.tracks[0];
    const cover = first?.artwork ? `<img class="cover" alt="Embedded album cover" src="${mediaUrl(job.id,first.file,true)}">` : '<div class="cover album-square">♫</div>';
    $('review-body').innerHTML = `<div class="review-top">${cover}<div class="review-meta"><p>${esc(job.artist)} · ${job.year || 'Year unknown'} · ${job.track_count} tracks ${badge(job.status)}</p><p>${esc(job.reason || 'Originals retained. This release stays here until explicitly approved.')}</p>${review ? `<p>Proposed destination<br><code>rhythm-attic/${esc(review.destination)}</code></p><small>Reviewed revision ${esc(review.revision.slice(0,16))}</small>` : ''}</div></div>${detail.destination_exists && job.status !== 'Approved' ? '<div class="banner">This destination already exists. Publication is blocked; no album will be replaced.</div>' : ''}`;
    if (review) {
      const t = review.tracks[0];
      $('review-body').innerHTML += `<div class="form-grid"><label>Album artist<input id="edit-artist" value="${esc(t.albumartist)}" ${editable ? '' : 'disabled'}></label><label>Album title<input id="edit-album" value="${esc(t.album)}" ${editable ? '' : 'disabled'}></label><label>Release year<input id="edit-year" type="number" value="${t.year}" ${editable ? '' : 'disabled'}></label><div class="muted">${number(review.tracks.filter(t => t.artwork).length)} / ${review.tracks.length} tracks have embedded artwork.<p>Check the edition and individual track tags before approving.</p></div></div><div class="table-scroll"><table><thead><tr><th>Disc</th><th>#</th><th>Title</th><th>Track artist</th><th>Length</th><th>Quality / art</th><th></th></tr></thead><tbody>${review.tracks.map((t, i) => `<tr class="review-track" data-index="${i}"><td><input data-field="disc" type="number" min="1" aria-label="Disc number" value="${t.disc}" ${editable ? '' : 'disabled'}></td><td><input data-field="track" type="number" min="1" aria-label="Track number" value="${t.track}" ${editable ? '' : 'disabled'}></td><td><input data-field="title" aria-label="Track title" value="${esc(t.title)}" ${editable ? '' : 'disabled'}><small>${esc(t.file.split('/').pop())}</small></td><td><input data-field="artist" aria-label="Track artist" value="${esc(t.artist)}" ${editable ? '' : 'disabled'}></td><td>${duration(t.seconds)}</td><td>${esc(t.format)}<small>${t.sample_rate / 1000} kHz${t.bitdepth ? ' · '+t.bitdepth+'-bit' : ''} · ${t.artwork ? 'Art ✓' : 'No art'}</small></td><td><button class="quiet" data-play-job="${i}" aria-label="Play ${esc(t.title)}">▶</button></td></tr>`).join('')}</tbody></table></div>`;
    }
    $('review-body').innerHTML += `<details><summary class="review-subheading">Source tracks (${sources.length})</summary><div class="review-log">${sources.map(s => esc(`${s.path} · ${duration(s.seconds)} · ${s.codec}`)).join('\n')}</div></details>${log ? `<details ${job.status === 'Needs review' ? 'open' : ''}><summary class="review-subheading">Beets matching log</summary><pre class="review-log">${esc(log)}</pre></details>` : ''}`;
    $('review-actions').innerHTML = job.status === 'Needs review' ? '<button id="ungroup-job" class="secondary">Return tracks to intake</button><label>MusicBrainz release ID (optional)<input id="retry-release" placeholder="Exact release UUID"></label><button id="retry-job" class="primary">Retry match</button>' : editable ? `<button id="ungroup-job" class="secondary">Regroup tracks</button><button id="save-tags" class="secondary">Save tag corrections</button><button id="request-approval" class="primary" ${!snapshot.approval_available || detail.destination_exists ? 'disabled' : ''}>Review approval →</button>` : job.status === 'Queued' ? '<button id="ungroup-job" class="secondary">Return tracks to intake</button>' : '<button data-close="review-dialog" class="secondary">Close</button>';
    if (!$('review-dialog').open) $('review-dialog').showModal();
  } catch (e) { error(e.message); }
}
function mediaUrl(job, file, art=false) { return '/api/media?' + new URLSearchParams({job,file,...(art ? {art:1} : {})}); }
function play(url,title,subtitle) {
  const prefix=$('review-dialog').open&&$('modal-audio')?'modal-':'';
  document.querySelectorAll('audio').forEach(node=>node.pause());
  const audio=$(prefix+'audio');
  audio.src=url;audio.load();
  $(prefix+'player-title').textContent=title;$(prefix+'player-subtitle').textContent=subtitle;
  $(prefix+'player').hidden=false;
  audio.ontimeupdate=()=>{audio.dataset.playbackSeconds=String(audio.currentTime);};
  audio.onerror=()=>toast('Audio preview failed: '+(audio.error?.message||'Unable to decode or load audio'));
  audio.play().catch(e=>toast('Playback could not start: '+e.message));
}
$('audio').addEventListener('timeupdate',()=>{$('audio').dataset.playbackSeconds=String($('audio').currentTime);});
$('audio').addEventListener('error',()=>toast('Audio preview failed: '+($('audio').error?.message||'Unable to decode or load audio')));

document.addEventListener('click', async e => {
  const button = e.target.closest('button');
  if (button?.dataset.view) view(button.dataset.view);
  if (button?.dataset.go) view(button.dataset.go);
  if (button?.dataset.close) $(button.dataset.close).close();
  if (button?.dataset.job) await inspect(button.dataset.job);
  if (button?.dataset.playTrack) { const t=snapshot.tracks.find(t=>t.id===button.dataset.playTrack); play('/api/media?track='+encodeURIComponent(t.id),t.title,t.artist); }
  if (button?.dataset.playJob !== undefined) { const t=detail.review.tracks[Number(button.dataset.playJob)]; play(mediaUrl(detail.job.id,t.file),t.title,t.artist); }
  const sort = e.target.closest('[data-sort]');
  if (sort) { const [type,field]=sort.dataset.sort.split(':'); sorts[type]=[field,sorts[type][0]===field ? -sorts[type][1] : 1]; renderJobs(); renderLibrary(); }
  try {
    if (button?.id==='ungroup-job') { await api(`/api/jobs/${detail.job.id}/ungroup`,{}); $('review-dialog').close(); toast('Tracks returned to intake for manual regrouping'); await refresh(); view('incoming'); }
    if (button?.id==='retry-job') { const jobId=detail.job.id; button.disabled=true; await api(`/api/jobs/${jobId}/retry`,{release_id:$('retry-release').value.trim()}); toast('Retry queued. This inspector stays open.'); await refresh(); await inspect(jobId); }
    if (button?.id==='save-tags') {
      const tracks = detail.review.tracks.map((t,i)=>{ const row=document.querySelector(`.review-track[data-index="${i}"]`); return {file:t.file,...Object.fromEntries([...row.querySelectorAll('[data-field]')].map(input=>[input.dataset.field,input.value]))}; });
      await api(`/api/jobs/${detail.job.id}/edit`,{revision:detail.review.revision,artist:$('edit-artist').value,album:$('edit-album').value,year:$('edit-year').value,tracks});
      toast('Corrections saved. Review this new version before approving.'); await inspect(detail.job.id); await refresh();
    }
    if (button?.id==='request-approval') {
      const changed = $('edit-artist').value !== detail.review.tracks[0].albumartist || $('edit-album').value !== detail.review.tracks[0].album || Number($('edit-year').value)!==detail.review.tracks[0].year || [...document.querySelectorAll('.review-track')].some((row,i)=>[...row.querySelectorAll('[data-field]')].some(input=>String(detail.review.tracks[i][input.dataset.field])!==input.value));
      if (changed) throw new Error('Save your tag corrections and inspect the new version before approval.');
      $('approval-summary').textContent = `${detail.review.destination} · ${detail.review.tracks.length} tracks · revision ${detail.review.revision.slice(0,16)}`;
      $('approval-check').checked=false; $('confirm-approve').disabled=true; $('approve-dialog').showModal();
    }
  } catch(err) { toast(err.message); }
});
document.addEventListener('change', e=> { if(e.target.dataset.track) { e.target.checked ? selected.add(e.target.dataset.track) : selected.delete(e.target.dataset.track); selectionLabel(); } });
$('login-form').addEventListener('submit', async e=>{e.preventDefault(); try {await signIn(await api('/api/login',{token:$('login-token').value})); $('login-token').value='';} catch(err){$('login-error').textContent=err.message;}});
$('logout').onclick=async()=>{await api('/api/logout',{}); showLogin();};
$('refresh').onclick=refresh;
$('job-filter').onchange=renderJobs;
$('track-filter').onchange=()=>{offset=0;refresh();};
let searchTimer; $('track-search').oninput=()=>{clearTimeout(searchTimer);searchTimer=setTimeout(()=>{offset=0;refresh();},300);};
$('library-search').oninput=renderLibrary;
$('previous').onclick=()=>{offset=Math.max(0,offset-200);refresh();}; $('next').onclick=()=>{offset+=200;refresh();};
$('select-page').onchange=e=>{snapshot.tracks.filter(eligible).forEach(t=>e.target.checked?selected.add(t.id):selected.delete(t.id));renderTracks();};
$('group-selected').onclick=()=>{const tracks=snapshot.tracks.filter(t=>selected.has(t.id)); const form=$('group-form'); form.reset(); if(tracks.length){form.elements.artist.value=tracks[0].albumartist||tracks[0].artist||'';form.elements.album.value=tracks[0].album||'';form.elements.year.value=tracks[0].year||'';} $('group-count').textContent=`${selected.size} tracks selected`; $('group-dialog').showModal();};
$('group-form').onsubmit=async e=>{e.preventDefault(); const data=Object.fromEntries(new FormData(e.target));try{await api('/api/group',{...data,track_ids:[...selected]});selected.clear();$('group-dialog').close();toast('Release queued for curation');await refresh();view('jobs');}catch(err){toast(err.message);}};
$('rescan-stats').onclick=async()=>{try{await api('/api/stats/refresh',{});toast('Library scan started');await refresh();}catch(err){error(err.message);}};
$('approval-check').onchange=e=>$('confirm-approve').disabled=!e.target.checked;
$('confirm-approve').onclick=async()=>{const button=$('confirm-approve');button.disabled=true;try{await api(`/api/jobs/${detail.job.id}/approve`,{revision:detail.review.revision});$('approve-dialog').close();$('review-dialog').close();toast('Approved album published to Rhythm Attic');await refresh();}catch(err){$('approve-dialog').close();toast(err.message);await refresh();}};
$('close-player').onclick=()=>{$('audio').pause();$('audio').removeAttribute('src');$('audio').load();$('player').hidden=true;};
(async()=>{try{await signIn(await api('/api/session'));}catch{showLogin();}})();
setInterval(()=>{if(csrf)refresh();},8000);
