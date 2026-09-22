/* LLM Wiki UI core — utilities, API, sidebar(toggles/presets/overrides), status, trace renderers, navigation, theme. */
window.LW = (function () {
  'use strict';
  const $ = (s, r) => (r || document).querySelector(s);
  const $$ = (s, r) => Array.from((r || document).querySelectorAll(s));
  const esc = (s) => String(s == null ? '' : s).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
  const fmt = (n, d) => (n == null || n === '' || isNaN(Number(n)) ? '-' : Number(n).toFixed(d == null ? 1 : d));
  const fmtK = (n) => (n == null ? '-' : n >= 1e6 ? (n / 1e6).toFixed(1) + 'M' : n >= 1e3 ? (n / 1e3).toFixed(1) + 'k' : String(n));
  const ts = (t) => (t ? new Date(t * 1000).toLocaleTimeString() : '-');
  const dt = (t) => (t ? new Date(t * 1000).toLocaleString() : '-');
  const PALETTE = ['#2a78d6', '#eb6834', '#1baf7a', '#eda100', '#e87ba4', '#008300', '#4a3aa7', '#e34948'];
  const STAGE_COLOR = { fts_search: '#2a78d6', fts_search_rules: '#2a78d6', fts_search_alt: '#5ea0f0', vector_search: '#eb6834', graph_search: '#1baf7a', doc_vector_search: '#4a3aa7', rerank_llm: '#eda100', rerank_api: '#eda100', rerank_local: '#eda100', answer_llm: '#4a3aa7', answer_extractive: '#4a3aa7', answer_insufficient: '#e34948', rule_extract: '#1baf7a', llm_extract: '#e87ba4', embed: '#eb6834', load_corpus: '#898781', chunk_index: '#2a78d6', graph_build: '#1baf7a', wiki_pages: '#008300', providers: '#e34948', cache_hit: '#008300', precompute_hit: '#008300', context: '#4a3aa7', rrf_fuse: '#eda100', boost: '#eda100', evidence_check: '#e87ba4', fallback: '#e34948', claim_check: '#e87ba4', query_rules: '#2a78d6', time_scope: '#898781', pins: '#eda100', health: '#898781', verify: '#898781' };
  const STATE = { settings: null, toggleNames: [], toggleHelp: {}, settingHelp: {}, toggleGroups: [], presets: [], lastQueryId: null, lastRequestId: null, lastResult: null, graph: null, providers: null, roles: [], status: null };
  const loaders = {};

  function toast(msg) { const t = $('#toast'); t.textContent = msg; t.classList.add('show'); setTimeout(() => t.classList.remove('show'), 2800); }
  // ---------------- 클립보드 복사 — 3단 대체 경로 (2026-09-18, 요청 16) ----------------
  // 사내 배포는 `http://호스트:포트` 접속이 기본이라 **보안 컨텍스트가 아니다** → navigator.clipboard 자체가 undefined.
  // 그래서 ① clipboard API(https/localhost 에서만) → ② 숨은 textarea + execCommand('copy') (http 에서도 사용자 클릭이면 동작)
  // → ③ 둘 다 안 되면 본문이 선택된 창을 띄워 Ctrl+C 로 복사하게 한다. 어느 경로든 예외를 밖으로 던지지 않는다.
  // 반환: 'api' | 'exec' | 'modal' (모두 truthy — "복사 시도가 끝났다"). 성공/실패는 toast 로 알린다.
  async function copyText(text, label) {
    text = String(text == null ? '' : text); label = label || '';
    const okMsg = (label ? label + ' ' : '') + '복사됨' + (text.length > 200 ? ' (' + fmtK(text.length) + '자)' : '');
    if (window.isSecureContext && navigator.clipboard && navigator.clipboard.writeText) {
      try { await navigator.clipboard.writeText(text); toast(okMsg); return 'api'; } catch (e) { /* 권한 거부 등 → 아래 경로 */ }
    }
    let ok = false;
    try {
      const ta = document.createElement('textarea');
      ta.value = text; ta.setAttribute('readonly', ''); ta.setAttribute('aria-hidden', 'true');
      ta.style.cssText = 'position:fixed;top:0;left:0;width:2px;height:2px;padding:0;border:0;opacity:0;pointer-events:none;z-index:-1';
      document.body.appendChild(ta);
      const active = document.activeElement;
      ta.focus(); ta.select();
      try { ta.setSelectionRange(0, text.length); } catch (e) { /* ignore */ }
      try { ok = document.execCommand('copy'); } catch (e) { ok = false; }
      ta.remove();
      if (active && active.focus) { try { active.focus(); } catch (e) { /* ignore */ } }
    } catch (e) { ok = false; }
    if (ok) { toast(okMsg); return 'exec'; }
    copyModal(text, label);
    toast('클립보드에 자동으로 넣지 못했습니다 — 창에서 Ctrl+C');
    return 'modal';
  }
  // ③ 마지막 수단: 본문을 선택해 둔 창. Ctrl+C 를 누르면 닫힌다.
  function copyModal(text, label) {
    let ov = $('#copy-modal'); if (ov) ov.remove();
    ov = document.createElement('div'); ov.id = 'copy-modal'; ov.className = 'modal-bg';
    ov.innerHTML = `<div class="modal">
      <h3>⧉ ${esc(label || '복사')}</h3>
      <p class="modal-msg">브라우저가 클립보드 접근을 막았습니다 — 보통 <code>http://</code> 로 연 화면입니다 (보안 컨텍스트가 아니면 <code>navigator.clipboard</code> 가 없고, 자동 복사도 거부될 수 있습니다).
        아래 내용이 <b>선택된 상태</b>입니다. <b>Ctrl+C</b> (Mac ⌘C) 를 누르면 복사되고 창이 닫힙니다.</p>
      <textarea id="copy-modal-text" readonly spellcheck="false" style="min-height:220px"></textarea>
      <div class="modal-actions"><button class="secondary" id="copy-modal-select">다시 선택</button><button id="copy-modal-close">닫기</button></div>
      <div class="muted small">${fmtK(text.length)}자 · 항상 복사되게 하려면 https 또는 localhost 로 접속하거나, 브라우저 정책에서 이 주소를 "안전한 출처" 로 등록하세요 (docs/BRINGUP_GUIDE 참고).</div>
    </div>`;
    document.body.appendChild(ov);
    const ta = $('#copy-modal-text'); ta.value = text;
    const sel = () => { ta.focus(); ta.select(); try { ta.setSelectionRange(0, ta.value.length); } catch (e) { /* ignore */ } };
    const close = () => ov.remove();
    $('#copy-modal-select').onclick = sel;
    $('#copy-modal-close').onclick = close;
    ov.addEventListener('keydown', (e) => {
      if (e.key === 'Escape') close();
      else if ((e.ctrlKey || e.metaKey) && String(e.key).toLowerCase() === 'c') setTimeout(() => { toast('복사됨 (Ctrl+C)'); close(); }, 120);
    });
    ov.addEventListener('mousedown', (e) => { if (e.target === ov) close(); });
    sel();
  }
  // ---------------- 권한 단계 확인(step-up) 모달 ----------------
  // 서버가 428 + need{confirm|phrase|password} 를 돌려주면 여기서 사용자에게 묻고 같은 요청을 다시 보낸다.
  // need.level: warn(변경) · admin(관리자 설정) · destructive(색인/DB 삭제 — 확인 문구 + 로컬 계정이면 비밀번호)
  function stepUp(need, path) {
    return new Promise((resolve) => {
      let ov = $('#stepup'); if (ov) ov.remove();
      ov = document.createElement('div'); ov.id = 'stepup'; ov.className = 'modal-bg';
      const destructive = need.level === 'destructive';
      const who = (STATE.auth && STATE.auth.user) || {};
      ov.innerHTML = `<div class="modal ${destructive ? 'danger' : ''}">
        <h3>${destructive ? '⚠ 파괴적 작업 확인' : need.level === 'admin' ? '관리자 설정 변경' : '변경 작업 확인'}</h3>
        <div class="modal-op"><code>${esc(need.op || path)}</code> <span class="pill">${esc(need.label || need.level || '')}</span></div>
        ${destructive ? `<p class="modal-msg">이 작업은 <b>색인/DB 를 지우거나 통째로 바꿉니다</b>. 다른 사용자의 검색이 중단되고, 다시 만드는 데 시간·토큰이 듭니다.${need.snapshot ? ' 직전 상태는 <b>data/snapshots/</b> 에 자동 저장되어 <code>snapshot restore</code> 로 되돌릴 수 있습니다.' : ''}</p>` : `<p class="modal-msg">되돌릴 수 있는 변경이지만 다른 사용자에게도 영향이 있습니다. 진행할까요?</p>`}
        ${need.phrase ? `<label>확인 문구 <b>${esc(need.phrase)}</b> 를 그대로 입력<input id="su-phrase" type="text" autocomplete="off" spellcheck="false" placeholder="${esc(need.phrase)}"></label>` : ''}
        ${need.password ? `<label>비밀번호 재입력 (${esc(who.name || '')})<input id="su-pw" type="password" autocomplete="current-password"></label>` : ''}
        <div class="modal-actions"><button class="secondary" id="su-cancel">취소</button><button id="su-ok" class="${destructive ? 'danger' : ''}">${destructive ? '삭제하고 진행' : '진행'}</button></div>
        <div class="muted small">이 작업은 감사 로그(logs/audit.jsonl)에 사용자 이름과 함께 기록됩니다.</div>
      </div>`;
      document.body.appendChild(ov);
      const done = (v) => { ov.remove(); resolve(v); };
      $('#su-cancel').onclick = () => done(null);
      ov.addEventListener('keydown', (e) => { if (e.key === 'Escape') done(null); });
      $('#su-ok').onclick = () => {
        const extra = { _confirm: true };
        if (need.phrase) { const v = ($('#su-phrase').value || '').trim(); if (v !== need.phrase) { $('#su-phrase').style.borderColor = 'var(--critical)'; $('#su-phrase').focus(); return; } extra._phrase = v; }
        if (need.password) { extra._password = $('#su-pw').value; if (!extra._password) { $('#su-pw').focus(); return; } }
        done(extra);
      };
      const first = $('#su-phrase') || $('#su-pw') || $('#su-ok'); if (first) first.focus();
    });
  }
  // 로그인 화면으로 보낼 때: 현재 그룹/탭을 해시로 남겨 돌아왔을 때 같은 자리로 복귀하고, replace 로 이동해 "뒤로가기 → 다시 /login" 튕김을 막는다.
  function gotoLogin() {
    if (STATE.leaving) return;
    STATE.leaving = true;
    location.replace('/login?next=' + encodeURIComponent(location.pathname + (location.hash || '')));
  }
  async function api(path, body, _retry) {
    const opts = body === undefined ? { headers: { 'X-Requested-With': 'llmwiki' } } : { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-Requested-With': 'llmwiki' }, body: JSON.stringify(body) };
    let r;
    try { r = await fetch(path, opts); } catch (e) { const j = { error: '서버에 연결할 수 없습니다 (' + path + ')', offline: true }; toast(j.error); return j; }
    let j; try { j = await r.json(); } catch (e) { j = { error: 'invalid response ' + r.status }; }
    if (r.status === 401) {
      // 게스트(anonymous_role)가 상위 작업을 눌렀을 때는 화면을 버리지 않고 로그인 안내만 (need 가 있으면 어떤 역할이 필요한지 표시)
      if (j && j.need && STATE.auth && STATE.auth.user && STATE.auth.user.via === 'anon') {
        toast('로그인 필요: ' + (j.need.op || path) + ' 은(는) ' + (j.need.role || '') + ' 이상 (게스트 ' + STATE.auth.user.role + ')');
        if (confirm('이 작업은 로그인이 필요합니다 (' + (j.need.role || '') + ' 이상). 로그인 화면으로 이동할까요?')) gotoLogin();
        return Object.assign({ unauthorized: true, cancelled: true }, j);
      }
      gotoLogin();
      return Object.assign({ unauthorized: true }, j);
    }
    if (r.status === 429 || (r.status === 503 && j && j.code)) {   // 요청 관리자: 속도 제한 · 대기열/점검
      const wait = j.retry_after_s ? ' (' + Math.ceil(j.retry_after_s) + '초 후 재시도)' : '';
      toast((r.status === 429 ? '요청 제한: ' : '서버 혼잡: ') + (j.error || '') + wait);
      return Object.assign({ busy: true }, j);
    }
    if (r.status === 499) { toast('취소됨'); return Object.assign({ cancelled: true }, j); }
    if (r.status === 428 && j && j.need && !_retry) {
      const extra = await stepUp(j.need, path);
      if (!extra) { toast('취소됨'); return { error: 'cancelled', cancelled: true }; }
      return api(path, Object.assign({}, body || {}, extra), true);
    }
    if (r.status === 428 && j && j.need && _retry) { toast('확인 실패: ' + (j.need.password ? '비밀번호가 올바르지 않습니다' : j.error)); return j; }
    if (r.status === 403) { toast('권한 없음: ' + (j.error || '')); return Object.assign({ forbidden: true }, j); }
    if (j && j.error && !j.result && !Array.isArray(j)) { toast('오류: ' + j.error); console.error(path, j); }
    return j;
  }
  // 없어진 탭 → 대신 열 곳 ('그룹/탭' 또는 '탭' 으로 찾는다). 화면을 합칠 때 예전 링크를 살려 두기 위한 표.
  const TAB_ALIAS = { 'observability/arch': ['pipeline', 'pipeline'], arch: ['pipeline', 'pipeline'] };
  function switchTab(name) {
    const a = TAB_ALIAS[name];
    if (a) { switchGroup(a[0]); name = a[1]; }
    const b = $$('.tabs button').find((x) => x.dataset.tab === name); if (b) b.click();
  }
  function switchGroup(name) { const b = $$('.groups button').find((x) => x.dataset.group === name); if (b) b.click(); }

  // ---------------- sidebar: toggles (auto from server), presets, overrides ----------------
  // 토글을 **켰을 때** 세 축이 어떻게 되는지를 배지로 보여 준다.
  // 이름만 보고는 "이걸 켜면 뭐가 좋아지고 뭘 내주나" 를 알 수 없어서, 누르기 전에 알 수 있게 한 것.
  const AXIS_LABEL = { q: '품질', s: '속도', t: '토큰' };
  // 세 축을 **늘 같은 자리**에 그린다 (품 · 속 · 토 순서). 영향 없는 축은 빈 칸으로 남겨
  // 세로로 훑을 때 "이 줄은 속도를 내주는구나" 가 한눈에 보이게 한다.
  function effectBadges(t) {
    const e = (STATE.toggleEffect || {})[t] || {};
    return ['q', 's', 't'].map((k) => {
      const ax = (STATE.toggleAxes || AXIS_LABEL)[k] || k;
      if (!e[k]) return `<span class="fx none" title="켜도 ${ax}에는 뚜렷한 영향이 없습니다">·</span>`;
      const up = e[k] > 0;
      return `<span class="fx ${up ? 'up' : 'down'}" title="켜면 ${ax}이(가) ${up ? '좋아집니다' : '나빠집니다'}">${ax[0]}${up ? '↑' : '↓'}</span>`;
    }).join('');
  }
  function effectText(t) {
    const e = (STATE.toggleEffect || {})[t] || {};
    const parts = ['q', 's', 't'].filter((k) => e[k]).map((k) => (AXIS_LABEL[k] + (e[k] > 0 ? ' 좋아짐' : ' 나빠짐')));
    return parts.length ? '\n\n[켜면] ' + parts.join(' · ') : '\n\n[켜도] 품질·속도·토큰에 뚜렷한 영향 없음';
  }
  // 배지는 **이름 뒤**, 줄의 오른쪽 끝에 온다 (2026-09-18, 요청 13). 예전에는 이름 앞에 두어 "켜면 무엇이 좋아지나" 를
  // 먼저 읽게 했는데, 체크박스와 이름 사이에 배지가 끼어 이름이 들쭉날쭉해지고 "이 줄이 무엇인지" 를 찾는 눈이 더 느렸다.
  // 지금은 [체크] 이름 …… 품↑ 속↓ 토↓ — 이름이 왼쪽에 정렬되고 세 축은 오른쪽 한 열에 고정된다 (CSS .fxs margin-left:auto).
  // 툴팁(label title) 은 그대로: 설명 + effectText.
  function toggleRow(t, help) {
    return `<label data-t="${esc(t)}" title="${esc((help || '') + effectText(t))}">` +
      `<input type="checkbox" data-toggle="${t}">` +
      `<span class="tname">${esc(t)}</span>` +
      `<span class="fxs">${effectBadges(t)}</span></label>`;
  }
  // 토글 묶음 하나 = 사이드바 블록(.sb-block) 하나. 제목(.tg-title.sb-head) 클릭으로 접히고, 상태 키는 "tg:<묶음 키>".
  function toggleGroupHtml(key, title, stage, hint, count, rowsHtml, perf) {
    return `<div class="toggle-group sb-block ${perf ? 'perf' : ''}" data-g="${esc(key)}" data-sb="tg:${esc(key)}">` +
      `<div class="tg-title sb-head" title="클릭: 접기/펼치기">${stage}${esc(title)} <small>(${count})</small></div>` +
      `<div class="sb-body">${hint}${rowsHtml}</div></div>`;
  }
  function buildSidebar(j) {
    const box = $('#toggle-groups');
    const known = new Set();
    STATE.toggleEffect = j.toggle_effect || {};
    STATE.toggleAxes = j.toggle_axes || AXIS_LABEL;
    box.innerHTML = (j.toggle_groups || []).map((g) => {
      g.toggles.forEach((t) => known.add(t));
      const stage = g.stage ? `<span class="tg-stage">${esc(g.stage)}</span>` : '';
      const hint = g.hint ? `<div class="tg-hint">${esc(g.hint)}</div>` : '';
      return toggleGroupHtml(g.key, g.title, stage, hint, g.toggles.length,
        g.toggles.filter((t) => j.toggle_names.includes(t)).map((t) => toggleRow(t, j.toggle_help[t])).join(''), g.perf);
    }).join('');
    const rest = j.toggle_names.filter((t) => !known.has(t));
    if (rest.length) box.innerHTML += toggleGroupHtml('rest', '기타', '', '', rest.length, rest.map((t) => toggleRow(t, j.toggle_help[t])).join(''), false);
    // 접기/펴기는 #sidebar 위임 핸들러(initSidebar)가 처리한다 — 여기서는 저장된 접힘 상태만 다시 입힌다
    applySidebarState();
    applyFxFilter();
    $$('[data-toggle]').forEach((cb) => cb.addEventListener('change', () => {
      // 사용자가 손으로 바꾼 값은 기억해 두고(프리셋을 껐다 켜도 유지), 프리셋이 정한 값과 다르면 프리셋 표시를 지운다
      const t = cb.dataset.toggle, base = (STATE.settings && STATE.settings.toggles) || {};
      if (cb.checked === !!base[t]) delete PRESET.manual[t]; else PRESET.manual[t] = cb.checked;
      if (t in PRESET.set && PRESET.set[t] !== cb.checked) { delete PRESET.set[t]; markPresetToggles(); }
      updateCli(); applyFxFilter(); if (loaders._toggleChanged) loaders._toggleChanged();
    }));
    STATE.presetDefs = j.preset_defs || {};
    $('#preset-box').innerHTML = (j.presets || []).map((p) => {
      const d = STATE.presetDefs[p] || {};
      return `<label title="${esc(presetTooltip(p))}"><input type="checkbox" data-preset="${esc(p)}"> <b>${esc(p)}</b><span class="pdesc">${esc((d.desc || '').split(' — ')[0])}</span><span class="pinfo" data-pinfo="${esc(p)}" title="이 프리셋이 바꾸는 항목 전부 보기">ⓘ</span></label>`;
    }).join('') || '<span class="muted small">(presets.json 없음)</span>';
    $$('[data-preset]').forEach((cb) => cb.addEventListener('change', () => { applyPresets(); }));
    $$('[data-pinfo]').forEach((b) => b.addEventListener('click', (e) => { e.preventDefault(); e.stopPropagation(); showPresetDetail(b.dataset.pinfo === PRESET.open ? null : b.dataset.pinfo); }));
  }
  // ---------------- presets → 토글 체크박스 반영 ----------------
  // PRESET.set: 현재 체크된 프리셋(과 Ask 의 mode)이 정한 토글 값. PRESET.manual: 사용자가 손으로 바꾼 토글. PRESET.open: 상세 패널이 열린 프리셋.
  const PRESET = { set: {}, src: {}, manual: {}, open: null };
  function presetNames() { return $$('[data-preset]').filter((c) => c.checked).map((c) => c.dataset.preset); }
  function modePreset() { const m = $('#q-mode') && $('#q-mode').value; return m === 'deep' ? 'deep_research' : m === 'fast' ? 'speed' : null; }
  function presetTooltip(name) {
    const d = STATE.presetDefs[name] || {}; const tg = d.toggles || {}, tu = d.tuning || {}, st = d.settings || {};
    const on = Object.keys(tg).filter((k) => tg[k]), off = Object.keys(tg).filter((k) => !tg[k]);
    const kv = (o) => Object.keys(o).map((k) => k + '=' + JSON.stringify(o[k])).join(', ');
    return [name + ' — ' + (d.desc || ''), '', '토글 ON (' + on.length + '): ' + (on.join(', ') || '-'), '토글 OFF (' + off.length + '): ' + (off.join(', ') || '-'),
      '튜닝 (' + Object.keys(tu).length + '): ' + (kv(tu) || '-'), '설정 (' + Object.keys(st).length + '): ' + (kv(st) || '-'),
      '', '체크하면 위 토글이 아래 체크박스에 바로 반영되고, 튜닝·설정은 요청을 보낼 때 서버에서 함께 적용됩니다. ⓘ 를 누르면 항목별 설명이 보입니다.'].join('\n');
  }
  function applyPresets() {
    // 적용 순서: config 값 → 사용자가 손으로 바꾼 값 → 체크된 프리셋(체크 순서, 뒤가 우선) → Ask 의 mode 프리셋. 서버(/api/query)도 오버라이드 뒤에 프리셋을 얹으므로 동일.
    const base = (STATE.settings && STATE.settings.toggles) || {};
    const names = presetNames(); const mp = modePreset(); if (mp && !names.includes(mp)) names.push(mp);
    PRESET.set = {}; PRESET.src = {};
    names.forEach((n) => { const tg = (STATE.presetDefs[n] || {}).toggles || {}; Object.keys(tg).forEach((k) => { PRESET.set[k] = !!tg[k]; PRESET.src[k] = n + (n === mp && !presetNames().includes(n) ? ' (mode)' : ''); }); });
    $$('[data-toggle]').forEach((cb) => {
      const t = cb.dataset.toggle;
      cb.checked = (t in PRESET.set) ? PRESET.set[t] : (t in PRESET.manual) ? PRESET.manual[t] : !!base[t];
    });
    markPresetToggles(); updateCli(); if (loaders._toggleChanged) loaders._toggleChanged();
    if (PRESET.open) showPresetDetail(PRESET.open);
  }
  // 축으로 걸러 보기 — 토글이 60개가 넘어 "지금 무엇을 찾는지" 로 좁히지 않으면 훑기 어렵다.
  let FX = { axis: '', q: '' };
  function applyFxFilter() {
    const axis = FX.axis, q = (FX.q || '').trim().toLowerCase();
    $$('#toggle-groups label[data-t]').forEach((lab) => {
      const t = lab.dataset.t, e = (STATE.toggleEffect || {})[t] || {};
      const cb = lab.querySelector('[data-toggle]');
      let ok = true;
      if (axis === 'on') ok = !!(cb && cb.checked);
      else if (axis) ok = e[axis] > 0;              // 그 축이 **좋아지는** 것만
      if (ok && q) ok = t.toLowerCase().indexOf(q) >= 0;
      lab.classList.toggle('fx-hidden', !ok);
    });
    // 남은 것이 없는 묶음은 통째로 감춘다 (빈 제목만 남으면 오히려 헷갈린다)
    $$('#toggle-groups .toggle-group').forEach((g) => {
      const vis = $$('label[data-t]', g).filter((l) => !l.classList.contains('fx-hidden')).length;
      g.classList.toggle('fx-hidden', vis === 0 && !!(axis || q));
      const c = g.querySelector('.tg-title small');
      if (c) c.textContent = (axis || q) ? '(' + vis + '/' + $$('label[data-t]', g).length + ')' : '(' + $$('label[data-t]', g).length + ')';
    });
  }
  function initFxFilter() {
    const bar = $('#fx-filter'); if (!bar) return;
    $$('#fx-filter button').forEach((b) => b.onclick = () => {
      FX.axis = b.dataset.fx || '';
      $$('#fx-filter button').forEach((x) => x.classList.toggle('active', x === b));
      applyFxFilter();
    });
    const s = $('#fx-search');
    if (s) s.oninput = () => { FX.q = s.value; applyFxFilter(); };
  }
  // ---------------- 사이드바 블록 접기/펴기 (2026-09-18, 요청 13) ----------------
  // 모든 블록이 .sb-block[data-sb=키] > .sb-head + .sb-body 구조다 (정적 블록은 index.html, 토글 묶음은 buildSidebar).
  // 상태 = { <키>: true|false, _default: true|false } — 키가 없는 블록은 _default 를 따른다.
  // 저장: localStorage llmwiki.sidebar(즉시) + 프로파일 sidebar_collapsed('내 설정 저장' 시, 로그인 계정).
  // 900px 이하(사이드바가 본문 위로 올라오는 폭 — CSS @media 와 같은 값)에서는 저장된 상태가 없으면 기본 접힘:
  // 폰/좁은 창에서 토글 60여 개가 본문을 화면 밖으로 밀어내지 않게.
  const SIDEBAR_NARROW_PX = 900;
  const SIDEBAR = { collapsed: {}, loaded: false, toggles: '' };
  // ---------------- 사이드바 '기능 토글' 블록: 간단히(compact) / 전체(full) (2026-09-18, §2.6 토글 이관) ----------------
  // compact(기본): 토글 묶음(#toggle-groups)·배지 범례·축 필터를 숨기고 "이번 요청 오버라이드: 토글 n · 튜닝 m" 요약 +
  //   '🧭 Pipeline 에서 편집' 버튼만 남긴다. 토글의 본 자리는 Pipeline 페이지(단계별 상세)다.
  // full: 예전 배치. 체크박스([data-toggle])는 두 모드 모두 DOM 에 그대로 있으므로(숨김만) overrides()·프리셋·프로파일·CLI 동등 명령은
  //   어느 쪽에서 바꿔도 같은 상태를 본다 — 상태는 하나다.
  // 저장: localStorage llmwiki.sidebar_toggles(즉시) + 프로파일 sidebar_toggles('내 설정 저장', profiles.py ALLOWED · 기본 compact).
  const SIDEBAR_TOGGLES_DEFAULT = 'compact';
  function sidebarToggles() { return SIDEBAR.toggles === 'full' ? 'full' : SIDEBAR_TOGGLES_DEFAULT; }
  function setSidebarToggles(mode, persist) {
    SIDEBAR.toggles = mode === 'full' ? 'full' : 'compact';
    applySidebarToggles();
    if (persist !== false) { try { localStorage.setItem('llmwiki.sidebar_toggles', SIDEBAR.toggles); } catch (e) { /* ignore */ } }
  }
  function applySidebarToggles() {
    const blk = $('#sidebar .sb-block[data-sb="toggles"]'); if (!blk) return;
    const compact = sidebarToggles() === 'compact';
    blk.classList.toggle('compact', compact);
    const sw = $('#sb-toggles-mode');
    if (sw) {
      sw.textContent = compact ? '전체 보기' : '간단히';
      sw.title = compact ? '토글 묶음 전체를 사이드바에 펼칩니다 (예전 배치 · 프로파일 sidebar_toggles=full). 상태는 이 브라우저에 남고 "내 설정 저장" 으로 계정에도 저장됩니다'
        : '토글 묶음을 접고 "이번 요청 오버라이드" 요약만 남깁니다 (기본 · sidebar_toggles=compact). 토글 편집은 🧭 Pipeline 페이지에서';
    }
    refreshSidebarSummary();
  }
  // "이번 요청 오버라이드: 토글 n · 튜닝 m" — n = config 값과 다르게 체크된 토글 수, m = 요청 단위 튜닝/config 값 수 (Pipeline '이번 요청에만')
  function refreshSidebarSummary() {
    const txt = $('#sb-summary-text'); if (!txt) return;
    const base = (STATE.settings && STATE.settings.toggles) || {};
    const changed = $$('[data-toggle]').filter((cb) => cb.checked !== !!base[cb.dataset.toggle]).map((cb) => cb.dataset.toggle + '=' + (cb.checked ? 'on' : 'off'));
    const tk = Object.keys(TUNING_OV).map((k) => k + '=' + TUNING_OV[k]).concat(Object.keys(SETTING_OV).map((k) => k + '=' + SETTING_OV[k]));
    txt.textContent = '이번 요청 오버라이드: 토글 ' + changed.length + ' · 튜닝 ' + tk.length;
    txt.title = (changed.length ? '토글 (config 값과 다른 것): ' + changed.join(', ') : '토글: config 값 그대로') + '\n' +
      (tk.length ? '튜닝·설정 (이번 요청에만): ' + tk.join(', ') : '튜닝: tuning.json / config.json 값 그대로') +
      '\n\n이 값들은 Ask 질의·⟲ 재실행·평가에 함께 실립니다. 편집: 🧭 Pipeline › 단계 상세';
    txt.classList.toggle('has-ov', changed.length + tk.length > 0);
  }
  function sidebarState() { return Object.assign({}, SIDEBAR.collapsed); }
  function setSidebarState(obj, persist) {
    if (!obj || typeof obj !== 'object') return;
    SIDEBAR.collapsed = {};
    Object.keys(obj).forEach((k) => { SIDEBAR.collapsed[k] = !!obj[k]; });
    SIDEBAR.loaded = true;
    applySidebarState();
    if (persist !== false) saveSidebarLocal();
  }
  function saveSidebarLocal() { try { localStorage.setItem('llmwiki.sidebar', JSON.stringify(SIDEBAR.collapsed)); } catch (e) { /* ignore */ } }
  function loadSidebarLocal() {
    if (SIDEBAR.loaded) return;
    let saved = null; try { saved = JSON.parse(localStorage.getItem('llmwiki.sidebar') || 'null'); } catch (e) { saved = null; }
    if (saved && typeof saved === 'object') { SIDEBAR.collapsed = {}; Object.keys(saved).forEach((k) => { SIDEBAR.collapsed[k] = !!saved[k]; }); }
    else if (window.innerWidth < SIDEBAR_NARROW_PX) SIDEBAR.collapsed = { _default: true };
    let tg = ''; try { tg = localStorage.getItem('llmwiki.sidebar_toggles') || ''; } catch (e) { tg = ''; }
    SIDEBAR.toggles = tg === 'full' ? 'full' : SIDEBAR_TOGGLES_DEFAULT;
    SIDEBAR.loaded = true;
  }
  function applySidebarState() {
    const c = SIDEBAR.collapsed, def = !!c._default;
    $$('#sidebar .sb-block').forEach((b) => {
      const k = b.dataset.sb || '';
      b.classList.toggle('collapsed', (k in c) ? !!c[k] : def);
    });
  }
  function setSidebarAll(collapsed) {
    SIDEBAR.collapsed = { _default: !!collapsed };
    $$('#sidebar .sb-block').forEach((b) => { if (b.dataset.sb) SIDEBAR.collapsed[b.dataset.sb] = !!collapsed; });
    applySidebarState(); saveSidebarLocal();
  }
  function initSidebar() {
    const sb = $('#sidebar'); if (!sb) return;
    loadSidebarLocal();
    // 위임: 토글 묶음은 서버 목록이 바뀔 때마다 다시 만들어지므로 요소마다 핸들러를 달지 않는다
    sb.addEventListener('click', (e) => {
      const h = e.target.closest('.sb-head'); if (!h || !sb.contains(h)) return;
      if (e.target.closest('button, input, select, a')) return;      // 제목 안의 조작 요소는 그대로
      const b = h.closest('.sb-block'); if (!b) return;
      const k = b.dataset.sb || ''; const now = !b.classList.contains('collapsed');
      b.classList.toggle('collapsed', now);
      if (k) { SIDEBAR.collapsed[k] = now; saveSidebarLocal(); }
    });
    const ca = $('#btn-sb-collapse-all'); if (ca) ca.onclick = () => setSidebarAll(true);
    const ea = $('#btn-sb-expand-all'); if (ea) ea.onclick = () => setSidebarAll(false);
    // 토글 블록 간단히/전체 스위치 + 'Pipeline 에서 편집' (compact 모드의 유일한 조작)
    const sw = $('#sb-toggles-mode'); if (sw) sw.onclick = (e) => { e.stopPropagation(); setSidebarToggles(sidebarToggles() === 'compact' ? 'full' : 'compact'); };
    const gp = $('#btn-sb-goto-pipeline'); if (gp) gp.onclick = () => switchGroup('pipeline');
    applySidebarState();
    applySidebarToggles();
  }
  function markPresetToggles() {
    $$('[data-toggle]').forEach((cb) => {
      const t = cb.dataset.toggle, lab = cb.parentElement, by = t in PRESET.set;
      lab.classList.toggle('by-preset', by);
      // 프리셋 출처(← name)는 이름과 배지 사이에 — 배지는 항상 줄의 오른쪽 끝이어야 한다
      let s = lab.querySelector('.src'); if (by) { if (!s) { s = document.createElement('span'); s.className = 'src'; lab.insertBefore(s, lab.querySelector('.fxs')); } s.textContent = '← ' + PRESET.src[t]; } else if (s) s.remove();
      lab.title = (STATE.toggleHelp[t] || '') + effectText(t) + (by ? '\n\n[프리셋 ' + PRESET.src[t] + ' 이(가) ' + (PRESET.set[t] ? 'ON' : 'OFF') + ' 으로 정함 — 손으로 바꾸면 프리셋보다 우선하지 않고 서버에서 프리셋 값이 다시 적용됩니다. 다른 값을 쓰려면 프리셋 체크를 해제하세요]' : '');
    });
  }
  async function showPresetDetail(name) {
    const box = $('#preset-detail'); PRESET.open = name;
    $$('[data-pinfo]').forEach((b) => b.classList.toggle('open', b.dataset.pinfo === name));
    if (!name) { box.classList.add('hidden'); box.innerHTML = ''; return; }
    if (!STATE.tuningHelp) { // 튜닝 항목 설명은 한 번만 가져와 캐시
      STATE.tuningHelp = {};
      try { const tj = await api('/api/tuning'); (tj.tunables || []).forEach((r) => { STATE.tuningHelp[r.key] = (r.stage ? '[' + r.stage + '] ' : '') + (r.desc || '') + (r.impact ? '\n영향: ' + r.impact : '') + (r.default !== undefined ? '\n기본값 ' + JSON.stringify(r.default) + ' · 현재 ' + JSON.stringify(r.value) : ''); }); } catch (e) { /* 설명 없이 진행 */ }
    }
    const d = STATE.presetDefs[name] || {}; const tg = d.toggles || {}, tu = d.tuning || {}, st = d.settings || {};
    const others = presetNames().filter((n) => n !== name);
    const conflict = (k, v) => others.some((n) => { const o = (STATE.presetDefs[n] || {}).toggles || {}; return k in o && !!o[k] !== !!v; });
    const chips = Object.keys(tg).map((k) => `<span class="chip ${tg[k] ? 'on' : 'off'} ${conflict(k, tg[k]) ? 'conf' : ''}" title="${esc((STATE.toggleHelp[k] || '') + (conflict(k, tg[k]) ? '\n⚠ 다른 체크된 프리셋과 값이 다름 — 나중에 체크한 프리셋이 이깁니다' : ''))}">${esc(k)}${tg[k] ? '' : ' ✕'}</span>`).join('');
    const kvChips = (o, help) => Object.keys(o).map((k) => `<span class="chip" title="${esc(help[k] || STATE.settingHelp[k] || '')}">${esc(k)} = ${esc(JSON.stringify(o[k]))}</span>`).join('') || '<span class="muted">-</span>';
    box.innerHTML = `<h4>${esc(name)} <span class="muted">(${Object.keys(tg).length} 토글 · ${Object.keys(tu).length} 튜닝 · ${Object.keys(st).length} 설정)</span></h4><p>${esc(d.desc || '')}</p>` +
      `<div class="sect">토글 — 초록 = ON, 빨강 ✕ = OFF, 노랑 = 다른 프리셋과 충돌 (마우스를 올리면 설명)</div><div>${chips || '<span class="muted">-</span>'}</div>` +
      `<div class="sect">튜닝 (tuning.json 값, 요청 단위 적용)</div><div>${kvChips(tu, STATE.tuningHelp || {})}</div>` +
      `<div class="sect">설정 (config 값, 요청 단위 적용)</div><div>${kvChips(st, STATE.tuningHelp || {})}</div>` +
      `<div class="muted" style="margin-top:6px">CLI: <code>--preset ${esc(name)}</code> · 영구 적용은 Settings › 프리셋 › 적용(저장) 또는 <code>preset apply ${esc(name)} --save</code></div>`;
    box.classList.remove('hidden');
  }
  // 요청 단위 오버라이드. 프로바이더·모델은 여기서 다루지 않는다 (2026-09-17) —
  // Settings › 모델 · 프로바이더 가 유일한 자리다. 두 곳에서 같은 값을 받으면 어느 쪽이 적용됐는지
  // 알 수 없고, 사이드바 값은 저장되지 않아 "바꿨는데 왜 안 남지?" 가 된다.
  // ---------------- 요청 단위 튜닝·config 값 오버라이드 (2026-09-18, IMPLEMENTATION_PLAN_0918_2 §2.6 Pipeline 페이지 '이번 요청에만') ----------------
  // TUNING_OV: tuning.json 항목(source=tuning) → overrides.tuning = {키: 값}. 서버 request_scope 가 T.set 으로 검증·적용하고 파일에는 남지 않는다.
  // SETTING_OV: config.json 항목(source=config, top_k_final·rrf_k 등) → 평면 overrides[키] (server.py OVERRIDE_SAFE_KEYS 가 허용하는 것만 통과).
  // 둘 다 '이번 요청에만' 이므로 프로파일에는 저장하지 않는다. 토글 체크박스처럼 상태는 하나 — Ask/재실행/평가가 overrides() 로 함께 보낸다.
  const TUNING_OV = {}, SETTING_OV = {};
  function tuningOverrides() { return { tuning: Object.assign({}, TUNING_OV), settings: Object.assign({}, SETTING_OV) }; }
  function setTuningOverride(key, value, source) {
    if (!key) return;
    const box = source === 'config' ? SETTING_OV : TUNING_OV;
    if (value === undefined || value === null || value === '') delete box[key]; else box[key] = value;
    updateCli(); if (loaders._toggleChanged) loaders._toggleChanged();
  }
  function clearTuningOverrides() {
    Object.keys(TUNING_OV).forEach((k) => delete TUNING_OV[k]); Object.keys(SETTING_OV).forEach((k) => delete SETTING_OV[k]);
    updateCli(); if (loaders._toggleChanged) loaders._toggleChanged();
  }
  function overrides() {
    const ov = {};
    $$('[data-toggle]').forEach((cb) => { ov[cb.dataset.toggle] = cb.checked; });
    const dEl = $('#ov-debug'), dbg = dEl ? dEl.value : '';
    if (dbg !== '') ov.debug_level = parseInt(dbg, 10);
    // Ask 의 출력 드롭다운 (#ov-output): 비어 있으면 config.json output_mode(answer) 를 따른다
    const oEl = $('#ov-output'); if (oEl && oEl.value) ov.output_mode = oEl.value;
    Object.keys(SETTING_OV).forEach((k) => { ov[k] = SETTING_OV[k]; });
    if (Object.keys(TUNING_OV).length) ov.tuning = Object.assign({}, TUNING_OV);
    return ov;
  }
  function cliEquiv(cmd, q) {
    const base = STATE.settings ? STATE.settings.toggles : {};
    const parts = ['python -m llmwiki', cmd];
    if (q) parts.push('"' + q.replace(/"/g, '\\"') + '"');
    const ov = overrides();
    for (const k in ov) {
      if (STATE.toggleNames.includes(k)) { if (!!ov[k] !== !!base[k] && !(k in PRESET.set && PRESET.set[k] === !!ov[k])) parts.push((ov[k] ? '--' : '--no-') + k.replace(/_/g, '-')); }
      else if (k === 'llm_provider') parts.push('--llm ' + ov[k]);
      else if (k === 'embed_provider') parts.push('--embed-provider ' + ov[k]);
      else if (k === 'top_k_final') parts.push('--k ' + ov[k]);
      else if (k === 'debug_level') parts.push('--debug ' + ov[k]);
      else if (k === 'output_mode') parts.push('--output ' + ov[k]);
      else if (k === 'tuning') parts.push('--tuning ' + Object.keys(ov[k]).map((a) => a + '=' + ov[k][a]).join(','));   // 요청 단위 튜닝 오버레이 (server._cli_equiv 와 같은 표기)
      else parts.push('--' + k.replace(/_/g, '-') + ' ' + ov[k]);
    }
    const pn = presetNames(); if (pn.length) parts.push('--preset ' + pn.join(','));
    const mode = $('#q-mode') && $('#q-mode').value; if (cmd === 'query' && mode) parts.push(mode === 'deep' ? '--preset deep_research' : '--preset speed');
    return parts.join(' ') + ' --trace';
  }
  function updateCli() {
    const ab = $('.tabs button.active'); const tab = ab ? ab.dataset.tab : 'query';
    const cmd = tab === 'build' ? 'build' : tab === 'eval' ? 'eval' : tab === 'trials' ? 'trial run' : 'query';
    $('#cli-equiv').textContent = cliEquiv(cmd, cmd === 'query' ? (($('#q') && $('#q').value) || '<질문>') : '');
    refreshSidebarSummary();    // 토글·튜닝 오버라이드가 바뀌는 모든 길이 여기를 지난다 → 사이드바 요약도 같이
  }
  function setTogglesFrom(t) { PRESET.manual = {}; $$('[data-toggle]').forEach((cb) => { cb.checked = !!t[cb.dataset.toggle]; }); if (presetNames().length || modePreset()) applyPresets(); else { markPresetToggles(); updateCli(); } }

  // ---------------- status ----------------
  // ---------------- 서버 설정이 바뀌었다는 한 줄 방송 (2026-09-19) ----------------
  // Settings(모델·프로바이더 / config / 튜닝 / 프리셋) 와 🧭 Pipeline 은 **같은 서버 값**을 다른 화면으로 보여 준다.
  // 한쪽에서 저장하면 다른 쪽이 캐시(PL.arch · TUN.rows · MODELS)를 들고 있다가 옛 값을 계속 보여 주던 문제가 있었다.
  // 저장·초기화·재로드 뒤에 이 함수를 부르면 (1) STATE.settings 를 서버에서 다시 읽고 (2) 각 화면이 등록한
  // 무효화 훅을 부르고 (3) 지금 보고 있는 탭을 다시 그린다. 화면끼리 서로를 직접 알 필요가 없다.
  const RELOADERS = [];
  function onSettingsChanged(fn) { RELOADERS.push(fn); }
  async function settingsChanged(what) {
    try { await loadStatus(); } catch (e) { console.error('settingsChanged/status', e); }
    RELOADERS.forEach((fn) => { try { fn(what || ''); } catch (e) { console.error('settingsChanged', e); } });
    // 지금 열려 있는 탭만 다시 그린다 (숨은 탭은 열릴 때 loader 가 돈다)
    const ab = document.querySelector('.tabs:not(.hidden) button.active') || document.querySelector('.tabs button.active');
    const key = ab && ab.dataset.tab;
    if (key && loaders[key]) { try { loaders[key](); } catch (e) { console.error('settingsChanged/loader', key, e); } }
  }
  async function loadStatus() {
    const j = await api('/api/status');
    // 401(로그인 이동)·네트워크 오류·점검 모드에서는 여기서 멈춘다. 예전에는 오류 객체로 계속 렌더하다 TypeError 가 나서
    // 사이드바(buildSidebar)가 영영 만들어지지 않았고, 그게 "로그인 갔다 뒤로 오면 메뉴가 깨진다"의 실제 원인이었다.
    if (!j || j.error || !j.providers || !j.stats) {
      const ub = $('#user-badge');
      if (ub && j && (j.unauthorized || j.busy)) ub.innerHTML = `<span class="warntxt">${esc(j.error || '접근할 수 없음')}</span> <a href="/login">로그인 →</a>`;
      if (j && j.busy) { const ab = $('#alert-badge'); if (ab) { ab.classList.remove('hidden'); ab.classList.add('alert'); ab.textContent = '⚠ ' + (j.error || '서버 혼잡'); } }
      return j || { error: 'no status' };
    }
    STATE.status = j; STATE.settings = j.settings; STATE.toggleNames = j.toggle_names; STATE.toggleHelp = j.toggle_help || {}; STATE.settingHelp = j.setting_help || {}; STATE.providers = j.providers; STATE.roles = j.roles || []; STATE.toggleGroups = j.toggle_groups || []; STATE.presets = j.presets || [];
    const p = j.providers, s = j.stats;
    const ra = p.roles && p.roles.answer, rr = p.roles && p.roles.rerank;
    $('#provider-badge').textContent = `answer: ${ra ? ra.name + '/' + ra.model + (ra.available ? '' : ' (unavailable)') : p.llm.name} · rerank: ${p.rerank && p.rerank.url ? 'api/' + p.rerank.model : (rr ? rr.name + '/' + rr.model : '-')} · embed: ${p.embedder.name}${p.embedder.dim ? ' d=' + p.embedder.dim : ''}`;
    $('#stats-badge').textContent = `docs ${s.docs} · chunks ${s.chunks} · vec ${s.embeddings} · entities ${s.entities} · rels ${s.relations} · requests ${s.requests} · pending ${s.proposals_pending}`;
    const w = j.watcher || {};
    $('#watch-badge').textContent = `auto-build: ${w.enabled ? 'on (' + w.interval + 's)' : 'off'}${w.last_scan ? ' · last scan ' + ts(w.last_scan) : ''}`;
    const sv = j.server || {};
    const sb = $('#server-badge');
    if (sb) {
      const busy = (sv.running || 0) + (sv.queued || 0);
      sb.className = 'badge' + (sv.maintenance ? ' alert' : busy > 0 ? ' busy' : '');
      sb.textContent = `서버: 실행 ${sv.running || 0}${sv.queued ? ' · 대기 ' + sv.queued : ''}${sv.lock && sv.lock.writer ? ' · ' + sv.lock.writer : ''}${sv.maintenance ? ' · 점검 중' : ''}`;
      sb.title = `동시 실행 상한 ${sv.max_parallel_reads} · 자세히: Observability › 서버 모니터` + (sv.scheduler ? `\n스케줄: ${sv.scheduler.tasks}개(활성 ${sv.scheduler.enabled})${sv.scheduler.next ? ' 다음 ' + sv.scheduler.next.name + ' ' + dt(sv.scheduler.next.at) : ''}` : '');
      sb.onclick = () => { switchGroup('observability'); switchTab('server'); };
    }
    STATE.auth = j.auth || null;
    const ub = $('#user-badge');
    if (ub) {
      const a = j.auth || {}, u = a.user || {};
      if (a.mode === 'off') { ub.innerHTML = `<span title="security.json mode=off/auto(loopback): 로그인 없음. 파괴적 작업은 확인 문구만 요구">🔓 로그인 없음 (local admin)</span>`; }
      else if (u.via === 'anon') { ub.innerHTML = `<span title="로그인하지 않은 접속자 — security.json anonymous_role(${esc(u.role || '')}) 권한. 상위 작업은 로그인 필요">👥 게스트 <small>(${esc(u.role || '')})</small></span> <a href="#" id="btn-login" title="로그인">로그인 →</a>`; const li = $('#btn-login'); if (li) li.onclick = (e) => { e.preventDefault(); gotoLogin(); }; }
      else { ub.innerHTML = `<span title="via ${esc(u.via || '')} · 역할 순서 ${esc((a.roles || []).join(' < '))}">👤 ${esc(u.name || '?')} <small>(${esc(u.role || '')})</small></span> <a href="#" id="btn-logout" title="로그아웃">⎋</a>`; const lo = $('#btn-logout'); if (lo) lo.onclick = async (e) => { e.preventDefault(); await api('/api/auth/logout', {}); STATE.leaving = true; location.replace('/login'); }; }
      document.body.dataset.role = u.role || 'admin';
      // 권한 미리보기 중이면 눈에 띄게 알린다 — 원래 권한으로 돌아오는 법도 함께
      const pv = u.preview_of || '';
      const sel = $('#preview-role'); if (sel) sel.value = pv ? (u.role || '') : '';
      document.body.classList.toggle('previewing', !!pv);
      let pb = $('#preview-bar');
      if (pv) {
        if (!pb) { pb = document.createElement('div'); pb.id = 'preview-bar'; document.body.insertBefore(pb, document.body.firstChild); }
        pb.innerHTML = `👁 <b>${esc(u.role || '')}</b> 권한으로 보는 중 — 원래 권한은 <b>${esc(pv)}</b> 입니다.`
          + ' <button class="mini" id="btn-preview-off">원래 권한으로</button>';
        $('#btn-preview-off').onclick = async () => { await api('/api/auth/preview', { role: '' }); location.reload(); };
      } else if (pb) { pb.remove(); }
    }
    const al = j.alerts || [];
    const ab = $('#alert-badge'); ab.classList.toggle('hidden', !al.length); ab.classList.toggle('alert', !!al.length); ab.textContent = al.length ? `⚠ alerts ${al.length}` : ''; ab.title = al.map((a) => `[${a.level}] ${a.check}: ${a.detail}`).join('\n'); ab.onclick = () => { switchGroup('corpus'); switchTab('build'); };
    if ($('#corpus-dirs')) $('#corpus-dirs').textContent = (j.settings.corpus_dirs || []).join('  |  ');
    if ($('#config-json') && !$('#config-json').value) $('#config-json').value = JSON.stringify(j.settings, null, 2);
    // 사이드바는 (a) 처음 · (b) 서버의 토글/프리셋 목록이 바뀜 · (c) 비어 있음(이전 렌더 실패/복원) 일 때만 다시 만든다.
    // 예전의 body.dataset.togglesInit 가드는 한 번 실패하면 영영 복구되지 않아 메뉴가 빈 채로 남았다.
    const box = $('#toggle-groups');
    const sig = (j.toggle_names || []).join(',') + '|' + (j.presets || []).join(',');
    if (box && (!box.children.length || document.body.dataset.togglesSig !== sig)) {
      buildSidebar(j); setTogglesFrom(j.settings.toggles); document.body.dataset.togglesSig = sig; document.body.dataset.togglesInit = '1';
      const tp = $('#tr-preset'); if (tp) tp.innerHTML = '<option value="">(프리셋 없음)</option>' + (j.presets || []).map((x) => `<option>${esc(x)}</option>`).join('');
    }
    // 사이드바의 모델 datalist 는 없앴다 (프로바이더/모델 오버라이드와 함께) — Settings › 모델 · 프로바이더 가 유일한 자리다.
    return j;
  }

  // ---------------- trace renderer ----------------
  /** 앙상블 상세 — **멤버 하나하나와 취합 LLM 을 표로**.
   *
   * 예전에는 `model` 이 `llama3.1+llama3.1+llama3.1` 처럼 `+` 로 이어 붙은 것을 보고 사람이
   * 유추해야 했다. "앙상블로 돌았나 · 몇이 성공했나 · 누가 느렸나 · 취합에 얼마나 더 썼나" 는
   * meta JSON 을 펼쳐 읽어야만 알 수 있었다 (2026-09-20).
   *
   * 멤버는 **병렬**로 도므로 합이 단계 시간이 아니다 — 그래서 막대를 '가장 느린 멤버' 기준으로 그리고
   * 그 사실을 적어 둔다. 순차로 보이면 "3배 걸렸다" 로 잘못 읽는다.
   */
  function ensembleBlock(e) {
    if (!e || !e.members || !e.members.length) return '';
    const base = Math.max(1, e.member_ms_max || 0, (e.aggregator || {}).ms || 0, (e.fallback || {}).ms || 0);
    const row = (label, model, prov, ms, itok, otok, ok, err, kind) =>
      `<tr class="${ok === false ? 'ens-bad' : ''}">`
      + `<td class="small">${esc(label)}</td>`
      + `<td class="small mono">${esc(model || '-')}${prov ? `<span class="muted"> · ${esc(prov)}</span>` : ''}</td>`
      + `<td class="ens-barcell"><i class="ens-bar ${kind}" style="width:${Math.max(2, Math.round(100 * (ms || 0) / base))}%"></i></td>`
      + `<td class="num small">${fmt(ms || 0)} ms</td>`
      + `<td class="num small muted">${fmtK((itok || 0) + (otok || 0))} tok</td>`
      + `<td class="small">${ok === false ? `<span class="errtxt" title="${esc(err || '')}">실패</span>` : ''}</td></tr>`;
    // 폴백으로 답이 나왔는데 화면이 그대로면 "앙상블 답" 으로 오해한다 — 가장 먼저 눈에 띄게 알린다.
    const fb = e.fallback;
    let h = `<div class="mb ens"><b>앙상블</b>${e.role ? ` <span class="muted">역할 ${esc(e.role)}</span>` : ''} `
      + `<span class="pill ${e.n_ok === e.n_members ? 'ok' : 'warn'}">멤버 ${e.n_ok}/${e.n_members} 성공</span> `
      + (fb ? `<span class="pill bad" title="멤버가 min_results 를 못 채워 역할 모델이 대신 답했습니다 (${esc(fb.why || '')}). 이 답은 앙상블 결과가 아닙니다.">폴백 · 역할 모델이 답함</span> ` : '')
      + (e.aggregated ? '<span class="pill">취합 1회</span>' : '<span class="pill warn" title="성공 멤버가 1개뿐이면 취합 없이 그 답을 그대로 씁니다">취합 없음</span>')
      + (e.policy && e.policy.wait ? ` <span class="muted small">대기 ${esc(e.policy.wait)}</span>` : '')
      + '<div class="tbl-wrap"><table class="ens-tbl"><tr><th></th><th>모델</th>'
      + '<th title="멤버는 병렬로 돕니다 — 막대는 가장 느린 것 기준입니다">시간</th><th>ms</th><th>토큰</th><th></th></tr>';
    e.members.forEach((m, i) => { h += row('멤버 ' + (i + 1), m.model, m.provider, m.ms, m.input_tokens, m.output_tokens, m.ok, m.error, 'mem'); });
    if (e.aggregator) h += row('취합', e.aggregator.model, e.aggregator.provider, e.aggregator.ms, e.aggregator.input_tokens, e.aggregator.output_tokens, true, '', 'agg');
    if (fb) h += row('폴백', fb.model, fb.provider, fb.ms, fb.input_tokens, fb.output_tokens, true, '', 'fb');
    h += '</table></div><div class="muted small">멤버는 <b>동시에</b> 실행됩니다 — 단계 시간 ≈ 가장 느린 멤버 + 취합.</div>'
      + (fb ? `<div class="ens-fb-note">이 답은 <b>${esc(fb.model || '')}</b>(역할 모델)이 만들었습니다 — ${esc(fb.why || '')}. `
             + (fb.mode === 'merge' ? `성공한 멤버 ${fb.used_members}개의 답을 <b>취합</b>했습니다.` : '멤버 답을 쓰지 않고 <b>원래 프롬프트로 다시</b> 돌렸습니다.')
             + ` <span class="muted">(설정: <code>ensemble.fallback_mode=${esc(fb.mode || '')}</code>)</span></div>` : '')
      + '</div>';
    return h;
  }

  function metaBlock(n) {
    const parts = [];
    const meta = Object.assign({}, n.meta || {}); delete meta.reason;
    const ens = meta.ensemble; delete meta.ensemble;      // 표로 따로 그린다 (JSON 으로 두면 안 읽힌다)
    if (ens) parts.push(ensembleBlock(ens));
    if (Object.keys(meta).length) parts.push(`<div class="mb"><b>meta</b><pre>${esc(JSON.stringify(meta, null, 1))}</pre></div>`);
    if (n.counters && Object.keys(n.counters).length) parts.push(`<div class="mb"><b>counters</b> ${esc(JSON.stringify(n.counters))}</div>`);
    if (n.debug && Object.keys(n.debug).length) parts.push(`<div class="mb dbg"><b>debug</b><pre>${esc(JSON.stringify(n.debug, null, 1))}</pre></div>`);
    if (n.samples && Object.keys(n.samples).length) parts.push(`<div class="mb smp"><b>samples (debug 2)</b>` + Object.keys(n.samples).map((k) => `<div><i>${esc(k)}</i><pre>${esc(String(n.samples[k]))}</pre></div>`).join('') + '</div>');
    if (n.logs && n.logs.length) parts.push(`<div class="mb"><b>logs</b><pre>${esc(n.logs.join('\n'))}</pre></div>`);
    if (n.error) parts.push(`<div class="mb err"><b>error</b><pre>${esc(n.error)}</pre></div>`);
    return parts.join('') || '<span class="muted">(no details)</span>';
  }
  // 단계 트리 툴바: 모두 펼치기 / 모두 접기 / 복사 (어느 화면에서든 같은 조작)
  function traceToolbar(el, trace) {
    const bar = document.createElement('div');
    bar.className = 'row tr-tools';
    bar.innerHTML = '<button class="mini secondary" data-tr="open">▼ 모든 단계 펼치기</button>' +
      '<button class="mini secondary" data-tr="close">▲ 모두 접기</button>' +
      '<button class="mini secondary" data-tr="copy">📋 trace 복사</button>' +
      '<span class="muted small">단계 이름을 클릭하면 그 단계만 펼쳐집니다</span>';
    el.insertBefore(bar, el.firstChild);
    $('[data-tr="open"]', bar).onclick = () => $$('.tr-row', el).forEach((r) => r.classList.add('open'));
    $('[data-tr="close"]', bar).onclick = () => $$('.tr-row', el).forEach((r) => r.classList.remove('open'));
    $('[data-tr="copy"]', bar).onclick = () => copyText(JSON.stringify(trace, null, 1), 'trace JSON');
  }
  // 단계 하나를 복사용 JSON 으로 — 이름·시간·meta·counters·debug·samples·logs·error 만 (children 은 뺀다: 그 줄 하나가 대상이다)
  function stageJson(n) {
    const o = {};
    ['name', 'ms', 'self_ms', 'offset_ms', 'enabled', 'replayed', 'error'].forEach((k) => { if (n[k] !== undefined && n[k] !== null) o[k] = n[k]; });
    ['meta', 'counters', 'debug', 'samples'].forEach((k) => { if (n[k] && Object.keys(n[k]).length) o[k] = n[k]; });
    if (n.logs && n.logs.length) o.logs = n.logs;
    return JSON.stringify(o, null, 1);
  }
  // 단계 요약을 마크다운 표로 — Ask '전체 복사' 와 리포트 붙여넣기용
  function traceMarkdown(trace) {
    if (!trace || !trace.ms) return '(단계 기록 없음)';
    const md = (s) => String(s == null ? '' : s).replace(/\|/g, '\\|').replace(/\s+/g, ' ');
    const total = trace.ms || 1, sm = trace.summary;
    const L = [];
    if (sm) L.push(`총 ${fmt(sm.total_ms)} ms · LLM 호출 ${sm.llm.calls} · 토큰 ${fmtK(sm.llm.total_tokens)} (in ${fmtK(sm.llm.input_tokens)} / out ${fmtK(sm.llm.output_tokens)}) · SQL ${sm.sql_statements} · debug ${trace.debug_level} · run ${trace.run_id || '-'}`, '');
    L.push('| 단계 | ms | % | LLM | tokens in/out | 요약 |', '|---|---:|---:|---:|---|---|');
    flatten(trace).filter((n) => n.depth > 0).forEach((n) => {
      const c = n.counters || {}, skip = n.enabled === false;
      const meta = Object.assign({}, n.meta || {}); delete meta.reason; const ms = JSON.stringify(meta);
      L.push(`| ${'　'.repeat(Math.max(0, n.depth - 1))}${md(n.name)}${skip ? ' (' + md((n.meta || {}).reason || 'off') + ')' : ''}${n.replayed ? ' (재생)' : ''} | ${skip ? '—' : fmt(n.ms)} | ${skip ? '' : fmt(100 * n.ms / total, 1)} | ${c.llm_calls || ''} | ${c.llm_calls ? fmtK(c.llm_input_tokens || 0) + '/' + fmtK(c.llm_output_tokens || 0) : ''} | ${md(ms.slice(0, 160))}${ms.length > 160 ? '…' : ''} |`);
    });
    return L.join('\n');
  }
  // ---------------- 단계 재실행 (docs/RERUN.md) ----------------
  // 재시작점 표는 서버가 준다 (`GET /api/rerun`). 화면에 박아 두면 파이프라인이 바뀔 때 어긋난다.
  const RERUN = { points: [], stage_point: {}, loaded: false };
  async function loadRerunPoints() {
    if (RERUN.loaded) return RERUN;
    const j = await api('/api/rerun');
    if (j && !j.error) { RERUN.points = j.points || []; RERUN.stage_point = j.stage_point || {}; RERUN.capture_on = !!j.capture_on; }
    RERUN.loaded = true;
    return RERUN;
  }
  function rerunLabel(id) { return ((RERUN.points || []).find((p) => p.id === id) || {}).label || id; }
  // ---------------- 단계별 시간 제한 (llmwiki/architecture.py stage_limits) ----------------
  // 실측 ms 만 보면 그게 여유 있는 값인지 제한에 거의 닿은 값인지 알 수 없다. 그래서 trace 의 ms 옆과
  // 🧭 Pipeline 단계 카드에 **그 단계를 끊을 수 있는 제한**을 괄호로 같이 보여 준다. 정의는 서버 한 곳뿐이다.
  const LIMITS = { trace: {}, stages: {}, flows: {}, roles: {}, loaded: false };
  async function loadLimits() {
    if (LIMITS.loaded) return LIMITS;
    const j = await api('/api/limits');
    if (j && !j.error) Object.assign(LIMITS, j);
    LIMITS.loaded = true;
    return LIMITS;
  }
  // 초 → 사람이 읽는 값. 0 이하는 '제한 없음'.
  function fmtLimit(s) {
    const v = Number(s) || 0;
    if (v <= 0) return '∞';
    if (v >= 86400) return (v / 86400 === Math.round(v / 86400) ? (v / 86400) : (v / 86400).toFixed(1)) + 'd';
    if (v >= 3600) return (v / 3600 === Math.round(v / 3600) ? (v / 3600) : (v / 3600).toFixed(1)) + 'h';
    if (v >= 60) return (v / 60 === Math.round(v / 60) ? (v / 60) : (v / 60).toFixed(1)) + 'm';
    return v + 's';
  }
  // 단계 이름 → 대표 제한(첫 번째)과 전체 목록. trace 노드 이름이 그대로 키다.
  function limitFor(name) { const rows = (LIMITS.trace || {})[name]; return rows && rows.length ? rows : null; }
  function limitTitle(rows) {
    return rows.map((x) => '· ' + x.label + ': ' + fmtLimit(x.s) + '  [' + x.file + ' ' + x.key + ']' + (x.note ? '\n    ' + x.note : '')).join('\n');
  }
  // 실측 ms 가 제한의 몇 %인가 — 80% 넘으면 경고색, 넘겼으면 위험색.
  function limitPct(ms, s) { const v = Number(s) || 0; return v > 0 ? (Number(ms) || 0) / (v * 1000) : 0; }
  // trace 한 줄의 ms 밑에 붙는 '(≤ 제한)' 칸. 제한을 모르면 아무것도 붙이지 않는다.
  function limCell(n, skip) {
    if (skip) return '';
    const rows = limitFor(n.name);
    if (!rows) return '';
    const top = rows[0], p = limitPct(n.ms, top.s);
    const cls = p >= 1 ? ' over' : p >= 0.8 ? ' near' : '';
    return `<small class="tr-lim${cls}" title="이 단계를 끊을 수 있는 시간 제한\n${esc(limitTitle(rows))}">≤ ${esc(fmtLimit(top.s))}</small>`;
  }
  // 실제 실행. 설정을 따로 주지 않으면 **왼쪽 사이드바의 지금 설정**(토글·오버라이드·프리셋)으로 돈다 —
  // 보통 질의와 같은 길이라 "사이드바에서 값을 바꾸고 ⟲ 를 누른다" 가 그대로 통한다.
  async function runRerun(requestId, point, extra, onDone) {
    const body = Object.assign({ request_id: requestId, from: point }, extra || {});
    if (!body.overrides) body.overrides = overrides();
    if (body.preset === undefined) { const pr = presetNames(); if (pr && pr.length) body.preset = pr.join(','); }
    toast('⟲ ' + rerunLabel(point) + ' 다시 실행 중…');
    const j = await api('/api/query/rerun', body);
    if (!j || j.error) { toast('재실행 실패: ' + esc((j && j.error) || '')); return j; }
    const rep = (((j.trace || {}).children) || []).filter((c) => c.replayed).length;
    toast('재실행 완료 — ' + rerunLabel(point) + (rep ? ' (앞 ' + rep + '단계 재생)' : ''));
    if (onDone) onDone(j);
    return j;
  }
  function openRerun(requestId, point, stage, onDone) {
    let ov = $('#rerun-modal'); if (ov) ov.remove();
    const pinfo = (RERUN.points || []).find((p) => p.id === point) || {};
    ov = document.createElement('div'); ov.id = 'rerun-modal'; ov.className = 'modal-bg';
    ov.innerHTML = `<div class="modal">
      <h3>⟲ <b>${esc(rerunLabel(point))}</b> 다시 실행</h3>
      <div class="modal-op">요청 <code>#${esc(String(requestId))}</code> · 누른 단계 <code>${esc(stage || point)}</code></div>
      <p class="modal-msg">${esc(pinfo.note || '')} 이 지점 <b>앞</b>의 단계는 저장해 둔 결과를 그대로 재생하고, <b>뒤</b>의 단계만 지금 설정으로 다시 계산합니다.</p>
      <label>재시작점 <select id="rr-point">${(RERUN.points || []).map((p) => `<option value="${esc(p.id)}" ${p.id === point ? 'selected' : ''}>${esc(p.label)}</option>`).join('')}</select></label>
      <label>이번 실행에만 적용할 설정 <small class="muted">(JSON · 평면. 예 <code>{"top_k_final": 12, "claim_check": false}</code>)</small>
        <textarea id="rr-ov" spellcheck="false" style="min-height:70px" placeholder="{}"></textarea></label>
      <label>프리셋 <input id="rr-preset" type="text" placeholder="(비움) 예: speed 또는 deep_research"></label>
      <div id="rr-msg" class="muted small"></div>
      <div class="modal-actions"><button class="secondary" id="rr-cancel">취소</button><button id="rr-go">실행</button></div>
      <div class="muted small">설정을 바꿔 가며 눌러 보세요. 결과는 새 요청으로 기록되며, 거기서 또 이어서 재실행할 수 있습니다.</div>
    </div>`;
    document.body.appendChild(ov);
    const close = () => ov.remove();
    $('#rr-cancel').onclick = close;
    ov.addEventListener('keydown', (e) => { if (e.key === 'Escape') close(); });
    $('#rr-go').onclick = async () => {
      let o = null;
      const raw = ($('#rr-ov').value || '').trim();
      if (raw) { try { o = JSON.parse(raw); } catch (e) { $('#rr-msg').innerHTML = '<span class="bad">설정 JSON 을 읽지 못했습니다: ' + esc(e.message) + '</span>'; return; } }
      $('#rr-go').disabled = true; $('#rr-msg').textContent = '실행 중…';
      // 칸을 비워 두면 사이드바의 지금 설정으로 (runRerun 이 채운다)
      const extra = {};
      if (o) extra.overrides = o;
      const pr = ($('#rr-preset').value || '').trim(); if (pr) extra.preset = pr;
      const j = await runRerun(requestId, $('#rr-point').value, extra, null);
      $('#rr-go').disabled = false;
      if (!j || j.error) { $('#rr-msg').innerHTML = '<span class="bad">' + esc((j && j.error) || '실패') + '</span>'; return; }
      close();
      if (onDone) onDone(j);
    };
  }
  function renderTrace(el, trace, opts) {
    opts = opts || {};
    if (!trace) { el.innerHTML = ''; return; }
    const rerunId = opts.rerun || null;      // 원 요청 id. 주면 각 단계에 ⟲ 가 붙는다
    if (rerunId && !RERUN.loaded) loadRerunPoints().then(() => renderTrace(el, trace, opts));
    // 제한 표가 아직 없으면 한 번 받아 오고 다시 그린다 (없어도 실측은 그대로 보인다).
    if (!LIMITS.loaded) loadLimits().then(() => renderTrace(el, trace, opts));
    const total = trace.ms || 1;
    const rows = [];
    (function walk(n, depth) { rows.push({ n, depth }); (n.children || []).forEach((c) => walk(c, depth + 1)); })(trace, 0);
    const sm = trace.summary;
    let head = '';
    if (sm) head = `<div class="tr-summary">총 <b>${fmt(sm.total_ms)} ms</b> · LLM 호출 <b>${sm.llm.calls}</b> · 토큰 <b>${fmtK(sm.llm.total_tokens)}</b> (in ${fmtK(sm.llm.input_tokens)} / out ${fmtK(sm.llm.output_tokens)}) · SQL <b>${sm.sql_statements}</b> · debug ${trace.debug_level} · run ${esc(trace.run_id || '-')} · 느린 단계: ${sm.slowest.slice(0, 3).map((x) => esc(x.name) + ' ' + x.pct + '%').join(', ')}${sm.errors.length ? ' · <span class="errtxt">오류 ' + sm.errors.length + '</span>' : ''}</div>`;
    el.innerHTML = head + '<div class="trace">' + rows.map((r, i) => {
      const n = r.n, skip = n.enabled === false;
      const off = n.offset_ms || 0;
      const left = Math.min(100, (off / total) * 100), w = Math.max(0.4, (n.ms / total) * 100);
      const color = STAGE_COLOR[n.name] || (r.depth === 0 ? '#898781' : '#2a78d6');
      const pct = r.depth === 1 && !skip ? ` <small class="muted">${fmt(100 * n.ms / total, 0)}%</small>` : '';
      const c = n.counters || {};
      // 앙상블 표시는 **글씨만** 둔다 (2026-09-20 요청). 테두리·배경이 있는 알약이면 막대와 같은 줄에서
      // 타임라인 위의 조각처럼 읽혀 "단계가 이어지지 않는다" 로 보였다. 정보는 그대로 두고 모양만 뺀다.
      const em = (n.meta || {}).ensemble;
      const ensB = em ? `<span class="ens-txt ${em.n_ok === em.n_members ? '' : 'warn'}" title="앙상블${em.role ? ' (역할 ' + esc(em.role) + ')' : ''}: 멤버 ${em.n_members}개를 동시에 부르고${em.aggregated ? ' 취합 LLM 이 1회 더 합칩니다' : ' (성공이 1개뿐이라 취합 없이 그대로 씁니다)'}. 펼치면 멤버별 모델·시간·토큰이 보입니다">앙상블 ${em.n_ok}/${em.n_members}${em.aggregated ? '+취합' : ''}</span>` : '';
      // 배지는 **한 덩어리로 묶어서** 오른쪽에 붙인다 (2026-09-20).
      //   예전에는 배지마다 `margin-left:auto` 가 걸려 있었는데, flex 에서 auto 여백이 여러 개면
      //   남는 공간이 그 사이에 **나눠진다**. 그래서 배지가 2개인 줄만 첫 배지가 timeline 과 아무 상관 없는
      //   가운데 자리로 밀려났고, 파란 앙상블 배지가 막대처럼 보여 "단계가 안 이어진다" 로 읽혔다.
      const badgeItems = ensB + (c.llm_calls ? `<span class="cnt llm" title="LLM 호출/토큰">llm ${c.llm_calls} · ${fmtK((c.llm_input_tokens || 0) + (c.llm_output_tokens || 0))} tok</span>` : '') + (c.sql ? `<span class="cnt" title="SQL 문 수">sql ${c.sql}</span>` : '');
      const badges = badgeItems ? `<span class="tr-badges">${badgeItems}</span>` : '';
      // 단계 재실행(⟲): 이 단계부터 저장된 중간 결과로 다시 돌린다 (docs/RERUN.md).
      // 재시작점이 없는 단계(sync_index 처럼 되돌릴 의미가 없는 것)에는 달지 않는다.
      const pt = rerunId ? (RERUN.stage_point || {})[n.name] : null;
      const rr = pt ? `<button class="tr-rerun" data-rerun="${esc(pt)}" data-stage="${esc(n.name)}" title="이 단계부터 다시 실행 — 지금 사이드바 설정으로 바로 실행합니다 (앞 단계는 저장된 결과를 재생).&#10;Shift 또는 Alt 를 누른 채 클릭하면 이번 실행에만 쓸 설정을 직접 적을 수 있습니다.">⟲</button>` : '';
      const rep = n.replayed ? ' <span class="pill replay" title="계산하지 않고 저장된 결과를 그대로 썼습니다">재생</span>' : '';
      // 단계 복사(⧉): 이 줄의 이름·ms·meta·debug 를 JSON 으로 (요청 16). ⟲ 와 같은 자리, 같은 흐림 규칙.
      const cp = `<button class="tr-copy" data-i="${i}" title="이 단계 복사 — 이름·ms·meta·counters·debug 를 JSON 으로 클립보드에">⧉</button>`;
      return `<div class="tr-row ${n.error ? 'has-err' : ''}${n.replayed ? ' replayed' : ''}"><div class="tr-name ${skip ? 'skip' : ''}" style="padding-left:${r.depth * 14}px" title="클릭: 상세">${rr}${cp}${esc(n.name)}${skip ? ' <small>(' + esc((n.meta || {}).reason || 'off') + ')</small>' : ''}${rep}${pct}</div>` +
        `<div class="tr-bar">${skip ? '' : `<i class="${n.error ? 'err' : ''}" style="left:${left}%;width:${w}%;background:${n.error ? '' : color}"></i>`}${badges}</div>` +
        `<div class="tr-ms">${skip ? '—' : fmt(n.ms) + ' ms'}${limCell(n, skip)}</div><div class="tr-meta">${metaBlock(n)}</div></div>`;
    }).join('') + '</div>';
    $$('.tr-name', el).forEach((d) => d.onclick = () => d.parentElement.classList.toggle('open'));
    $$('.tr-copy', el).forEach((b) => b.onclick = (e) => { e.stopPropagation(); const r = rows[parseInt(b.dataset.i, 10)]; if (r) copyText(stageJson(r.n), '단계 ' + r.n.name); });
    // 그냥 클릭 = 지금 설정으로 **바로 실행**. Shift/Alt + 클릭 = 이번만 쓸 설정을 적는 창.
    // (창을 항상 띄우면 "값 하나 바꾸고 눌러 본다" 는 흐름이 매번 한 단계 늘어난다.)
    $$('.tr-rerun', el).forEach((b) => b.onclick = (e) => {
      e.stopPropagation();
      if (e.shiftKey || e.altKey) openRerun(rerunId, b.dataset.rerun, b.dataset.stage, opts.onRerun);
      else runRerun(rerunId, b.dataset.rerun, null, opts.onRerun);
    });
    if (opts.openAll) $$('.tr-row', el).forEach((r) => r.classList.add('open'));
    if (opts.controls !== false) traceToolbar(el, trace);
  }
  function flatten(trace) { const out = []; (function walk(n, d) { out.push(Object.assign({ depth: d }, n)); (n.children || []).forEach((c) => walk(c, d + 1)); })(trace, 0); return out; }
  function renderStageTable(el, trace, other) {
    const total = trace.ms || 1, flat = flatten(trace);
    const om = {}; if (other) flatten(other).forEach((n) => { om[n.name + '@' + n.depth] = n; });
    if (!LIMITS.loaded) loadLimits().then(() => renderStageTable(el, trace, other));
    el.innerHTML = '<table class="stage-table"><tr><th>단계</th><th>ms</th><th title="이 단계를 끊을 수 있는 시간 제한 (config.json llm_roles / server.json timeouts)">제한</th><th>self</th><th>%</th><th>offset</th><th>SQL</th><th>LLM</th><th>tokens in/out</th>' + (other ? '<th>비교 ms</th><th>Δ</th>' : '') + '<th>요약</th></tr>' +
      flat.filter((n) => n.depth > 0).map((n) => {
        const c = n.counters || {}, skip = n.enabled === false; const o = om[n.name + '@' + n.depth];
        const meta = Object.assign({}, n.meta || {}); delete meta.reason; const ms = JSON.stringify(meta);
        const d = o && !skip ? n.ms - (o.ms || 0) : null;
        const lrows = limitFor(n.name), lp = lrows ? limitPct(n.ms, lrows[0].s) : 0;
        const lcell = (!lrows || skip) ? '' : `<span class="tr-lim${lp >= 1 ? ' over' : lp >= 0.8 ? ' near' : ''}" title="${esc(limitTitle(lrows))}">${esc(fmtLimit(lrows[0].s))}</span>`;
        return `<tr class="${skip ? 'skip' : ''}"><td style="padding-left:${8 + n.depth * 12}px">${esc(n.name)}${skip ? ' <small class="muted">(' + esc((n.meta || {}).reason || 'off') + ')</small>' : ''}</td><td class="num">${skip ? '—' : fmt(n.ms)}</td><td class="num">${lcell}</td><td class="num">${skip ? '' : fmt(n.self_ms)}</td><td class="num">${skip ? '' : fmt(100 * n.ms / total, 1)}</td><td class="num">${skip ? '' : fmt(n.offset_ms, 0)}</td><td class="num">${c.sql || ''}</td><td class="num">${c.llm_calls || ''}</td><td class="num">${c.llm_calls ? fmtK(c.llm_input_tokens || 0) + '/' + fmtK(c.llm_output_tokens || 0) : ''}</td>` +
          (other ? `<td class="num">${o ? fmt(o.ms) : '-'}</td><td class="num ${d > 0 ? 'worse' : d < 0 ? 'better' : ''}">${d == null ? '' : (d > 0 ? '+' : '') + fmt(d)}</td>` : '') + `<td class="muted small mono">${esc(ms.slice(0, 160))}${ms.length > 160 ? '…' : ''}</td></tr>`;
      }).join('') + '</table>';
  }
  // ---------------- live progress (progress.py 스냅샷 렌더) ----------------
  // live = { status, path_labels[], stage_label, detail, done, total, pct, elapsed_s, stage_elapsed_s, llm:{active,provider,model,elapsed_s,calls}, log[] }
  function fmtS(s) { s = Math.round(s || 0); return s >= 3600 ? Math.floor(s / 3600) + 'h ' + Math.floor((s % 3600) / 60) + 'm' : s >= 60 ? Math.floor(s / 60) + 'm ' + (s % 60) + 's' : s + 's'; }
  // 걸린 시간 표시. 1초 미만은 ms 로 — '0.0s' 로 뭉개면 "실행이 안 된 것 아니냐"는 오해를 준다
  // (답변 캐시 적중은 실제로 1ms 안팎이다).
  function fmtDur(s) {
    const v = Number(s);
    if (!isFinite(v) || v < 0) return '-';
    if (v === 0) return '0 ms';
    if (v < 1) return Math.max(1, Math.round(v * 1000)) + ' ms';
    if (v < 60) return v.toFixed(1) + 's';
    return fmtS(v);
  }
  // 실행 중인 작업 취소 (본인 요청 또는 admin). token = progress token = job id = 요청 관리자 티켓.
  async function cancelToken(token, reason) {
    if (!token) return { ok: false };
    const j = await api('/api/activity', { action: 'cancel', token: token, reason: reason || 'user' });
    toast(j && j.ok ? '중지 요청됨 — 현재 단계가 끝나는 대로 멈춥니다' : ('중지 실패: ' + ((j && j.error) || '')));
    return j;
  }
  function renderLive(el, live, extra, token) {
    if (!el) return;
    if (!live || !live.status) { el.classList.add('hidden'); el.innerHTML = ''; return; }
    el.classList.remove('hidden');
    const running = live.status === 'running';
    const path = (live.path_labels || []).join(' › ');
    const llm = live.llm || {};
    const pct = live.pct == null ? null : Math.max(0, Math.min(100, live.pct));
    const q = live.queue;
    const eta = live.eta_s ? ` · 남은 시간 ≈ ${fmtS(live.eta_s)}` : '';
    const bar = q
      ? `<div class="progress indet"><i></i><span>대기열 ${q.position}번째 (대기 ${q.waiting}건) — 앞 작업이 끝나면 시작합니다</span></div>`
      : pct == null
        ? (running ? `<div class="progress indet"><i></i><span>${esc(live.detail || '진행 중…')}</span></div>` : '')
        : `<div class="progress"><i style="width:${pct}%"></i><span>${live.done}/${live.total} · ${fmt(pct, 0)}%${eta}${live.detail ? ' · ' + esc(live.detail) : ''}</span></div>`;
    const llmTxt = llm.active ? `<span class="pill warn">LLM 응답 대기 ${esc(llm.provider || '')}/${esc(llm.model || '')} · ${fmtS(llm.elapsed_s)}</span>` : (llm.calls ? `<span class="pill">LLM 호출 ${llm.calls}회 · ${fmtS((llm.ms_total || 0) / 1000)}</span>` : '');
    const cancelBtn = (running && token) ? `<button class="mini danger live-cancel" title="이 작업을 중지합니다 (현재 단계가 끝나는 대로 멈춤)">${live.cancel_requested ? '중지 중…' : '■ 중지'}</button>` : '';
    const log = live.log || [];
    const copyBtn = log.length ? '<button class="mini secondary live-copy" title="이 진행 로그를 클립보드에 복사 (디버깅·문의용)">📋 로그 복사</button>' : '';
    const head = running
      ? `<span class="spin"></span><b>${esc(path || live.stage_label || live.label || '진행 중')}</b> <span class="muted small">단계 ${fmtS(live.stage_elapsed_s)} · 전체 ${fmtS(live.elapsed_s)}</span> ${llmTxt}${cancelBtn}${copyBtn}`
      : `<b>${live.status === 'done' ? '✔ 완료' : live.status === 'cancelled' ? '■ 중지됨' : '✖ ' + esc(live.status)}</b> <span class="muted small">${fmtS(live.elapsed_s)}${live.detail ? ' · ' + esc(live.detail) : ''}</span>${copyBtn}<button class="mini secondary live-close" title="이 패널 닫기">✕</button>`;
    // 로그: 기본은 최근 3줄, 클릭하면 누적 전체. 끝난 뒤에도 패널을 지우지 않는다 (완료 로그를 그대로 두고 복사할 수 있게 — 2026-09-15)
    const expanded = el.dataset.expanded === '1' || (!running && el.dataset.expanded !== '0');
    const shown = expanded ? log : log.slice(-3);
    const logHtml = log.length ? `<div class="live-log muted small ${expanded ? 'expanded' : ''}" title="클릭: ${expanded ? '접기' : '누적 로그 전체 보기'}">${esc(shown.join('\n'))}<span class="log-toggle">${expanded ? '▲ 접기' : (log.length > 3 ? `▼ 전체 보기 (${log.length}줄)` : '')}</span></div>` : '';
    el.innerHTML = `<div class="live-head">${head}${extra || ''}</div>${running || q ? bar : ''}${logHtml}`;
    el._log = log;
    const cb = $('.live-cancel', el);
    if (cb) cb.onclick = async () => { cb.disabled = true; cb.textContent = '중지 중…'; await cancelToken(token, 'web'); };
    const cp = $('.live-copy', el);
    if (cp) cp.onclick = () => copyText((live.label ? live.label + '\n' : '') + (el._log || []).join('\n'), '진행 로그 ' + (el._log || []).length + '줄');
    const cl = $('.live-close', el);
    if (cl) cl.onclick = () => { el.classList.add('hidden'); el.innerHTML = ''; };
    const lg = $('.live-log', el);
    if (lg) {
      lg.onclick = () => { el.dataset.expanded = expanded ? '0' : '1'; renderLive(el, live, extra, token); if (!expanded) { const n = $('.live-log', el); if (n) n.scrollTop = n.scrollHeight; } };
      if (expanded) lg.scrollTop = lg.scrollHeight;
    }
  }
  // 요청 단위 progress_token 폴링 (Ask 처럼 동기 API 를 기다리는 동안 사용). stop() 을 돌려준다.
  function watchProgress(token, el, interval) {
    let busy = false, stopped = false;
    const tick = async () => {
      if (busy || stopped) return; busy = true;
      try { const r = await fetch('/api/progress/' + token); const live = await r.json(); if (!stopped) renderLive(el, live.status === 'unknown' ? { status: 'running', label: '요청 접수 대기…' } : live, '', token); } catch (e) { /* 서버 재시작 등 — 조용히 */ }
      busy = false;
    };
    const t = setInterval(tick, interval || 500); tick();
    return () => { stopped = true; clearInterval(t); };
  }
  function liveElFor(logEl) {
    if (!logEl) return null;
    let el = logEl.previousElementSibling;
    if (!el || !el.classList.contains('live')) { el = document.createElement('div'); el.className = 'live hidden'; logEl.parentNode.insertBefore(el, logEl); }
    return el;
  }
  // 이 브라우저가 직접 폴링 중인 job 토큰 — '서버에서 진행 중' 목록에서 빼서 같은 작업이 두 번 보이지 않게 한다
  const MY_JOBS = new Set();
  async function pollJob(id, logEl, onDone, onTick) {
    const liveEl = liveElFor(logEl);
    MY_JOBS.add(id);
    let busy = false;   // 응답이 늦어도 폴링이 겹쳐 쌓이지 않게
    const t = setInterval(async () => {
      if (busy) return; busy = true;
      let j;
      try { j = await api('/api/jobs/' + id); } catch (e) { busy = false; return; }
      busy = false;
      if (!j || j.error === 'no such job') { clearInterval(t); renderLive(liveEl, null); if (logEl) logEl.textContent = 'job not found: ' + id; return; }
      if (logEl) logEl.textContent = (j.log || []).join('\n') + (j.status === 'running' ? '\n… (진행 중 — 위 진행 표시가 멈춰 있으면 LLM/임베딩 응답 대기 중입니다. elapsed ' + fmtS(j.elapsed_s) + ')' : '\n[' + j.status + ' · ' + fmtS(j.elapsed_s) + ']' + (j.error ? '\n' + j.error : ''));
      renderLive(liveEl, j.live && j.live.status ? j.live : { status: j.status, label: j.kind, elapsed_s: j.elapsed_s }, '', id);
      if (onTick) onTick(j);
      // 끝나도 진행 패널을 지우지 않는다 — 완료/실패/중지 상태와 전체 로그를 남겨 두고 사용자가 직접 닫거나 복사한다
      if (j.status !== 'running') { clearInterval(t); MY_JOBS.delete(id); onDone(j); loadStatus(); }
    }, 700);
  }

  // ---------------- navigation ----------------
  // 현재 그룹/탭을 주소의 해시(#group/tab)에 replaceState 로 기록한다. 그래야 (1) 로그인 왕복(?next=/#settings/models) 뒤 같은 자리로 돌아오고
  // (2) 새로고침·bfcache 복원에서도 화면이 유지된다. 히스토리는 오염시키지 않으므로 '뒤로가기'는 로그인 이전 페이지로 바로 간다.
  function navState() {
    const g = $('.groups button.active'), nav = $('.tabs:not(.hidden)');
    const t = nav ? $('button.active', nav) : null;
    return { group: g ? g.dataset.group : 'ask', tab: t ? t.dataset.tab : '' };
  }
  function writeHash() {
    const s = navState(); const h = '#' + s.group + (s.tab ? '/' + s.tab : '');
    if (location.hash === h) return;
    try { history.replaceState(null, '', location.pathname + location.search + h); } catch (e) { location.hash = h; }
  }
  function selectTab(b, silent) {
    const nav = b.parentElement;
    $$('button', nav).forEach((x) => x.classList.remove('active')); b.classList.add('active');
    $$('.tabs button').forEach((x) => { if (x.parentElement !== nav) x.classList.remove('active'); });
    $$('.tab').forEach((t) => { if (!t.classList.contains('pinned')) t.classList.remove('active'); });
    const sec = $('#tab-' + b.dataset.tab); if (sec && !sec.classList.contains('pinned')) sec.classList.add('active');
    writeHash(); updateCli();
    renderPinList();
    if (!silent && loaders[b.dataset.tab]) { try { loaders[b.dataset.tab](); } catch (e) { console.error('tab loader', b.dataset.tab, e); } }
  }
  function selectGroup(b, tabKey) {
    $$('.groups button').forEach((x) => x.classList.remove('active')); b.classList.add('active');
    $$('.tabs').forEach((n) => n.classList.toggle('hidden', n.dataset.group !== b.dataset.group));
    const nav = $(`.tabs[data-group="${b.dataset.group}"]`);
    const want = tabKey ? $$('button', nav).find((x) => x.dataset.tab === tabKey) : null;
    const act = want || $('button.active', nav) || $('button', nav);
    if (act) selectTab(act);
  }
  function applyHash() {
    const parts = (location.hash || '').replace(/^#/, '').split('/');
    let g = parts[0] || '', t = parts[1] || '';
    // 없어진 탭의 예전 주소를 새 자리로 보낸다 (북마크·문서 링크가 빈 화면으로 끝나지 않게).
    // observability/arch(구조·흐름)는 2026-09-19 에 🧭 Pipeline 으로 흡수됐다.
    const alias = TAB_ALIAS[g + '/' + t] || TAB_ALIAS[t];
    if (alias) { g = alias[0]; t = alias[1]; }
    const gb = $$('.groups button').find((x) => x.dataset.group === g);
    if (gb) { selectGroup(gb, t); return true; }
    if (t || g) { const tb = $$('.tabs button').find((x) => x.dataset.tab === (t || g)); if (tb) { const grp = $$('.groups button').find((x) => x.dataset.group === tb.parentElement.dataset.group); if (grp) { selectGroup(grp, tb.dataset.tab); return true; } } }
    return false;
  }
  // ---------------- 탭 고정 (복수 메뉴 동시 보기) ----------------
  // 고정한 탭의 <section> 을 #pinned-pane 으로 옮겨 항상 보이게 한다. 예: 서버 모니터 + 로그를 고정해 두고 Ask 에서 질의.
  const PINS = [];
  // 배치 상태: cols(자동/1~4열) · height(패널 높이) · wide(2칸 차지) · collapsed(제목만)
  const PINVIEW = { cols: 'auto', height: 'm', wide: {}, collapsed: {} };
  const COLS = ['auto', '1', '2', '3', '4'];
  const HEIGHTS = { s: '260px', m: '420px', l: '620px', auto: '' };
  function tabLabel(key) { const b = $$('.tabs button').find((x) => x.dataset.tab === key); return b ? b.textContent : key; }
  function groupOf(key) { const b = $$('.tabs button').find((x) => x.dataset.tab === key); return b ? (b.parentElement.dataset.group || '') : ''; }
  function groupIcon(key) { const g = $$('.groups button').find((x) => x.dataset.group === groupOf(key)); return g ? (g.textContent.trim().split(/\s+/)[0] || '') : ''; }
  function isPinned(key) { return PINS.indexOf(key) >= 0; }
  function tabVisible(key) { const s = $('#tab-' + key); return !!s && (s.classList.contains('active') || s.classList.contains('pinned')); }

  function savePinView() {
    try {
      localStorage.setItem('llmwiki.pins', JSON.stringify(PINS));
      localStorage.setItem('llmwiki.pinview', JSON.stringify(PINVIEW));
    } catch (e) { /* ignore */ }
  }
  // 고정 패널 하나의 머리글: 제목 · 새로고침 · 넓게 · 접기 · 좌우 이동 · 해제
  function pinHeadHtml(key) {
    const wide = !!PINVIEW.wide[key], col = !!PINVIEW.collapsed[key];
    return `<b><span class="pin-ico">${esc(groupIcon(key))}</span>${esc(tabLabel(key))}</b>
      <span class="pin-acts">
        <button class="pin-act" data-act="left"  data-k="${esc(key)}" title="왼쪽으로 이동">◀</button>
        <button class="pin-act" data-act="right" data-k="${esc(key)}" title="오른쪽으로 이동">▶</button>
        <button class="pin-act" data-act="reload" data-k="${esc(key)}" title="이 패널만 새로 고침">⟳</button>
        <button class="pin-act${wide ? ' on' : ''}" data-act="wide" data-k="${esc(key)}" title="${wide ? '한 칸으로' : '두 칸 넓게'}">${wide ? '⇲' : '⇱'}</button>
        <button class="pin-act" data-act="fold" data-k="${esc(key)}" title="${col ? '펼치기' : '제목만 남기고 접기'}">${col ? '▸' : '▾'}</button>
        <button class="pin-act danger" data-act="unpin" data-k="${esc(key)}" title="고정 해제">✕</button>
      </span>`;
  }
  function wirePinHead(hd, key) {
    $$('.pin-act', hd).forEach((b) => b.onclick = (ev) => {
      ev.stopPropagation();
      const a = b.dataset.act;
      if (a === 'unpin') return togglePin(key);
      if (a === 'wide') { PINVIEW.wide[key] = !PINVIEW.wide[key]; if (!PINVIEW.wide[key]) delete PINVIEW.wide[key]; }
      else if (a === 'fold') { PINVIEW.collapsed[key] = !PINVIEW.collapsed[key]; if (!PINVIEW.collapsed[key]) delete PINVIEW.collapsed[key]; }
      else if (a === 'reload') { if (loaders[key]) { try { loaders[key](); toast(tabLabel(key) + ' 새로 고침'); } catch (e) { console.error(e); } } return; }
      else if (a === 'left' || a === 'right') {
        const i = PINS.indexOf(key), j = a === 'left' ? i - 1 : i + 1;
        if (i < 0 || j < 0 || j >= PINS.length) return;
        PINS[i] = PINS[j]; PINS[j] = key;
      }
      applyPinView();
    });
  }
  // PINS 순서대로 DOM 을 다시 배열하고 열 수·높이·넓게·접기를 반영한다.
  function applyPinView() {
    const pane = $('#pinned-pane'); if (!pane) return;
    PINS.forEach((k) => { const s = $('#tab-' + k); if (s && s.parentElement === pane) pane.appendChild(s); });
    pane.dataset.cols = PINVIEW.cols;
    pane.style.setProperty('--pin-h', HEIGHTS[PINVIEW.height] || '');
    pane.classList.toggle('free-height', PINVIEW.height === 'auto');
    PINS.forEach((k) => {
      const sec = $('#tab-' + k); if (!sec) return;
      sec.classList.toggle('wide', !!PINVIEW.wide[k]);
      sec.classList.toggle('folded', !!PINVIEW.collapsed[k]);
      const hd = $(':scope > .pin-head', sec);
      if (hd) { hd.innerHTML = pinHeadHtml(k); wirePinHead(hd, k); }
    });
    $$('#pin-cols button').forEach((b) => b.classList.toggle('active', b.dataset.cols === PINVIEW.cols));
    $$('#pin-height button').forEach((b) => b.classList.toggle('active', b.dataset.h === PINVIEW.height));
    renderPinList();
  }
  function renderPinList() {
    const box = $('#tabpin-list');
    if (box) {
      box.innerHTML = PINS.map((k) => `<span class="pin-chip" data-goto="${esc(k)}" title="이 패널로 이동"><span class="pin-ico">${esc(groupIcon(k))}</span>${esc(tabLabel(k))}<i data-pin="${esc(k)}" title="고정 해제">✕</i></span>`).join('');
      $$('#tabpin-list [data-pin]').forEach((c) => c.onclick = (e) => { e.stopPropagation(); togglePin(c.dataset.pin); });
      $$('#tabpin-list [data-goto]').forEach((c) => c.onclick = () => {
        const s = $('#tab-' + c.dataset.goto);
        if (s) { s.scrollIntoView({ behavior: 'smooth', block: 'center' }); s.classList.add('flash'); setTimeout(() => s.classList.remove('flash'), 900); }
      });
    }
    const btn = $('#btn-pin-tab');
    if (btn) { const cur = navState().tab; btn.textContent = isPinned(cur) ? '📌 고정 해제' : '📌 이 탭 고정'; btn.classList.toggle('on', isPinned(cur)); }
    const clr = $('#btn-pin-clear'); if (clr) clr.classList.toggle('hidden', PINS.length === 0);
    const seg = $('#pin-cols'), hseg = $('#pin-height');
    if (seg) seg.classList.toggle('hidden', PINS.length < 2);
    if (hseg) hseg.classList.toggle('hidden', PINS.length === 0);
    savePinView();
  }
  function togglePin(key) {
    const sec = $('#tab-' + key); const pane = $('#pinned-pane');
    if (!sec || !pane) return;
    const i = PINS.indexOf(key);
    if (i >= 0) {
      PINS.splice(i, 1);
      delete PINVIEW.wide[key]; delete PINVIEW.collapsed[key];
      sec.classList.remove('pinned', 'wide', 'folded');
      const body = $(':scope > .pin-body', sec);
      if (body) { while (body.firstChild) sec.appendChild(body.firstChild); body.remove(); }  // 감쌌던 내용을 되돌린다
      const hdr = $(':scope > .pin-head', sec); if (hdr) hdr.remove();
      $('main').insertBefore(sec, pane);          // 원래 위치(본문)로 되돌린다
      sec.classList.remove('active');
      // 지금 보고 있던 탭을 해제했으면 본문에 다시 띄운다 (빈 화면이 남지 않게)
      if (navState().tab === key) { const b = $$('.tabs button').find((x) => x.dataset.tab === key); if (b) selectTab(b, true); }
    } else {
      PINS.push(key);
      // 내용을 .pin-body 로 감싸 머리글만 고정하고 내용만 스크롤시킨다 (패널이 여러 개여도 제목이 보인다)
      const body = document.createElement('div');
      body.className = 'pin-body';
      while (sec.firstChild) body.appendChild(sec.firstChild);
      const hdr = document.createElement('div');
      hdr.className = 'pin-head';
      sec.appendChild(hdr); sec.appendChild(body);
      sec.classList.add('pinned');
      pane.appendChild(sec);
      if (loaders[key]) { try { loaders[key](); } catch (e) { console.error(e); } }
    }
    applyPinView();
  }
  function clearPins() { PINS.slice().forEach(togglePin); }
  function restorePins() {
    let saved = [], view = null;
    try { saved = JSON.parse(localStorage.getItem('llmwiki.pins') || '[]'); } catch (e) { saved = []; }
    try { view = JSON.parse(localStorage.getItem('llmwiki.pinview') || 'null'); } catch (e) { view = null; }
    if (!view) {                                   // 이전 버전(2열 on/off) 호환
      let sp = '0'; try { sp = localStorage.getItem('llmwiki.split') || '0'; } catch (e) { /* ignore */ }
      view = { cols: sp === '1' ? '2' : 'auto' };
    }
    setPinView(view);
    (saved || []).filter((k) => $('#tab-' + k)).forEach((k) => { if (!isPinned(k)) togglePin(k); });
    applyPinView();
  }
  function setPinView(v) {
    if (!v || typeof v !== 'object') return;
    if (COLS.indexOf(String(v.cols)) >= 0) PINVIEW.cols = String(v.cols);
    if (v.height && HEIGHTS[v.height] !== undefined) PINVIEW.height = v.height;
    if (v.wide && typeof v.wide === 'object') { Object.keys(PINVIEW.wide).forEach((k) => delete PINVIEW.wide[k]); Object.keys(v.wide).forEach((k) => { if (v.wide[k]) PINVIEW.wide[k] = true; }); }
    if (v.collapsed && typeof v.collapsed === 'object') { Object.keys(PINVIEW.collapsed).forEach((k) => delete PINVIEW.collapsed[k]); Object.keys(v.collapsed).forEach((k) => { if (v.collapsed[k]) PINVIEW.collapsed[k] = true; }); }
  }
  function initNav() {
    $$('.groups button').forEach((b) => b.onclick = () => selectGroup(b));
    $$('.tabs button').forEach((b) => b.onclick = () => selectTab(b));
    window.addEventListener('hashchange', () => applyHash());
    const pb = $('#btn-pin-tab');
    if (pb) pb.onclick = () => { const cur = navState().tab; if (cur) togglePin(cur); };
    const cb = $('#btn-pin-clear'); if (cb) cb.onclick = () => clearPins();
    $$('#pin-cols button').forEach((b) => b.onclick = () => { PINVIEW.cols = b.dataset.cols; applyPinView(); });
    $$('#pin-height button').forEach((b) => b.onclick = () => { PINVIEW.height = b.dataset.h; applyPinView(); });
  }

  // ---------------- 계정별 설정 프로파일 ----------------
  // 토글·프리셋·요청 오버라이드·테마·고정 탭을 내 계정에 저장한다 (서버 기본 설정은 건드리지 않는다 — /api/profile).
  function currentProfile() {
    const tg = {};
    $$('[data-toggle]').forEach((cb) => { tg[cb.dataset.toggle] = cb.checked; });
    const ov = {};
    // 예전 프로파일에 남아 있는 ov-llm 등은 그냥 무시된다 (아래 복원도 있는 요소에만 값을 넣는다)
    ['ov-debug', 'ov-output'].forEach((id) => { const e = $('#' + id); if (e) ov[id] = e.value; });
    let theme = 'light'; try { theme = localStorage.getItem('llmwiki.theme') || 'light'; } catch (e) { /* ignore */ }
    const s = navState();
    return { theme: theme, toggles: tg, presets: presetNames(), overrides: ov, pins: PINS.slice(),
             pinview: { cols: PINVIEW.cols, height: PINVIEW.height,
                        wide: Object.assign({}, PINVIEW.wide), collapsed: Object.assign({}, PINVIEW.collapsed) },
             split: PINVIEW.cols === '2',          // 이전 버전과의 호환용
             sidebar_collapsed: sidebarState(),    // 사이드바 블록 접힘 상태 (요청 13) — 서버 profiles.ALLOWED 에도 있어야 저장된다
             sidebar_toggles: sidebarToggles(),    // 토글 블록 배치 compact|full (§2.6) — profiles.ALLOWED · 기본 compact
             mode: ($('#q-mode') || {}).value || '', group: s.group, tab: s.tab };
  }
  function applyProfile(p) {
    if (!p || typeof p !== 'object') return false;
    try {
      if (p.theme) { localStorage.setItem('llmwiki.theme', p.theme); const sel = $('#theme-select'); if (sel) { sel.value = p.theme; sel.onchange(); } }
    } catch (e) { /* ignore */ }
    if (p.toggles) $$('[data-toggle]').forEach((cb) => { if (cb.dataset.toggle in p.toggles) cb.checked = !!p.toggles[cb.dataset.toggle]; });
    if (p.presets) $$('[data-preset]').forEach((cb) => { cb.checked = p.presets.indexOf(cb.dataset.preset) >= 0; });
    if (p.overrides) Object.keys(p.overrides).forEach((id) => { const e = $('#' + id); if (e) e.value = p.overrides[id]; });
    if (p.mode && $('#q-mode')) $('#q-mode').value = p.mode;
    if (p.pinview) setPinView(p.pinview);
    else if (typeof p.split === 'boolean') PINVIEW.cols = p.split ? '2' : 'auto';   // 이전 버전 프로파일
    if (p.sidebar_collapsed && typeof p.sidebar_collapsed === 'object') setSidebarState(p.sidebar_collapsed);
    if (p.sidebar_toggles === 'full' || p.sidebar_toggles === 'compact') setSidebarToggles(p.sidebar_toggles);
    (p.pins || []).filter((k) => $('#tab-' + k) && !isPinned(k)).forEach(togglePin);
    applyPinView();
    if (p.presets && p.presets.length) applyPresets(); else { markPresetToggles(); updateCli(); }
    return true;
  }
  async function saveProfile() {
    const prof = currentProfile();
    const j = await api('/api/profile', { action: 'save', profile: prof });
    const msg = $('#profile-msg');
    if (j && j.ok) { if (msg) msg.textContent = '내 설정을 저장했습니다 (' + (j.user || '') + ')'; toast('내 설정 저장됨'); }
    else if (j && j.anonymous) {
      try { localStorage.setItem('llmwiki.profile', JSON.stringify(prof)); } catch (e) { /* ignore */ }
      if (msg) msg.textContent = '게스트라 이 브라우저에만 저장했습니다 (로그인하면 계정에 저장됩니다)';
      toast('브라우저에 저장됨 (게스트)');
    }
    return j;
  }
  async function loadProfile(quiet) {
    let prof = null;
    const j = await api('/api/profile');
    if (j && j.profile && Object.keys(j.profile).length) prof = j.profile;
    if (!prof) { try { prof = JSON.parse(localStorage.getItem('llmwiki.profile') || 'null'); } catch (e) { prof = null; } }
    const msg = $('#profile-msg');
    if (!prof) { if (!quiet && msg) msg.textContent = '저장된 설정이 없습니다'; return null; }
    applyProfile(prof);
    if (msg) msg.textContent = '내 설정을 불러왔습니다' + (j && j.anonymous ? ' (브라우저)' : '');
    if (!quiet) toast('내 설정 적용됨');
    return prof;
  }
  // ---------------- theme ----------------
  async function initTheme() {
    let reg = { themes: [{ key: 'light', title: 'Light' }, { key: 'dark', title: 'Dark' }] };
    try { reg = await api('/api/themes'); } catch (e) { /* ignore */ }
    const sel = $('#theme-select');
    sel.innerHTML = '<option value="auto">auto (시스템)</option>' + (reg.themes || []).map((t) => `<option value="${esc(t.key)}">${esc(t.title)}</option>`).join('');
    let saved = null; try { saved = localStorage.getItem('llmwiki.theme'); } catch (e) { /* ignore */ }
    sel.value = saved || reg.default || 'light';   // themes.json 의 default (light) — 사용자가 고르면 localStorage 값이 우선
    const apply = () => {
      const v = sel.value; let key = v;
      if (v === 'auto') key = (window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches) ? ((reg.auto || {}).dark || 'dark') : ((reg.auto || {}).light || 'light');
      if (key === 'light') delete document.documentElement.dataset.theme; else document.documentElement.dataset.theme = key;
    };
    sel.onchange = () => { try { localStorage.setItem('llmwiki.theme', sel.value); } catch (e) { /* ignore */ } apply(); };
    apply();
    if (window.matchMedia) window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', apply);
  }

  function boot() {
    initNav();
    initTheme();
    initFxFilter();
    initSidebar();
    ['#ov-debug', '#ov-output'].forEach((s) => { const e = $(s); if (e) e.addEventListener('change', updateCli); });
    // 값이 있는 입력칸을 × 로 비운다 (datalist 입력은 값이 남아 있으면 목록이 그 값으로 걸러진다)
    $$('[data-clear]').forEach((b) => b.onclick = () => { const i = $('#' + b.dataset.clear); if (!i) return; i.value = ''; i.dispatchEvent(new Event('change', { bubbles: true })); i.focus(); });
    $('#btn-reset-toggles').onclick = () => setTogglesFrom(STATE.settings.toggles);
    // 사이드바의 '토글을 config.json 에 저장' 은 제거했다: 한 사람이 누르면 모든 사용자의 서버 기본값이 바뀌기 때문.
    // 서버 기본값 변경은 Settings › config.json (admin 등급, 감사 로그에 기록) 에서만 한다.
    // bfcache 로 되돌아왔을 때(뒤로가기): DOM 은 그대로지만 로그인 상태·서버 상태는 오래됐으므로 다시 읽는다.
    window.addEventListener('pageshow', (e) => { if (e.persisted) { STATE.leaving = false; applyHash(); loadStatus().catch(() => {}); } });
    document.addEventListener('visibilitychange', () => { if (!document.hidden && !STATE.leaving && STATE.status) loadStatus().catch(() => {}); });
    const pvs = $('#preview-role');
    if (pvs) pvs.onchange = async () => { await api('/api/auth/preview', { role: pvs.value }); location.reload(); };
    const ps = $('#btn-profile-save'); if (ps) ps.onclick = saveProfile;
    const pl = $('#btn-profile-load'); if (pl) pl.onclick = () => loadProfile(false);
    const pr = $('#btn-profile-reset'); if (pr) pr.onclick = async () => { await api('/api/profile', { action: 'reset' }); try { localStorage.removeItem('llmwiki.profile'); } catch (e) { /* ignore */ } const m = $('#profile-msg'); if (m) m.textContent = '저장한 설정을 지웠습니다'; toast('내 설정 삭제됨'); };
    loadStatus().then((j) => {
      // 해시가 없으면 예전에는 주소만 적고 끝나서 **처음 열린 탭의 loader 가 돌지 않았다**
      // → 그 탭(예: Settings › 모델)이 서버 설정을 읽지 않은 빈/오래된 화면으로 남았다 (2026-09-16).
      if (!applyHash()) { writeHash(); const ab = $('.tabs:not(.hidden) button.active') || $('.tabs button.active'); if (ab && loaders[ab.dataset.tab]) { try { loaders[ab.dataset.tab](); } catch (e) { console.error('boot loader', ab.dataset.tab, e); } } }
      restorePins();
      loadProfile(true).catch(() => {});      // 로그인 사용자의 저장된 화면 설정을 자동 적용
      updateCli();
      startQueueStrip();
      (LW.onReady || []).forEach((f) => { try { f(j); } catch (e) { console.error(e); } });
    }).catch((e) => { console.error('boot', e); toast('초기화 실패: ' + e); });
  }

  // ---------------- 질의 화면의 '지금 서버에서' 한 줄 요약 ----------------
  // 내 요청이 대기열에 있는지, 다른 사람의 빌드가 도는지, 임베딩이 몇 %인지 한눈에. 2초마다 갱신(락 없는 엔드포인트라 가볍다).
  function renderQueueStrip(j) {
    const el = $('#q-queue'); if (!el) return;
    if (!j || j.error) { el.classList.add('hidden'); return; }
    const run = j.running || [], q = j.queued || [], ext = j.external || [];
    const all = run.concat(ext);
    if (!all.length && !q.length) { el.classList.add('hidden'); el.innerHTML = ''; return; }
    const me = j.me;
    const item = (r) => {
      const pct = r.pct == null ? '' : ` ${fmt(r.pct, 0)}%`;
      const mine = me && r.user === me;
      const llm = r.llm && r.llm.active ? ' · LLM 대기' : '';
      return `<span class="qs-item ${mine ? 'mine' : ''}" title="${esc((r.label || '') + ' — ' + (r.user || '') + ' · ' + (r.origin || ''))}">` +
        `${r.weight === 'exclusive' ? '🔒' : r.external ? '🖥' : '▶'} ${esc(r.kind || '')}${pct} <span class="muted">${LW.fmtS(r.elapsed_s)}${llm}</span>` +
        `${(mine || j.admin) && r.token ? `<button class="qs-x" data-qcancel="${esc(r.token)}" title="중지">✕</button>` : ''}</span>`;
    };
    el.classList.remove('hidden');
    el.innerHTML = `<span class="muted small">지금 서버에서</span> ${all.map(item).join('')}` +
      (q.length ? ` <span class="qs-item wait">⏳ 대기 ${q.length}건</span>` : '') +
      ` <a href="#observability/activity" class="muted small" title="진행 중 작업 전체 보기">전체 보기 →</a>`;
    $$('#q-queue [data-qcancel]').forEach((b) => b.onclick = async () => { b.disabled = true; await cancelToken(b.dataset.qcancel, 'ask'); });
  }
  // ---------------- 헤더의 항상 보이는 활동 표시기 (HUD) ----------------
  // 동시 실행 슬롯을 칸으로 그린다: 채워진 칸 = 실행 중, 초록 = 내 요청, 점선 = 대기.
  // 어느 화면에 있든 "서버가 지금 바쁜가 / 내 요청은 어디쯤인가"를 눈으로 알 수 있게 한다.
  function renderHud(j) {
    const bars = $('#hud-bars'), txt = $('#hud-txt'), hud = $('#hud');
    if (!bars || !txt || !hud) return;
    if (!j || j.error) { bars.innerHTML = ''; txt.textContent = '—'; hud.classList.remove('busy', 'mine'); hud.title = '서버 활동을 읽지 못했습니다'; return; }
    const run = (j.running || []).concat(j.external || []), q = j.queued || [], me = j.me;
    const slots = Math.max(1, Math.min(12, Number((j.limits || {}).max_parallel_reads || 8)));
    const isMine = (r) => !!(me && r.user === me);
    const mineRun = run.filter(isMine).length, mineQ = q.filter(isMine).length;
    const heavy = run.some((r) => r.weight === 'exclusive' || r.weight === 'soft');
    // 실제 작업 하나 = 막대 하나. 실행 중을 먼저, 그 뒤에 대기열을 순서대로.
    // 내 요청은 초록, 남의 요청은 파랑, 대기는 점선. 빈 슬롯은 옅은 칸으로 남겨 여유를 보여 준다.
    const MAX = 14;
    const cells = [];
    const label = (r, st) => esc((r.kind || '') + ' ' + String(r.label || '').slice(0, 40) + ' · ' + st);
    run.slice(0, MAX).forEach((r) => {
      const pct = r.pct != null ? Math.max(8, Math.min(100, r.pct)) : 100;
      cells.push(`<i class="on${isMine(r) ? ' mine' : ''}${r.weight === 'exclusive' || r.weight === 'soft' ? ' heavy' : ''}"`
        + ` style="--f:${pct}%" title="${label(r, '실행 중 ' + LW.fmtDur(r.elapsed_s))}"></i>`);
    });
    for (let i = run.length; i < Math.min(slots, MAX); i++) cells.push('<i class="free" title="빈 슬롯"></i>');
    q.slice(0, Math.max(0, MAX - cells.length)).forEach((r, i) => {
      cells.push(`<i class="wait${isMine(r) ? ' mine' : ''}" title="${label(r, '대기 ' + (i + 1) + '번째')}"></i>`);
    });
    bars.innerHTML = cells.join('') + (run.length + q.length > MAX ? '<b class="hud-more">+</b>' : '');
    txt.innerHTML = `${run.length}<small>/${slots}</small>` + (q.length ? ` <span class="hud-q">+${q.length}</span>` : '');
    hud.classList.toggle('busy', run.length >= slots || heavy);
    hud.classList.toggle('mine', mineRun + mineQ > 0);
    const top = run.slice(0, 4).map((r) => `· ${r.kind || ''} ${(r.label || '').slice(0, 40)} (${LW.fmtDur(r.elapsed_s)})${isMine(r) ? ' ← 내 요청' : ''}`);
    hud.title = `실행 중 ${run.length} / 슬롯 ${slots} · 대기 ${q.length}` +
      (mineRun + mineQ ? ` · 내 요청 ${mineRun + mineQ}건` : '') +
      (top.length ? '\n' + top.join('\n') : '') + '\n클릭하면 진행 중 작업';
  }
  // ---------------- 활동 폴링 한 곳으로 모으기 ----------------
  // 예전에는 헤더 HUD · 질의 화면 요약 · '진행 중 작업' 탭 · 빌드 탭이 각자 2초마다 /api/activity 를 불렀다.
  // 브라우저는 한 사이트에 동시 연결을 6개까지만 열기 때문에, 오래 걸리는 질의 몇 개가 연결을 물고 있으면
  // 갱신 요청이 브라우저 안에서 줄을 서다가 한꺼번에 처리된다. 그래서 **한 번만 불러서 나눠 준다**.
  const ACT_SUBS = new Set();
  let ACT_TIMER = null, ACT_BUSY = false, ACT_LAST = null;
  function onActivity(fn) { ACT_SUBS.add(fn); if (ACT_LAST) { try { fn(ACT_LAST); } catch (e) { /* ignore */ } } return () => ACT_SUBS.delete(fn); }
  async function activityTick(force) {
    if (ACT_BUSY) return ACT_LAST;              // 응답이 늦으면 건너뛴다 (요청이 쌓이지 않게)
    if (!force && (document.hidden || STATE.leaving)) return ACT_LAST;
    ACT_BUSY = true;
    let j = null;
    try { j = await api('/api/activity?history=25'); } catch (e) { /* 조용히 */ }
    ACT_BUSY = false;
    ACT_LAST = j;
    ACT_SUBS.forEach((fn) => { try { fn(j); } catch (e) { console.error('activity sub', e); } });
    return j;
  }
  function startQueueStrip() {
    if (ACT_TIMER) clearInterval(ACT_TIMER);
    onActivity((j) => {
      renderHud(j);                             // HUD 는 어느 화면에서든 항상 갱신
      if (!tabVisible('query') && !tabVisible('activity')) { const el = $('#q-queue'); if (el) el.classList.add('hidden'); return; }
      renderQueueStrip(j);
    });
    ACT_TIMER = setInterval(() => activityTick(false), 2000);
    activityTick(true);
  }
  return { $, $$, esc, fmt, fmtK, ts, dt, PALETTE, STAGE_COLOR, STATE, loaders, toast, api, switchTab, switchGroup, overrides, presetNames, applyPresets, cliEquiv, updateCli, setTogglesFrom, loadStatus, metaBlock, renderTrace, flatten, renderStageTable, pollJob, renderLive, watchProgress, fmtS, stepUp, cancelToken, gotoLogin, applyHash, togglePin, tabVisible, myJobs: MY_JOBS, saveProfile, loadProfile, boot, fmtDur,
           openRerun, runRerun, loadRerunPoints,
           LIMITS, loadLimits, fmtLimit, limitFor, limitTitle, limitPct,
           settingsChanged, onSettingsChanged,
           copyText, copyModal, stageJson, traceMarkdown, sidebarState, setSidebarState, setSidebarAll,
           sidebarToggles, setSidebarToggles, refreshSidebarSummary, tuningOverrides, setTuningOverride, clearTuningOverrides,
           onActivity, refreshActivity: () => activityTick(true), onReady: [] };
})();
