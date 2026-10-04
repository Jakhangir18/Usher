/* Provider-neutral caption UI. See FRONTEND.md for the wire contract. */
(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  const KEY = 'usher.transcripts.v1';
  let saved = [], demoRows = [], demoTimer, socket, retryTimer, staleTimer, generation = 0;
  let demo = false, desired = false, endpoint = '', lastLegacy = '', lastMessage = 0;
  let storageOk = true;
  let liveRows = [], follow = true, cameraUrl = '';
  const colors = ['#305eab', '#985118', '#7560a5', '#217263', '#a03e65', '#546629'];
  function identity(entry) { return entry.speakerKey || entry.label; }
  function color(entry) { const numbered=identity(entry).match(/speaker_(\d+)/);if(numbered)return colors[Number(numbered[1])%colors.length];let hash = 0; for (const ch of identity(entry)) hash = (hash * 31 + ch.charCodeAt(0)) >>> 0; return colors[hash % colors.length]; }
  try { saved = JSON.parse(localStorage.getItem(KEY) || '[]'); if (!Array.isArray(saved)) saved = []; saved = saved.filter(e => e && typeof e.text === 'string' && typeof e.key === 'string').slice(-2000); endpoint = localStorage.getItem('usher.endpoint') || ''; } catch { storageOk = false; }
  const rows = () => demo ? demoRows : saved;
  function notice(message) { $('notice').textContent = message; $('notice').hidden = false; clearTimeout(notice.timer); notice.timer = setTimeout(() => $('notice').hidden = true, 4500); }
  function persist() { try { localStorage.setItem(KEY, JSON.stringify(saved)); storageOk = true; } catch { storageOk = false; notice('Storage is unavailable or full. Export to keep your transcript.'); } }
  function direction(angle) { return ['Front','Front right','Right','Behind right','Behind','Behind left','Left','Front left'][Math.round(angle / 45) % 8]; }
  function normalize(raw) {
    if (!raw || typeof raw !== 'object' || Array.isArray(raw)) return null;
    if (raw.type !== 'caption' && raw.stage !== 'final') return null;
    const text = raw.text ?? raw.transcript;
    if (typeof text !== 'string' || !text.trim()) return null;
    const modern = raw.type === 'caption';
    if (modern && (raw.segment_id == null || !raw.session_id)) return null;
    const angle = typeof raw.angle === 'number' && Number.isFinite(raw.angle) ? ((raw.angle % 360) + 360) % 360 : null;
    const timestamp = typeof raw.timestamp === 'number' ? raw.timestamp : typeof raw.ts === 'number' ? raw.ts : Date.now()/1000;
    const label = String(raw.speaker_name || raw.speaker_label || raw.speaker_id || 'Unknown speaker').slice(0,80);
    return { key: modern ? JSON.stringify([String(raw.session_id), String(raw.segment_id)]) : 'legacy:' + text.trim(), speakerKey: JSON.stringify([raw.session_id || 'legacy', raw.speaker_id || label]), text:text.trim().slice(0,20000), label, angle, timestamp: Number.isFinite(timestamp) ? timestamp : Date.now()/1000, final: modern ? raw.is_final === true : true, source: String(raw.source || 'whisper') };
  }
  function current(entry) {
    const index=liveRows.findIndex(row=>row.key===entry.key);
    if(index>=0) liveRows[index]=entry; else liveRows.push(entry);
    liveRows=liveRows.slice(-80);
    const panel=$('live-lines'), scroll=panel.scrollTop;
    panel.replaceChildren();
    for(const row of liveRows) {
      const line=document.createElement('article'); line.className='live-line'+(row.final?'':' partial'); line.style.setProperty('--speaker-color',color(row));
      const tag=document.createElement('span'); tag.className='speaker-tag';tag.textContent=row.label;
      const text=document.createElement('p');text.textContent=row.text;
      const state=document.createElement('span');state.className='line-state';state.textContent=row.final?'':'Speaking…';line.append(tag,text,state);panel.append(line);
    }
    panel.scrollTop=follow?panel.scrollHeight:scroll;
    $('caption-state').textContent=demo?'Simulated Scribe stream · sample data':entry.final?'Caption finalized':'Transcribing as they speak…';
  }
  function idle(message) { liveRows=[];const empty=document.createElement('p');empty.className='live-empty';empty.textContent=message;$('live-lines').replaceChildren(empty);$('caption-state').textContent='Waiting for captions'; }
  function status(text, connected=false) { $('connection-label').textContent=text; $('connect').classList.toggle('connected',connected); $('mode').textContent=demo?'Demo · sample data':text; }
  function receive(raw) {
    const entry = normalize(raw); if (!entry) return;
    if (raw.stage === 'final') { if (lastLegacy === entry.text) return; lastLegacy=entry.text; entry.key='legacy:'+Date.now()+':'+generation; }
    const list=rows(), index=list.findIndex(e=>e.key===entry.key);
    if (!entry.final && index >= 0) return;
    current(entry);
    if (entry.final) { if(index >= 0) list[index]=entry; else list.push(entry); if(list.length>2000) list.shift(); if(!demo) persist(); render(); }
    clearTimeout(staleTimer); staleTimer=setTimeout(()=>{ $('caption-state').textContent='Waiting for the next voice'; },10000);
  }
  function render() {
    const list=rows(), query=$('search').value.toLowerCase(), selected=$('speaker-filter').value;
    const speakers=new Map(list.map(e=>[identity(e),e.label])); $('speaker-filter').replaceChildren(new Option('Everyone',''),...Array.from(speakers,([id,label])=>new Option(label,id))); if(speakers.has(selected)) $('speaker-filter').value=selected;
    $('count').textContent=list.length; $('export').disabled=!list.length; $('clear').disabled=!list.length;
    $('storage-note').textContent=demo?'Sample conversation · demo history is not saved':storageOk?'Saved on this browser · Text only, no audio':'Browser storage unavailable · Export to keep your transcript';
    const filtered=list.filter(e=>(!selected||identity(e)===selected)&&(!query||(e.text+' '+e.label).toLowerCase().includes(query)));
    const container=$('transcripts'); container.replaceChildren();
    if(!filtered.length) { const box=document.createElement('div'); box.className='empty'; const title=document.createElement('h3'); title.textContent=list.length?'No matching captions':'Your conversations belong here.'; const p=document.createElement('p'); p.textContent=list.length?'Try a different search or speaker.':'Connect your device or try the demo. Final captions will appear here as people speak.'; box.append(title,p); container.append(box); return; }
    for(const entry of filtered.slice().reverse()) {
      const article=document.createElement('article'); article.className='entry';article.style.setProperty('--speaker-color',color(entry));
      const avatar=document.createElement('span');avatar.className='avatar';avatar.textContent=entry.label.match(/\d+/)?.[0] || '?';
      const content=document.createElement('div'),head=document.createElement('div');head.className='entry-head';
      const name=document.createElement('strong');name.textContent=entry.label;
      const p=document.createElement('p');p.textContent=entry.text;
      const time=document.createElement('time'),date=new Date(entry.timestamp*1000);
      if(Number.isFinite(date.getTime())){time.dateTime=date.toISOString();time.textContent=date.toLocaleString([], {month:'short',day:'numeric',hour:'2-digit',minute:'2-digit'});}
      head.append(name);content.append(head,p);article.append(avatar,content,time);container.append(article);
    }
  }
  function stop() { desired=false; generation++; clearTimeout(retryTimer);clearTimeout(staleTimer);clearInterval(demoTimer); demoTimer=null; if(socket){socket.close();socket=null;} if(demo)cameraIdle(); }
  function connect(url) {
    stop();demo=false; $('demo').textContent='Try a demo ↗'; render(); idle('Waiting for captions from your Pi');desired=true;endpoint=url;lastLegacy='';const token=generation;
    try{localStorage.setItem('usher.endpoint',url);}catch{}
    function open() { if(!desired||token!==generation)return;status('Connecting…');
      try{socket=new WebSocket(url);}catch{status('Could not connect');return;}
      socket.onopen=()=>{if(token===generation){status('Device connected',true);lastMessage=Date.now();}};
      socket.onmessage=event=>{if(token!==generation)return;lastMessage=Date.now();try{const raw=JSON.parse(event.data);if(raw.type==='snapshot'&&Array.isArray(raw.captions)){raw.captions.slice(-2000).forEach(receive);}else receive(raw);}catch{ /* Ignore malformed input without interrupting the connection. */ }};
      socket.onclose=()=>{if(!desired||token!==generation)return;status('Disconnected · retrying');$('caption-state').textContent='Connection lost · saved captions are available below';retryTimer=setTimeout(open,3000);};socket.onerror=()=>{};
    }open();
  }
  $('connect').onclick=()=>{ $('endpoint').value=endpoint||`ws://${location.hostname||'raspberrypi.local'}:8765`; $('connection-error').textContent='';$('connection-dialog').showModal(); };
  $('close-dialog').onclick=()=>$('connection-dialog').close();
  $('connection-form').onsubmit=event=>{event.preventDefault();let url;try{url=new URL($('endpoint').value.trim());if(!['ws:','wss:'].includes(url.protocol)||url.username||url.password)throw Error();if(location.protocol==='https:'&&url.protocol==='ws:'){ $('connection-error').textContent='This HTTPS page requires a secure wss:// connection. Open the interface over HTTP on your Pi for its default ws:// endpoint.';return;}}catch{$('connection-error').textContent='Enter a valid ws:// or wss:// device address.';return;} connect(url.href);$('connection-dialog').close();};
  $('disconnect').onclick=()=>{stop();demo=false;render();status('Not connected');idle('Connect your Pi to start listening');$('demo').textContent='Try a demo ↗';$('connection-dialog').close();};
  $('demo').onclick=()=>{
    if(demo){stop();demo=false;render();idle('Connect your Pi to start listening');status('Not connected');$('demo').textContent='Try a demo ↗';cameraIdle();return;}
    stop();cameraIdle();demo=true;demoRows=[];idle('');$('demo').textContent='End demo';status('Demo · sample data');render();
    $('camera-empty').hidden=true;$('camera-demo').hidden=false;$('camera-status').textContent='Illustrated demo';$('camera-note').textContent='Simulated conversation · no camera is recording';
    let i=0,word=0,pause=0;const samples=[['speaker_0','Speaker 1','Hey Jax, we saved you a seat over here.'],['speaker_1','Speaker 2','We’re planning a trip to the café after the workshop.'],['speaker_0','Speaker 1','Would you like to come with us? There’s a quiet table outside.'],['speaker_1','Speaker 2','We can walk over together when you’re ready.']];
    function tick(){if(pause){pause--;return;}const row=samples[i%samples.length],words=row[2].split(' ');word++;const final=word>=words.length;receive({type:'caption',session_id:'demo',segment_id:'demo-'+i,speaker_id:row[0],speaker_label:row[1],text:words.slice(0,word).join(' '),is_final:final,source:'demo'});$('camera-demo').className=row[0]==='speaker_0'?'speaking-one':'speaking-two';if(final){i++;word=0;pause=5;}}
    tick();demoTimer=setInterval(tick,260);
  };
  $('follow').onclick=()=>{follow=!follow;$('follow').setAttribute('aria-pressed',String(follow));$('follow').textContent=follow?'Auto-scroll on':'Auto-scroll off';if(follow)$('live-lines').scrollTop=$('live-lines').scrollHeight;};
  function cameraIdle(){ $('camera-stream').hidden=true;$('camera-stream').removeAttribute('src');$('camera-demo').hidden=true;$('camera-empty').hidden=false;$('camera-status').textContent='Camera offline';$('camera-note').textContent='Logi camera → Pi → your browser'; }
  function cameraSettings(){ $('camera-url').value=cameraUrl;$('camera-error').textContent='';$('camera-dialog').showModal(); }
  $('camera-setup').onclick=cameraSettings;$('camera-settings').onclick=cameraSettings;$('camera-close').onclick=()=>$('camera-dialog').close();
  $('camera-stop').onclick=()=>{cameraIdle();$('camera-dialog').close();};
  $('camera-form').onsubmit=event=>{event.preventDefault();let url;try{url=new URL($('camera-url').value);if(!['http:','https:'].includes(url.protocol)||url.username||url.password)throw Error();if(location.protocol==='https:'&&url.protocol==='http:')throw Error();}catch{$('camera-error').textContent='Enter a valid HTTP(S) stream URL. HTTPS pages require HTTPS feeds.';return;}
    if(demo)$('demo').onclick();cameraUrl=url.href;cameraIdle();$('camera-empty').hidden=true;$('camera-stream').hidden=false;$('camera-status').textContent='Connecting feed…';$('camera-note').textContent='External camera stream · no video saved';
    $('camera-stream').onload=()=>{$('camera-status').textContent='Feed connected';};$('camera-stream').onerror=()=>{cameraIdle();$('camera-status').textContent='Feed unavailable';notice('Camera feed unavailable. Check the Pi streaming service and address.');};$('camera-stream').src=cameraUrl;$('camera-dialog').close();
  };
  $('search').oninput=render;$('speaker-filter').onchange=render;
  $('export').onclick=()=>{const text=rows().map(e=>`[${new Date(e.timestamp*1000).toLocaleString()}] ${e.label} (${e.angle===null?'unknown direction':direction(e.angle)})\n${e.text}`).join('\n\n');const blob=new Blob([(demo?'USHER DEMO — SAMPLE DATA\n\n':'USHER TRANSCRIPT\n\n')+text],{type:'text/plain;charset=utf-8'}),url=URL.createObjectURL(blob),a=document.createElement('a');a.href=url;a.download=`usher-${demo?'demo-':''}${new Date().toISOString().slice(0,10)}.txt`;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);};
  $('clear').onclick=()=>{if(!confirm('Clear the saved transcript history in this browser? Export first if you want to keep a copy.'))return;if(demo)demoRows=[];else{saved=[];persist();}render();};
  $('text-size').onclick=()=>{const large=document.body.classList.toggle('large-text');$('text-size').setAttribute('aria-pressed',String(large));$('text-size').textContent=large?'Aa · Standard text':'Aa · Larger text';};
  render();
  // Receiving data is opt-in: opening the interface does not start cloud transcription.
})();
