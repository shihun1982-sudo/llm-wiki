/* Ask — 질의·답변·근거 판정·claim·pin·포렌식 + 채널 디버그. */
(function (LW) {
  'use strict';
  const { $, $$, esc, fmt, fmtK, api, toast, STATE, overrides, presetNames, cliEquiv, updateCli, renderTrace, switchTab, switchGroup, loadStatus } = LW;

  const SAMPLES = ['ISSUE-2001 의 원인과 수정 CL 은?', 'CL-55302 는 어떤 이슈를 수정했나?', 'HW rev B1 에서 t_setup 은 몇 ns 인가?', 'ISR 안에서 blocking 대기를 써도 되나?', '지난주 리뷰한 CL', '2026년 8월 주간 보고 이슈 요약', 'RX DMA 드라이버의 code map', 'Physical Downlink Control Channel 디코더 문제'];
  $('#samples').innerHTML = SAMPLES.map((s) => `<span>${esc(s)}</span>`).join('');
  $$('#samples span').forEach((s) => s.onclick = () => { $('#q').value = s.textContent; runQuery(); });
  $('#q').addEventListener('input', updateCli);
  $('#q-mode').addEventListener('change', () => LW.applyPresets());
  $('#q').addEventListener('keydown', (e) => { if (e.key === 'Enter') runQuery(); });
  $('#btn-query').onclick = runQuery;
  $('#btn-q-inspect').onclick = () => { if (STATE.lastRequestId) { switchGroup('observability'); switchTab('requests'); setTimeout(() => LW.openRequest && LW.openRequest(STATE.lastRequestId), 300); } };
  $('#btn-q-forensic').onclick = async () => {
    if (!STATE.lastRequestId) return;
    const el = $('#q-forensic'); el.classList.remove('hidden'); el.innerHTML = '진단 중…';
    const f = await api('/api/forensic?request_id=' + STATE.lastRequestId + '&rerun=1');
    el.innerHTML = LW.renderForensic ? LW.renderForensic(f) : `<pre class="pre">${esc(JSON.stringify(f, null, 1))}</pre>`;
  };

  // ---------------- 상세 분석 리포트 (analysis_mode) ----------------
  const SEV = { error: '🔴', warn: '🟠', info: '🔵', ok: '🟢' };
  $('#btn-q-analysis').onclick = async () => {
    if (!STATE.lastRequestId) { toast('먼저 질의를 실행하세요'); return; }
    const el = $('#q-analysis'); el.classList.remove('hidden'); el.innerHTML = '분석 리포트 생성 중…';
    const focus = ($('#qa-focus') && $('#qa-focus').value) || 'all';
    const j = await api('/api/analysis?request_id=' + STATE.lastRequestId + '&focus=' + focus);
    if (!j || j.error) { el.innerHTML = `<div class="banner err">${esc((j || {}).error || '실패')}</div>`; return; }
    const s = j.summary || {};
    const mdUrl = '/api/analysis?request_id=' + s.request_id + '&format=md&focus=' + focus;
    let html = `<div class="req-head"><b>📊 상세 분석 리포트</b> request #${s.request_id} · ${fmt(s.total_ms, 0)} ms (LLM ${fmt(s.llm_ms, 0)} ms) · 토큰 ${fmtK((s.tokens || {}).total_tokens || 0)} · 판정 <span class="pill">${esc(s.verdict || '-')}</span> · groundedness ${s.groundedness == null ? '-' : fmt(s.groundedness, 2)} · 상세도 ${s.detail_level}${s.detail_level >= 2 ? '' : ' <span class="muted">(사이드바 토글 analysis_mode 를 켜고 다시 질의하면 debug·프롬프트 샘플 포함)</span>'}</div>` +
      `<div class="row"><label>초점 <select id="qa-focus"><option value="all"${focus === 'all' ? ' selected' : ''}>전체</option><option value="quality"${focus === 'quality' ? ' selected' : ''}>품질</option><option value="speed"${focus === 'speed' ? ' selected' : ''}>속도</option><option value="tokens"${focus === 'tokens' ? ' selected' : ''}>토큰</option></select></label> <a class="button mini secondary" href="${mdUrl}" target="_blank">md 열기</a> <a class="button mini secondary" href="${mdUrl}&download=1">다운로드</a> <button class="mini secondary" id="qa-copy">클립보드 복사 (LLM 에게 붙여넣기)</button> <button class="mini" id="qa-insight-btn" title="이 리포트를 LLM 에게 읽히고 무엇을 바꾸면 좋아지는지 받아 옵니다">🧠 LLM 소견 받기</button> <button class="mini secondary" id="qa-insight-propose" title="LLM 소견을 Evolve 제안(HITL)으로 등록합니다">소견 → 제안 등록</button> <span class="muted small">파일: ${esc((j.paths || {}).md || '')}</span></div>` +
      `<div class="row"><span class="muted small">다른 LLM 에게 통째로 줄 자료 — 최적화 가이드(손잡이 지도) + 지금 설정 + 이 질의의 실측 + 지시문을 한 파일로:</span> ` +
      `<a class="button mini" href="/api/optimize/bundle?request_id=${s.request_id}&focus=${focus}" title="가이드+설정+실측을 한 파일(.md)로 내려받습니다">📦 최적화 자료 묶음 다운로드</a> ` +
      `<button class="mini secondary" id="qa-bundle-copy" title="같은 내용을 클립보드로 복사합니다">묶음 복사</button> ` +
      `<a class="button mini secondary" href="/api/optimize/guide" target="_blank" title="손잡이 지도만 보기 (docs/OPTIMIZATION_GUIDE.md 와 같은 내용)">손잡이 지도만 보기</a></div>`;
    for (const lens of ['quality', 'speed', 'tokens']) {
      if (focus !== 'all' && focus !== lens) continue;
      const rows = (s.top || {})[lens] || [];
      html += `<div><b>${{ quality: '품질', speed: '속도', tokens: '토큰' }[lens]}</b> ` + rows.map((f) => `${SEV[f.severity] || '•'} ${esc(f.title)}${(f.knobs || []).length ? ' <span class="muted">→ ' + f.knobs.map(esc).join(', ') + '</span>' : ''}`).join(' &nbsp;·&nbsp; ') + '</div>';
    }
    html += '<div id="qa-insight"></div>';
    html += `<details><summary>리포트 전문 (마크다운)</summary><pre class="pre" id="qa-md">${esc(j.markdown || '')}</pre></details>`;
    el.innerHTML = html;
    $('#qa-focus').onchange = () => $('#btn-q-analysis').click();
    $('#qa-copy').onclick = async () => { try { await navigator.clipboard.writeText(j.markdown || ''); toast('복사됨'); } catch (e) { toast('복사 실패: ' + e); } };
    $('#qa-bundle-copy').onclick = async (e) => {
      const btn = e.currentTarget; btn.disabled = true; btn.textContent = '묶는 중…';
      try {
        const b = await api('/api/optimize/bundle?request_id=' + s.request_id + '&focus=' + focus + '&format=json');
        if (!b || b.error) { toast('실패: ' + ((b || {}).error || '')); return; }
        await navigator.clipboard.writeText(b.markdown || '');
        toast('복사됨 — ' + fmtK(b.chars || 0) + '자 (LLM 대화창에 붙여넣기)');
      } catch (err) { toast('복사 실패: ' + err); } finally { btn.disabled = false; btn.textContent = '묶음 복사'; }
    };
    $('#qa-insight-btn').onclick = () => runInsight(s.request_id, focus, false);
    $('#qa-insight-propose').onclick = () => runInsight(s.request_id, focus, true);
  };
  // 리포트를 LLM 에게 읽히고 '무엇을 바꾸면 좋아지는지' 를 받는다 (규칙 소견은 수치까지만 알려 준다)
  async function runInsight(rid, focus, propose) {
    const box = $('#qa-insight'); if (!box) return;
    box.innerHTML = '<div class="muted small">LLM 이 리포트를 읽는 중…</div>';
    const j = await api('/api/analysis/insight', { request_id: rid, focus: focus, propose: !!propose });
    const rawBlock = j.raw ? `<details><summary class="muted small">LLM 원문 보기</summary><pre class="pre small">${esc(j.raw)}</pre></details>` : '';
    if (!j || j.error) { box.innerHTML = `<div class="banner warn">${esc((j || {}).error || '실패')}</div>` + rawBlock; return; }
    const ins = j.insights || [];
    if (!ins.length) { box.innerHTML = `<div class="banner ok">LLM 소견: 바꿀 만한 것을 찾지 못했습니다. ${esc(j.verdict || '')}</div>` + rawBlock; return; }
    box.innerHTML = `<div class="req-head"><b>🧠 LLM 소견</b> <span class="muted">${esc(j.model || '')}</span> ${esc(j.verdict || '')}</div>` +
      '<table><tr><th></th><th>렌즈</th><th>문제</th><th>바꿀 설정</th><th>기대 효과</th><th>부작용</th></tr>' +
      ins.map((x) => `<tr><td>${SEV[x.severity] || '•'}</td><td class="small">${esc(x.lens || '')}</td><td>${esc(x.problem || '')}</td>` +
        `<td class="small"><code>${esc(x.change || ((x.key || '') + ' ' + (x.from == null ? '' : x.from) + ' → ' + (x.to == null ? '' : x.to)))}</code></td>` +
        `<td class="small">${esc(x.effect || '')}</td><td class="small muted">${esc(x.risk || '')}</td></tr>`).join('') + '</table>' +
      ((j.proposals || []).length ? `<div class="banner ok">제안 ${j.proposals.length}건 등록됨 — Evolve › 제안(HITL) 에서 검토·적용</div>` : '');
  }

  function verdictPill(v) { return `<span class="pill ${v === 'sufficient' ? 'ok' : v === 'weak' ? 'warn' : 'bad'}">${esc(v || '-')}</span>`; }
  function boostChips(b) { return Object.keys(b || {}).map((k) => `<span class="boost" title="${k}">${k} ×${fmt(b[k], 2)}</span>`).join(''); }

  async function runQuery() {
    const q = $('#q').value.trim(); if (!q) return;
    $('#btn-query').disabled = true;
    // 응답을 기다리는 동안 단계/LLM 대기 시간을 보여 준다 (progress_token → GET /api/progress/<token>, 락 없이 응답)
    const token = 'q-' + Date.now().toString(36) + Math.random().toString(36).slice(2, 8);
    const liveEl = $('#q-live'); LW.renderLive(liveEl, { status: 'running', label: '질의 전송 중…' });
    const stopWatch = LW.watchProgress(token, liveEl, 500);
    let j;
    try {
      j = await api('/api/query', { q, overrides: overrides(), log: $('#q-log').checked, preset: presetNames().join(','), mode: $('#q-mode').value, progress_token: token });
    } finally { stopWatch(); }
    try {
      if (!j.result) { LW.renderLive(liveEl, { status: j.cancelled ? 'cancelled' : 'error', detail: j.error || '응답 없음', elapsed_s: 0, log: liveEl._log || [] }, '', token); return; }
      // 완료 상태와 단계 로그를 그대로 남긴다 (✕ 로 닫기 · 📋 로 복사)
      LW.renderLive(liveEl, { status: 'done', elapsed_s: (j.result.ms || 0) / 1000, detail: j.result.answer_mode, log: liveEl._log || [] }, '', token);
      const r = j.result; STATE.lastQueryId = r.query_id || null; STATE.lastRequestId = r.request_id || null; STATE.lastResult = r;
      $('#query-out').classList.remove('hidden'); $('#q-forensic').classList.add('hidden'); $('#q-analysis').classList.add('hidden');
      if (r.analysis && r.analysis.md) { $('#q-analysis').classList.remove('hidden'); $('#q-analysis').innerHTML = `<div class="banner"><b>📊 analysis_mode</b> — 리포트 저장됨: <code>${esc(r.analysis.md)}</code> · ` + ['quality', 'speed', 'tokens'].map((l) => (((r.analysis.top || {})[l] || [])[0] ? `${{ quality: '품질', speed: '속도', tokens: '토큰' }[l]}: ${esc(((r.analysis.top || {})[l] || [])[0].title)}` : '')).filter(Boolean).join(' · ') + ' · <a href="#" id="qa-open">전문 보기</a></div>'; const o = $('#qa-open'); if (o) o.onclick = (e) => { e.preventDefault(); $('#btn-q-analysis').click(); }; }
      $('#answer-mode').textContent = r.answer_mode + (r.model ? ' · ' + r.model : '');
      $('#answer-ms').textContent = fmt(r.ms) + ' ms';
      $('#answer-cached').classList.toggle('hidden', !(r.cached || r.precomputed)); $('#answer-cached').textContent = r.precomputed ? 'precomputed' : 'cached';
      const g = r.groundedness; $('#answer-ground').textContent = g == null ? '' : 'groundedness ' + fmt(g, 2); $('#answer-ground').className = 'pill ' + (g == null ? '' : g >= 0.8 ? 'ok' : g >= 0.5 ? 'warn' : 'bad');
      const tk = r.tokens || {};
      const ev = r.evidence || {};
      $('#q-stats').innerHTML = `<div class="stat"><b>${fmt(r.ms, 0)}</b>ms</div><div class="stat"><b>${tk.calls || 0}</b>LLM 호출</div><div class="stat"><b>${fmtK(tk.total_tokens || 0)}</b>토큰</div><div class="stat"><b>${j.trace && j.trace.summary ? j.trace.summary.sql_statements : '-'}</b>SQL</div><div class="stat"><b>${r.hits.filter((h) => h.in_context).length}/${r.hits.length}</b>컨텍스트 청크</div><div class="stat"><b>${(r.fallback || []).length}</b>fallback</div><div class="stat"><b>#${r.request_id || '-'}</b>request</div><div class="stat"><b>${esc(r.run_id || '-')}</b>run</div>`;
      const ban = $('#q-banner');
      if (r.answer_mode === 'insufficient') { ban.className = 'banner err'; ban.innerHTML = `<b>근거 부족(insufficient)</b> — ${esc((ev.reasons || []).join('; '))}. fallback ${(r.fallback || []).length}회 시도. <a href="#" id="q-fx-link">포렌식 보기</a>`; ban.classList.remove('hidden'); }
      else if (ev.verdict === 'weak' || (g != null && g < 0.6)) { ban.className = 'banner warn'; ban.innerHTML = `<b>주의</b> — 근거 ${esc(ev.verdict || '-')}${g != null ? ', groundedness ' + fmt(g, 2) : ''}. ${esc((ev.reasons || []).join('; '))}`; ban.classList.remove('hidden'); }
      else if (r.forensic && r.forensic.id) { ban.className = 'banner warn'; ban.textContent = '포렌식 #' + r.forensic.id + ' 기록됨 (' + r.forensic.severity + ')'; ban.classList.remove('hidden'); }
      else ban.classList.add('hidden');
      const fxl = $('#q-fx-link'); if (fxl) fxl.onclick = (e) => { e.preventDefault(); $('#btn-q-forensic').click(); };
      // LLM 실행 보고 (재시도 후 실패 → 대체 경로)
      const lr = $('#q-llm-report');
      if (r.llm_report && (r.llm_report.summary || []).length) { lr.classList.remove('hidden'); lr.innerHTML = '<b>⚠ LLM 실행 보고</b> — ' + r.llm_report.summary.map(esc).join('<br>') + '<div class="muted small">설정: agents.json timeout_s/retries · config.json llm_timeout/llm_retries · Settings › 모델 › 실제 호출 테스트</div>'; }
      else if (r.repeat_loop) {
        // 모델이 같은 구절을 되풀이하는 고장 — 잘라내고 이유를 알린다 (이 답변은 캐시에 저장되지 않는다)
        lr.classList.remove('hidden');
        lr.innerHTML = '<b>⚠ 답변이 잘렸습니다 — 모델 반복 루프</b> — 같은 구절을 ' + esc(r.repeat_loop.times) + '번 되풀이해 그 지점부터 잘라냈습니다.' +
          '<div class="muted small">되풀이된 구절: <code>' + esc(String(r.repeat_loop.phrase || '').slice(0, 80)) + '…</code> · ' +
          '작은 모델에 컨텍스트가 길 때 생깁니다. 다시 질의하거나 더 큰 answer 모델을 쓰거나 token/speed 프리셋으로 컨텍스트를 줄여 보세요. ' +
          '억제 강도: config.json llm_frequency_penalty · llm_repeat_penalty</div>';
      }
      else lr.classList.add('hidden');
      $('#answer').innerHTML = esc(r.answer).replace(/\[C(\d+)\]/g, (m, n) => `<span class="cite" data-n="${n}">[C${n}]</span>`);
      $$('#answer .cite').forEach((c) => c.onclick = () => { const h = $(`#hit-${c.dataset.n}`); if (h) { h.classList.add('open'); h.scrollIntoView({ behavior: 'smooth', block: 'center' }); } });
      $('#fb-result').textContent = ''; $('#fb-note').value = '';
      // evidence / claims / plan
      const plan = r.plan || {}; const cl = r.claims || {};
      const qr = plan.query_rules || {};
      const lines = [];
      lines.push(`근거 판정: ${verdictPill(ev.verdict)} ${ev.score != null ? 'score ' + fmt(ev.score, 2) : ''} ${(ev.reasons || []).map(esc).join(' · ')}${ev.llm ? ' · LLM: ' + esc(ev.llm.verdict) + (ev.llm.missing && ev.llm.missing.length ? ' 부족=' + esc(ev.llm.missing.join('; ')) : '') : ''}`);
      if (r.fallback && r.fallback.length) lines.push('fallback: ' + r.fallback.map((f) => `${esc(f.level)}→${esc(f.verdict)}${f.improved ? '↑' : ''}${f.stop_reason ? ' (' + esc(f.stop_reason) + ')' : ''}`).join(' , '));
      if (r.claims) lines.push(`claim 검증: 사실문장 ${cl.n_factual} · supported ${cl.supported} · partial ${cl.partial} · unsupported ${cl.unsupported} · citation precision ${fmt(cl.citation_precision, 2)}${cl.refined ? ' · 재작성됨' : ''}${cl.policy_applied ? ' · 정책 적용 ' + cl.policy_applied : ''}`);
      if (plan.time_scope) lines.push(`시간: "${esc(plan.time_scope.expr)}" → ${plan.time_scope.from} ~ ${plan.time_scope.to}`);
      if (qr.fired && qr.fired.length) lines.push('규칙 확장: ' + qr.fired.map((f) => `${esc(f.type)}:${esc(f.matched)}→${esc((f.values || []).slice(0, 3).join('|'))}`).join(' · '));
      if (plan.doc_types && plan.doc_types.length) lines.push('문서 유형 힌트: ' + plan.doc_types.join(', '));
      if (plan.alt_llm && plan.alt_llm.length) lines.push('LLM 확장 질의: ' + plan.alt_llm.map(esc).join(' | '));
      if (plan.pins && plan.pins.length) lines.push('pin: ' + plan.pins.map((p) => esc(p.id + ' ' + (p.doc || p.chunk))).join(', '));
      if (r.boosts && Object.keys(r.boosts).length) lines.push('boosts: ' + esc(JSON.stringify(r.boosts)));
      if (r.doc_expand && r.doc_expand.docs != null) lines.push(`문서 단위 확장(doc_expand): 문서 ${r.doc_expand.docs} · 후보 ${r.doc_expand.candidates} · 추가 ${r.doc_expand.added} (${esc(r.doc_expand.mode)}${r.doc_expand.vector ? '+vector' : ''}, min ${r.doc_expand.min_score})`);
      $('#q-evidence').innerHTML = lines.join('\n');
      const route = r.route || {};
      $('#route').textContent = `kind=${route.kind || '(router off)'}  weights=${JSON.stringify(r.config.weights)}\nkeywords=${JSON.stringify(route.keywords || [])}\ngraph seeds=${JSON.stringify((r.graph || {}).seeds || [])}  provenance=${JSON.stringify((r.graph || {}).provenance || {})}\nanswer llm=${r.config.llm}/${r.config.llm_model}  rerank=${r.config.rerank_llm}  embedder=${r.config.embedder}  round=${(r.config.round || {}).level || 'base'}${r.cached ? '\n(cached result)' : ''}`;
      $('#hits').innerHTML = r.hits.map((h) => `<div class="hit ${h.in_context ? '' : 'excluded'}" id="hit-${h.n || 'x'}"><div class="h"><b>${h.in_context ? '[C' + h.n + ']' : '[제외]'}</b> <span>${esc(h.doc_id)}</span> <span class="pill">${esc(h.doc_type || '-')}${h.ext_id ? ' ' + esc(h.ext_id) : ''}${h.date ? ' · ' + esc(h.date) : ''}</span> <span class="muted">${esc(h.heading)}</span> ` +
        (h.why || []).map((w) => `<span class="src ${w.split('#')[0]}">${esc(w)}</span>`).join('') + boostChips(h.boosts) + ` <span class="muted">fused=${fmt(h.fused, 4)} rerank=${h.rerank == null ? '-' : fmt(h.rerank, 3)}</span> <button class="mini secondary" data-pin="${esc(h.chunk_id)}" title="이 질의에 이 근거를 고정">📌</button></div><div class="t">${esc(h.text)}</div></div>`).join('');
      $$('#hits .hit .t').forEach((t) => t.onclick = () => t.parentElement.classList.toggle('open'));
      $$('#hits [data-pin]').forEach((b) => b.onclick = async (e) => { e.stopPropagation(); const p = await api('/api/pins', { action: 'add', chunk: b.dataset.pin, query: q, note: 'from query' }); toast('pin 추가: ' + p.id); });
      renderTrace($('#trace'), j.trace);
      const rels = ((r.graph || {}).relations || []).slice(0, 15);
      $('#graph-rels').innerHTML = rels.length ? '<table><tr><th>src</th><th>rel</th><th>dst</th><th>출처</th><th>gain</th><th>근거</th></tr>' + rels.map((x) => `<tr><td>${esc(x.src)}</td><td>${esc(x.rel)}</td><td>${esc(x.dst)}</td><td class="muted">${esc(x.provenance || '')}</td><td class="num">${fmt(x.gain, 3)}</td><td class="muted">${esc((x.description || '').slice(0, 60))}</td></tr>`).join('') + '</table>' : '<span class="muted">(graph off 또는 시드 없음)</span>';
      $('#q-proposals').textContent = (r.proposals && r.proposals.length) ? '생성된 제안 id: ' + r.proposals.join(', ') + ' → Evolve 에서 검토' : '새 제안 없음';
      $('#cli-equiv').textContent = r.cli || cliEquiv('query', q);
      loadStatus();
    } finally { $('#btn-query').disabled = false; }
  }
  $$('.feedback button[data-fb]').forEach((b) => b.onclick = async () => {
    if (!STATE.lastQueryId) { toast('로그가 꺼진(또는 캐시된) 질의에는 피드백을 남길 수 없습니다'); return; }
    const j = await api('/api/feedback', { query_id: STATE.lastQueryId, feedback: parseInt(b.dataset.fb, 10), note: $('#fb-note').value });
    $('#fb-result').textContent = '기록됨. 제안: ' + JSON.stringify(j.proposals || []) + (j.episode ? ' · 에피소드 #' + j.episode : '');
    loadStatus();
  });
  // ---------------- 기대 결과 포렌식 (forensic expect) ----------------
  function renderExpect(rep) {
    if (!rep || (rep.error && !(rep.targets || []).length)) return `<div class="banner err">${esc((rep || {}).error || 'no data')}</div>`;
    const stageMark = (s) => s === 'hit' ? '<span class="ok">✔</span>' : s === 'miss' ? '<span class="bad">✘</span>' : s === 'off' ? '<span class="muted">—</span>' : '<span class="muted">?</span>';
    let html = `<div class="req-head"><b>기대 결과 포렌식</b> request #${rep.request_id} · 원 판정 <span class="pill">${esc(rep.verdict || '-')}</span> · 재실행 ${rep.rerun ? rep.rerun_rounds + ' 라운드 (' + esc(rep.rerun_verdict || '') + ')' : '없음'}${rep.forensic_id ? ' · forensics #' + rep.forensic_id : ''}</div>` +
      `<div class="muted small">Q: ${esc(rep.query || '')} · 기대 docs=${esc(JSON.stringify((rep.expected || {}).docs ? Object.keys(rep.expected.docs) : []))} terms=${esc(JSON.stringify((rep.expected || {}).terms || []))}${((rep.expected || {}).unresolved || []).length ? ' · <span class="errtxt">미해결 ' + esc(rep.expected.unresolved.join(', ')) + '</span>' : ''}</div>` +
      '<ul class="small" style="margin:6px 0 6px 16px">' + (rep.summary || []).map((s) => `<li>${esc(s)}</li>`).join('') + '</ul>';
    (rep.targets || []).forEach((t) => {
      html += `<details ${t.chunk_id === rep.best_target ? 'open' : ''}><summary><b>${esc(t.chunk_id)}</b> <span class="muted small">${esc((t.heading || '').slice(0, 60))} · ${esc(t.why || '')}</span> · 원 결과 <span class="pill ${t.original === 'cited' ? 'ok' : t.original === 'candidate' ? 'warn' : 'bad'}">${esc(t.original)}</span> → 탈락 <span class="pill ${t.lost_at === 'none' ? 'ok' : 'bad'}">${esc(t.lost_at)}</span></summary>` +
        '<table class="small"><tr><th></th><th>단계</th><th>순위</th><th>상세</th></tr>' + (t.journey || []).map((j) => `<tr><td>${stageMark(j.status)}</td><td>${esc(j.stage)}</td><td class="num">${j.rank || ''}</td><td>${esc(j.detail || '')}</td></tr>`).join('') + '</table></details>';
    });
    html += '<h4 style="margin:8px 0 4px">수정안</h4>' + ((rep.suggestions || []).map((s) => `<div class="sugg"><span class="pill">${esc(s.kind)}</span> ${esc(s.detail)} <span class="muted">conf ${fmt(s.confidence, 2)}</span></div>`).join('') || '<div class="muted small">없음</div>');
    if (rep.proposals && rep.proposals.length) html += `<div class="banner ok">제안 등록: #${rep.proposals.join(', #')} → Evolve 탭에서 승인</div>`;
    return html;
  }
  LW.renderExpect = renderExpect;
  async function runExpect(rid, docs, terms, note, propose, outEl) {
    outEl.classList.remove('hidden'); outEl.innerHTML = '기대 결과 포렌식 실행 중… (같은 설정으로 검색을 다시 실행합니다)';
    const rep = await api('/api/forensic/expect', { request_id: rid, docs, terms, note, propose });
    outEl.innerHTML = renderExpect(rep);
    return rep;
  }
  LW.runExpect = runExpect;
  $('#btn-q-expect').onclick = () => { const p = $('#q-expect-form'); p.classList.toggle('hidden'); };
  $('#btn-q-expect-run').onclick = async () => {
    if (!STATE.lastRequestId) { toast('먼저 질의를 실행하세요'); return; }
    const docs = $('#qe-docs').value.trim(), terms = $('#qe-terms').value.trim();
    if (!docs && !terms) { toast('기대 문서(ID) 또는 용어를 하나 이상 입력하세요'); return; }
    await runExpect(STATE.lastRequestId, docs, terms, $('#qe-note').value.trim(), $('#qe-propose').checked, $('#q-expect-out'));
  };

  // ---------------- SEARCH DEBUG ----------------
  $('#btn-search').onclick = async () => {
    const q = $('#s-q').value.trim(); if (!q) return;
    const j = await api('/api/search', { q, channel: $('#s-channel').value, k: parseInt($('#s-k').value, 10), overrides: overrides() });
    $('#cli-equiv').textContent = j.cli || '';
    const r = j.result;
    if (Array.isArray(r)) {
      $('#search-out').innerHTML = '<table><tr><th>#</th><th>chunk</th><th>score</th><th>snippet</th></tr>' + r.map((x, i) => `<tr><td>${i + 1}</td><td>${esc(x.chunk_id)}</td><td class="num">${fmt(x.score, 4)}</td><td>${esc(x.snippet || '')}</td></tr>`).join('') + '</table>';
    } else {
      $('#search-out').innerHTML = `<div class="kv">seeds=${esc(JSON.stringify(r.seeds))} provenance=${esc(JSON.stringify(r.provenance || {}))}</div><h3>chunks</h3><table>` + (r.chunks || []).map((c) => `<tr><td>${esc(c[0])}</td><td class="num">${fmt(c[1], 3)}</td></tr>`).join('') + '</table><h3>expanded entities</h3><table>' + (r.entities || []).map((e) => `<tr><td>${esc(e.name)}</td><td>${e.type}</td><td class="num">${fmt(e.score, 3)}</td></tr>`).join('') + '</table><h3>relations</h3><table>' + (r.relations || []).map((x) => `<tr><td>${esc(x.src)}</td><td>${esc(x.rel)}</td><td>${esc(x.dst)}</td><td class="muted">${esc(x.provenance || '')}</td><td class="num">${fmt(x.gain, 3)}</td></tr>`).join('') + '</table>';
    }
    renderTrace($('#search-trace'), j.trace, { openAll: true });
  };
  $('#s-q').addEventListener('keydown', (e) => { if (e.key === 'Enter') $('#btn-search').click(); });
  $('#btn-rules-test').onclick = async () => { const r = await api('/api/query_rules/test?q=' + encodeURIComponent($('#rules-test-q').value)); $('#search-out').innerHTML = `<pre class="pre">${esc(JSON.stringify(r, null, 1))}</pre>`; };
  $('#btn-time-test').onclick = async () => { const r = await api('/api/time?q=' + encodeURIComponent($('#time-test-q').value)); $('#search-out').innerHTML = `<pre class="pre">${esc(JSON.stringify(r, null, 1))}</pre>`; };
  LW.runQuery = runQuery;
})(window.LW);
