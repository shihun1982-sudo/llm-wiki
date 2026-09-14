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
  async function api(path, body, _retry) {
    const opts = body === undefined ? { headers: { 'X-Requested-With': 'llmwiki' } } : { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-Requested-With': 'llmwiki' }, body: JSON.stringify(body) };
    const r = await fetch(path, opts);
    let j; try { j = await r.json(); } catch (e) { j = { error: 'invalid response ' + r.status }; }
    if (r.status === 401) { location.href = '/login?next=' + encodeURIComponent(location.pathname + location.hash); return j; }
    if (r.status === 428 && j && j.need && !_retry) {
      const extra = await stepUp(j.need, path);
      if (!extra) { toast('취소됨'); return { error: 'cancelled', cancelled: true }; }
      return api(path, Object.assign({}, body || {}, extra), true);
    }
    if (r.status === 428 && j && j.need && _retry) { toast('확인 실패: ' + (j.need.password ? '비밀번호가 올바르지 않습니다' : j.error)); return j; }
    if (r.status === 403) { toast('권한 없음: ' + (j.error || '')); return j; }
    if (j && j.error && !j.result && !Array.isArray(j)) { toast('오류: ' + j.error); console.error(j); }
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
    STATE.status = j; STATE.settings = j.settings; STATE.toggleNames = j.toggle_names; STATE.toggleHelp = j.toggle_help || {}; STATE.settingHelp = j.setting_help || {}; STATE.providers = j.providers; STATE.roles = j.roles || []; STATE.toggleGroups = j.toggle_groups || []; STATE.presets = j.presets || [];
    const p = j.providers, s = j.stats;
    const ra = p.roles && p.roles.answer, rr = p.roles && p.roles.rerank;
    $('#provider-badge').textContent = `answer: ${ra ? ra.name + '/' + ra.model + (ra.available ? '' : ' (unavailable)') : p.llm.name} · rerank: ${p.rerank && p.rerank.url ? 'api/' + p.rerank.model : (rr ? rr.name + '/' + rr.model : '-')} · embed: ${p.embedder.name}${p.embedder.dim ? ' d=' + p.embedder.dim : ''}`;
    $('#stats-badge').textContent = `docs ${s.docs} · chunks ${s.chunks} · vec ${s.embeddings} · entities ${s.entities} · rels ${s.relations} · requests ${s.requests} · pending ${s.proposals_pending}`;
    const w = j.watcher || {};
    $('#watch-badge').textContent = `auto-build: ${w.enabled ? 'on (' + w.interval + 's)' : 'off'}${w.last_scan ? ' · last scan ' + ts(w.last_scan) : ''}`;
    STATE.auth = j.auth || null;
    const ub = $('#user-badge');
    if (ub) {
      const a = j.auth || {}, u = a.user || {};
      if (a.mode === 'off') { ub.innerHTML = `<span title="security.json mode=off/auto(loopback): 로그인 없음. 파괴적 작업은 확인 문구만 요구">🔓 로그인 없음 (local admin)</span>`; }
      else { ub.innerHTML = `<span title="via ${esc(u.via || '')}">👤 ${esc(u.name || '?')} <small>(${esc(u.role || '')})</small></span> <a href="#" id="btn-logout" title="로그아웃">⎋</a>`; const lo = $('#btn-logout'); if (lo) lo.onclick = async (e) => { e.preventDefault(); await api('/api/auth/logout', {}); location.href = '/login'; }; }
      document.body.dataset.role = u.role || 'admin';
    }
    const al = j.alerts || [];
    const ab = $('#alert-badge'); ab.classList.toggle('hidden', !al.length); ab.classList.toggle('alert', !!al.length); ab.textContent = al.length ? `⚠ alerts ${al.length}` : ''; ab.title = al.map((a) => `[${a.level}] ${a.check}: ${a.detail}`).join('\n'); ab.onclick = () => { switchGroup('corpus'); switchTab('build'); };
    if ($('#corpus-dirs')) $('#corpus-dirs').textContent = (j.settings.corpus_dirs || []).join('  |  ');
    if ($('#config-json') && !$('#config-json').value) $('#config-json').value = JSON.stringify(j.settings, null, 2);
    if (!document.body.dataset.togglesInit) {
      buildSidebar(j); setTogglesFrom(j.settings.toggles); document.body.dataset.togglesInit = '1';
      const cat = (p.catalog && p.catalog.llm) || {}; $('#dl-llm-models').innerHTML = [].concat(cat.anthropic || [], cat.openai || [], cat.ollama || []).filter((m) => !String(m).startsWith('(')).map((m) => `<option value="${esc(m)}">`).join('');
      const tp = $('#tr-preset'); if (tp) tp.innerHTML = '<option value="">(프리셋 없음)</option>' + (j.presets || []).map((x) => `<option>${esc(x)}</option>`).join('');
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
  function fmtS(s) { s = Math.round(s || 0); return s >= 60 ? Math.floor(s / 60) + 'm ' + (s % 60) + 's' : s + 's'; }
  function renderLive(el, live, extra) {
    if (!el) return;
    if (!live || !live.status) { el.classList.add('hidden'); el.innerHTML = ''; return; }
    el.classList.remove('hidden');
    const running = live.status === 'running';
    const path = (live.path_labels || []).join(' › ');
    const llm = live.llm || {};
    const pct = live.pct == null ? null : Math.max(0, Math.min(100, live.pct));
    const bar = pct == null
      ? (running ? `<div class="progress indet"><i></i><span>${esc(live.detail || '')}</span></div>` : '')
      : `<div class="progress"><i style="width:${pct}%"></i><span>${live.done}/${live.total} · ${fmt(pct, 0)}%${live.detail ? ' · ' + esc(live.detail) : ''}</span></div>`;
    const llmTxt = llm.active ? `<span class="pill warn">LLM 응답 대기 ${esc(llm.provider || '')}/${esc(llm.model || '')} · ${fmtS(llm.elapsed_s)}</span>` : (llm.calls ? `<span class="pill">LLM 호출 ${llm.calls}회 · ${fmtS((llm.ms_total || 0) / 1000)}</span>` : '');
    const head = running
      ? `<span class="spin"></span><b>${esc(path || live.stage_label || live.label || '진행 중')}</b> <span class="muted small">단계 ${fmtS(live.stage_elapsed_s)} · 전체 ${fmtS(live.elapsed_s)}</span> ${llmTxt}`
      : `<b>${live.status === 'done' ? '✔ 완료' : '✖ ' + esc(live.status)}</b> <span class="muted small">${fmtS(live.elapsed_s)}${live.detail ? ' · ' + esc(live.detail) : ''}</span>`;
    // 로그: 기본은 최근 3줄, 클릭하면 누적 전체(펼침 상태는 폴링으로 다시 그려도 유지)
    const log = live.log || [];
    const expanded = el.dataset.expanded === '1';
    const shown = expanded ? log : log.slice(-3);
    const logHtml = log.length ? `<div class="live-log muted small ${expanded ? 'expanded' : ''}" title="클릭: ${expanded ? '접기' : '누적 로그 전체 보기'}">${esc(shown.join('\n'))}<span class="log-toggle">${expanded ? '▲ 접기' : (log.length > 3 ? `▼ 전체 보기 (${log.length}줄)` : '')}</span></div>` : '';
    el.innerHTML = `<div class="live-head">${head}${extra || ''}</div>${running ? bar : ''}${logHtml}`;
    const lg = $('.live-log', el);
    if (lg) {
      lg.onclick = () => { el.dataset.expanded = expanded ? '0' : '1'; renderLive(el, live, extra); if (!expanded) { const n = $('.live-log', el); if (n) n.scrollTop = n.scrollHeight; } };
      if (expanded) lg.scrollTop = lg.scrollHeight;
    }
  }
  // 요청 단위 progress_token 폴링 (Ask 처럼 동기 API 를 기다리는 동안 사용). stop() 을 돌려준다.
  function watchProgress(token, el, interval) {
    let busy = false, stopped = false;
    const tick = async () => {
      if (busy || stopped) return; busy = true;
      try { const r = await fetch('/api/progress/' + token); const live = await r.json(); if (!stopped) renderLive(el, live.status === 'unknown' ? { status: 'running', label: '요청 접수 대기…' } : live); } catch (e) { /* 서버 재시작 등 — 조용히 */ }
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
  async function pollJob(id, logEl, onDone, onTick) {
    const liveEl = liveElFor(logEl);
    let busy = false;   // 응답이 늦어도 폴링이 겹쳐 쌓이지 않게
    const t = setInterval(async () => {
      if (busy) return; busy = true;
      let j;
      try { j = await api('/api/jobs/' + id); } catch (e) { busy = false; return; }
      busy = false;
      if (!j || j.error === 'no such job') { clearInterval(t); renderLive(liveEl, null); if (logEl) logEl.textContent = 'job not found: ' + id; return; }
      if (logEl) logEl.textContent = (j.log || []).join('\n') + (j.status === 'running' ? '\n… (진행 중 — 위 진행 표시가 멈춰 있으면 LLM/임베딩 응답 대기 중입니다. elapsed ' + fmtS(j.elapsed_s) + ')' : '\n[' + j.status + ' · ' + fmtS(j.elapsed_s) + ']' + (j.error ? '\n' + j.error : ''));
      renderLive(liveEl, j.live && j.live.status ? j.live : { status: j.status, label: j.kind, elapsed_s: j.elapsed_s });
      if (onTick) onTick(j);
      if (j.status !== 'running') { clearInterval(t); setTimeout(() => renderLive(liveEl, null), 4000); onDone(j); loadStatus(); }
    }, 700);
  }

  // ---------------- navigation ----------------
  function initNav() {
    $$('.groups button').forEach((b) => b.onclick = () => {
      $$('.groups button').forEach((x) => x.classList.remove('active')); b.classList.add('active');
      $$('.tabs').forEach((n) => n.classList.toggle('hidden', n.dataset.group !== b.dataset.group));
      const nav = $(`.tabs[data-group="${b.dataset.group}"]`); const act = $('button.active', nav) || $('button', nav); if (act) act.click();
    });
    $$('.tabs button').forEach((b) => b.onclick = () => {
      const nav = b.parentElement;
      $$('button', nav).forEach((x) => x.classList.remove('active')); b.classList.add('active');
      $$('.tabs button').forEach((x) => { if (x.parentElement !== nav) x.classList.remove('active'); });
      $$('.tab').forEach((t) => t.classList.remove('active')); const sec = $('#tab-' + b.dataset.tab); if (sec) sec.classList.add('active');
      updateCli();
      if (loaders[b.dataset.tab]) loaders[b.dataset.tab]();
    });
  }
  // ---------------- theme ----------------
  async function initTheme() {
    let reg = { themes: [{ key: 'light', title: 'Light' }, { key: 'dark', title: 'Dark' }] };
    try { reg = await api('/api/themes'); } catch (e) { /* ignore */ }
    const sel = $('#theme-select');
    sel.innerHTML = '<option value="auto">auto (시스템)</option>' + (reg.themes || []).map((t) => `<option value="${esc(t.key)}">${esc(t.title)}</option>`).join('');
    let saved = null; try { saved = localStorage.getItem('llmwiki.theme'); } catch (e) { /* ignore */ }
    sel.value = saved || reg.default || 'auto';   // themes.json 의 default (dark) — 사용자가 고르면 localStorage 값이 우선
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
    ['#ov-llm', '#ov-embed', '#ov-k', '#ov-debug', '#ov-answer-model', '#ov-rerank-model'].forEach((s) => $(s).addEventListener('change', updateCli));
    // 모델 입력(datalist): 값이 있으면 브라우저가 목록을 그 값으로 필터해 다시 고를 수 없으므로 × 로 비우거나, 포커스 시 전체 선택해 바로 덮어쓰게 한다
    $$('[data-clear]').forEach((b) => b.onclick = () => { const i = $('#' + b.dataset.clear); i.value = ''; i.dispatchEvent(new Event('change', { bubbles: true })); i.focus(); });
    ['#ov-answer-model', '#ov-rerank-model'].forEach((s) => { const i = $(s); i.addEventListener('focus', () => i.select()); i.addEventListener('dblclick', () => { i.value = ''; i.dispatchEvent(new Event('change', { bubbles: true })); }); });
    $('#btn-reset-toggles').onclick = () => setTogglesFrom(STATE.settings.toggles);
    $('#btn-save-config').onclick = async () => { const ov = overrides(); const j = await api('/api/config', { settings: ov }); STATE.settings = j.settings; toast('config.json 저장됨'); loadStatus(); };
    loadStatus().then(() => { updateCli(); (LW.onReady || []).forEach((f) => f()); });
  }
  return { $, $$, esc, fmt, fmtK, ts, dt, PALETTE, STAGE_COLOR, STATE, loaders, toast, api, switchTab, switchGroup, overrides, presetNames, applyPresets, cliEquiv, updateCli, setTogglesFrom, loadStatus, metaBlock, renderTrace, flatten, renderStageTable, pollJob, renderLive, watchProgress, fmtS, stepUp, boot, onReady: [] };
})();
