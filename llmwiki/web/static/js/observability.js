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
  loaders._toggleChanged = () => { if ($('#tab-arch').classList.contains('active')) renderArch(); };
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
  $$('#sys-maint button').forEach((b) => b.onclick = async () => { if (b.dataset.m === 'purge_requests' && !confirm('requests 테이블을 비웁니다.')) return; b.disabled = true; const r = await api('/api/maintenance', { action: b.dataset.m }); b.disabled = false; $('#sys-maint-out').textContent = JSON.stringify(r); loadSystem(); loadStatus(); });
  $('#btn-perf-save').onclick = async () => { const st = {}; $$('[data-perf]').forEach((i) => { st[i.dataset.perf] = parseInt(i.value, 10); }); await api('/api/config', { settings: st }); toast('설정 저장됨'); loadStatus(); };
  loaders.system = loadSystem;

  // ---------------- QUERY LOG ----------------
  async function loadQLog() {
    const q = await api('/api/queries?limit=60');
    $('#logs').innerHTML = '<table><tr><th>id</th><th>time</th><th>query</th><th>fb</th><th>판정</th><th>g</th><th>answer</th><th></th></tr>' + q.map((x) => { const sc = JSON.parse(x.scores || '{}'); return `<tr><td>${x.id}</td><td>${ts(x.ts)}</td><td>${esc(x.query)}</td><td>${x.feedback == null ? '' : x.feedback > 0 ? '👍' : '👎'}</td><td class="small">${esc(sc.verdict || '')}</td><td class="num">${sc.groundedness == null ? '' : fmt(sc.groundedness, 2)}</td><td class="muted small">${esc((x.answer || '').slice(0, 80))}</td><td><button class="secondary mini" data-tr="${x.id}">trace</button></td></tr>`; }).join('') + '</table>';
    $$('#logs [data-tr]').forEach((b) => b.onclick = async () => renderTrace($('#logs-trace'), await api('/api/query_trace?id=' + b.dataset.tr)));
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
    const j = await api('/api/cli', { argv: cmd });
    $('#console-out').textContent = '$ python -m llmwiki ' + cmd + '\n' + (j.output || '') + '\n[exit ' + j.code + ']';
    loadStatus();
  }
  $('#btn-console').onclick = runConsole;
  $('#c-cmd').addEventListener('keydown', (e) => { if (e.key === 'Enter') runConsole(); });
})(window.LW);
