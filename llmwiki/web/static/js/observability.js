/* Observability — 요청 프로파일, 로그 뷰어, 구조·흐름, 시스템·규모, 질의 로그, 콘솔. */
(function (LW) {
  'use strict';
  const { $, $$, esc, fmt, fmtK, ts, dt, api, toast, STATE, overrides, renderTrace, renderStageTable, flatten, loadStatus, loaders, switchTab, switchGroup } = LW;

  // ---------------- REQUESTS ----------------
  async function loadRequests() {
    const kind = $('#req-kind').value, lim = $('#req-limit').value || 60;
    // 관측 탭은 **서버 전체**를 본다 (권한이 없으면 서버가 내 것만 돌려준다).
    // 응답은 {rows, live, scope, me, can_all} — 예전에는 배열이었다 (2026-09-16).
    const j = await api(`/api/requests?kind=${kind}&limit=${lim}&scope=all`);
    const rows = Array.isArray(j) ? j : ((j && j.rows) || []);
    const showUser = !Array.isArray(j) && rows.some((r) => r.user);
    $('#req-list').innerHTML = '<table class="req"><tr><th>#</th><th>time</th><th>kind</th><th>summary</th><th>ms</th><th>llm</th><th>tok</th><th>sql</th>' + (showUser ? '<th>user</th>' : '') + '<th>run</th></tr>' + rows.map((r) => `<tr data-id="${r.id}" class="${r.error ? 'has-err' : ''}"><td>${r.id}</td><td>${ts(r.ts)}</td><td><span class="kind ${r.kind}">${r.kind}</span></td><td class="sum">${esc((r.summary || '').slice(0, 60))}</td><td class="num">${fmt(r.ms, 0)}</td><td class="num">${r.llm_calls || ''}</td><td class="num">${r.llm_calls ? fmtK((r.input_tokens || 0) + (r.output_tokens || 0)) : ''}</td><td class="num">${r.sql_count}</td>${showUser ? '<td class="small muted">' + esc(r.user || '') + '</td>' : ''}<td class="mono small muted">${esc((r.run_id || '').slice(0, 8))}</td></tr>`).join('') + '</table>'
      + (Array.isArray(j) || j.can_all !== false ? '' : '<div class="muted small">전체 조회 권한이 없어 내 요청만 보입니다 (작업 권한 <code>requests all</code>).</div>');
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
    // 워터폴의 각 단계에 ⟲ — 이 요청의 중간 결과로 그 단계부터 다시 실행 (docs/RERUN.md)
    renderTrace($('#req-trace'), tr, { rerun: r.id, onRerun: (j) => { const nid = (j.result || {}).request_id; loadRequests(); if (nid) openRequest(nid); } });
    renderStageTable($('#req-table'), tr, other);
    $('#req-open-all').onclick = () => $$('#req-trace .tr-row').forEach((x) => x.classList.add('open'));
    $('#req-raw').onclick = () => { const p = $('#req-rawjson'); p.classList.toggle('hidden'); p.textContent = JSON.stringify(r, null, 1); };
    $('#req-copy').onclick = () => LW.copyText(JSON.stringify(tr, null, 1), 'trace JSON');   // http 접속에서도 되는 3단 복사 (core.js copyText)
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
  // 2026-09-19: 이 탭(구조·흐름)은 🧭 Pipeline 에 흡수됐다. 같은 레지스트리(/api/architecture)를 읽기 전용 지도와
  // 편집 화면으로 나눠 그리던 것이 "보는 곳과 고치는 곳이 다르다" 를 만들었다. 지금은 pipeline.js 하나가
  // 흐름 → 페이즈 → 단계 → trace 를 모두 그린다. 예전 주소는 core.js TAB_ALIAS 가 보낸다.
  // 이 파일에는 더 이상 구조·흐름 코드가 없다. 단계 지도는 llmwiki/web/static/js/pipeline.js 하나가 그린다.

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

  // ---------------- 운영 통계 (2026-09-20) ----------------
  // 이 화면은 '규모'(문서·청크 수)만 보여 줬다. 운영자가 묻는 것은 다른 쪽이다 —
  // 빌드가 왜 느린가 · 질의가 언제 몰리나 · 토큰을 어디에 쓰나 · 디스크가 어디서 커지나.
  // 데이터는 이미 DB 에 있었고(요청 trace·질의 로그·임베딩 실행), 읽지 않고 있었을 뿐이다.
  const MB = (b) => (Number(b || 0) / 1048576).toFixed(1) + ' MB';
  function opsTable(head, rows) {
    return '<div class="tbl-wrap"><table><tr>' + head.map((h) => `<th>${esc(h)}</th>`).join('') + '</tr>'
      + rows.map((r) => '<tr>' + r.map((c) => `<td>${c}</td>`).join('') + '</tr>').join('') + '</table></div>';
  }
  // ---- 차트 (의존성 없이 inline SVG) --------------------------------------
  // 외부 차트 라이브러리를 쓰지 않는 이유는 나머지와 같다 — 사내망에 폴더째 복사해서 돌아야 한다.
  // 막대(개수)와 꺾은선(지연·토큰)이면 "나아지나 나빠지나" 에 답하기에 충분하다.
  const SVGNS = 'http://www.w3.org/2000/svg';

  /** 축 눈금용 짧은 수 (1.2k · 243k · 0.85). 자릿수가 길면 축이 본문을 밀어낸다. */
  function nfmt(v, pct) {
    if (v == null) return '-';
    if (pct) return (v * 100).toFixed(v < 0.1 ? 1 : 0) + '%';
    const a = Math.abs(v);
    if (a >= 1e6) return (v / 1e6).toFixed(1).replace(/\.0$/, '') + 'M';
    if (a >= 1e3) return (v / 1e3).toFixed(a >= 1e4 ? 0 : 1).replace(/\.0$/, '') + 'k';
    if (a >= 10) return String(Math.round(v));
    if (a >= 1) return v.toFixed(1).replace(/\.0$/, '');
    return v.toFixed(2);
  }
  /** 눈금이 1·2·5×10^n 으로 떨어지게 위쪽 한계를 올린다 (축 숫자가 7391 같으면 읽기 어렵다). */
  function niceMax(v) {
    if (!(v > 0)) return 1;
    const e = Math.pow(10, Math.floor(Math.log10(v)));
    const m = v / e;
    return (m <= 1 ? 1 : m <= 2 ? 2 : m <= 5 ? 5 : 10) * e;
  }
  /** 구간 이름을 축에 맞게 줄인다 — `2026-09-20`→`09-20`, `2026-W38`→`W38`. */
  function shortBucket(b) {
    const s = String(b || '');
    return s.length === 10 ? s.slice(5) : s.replace(/^\d{4}-/, '');
  }

  /**
   * 구간 차트 — 막대(왼쪽 축) + 꺾은선(오른쪽 축).
   *
   * 예전 판은 `viewBox="0 0 100 30"` 을 `preserveAspectRatio="none"` 으로 늘였다. 그래서
   * 가로가 15배, 세로가 4배 늘어나 **점이 타원이 되고 선이 뭉개졌고**, 축이 없어 값을 읽을 수 없었다.
   * 게다가 `.ch-c0{fill:…}` 이 `.ch-line{fill:none}` 을 덮어써 꺾은선이 **면으로 칠해졌다**(주황 덩어리).
   *
   * 지금은 고정 좌표계에 비율을 유지해 그리고, **축을 둘로 나눈다** — 질의 수(364)와 빌드 횟수(15)를
   * 한 눈금에 올리면 한쪽이 바닥에 깔려 아무것도 못 읽는다. 왼쪽은 막대, 오른쪽은 선의 눈금이다.
   */
  function chart(points, series, opt) {
    opt = opt || {};
    if (!points || !points.length) return '<div class="muted small">그릴 값이 없습니다.</div>';
    const W = 780, H = 190, PL = 54, PR = 54, PT = 14, PB = 30;
    const iw = W - PL - PR, ih = H - PT - PB;
    const n = points.length, step = iw / n;

    // 축별 최대값 (왼쪽 'l' · 오른쪽 'r'). 같은 축의 계열은 눈금을 공유한다(p50/p95 처럼).
    const ax = {};
    series.forEach((s) => {
      const k = s.axis || 'l';
      const a = ax[k] || (ax[k] = { max: 0, pct: !!s.pct, any: false });
      points.forEach((p) => {
        const v = p[s.key];
        if (typeof v === 'number') { a.any = true; if (v > a.max) a.max = v; }
      });
      if (s.pct) a.pct = true;
    });
    Object.keys(ax).forEach((k) => { ax[k].top = niceMax(ax[k].max) || 1; });
    const yOf = (v, k) => PT + ih - (v / ax[k].top) * ih;

    // 가로 눈금선 + 좌우 축 숫자
    const TICKS = 4;
    let grid = '';
    for (let t = 0; t <= TICKS; t++) {
      const y = PT + ih - (ih * t) / TICKS;
      grid += `<line class="ch-grid" x1="${PL}" y1="${y.toFixed(1)}" x2="${PL + iw}" y2="${y.toFixed(1)}"/>`;
      if (ax.l && ax.l.any) grid += `<text class="ch-ylab ch-a-l" x="${PL - 6}" y="${(y + 3.5).toFixed(1)}" text-anchor="end">${esc(nfmt(ax.l.top * t / TICKS, ax.l.pct))}</text>`;
      if (ax.r && ax.r.any) grid += `<text class="ch-ylab ch-a-r" x="${PL + iw + 6}" y="${(y + 3.5).toFixed(1)}">${esc(nfmt(ax.r.top * t / TICKS, ax.r.pct))}</text>`;
    }

    // 계열 그리기 — 같은 축의 막대는 나란히 놓는다
    const bars = series.filter((s) => s.type === 'bar');
    let body = '';
    series.forEach((s, si) => {
      const k = s.axis || 'l', cls = 'ch-s' + si;
      const vals = points.map((p) => (typeof p[s.key] === 'number' ? p[s.key] : null));
      if (!vals.some((v) => v != null)) return;
      if (s.type === 'bar') {
        const bi = bars.indexOf(s), bn = bars.length || 1;
        const gw = step * 0.66, bw = gw / bn;
        body += vals.map((v, i) => {
          if (v == null) return '';
          const x = PL + i * step + (step - gw) / 2 + bi * bw;
          const y = yOf(v, k), h = Math.max(0.5, PT + ih - y);
          return `<rect class="ch-bar ${cls}" x="${x.toFixed(1)}" y="${y.toFixed(1)}" width="${Math.max(1, bw - 1).toFixed(1)}" height="${h.toFixed(1)}" rx="1">`
            + `<title>${esc(points[i].bucket)} · ${esc(s.label)} ${nfmt(v, s.pct)}</title></rect>`;
        }).join('');
      } else {
        // 값이 없는 구간에서 **선을 잇지 않는다** — 이으면 없는 추세를 그린 것이 된다
        let seg = [], segs = [];
        vals.forEach((v, i) => {
          if (v == null) { if (seg.length) segs.push(seg); seg = []; return; }
          seg.push(`${(PL + i * step + step / 2).toFixed(1)},${yOf(v, k).toFixed(1)}`);
        });
        if (seg.length) segs.push(seg);
        body += segs.map((sg) => sg.length === 1
          ? ''
          : `<polyline class="ch-line ${cls}" points="${sg.join(' ')}"/>`).join('');
        body += vals.map((v, i) => v == null ? '' :
          `<circle class="ch-dot ${cls}" cx="${(PL + i * step + step / 2).toFixed(1)}" cy="${yOf(v, k).toFixed(1)}" r="3">`
          + `<title>${esc(points[i].bucket)} · ${esc(s.label)} ${nfmt(v, s.pct)}</title></circle>`).join('');
      }
    });

    // x 축 — 칸이 좁으면 건너뛰며 적는다
    const every = Math.max(1, Math.ceil(n / Math.max(2, Math.floor(iw / 58))));
    let xlab = '';
    points.forEach((p, i) => {
      if (i % every && i !== n - 1) return;
      xlab += `<text class="ch-xlab" x="${(PL + i * step + step / 2).toFixed(1)}" y="${(PT + ih + 18).toFixed(1)}" text-anchor="middle">${esc(shortBucket(p.bucket))}</text>`;
    });
    const base = `<line class="ch-axisline" x1="${PL}" y1="${PT + ih}" x2="${PL + iw}" y2="${PT + ih}"/>`;
    const legend = series.map((s, si) => `<span class="ch-leg ch-s${si}">${s.type === 'bar' ? '▮' : '━'} ${esc(s.label)}`
      + `<span class="muted"> (${(s.axis || 'l') === 'l' ? '왼쪽' : '오른쪽'} 눈금)</span></span>`).join('');
    return `<div class="chart"><svg viewBox="0 0 ${W} ${H}" role="img" xmlns="${SVGNS}">${grid}${base}${body}${xlab}</svg>`
      + `<div class="ch-legend">${legend}</div></div>`;
  }

  let OPS_BUCKET = 'day';
  async function loadOps() {
    const days = parseFloat(($('#ops-days') && $('#ops-days').value) || 7);
    $('#ops-out').innerHTML = '<div class="muted small">집계 중…</div>';
    const d = await api('/api/opstats?days=' + days + '&bucket=' + encodeURIComponent(OPS_BUCKET));
    if (!d || d.error) { $('#ops-out').innerHTML = `<div class="bad">${esc((d && d.error) || '실패')}</div>`; return; }
    const H = d.help || {};
    const sec = (key, title, body) => `<details class="dbg-sec" open><summary><b>${esc(title)}</b> <span class="muted small">${esc(H[key] || '')}</span></summary>${body}</details>`;
    let h = '';
    // 추세를 맨 위에 둔다 — 한 시점의 p95 만으로는 "나아지나 나빠지나" 에 답할 수 없다.
    if (d.trend) {
      const tr = d.trend, pts = tr.points || [], dl = tr.delta_last || {};
      const BL = { day: '일간', week: '주간', month: '월간' };
      const arrow = (k, lower) => {
        if (dl[k] == null) return '';
        const good = lower ? dl[k] < 0 : dl[k] > 0;
        return `<span class="${dl[k] === 0 ? 'muted' : (good ? 'oktxt' : 'warntxt')}">${dl[k] > 0 ? '▲' : dl[k] < 0 ? '▼' : '–'}${fmt(Math.abs(dl[k]), 2)}</span>`;
      };
      h += sec('trend', '추세',
        '<div class="row fx-chips">' + ['day', 'week', 'month'].map((b) =>
          `<button class="chip ${OPS_BUCKET === b ? 'on' : ''}" data-bucket="${b}">${BL[b]}</button>`).join('')
        + `<span class="muted small">구간 ${pts.length}개 · 최근 ${fmt(tr.days, 0)}일 (묶음에 맞춰 자동)</span></div>`
        + (tr.note ? `<div class="muted small">${esc(tr.note)}</div>` : '')
        + '<div class="row">'
        + `<div class="stat"><b>${(pts[pts.length - 1] || {}).queries != null ? pts[pts.length - 1].queries : '-'}</b>최근 구간 질의 ${arrow('queries', false)}</div>`
        + `<div class="stat"><b>${fmt((pts[pts.length - 1] || {}).p95_ms, 0)}ms</b>p95 ${arrow('p95_ms', true)}</div>`
        + `<div class="stat"><b>${fmt((pts[pts.length - 1] || {}).tokens_per_query, 0)}</b>토큰/질의 ${arrow('tokens_per_query', true)}</div>`
        + `<div class="stat"><b>${fmt(100 * ((pts[pts.length - 1] || {}).insufficient_rate || 0), 1)}%</b>근거 부족 ${arrow('insufficient_rate', true)}</div>`
        + '</div>'
        + '<h4 class="ch-title">질의 수 · 빌드 횟수</h4>'
        + chart(pts, [{ key: 'queries', label: '질의', type: 'bar', axis: 'l' },
                      { key: 'builds', label: '빌드', type: 'line', axis: 'r' }])
        + '<h4 class="ch-title">지연 <small class="muted">낮을수록 좋다 · 같은 눈금</small></h4>'
        + chart(pts, [{ key: 'p50_ms', label: 'p50 ms', type: 'line', axis: 'l' },
                      { key: 'p95_ms', label: 'p95 ms', type: 'line', axis: 'l' }])
        + '<h4 class="ch-title">질의당 토큰 · 근거 부족률</h4>'
        + chart(pts, [{ key: 'tokens_per_query', label: '토큰/질의', type: 'bar', axis: 'l' },
                      { key: 'insufficient_rate', label: '근거 부족률', type: 'line', axis: 'r', pct: true }])
        + opsTable(['구간', '질의', 'p50 ms', 'p95 ms', '토큰/질의', '빌드', '근거 부족', '👍/👎'],
          pts.slice(-12).reverse().map((p) => [esc(p.bucket), p.queries, fmt(p.p50_ms, 0), fmt(p.p95_ms, 0),
            fmt(p.tokens_per_query, 0), p.builds, p.insufficient, (p.up || 0) + '/' + (p.down || 0)])));
    }
    if (d.build) {
      const b = d.build, last = b.last || {};
      h += sec('build', '빌드',
        `<div class="row"><div class="stat"><b>${b.n_builds || 0}</b>최근 ${days}일 빌드</div>`
        + (b.last_ms ? `<div class="stat"><b>${fmt(b.last_ms / 1000, 1)}s</b>마지막 소요</div>` : '')
        + (last.docs != null ? `<div class="stat"><b>${last.docs}</b>문서 (변경 ${last.changed})</div>` : '')
        + '</div>'
        + ((b.slowest_stages || []).length
          ? opsTable(['느린 단계', 'ms'], b.slowest_stages.map((s) => [`<code>${esc(s.stage)}</code>`, fmt(s.ms, 0)])) : '')
        + ((b.alerts || []).length ? `<div class="chk-warn">⚠ ${b.alerts.map((a) => esc(String(a.detail || a))).join(' · ')}</div>` : ''));
    }
    if (d.queries) {
      const q = d.queries;
      const mx = Math.max.apply(null, q.by_hour.concat([1]));
      h += sec('queries', '질의량',
        `<div class="row"><div class="stat"><b>${q.total}</b>건</div><div class="stat"><b>${q.per_day}</b>하루 평균</div>`
        + (q.busiest_hour != null ? `<div class="stat"><b>${String(q.busiest_hour).padStart(2, '0')}시</b>가장 몰림</div>` : '')
        + '</div>'
        + '<div class="dbg-w">' + q.by_hour.map((n, i) => `<div class="dbg-wrow" title="${i}시 ${n}건"><span class="muted small" style="width:26px">${String(i).padStart(2, '0')}</span><i style="width:${Math.round(100 * n / mx)}%"></i><b>${n || ''}</b></div>`).join('') + '</div>'
        + `<div class="muted small">창구: ${Object.keys(q.by_origin).map((k) => esc(k) + ' ' + q.by_origin[k]).join(' · ')}</div>`
        + (q.capped ? '<div class="chk-warn">⚠ 표본 상한에 걸렸습니다 — 기간을 줄이면 정확해집니다</div>' : ''));
    }
    if (d.latency) {
      const la = d.latency;
      h += sec('latency', '지연',
        `<div class="row"><div class="stat"><b>${fmt(la.p50_ms, 0)}</b>p50 ms</div><div class="stat"><b>${fmt(la.p95_ms, 0)}</b>p95 ms</div><div class="stat"><b>${fmt(la.max_ms, 0)}</b>최대 ms</div><div class="stat"><b>${la.n}</b>표본</div></div>`
        + opsTable(['가장 느린 질의', 'ms', ''], (la.slowest || []).map((s) => [esc(s.summary), fmt(s.ms, 0), `<a href="#" data-opsreq="${s.request_id}">#${s.request_id}</a>`])));
    }
    if (d.tokens) {
      const t = d.tokens;
      h += sec('tokens', '토큰',
        `<div class="row"><div class="stat"><b>${fmtK(t.total)}</b>총 토큰</div><div class="stat"><b>${fmt(t.per_query, 0)}</b>질의당</div><div class="stat"><b>${t.calls_per_query}</b>LLM 호출/질의</div></div>`
        + opsTable(['가장 무거운 질의', '토큰', ''], (t.heaviest || []).map((s) => [esc(s.summary), fmtK(s.tokens), `<a href="#" data-opsreq="${s.request_id}">#${s.request_id}</a>`])));
    }
    if (d.quality) {
      const qa = d.quality;
      h += sec('quality', '품질 신호',
        `<div class="row"><div class="stat" title="근거를 찾지 못해 답하지 못한 질의"><b>${fmt(100 * qa.insufficient_rate, 1)}%</b>근거 부족 (${qa.insufficient}/${qa.queries})</div>`
        + `<div class="stat"><b>${qa.feedback_positive}/${qa.feedback_negative}</b>👍/👎</div>`
        + `<div class="stat" title="Evolve 탭에서 승인·거절합니다"><b>${qa.proposals_pending}</b>대기 제안</div></div>`
        + (Object.keys(qa.forensics || {}).length ? `<div class="muted small">포렌식: ${Object.keys(qa.forensics).map((k) => esc(k) + ' ' + qa.forensics[k]).join(' · ')}</div>` : ''));
    }
    if (d.users) {
      h += sec('users', '사용자',
        opsTable(['사용자', '질의'], (d.users.top || []).map((x) => [esc(x.user), x.queries])));
    }
    if (d.storage) {
      const s = d.storage;
      h += sec('storage', '디스크',
        `<div class="row"><div class="stat"><b>${MB(s.db_bytes)}</b>DB</div><div class="stat"><b>${MB(s.logs_bytes)}</b>로그</div></div>`
        + opsTable(['data/ 폴더', '크기', '파일'], (s.data_dirs || []).map((x) => [`<code>${esc(x.dir)}</code>`, MB(x.bytes), x.files]))
        + (s.hints || []).map((x) => `<div class="chk-warn">⚠ ${esc(x)}</div>`).join(''));
    }
    if (d.embed) {
      const e = d.embed;
      h += sec('embed', '임베딩',
        `<div class="row"><div class="stat" title="캐시가 맞으면 다시 임베딩하지 않습니다"><b>${fmt(100 * e.cache_hit_rate, 1)}%</b>캐시 적중</div><div class="stat"><b>${e.failed}</b>실패</div></div>`
        + opsTable(['실행', '모델', 'done', '캐시', '초'], (e.runs || []).map((r) => [esc(String(r.run_id).slice(0, 10)), esc(String(r.model || '')), r.done, r.cache_hits, r.elapsed_s == null ? '-' : r.elapsed_s])));
    }
    $('#ops-out').innerHTML = h || '<div class="muted">집계할 것이 없습니다.</div>';
    $$('#ops-out [data-opsreq]').forEach((a) => a.onclick = (e) => {
      e.preventDefault();
      switchTab('requests');
      setTimeout(() => LW.openRequest && LW.openRequest(parseInt(a.dataset.opsreq, 10)), 250);
    });
    // 일/주/월 전환 — 누르면 그 묶음으로 다시 집계한다 (기간은 서버가 묶음에 맞춰 늘린다)
    $$('#ops-out [data-bucket]').forEach((b) => b.onclick = () => { OPS_BUCKET = b.dataset.bucket; loadOps(); });
  }
  if ($('#btn-ops')) $('#btn-ops').onclick = loadOps;

  // ---------------- 초기화 (관리자) ----------------
  // 지우는 명령이 **먼저 보여 주고 나중에 지우게** 한다. 누르면 미리보기가 뜨고, 거기서 한 번 더 눌러야 실행된다.
  // 실행은 서버가 파괴적 작업으로 분류하므로 확인 문구 + 비밀번호 모달이 뒤따른다 (2026-09-19).
  const RESET_OPTS = {
    data: [['clear_embed_cache', '임베딩 캐시도 지우기', '임베더를 바꿀 때만. 다음 빌드에서 전부 다시 임베딩합니다'],
           ['purge_wiki_notes', '사람이 쓴 위키 노트까지', '정정·보강 메모가 사라집니다'],
           ['no_snapshot', '스냅샷 만들지 않기', '되돌릴 수 없게 됩니다 — 권장하지 않습니다']],
    settings: [['include_security', 'security.json · docacl.json 도', '계정·권한이 초기화됩니다. 원격이면 다시 로그인하지 못할 수 있습니다'],
               ['include_env', '.env 도 삭제', 'API 키·PAT 가 사라집니다 (복구 불가)']],
    logs: [['include_proposals', '자가진화 제안도', '사람이 검토할 후보가 사라집니다'],
           ['include_sessions', '로그인 세션도', '모든 사용자가 다시 로그인해야 합니다']],
  };
  function resetOpts(scope) {
    const o = {};
    (RESET_OPTS[scope] || []).forEach(([k]) => { const el = $('#rs-opt-' + k); if (el && el.checked) o[k] = true; });
    if (o.no_snapshot) { o.snapshot = false; delete o.no_snapshot; }
    return o;
  }
  async function resetPreview(scope) {
    const o = resetOpts(scope);
    const qs = Object.keys(o).map((k) => k + '=' + (o[k] === false ? '0' : '1')).join('&');
    const pl = await api('/api/reset?scope=' + scope + (qs ? '&' + qs : ''));
    if (pl.error) { toast('실패: ' + pl.error); return; }
    const mb = (b) => (b / 1048576).toFixed(1) + ' MB';
    $('#sys-reset-out').innerHTML =
      `<div class="panel-intro"><b>${esc(scope)}</b> — ${esc(pl.description)}</div>`
      + `<div class="row"><div class="stat"><b>${pl.total_rows}</b>행</div><div class="stat"><b>${pl.total_files}</b>파일</div><div class="stat"><b>${mb(pl.total_bytes)}</b></div></div>`
      + '<div class="tbl-wrap"><table><tr><th>대상</th><th>양</th><th>설명</th></tr>'
      + pl.items.map((i) => `<tr class="${i.level === 'warn' ? 'chk-warn' : ''}"><td><code>${esc(i.target)}</code></td>`
        + `<td>${i.kind === 'table' ? i.count + '행' : i.count + '개 · ' + mb(i.bytes)}</td><td>${esc(i.detail)}</td></tr>`).join('')
      + '</table></div>'
      + '<div class="prop-checks">' + (pl.kept || []).map((k) => `<div class="chk-info">유지 · ${esc(k)}</div>`).join('') + '</div>'
      + '<div class="row">' + (RESET_OPTS[scope] || []).map(([k, label, why]) =>
        `<label title="${esc(why)}"><input type="checkbox" id="rs-opt-${esc(k)}"${resetOpts(scope)[k] || (k === 'no_snapshot' && pl.options.snapshot === false) ? ' checked' : ''}> ${esc(label)}</label>`).join(' ')
      + `<button class="secondary" data-reset-refresh="${esc(scope)}">옵션 반영해 다시 보기</button>`
      + `<button class="danger" data-reset-go="${esc(scope)}">지금 초기화 (되돌릴 수 없습니다)</button></div>`;
    $$('#sys-reset-out [data-reset-refresh]').forEach((b) => b.onclick = () => resetPreview(b.dataset.resetRefresh));
    $$('#sys-reset-out [data-reset-go]').forEach((b) => b.onclick = async () => {
      b.disabled = true; b.textContent = '초기화 중…';
      const r = await api('/api/reset', Object.assign({ scope: b.dataset.resetGo }, resetOpts(b.dataset.resetGo)));
      b.disabled = false; b.textContent = '지금 초기화 (되돌릴 수 없습니다)';
      if (r && r.cancelled) { toast('취소됨'); return; }
      if (r && r.error) { toast('실패: ' + r.error); return; }
      $('#sys-reset-out').innerHTML = `<div class="panel-intro"><b>초기화 완료</b> — ${esc(b.dataset.resetGo)} (${fmt(r.ms, 0)}ms)</div>`
        + '<div class="prop-checks">' + (r.next || []).map((x, i) => `<div class="chk-info">${i + 1}. ${esc(x)}</div>`).join('') + '</div>'
        + `<details><summary>지운 내역</summary><pre class="log">${esc(JSON.stringify(r.done, null, 1))}</pre></details>`;
      toast('초기화 완료: ' + b.dataset.resetGo);
      loadSystem(); loadStatus();
    });
  }
  $$('#sys-reset button').forEach((b) => b.onclick = () => resetPreview(b.dataset.reset));
  loaders.system = loadSystem;

  // ---------------- ACTIVITY (진행 중 작업 — viewer 도 조회 가능) ----------------
  function actRow(r, canCancel) {
    const pct = r.pct == null ? '' : `<div class="progress mini"><i style="width:${Math.max(0, Math.min(100, r.pct))}%"></i><span>${fmt(r.pct, 0)}%${r.eta_s ? ' · ≈' + LW.fmtS(r.eta_s) : ''}</span></div>`;
    const llm = r.llm && r.llm.active ? `<span class="pill warn">LLM ${esc(r.llm.provider || '')}/${esc(r.llm.model || '')} ${LW.fmtS(r.llm.elapsed_s)}</span>` : '';
    const q = r.queue ? `<span class="pill">대기열 ${r.queue.position}</span>` : '';
    const btn = canCancel && r.status === 'running' && !r.cancel_requested ? `<button class="mini danger" data-cancel="${esc(r.token)}">■ 중지</button>` : (r.cancel_requested ? '<span class="pill warn">중지 중</span>' : '');
    return `<tr data-token="${esc(r.token || '')}"><td class="mono small">${esc(r.token || '')}</td><td>${esc(r.kind || '')}${r.external ? ' <span class="pill">외부</span>' : ''}${r.weight === 'exclusive' ? ' <span class="pill bad">배타</span>' : r.weight === 'soft' ? ' <span class="pill">쓰기</span>' : ''}</td>` +
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
    return `<div class="act-item ${esc(kind)} k-${esc(r.kind || 'etc')}${r.mine ? ' mine' : ''}" data-token="${esc(r.token || '')}" title="${esc(r.label || '')}">
      ${pos}<span class="act-ico">${KIND_ICON[r.kind] || '•'}</span>
      <span class="act-main"><span class="act-label">${esc((r.label || r.kind || '-').slice(0, 90))}</span>
        <span class="act-sub">${esc(r.kind || '')}${r.user ? ' · ' + esc(r.user) : ''}${r.origin ? ' · ' + esc(r.origin) : ''}${where ? ' · ' + esc(where) : ''}</span></span>
      <span class="act-tags">${tags}</span><span class="act-t">${LW.fmtDur(r.elapsed_s)}</span>${bar}${btn}</div>`;
  }
  function recentCard(r) {
    const cls = r.status === 'done' ? 'ok' : r.status === 'cancelled' ? 'warn' : 'bad';
    return `<div class="act-item done ${cls}" data-token="${esc(r.token || '')}" title="${esc(r.error || r.label || '')}">
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
    // 줄(카드·표)을 누르면 상세를 연다. 각 줄이 자기 token 을 data 속성으로 들고 있으므로
    // 본문 텍스트를 뒤져 짝을 찾을 필요가 없다 (최근 완료 카드는 token 을 표시하지 않는다).
    const byToken = {};
    [].concat(run, qd, ext, rec).forEach((r) => { if (r.token) byToken[r.token] = r; });
    $$('#tab-activity [data-token]').forEach((el) => {
      const rec2 = byToken[el.dataset.token];
      if (!rec2) return;
      el.classList.add('clickable');
      el.onclick = (e) => { if (e.target.closest('[data-cancel], .act-x')) return; openActDetail(rec2); };
    });
    if (ACT_OPEN && byToken[ACT_OPEN.token]) renderActDetail(byToken[ACT_OPEN.token]);
  }

  // ---------------- 진행 중 작업 상세 (실시간 진행 + 그때 남긴 기록) ----------------
  // 실행 중이면 progress.py 의 스냅샷을 1.5초마다 받아 단계·로그를 보여 주고,
  // 끝난 작업이면 남아 있는 기록과 **저장해 둔 결과(요청 프로파일)** 로 가는 길을 준다.
  let ACT_OPEN = null, ACT_TIMER2 = null, ACT_SLOW = null;
  function closeActDetail() {
    ACT_OPEN = null;
    if (ACT_TIMER2) { clearInterval(ACT_TIMER2); ACT_TIMER2 = null; }
    if (ACT_SLOW) { clearTimeout(ACT_SLOW); ACT_SLOW = null; }
    const el = $('#act-detail'); if (el) { el.classList.add('hidden'); el.innerHTML = ''; }
  }
  function openActDetail(r) {
    if (ACT_OPEN && ACT_OPEN.token === r.token) { closeActDetail(); return; }   // 같은 줄을 다시 누르면 닫기
    ACT_OPEN = { token: r.token };
    renderActDetail(r);
    pollActDetail();
    if (ACT_TIMER2) clearInterval(ACT_TIMER2);
    ACT_TIMER2 = setInterval(pollActDetail, 1500);
  }
  async function pollActDetail() {
    if (!ACT_OPEN) return;
    if (!LW.tabVisible('activity')) return;
    const j = await api('/api/progress?token=' + encodeURIComponent(ACT_OPEN.token));
    if (!ACT_OPEN) return;
    ACT_OPEN.live = (j && j.status) ? j : null;
    renderActDetail(ACT_OPEN.row || {});
    // 더 볼 게 없으면 **폴링을 멈춘다**. 예전에는 진행 기록이 아예 없는 경우(live === null, 끝나서 서버가
    // 정리한 작업)에 이 조건이 false 라 타이머가 계속 돌았고, 1.5초마다 화면을 다시 그리는 바람에
    // '전체 보기' 로 펼친 답변이 곧바로 도로 접혔다.
    if ((!ACT_OPEN.live || ACT_OPEN.live.status !== 'running') && ACT_TIMER2) { clearInterval(ACT_TIMER2); ACT_TIMER2 = null; }
  }
  // 끝난 작업의 **저장해 둔 결과**를 가져온다. 목록은 훑어보는 화면이므로 여기서는 요약만 보여 주고,
  // 자세히 볼 사람은 Ask 화면 복원이나 요청 프로파일로 한 번에 갈 수 있게 한다.
  async function loadActResult(rid) {
    if (!ACT_OPEN || ACT_OPEN.reqLoading || (ACT_OPEN.req && ACT_OPEN.req.id === rid)) return;
    const mine = ACT_OPEN;                 // 응답이 늦게 와도 **그 사이 다른 줄을 눌렀으면** 버린다
    mine.reqLoading = true;
    mine.loadStarted = Date.now();
    // 느린 질의가 여러 개 떠 있으면 브라우저 연결(호스트당 6개)이 붐벼 이 요청이 늦게 출발한다.
    // 8초가 지나도 안 오면 "멈춘 것" 이 아니라 "밀려 있는 것" 이라고 화면에 말해 준다.
    if (ACT_SLOW) clearTimeout(ACT_SLOW);
    ACT_SLOW = setTimeout(() => { if (ACT_OPEN === mine && mine.reqLoading) { mine.slow = true; renderActDetail(mine.row || {}); } }, 8000);
    // brief=1 → 단계별 trace 를 뺀 요약 (67KB → 약 19KB). 이 패널에는 답변 요약만 필요하다.
    const r = await api('/api/request?id=' + rid + '&brief=1');
    if (ACT_SLOW) { clearTimeout(ACT_SLOW); ACT_SLOW = null; }
    if (ACT_OPEN !== mine) return;         // 다른 줄로 옮겨 갔다 — 이 응답은 그 줄의 것이 아니다
    mine.reqLoading = false;
    mine.slow = false;
    mine.req = (r && r.id) ? r : { id: rid, missing: true, why: (r && r.error) || "" };
    renderActDetail(mine.row || {});
  }
  function savedBlock(req) {
    if (!req) {
      const waited = ACT_OPEN && ACT_OPEN.loadStarted ? Math.round((Date.now() - ACT_OPEN.loadStarted) / 1000) : 0;
      if (ACT_OPEN && ACT_OPEN.slow) {
        return '<div class="muted small">저장된 결과를 기다리는 중입니다 (' + waited + '초). ' +
          '지금 <b>오래 걸리는 질의가 실행 중</b>이면 브라우저가 연결을 재사용할 때까지 이 조회가 밀립니다 — ' +
          '서버가 멈춘 것이 아니며, 앞선 질의가 끝나면 채워집니다.</div>';
      }
      return '<div class="muted small">저장된 결과를 불러오는 중…</div>';
    }
    if (req.missing) return '<div class="muted small">저장된 결과를 찾지 못했습니다' +
      (req.why ? ' (' + esc(String(req.why).slice(0, 80)) + ')' : '') +
      ' — 보존 기간이 지나 정리되었을 수 있습니다 (<code>keep_requests</code> · <code>requests_keep_days</code>).</div>';
    const res = req.result || {};
    const ev = res.evidence || {};
    const ans = String(res.answer || '');
    if (!ans) return `<div class="muted small">이 작업에는 저장된 답변이 없습니다 (${esc(req.kind || '')} 작업). 아래 '요청 프로파일' 에서 단계별 기록을 볼 수 있습니다.</div>`;
    const stat = `<div class="statrow compact"><div class="stat"><b>${fmt(req.ms, 0)}</b>ms</div>` +
      `<div class="stat"><b>${fmtK((req.input_tokens || 0) + (req.output_tokens || 0))}</b>토큰</div>` +
      `<div class="stat"><b>${(res.hits_brief || []).length || (res.cited || []).length}</b>근거</div>` +
      (ev.verdict ? `<div class="stat"><b>${esc(ev.verdict)}</b>근거 판정</div>` : '') +
      (res.groundedness != null ? `<div class="stat"><b>${fmt(res.groundedness, 2)}</b>groundedness</div>` : '') +
      (res.cached ? '<div class="stat"><b>캐시</b>응답</div>' : '') + '</div>';
    // 한 번 펼친 답변은 계속 펼쳐 둔다. 실행 중인 작업은 화면을 주기적으로 다시 그리므로,
    // 펼침 상태를 기억하지 않으면 새로 그릴 때마다 도로 접힌다.
    const long = ans.length > 1200 && !(ACT_OPEN && ACT_OPEN.expanded);
    return stat + `<div class="act-answer${long ? ' clip' : ''}" id="act-answer">${esc(ans)}</div>` +
      (long ? '<button class="mini secondary" id="btn-act-more">전체 보기</button>' : '');
  }
  function renderActDetail(r) {
    const el = $('#act-detail'); if (!el || !ACT_OPEN) return;
    ACT_OPEN.row = Object.assign({}, ACT_OPEN.row || {}, r || {});
    const row = ACT_OPEN.row, live = ACT_OPEN.live;
    const rid = row.request_id;
    const done = !live || live.status !== 'running';
    const head = `<div class="act-detail-head"><b>${esc(row.label || row.kind || row.token || '')}</b>
      <span class="pill">${esc(row.kind || '')}</span>${row.user ? '<span class="muted small">' + esc(row.user) + '</span>' : ''}
      <span class="muted small mono">${esc(row.token || '')}</span>
      <span class="grow"></span>
      ${rid ? `<button class="mini" id="btn-act-ask" title="Ask 화면에 그때 그 결과를 그대로 복원 (다시 실행하지 않습니다)">↩ Ask 화면에서 열기</button>` : ''}
      ${rid ? `<button class="mini secondary" id="btn-act-profile">📄 요청 프로파일 #${esc(String(rid))}</button>` : ''}
      ${rid && row.kind === 'query' ? '<button class="mini secondary" id="btn-act-rerun" title="저장된 중간 결과로 특정 단계부터 다시 실행">⟲ 다시 실행</button>' : ''}
      ${!done && row.token ? `<button class="mini danger" data-cancel="${esc(row.token)}">■ 중지</button>` : ''}
      <button class="mini secondary" id="btn-act-close">닫기</button></div>`;
    let body = '';
    // 끝난 작업이면 **저장해 둔 결과**를 먼저 보여 준다 — 목록에서 누른 이유는 대개 "그래서 뭐라고 답했지?" 다.
    if (done && rid) {
      body += savedBlock(ACT_OPEN.req && ACT_OPEN.req.id === rid ? ACT_OPEN.req : null);
      if (!ACT_OPEN.req || ACT_OPEN.req.id !== rid) loadActResult(rid);
    }
    if (live && live.status) {
      const path = (live.path_labels || []).join(' › ');
      body += `<div class="kv small">상태 <b>${esc(live.status)}</b>${live.stage_label ? ' · 단계 <b>' + esc(live.stage_label) + '</b>' : ''}` +
        `${path ? ' <span class="muted">(' + esc(path) + ')</span>' : ''}${live.pct != null ? ' · ' + fmt(live.pct, 0) + '%' : ''}` +
        `${live.elapsed_s != null ? ' · 경과 ' + LW.fmtDur(live.elapsed_s) : ''}` +
        `${live.llm && live.llm.active ? ' · <span class="pill warn">LLM ' + esc(live.llm.model || '') + ' ' + LW.fmtS(live.llm.elapsed_s) + '</span>' : ''}` +
        `${live.queue ? ' · <span class="pill">대기열 ' + live.queue.position + '</span>' : ''}</div>`;
      const log = live.log || [];
      body += log.length
        ? `<details ${done ? '' : 'open'}><summary class="muted small">진행 기록 ${log.length}줄</summary><pre class="pre small act-log">${esc(log.slice(-200).join('\n'))}</pre></details>`
        : '<div class="muted small">아직 남은 진행 기록이 없습니다.</div>';
    } else if (!(done && rid)) {
      body += `<div class="muted small">이 작업의 실시간 진행 기록은 남아 있지 않습니다${done ? ' (이미 끝났고 서버가 정리했습니다)' : ''}.` +
        (rid ? ' 아래 버튼으로 <b>그때 저장한 결과와 단계별 프로파일</b>을 볼 수 있습니다.' : '') + '</div>';
    }
    if (row.error) body += `<div class="errtxt small">${esc(String(row.error))}</div>`;
    el.innerHTML = head + body;
    el.classList.remove('hidden');
    $('#btn-act-close').onclick = closeActDetail;
    const pb = $('#btn-act-profile');
    if (pb) pb.onclick = () => { switchTab('requests'); setTimeout(() => openRequest(rid), 150); };
    const mb = $('#btn-act-more');
    if (mb) mb.onclick = (e) => {
      e.stopPropagation();
      if (ACT_OPEN) ACT_OPEN.expanded = true;      // 다시 그려도 펼친 채로
      const a = $('#act-answer'); if (a) a.classList.remove('clip');
      mb.remove();
    };
    const ab = $('#btn-act-ask');
    // 저장해 둔 결과를 Ask 화면에 그대로 복원한다 — **다시 실행하지 않는다**.
    // '내 지난 요청' 과 **같은 함수**를 쓴다: 저장된 결과에는 근거 전문(hits)이 없어 그대로 그리면 깨진다.
    if (ab) ab.onclick = () => { LW.switchGroup('ask'); switchTab('query'); setTimeout(() => LW.openPastRequest(rid), 120); };
    const rb = $('#btn-act-rerun');
    if (rb) rb.onclick = () => LW.openRerun(rid, 'answer_llm', 'answer_llm', (j) => {
      LW.switchGroup('ask'); switchTab('query');
      setTimeout(() => LW.renderResult(j.result, j.trace, (j.result || {}).query || ''), 120);
    });
    $$('#act-detail [data-cancel]').forEach((b) => b.onclick = async (e) => {
      e.stopPropagation(); b.disabled = true; await LW.cancelToken(b.dataset.cancel, 'activity'); setTimeout(LW.refreshActivity, 600);
    });
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
    'concurrency.write_wait_timeout_s': '빌드(쓰기)가 진행 중인 질의가 끝나길 기다리는 최대 시간. 기본 172800초(48시간) — 짧으면 긴 빌드가 시작도 못 하고 503.',
    'concurrency.read_wait_timeout_s': '읽기가 배타 작업(전체 빌드)을 기다리는 최대 시간. 이 값은 빌드가 아니라 질의의 수명이라 일부러 짧다(900). 빌드 중 질의를 받으려면 reads_during_build.',
    'rate_limit.per_user_per_min': '사용자별 분당 요청 수 (0 = 무제한).',
    'rate_limit.per_ip_per_min': 'IP 별 분당 요청 수.',
    'rate_limit.query_per_user_per_min': '사용자별 분당 질의 수 (LLM 비용 보호).',
    'timeouts.query_s': '질의 1건의 시간 제한(초). 넘으면 자동 중지. 0 = 없음.',
    'timeouts.job_s': '빌드/평가/스냅샷 등 백그라운드 작업의 시간 제한(초). 기본 172800(48시간). Web 콘솔의 build 도 cli_s 가 아니라 이 값을 따른다. 0 = 없음.',
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
  // 사용자 칸: id 는 짧게, 나머지 상세(역할·창구·로그인 방법·IP·에이전트)는 툴팁으로.
  // 30명이 쓰는 서버에서 "이 질문 누가 했지" 와 "저 사람 질문이 왜 자꾸 실패하지" 를 이 표에서 끝내기 위한 것이다.
  function actorCell(x, me) {
    const who = x.user || '';
    if (!who) return '<span class="muted small" title="사용자 정보가 없는 예전 행이거나 로그인 없이 실행된 질의입니다">—</span>';
    const tip = ['사용자: ' + who, x.role ? '역할: ' + x.role : '', x.origin ? '창구: ' + x.origin : '',
      x.via ? '로그인: ' + x.via : '', x.ip ? 'IP: ' + x.ip : '', x.agent ? '에이전트: ' + x.agent : '',
      x.request_id ? '요청 #' + x.request_id : '', '클릭: 이 사용자만 보기'].filter(Boolean).join('\n');
    return `<span class="qlog-who${who === me ? ' me' : ''}" data-quser="${esc(who)}" title="${esc(tip)}">${esc(who)}` +
      (x.role ? `<span class="pill">${esc(x.role)}</span>` : '') +
      (x.origin && x.origin !== 'web' ? `<span class="pill">${esc(x.origin)}</span>` : '') + '</span>';
  }
  function qlogParams() {
    const p = ['limit=' + (parseInt(($('#qlog-limit') || {}).value, 10) || 60)];
    const mine = $('#qlog-mine') && $('#qlog-mine').checked;
    const u = mine ? ((STATE.auth && STATE.auth.user && STATE.auth.user.name) || '') : (($('#qlog-user') || {}).value || '').trim();
    if (u) p.push('user=' + encodeURIComponent(u));
    const o = (($('#qlog-origin') || {}).value || '').trim(); if (o) p.push('origin=' + encodeURIComponent(o));
    const q = (($('#qlog-q') || {}).value || '').trim(); if (q) p.push('q=' + encodeURIComponent(q));
    return p.join('&');
  }
  async function loadQLog() {
    const j = await api('/api/queries?' + qlogParams());
    // 예전 형식(배열)과 새 형식({rows, me, admin, show_user}) 을 모두 받는다
    const q = Array.isArray(j) ? j : ((j && j.rows) || []);
    const me = (j && j.me) || '';
    if ($('#qlog-msg')) {
      $('#qlog-msg').textContent = q.length + '건' + (j && j.show_user === false ? ' · 사용자 id 비공개 (server.json monitor.show_user_to_viewer)' : '')
        + (j && j.admin === false ? ' · IP·에이전트는 admin 만' : '');
    }
    $('#logs').innerHTML = '<table><tr><th>id</th><th>time</th><th title="이 질의를 낸 사용자 — 마우스를 올리면 역할·창구·IP">사용자</th><th>query</th><th>fb</th><th>판정</th><th>g</th><th>answer</th><th></th></tr>' + q.map((x) => { const sc = JSON.parse(x.scores || '{}'); return `<tr><td>${x.id}</td><td>${ts(x.ts)}</td><td>${actorCell(x, me)}</td><td>${esc(x.query)}</td><td>${x.feedback == null ? '' : x.feedback > 0 ? '👍' : '👎'}</td><td class="small">${esc(sc.verdict || '')}</td><td class="num">${sc.groundedness == null ? '' : fmt(sc.groundedness, 2)}</td><td class="muted small">${esc((x.answer || '').slice(0, 80))}</td><td><button class="secondary mini" data-tr="${x.id}">trace</button>${x.request_id ? `<button class="secondary mini" data-qreq="${x.request_id}" title="요청 프로파일에서 이 실행의 전체 trace">#${x.request_id}</button>` : ''}</td></tr>`; }).join('') + '</table>';
    // 사용자 이름을 누르면 그 사람 질의만
    $$('#logs [data-quser]').forEach((s) => s.onclick = () => {
      if ($('#qlog-mine')) $('#qlog-mine').checked = false;
      if ($('#qlog-user')) $('#qlog-user').value = s.dataset.quser;
      loadQLog();
    });
    $$('#logs [data-qreq]').forEach((b) => b.onclick = () => {
      LW.switchTab('requests');
      setTimeout(() => { if (LW.openRequest) LW.openRequest(parseInt(b.dataset.qreq, 10)); }, 250);
    });
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
  ['#qlog-user', '#qlog-q'].forEach((sel) => { const e = $(sel); if (e) e.onkeydown = (ev) => { if (ev.key === 'Enter') loadQLog(); }; });
  ['#qlog-mine', '#qlog-origin', '#qlog-limit'].forEach((sel) => { const e = $(sel); if (e) e.onchange = loadQLog; });
  // 사용자별 집계 (admin 전용) — 누가 얼마나 묻고 있고, 👎 가 몰리는 사람이 있는지.
  const btnQU = $('#btn-qlog-users');
  if (btnQU) btnQU.onclick = async () => {
    const box = $('#qlog-users'); if (!box) return;
    if (box.innerHTML) { box.innerHTML = ''; return; }
    const j = await api('/api/query_users?limit=50');
    if (!j || j.error) { box.innerHTML = `<div class="muted small">${esc((j && (j.detail || j.error)) || '집계를 읽지 못했습니다')}</div>`; return; }
    box.innerHTML = '<table><tr><th>사용자</th><th>질의 수</th><th>👍</th><th>👎</th><th>마지막</th><th></th></tr>' +
      j.map((r) => `<tr><td>${esc(r.user)}</td><td class="num">${r.n}</td><td class="num">${r.up || 0}</td><td class="num">${r.down || 0}</td><td class="small">${ts(r.last_ts)}</td><td><button class="secondary mini" data-quonly="${esc(r.user)}">이 사용자만</button></td></tr>`).join('') + '</table>';
    $$('#qlog-users [data-quonly]').forEach((b) => b.onclick = () => {
      if ($('#qlog-mine')) $('#qlog-mine').checked = false;
      if ($('#qlog-user')) $('#qlog-user').value = b.dataset.quonly;
      loadQLog();
    });
  };
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

  // ---------------- 요청 원장 (모든 요청 통합) ----------------
  // 진행 중 작업 · 요청 프로파일 · 질의 로그 · 로그 는 같은 사건을 **원천별로** 쪼개 놓은 화면이다.
  // 사람이 던지는 질문은 "이 요청 왜 이래?" 하나뿐이라, 여기서 한 줄로 합쳐 보여 주고
  // 한 건을 누르면 네 원천을 한 패널에 모은다. 거절·시간초과·중단은 **여기에만** 있다.
  const LED = { rows: [], open: null, timer: null, since: 3600, view: 'all', canAll: false, kinds: [] };
  const LED_LABEL = { done: '완료', running: '실행 중', queued: '대기', error: '오류', rejected: '거절', timeout: '시간초과', cancelled: '취소', unknown: '중단' };
  const LED_CLASS = { done: 'ok', running: 'run', queued: 'warn', error: 'bad', rejected: 'bad', timeout: 'bad', cancelled: 'warn', unknown: 'bad' };

  function ledQuery(extra) {
    const p = new URLSearchParams();
    p.set('view', LED.view);
    p.set('limit', $('#led-limit').value || 100);
    if (LED.range) { p.set('since', String(Math.floor(LED.range[0]))); p.set('until', String(Math.ceil(LED.range[1]))); }
    else if (LED.since) p.set('since', String(Math.floor(Date.now() / 1000 - LED.since)));
    const k = $('#led-kind').value, o = $('#led-origin').value, q = $('#led-q').value.trim(), m = $('#led-minms').value;
    const lq = ($('#led-logq') || {}).value ? $('#led-logq').value.trim() : '';
    if (k) p.set('kind', k);
    if (o) p.set('origin', o);
    if (q) p.set('q', q);
    if (lq) p.set('log_q', lq);
    if (m && +m > 0) p.set('min_ms', m);
    Object.entries(extra || {}).forEach(([a, b]) => p.set(a, b));
    return p.toString();
  }

  async function loadLedger() {
    const j = await api('/api/ledger?' + ledQuery());
    if (!j || j.error) { $('#led-table').innerHTML = `<div class="banner warn">${esc((j || {}).error || '원장을 읽지 못했습니다')}</div>`; return; }
    LED.rows = j.rows || []; LED.canAll = !!j.can_all;
    // 갱신 주기는 **서버가 정한다** (server.json ledger.refresh_ms). 화면은 그 값으로 시작하고
    // 사용자가 드롭다운으로 더 길게/짧게 바꿀 수 있다 — 접속자가 많으면 폴링이 그만큼 늘기 때문이다.
    if (!LED.refreshInit) {
      LED.refreshInit = true;
      LED.refreshMs = j.refresh_ms != null ? j.refresh_ms : 5000;
      const sel = $('#led-every');
      if (sel) {
        const choices = (j.refresh_choices_ms && j.refresh_choices_ms.length) ? j.refresh_choices_ms : [2000, 5000, 10000, 30000, 0];
        if (choices.indexOf(LED.refreshMs) < 0) choices.unshift(LED.refreshMs);
        sel.innerHTML = choices.map((ms) => `<option value="${ms}"${ms === LED.refreshMs ? ' selected' : ''}>${ms ? (ms / 1000) + '초' : '끔'}</option>`).join('');
      }
      ledAuto();
    }
    renderStrip(j); renderHist(j.histogram || [], j.hist_keys); renderLedTable(j);
  }

  function renderStrip(j) {
    const s = j.summary || {}, L = s.ledger || {}, st = L.by_status || {}, lim = s.limits || {};
    const cls = (s.classes || []).filter((c) => c.max_parallel_set);
    const bad = (st.rejected || 0) + (st.error || 0) + (st.timeout || 0) + (st.unknown || 0);
    $('#led-strip').innerHTML =
      `<b>실행 ${s.running || 0}</b> · 대기 ${s.queued || 0} · 슬롯 상한 ${lim.max_parallel_reads || '-'} · 대기열 상한 ${lim.queue_max || '-'}` +
      ((s.lock || {}).writer ? ` · <span class="pill bad">쓰기 락 ${esc((s.lock || {}).writer_label || s.lock.writer)}</span>` : ' · 쓰기 락 없음') +
      `<br>최근 ${fmt(L.days * 24, 0)}시간: 총 ${L.total || 0}건 · ` +
      Object.keys(LED_LABEL).filter((k) => st[k]).map((k) => `<a href="#" data-led-st="${k}" class="pill ${LED_CLASS[k]}">${LED_LABEL[k]} ${st[k]}</a>`).join(' ') +
      (bad ? '' : ' <span class="muted">(문제 없음)</span>') +
      ` · writer 기록 ${(L.writer || {}).written || 0}` +
      ((L.writer || {}).dropped ? ` <span class="pill bad">드롭 ${L.writer.dropped}</span> <span class="muted small">ledger.queue_max 를 올리세요</span>` : '') +
      // 깨진 줄 = 여러 프로세스(서버·CLI·MCP stdio)의 쓰기가 한 줄 안에서 섞인 것. 그 줄의 요청은 읽을 때 버려진다.
      ((L.writer || {}).corrupt ? ` <span class="pill bad">깨진 줄 ${L.writer.corrupt}</span> <span class="muted small">원장 폴더가 네트워크 드라이브인지 확인하세요</span>` : '') +
      ` · ${fmt(L.mb, 1)}MB` +
      (cls.length ? `<br>종류별 한도: ${cls.map((c) => `${esc(c.kind)} ${c.max_parallel}/${lim.max_parallel_reads}${c.reserved_for_others ? ` <span class="muted">(다른 종류에 ${c.reserved_for_others} 예약)</span>` : ''}`).join(' · ')}` : '');
    $$('#led-strip [data-led-st]').forEach((a) => a.onclick = (e) => {
      e.preventDefault(); LED.view = 'all'; syncViewSeg();
      $('#led-q').value = ''; loadLedgerStatus(a.dataset.ledSt);
    });
    if (!LED.kinds.length) {
      LED.kinds = Object.keys(L.by_kind || {}).sort();
      $('#led-kind').innerHTML = '<option value="">all</option>' + LED.kinds.map((k) => `<option>${esc(k)}</option>`).join('');
    }
  }

  async function loadLedgerStatus(st) {
    const j = await api('/api/ledger?' + ledQuery({ status: st }));
    LED.rows = j.rows || []; renderLedTable(j, `상태 = ${LED_LABEL[st] || st}`);
  }

  // 한 칸에 여러 상태가 섞이는 것이 정상이므로 **누적 막대**로 그린다.
  // (예전에는 칸마다 색 하나만 골라서, 같은 1분 안의 완료 50건과 거절 1건이 전부 빨강으로 보였다.)
  const HIST_LABEL = { done: '완료', running: '진행/대기', slow: '느림', cancelled: '취소', timeout: '시간초과', rejected: '거절', error: '오류', unknown: '중단' };

  function renderHist(h, keys) {
    const box = $('#led-hist');
    if (!h || !h.length) { box.innerHTML = ''; return; }
    const K = (keys && keys.length ? keys : Object.keys(HIST_LABEL));
    const max = Math.max(1, ...h.map((b) => b.n));
    const width = h.length > 1 ? (h[1].t - h[0].t) : 60;
    box.innerHTML = h.map((b) => {
      const tall = Math.max(b.n ? 8 : 1, Math.round((b.n / max) * 100));
      const segs = K.filter((k) => b[k]).map((k) =>
        `<u class="h-${k}" style="flex:${b[k]} 0 0"></u>`).join('');
      const tip = `${dt(b.t)} (~${fmt(width, 0)}초) · 총 ${b.n}건` +
        K.filter((k) => b[k]).map((k) => ` · ${HIST_LABEL[k] || k} ${b[k]}`).join('');
      return `<i style="height:${tall}%" title="${esc(tip)}" data-t="${b.t}" data-w="${width}">${segs}</i>`;
    }).join('');
    // 범례 — 어떤 색이 무엇인지 (누적이라 범례가 없으면 읽을 수 없다)
    const seen = K.filter((k) => h.some((b) => b[k]));
    $('#led-legend').innerHTML = seen.length
      ? '<span class="muted small">막대:</span> ' + seen.map((k) => `<span class="h-key"><u class="h-${k}"></u>${HIST_LABEL[k] || k}</span>`).join(' ')
      : '';
    // 막대를 누르면 그 구간만 본다
    $$('#led-hist i').forEach((el) => el.onclick = () => {
      const t = +el.dataset.t, w = +el.dataset.w;
      LED.range = [t, t + w];
      loadLedger();
    });
  }

  function renderLedTable(j, note) {
    const rows = LED.rows;
    $('#led-chips').innerHTML = `<span class="muted small">${j.total || rows.length}건 중 ${rows.length}건${note ? ' · ' + esc(note) : ''}` +
      (j.scope === 'mine' ? ' · <b>내 요청만</b> (전체를 보려면 작업 <code>requests all</code> 권한이 필요합니다)' : '') + '</span>';
    if (!rows.length) { $('#led-table').innerHTML = '<div class="muted">해당하는 요청이 없습니다.</div>'; return; }
    $('#led-table').innerHTML = '<table class="req led"><tr><th>상태</th><th>시각</th><th>종류/창구</th><th>사용자</th><th>라벨</th><th>대기</th><th>소요</th><th>단계</th><th>결과</th><th></th></tr>' +
      rows.map((r) => {
        const badge = `<span class="pill ${LED_CLASS[r.status] || ''}">${LED_LABEL[r.status] || esc(r.status || '?')}</span>`;
        const res = r.http ? `${r.http}${r.code ? ' ' + esc(r.code) : ''}` : (r.code ? esc(r.code) : '');
        const links = (r.request_id ? `<a class="mini" href="#" data-led-req="${r.request_id}" title="요청 프로파일">📄</a>` : '')
          + (r.run_id ? `<a class="mini" href="#" data-led-run="${esc(r.run_id)}" title="로그">📜</a>` : '');
        const live = r.status === 'running' || r.status === 'queued';
        // 실행 중이면 '소요' 가 아니라 **경과**다. ms 로 찍으면 93700 같은 숫자가 되어 읽기 어렵다.
        const took = live
          ? `<span class="muted">${LW.fmtS(r.elapsed_s || 0)} 경과</span>`
          : (r.ms != null ? fmt(r.ms, 0) + '<span class="muted small">ms</span>' : '');
        const stage = live
          ? (esc(r.stage || (r.status === 'queued' ? '대기열' : '시작 중…'))
             + (r.pct != null ? ` <span class="muted">${fmt(r.pct, 0)}%</span>` : '')
             + (r.llm ? ` <span class="muted">· LLM ${esc(r.llm.model || '')} ${fmt(r.llm.elapsed_s, 0)}s</span>` : '')
             + (r.queue_pos ? ` <span class="muted">· 대기 ${r.queue_pos}번째</span>` : ''))
          : esc(r.stage || '');
        return `<tr data-led-tok="${esc(r.token)}" class="${LED_CLASS[r.status] === 'bad' ? 'has-err' : ''}${r.sub ? ' is-sub' : ''}">` +
          `<td>${badge}</td><td class="small">${ts(r.opened)}</td><td class="small">${esc(r.kind || '')}<span class="muted"> / ${esc(r.origin || '')}</span></td>` +
          `<td class="small">${esc(r.user || '')}</td><td class="sum">${esc((r.label || '').slice(0, 64))}</td>` +
          `<td class="num small">${r.queue_wait_s ? fmt(r.queue_wait_s, 1) + 's' : (live && r.queue_pos ? '대기 중' : '')}</td>` +
          `<td class="num">${took}</td>` +
          `<td class="small muted">${stage}</td><td class="small">${res}</td><td class="small">${links}</td></tr>`;
      }).join('') + '</table>';
    $$('#led-table tr[data-led-tok]').forEach((tr) => tr.onclick = (e) => {
      if (e.target.closest('[data-led-req]') || e.target.closest('[data-led-run]')) return;
      openLedDetail(tr.dataset.ledTok, tr);
    });
    $$('#led-table [data-led-req]').forEach((a) => a.onclick = (e) => { e.preventDefault(); switchTab('requests'); LW.openRequest(+a.dataset.ledReq); });
    $$('#led-table [data-led-run]').forEach((a) => a.onclick = (e) => { e.preventDefault(); switchTab('logs'); const f = $('#log-grep'); if (f) { f.value = a.dataset.ledRun; loaders.logs(); } });
  }

  // 상세는 **누른 줄 바로 아래**에 편다. 목록이 100줄이면 표 아래의 고정 패널은 화면 밖이라
  // "눌러도 아무것도 안 보인다" 가 된다 (2026-09-23 실사용에서 확인).
  function ledPanel(trEl) {
    const table = $('#led-table');
    let row = $('#led-drow');
    if (trEl && trEl.parentNode) {
      if (!row || row.previousElementSibling !== trEl) {
        if (row) row.remove();
        row = document.createElement('tr');
        row.id = 'led-drow';
        row.innerHTML = '<td colspan="10"></td>';
        trEl.parentNode.insertBefore(row, trEl.nextSibling);
      }
      $$('#led-table tr[data-led-tok]').forEach((t) => t.classList.toggle('sel', t === trEl));
      return row.firstElementChild;
    }
    return table ? table : $('#led-detail');
  }

  function ledPanelClear() {
    const row = $('#led-drow');
    if (row) row.remove();
    $$('#led-table tr[data-led-tok]').forEach((t) => t.classList.remove('sel'));
    $('#led-detail').innerHTML = '';
  }

  async function openLedDetail(token, trEl) {
    if (LED.open === token) { LED.open = null; ledPanelClear(); return; }
    LED.open = token;
    let box = ledPanel(trEl);
    box.innerHTML = '<div class="muted">불러오는 중…</div>';
    const j = await api('/api/ledger/' + encodeURIComponent(token));
    if (LED.open !== token) return;           // 늦게 온 응답은 버린다 (다른 줄을 눌렀다)
    box = ledPanel(trEl);                     // 그 사이 표가 다시 그려졌을 수 있다
    if (!j || j.error) {
      box.innerHTML = `<div class="banner warn">${esc((j || {}).error || '읽지 못했습니다')}</div>`;
      return;
    }
    const badge = `<span class="pill ${LED_CLASS[j.status] || ''}">${LED_LABEL[j.status] || esc(j.status)}</span>`;
    const rows = (k, v) => (v == null || v === '' ? '' : `<div class="ag-item"><span class="ag-cap">${esc(k)}</span>${esc(String(v))}</div>`);
    let html = `<div class="req-head">${badge} <b>${esc(j.label || j.token)}</b> <span class="muted small mono">${esc(j.token)}</span>` +
      (j.status === 'running' ? ` <button class="mini danger" id="led-stop">■ 중지</button>` : '') + '</div>';
    html += '<div class="ag">' + rows('종류', j.kind) + rows('창구', j.origin) + rows('사용자', j.user) + rows('역할', j.role) +
      rows('IP', j.ip) + rows('경로', (j.method ? j.method + ' ' : '') + (j.path || '')) +
      rows('시각', dt(j.opened)) + rows('대기', j.queue_wait_s != null ? j.queue_wait_s + 's' : null) +
      rows('소요', j.ms != null ? fmt(j.ms, 0) + 'ms' : (j.elapsed_s + 's')) +
      rows('HTTP', j.http) + rows('사유 코드', j.code) + rows('마지막 단계', j.stage) +
      rows('락', j.lock_mode) + rows('시간 제한', j.limit_s ? j.limit_s + 's' : null) + '</div>';
    if (j.error) html += `<div class="banner warn">${esc(j.error)}</div>`;
    // 거절은 **무엇이 막았는지**를 그 자리에서 보여 준다. 로그 파일에는 거절이 남지 않으므로
    // 여기 말고는 알 방법이 없다 (2026-09-23).
    if (j.limit_json) {
      let L = {};
      try { L = JSON.parse(j.limit_json); } catch (e) { L = {}; }
      html += '<h3>왜 거절됐나</h3><div class="ag">' +
        rows('막은 것', L.what) + rows('대상', L.who) + rows('종류', L.kind) +
        rows('그때 값', L.current != null ? `${L.current} / ${L.value}` : L.value) +
        rows('설정 키', L.key) + '</div>' +
        (L.key ? `<div class="row"><code class="small">python -m llmwiki server limits set ${esc(String(L.key).split(' / ')[0])}=&lt;값&gt;</code> ` +
                 '<button class="mini secondary" data-led-go="srv">서버 모니터에서 바꾸기</button></div>' : '') +
        (L.hint ? `<div class="muted small">${esc(L.hint)}</div>` : '');
    }
    if (j.status === 'unknown') html += '<div class="banner warn">이 요청은 <b>끝이 기록되지 않았습니다</b>. 서버가 그 사이에 멈췄거나 강제 종료됐을 수 있습니다 — 서버 시작 시각과 대조해 보세요.</div>';
    // ── 요청 프로파일 · 질의 로그를 **여기서 바로** 보여 준다 (탭을 옮기지 않게) ──
    if (j.profile_missing) {
      html += '<div class="banner">이 요청의 <b>단계 프로파일이 정리됐습니다</b> (keep_requests 로 오래된 행이 잘림). 원장 기록은 그대로 남아 있습니다.</div>';
    }
    if (j.answer) {
      const a = j.answer, pr = j.profile || {}, ql = j.qlog || {};
      const ans = a.text || '';
      html += '<h3>답변 · 근거 <small class="muted">질의 로그와 같은 원천</small></h3>' +
        `<div class="ag">${rows('판정', a.verdict)}${rows('groundedness', a.groundedness)}${rows('모드', a.mode)}${rows('모델', a.model)}` +
        `${rows('인용', (a.cited || []).length ? (a.cited || []).join(', ') : null)}${rows('근거', (a.hits || []).length)}` +
        `${rows('캐시', a.cached ? '예' : null)}${rows('피드백', ql.feedback === 1 ? '👍' : (ql.feedback === -1 ? '👎' : null))}</div>` +
        (ans ? `<details${ans.length < 600 ? ' open' : ''}><summary class="muted small">답변 (${ans.length}자)</summary><div class="pre small">${esc(ans)}</div></details>`
             : '<div class="muted small">저장된 답변이 없습니다.</div>');
      html += '<h3>단계 프로파일 <small class="muted">요청 프로파일과 같은 원천</small></h3>' +
        `<div class="ag">${rows('전체', pr.ms != null ? fmt(pr.ms, 0) + 'ms' : null)}${rows('LLM 호출', pr.llm_calls)}` +
        `${rows('입력 토큰', pr.input_tokens ? fmtK(pr.input_tokens) : null)}${rows('출력 토큰', pr.output_tokens ? fmtK(pr.output_tokens) : null)}` +
        `${rows('SQL 문', pr.sql_count)}${rows('run_id', (pr.run_id || '').slice(0, 8))}</div>` +
        `<div class="row"><button class="mini secondary" data-led-trace="${j.token}">단계별 워터폴 펼치기</button></div><div class="led-trace"></div>`;
    }
    html += '<h3>사건</h3><table class="req"><tr><th>시각</th><th>구분</th><th>내용</th></tr>' +
      (j.events || []).map((e) => `<tr><td class="small">${ts(e.ts)}</td><td class="small"><b>${esc(e.ev)}</b></td><td class="small mono">${esc(Object.entries(e).filter(([k]) => !['ev', 'ts', 'token', 'pid'].includes(k)).map(([k, v]) => k + '=' + v).join(' ').slice(0, 220))}</td></tr>`).join('') + '</table>';
    // 이 요청이 남긴 로그 줄 — 로그 탭으로 옮기지 않고 여기서 본다 (run_id 로 거른 것).
    // 로그 탭 자체는 남는다: 로그에는 요청에 속하지 않는 줄(빌드 단계·워처·디스크 경고)이 절반쯤 있다.
    if (j.logs && j.logs.length) {
      const LV = { ERROR: 'bad', CRITICAL: 'bad', WARNING: 'warn' };
      html += `<h3>로그 <small class="muted">이 요청의 run_id 로 거른 줄${j.logs_warn ? ` · <span class="pill bad">경고 이상 ${j.logs_warn}</span>` : ''}</small></h3>` +
        `<details${j.logs_warn ? ' open' : ''}><summary class="muted small">${j.logs.length}줄</summary>` +
        '<table class="req"><tr><th>시각</th><th>수준</th><th>파일</th><th>내용</th></tr>' +
        j.logs.map((r) => `<tr class="${LV[String(r.level || '').toUpperCase()] === 'bad' ? 'has-err' : ''}">` +
          `<td class="small">${ts(r.ts)}</td><td class="small">${esc(r.level || '')}</td>` +
          `<td class="small muted">${esc(r.file || '')}</td><td class="small">${esc(String(r.msg || '').slice(0, 220))}</td></tr>`).join('') +
        '</table></details>';
    } else if (j.logs_run_id) {
      html += '<div class="muted small">이 요청이 남긴 로그 줄이 없습니다 (경고·실패가 없었다는 뜻입니다).</div>';
    }
    const links = [];
    if (j.request_id) links.push(`<button class="mini" data-led-go="req">📄 요청 프로파일 #${j.request_id}</button>`);
    if (j.run_id) links.push(`<button class="mini secondary" data-led-go="log">📜 로그 (run ${esc(String(j.run_id).slice(0, 8))})</button>`);
    if (j.status === 'running') links.push('<button class="mini secondary" data-led-go="act">⏱ 진행 중 작업</button>');
    if (links.length) html += '<div class="row">' + links.join(' ') + '</div>';
    if (j.concurrent_hidden) {
      html += '<div class="muted small">같은 시각의 요청은 전체 조회 권한이 있어야 보입니다 (작업 <code>requests all</code>).</div>';
    } else {
      const c = j.concurrent || [];
      // 기본은 **접어 둔다** — 대부분은 안 보고 지나가는 정보이고, 펼쳐 두면 상세가 길어져
      // 정작 먼저 봐야 할 것(개요·사건·거절 사유)이 밀린다. 느린 요청을 파고들 때만 편다.
      html += '<details class="led-con"><summary><b>같은 시각의 요청</b> ' +
        `<span class="muted small">${c.length ? c.length + '건 겹침 — 느린 이유가 여기 있을 때가 많습니다' : '없음 (이 요청 혼자 돌았습니다)'}</span></summary>` +
        (c.length ? '<table class="req"><tr><th>겹침</th><th>상태</th><th>종류</th><th>사용자</th><th>라벨</th><th>소요</th></tr>' +
          c.map((x) => `<tr data-led-tok2="${esc(x.token)}"><td class="num">${fmt(x.overlap_s, 1)}s</td><td><span class="pill ${LED_CLASS[x.status] || ''}">${LED_LABEL[x.status] || esc(x.status)}</span></td><td class="small">${esc(x.kind || '')}</td><td class="small">${esc(x.user || '')}</td><td class="sum">${esc((x.label || '').slice(0, 50))}</td><td class="num">${x.ms != null ? fmt(x.ms, 0) : ''}</td></tr>`).join('') + '</table>'
          : '<div class="muted small">겹친 요청이 없습니다.</div>') + '</details>';
    }
    box.innerHTML = '<div class="led-detail">' + html + '</div>';
    const scope = box;
    const stop = scope.querySelector('#led-stop');
    if (stop) stop.onclick = async () => { await LW.cancelToken(j.client_token || j.token); setTimeout(loadLedger, 500); };
    scope.querySelectorAll('[data-led-go]').forEach((b) => b.onclick = () => {
      const w = b.dataset.ledGo;
      if (w === 'req') { switchTab('requests'); LW.openRequest(j.request_id); }
      else if (w === 'log') { switchTab('logs'); const f = $('#log-grep'); if (f) { f.value = j.run_id; loaders.logs(); } }
      else if (w === 'srv') { switchTab('server'); }
      else { switchTab('activity'); }
    });
    // 워터폴은 한 건에 수십 KB 라 **펼칠 때만** 가져온다 (목록을 훑을 때마다 끌고 오지 않게)
    const tb = scope.querySelector('[data-led-trace]');
    if (tb) tb.onclick = async () => {
      const box2 = scope.querySelector('.led-trace');
      if (box2.innerHTML) { box2.innerHTML = ''; tb.textContent = '단계별 워터폴 펼치기'; return; }
      box2.innerHTML = '<div class="muted">불러오는 중…</div>';
      const full = await api('/api/ledger/' + encodeURIComponent(j.token) + '?sections=trace');
      if (!full || !full.trace) { box2.innerHTML = '<div class="muted">저장된 단계 정보가 없습니다.</div>'; return; }
      box2.innerHTML = '<div id="led-trace-host"></div>';
      try { renderTrace(box2.querySelector('#led-trace-host'), full.trace); }
      catch (e) { box2.innerHTML = `<pre class="pre small">${esc(JSON.stringify(full.trace, null, 1).slice(0, 4000))}</pre>`; }
      tb.textContent = '워터폴 접기';
    };
    scope.querySelectorAll('tr[data-led-tok2]').forEach((tr) => tr.onclick = (e) => {
      e.stopPropagation();
      const t2 = tr.dataset.ledTok2;
      const target = $(`#led-table tr[data-led-tok="${t2}"]`);
      LED.open = null;                       // 토글이 아니라 '그 요청으로 이동'
      openLedDetail(t2, target || trEl);
    });
    try { box.scrollIntoView({ behavior: 'smooth', block: 'nearest' }); } catch (e) { /* 구형 브라우저 */ }
  }

  function syncViewSeg() {
    $$('#led-view button').forEach((b) => b.classList.toggle('active', b.dataset.v === LED.view));
  }

  // 목록·상태 스트립·시간 막대는 **한 번의 요청으로 함께** 갱신된다 (따로 폴링하지 않는다).
  // 주기는 server.json 의 ledger.refresh_ms 가 기본이고 사용자가 드롭다운으로 바꾼다. 0 = 끔.
  function ledAuto() {
    clearInterval(LED.timer);
    const ms = LED.refreshMs || 0;
    $('#led-every-note').textContent = ms ? `목록·막대 모두 ${ms / 1000}초마다` : '자동 갱신 꺼짐 (새로고침 버튼으로)';
    if (!ms) return;
    LED.timer = setInterval(() => {
      if (LW.tabVisible('ledger') && !LED.open) loadLedger();
    }, ms);
  }

  $$('#led-view button').forEach((b) => b.onclick = () => { LED.view = b.dataset.v; syncViewSeg(); LED.open = null; ledPanelClear(); loadLedger(); });
  $('#btn-led-refresh').onclick = () => { LED.range = null; loadLedger(); };
  $('#led-every').onchange = () => { LED.refreshMs = +$('#led-every').value; ledAuto(); };
  ['#led-kind', '#led-origin', '#led-since', '#led-limit'].forEach((s) => { const el = $(s); if (el) el.onchange = () => { if (s === '#led-since') { LED.since = +$('#led-since').value; LED.range = null; } loadLedger(); }; });
  $('#led-minms').onchange = loadLedger;
  $('#led-q').addEventListener('keydown', (e) => { if (e.key === 'Enter') loadLedger(); });
  if ($('#led-logq')) $('#led-logq').addEventListener('keydown', (e) => { if (e.key === 'Enter') loadLedger(); });
  $('#btn-led-csv').onclick = () => { window.location = '/api/ledger/export?format=csv&' + ledQuery(); };
  $('#btn-led-jsonl').onclick = () => { window.location = '/api/ledger/export?format=jsonl&' + ledQuery(); };
  loaders.ledger = () => { LED.since = +$('#led-since').value; LED.range = null; loadLedger(); };
})(window.LW);
