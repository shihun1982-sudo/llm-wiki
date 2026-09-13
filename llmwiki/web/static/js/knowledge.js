/* Knowledge — 그래프(provenance 필터), 위키, 그래프 규칙. */
(function (LW) {
  'use strict';
  const { $, $$, esc, fmt, api, toast, STATE, PALETTE, switchTab, switchGroup, loaders } = LW;

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
  async function showEntity(id) {
    const d = await api('/api/entity?id=' + encodeURIComponent(id)); if (!d.entity) return;
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

  // ---------------- WIKI ----------------
  let WIKI_PAGES = [];
  async function loadWikiList() { WIKI_PAGES = await api('/api/wiki/list'); renderWikiList(); if (!$('#w-content').value) openWiki('INDEX'); }
  function renderWikiList() { const f = ($('#w-filter').value || '').toLowerCase(); $('#w-list').innerHTML = WIKI_PAGES.filter((p) => p.toLowerCase().includes(f)).slice(0, 800).map((p) => `<div data-p="${esc(p)}" class="${p === $('#w-name').textContent ? 'sel' : ''}">${esc(p)}</div>`).join(''); $$('#w-list div').forEach((d) => d.onclick = () => openWiki(d.dataset.p)); }
  $('#w-filter').addEventListener('input', renderWikiList);
  async function openWiki(name) { const j = await api('/api/wiki/page?name=' + encodeURIComponent(name)); if (j.content == null) return; $('#w-name').textContent = name; $('#w-content').value = j.content; renderWikiList(); }
  $('#btn-w-save').onclick = async () => { await api('/api/wiki/page', { name: $('#w-name').textContent, content: $('#w-content').value }); toast('저장됨'); };
  $('#btn-w-rebuild').onclick = async () => { await api('/api/wiki/page', { name: $('#w-name').textContent, content: $('#w-content').value }); switchGroup('corpus'); switchTab('build'); LW.runBuild(false); };
  loaders.wiki = loadWikiList;

  // ---------------- GRAPH RULES ----------------
  async function loadRules() { const r = await api('/api/rules'); $('#rules-json').value = JSON.stringify(r, null, 2); }
  $('#btn-rules-save').onclick = async () => { let r; try { r = JSON.parse($('#rules-json').value); } catch (e) { toast('JSON 오류'); return; } await api('/api/rules', { rules: r }); toast('규칙 저장됨 — 빌드(전체) 시 반영'); };
  loaders.rules = loadRules;
})(window.LW);
