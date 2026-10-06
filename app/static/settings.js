'use strict';
const settingsNav=document.createElement('button');
settingsNav.dataset.view='settings'; settingsNav.textContent='Settings';
document.querySelector('nav').append(settingsNav);
settingsNav.addEventListener('click',()=>loadSettings());
async function loadSettings() {
  try { renderSettings(await api('/api/settings')); $('page-title').textContent='Settings'; }
  catch(e){error(e.message);}
}
function renderSettings(s) {
  $('settings-status').textContent=s.runtime.paused?'● Intake paused':'● Intake enabled';
  $('settings-status').className='status '+(s.runtime.paused?'warning':'live');
  $('mount-table').innerHTML='<div class="table-scroll"><table><thead><tr><th>Mapping</th><th>Host directory</th><th>App directory</th><th>Access from Web UI</th></tr></thead><tbody>'+s.mounts.map(m=>`<tr><td>${esc(m.key)}</td><td><code>${esc(m.host)}</code></td><td><code>${esc(m.internal)}</code></td><td>${!m.exists?'Not mounted':!m.readable?'Not readable':m.writable?'Read / write':'Read only'}</td></tr>`).join('')+'</tbody></table></div>';
  $('intake-paths').innerHTML=`<span class="muted">Drop files into</span> <code>${esc(s.incoming)}</code><br><span class="muted">Awaiting approval</span> <code>${esc(s.curated)}</code><br><span class="muted">Needs review</span> <code>${esc(s.review)}</code><br><small>Docker: the Web UI reads the library; only the isolated publisher can write after approval. Local demo access may differ.</small>`;
  for(const [key,value] of Object.entries(s.runtime)){const el=$('runtime-form').elements[key];if(typeof value==='boolean')el.checked=value;else el.value=value;}
  const defaults={STAGING_PATH:'/srv/media/music/staging',STATE_PATH:'/srv/media/music/curator-state',LIBRARY_PATH:'/srv/media/music/rhythm-attic'};
  for(const m of s.mounts)$('paths-form').elements[m.key].value=s.plan?.[m.key] || (m.host.startsWith('/')?m.host:defaults[m.key]);
  $('download-paths').hidden=!s.plan;
  $('plan-status').textContent=s.plan?'Saved plan · not applied automatically':'No saved plan';
}
$('runtime-form').onsubmit=async e=>{e.preventDefault();const f=e.target.elements;try{const s=await api('/api/settings/runtime',{stable_seconds:Number(f.stable_seconds.value),scan_interval:Number(f.scan_interval.value),automatic_grouping:f.automatic_grouping.checked,paused:f.paused.checked});renderSettings(s);toast('Intake settings saved. Applies on the worker’s next cycle.');}catch(err){toast(err.message);}};
$('paths-form').onsubmit=async e=>{e.preventDefault();try{renderSettings(await api('/api/settings/paths',Object.fromEntries(new FormData(e.target))));toast('Directory plan saved. Current library destination is unchanged.');}catch(err){toast(err.message);}};
