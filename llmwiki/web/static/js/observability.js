/* Observability — 요청 프로파일, 로그 뷰어, 구조·흐름, 시스템·규모, 질의 로그, 콘솔. */
(function (LW) {
  'use strict';
  const { $, $$, esc, fmt, fmtK, ts, dt, api, toast, STATE, overrides, renderTrace, renderStageTable, flatten, loadStatus, loaders, switchTab, switchGroup } = LW;

  // ---------------- REQUESTS ----------------
  async function loadRequests() {
    const kind = $('#req-kind').value, lim = $('#req-limit').value || 60;
    const rows = await api(`/api/requests?kind=${kind}&limit=${lim}`);
    $('#req-list').innerHTML = '<table class="req"><tr><th>#</th><th>time</th><th>kind</th><th>summary</th><th>ms</th><th>llm</th><th>tok</th><th>sql</th><th>run</th></tr>' + rows.map((r) => `<tr data-id="${r.id}" class="${r.error ? 'has-err' : ''}"><td>${r.id}</td><td>${ts(r.ts)}</td><td><span class="kind ${r.kind}">${r.kind}</span></td><td class="sum">${esc((r.summary || '').slice(0, 60))}</td><td class="num">${fmt(r.ms, 0)}</td><td class="num">${r.llm_calls || ''}</td><td class="num">${r.llm_calls ? fmtK((r.input_tokens || 0) + (r.output_tokens || 0)) : ''}</td><td class="num">${r.sql_count}</td><td class="mono small muted">${esc((r.run_id || '').slice(0, 8))}</td></tr>`).join('') + '</table>';
    $$('#req-list tr[data-id]').forEach((tr) => tr.onclick = () => { $$('#req-list tr').forEach((x) => x.classList.remove('sel')); tr.classList.add('sel'); openRequest(parseInt(tr.dataset.id, 10)); });
  }
  async function openRequest(id) {
    const r = await api('/api/request?id=' + id); if (!r || !r.id) return;
    let other = null; const cid = $('#req-compare').value;
    if (cid) { const o = await api('/api/request?id=' + cid); if (o && o.trace) other = o.trace; }
    const el = $('#req-inspector'); const tr = r.trace || {}; const sm = tr.summary || { llm: {}, slowest: [], skipped: [], errors: [] };
    const res = r.result || {};
    el.innerHTML = `<div class="req-head"><b>#${r.id}</b> <span class="kind ${r.kind}">${r.kind}</span> ${esc(r.summary)} <span class="muted">${dt(r.ts)} · run ${esc(r.run_id || '-')}</span></div>` +
      `<div class="statrow"><div class="stat"><b>${fmt(r.ms, 0)}</b>ms</div><div class="stat"><b>${r.llm_calls}</b>LLM 호출</div><div class="stat"><b>${fmtK((r.input_tokens || 0) + (r.output_tokens || 0))}</b>토큰</div><div class="stat"><b>${r.sql_count}</b>SQL</div><div class="stat"><b>${r.debug_level}</b>debug</div><div class="stat"><b>${(sm.skipped || []).length}</b>skipped</div>${res.evidence ? `<div class="stat"><b>${esc(res.evidence.verdict)}</b>근거 판정</div>` : ''}${res.groundedness != null ? `<div class="stat"><b>${fmt(res.groundedness, 2)}</b>groundedness</div>` : ''}</div>` +
      (r.error ? `<div class="mb err"><pre>${esc(r.error)}</pre></div>` : '') +
      `<div class="row"><button class="secondary mini" id="req-open-all">모든 단계 펼치기</button><button class="secondary mini" id="req-raw">raw JSON</button><button class="secondary mini" id="req-copy">trace 복사</button><button class="secondary mini" id="req-logs">로그 보기 (run_id)</button><button class="secondary mini" id="req-fx">포렌식</button>${r.kind === 'query' && r.result ? '<button class="secondary mini" id="req-rerun">같은 질의 다시 실행</button>' : ''}</div>` +
      `<h3>워터폴</h3><div id="req-trace"></div><h3>단계 표 ${other ? '(비교: #' + cid + ')' : ''}</h3><div id="req-table"></div>` +
      (r.config ? `<h3>요청 설정</h3><pre class="small pre">${esc(JSON.stringify(r.config, null, 1).slice(0, 4000))}</pre>` : '') +
      (r.result ? `<h3>결과 요약</h3><pre class="small pre">${esc(JSON.stringify(r.result, null, 1).slice(0, 6000))}</pre>` : '') + `<pre id="req-rawjson" class="small pre hidden"></pre>`;
    renderTrace($('#req-trace'), tr); renderStageTable($('#req-table'), tr, other);
    $('#req-open-all').onclick = () => $$('#req-trace .tr-row').forEach((x) => x.classList.add('open'));
    $('#req-raw').onclick = () => { const p = $('#req-rawjson'); p.classList.toggle('hidden'); p.textContent = JSON.stringify(r, null, 1); };
    $('#req-copy').onclick = () => { navigator.clipboard.writeText(JSON.stringify(tr, null, 1)).then(() => toast('복사됨')); };
    $('#req-logs').onclick = () => { $('#log-request').value = r.id; $('#log-run').value = ''; switchTab('logs'); setTimeout(loadLogs, 200); };
    $('#req-fx').onclick = () => { $('#fx-req').value = r.id; switchGroup('quality'); switchTab('forensics'); setTimeout(() => $('#btn-fx-run').click(), 300); };
    const rr = $('#req-rerun'); if (rr) rr.onclick = () => { $('#q').value = r.result.query || r.summary; switchGroup('ask'); switchTab('query'); LW.runQuery(); };
  }
  $('#btn-req-refresh').onclick = loadRequests; $('#req-kind').onchange = loadRequests;
  loaders.requests = loadRequests; LW.openRequest = openRequest;

  // ---------------- LOGS ----------------
  async function loadLogs() {
    const q = new URLSearchParams({ file: $('#log-file').value, n: $('#log-n').value || 200 });
    if ($('#log-request').value) q.set('request', $('#log-request').value);
    if ($('#log-run').value) q.set('run', $('#log-run').value);
    if ($('#log-text').value) q.set('text', $('#log-text').value);
    if ($('#log-level').value) q.set('level', $('#log-level').value);
    if ($('#log-since').value) q.set('since', $('#log-since').value);
    const j = await api('/api/logs?' + q.toString());
    const rows = j.rows || [];
    $('#log-table').innerHTML = '<table><tr><th>시각</th><th>level</th><th>kind</th><th>run</th><th>logger</th><th>메시지</th><th>data</th></tr>' + rows.map((r) => `<tr class="${r.level === 'ERROR' ? 'has-err' : ''}"><td class="muted small">${esc(r.t || '')}</td><td><span class="pill ${r.level === 'ERROR' ? 'bad' : r.level === 'WARNING' ? 'warn' : ''}">${esc(r.level || '')}</span></td><td class="small">${esc(r.kind || '')}</td><td class="mono small">${esc((r.run_id || '').slice(0, 8))}</td><td class="small muted">${esc((r.logger || '').replace('llmwiki.', ''))}</td><td class="small">${esc(r.msg || '')}</td><td class="small mono muted">${esc(r.data ? JSON.stringify(r.data).slice(0, 200) : '')}</td></tr>`).join('') + '</table>' + (rows.length ? '' : '<div class="muted">로그 없음</div>');
    const f = await api('/api/logs/files'); $('#log-dir').textContent = f.dir; $('#log-files').textContent = (f.files || []).map((x) => `${x.file} ${fmtK(x.bytes)}B`).join(' · ');
  }
  $('#btn-logs-load').onclick = loadLogs;
  loaders.logs = loadLogs;

  // ---------------- ARCHITECTURE / FLOW ----------------
  let ARCH = null, ARCH_SEL = null;
  async function loadArch() { ARCH = await api('/api/architecture'); renderArch(); }
  function lastStageInfo(flowKey, stageNames) {
    if (!ARCH || !$('#arch-last').checked) return null;
    const kind = flowKey === 'query' ? 'query' : flowKey === 'build' || flowKey === 'watch' ? 'build' : flowKey === 'evolve' ? 'eval' : null;
    const last = kind && ARCH.last && ARCH.last[kind]; if (!last) return null;
    const flat = flatten(last.trace); const nodes = flat.filter((n) => stageNames.includes(n.name));
    if (!nodes.length) return { missing: true, total: last.trace.ms };
    const enabled = nodes.filter((n) => n.enabled !== false);
    const ms = enabled.reduce((a, n) => a + (n.ms || 0), 0);
    return { ms, total: last.trace.ms || 1, skipped: !enabled.length, reason: !enabled.length ? ((nodes[0].meta || {}).reason || 'skipped') : null, error: nodes.some((n) => n.error), nodes, id: last.id };
  }
  function renderArch() {
    if (!ARCH) return;
    const live = $('#arch-live').checked; const tg = live ? Object.assign({}, ARCH.toggles, overrides()) : ARCH.toggles;
    const sel = $('#arch-flow').value; const flows = Object.keys(ARCH.flows).filter((k) => sel === 'all' || k === sel);
    $('#arch-flows').innerHTML = flows.map((fk) => {
      const f = ARCH.flows[fk]; const kind = fk === 'query' ? 'query' : (fk === 'build' || fk === 'watch') ? 'build' : 'eval'; const last = ARCH.last && ARCH.last[kind];
      return `<div class="flow"><h4>${esc(f.title)} <span class="pill">${fk}</span>${last && $('#arch-last').checked ? ` <span class="pill" title="${esc(last.summary)}">last: #${last.id} ${fmt(last.ms, 0)} ms</span>` : ''}</h4><div class="entry">진입: ${esc(f.entry)}</div><div class="fdesc">${esc(f.desc)}</div><div class="flow-chain">` +
        f.stages.map((st, i) => {
          const offT = st.toggles.filter((t) => tg[t] === false); const onT = st.toggles.filter((t) => tg[t] !== false);
          const gate = st.toggles.length && offT.length === st.toggles.length; const li = lastStageInfo(fk, st.trace);
          const cls = ['snode', gate || (li && li.skipped) ? 'off' : '', li && li.error ? 'err' : '', ARCH_SEL === fk + ':' + st.key ? 'sel' : ''].join(' ');
          const msChip = li ? (li.missing ? '' : li.skipped ? `<span class="chip skip">${esc(li.reason).slice(0, 22)}</span>` : `<span class="chip ms">${fmt(li.ms, 0)} ms · ${fmt(100 * li.ms / li.total, 0)}%</span>`) : '';
          const bar = li && !li.skipped && !li.missing ? `<div class="msbar" style="width:${Math.min(100, 100 * li.ms / li.total)}%"></div>` : '';
          return `<div class="${cls}" data-flow="${fk}" data-stage="${st.key}" title="${esc(st.desc)}"><div class="st">${esc(st.title)}</div><div class="sk">${esc(st.key)}</div><div class="chips">${onT.map((t) => `<span class="chip on">${t}</span>`).join('')}${offT.map((t) => `<span class="chip off">${t}</span>`).join('')}${st.tunables.length ? `<span class="chip tune">tune ${st.tunables.length}</span>` : ''}${msChip}</div>${bar}</div>` + (i < f.stages.length - 1 ? '<div class="arrow">→</div>' : '');
        }).join('') + '</div></div>';
    }).join('');
    $$('#arch-flows .snode').forEach((n) => n.onclick = () => { ARCH_SEL = n.dataset.flow + ':' + n.dataset.stage; renderArch(); renderArchDetail(n.dataset.flow, n.dataset.stage); });
  }
  function renderArchDetail(fk, key) {
    const st = ARCH.flows[fk].stages.find((s) => s.key === key); if (!st) return;
    const tg = $('#arch-live').checked ? Object.assign({}, ARCH.toggles, overrides()) : ARCH.toggles; const li = lastStageInfo(fk, st.trace);
    const settingsVal = (k) => { const base = k.split('.')[0]; const v = ARCH.settings[base]; return v == null ? '' : (typeof v === 'object' ? JSON.stringify(k.includes('.') ? (v[k.split('.')[1]] || {}) : v) : String(v)); };
    let html = `<div class="adetail"><h4>${esc(st.title)} <code>${esc(st.key)}</code></h4><div class="muted small">${esc(st.module || '')}${st.io ? ' · ' + esc(st.io) : ''}</div>` +
      `<div class="sec"><b>동작</b>${esc(st.desc)}</div><div class="sec"><b>impact</b><div class="imp">${esc(st.impact)}</div></div>`;
    if (st.toggles.length) html += `<div class="sec"><b>토글 (사이드바에서 즉시 변경)</b>${st.toggles.map((t) => `<div class="tg-row"><span><span class="chip ${tg[t] === false ? 'off' : 'on'}">${t}</span> ${tg[t] === false ? 'OFF' : 'ON'}${ARCH.toggles[t] !== tg[t] ? ' <small class="muted">(config: ' + (ARCH.toggles[t] ? 'on' : 'off') + ')</small>' : ''}</span><span class="muted">${esc(ARCH.toggle_help[t] || '')}</span></div>`).join('')}</div>`;
    if (st.settings.length) html += `<div class="sec"><b>config.json 설정</b>${st.settings.map((k) => `<div class="tg-row"><span><code>${esc(k)}</code> = ${esc(settingsVal(k)).slice(0, 60)}</span><span class="muted">${esc(ARCH.setting_help[k] || '')}</span></div>`).join('')}</div>`;
    if (st.tunables.length) html += `<div class="sec"><b>튜닝 파라미터 (${st.tunables.length}) <button class="mini secondary" id="arch-go-tuning">튜닝에서 편집</button></b><table><tr><th>키</th><th>현재</th><th>impact</th></tr>${st.tunables.map((t) => { const cur = t.source === 'config' ? ARCH.settings[t.key] : (ARCH.tuning[t.key] != null ? ARCH.tuning[t.key] : t.default); const ch = t.source !== 'config' && ARCH.tuning[t.key] != null; return `<tr><td><code>${esc(t.key)}</code>${t.rebuild ? ' <small class="muted">rebuild</small>' : ''}</td><td class="${ch ? 'ok' : ''}">${esc(String(cur))}${ch ? ' <small class="muted">(기본 ' + esc(String(t.default)) + ')</small>' : ''}</td><td class="muted">${esc(t.impact || t.desc)}</td></tr>`; }).join('')}</table></div>`;
    if (st.cli.length) html += `<div class="sec"><b>CLI</b>${st.cli.map((c) => `<code>${esc(c)}</code>`).join(' ')}</div>`;
    if (li && !li.missing) {
      html += `<div class="sec"><b>마지막 실행 (request #${li.id})</b>` + (li.skipped ? `<span class="chip skip">skipped: ${esc(li.reason)}</span>` : `<span class="chip ms">${fmt(li.ms, 1)} ms · ${fmt(100 * li.ms / li.total, 1)}%</span>`) +
        (li.nodes || []).map((n) => { const m = Object.assign({}, n.meta || {}); delete m.reason; const c = n.counters || {}; return `<div style="margin-top:6px"><code>${esc(n.name)}</code> ${n.enabled === false ? '<span class="muted">skipped</span>' : fmt(n.ms, 1) + ' ms'}${c.sql ? ' · sql ' + c.sql : ''}${c.llm_calls ? ' · llm ' + c.llm_calls + ' (' + fmtK((c.llm_input_tokens || 0) + (c.llm_output_tokens || 0)) + ' tok)' : ''}${n.error ? '<div class="errtxt">' + esc(n.error) + '</div>' : ''}<pre class="pre" style="max-height:160px">${esc(JSON.stringify(m, null, 1).slice(0, 1500))}</pre></div>`; }).join('') + `<button class="mini secondary" id="arch-go-req">요청 프로파일에서 전체 trace</button></div>`;
    } else if ($('#arch-last').checked) html += `<div class="sec muted">이 흐름의 최근 실행 기록이 없거나 단계가 trace 에 없습니다.</div>`;
    $('#arch-detail').innerHTML = html + '</div>';
    const gt = $('#arch-go-tuning'); if (gt) gt.onclick = () => { switchGroup('settings'); switchTab('tuning'); setTimeout(() => { $('#tuning-stage').value = st.tuning_stage; LW.renderTuning(); }, 500); };
    const gr = $('#arch-go-req'); if (gr) gr.onclick = () => { switchTab('requests'); setTimeout(() => openRequest(li.id), 300); };
  }
  $('#btn-arch-refresh').onclick = loadArch; $('#arch-flow').onchange = renderArch; $('#arch-last').onchange = renderArch; $('#arch-live').onchange = renderArch;
  loaders._toggleChanged = () => { if (LW.tabVisible('arch')) renderArch(); };
  loaders.arch = loadArch;

  // ---------------- SYSTEM ----------------
  async function loadSystem() {
    const j = await api(`/api/system?target_docs=${$('#sys-target').value}&daily_new=${$('#sys-daily').value}&horizon_days=${$('#sys-days').value}`);
    const ix = j.index, pr = j.projection;
    const row = (k, v) => `<tr><td>${k}</td><td class="num">${v.docs}</td><td class="num">${fmtK(v.chunks)}</td><td class="num">${v.vector_matrix_mb} MB</td><td class="num">${v.db_mb} MB</td></tr>`;
    $('#sys-index').innerHTML = `<div class="statrow"><div class="stat"><b>${ix.docs}</b>docs</div><div class="stat"><b>${ix.chunks}</b>chunks</div><div class="stat"><b>${ix.embeddings}</b>vectors</div><div class="stat"><b>${ix.entities}</b>entities</div><div class="stat"><b>${fmtK(ix.relations)}</b>relations</div><div class="stat"><b>${j.files.db_mb}</b>DB MB</div><div class="stat"><b>${j.files.wal_mb}</b>WAL MB</div><div class="stat"><b>${j.avg_chunks_per_doc}</b>chunks/doc</div><div class="stat"><b>${j.embedding.dim}</b>embed dim</div></div>` +
      `<table><tr><th></th><th>docs</th><th>chunks</th><th>벡터 행렬 RAM</th><th>DB 크기</th></tr>${row('현재', pr.current)}${row('목표 (' + pr.assumptions.target_docs + ')', pr.target)}${row(pr.assumptions.horizon_days + '일 후 (+' + pr.assumptions.daily_new + '/일)', pr.after_horizon)}</table><div class="muted small">${esc(pr.assumptions.note)}</div>`;
    const b = j.build_history || []; const mx = Math.max.apply(null, b.map((x) => x.ms).concat([1]));
    $('#sys-builds').innerHTML = b.length ? '<div class="bars">' + b.map((x) => `<div class="barcol" title="#${x.id} ${esc(x.summary)} ${fmt(x.ms, 0)} ms"><i style="height:${Math.max(2, 100 * x.ms / mx)}%"></i><span>${fmt(x.ms / 1000, 1)}s</span></div>`).join('') + '</div>' : '<span class="muted">빌드 이력 없음</span>';
    const ql = j.query_latency;
    $('#sys-latency').innerHTML = `<div class="statrow"><div class="stat"><b>${ql.n}</b>최근 질의</div><div class="stat"><b>${fmt(ql.avg_ms, 0)}</b>avg ms</div><div class="stat"><b>${fmt(ql.p50_ms, 0)}</b>p50</div><div class="stat"><b>${fmt(ql.p95_ms, 0)}</b>p95</div></div>`;
    const w = j.watcher; $('#watch-interval').value = w.interval;
    $('#sys-watch').textContent = `enabled=${w.enabled} interval=${w.interval}s builds=${w.builds} errors=${w.errors}\nlast_scan=${w.last_scan ? dt(w.last_scan) : '-'} ${JSON.stringify(w.last_result || {})}\nlast_build=${JSON.stringify(w.last_build || {})}`;
    $('#sys-caches').textContent = JSON.stringify(j.caches, null, 1);
    const ps = j.perf_settings;
    $('#perf-form').innerHTML = Object.keys(ps).map((k) => `<label title="${esc(STATE.settingHelp[k] || '')}">${k} <input data-perf="${k}" type="number" value="${ps[k]}" style="width:90px"><small class="muted">${esc((STATE.settingHelp[k] || '').slice(0, 70))}</small></label>`).join('');
  }
  $('#btn-sys-refresh').onclick = loadSystem;
  ['#sys-target', '#sys-daily', '#sys-days'].forEach((s) => $(s).addEventListener('change', loadSystem));
  $('#btn-watch-start').onclick = async () => { await api('/api/watch', { action: 'start', interval: parseInt($('#watch-interval').value, 10), save: true }); toast('워처 시작'); loadSystem(); loadStatus(); };
  $('#btn-watch-stop').onclick = async () => { await api('/api/watch', { action: 'stop', save: true }); toast('워처 중지'); loadSystem(); loadStatus(); };
  $('#btn-watch-scan').onclick = async () => { const r = await api('/api/watch', { action: 'scan' }); toast(`scan ${r.scan_ms}ms changed=${r.n_changed} removed=${r.n_removed}`); loadSystem(); };
  $('#btn-watch-tick').onclick = async () => { const r = await api('/api/watch', { action: 'tick' }); toast(`changed=${r.n_changed} built=${r.built}` + (r.build ? ' ' + fmt(r.build.ms, 0) + 'ms' : '')); loadSystem(); loadStatus(); };
  // purge_requests 는 서버 게이트가 파괴적 작업으로 분류 → 확인 문구(+비밀번호) 모달이 뜬다
  $$('#sys-maint button').forEach((b) => b.onclick = async () => { b.disabled = true; const r = await api('/api/maintenance', { action: b.dataset.m }); b.disabled = false; $('#sys-maint-out').textContent = r.cancelled ? '취소됨' : JSON.stringify(r); loadSystem(); loadStatus(); });
  $('#btn-perf-save').onclick = async () => { const st = {}; $$('[data-perf]').forEach((i) => { st[i.dataset.perf] = parseInt(i.value, 10); }); await api('/api/config', { settings: st }); toast('설정 저장됨'); loadStatus(); };
  loaders.system = loadSystem;

  // ---------------- ACTIVITY (진행 중 작업 — viewer 도 조회 가능) ----------------
  function actRow(r, canCancel) {
    const pct = r.pct == null ? '' : `<div class="progress mini"><i style="width:${Math.max(0, Math.min(100, r.pct))}%"></i><span>${fmt(r.pct, 0)}%${r.eta_s ? ' · ≈' + LW.fmtS(r.eta_s) : ''}</span></div>`;
    const llm = r.llm && r.llm.active ? `<span class="pill warn">LLM ${esc(r.llm.provider || '')}/${esc(r.llm.model || '')} ${LW.fmtS(r.llm.elapsed_s)}</span>` : '';
    const q = r.queue ? `<span class="pill">대기열 ${r.queue.position}</span>` : '';
    const btn = canCancel && r.status === 'running' && !r.cancel_requested ? `<button class="mini danger" data-cancel="${esc(r.token)}">■ 중지</button>` : (r.cancel_requested ? '<span class="pill warn">중지 중</span>' : '');
    return `<tr><td class="mono small">${esc(r.token || '')}</td><td>${esc(r.kind || '')}${r.external ? ' <span class="pill">외부</span>' : ''}${r.weight === 'exclusive' ? ' <span class="pill bad">배타</span>' : r.weight === 'soft' ? ' <span class="pill">쓰기</span>' : ''}</td>` +
      `<td>${esc((r.label || '').slice(0, 70))}</td><td>${esc(r.user || '-')}${r.role ? ' <small class="muted">' + esc(r.role) + '</small>' : ''}</td><td class="small">${esc(r.origin || '')}${r.ip ? '<br><span class="muted">' + esc(r.ip) + '</span>' : ''}</td>` +
      `<td class="num">${LW.fmtDur(r.elapsed_s)}</td><td class="small">${esc(r.stage || r.status || '')}${r.note ? ' <span class="pill ok">' + esc(r.note) + '</span>' : ''}${pct}${llm}${q}</td><td>${btn}</td></tr>`;
  }
  // ---- 보드(압축 목록) ----
  // 한 줄에 종류·작업·사용자·경과·진행을 담아 쭉 나열한다. 30명이 동시에 써도 스크롤 한 번으로 훑을 수 있게.
  const KIND_ICON = { query: '💬', search: '🔎', build: '🔨', job: '🔨', cli: '⌨', mcp: '🔌', schedule: '⏰', snapshot: '💾', login: '🔑', read: '📄', watch: '👁' };
  function actCard(r, canCancel, kind) {
    const pctN = r.pct == null ? null : Math.max(0, Math.min(100, r.pct));
    const bar = kind === 'queued'
      ? '<div class="act-bar wait"><i style="width:100%"></i></div>'
      : `<div class="act-bar${pctN == null ? ' indet' : ''}"><i style="width:${pctN == null ? 100 : pctN}%"></i></div>`;
    const tags = [
      r.external ? '<span class="pill">외부</span>' : '',
      r.weight === 'exclusive' ? '<span class="pill bad">배타</span>' : r.weight === 'soft' ? '<span class="pill">쓰기</span>' : '',
      r.llm && r.llm.active ? `<span class="pill warn">LLM ${LW.fmtS(r.llm.elapsed_s)}</span>` : '',
      r.cancel_requested ? '<span class="pill warn">중지 중</span>' : '',
      r.note ? `<span class="pill ok">${esc(r.note)}</span>` : '',
    ].filter(Boolean).join('');
    const where = [r.stage, pctN == null ? '' : fmt(pctN, 0) + '%', r.eta_s ? '≈' + LW.fmtS(r.eta_s) : ''].filter(Boolean).join(' · ');
    const pos = kind === 'queued' && r.queue ? `<span class="act-pos">${r.queue.position}</span>` : '';
    const btn = canCancel && !r.cancel_requested ? `<button class="act-x" data-cancel="${esc(r.token)}" title="중지">■</button>` : '';
    return `<div class="act-item ${esc(kind)} k-${esc(r.kind || 'etc')}${r.mine ? ' mine' : ''}" title="${esc(r.label || '')}">
      ${pos}<span class="act-ico">${KIND_ICON[r.kind] || '•'}</span>
      <span class="act-main"><span class="act-label">${esc((r.label || r.kind || '-').slice(0, 90))}</span>
        <span class="act-sub">${esc(r.kind || '')}${r.user ? ' · ' + esc(r.user) : ''}${r.origin ? ' · ' + esc(r.origin) : ''}${where ? ' · ' + esc(where) : ''}</span></span>
      <span class="act-tags">${tags}</span><span class="act-t">${LW.fmtDur(r.elapsed_s)}</span>${bar}${btn}</div>`;
  }
  function recentCard(r) {
    const cls = r.status === 'done' ? 'ok' : r.status === 'cancelled' ? 'warn' : 'bad';
    return `<div class="act-item done ${cls}" title="${esc(r.error || r.label || '')}">
      <span class="act-ico">${KIND_ICON[r.kind] || '•'}</span>
      <span class="act-main"><span class="act-label">${esc((r.label || r.kind || '-').slice(0, 90))}</span>
        <span class="act-sub">${esc(r.kind || '')}${r.user ? ' · ' + esc(r.user) : ''}${r.error ? ' · ' + esc(String(r.error).slice(0, 50)) : ''}</span></span>
      <span class="act-tags">${r.note ? `<span class="pill ok">${esc(r.note)}</span>` : ''}<span class="pill ${cls}">${esc(r.status || '')}</span></span>
      <span class="act-t">${LW.fmtDur(r.elapsed_s)}</span></div>`;
  }
  // 용량 게이지: 동시 실행 슬롯을 칸으로 그려 '지금 얼마나 찼는지'를 숫자 대신 눈으로 보게 한다.
  function actGauge(j, run, qd, ext) {
    const lim = (j.limits || {}), max = Math.max(1, Number(lim.max_parallel_reads || 8));
    const used = (j.running || []).length, qn = (j.queued || []).length, qmax = Number(lim.queue_max || 64);
    const pips = Array.from({ length: Math.min(max, 24) }, (_, i) => `<i class="${i < used ? 'on' : ''}"></i>`).join('');
    const lock = j.lock || {};
    const lockTxt = lock.writer ? `<span class="pill bad">쓰기 중 ${esc(lock.writer_label || lock.writer)}</span>`
      : (lock.writers_waiting ? `<span class="pill warn">쓰기 대기 ${lock.writers_waiting}</span>` : '<span class="pill ok">여유</span>');
    const qbar = `<span class="act-qbar" title="대기열 ${qn}/${qmax}"><i style="width:${Math.min(100, qmax ? qn / qmax * 100 : 0)}%"></i></span>`;
    return `<div class="ag-slots" title="동시 실행 슬롯 ${used}/${max}"><span class="ag-cap">슬롯</span>${pips}<b>${used}/${max}</b></div>
      <div class="ag-item"><span class="ag-cap">대기</span>${qbar}<b>${qn}</b></div>
      <div class="ag-item"><span class="ag-cap">외부</span><b>${(j.external || []).length}</b><small class="muted">CLI·MCP</small></div>
      <div class="ag-item"><span class="ag-cap">락</span>${lockTxt}</div>
      <div class="ag-item grow"></div>
      <div class="ag-item"><small class="muted">보이는 항목 ${run.length + qd.length + ext.length}건</small></div>`;
  }
  // 공용 활동 피드가 주는 데이터로 그린다 (탭마다 따로 폴링하지 않는다 — 브라우저 연결 6개 제한)
  function renderActivity(j) {
    if (!LW.tabVisible('activity')) return;
    if (!j || j.error) { $('#act-board').innerHTML = `<div class="muted">${esc((j && j.error) || '조회 실패')}</div>`; return; }
    const mine = $('#act-mine').checked, me = j.me;
    const filt = (rows) => (mine ? (rows || []).filter((r) => r.user === me) : (rows || []));
    const mark = (rows) => (rows || []).map((r) => Object.assign({ mine: !!(me && r.user === me) }, r));
    const run = mark(filt(j.running)), qd = mark(filt(j.queued)), ext = mark(filt(j.external)), rec = filt(j.recent);
    const canCancel = (r) => j.admin || (me && r.user === me);
    $('#act-gauge').innerHTML = actGauge(j, run, qd, ext);
    $('#act-summary').innerHTML = `<div class="stat"><b>${(j.running || []).length}</b>실행 중</div><div class="stat"><b>${(j.queued || []).length}</b>대기</div><div class="stat"><b>${(j.external || []).length}</b>외부(CLI/MCP)</div>` +
      `<div class="stat"><b>${(j.lock || {}).readers || 0}</b>읽기 락</div><div class="stat"><b>${esc((j.lock || {}).writer || '-')}</b>쓰기 락</div><div class="stat"><b>${(j.limits || {}).max_parallel_reads}</b>동시 실행 상한</div>`;
    const sect = (title, rows, kind) => rows.length ? `<div class="act-sect">${title} <b>${rows.length}</b></div>` + rows.map((r) => actCard(r, kind !== 'queued' && canCancel(r), kind)).join('') : '';
    $('#act-board').innerHTML = (run.length + qd.length + ext.length)
      ? sect('실행 중', run, 'running') + sect('대기', qd, 'queued') + sect('외부 (CLI · MCP · 스케줄러)', ext, 'running')
      : '<div class="act-empty">지금 실행 중인 작업이 없습니다.</div>';
    $('#act-recent-board').innerHTML = rec.length ? rec.map(recentCard).join('') : '<div class="act-empty">-</div>';
    const head = '<table><tr><th>토큰</th><th>종류</th><th>작업</th><th>사용자</th><th>출처</th><th>경과</th><th>진행</th><th></th></tr>';
    $('#act-running').innerHTML = (run.length + qd.length + ext.length)
      ? head + run.concat(qd, ext).map((r) => actRow(r, canCancel(r))).join('') + '</table>'
      : '<div class="muted">실행 중인 작업이 없습니다.</div>';
    $('#act-recent').innerHTML = rec.length ? head.replace('<th></th>', '<th>결과</th>') + rec.map((r) => actRow(r, false).replace(/<td><\/td>$/, `<td class="small">${esc(r.status || '')}${r.error ? ' ' + esc(String(r.error).slice(0, 60)) : ''}</td>`)).join('') + '</table>' : '<div class="muted">-</div>';
    $$('#tab-activity [data-cancel]').forEach((b) => b.onclick = async () => { b.disabled = true; await LW.cancelToken(b.dataset.cancel, 'activity'); setTimeout(LW.refreshActivity, 600); });
  }
  function actView(v) {
    const board = v !== 'table';
    ['#act-gauge', '#act-board', '#act-recent-board'].forEach((s) => $(s).classList.toggle('hidden', !board));
    ['#act-summary', '#act-running', '#act-recent'].forEach((s) => $(s).classList.toggle('hidden', board));
    $$('#act-view button').forEach((b) => b.classList.toggle('active', (b.dataset.v === 'board') === board));
    try { localStorage.setItem('llmwiki.actview', board ? 'board' : 'table'); } catch (e) { /* ignore */ }
  }
  // 자동 갱신은 공용 피드가 맡는다. 체크를 끄면 그리지만 않는다 (요청 수는 어차피 늘지 않는다).
  let ACT_AUTO = true;
  LW.onActivity((j) => { if (ACT_AUTO) renderActivity(j); });
  $('#btn-act-refresh').onclick = () => LW.refreshActivity();
  $('#act-auto').onchange = () => { ACT_AUTO = $('#act-auto').checked; if (ACT_AUTO) LW.refreshActivity(); };
  $('#act-mine').onchange = () => LW.refreshActivity();
  $$('#act-view button').forEach((b) => b.onclick = () => actView(b.dataset.v));
  let av = 'board'; try { av = localStorage.getItem('llmwiki.actview') || 'board'; } catch (e) { /* ignore */ }
  actView(av);
  loaders.activity = () => { LW.refreshActivity(); };

  // ---------------- SERVER MONITOR (admin) ----------------
  let SRV_TIMER = null, SRV = null;
  const LIMIT_HELP = {
    'concurrency.max_parallel_reads': '동시에 실행할 질의/검색/MCP 도구 호출 수. LLM 대기가 대부분이라 코어 수보다 크게 잡아도 된다 (30명 동시 접속이면 8~16).',
    'concurrency.max_parallel_per_user': '한 사용자가 동시에 돌릴 수 있는 요청 수 (초과 → 429).',
    'concurrency.max_parallel_per_ip': '한 IP 의 동시 요청 수. 리버스 프록시 뒤면 크게.',
    'concurrency.queue_max': '슬롯을 기다리는 요청 상한 (초과 → 503).',
    'concurrency.queue_timeout_s': '대기 최대 시간 (초과 → 503).',
    'concurrency.reads_during_build': 'never | incremental | always — 빌드 중 질의 허용 범위.',
    'concurrency.write_wait_timeout_s': '쓰기 작업이 진행 중인 읽기를 기다리는 최대 시간.',
    'concurrency.read_wait_timeout_s': '읽기가 배타 작업(전체 빌드)을 기다리는 최대 시간.',
    'rate_limit.per_user_per_min': '사용자별 분당 요청 수 (0 = 무제한).',
    'rate_limit.per_ip_per_min': 'IP 별 분당 요청 수.',
    'rate_limit.query_per_user_per_min': '사용자별 분당 질의 수 (LLM 비용 보호).',
    'timeouts.query_s': '질의 1건의 시간 제한(초). 넘으면 자동 중지. 0 = 없음.',
    'timeouts.job_s': '빌드/평가 등 백그라운드 작업의 시간 제한(초). 0 = 없음.',
    'timeouts.mcp_s': 'MCP 도구 호출 시간 제한(초).',
    'sessions.enforce': '켜면 서버가 세션을 기억하고 강제 로그아웃·동시 세션 수 제한이 동작합니다.',
    'sessions.max_per_user': '사용자당 동시 로그인 세션 수 (초과 시 가장 오래된 세션 만료).',
    'monitor.viewer_can_see_activity': 'viewer 도 진행 중 작업 목록을 볼 수 있게 합니다.',
  };
  const EDIT_KEYS = ['concurrency.max_parallel_reads', 'concurrency.max_parallel_per_user', 'concurrency.max_parallel_per_ip', 'concurrency.queue_max', 'concurrency.queue_timeout_s',
    'concurrency.reads_during_build', 'concurrency.write_wait_timeout_s', 'concurrency.read_wait_timeout_s',
    'rate_limit.enabled', 'rate_limit.per_user_per_min', 'rate_limit.per_ip_per_min', 'rate_limit.query_per_user_per_min',
    'timeouts.query_s', 'timeouts.search_s', 'timeouts.job_s', 'timeouts.mcp_s',
    'sessions.enforce', 'sessions.max_per_user', 'sessions.idle_timeout_min',
    'monitor.viewer_can_see_activity', 'monitor.show_user_to_viewer', 'monitor.slow_request_ms'];
  function srvInput(key, val) {
    const id = 'srv-' + key.replace(/\./g, '-');
    if (typeof val === 'boolean') return `<select id="${id}" data-lk="${key}"><option value="true"${val ? ' selected' : ''}>true</option><option value="false"${!val ? ' selected' : ''}>false</option></select>`;
    if (key === 'concurrency.reads_during_build') return `<select id="${id}" data-lk="${key}">${['never', 'incremental', 'always'].map((o) => `<option${o === val ? ' selected' : ''}>${o}</option>`).join('')}</select>`;
    return `<input id="${id}" data-lk="${key}" type="number" value="${esc(val)}" style="width:100px">`;
  }
  let SRV_BUSY = false;
  async function loadServer() {
    if (SRV_BUSY) return;                 // 응답이 늦어도 요청이 쌓이지 않게
    SRV_BUSY = true;
    let j = null;
    try { j = await api('/api/admin/server?history=30'); } finally { SRV_BUSY = false; }
    if (!j || j.error) {
      $('#srv-overview').innerHTML = '';
      $('#srv-msg').innerHTML = `<span class="warntxt">${esc((j && j.error) || '조회 실패')}</span> — 서버 모니터는 admin 전용입니다. 진행 중 작업만 보려면 '진행 중 작업' 탭을 쓰세요.`;
      return;
    }
    SRV = j; $('#srv-msg').textContent = ''; $('#srv-path').textContent = j.config_path || '';
    const lim = j.limits || {};
    $('#srv-overview').innerHTML = `<div class="stat"><b>${j.running}</b>실행 중</div><div class="stat"><b>${j.queued}</b>대기</div><div class="stat"><b>${fmt(j.throughput_per_min, 1)}</b>요청/분</div>` +
      `<div class="stat"><b>${j.errors_in_window}</b>오류(${fmt(j.window_min, 0)}분)</div><div class="stat"><b>${LW.fmtS(j.uptime_s)}</b>uptime</div><div class="stat"><b>${esc((j.lock || {}).writer || '-')}</b>쓰기 락</div>` +
      `<div class="stat"><b>${(j.lock || {}).readers || 0}</b>읽기</div><div class="stat"><b>${(lim.access || {}).maintenance_mode ? 'ON' : 'off'}</b>점검 모드</div><div class="stat"><b>${(j.counters || {}).rejected_rate || 0}</b>속도 제한 거부</div>` +
      `<div class="stat"><b>${(j.counters || {}).rejected_queue || 0}</b>대기열 거부</div><div class="stat"><b>${(j.counters || {}).timeouts || 0}</b>시간 초과</div><div class="stat"><b>${(j.counters || {}).slow || 0}</b>느린 요청</div>`;
    $('#srv-limits').innerHTML = EDIT_KEYS.map((k) => {
      const [a, b] = k.split('.'); const v = (lim[a] || {})[b];
      if (v === undefined) return '';
      return `<label title="${esc(LIMIT_HELP[k] || '')}">${k} ${srvInput(k, v)}<small class="muted">${esc((LIMIT_HELP[k] || '').slice(0, 60))}</small></label>`;
    }).join('');
    const lat = j.latency || {};
    $('#srv-latency').innerHTML = Object.keys(lat).length
      ? '<table><tr><th>종류</th><th>n</th><th>avg</th><th>p50</th><th>p95</th><th>max</th></tr>' + Object.keys(lat).map((k) => { const v = lat[k]; return `<tr><td>${esc(k)}</td><td class="num">${v.n}</td><td class="num">${fmt(v.avg_ms, 0)}</td><td class="num">${fmt(v.p50_ms, 0)}</td><td class="num">${fmt(v.p95_ms, 0)}</td><td class="num">${fmt(v.max_ms, 0)}</td></tr>`; }).join('') + '</table>'
      : '<div class="muted">(최근 요청 없음)</div>';
    const cir = j.circuits || {};
    $('#srv-circuits').innerHTML = Object.keys(cir).length
      ? '<table><tr><th>provider/model</th><th>연속 실패</th><th>상태</th><th>마지막 오류</th></tr>' + Object.keys(cir).map((k) => { const v = cir[k]; return `<tr><td class="mono small">${esc(k)}</td><td class="num">${v.failures}</td><td>${v.open ? '<span class="bad">차단 중 (' + fmt(v.open_until - (Date.now() / 1000), 0) + 's)</span>' : '<span class="ok">정상</span>'}</td><td class="small muted">${esc((v.last_error || '').slice(0, 80))}</td></tr>`; }).join('') + '</table>'
      : '<div class="muted">기록 없음 (모든 LLM 호출 정상)</div>';
    $('#srv-clients').innerHTML = '<table><tr><th>대상</th><th>실행</th><th>누적</th><th>거부</th><th>오류</th><th>마지막</th><th>클라이언트</th></tr>' +
      (j.clients || []).slice(0, 30).map((c) => `<tr><td class="mono small">${esc(c.key)}</td><td class="num">${c.active}</td><td class="num">${c.total}</td><td class="num">${c.rejected || 0}</td><td class="num">${c.errors || 0}</td><td class="small">${ts(c.last_seen)}</td><td class="small muted">${esc((c.agent || '').slice(0, 40))}</td></tr>`).join('') + '</table>';
    const acc = lim.access || {};
    $('#srv-blocks').textContent = `block_ips=${JSON.stringify(acc.block_ips || [])}\nblock_users=${JSON.stringify(acc.block_users || [])}\nallow_ips=${JSON.stringify(acc.allow_ips || [])}`;
    $('#srv-sessions').innerHTML = (j.sessions || []).length
      ? '<table><tr><th>sid</th><th>사용자</th><th>역할</th><th>IP</th><th>로그인</th><th>최근</th><th></th></tr>' + j.sessions.map((s) => `<tr><td class="mono small">${esc(s.sid)}</td><td>${esc(s.user)}</td><td>${esc(s.role)}</td><td class="small">${esc(s.ip || '')}</td><td class="small">${dt(s.created)}</td><td class="small">${ts(s.last_seen)}</td><td><button class="mini secondary" data-rev="${esc(s.sid)}">로그아웃</button></td></tr>`).join('') + '</table>' + ((lim.sessions || {}).enforce ? '' : '<div class="muted small">sessions.enforce=false — 목록만 기록되고 강제 로그아웃은 적용되지 않습니다.</div>')
      : '<div class="muted">기록된 세션 없음</div>';
    $$('#srv-sessions [data-rev]').forEach((b) => b.onclick = async () => { await api('/api/admin/server', { action: 'sessions', sub: 'revoke', sid: b.dataset.rev }); loadServer(); });
    const sel = $('#srv-loglevel'); if (sel && STATE.settings) sel.value = STATE.settings.log_level || 'INFO';
  }
  function srvAuto() {
    if (SRV_TIMER) { clearInterval(SRV_TIMER); SRV_TIMER = null; }
    if ($('#srv-auto').checked && LW.tabVisible('server')) SRV_TIMER = setInterval(() => { if (LW.tabVisible('server')) loadServer(); }, 3000);
  }
  $('#btn-srv-refresh').onclick = loadServer;
  $('#srv-auto').onchange = srvAuto;
  $('#btn-srv-save').onclick = async () => {
    const vals = {};
    $$('#srv-limits [data-lk]').forEach((i) => { vals[i.dataset.lk] = i.value; });
    const j = await api('/api/admin/server', { action: 'set_limits', values: vals, save: true });
    toast(j && j.ok ? '서버 제한 저장됨 (server.json)' : '저장 실패'); loadServer(); loadStatus();
  };
  $('#btn-srv-reload').onclick = async () => { await api('/api/admin/server', { action: 'reload' }); toast('server.json 다시 읽음'); loadServer(); };
  $('#btn-srv-maint').onclick = async () => {
    const on = !((((SRV || {}).limits || {}).access || {}).maintenance_mode);
    if (on && !confirm('점검 모드를 켜면 admin 외의 모든 요청이 503 으로 거부됩니다. 진행할까요?')) return;
    await api('/api/admin/server', { action: 'maintenance', enabled: on }); toast('점검 모드 ' + (on ? 'ON' : 'off')); loadServer(); loadStatus();
  };
  $('#btn-srv-block').onclick = async () => {
    const v = $('#srv-block-value').value.trim(); if (!v) return;
    await api('/api/admin/server', { action: 'block', kind: $('#srv-block-kind').value, value: v, add: true });
    $('#srv-block-value').value = ''; loadServer();
  };
  $('#btn-srv-circuit-reset').onclick = async () => { await api('/api/admin/server', { action: 'circuit_reset' }); toast('회로 초기화'); loadServer(); };
  $('#btn-srv-loglevel').onclick = async () => { const j = await api('/api/admin/server', { action: 'log_level', level: $('#srv-loglevel').value, save: false }); toast('log_level=' + ((j && j.log_level) || '?')); };
  loaders.server = () => { loadServer(); srvAuto(); };

  // ---------------- QUERY LOG ----------------
  async function loadQLog() {
    const q = await api('/api/queries?limit=60');
    $('#logs').innerHTML = '<table><tr><th>id</th><th>time</th><th>query</th><th>fb</th><th>판정</th><th>g</th><th>answer</th><th></th></tr>' + q.map((x) => { const sc = JSON.parse(x.scores || '{}'); return `<tr><td>${x.id}</td><td>${ts(x.ts)}</td><td>${esc(x.query)}</td><td>${x.feedback == null ? '' : x.feedback > 0 ? '👍' : '👎'}</td><td class="small">${esc(sc.verdict || '')}</td><td class="num">${sc.groundedness == null ? '' : fmt(sc.groundedness, 2)}</td><td class="muted small">${esc((x.answer || '').slice(0, 80))}</td><td><button class="secondary mini" data-tr="${x.id}">trace</button></td></tr>`; }).join('') + '</table>';
    // trace 결과는 60행짜리 표 아래에 그려져 화면 밖에 있기 쉽다 → 그린 뒤 그 자리로 스크롤한다
    // (누르면 아무 일도 안 일어나는 것처럼 보이던 문제)
    $$('#logs [data-tr]').forEach((b) => b.onclick = async () => {
      const box = $('#logs-trace');
      box.innerHTML = '<div class="muted small">trace 불러오는 중…</div>';
      box.scrollIntoView({ behavior: 'smooth', block: 'start' });
      renderTrace(box, await api('/api/query_trace?id=' + b.dataset.tr));
      box.scrollIntoView({ behavior: 'smooth', block: 'start' });
    });
  }
  $('#btn-logs').onclick = loadQLog;
  loaders.qlog = loadQLog;

  // ---------------- CONSOLE ----------------
  const CSAMPLES = ['--help', 'health --quick', 'build status', 'build verify', 'embed report', 'stats', 'models', 'models test', 'requests last', 'logs tail -n 30', 'forensic last', 'trial list', 'preset list', 'rules test "PDCCH 재시작 오류"', 'time "지난주 리뷰한 CL"', 'corpus lint', 'corpus example issue', 'graph --provenance explicit --limit 20', 'query "ISSUE-2001 원인" --trace --debug 2', 'eval --matrix', 'memory status', 'system', 'config show --effective'];
  $('#console-samples').innerHTML = CSAMPLES.map((s) => `<span>${esc(s)}</span>`).join('');
  $$('#console-samples span').forEach((s) => s.onclick = () => { $('#c-cmd').value = s.textContent; runConsole(); });
  async function runConsole() {
    const cmd = $('#c-cmd').value.trim(); if (!cmd) return;
    $('#console-out').textContent = '$ python -m llmwiki ' + cmd + '\n…';
    const j = await api('/api/cli', { argv: cmd });   // 파괴적 명령(build --full 등)은 서버 게이트의 확인 모달을 거친다
    $('#console-out').textContent = '$ python -m llmwiki ' + cmd + '\n' + (j.cancelled ? '(취소됨)' : (j.output || (j.error ? 'ERROR: ' + j.error : ''))) + (j.code != null ? '\n[exit ' + j.code + ']' : '');
    loadStatus();
  }
  $('#btn-console').onclick = runConsole;
  $('#c-cmd').addEventListener('keydown', (e) => { if (e.key === 'Enter') runConsole(); });
})(window.LW);
