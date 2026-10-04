/* Usher judge demo — prototype. Connection/reconnect/demo/camera ideas from syedak1's usher.js. */
(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  const LEVELS = { full: null, mild: 0.62, moderate: 0.38, severe: 0.2, none: 0 }; // fraction of half-diagonal
  const FOV = 70;            // camera field of view (deg): speakers outside it get the edge chip
  // Speaker colours tuned to the navy / ice-blue / pink palette (distinct, readable on #092348).
  const COLORS = ['#DEAFC3', '#9EC3F5', '#F2C49B', '#B9A7E8', '#9FD9C9', '#E89AA8'];
  const NEUTRAL = 'rgba(210, 226, 249, .45)';  // live text not yet voice-identified
  const PROMOTE_SEC = 12;    // live lines never replaced by a voice-ID line (e.g. no internet) stay after this
  // refine: the server sends voice-identified lines ({type:"refined"}) that replace live ones, so
  // live captions are only the faint tail and don't count as people.
  const S = { lines: [], live: [], partial: null, sounds: [], people: new Map(), level: 'moderate',
              refine: false, ws: null, retry: null, host: '', demo: false, demoTimer: null, chipAt: 0, last: null };

  const color = id => { if (!S.people.has(id)) S.people.set(id, { color: COLORS[S.people.size % COLORS.length], label: id }); return S.people.get(id).color; };
  const dirWord = a => { if (a == null) return ''; const e = ((a + 180) % 360) - 180; return Math.abs(e) <= 15 ? 'front' : Math.abs(e) >= 120 ? 'behind' : e > 0 ? 'right' : 'left'; };
  const store = (k, v) => { try { v === undefined ? (v = localStorage.getItem(k)) : localStorage.setItem(k, v); } catch { } return v; };

  // ---------- tunnel overlay ----------
  const stage = $('stage'), canvas = $('reticle'), ctx = canvas.getContext('2d');
  function radius() {
    const w = stage.clientWidth, h = stage.clientHeight, f = LEVELS[S.level];
    return f === null ? Math.min(w, h) * 0.46 : f === 0 ? 60 : f * Math.hypot(w, h) / 2;
  }
  function layoutTunnel() {
    const r = radius();
    stage.style.setProperty('--clear', `${r * 0.72}px`);
    stage.style.setProperty('--edge', `${r * 1.12}px`);
    canvas.width = stage.clientWidth * devicePixelRatio; canvas.height = stage.clientHeight * devicePixelRatio;
  }
  document.querySelectorAll('.toggle button').forEach(b => b.onclick = () => {
    document.querySelectorAll('.toggle button').forEach(x => x.classList.toggle('on', x === b));
    S.level = b.dataset.level;
    stage.className = 'stage level-' + S.level + (stage.classList.contains('has-camera') ? ' has-camera' : '');
    layoutTunnel();
  });
  addEventListener('resize', layoutTunnel);

  // ---------- reticle + off-view speaker chip ----------
  const toCanvas = a => (a - 90) * Math.PI / 180; // 0 = front = top of the circle
  function draw() {
    const d = devicePixelRatio, w = canvas.width / d, h = canvas.height / d, cx = w / 2, cy = h / 2, R = radius() * 0.95, now = performance.now();
    ctx.setTransform(d, 0, 0, d, 0, 0); ctx.clearRect(0, 0, w, h);
    S.sounds = S.sounds.filter(s => now - s.t < 900);
    for (const s of S.sounds) {
      const age = (now - s.t) / 900, k = Math.min(1, Math.max(0, (s.level - 0.015) / 0.1));
      const span = (12 + 12 * k) * Math.PI / 180, a = toCanvas(s.angle);
      ctx.strokeStyle = k > 0.5 ? '#ff5c5c' : '#ffd166';
      ctx.globalAlpha = (1 - age) * (0.45 + 0.55 * k);
      ctx.lineWidth = 5 + 9 * k; ctx.lineCap = 'round';
      ctx.beginPath(); ctx.arc(cx, cy, R, a - span, a + span); ctx.stroke();
    }
    ctx.globalAlpha = 1;
    const chip = $('speaker-chip');
    if (S.last && now - S.chipAt < 3000) {
      const e = ((S.last.angle + 180) % 360) - 180;
      if (Math.abs(e) > FOV / 2) {
        const a = toCanvas(S.last.angle), r = Math.max(70, R - 10);
        chip.hidden = false;
        chip.style.left = `${cx + Math.cos(a) * r}px`; chip.style.top = `${cy + Math.sin(a) * r}px`;
        chip.style.setProperty('--c', S.last.neutral ? NEUTRAL : color(S.last.id));
        $('chip-name').textContent = S.last.neutral ? S.last.speaker : S.last.label;
        $('chip-arrow').style.transform = `rotate(${S.last.angle - 90}deg)`;
      } else chip.hidden = true;
    } else chip.hidden = true;
    requestAnimationFrame(draw);
  }

  // ---------- transcript ----------
  function receive(m) {
    if (!m || typeof m !== 'object') return;
    if (m.type === 'snapshot') { S.refine = !!m.refine; return Array.isArray(m.captions) && m.captions.forEach(receive); }
    if (m.type === 'sound' && typeof m.angle === 'number') return void S.sounds.push({ angle: m.angle, level: +m.level || 0, t: performance.now() });
    if (m.type === 'summary') {
      // Gemini: summary bullets + learned names (relabel everyone already on screen).
      S.summary = { bullets: Array.isArray(m.bullets) ? m.bullets.map(String).slice(0, 4) : [], model: String(m.model || 'Gemini'), at: Date.now() };
      for (const [id, name] of Object.entries(m.names || {})) {
        if (typeof name !== 'string' || !name) continue;
        if (S.refine && !S.people.has(id)) continue;  // a direction label (?/??): not shown as a person
        color(id); S.people.get(id).label = name;
        S.lines.forEach(l => { if (l.id === id) l.label = name; });
        if (S.partial && S.partial.id === id) S.partial.label = name;
      }
      return render();
    }
    if (m.type !== 'caption' && m.type !== 'refined') return;
    const id = String(m.speaker_id || '?'), session = String(m.session_id), key = session + ':' + String(m.segment_id);
    const text = (m.text || '').trim();
    if (!text) {  // an empty final: the piece was all background chatter, clear its "speaking…" line
      if (m.is_final !== false && S.partial && S.partial.key === key) { S.partial = null; render(); }
      return;
    }
    const refined = m.type === 'refined';
    if (refined) S.refine = true;
    const live = S.refine && !refined;  // live caption while voice-ID lines are coming: faint, no person
    const ts = +m.timestamp || Date.now() / 1000;
    // rx: when this page got it (promotion timing must not compare the Pi's clock with this one's).
    const entry = { key, id, label: live ? 'LIVE' : String(m.speaker_label || id).slice(0, 40),
                    text, angle: typeof m.angle === 'number' ? m.angle : null, final: m.is_final !== false, ts, rx: Date.now() / 1000,
                    speaker: String(m.speaker_label || id).slice(0, 40), t0: refined && typeof m.start === 'number' ? m.start : ts, refined, neutral: live };
    if (!live) {
      color(id); S.people.get(id).label = entry.label;
      S.lines.forEach(l => { if (l.id === id) l.label = entry.label; });  // e.g. a voice just got its name
    }
    if (entry.angle !== null) { S.last = entry; S.chipAt = performance.now(); }
    if (!entry.final) { S.partial = entry; return render(); }
    if (S.partial && S.partial.key === entry.key) S.partial = null;
    if (live) {
      // (a reconnect's snapshot replays lines this page may already have promoted)
      if (!S.live.some(l => l.key === entry.key) && !S.lines.some(l => l.key === entry.key)) S.live.push(entry);
    } else {
      if (refined) {
        const gone = new Set((Array.isArray(m.replaces) ? m.replaces : []).map(seg => session + ':' + seg));
        S.live = S.live.filter(l => !gone.has(l.key));
        S.lines = S.lines.filter(l => !gone.has(l.key));  // promoted live lines it now replaces
      }
      addLine(entry);
    }
    render();
  }
  function addLine(entry) {
    const i = S.lines.findIndex(l => l.key === entry.key);
    if (i >= 0) S.lines[i] = entry; else S.lines.push(entry);
    S.lines.sort((a, b) => a.t0 - b.t0);
    S.lines = S.lines.slice(-200);
  }
  // Live lines that no voice-ID line replaced in time (offline, Scribe error) become ordinary lines,
  // still in the neutral colour and not counted as people (their speakers are directions, not voices).
  setInterval(() => {
    const cutoff = Date.now() / 1000 - PROMOTE_SEC, old = S.live.filter(l => l.rx < cutoff);
    if (!old.length) return;
    S.live = S.live.filter(l => l.rx >= cutoff);
    old.forEach(l => addLine({ ...l, neutral: false, promoted: true, label: l.speaker }));
    render();
  }, 1000);
  function lineEl(e, partial) {
    const el = document.createElement('article');
    el.className = 'line' + (partial ? ' partial' : e.neutral ? ' live' : '');
    el.style.setProperty('--c', e.neutral || e.promoted ? NEUTRAL : color(e.id));
    const who = document.createElement('span'); who.className = 'who'; who.textContent = e.label;
    const meta = document.createElement('span'); meta.className = 'meta';
    meta.textContent = partial ? 'speaking…' : e.neutral ? 'identifying voice…'
      : [dirWord(e.angle), new Date(e.ts * 1000).toLocaleTimeString([], { minute: '2-digit', second: '2-digit' })].filter(Boolean).join(' · ');
    const p = document.createElement('p'); p.textContent = e.text;
    el.append(who, meta);
    if (e.refined) { const b = document.createElement('span'); b.className = 'badge'; b.textContent = 'VOICE-ID'; el.append(b); }
    el.append(p); return el;
  }
  function render() {
    const box = $('lines'), atBottom = box.scrollHeight - box.scrollTop - box.clientHeight < 40;
    box.replaceChildren(...S.lines.map(l => lineEl(l, false)), ...S.live.map(l => lineEl(l, false)),
                        ...(S.partial ? [lineEl(S.partial, true)] : []));
    if (!S.lines.length && !S.live.length && !S.partial) { const p = document.createElement('p'); p.className = 'empty'; p.textContent = 'Captions appear here as people speak.'; box.append(p); }
    if (atBottom) box.scrollTop = box.scrollHeight;
    const people = [...S.people.values()];
    // "01 ━━━ 03": who is talking now (order they were first heard) out of everyone heard.
    const current = (S.partial && !S.partial.neutral && S.partial) || S.lines.filter(l => !l.promoted).pop();
    const ids = [...S.people.keys()], idx = current ? ids.indexOf(current.id) + 1 : 0;
    const pad = n => String(n).padStart(2, '0');
    $('speaker-now').textContent = pad(idx); $('speaker-total').textContent = pad(ids.length);
    $('bar-fill').style.width = ids.length ? `${(idx / ids.length) * 100}%` : '0';
    if (current) $('bar-fill').style.background = color(current.id);
    $('people').textContent = people.length ? `${people.length} ${people.length === 1 ? 'person' : 'people'} heard` : 'No one heard yet';
    const last = S.lines[S.lines.length - 1];
    // Gemini's points; until there are some, the latest line stands in.
    let items = S.summary && S.summary.bullets.length ? S.summary.bullets.map(esc)
      : last ? [`${esc(last.label)}${last.angle !== null ? ' (' + dirWord(last.angle) + ')' : ''}: “${esc(last.text.slice(0, 90))}${last.text.length > 90 ? '…' : ''}”`]
      : ['Waiting for the conversation…'];
    $('summary').innerHTML = items.map(i => `<li>${i}</li>`).join('');
    // Counted from the lines on screen, so a voice-ID line replacing a promoted one isn't counted twice.
    const mentions = S.lines.filter(l => /\bjax\b/i.test(l.text)).length;
    $('chips').innerHTML = people.map(p => `<span class="chip"><i style="background:${p.color}"></i>${esc(p.label)}</span>`).join('')
      + (mentions ? `<span class="chip jax">Jax ×${mentions}</span>` : '');
  }
  const esc = s => s.replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));

  // ---------- connection ----------
  // Served by the Pi's camera_server (http://<pi>:8081/)? Then use the same-origin camera path,
  // which avoids browsers upgrading a separate camera URL to HTTPS.
  const servedByPi = location.protocol === 'http:' && location.port === '8081';
  const cameraUrl = host => (servedByPi && host === location.hostname) ? '/camera/stream' : `http://${host}:8081/camera/stream`;
  function status(text, ok) { $('connection-label').textContent = text; $('connect').classList.toggle('connected', !!ok); }
  function connect(host) {
    stopDemo(); disconnect(true); S.host = host; store('usher.host', host);
    S.refine = false;  // the server's snapshot says whether voice-ID lines are coming
    let everOpened = false;
    const open = () => {
      status('Connecting…');
      try { S.ws = new WebSocket(`ws://${host}:8765`); } catch { status('Bad address'); return; }
      S.ws.onopen = () => { everOpened = true; status('Headset connected', true); };
      S.ws.onmessage = ev => { try { receive(JSON.parse(ev.data)); } catch { } };
      S.ws.onclose = () => {
        if (S.host !== host) return;
        // Never reached it: almost always server.py not running on the Pi, or a different network.
        status(everOpened ? 'Reconnecting…' : `Can't reach ${host} · is server.py running?`);
        S.retry = setTimeout(open, 3000);
      };
    };
    open();
    const img = $('camera');
    img.onload = () => { stage.classList.add('has-camera'); img.hidden = false; };
    img.onerror = () => { stage.classList.remove('has-camera'); img.hidden = true; setTimeout(() => { if (S.host === host) img.src = cameraUrl(host) + `?${Date.now()}`; }, 5000); };
    img.src = cameraUrl(host);
  }
  function disconnect(quiet) {
    S.host = ''; clearTimeout(S.retry); if (S.ws) { S.ws.onclose = null; S.ws.close(); S.ws = null; }
    $('camera').removeAttribute('src'); $('camera').hidden = true; stage.classList.remove('has-camera');
    if (!quiet) status('Connect headset');
  }
  $('connect').onclick = () => { $('host').value = S.host || store('usher.host') || ''; $('dialog').showModal(); };
  // Hosted over HTTPS (GitHub Pages): browsers block plain ws:// and http:// from an https page, so
  // live mode means opening the page the Pi itself serves (same Wi-Fi as the Pi).
  const hosted = location.protocol === 'https:';
  if (hosted) {
    $('connect-help').textContent = 'Live mode runs on the Pi: this opens http://ADDRESS:8081/ (start server.py and camera_server.py, same Wi-Fi as the Pi). Without the headset, use Try demo.';
  }
  $('form').onsubmit = ev => {
    ev.preventDefault();
    const h = $('host').value.trim().replace(/^\w+:\/\//, '').replace(/[:/].*$/, '');
    if (!h) return;
    store('usher.host', h);
    if (hosted) { location.href = `http://${h}:8081/`; return; }
    connect(h); $('dialog').close();
  };
  $('disconnect').onclick = () => { disconnect(); $('dialog').close(); };

  // ---------- demo ----------
  const SCRIPT = [
    ['sajad', 'SAJAD', 70, 'f2', "Hey, are you coming to the café after the workshop?"],
    ['oliver', 'OLIVER', 300, 'f1', "Yeah, give me a minute to pack up."],
    ['sajad', 'SAJAD', 70, 'f2', "Hello Jax, we saved you a seat over here."],
    ['s3', 'SPEAKER 3', 180, null, "Can I come too? I've been stuck on this all day."],
    ['oliver', 'OLIVER', 300, 'f1', "Of course, the more the merrier."],
  ];
  function stopDemo() { clearInterval(S.demoTimer); S.demo = false; $('demo').textContent = 'Try demo'; document.querySelectorAll('.figure').forEach(f => f.classList.remove('speaking')); }
  $('demo').onclick = () => {
    if (S.demo) return stopDemo();
    disconnect(true); status('Demo mode'); S.demo = true; S.refine = true; $('demo').textContent = 'End demo';
    let i = 0, word = 0, pause = 0;
    S.demoTimer = setInterval(() => {
      if (pause) { pause--; return; }
      const [id, label, angle, fig, text] = SCRIPT[i % SCRIPT.length], words = text.split(' ');
      word++;
      document.querySelectorAll('.figure').forEach(f => f.classList.toggle('speaking', !!fig && f.classList.contains(fig)));
      S.sounds.push({ angle: angle + (Math.random() * 10 - 5), level: 0.03 + Math.random() * 0.04, t: performance.now() });
      receive({ type: 'caption', session_id: 'demo', segment_id: i, speaker_id: id, speaker_label: label, text: words.slice(0, word).join(' '), is_final: word >= words.length, angle, timestamp: Date.now() / 1000, source: 'demo' });
      if (word >= words.length) {
        // ~1.4 s later the voice-identified line replaces the faint live one, as on the headset.
        const seg = i, t0 = Date.now() / 1000;
        setTimeout(() => S.demo && receive({ type: 'refined', session_id: 'demo', segment_id: `voice-${seg}`, replaces: [seg],
          speaker_id: id, speaker_label: label, text, angle, start: t0, is_final: true, timestamp: Date.now() / 1000, source: 'demo' }), 1400);
        i++; word = 0; pause = 6;
        if (i === 3) receive({ type: 'summary', model: 'Gemini (demo)', names: {},
          bullets: ['Sajad saved Jax a seat and invited them to the café.', 'Oliver is packing up and will join.'] });
      }
    }, 240);
  };

  layoutTunnel(); render(); requestAnimationFrame(draw);
  const saved = store('usher.host'); if (saved) $('host').value = saved;
  if (servedByPi) connect(location.hostname);  // opened from the Pi itself: connect straight away
})();
