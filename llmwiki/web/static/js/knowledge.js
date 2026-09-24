/* Knowledge — 그래프(provenance 필터), 위키, 그래프 규칙. */
(function (LW) {
  'use strict';
  const { $, $$, esc, fmt, api, toast, STATE, PALETTE, switchTab, switchGroup, loaders } = LW;

  // ---------------- DOC (문서 한 건) ----------------
  // 채널 검색·질의 결과에서 문서를 누르면 여기로 온다. 서버는 querydebug.doc_detail — MCP wiki_doc 와 같은 함수다.
  async function openDocPage(id) {
    if (!id) return;
    switchGroup('knowledge'); switchTab('doc');
    $('#doc-id').value = id;
    await loadDoc(id);
  }
  async function loadDoc(id) {
    const ident = (id || $('#doc-id').value || '').trim();
    if (!ident) { $('#doc-out').innerHTML = '<div class="muted">문서 id 를 넣고 [열기] 를 누르세요.</div>'; return; }
    $('#doc-out').innerHTML = '<div class="muted small">읽는 중…</div>';
    const d = await api('/api/doc?id=' + encodeURIComponent(ident));
    if (!d || d.error) { $('#doc-out').innerHTML = `<div class="bad">${esc((d && d.error) || '문서를 읽지 못했습니다')}</div>`; return; }
    const m = d.meta || {};
    const fold = $('#doc-fold') && $('#doc-fold').checked;
    const metaRows = Object.keys(m).filter((k) => m[k] != null && m[k] !== '' && !(Array.isArray(m[k]) && !m[k].length))
      .map((k) => `<tr><th>${esc(k)}</th><td>${esc(Array.isArray(m[k]) ? m[k].join(', ') : String(m[k]))}</td></tr>`).join('');
    $('#doc-out').innerHTML =
      `<div class="dbg-sec"><h4>${esc(d.doc_id)} <span class="muted small">청크 ${d.n_chunks}개</span></h4>` +
      (metaRows ? `<table class="dbg-kv">${metaRows}</table>` : '<div class="muted small">front matter 메타가 없습니다.</div>') +
      (d.alternatives && d.alternatives.length
        ? `<div class="muted small">같은 이름의 다른 문서: ${d.alternatives.map((x) => `<a href="#" data-docgo="${esc(x)}">${esc(x)}</a>`).join(' · ')}</div>` : '') +
      '</div>' +
      (d.relations && d.relations.length
        ? '<div class="dbg-sec"><h4>관계</h4><div class="tbl-wrap"><table class="dbg-kv"><tr><th>src</th><th>rel</th><th>dst</th><th>출처</th></tr>' +
          d.relations.map((r) => `<tr><td><a href="#" data-entgo="${esc(r.src)}">${esc(r.src)}</a></td><td>${esc(r.rel)}</td><td><a href="#" data-entgo="${esc(r.dst)}">${esc(r.dst)}</a></td><td class="muted">${esc(r.provenance || '')}</td></tr>`).join('') +
          '</table></div></div>' : '') +
      '<div class="dbg-sec"><h4>청크</h4><div class="tbl-wrap"><table class="dbg-kv"><tr><th>chunk</th><th>heading</th><th>글자</th></tr>' +
      (d.chunks || []).map((c) => `<tr><td class="mono small">${esc(c.chunk_id || '')}</td><td>${esc(c.heading || '')}</td><td class="num">${c.chars}</td></tr>`).join('') +
      '</table></div>' +
      (fold ? '' : (d.chunks || []).map((c) => `<details class="doc-chunk"><summary><code>${esc(c.chunk_id || '')}</code> ${esc(c.heading || '')}</summary><pre class="pre">${esc(c.text || '')}</pre></details>`).join('')) +
      '</div>';
    $$('#doc-out [data-docgo]').forEach((a) => a.onclick = (e) => { e.preventDefault(); openDocPage(a.dataset.docgo); });
    $$('#doc-out [data-entgo]').forEach((a) => a.onclick = (e) => { e.preventDefault(); switchTab('graph'); if (LW.openEntity) LW.openEntity(a.dataset.entgo); });
  }
  if ($('#btn-doc')) $('#btn-doc').onclick = () => loadDoc();
  if ($('#doc-id')) $('#doc-id').addEventListener('keydown', (e) => { if (e.key === 'Enter') loadDoc(); });
  if ($('#doc-fold')) $('#doc-fold').onchange = () => loadDoc();
  loaders.doc = () => loadDoc();
  LW.openDocPage = openDocPage;

  // ---------------- GRAPH (지식 › 그래프) ----------------
  // 2026-09-24 다시 씀. 사용자가 "노드 수·커뮤니티·출처 c0/c1 이 뭔지 모르겠다" 고 했다 — 화면이 용어를 직접 설명하고,
  // 보기 모드(개요·이웃·무리 하나·유형별)로 더 자세히·다양하게 본다. 서버는 /api/graph(필터·중심·간선 종류) · /api/community.
  //   상위 N개  = 연결(관계 행 수)이 많은 순 N개 — 전체 그래프가 아니다 (전체 수를 옆에 보여 준다)
  //   무리      = 라벨 전파가 묶은 엔티티 집합. 번호는 순번일 뿐 뜻이 없고(0 = 연결 최다 노드의 무리) 재빌드하면 바뀐다. 이름 = 대표 엔티티 3개 / LLM 요약 첫 문장
  //   관계 출처 = explicit(front matter related) · rule(규칙 정규식) · human(사람 승인) · llm(LLM 추출) · cooccur(같은 청크에 함께 등장)
  //   연결/이웃 = 연결은 관계 행 수(청크마다 셈, 부풀려짐) · 이웃은 서로 다른 이웃 노드 수
  const G = { nodes: [], edges: [], sel: null, hover: null, drag: null, running: false, tx: 0, ty: 0, scale: 1, raf: 0, iter: 0, view: 'overview', data: null };
  const PROV_LABEL = { explicit: 'explicit — front matter related 링크', rule: 'rule — 규칙 정규식·ID 링크', human: 'human — 사람이 승인', llm: 'llm — LLM 추출', cooccur: 'cooccur — 같은 청크에 함께 등장' };
  const PROV_COLOR = { explicit: '#1baf7a', rule: '#2a78d6', human: '#2a78d6', llm: '#e87ba4', cooccur: '' };
  const TYPE_COLOR_CACHE = {};
  function cssVar(n, d) { return (getComputedStyle(document.documentElement).getPropertyValue(n) || '').trim() || d; }
  function commColor(c) { return c == null || c < 0 ? '#898781' : PALETTE[c % PALETTE.length]; }
  function typeColor(t) { if (!(t in TYPE_COLOR_CACHE)) TYPE_COLOR_CACHE[t] = PALETTE[Object.keys(TYPE_COLOR_CACHE).length % PALETTE.length]; return TYPE_COLOR_CACHE[t]; }
  function nodeColor(n) { return G.view === 'types' ? typeColor(n.type || '?') : commColor(n.community); }
  function edgeColor(e, hi) { if (hi) return cssVar('--ink', '#0b0b0b'); return PROV_COLOR[e.provenance || ''] || cssVar('--grid', '#e1e0d9'); }
  function commLabel(c) {
    if (!c) return '';
    if (c.source === 'llm' && c.summary) { const first = String(c.summary).split(/\. |다\./)[0].trim(); if (first.length >= 4 && first.length <= 60) return first; }
    let top = []; try { top = JSON.parse(c.top_entities || '[]'); } catch (e) { top = []; }
    return (top || []).slice(0, 3).join(', ') || ('무리 ' + c.community);
  }
  function graphQuery() {
    const view = $('#g-view').value; G.view = view;
    const lim = Math.max(1, Number($('#g-limit').value) || 120);
    const comm = $('#g-comm').value, prov = $('#g-prov').value, types = $('#g-types').value.trim();
    const center = ($('#g-center').value || '').trim(), hops = $('#g-hops').value || '1';
    const structure = $('#g-structure').checked;
    const q = [`limit=${view === 'community' ? Math.max(lim, 400) : lim}`];
    if (comm && comm !== 'all') q.push('community=' + encodeURIComponent(comm));
    if (prov) q.push('provenance=' + encodeURIComponent(prov));
    if (types) q.push('types=' + encodeURIComponent(types));
    if (structure) q.push('edge_kinds=structure');
    if (view === 'neighbors') { if (!center) return null; q.push('center=' + encodeURIComponent(center) + '&hops=' + encodeURIComponent(hops)); }
    return '/api/graph?' + q.join('&');
  }
  async function loadGraph() {
    const view = $('#g-view').value;
    if (view === 'community' && ($('#g-comm').value === 'all' || $('#g-comm').value === '')) {
      // 무리 목록이 아직 없으면 개요를 먼저 받아 목록을 채운다
      if (!G.data) await loadGraphData('/api/graph?limit=' + (Number($('#g-limit').value) || 120));
      $('#g-summary').innerHTML = '<span class="warntxt">무리를 고르세요</span> — 오른쪽 목록의 [이 무리만] 이나 위의 무리 선택.';
      return;
    }
    const url = graphQuery();
    if (!url) { $('#g-summary').innerHTML = '<span class="warntxt">중심 엔티티를 넣으세요</span> — 노드를 클릭한 뒤 상세의 [이 노드 중심으로] 를 눌러도 됩니다.'; return; }
    await loadGraphData(url);
    if (view === 'community') showCommunity(Number($('#g-comm').value));
  }
  async function loadGraphData(url) {
    $('#g-summary').innerHTML = '<span class="muted">불러오는 중…</span>';
    const g = await api(url);
    if (!g || g.error) { $('#g-summary').innerHTML = `<span class="bad">${esc((g && g.error) || '그래프를 읽지 못했습니다')}</span>`; return; }
    STATE.graph = g; G.data = g;
    // 무리 선택 목록 (그려진 노드가 속한 무리만 — 전체는 all_communities_n)
    const sel = $('#g-comm'); const cur = sel.value;
    sel.innerHTML = '<option value="all">전체</option>' + (g.communities || []).map((c) => `<option value="${c.community}">무리 #${c.community} · ${esc(commLabel(c))} (${c.size})</option>`).join('');
    if (Array.from(sel.options).some((o) => o.value === cur)) sel.value = cur;
    renderSummary(g);
    renderCommunities(g);
    layoutInit(g);
    renderLegend(g);
  }
  function renderSummary(g) {
    const ec = g.edge_counts_shown || {}; const total = Object.values(ec).reduce((a, b) => a + b, 0) || 0;
    const bar = Object.entries(ec).sort((a, b) => b[1] - a[1]).map(([k, v]) => `<i title="${esc(PROV_LABEL[k] || k)}: ${v}" style="width:${Math.max(2, (v / (total || 1)) * 100)}%;background:${PROV_COLOR[k] || cssVar('--axis', '#c3c2b7')}"></i>`).join('');
    // 전체 규모를 **먼저** — 몇 개를 볼지는 전체가 몇 개인지 알아야 정할 수 있다 (2026-09-24 사용자 지적)
    const cand = g.candidates || g.total_entities || 0;
    const pctShown = cand ? Math.round(100 * g.shown / cand) : 0;
    const byType = Object.entries(g.entities_by_type || {}).slice(0, 8).map(([t, n]) => `${esc(t)} ${n}`).join(' · ');
    const whole = `<div class="g-total"><b>전체 그래프</b>: 엔티티 <b>${g.total_entities}</b>개 <span class="muted">(그릴 수 있는 것 ${cand} — 날짜·금액 제외)</span> · 관계 <b>${(g.total_relations || 0).toLocaleString()}</b>개 <span class="muted">(구조 ${(g.total_structural || 0).toLocaleString()} · 공동출현 ${(g.total_cooccur || 0).toLocaleString()})</span> · 무리 ${g.all_communities_n}개` +
      (byType ? `<div class="muted small">유형별: ${byType}</div>` : '') + '</div>';
    $('#g-total-hint').textContent = cand ? `= 전체의 ${pctShown}%` : '';
    const parts = [];
    if (g.center) parts.push(`<b>이웃 보기</b>: <b>${esc(g.nodes[0] ? g.nodes[0].name : g.center)}</b> 에서 ${g.hops}홉 안 ${g.shown}개`);
    else parts.push(`<b>지금 그린 것: 상위 ${g.shown}개</b> <span class="muted">(연결 많은 순 · 그릴 수 있는 ${cand}개의 ${pctShown}%)</span>`);
    parts.push(`간선 ${g.edges.length}개${g.edge_kinds === 'structure' ? ' <span class="muted">(공동출현 숨김 — 전체 관계 중 ' + (g.total_relations_kept || 0).toLocaleString() + '개가 구조 관계)</span>' : ''}`);
    parts.push(`무리 ${(g.communities || []).length}개 <span class="muted">(그려진 노드가 속한 것)</span>`);
    $('#g-summary').innerHTML = whole + parts.join(' · ') + `<div class="g-bar" title="그려진 간선의 출처 비율">${bar}</div>` +
      `<span class="muted small">간선 출처: ${Object.entries(ec).map(([k, v]) => `<i class="dot" style="background:${PROV_COLOR[k] || cssVar('--axis', '#c3c2b7')}"></i>${esc(k)} ${v}`).join(' · ') || '-'}</span>`;
  }
  function renderCommunities(g) {
    const rows = (g.communities || []).slice().sort((a, b) => b.size - a.size);
    $('#g-comms').innerHTML = rows.length ? rows.map((c) => `<div class="g-comm"><b><i class="dot" style="background:${commColor(c.community)}"></i>무리 #${c.community}</b> ${esc(commLabel(c))}
      <div class="muted small">${c.size}개 (여기 ${c.shown || 0}개 표시) · 요약: ${c.source === 'llm' ? 'LLM 작성' : '규칙 기본문(대표 엔티티 나열)'}</div>
      <div class="small">${esc((c.summary || '').slice(0, 160))}</div>
      <button class="secondary small" data-comm-view="${c.community}">이 무리만</button> <button class="secondary small" data-comm-detail="${c.community}">안 보기</button></div>`).join('')
      : '<div class="muted small">그려진 노드가 속한 무리가 없습니다 (무리 탐지 전이면 그래프 진단 탭의 소견을 보세요).</div>';
    $$('#g-comms [data-comm-view]').forEach((b) => b.onclick = () => { $('#g-view').value = 'community'; $('#g-comm').value = b.dataset.commView; loadGraph(); });
    $$('#g-comms [data-comm-detail]').forEach((b) => b.onclick = () => showCommunity(Number(b.dataset.commDetail)));
  }
  function renderLegend(g) {
    const comms = Array.from(new Set(G.nodes.map((n) => n.community))).sort((a, b) => a - b);
    const byId = {}; (g.communities || []).forEach((c) => { byId[c.community] = c; });
    const colorKey = G.view === 'types'
      ? Array.from(new Set(G.nodes.map((n) => n.type || '?'))).map((t) => `<span><i style="background:${typeColor(t)}"></i>${esc(t)}</span>`).join('')
      : comms.map((c) => `<span title="${esc(commLabel(byId[c]))}"><i style="background:${commColor(c)}"></i>${c < 0 ? '무리 없음(날짜·금액·문서)' : '무리 #' + c + (byId[c] ? ' ' + esc(commLabel(byId[c])).slice(0, 24) : '')}</span>`).join('');
    $('#g-legend').innerHTML = `<span class="muted">색 = ${G.view === 'types' ? '유형' : '무리'}:</span>` + colorKey +
      `<span class="muted">· 간선 색:</span><span><i class="line" style="background:#1baf7a"></i>explicit</span><span><i class="line" style="background:#2a78d6"></i>rule/human</span><span><i class="line" style="background:#e87ba4"></i>llm</span><span><i class="line" style="background:${cssVar('--grid', '#e1e0d9')}"></i>cooccur</span>` +
      '<span class="muted">· 크기 = 연결 수 · 굵은 테두리 = 추출기 둘 이상 합의 · 클릭 = 상세 · 휠 = 확대 · 드래그 = 이동</span>';
  }
  function layoutInit(g) {
    const cv = $('#gcanvas'); const dpr = window.devicePixelRatio || 1;
    const cssH = cv.clientHeight || 600;
    const W = cv.width = cv.clientWidth * dpr; const H = cv.height = cssH * dpr;
    const idx = {}; const N = g.nodes.length;
    const typesOrder = Array.from(new Set(g.nodes.map((n) => n.type || '?')));
    G.nodes = g.nodes.map((n, i) => {
      idx[n.id] = i;
      let x, y;
      if (G.view === 'types') { const col = typesOrder.indexOf(n.type || '?'); x = (col + 0.5) / typesOrder.length * W; y = (0.15 + 0.7 * Math.random()) * H; }
      else { const a = i / N * Math.PI * 2; x = W / 2 + Math.cos(a) * W / 3 * (0.5 + Math.random() / 2); y = H / 2 + Math.sin(a) * H / 3 * (0.5 + Math.random() / 2); }
      return Object.assign({}, n, { x, y, vx: 0, vy: 0, col: typesOrder.indexOf(n.type || '?') });
    });
    G.typesOrder = typesOrder;
    G.edges = g.edges.filter((e) => e.src in idx && e.dst in idx).map((e) => Object.assign({}, e, { a: idx[e.src], b: idx[e.dst] }));
    G.nb = {}; G.edges.forEach((e) => { (G.nb[e.src] = G.nb[e.src] || new Set()).add(e.dst); (G.nb[e.dst] = G.nb[e.dst] || new Set()).add(e.src); });
    G.W = W; G.H = H; G.sel = null; G.hover = null; G.iter = 0; G.running = true; G.tx = 0; G.ty = 0; G.scale = 1;
    G.labelTop = new Set(G.nodes.slice().sort((a, b) => (b.degree || 0) - (a.degree || 0)).slice(0, 40).map((n) => n.id));
    if (!G.raf) tick();
  }
  function radius(n) { const dpr = window.devicePixelRatio || 1; return Math.min(28, 4 + Math.sqrt(n.degree || 1) * 1.2) * dpr; }
  function tick() {
    const cv = $('#gcanvas'); if (!cv || !cv.isConnected) { G.raf = 0; return; }
    const ctx = cv.getContext('2d'); const { nodes, edges, W, H } = G;
    if (G.running && nodes.length) {
      G.iter++;
      const k = Math.sqrt((W * H) / (nodes.length + 1)) * 0.6;
      for (const n of nodes) { n.fx = 0; n.fy = 0; }
      for (let i = 0; i < nodes.length; i++) for (let j = i + 1; j < nodes.length; j++) {
        const a = nodes[i], b = nodes[j]; let dx = a.x - b.x, dy = a.y - b.y; const d2 = dx * dx + dy * dy + 0.01; if (d2 > k * k * 16) continue;
        const f = (k * k) / d2; dx *= f; dy *= f; a.fx += dx; a.fy += dy; b.fx -= dx; b.fy -= dy;
      }
      for (const e of edges) { const a = nodes[e.a], b = nodes[e.b]; const dx = b.x - a.x, dy = b.y - a.y; const d = Math.sqrt(dx * dx + dy * dy) + 0.01; const f = (d - k * 0.8) / d * 0.05 * Math.min(2, e.weight); a.fx += dx * f; a.fy += dy * f; b.fx -= dx * f; b.fy -= dy * f; }
      const cool = Math.max(0.02, 1 - G.iter / 300);
      for (const n of nodes) {
        if (n === G.drag) continue;
        if (G.view === 'types') { const cx = (n.col + 0.5) / G.typesOrder.length * W; n.fx += (cx - n.x) * 0.02; n.fy += (H / 2 - n.y) * 0.002; }
        else { n.fx += (W / 2 - n.x) * 0.002; n.fy += (H / 2 - n.y) * 0.002; }
        n.vx = (n.vx + n.fx * 0.01) * 0.6; n.vy = (n.vy + n.fy * 0.01) * 0.6; const sp = Math.sqrt(n.vx * n.vx + n.vy * n.vy); const mx = 8 * cool + 0.5; if (sp > mx) { n.vx *= mx / sp; n.vy *= mx / sp; }
        n.x = Math.max(20, Math.min(W - 20, n.x + n.vx)); n.y = Math.max(20, Math.min(H - 20, n.y + n.vy));
      }
      if (G.iter > 400 && !G.drag) G.running = false;
    }
    ctx.clearRect(0, 0, W, H); ctx.save(); ctx.translate(G.tx, G.ty); ctx.scale(G.scale, G.scale);
    const find = ($('#g-find').value || '').toLowerCase();
    const selId = G.sel ? G.sel.id : null; const nbSel = selId ? (G.nb[selId] || new Set()) : null;
    const dpr = window.devicePixelRatio || 1; const ink = cssVar('--ink', '#0b0b0b');
    if (G.view === 'types' && G.typesOrder) { ctx.fillStyle = cssVar('--muted', '#898781'); ctx.font = `${12 * dpr}px sans-serif`; G.typesOrder.forEach((t, i) => ctx.fillText(t, (i + 0.5) / G.typesOrder.length * W - 20, 16 * dpr)); }
    for (const e of edges) { const a = nodes[e.a], b = nodes[e.b]; const hi = selId && (a.id === selId || b.id === selId); ctx.globalAlpha = selId && !hi ? 0.15 : 1; ctx.strokeStyle = edgeColor(e, hi); ctx.lineWidth = hi ? 2 : Math.min(3, 0.5 + e.weight * 0.5); ctx.beginPath(); ctx.moveTo(a.x, a.y); ctx.lineTo(b.x, b.y); ctx.stroke(); }
    ctx.globalAlpha = 1;
    for (const n of nodes) {
      const r = radius(n); const hit = find && n.name.toLowerCase().includes(find);
      const dim = (find && !hit) || (selId && n.id !== selId && !nbSel.has(n.id));
      ctx.beginPath(); ctx.arc(n.x, n.y, r, 0, Math.PI * 2); ctx.fillStyle = nodeColor(n); ctx.globalAlpha = dim ? 0.2 : 1; ctx.fill(); ctx.globalAlpha = 1;
      ctx.lineWidth = (n.source || '').includes('+') ? 3 : 1; ctx.strokeStyle = n === G.sel || hit || n === G.hover ? ink : '#fff'; ctx.stroke();
      if ((G.labelTop.has(n.id) && !dim) || n === G.sel || hit || n === G.hover || (selId && nbSel.has(n.id))) { ctx.fillStyle = ink; ctx.font = `${11 * dpr}px sans-serif`; ctx.fillText(n.name.slice(0, 22), n.x + r + 2, n.y + 4); }
    }
    ctx.restore();
    if (G.running || G.drag || G.hover || find || G.needsDraw) { G.needsDraw = false; G.raf = requestAnimationFrame(tick); } else { G.raf = 0; }
  }
  function redraw() { G.needsDraw = true; if (!G.raf) tick(); }
  (function graphEvents() {
    const cv = $('#gcanvas'); const tip = $('#g-tip');
    const dpr = () => window.devicePixelRatio || 1;
    const pos = (ev) => { const rc = cv.getBoundingClientRect(); const d = dpr(); return { x: ((ev.clientX - rc.left) * d - G.tx) / G.scale, y: ((ev.clientY - rc.top) * d - G.ty) / G.scale }; };
    const at = (p) => { let best = null, bd = 1e9; for (const n of G.nodes) { const dd = Math.hypot(n.x - p.x, n.y - p.y); const r = radius(n) + 3; if (dd < r && dd < bd) { best = n; bd = dd; } } return best; };
    cv.onmousedown = (ev) => { const n = at(pos(ev)); if (n) { G.drag = n; G.dragMoved = false; G.running = true; G.iter = Math.min(G.iter, 250); } else { G.pan = { x: ev.clientX * dpr() - G.tx, y: ev.clientY * dpr() - G.ty }; } redraw(); };
    cv.onmousemove = (ev) => {
      if (G.drag) { const p = pos(ev); G.drag.x = p.x; G.drag.y = p.y; G.dragMoved = true; redraw(); return; }
      if (G.pan) { G.tx = ev.clientX * dpr() - G.pan.x; G.ty = ev.clientY * dpr() - G.pan.y; redraw(); return; }
      const n = at(pos(ev));
      if (n !== G.hover) { G.hover = n; redraw(); }
      if (n && tip) {
        const rc = cv.getBoundingClientRect();
        tip.classList.remove('hidden'); tip.style.left = (ev.clientX - rc.left + 12) + 'px'; tip.style.top = (ev.clientY - rc.top + 12) + 'px';
        tip.innerHTML = `<b>${esc(n.name)}</b> <span class="pill">${esc(n.type)}</span><br>연결 ${n.degree} · 이웃 ${n.neighbors != null ? n.neighbors : '-'} · 문서 ${n.n_docs || 0}<br>${n.community == null || n.community < 0 ? '무리 없음' : '무리 #' + n.community}${n.source && n.source.includes('+') ? ' · 추출기 합의' : ''}`;
      } else if (tip) tip.classList.add('hidden');
    };
    cv.onmouseup = () => { if (G.drag) { const n = G.drag; G.drag = null; G.sel = n; showEntity(n.id); } G.pan = null; redraw(); };
    cv.onmouseleave = () => { G.drag = null; G.pan = null; G.hover = null; if (tip) tip.classList.add('hidden'); redraw(); };
    cv.onwheel = (ev) => {
      ev.preventDefault(); const f = ev.deltaY < 0 ? 1.1 : 0.9; const ns = Math.max(0.3, Math.min(4, G.scale * f));
      const rc = cv.getBoundingClientRect(); const d = dpr(); const mx = (ev.clientX - rc.left) * d, my = (ev.clientY - rc.top) * d;
      // 커서 위치가 고정되도록 (확대 중심 = 커서)
      G.tx = mx - (mx - G.tx) * (ns / G.scale); G.ty = my - (my - G.ty) * (ns / G.scale); G.scale = ns; redraw();
    };
    $('#btn-g-fit').onclick = () => {
      if (!G.nodes.length) return;
      const xs = G.nodes.map((n) => n.x), ys = G.nodes.map((n) => n.y);
      const minx = Math.min(...xs) - 40, maxx = Math.max(...xs) + 40, miny = Math.min(...ys) - 40, maxy = Math.max(...ys) + 40;
      G.scale = Math.max(0.3, Math.min(4, Math.min(G.W / (maxx - minx), G.H / (maxy - miny))));
      G.tx = (G.W - (maxx + minx) * G.scale) / 2; G.ty = (G.H - (maxy + miny) * G.scale) / 2; redraw();
    };
    $('#btn-g-reset').onclick = () => { G.tx = 0; G.ty = 0; G.scale = 1; G.sel = null; $('#g-find').value = ''; redraw(); };
    $('#g-find').addEventListener('input', redraw);
    // 상위 N 빠른 선택 — '전체' 는 그릴 수 있는 엔티티 수(날짜·금액 제외)로 채운다 (큰 그래프는 느릴 수 있어 확인을 받는다)
    $$('[data-g-limit]').forEach((b) => b.onclick = () => {
      const v = b.dataset.gLimit;
      if (v === 'all') { const n = (G.data && G.data.candidates) || 0; if (n > 1500 && !confirm(`엔티티 ${n}개를 전부 그립니다 — 배치에 시간이 걸릴 수 있습니다. 계속할까요?`)) return; $('#g-limit').value = n || 5000; }
      else $('#g-limit').value = v;
      $('#g-view').value = 'overview'; loadGraph();
    });
    $('#g-view').addEventListener('change', () => { const v = $('#g-view').value; $('#g-center').style.display = v === 'neighbors' ? '' : 'none'; $('#g-hops').style.display = v === 'neighbors' ? '' : 'none'; });
    window.addEventListener('resize', () => { if (G.data && $('#tab-graph').classList.contains('active')) layoutInit(G.data); });
  })();
  async function showCommunity(cid) {
    if (cid == null || isNaN(cid)) return;
    const d = await api('/api/community?id=' + encodeURIComponent(cid));
    if (!d || d.error) { $('#g-detail').innerHTML = `<div class="muted">무리를 찾지 못했습니다: #${esc(cid)}</div>`; return; }
    const ec = d.edge_counts || {};
    $('#g-detail').innerHTML = `<h3 style="margin-top:0"><i class="dot" style="background:${commColor(d.community)}"></i>무리 #${d.community} — ${esc(d.label)}</h3>
      <div class="small">구성원 ${d.size}개 · 안쪽 관계 ${d.edges.length}개 (구조 관계 ${d.structural_edges}) · 요약: ${d.summary_source === 'llm' ? 'LLM 작성' : '규칙 기본문'}</div>
      <p class="small">${esc(d.summary)}</p>
      <div class="muted small">번호는 무리 탐지(라벨 전파)가 붙인 순번이라 뜻이 없습니다. 0 은 연결이 가장 많은 노드가 속한 무리이고, 재빌드하면 바뀔 수 있습니다.</div>
      <div class="small">유형: ${Object.entries(d.types).map(([t, n]) => `<span class="pill">${esc(t)} ${n}</span>`).join(' ')}</div>
      <div class="small">관계 출처: ${Object.entries(ec).map(([k, v]) => `<span class="pill">${esc(k)} ${v}</span>`).join(' ') || '-'}</div>
      <h3>구성원 (연결 많은 순)</h3><table><tr><th>엔티티</th><th>유형</th><th>연결</th><th>문서</th></tr>${d.members.slice(0, 40).map((m) => `<tr><td><a href="#" data-ent="${esc(m.id)}">${esc(m.name)}</a></td><td><span class="pill">${esc(m.type)}</span></td><td class="num">${m.degree}</td><td class="num">${m.n_docs}</td></tr>`).join('')}</table>
      ${d.docs.length ? '<h3>관련 문서</h3><table><tr><th>문서</th><th>제목</th><th>언급</th></tr>' + d.docs.slice(0, 12).map((x) => `<tr><td><a href="#" data-doc="${esc(x.doc_id)}">${esc(x.doc_id)}</a></td><td class="muted">${esc(x.title || '')}</td><td class="num">${x.mentions}</td></tr>`).join('') + '</table>' : ''}
      ${d.edges.length ? '<h3>안쪽 관계 (무거운 순)</h3><table>' + d.edges.slice(0, 25).map((r) => `<tr><td>${esc(r.src_name)}</td><td><b>${esc(r.rel)}</b></td><td>${esc(r.dst_name)}</td><td><span class="pill">${esc(r.provenance)}</span></td><td class="num">${fmt(r.weight, 2)}</td></tr>`).join('') + '</table>' : ''}`;
    $$('#g-detail a[data-ent]').forEach((a) => a.onclick = (ev) => { ev.preventDefault(); showEntity(a.dataset.ent); });
    $$('#g-detail a[data-doc]').forEach((a) => a.onclick = (ev) => { ev.preventDefault(); LW.openDocPage(a.dataset.doc); });
  }
  async function showEntity(id, byName) {
    // byName=true 면 이름으로 찾는다 — id 규칙(e:소문자_언더바)을 화면이 만들어 내지 않게 서버가 해석한다.
    const d = await api('/api/entity?' + (byName ? 'name=' : 'id=') + encodeURIComponent(id));
    if (!d || !d.entity) {
      $('#g-detail').innerHTML = `<div class="muted">엔티티를 찾지 못했습니다: <b>${esc(id)}</b>` + '<div class="small">그래프에 없는 이름이거나, 빌드 뒤에 사라진 엔티티입니다.</div></div>';
      return;
    }
    const e = d.entity; const refs = e.doc_refs || [];
    let aliases = []; try { aliases = JSON.parse(e.aliases || '[]'); } catch (x) { aliases = []; }
    const rels = d.relations || []; const byProv = {}; rels.forEach((r) => { const k = r.provenance || r.source || '?'; byProv[k] = (byProv[k] || 0) + 1; });
    const provTabs = ['all', ...Object.keys(byProv)];
    const srcNote = (e.source || '').includes('+') ? '추출기 둘 이상이 합의' : e.source === 'rule' ? '규칙 사전·패턴에서' : e.source === 'llm' ? 'LLM 추출' : e.source === 'evolve' ? '제안 승인으로 추가' : esc(e.source || '');
    $('#g-detail').innerHTML = `<h3 style="margin-top:0">${esc(e.name)} <span class="pill">${esc(e.type)}</span></h3>
      <div class="small">연결 ${e.degree} <span class="muted">(관계 행 수)</span> · 문서 ${e.n_docs || refs.length}개 · ${e.community == null || e.community < 0 ? '무리 없음' : `<a href="#" data-comm="${e.community}">무리 #${e.community}</a>`} · 출처: ${srcNote}</div>
      ${e.description ? `<p class="small">${esc(e.description)}</p>` : ''}
      ${aliases.length ? `<div class="small">별칭: ${aliases.map((a) => `<span class="pill">${esc(a)}</span>`).join(' ')}</div>` : ''}
      <div class="row"><button class="secondary small" id="btn-g-center-me">이 노드 중심으로 보기 (이웃)</button></div>
      <h3>관계 (${rels.length}) <small class="muted">출처별:</small> ${provTabs.map((k) => `<button class="secondary small g-prov-tab${k === 'all' ? ' on' : ''}" data-prov="${esc(k)}">${k === 'all' ? '전부' : esc(k) + ' ' + byProv[k]}</button>`).join(' ')}</h3>
      <div id="g-rels"></div>
      <h3>문서 참조</h3><table><tr><th>문서</th><th>제목</th><th>언급</th><th>첫 청크</th></tr>${refs.map((r) => `<tr><td><a href="#" data-doc="${esc(r.doc_id)}">${esc(r.doc_id)}</a></td><td class="muted">${esc(r.title || '')}</td><td class="num">${r.mentions}</td><td class="muted small">${esc(r.first_chunk || '')}</td></tr>`).join('')}</table>
      <h3>근거 문단</h3>` + (d.mentions || []).map((m) => `<div class="hit"><div class="h">${esc(m.chunk_id)} · ${esc(m.heading)}</div><div class="t">${esc(m.text)}</div></div>`).join('');
    const renderRels = (prov) => { $('#g-rels').innerHTML = '<table>' + rels.filter((r) => prov === 'all' || (r.provenance || r.source) === prov).slice(0, 60).map((r) => `<tr><td><a href="#" data-ent="${esc(r.src)}">${esc(r.src_name)}</a></td><td><b>${esc(r.rel)}</b></td><td><a href="#" data-ent="${esc(r.dst)}">${esc(r.dst_name)}</a></td><td class="num">${fmt(r.weight, 2)}</td><td><span class="pill">${esc(r.provenance || r.source)}</span></td><td class="num muted">${fmt(r.confidence, 2)}</td></tr>`).join('') + '</table>'; $$('#g-rels a[data-ent]').forEach((a) => a.onclick = (ev) => { ev.preventDefault(); showEntity(a.dataset.ent); }); };
    renderRels('all');
    $$('#g-detail .g-prov-tab').forEach((b) => b.onclick = () => { $$('#g-detail .g-prov-tab').forEach((x) => x.classList.remove('on')); b.classList.add('on'); renderRels(b.dataset.prov); });
    $$('#g-detail a[data-doc]').forEach((a) => a.onclick = (ev) => { ev.preventDefault(); LW.openDocPage(a.dataset.doc); });
    $$('#g-detail a[data-comm]').forEach((a) => a.onclick = (ev) => { ev.preventDefault(); showCommunity(Number(a.dataset.comm)); });
    const cm = $('#btn-g-center-me'); if (cm) cm.onclick = () => { $('#g-view').value = 'neighbors'; $('#g-center').style.display = ''; $('#g-hops').style.display = ''; $('#g-center').value = e.name; loadGraph(); };
  }
  $('#btn-graph').onclick = loadGraph;
  loaders.graph = () => { if (!G.data) loadGraph(); };
  // 엔티티 상세를 다른 화면에서도 열 수 있게 내보낸다 (2026-09-20).
  // `LW.openEntity` 는 예전부터 **불리기만 하고 정의된 적이 없었다** — 문서 화면의 엔티티 링크가
  // 조용히 아무 일도 하지 않았다. 질의 결과의 그래프 관계도 여기로 온다.
  LW.openEntity = async (idOrName, byName) => {
    switchGroup('knowledge');
    switchTab('graph');
    await showEntity(idOrName, byName);
    const d = $('#g-detail');
    if (d) d.scrollIntoView({ behavior: 'smooth', block: 'start' });
  };
  LW.openCommunity = async (cid) => { switchGroup('knowledge'); switchTab('graph'); await showCommunity(cid); };

  // ---------------- WIKI ----------------
  let WIKI_PAGES = [];
  async function loadWikiList() { WIKI_PAGES = await api('/api/wiki/list'); renderWikiList(); if (!$('#w-content').value) openWiki('INDEX'); }
  function renderWikiList() { const f = ($('#w-filter').value || '').toLowerCase(); $('#w-list').innerHTML = WIKI_PAGES.filter((p) => p.toLowerCase().includes(f)).slice(0, 800).map((p) => `<div data-p="${esc(p)}" class="${p === $('#w-name').textContent ? 'sel' : ''}">${esc(p)}</div>`).join(''); $$('#w-list div').forEach((d) => d.onclick = () => openWiki(d.dataset.p)); }
  $('#w-filter').addEventListener('input', renderWikiList);
  async function openWiki(name) { const j = await api('/api/wiki/page?name=' + encodeURIComponent(name)); if (j.content == null) return; $('#w-name').textContent = name; $('#w-content').value = j.content; renderWikiList(); }
  $('#btn-w-save').onclick = async () => { await api('/api/wiki/page', { name: $('#w-name').textContent, content: $('#w-content').value }); toast('저장됨'); };
  $('#btn-w-rebuild').onclick = async () => { await api('/api/wiki/page', { name: $('#w-name').textContent, content: $('#w-content').value }); switchGroup('corpus'); switchTab('build'); LW.runBuild(false); };
  loaders.wiki = loadWikiList;

  // ---------------- GRAPH PROFILE (그래프 진단 — /api/graph/profile) ----------------
  // 2026-09-24: 지표 카드 위에 **소견**(무엇이 잘못됐고 무엇을 어떻게 고칠지)을 앞세운다. 소견 = 증거 → 원인 → 처방(붙여 넣을 조각) → 확인.
  // 엔진은 llmwiki/graph_findings.py — CLI `graph profile` · MCP wiki_graph_profile 과 같은 dict.
  let GP = null;
  let GP_AREA = 'all';
  const tbl = (head, rows) => rows.length ? '<table><tr>' + head.map((h) => `<th>${h}</th>`).join('') + '</tr>' + rows.map((r) => '<tr>' + r.map((c) => `<td>${c}</td>`).join('') + '</tr>').join('') + '</table>' : '<div class="muted small">(없음)</div>';
  const pct = (x) => fmt((x || 0) * 100, 0) + '%';
  const AREA_LABEL = { rules: '규칙 (rules.json)', corpus: '코퍼스 (문서)', tuning: '튜닝', build: '빌드', query_rules: '질의 규칙' };
  const SEV_ICON = { error: '⛔', warn: '⚠', info: 'ℹ' };
  function sampleTable(samples) {
    if (!samples || !samples.length) return '';
    if (samples.every((s) => typeof s !== 'object' || s === null)) return '<div class="small">' + samples.map((s) => `<span class="pill">${esc(String(s))}</span>`).join(' ') + '</div>';
    const keys = []; samples.forEach((s) => Object.keys(s || {}).forEach((k) => { if (!keys.includes(k)) keys.push(k); }));
    const cols = keys.slice(0, 7);
    const cell = (v) => Array.isArray(v) ? esc(v.slice(0, 4).map((x) => typeof x === 'object' ? JSON.stringify(x) : String(x)).join(' | ')) + (v.length > 4 ? ' …' : '') : (v && typeof v === 'object') ? esc(JSON.stringify(v).slice(0, 80)) : esc(String(v == null ? '' : v)).slice(0, 120);
    return '<div class="tbl-wrap"><table class="small"><tr>' + cols.map((k) => `<th>${esc(k)}</th>`).join('') + '</tr>' +
      samples.slice(0, 12).map((s) => '<tr>' + cols.map((k) => `<td>${cell((s || {})[k])}</td>`).join('') + '</tr>').join('') + '</table>' +
      (samples.length > 12 ? `<div class="muted small">…외 ${samples.length - 12}건 (CLI graph profile --json 에 전부)</div>` : '') + '</div>';
  }
  function renderFindings(p) {
    const fr = p.findings || { findings: [], by_area: {}, errors: [] };
    const fs = fr.findings || [];
    const areas = ['all', 'rules', 'corpus', 'tuning', 'build', 'query_rules'];
    const cnt = (a) => a === 'all' ? fs.length : (fr.by_area || {})[a] || 0;
    const head = `<h3 style="margin-bottom:4px">소견 ${fs.length}건 <small class="muted">— 무엇이 잘못됐고 무엇을 고칠지. 증거 → 원인 → 처방 → 확인</small></h3>` +
      '<div class="row">' + areas.map((a) => `<button class="secondary small gp-area${GP_AREA === a ? ' on' : ''}" data-area="${a}">${a === 'all' ? '전체' : AREA_LABEL[a]} ${cnt(a)}</button>`).join(' ') +
      ' <button id="btn-gp-md" class="secondary small" title="같은 내용을 마크다운으로 — LLM 에게 그대로 주어 규칙을 짜게 할 때">보고서(md) 내려받기</button></div>';
    const list = fs.filter((f) => GP_AREA === 'all' || f.area === GP_AREA);
    const body = list.length ? list.map((f, i) => {
      const fx = f.fix || {}; const v = f.verify || {}; const nums = (f.evidence || {}).numbers || {};
      const numStr = Object.entries(nums).filter(([k]) => k !== 'detail').map(([k, val]) => `${esc(k)} ${esc(typeof val === 'object' ? JSON.stringify(val) : String(val)).slice(0, 60)}`).join(' · ');
      return `<details class="finding sev-${f.severity}"${i < 2 ? ' open' : ''}>
        <summary>${SEV_ICON[f.severity] || '•'} <b>${esc(f.title)}</b> <span class="pill area-${esc(f.area)}">${esc(AREA_LABEL[f.area] || f.area)}</span> <span class="muted small">${nums.detail ? esc(String(nums.detail)).slice(0, 140) : numStr.slice(0, 140)}</span></summary>
        <div class="finding-body">
          <div class="small"><b>증거</b> — ${numStr || '-'}</div>${sampleTable((f.evidence || {}).samples)}
          <div class="small" style="margin-top:6px"><b>원인</b> — ${esc(f.why)}</div>
          <div class="small" style="margin-top:6px"><b>처방</b> <span class="pill">${esc(fx.kind || '')}</span>${fx.section ? ` <code>${esc(fx.section)}</code>` : ''}<ol class="small">${(fx.steps || []).map((s) => `<li>${esc(s)}</li>`).join('')}</ol>
          ${fx.snippet ? `<div class="row"><button class="secondary small" data-gp-copy="${i}">조각 복사</button><button class="secondary small" data-gp-rules="${i}">규칙 탭에 붙여 넣기</button><span class="muted small">rules.json 의 해당 절에 병합해 저장 → build graph</span></div><pre class="pre small" id="gp-snip-${i}">${esc(JSON.stringify(fx.snippet, null, 2))}</pre>` : ''}
          ${(fx.files || []).length ? `<div class="small"><b>파일</b> — ${(fx.files || []).slice(0, 12).map((x) => `<code>${esc(String(x))}</code>`).join(' ')}${fx.files.length > 12 ? ` …외 ${fx.files.length - 12}` : ''}</div>` : ''}
          ${(fx.commands || []).length ? `<div class="small"><b>명령</b> — ${(fx.commands || []).map((c) => `<code>${esc(c)}</code>`).join(' → ')}</div>` : ''}</div>
          <div class="small" style="margin-top:6px"><b>확인</b> — <code>${esc(v.metric || '')}</code> → ${esc(v.expect || '')} <span class="muted">(${esc(v.command || '')})</span></div>
        </div></details>`;
    }).join('') : '<div class="muted small">이 영역의 소견이 없습니다 — 임계값 안입니다.</div>';
    const errs = (fr.errors || []).length ? `<div class="warntxt small">일부 소견 계산 실패: ${esc(fr.errors.map((e) => e.step + ': ' + e.error).join('; '))}</div>` : '';
    $('#gp-findings').innerHTML = head + errs + body;
    $$('#gp-findings .gp-area').forEach((b) => b.onclick = () => { GP_AREA = b.dataset.area; renderFindings(GP); });
    $$('#gp-findings [data-gp-copy]').forEach((b) => b.onclick = () => LW.copyText($('#gp-snip-' + b.dataset.gpCopy).textContent, '규칙 조각'));
    $$('#gp-findings [data-gp-rules]').forEach((b) => b.onclick = () => { const t = $('#gp-snip-' + b.dataset.gpRules).textContent; switchGroup('knowledge'); switchTab('rules'); const box = $('#gr-snippet'); if (box) { box.value = t; $('#gr-snippet-wrap').classList.remove('hidden'); } LW.copyText(t, '규칙 조각'); });
    const md = $('#btn-gp-md'); if (md) md.onclick = () => { window.location = '/api/graph/profile?format=md&eval=' + ($('#gp-eval').checked ? 1 : 0); };
  }
  function renderProfile(p) {
    GP = p;
    const s = p.size, c = p.connectivity, cov = p.coverage, q = p.quality, ru = p.rules, us = p.usage;
    $('#gp-msg').textContent = `${p.generated_at} · build_version ${p.build_version} · ${fmt(p.ms, 0)} ms · 저장 ${p.saved || '-'}`;
    renderFindings(p);
    const stat = (v, l, cls, title) => `<div class="stat"${title ? ` title="${esc(title)}"` : ''}><b class="${cls || ''}">${v}</b>${l}</div>`;
    $('#gp-cards').innerHTML = stat(s.entities, '엔티티', '', '그래프의 노드 수') + stat(s.relations, '관계', '', '관계 행 수 (청크마다 따로 셈)') + stat(s.mentions, '멘션', '', '엔티티가 청크에 나온 횟수') + stat(s.communities, '무리(커뮤니티)', s.communities === 0 ? 'warntxt' : '', '서로 많이 연결된 엔티티 묶음. 0 이면 무리 탐지가 안 돈 것') +
      stat(c.components, '연결 성분') + stat(pct(c.largest_component_ratio), '최대 성분') + stat(`${c.isolated} (${pct(c.isolated_ratio)})`, '고립 엔티티', c.isolated_ratio >= (p.thresholds || {}).isolated_ratio ? 'warntxt' : '', '관계가 하나도 없는 엔티티') +
      stat(`${fmt(c.degree.median, 0)} / ${fmt(c.degree.p90, 0)} / ${fmt(c.degree.max, 0)}`, '연결 중앙값 / p90 / 최대') + stat(`${cov.pct}%`, `문서 커버리지 (${cov.covered}/${cov.docs})`, '', '문서 노드·날짜·금액을 뺀 엔티티가 하나라도 있는 문서의 비율') +
      stat(pct(q.cooccur_share), '공동출현 비중', '', '관계 중 co_occurs·mentions 의 비율 — 높을수록 구조 관계가 적다') + stat(q.duplicate_candidates, '합치기 후보') + stat(q.dangling_relations, '끊긴 관계', q.dangling_relations ? 'bad' : '') +
      stat(`${ru.dictionary.active}/${ru.dictionary.total}`, '사전 엔티티 활성') + stat(ru.dead_rules.length, '죽은 규칙') +
      (p.eval ? stat(p.eval.error ? '오류' : fmt(p.eval['hit@k'], 3), 'graph 채널 hit@k') + stat(p.eval.error ? '-' : fmt(p.eval.mrr, 3), 'graph 채널 MRR') : '') +
      `<div class="muted small">엔티티 유형: ${esc(Object.entries(s.entities_by_type).slice(0, 10).map(([k, v]) => `${k} ${v}`).join(' · '))}<br>관계 출처: ${esc(Object.entries(s.relations_by_provenance).map(([k, v]) => `${k} ${v}`).join(' · '))}</div>`;
    const sev = { error: 'bad', warn: 'warntxt', info: '' };
    $('#gp-suggest').innerHTML = '<details><summary>임계값 제안 (예전 형식) ' + p.suggestions.length + '건 <small class="muted">— 소견이 같은 내용을 더 자세히 다룬다</small></summary>' + (p.suggestions.length ? p.suggestions.map((g, i) => `<div class="hit"><div class="h"><span class="${sev[g.severity] || ''}">[${g.severity}]</span> <b>${esc(g.kind)}</b> ${esc(g.detail)}</div><div class="t">→ ${esc(g.action)}</div>` +
      (g.target === 'rules' ? `<button class="secondary" data-gp-go="rules" data-i="${i}">규칙 편집으로</button>` : g.target === 'tuning' ? `<button class="secondary" data-gp-go="tuning" data-i="${i}">튜닝으로</button>` : g.target === 'build' ? `<button class="secondary" data-gp-go="build" data-i="${i}">빌드로</button>` : '') + '</div>').join('') : '<div class="muted small">(없음)</div>') + '</details>';
    $$('#gp-suggest button[data-gp-go]').forEach((b) => b.onclick = () => { const to = b.dataset.gpGo; if (to === 'rules') { switchGroup('knowledge'); switchTab('rules'); } else if (to === 'tuning') { switchGroup('settings'); switchTab('tuning'); } else { switchGroup('corpus'); switchTab('build'); } });
    $('#gp-hubs').innerHTML = tbl(['엔티티', '유형', '연결', ''], c.hubs.map((h) => [`<a href="#" data-ent="${esc(h.entity_id)}">${esc(h.name)}</a>`, `<span class="pill">${esc(h.type)}</span>`, `<span class="num">${h.degree}</span>`, h.warning ? '<span class="warntxt">△ 허브 부적합 유형</span>' : '']));
    $$('#gp-hubs a[data-ent]').forEach((a) => a.onclick = (ev) => { ev.preventDefault(); LW.openEntity(a.dataset.ent); });
    $('#gp-isolated').innerHTML = `<div class="muted small">유형별: ${esc(Object.entries(c.isolated_by_type).map(([k, v]) => `${k} ${v}`).join(' · ') || '-')}</div>` + tbl(['엔티티', '유형', '출처'], c.isolated_samples.map((e) => [esc(e.name), `<span class="pill">${esc(e.type)}</span>`, esc(e.source || '')]));
    $('#gp-rules').innerHTML = tbl(['종류', '규칙', '엔티티', '관계'], ru.rows.map((r) => [esc(r.kind), `${esc(r.name)}${r.detail ? ` <small class="muted">${esc(r.detail)}</small>` : ''}`, `<span class="num ${r.entities === 0 && r.relations === 0 ? 'bad' : ''}">${r.entities}</span>`, `<span class="num ${r.entities === 0 && r.relations === 0 ? 'bad' : ''}">${r.relations}</span>`])) +
      `<div class="muted small">사전 엔티티 ${ru.dictionary.total} · 코퍼스에 나온 것 ${ru.dictionary.active} · 죽은 것 ${ru.dictionary.dead}${ru.dictionary.dead_names.length ? ' — ' + esc(ru.dictionary.dead_names.slice(0, 12).join(', ')) : ''}</div>` +
      (ru.dictionary.top.length ? tbl(['사전 엔티티 (상위)', '유형', '멘션', '연결'], ru.dictionary.top.slice(0, 10).map((d) => [esc(d.name), `<span class="pill">${esc(d.type)}</span>`, `<span class="num">${d.mentions}</span>`, `<span class="num">${d.degree}</span>`])) : '');
    $('#gp-coverage').innerHTML = `<div class="muted small">문서 노드·날짜·금액을 뺀 엔티티가 하나라도 있는 문서 = 커버. 문서당 엔티티 ${fmt(cov.entities_per_doc, 1)} · 청크당 멘션 ${fmt(cov.mentions_per_chunk, 2)}</div>` +
      tbl(['문서 유형', '문서', '커버', '%', '미커버 예시'], Object.entries(cov.by_doc_type).map(([dt, r]) => [esc(dt), `<span class="num">${r.total}</span>`, `<span class="num">${r.covered}</span>`, `<span class="num ${r.pct < (p.thresholds || {}).coverage_pct ? 'warntxt' : ''}">${r.pct}</span>`, `<span class="muted small">${esc((r.uncovered_samples || []).slice(0, 2).join(', '))}</span>`]));
    $('#gp-dups').innerHTML = tbl(['후보', '이유', '유형'], q.duplicates.slice(0, 15).map((d) => [esc(d.names.slice(0, 3).join(' / ')), esc(d.reason), esc(d.types.slice(0, 3).join(','))])) + `<div class="muted small">끊긴 관계 ${q.dangling_relations} · 자기 관계 ${q.self_loops}</div>`;
    $('#gp-usage').innerHTML = `<div class="small">최근 질의 ${us.requests}건 (표본 상한 ${us.sample_limit}) · 그래프 시드 있음 ${us.with_seeds} (${pct(us.seed_share)}) · graph 근거 있던 질의 ${pct(us.requests_with_graph_hit_share)} · 최종 근거 중 graph 채널 ${fmt(us.graph_hit_share * 100, 1)}%</div>` +
      (us.no_seed_keywords.length ? `<div class="small" style="margin-top:4px">시드 없던 질의의 키워드(엔티티 후보): ${us.no_seed_keywords.map((k) => `<span class="pill">${esc(k.keyword)} ${k.count}</span>`).join(' ')}</div>` : '') +
      (us.no_seed_samples.length ? `<div class="muted small">예: ${esc(us.no_seed_samples.slice(0, 4).join(' | '))}</div>` : '');
    const cp = p.compare; const cel = $('#gp-compare');
    if (cp) { cel.classList.remove('hidden'); cel.innerHTML = `<h3>직전 실행과 비교 <small class="muted">${esc(cp.before.generated_at || '')} (v${esc(cp.before.build_version || '')}) → ${esc(cp.after.generated_at || '')} (v${esc(cp.after.build_version || '')})</small></h3>` + tbl(['지표', '이전', '이번', 'Δ'], Object.values(cp.deltas).map((d) => [esc(d.name), esc(String(d.before)), esc(String(d.after)), d.delta == null ? '' : `<span class="num ${d.delta > 0 ? 'ok' : d.delta < 0 ? 'warntxt' : ''}">${d.delta > 0 ? '+' : ''}${esc(String(d.delta))}</span>`])); }
    else if (p.compare === null) { cel.classList.remove('hidden'); cel.innerHTML = '<div class="muted small">비교할 이전 프로파일이 없습니다 — 이번 실행이 첫 기록입니다. 규칙을 고치고 빌드(graph) 한 뒤 다시 "이전 실행과 비교" 를 누르세요.</div>'; }
    else cel.classList.add('hidden');
  }
  async function runProfile(compare) {
    $('#gp-msg').textContent = '진단 중… (큰 그래프는 몇 초, 평가 포함이면 질문 수만큼 더)';
    const p = await api(`/api/graph/profile?eval=${$('#gp-eval').checked ? 1 : 0}&compare=${compare ? 1 : 0}`);
    if (p.error) { $('#gp-msg').textContent = '오류: ' + p.error; return; }
    renderProfile(p); loadProfileHistory();
  }
  async function loadProfileHistory() {
    const h = await api('/api/graph/profile/history'); if (h.error) return;
    $('#gp-history').innerHTML = `<div class="muted small">${esc(h.dir)} · 보관 ${h.keep}개 (config.json graph_profile_keep)</div>` + tbl(['시각', 'build', '엔티티', '관계', '고립%', '성분', '커버%', 'cooccur%', '소견 error/warn'], (h.history || []).slice(0, 12).map((r) => { const m = r.key_metrics || r; return [esc(r.generated_at || ''), esc(String(r.build_version || '')), esc(String(m.entities ?? '')), esc(String(m.relations ?? '')), pct(m.isolated_ratio), esc(String(m.components ?? '')), esc(String(m.coverage_pct ?? '')), pct(m.cooccur_share), `${m.findings_error ?? '-'}/${m.findings_warn ?? '-'}`]; }));
  }
  $('#btn-gp-run').onclick = () => runProfile(false);
  $('#btn-gp-compare').onclick = () => runProfile(true);
  $('#btn-gp-history').onclick = loadProfileHistory;
  loaders.graphprof = () => { if (!GP) loadProfileHistory(); };

  // ---------------- GRAPH RULES ----------------
  // 예전에는 data/rules.json 원문 textarea 하나였다. 무엇을 고칠 수 있는지, 고친 것이 성한지,
  // 고치면 무엇이 달라지는지를 화면에서 알 수 없었다 (2026-09-19). CLI `graph-rules` · MCP `wiki_graph_rules` 와 같은 내용.
  let GRS = null;                                   // /api/graph_rules 요약 (엔티티 사전 · type · 값 종류 · 관계 어휘)
  const LVL = { error: ['✖', 'chk-error'], warn: ['⚠', 'chk-warn'], info: ['ℹ', 'chk-info'] };

  async function loadRules() {
    const raw = await api('/api/rules');
    $('#rules-json').value = JSON.stringify(raw, null, 2);
    GRS = await api('/api/graph_rules');
    const c = GRS.counts || {};
    $('#gr-summary').innerHTML =
      `<div class="stat" title="규칙 사전에 이름이 등록된 엔티티. 문서에서 이 이름(과 별칭)을 찾아 노드로 만듭니다"><b>${(GRS.entities || []).length}</b>엔티티</div>` +
      `<div class="stat" title="쓸 수 있는 엔티티 유형 — schema.entity_types · types_for_cooccur · id_patterns 의 합집합"><b>${(GRS.entity_types || []).length}</b>유형</div>` +
      `<div class="stat" title="relation_patterns[*].value 와 chunk_values[*].value 에 쓸 수 있는 값 종류"><b>${(GRS.value_types || []).length}</b>값 종류</div>` +
      `<div class="stat" title="schema.relations — 관계 이름 어휘. 갈라진 이름(used/uses)을 하나로 모으고, 어휘 밖 이름은 빌드 보고서에 남습니다"><b>${Object.keys(GRS.relations || {}).length}</b>관계 어휘</div>` +
      `<div class="stat" title="본문 필드를 잡는 정규식 (담당·마감·영향 모듈 …)"><b>${c.relation_patterns || 0}</b>관계 패턴</div>` +
      `<div class="stat" title="청크마다 남기는 스칼라 값 (날짜·금액·수치+단위·버전)"><b>${c.chunk_values || 0}</b>청크 값</div>` +
      `<div class="stat" title="ISSUE-2041 · CL-55321 같은 문서 ID 를 잡는 패턴"><b>${c.id_patterns || 0}</b>ID 패턴</div>` +
      `<div class="stat" title="문서 유형 × 대상 유형 → 결정적 관계"><b>${c.link_rules || 0}</b>링크 규칙</div>` +
      `<span class="muted small" title="모르는 관계 이름을 만났을 때 — keep(그대로 두되 보고) · map(별칭이면 고침) · drop(버림)">모르는 관계 정책 <code>${esc(GRS.on_unknown || 'keep')}</code> · <code>${esc(GRS.path || '')}</code></span>`;
    const sel = $('#gr-new-type');
    if (sel) sel.innerHTML = (GRS.entity_types || []).map((t) => `<option value="${esc(t)}">${esc(t)}</option>`).join('');
    renderDict();
  }

  function renderDict() {
    if (!GRS) return;
    const q = ($('#gr-dict-q') && $('#gr-dict-q').value || '').trim().toLowerCase();
    const rows = (GRS.entities || []).filter((e) => !q
      || (e.name || '').toLowerCase().includes(q)
      || (e.aliases || []).some((a) => (a || '').toLowerCase().includes(q)));
    $('#gr-dict-n').textContent = (GRS.entities || []).length;
    $('#gr-dict-list').innerHTML = '<table><tr><th>이름</th><th>유형</th><th>별칭</th></tr>'
      + rows.slice(0, 300).map((e) => `<tr><td>${esc(e.name)}</td><td><code>${esc(e.type || '')}</code></td><td class="muted">${esc((e.aliases || []).join(', '))}</td></tr>`).join('')
      + '</table>' + (rows.length > 300 ? `<div class="muted small">…외 ${rows.length - 300}개 (검색으로 좁히세요)</div>` : '');
  }

  function renderIssues(issues, where) {
    $(where).innerHTML = (issues || []).length
      ? '<div class="prop-checks">' + issues.map((i) => {
        const m = LVL[i.level] || LVL.info;
        return `<div class="${m[1]}">${m[0]} <code>${esc(i.where)}</code> ${esc(i.detail)}`
          + (i.fix ? `<span class="muted"> → ${esc(i.fix)}</span>` : '') + '</div>';
      }).join('') + '</div>'
      : '<div class="muted small">점검 통과 — 이 규칙 파일은 그대로 빌드할 수 있습니다.</div>';
  }

  $('#btn-gr-refresh').onclick = loadRules;
  $('#btn-gr-lint').onclick = async () => {
    const r = await api('/api/graph_rules?action=lint');
    const c = r.counts || {};
    renderIssues(r.issues, '#gr-lint');
    toast(`점검: 오류 ${c.errors || 0} · 경고 ${c.warns || 0} (파일만 보는 정적 점검 — 빌드된 그래프 진단은 '그래프 진단' 탭)`);
  };
  $('#btn-gr-fill').onclick = async () => {
    const r = await api('/api/graph_rules', { action: 'fill_defaults' });
    toast((r.added || []).length ? `채운 항목 ${r.added.length}개: ${r.added.slice(0, 5).join(', ')}${r.added.length > 5 ? ' …' : ''}` : '이미 모두 있습니다');
    loadRules();
  };
  $('#btn-gr-test').onclick = async () => {
    const q = $('#gr-test-q').value.trim();
    if (!q) { toast('문장을 넣으세요'); return; }
    const r = await api('/api/graph_rules?action=test&q=' + encodeURIComponent(q)
      + '&doc_type=' + encodeURIComponent($('#gr-test-doctype').value.trim())
      + '&ext_id=' + encodeURIComponent($('#gr-test-extid').value.trim()));
    const unk = Object.keys(r.unknown_rels || {}).length || Object.keys(r.unknown_types || {}).length;
    $('#gr-test-out').innerHTML =
      `<div class="tbl-wrap"><table><tr><th>노드</th><th>유형</th><th>멘션</th></tr>`
      + (r.entities || []).map((e) => `<tr><td>${esc(e.name)}</td><td><code>${esc(e.type)}</code></td><td>${e.mentions}</td></tr>`).join('')
      + `</table></div><div class="tbl-wrap"><table><tr><th>출발</th><th>관계</th><th>도착</th><th title="rule=결정적 규칙 · explicit=front matter · cooccur=같이 나옴">출처</th><th>가중치</th></tr>`
      + (r.relations || []).map((x) => `<tr><td>${esc(x.src)}</td><td><code>${esc(x.rel)}</code></td><td>${esc(x.dst)}</td><td class="muted">${esc(x.provenance)}</td><td>${fmt(x.weight, 2)}</td></tr>`).join('')
      + '</table></div>'
      + (unk ? `<div class="chk-warn">⚠ 어휘 밖 — 관계 ${esc(JSON.stringify(r.unknown_rels))} · 유형 ${esc(JSON.stringify(r.unknown_types))} (schema.relations / schema.entity_types 에 추가하세요)</div>` : '')
      + ((r.entities || []).length ? '' : '<div class="muted small">아무것도 잡히지 않았습니다 — 사전에 없는 말이거나 패턴이 맞지 않습니다.</div>');
  };
  if ($('#gr-dict-q')) $('#gr-dict-q').oninput = renderDict;
  $('#btn-gr-add-entity').onclick = async () => {
    const name = $('#gr-new-name').value.trim();
    if (!name) { toast('이름을 넣으세요'); return; }
    const aliases = $('#gr-new-alias').value.split(',').map((s) => s.trim()).filter(Boolean);
    const r = await api('/api/graph_rules', { action: 'add_entity', name, type: $('#gr-new-type').value, aliases });
    if (r && r.error) { toast('실패: ' + r.error); return; }
    toast(`엔티티 추가: ${name} — 반영하려면 코퍼스 › 빌드에서 '그래프 빌드'`);
    $('#gr-new-name').value = ''; $('#gr-new-alias').value = '';
    loadRules();
  };
  $('#btn-gr-add-alias').onclick = async () => {
    const name = $('#gr-new-name').value.trim();
    const aliases = $('#gr-new-alias').value.split(',').map((s) => s.trim()).filter(Boolean);
    if (!name || !aliases.length) { toast('엔티티 이름과 별칭을 넣으세요'); return; }
    const r = await api('/api/graph_rules', { action: 'add_alias', name, aliases });
    if (r && r.error) { toast('실패: ' + r.error); return; }
    toast(`별칭 ${(r.added || []).length}개 추가 — 반영하려면 '그래프 빌드'`);
    $('#gr-new-alias').value = '';
    loadRules();
  };
  $('#btn-rules-save').onclick = async () => {
    let r;
    try { r = JSON.parse($('#rules-json').value); } catch (e) { toast('JSON 오류'); return; }
    const res = await api('/api/rules', { rules: r });
    if (res && res.error) {                        // 저장 전 점검에 걸렸다 — 이유를 보여 준다
      renderIssues(res.issues, '#gr-lint');
      toast('저장 안 됨 — ' + res.error);
      return;
    }
    renderIssues((res && res.issues) || [], '#gr-lint');
    toast('규칙 저장됨 — 다음 `build graph` 부터 반영');
    loadRules();
  };
  loaders.rules = loadRules;
})(window.LW);
