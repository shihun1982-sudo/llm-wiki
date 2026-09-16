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
  function switchTab(name) { const b = $$('.tabs button').find((x) => x.dataset.tab === name); if (b) b.click(); }
  function switchGroup(name) { const b = $$('.groups button').find((x) => x.dataset.group === name); if (b) b.click(); }

  // ---------------- sidebar: toggles (auto from server), presets, overrides ----------------
  function buildSidebar(j) {
    const box = $('#toggle-groups');
    const known = new Set();
    box.innerHTML = (j.toggle_groups || []).map((g) => {
      g.toggles.forEach((t) => known.add(t));
      return `<div class="toggle-group ${g.perf ? 'perf' : ''}" data-g="${g.key}"><div class="tg-title" title="클릭: 접기/펼치기">${esc(g.title)} <small>(${g.toggles.length})</small></div>` +
        g.toggles.filter((t) => j.toggle_names.includes(t)).map((t) => `<label title="${esc(j.toggle_help[t] || '')}"><input type="checkbox" data-toggle="${t}"> ${t}</label>`).join('') + '</div>';
    }).join('');
    const rest = j.toggle_names.filter((t) => !known.has(t));
    if (rest.length) box.innerHTML += `<div class="toggle-group"><div class="tg-title">기타</div>${rest.map((t) => `<label title="${esc(j.toggle_help[t] || '')}"><input type="checkbox" data-toggle="${t}"> ${t}</label>`).join('')}</div>`;
    $$('#toggle-groups .tg-title').forEach((h) => h.onclick = () => h.parentElement.classList.toggle('collapsed'));
    $$('[data-toggle]').forEach((cb) => cb.addEventListener('change', () => {
      // 사용자가 손으로 바꾼 값은 기억해 두고(프리셋을 껐다 켜도 유지), 프리셋이 정한 값과 다르면 프리셋 표시를 지운다
      const t = cb.dataset.toggle, base = (STATE.settings && STATE.settings.toggles) || {};
      if (cb.checked === !!base[t]) delete PRESET.manual[t]; else PRESET.manual[t] = cb.checked;
      if (t in PRESET.set && PRESET.set[t] !== cb.checked) { delete PRESET.set[t]; markPresetToggles(); }
      updateCli(); if (loaders._toggleChanged) loaders._toggleChanged();
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
  function markPresetToggles() {
    $$('[data-toggle]').forEach((cb) => {
      const t = cb.dataset.toggle, lab = cb.parentElement, by = t in PRESET.set;
      lab.classList.toggle('by-preset', by);
      let s = lab.querySelector('.src'); if (by) { if (!s) { s = document.createElement('span'); s.className = 'src'; lab.appendChild(s); } s.textContent = '← ' + PRESET.src[t]; } else if (s) s.remove();
      lab.title = (STATE.toggleHelp[t] || '') + (by ? '\n\n[프리셋 ' + PRESET.src[t] + ' 이(가) ' + (PRESET.set[t] ? 'ON' : 'OFF') + ' 으로 정함 — 손으로 바꾸면 프리셋보다 우선하지 않고 서버에서 프리셋 값이 다시 적용됩니다. 다른 값을 쓰려면 프리셋 체크를 해제하세요]' : '');
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
  function overrides() {
    const ov = {};
    $$('[data-toggle]').forEach((cb) => { ov[cb.dataset.toggle] = cb.checked; });
    const llm = $('#ov-llm').value, emb = $('#ov-embed').value, k = $('#ov-k').value, dbg = $('#ov-debug').value;
    if (llm) ov.llm_provider = llm;
    if (emb) ov.embed_provider = emb;
    if (k) ov.top_k_final = parseInt(k, 10);
    if (dbg !== '') ov.debug_level = parseInt(dbg, 10);
    if ($('#ov-answer-model').value.trim()) ov.answer_model = $('#ov-answer-model').value.trim();
    if ($('#ov-rerank-model').value.trim()) ov.rerank_model = $('#ov-rerank-model').value.trim();
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
  }
  function setTogglesFrom(t) { PRESET.manual = {}; $$('[data-toggle]').forEach((cb) => { cb.checked = !!t[cb.dataset.toggle]; }); if (presetNames().length || modePreset()) applyPresets(); else { markPresetToggles(); updateCli(); } }

  // ---------------- status ----------------
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
    const dl = $('#dl-llm-models');
    if (dl && !dl.children.length) {
      const cat = (p.catalog && p.catalog.llm) || {};
      dl.innerHTML = [].concat(cat.anthropic || [], cat.openai || [], cat.ollama || []).filter((m) => !String(m).startsWith('(')).map((m) => `<option value="${esc(m)}">`).join('');
      api('/api/models/catalog').then((c) => {   // models.json 카탈로그가 있으면 그것으로 교체 (사람이 추가/삭제하는 목록)
        const ms = (c && c.models) || []; if (!ms.length) return;
        dl.innerHTML = ms.filter((m) => m.enabled !== false).map((m) => `<option value="${esc(m.id)}">${esc(m.provider + ' · ' + (m.label || ''))}</option>`).join('');
        STATE.catalog = c;
      }).catch(() => {});
    }
    return j;
  }

  // ---------------- trace renderer ----------------
  function metaBlock(n) {
    const parts = [];
    const meta = Object.assign({}, n.meta || {}); delete meta.reason;
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
    $('[data-tr="copy"]', bar).onclick = async () => {
      try { await navigator.clipboard.writeText(JSON.stringify(trace, null, 1)); toast('trace 를 클립보드에 복사했습니다'); } catch (e) { toast('복사 실패: ' + e); }
    };
  }
  function renderTrace(el, trace, opts) {
    opts = opts || {};
    if (!trace) { el.innerHTML = ''; return; }
    const total = trace.ms || 1;
    const rows = [];
    (function walk(n, depth) { rows.push({ n, depth }); (n.children || []).forEach((c) => walk(c, depth + 1)); })(trace, 0);
    const sm = trace.summary;
    let head = '';
    if (sm) head = `<div class="tr-summary">총 <b>${fmt(sm.total_ms)} ms</b> · LLM 호출 <b>${sm.llm.calls}</b> · 토큰 <b>${fmtK(sm.llm.total_tokens)}</b> (in ${fmtK(sm.llm.input_tokens)} / out ${fmtK(sm.llm.output_tokens)}) · SQL <b>${sm.sql_statements}</b> · debug ${trace.debug_level} · run ${esc(trace.run_id || '-')} · 느린 단계: ${sm.slowest.slice(0, 3).map((x) => esc(x.name) + ' ' + x.pct + '%').join(', ')}${sm.errors.length ? ' · <span class="errtxt">오류 ' + sm.errors.length + '</span>' : ''}</div>`;
    el.innerHTML = head + '<div class="trace">' + rows.map((r) => {
      const n = r.n, skip = n.enabled === false;
      const off = n.offset_ms || 0;
      const left = Math.min(100, (off / total) * 100), w = Math.max(0.4, (n.ms / total) * 100);
      const color = STAGE_COLOR[n.name] || (r.depth === 0 ? '#898781' : '#2a78d6');
      const pct = r.depth === 1 && !skip ? ` <small class="muted">${fmt(100 * n.ms / total, 0)}%</small>` : '';
      const c = n.counters || {};
      const badges = (c.llm_calls ? `<span class="cnt llm" title="LLM 호출/토큰">llm ${c.llm_calls} · ${fmtK((c.llm_input_tokens || 0) + (c.llm_output_tokens || 0))} tok</span>` : '') + (c.sql ? `<span class="cnt" title="SQL 문 수">sql ${c.sql}</span>` : '');
      return `<div class="tr-row ${n.error ? 'has-err' : ''}"><div class="tr-name ${skip ? 'skip' : ''}" style="padding-left:${r.depth * 14}px" title="클릭: 상세">${esc(n.name)}${skip ? ' <small>(' + esc((n.meta || {}).reason || 'off') + ')</small>' : ''}${pct}</div>` +
        `<div class="tr-bar">${skip ? '' : `<i class="${n.error ? 'err' : ''}" style="left:${left}%;width:${w}%;background:${n.error ? '' : color}"></i>`}${badges}</div>` +
        `<div class="tr-ms">${skip ? '—' : fmt(n.ms) + ' ms'}</div><div class="tr-meta">${metaBlock(n)}</div></div>`;
    }).join('') + '</div>';
    $$('.tr-name', el).forEach((d) => d.onclick = () => d.parentElement.classList.toggle('open'));
    if (opts.openAll) $$('.tr-row', el).forEach((r) => r.classList.add('open'));
    if (opts.controls !== false) traceToolbar(el, trace);
  }
  function flatten(trace) { const out = []; (function walk(n, d) { out.push(Object.assign({ depth: d }, n)); (n.children || []).forEach((c) => walk(c, d + 1)); })(trace, 0); return out; }
  function renderStageTable(el, trace, other) {
    const total = trace.ms || 1, flat = flatten(trace);
    const om = {}; if (other) flatten(other).forEach((n) => { om[n.name + '@' + n.depth] = n; });
    el.innerHTML = '<table class="stage-table"><tr><th>단계</th><th>ms</th><th>self</th><th>%</th><th>offset</th><th>SQL</th><th>LLM</th><th>tokens in/out</th>' + (other ? '<th>비교 ms</th><th>Δ</th>' : '') + '<th>요약</th></tr>' +
      flat.filter((n) => n.depth > 0).map((n) => {
        const c = n.counters || {}, skip = n.enabled === false; const o = om[n.name + '@' + n.depth];
        const meta = Object.assign({}, n.meta || {}); delete meta.reason; const ms = JSON.stringify(meta);
        const d = o && !skip ? n.ms - (o.ms || 0) : null;
        return `<tr class="${skip ? 'skip' : ''}"><td style="padding-left:${8 + n.depth * 12}px">${esc(n.name)}${skip ? ' <small class="muted">(' + esc((n.meta || {}).reason || 'off') + ')</small>' : ''}</td><td class="num">${skip ? '—' : fmt(n.ms)}</td><td class="num">${skip ? '' : fmt(n.self_ms)}</td><td class="num">${skip ? '' : fmt(100 * n.ms / total, 1)}</td><td class="num">${skip ? '' : fmt(n.offset_ms, 0)}</td><td class="num">${c.sql || ''}</td><td class="num">${c.llm_calls || ''}</td><td class="num">${c.llm_calls ? fmtK(c.llm_input_tokens || 0) + '/' + fmtK(c.llm_output_tokens || 0) : ''}</td>` +
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
    if (cp) cp.onclick = async () => {
      const txt = (live.label ? live.label + '\n' : '') + (el._log || []).join('\n');
      try { await navigator.clipboard.writeText(txt); toast('로그 %d줄을 복사했습니다'.replace('%d', (el._log || []).length)); } catch (e) { toast('복사 실패: ' + e); }
    };
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
    const g = parts[0] || '', t = parts[1] || '';
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
    ['ov-llm', 'ov-embed', 'ov-k', 'ov-debug', 'ov-answer-model', 'ov-rerank-model'].forEach((id) => { const e = $('#' + id); if (e) ov[id] = e.value; });
    let theme = 'light'; try { theme = localStorage.getItem('llmwiki.theme') || 'light'; } catch (e) { /* ignore */ }
    const s = navState();
    return { theme: theme, toggles: tg, presets: presetNames(), overrides: ov, pins: PINS.slice(),
             pinview: { cols: PINVIEW.cols, height: PINVIEW.height,
                        wide: Object.assign({}, PINVIEW.wide), collapsed: Object.assign({}, PINVIEW.collapsed) },
             split: PINVIEW.cols === '2',          // 이전 버전과의 호환용
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
    ['#ov-llm', '#ov-embed', '#ov-k', '#ov-debug', '#ov-answer-model', '#ov-rerank-model'].forEach((s) => { const e = $(s); if (e) e.addEventListener('change', updateCli); });
    // 모델 입력(datalist): 값이 있으면 브라우저가 목록을 그 값으로 필터해 다시 고를 수 없으므로 × 로 비우거나, 포커스 시 전체 선택해 바로 덮어쓰게 한다
    $$('[data-clear]').forEach((b) => b.onclick = () => { const i = $('#' + b.dataset.clear); i.value = ''; i.dispatchEvent(new Event('change', { bubbles: true })); i.focus(); });
    ['#ov-answer-model', '#ov-rerank-model'].forEach((s) => { const i = $(s); if (!i) return; i.addEventListener('focus', () => i.select()); i.addEventListener('dblclick', () => { i.value = ''; i.dispatchEvent(new Event('change', { bubbles: true })); }); });
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
      if (!applyHash()) writeHash();
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
           onActivity, refreshActivity: () => activityTick(true), onReady: [] };
})();
