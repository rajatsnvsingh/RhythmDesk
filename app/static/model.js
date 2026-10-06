/* Pure editor operations, shared by the browser and regression tests. */
'use strict';
const RhythmModel = (() => {
  const labels = value => [...new Map((Array.isArray(value) ? value : String(value || '').split(';')).map(x => x.trim()).filter(Boolean).map(x => [x.toLocaleLowerCase(), x])).values()];
  function draft(detail, manual = false) {
    const source = manual && !detail.review ? detail.sources : detail.review?.tracks || detail.sources;
    const tracks = source.map((t, i) => ({file:t.file, id:t.source_id || t.id, disc:Number(t.disc || 1), track:Number(t.track || i + 1),
      title:(!detail.review?t.suggested?.title:'') || t.title || t.path?.split('/').pop() || '', artist:(!detail.review?t.suggested?.artist:'') || t.artist || detail.job.artist || '',
      genres:labels(t.genres), tags:labels(t.tags)}));
    if (manual && detail.review) tracks.forEach((t,i) => {t.id ||= detail.sources.find(s=>s.disc===t.disc&&s.track===t.track)?.id || detail.sources[i]?.id;});
    return {artist:detail.review?.tracks[0]?.albumartist || detail.sources[0]?.suggested?.albumartist || detail.job.artist || '',
      album:detail.review?.tracks[0]?.album || detail.sources[0]?.suggested?.album || detail.job.album || '', year:detail.review?.tracks[0]?.year || detail.job.year || '', tracks, artwork:''};
  }
  const copy = value => JSON.parse(JSON.stringify(value));
  const navCount = value => {const n=Number(value);return Number.isFinite(n)&&n>0?Math.floor(n):0;};
  const navActive = (target, view) => target===view || (target==='more' && ['settings','activity'].includes(view));
  const dirty = (draft, baseline) => JSON.stringify(draft) !== JSON.stringify(baseline);
  function unknown(draft, policy) {
    return ['genres','tags'].flatMap(kind => {
      const allowed = (policy[kind] || []).map(x=>x.toLocaleLowerCase());
      const values = labels(draft.tracks.flatMap(t=>t[kind]));
      return values.filter(v=>!allowed.includes(v.toLocaleLowerCase())).map(value=>({kind,value,count:draft.tracks.filter(t=>t[kind].some(v=>v.toLocaleLowerCase()===value.toLocaleLowerCase())).length}));
    });
  }
  function replaceLabel(draft, kind, old, replacement) {
    draft.tracks.forEach(t=>t[kind]=labels(t[kind].flatMap(v=>v.toLocaleLowerCase()===old.toLocaleLowerCase()?labels(replacement):[v])));
  }
  function batch(draft, indexes, field, value, operation='replace') {
    indexes.forEach(i=>{draft.tracks[i][field]=['genres','tags'].includes(field)?labels(operation==='add'?[...draft.tracks[i][field],...labels(value)]:value):String(value);});
  }
  function payload(draft, revision, manual=false) {
    return {...copy(draft),revision,tracks:draft.tracks.map(t=>manual?{...t,id:t.id}:{...t,file:t.file})};
  }
  return {labels,draft,copy,dirty,unknown,replaceLabel,batch,payload,navCount,navActive};
})();
if (typeof module !== 'undefined') module.exports = RhythmModel;
