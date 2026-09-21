/* Evolve — 제안(HITL), 메모리(에피소드·decay·consolidate). */
(function (LW) {
  'use strict';
  const { $, $$, esc, fmt, dt, api, toast, overrides, loadStatus, loaders } = LW;

  // 제안 카드 — payload JSON 원문 대신 "무엇이 · 어디서 · 어떻게 바뀌고 · 무슨 영향인지" 를 보여 준다 (2026-09-19).
  // 설명은 서버가 계산해 붙여 준다 (p.explain = evolve.describe_proposal — CLI `evolve show`, MCP wiki_evolve 와 같은 내용).
  const CHK = { error: ['✖', 'chk-error', '이대로 승인하면 실패하거나 효과가 없습니다'],
                warn: ['⚠', 'chk-warn', '확인이 필요합니다'], info: ['ℹ', 'chk-info', ''] };
  const RISK = { low: '낮음', medium: '보통', high: '높음' };

  function propCard(p, status) {
    const x = p.explain || null;
    const checks = (x && x.checks) || [];
    const bad = checks.some((c) => c.level === 'error');
    const head = `<b>#${p.id}</b> <code>${esc(p.kind)}</code> `
      + `<span class="muted small" title="신뢰도 — 제안을 만든 근거의 확신도">conf ${fmt(p.confidence, 2)}</span> `
      + `<span class="muted small" title="강도 — 같은 제안이 반복될수록 올라가고, 안 쓰이면 memory decay 로 내려갑니다">strength ${fmt(p.strength == null ? 1 : p.strength, 2)}</span> `
      + `<span class="muted small">[${esc((x && x.origin_text) || p.origin)}] ${dt(p.ts)}</span>`;
    if (!x) {   // 설명 계산 실패 시에도 화면은 살아 있어야 한다
      return `<div class="prop"><div>${head}<br><code>${esc(JSON.stringify(p.payload))}</code></div>`
        + `<div>${propButtons(p, status)}</div></div>`;
    }
    const imp = x.impact || {};
    const body = [
      `<div class="prop-title${bad ? ' bad' : ''}">${esc(x.title)}</div>`,
      `<div class="prop-what">${esc(x.what)}</div>`,
      `<div class="prop-where"><b>바뀌는 곳</b> <code>${esc(x.target)}</code></div>`,
      (x.diff || []).length ? `<pre class="prop-diff">${(x.diff || []).map((l) => {
        const cls = l.startsWith('+') ? 'dif-add' : (l.startsWith('-') ? 'dif-del' : '');
        return `<span class="${cls}">${esc(l)}</span>`;
      }).join('\n')}</pre>` : '',
      `<div class="prop-impact"><b>영향</b>`
        + `<div>· 리빌드: ${esc(imp.rebuild_text || '')}</div>`
        + `<div>· 범위: ${esc(imp.scope || '')}</div>`
        + `<div>· 위험도: ${esc(RISK[imp.risk] || imp.risk || '')}${imp.auto_apply ? ' <span class="pill small" title="config.json evolve_auto_apply_kinds 에 든 종류 — 자동 적용이 켜져 있으면 사람 승인 없이 적용될 수 있습니다">자동 적용 대상</span>' : ''}</div>`
        + `<div>· 되돌리기: ${esc(imp.revert || '')}</div></div>`,
      checks.length ? `<div class="prop-checks">` + checks.map((c) => {
        const m = CHK[c.level] || CHK.info;
        return `<div class="${m[1]}" title="${esc(m[2])}">${m[0]} ${esc(c.text)}`
          + (c.fix ? `<span class="muted"> → ${esc(c.fix)}</span>` : '') + `</div>`;
      }).join('') + `</div>` : '',
      `<details class="prop-why"><summary>제안 근거 · 원문</summary>`
        + `<div class="muted small">${esc(x.why || p.reason || '')}</div>`
        + ((x.queries || []).length ? `<div class="small">관련 질의: ${(x.queries).map((q) => `<a href="#" data-askq="${esc(q)}" title="이 질의를 Ask 탭에서 실행해 봅니다">${esc(q)}</a>`).join(' · ')}</div>` : '')
        + (x.events ? `<div class="small muted">반복 관측 ${x.events}회</div>` : '')
        + `<code class="small">${esc(JSON.stringify(p.payload))}</code></details>`,
      p.eval_before ? `<div class="small muted">before ${esc(p.eval_before)} → after ${esc(p.eval_after)}</div>` : '',
    ].join('');
    return `<div class="prop${bad ? ' prop-bad' : ''}"><div class="prop-body">${head}${body}</div>`
      + `<div class="prop-act">${propButtons(p, status, x)}</div></div>`;
  }

  function propButtons(p, status, x) {
    if (status !== 'proposed') return `<span class="pill">${esc(p.status)}</span>`;
    const bad = x && !x.applicable;
    return `<button data-apply="${p.id}"${bad ? ' class="secondary" title="점검에서 오류가 나온 제안입니다 — 적용은 막혀 있습니다(거절을 권합니다)"' : ''}>승인·적용</button>`
      + ` <button class="secondary" data-reject="${p.id}">거절</button>`;
  }

  async function loadEvolve() {
    const s = await api('/api/evolve/status');
    const status = $('#ev-status').value;
    const list = status === 'proposed' ? s.pending : await api('/api/evolve/proposals?status=' + status);
    // 종류 드롭다운은 서버 목록으로 채운다 (evolve.KINDS) — 설명은 툴팁에
    const kinds = s.kinds || {};
    const sel = $('#mp-kind');
    if (sel && Object.keys(kinds).length && sel.options.length !== Object.keys(kinds).length) {
      const cur = sel.value;
      sel.innerHTML = Object.keys(kinds).map((k) => `<option value="${esc(k)}" title="${esc(kinds[k])}">${esc(k)}</option>`).join('');
      if (cur) sel.value = cur;
    }
    if (sel) sel.title = kinds[sel.value] || '';
    $('#ev-summary').innerHTML = `auto_apply=<b>${s.auto_apply}</b> · min_confidence=${s.min_confidence} · pending=${s.pending.length} · applied=${s.applied.length}` +
      (s.auto_apply ? ` <span class="muted small" title="자동 적용을 허용한 종류 — config.json evolve_auto_apply_kinds. 여기에 없는 종류는 사람이 승인해야 적용됩니다">· 자동 적용 허용: ${(s.auto_apply_kinds || []).map(esc).join(', ') || '(없음)'}</span>` : '') +
      (s.snapshot_keep ? ` <span class="muted small" title="적용마다 만드는 자동 스냅샷 보관 개수 — config.json evolve_snapshot_keep">· 스냅샷 보관 ${s.snapshot_keep}개</span>` : '');
    $('#ev-pending').innerHTML = list.length ? list.map((p) => propCard(p, status)).join('')
      : '<div class="muted">해당 상태의 제안이 없습니다.</div>';
    // 근거 질의를 눌러 Ask 탭에서 바로 재현해 본다 — "정말 이 제안이 필요한가" 를 화면에서 확인하는 가장 빠른 길
    $$('#ev-pending [data-askq]').forEach((a) => a.onclick = (e) => {
      e.preventDefault();
      const box = $('#q');
      if (box) { box.value = a.dataset.askq; }
      location.hash = '#ask/query';
      if (box) box.focus();
    });
    $$('#ev-pending [data-apply]').forEach((b) => b.onclick = async () => { b.disabled = true; b.textContent = '적용 중(평가 포함)…'; const j = await api('/api/evolve/apply', { id: parseInt(b.dataset.apply, 10), evaluate: $('#ev-eval').checked });
      toast(j.error ? ('적용 안 됨 — ' + j.error)
        : ('결과: ' + j.status + (j.after ? ` hit@k ${j.before['hit@k']}→${j.after['hit@k']}, term ${j.before.term_recall}→${j.after.term_recall}` : '')));
      loadEvolve(); loadStatus(); });
    // 거절 사유를 함께 보낸다 (2026-09-19). 예전에는 id 만 보내서, 왜 거절했는지가 어디에도 남지 않았다 —
    // 같은 제안이 다시 올라왔을 때 지난번에 왜 반려했는지 알 길이 없었다. CLI `evolve reject <id> 사유` 와 같아졌다.
    $$('#ev-pending [data-reject]').forEach((b) => b.onclick = async () => {
      const note = prompt('거절 사유 (evolution_log 에 남습니다 · 비워도 됩니다)', '') ;
      if (note === null) return;                      // 취소
      await api('/api/evolve/reject', { id: parseInt(b.dataset.reject, 10), note: note });
      loadEvolve();
    });
    $('#ev-log').innerHTML = '<table><tr><th>ts</th><th>proposal</th><th>action</th><th>detail</th><th>checksum</th></tr>' + s.recent_log.map((l) => `<tr><td>${dt(l.ts)}</td><td>#${l.proposal_id}</td><td>${esc(l.action)}</td><td>${esc((l.detail || '').slice(0, 200))}</td><td class="muted">${(l.checksum || '').slice(0, 8)}</td></tr>`).join('') + '</table>';
    $('#ev-syn').textContent = JSON.stringify(s.synonyms);
  }
  $('#btn-ev-refresh').onclick = loadEvolve; $('#ev-status').onchange = loadEvolve;
  $('#btn-ev-review').onclick = async () => { const j = await api('/api/evolve/review', { overrides: overrides() }); toast(j.error ? 'LLM 리뷰 실패: ' + j.error : '제안 ' + (j.proposals || []).length + '건 생성'); loadEvolve(); };
  $('#btn-mp-add').onclick = async () => { let payload; try { payload = JSON.parse($('#mp-payload').value); } catch (e) { toast('payload JSON 오류'); return; } const r = await api('/api/evolve/propose', { kind: $('#mp-kind').value, payload, reason: $('#mp-reason').value || 'manual' }); if (r && r.error) { toast('제안 실패: ' + r.error); return; } loadEvolve(); };
  if ($('#mp-kind')) $('#mp-kind').onchange = () => { const s = $('#mp-kind'); const o = s.options[s.selectedIndex]; s.title = (o && o.title) || ''; };
  // 자동 적용을 손으로 한 번 (CLI `evolve auto-apply` · 스케줄러 evolve op=auto_apply 와 같은 규칙)
  if ($('#btn-ev-auto')) $('#btn-ev-auto').onclick = async () => {
    const dry = $('#ev-auto-dry').checked;
    const max = parseInt($('#ev-auto-max').value, 10) || 5;
    if (!dry && !confirm('허용된 종류의 제안을 최대 ' + max + '건 자동 적용합니다. 회귀 평가가 함께 돌고, 악화되면 되돌립니다. 진행할까요?')) return;
    $('#ev-auto-out').textContent = dry ? '대상 확인 중…' : '적용 중(평가 포함)…';
    const j = await api('/api/evolve/auto_apply', { dry_run: dry, max_apply: max, evaluate: true });
    if (!j || j.error) { $('#ev-auto-out').innerHTML = `<span class="bad">${esc((j && j.error) || '실패')}</span>`; return; }
    const picked = j.picked || [];
    $('#ev-auto-out').innerHTML = `대상 <b>${picked.length}</b>건 (신뢰도 ≥ ${j.min_confidence} · 종류 ${(j.kinds || []).map(esc).join(', ')})` +
      (picked.length ? ' — ' + picked.map((x) => `#${x.id} ${esc(x.kind)}`).join(', ') : '') +
      (j.dry_run ? ' <span class="muted">(미리보기 — 체크를 풀고 다시 누르면 적용)</span>'
        : ` · 적용 ${(j.applied || []).length}건` + ((j.errors || []).length ? ` · <span class="bad">오류 ${j.errors.length}건</span>` : ''));
    if (!j.dry_run) loadEvolve();
  };
  loaders.evolve = loadEvolve;

  // ---------------- MEMORY ----------------
  // 이 화면은 "시스템이 스스로 배운 것"을 본다. 예전에는 통계 숫자 한 줄과 에피소드 표 하나뿐이라
  // **무엇을 하는 화면인지, 눌러서 어디로 가는지** 알 수 없었다 (2026-09-19 사용자 보고).
  // 그래서 셋을 더했다: (1) 지금 검색이 받는 피드백 부스트 목록 (2) 행에서 문서·요청·질의로 건너뛰기
  // (3) 사라지기 직전인 제안. 숫자 대신 **목록과 이동**이 이 화면의 쓸모다.
  const MEM = { only: '', q: '' };

  function memGoDoc(id) { if (LW.openDocPage) LW.openDocPage(id); else { LW.switchGroup('knowledge'); LW.switchTab('doc'); } }
  function memGoRequest(id) {
    LW.switchGroup('observability'); LW.switchTab('requests');
    setTimeout(() => LW.openRequest && LW.openRequest(id), 300);
  }
  function memAskAgain(q) {
    LW.switchGroup('ask'); LW.switchTab('query');
    setTimeout(() => { const el = $('#q'); if (el) { el.value = q; el.focus(); } }, 200);
  }

  async function loadMemory() {
    const m = await api('/api/memory?limit=40&only=' + encodeURIComponent(MEM.only) + '&q=' + encodeURIComponent(MEM.q));
    const props = m.proposals || {};
    const pTxt = Object.keys(props).map((k) => `${k} ${props[k]}`).join(' · ') || '없음';
    $('#mem-status').innerHTML =
      `<div class="stat" title="질의 1건 = 에피소드 1건. 토글 evolve_capture 로 쌓입니다"><b>${Array.isArray(m.episodes) ? m.episodes.length : (m.episodes || 0)}</b>에피소드</div>` +
      `<div class="stat" title="그중 👍/👎 를 받은 것 — 아래 부스트의 원천"><b>${m.episodes_with_feedback}</b>피드백 있음</div>` +
      `<div class="stat" title="지금 가중치를 받고 있는 청크 수 (아래 표)"><b>${m.feedback_chunks}</b>부스트 청크</div>` +
      `<div class="stat" title="미승인 제안의 평균 strength. 낮을수록 곧 보관됩니다"><b>${fmt(m.avg_strength_proposed, 2)}</b>제안 평균 강도</div>` +
      `<div class="stat" title="tuning memory_half_life_days — 이 날짜가 지나면 영향이 절반이 됩니다"><b>${m.half_life_days}일</b>반감기</div>` +
      `<div class="stat" title="'왜 못 찾았나' 기록 — consolidate 의 원천"><b>${m.forensics}</b>포렌식</div>` +
      `<div class="stat" title="제안 상태별 개수 (Evolve 탭에서 봅니다)"><b>${esc(pTxt)}</b>제안 상태</div>`;

    // ---- 피드백 부스트: 이 화면의 핵심. "내 피드백이 검색에 어떻게 반영됐나" ----
    const bs = m.boosts || [];
    const bar = (w) => {
      const p = Math.min(100, Math.round(Math.abs(w) * 100));
      return `<span class="bst" title="가중치 ${fmt(w, 3)}">${w < 0 ? `<i class="neg" style="width:${p}%"></i>` : '<i></i>'}${w > 0 ? `<i class="pos" style="width:${p}%"></i>` : ''}</span>`;
    };
    $('#mem-boosts').innerHTML = bs.length
      ? '<div class="tbl-wrap"><table class="tbl"><thead><tr><th>가중치</th><th></th><th>문서 · 제목</th><th>청크</th><th title="이 가중치를 만든 피드백 수">근거</th><th></th></tr></thead><tbody>'
        + bs.map((b) => `<tr data-bdoc="${esc(b.doc_id)}" style="cursor:pointer" title="${esc((b.episodes || []).map((x) => (x.feedback > 0 ? '👍 ' : '👎 ') + (x.query || '')).join('\n'))}">`
          + `<td class="num ${b.weight < 0 ? 'bad' : 'ok'}">${b.weight > 0 ? '+' : ''}${fmt(b.weight, 3)}</td>`
          + `<td>${bar(b.weight)}</td>`
          + `<td>${esc(b.doc_id)}${b.heading ? ` <span class="muted small">${esc(String(b.heading).slice(0, 50))}</span>` : ''}`
          + `${b.exists ? '' : ' <span class="warntxt small">색인에 없음</span>'}</td>`
          + `<td class="mono small muted">${esc(String(b.chunk_id).split('#').pop())}</td>`
          + `<td class="num">${b.n_episodes}</td>`
          + `<td><button class="mini secondary" data-bgo="${esc(b.doc_id)}">문서 →</button></td></tr>`).join('')
        + '</tbody></table></div>'
        + '<div class="muted small">양수는 그 청크를 <b>위로</b>, 음수는 <b>아래로</b> 밀어냅니다. 반감기가 지나면 절반으로 줄고, 재빌드로 청크가 사라지면 “색인에 없음” 으로 남습니다(무해).</div>'
      : '<div class="muted small">아직 없습니다 — Ask 화면에서 답변에 👍/👎 를 누르면 그 근거 청크가 여기 쌓이고, 다음 질의부터 순위에 반영됩니다.</div>';
    $$('#mem-boosts [data-bgo]').forEach((b) => b.onclick = (e) => { e.stopPropagation(); memGoDoc(b.dataset.bgo); });
    $$('#mem-boosts tr[data-bdoc]').forEach((tr) => tr.onclick = () => memGoDoc(tr.dataset.bdoc));

    // ---- 에피소드: 눌러서 그때로 돌아갈 수 있어야 기록이 쓸모가 있다 ----
    // m.episodes 는 '개수', m.recent 가 최근 에피소드 목록이다 (예전 버전 호환: 배열이면 그대로 쓴다)
    const eps = m.recent || (Array.isArray(m.episodes) ? m.episodes : []);
    $('#mem-episodes').innerHTML = eps.length
      ? '<div class="tbl-wrap"><table class="tbl"><thead><tr><th>#</th><th>시각</th><th>질의</th><th>종류</th><th>결과</th><th>피드백</th><th title="재사용하면 올라가고 시간이 지나면 내려갑니다">강도</th><th>근거 청크</th><th></th></tr></thead><tbody>'
        + eps.map((e) => `<tr><td class="muted small">${e.id}</td><td class="muted small">${dt(e.ts)}</td>`
          + `<td>${esc(e.query)}</td><td class="small">${esc(e.kind)}</td><td class="small">${esc(e.outcome)}</td>`
          + `<td>${e.feedback == null ? '<span class="muted">-</span>' : e.feedback > 0 ? '👍' : '👎'}</td>`
          + `<td class="num">${fmt(e.strength, 2)}</td>`
          + `<td class="small">${(e.chunks || []).slice(0, 3).map((c) => `<a href="#" data-echunk="${esc(String(c).split('#')[0])}" title="${esc(c)}">${esc(String(c).split('/').pop())}</a>`).join(' ') || '<span class="muted">-</span>'}${(e.chunks || []).length > 3 ? ` <span class="muted">+${e.chunks.length - 3}</span>` : ''}</td>`
          + `<td style="white-space:nowrap"><button class="mini secondary" data-eask="${esc(e.query)}" title="같은 질문을 Ask 화면에 채워 넣습니다">↩</button>`
          + `${e.request_id ? ` <button class="mini secondary" data-ereq="${e.request_id}" title="그때의 요청 프로파일(단계별 실측·근거)">📄</button>` : ''}</td></tr>`).join('')
        + '</tbody></table></div>'
      : '<div class="muted small">조건에 맞는 에피소드가 없습니다.</div>';
    $$('#mem-episodes [data-echunk]').forEach((a) => a.onclick = (e) => { e.preventDefault(); memGoDoc(a.dataset.echunk); });
    $$('#mem-episodes [data-eask]').forEach((b) => b.onclick = () => memAskAgain(b.dataset.eask));
    $$('#mem-episodes [data-ereq]').forEach((b) => b.onclick = () => memGoRequest(parseInt(b.dataset.ereq, 10)));

    // ---- 감쇠 중인 제안: 사라지기 **전에** 보여 준다 ----
    const dec = m.decaying || [];
    $('#mem-decaying').innerHTML = dec.length
      ? '<div class="tbl-wrap"><table class="tbl"><thead><tr><th>#</th><th>종류</th><th>내용</th><th>지금 강도</th><th>남은 날</th><th></th></tr></thead><tbody>'
        + dec.map((d) => `<tr class="${d.at_risk ? 'has-err' : ''}"><td class="muted small">${d.id}</td><td><code>${esc(d.kind)}</code></td>`
          + `<td class="small">${esc(JSON.stringify(d.payload || {}).slice(0, 90))}<br><span class="muted">${esc((d.reason || '').slice(0, 80))}</span></td>`
          + `<td class="num">${fmt(d.strength_now, 3)}</td>`
          + `<td class="num">${d.days_left == null ? '<span class="muted">감쇠 없음</span>' : (d.days_left <= 0 ? '<span class="bad">다음 감쇠에 보관</span>' : d.days_left)}</td>`
          + '<td><button class="mini secondary" data-dgo="1">Evolve 에서 보기 →</button></td></tr>').join('')
        + '</tbody></table></div>'
        + `<div class="muted small">강도가 <code>${fmt(m.archive_strength, 2)}</code> 아래로 내려가면 <b>보관(archived)</b> 됩니다 — 삭제가 아니라 Evolve 에서 status=archived 로 계속 볼 수 있습니다. 살리려면 승인하거나 같은 제안이 다시 올라와야 합니다.</div>`
      : '<div class="muted small">감쇠 중인 미승인 제안이 없습니다.</div>';
    $$('#mem-decaying [data-dgo]').forEach((b) => b.onclick = () => { LW.switchGroup('evolve'); LW.switchTab('evolve'); });
  }
  $('#btn-mem-refresh').onclick = loadMemory;
  if ($('#mem-only')) $('#mem-only').onchange = () => { MEM.only = $('#mem-only').value; loadMemory(); };
  if ($('#mem-q')) {
    let t = null;
    $('#mem-q').addEventListener('input', () => { clearTimeout(t); t = setTimeout(() => { MEM.q = $('#mem-q').value.trim(); loadMemory(); }, 300); });
  }
  // 결과를 JSON 덩어리로 toast 하던 것을 **문장**으로. 무엇이 일어났는지 읽을 수 있어야 한다.
  $('#btn-mem-decay').onclick = async () => {
    if (!confirm('지금 한 번 감쇠시킵니다.\n\n오래 안 쓰인 제안의 강도가 줄고, 임계 아래로 내려간 것은 보관(archived)됩니다.\n삭제는 아니며 Evolve 에서 계속 볼 수 있습니다. 진행할까요?')) return;
    const r = await api('/api/memory', { action: 'decay' });
    if (!r || r.error) { toast('감쇠 실패: ' + ((r && r.error) || '')); return; }
    const msg = `감쇠 완료 — 제안 ${r.proposals_decayed}건 약해짐 · ${r.proposals_archived}건 보관 · 고정 근거 ${r.pins_decayed}건 조정`;
    $('#mem-act-out').textContent = msg; toast(msg);
    loadMemory();
  };
  $('#btn-mem-consolidate').onclick = async () => {
    const r = await api('/api/memory', { action: 'consolidate' });
    if (!r || r.error) { toast('실패: ' + ((r && r.error) || '')); return; }
    const n = (r.proposals || []).length;
    const msg = n
      ? `반복 실패 ${r.groups}묶음에서 제안 ${n}건 생성 (#${(r.proposals || []).join(', #')}) — Evolve 탭에서 승인하세요`
      : `새로 만들 제안이 없습니다 (묶음 ${r.groups}개 · 같은 소견이 ${r.min_events}번 이상 반복돼야 제안이 됩니다)`;
    $('#mem-act-out').textContent = msg; toast(msg);
    loadMemory();
  };
  loaders.memory = loadMemory;
})(window.LW);
