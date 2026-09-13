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

  function verdictPill(v) { return `<span class="pill ${v === 'sufficient' ? 'ok' : v === 'weak' ? 'warn' : 'bad'}">${esc(v || '-')}</span>`; }
  function boostChips(b) { return Object.keys(b || {}).map((k) => `<span class="boost" title="${k}">${k} ×${fmt(b[k], 2)}</span>`).join(''); }

  async function runQuery() {
    const q = $('#q').value.trim(); if (!q) return;
    $('#btn-query').disabled = true;
    try {
      const j = await api('/api/query', { q, overrides: overrides(), log: $('#q-log').checked, preset: presetNames().join(','), mode: $('#q-mode').value });
      if (!j.result) return;
      const r = j.result; STATE.lastQueryId = r.query_id || null; STATE.lastRequestId = r.request_id || null; STATE.lastResult = r;
      $('#query-out').classList.remove('hidden'); $('#q-forensic').classList.add('hidden');
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
