/* 🧭 Pipeline — 흐름 · 단계 블록 · 토글/config/튜닝 상세 · 범위 스윕 (2026-09-18, IMPLEMENTATION_PLAN_0918_2 §2.6 · docs/PIPELINE_PAGE.md).
   세 열: 왼쪽 흐름(query·build·evolve·watch) → 가운데 단계 블록(위→아래) → 오른쪽 선택한 단계의 상세.
   데이터: GET /api/architecture (architecture.py 레지스트리 + 현재 toggles/settings/tuning + 최근 실행 trace)
         · GET /api/tuning (튜닝 항목의 type/min/max/choices) · GET /api/sweep/keys · GET/POST /api/sweep (llmwiki/sweep.py).
   상태는 하나: 토글은 사이드바의 [data-toggle] 체크박스, 튜닝 '이번 요청에만' 은 core.js TUNING_OV/SETTING_OV → 둘 다 LW.overrides() 로
   Ask 질의·⟲ 재실행·평가·스윕에 함께 실린다. 파일에 남기는 것은 'tuning.json 저장'(/api/tuning set, edit 등급) 과 config 저장(/api/config, admin) 뿐. */
(function (LW) {
  'use strict';
  const { $, $$, esc, fmt, fmtK, dt, api, toast, STATE, loaders, overrides, switchTab, switchGroup, loadStatus, copyText, pollJob, flatten, setTuningOverride, tuningOverrides,
    fmtLimit, limitTitle, limitPct } = LW;

  const PL = { arch: null, tun: {}, flow: 'query', sel: null, selPhase: null, view: 'blocks', phaseCollapsed: {},
    keys: null, sweeps: [], rec: null, cmp: null, text: '', edits: {}, cfgEdits: {}, loading: false };
  const FLOW_ORDER = ['query', 'build', 'evolve', 'watch'];
  const FLOW_KIND = { query: 'query', build: 'build', watch: 'build', evolve: 'eval' };   // /api/architecture last[kind] 와 같은 대응 (observability.js 와 동일)
  const ROLE_STAGE = { answer: 'answer', rerank: 'rerank', verify: 'claim', expand: 'query_expand', fusion: 'rrf_fuse', select: 'rerank' };   // 역할 LLM 스윕 키 → 단계

  // ---------------- 권한 (버튼을 미리 막고 이유를 툴팁에 — 눌러서 403 을 보게 하지 않는다) ----------------
  const ROLE_ORDER = ['viewer', 'class3', 'class2', 'class1', 'builder', 'admin'];
  function roleRank(r) { const roles = (STATE.auth && STATE.auth.roles) || ROLE_ORDER; return roles.indexOf(r); }
  function myRole() { const a = STATE.auth; if (!a || a.mode === 'off') return 'admin'; return (a.user && a.user.role) || 'viewer'; }
  function needRole(level) { const a = STATE.auth; const lv = a && a.permissions && a.permissions.levels; return (lv && lv[level]) || { edit: 'class2', admin: 'admin' }[level] || 'admin'; }
  function can(level) { return roleRank(myRole()) >= roleRank(needRole(level)); }

  // ---------------- 읽기 ----------------
  function indexTun(j) { PL.tun = {}; (j && j.tunables || []).forEach((r) => { PL.tun[r.key] = r; }); }
  async function loadPipeline() {
    if (PL.loading) return; PL.loading = true;
    const msg = $('#pl-msg'); msg.textContent = '읽는 중…';
    try {
      const [arch, keys, tun] = await Promise.all([api('/api/architecture'), api('/api/sweep/keys'), api('/api/tuning')]);
      if (!arch || arch.error || !arch.flows) { msg.textContent = '구조 정보를 읽지 못했습니다'; return; }
      PL.arch = arch;
      if (keys && !keys.error) PL.keys = keys;
      if (tun && tun.tunables) indexTun(tun);
      if (!arch.flows[PL.flow]) PL.flow = Object.keys(arch.flows)[0];
      syncViewSeg();
      renderFlows(); renderBlocks(); renderDetail(); fillSweepForm();
      msg.textContent = '';
      loadSweepList();
    } finally { PL.loading = false; }
  }
  async function reloadArch() {
    const [arch, tun] = await Promise.all([api('/api/architecture'), api('/api/tuning')]);
    if (arch && arch.flows) PL.arch = arch;
    if (tun && tun.tunables) indexTun(tun);
    renderBlocks(); renderDetail(); fillSweepForm();
  }
  // 다른 화면(Settings › config/튜닝/모델/프리셋)에서 서버 값을 바꾸면 여기 캐시(PL.arch)도 버린다.
  // 이 화면이 숨어 있으면 다시 그릴 필요가 없으므로 캐시만 비우고, 열릴 때 loadPipeline() 이 채운다.
  LW.onSettingsChanged(() => {
    if (!LW.tabVisible || !LW.tabVisible('pipeline')) { PL.arch = null; return; }
    reloadArch().catch((e) => console.error('pipeline reload', e));
  });

  // ---------------- 마지막 실행 시간: 이 화면의 마지막 질의(STATE.lastTrace) → 없으면 서버의 최근 요청(/api/architecture last) ----------------
  // '마지막 실행 시간' 체크를 끄면 시간 표시를 통째로 뺀다 (구조만 읽고 싶을 때 — 예전 구조·흐름 탭의 같은 옵션).
  function showLast() { const c = $('#pl-last'); return !c || c.checked; }
  function liveToggles() { const c = $('#pl-live'); return !c || c.checked; }
  function lastTraceFor(fk) {
    if (!showLast()) return null;
    if (fk === 'query' && STATE.lastTrace) return { trace: STATE.lastTrace, id: STATE.lastRequestId, src: '이 화면의 마지막 질의' };
    const kind = FLOW_KIND[fk]; const last = PL.arch && PL.arch.last && PL.arch.last[kind];
    return last && last.trace ? { trace: last.trace, id: last.id, src: '서버의 최근 ' + kind + ' 요청', summary: last.summary } : null;
  }
  function stageLast(fk, st) {
    const lt = lastTraceFor(fk); if (!lt) return null;
    const names = (st.trace && st.trace.length) ? st.trace : [st.key];
    const nodes = flatten(lt.trace).filter((n) => names.includes(n.name));
    if (!nodes.length) return { missing: true };
    const en = nodes.filter((n) => n.enabled !== false);
    const ms = en.reduce((a, n) => a + (n.ms || 0), 0);
    return { ms, total: lt.trace.ms || 1, skipped: !en.length, reason: !en.length ? ((nodes[0].meta || {}).reason || 'skipped') : '', error: nodes.some((n) => n.error), nodes, id: lt.id, src: lt.src };
  }
  // ---------------- 단계별 시간 제한 (architecture.stage_limits · /api/architecture limits) ----------------
  // 실측 ms 옆에 "이 단계를 끊을 수 있는 값" 을 같이 둔다. 어디서 고치는지(파일·키)까지 툴팁에 붙여
  // '느리다 → 제한을 올릴지 단계를 끌지' 를 이 화면에서 바로 판단할 수 있게 한다.
  function limitsOf(st) {
    const rows = ((PL.arch && PL.arch.limits && PL.arch.limits.stages) || {})[st.key];
    return rows && rows.length ? rows : null;
  }
  function flowLimits(fk) {
    const rows = ((PL.arch && PL.arch.limits && PL.arch.limits.flows) || {})[fk];
    return rows && rows.length ? rows : [];
  }
  // 단계 블록/표에 붙는 작은 칩. li 가 있으면 실측이 제한의 몇 %인지에 따라 색이 바뀐다.
  function limChip(st, li) {
    const rows = limitsOf(st); if (!rows) return '';
    const top = rows[0], p = li && !li.skipped && !li.missing ? limitPct(li.ms, top.s) : 0;
    const cls = p >= 1 ? ' over' : p >= 0.8 ? ' near' : '';
    return `<span class="chip lim${cls}" title="이 단계를 끊을 수 있는 시간 제한&#10;${esc(limitTitle(rows))}">≤ ${esc(fmtLimit(top.s))}</span>`;
  }
  // '요청 토글 상태 반영' 을 끄면 서버 config.json 값으로 그린다 (배포 기본값이 어떤 모습인지 보려는 것).
  function toggleOn(t) {
    if (!liveToggles()) return !!(PL.arch && PL.arch.toggles[t]);
    const cb = $(`[data-toggle="${t}"]`); return cb ? cb.checked : !!(PL.arch && PL.arch.toggles[t]);
  }
  function curStage() { const f = PL.arch && PL.arch.flows[PL.flow]; return f ? f.stages.find((s) => s.key === PL.sel) : null; }
  function stagesOf(fk, ph) { const f = PL.arch.flows[fk]; return ph.stages.map((k) => f.stages.find((s) => s.key === k)).filter(Boolean); }
  function curPhase() { const f = PL.arch && PL.arch.flows[PL.flow]; return f ? (f.phases || []).find((p) => p.key === PL.selPhase) : null; }
  /** 페이즈 합계: 소요 ms·비중, 켜짐/꺼짐 토글 수, 튜닝 수, 오버라이드 수, 건너뛴 단계 수, 오류 여부. */
  function phaseSum(fk, ph) {
    const ovs = tuningOverrides();
    const out = { ms: 0, total: 0, on: 0, off: 0, tune: 0, ov: 0, skipped: 0, err: false, known: false, stages: 0 };
    stagesOf(fk, ph).forEach((st) => {
      out.stages++;
      st.toggles.forEach((t) => (toggleOn(t) ? out.on++ : out.off++));
      out.tune += st.tunables.length;
      out.ov += st.tunables.filter((t) => (t.key in ovs.tuning) || (t.key in ovs.settings)).length;
      const li = stageLast(fk, st);
      if (li && !li.missing) {
        out.known = true; out.total = li.total;
        if (li.skipped) out.skipped++; else out.ms += li.ms;
        if (li.error) out.err = true;
      }
    });
    return out;
  }

  // ---------------- 왼쪽: 흐름 ----------------
  function renderFlows() {
    const flows = FLOW_ORDER.filter((k) => PL.arch.flows[k]).concat(Object.keys(PL.arch.flows).filter((k) => !FLOW_ORDER.includes(k)));
    $('#pl-flows').innerHTML = flows.map((fk) => {
      const f = PL.arch.flows[fk]; const lt = lastTraceFor(fk);
      return `<button type="button" data-flow="${esc(fk)}" class="${fk === PL.flow ? 'active' : ''}" title="${esc(f.desc)}&#10;진입: ${esc(f.entry)}">${esc(fk)}<span class="n">${esc(f.title)} · ${f.stages.length}단계${lt ? ' · 최근 #' + esc(String(lt.id)) : ''}</span></button>`;
    }).join('');
    $$('#pl-flows button').forEach((b) => b.onclick = () => { selectFlow(b.dataset.flow); });
    const f = PL.arch.flows[PL.flow];
    $('#pl-flow-desc').innerHTML = `<b>${esc(f.title)}</b><br>${esc(f.desc)}`;
    renderFlowHead(); renderPhaseNav();
  }
  function selectFlow(fk) {
    PL.flow = fk; PL.sel = null; PL.selPhase = null; PL.edits = {}; PL.cfgEdits = {};
    renderFlows(); renderBlocks(); renderDetail(); fillSweepForm();
  }
  /** 흐름 머리 — 진입 명령과 최근 실행 요약 (예전 구조·흐름 탭의 흐름 헤더). */
  function renderFlowHead() {
    const el = $('#pl-flow-head'); if (!el) return;
    const f = PL.arch.flows[PL.flow], lt = lastTraceFor(PL.flow);
    el.innerHTML = `<div class="entry" title="이 흐름을 시작하는 명령·화면">진입: <code>${esc(f.entry)}</code></div>` +
      (lt ? `<div class="last" title="${esc(lt.summary || '')}&#10;${esc(lt.src)}">최근 <b>#${esc(String(lt.id))}</b> · ${fmt(lt.trace.ms, 0)} ms · ${esc(lt.src)}` +
        ` <button type="button" class="mini secondary" data-pl-act="go-flow-req" title="요청 프로파일에서 이 실행의 전체 trace 를 봅니다">전체 trace</button></div>`
        : '<div class="muted">최근 실행 기록 없음 — 이 흐름을 한 번 실행하면 단계마다 시간이 붙습니다</div>') +
      // 흐름 전체에 걸리는 제한 (단계별 제한은 각 블록의 ≤ 칩과 단계 상세의 '시간 제한' 표)
      (flowLimits(PL.flow).length
        ? `<div class="lims" title="이 흐름 전체에 걸리는 시간 제한 — 단계별 제한은 블록의 ≤ 칩">제한: ` +
          flowLimits(PL.flow).map((x) => `<span class="chip lim" title="${esc(x.label)}&#10;${esc(x.file)} ${esc(x.key)}${x.note ? '&#10;' + esc(x.note) : ''}">${esc(x.key.split('.').pop())} ≤ ${esc(fmtLimit(x.s))}</span>`).join('') + '</div>'
        : '');
    const b = el.querySelector('[data-pl-act="go-flow-req"]');
    if (b) b.onclick = (e) => { e.stopPropagation(); switchGroup('observability'); switchTab('requests'); setTimeout(() => { if (LW.openRequest) LW.openRequest(lt.id); }, 300); };
  }
  /** 페이즈 목차 — 국면만 훑고 바로 이동 (단계가 26개인 질의 흐름에서 필요하다). */
  function renderPhaseNav() {
    const el = $('#pl-phase-nav'); if (!el) return;
    const f = PL.arch.flows[PL.flow], phs = f.phases || [];
    el.innerHTML = '<div class="nav-h muted">페이즈 (상위 단계)</div>' + phs.map((ph) => {
      const s = phaseSum(PL.flow, ph);
      return `<button type="button" data-phase="${esc(ph.key)}" class="${PL.selPhase === ph.key ? 'active' : ''}" title="${esc(ph.desc)}">` +
        `${esc(ph.title)}<span class="n">${s.stages}단계${s.known ? ' · ' + fmt(s.ms, 0) + ' ms' : ''}${s.ov ? ' · 요청값 ' + s.ov : ''}</span></button>`;
    }).join('');
    $$('#pl-phase-nav button').forEach((b) => b.onclick = () => selectPhase(b.dataset.phase, true));
  }
  function selectPhase(key, scroll) {
    PL.selPhase = key; PL.sel = null; PL.edits = {}; PL.cfgEdits = {};
    renderPhaseNav(); renderBlocks(); renderDetail(); fillSweepForm();
    if (scroll) { const g = $(`#pl-blocks .pl-phase[data-phase="${key}"]`); if (g) g.scrollIntoView({ behavior: 'smooth', block: 'start' }); }
  }

  // ---------------- 가운데: 페이즈(상위 단계) → 단계 블록 ----------------
  // 보기 3종: blocks(한 흐름 세로) · map(한 흐름 가로 지도) · all(네 흐름 한 화면, 가로).
  // 셋 다 같은 데이터·같은 클릭 동작을 쓴다 — 예전에는 '구조·흐름'(가로 지도, 읽기 전용)과 'Pipeline'(세로, 편집)이
  // 서로 다른 화면이라 무엇을 어디서 보는지 갈렸다.
  function stageChipsHtml(fk, st) {
    const ovs = tuningOverrides();
    const onT = st.toggles.filter(toggleOn), offT = st.toggles.filter((t) => !toggleOn(t));
    const li = stageLast(fk, st);
    const ntune = st.tunables.length, nov = st.tunables.filter((t) => (t.key in ovs.tuning) || (t.key in ovs.settings)).length;
    const msChip = !li ? '<span class="ms-none" title="마지막 실행 기록 없음 — 이 흐름을 한 번 실행하면 여기에 시간이 붙습니다">—</span>'
      : li.missing ? '<span class="ms-none" title="마지막 실행 trace 에 이 단계가 없음 (건너뛰었거나 기록 대상이 아님)">—</span>'
        : li.skipped ? `<span class="chip skip" title="건너뜀: ${esc(li.reason)}">skip</span>`
          : `<span class="chip ms" title="마지막 실행 #${esc(String(li.id))} (${esc(li.src)}) · 전체의 ${fmt(100 * li.ms / li.total, 0)}%">${fmt(li.ms, li.ms < 10 ? 1 : 0)} ms</span>`;
    const tg = st.toggles.length
      ? `<span class="chip on" title="켜진 토글: ${esc(onT.join(', ') || '-')}">on ${onT.length}</span><span class="chip off" title="꺼진 토글: ${esc(offT.join(', ') || '-')}">off ${offT.length}</span>`
      : '<span class="chip" title="이 단계에는 토글이 없습니다">토글 없음</span>';
    return { li, onT, chips: `${tg}${ntune ? `<span class="chip tune" title="튜닝 파라미터 ${ntune}개${nov ? ' · 이번 요청 오버라이드 ' + nov + '개' : ''}">tune ${ntune}${nov ? ' ●' : ''}</span>` : ''}${msChip}${limChip(st, li)}` };
  }
  function stageBlockHtml(fk, st) {
    const { li, onT, chips } = stageChipsHtml(fk, st);
    const gate = st.toggles.length && !onT.length;
    const bar = li && !li.skipped && !li.missing ? `<div class="msbar" style="width:${Math.min(100, 100 * li.ms / li.total)}%" title="이 흐름 전체 시간의 ${fmt(100 * li.ms / li.total, 0)}%"></div>` : '';
    const cls = ['pl-block', gate || (li && li.skipped) ? 'off' : '', li && li.error ? 'err' : '', (PL.flow === fk && PL.sel === st.key) ? 'sel' : ''].join(' ');
    return `<div class="${cls}" data-flow="${esc(fk)}" data-stage="${esc(st.key)}" title="${esc(st.desc)}&#10;클릭: 오른쪽에 상세">` +
      `<div class="st">${esc(st.title)} <span class="sk">${esc(st.key)}</span></div><div class="chips">${chips}</div>${bar}</div>`;
  }
  function phaseHeadHtml(fk, ph) {
    const s = phaseSum(fk, ph);
    const collapsed = !!PL.phaseCollapsed[fk + ':' + ph.key];
    const pct = s.known && s.total ? Math.min(100, 100 * s.ms / s.total) : 0;
    return `<div class="ph-head ${PL.flow === fk && PL.selPhase === ph.key ? 'sel' : ''} ${s.err ? 'err' : ''}" data-flow="${esc(fk)}" data-phase="${esc(ph.key)}"` +
      ` title="${esc(ph.desc)}&#10;클릭: 이 국면 전체를 오른쪽에 · ▸ 는 접기/펴기">` +
      `<span class="ph-fold" data-fold="${esc(fk)}:${esc(ph.key)}" title="이 국면의 단계를 접거나 폅니다">${collapsed ? '▸' : '▾'}</span>` +
      `<span class="ph-t">${esc(ph.title)}</span><span class="ph-k">${esc(ph.key)}</span>` +
      `<span class="ph-chips"><span class="chip" title="이 국면의 단계 수">${s.stages}단계</span>` +
      (s.on || s.off ? `<span class="chip on" title="켜진 토글 ${s.on}개">on ${s.on}</span>${s.off ? `<span class="chip off" title="꺼진 토글 ${s.off}개">off ${s.off}</span>` : ''}` : '') +
      (s.tune ? `<span class="chip tune" title="이 국면의 튜닝 파라미터 ${s.tune}개${s.ov ? ' · 이번 요청 오버라이드 ' + s.ov + '개' : ''}">tune ${s.tune}${s.ov ? ' ●' : ''}</span>` : '') +
      (s.skipped ? `<span class="chip skip" title="건너뛴 단계 ${s.skipped}개">skip ${s.skipped}</span>` : '') +
      (s.known ? `<span class="chip ms" title="이 국면의 합계 — 흐름 전체의 ${fmt(pct, 0)}%">${fmt(s.ms, 0)} ms · ${fmt(pct, 0)}%</span>` : '') +
      `</span>${s.known ? `<div class="msbar ph" style="width:${pct}%"></div>` : ''}</div>`;
  }
  function flowSectionHtml(fk, opts) {
    const f = PL.arch.flows[fk], horiz = !!(opts || {}).horiz;
    const phs = f.phases || [{ key: '_all', title: f.title, desc: f.desc, stages: f.stages.map((s) => s.key) }];
    const body = phs.map((ph) => {
      const collapsed = !!PL.phaseCollapsed[fk + ':' + ph.key];
      const sts = stagesOf(fk, ph);
      const inner = collapsed ? '' :
        `<div class="ph-body ${horiz ? 'horiz' : ''}">` + sts.map((st, i) => stageBlockHtml(fk, st) +
          (i < sts.length - 1 ? `<div class="pl-arrow">${horiz ? '→' : '↓'}</div>` : '')).join('') + '</div>';
      return `<div class="pl-phase" data-flow="${esc(fk)}" data-phase="${esc(ph.key)}">${phaseHeadHtml(fk, ph)}${inner}</div>`;
    }).join(horiz ? '' : '<div class="pl-arrow ph">↓</div>');
    const head = (opts || {}).withHead
      ? `<div class="pl-flow-title ${fk === PL.flow ? 'sel' : ''}" data-flow="${esc(fk)}" title="${esc(f.desc)}&#10;클릭: 이 흐름만 보기">` +
        `<b>${esc(f.title)}</b> <span class="pill">${esc(fk)}</span> <span class="muted small">${esc(f.entry)}</span></div>` : '';
    return `<div class="pl-flowsec ${horiz ? 'horiz' : ''}">${head}${body}</div>`;
  }
  function renderBlocks() {
    const box = $('#pl-blocks'); if (!PL.arch) return;
    if (PL.view === 'all') {
      const flows = FLOW_ORDER.filter((k) => PL.arch.flows[k]).concat(Object.keys(PL.arch.flows).filter((k) => !FLOW_ORDER.includes(k)));
      box.innerHTML = flows.map((fk) => flowSectionHtml(fk, { horiz: true, withHead: true })).join('');
    } else {
      box.innerHTML = flowSectionHtml(PL.flow, { horiz: PL.view === 'map' });
    }
    $$('#pl-blocks .pl-block').forEach((b) => b.onclick = () => {
      if (b.dataset.flow !== PL.flow) { PL.flow = b.dataset.flow; renderFlows(); }
      PL.sel = b.dataset.stage; PL.selPhase = null; PL.edits = {}; PL.cfgEdits = {};
      renderBlocks(); renderDetail(); fillSweepForm(); renderPhaseNav();
    });
    $$('#pl-blocks .ph-head').forEach((h) => h.onclick = (e) => {
      const fold = e.target.closest('[data-fold]');
      if (fold) {                       // ▸/▾ 는 접기만 (선택을 바꾸지 않는다)
        e.stopPropagation();
        const k = fold.dataset.fold; PL.phaseCollapsed[k] = !PL.phaseCollapsed[k]; renderBlocks(); return;
      }
      if (h.dataset.flow !== PL.flow) { PL.flow = h.dataset.flow; renderFlows(); }
      selectPhase(h.dataset.phase, false); renderBlocks();
    });
    $$('#pl-blocks .pl-flow-title').forEach((t) => t.onclick = () => { PL.view = 'blocks'; syncViewSeg(); selectFlow(t.dataset.flow); });
  }
  function syncViewSeg() { $$('#pl-view button').forEach((b) => b.classList.toggle('active', b.dataset.v === PL.view)); }

  // ---------------- 오른쪽: 선택한 단계의 상세 ----------------
  function settingsVal(k) {
    const base = k.split('.')[0]; const v = PL.arch.settings[base];
    if (v == null) return '';
    if (typeof v === 'object') return JSON.stringify(k.includes('.') ? (v[k.split('.')[1]] || {}) : v);
    return String(v);
  }
  // 현재값: 이번 요청 오버라이드가 있으면 그것, 없으면 파일 값(config.json 또는 tuning.json, 없으면 기본)
  function tuneFile(t) { return t.source === 'config' ? PL.arch.settings[t.key] : (PL.arch.tuning[t.key] != null ? PL.arch.tuning[t.key] : t.default); }
  function tuneCur(t) { const ovs = tuningOverrides(); const box = t.source === 'config' ? ovs.settings : ovs.tuning; return (t.key in box) ? box[t.key] : tuneFile(t); }
  function tuneType(t, v) { const sp = PL.tun[t.key] || {}; return sp.type || (typeof v === 'boolean' ? 'bool' : typeof v === 'number' ? (Number.isInteger(v) ? 'int' : 'float') : 'str'); }
  function tuneInput(t, v) {
    const sp = PL.tun[t.key] || {}; const a = `data-pl-tune="${esc(t.key)}" title="${esc(t.key)} 새 값 — 아래 '이번 요청에만' 또는 'tuning.json 저장'"`;
    const typ = tuneType(t, v);
    if (typ === 'bool') return `<select ${a}><option value="true" ${v ? 'selected' : ''}>true</option><option value="false" ${!v ? 'selected' : ''}>false</option></select>`;
    if (typ === 'choice') return `<select ${a}>${(sp.choices || []).map((c) => `<option ${String(c) === String(v) ? 'selected' : ''}>${esc(c)}</option>`).join('')}</select>`;
    if (typ === 'str') return `<input type="text" ${a} value="${esc(v)}">`;
    return `<input type="number" ${a} value="${esc(v)}" ${sp.min != null ? 'min="' + sp.min + '"' : ''} ${sp.max != null ? 'max="' + sp.max + '"' : ''} step="${typ === 'int' ? 1 : 'any'}">`;
  }
  function coerce(t, raw) {
    const typ = tuneType(t, tuneFile(t));
    if (typ === 'bool') return raw === 'true' || raw === true;
    if (typ === 'int') return parseInt(raw, 10);
    if (typ === 'float') return parseFloat(raw);
    return raw;
  }
  /** 페이즈(상위 단계) 상세 — 그 국면이 무엇을 하는지, 포함 단계의 시간·토글·튜닝을 한 표로.
   *  단계 하나로 내려가기 전에 "이 국면이 느린가/꺼져 있나" 를 먼저 판단하는 자리다. */
  function renderPhaseDetail(ph) {
    const box = $('#pl-detail'), fk = PL.flow, f = PL.arch.flows[fk];
    const sts = stagesOf(fk, ph), s = phaseSum(fk, ph), ovs = tuningOverrides();
    const row = (st) => {
      const li = stageLast(fk, st);
      const onT = st.toggles.filter(toggleOn), offT = st.toggles.filter((t) => !toggleOn(t));
      const nov = st.tunables.filter((t) => (t.key in ovs.tuning) || (t.key in ovs.settings)).length;
      return `<tr data-ph-stage="${esc(st.key)}" title="클릭: 이 단계 상세"><td><b>${esc(st.title)}</b><br><code>${esc(st.key)}</code></td>` +
        `<td>${li && !li.missing ? (li.skipped ? `<span class="chip skip">skip</span>` : fmt(li.ms, 1) + ' ms') : '<span class="muted">—</span>'}</td>` +
        `<td>${limChip(st, li) || '<span class="muted">-</span>'}</td>` +
        `<td>${onT.length ? `<span class="chip on">${esc(onT.join(', '))}</span>` : ''}${offT.length ? `<span class="chip off">${esc(offT.join(', '))}</span>` : ''}${st.toggles.length ? '' : '<span class="muted">-</span>'}</td>` +
        `<td>${st.tunables.length || '-'}${nov ? ' <span class="pl-ovchip">요청 ' + nov + '</span>' : ''}</td>` +
        `<td class="muted small">${esc(st.impact || '')}</td></tr>`;
    };
    const allTun = []; sts.forEach((st) => st.tunables.forEach((t) => { if (!allTun.some((x) => x.key === t.key)) allTun.push(t); }));
    box.innerHTML = `<div class="adetail"><h4>${esc(ph.title)} <code>${esc(ph.key)}</code> <span class="pill">${esc(fk)} 페이즈</span></h4>` +
      `<div class="muted small">${esc(f.title)} 의 ${sts.length}단계 · 상위 단계(국면)</div>` +
      `<div class="sec"><b>이 국면이 하는 일</b>${esc(ph.desc)}</div>` +
      `<div class="sec"><b>합계</b>` +
      `<span class="chip">${s.stages}단계</span><span class="chip on">on ${s.on}</span><span class="chip off">off ${s.off}</span>` +
      `<span class="chip tune">tune ${s.tune}${s.ov ? ' · 요청 ' + s.ov : ''}</span>` +
      (s.known ? `<span class="chip ms">${fmt(s.ms, 0)} ms · 흐름의 ${fmt(s.total ? 100 * s.ms / s.total : 0, 0)}%</span>` : '<span class="ms-none">실행 기록 없음</span>') +
      (s.skipped ? `<span class="chip skip">건너뜀 ${s.skipped}</span>` : '') + '</div>' +
      `<div class="sec"><b>포함 단계</b><div class="tbl-wrap"><table class="pl-phase-tbl"><tr><th>단계</th><th>ms</th><th title="이 단계를 끊을 수 있는 시간 제한">제한</th><th>토글</th><th>튜닝</th><th>impact</th></tr>${sts.map(row).join('')}</table></div></div>` +
      (allTun.length ? `<div class="sec"><b>이 국면의 튜닝 키 (${allTun.length})</b><div class="ph-keys">${allTun.map((t) => `<code class="k" data-ph-key="${esc(t.key)}" title="${esc(t.desc || '')}&#10;클릭: 이 키로 스윕 폼 채우기">${esc(t.key)}</code>`).join(' ')}</div>
        <div class="muted small">값 편집은 단계를 고른 뒤 오른쪽 표에서 — 한 번에 바꿔 보려면 아래 스윕에서 키를 고르세요.</div></div>` : '') +
      `<div class="pl-acts"><button class="mini secondary" data-pl-act="ph-first" title="이 국면의 첫 단계 상세로 내려갑니다">첫 단계 열기</button>` +
      `<button class="mini secondary" data-pl-act="ph-fold" title="가운데에서 이 국면을 접습니다">국면 접기</button></div></div>`;
    $$('#pl-detail [data-ph-stage]').forEach((tr) => tr.onclick = () => { PL.sel = tr.dataset.phStage; PL.selPhase = null; renderBlocks(); renderDetail(); fillSweepForm(); });
    $$('#pl-detail [data-ph-key]').forEach((c) => c.onclick = () => {
      $('#pl-sw-allkeys').checked = true; fillSweepForm();
      const sel = $('#pl-sw-key'); if (sel && [...sel.options].some((o) => o.value === c.dataset.phKey)) { sel.value = c.dataset.phKey; onKeyChange(); }
      const d = $('#pl-sweep'); if (d) { d.open = true; d.scrollIntoView({ behavior: 'smooth', block: 'start' }); }
    });
    $$('#pl-detail [data-pl-act]').forEach((b) => b.onclick = () => {
      if (b.dataset.plAct === 'ph-first' && sts.length) { PL.sel = sts[0].key; PL.selPhase = null; renderBlocks(); renderDetail(); fillSweepForm(); }
      else if (b.dataset.plAct === 'ph-fold') { PL.phaseCollapsed[fk + ':' + ph.key] = true; renderBlocks(); }
    });
  }
  function renderDetail() {
    const box = $('#pl-detail'); const st = curStage();
    const ph = curPhase();
    if (!st && ph) return renderPhaseDetail(ph);
    if (!st) { box.innerHTML = '<div class="muted">가운데에서 <b>페이즈 머리</b>(국면 전체) 또는 <b>단계 블록</b>을 선택하세요. 국면을 고르면 포함 단계의 시간·토글·튜닝이 한 표로, 단계를 고르면 그 단계의 동작 · 토글 스위치 · config 값 · 튜닝 표가 나오고 아래 스윕 폼의 키 목록이 좁혀집니다.</div>'; return; }
    const li = stageLast(PL.flow, st); const ovs = tuningOverrides();
    const admin = can('admin'), editRole = needRole('edit'), canEdit = can('edit');
    let html = `<div class="adetail"><h4>${esc(st.title)} <code>${esc(st.key)}</code> <span class="pill">${esc(PL.flow)}</span></h4>` +
      `<div class="muted small">${esc(st.module || '')}${st.io ? ' · ' + esc(st.io) : ''}${st.tuning_stage ? ' · 튜닝 단계 <code>' + esc(st.tuning_stage) + '</code>' : ''}</div>` +
      `<div class="sec"><b>동작</b>${esc(st.desc)}</div><div class="sec"><b>impact</b><div class="imp">${esc(st.impact)}</div></div>`;
    // ---- 토글: 사이드바 체크박스를 비추는 스위치 (같은 상태) ----
    html += `<div class="sec"><b>토글 (${st.toggles.length}) — 이번 요청에만 · 사이드바와 같은 상태</b>` + (st.toggles.length ? st.toggles.map((t) => {
      const cb = $(`[data-toggle="${t}"]`); const v = cb ? cb.checked : !!PL.arch.toggles[t]; const base = !!PL.arch.toggles[t];
      const byPreset = !!(cb && cb.parentElement && cb.parentElement.classList.contains('by-preset'));
      const tip = (PL.arch.toggle_help[t] || '') + '\nconfig.json 값: ' + (base ? 'on' : 'off') + ' — 여기서 바꾸면 이번 요청에만 적용됩니다 (서버 기본값은 Settings › config.json, admin)' +
        (byPreset ? '\n[프리셋이 정한 값 — 손으로 바꾸면 서버에서 프리셋 값이 다시 적용됩니다. 다른 값을 쓰려면 사이드바에서 프리셋 체크를 해제]' : '');
      return `<label class="pl-tg ${byPreset ? 'by-preset' : ''}" title="${esc(tip)}"><input type="checkbox" data-pl-toggle="${esc(t)}" ${v ? 'checked' : ''} ${cb ? '' : 'disabled'}><code>${esc(t)}</code><span class="pill ${v ? 'ok' : ''}">${v ? 'ON' : 'OFF'}</span>${v !== base ? `<span class="pill warn" title="config 값(${base ? 'on' : 'off'})과 다름 — 이번 요청 오버라이드">≠ config</span>` : ''}<span class="help">${esc(PL.arch.toggle_help[t] || '')}</span></label>`;
    }).join('') : '<div class="muted small">이 단계에는 토글이 없습니다.</div>') + '</div>';
    // ---- config.json 설정: admin 만 저장, 그 외 읽기 전용 ----
    if (st.settings.length) {
      html += `<div class="sec"><b>config.json 설정 (${st.settings.length}) — ${admin ? '저장은 config.json 에 (admin)' : 'admin 만 저장할 수 있습니다 (지금 ' + esc(myRole()) + ' · 읽기 전용)'}</b>` + st.settings.map((k) => {
        const v = PL.arch.settings[k.split('.')[0]]; const dotted = k.includes('.') || (v != null && typeof v === 'object'); const cur = settingsVal(k);
        const inp = dotted ? `<code title="객체 값 — Settings › 모델·프로바이더 / config.json 탭에서 고칩니다">${esc(cur.slice(0, 80))}${cur.length > 80 ? '…' : ''}</code>`
          : typeof v === 'boolean' ? `<select data-pl-cfg="${esc(k)}" ${admin ? '' : 'disabled'} title="${esc(k)}"><option value="true" ${v ? 'selected' : ''}>true</option><option value="false" ${!v ? 'selected' : ''}>false</option></select>`
            : `<input type="${typeof v === 'number' ? 'number' : 'text'}" step="any" data-pl-cfg="${esc(k)}" value="${esc(cur)}" ${admin ? '' : 'readonly'} title="${esc(k)}${admin ? '' : ' (읽기 전용)'}">`;
        return `<div class="pl-cfg-row"><code>${esc(k)}</code> ${inp}<span class="help">${esc(PL.arch.setting_help[k] || '')}</span></div>`;
      }).join('') + (admin ? `<div class="pl-acts"><button class="mini" data-pl-act="cfg-save" title="바꾼 config 값을 config.json 에 저장하고 프로바이더를 다시 만듭니다 — 모든 사용자의 서버 기본값이 바뀝니다 (admin · 감사 로그). 한 번만 시험하려면 아래 튜닝 표의 '이번 요청에만'">config.json 저장 (admin)</button></div>` : '') + '</div>';
    }
    // ---- 튜닝 파라미터: 요청에만 / tuning.json 저장 ----
    html += `<div class="sec"><b>튜닝 파라미터 (${st.tunables.length})</b>` + (st.tunables.length ? `<table class="pl-tune"><tr><th>키</th><th>현재 (파일)</th><th>기본</th><th>새 값</th><th>설명 / impact</th></tr>` + st.tunables.map((t) => {
      const cur = tuneCur(t), file = tuneFile(t); const isOv = (t.key in (t.source === 'config' ? ovs.settings : ovs.tuning)); const edited = t.key in PL.edits;
      const where = t.source === 'config' ? 'config.json 항목 — 요청 단위는 평면 overrides(서버 OVERRIDE_SAFE_KEYS 가 허용하는 키만) · 저장은 config.json' : 'tuning.json 항목 — 요청 단위는 overrides.tuning · 저장은 tuning.json';
      return `<tr class="${edited ? 'edited' : ''} ${isOv ? 'ov' : ''}"><td class="k" title="${esc(where)}${t.rebuild ? ' · 변경 후 전체 리빌드 필요' : ''}">${esc(t.key)}${t.rebuild ? ' <span class="chip" title="변경 후 전체 리빌드 필요 — 요청 단위 적용·스윕 불가">rebuild</span>' : ''}<br><small class="muted">${t.source === 'config' ? 'config.json' : 'tuning.json'}</small></td>` +
        `<td class="${String(file) !== String(t.default) ? 'ok' : ''}" title="파일 값${isOv ? ' · 이번 요청: ' + esc(String(cur)) : ''}">${esc(String(file))}${isOv ? `<br><span class="pl-ovchip" title="이번 요청에만 적용 중 — ✕ 로 해제">요청 ${esc(String(cur))}<i data-pl-unov="${esc(t.key)}" data-src="${esc(t.source)}" title="이 요청 오버라이드 해제">✕</i></span>` : ''}</td>` +
        `<td class="muted">${esc(String(t.default))}</td><td>${tuneInput(t, edited ? PL.edits[t.key] : cur)}</td>` +
        `<td class="muted small">${esc(t.desc || '')}${t.impact ? `<div class="imp" style="margin-top:2px">${esc(t.impact)}</div>` : ''}</td></tr>`;
    }).join('') + '</table>' +
      `<div class="pl-acts"><button class="mini secondary" data-pl-act="tune-request" title="입력한 새 값을 이번 요청에만 적용합니다 (파일에 저장 안 함). Ask 질의·⟲ 재실행·평가·스윕에 overrides 로 함께 실리고 사이드바 요약의 '튜닝 m' 이 늘어납니다. rebuild 항목은 적용되지 않습니다">이번 요청에만</button>` +
      `<button class="mini" data-pl-act="tune-save" ${canEdit ? '' : 'disabled'} title="입력한 새 값을 tuning.json(config.json 항목은 config.json)에 저장합니다 — 모든 사용자의 기본값이 바뀝니다. 필요 역할: ${esc(editRole)} 이상${canEdit ? '' : ' (지금 ' + esc(myRole()) + ' — 버튼 비활성)'}. CLI: tuning set 키=값">tuning.json 저장 <small>(${esc(editRole)}+)</small></button>` +
      `<button class="mini secondary" data-pl-act="tune-clear" title="이 단계의 '이번 요청에만' 값을 모두 해제합니다 (파일 값으로 돌아감)">요청 오버라이드 해제</button>` +
      `<button class="mini secondary" data-pl-act="tune-tab" title="Settings › 튜닝 표에서 이 단계만 걸러 봅니다 (예시·범위 열 포함)">Settings › 튜닝</button></div>` : '<div class="muted small">이 단계에는 튜닝 파라미터가 없습니다.</div>') + '</div>';
    // ---- 이 단계가 부르는 LLM 역할: 모델 · 앙상블 상태 (Settings 와 같은 값) ----
    {
      const roles = ((PL.arch.limits || {}).roles) || {};
      const mine = (((PL.arch.limits || {}).stage_roles) || {})[st.key] || [];
      if (mine.length) {
        const rrow = (r) => {
          const x = roles[r] || {};
          const en = x.ensemble || {};
          const flow = en.enabled
            ? `멤버 ${(en.members || []).length}개 동시 호출 → ${(en.members || []).length > 1 ? '취합 LLM 1회' : '취합 건너뜀'}`
            : '단일 모델 1회';
          return `<tr><td><b>${esc(r)}</b></td>` +
            `<td class="mono small">${esc(x.provider || '')}/${esc(x.model || '')}</td>` +
            `<td>${en.enabled ? '<span class="pill ok">앙상블 ON</span>' : '<span class="pill">앙상블 off</span>'}</td>` +
            `<td class="small muted">${esc(flow)}${en.enabled && (en.members || []).length ? ' · ' + (en.members || []).map((m) => esc(m.provider + '/' + m.model)).join(', ') : ''}</td>` +
            `<td><button class="mini secondary" data-pl-role="${esc(r)}" title="Settings › 모델·프로바이더 에서 이 역할의 모델과 앙상블을 고칩니다">고치기</button></td></tr>`;
        };
        html += `<div class="sec"><b>이 단계가 부르는 LLM (${mine.length})</b>` +
          '<div class="tbl-wrap"><table class="pl-lim-tbl"><tr><th>역할</th><th>모델</th><th>앙상블</th><th>호출 방식</th><th></th></tr>' +
          mine.map(rrow).join('') + '</table></div>' +
          '<div class="muted small">앙상블을 켜면 <b>역할 모델 대신</b> 멤버들이 같은 프롬프트로 동시에 불리고, 성공이 2개 이상일 때만 취합 LLM 이 한 번 더 돕니다. ' +
          '저장 위치는 <code>config.json llm_roles.&lt;역할&gt;.ensemble</code> — CLI <code>models ensemble set &lt;역할&gt;</code>.</div></div>';
      }
    }
    // ---- 시간 제한: 이 단계를 중간에 끊을 수 있는 값과 그 출처 ----
    {
      const rows = limitsOf(st) || [];
      const lrow = (x) => {
        const p = li && !li.skipped && !li.missing ? limitPct(li.ms, x.s) : 0;
        const KIND = { llm: 'LLM 1회', llm_budget: '재시도 합계', request: '요청 전체', job: '작업 전체', lock: '락 대기', db: 'DB 잠금 대기' };
        return `<tr class="${p >= 1 ? 'over' : p >= 0.8 ? 'near' : ''}"><td><b>${esc(fmtLimit(x.s))}</b></td><td>${esc(x.label)}</td>` +
          `<td><span class="pill">${esc(KIND[x.kind] || x.kind)}</span></td>` +
          `<td><code title="${esc(x.file)} 에서 고칩니다">${esc(x.file)}</code> <code>${esc(x.key)}</code></td>` +
          `<td class="muted small">${esc(x.note || '')}${p ? ' · 마지막 실행은 제한의 ' + fmt(100 * p, 0) + '%' : ''}</td></tr>`;
      };
      html += `<div class="sec"><b>시간 제한 (${rows.length}) — 이 단계를 중간에 끊을 수 있는 값</b>` +
        (rows.length
          ? `<div class="tbl-wrap"><table class="pl-lim-tbl"><tr><th>제한</th><th>무엇을</th><th>종류</th><th>어디서 고치나</th><th>비고</th></tr>${rows.map(lrow).join('')}</table></div>` +
            `<div class="muted small">위에서부터 먼저 걸리는 순서입니다. 첫 줄이 가운데 블록의 <code>≤</code> 칩에 나오는 값입니다. CLI 로는 <code>python -m llmwiki arch limits</code>.</div>`
          : '<div class="muted small">이 단계에만 걸리는 제한은 없습니다 — 흐름 전체 제한만 적용됩니다.</div>') + '</div>';
    }
    if (st.cli.length) html += `<div class="sec"><b>CLI</b>${st.cli.map((c) => `<code>${esc(c)}</code>`).join(' ')}</div>`;
    // ---- 마지막 실행 ----
    if (li && !li.missing) {
      html += `<div class="sec"><b>마지막 실행 (#${esc(String(li.id))} · ${esc(li.src)})</b>` +
        (li.skipped ? `<span class="chip skip">skipped: ${esc(li.reason)}</span>` : `<span class="chip ms">${fmt(li.ms, 1)} ms · ${fmt(100 * li.ms / li.total, 1)}%</span>`) +
        (li.nodes || []).map((n) => {
          const m = Object.assign({}, n.meta || {}); delete m.reason; const c = n.counters || {};
          return `<div style="margin-top:4px"><code>${esc(n.name)}</code> ${n.enabled === false ? '<span class="muted">skipped</span>' : fmt(n.ms, 1) + ' ms'}${c.llm_calls ? ' · llm ' + c.llm_calls + ' (' + fmtK((c.llm_input_tokens || 0) + (c.llm_output_tokens || 0)) + ' tok)' : ''}${n.replayed ? ' <span class="pill replay">재생</span>' : ''}${n.error ? '<div class="errtxt">' + esc(n.error) + '</div>' : ''}${Object.keys(m).length ? `<pre class="pre" style="max-height:120px">${esc(JSON.stringify(m, null, 1).slice(0, 800))}</pre>` : ''}</div>`;
        }).join('') + `<div class="pl-acts"><button class="mini secondary" data-pl-act="go-req" title="Observability › 요청 프로파일에서 이 요청의 전체 trace 를 봅니다">요청 프로파일에서 전체 trace</button></div></div>`;
    } else {
      html += '<div class="sec muted small">마지막 실행 기록 없음 — Ask 에서 질의하면 이 단계의 시간이 여기와 가운데 블록에 표시됩니다.</div>';
    }
    html += `<div class="pl-acts"><button class="mini secondary" data-pl-act="sweep-here" title="아래 스윕 폼의 키 목록을 이 단계의 키로 좁히고 폼으로 이동합니다">🔁 이 단계 키로 스윕</button></div></div>`;
    box.innerHTML = html;
    wireDetail(st, li);
  }
  function wireDetail(st, li) {
    // 토글 스위치 → 사이드바 체크박스가 유일한 상태. 그 change 핸들러(core.js buildSidebar)가 PRESET.manual·CLI·요약을 갱신하고 _toggleChanged 로 이 화면을 다시 그린다.
    $$('#pl-detail [data-pl-toggle]').forEach((cb) => cb.onchange = () => {
      const side = $(`[data-toggle="${cb.dataset.plToggle}"]`); if (!side) return;
      side.checked = cb.checked; side.dispatchEvent(new Event('change', { bubbles: true }));
    });
    $$('#pl-detail [data-pl-tune]').forEach((i) => i.onchange = () => { PL.edits[i.dataset.plTune] = i.value; const tr = i.closest('tr'); if (tr) tr.classList.add('edited'); });
    $$('#pl-detail [data-pl-cfg]').forEach((i) => i.onchange = () => { PL.cfgEdits[i.dataset.plCfg] = i.value; });
    $$('#pl-detail [data-pl-unov]').forEach((x) => x.onclick = (e) => { e.preventDefault(); setTuningOverride(x.dataset.plUnov, undefined, x.dataset.src); renderDetail(); renderBlocks(); });
    $$('#pl-detail [data-pl-act]').forEach((b) => b.onclick = () => act(b.dataset.plAct, st, li));
    // 역할의 모델·앙상블은 Settings › 모델·프로바이더 가 유일한 편집 자리다 (두 곳에서 같은 값을 받으면 어느 쪽이 적용됐는지 헷갈린다)
    $$('#pl-detail [data-pl-role]').forEach((b) => b.onclick = () => {
      switchGroup('settings'); switchTab('models');
      setTimeout(() => {
        const row = document.querySelector(`#roles-table [data-ens-row="${b.dataset.plRole}"]`) || document.querySelector(`#roles-table [data-role="${b.dataset.plRole}"]`);
        if (row) { row.scrollIntoView({ behavior: 'smooth', block: 'center' }); row.classList.add('flash'); setTimeout(() => row.classList.remove('flash'), 1500); }
      }, 500);
    });
  }
  async function act(a, st, li) {
    const byKey = {}; st.tunables.forEach((t) => { byKey[t.key] = t; });
    if (a === 'tune-request') {
      const ks = Object.keys(PL.edits); if (!ks.length) { toast('바꾼 값이 없습니다 — "새 값" 칸을 고친 뒤 누르세요'); return; }
      const applied = [], skipped = [];
      ks.forEach((k) => { const t = byKey[k]; if (!t) return; if (t.rebuild) { skipped.push(k); return; } setTuningOverride(k, coerce(t, PL.edits[k]), t.source); applied.push(k + '=' + PL.edits[k]); });
      PL.edits = {};
      toast((applied.length ? '이번 요청에만 적용: ' + applied.join(', ') : '적용된 값 없음') + (skipped.length ? ' · rebuild 항목 제외: ' + skipped.join(', ') : ''));
      renderDetail(); renderBlocks();
    } else if (a === 'tune-save') {
      const ks = Object.keys(PL.edits); if (!ks.length) { toast('바꾼 값이 없습니다 — "새 값" 칸을 고친 뒤 누르세요'); return; }
      const j = await api('/api/tuning', { action: 'set', values: PL.edits });
      if (!j || j.error) return;
      const errs = Object.keys(j.errors || {});
      if (errs.length) toast('오류: ' + errs.map((k) => k + ': ' + j.errors[k]).join('; '));
      else toast('저장됨 (' + ks.length + '개): ' + ks.join(', ') + (ks.some((k) => byKey[k] && byKey[k].rebuild) ? ' — rebuild 항목은 Corpus › 빌드에서 전체 리빌드' : ''));
      // tuning.json 이 바뀌었다 → Settings › 튜닝 화면도 같은 값을 보게 방송한다
      PL.edits = {}; await reloadArch(); await LW.settingsChanged('tuning');
    } else if (a === 'tune-clear') {
      st.tunables.forEach((t) => setTuningOverride(t.key, undefined, t.source)); PL.edits = {};
      toast('이 단계의 요청 오버라이드를 해제했습니다'); renderDetail(); renderBlocks();
    } else if (a === 'tune-tab') {
      switchGroup('settings'); switchTab('tuning');
      setTimeout(() => { const s = $('#tuning-stage'); if (s) { s.value = st.tuning_stage || ''; if (LW.renderTuning) LW.renderTuning(); } }, 500);
    } else if (a === 'cfg-save') {
      const ks = Object.keys(PL.cfgEdits); if (!ks.length) { toast('바꾼 config 값이 없습니다'); return; }
      const settings = {};
      ks.forEach((k) => { const cur = PL.arch.settings[k]; const raw = PL.cfgEdits[k]; settings[k] = typeof cur === 'boolean' ? raw === 'true' : typeof cur === 'number' ? Number(raw) : raw; });
      const j = await api('/api/config', { settings });
      if (!j || j.error) return;
      toast('config.json 저장됨: ' + ks.join(', ')); PL.cfgEdits = {};
      if (j.settings) STATE.settings = j.settings;
      // config.json 이 바뀌었다 → Settings › config / 모델·프로바이더 화면도 다시 읽는다
      await reloadArch(); await LW.settingsChanged('config');
    } else if (a === 'go-req') {
      if (li && li.id != null) { switchGroup('observability'); switchTab('requests'); setTimeout(() => { if (LW.openRequest) LW.openRequest(li.id); }, 300); }
    } else if (a === 'sweep-here') {
      $('#pl-sw-allkeys').checked = false; fillSweepForm();
      const d = $('#pl-sweep'); if (d) { d.open = true; d.scrollIntoView({ behavior: 'smooth', block: 'start' }); }
    }
  }

  // ---------------- 스윕 폼 ----------------
  function stageKeys(st) {
    const all = (PL.keys && PL.keys.keys) || [];
    if (!st) return all;
    return all.filter((k) => {
      if (k.kind === 'toggle') return st.toggles.includes(k.key);
      if (k.kind === 'role') return (ROLE_STAGE[k.stage] || '') === st.tuning_stage || (k.stage === 'verify' && (st.key === 'evidence' || st.key === 'claim'));
      if (k.stage) return k.stage === st.tuning_stage;
      if (k.key === 'answer_mode') return st.key === 'answer';
      return false;
    });
  }
  /** 페이즈를 고르면 그 국면의 **모든 단계** 키를 모은다 (단계 하나보다 넓고 전체보다 좁은 자리). */
  function phaseKeys(ph) {
    const seen = new Set(), out = [];
    stagesOf(PL.flow, ph).forEach((st) => stageKeys(st).forEach((k) => { if (!seen.has(k.key)) { seen.add(k.key); out.push(k); } }));
    return out;
  }
  function fillSweepForm() {
    const sel = $('#pl-sw-key'); if (!sel || !PL.keys) return;
    const st = curStage(), ph = curPhase();
    const scoped = st ? stageKeys(st) : (ph ? phaseKeys(ph) : null);
    const all = $('#pl-sw-allkeys').checked || !scoped;
    const keys = all ? (PL.keys.keys || []) : scoped;
    const cur = sel.value;
    sel.innerHTML = keys.length
      ? keys.map((k) => `<option value="${esc(k.key)}" title="${esc(k.desc || '')}">${esc(k.key)} · ${esc(k.kind)}${k.stage ? ' · ' + esc(k.stage) : ''} → ${esc(k.point)}</option>`).join('')
      : `<option value="">(${st ? '이 단계' : '이 국면'}에 스윕할 키가 없습니다 — "모든 단계의 키" 를 켜세요)</option>`;
    if (keys.some((k) => k.key === cur)) sel.value = cur;
    const from = $('#pl-sw-from');
    if (from.options.length <= 1) from.innerHTML = '<option value="">(자동 — 키의 단계)</option>' + (PL.keys.points || []).map((p) => `<option value="${esc(p.id)}" title="${esc(p.note || '')}">${esc(p.label)}</option>`).join('');
    $('#pl-sw-limit').textContent = `상한: 값 ${PL.keys.max_values}개 (config.json sweep_max_values) · 반복 ${PL.keys.max_repeats}회`;
    onKeyChange();
  }
  function keyInfo() { const k = $('#pl-sw-key').value; return ((PL.keys && PL.keys.keys) || []).find((x) => x.key === k) || null; }
  function onKeyChange() {
    const k = keyInfo(); const el = $('#pl-sw-keyinfo');
    if (!k) { el.textContent = ''; return; }
    const rng = (k.min != null || k.max != null) ? ` · 범위 ${k.min == null ? '' : k.min}~${k.max == null ? '' : k.max}` : (k.choices ? ' · 값 ' + k.choices.join(' | ') : '');
    el.textContent = `${k.kind} · ${k.type}${rng} · 기본 ${JSON.stringify(k.default)}${k.value !== undefined ? ' · 현재 ' + JSON.stringify(k.value) : ''} · 재시작점 ${k.point}${k.desc ? ' — ' + k.desc : ''}`;
    if (k.type === 'bool' || k.type === 'choice' || k.type === 'str') $('#pl-sw-mode').value = 'values';   // 수치가 아니면 값 목록이 자연스럽다
    onModeChange();
  }
  function onModeChange() { const m = $('#pl-sw-mode').value; $('#pl-sw-range').classList.toggle('hidden', m !== 'range'); $('#pl-sw-valuesbox').classList.toggle('hidden', m !== 'values'); }
  function parseLlm(s) {
    const out = {};
    (s || '').split(',').map((x) => x.trim()).filter(Boolean).forEach((kv) => { const m = kv.match(/^(\w+)\.(\w+)\s*=\s*(.+)$/); if (m) { out[m[1]] = out[m[1]] || {}; out[m[1]][m[2]] = m[3].trim(); } });
    return Object.keys(out).length ? out : null;
  }
  async function runSweep() {
    const k = keyInfo(); if (!k) { toast('스윕할 키를 고르세요 (단계를 선택하거나 "모든 단계의 키")'); return; }
    const body = { action: 'run', key: k.key, repeats: parseInt($('#pl-sw-repeats').value, 10) || 1 };
    const enumerable = k.type === 'bool' || k.type === 'choice';
    if ($('#pl-sw-mode').value === 'range') {
      const a = $('#pl-sw-start').value, b = $('#pl-sw-stop').value, s = $('#pl-sw-step').value;
      if (a === '' || b === '') { if (!enumerable) { toast('범위의 start 와 stop 을 입력하세요 (예 10 · 100 · 10)'); return; } }
      else body.range = a + ':' + b + (s !== '' ? ':' + s : '');
    } else {
      const v = $('#pl-sw-values').value.trim();
      if (v) body.values = v.split(',').map((x) => x.trim()).filter(Boolean);
      else if (!enumerable) { toast('값 목록을 입력하세요 (쉼표 구분)'); return; }
    }
    const base = $('#pl-sw-base').value.trim(), q = $('#pl-sw-query').value.trim();
    if (base) body.request_id = /^\d+$/.test(base) ? parseInt(base, 10) : base;
    else if (q) body.query = q;
    else { toast('기준 요청(last 또는 id) 이나 질의를 입력하세요'); return; }
    const from = $('#pl-sw-from').value; if (from) body.from = from;
    const llm = parseLlm($('#pl-sw-llm').value); if (llm) body.llm = llm;
    body.overrides = overrides();   // 사이드바/Pipeline 의 지금 상태(토글·요청 튜닝)를 모든 값에 공통으로 — ⟲ 재실행과 같은 규칙
    const btn = $('#btn-pl-sweep-run'); btn.disabled = true; $('#pl-sw-msg').textContent = '스윕 시작 중…';
    const j = await api('/api/sweep', body);
    if (!j || !j.job) { btn.disabled = false; $('#pl-sw-msg').textContent = (j && j.error) ? '실패: ' + j.error : ''; return; }
    const log = $('#pl-sw-log'); log.classList.remove('hidden'); log.textContent = 'running…';
    $('#pl-sw-msg').textContent = `잡 ${j.job} 실행 중 — ${k.key} (재시작점 ${from || k.point}) · Observability › 진행 중 작업에서도 보입니다`;
    pollJob(j.job, log, (job) => {
      btn.disabled = false;
      if (job.status !== 'done' || !job.result) { $('#pl-sw-msg').innerHTML = `<span class="bad">${esc(job.status)}: ${esc(String(job.error || '').split('\n')[0])}</span>`; return; }
      $('#pl-sw-msg').textContent = '완료 — 아래 격자 (★ = 기준과 다름 · 셀 클릭 = diff)';
      showSweep(job.result.record, job.result.compare, job.result.text); loadSweepList();
    });
  }

  // ---------------- 격자 (단계 × 값) ----------------
  function cellText(c) {
    if (!c || !c.present) return '·';
    if (c.skipped) return '–';
    const ms = c.ms || 0; let t = ms >= 10 ? fmt(ms, 0) : fmt(ms, 1);
    if (c.replayed) t = '⟲' + t;
    if (c.changed) t += '★';
    return t;
  }
  function cellTitle(stg, c) {
    if (!c || !c.present) return '이 실행의 trace 에 이 단계가 없음';
    const p = [`${stg}: ${fmt(c.ms, 1)} ms`];
    if (c.ms_delta != null) p.push(`기준 대비 ${c.ms_delta > 0 ? '+' : ''}${fmt(c.ms_delta, 1)} ms`);
    if (c.replayed) p.push('⟲ 재생 (저장값 그대로 — 이 값의 영향 밖)');
    if (c.skipped) p.push('건너뜀');
    if (c.order) p.push(c.order.same ? '순위/집합 기준과 같음' : `순위 유사도 ${fmt(c.order.ratio != null ? c.order.ratio : c.order.jaccard, 2)} (+${(c.order.added || []).length} / -${(c.order.removed || []).length})`);
    if (c.meta_changed) p.push('meta 바뀜');
    if (c.answer && !c.answer.same) p.push(`답변 유사도 ${fmt(c.answer.ratio, 2)}`);
    if (c.changed) p.push('★ 기준과 다름');
    p.push('클릭: diff');
    return p.join('\n');
  }
  function showSweep(rec, cmp, text) {
    PL.rec = rec; PL.cmp = cmp || {}; PL.text = text || '';
    const head = $('#pl-sweep-head'); head.classList.remove('hidden');
    const best = PL.cmp.best || {};
    head.innerHTML = `<b>sw_${esc(rec.id)}</b> — <code>${esc(rec.key)}</code> = ${esc((rec.values || []).map(String).join(', '))} · ${esc(rec.kind)} · 재시작점 <b>${esc(rec.point_label || rec.point)}</b> · 기준 요청 #${esc(String(rec.request_id))}${rec.repeats > 1 ? ' · ' + rec.repeats + '회 반복' : ''} · ${fmt(rec.ms, 0)} ms · 성공 ${rec.n_ok}/${(rec.runs || []).length}` +
      `<br><span class="muted">질의: ${esc(String(rec.query || '').slice(0, 120))}</span>` +
      (Object.keys(best).length ? `<br><span class="best">최적 힌트:</span> ` +
        [best.groundedness ? `groundedness 최고 = <b>${esc(String(best.groundedness.value))}</b> (${fmt(best.groundedness.groundedness, 2)})` : '',
          best.ms ? `가장 빠름 = <b>${esc(String(best.ms.value))}</b> (${fmt(best.ms.ms, 0)} ms)` : '',
          best.citations ? `인용 최다 = <b>${esc(String(best.citations.value))}</b> (${best.citations.n_citations})` : '',
          best.tokens ? `토큰 최소 = <b>${esc(String(best.tokens.value))}</b> (${fmtK(best.tokens.tokens)})` : ''].filter(Boolean).join(' · ') : '') +
      (PL.cmp.error ? `<br><span class="bad">${esc(PL.cmp.error)}</span>` : '') +
      (rec.path ? `<br><span class="muted small">파일: ${esc(rec.path)} · CLI <code>sweep show ${esc(rec.id)}</code> · <code>sweep compare ${esc(rec.id)}</code></span>` : '');
    const runs = PL.cmp.runs || [], stages = PL.cmp.stages || rec.stages || [];
    const colHead = (r) => `${esc(String(r.value))}${rec.repeats > 1 ? ' #' + ((r.repeat || 0) + 1) : ''}${r.is_base ? ' (기준)' : ''}`;
    let html = `<table><tr><th>단계 ╲ 값</th>${runs.map((r) => `<th class="val ${r.is_base ? 'base' : ''}" title="${r.error ? esc(r.error) : '요청 #' + esc(String(r.request_id || '')) + (r.is_base ? ' · 기준 (첫 값의 첫 실행)' : '')}">${colHead(r)}</th>`).join('')}</tr>`;
    stages.forEach((stg) => {
      html += `<tr><td class="stage" title="${esc(stg)} 단계 가족 (sweep.STAGE_FAMILY)">${esc(stg)}</td>` + runs.map((r, i) => {
        if (r.error) return `<td class="cell err" data-run="${i}" data-stage="${esc(stg)}" title="${esc(r.error)}">오류</td>`;
        const c = (r.stages || {})[stg] || {};
        return `<td class="cell ${c.changed ? 'changed' : ''} ${c.replayed ? 'replayed' : ''}" data-run="${i}" data-stage="${esc(stg)}" title="${esc(cellTitle(stg, c))}">${cellText(c)}</td>`;
      }).join('') + '</tr>';
    });
    html += `<tr class="final"><td class="stage" title="총 ms · groundedness · 인용 수 · result_type · 답변 앞부분">결과</td>` + runs.map((r, i) => {
      if (r.error) return `<td class="cell err" data-run="${i}" data-stage="_final" title="${esc(r.error)}">${esc(r.error.slice(0, 80))}</td>`;
      const run = (rec.runs || [])[i] || {}; const g = r.groundedness; const ans = String(run.answer || '');
      const changed = !r.is_base && (r.result_type_changed || (r.answer && !r.answer.same));
      return `<td class="cell ${changed ? 'changed' : ''}" data-run="${i}" data-stage="_final" title="클릭: 최종 순위 · 컨텍스트 · 답변 diff"><b>${fmt(r.ms, 0)} ms</b>${r.ms_delta != null && !r.is_base ? ` <small class="${r.ms_delta > 0 ? 'bad' : 'ok'}">(${r.ms_delta > 0 ? '+' : ''}${fmt(r.ms_delta, 0)})</small>` : ''} · g=${g == null ? '-' : fmt(g, 2)} · 인용 ${r.n_citations == null ? '-' : r.n_citations} · <span class="pill">${esc(r.result_type || '')}</span><div class="muted">${esc(ans.slice(0, 140))}${ans.length > 140 ? '…' : ''}</div></td>`;
    }).join('') + '</tr></table>';
    $('#pl-sweep-grid').innerHTML = html;
    $$('#pl-sweep-grid td.cell').forEach((td) => td.onclick = () => { $$('#pl-sweep-grid td.cell.sel').forEach((x) => x.classList.remove('sel')); td.classList.add('sel'); showDiff(parseInt(td.dataset.run, 10), td.dataset.stage); });
    $('#pl-sweep-diff').classList.add('hidden');
  }
  function listDiff(d, name) {
    if (!d) return '';
    const n = (d.added || []).length + (d.removed || []).length;
    return `<div><b>${name}</b>: ${d.same ? '기준과 같음' : `유사도 ${fmt(d.ratio != null ? d.ratio : d.jaccard, 2)} · 추가 ${(d.added || []).length} · 제거 ${(d.removed || []).length}${d.moved ? ' · 이동 ' + d.moved.length : ''}${d.top1_same === false ? ' · <span class="bad">1위 바뀜</span>' : ''}`}` +
      (!d.same && n ? `<div class="dl">${(d.added || []).map((x) => `<span class="add">+ ${esc(x)}</span>`).join('\n')}${(d.added || []).length && (d.removed || []).length ? '\n' : ''}${(d.removed || []).map((x) => `<span class="del">- ${esc(x)}</span>`).join('\n')}</div>` : '') +
      (d.moved && d.moved.length ? `<div class="dl muted">${d.moved.map((m) => `↕ ${esc(m.id)} ${m.from + 1}→${m.to + 1}`).join('\n')}</div>` : '') + '</div>';
  }
  function showDiff(i, stg) {
    const r = (PL.cmp.runs || [])[i], run = (PL.rec.runs || [])[i] || {}; const box = $('#pl-sweep-diff'); if (!r) return;
    box.classList.remove('hidden');
    const label = `<code>${esc(PL.rec.key)}</code> = <b>${esc(String(r.value))}</b>${r.is_base ? ' (기준)' : ' vs 기준 ' + esc(String((PL.cmp.baseline || {}).value))} · 요청 #${esc(String(r.request_id || ''))}`;
    let html = `<h4>${stg === '_final' ? '결과' : esc(stg)} — ${label} <button class="mini secondary" data-pl-diffclose title="diff 패널 닫기" style="float:right">✕</button></h4>`;
    if (r.error) { box.innerHTML = html + `<div class="bad">${esc(r.error)}</div>`; wireDiffClose(box); return; }
    if (stg === '_final') {
      html += `<div>총 ${fmt(r.ms, 0)} ms${r.ms_delta != null ? ` (Δ ${r.ms_delta > 0 ? '+' : ''}${fmt(r.ms_delta, 0)})` : ''} · groundedness ${r.groundedness == null ? '-' : fmt(r.groundedness, 2)}${r.groundedness_delta ? ` (Δ ${r.groundedness_delta > 0 ? '+' : ''}${fmt(r.groundedness_delta, 2)})` : ''} · 인용 ${r.n_citations == null ? '-' : r.n_citations} · 토큰 ${r.tokens == null ? '-' : fmtK(r.tokens)} · result_type ${esc(r.result_type || '')}${r.result_type_changed ? ' <span class="bad">(바뀜)</span>' : ''} · 판정 ${esc(r.verdict || '-')}${r.verdict_changed ? ' <span class="bad">(바뀜)</span>' : ''}</div>`;
      html += listDiff(r.hits, '최종 순위 (상위 id)') + listDiff(r.context, '컨텍스트 청크');
      const a = r.answer || {};
      html += `<div><b>답변</b>: ${a.same ? '기준과 같음' : `유사도 ${fmt(a.ratio, 2)} · ${a.chars_delta > 0 ? '+' : ''}${a.chars_delta}자`}</div>` +
        ((a.diff || []).length ? `<div class="dl">${a.diff.map((l) => `<span class="${l[0] === '+' ? 'add' : l[0] === '-' ? 'del' : ''}">${esc(l)}</span>`).join('\n')}</div>` : '') +
        `<details><summary class="muted small">이 값의 답변 전문</summary><pre class="pre">${esc(run.answer || '')}</pre></details>`;
    } else {
      const c = (r.stages || {})[stg] || {}; const rs = ((run.stages || {})[stg]) || {};
      html += `<div>${c.present ? (c.skipped ? '건너뜀' : `${fmt(c.ms, 1)} ms${c.ms_delta != null ? ` (Δ ${c.ms_delta > 0 ? '+' : ''}${fmt(c.ms_delta, 1)})` : ''}`) : '이 실행에 없음'}${c.replayed ? ' · <span class="pill replay">재생</span>' : ''}${c.state_changed ? ' · <span class="bad">상태 바뀜 (재생/건너뜀/유무)</span>' : ''}${c.meta_changed ? ' · meta 바뀜' : ''}${c.changed ? ' · <b class="warntxt">★ 기준과 다름</b>' : (r.is_base ? '' : ' · 기준과 같음')}</div>`;
      if (c.order) html += listDiff(c.order, stg === 'context' ? '컨텍스트 청크' : '순위 (상위 ' + (c.order.n || 0) + ')');
      if (c.answer) html += `<div><b>답변</b>: ${c.answer.same ? '기준과 같음' : `유사도 ${fmt(c.answer.ratio, 2)} · ${c.answer.chars_delta > 0 ? '+' : ''}${c.answer.chars_delta}자`} <span class="muted">(전문 diff 는 결과 행)</span></div>`;
      if (c.groundedness_delta != null) html += `<div>groundedness Δ ${c.groundedness_delta > 0 ? '+' : ''}${fmt(c.groundedness_delta, 3)}</div>`;
      if (c.verdict !== undefined) html += `<div>판정 ${esc(c.verdict || '-')}${c.verdict_changed ? ' <span class="bad">(바뀜)</span>' : ''}</div>`;
      if (rs.names && rs.names.length) html += `<div class="muted small">trace 단계: ${rs.names.map(esc).join(', ')}</div>`;
      if (rs.meta && Object.keys(rs.meta).length) html += `<details open><summary class="muted small">meta (이 값)</summary><pre class="pre">${esc(JSON.stringify(rs.meta, null, 1))}</pre></details>`;
      html += `<details><summary class="muted small">compare 항목 전체 (JSON)</summary><pre class="pre">${esc(JSON.stringify(c, null, 1))}</pre></details>`;
    }
    html += `<div class="muted small" style="margin-top:6px">요청 #${esc(String(r.request_id || ''))} 의 전체 trace: Observability › 요청 프로파일 · 텍스트 표: 위 "⧉ 텍스트 복사" · CLI <code>sweep compare ${esc(PL.rec.id)}</code></div>`;
    box.innerHTML = html;
    wireDiffClose(box);
  }
  function wireDiffClose(box) { const cl = $('#pl-sweep-diff [data-pl-diffclose]'); if (cl) cl.onclick = () => box.classList.add('hidden'); }

  // ---------------- 지난 스윕 ----------------
  async function loadSweepList() {
    const j = await api('/api/sweep'); const box = $('#pl-sweep-list'); if (!box) return;
    if (!j || j.error) { box.innerHTML = '<span class="muted">목록을 읽지 못했습니다</span>'; return; }
    PL.sweeps = j.sweeps || [];
    if (!PL.sweeps.length) { box.innerHTML = '<div class="muted">저장된 스윕이 없습니다 (config.json sweep_dir · sweep_keep).</div>'; return; }
    box.innerHTML = PL.sweeps.map((s) => `<div class="sw-row ${PL.rec && PL.rec.id === s.id ? 'sel' : ''}" data-sw="${esc(s.id)}" title="클릭 → 기록과 비교를 격자로 불러옵니다">` +
      `<code>sw_${esc(s.id)}</code> <b>${esc(s.key || '')}</b> <span class="muted">= ${esc((s.values || []).map(String).join(', ').slice(0, 60))}</span> <span class="pill">${esc(s.point || '')}</span> <span class="q">${esc(s.query || '')}</span>` +
      `<span class="muted">${s.n_runs || 0}회${s.n_errors ? ' · <span class="bad">오류 ' + s.n_errors + '</span>' : ''} · ${fmt(s.ms, 0)} ms · ${dt(s.created || s.mtime)}</span>${s.error ? `<span class="bad">${esc(s.error)}</span>` : ''}</div>`).join('');
    $$('#pl-sweep-list .sw-row').forEach((r) => r.onclick = async () => {
      const j2 = await api('/api/sweep?id=' + encodeURIComponent(r.dataset.sw));
      if (!j2 || j2.error || !j2.record) return;
      showSweep(j2.record, j2.compare, j2.text);
      $$('#pl-sweep-list .sw-row').forEach((x) => x.classList.toggle('sel', x === r));
      $('#pl-sweep-head').scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    });
  }

  // ---------------- 배선 ----------------
  // 보기 3종 · 시간 겹쳐 보기 · 요청 토글 반영 — 예전 '구조·흐름' 탭의 옵션을 그대로 흡수했다.
  $$('#pl-view button').forEach((b) => b.onclick = () => {
    PL.view = b.dataset.v; syncViewSeg();
    if (PL.view === 'all') { PL.sel = null; PL.selPhase = null; }
    renderBlocks(); renderDetail();
  });
  $('#pl-last').onchange = () => { renderFlows(); renderBlocks(); renderDetail(); };
  $('#pl-live').onchange = () => { renderBlocks(); renderDetail(); renderPhaseNav(); };
  $('#btn-pl-phases-toggle').onclick = () => {
    const f = PL.arch && PL.arch.flows[PL.flow]; if (!f) return;
    const phs = (f.phases || []).map((p) => PL.flow + ':' + p.key);
    const anyOpen = phs.some((k) => !PL.phaseCollapsed[k]);
    phs.forEach((k) => { PL.phaseCollapsed[k] = anyOpen; });
    $('#btn-pl-phases-toggle').textContent = anyOpen ? '▼ 페이즈 펴기' : '▲ 페이즈 접기';
    renderBlocks();
  };
  $('#btn-pl-refresh').onclick = () => loadPipeline();
  $('#pl-sw-allkeys').onchange = fillSweepForm;
  $('#pl-sw-key').onchange = onKeyChange;
  $('#pl-sw-mode').onchange = onModeChange;
  $('#btn-pl-sweep-run').onclick = runSweep;
  $('#btn-pl-sweep-refresh').onclick = loadSweepList;
  $('#btn-pl-sweep-copy').onclick = () => { if (!PL.text) { toast('복사할 스윕 결과가 없습니다 — 먼저 실행하거나 "지난 스윕" 에서 고르세요'); return; } copyText(PL.text, '스윕 sw_' + PL.rec.id); };
  // 토글·요청 튜닝이 어디서 바뀌어도(사이드바·프리셋·Pipeline) 블록과 상세를 다시 그린다. 콜백 슬롯은 하나라 observability.js 의 것과 이어 쓴다.
  const prevToggleChanged = loaders._toggleChanged;
  loaders._toggleChanged = () => { if (prevToggleChanged) prevToggleChanged(); if (PL.arch && LW.tabVisible('pipeline')) { renderBlocks(); if (PL.sel) renderDetail(); } };
  loaders.pipeline = loadPipeline;
})(window.LW);
