/* Quality — trial 비교, 평가, 포렌식, fusion 비교. */
(function (LW) {
  'use strict';
  const { $, $$, esc, fmt, fmtK, dt, api, toast, STATE, overrides, presetNames, pollJob, renderTrace, loaders, switchTab, switchGroup } = LW;

  // ---------------- TRIALS ----------------
  let TRIALS = [];
  async function loadTrials() {
    TRIALS = await api('/api/trials');
    $('#tr-list').innerHTML = '<table><tr><th></th><th>#</th><th>이름</th><th>시각</th><th>v</th><th>n</th><th>hit@k</th><th>MRR</th><th>term</th><th>ground.</th><th>cite prec.</th><th>insuf.</th><th>fallback</th><th>avg ms</th><th>p95</th><th>tok/q</th><th>coverage</th><th>note</th></tr>' +
      TRIALS.map((t) => { const s = t.summary || {}; return `<tr><td><input type="checkbox" data-tid="${t.trial_id}"></td><td>${t.trial_id}</td><td><b>${esc(t.name)}</b></td><td class="muted small">${dt(t.ts)}</td><td>${t.build_version}</td><td class="num">${s.n}</td><td class="num">${fmt(s['hit@k'], 3)}</td><td class="num">${fmt(s.mrr, 3)}</td><td class="num">${fmt(s.term_recall, 3)}</td><td class="num">${fmt(s.groundedness, 2)}</td><td class="num">${fmt(s.citation_precision, 2)}</td><td class="num">${fmt(s.insufficient_rate, 2)}</td><td class="num">${fmt(s.fallback_rate, 2)}</td><td class="num">${fmt(s.avg_ms, 0)}</td><td class="num">${fmt(s.p95_ms, 0)}</td><td class="num">${fmt(s.tokens_per_query, 0)}</td><td class="num">${fmt(s.embed_coverage, 2)}</td><td class="muted small">${esc(t.note || '')}</td></tr>`; }).join('') + '</table>' + (TRIALS.length ? '' : '<div class="muted">trial 이 없습니다. 위에서 실행하세요.</div>');
  }
  $('#btn-tr-refresh').onclick = loadTrials;
  $('#btn-tr-run').onclick = async () => {
    const sets = {}; ($('#tr-sets').value || '').split(',').map((x) => x.trim()).filter(Boolean).forEach((kv) => { const [k, v] = kv.split('='); if (k && v != null) sets[k.trim()] = v.trim(); });
    const j = await api('/api/trials', { action: 'run', name: $('#tr-name').value.trim(), preset: [$('#tr-preset').value].concat(presetNames()).filter(Boolean).join(','), sets, k: parseInt($('#tr-k').value, 10), overrides: overrides() });
    $('#tr-log').textContent = 'running…';
    pollJob(j.job, $('#tr-log'), (job) => { if (job.status === 'done') toast('trial 완료: ' + JSON.stringify(job.result.summary)); loadTrials(); });
  };
  let LAST_CMP = null;
  $('#btn-tr-compare').onclick = async () => {
    const ids = $$('#tr-list [data-tid]').filter((c) => c.checked).map((c) => c.dataset.tid);
    if (ids.length < 2) { toast('2개 이상 선택'); return; }
    const c = await api('/api/trials/compare?ids=' + ids.slice(0, 4).join(',')); LAST_CMP = c;
    if (c.error) { $('#tr-compare').innerHTML = `<div class="banner err">${esc(c.error)}</div>`; return; }
    const names = c.trials.map((t) => t.name);
    let html = `<h3>지표 비교 (기준: ${esc(names[0])})</h3><table><tr><th>지표</th>${names.map((n) => `<th>${esc(n)}</th>`).join('')}<th>Δ (vs 기준)</th></tr>` + c.metrics.map((m) => `<tr><td>${esc(m.metric)} <small class="muted">${m.higher_better ? '↑' : '↓'}</small></td>${m.values.map((v, i) => `<td class="num ${m.best === i && i !== 0 ? 'best' : ''}">${v == null ? '-' : typeof v === 'number' ? fmt(v, 3) : esc(String(v))}</td>`).join('')}<td class="num small">${(m.delta || []).slice(1).map((d) => d == null ? '-' : (d > 0 ? '+' : '') + fmt(d, 3)).join(' , ')}</td></tr>`).join('') + '</table>';
    if (Object.keys(c.wins).length) html += '<div class="statrow">' + Object.keys(c.wins).map((n) => `<div class="stat"><b>${c.wins[n].win}/${c.wins[n].loss}/${c.wins[n].tie}</b>${esc(n)} win/loss/tie</div>`).join('') + '</div>';
    if (c.per_question.length) html += `<h3>질문별</h3><table class="grid-q"><tr><th>질문</th>${names.map((n) => `<th>${esc(n)}</th>`).join('')}</tr>` + c.per_question.map((p) => `<tr><td>${esc(p.q)}</td>${p.cells.map((cell, i) => `<td class="${i > 0 ? (p.outcome[i - 1] || '') : ''}">${cell.hit ? '✔' : '✘'} r=${cell.rank || '-'} g=${cell.groundedness == null ? '-' : fmt(cell.groundedness, 2)} ${cell.answer_mode === 'insufficient' ? '<span class="pill bad">insuf</span>' : ''} <a href="#" data-req="${cell.request_id}" class="muted small">#${cell.request_id}</a></td>`).join('')}</tr>`).join('') + '</table>';
    if (c.config_diff.length) html += '<h3>설정 차이</h3><table><tr><th>키</th>' + names.map((n) => `<th>${esc(n)}</th>`).join('') + '</tr>' + c.config_diff.map((d) => `<tr><td class="mono small">${esc(d.key)}</td>${d.values.map((v) => `<td class="small">${esc(JSON.stringify(v))}</td>`).join('')}</tr>`).join('') + '</table>';
    if (c.recommendation.length) html += '<div class="banner ok"><b>추천</b><br>' + c.recommendation.map(esc).join('<br>') + '</div>';
    $('#tr-compare').innerHTML = html;
    $$('#tr-compare a[data-req]').forEach((a) => a.onclick = (e) => { e.preventDefault(); switchGroup('observability'); switchTab('requests'); setTimeout(() => LW.openRequest && LW.openRequest(parseInt(a.dataset.req, 10)), 300); });
  };
  $('#btn-tr-md').onclick = () => { if (!LAST_CMP || !LAST_CMP.markdown) { toast('먼저 비교하세요'); return; } navigator.clipboard.writeText(LAST_CMP.markdown).then(() => toast('markdown 복사됨')); };
  $('#btn-fusion-compare').onclick = async () => {
    const j = await api('/api/fusion/compare', { k: parseInt($('#tr-k').value, 10) || 5 });
    $('#tr-log').textContent = 'fusion compare running…';
    pollJob(j.job, $('#tr-log'), (job) => {
      if (job.status !== 'done') return;
      const rows = job.result.rows;
      $('#tr-compare').innerHTML = '<h3>Fusion 방식 비교</h3><table><tr><th>method</th><th>hit@k</th><th>MRR</th><th>term</th><th>avg ms</th><th>tokens</th></tr>' + rows.map((r) => `<tr><td><b>${esc(r.method)}</b></td><td class="num">${fmt(r['hit@k'], 3)}</td><td class="num">${fmt(r.mrr, 3)}</td><td class="num">${fmt(r.term_recall, 3)}</td><td class="num">${fmt(r.avg_ms, 0)}</td><td class="num">${fmtK(r.total_tokens || 0)}</td></tr>`).join('') + '</table><div class="muted small">적용: Settings › 튜닝 › fusion_method</div>';
    });
  };
  loaders.trials = loadTrials;

  // ---------------- EVAL ----------------
  async function loadEvalQuestions() { const q = await api('/api/eval/questions'); if (!$('#eval-out').innerHTML) $('#eval-out').innerHTML = '<div class="muted small">' + q.length + ' 문항: ' + q.map((x) => esc(x.q)).join(' · ') + '</div>'; }
  function runEval(matrix) {
    $('#eval-log').textContent = 'running…'; $('#eval-out').innerHTML = '';
    api('/api/eval', { k: parseInt($('#e-k').value, 10), matrix, overrides: overrides() }).then((j) => pollJob(j.job, $('#eval-log'), (job) => {
      if (job.status !== 'done') return;
      $('#cli-equiv').textContent = job.result.cli;
      if (job.result.matrix) {
        const rows = job.result.matrix;
        $('#eval-out').innerHTML = '<table><tr><th>combo</th><th>hit@k</th><th></th><th>MRR</th><th>term recall</th><th>answer term recall</th><th>avg ms</th><th>tokens</th></tr>' + rows.map((r) => `<tr><td><b>${r.combo}</b></td><td class="num">${fmt(r['hit@k'], 3)}</td><td><div class="bar"><i style="width:${r['hit@k'] * 100}%"></i></div></td><td class="num">${fmt(r.mrr, 3)}</td><td class="num">${fmt(r.term_recall, 3)}</td><td class="num">${fmt(r.answer_term_recall, 3)}</td><td class="num">${fmt(r.avg_ms, 0)}</td><td class="num">${fmtK(r.total_tokens || 0)}</td></tr>`).join('') + '</table>';
      } else {
        const r = job.result.result;
        $('#eval-out').innerHTML = `<div class="stat"><b>${fmt(r.summary['hit@k'], 3)}</b>hit@k</div><div class="stat"><b>${fmt(r.summary.mrr, 3)}</b>MRR</div><div class="stat"><b>${fmt(r.summary.term_recall, 3)}</b>term recall</div><div class="stat"><b>${fmt(r.summary.answer_term_recall, 3)}</b>answer term recall</div><div class="stat"><b>${fmt(r.summary.avg_ms, 0)}</b>avg ms</div><div class="stat"><b>${fmtK(r.summary.total_tokens || 0)}</b>tokens</div><div class="stat"><b>#${r.request_id || '-'}</b>request</div>` +
          '<table><tr><th></th><th>질문</th><th>rank</th><th>term</th><th>ms</th><th>tok</th><th>request</th><th>top</th></tr>' + r.rows.map((x) => `<tr><td>${x.hit ? '✔' : '<span class="bad">✘</span>'}</td><td>${esc(x.q)}</td><td class="num">${x.rank || '-'}</td><td class="num">${fmt(x.term_recall, 2)}</td><td class="num">${fmt(x.ms, 0)}${x.cached ? ' <small class="muted">(c)</small>' : ''}</td><td class="num">${fmtK(x.tokens || 0)}</td><td><a href="#" data-req="${x.request_id}">#${x.request_id || '-'}</a></td><td class="muted small">${esc(x.top.slice(0, 3).join(', '))}</td></tr>`).join('') + '</table>';
        $$('#eval-out a[data-req]').forEach((a) => a.onclick = (e) => { e.preventDefault(); switchGroup('observability'); switchTab('requests'); setTimeout(() => LW.openRequest && LW.openRequest(parseInt(a.dataset.req, 10)), 300); });
      }
    }));
  }
  $('#btn-eval').onclick = () => runEval(false);
  $('#btn-eval-matrix').onclick = () => runEval(true);
  loaders.eval = loadEvalQuestions;

  // ---------------- FORENSICS ----------------
  function renderForensic(f) {
    if (!f || f.error) return `<div class="banner err">${esc((f || {}).error || 'no data')}</div>`;
    return `<div class="req-head"><b>포렌식 #${f.id || '-'}</b> request #${f.request_id || '-'} · run ${esc(f.run_id || '-')} · 판정 <span class="pill">${esc(f.verdict || '-')}</span> · groundedness ${f.groundedness == null ? '-' : fmt(f.groundedness, 2)}</div><div class="muted small">Q: ${esc(f.query || '')}</div>` +
      '<h3>소견</h3>' + (f.findings || []).map((x) => `<div class="finding ${esc(x.severity)}"><b>${esc(x.stage)}</b> ${esc(x.problem)}${x.evidence ? `<div class="muted small mono">${esc(String(x.evidence).slice(0, 300))}</div>` : ''}${x.source ? ' <span class="pill">' + esc(x.source) + '</span>' : ''}</div>`).join('') +
      '<h3>제안</h3>' + ((f.suggestions || []).map((s) => `<div class="sugg"><span class="pill">${esc(s.kind)}</span> ${esc(s.detail)} <span class="muted">conf ${fmt(s.confidence, 2)}</span></div>`).join('') || '<div class="muted small">없음</div>');
  }
  async function loadForensics() {
    const s = await api('/api/forensics/summary');
    $('#fx-summary').innerHTML = `<div class="stat"><b>${s.n}</b>기록</div>` + Object.keys(s.by_verdict || {}).map((k) => `<div class="stat"><b>${s.by_verdict[k]}</b>${esc(k)}</div>`).join('') + `<div class="stat"><b>${esc(JSON.stringify(s.suggestion_kinds || {}))}</b>제안 종류</div><div class="stat"><b>${esc((s.top_topics || []).slice(0, 6).map((t) => t[0] + '(' + t[1] + ')').join(', '))}</b>주제</div><div class="stat"><b>${esc((s.problem_stages || []).slice(0, 5).map((t) => t[0] + '(' + t[1] + ')').join(', '))}</b>문제 단계</div>`;
    const rows = await api('/api/forensics?limit=60');
    $('#fx-list').innerHTML = '<table><tr><th>#</th><th>시각</th><th>req</th><th>판정</th><th>g</th><th>질의</th><th>소견</th></tr>' + rows.map((r) => `<tr data-id="${r.id}"><td>${r.id}</td><td class="muted small">${dt(r.ts)}</td><td>${r.request_id || '-'}</td><td><span class="pill ${r.verdict === 'sufficient' ? 'ok' : r.verdict === 'weak' ? 'warn' : 'bad'}">${esc(r.verdict)}</span></td><td class="num">${r.groundedness == null ? '-' : fmt(r.groundedness, 2)}</td><td class="small">${esc((r.query || '').slice(0, 50))}</td><td class="num">${(r.findings || []).length}</td></tr>`).join('') + '</table>' + (rows.length ? '' : '<div class="muted">기록 없음</div>');
    $$('#fx-list tr[data-id]').forEach((tr) => tr.onclick = () => { $$('#fx-list tr').forEach((x) => x.classList.remove('sel')); tr.classList.add('sel'); const f = rows.find((x) => String(x.id) === tr.dataset.id); $('#fx-detail').innerHTML = renderForensic(f); });
  }
  $('#btn-fx-refresh').onclick = loadForensics;
  $('#btn-fx-run').onclick = async () => { const id = $('#fx-req').value; if (!id) return; $('#fx-detail').innerHTML = '진단 중…'; $('#fx-detail').innerHTML = renderForensic(await api('/api/forensic?request_id=' + id + '&rerun=1')); loadForensics(); };
  $('#btn-fx-llm').onclick = async () => { const id = $('#fx-req').value || (STATE.lastRequestId || ''); if (!id) { toast('request id 필요'); return; } $('#fx-detail').innerHTML = 'LLM 분석 중…'; const r = await api('/api/forensic/llm', { request_id: parseInt(id, 10) }); if (!r.available) { $('#fx-detail').innerHTML = '<div class="banner warn">forensic 역할 LLM 이 없습니다 (Settings › 모델).</div>'; return; } const merged = Object.assign({}, r.heuristic, { request_id: id, findings: (r.heuristic.findings || []).concat(((r.llm || {}).findings || []).map((f) => Object.assign({ source: 'llm', severity: 'warn' }, f))), suggestions: (r.heuristic.suggestions || []).concat(((r.llm || {}).suggestions || []).map((s) => Object.assign({ source: 'llm' }, s))) }); $('#fx-detail').innerHTML = renderForensic(merged); };
  $('#btn-fx-consolidate').onclick = async () => { const r = await api('/api/memory', { action: 'consolidate' }); toast('consolidate: 제안 ' + (r.proposals || []).length + '건'); };
  loaders.forensics = loadForensics;
  LW.renderForensic = renderForensic;
})(window.LW);
