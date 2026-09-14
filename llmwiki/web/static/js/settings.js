/* Settings — 모델/프로바이더/엔드포인트/agents, 프리셋, 튜닝, 질의 규칙 사전, pin, 프롬프트, config. */
(function (LW) {
  'use strict';
  const { $, $$, esc, fmt, fmtK, api, toast, STATE, setTogglesFrom, loadStatus, loaders } = LW;

  // ---------------- MODELS ----------------
  function sel(id, opts, cur, allowEmpty) { return `<select id="${id}">${allowEmpty ? '<option value="">(상속)</option>' : ''}${opts.map((o) => `<option ${o === cur ? 'selected' : ''}>${esc(o)}</option>`).join('')}</select>`; }
  const PROVIDERS = ['auto', 'anthropic', 'openai', 'ollama', 'headless:opencode', 'headless:claude', 'headless:codex', 'headless:mock', 'mock', 'none'];
  async function loadModels() {
    const j = await api('/api/models'); const p = j.providers, s = j.settings, cat = p.catalog; STATE.providers = p;
    const embModels = [].concat.apply([], Object.keys(cat.embed).map((k) => cat.embed[k])).filter((m) => !m.startsWith('('));
    $('#embed-form').innerHTML = `<label>provider ${sel('m-embed-provider', ['auto', 'hash', 'voyage', 'openai', 'ollama', 'st'], s.embed_provider)}</label>` +
      `<label>model <input id="m-embed-model" list="dl-embed" value="${esc(s.embed_model)}" placeholder="hash: 비움 · voyage-3.5 · bge-m3 · nomic-embed-text"><datalist id="dl-embed">${embModels.map((m) => `<option value="${esc(m)}">`).join('')}</datalist></label>` +
      `<label>embed_dim (hash) <input id="m-embed-dim" type="number" value="${s.embed_dim}" style="width:90px" title="${esc(STATE.settingHelp.embed_dim || '')}"> <small>모든 차원 지원 · 변경 시 전체 리빌드</small></label>` +
      `<label>embed_store_dtype ${sel('m-embed-dtype', ['float32', 'float16'], s.embed_store_dtype)} <small>float16 = 저장/행렬 메모리 절반</small></label>` +
      `<label>embed_batch <input id="m-embed-batch" type="number" value="${s.embed_batch}" style="width:70px"> max <input id="m-embed-batch-max" type="number" value="${s.embed_batch_max}" style="width:70px"></label>` +
      `<div class="muted small">현재: <b>${p.embedder.name}</b> model=${esc(p.embedder.model)} dim=${p.embedder.dim} available=${p.embedder.available} · auto = VOYAGE_API_KEY 있으면 voyage → Ollama 에 bge-m3/nomic 있으면 ollama → hash.</div>`;
    $('#llm-form').innerHTML = `<label>llm_provider ${sel('m-llm-provider', PROVIDERS, s.llm_provider)}</label>` +
      `<label>llm_model <input id="m-llm-model" list="dl-llm-models" value="${esc(s.llm_model)}"></label>` +
      `<label>llm_effort ${sel('m-llm-effort', cat.effort, s.llm_effort)} · answer_effort ${sel('m-answer-effort', cat.effort, s.answer_effort)}</label>` +
      `<label>ollama_url <input id="m-ollama-url" value="${esc(s.ollama_url)}"></label><label>ollama_model <input id="m-ollama-model" value="${esc(s.ollama_model)}"></label>` +
      `<label><input type="checkbox" id="m-fallbacks" ${s.llm_fallbacks ? 'checked' : ''}> llm_fallbacks (Anthropic server-side refusal fallback)</label>`;
    $('#endpoint-form').innerHTML = `<label>openai_base_url <input id="m-openai-url" value="${esc(s.openai_base_url)}" placeholder="https://gateway.corp/v1"> <small>키: .env OPENAI_API_KEY 또는 LLM_API_KEY (${p.openai && p.openai.key ? '설정됨' : '없음'}) · 사내 PAT 게이트웨이도 여기</small></label>` +
      `<label>openai_api_key_header ${sel('m-openai-key-header', ['authorization', 'api-key', 'x-api-key'], s.openai_api_key_header || 'authorization')} <small>authorization = "Bearer &lt;PAT&gt;", 그 외는 키 값 그대로</small></label>` +
      `<label>openai_extra_headers <input id="m-openai-extra" value="${esc(JSON.stringify(s.openai_extra_headers || {}))}" placeholder='{"X-Tenant":"modem"}' style="width:260px"> <small>JSON</small></label>` +
      `<label>openai_embed_base_url <input id="m-openai-embed-url" value="${esc(s.openai_embed_base_url || '')}" placeholder="(비우면 openai_base_url)"> openai_embed_model <input id="m-openai-embed" value="${esc(s.openai_embed_model)}" placeholder="text-embedding-3-small / bge-m3"></label>` +
      `<label>anthropic_base_url <input id="m-anthropic-url" value="${esc(s.anthropic_base_url || '')}" placeholder="(비우면 api.anthropic.com) https://gateway.corp"> <small>키: ANTHROPIC_API_KEY(x-api-key) 또는 ANTHROPIC_AUTH_TOKEN(Bearer PAT)</small></label>` +
      `<label>llm_timeout <input id="m-llm-timeout" type="number" value="${s.llm_timeout || 600}" style="width:80px"> <small>초/호출 — 멈춘 서버를 빨리 감지하려면 줄임</small></label>` +
      `<label>rerank_url <input id="m-rerank-url" value="${esc(s.rerank_url)}" placeholder="http://host:8000/v1/rerank"> <small>비우면 api 리랭크 비활성</small></label>` +
      `<label>rerank_api_model <input id="m-rerank-model" value="${esc(s.rerank_api_model || '')}" placeholder="BAAI/bge-reranker-v2-m3"> style ${sel('m-rerank-style', ['cohere', 'voyage'], s.rerank_api_style)} <small>rerank_method(튜닝)=auto 면 URL 있을 때 api 우선</small></label>`;
    $('#models-note').innerHTML = `Python ${p.python} / SQLite ${p.sqlite}. headless 에이전트: ${Object.keys(p.agents || {}).map(esc).join(', ')} (agents.json).`;
    const llmModels = [].concat(cat.llm.anthropic, cat.llm.openai || [], cat.llm.ollama).filter((m) => !String(m).startsWith('('));
    $('#roles-table').innerHTML = '<table class="roles"><tr><th>역할</th><th>용도</th><th>provider</th><th>model</th><th>effort</th><th>실제 인스턴스</th><th>상태</th><th></th></tr>' + j.roles.map((role) => {
      const r = p.roles[role]; const cfg = (s.llm_roles || {})[role] || {};
      const inh = `(상속: 전역 ${esc(s.llm_provider)})`;
      return `<tr data-role="${role}"><td><b>${role}</b></td><td class="muted small">${esc(cat.roles[role] || '')}</td><td><input id="r-${role}-provider" list="dl-providers" value="${esc(cfg.provider || '')}" placeholder="${inh}" title="비우면 전역 llm_provider(${esc(s.llm_provider)})를 상속" style="width:150px"></td><td><input id="r-${role}-model" list="dl-llm-models" value="${esc(cfg.model || '')}" placeholder="(상속: ${esc(s.llm_model)})" title="비우면 전역 llm_model(${esc(s.llm_model)})을 상속"></td><td>${sel('r-' + role + '-effort', cat.effort, cfg.effort || '', true)}</td><td class="small">${esc(r.name)}/${esc(r.model)}<br><span class="muted">effort=${r.configured.effort}</span></td><td>${r.available ? '<span class="ok">available</span>' : '<span class="bad" title="' + esc(r.reason || '') + '">unavailable</span>'}${r.available ? '' : '<br><span class="reason small">' + esc(r.reason || '') + '</span>'}<br><span class="muted small">calls ${r.stats.calls || 0} · tok ${fmtK((r.stats.input_tokens || 0) + (r.stats.output_tokens || 0))}</span></td><td><button class="mini secondary" data-test="${role}" title="이 행에 입력한(아직 저장 안 한) provider/model 로 연결 테스트">테스트</button></td></tr>`;
    }).join('') + `</table><datalist id="dl-providers">${PROVIDERS.map((x) => `<option value="${x}">`).join('')}</datalist>` +
      `<div class="muted small" style="margin-top:6px"><b>상속</b> = 비워 두면 위 "전역 LLM 기본값"(llm_provider / llm_model / llm_effort)을 그대로 씀. <b>auto</b> = ANTHROPIC_API_KEY 가 있으면 anthropic → 없으면 Ollama(ollama_url 에 ollama_model 이 받아져 있을 때) → 둘 다 없으면 none(추출식 답변). openai / headless 는 auto 가 고르지 않으므로 provider 에 직접 적는다. "테스트" 는 저장 전 입력값으로도 동작하며, 실제 적용은 "저장 &amp; 프로바이더 재로드".</div>`;
    $$('#roles-table [data-test]').forEach((b) => b.onclick = async () => {
      b.disabled = true;
      // 저장하지 않은 폼 값(전역 + 이 역할)을 요청 단위 오버라이드로 보내 실제 연결을 확인한다
      const role = b.dataset.test, ov = { llm_provider: $('#m-llm-provider').value, llm_model: $('#m-llm-model').value.trim(), ollama_url: $('#m-ollama-url').value.trim(), ollama_model: $('#m-ollama-model').value.trim(), openai_base_url: $('#m-openai-url').value.trim(), openai_api_key_header: $('#m-openai-key-header').value, anthropic_base_url: $('#m-anthropic-url').value.trim() };
      const pv = $('#r-' + role + '-provider').value.trim(), m = $('#r-' + role + '-model').value.trim();
      ov[role + '_provider'] = pv; ov[role + '_model'] = m;
      const r = await api('/api/models/test', { which: [role], overrides: ov }); b.disabled = false; renderTest(r);
    });
    $('#dl-llm-models').innerHTML = llmModels.map((m) => `<option value="${esc(m)}">`).join('');
    const ag = await api('/api/agents'); $('#agents-json').value = JSON.stringify(ag.agents, null, 2);
  }
  function renderTest(r) {
    $('#models-test').innerHTML = '<table><tr><th>대상</th><th>provider/model</th><th>ok</th><th>ms</th><th>detail</th></tr>' + Object.keys(r).map((k) => { const x = r[k]; return `<tr><td><b>${k}</b></td><td>${esc(x.provider || x.url || '')}/${esc(x.model)}${x.dim ? ' d=' + x.dim : ''}</td><td>${x.ok ? '<span class="ok">✔</span>' : '<span class="bad">✘</span>'}</td><td class="num">${fmt(x.ms, 0)}</td><td class="small">${esc(x.detail || '')}${x.models ? '<br><span class="muted">models: ' + esc(x.models.slice(0, 12).join(', ')) + '</span>' : ''}${'live_ok' in x ? '<br><b>실제 호출:</b> ' + (x.live_ok ? '<span class="ok">✔</span>' : '<span class="bad">✘</span>') + ' ' + fmt(x.live_ms, 0) + 'ms ' + esc(x.live_detail || '') : ''}</td></tr>`; }).join('') + '</table>';
  }
  function modelsSettings() {
    const st = { embed_provider: $('#m-embed-provider').value, embed_model: $('#m-embed-model').value.trim(), embed_dim: parseInt($('#m-embed-dim').value, 10), embed_store_dtype: $('#m-embed-dtype').value, embed_batch: parseInt($('#m-embed-batch').value, 10), embed_batch_max: parseInt($('#m-embed-batch-max').value, 10),
      llm_provider: $('#m-llm-provider').value, llm_model: $('#m-llm-model').value.trim(), llm_effort: $('#m-llm-effort').value, answer_effort: $('#m-answer-effort').value,
      ollama_url: $('#m-ollama-url').value.trim(), ollama_model: $('#m-ollama-model').value.trim(), llm_fallbacks: $('#m-fallbacks').checked,
      openai_base_url: $('#m-openai-url').value.trim(), openai_api_key_header: $('#m-openai-key-header').value, openai_embed_base_url: $('#m-openai-embed-url').value.trim(), openai_embed_model: $('#m-openai-embed').value.trim(),
      anthropic_base_url: $('#m-anthropic-url').value.trim(), llm_timeout: parseInt($('#m-llm-timeout').value, 10) || 600,
      rerank_url: $('#m-rerank-url').value.trim(), rerank_api_model: $('#m-rerank-model').value.trim(), rerank_api_style: $('#m-rerank-style').value, llm_roles: {} };
    try { st.openai_extra_headers = JSON.parse($('#m-openai-extra').value.trim() || '{}'); } catch (e) { toast('openai_extra_headers JSON 오류 — 무시'); }
    STATE.roles.forEach((role) => { const c = {}; const pv = $('#r-' + role + '-provider').value.trim(), m = $('#r-' + role + '-model').value.trim(), e = $('#r-' + role + '-effort').value; if (pv) c.provider = pv; if (m) c.model = m; if (e) c.effort = e; if (Object.keys(c).length) st.llm_roles[role] = c; });
    return st;
  }
  $('#btn-models-save').onclick = async () => { const j = await api('/api/models/set', { settings: modelsSettings() }); $('#models-msg').textContent = '저장됨 · answer=' + j.providers.roles.answer.name + '/' + j.providers.roles.answer.model + ' · embed=' + j.providers.embedder.name; loadModels(); loadStatus(); };
  $('#btn-models-test').onclick = async () => { $('#models-test').textContent = '테스트 중…'; renderTest(await api('/api/models/test', { overrides: modelsSettings() })); };
  if ($('#btn-models-test-live')) $('#btn-models-test-live').onclick = async () => { $('#models-test').textContent = '실제 호출 테스트 중… (역할별 provider/model 당 1회, 수십 초 걸릴 수 있음)'; renderTest(await api('/api/models/test', { overrides: modelsSettings(), live: true })); };
  $('#btn-models-reload').onclick = loadModels;
  $('#btn-agents-save').onclick = async () => { let a; try { a = JSON.parse($('#agents-json').value); } catch (e) { toast('JSON 오류'); return; } await api('/api/agents', { agents: a }); toast('agents.json 저장됨'); };
  loaders.models = loadModels;

  // ---------------- SECURITY · USERS · SNAPSHOTS · AUDIT ----------------
  async function loadSecurity() {
    const me = await api('/api/auth/me'); const u = me.user || {};
    const admin = me.mode === 'off' || u.role === 'admin';
    $('#sec-summary').innerHTML = `<div class="stat"><b>${esc(me.mode)}</b>auth mode</div><div class="stat"><b>${me.local ? 'on' : 'off'}</b>로컬 로그인</div><div class="stat"><b>${me.sso ? esc(me.sso_type) : 'off'}</b>SSO</div><div class="stat"><b>${esc(u.name || 'local')}</b>${esc(u.role || 'admin')} (${esc(u.via || 'off')})</div><div class="stat"><b>${esc(me.confirm_phrase)}</b>파괴적 작업 확인 문구${me.require_reauth ? ' + 비밀번호' : ''}</div>`;
    if (!admin) { $('#sec-users').innerHTML = '<div class="muted small">사용자 목록은 admin 만 볼 수 있습니다.</div>'; $('#sec-snapshots').innerHTML = ''; $('#sec-audit').innerHTML = ''; return; }
    const [us, sec, sn, au] = await Promise.all([api('/api/auth/users'), api('/api/security'), api('/api/snapshot'), api('/api/audit?n=60')]);
    $('#sec-path').textContent = sec.path || '';
    $('#sec-users').innerHTML = '<table><tr><th>id</th><th>역할</th><th>표시</th><th>비밀번호</th><th></th></tr>' + (us.users || []).map((x) => `<tr><td><b>${esc(x.name)}</b></td><td><select data-role-of="${esc(x.name)}"><option ${x.role === 'viewer' ? 'selected' : ''}>viewer</option><option ${x.role === 'operator' ? 'selected' : ''}>operator</option><option ${x.role === 'admin' ? 'selected' : ''}>admin</option></select></td><td>${esc(x.display || '')}</td><td>${x.has_password ? '있음' : '<span class="muted">없음 (SSO)</span>'}</td><td><button class="mini secondary" data-pw-of="${esc(x.name)}">비밀번호</button> <button class="mini danger" data-del-of="${esc(x.name)}">삭제</button></td></tr>`).join('') + '</table>' + (!(us.users || []).length ? '<div class="muted small">사용자 없음 — 아래에서 admin 을 먼저 추가하세요 (또는 CLI: users add &lt;id&gt; --role admin)</div>' : '');
    $$('#sec-users [data-role-of]').forEach((s) => s.onchange = async () => { const r = await api('/api/auth/users', { action: 'set_role', name: s.dataset.roleOf, role: s.value }); if (r.ok) toast('역할 변경: ' + s.dataset.roleOf + ' → ' + s.value); loadSecurity(); });
    $$('#sec-users [data-pw-of]').forEach((b) => b.onclick = async () => { const pw = prompt(b.dataset.pwOf + ' 의 새 비밀번호'); if (!pw) return; const r = await api('/api/auth/users', { action: 'set_password', name: b.dataset.pwOf, password: pw }); if (r.ok) toast('비밀번호 변경됨'); });
    $$('#sec-users [data-del-of]').forEach((b) => b.onclick = async () => { if (!confirm(b.dataset.delOf + ' 사용자를 삭제할까요?')) return; const r = await api('/api/auth/users', { action: 'remove', name: b.dataset.delOf }); if (r.ok) toast('삭제됨'); loadSecurity(); });
    $('#sec-snapshots').innerHTML = '<table><tr><th>이름</th><th>tag</th><th>크기</th><th>내용</th><th></th></tr>' + (sn.snapshots || []).map((x) => `<tr><td class="mono small">${esc(x.name)}</td><td>${esc(x.tag || '')}</td><td class="num">${fmt((x.bytes || 0) / 1e6, 1)}MB</td><td class="small muted">${esc(JSON.stringify(x.counts || {}))} ${esc(x.reason || '')}</td><td>${x.has_db ? `<button class="mini danger" data-restore="${esc(x.name)}">복원</button>` : ''}</td></tr>`).join('') + '</table>' + (!(sn.snapshots || []).length ? '<div class="muted small">스냅샷 없음. 전체 초기화 시 자동 생성됩니다.</div>' : '');
    $$('#sec-snapshots [data-restore]').forEach((b) => b.onclick = async () => { const r = await api('/api/snapshot', { action: 'restore', name: b.dataset.restore }); if (r.restored) { toast('복원됨: ' + r.restored); loadStatus(); loadSecurity(); } });
    $('#sec-audit').innerHTML = '<table><tr><th>시각</th><th>사용자</th><th>역할</th><th>결과</th><th>등급</th><th>작업</th></tr>' + (au.rows || []).slice().reverse().map((r) => `<tr class="${r.ok ? '' : 'has-err'}"><td class="small">${esc(r.time)}</td><td>${esc(r.user || '')}</td><td class="small">${esc(r.role || '')}</td><td>${r.ok ? '<span class="ok">ok</span>' : '<span class="bad">DENY</span>'}</td><td class="small">${esc(r.level || '')}</td><td class="small">${esc(r.op || '')}${r.error ? ' <span class="errtxt">' + esc(r.error) + '</span>' : ''}</td></tr>`).join('') + '</table>';
  }
  $('#btn-sec-refresh').onclick = loadSecurity;
  $('#btn-sec-reload').onclick = async () => { const r = await api('/api/security', { action: 'reload' }); if (r.ok) toast('security.json 다시 읽음 (mode ' + r.mode + ')'); loadSecurity(); };
  $('#btn-snap-create').onclick = async () => { const tag = prompt('스냅샷 tag', 'manual'); if (tag === null) return; const r = await api('/api/snapshot', { action: 'create', tag }); if (r.name) toast('스냅샷 ' + r.name); loadSecurity(); };
  $('#btn-user-add').onclick = async () => { const name = $('#su-name').value.trim(); if (!name) return; const pw = $('#su-pass').value; const r = await api('/api/auth/users', { action: 'add', name, role: $('#su-role').value, password: pw || null }); if (r.ok) { toast('추가됨: ' + name); $('#su-name').value = ''; $('#su-pass').value = ''; } loadSecurity(); };
  $('#btn-pw-change').onclick = async () => { const r = await api('/api/auth/password', { old: $('#pw-old').value, new: $('#pw-new').value }); if (r.ok) { toast('비밀번호 변경됨'); $('#pw-old').value = ''; $('#pw-new').value = ''; } };
  loaders.security = loadSecurity;

  // ---------------- PRESETS ----------------
  async function loadPresets() {
    const j = await api('/api/presets'); const pr = j.presets;
    $('#presets-json').value = JSON.stringify(pr, null, 2);
    $('#preset-cards').innerHTML = '<div class="cards">' + Object.keys(pr).map((k) => `<div class="card"><h4>${esc(k)}</h4><div class="cdesc">${esc(pr[k].desc || '')}</div><div class="muted small">toggles ${Object.keys(pr[k].toggles || {}).length} · tuning ${Object.keys(pr[k].tuning || {}).length} · settings ${Object.keys(pr[k].settings || {}).length}</div><div class="row" style="margin:6px 0 0"><button class="mini secondary" data-diff="${esc(k)}">diff</button><button class="mini" data-apply="${esc(k)}">적용(저장)</button></div></div>`).join('') + '</div>';
    $$('#preset-cards [data-diff]').forEach((b) => b.onclick = async () => { const d = await api('/api/presets/diff?name=' + encodeURIComponent(b.dataset.diff)); $('#preset-diff').innerHTML = `<h3>${esc(b.dataset.diff)} 적용 시 변경</h3><table><tr><th>키</th><th>현재</th><th>프리셋</th></tr>` + d.map((r) => `<tr class="${r.changes ? 'changed' : ''}"><td class="mono small">${esc(r.key)}</td><td>${esc(JSON.stringify(r.current))}</td><td class="${r.changes ? 'ok' : 'muted'}">${esc(JSON.stringify(r.preset))}</td></tr>`).join('') + '</table>'; });
    $$('#preset-cards [data-apply]').forEach((b) => b.onclick = async () => { if (!confirm(`프리셋 ${b.dataset.apply} 를 config.json/tuning.json 에 저장 적용합니다.`)) return; const r = await api('/api/presets', { action: 'apply', names: b.dataset.apply, save: true }); toast(`적용: toggles ${Object.keys(r.toggles).length} tuning ${Object.keys(r.tuning).length} settings ${Object.keys(r.settings).length}`); STATE.settings = r.settings; setTogglesFrom(r.settings.toggles); loadStatus(); });
  }
  $('#btn-preset-refresh').onclick = loadPresets;
  $('#btn-preset-save').onclick = async () => { let pr; try { pr = JSON.parse($('#presets-json').value); } catch (e) { toast('JSON 오류'); return; } await api('/api/presets', { action: 'save', presets: pr }); toast('presets.json 저장됨'); loadStatus(); };
  loaders.presets = loadPresets;

  // ---------------- TUNING ----------------
  let TUN = { rows: [], stages: {}, edits: {} };
  async function loadTuning() {
    const j = await api('/api/tuning'); TUN.rows = j.tunables; TUN.stages = j.stages; TUN.edits = {};
    $('#tuning-path').textContent = `tuning.json: ${j.path} · 오버라이드 ${Object.keys(j.overrides).length}개 · config.json 항목은 저장 시 config.json 에 기록 · rebuild 표시 항목은 변경 후 전체 리빌드 필요`;
    const s = $('#tuning-stage'); const cur = s.value; s.innerHTML = '<option value="">전체</option>' + Object.keys(j.stages).map((k) => `<option value="${k}">${k} — ${esc(j.stages[k])}</option>`).join(''); s.value = cur;
    renderTuning();
  }
  function tuningInput(r) {
    const id = 'tn-' + r.key, v = r.value;
    if (r.type === 'bool') return `<select id="${id}" data-key="${r.key}"><option value="true" ${v ? 'selected' : ''}>true</option><option value="false" ${!v ? 'selected' : ''}>false</option></select>`;
    if (r.type === 'choice') return `<select id="${id}" data-key="${r.key}">${(r.choices || []).map((c) => `<option ${String(c) === String(v) ? 'selected' : ''}>${esc(c)}</option>`).join('')}</select>`;
    if (r.type === 'str') return `<input id="${id}" data-key="${r.key}" value="${esc(v)}" style="width:220px">`;
    return `<input id="${id}" data-key="${r.key}" type="number" value="${v}" ${r.min != null ? 'min="' + r.min + '"' : ''} ${r.max != null ? 'max="' + r.max + '"' : ''} step="${r.type === 'int' ? 1 : 'any'}">`;
  }
  function renderTuning() {
    const st = $('#tuning-stage').value, onlyChanged = $('#tuning-changed').checked;
    const rows = TUN.rows.filter((r) => (!st || r.stage === st) && (!onlyChanged || r.overridden));
    let cur = null, html = '<table class="tuning"><tr><th>키</th><th>현재값</th><th>기본</th><th>범위</th><th>설명 / impact / 예시</th><th>파일</th><th></th></tr>';
    rows.forEach((r) => {
      if (r.stage !== cur) { cur = r.stage; html += `<tr class="stage-head"><td colspan="7">${esc(cur)} — ${esc(TUN.stages[cur] || '')}</td></tr>`; }
      const rng = r.min != null || r.max != null ? `${r.min ?? ''}~${r.max ?? ''}` : (r.choices || []).join(' | ');
      html += `<tr class="${r.overridden ? 'changed' : ''}" data-key="${r.key}"><td class="k">${esc(r.key)}${r.rebuild ? ' <span class="chip" title="변경 후 전체 리빌드 필요">rebuild</span>' : ''}</td><td>${tuningInput(r)}</td><td class="muted">${esc(String(r.default))}</td><td class="muted small">${esc(rng)}</td><td class="d small">${esc(r.desc)}${r.impact ? `<div class="imp">impact: ${esc(r.impact)}</div>` : ''}${r.example ? `<div class="muted">예: ${esc(r.example)}</div>` : ''}</td><td class="muted small">${r.source === 'config' ? 'config.json' : 'tuning.json'}</td><td>${r.overridden && r.source !== 'config' ? `<button class="mini secondary" data-reset="${r.key}">초기화</button>` : ''}</td></tr>`;
    });
    $('#tuning-table').innerHTML = html + '</table>';
    $$('#tuning-table [data-key]').forEach((i) => { if (i.tagName === 'INPUT' || i.tagName === 'SELECT') i.addEventListener('change', () => { TUN.edits[i.dataset.key] = i.value; i.closest('tr').classList.add('changed'); }); });
    $$('#tuning-table [data-reset]').forEach((b) => b.onclick = async () => { await api('/api/tuning', { action: 'reset', key: b.dataset.reset }); toast(b.dataset.reset + ' 초기화'); loadTuning(); });
  }
  $('#tuning-stage').onchange = renderTuning; $('#tuning-changed').onchange = renderTuning;
  $('#btn-tuning-reload').onclick = loadTuning;
  $('#btn-tuning-save').onclick = async () => {
    if (!Object.keys(TUN.edits).length) { toast('변경된 값이 없습니다'); return; }
    const j = await api('/api/tuning', { action: 'set', values: TUN.edits });
    const errs = Object.keys(j.errors || {});
    $('#tuning-msg').textContent = errs.length ? '오류: ' + errs.map((k) => k + ': ' + j.errors[k]).join('; ') : `저장됨 (${Object.keys(TUN.edits).length}개). rebuild 항목을 바꿨다면 Corpus › 빌드에서 전체 리빌드하세요.`;
    if (!errs.length) toast('튜닝 저장됨'); loadTuning(); loadStatus();
  };
  $('#btn-tuning-reset').onclick = async () => { if (!confirm('tuning.json 의 모든 오버라이드를 지웁니다.')) return; await api('/api/tuning', { action: 'reset' }); loadTuning(); };
  loaders.tuning = loadTuning;
  LW.renderTuning = renderTuning; LW.loadTuning = loadTuning;

  // ---------------- QUERY RULES ----------------
  async function loadQRules() { const j = await api('/api/query_rules'); $('#qrules-json').value = JSON.stringify(j.rules, null, 2); $('#qr-msg').textContent = `${j.path} · ${JSON.stringify(j.stats)}`; }
  $('#btn-qr-add').onclick = async () => { const vals = $('#qr-values').value.split(',').map((x) => x.trim()).filter(Boolean); const r = await api('/api/query_rules', { action: 'add', type: $('#qr-type').value, term: $('#qr-term').value.trim(), values: vals }); toast('추가: ' + JSON.stringify(r.values || r)); loadQRules(); };
  $('#btn-qr-test').onclick = async () => { const r = await api('/api/query_rules/test?q=' + encodeURIComponent($('#qr-test').value)); const el = $('#qr-test-out'); el.classList.remove('hidden'); el.textContent = JSON.stringify(r, null, 1); };
  $('#btn-qr-save').onclick = async () => { let r; try { r = JSON.parse($('#qrules-json').value); } catch (e) { toast('JSON 오류'); return; } const j = await api('/api/query_rules', { action: 'save', rules: r }); toast('저장됨 ' + JSON.stringify(j.stats)); };
  loaders.qrules = loadQRules;

  // ---------------- PINS ----------------
  async function loadPins() {
    const rows = await api('/api/pins');
    $('#pin-list').innerHTML = '<table><tr><th>id</th><th>doc / chunk</th><th>조건</th><th>weight</th><th>strength</th><th>hits</th><th>note</th><th></th></tr>' + rows.map((p) => `<tr><td>${esc(p.id)}</td><td class="small">${esc(p.doc || p.chunk || '')}</td><td class="small mono">${esc(JSON.stringify(p.when))}</td><td class="num">${fmt(p.weight, 2)}</td><td class="num">${fmt(p.strength, 2)}</td><td class="num">${p.hits || 0}</td><td class="small muted">${esc(p.note || '')}</td><td><button class="mini secondary" data-rm="${esc(p.id)}">삭제</button></td></tr>`).join('') + '</table>' + (rows.length ? '' : '<div class="muted">pin 없음</div>');
    $$('#pin-list [data-rm]').forEach((b) => b.onclick = async () => { await api('/api/pins', { action: 'remove', id: b.dataset.rm }); loadPins(); });
  }
  $('#btn-pin-add').onclick = async () => { const kw = $('#pin-keywords').value.split(',').map((x) => x.trim()).filter(Boolean); const r = await api('/api/pins', { action: 'add', doc: $('#pin-doc').value.trim() || null, chunk: $('#pin-chunk').value.trim() || null, keywords: kw.length ? kw : null, query: $('#pin-query').value.trim() || null, always: $('#pin-always').checked }); toast('pin ' + (r.id || JSON.stringify(r))); loadPins(); };
  loaders.pins = loadPins;

  // ---------------- PROMPTS ----------------
  let PROMPTS = [];
  async function loadPrompts() {
    PROMPTS = await api('/api/prompts');
    $('#prompt-list').innerHTML = PROMPTS.map((p) => `<div data-p="${esc(p.name)}" class="${p.name === $('#prompt-name').textContent ? 'sel' : ''}">${esc(p.name)} <small class="muted">${p.is_default ? '' : '(edited)'}</small></div>`).join('');
    $$('#prompt-list div').forEach((d) => d.onclick = () => openPrompt(d.dataset.p));
    if (!$('#prompt-content').value) openPrompt('answer_guide');
  }
  async function openPrompt(name) { const j = await api('/api/prompts?name=' + encodeURIComponent(name)); $('#prompt-name').textContent = name; $('#prompt-content').value = j.content || ''; $$('#prompt-list div').forEach((d) => d.classList.toggle('sel', d.dataset.p === name)); }
  $('#btn-prompt-save').onclick = async () => { const j = await api('/api/prompts', { name: $('#prompt-name').textContent, content: $('#prompt-content').value }); $('#prompt-msg').textContent = '저장됨: ' + j.path; loadPrompts(); };
  $('#btn-prompt-reset').onclick = async () => { if (!confirm('기본값으로 되돌립니다.')) return; const j = await api('/api/prompts', { action: 'reset', name: $('#prompt-name').textContent }); $('#prompt-content').value = j.content; loadPrompts(); };
  loaders.prompts = loadPrompts;

  // ---------------- CONFIG ----------------
  async function loadConfig() { const j = await api('/api/status'); $('#config-json').value = JSON.stringify(j.settings, null, 2); $('#config-paths').textContent = '파일 위치: ' + Object.keys(j.paths || {}).map((k) => k + '=' + j.paths[k]).join(' · '); }
  $('#btn-config-reload').onclick = loadConfig;
  $('#btn-config-save').onclick = async () => {
    let s; try { s = JSON.parse($('#config-json').value); } catch (e) { toast('JSON 오류'); return; }
    const flat = Object.assign({}, s, s.toggles || {}); delete flat.toggles; delete flat.LLM_ROLES;
    const j = await api('/api/config', { settings: flat }); STATE.settings = j.settings; setTogglesFrom(j.settings.toggles);
    $('#config-msg').textContent = '저장됨 · answer=' + j.providers.roles.answer.name + '/' + j.providers.roles.answer.model; loadStatus();
  };
  $('#btn-config-effective').onclick = async () => { const rows = await api('/api/config/effective'); const el = $('#config-effective'); el.classList.toggle('hidden'); el.innerHTML = '<table><tr><th>키</th><th>값</th><th>기본</th><th>출처</th><th>env</th><th>설명</th></tr>' + rows.map((r) => `<tr class="${r.source === 'env' ? 'changed' : ''}"><td class="mono small">${esc(r.key)}</td><td class="small">${esc(JSON.stringify(r.value)).slice(0, 60)}</td><td class="small muted">${esc(JSON.stringify(r.default)).slice(0, 40)}</td><td><span class="pill">${esc(r.source)}</span></td><td class="mono small muted">${esc(r.env)}</td><td class="small muted">${esc((r.help || '').slice(0, 80))}</td></tr>`).join('') + '</table>'; };
  loaders.config = loadConfig;
})(window.LW);
