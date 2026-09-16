/* Evolve — 제안(HITL), 메모리(에피소드·decay·consolidate). */
(function (LW) {
  'use strict';
  const { $, $$, esc, fmt, dt, api, toast, overrides, loadStatus, loaders } = LW;

  async function loadEvolve() {
    const s = await api('/api/evolve/status');
    const status = $('#ev-status').value;
    const list = status === 'proposed' ? s.pending : await api('/api/evolve/proposals?status=' + status);
    $('#ev-summary').textContent = `auto_apply=${s.auto_apply} · min_confidence=${s.min_confidence} · pending=${s.pending.length} · applied=${s.applied.length}`;
    $('#ev-pending').innerHTML = list.length ? list.map((p) => `<div class="prop"><div><b>#${p.id}</b> <code>${esc(p.kind)}</code> conf=${fmt(p.confidence, 2)} strength=${fmt(p.strength == null ? 1 : p.strength, 2)} <span class="muted">[${esc(p.origin)}] ${dt(p.ts)}</span><br><code>${esc(JSON.stringify(p.payload))}</code><br><span class="muted">${esc(p.reason)}</span>${p.eval_before ? `<br><span class="small muted">before ${esc(p.eval_before)} → after ${esc(p.eval_after)}</span>` : ''}</div><div>${status === 'proposed' ? `<button data-apply="${p.id}">승인·적용</button> <button class="secondary" data-reject="${p.id}">거절</button>` : `<span class="pill">${esc(p.status)}</span>`}</div></div>`).join('') : '<div class="muted">해당 상태의 제안이 없습니다.</div>';
    $$('#ev-pending [data-apply]').forEach((b) => b.onclick = async () => { b.disabled = true; b.textContent = '적용 중(평가 포함)…'; const j = await api('/api/evolve/apply', { id: parseInt(b.dataset.apply, 10), evaluate: $('#ev-eval').checked }); toast('결과: ' + j.status + (j.after ? ` hit@k ${j.before['hit@k']}→${j.after['hit@k']}, term ${j.before.term_recall}→${j.after.term_recall}` : '')); loadEvolve(); loadStatus(); });
    $$('#ev-pending [data-reject]').forEach((b) => b.onclick = async () => { await api('/api/evolve/reject', { id: parseInt(b.dataset.reject, 10) }); loadEvolve(); });
    $('#ev-log').innerHTML = '<table><tr><th>ts</th><th>proposal</th><th>action</th><th>detail</th><th>checksum</th></tr>' + s.recent_log.map((l) => `<tr><td>${dt(l.ts)}</td><td>#${l.proposal_id}</td><td>${esc(l.action)}</td><td>${esc((l.detail || '').slice(0, 200))}</td><td class="muted">${(l.checksum || '').slice(0, 8)}</td></tr>`).join('') + '</table>';
    $('#ev-syn').textContent = JSON.stringify(s.synonyms);
  }
  $('#btn-ev-refresh').onclick = loadEvolve; $('#ev-status').onchange = loadEvolve;
  $('#btn-ev-review').onclick = async () => { const j = await api('/api/evolve/review', { overrides: overrides() }); toast(j.error ? 'LLM 리뷰 실패: ' + j.error : '제안 ' + (j.proposals || []).length + '건 생성'); loadEvolve(); };
  $('#btn-mp-add').onclick = async () => { let payload; try { payload = JSON.parse($('#mp-payload').value); } catch (e) { toast('payload JSON 오류'); return; } await api('/api/evolve/propose', { kind: $('#mp-kind').value, payload, reason: $('#mp-reason').value || 'manual' }); loadEvolve(); };
  loaders.evolve = loadEvolve;

  async function loadMemory() {
    const m = await api('/api/memory?limit=40');
    $('#mem-status').innerHTML = `<div class="stat"><b>${Array.isArray(m.episodes) ? m.episodes.length : (m.episodes || 0)}</b>에피소드</div><div class="stat"><b>${m.episodes_with_feedback}</b>피드백 있음</div><div class="stat"><b>${m.feedback_chunks}</b>부스트 청크</div><div class="stat"><b>${fmt(m.avg_strength_proposed, 2)}</b>제안 평균 strength</div><div class="stat"><b>${m.half_life_days}d</b>반감기</div><div class="stat"><b>${m.forensics}</b>포렌식</div><div class="stat"><b>${esc(JSON.stringify(m.proposals || {}))}</b>제안 상태</div>`;
    // m.episodes 는 '개수', m.recent 가 최근 에피소드 목록이다 (예전 버전 호환: 배열이면 그대로 쓴다)
    const eps = m.recent || (Array.isArray(m.episodes) ? m.episodes : []);
    $('#mem-episodes').innerHTML = '<table><tr><th>#</th><th>시각</th><th>질의</th><th>kind</th><th>outcome</th><th>fb</th><th>strength</th><th>청크</th></tr>' + eps.map((e) => `<tr><td>${e.id}</td><td class="muted small">${dt(e.ts)}</td><td>${esc(e.query)}</td><td>${esc(e.kind)}</td><td>${esc(e.outcome)}</td><td>${e.feedback == null ? '' : e.feedback > 0 ? '👍' : '👎'}</td><td class="num">${fmt(e.strength, 2)}</td><td class="muted small">${esc((e.chunks || []).slice(0, 2).join(', '))}</td></tr>`).join('') + '</table>';
  }
  $('#btn-mem-refresh').onclick = loadMemory;
  $('#btn-mem-decay').onclick = async () => { toast(JSON.stringify(await api('/api/memory', { action: 'decay' }))); loadMemory(); };
  $('#btn-mem-consolidate').onclick = async () => { toast(JSON.stringify(await api('/api/memory', { action: 'consolidate' }))); loadMemory(); };
  loaders.memory = loadMemory;
})(window.LW);
