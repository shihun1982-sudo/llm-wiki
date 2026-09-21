/* Ask — 질의·답변·근거 판정·claim·pin·포렌식 + 채널 디버그. */
(function (LW) {
  'use strict';
  const { $, $$, esc, fmt, fmtK, ts, dt, api, toast, STATE, overrides, presetNames, cliEquiv, updateCli, renderTrace, switchTab, switchGroup, loadStatus, copyText, traceMarkdown, loaders } = LW;

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

  // ---------------- 근거 → 원본 가기 (2026-09-20) ----------------
  // 답변의 근거를 보고도 원문으로 갈 길이 없다는 피드백. 채널 검색 결과는 이미 문서로 가는데
  // 정작 **답변의 근거**와 **그래프 관계**가 안 갔다. 한 곳에 두고 세 자리(근거 카드·그래프 표·포렌식)가 같이 쓴다.
  function entLink(name) {
    const n = String(name == null ? '' : name);
    if (!n) return '<span class="muted">-</span>';
    return `<a href="#" class="go-ent" data-goent="${esc(n)}" title="엔티티 ${esc(n)} 의 관계와 나온 문서를 봅니다">${esc(n)}</a>`;
  }
  function bindEvidenceLinks(sel) {
    $$(`${sel} [data-godoc]`).forEach((a) => a.onclick = (e) => {
      e.preventDefault(); e.stopPropagation();
      const id = a.dataset.godoc;
      if (!id) { toast('문서 id 가 없는 근거입니다'); return; }
      if (LW.openDocPage) LW.openDocPage(id);
      else { switchGroup('knowledge'); switchTab('doc'); }
    });
    $$(`${sel} [data-gochunk]`).forEach((a) => a.onclick = async (e) => {
      e.preventDefault(); e.stopPropagation();
      // 청크는 그 문서 화면에서 연다 — 문단만 따로 보여 주면 앞뒤 맥락을 잃는다.
      const cid = a.dataset.gochunk;
      const docId = (a.closest('.hit') && a.closest('.hit').querySelector('[data-godoc]'))
        ? a.closest('.hit').querySelector('[data-godoc]').dataset.godoc : cid.split('#')[0];
      if (LW.openDocPage) await LW.openDocPage(docId);
      else { switchGroup('knowledge'); switchTab('doc'); }
      setTimeout(() => {
        const det = $$('#doc-out .doc-chunk').find((d) => (d.textContent || '').indexOf(cid) === 0
          || (d.querySelector('code') && d.querySelector('code').textContent === cid));
        if (det) { det.open = true; det.scrollIntoView({ behavior: 'smooth', block: 'center' }); }
        else toast('문서는 열었지만 그 문단을 찾지 못했습니다: ' + cid);
      }, 350);
    });
    $$(`${sel} [data-goent]`).forEach((a) => a.onclick = (e) => {
      e.preventDefault(); e.stopPropagation();
      if (LW.openEntity) LW.openEntity(a.dataset.goent, true);
      else { switchGroup('knowledge'); switchTab('graph'); }
    });
  }
  LW.bindEvidenceLinks = bindEvidenceLinks;
  LW.entLink = entLink;

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
    $('#qa-copy').onclick = () => copyText(j.markdown || '', '분석 리포트');
    $('#qa-bundle-copy').onclick = async (e) => {
      const btn = e.currentTarget; btn.disabled = true; btn.textContent = '묶는 중…';
      try {
        const b = await api('/api/optimize/bundle?request_id=' + s.request_id + '&focus=' + focus + '&format=json');
        if (!b || b.error) { toast('실패: ' + ((b || {}).error || '')); return; }
        await copyText(b.markdown || '', '최적화 자료 묶음');
      } finally { btn.disabled = false; btn.textContent = '묶음 복사'; }
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

  // ---------------- 복사 (요청 16) ----------------
  // 결과 전체를 마크다운 한 장으로: 답변 + REF(LLM 에 실제로 전달된 근거 — refs 가 있으면 그것, 없으면 in_context hits) + 단계 요약.
  // 다른 LLM 이나 게시판·메일에 그대로 붙여 넣는 용도라 표는 GFM, 인용 번호 [C#] 는 그대로 둔다.
  const mdCell = (s) => String(s == null ? '' : s).replace(/\|/g, '\\|').replace(/\s+/g, ' ').trim();
  function refsOf(r) {
    if (Array.isArray(r.refs) && r.refs.length) return r.refs;
    return (r.hits || []).filter((h) => h.in_context).map((h) => ({ n: h.n, chunk_id: h.chunk_id, doc_id: h.doc_id, ext_id: h.ext_id, heading: h.heading, chars: (h.text || '').length, preview: (h.text || '').slice(0, 200) }));
  }
  function resultMarkdown(r, trace) {
    const q = r.query || ($('#q') && $('#q').value) || '';
    const ev = r.evidence || {}, cl = r.claims || {};
    const L = ['# ' + (q || '질의'), '',
      `- request #${r.request_id || '-'} · run ${r.run_id || '-'} · ${r.answer_mode || ''}${r.result_type ? ' · ' + r.result_type : ''}${r.model ? ' · ' + r.model : ''} · ${fmt(r.ms, 0)} ms${r.groundedness != null ? ' · groundedness ' + fmt(r.groundedness, 2) : ''}${r.cached ? ' · cached' : ''}`];
    if (ev.verdict) L.push(`- 근거 판정: ${ev.verdict}${ev.score != null ? ' (score ' + fmt(ev.score, 2) + ')' : ''}${(ev.reasons || []).length ? ' — ' + ev.reasons.join('; ') : ''}`);
    if (r.claims) L.push(`- claim 검증: 사실문장 ${cl.n_factual} · supported ${cl.supported} · partial ${cl.partial} · unsupported ${cl.unsupported}${cl.bad_citations ? ` · **없는 인용 ${cl.bad_citations}건**(모델이 지어낸 [C#])` : ''} · citation precision ${fmt(cl.citation_precision, 2)}`);
    L.push('', '## 답변', '', r.answer || '', '');
    const refs = refsOf(r);
    L.push(`## REF — 컨텍스트 근거 ${refs.length}건`, '', '| n | doc_id | ext_id | heading | chunk_id | chars |', '|---|---|---|---|---|---:|');
    refs.forEach((x) => L.push(`| C${x.n == null ? '' : x.n} | ${mdCell(x.doc_id)} | ${mdCell(x.ext_id)} | ${mdCell(x.heading)} | ${mdCell(x.chunk_id)} | ${x.chars == null ? '' : x.chars} |`));
    L.push('', '## 단계 요약', '', traceMarkdown(trace), '', `> CLI: \`${r.cli || cliEquiv('query', q)}\``);
    return L.join('\n');
  }
  // 근거 문단 목록: 표(전부) + 컨텍스트에 들어간 문단의 본문
  function hitsMarkdown(r) {
    const hits = r.hits || [];
    const L = [`## 근거 문단 ${hits.length}건 (컨텍스트 ${hits.filter((h) => h.in_context).length})`, '',
      '| # | doc_id | 유형 · ID | 날짜 | heading | 채널 | fused | rerank |', '|---|---|---|---|---|---|---:|---:|'];
    hits.forEach((h) => L.push(`| ${h.in_context ? 'C' + h.n : '제외'} | ${mdCell(h.doc_id)} | ${mdCell((h.doc_type || '-') + (h.ext_id ? ' ' + h.ext_id : ''))} | ${mdCell(h.date)} | ${mdCell(h.heading)} | ${mdCell((h.why || []).join(' '))} | ${fmt(h.fused, 4)} | ${h.rerank == null ? '-' : fmt(h.rerank, 3)} |`));
    hits.filter((h) => h.in_context).forEach((h) => L.push('', `### [C${h.n}] ${h.doc_id}${h.heading ? ' — ' + h.heading : ''}`, '', h.text || ''));
    return L.join('\n');
  }
  if ($('#btn-q-copy')) $('#btn-q-copy').onclick = () => {
    const r = STATE.lastResult; if (!r) { toast('먼저 질의를 실행하세요'); return; }
    copyText(resultMarkdown(r, STATE.lastTrace), '결과 전체 (markdown)');
  };
  if ($('#btn-hits-copy')) $('#btn-hits-copy').onclick = () => {
    const r = STATE.lastResult; if (!r || !(r.hits || []).length) { toast('복사할 근거가 없습니다 — 먼저 질의를 실행하세요'); return; }
    copyText(hitsMarkdown(r), '근거 문단 (markdown)');
  };

  function verdictPill(v) { return `<span class="pill ${v === 'sufficient' ? 'ok' : v === 'weak' ? 'warn' : 'bad'}">${esc(v || '-')}</span>`; }
  function boostChips(b) { return Object.keys(b || {}).map((k) => `<span class="boost" title="${k}">${k} ×${fmt(b[k], 2)}</span>`).join(''); }

  // 출력 모드 fused|reranked: 후보 표 (행을 누르면 본문 펼침) + "⧉ 후보 복사" (r.answer = 같은 표의 마크다운). stages 로 리랭크 입력 전후를 비교한다.
  function renderCandidates(r) {
    const cands = r.candidates || [], st = r.stages || {};
    const before = st.rerank_before || [], after = st.final_order || [];
    const posBefore = {}; before.forEach((id, i) => { posBefore[id] = i + 1; });
    const isRerank = r.output_mode === 'reranked';
    const movedNote = isRerank ? ` · 리랭크로 순서가 바뀐 후보 ${after.filter((id, i) => before[i] !== id).length}/${Math.min(before.length, after.length)}` : '';
    const injectNote = st.inject && Object.keys(st.inject).length ? ' · 주입(channel_inject): ' + Object.keys(st.inject).map((k) => `${k} ${st.inject[k].length}`).join(', ') : '';
    let html = `<div class="row"><b>${isRerank ? '리랭크 후보' : '융합·부스트 후보 (리랭크 전)'} ${cands.length}건</b> <button class="mini secondary" id="cand-copy" title="후보 표를 마크다운으로 복사합니다 (CLI/MCP 의 본문과 같은 표)">⧉ 후보 복사</button> <button class="mini secondary" id="cand-json" title="candidates/lists/stages 를 JSON 으로 복사합니다">JSON 복사</button> <span class="muted small">채널 리스트 ${Object.keys(r.lists || {}).length}개${movedNote}${injectNote}</span></div>` +
      '<table class="small"><tr><th>#</th>' + (isRerank ? '<th title="리랭크 입력 순서(rerank_before)에서의 위치">입력#</th>' : '') + '<th>chunk_id</th><th>문서 · ID</th><th>heading</th><th class="num">fused</th><th class="num">rerank</th><th>채널(why)</th><th>boosts</th></tr>' +
      cands.map((c, i) => `<tr class="cand-row" data-i="${i}" title="누르면 본문을 펼칩니다"><td class="num">${c.rank || i + 1}</td>` +
        (isRerank ? `<td class="num muted">${posBefore[c.chunk_id] || '-'}</td>` : '') +
        `<td><code>${esc(c.chunk_id)}</code></td><td class="small">${esc(c.ext_id || '')}${c.ext_id && c.doc_id ? ' · ' : ''}${esc((c.doc_id || '').split('/').pop())}</td><td class="small">${esc((c.heading || '').slice(0, 60))}</td>` +
        `<td class="num">${fmt(c.fused, 4)}</td><td class="num">${c.rerank == null ? '-' : fmt(c.rerank, 3)}</td><td>${(c.why || []).map((w) => `<span class="src ${w.split('#')[0].split(':')[0]}">${esc(w)}</span>`).join('')}</td><td>${boostChips(c.boosts)}</td></tr>` +
        `<tr class="cand-text hidden" data-for="${i}"><td colspan="${isRerank ? 9 : 8}"><div class="t"><span class="muted small">scores ${esc(JSON.stringify(c.scores || {}))} · ranks ${esc(JSON.stringify(c.ranks || {}))}${c.chars ? ' · ' + c.chars + ' 자' : ''}</span><pre class="pre">${esc(c.text || '')}</pre></div></td></tr>`).join('') + '</table>';
    if (r.lists && Object.keys(r.lists).length) {
      html += `<details><summary class="muted small">채널별 리스트 (상위 output_list_n 개) — ${Object.keys(r.lists).map((k) => k + ' ' + r.lists[k].length).join(' · ')}</summary><div class="cols">` +
        Object.keys(r.lists).map((k) => `<div class="col small"><b>${esc(k)}</b><table class="small">` + r.lists[k].map((x, i) => `<tr><td class="num">${i + 1}</td><td><code>${esc(x[0])}</code></td><td class="num">${fmt(x[1], 4)}</td></tr>`).join('') + '</table></div>').join('') + '</div></details>';
    }
    if (isRerank && before.length) {
      html += `<details><summary class="muted small">리랭크 입력 순서 (rerank_before ${before.length}개) → 출력 순서 (final_order ${after.length}개)</summary><table class="small"><tr><th>#</th><th>입력</th><th>출력</th></tr>` +
        before.map((id, i) => `<tr><td class="num">${i + 1}</td><td><code>${esc(id)}</code></td><td><code>${esc(after[i] || '')}</code>${after[i] && after[i] !== id ? ' <span class="pill warn">↕</span>' : ''}</td></tr>`).join('') + '</table></details>';
    }
    $('#answer').innerHTML = html;
    $$('#answer .cand-row').forEach((tr) => tr.onclick = () => { const t = $(`#answer .cand-text[data-for="${tr.dataset.i}"]`); if (t) t.classList.toggle('hidden'); });
    $('#cand-copy').onclick = () => copyText(r.answer || '', '후보 표 (markdown)');
    $('#cand-json').onclick = () => copyText(JSON.stringify({ query: r.query, output_mode: r.output_mode, candidates: cands, lists: r.lists || {}, stages: st }, null, 1), '후보 JSON');
  }

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
      renderResult(j.result, j.trace, q);
      loadMyRequests();
    } finally { $('#btn-query').disabled = false; }
  }

  // 질의 결과를 화면에 그린다. 방금 실행한 결과와 **지난 요청에서 불러온 결과**가 같은 함수를 쓴다
  // (예전에는 runQuery 안에 인라인이라 지난 결과를 그대로 다시 볼 방법이 없었다 — 2026-09-16).
  function renderResult(r, trace, q) {
    {
      STATE.lastQueryId = r.query_id || null; STATE.lastRequestId = r.request_id || null; STATE.lastResult = r; STATE.lastTrace = trace || null;
      q = q || r.query || '';
      $('#query-out').classList.remove('hidden'); $('#q-forensic').classList.add('hidden'); $('#q-analysis').classList.add('hidden');
      if (r.analysis && r.analysis.md) { $('#q-analysis').classList.remove('hidden'); $('#q-analysis').innerHTML = `<div class="banner"><b>📊 analysis_mode</b> — 리포트 저장됨: <code>${esc(r.analysis.md)}</code> · ` + ['quality', 'speed', 'tokens'].map((l) => (((r.analysis.top || {})[l] || [])[0] ? `${{ quality: '품질', speed: '속도', tokens: '토큰' }[l]}: ${esc(((r.analysis.top || {})[l] || [])[0].title)}` : '')).filter(Boolean).join(' · ') + ' · <a href="#" id="qa-open">전문 보기</a></div>'; const o = $('#qa-open'); if (o) o.onclick = (e) => { e.preventDefault(); $('#btn-q-analysis').click(); }; }
      // 배지: answer_mode(llm|extractive|insufficient|candidates|context) · result_type(grounded|best_effort|…|candidates_fused|candidates_reranked|context) · 모델
      $('#answer-mode').textContent = r.answer_mode + (r.result_type && r.result_type !== r.answer_mode ? ' · ' + r.result_type : '') + (r.model ? ' · ' + r.model : '');
      $('#answer-ms').textContent = fmt(r.ms) + ' ms';
      $('#answer-cached').classList.toggle('hidden', !(r.cached || r.precomputed)); $('#answer-cached').textContent = r.precomputed ? 'precomputed' : 'cached';
      const g = r.groundedness; $('#answer-ground').textContent = g == null ? '' : 'groundedness ' + fmt(g, 2); $('#answer-ground').className = 'pill ' + (g == null ? '' : g >= 0.8 ? 'ok' : g >= 0.5 ? 'warn' : 'bad');
      const tk = r.tokens || {};
      const ev = r.evidence || {};
      $('#q-stats').innerHTML = `<div class="stat"><b>${fmt(r.ms, 0)}</b>ms</div><div class="stat"><b>${tk.calls || 0}</b>LLM 호출</div><div class="stat"><b>${fmtK(tk.total_tokens || 0)}</b>토큰</div><div class="stat"><b>${trace && trace.summary ? trace.summary.sql_statements : '-'}</b>SQL</div><div class="stat"><b>${r.hits.filter((h) => h.in_context).length}/${r.hits.length}</b>컨텍스트 청크</div><div class="stat"><b>${(r.fallback || []).length}</b>fallback</div><div class="stat"><b>#${r.request_id || '-'}</b>request</div><div class="stat"><b>${esc(r.run_id || '-')}</b>run</div>`;
      const ban = $('#q-banner');
      const omode = r.output_mode || 'answer';
      if (omode !== 'answer') {
        // 출력 모드(fused|reranked|context): 답변을 만들지 않았다 — 무엇이 들어 있는지 한 줄로
        const what = { fused: '융합·부스트 뒤(리랭크 전) 후보 표', reranked: '리랭크 뒤 후보 표 (리랭크 입력 전후 순서는 stages 에)', context: '컨텍스트([C#] 블록) 본문 — 답변 LLM 은 부르지 않았습니다' }[omode] || omode;
        ban.className = 'banner'; ban.innerHTML = `<b>출력 모드 ${esc(omode)}</b> — ${esc(what)}. 답변으로 돌아가려면 위 "출력" 드롭다운을 "답변" 으로.${omode !== 'context' && ev.verdict == null ? ' 근거 판정·claim 검증·자가진화 기록·캐시 저장은 하지 않았습니다.' : ''}`; ban.classList.remove('hidden');
      }
      else if (r.answer_mode === 'insufficient') { ban.className = 'banner err'; ban.innerHTML = `<b>근거 부족(insufficient)</b> — ${esc((ev.reasons || []).join('; '))}. fallback ${(r.fallback || []).length}회 시도. <a href="#" id="q-fx-link">포렌식 보기</a>`; ban.classList.remove('hidden'); }
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
      if (Array.isArray(r.candidates)) renderCandidates(r);
      else {
        $('#answer').innerHTML = esc(r.answer).replace(/\[C(\d+)\]/g, (m, n) => `<span class="cite" data-n="${n}">[C${n}]</span>`);
        if (omode === 'context') {
          // 컨텍스트만: 본문 복사 버튼 + REF 목록 (refs = 컨텍스트에 실제로 들어간 근거)
          $('#answer').innerHTML = `<div class="row"><button class="mini secondary" id="ctx-copy" title="컨텍스트 본문([C#] 블록)을 그대로 복사합니다 — 다른 LLM 에 붙여 넣을 수 있습니다">⧉ 컨텍스트 복사</button> <span class="muted small">${fmtK(((r.context || {}).chars) || (r.answer || '').length)} 자 · REF ${(r.refs || []).length}건</span></div><pre class="pre">${$('#answer').innerHTML}</pre>`;
          $('#ctx-copy').onclick = () => copyText(r.answer || '', '컨텍스트');
        }
      }
      $$('#answer .cite').forEach((c) => c.onclick = () => { const h = $(`#hit-${c.dataset.n}`); if (h) { h.classList.add('open'); h.scrollIntoView({ behavior: 'smooth', block: 'center' }); } });
      $('#fb-result').textContent = ''; $('#fb-note').value = '';
      // evidence / claims / plan
      const plan = r.plan || {}; const cl = r.claims || {};
      const qr = plan.query_rules || {};
      const lines = [];
      lines.push(`근거 판정: ${verdictPill(ev.verdict)} ${ev.score != null ? 'score ' + fmt(ev.score, 2) : ''} ${(ev.reasons || []).map(esc).join(' · ')}${ev.llm ? ' · LLM: ' + esc(ev.llm.verdict) + (ev.llm.missing && ev.llm.missing.length ? ' 부족=' + esc(ev.llm.missing.join('; ')) : '') : ''}`);
      if (r.fallback && r.fallback.length) lines.push('fallback: ' + r.fallback.map((f) => `${esc(f.level)}→${esc(f.verdict)}${f.improved ? '↑' : ''}${f.stop_reason ? ' (' + esc(f.stop_reason) + ')' : ''}`).join(' , '));
      if (r.claims) lines.push(`claim 검증: 사실문장 ${cl.n_factual} · supported ${cl.supported} · partial ${cl.partial} · unsupported ${cl.unsupported}${cl.bad_citations ? ` · 없는 인용 ${cl.bad_citations}건` : ''} · citation precision ${fmt(cl.citation_precision, 2)}${cl.refined ? ' · 재작성됨' : ''}${cl.policy_applied ? ' · 정책 적용 ' + cl.policy_applied : ''}`);
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
      // 근거 문단 — 문서 경로와 chunk 를 **원본으로 가는 링크**로 (2026-09-20).
      // 답변의 근거인데 정작 원문으로 갈 길이 없었다 (채널 검색 결과는 이미 되는데).
      $('#hits').innerHTML = r.hits.map((h) => {
        const docLink = h.doc_id
          ? `<a href="#" class="go-doc" data-godoc="${esc(h.doc_id)}" title="이 문서를 지식 › 문서 에서 엽니다">${esc(h.doc_id)}</a>`
          : `<span class="muted" title="문서 id 가 없는 근거입니다 (외부 RAG 결과 등) — 원문으로 갈 수 없습니다">(문서 없음)</span>`;
        const chunkLink = h.chunk_id
          ? ` <a href="#" class="go-chunk mono small" data-gochunk="${esc(h.chunk_id)}" title="이 문단의 원문을 그 문서 화면에서 엽니다">${esc(h.chunk_id)}</a>` : '';
        return `<div class="hit ${h.in_context ? '' : 'excluded'}" id="hit-${h.n || 'x'}"><div class="h">`
          + `<b>${h.in_context ? '[C' + h.n + ']' : '[제외]'}</b> ${docLink}`
          + ` <span class="pill">${esc(h.doc_type || '-')}${h.ext_id ? ' ' + esc(h.ext_id) : ''}${h.date ? ' · ' + esc(h.date) : ''}</span>`
          + ` <span class="muted">${esc(h.heading)}</span>${chunkLink} `
          + (h.why || []).map((w) => `<span class="src ${w.split('#')[0]}">${esc(w)}</span>`).join('') + boostChips(h.boosts)
          + ` <span class="muted">fused=${fmt(h.fused, 4)} rerank=${h.rerank == null ? '-' : fmt(h.rerank, 3)}</span>`
          + ` <button class="mini secondary" data-pin="${esc(h.chunk_id)}" title="이 질의에 이 근거를 고정">📌</button></div>`
          + `<div class="t">${esc(h.text)}</div></div>`;
      }).join('');
      $$('#hits .hit .t').forEach((t) => t.onclick = () => t.parentElement.classList.toggle('open'));
      $$('#hits [data-pin]').forEach((b) => b.onclick = async (e) => { e.stopPropagation(); const p = await api('/api/pins', { action: 'add', chunk: b.dataset.pin, query: q, note: 'from query' }); toast('pin 추가: ' + p.id); });
      bindEvidenceLinks('#hits');
      // 단계별 ⟲: 이 요청의 중간 결과로 그 단계부터 다시 실행 → 결과가 오면 이 화면을 그대로 다시 그린다
      renderTrace($('#trace'), trace, {
        rerun: r.request_id,
        onRerun: (j) => { LW.renderResult(j.result, j.trace, (j.result || {}).query || q); },
      });
      const rr = r.rerun_from ? `<span class="pill ok">⟲ 재실행: ${esc(r.rerun_from)} 부터 (원 요청 #${esc(String(r.rerun_of))})</span>` : '';
      if ($('#q-rerun-note')) $('#q-rerun-note').innerHTML = rr;
      // 그래프 관계 — src/dst 를 **엔티티 화면 링크**로 (2026-09-20). `CL-55303 fixes ISSUE-2003` 를
      // 보고도 그 문서/엔티티로 갈 길이 없었다. 엔티티 화면에는 그 엔티티가 나온 문서 참조가 있다.
      const rels = ((r.graph || {}).relations || []).slice(0, 15);
      $('#graph-rels').innerHTML = rels.length
        ? '<table><tr><th>src</th><th>rel</th><th>dst</th><th>출처</th><th>gain</th><th>근거</th></tr>'
          + rels.map((x) => `<tr><td>${entLink(x.src)}</td><td><code>${esc(x.rel)}</code></td><td>${entLink(x.dst)}</td>`
            + `<td class="muted">${esc(x.provenance || '')}</td><td class="num">${fmt(x.gain, 3)}</td>`
            + `<td class="muted">${esc((x.description || '').slice(0, 60))}</td></tr>`).join('')
          + '</table><div class="muted small">이름을 누르면 그 엔티티 화면으로 갑니다 — 관계와 <b>나온 문서</b> 를 거기서 봅니다.</div>'
        : '<span class="muted">(graph off 또는 시드 없음)</span>';
      bindEvidenceLinks('#graph-rels');
      $('#q-proposals').textContent = (r.proposals && r.proposals.length) ? '생성된 제안 id: ' + r.proposals.join(', ') + ' → Evolve 에서 검토' : '새 제안 없음';
      $('#cli-equiv').textContent = r.cli || cliEquiv('query', q);
      loadStatus();
    }
  }
  LW.renderResult = renderResult;

  // ---------------- 내 지난 요청 ----------------
  // 한 줄을 누르면 저장해 둔 결과로 위 화면을 그대로 다시 그린다 (질의를 **다시 실행하지 않는다**).
  async function loadMyRequests() {
    const box = $('#myreq-list'); if (!box) return;
    const all = $('#myreq-all') && $('#myreq-all').checked;
    const qs = new URLSearchParams({ scope: all ? 'all' : 'mine', limit: '40' });
    if ($('#myreq-kind') && $('#myreq-kind').value) qs.set('kind', $('#myreq-kind').value);
    if ($('#myreq-q') && $('#myreq-q').value.trim()) qs.set('q', $('#myreq-q').value.trim());
    const j = await api('/api/requests?' + qs.toString());
    const rows = (j && j.rows) || [], live = (j && j.live) || [];
    const msg = $('#myreq-msg');
    if (msg) msg.textContent = j && j.can_all === false && all ? '전체 조회 권한이 없어 내 요청만 보입니다' : '';
    const cnt = $('#myreq-count'); if (cnt) cnt.textContent = `(${live.length ? live.length + ' 진행 중 · ' : ''}${rows.length}건${j && j.me ? ' · ' + j.me : ''})`;
    const stat = (s) => s === 'running' ? '<span class="pill warn">진행 중</span>' : s === 'queued' ? '<span class="pill">대기</span>'
      : s === 'error' ? '<span class="pill bad">오류</span>' : '<span class="pill ok">완료</span>';
    box.innerHTML = '<table><tr><th></th><th>시각</th><th>종류</th><th>요약</th><th>ms</th>' + (all ? '<th>사용자</th>' : '') + '<th></th></tr>' +
      live.map((a) => `<tr class="live-row"><td>${stat(a.status)}</td><td class="muted small">${a.ts ? ts(a.ts) : '-'}</td><td class="small">${esc(a.kind || '')}</td>` +
        `<td class="small">${esc(String(a.summary || '').slice(0, 70))}${a.stage ? ' <span class="muted">· ' + esc(a.stage) + '</span>' : ''}</td>` +
        `<td class="num">${fmt(a.ms, 0)}</td>${all ? '<td class="small muted">' + esc(a.user || '') + '</td>' : ''}<td></td></tr>`).join('') +
      rows.map((r) => `<tr data-req="${r.id}"><td>${stat(r.status)}</td><td class="muted small">${ts(r.ts)}</td><td class="small">${esc(r.kind)}</td>` +
        `<td class="small">${esc(String(r.summary || '').slice(0, 70))}</td><td class="num">${fmt(r.ms, 0)}</td>` +
        `${all ? '<td class="small muted">' + esc(r.user || '') + '</td>' : ''}` +
        `<td>${r.file ? '<span class="pill" title="결과가 파일로 보관되어 있습니다: ' + esc(r.file) + '">보관됨</span>' : ''}</td></tr>`).join('') +
      '</table>' + (rows.length || live.length ? '' : '<div class="muted small">아직 기록이 없습니다.</div>');
    $$('#myreq-list tr[data-req]').forEach((tr) => tr.onclick = () => openPastRequest(parseInt(tr.dataset.req, 10)));
  }
  LW.loadMyRequests = loadMyRequests;
  // 다른 화면(진행 중 작업의 완료 항목 등)에서도 "그때 그 결과" 를 같은 방식으로 열 수 있게 공개한다.
  // 저장된 결과는 근거 전문(hits)을 담지 않으므로 복원 처리가 필요하다 — openPastRequest 가 그걸 안다.
  LW.openPastRequest = (id) => openPastRequest(id);

  async function openPastRequest(id) {
    const r = await api('/api/request?id=' + id);
    if (!r || r.error) { toast('요청 #' + id + ' 을 찾을 수 없습니다' + (r && r.error ? ': ' + r.error : '')); return; }
    if (r.kind !== 'query' || !r.result) {
      // 질의가 아니면(빌드·평가 등) 관측 탭의 요청 프로파일로 보낸다
      LW.switchGroup('observability'); LW.switchTab('requests');
      setTimeout(() => LW.openRequest && LW.openRequest(id), 300);
      return;
    }
    const res = r.result || {};
    // 저장된 결과에는 hits 전문 대신 hits_brief 가 있다 — 화면이 비지 않게 최소 형태로 채운다
    if (!res.hits) res.hits = (res.hits_brief || []).map((h) => Object.assign({ doc_id: '(저장된 요약)', heading: '', text: '(근거 전문은 보관하지 않습니다 — 같은 질의를 다시 실행하면 볼 수 있습니다)', in_context: h.in_context }, h));
    $('#query-out').classList.remove('hidden');
    renderResult(res, r.trace || {}, res.query || r.summary);
    const ban = $('#q-banner');
    ban.className = 'banner'; ban.classList.remove('hidden');
    ban.innerHTML = `🕘 <b>지난 요청 #${r.id}</b> 의 결과입니다 (${dt(r.ts)}${r.user ? ' · ' + esc(r.user) : ''}${r.from_archive ? ' · 보관 파일에서 복원' : ''}). ` +
      '다시 실행한 것이 아니라 <b>그때 저장된 답</b>을 그대로 보여 줍니다. <a href="#" id="myreq-rerun">같은 질의 다시 실행</a>';
    const rr = $('#myreq-rerun');
    if (rr) rr.onclick = (e) => { e.preventDefault(); $('#q').value = res.query || r.summary || ''; runQuery(); };
    $('#query-out').scrollIntoView({ behavior: 'smooth', block: 'start' });
  }
  if ($('#btn-myreq-refresh')) $('#btn-myreq-refresh').onclick = loadMyRequests;
  if ($('#myreq-all')) $('#myreq-all').onchange = loadMyRequests;
  if ($('#myreq-kind')) $('#myreq-kind').onchange = loadMyRequests;
  if ($('#myreq-q')) $('#myreq-q').onkeydown = (e) => { if (e.key === 'Enter') loadMyRequests(); };
  if ($('#myreq-box')) $('#myreq-box').addEventListener('toggle', () => { if ($('#myreq-box').open) loadMyRequests(); });

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
    const unres = (rep.expected || {}).unresolved || [];
    let html = `<div class="req-head"><b>기대 결과 포렌식</b> request #${rep.request_id} · 원 판정 <span class="pill">${esc(rep.verdict || '-')}</span> · 재실행 ${rep.rerun ? rep.rerun_rounds + ' 라운드 (' + esc(rep.rerun_verdict || '') + ')' : '없음'}${rep.forensic_id ? ' · forensics #' + rep.forensic_id : ''}</div>` +
      `<div class="muted small">Q: ${esc(rep.query || '')} · 기대 docs=${esc(JSON.stringify((rep.expected || {}).docs ? Object.keys(rep.expected.docs) : []))} terms=${esc(JSON.stringify((rep.expected || {}).terms || []))}</div>`;
    // "원 판정 sufficient" 와 "미해결" 이 나란히 있으면 모순처럼 읽힌다 — 무슨 뜻인지 한 문장으로 붙인다.
    if (unres.length) {
      html += `<div class="banner warn"><b>색인에서 못 찾은 기대 항목:</b> ${esc(unres.join(', '))} — ` +
        '이 ID/청크가 <b>코퍼스에 없거나 표기가 다르다</b>는 뜻이다(답변 품질과는 다른 문제). ' +
        '문서를 넣거나, ID 표기를 <code>data/rules.json</code> 의 <code>id_patterns</code> 와 맞춘다.</div>';
    }
    html += '<ul class="small" style="margin:6px 0 6px 16px">' + (rep.summary || []).map((s) => `<li>${esc(s)}</li>`).join('') + '</ul>';
    // 목표 청크가 수십 개일 수 있다 (용어만 준 경우). 가장 멀리 간 것부터 몇 개만 바로 보이고 나머지는 접는다.
    const targets = rep.targets || [], shown = Math.max(1, rep.targets_shown || 3);
    const targetHtml = (t) => `<details ${t.chunk_id === rep.best_target ? 'open' : ''}><summary><b>${esc(t.chunk_id)}</b> <span class="muted small">${esc((t.heading || '').slice(0, 60))} · ${esc(t.why || '')}</span> · 원 결과 <span class="pill ${t.original === 'cited' ? 'ok' : t.original === 'candidate' ? 'warn' : 'bad'}">${esc(t.original)}</span> → 탈락 <span class="pill ${t.lost_at === 'none' ? 'ok' : 'bad'}">${esc(t.lost_at)}</span></summary>` +
      '<table class="small"><tr><th></th><th>단계</th><th>순위</th><th>상세</th></tr>' + (t.journey || []).map((j) => `<tr><td>${stageMark(j.status)}</td><td>${esc(j.stage)}</td><td class="num">${j.rank || ''}</td><td>${esc(j.detail || '')}</td></tr>`).join('') + '</table></details>';
    html += targets.slice(0, shown).map(targetHtml).join('');
    if (targets.length > shown) {
      html += `<details><summary class="muted small">나머지 목표 청크 ${targets.length - shown}개 (같은 형식의 탈락 단계 표)</summary>` +
        targets.slice(shown).map(targetHtml).join('') + '</details>';
    }
    // 수정안은 서버가 confidence 내림차순으로 준다. 임계 미만은 버리지 않고 접어 둔다.
    const sug = rep.suggestions || [], strong = sug.filter((s) => !s.low_confidence), weak = sug.filter((s) => s.low_confidence);
    const sugHtml = (s) => `<div class="sugg"><span class="pill">${esc(s.kind)}</span> ${esc(s.detail)} <span class="muted">conf ${fmt(s.confidence, 2)}</span></div>`;
    html += '<h4 style="margin:8px 0 4px">수정안 <span class="muted small">확신이 큰 순서</span></h4>' +
      (strong.map(sugHtml).join('') || (weak.length ? '' : '<div class="muted small">없음</div>'));
    if (weak.length) {
      html += `<details><summary class="muted small">확신이 낮은 수정안 ${weak.length}건 (conf &lt; ${fmt(rep.suggestion_min_confidence, 2)} — 근거가 약하니 먼저 위의 것부터)</summary>` +
        weak.map(sugHtml).join('') + '</details>';
    }
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

  // ---------------- SEARCH DEBUG (채널 검색) ----------------
  // 채널을 체크박스로 고르고 OR/AND/RRF 로 조합한다. 서버는 CLI `search` · MCP wiki_search 와
  // **같은 엔진**(retrieval.channel_search)을 쓰므로 세 창구의 결과가 같다.
  // 각 행에 "어느 채널이 몇 위로 찾았는지" 를 함께 보여 주는 것이 이 화면의 핵심이다 —
  // 예전에는 한 채널만 볼 수 있어서 "FTS 는 찾았는데 벡터는 못 찾았나" 를 눈으로 맞춰야 했다.
  const S_CHANNELS = ['fts', 'vector', 'graph'];
  // 채널마다 require(필수·AND) · any(포함·OR) · exclude(제외·NOT) · off 중 하나
  function chCond(ch) { const el = document.querySelector(`input[name="ch-${ch}"]:checked`); return (el && el.value) || 'off'; }
  function setChCond(ch, v) { const el = document.querySelector(`input[name="ch-${ch}"][value="${v}"]`); if (el) el.checked = true; }
  function searchCond() {
    const c = { require: [], any: [], exclude: [] };
    S_CHANNELS.forEach((ch) => { const v = chCond(ch); if (v !== 'off') c[v === 'require' ? 'require' : v === 'exclude' ? 'exclude' : 'any'].push(ch); });
    return c;
  }
  function searchMode() { const b = document.querySelector('#s-mode button.active'); return (b && b.dataset.m) || 'or'; }

  // ---- 문서 유형 필터 · 최근 검색어 (2026-09-20) ----
  // 유형 목록은 서버가 준다(/api/status 의 doc_types) — 코퍼스마다 다르므로 화면에 박아 두지 않는다.
  let S_TYPES = [];                 // 고른 유형 (빈 배열 = 전체)
  function renderTypes(counts) {
    const box = $('#s-types');
    if (!box) return;
    const rows = Object.keys(counts || {}).filter((t) => t).sort();
    if (!rows.length) { box.innerHTML = '<span class="muted small">(문서 유형 정보 없음)</span>'; return; }
    box.innerHTML = `<button type="button" data-t="" class="${S_TYPES.length ? '' : 'active'}" title="모든 유형">전체</button>`
      + rows.map((t) => `<button type="button" data-t="${esc(t)}" class="${S_TYPES.includes(t) ? 'active' : ''}" title="${esc(t)} 문서 ${counts[t]}개">${esc(t)} <span class="muted">${counts[t]}</span></button>`).join('');
    $$('#s-types button').forEach((b) => b.onclick = () => {
      const t = b.dataset.t;
      if (!t) S_TYPES = [];                                  // '전체'
      else if (S_TYPES.includes(t)) S_TYPES = S_TYPES.filter((x) => x !== t);
      else S_TYPES = S_TYPES.concat([t]);                    // 여러 개 고를 수 있다
      renderTypes(counts);
      if ($('#s-q').value.trim()) $('#btn-search').click();
    });
  }
  async function loadSearchTypes() {
    if ($('#s-types') && $('#s-types').dataset.loaded) return;
    try {
      const st = await api('/api/status');
      const counts = (st && (st.doc_types || (st.stats || {}).doc_types)) || {};
      renderTypes(counts);
      if ($('#s-types')) $('#s-types').dataset.loaded = '1';
    } catch (e) { /* 유형 정보가 없어도 검색은 된다 */ }
  }

  const S_RECENT_KEY = 'lw.search.recent';
  function recentGet() {
    try { return JSON.parse(localStorage.getItem(S_RECENT_KEY) || '[]').slice(0, 8); } catch (e) { return []; }
  }
  function recentPut(q) {
    try {
      const cur = recentGet().filter((x) => x !== q);
      localStorage.setItem(S_RECENT_KEY, JSON.stringify([q].concat(cur).slice(0, 8)));
    } catch (e) { /* 사생활 보호 모드 등 — 없어도 그만 */ }
    renderRecent();
  }
  function renderRecent() {
    const box = $('#s-recent'); if (!box) return;
    const rs = recentGet();
    box.innerHTML = rs.map((q) => `<span class="pin-chip" data-rq="${esc(q)}" title="다시 검색">${esc(q.slice(0, 28))}</span>`).join('')
      || '<span class="muted small">(없음)</span>';
    $$('#s-recent [data-rq]').forEach((c) => c.onclick = () => { $('#s-q').value = c.dataset.rq; $('#btn-search').click(); });
  }

  // 발췌에서 찾은 말을 굵게 — 결과가 왜 걸렸는지 눈으로 바로 보이게 한다.
  function hilite(text, terms) {
    let out = esc(String(text || ''));
    (terms || []).filter((t) => t && t.length > 1).slice(0, 12).forEach((t) => {
      try {
        out = out.replace(new RegExp('(' + t.replace(/[.*+?^${}()|[\]\\]/g, '\\$&') + ')', 'gi'), '<mark>$1</mark>');
      } catch (e) { /* 이상한 문자는 그냥 넘어간다 */ }
    });
    return out;
  }
  function condExpr() {
    const c = searchCond();
    const parts = [];
    if (c.any.length) parts.push(c.any.length > 1 ? '(' + c.any.join(' 또는 ') + ')' : c.any[0]);
    if (c.require.length) parts.push(c.require.join(' 그리고 '));
    let s = parts.join(' 그리고 ') || '(채널 없음)';
    if (c.exclude.length) s += ' 제외 ' + c.exclude.join(', ');
    if (!c.require.length && !c.exclude.length && c.any.length > 1) s += searchMode() === 'rrf' ? ' [가중 RRF]' : '';
    return s;
  }
  function syncExpr() { const e = $('#s-expr'); if (e) e.textContent = condExpr(); }
  $$('#s-mode button').forEach((b) => b.onclick = () => {
    $$('#s-mode button').forEach((x) => x.classList.toggle('active', x === b));
    // 빠른 설정: 세 채널을 한 번에 같은 조건으로
    if (b.dataset.m === 'and') S_CHANNELS.forEach((ch) => setChCond(ch, 'require'));
    else S_CHANNELS.forEach((ch) => setChCond(ch, 'any'));
    syncExpr();
    if ($('#s-q').value.trim()) $('#btn-search').click();
  });
  S_CHANNELS.forEach((ch) => $$(`input[name="ch-${ch}"]`).forEach((r) => r.onchange = () => {
    const c = searchCond();
    if (!c.require.length && !c.any.length) { setChCond(ch, 'any'); toast('채널을 하나 이상 필수 또는 포함으로 두어야 합니다'); }
    syncExpr();
    if ($('#s-q').value.trim()) $('#btn-search').click();
  }));
  syncExpr();
  function chBadges(chmap) {
    return Object.keys(chmap).sort().map((c) => `<span class="chip ch-${esc(c)}" title="${esc(c)} 채널이 ${chmap[c].rank}위로 찾음 (점수 ${chmap[c].score})">${esc(c)} #${chmap[c].rank}</span>`).join('');
  }
  $('#btn-search').onclick = async () => {
    const q = $('#s-q').value.trim(); if (!q) return;
    const cond = searchCond(), mode = searchMode();
    if (!cond.any.length && !cond.require.length) { toast('채널을 하나 이상 고르세요'); return; }
    $('#search-out').innerHTML = '<div class="muted small">검색 중…</div>';
    recentPut(q);
    const j = await api('/api/search', { q, channels: cond.any, require: cond.require, exclude: cond.exclude,
      mode: mode, k: parseInt($('#s-k').value, 10), doc_types: S_TYPES, overrides: overrides() });
    $('#cli-equiv').textContent = j.cli || '';
    const r = j.result || {};
    if (r.error || !r.rows) { $('#search-out').innerHTML = `<div class="bad">${esc(r.error || j.error || '검색 실패')}</div>`; return; }
    const per = r.per_channel || {}, c = r.counts || {};
    $('#s-summary').innerHTML = `합집합 <b>${c.union || 0}</b> · 교집합 <b>${c.intersection || 0}</b> · 표시 <b>${c.returned || 0}</b>`
      + (c.doc_type_filtered ? ` <span class="chk-warn" title="고른 문서 유형이 아니라서 뺀 후보 수">· 유형 필터로 ${c.doc_type_filtered}건 제외</span>` : '')
      + (c.acl_blocked ? ` <span class="chk-warn" title="내 등급으로는 볼 수 없는 문서">· 권한으로 ${c.acl_blocked}건 가려짐</span>` : '');
    if ($('#s-expr')) $('#s-expr').textContent = r.expr || condExpr();
    const chStat = Object.keys(per).map((k) => `<span class="chip ch-${esc(k)}" title="${esc(k)} 채널 ${per[k].n}건 · ${fmt(per[k].ms, 0)} ms${per[k].provider ? ' · ' + esc(per[k].provider) : ''}">${esc(k)} ${per[k].n}건 · ${fmt(per[k].ms, 0)}ms</span>`).join('');
    const wt = r.weights && Object.keys(r.weights).length
      ? `<div class="muted small">RRF 가중치(라우터): ${Object.keys(r.weights).map((k) => esc(k) + '=' + fmt(r.weights[k], 2)).join(' · ')}</div>` : '';
    const modeNote = { or: '고른 채널 중 <b>하나라도</b> 찾은 청크 (커버리지)',
      and: '고른 채널이 <b>모두</b> 찾은 청크 (채널 합의)',
      rrf: '질의 경로와 <b>같은 가중 RRF</b> 로 융합한 순위',
      composite: `조건: <b>${esc(r.expr || '')}</b>` }[r.mode] || '';
    // 발췌에서 강조할 말 — 내가 친 말 + 규칙이 넓혀 준 말 (왜 이 결과가 걸렸는지 보이게)
    const terms = (q.split(/\s+/).filter(Boolean))
      .concat(((r.per_channel || {}).fts || {}).terms || [])
      .concat(r.expanded_terms || []);
    // 결과가 없을 때 **다음에 무엇을 할지** 말해 준다 (빈 화면으로 끝내지 않는다)
    const noHitHelp = (rr) => {
      const tips = [];
      if ((rr.counts || {}).doc_type_filtered) tips.push('문서 유형 필터를 <b>전체</b>로 풀어 보세요');
      if ((rr.exclude || []).length) tips.push('제외 채널을 해제해 보세요');
      if ((rr.require || []).length) tips.push('<b>넓게 찾기</b>(OR) 로 바꿔 보세요');
      if ((rr.channels || []).length < 3) tips.push('Vector·Graph 채널도 켜 보세요 — 표현이 달라도 뜻이 가까운 문단을 찾습니다');
      tips.push('디버그 탭의 <b>규칙 설명</b> 으로 이 말이 어떻게 넓혀지는지 확인하세요');
      return '<div class="muted">결과가 없습니다.<ul>' + tips.map((t) => '<li>' + t + '</li>').join('') + '</ul></div>';
    };
    // 행을 누르면 그 문서 화면으로 간다 (Knowledge › 문서). chunk 칸은 그 청크 원문을 펼친다.
    $('#search-out').innerHTML =
      `<div class="s-stat">${chStat}</div>${wt}<div class="muted small">${modeNote}</div>` +
      '<div class="tbl-wrap"><table class="s-tbl"><tr><th>#</th><th>채널(순위)</th><th>점수</th><th>문서 · 헤딩</th><th>chunk</th><th>발췌</th><th title="고정 근거로 등록">📌</th></tr>' +
      r.rows.map((x, i) => `<tr class="${x.n_channels > 1 ? 'multi' : ''} s-row" data-doc="${esc(x.doc_id || '')}" data-chunk="${esc(x.chunk_id)}" title="클릭: 이 문서 화면으로 · chunk 를 누르면 원문을 펼칩니다">` +
        `<td class="num">${i + 1}</td><td>${chBadges(x.channels || {})}</td>` +
        `<td class="num">${fmt(x.score, 4)}</td><td class="small"><a href="#" data-godoc="${esc(x.doc_id || '')}">${esc(x.doc_id || '')}</a>${x.heading ? ' <span class="muted">› ' + esc(x.heading) + '</span>' : ''}</td>` +
        `<td class="mono small"><a href="#" data-gochunk="${esc(x.chunk_id)}">${esc(x.chunk_id)}</a></td>` +
        `<td class="muted small">${hilite((x.snippet || '').slice(0, 200), terms)}</td>` +
        `<td class="nowrap"><button class="mini secondary" data-pin="${esc(x.doc_id || '')}" title="이 문서를 이 질의 유형의 고정 근거로 등록합니다 (pins.json)">📌</button></td></tr>`).join('') +
      '</table></div><div id="s-chunk"></div>' +
      (r.rows.length ? '' : noHitHelp(r));
    const goDoc = (id) => { if (!id) { toast('문서 id 가 없는 결과입니다'); return; } if (LW.openDocPage) LW.openDocPage(id); else { switchGroup('knowledge'); switchTab('doc'); } };
    $$('#search-out [data-godoc]').forEach((a) => a.onclick = (e) => { e.preventDefault(); e.stopPropagation(); goDoc(a.dataset.godoc); });
    $$('#search-out [data-gochunk]').forEach((a) => a.onclick = async (e) => {
      e.preventDefault(); e.stopPropagation();
      const ch = await api('/api/chunk?id=' + encodeURIComponent(a.dataset.gochunk));
      $('#s-chunk').innerHTML = `<div class="dbg-sec"><h4>${esc(a.dataset.gochunk)} <span class="muted small">${esc((ch && ch.heading) || '')}</span></h4><pre class="pre">${esc((ch && ch.text) || '(본문 없음)')}</pre></div>`;
      $('#s-chunk').scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    });
    $$('#search-out tr.s-row').forEach((tr) => tr.onclick = () => goDoc(tr.dataset.doc));
    // 📌 이 문서를 이 질의의 고정 근거로 — 검색에서 "이게 정답인데 왜 안 올라오지" 를 본 자리에서 바로 고친다
    $$('#search-out [data-pin]').forEach((b) => b.onclick = async (e) => {
      e.preventDefault(); e.stopPropagation();
      const doc = b.dataset.pin;
      if (!doc) { toast('문서 id 가 없는 결과입니다'); return; }
      const kws = q.split(/\s+/).filter((w) => w.length > 1).slice(0, 3);
      const res = await api('/api/pins', { action: 'add', doc, keywords: kws, note: '채널 검색에서 지정' });
      if (res && res.cancelled) { toast('취소됨'); return; }
      toast(res && res.error ? ('고정 실패: ' + res.error)
        : `고정 근거 추가 — ${doc} (조건: ${kws.join(', ') || '이 질의'}) · 설정 › 고정 근거에서 관리`);
    });
    // 그래프 채널을 골랐으면 시드·엔티티·관계도 (그래프만의 정보라 표 아래에 따로)
    const g = r.graph;
    $('#search-graph').innerHTML = !g ? '' :
      `<details class="s-graph" open><summary>그래프 채널 상세 — 시드 ${((g.seeds || []).length)} · 엔티티 ${((g.entities || []).length)} · 관계 ${((g.relations || []).length)}</summary>` +
      `<div class="kv">seeds = ${esc(JSON.stringify(g.seeds))}${g.provenance ? ' · provenance = ' + esc(JSON.stringify(g.provenance)) : ''}</div>` +
      '<div class="tbl-wrap"><table><tr><th>엔티티</th><th>유형</th><th>점수</th></tr>' +
      (g.entities || []).map((e) => `<tr><td>${esc(e.name)}</td><td>${esc(e.type)}</td><td class="num">${fmt(e.score, 3)}</td></tr>`).join('') +
      '</table></div><div class="tbl-wrap"><table><tr><th>src</th><th>rel</th><th>dst</th><th>provenance</th><th>gain</th></tr>' +
      // 질의 결과의 그래프 표와 같은 동작 — 이름을 누르면 엔티티 화면으로 (한쪽만 되면 더 헷갈린다)
      (g.relations || []).map((x) => `<tr><td>${LW.entLink(x.src)}</td><td><code>${esc(x.rel)}</code></td><td>${LW.entLink(x.dst)}</td><td class="muted">${esc(x.provenance || '')}</td><td class="num">${fmt(x.gain, 3)}</td></tr>`).join('') +
      '</table></div></details>';
    LW.bindEvidenceLinks('#search-graph');
    // trace 는 접힌 채로 둔다 — 이 화면은 이제 디버그가 아니라 **사용자용 검색**이다 (필요하면 펼친다)
    renderTrace($('#search-trace'), j.trace, { openAll: false });
  };
  $('#s-q').addEventListener('keydown', (e) => { if (e.key === 'Enter') $('#btn-search').click(); });
  // 검색어를 그대로 질의·답변으로 — 찾기만 하고 끝나지 않게
  if ($('#btn-s-ask')) $('#btn-s-ask').onclick = () => {
    const q = $('#s-q').value.trim();
    if (!q) { toast('찾을 말을 넣으세요'); return; }
    const box = $('#q');
    if (box) box.value = q;
    switchTab('query');
    if (box) box.focus();
    toast('질의·답변 탭으로 가져왔습니다 — 실행하려면 “질의” 를 누르세요');
  };
  loaders.search = () => { loadSearchTypes(); renderRecent(); };

  // ---------------- DEBUG (질의 해부) ----------------
  // 채널 검색과 달리 **검색을 돌리지 않는다**. "내 질문이 검색어로 어떻게 변했나" 만 본다 (LLM 호출 0, 수 ms).
  // 서버는 llmwiki/querydebug.py 의 inspect_query — CLI `inspect` · MCP wiki_inspect 와 같은 함수다.
  let DBG = null;
  function kvRows(pairs) {
    return '<table class="dbg-kv">' + pairs.map(([k, v, t]) => `<tr><th title="${esc(t || '')}">${esc(k)}</th><td>${v}</td></tr>`).join('') + '</table>';
  }
  function chips(list, cls) {
    if (!list || !list.length) return '<span class="muted">(없음)</span>';
    return list.map((x) => `<span class="chip ${cls || ''}">${esc(typeof x === 'string' ? x : JSON.stringify(x))}</span>`).join('');
  }
  function renderDebug(d) {
    DBG = d;
    const f = d.fts || {}, t = d.time || {}, r = d.router || {}, st = d.stats || {};
    const w = r.weights || {};
    const pins = Array.isArray(d.pins) ? d.pins : ((d.pins && d.pins.matched) || []);
    const alt = (f.alt_queries || []).map((a) => Array.isArray(a) ? `${a[0]} <span class="muted">(${a[2] || ''} w=${a[1]})</span>` : esc(String(a)));
    const rel = (f.related || []).map((a) => Array.isArray(a) ? `${a[0]} <span class="muted">(w=${a[1]})</span>` : esc(String(a)));
    $('#dbg-out').innerHTML =
      `<div class="dbg-sec"><h4>1. 토큰화 <span class="muted small">${st.chars}자 · 단어 ${st.words} · 토큰 ${st.tokens} · 키워드 ${st.keywords}</span></h4>` +
      kvRows([
        ['검색 토큰', chips(d.tokens, 'ch-fts'), 'FTS 색인에 들어가는 토큰 (조사 제거·bigram 포함)'],
        ['키워드', chips(d.keywords), '커버리지 계산과 리랭크에 쓰이는 핵심어'],
        ['불용어 제거', chips(d.stopwords_removed), 'STOPWORDS 로 검색에서 빠진 말'],
      ]) + '</div>' +
      `<div class="dbg-sec"><h4>2. 규칙 확장 <span class="muted small">발화한 규칙 ${st.rules_fired}개 · 토글 query_rules=${(d.toggles || {}).query_rules ? 'on' : 'OFF'}</span></h4>` +
      kvRows([
        ['FTS 식', `<code class="mono small">${esc(f.match_expr || '-')}</code>`, '규칙 없이 만든 기본 MATCH 식'],
        ['규칙 적용 식', `<code class="mono small">${esc(f.rule_match || '(규칙이 걸리지 않음)')}</code>`, '동의어·약어 OR 그룹과 별칭 치환이 들어간 식'],
        ['대체 질의', alt.length ? alt.join('<br>') : '<span class="muted">(없음)</span>', 'FTS·벡터를 추가로 돌리는 질의'],
        ['관련어', rel.length ? rel.join('<br>') : '<span class="muted">(없음)</span>', '별도 보조 리스트로 융합되는 말'],
        ['제외', chips(f.exclude), 'NOT 으로 빠지고 페널티를 받는 말'],
        ['발화한 규칙', chips((f.fired || []).map((x) => (x && x.matched) || x)), 'query_rules.json 에서 걸린 항목'],
      ]) + '</div>' +
      `<div class="dbg-sec"><h4>3. 시간 표현 <span class="muted small">mode=${esc(t.mode || '')} · 토글 time_scope=${t.enabled ? 'on' : 'OFF'}</span></h4>` +
      (t.scope ? kvRows([['범위', `${esc(t.scope.from)} ~ ${esc(t.scope.to)} <span class="muted">(${esc(t.scope.expr || '')} · ${esc(t.scope.kind || '')})</span>`, '문서 날짜를 이 범위로 boost 또는 filter'],
        ['남은 질의', `<code class="mono small">${esc(t.scope.query || '')}</code>`, '시간 표현을 뺀 뒤 검색에 쓰이는 질의']])
        : '<div class="muted small">시간 표현이 없습니다.</div>') + '</div>' +
      `<div class="dbg-sec"><h4>4. 채널 라우팅 <span class="muted small">유형 <b>${esc(r.kind || '-')}</b>${r.kind_label ? ' · ' + esc(r.kind_label) : ''}</span></h4>` +
      (r.error ? `<div class="bad">${esc(r.error)}</div>` :
        (r.kind_desc ? `<div class="panel-intro">${esc(r.kind_desc)}</div>` : '') +
        ((r.why || []).length ? '<div class="prop-checks">' + (r.why).map((x) => `<div class="chk-info">이렇게 정해진 이유 · ${esc(x)}</div>`).join('') + '</div>' : '') +
        '<div class="dbg-w">' + Object.keys(w).map((k) => `<div class="dbg-wrow"><span class="chip ch-${esc(k)}">${esc(k)}</span><i style="width:${Math.min(100, (w[k] / 2) * 100)}%"></i><b>${fmt(w[k], 2)}</b></div>`).join('') + '</div>' +
        '<div class="muted small">이 유형이 하는 일은 <b>위 가중치를 정하는 것 하나뿐</b>입니다 — 채널을 끄거나 켜지 않습니다. 실제 순위는 Ask › 채널 검색의 <b>실제 순위</b>(RRF) 로 볼 수 있습니다.</div>' +
        (r.reason ? `<div class="muted small">${esc(r.reason)}</div>` : '')) + '</div>' +
      `<div class="dbg-sec"><h4>5. 고정 근거(pin) <span class="muted small">${pins.length}건 · 토글 pins=${(d.toggles || {}).pins ? 'on' : 'OFF'}</span></h4>` +
      (pins.length ? '<table class="dbg-kv">' + pins.map((p) => `<tr><th>${esc(p.id || '')}</th><td class="mono small">${esc(p.chunk || p.doc || '')}</td></tr>`).join('') + '</table>'
        : '<div class="muted small">이 질의에 걸린 pin 이 없습니다.</div>') + '</div>' +
      // 이 줄이 무엇인지 몰랐다는 피드백(2026-09-20). ① 무슨 뜻인지 적고 ② **여기서 임시로 껐다 켜** 볼 수 있게 했다.
      // 임시란 뜻: 이 해부를 다시 돌릴 때만 적용하는 요청 단위 오버라이드다. 파일(config.json)은 바뀌지 않는다.
      `<div class="dbg-sec"><h4>이 해부에 적용된 단계 <span class="muted small">눌러서 임시로 껐다 켤 수 있습니다 (저장되지 않음)</span></h4>` +
      `<div class="panel-intro">아래는 <b>지금 서버 설정에서 켜져 있는 단계</b>입니다. 꺼진 단계는 위 결과에 반영되지 않습니다 —
        예를 들어 <code>query_rules=OFF</code> 면 2번(규칙 확장)이 비어 보이는 게 정상입니다.
        칩을 누르면 <b>이 화면에서만</b> 값을 뒤집어 다시 해부합니다. 영구 변경은 <b>설정 › 파이프라인</b> 에서 합니다.</div>` +
      '<div class="row" id="dbg-toggles">' + Object.keys(d.toggles || {}).map((k) =>
        `<button type="button" class="mini ${d.toggles[k] ? '' : 'secondary'}" data-tg="${esc(k)}" data-on="${d.toggles[k] ? 1 : 0}" title="${esc(DBG_TOGGLE_HELP[k] || k)} — 눌러서 임시로 ${d.toggles[k] ? '끄기' : '켜기'}">${esc(k)}=${d.toggles[k] ? 'on' : 'OFF'}</button>`).join('')
      + (Object.keys(DBG_OVERRIDE).length
        ? ` <span class="chk-warn">임시로 바꾼 값 ${Object.keys(DBG_OVERRIDE).length}개 — <a href="#" id="dbg-tg-reset">되돌리기</a></span>` : '')
      + '</div></div>';
    $$('#dbg-toggles [data-tg]').forEach((b) => b.onclick = () => {
      const k = b.dataset.tg;
      DBG_OVERRIDE[k] = !(b.dataset.on === '1');
      $('#btn-dbg').click();
    });
    if ($('#dbg-tg-reset')) $('#dbg-tg-reset').onclick = (e) => {
      e.preventDefault();
      Object.keys(DBG_OVERRIDE).forEach((k) => delete DBG_OVERRIDE[k]);
      $('#btn-dbg').click();
    };
  }
  // 디버그 화면에서만 쓰는 임시 토글 오버라이드 (요청 단위 — 파일은 바뀌지 않는다)
  const DBG_OVERRIDE = {};
  const DBG_TOGGLE_HELP = {
    query_rules: '동의어·약어 사전으로 질의를 넓히는 단계 (2번 결과)',
    time_scope: "'지난주' 같은 시간 표현을 날짜 범위로 바꾸는 단계 (3번 결과)",
    router: '질의 유형을 보고 채널 가중치를 정하는 단계 (4번 결과)',
    pins: '사람이 지정한 고정 근거를 끼워 넣는 단계 (5번 결과)',
    fts: '키워드(BM25) 검색 채널',
    vector: '임베딩 유사도 검색 채널',
    graph: '지식 그래프 검색 채널',
  };
  $('#btn-dbg').onclick = async () => {
    const q = $('#dbg-q').value.trim(); if (!q) return;
    $('#dbg-out').innerHTML = '<div class="muted small">해부 중…</div>';
    const ov = Object.assign({}, overrides());
    if (Object.keys(DBG_OVERRIDE).length) {
      ov.toggles = Object.assign({}, ov.toggles || {}, DBG_OVERRIDE);
    }
    const d = await api('/api/debug/query', { q, overrides: ov });
    if (!d || d.error) { $('#dbg-out').innerHTML = `<div class="bad">${esc((d && d.error) || '실패')}</div>`; return; }
    $('#cli-equiv').textContent = 'python -m llmwiki inspect "' + q + '"';
    renderDebug(d);
  };
  $('#dbg-q').addEventListener('keydown', (e) => { if (e.key === 'Enter') $('#btn-dbg').click(); });
  $('#btn-dbg-copy').onclick = () => { if (DBG) copyText(JSON.stringify(DBG, null, 1), '질의 해부'); else toast('먼저 해부를 실행하세요'); };
  const dbgTool = (html) => { $('#dbg-tool-out').innerHTML = html; };
  $('#btn-rules-test').onclick = async () => { const r = await api('/api/query_rules/test?q=' + encodeURIComponent($('#rules-test-q').value)); dbgTool(`<pre class="pre">${esc(JSON.stringify(r, null, 1))}</pre>`); };
  $('#btn-rules-explain').onclick = async () => { const r = await api('/api/query_rules/explain?term=' + encodeURIComponent($('#rules-explain-q').value)); dbgTool(`<pre class="pre">${esc(JSON.stringify(r, null, 1))}</pre>`); };
  $('#btn-time-test').onclick = async () => { const r = await api('/api/time?q=' + encodeURIComponent($('#time-test-q').value)); dbgTool(`<pre class="pre">${esc(JSON.stringify(r, null, 1))}</pre>`); };
  $('#btn-dbg-chunk').onclick = async () => { const r = await api('/api/chunk?id=' + encodeURIComponent($('#dbg-chunk').value.trim())); dbgTool(`<pre class="pre">${esc(JSON.stringify(r, null, 1))}</pre>`); };
  $('#btn-dbg-doc').onclick = async () => { const r = await api('/api/doc_chunks?id=' + encodeURIComponent($('#dbg-doc').value.trim())); dbgTool('<table class="dbg-kv"><tr><th>chunk</th><th>heading</th><th>글자</th></tr>' + (r || []).map((c) => `<tr><td class="mono small">${esc(c.chunk_id || c.id || '')}</td><td>${esc(c.heading || '')}</td><td class="num">${(c.text || '').length}</td></tr>`).join('') + '</table>'); };
  LW.runQuery = runQuery;
})(window.LW);
