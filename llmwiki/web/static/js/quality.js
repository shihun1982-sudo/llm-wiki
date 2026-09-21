/* Quality — trial 비교, 평가, 포렌식, fusion 비교. */
(function (LW) {
  'use strict';
  const { $, $$, esc, fmt, fmtK, dt, api, toast, STATE, overrides, presetNames, pollJob, renderTrace, loaders, switchTab, switchGroup } = LW;

  // ---------------- TRIALS ----------------
  // 선언을 **쓰기 전에** 둔다. 아래 `syncSrc()` 가 초기화 시점에 `pickInfo()` 를 부르는데,
  // 그때 `PICKED` 가 아직 TDZ 면 `Cannot access 'PICKED' before initialization` 으로
  // IIFE 전체가 죽는다 — 2026-09-20 오전에 `loaders` 로 같은 사고가 있었다(화면 전체 무반응).
  let PICKED = [];
  let TRIALS = [];
  const SRC_LABEL = { evalset: '평가셋', queries: '실제 질의 이력', list: '직접 고른 문항' };
  const SRC_SHORT = { evalset: '평가셋', queries: '질의 이력', list: '직접' };
  // 계산하지 않은 지표는 **공백으로 그리고 이유를 말한다**. 0 으로 보이면 "완전 실패한 설정" 으로 읽힌다.
  const NAC = '<span class="muted" title="채점할 정답이 없어 계산하지 않았습니다 — 0점이 아닙니다">—</span>';
  const cell = (v, d) => (v == null ? NAC : fmt(v, d));
  async function loadTrials() {
    TRIALS = await api('/api/trials');
    const ungraded = TRIALS.some((t) => t.graded === false);
    $('#tr-list').innerHTML =
      (TRIALS.length ? '<div class="muted small">비교할 trial 을 <b>2~4개</b> 체크하고 <b>비교</b> 를 누릅니다. 같은 <b>원천</b>끼리 비교해야 뜻이 있습니다.</div>' : '') +
      '<div class="tbl-wrap"><table><tr><th></th><th>#</th><th>이름</th><th>원천</th><th>시각</th><th>v</th><th>n</th><th>hit@k</th><th>MRR</th><th>term</th><th>ground.</th><th>cite prec.</th><th>insuf.</th><th>fallback</th><th>avg ms</th><th>p95</th><th>tok/q</th><th>note</th></tr>' +
      TRIALS.map((t) => {
        const s = t.summary || {}; const sk = (t.source || {}).kind || 'evalset';
        return `<tr><td><input type="checkbox" data-tid="${t.trial_id}" data-src="${esc(sk)}"></td><td>${t.trial_id}</td><td><b>${esc(t.name)}</b></td>`
          + `<td><span class="pill ${sk === 'queries' ? 'warn' : ''}" title="${esc(SRC_LABEL[sk] || sk)}${sk === 'queries' ? ' — 정답이 없어 hit@k·MRR·term 은 계산되지 않습니다' : ''}">${esc(SRC_SHORT[sk] || sk)}</span></td>`
          + `<td class="muted small">${dt(t.ts)}</td><td>${t.build_version}</td><td class="num">${s.n}</td>`
          + `<td class="num">${cell(s['hit@k'], 3)}</td><td class="num">${cell(s.mrr, 3)}</td><td class="num">${cell(s.term_recall, 3)}</td>`
          + `<td class="num">${cell(s.groundedness, 2)}</td><td class="num">${cell(s.citation_precision, 2)}</td><td class="num">${cell(s.insufficient_rate, 2)}</td>`
          + `<td class="num">${cell(s.fallback_rate, 2)}</td><td class="num">${cell(s.avg_ms, 0)}</td><td class="num">${cell(s.p95_ms, 0)}</td>`
          + `<td class="num">${cell(s.tokens_per_query, 0)}</td><td class="muted small">${esc(t.note || '')}</td></tr>`;
      }).join('') + '</table></div>'
      + (ungraded ? '<div class="muted small"><b>—</b> 는 <b>계산하지 않은 것</b>입니다(0점이 아닙니다). 실제 질의 이력에는 채점할 정답이 없어 hit@k·MRR·term 을 낼 수 없습니다 — <b>groundedness · insuf. · avg ms · tok/q</b> 로 비교하세요.</div>' : '')
      + (TRIALS.length ? '' : '<div class="muted">trial 이 없습니다. 위에서 실행하세요.</div>');
    // 체크를 바꾸면 '다음에 할 일' 도 따라 바뀐다 (2개 이상이면 "비교를 누르세요")
    $$('#tr-list [data-tid]').forEach((c) => c.onchange = nextStep);
    nextStep();
  }
  $('#btn-tr-refresh').onclick = loadTrials;
  if ($('#tr-source')) {
    const syncSrc = () => {
      const on = $('#tr-source').value === 'queries';
      $('#tr-q-opts').classList.toggle('hidden', !on);
      if (!on) { PICKED = []; if ($('#tr-pick')) $('#tr-pick').classList.add('hidden'); }
      pickInfo();
    };
    $('#tr-source').onchange = syncSrc;
    syncSrc();
  }
  // 설정을 적는 즉시 '다음에 할 일' 이 ②로 넘어간다
  if ($('#tr-sets')) $('#tr-sets').oninput = nextStep;
  if ($('#tr-preset')) $('#tr-preset').addEventListener('change', nextStep);
  $('#btn-tr-run').onclick = async () => {
    // 문항 원천 — 평가셋(고정 목록) 또는 **실제 질의 이력**. CLI `trial run --source` 와 같다.
    const src = ($('#tr-source') && $('#tr-source').value) || 'evalset';
    const id = await runTrial($('#tr-name').value.trim(), parseSets(), $('#tr-preset').value);
    if (id) toast('trial #' + id + ' 완료' + (src === 'queries' ? ' — 실제 질의 문항 (hit@k 등은 계산되지 않습니다)' : ''));
    loadTrials();
  };
  // **A/B 한 번에** — trial 비교의 실제 목적은 "이 설정을 바꾸면 좋아지나?" 하나다.
  // 예전에는 그러려면 (1) 기준 trial 실행 (2) 설정 바꾸기 (3) 두 번째 실행 (4) 체크 (5) 비교 — 다섯 걸음이었다.
  // 여기서는 **같은 문항으로 기준(설정 오버라이드 없음)과 변경본을 연달아 돌리고 바로 비교**한다.
  if ($('#btn-tr-ab')) $('#btn-tr-ab').onclick = async () => {
    const sets = parseSets();
    if (!Object.keys(sets).length && !$('#tr-preset').value) {
      toast('비교할 변경이 없습니다 — 설정 오버라이드(k=v)나 프리셋을 먼저 고르세요'); return;
    }
    const base = ($('#tr-name').value.trim() || 'ab') + '-기준';
    const chg = ($('#tr-name').value.trim() || 'ab') + '-변경';
    $('#tr-log').textContent = 'A/B: 기준 실행 중…';
    const r1 = await runTrial(base, {}, '');            // 지금 설정 그대로
    if (!r1) return;
    $('#tr-log').textContent = 'A/B: 변경본 실행 중…';
    const r2 = await runTrial(chg, sets, $('#tr-preset').value);
    if (!r2) return;
    await loadTrials();
    const ids = [r1, r2].map(String);
    $$('#tr-list [data-tid]').forEach((cb) => { cb.checked = ids.indexOf(cb.dataset.tid) >= 0; });
    $('#tr-log').textContent = 'A/B 완료 — 아래 비교';
    $('#btn-tr-compare').click();
  };

  function parseSets() {
    const sets = {};
    ($('#tr-sets').value || '').split(',').map((x) => x.trim()).filter(Boolean)
      .forEach((kv) => { const [k, v] = kv.split('='); if (k && v != null) sets[k.trim()] = v.trim(); });
    return sets;
  }

  // ---- 비교에 쓸 **과거 질의 고르기** (2026-09-20) ----------------------------
  // 예전에는 기간·건수·피드백으로 **뭉뚱그려** 가져갔다. 그런데 비교하고 싶은 질의는 대개 몇 개로
  // 정해져 있다 — "이 세 질문이 느린데 설정을 바꾸면 나아지나". 고르지 못하면 비교가 내 관심사와
  // 상관없는 문항 위에서 돈다. CLI `trial candidates` → `trial run --pick` 과 같은 일이다.
  function pickInfo() {
    $('#tr-pick-info').innerHTML = PICKED.length
      ? `<b>${PICKED.length}개</b> 고름 — 이 질의로 돌립니다 <a href="#" id="tr-pick-clear">해제</a>`
      : '고르지 않으면 위 조건으로 자동 선택';
    const c = $('#tr-pick-clear');
    if (c) c.onclick = (e) => { e.preventDefault(); PICKED = []; $$('#tr-pick [data-qid]').forEach((b) => { b.checked = false; }); pickInfo(); };
    nextStep();
  }

  /** **다음에 뭘 해야 하나** — 고르고 나서 막히는 자리가 여기였다 (2026-09-20).
   *
   * trial 비교는 "돌린다 → 설정을 바꾼다 → 다시 돌린다 → 둘을 고른다 → 비교" 다섯 걸음이라,
   * 처음 오는 사람은 질의를 고른 뒤 무엇을 눌러야 할지 알 수 없다. 상태에 따라 **지금 할 일 하나**를
   * 굵게 말해 준다. */
  function nextStep() {
    const el = $('#tr-next');
    if (!el) return;
    const nSel = $$('#tr-list [data-tid]').filter((c) => c.checked).length;
    const hasChange = Object.keys(parseSets()).length > 0 || !!($('#tr-preset') && $('#tr-preset').value);
    let h;
    if (nSel >= 2) {
      h = `③ trial 을 <b>${nSel}개</b> 골랐습니다 → <b>비교</b> 를 누르세요.`;
    } else if (hasChange) {
      h = '② 바꿀 설정을 적었습니다 → <b>A/B 비교 (기준 ↔ 변경) 한 번에</b> 를 누르면 '
        + '같은 질의로 <b>지금 설정</b>과 <b>바꾼 설정</b>을 연달아 돌리고 바로 비교합니다.';
    } else if (PICKED.length) {
      h = `① 질의 <b>${PICKED.length}개</b>를 골랐습니다 → 위 <b>설정 오버라이드</b>(예 <code>top_k_final=10</code>)나 `
        + '<b>프리셋</b>에 <b>바꿔서 시험할 값</b>을 적고 <b>A/B 비교</b> 를 누르세요. '
        + '<span class="muted">한 번만 돌려 보려면 <b>Trial 실행</b>.</span>';
    } else {
      h = '① <b>질의 고르기…</b> 로 비교에 쓸 과거 질의를 고르거나, 그냥 두면 위 조건으로 자동 선택됩니다. '
        + '② 바꿀 설정을 적고 ③ <b>A/B 비교</b>.';
    }
    el.innerHTML = '<b>다음:</b> ' + h;
  }
  if ($('#btn-tr-pick')) $('#btn-tr-pick').onclick = async () => {
    const box = $('#tr-pick');
    const qs = '?days=' + (($('#tr-days') && $('#tr-days').value) || 7)
      + '&limit=' + Math.max(20, parseInt(($('#tr-limit') && $('#tr-limit').value) || 30, 10) * 2)
      + '&only=' + encodeURIComponent(($('#tr-only') && $('#tr-only').value) || '');
    box.classList.remove('hidden');
    box.innerHTML = '<div class="muted small">불러오는 중…</div>';
    const r = await api('/api/eval/candidates' + qs);
    const rows = (r && r.candidates) || [];
    if (!rows.length) {
      box.innerHTML = '<div class="muted small">그 조건에 맞는 과거 질의가 없습니다 — 기간을 늘리거나 거르기를 푸세요.</div>';
      return;
    }
    box.innerHTML = '<div class="row"><b>비교에 쓸 질의를 고르세요</b>'
      + `<span class="muted small">${rows.length}건 · 고르지 않으면 위 조건으로 자동 선택합니다</span>`
      + '<button class="secondary" id="tr-pick-all">전체 선택</button>'
      + '<button class="secondary" id="tr-pick-none">전체 해제</button>'
      + '<button class="secondary" id="tr-pick-close">닫기</button></div>'
      + '<div class="tbl-wrap"><table><tr><th></th><th>#</th><th>시각</th><th>평가</th>'
      + '<th title="그때의 근거 판정 — 약하거나 못 찾은 질의가 고칠 값어치가 크다">판정</th>'
      + '<th title="답변 문장이 근거로 뒷받침된 비율">근거</th><th>질의</th></tr>'
      + rows.map((c) => {
        const id = c.from_query_id, fb = c.feedback;
        const vp = { insufficient: ['bad', '근거 못 찾음'], weak: ['warn', '근거 약함'], sufficient: ['ok', '정상'] }[c.verdict];
        return `<tr><td><input type="checkbox" data-qid="${id}" ${PICKED.indexOf(id) >= 0 ? 'checked' : ''}></td>`
          + `<td class="muted small">${id}</td><td class="muted small">${dt(c.ts)}</td>`
          + `<td>${fb == null ? '' : (fb < 0 ? '👎' : '👍')}</td>`
          + `<td>${vp ? `<span class="pill ${vp[0]}">${vp[1]}</span>` : ''}</td>`
          + `<td class="num small">${c.groundedness == null ? '' : fmt(c.groundedness, 2)}</td>`
          + `<td class="small">${esc((c.q || '').slice(0, 90))}</td></tr>`;
      }).join('') + '</table></div>';
    const sync = () => { PICKED = $$('#tr-pick [data-qid]').filter((b) => b.checked).map((b) => parseInt(b.dataset.qid, 10)); pickInfo(); };
    $$('#tr-pick [data-qid]').forEach((b) => b.onchange = sync);
    $('#tr-pick-all').onclick = () => { $$('#tr-pick [data-qid]').forEach((b) => { b.checked = true; }); sync(); };
    $('#tr-pick-none').onclick = () => { $$('#tr-pick [data-qid]').forEach((b) => { b.checked = false; }); sync(); };
    $('#tr-pick-close').onclick = () => box.classList.add('hidden');
    pickInfo();
  };

  /** trial 하나를 돌리고 trial_id 를 돌려준다 (실패면 null). */
  function runTrial(name, sets, preset) {
    const src = ($('#tr-source') && $('#tr-source').value) || 'evalset';
    const body = { action: 'run', name, preset: [preset].concat(presetNames()).filter(Boolean).join(','),
      sets, k: parseInt($('#tr-k').value, 10), source: src, overrides: overrides() };
    if (src === 'queries') {
      if (PICKED.length) {
        body.pick = PICKED.slice();        // 고른 것이 있으면 그것만 (CLI `trial run --pick` 과 같다)
      } else {
        body.days = parseFloat(($('#tr-days') && $('#tr-days').value) || 7);
        body.limit = parseInt(($('#tr-limit') && $('#tr-limit').value) || 30, 10);
        body.only = ($('#tr-only') && $('#tr-only').value) || '';
      }
    }
    return api('/api/trials', body).then((j) => {
      if (j && j.error) { toast('실행 실패: ' + j.error); return null; }
      return new Promise((res) => pollJob(j.job, $('#tr-log'), (job) => {
        if (job.status === 'done') res((job.result || {}).trial_id || null);
        else if (job.status === 'error') { toast('trial 실패'); res(null); }
      }));
    });
  }

  let LAST_CMP = null;
  $('#btn-tr-compare').onclick = async () => {
    const ids = $$('#tr-list [data-tid]').filter((c) => c.checked).map((c) => c.dataset.tid);
    if (ids.length < 2) { toast('2개 이상 선택'); return; }
    const srcs = new Set($$('#tr-list [data-tid]').filter((c) => c.checked).map((c) => c.dataset.src));
    if (srcs.size > 1 && !confirm('문항 원천이 서로 다른 trial 을 골랐습니다.\n문항 자체가 달라 품질 지표를 같은 잣대로 비교할 수 없습니다.\n그래도 계속할까요?')) return;
    const c = await api('/api/trials/compare?ids=' + ids.slice(0, 4).join(',')); LAST_CMP = c;
    if (c.error) { $('#tr-compare').innerHTML = `<div class="banner err">${esc(c.error)}</div>`; return; }
    const names = c.trials.map((t) => t.name);
    const NA = c.unavailable_metrics || [];
    // 문항을 어디서 가져왔는지 — 읽는 법이 달라지므로 맨 위에 둔다
    let html = '<div class="row">' + c.trials.map((t) => {
      const s = t.source || { kind: 'evalset' };
      const extra = s.kind === 'queries' ? ` 최근 ${s.days}일 · ${s.only || 'all'}` : '';
      return `<div class="stat" title="${esc(s.note || '')}"><b>${esc(SRC_LABEL[s.kind] || s.kind)}</b>${esc(t.name)}${esc(extra)} · ${s.n || t.n}문항</div>`;
    }).join('') + '</div>';
    // 원천이 섞이면 **문항 자체가 다르다** — 숫자를 나란히 놓기 전에 먼저 말한다.
    if (c.mixed_sources) {
      html += `<div class="banner err"><b>원천이 다른 trial 을 비교하고 있습니다</b><br><span class="small">${esc(c.mixed_why || '')}</span></div>`;
    }
    if (NA.length) {
      html += `<div class="banner warn"><b>계산할 수 없는 지표</b> ${NA.map(esc).join(', ')}
        <br><span class="muted small">${esc(c.unavailable_why || '')}</span>
        <br><span class="small">→ 이 비교에서 <b>읽을 수 있는 지표</b>: ${(c.comparable_metrics || []).map((m) => `<code>${esc(m)}</code>`).join(' · ')}</span></div>`;
    }
    html += `<h3>지표 비교 (기준: ${esc(names[0])})</h3><table><tr><th>지표</th>${names.map((n) => `<th>${esc(n)}</th>`).join('')}<th>Δ (vs 기준)</th></tr>` + c.metrics.map((m) => {
      const na = NA.indexOf(m.metric) >= 0;
      return `<tr class="${na ? 'muted' : ''}"><td>${esc(m.metric)} <small class="muted">${m.higher_better ? '↑' : '↓'}</small>${na ? ' <span class="pill" title="정답이 없는 문항이라 계산할 수 없습니다 — 0 점이 아닙니다">계산 불가</span>' : ''}</td>`
        + m.values.map((v, i) => `<td class="num ${m.best === i && i !== 0 ? 'best' : ''}">${v == null ? '-' : typeof v === 'number' ? fmt(v, 3) : esc(String(v))}</td>`).join('')
        + `<td class="num small">${(m.delta || []).slice(1).map((d) => d == null ? '-' : (d > 0 ? '+' : '') + fmt(d, 3)).join(' , ')}</td></tr>`;
    }).join('') + '</table>';
    // 단계별 — "품질이 올랐는데 어느 단계가 그 값을 치렀나"
    const stg = c.stages || {};
    if (stg.available && (stg.rows || []).length) {
      const changed = stg.rows.filter((r) => r.changed);
      html += `<h3>단계별 <small class="muted">질의 1건당 · 낮을수록 좋다${changed.length ? '' : ' — 의미 있게 달라진 단계 없음'}</small></h3>`;
      if (changed.length) {
        html += '<div class="tbl-wrap"><table><tr><th>단계</th>' + names.map((n) => `<th>${esc(n)} ms</th>`).join('') + '<th>Δms</th><th>Δ토큰</th><th title="질의 1건당 이 단계가 몇 번 돌았나">횟수</th></tr>'
          + changed.slice(0, 24).map((r) => `<tr><td class="mono small">${esc(r.stage)}</td>`
            + r.cells.map((cc) => `<td class="num">${cc.ran ? fmt(cc.avg_ms, 1) : '<span class="muted" title="이 설정에서는 이 단계가 돌지 않았습니다">—</span>'}</td>`).join('')
            + `<td class="num small">${r.delta_ms.slice(1).map((d) => d == null ? '-' : (d > 0 ? '+' : '') + fmt(d, 1)).join(' , ')}</td>`
            + `<td class="num small">${r.delta_tokens.slice(1).map((d) => d == null ? '-' : (d > 0 ? '+' : '') + d).join(' , ')}</td>`
            + `<td class="num small muted">${r.cells.map((cc) => cc.ran ? cc.runs_per_query : '-').join(' / ')}</td></tr>`).join('')
          + '</table></div>'
          + (stg.rows.length > changed.length ? `<div class="muted small">달라지지 않은 단계 ${stg.rows.length - changed.length}개는 감췄습니다.</div>` : '');
      }
    } else if (stg.note) {
      html += `<h3>단계별</h3><div class="muted small">${esc(stg.note)}</div>`;
    }
    if (Object.keys(c.wins).length) html += '<div class="statrow">' + Object.keys(c.wins).map((n) => `<div class="stat"><b>${c.wins[n].win}/${c.wins[n].loss}/${c.wins[n].tie}</b>${esc(n)} win/loss/tie</div>`).join('') + '</div>';
    if (c.per_question.length) html += `<h3>질문별</h3><table class="grid-q"><tr><th>질문</th>${names.map((n) => `<th>${esc(n)}</th>`).join('')}</tr>` + c.per_question.map((p) => `<tr><td>${esc(p.q)}</td>${p.cells.map((cell, i) => `<td class="${i > 0 ? (p.outcome[i - 1] || '') : ''}">${cell.hit ? '✔' : '✘'} r=${cell.rank || '-'} g=${cell.groundedness == null ? '-' : fmt(cell.groundedness, 2)} ${cell.answer_mode === 'insufficient' ? '<span class="pill bad">insuf</span>' : ''} <a href="#" data-req="${cell.request_id}" class="muted small">#${cell.request_id}</a></td>`).join('')}</tr>`).join('') + '</table>';
    if (c.config_diff.length) html += '<h3>설정 차이</h3><table><tr><th>키</th>' + names.map((n) => `<th>${esc(n)}</th>`).join('') + '</tr>' + c.config_diff.map((d) => `<tr><td class="mono small">${esc(d.key)}</td>${d.values.map((v) => `<td class="small">${esc(JSON.stringify(v))}</td>`).join('')}</tr>`).join('') + '</table>';
    // 판정 — 값이 높은 쪽을 그냥 "최선" 이라 하지 않는다. 문항이 25개면 한 칸이 0.04 라서
    // **한 문항 뒤집힘**을 개선으로 읽게 된다. 부호 검정 결과와 토큰·지연 대가를 함께 보여 준다.
    if ((c.recommendation || []).length) {
      const any = c.recommendation.some((r) => r && typeof r === 'object' && r.significant);
      html += `<div class="banner ${any ? 'ok' : 'warn'}"><b>판정</b> <span class="muted small">문항 ${c.n_questions} · 한 문항 = ${fmt(c.one_question, 3)} (이보다 작은 차이는 우연과 구분되지 않습니다)</span><br>`
        + c.recommendation.map((r) => {
          if (!r || typeof r !== 'object') return esc(String(r));
          const sig = r.significant ? '<b>차이 있음</b>' : '<span class="muted">차이 확인 안 됨</span>';
          return `${esc(r.trial)}: ${sig} · hit@k ${r['hit@k_delta'] > 0 ? '+' : ''}${fmt(r['hit@k_delta'], 3)}`
            + ` · 승 ${r.win} / 패 ${r.loss} / 무 ${r.tie} (p≈${fmt(r.p, 2)})`
            + (r.cost ? ` · <span class="warntxt">대가: ${esc(r.cost)}</span>` : '');
        }).join('<br>') + '</div>';
    }
    $('#tr-compare').innerHTML = html;
    $$('#tr-compare a[data-req]').forEach((a) => a.onclick = (e) => { e.preventDefault(); switchGroup('observability'); switchTab('requests'); setTimeout(() => LW.openRequest && LW.openRequest(parseInt(a.dataset.req, 10)), 300); });
  };
  $('#btn-tr-md').onclick = () => { if (!LAST_CMP || !LAST_CMP.markdown) { toast('먼저 비교하세요'); return; } LW.copyText(LAST_CMP.markdown, 'trial 비교 markdown'); };
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
  // "이 숫자를 믿어도 되나" — 점수를 내기 전에 평가셋·색인 상태를 본다 (LLM 없음).
  // 오염된 색인에서 튜닝을 시작하면 며칠을 버리므로, 결과가 bad 면 평가 버튼보다 먼저 읽히게 크게 띄운다.
  async function evalCheck(quiet) {
    const h = await api('/api/eval/check');
    const box = $('#eval-health'); if (!box) return h;
    if (!h || h.error) { box.innerHTML = quiet ? '' : `<div class="banner err">${esc((h && h.error) || '점검 실패')}</div>`; return h; }
    if (h.level === 'ok' && quiet) { box.innerHTML = ''; return h; }
    const cls = h.level === 'bad' ? 'err' : (h.level === 'warn' ? 'warn' : 'ok');
    const head = { ok: '✔ 평가셋 신뢰도: 문제 없음', warn: '△ 평가셋 신뢰도: 주의', bad: '✘ 이 상태의 점수는 믿을 수 없습니다' }[h.level];
    box.innerHTML = `<div class="banner ${cls}"><b>${head}</b> <span class="muted small">문항 ${h.n} · 색인 문서 ${(h.checked || {}).docs_indexed}</span>`
      + (h.issues || []).map((i) => `<div style="margin-top:4px">[${esc(i.level)}] ${esc(i.detail)}`
        + ((i.questions || []).length ? `<div class="muted small mono">${i.questions.map(esc).join('<br>')}</div>` : '') + '</div>').join('')
      + '</div>';
    return h;
  }
  if ($('#btn-eval-check')) $('#btn-eval-check').onclick = () => evalCheck(false);

  function runEval(matrix) {
    $('#eval-log').textContent = 'running…'; $('#eval-out').innerHTML = '';
    evalCheck(true);      // 점수 옆에 신뢰도를 함께 — 나쁠 때만 뜬다
    const ro = !!($('#e-retrieval') && $('#e-retrieval').checked);
    const fx = !!($('#e-forensic') && $('#e-forensic').checked);
    api('/api/eval', { k: parseInt($('#e-k').value, 10), matrix, retrieval_only: ro, forensic: fx, overrides: overrides() }).then((j) => pollJob(j.job, $('#eval-log'), (job) => {
      if (job.status !== 'done') return;
      $('#cli-equiv').textContent = job.result.cli;
      if (job.result.matrix) {
        const rows = job.result.matrix;
        $('#eval-out').innerHTML = '<table><tr><th>combo</th><th>hit@k</th><th></th><th>MRR</th><th>term recall</th><th>answer term recall</th><th>avg ms</th><th>tokens</th></tr>' + rows.map((r) => `<tr><td><b>${r.combo}</b></td><td class="num">${fmt(r['hit@k'], 3)}</td><td><div class="bar"><i style="width:${r['hit@k'] * 100}%"></i></div></td><td class="num">${fmt(r.mrr, 3)}</td><td class="num">${fmt(r.term_recall, 3)}</td><td class="num">${fmt(r.answer_term_recall, 3)}</td><td class="num">${fmt(r.avg_ms, 0)}</td><td class="num">${fmtK(r.total_tokens || 0)}</td></tr>`).join('') + '</table>';
      } else {
        const r = job.result.result;
        $('#eval-out').innerHTML = `<div class="stat"><b>${fmt(r.summary['hit@k'], 3)}</b>hit@k</div><div class="stat"><b>${fmt(r.summary.mrr, 3)}</b>MRR</div><div class="stat"><b>${fmt(r.summary.term_recall, 3)}</b>term recall</div><div class="stat"><b>${fmt(r.summary.answer_term_recall, 3)}</b>answer term recall</div><div class="stat"><b>${fmt(r.summary.avg_ms, 0)}</b>avg ms</div><div class="stat"><b>${fmtK(r.summary.total_tokens || 0)}</b>tokens</div><div class="stat"><b>#${r.request_id || '-'}</b>request</div>` +
          '<table><tr><th></th><th>질문</th><th>rank</th><th>term</th><th>ms</th><th>tok</th><th>request</th><th>top</th></tr>' + r.rows.map((x) => `<tr><td>${x.hit ? '✔' : '<span class="bad">✘</span>'}</td><td>${esc(x.q)}</td><td class="num">${x.rank || '-'}</td><td class="num">${fmt(x.term_recall, 2)}</td><td class="num">${fmt(x.ms, 0)}${x.cached ? ' <small class="muted">(c)</small>' : ''}</td><td class="num">${fmtK(x.tokens || 0)}</td><td><a href="#" data-req="${x.request_id}">#${x.request_id || '-'}</a></td><td class="muted small">${esc(x.top.slice(0, 3).join(', '))}</td></tr>`).join('') + '</table>';
        // 변별력 없는 지표를 말해 준다 — 만점이라고 좋은 게 아니라 '안 움직이는 눈금' 일 수 있다
        const dull = Object.keys(r.discriminating || {}).filter((m) => !(r.discriminating[m] || {}).useful);
        if (dull.length) {
          $('#eval-out').innerHTML += `<div class="muted small">△ <b>변별력 없음</b>: ${dull.map(esc).join(', ')} — 모든 문항이 같은 값이라 이 지표로는 튜닝 효과를 볼 수 없습니다. hit@k·MRR 을 보세요.</div>`;
        }
        if (r.summary.retrieval_only) {
          $('#eval-out').innerHTML += `<div class="muted small">검색 전용으로 돌렸습니다 — LLM 단계를 껐고 토큰 ${fmtK(r.summary.total_tokens || 0)} 를 썼습니다. 답변 지표는 계산하지 않았습니다.</div>`;
        }
        // 놓친 문항의 원인 — 평가에서 바로 이어진다 (예전에는 기대값을 손으로 다시 적어야 했다)
        const fxs = r.forensics || [];
        if (fxs.length) {
          $('#eval-out').innerHTML += '<h3 style="margin-top:12px">놓친 문항의 원인</h3>' + fxs.map((f) => {
            if (f.error) return `<div class="prop"><b>${esc(f.q)}</b><br><span class="bad">분석 실패: ${esc(f.error)}</span></div>`;
            return `<div class="prop"><div><b>${esc(f.q)}</b> <a href="#" data-req="${f.request_id}">#${f.request_id}</a>`
              + `<div class="small muted">${(f.summary || []).slice(0, 3).map(esc).join('<br>')}</div>`
              + ((f.suggestions || []).length
                ? '<div class="small" style="margin-top:4px">' + (f.suggestions || []).slice(0, 4).map((s) =>
                  `→ <span class="pill">${esc(s.kind)}</span> ${esc((s.detail || '').slice(0, 120))}`).join('<br>') + '</div>'
                : '') + '</div></div>';
          }).join('');
        }
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
  // 목록에서 고른 행 / 마지막 질의 / 가장 최근 기록 순으로 request id 를 찾는다.
  // 예전에는 입력칸이 비면 '진단 실행' 이 아무 반응 없이 끝나 눌러도 안 되는 것처럼 보였다 (2026-09-16).
  let FX_ROWS = [], FX_SEL = null;
  function fxRequestId(quiet) {
    const typed = ($('#fx-req').value || '').trim();
    const id = typed || (FX_SEL && FX_SEL.request_id) || STATE.lastRequestId
      || ((FX_ROWS.find((r) => r.request_id) || {}).request_id) || '';
    if (!id) {
      if (!quiet) toast('진단할 request id 가 없습니다 — 아래 목록에서 한 줄을 고르거나 번호를 입력하세요');
      return null;
    }
    if (!typed) $('#fx-req').value = id;        // 무엇을 썼는지 보이게 채워 준다
    return parseInt(id, 10);
  }
  // 포렌식 목록은 **볼 이유가 있는 것부터** 보여 준다.
  // 이 저장소 실측: 기록 1,122건 중 1,025건이 `sufficient`(정상). 최근 60건을 그냥 나열하면
  // "왜 답이 부실했나" 를 보러 온 사람이 찾는 줄을 하나도 못 본다 (2026-09-20 에 고침).
  let FX_ONLY = 'problems', FX_Q = '';
  const VLABEL = { insufficient: '근거 못 찾음', weak: '근거 약함', expectation: '기대 결과', sufficient: '정상', error: '오류', '?': '미분류' };
  async function loadForensics() {
    const s = await api('/api/forensics/summary');
    const bv = s.by_verdict || {};
    const chip = (key, label, n, cls) =>
      `<button class="chip ${FX_ONLY === key ? 'on' : ''} ${cls || ''}" data-only="${esc(key)}">${esc(label)} <b>${n}</b></button>`;
    $('#fx-summary').innerHTML =
      '<div class="row fx-chips">'
      + chip('problems', '문제만', s.n_problems || 0, 'warn')
      + Object.keys(bv).sort((a, b) => bv[b] - bv[a]).map((k) => chip(k, VLABEL[k] || k, bv[k], k === 'sufficient' ? 'ok' : 'warn')).join('')
      + chip('all', '전부', s.n || 0, '')
      + `<input id="fx-q" type="search" placeholder="질의문 검색" value="${esc(FX_Q)}" style="max-width:200px">`
      + '</div>'
      + `<div class="row"><div class="stat"><b>${fmt(100 * (s.problem_rate || 0), 1)}%</b>문제 비율 <span class="muted small">(${s.n_problems}/${s.n})</span></div>`
      + `<div class="stat" title="소견이 warn/error 로 잡힌 단계 — 여기부터 고친다"><b>${esc((s.problem_stages || []).slice(0, 4).map((t) => t[0] + ' ' + t[1]).join(' · ') || '-')}</b>문제가 잡힌 단계</div>`
      + `<div class="stat"><b>${esc(Object.keys(s.suggestion_kinds || {}).map((k) => k + ' ' + s.suggestion_kinds[k]).join(' · ') || '-')}</b>제안 종류</div>`
      + `<div class="stat"><b>${esc((s.top_topics || []).slice(0, 5).map((t) => t[0]).join(', ') || '-')}</b>자주 나온 주제</div></div>`;
    $$('#fx-summary [data-only]').forEach((b) => b.onclick = () => { FX_ONLY = b.dataset.only; loadForensics(); });
    if ($('#fx-q')) {
      let t = null;
      $('#fx-q').oninput = () => { clearTimeout(t); t = setTimeout(() => { FX_Q = $('#fx-q').value; loadForensics(); }, 350); };
      $('#fx-q').onkeydown = (e) => { if (e.key === 'Enter') { clearTimeout(t); FX_Q = $('#fx-q').value; loadForensics(); } };
    }
    const qs = '?limit=60&only=' + encodeURIComponent(FX_ONLY === 'problems' || FX_ONLY === 'all' ? FX_ONLY : 'all')
      + (FX_ONLY !== 'problems' && FX_ONLY !== 'all' ? '&verdict=' + encodeURIComponent(FX_ONLY) : '')
      + (FX_Q ? '&q=' + encodeURIComponent(FX_Q) : '');
    const rows = await api('/api/forensics' + qs);
    $('#fx-list').innerHTML = '<div class="tbl-wrap"><table><tr><th>#</th><th>시각</th><th>req</th><th>판정</th><th>g</th><th>출처</th><th>질의</th><th title="이 기록에 잡힌 소견 수">소견</th></tr>' + rows.map((r) => `<tr data-id="${r.id}"><td>${r.id}</td><td class="muted small">${dt(r.ts)}</td><td>${r.request_id || '-'}</td><td><span class="pill ${r.verdict === 'sufficient' ? 'ok' : r.verdict === 'weak' ? 'warn' : 'bad'}">${esc(VLABEL[r.verdict] || r.verdict)}</span></td><td class="num">${r.groundedness == null ? '-' : fmt(r.groundedness, 2)}</td><td class="small">${esc(r.origin || '')}</td><td class="small">${esc((r.query || '').slice(0, 50))}</td><td class="num">${(r.findings || []).length}</td></tr>`).join('') + '</table></div>'
      + (rows.length ? '' : `<div class="muted">해당하는 기록이 없습니다${FX_ONLY === 'problems' ? ' — 문제로 잡힌 질의가 없다는 뜻입니다. <b>전부</b> 를 눌러 정상 건까지 봅니다.' : ''}</div>`);
    FX_ROWS = rows;
    $$('#fx-list tr[data-id]').forEach((tr) => tr.onclick = () => {
      $$('#fx-list tr').forEach((x) => x.classList.remove('sel')); tr.classList.add('sel');
      const f = rows.find((x) => String(x.id) === tr.dataset.id);
      FX_SEL = f || null;
      if (f && f.request_id) $('#fx-req').value = f.request_id;   // 고른 행을 버튼들이 바로 쓸 수 있게
      $('#fx-detail').innerHTML = renderForensic(f);
    });
  }
  $('#btn-fx-refresh').onclick = loadForensics;
  $('#btn-fx-run').onclick = async () => {
    const id = fxRequestId();
    if (id === null) return;
    $('#fx-detail').innerHTML = '진단 중…';
    const r = await api('/api/forensic?request_id=' + id + '&rerun=1');
    $('#fx-detail').innerHTML = renderForensic(r);
    if (r && !r.error) toast('request #' + id + ' 진단 완료');
    loadForensics();
  };
  $('#btn-fx-llm').onclick = async () => { const id = fxRequestId(); if (id === null) return; $('#fx-detail').innerHTML = 'LLM 분석 중…'; const r = await api('/api/forensic/llm', { request_id: parseInt(id, 10) }); if (!r.available) { $('#fx-detail').innerHTML = '<div class="banner warn">forensic 역할 LLM 이 없습니다 (Settings › 모델).</div>'; return; } const merged = Object.assign({}, r.heuristic, { request_id: id, findings: (r.heuristic.findings || []).concat(((r.llm || {}).findings || []).map((f) => Object.assign({ source: 'llm', severity: 'warn' }, f))), suggestions: (r.heuristic.suggestions || []).concat(((r.llm || {}).suggestions || []).map((s) => Object.assign({ source: 'llm' }, s))) }); $('#fx-detail').innerHTML = renderForensic(merged); };
  $('#btn-fx-consolidate').onclick = async () => { const r = await api('/api/memory', { action: 'consolidate' }); toast('consolidate: 제안 ' + (r.proposals || []).length + '건'); };
  $('#btn-fx-expect').onclick = async () => {
    const docs = $('#fx-docs').value.trim(), terms = $('#fx-terms').value.trim();
    if (!docs && !terms) { toast('기대 문서 ID 또는 용어를 입력하세요'); return; }
    const id = fxRequestId(true);      // 없으면 0 = 마지막 질의 기준 (서버가 알아서 고른다)
    $('#fx-detail').innerHTML = '기대 결과 포렌식 실행 중…';
    const rep = await api('/api/forensic/expect', { request_id: id || 0, docs, terms, note: $('#fx-note').value.trim(), propose: $('#fx-propose').checked });
    $('#fx-detail').innerHTML = LW.renderExpect ? LW.renderExpect(rep) : `<pre class="pre">${esc(rep.text || JSON.stringify(rep, null, 1))}</pre>`;
    loadForensics();
  };
  loaders.forensics = loadForensics;
  LW.renderForensic = renderForensic;
})(window.LW);
