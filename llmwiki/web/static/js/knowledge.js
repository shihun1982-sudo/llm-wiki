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

  const G = { nodes: [], edges: [], sel: null, drag: null, running: false, tx: 0, ty: 0, scale: 1 };
  async function loadGraph() {
    const lim = $('#g-limit').value, comm = $('#g-comm').value, prov = $('#g-prov').value, types = $('#g-types').value.trim();
    const g = await api(`/api/graph?limit=${lim}&community=${comm}${prov ? '&provenance=' + encodeURIComponent(prov) : ''}${types ? '&types=' + encodeURIComponent(types) : ''}`);
    STATE.graph = g;
    const sel = $('#g-comm'); const cur = sel.value;
    sel.innerHTML = '<option value="all">all</option>' + g.communities.map((c) => `<option value="${c.community}">C${c.community} (n=${c.size})</option>`).join('');
    sel.value = cur;
    $('#g-prov-counts').textContent = '관계 출처: ' + JSON.stringify(g.provenance_counts || {});
    $('#g-comms').innerHTML = g.communities.map((c) => `<div><b>C${c.community}</b> n=${c.size} <span class="muted">[${c.source}]</span><br>${esc(c.summary)}</div>`).join('<hr>');
    const cv = $('#gcanvas'); const W = cv.width = cv.clientWidth * (window.devicePixelRatio || 1); const H = cv.height = 600 * (window.devicePixelRatio || 1);
    const idx = {}; G.nodes = g.nodes.map((n, i) => { idx[n.id] = i; const a = i / g.nodes.length * Math.PI * 2; return Object.assign({}, n, { x: W / 2 + Math.cos(a) * W / 3 * (0.5 + Math.random() / 2), y: H / 2 + Math.sin(a) * H / 3 * (0.5 + Math.random() / 2), vx: 0, vy: 0 }); });
    G.edges = g.edges.filter((e) => e.src in idx && e.dst in idx).map((e) => Object.assign({}, e, { a: idx[e.src], b: idx[e.dst] }));
    G.W = W; G.H = H; G.sel = null; G.iter = 0; G.running = true;
    const comms = Array.from(new Set(G.nodes.map((n) => n.community))).sort((a, b) => a - b);
    $('#g-legend').innerHTML = comms.map((c) => `<span><i style="background:${cc(c)}"></i>C${c}</span>`).join('') + '<span class="muted">· 노드 크기 = degree · 굵은 테두리 = rule+llm 합의 · 간선 색: explicit/rule=진함, cooccur=연함</span>';
    if (!G.raf) tick();
  }
  function cc(c) { return c == null || c < 0 ? '#898781' : PALETTE[c % PALETTE.length]; }
  function edgeColor(e, hi) { if (hi) return getComputedStyle(document.documentElement).getPropertyValue('--ink') || '#0b0b0b'; const p = e.provenance || ''; return p === 'explicit' ? '#1baf7a' : p === 'rule' || p === 'human' ? '#2a78d6' : p === 'llm' ? '#e87ba4' : (getComputedStyle(document.documentElement).getPropertyValue('--grid') || '#e1e0d9'); }
  function tick() {
    const cv = $('#gcanvas'); if (!cv.isConnected) return;
    const ctx = cv.getContext('2d'); const { nodes, edges, W, H } = G;
    if (G.running && nodes.length) {
      G.iter++;
      const k = Math.sqrt((W * H) / (nodes.length + 1)) * 0.6;
      for (const n of nodes) { n.fx = 0; n.fy = 0; }
      for (let i = 0; i < nodes.length; i++) for (let j = i + 1; j < nodes.length; j++) {
        const a = nodes[i], b = nodes[j]; let dx = a.x - b.x, dy = a.y - b.y; let d2 = dx * dx + dy * dy + 0.01; if (d2 > k * k * 16) continue;
        const f = (k * k) / d2; dx *= f; dy *= f; a.fx += dx; a.fy += dy; b.fx -= dx; b.fy -= dy;
      }
      for (const e of edges) { const a = nodes[e.a], b = nodes[e.b]; const dx = b.x - a.x, dy = b.y - a.y; const d = Math.sqrt(dx * dx + dy * dy) + 0.01; const f = (d - k * 0.8) / d * 0.05 * Math.min(2, e.weight); a.fx += dx * f; a.fy += dy * f; b.fx -= dx * f; b.fy -= dy * f; }
      const cool = Math.max(0.02, 1 - G.iter / 300);
      for (const n of nodes) { if (n === G.drag) continue; n.fx += (W / 2 - n.x) * 0.002; n.fy += (H / 2 - n.y) * 0.002; n.vx = (n.vx + n.fx * 0.01) * 0.6; n.vy = (n.vy + n.fy * 0.01) * 0.6; const sp = Math.sqrt(n.vx * n.vx + n.vy * n.vy); const mx = 8 * cool + 0.5; if (sp > mx) { n.vx *= mx / sp; n.vy *= mx / sp; } n.x = Math.max(20, Math.min(W - 20, n.x + n.vx)); n.y = Math.max(20, Math.min(H - 20, n.y + n.vy)); }
      if (G.iter > 400 && !G.drag) G.running = false;
    }
    ctx.clearRect(0, 0, W, H); ctx.save(); ctx.translate(G.tx, G.ty); ctx.scale(G.scale, G.scale);
    const find = ($('#g-find').value || '').toLowerCase();
    const selId = G.sel ? G.sel.id : null;
    for (const e of edges) { const a = nodes[e.a], b = nodes[e.b]; const hi = selId && (a.id === selId || b.id === selId); ctx.strokeStyle = edgeColor(e, hi); ctx.lineWidth = hi ? 2 : Math.min(3, 0.5 + e.weight * 0.5); ctx.beginPath(); ctx.moveTo(a.x, a.y); ctx.lineTo(b.x, b.y); ctx.stroke(); }
    const dpr = window.devicePixelRatio || 1;
    const ink = getComputedStyle(document.documentElement).getPropertyValue('--ink') || '#0b0b0b';
    for (const n of nodes) {
      const r = (4 + Math.sqrt(n.degree || 1) * 1.2) * dpr; const hit = find && n.name.toLowerCase().includes(find);
      ctx.beginPath(); ctx.arc(n.x, n.y, r, 0, Math.PI * 2); ctx.fillStyle = cc(n.community); ctx.globalAlpha = find && !hit ? 0.25 : 1; ctx.fill(); ctx.globalAlpha = 1;
      ctx.lineWidth = (n.source || '').includes('+') ? 3 : 1; ctx.strokeStyle = n === G.sel || hit ? ink : '#fff'; ctx.stroke();
      if (r > 7 * dpr || n === G.sel || hit) { ctx.fillStyle = ink; ctx.font = `${11 * dpr}px sans-serif`; ctx.fillText(n.name.slice(0, 22), n.x + r + 2, n.y + 4); }
    }
    ctx.restore();
    G.raf = requestAnimationFrame(tick);
  }
  (function graphEvents() {
    const cv = $('#gcanvas');
    const pos = (ev) => { const rc = cv.getBoundingClientRect(); const dpr = window.devicePixelRatio || 1; return { x: ((ev.clientX - rc.left) * dpr - G.tx) / G.scale, y: ((ev.clientY - rc.top) * dpr - G.ty) / G.scale }; };
    const at = (p) => { let best = null, bd = 1e9; for (const n of G.nodes) { const d = Math.hypot(n.x - p.x, n.y - p.y); const r = (6 + Math.sqrt(n.degree || 1) * 1.2) * (window.devicePixelRatio || 1); if (d < r + 4 && d < bd) { best = n; bd = d; } } return best; };
    cv.onmousedown = (ev) => { const n = at(pos(ev)); if (n) { G.drag = n; G.running = true; G.iter = Math.min(G.iter, 250); } else { G.pan = { x: ev.clientX - G.tx, y: ev.clientY - G.ty }; } };
    cv.onmousemove = (ev) => { if (G.drag) { const p = pos(ev); G.drag.x = p.x; G.drag.y = p.y; } else if (G.pan) { G.tx = ev.clientX - G.pan.x; G.ty = ev.clientY - G.pan.y; } };
    cv.onmouseup = async () => { if (G.drag) { const n = G.drag; G.drag = null; G.sel = n; showEntity(n.id); } G.pan = null; };
    cv.onmouseleave = () => { G.drag = null; G.pan = null; };
    cv.onwheel = (ev) => { ev.preventDefault(); const f = ev.deltaY < 0 ? 1.1 : 0.9; G.scale = Math.max(0.3, Math.min(4, G.scale * f)); };
  })();
  async function showEntity(id, byName) {
    // byName=true 면 이름으로 찾는다 — id 규칙(e:소문자_언더바)을 화면이 만들어 내지 않게 서버가 해석한다.
    const d = await api('/api/entity?' + (byName ? 'name=' : 'id=') + encodeURIComponent(id));
    if (!d || !d.entity) {
      $('#g-detail').innerHTML = `<div class="muted">엔티티를 찾지 못했습니다: <b>${esc(id)}</b>`
        + '<div class="small">그래프에 없는 이름이거나, 빌드 뒤에 사라진 엔티티입니다.</div></div>';
      return;
    }
    const e = d.entity; const refs = e.doc_refs || [];
    $('#g-detail').innerHTML = `<h3 style="margin-top:0">${esc(e.name)} <span class="pill">${e.type}</span> <span class="pill">${e.source}</span> <span class="pill">deg ${e.degree}</span> <span class="pill">C${e.community}</span> <span class="pill">docs ${e.n_docs || refs.length}</span></h3>` +
      (e.description ? `<p>${esc(e.description)}</p>` : '') + `<div class="muted">aliases: ${esc(e.aliases)}</div>` +
      `<h3>문서 참조 (doc_refs)</h3><table><tr><th>doc_id</th><th>제목</th><th>언급</th><th>첫 청크</th></tr>` + refs.map((r) => `<tr><td><a href="#" data-doc="${esc(r.doc_id)}">${esc(r.doc_id)}</a></td><td class="muted">${esc(r.title || '')}</td><td class="num">${r.mentions}</td><td class="muted small">${esc(r.first_chunk || '')}</td></tr>`).join('') + '</table>' +
      `<h3>관계 (${d.relations.length})</h3><table>` + d.relations.slice(0, 40).map((r) => `<tr><td>${esc(r.src_name)}</td><td><b>${esc(r.rel)}</b></td><td>${esc(r.dst_name)}</td><td class="num">${fmt(r.weight, 2)}</td><td><span class="pill">${esc(r.provenance || r.source)}</span></td><td class="num muted">${fmt(r.confidence, 2)}</td></tr>`).join('') + '</table><h3>근거 문단</h3>' +
      d.mentions.map((m) => `<div class="hit"><div class="h">${esc(m.chunk_id)} · ${esc(m.heading)}</div><div class="t">${esc(m.text)}</div></div>`).join('');
    $$('#g-detail a[data-doc]').forEach((a) => a.onclick = async (ev) => { ev.preventDefault(); const ch = await api('/api/doc_chunks?id=' + encodeURIComponent(a.dataset.doc)); $('#console-out').textContent = ch.map((c) => `--- ${c.chunk_id} | ${c.heading}\n${c.text}\n`).join('\n'); switchGroup('observability'); switchTab('console'); });
  }
  $('#btn-graph').onclick = loadGraph;
  loaders.graph = loadGraph;
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

  // ---------------- WIKI ----------------
  let WIKI_PAGES = [];
  async function loadWikiList() { WIKI_PAGES = await api('/api/wiki/list'); renderWikiList(); if (!$('#w-content').value) openWiki('INDEX'); }
  function renderWikiList() { const f = ($('#w-filter').value || '').toLowerCase(); $('#w-list').innerHTML = WIKI_PAGES.filter((p) => p.toLowerCase().includes(f)).slice(0, 800).map((p) => `<div data-p="${esc(p)}" class="${p === $('#w-name').textContent ? 'sel' : ''}">${esc(p)}</div>`).join(''); $$('#w-list div').forEach((d) => d.onclick = () => openWiki(d.dataset.p)); }
  $('#w-filter').addEventListener('input', renderWikiList);
  async function openWiki(name) { const j = await api('/api/wiki/page?name=' + encodeURIComponent(name)); if (j.content == null) return; $('#w-name').textContent = name; $('#w-content').value = j.content; renderWikiList(); }
  $('#btn-w-save').onclick = async () => { await api('/api/wiki/page', { name: $('#w-name').textContent, content: $('#w-content').value }); toast('저장됨'); };
  $('#btn-w-rebuild').onclick = async () => { await api('/api/wiki/page', { name: $('#w-name').textContent, content: $('#w-content').value }); switchGroup('corpus'); switchTab('build'); LW.runBuild(false); };
  loaders.wiki = loadWikiList;

  // ---------------- GRAPH PROFILE (그래프 진단, 요청 5 — /api/graph/profile) ----------------
  let GP = null;
  const tbl = (head, rows) => rows.length ? '<table><tr>' + head.map((h) => `<th>${h}</th>`).join('') + '</tr>' + rows.map((r) => '<tr>' + r.map((c) => `<td>${c}</td>`).join('') + '</tr>').join('') + '</table>' : '<div class="muted small">(없음)</div>';
  const pct = (x) => fmt((x || 0) * 100, 0) + '%';
  function renderProfile(p) {
    GP = p;
    const s = p.size, c = p.connectivity, cov = p.coverage, q = p.quality, ru = p.rules, us = p.usage;
    $('#gp-msg').textContent = `${p.generated_at} · build_version ${p.build_version} · ${fmt(p.ms, 0)} ms · 저장 ${p.saved || '-'}`;
    const stat = (v, l, cls) => `<div class="stat"><b class="${cls || ''}">${v}</b>${l}</div>`;
    $('#gp-cards').innerHTML = stat(s.entities, '엔티티') + stat(s.relations, '관계') + stat(s.mentions, '멘션') + stat(s.communities, '커뮤니티') +
      stat(c.components, '연결 성분') + stat(pct(c.largest_component_ratio), '최대 성분') + stat(`${c.isolated} (${pct(c.isolated_ratio)})`, '고립 엔티티', c.isolated_ratio >= (p.thresholds || {}).isolated_ratio ? 'warntxt' : '') +
      stat(`${fmt(c.degree.median, 0)} / ${fmt(c.degree.p90, 0)} / ${fmt(c.degree.max, 0)}`, '차수 중앙값 / p90 / 최대') + stat(`${cov.pct}%`, `문서 커버리지 (${cov.covered}/${cov.docs})`) +
      stat(pct(q.cooccur_share), 'cooccur 비중') + stat(q.duplicate_candidates, '합치기 후보') + stat(q.dangling_relations, '끊긴 관계', q.dangling_relations ? 'bad' : '') +
      stat(`${ru.dictionary.active}/${ru.dictionary.total}`, '사전 엔티티 활성') + stat(ru.dead_rules.length, '죽은 규칙') +
      (p.eval ? stat(p.eval.error ? '오류' : fmt(p.eval['hit@k'], 3), 'graph 채널 hit@k') + stat(p.eval.error ? '-' : fmt(p.eval.mrr, 3), 'graph 채널 MRR') : '') +
      `<div class="muted small">엔티티 유형: ${esc(Object.entries(s.entities_by_type).slice(0, 10).map(([k, v]) => `${k} ${v}`).join(' · '))}<br>관계 출처: ${esc(JSON.stringify(s.relations_by_provenance))}</div>`;
    const sev = { error: 'bad', warn: 'warntxt', info: '' };
    $('#gp-suggest').innerHTML = '<h3>제안 <small class="muted">action = 어느 파일·키를 고칠지</small></h3>' + (p.suggestions.length ? p.suggestions.map((g, i) => `<div class="hit"><div class="h"><span class="${sev[g.severity] || ''}">[${g.severity}]</span> <b>${esc(g.kind)}</b> ${esc(g.detail)}</div><div class="t">→ ${esc(g.action)} ` +
      (g.target === 'rules' ? `<button class="secondary" data-gp-go="rules" data-i="${i}">규칙 편집으로</button>` : g.target === 'tuning' ? `<button class="secondary" data-gp-go="tuning" data-i="${i}">튜닝으로</button>` : g.target === 'build' ? `<button class="secondary" data-gp-go="build" data-i="${i}">빌드로</button>` : '') + '</div></div>').join('') : '<div class="muted small">제안 없음 — 임계값 안 (' + esc(JSON.stringify(p.thresholds || {})) + ')</div>');
    $$('#gp-suggest button[data-gp-go]').forEach((b) => b.onclick = () => { const to = b.dataset.gpGo; if (to === 'rules') { switchGroup('knowledge'); switchTab('rules'); } else if (to === 'tuning') { switchGroup('settings'); switchTab('tuning'); } else { switchGroup('corpus'); switchTab('build'); } });
    $('#gp-hubs').innerHTML = tbl(['엔티티', '유형', '차수', ''], c.hubs.map((h) => [`<a href="#" data-ent="${esc(h.entity_id)}">${esc(h.name)}</a>`, `<span class="pill">${esc(h.type)}</span>`, `<span class="num">${h.degree}</span>`, h.warning ? '<span class="warntxt">△ 허브 부적합 유형</span>' : '']));
    $$('#gp-hubs a[data-ent]').forEach((a) => a.onclick = (ev) => { ev.preventDefault(); switchTab('graph'); showEntity(a.dataset.ent); });
    $('#gp-isolated').innerHTML = `<div class="muted small">유형별: ${esc(Object.entries(c.isolated_by_type).map(([k, v]) => `${k} ${v}`).join(' · ') || '-')}</div>` + tbl(['엔티티', '유형', '출처'], c.isolated_samples.map((e) => [esc(e.name), `<span class="pill">${esc(e.type)}</span>`, esc(e.source || '')]));
    $('#gp-rules').innerHTML = tbl(['종류', '규칙', '엔티티', '관계'], ru.rows.map((r) => [esc(r.kind), `${esc(r.name)}${r.detail ? ` <small class="muted">${esc(r.detail)}</small>` : ''}`, `<span class="num ${r.entities === 0 && r.relations === 0 ? 'bad' : ''}">${r.entities}</span>`, `<span class="num ${r.entities === 0 && r.relations === 0 ? 'bad' : ''}">${r.relations}</span>`])) +
      `<div class="muted small">사전 엔티티 ${ru.dictionary.total} · 코퍼스에 나온 것 ${ru.dictionary.active} · 죽은 것 ${ru.dictionary.dead}${ru.dictionary.dead_names.length ? ' — ' + esc(ru.dictionary.dead_names.slice(0, 12).join(', ')) : ''}</div>` +
      (ru.dictionary.top.length ? tbl(['사전 엔티티 (상위)', '유형', '멘션', '차수'], ru.dictionary.top.slice(0, 10).map((d) => [esc(d.name), `<span class="pill">${esc(d.type)}</span>`, `<span class="num">${d.mentions}</span>`, `<span class="num">${d.degree}</span>`])) : '');
    $('#gp-coverage').innerHTML = `<div class="muted small">문서 노드·날짜·금액을 뺀 엔티티가 하나라도 있는 문서 = 커버. 문서당 엔티티 ${fmt(cov.entities_per_doc, 1)} · 청크당 멘션 ${fmt(cov.mentions_per_chunk, 2)}</div>` +
      tbl(['문서 유형', '문서', '커버', '%', '미커버 예시'], Object.entries(cov.by_doc_type).map(([dt, r]) => [esc(dt), `<span class="num">${r.total}</span>`, `<span class="num">${r.covered}</span>`, `<span class="num ${r.pct < (p.thresholds || {}).coverage_pct ? 'warntxt' : ''}">${r.pct}</span>`, `<span class="muted small">${esc(r.uncovered_samples.slice(0, 3).join(', '))}</span>`]));
    $('#gp-dups').innerHTML = tbl(['후보', '이유', '유형'], q.duplicates.slice(0, 15).map((d) => [esc(d.names.slice(0, 3).join(' / ')), esc(d.reason), esc(d.types.slice(0, 3).join(','))])) + `<div class="muted small">끊긴 관계 ${q.dangling_relations} · 자기 관계 ${q.self_loops} · weight 중앙값 ${fmt(q.weight.median, 2)} (p25 ${fmt(q.weight.p25, 2)} · p75 ${fmt(q.weight.p75, 2)})</div>`;
    $('#gp-usage').innerHTML = `<div class="small">최근 질의 ${us.requests}건 (표본 상한 ${us.sample_limit}) · 그래프 시드 있음 ${us.with_seeds} (${pct(us.seed_share)}) · graph 근거 있던 질의 ${pct(us.requests_with_graph_hit_share)} · 최종 근거 중 graph 채널 ${fmt(us.graph_hit_share * 100, 1)}%</div>` +
      (us.no_seed_keywords.length ? `<div class="small" style="margin-top:4px">시드 없던 질의의 키워드(엔티티 후보): ${us.no_seed_keywords.map((k) => `<span class="pill">${esc(k.keyword)} ${k.count}</span>`).join(' ')}</div>` : '') +
      (us.no_seed_samples.length ? `<div class="muted small">예: ${esc(us.no_seed_samples.slice(0, 4).join(' | '))}</div>` : '');
    const cp = p.compare; const cel = $('#gp-compare');
    if (cp) { cel.classList.remove('hidden'); cel.innerHTML = `<h3>직전 실행과 비교 <small class="muted">${esc(cp.before.generated_at || '')} (v${esc(cp.before.build_version || '')}) → ${esc(cp.after.generated_at || '')} (v${esc(cp.after.build_version || '')})</small></h3>` + tbl(['지표', '이전', '이번', 'Δ'], Object.values(cp.deltas).map((d) => [esc(d.name), `<span class="num">${d.before == null ? '-' : d.before}</span>`, `<span class="num">${d.after == null ? '-' : d.after}</span>`, `<span class="num ${d.delta > 0 ? 'ok' : d.delta < 0 ? 'warntxt' : 'muted'}">${d.delta == null ? '' : (d.delta > 0 ? '+' : '') + d.delta}</span>`])); }
    else if (p.compare === null) { cel.classList.remove('hidden'); cel.innerHTML = '<div class="muted small">비교할 이전 프로파일이 없습니다 — 이번 실행이 첫 기록입니다. 규칙을 고치고 빌드(graph) 한 뒤 다시 "이전 실행과 비교"를 누르세요.</div>'; }
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
    $('#gp-history').innerHTML = `<div class="muted small">${esc(h.dir)} · 보관 ${h.keep}개 (config.json graph_profile_keep)</div>` + tbl(['시각', 'build', '엔티티', '관계', '고립%', '성분', '커버%', 'cooccur%'], (h.history || []).slice(0, 12).map((r) => [esc(r.generated_at), esc(r.build_version), r.entities, r.relations, pct(r.isolated_ratio), r.components, r.coverage_pct, pct(r.cooccur_share)]));
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
