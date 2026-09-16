/* Settings — 모델/프로바이더/엔드포인트/agents, 프리셋, 튜닝, 질의 규칙 사전, pin, 프롬프트, config. */
(function (LW) {
  'use strict';
  const { $, $$, esc, fmt, fmtK, api, toast, STATE, setTogglesFrom, loadStatus, loaders } = LW;

  // ---------------- MODELS ----------------
  function sel(id, opts, cur, allowEmpty) { return `<select id="${id}">${allowEmpty ? '<option value="">(상속)</option>' : ''}${opts.map((o) => `<option ${o === cur ? 'selected' : ''}>${esc(o)}</option>`).join('')}</select>`; }
  let PROVIDERS = ['auto', 'anthropic', 'openai', 'ollama', 'headless:opencode', 'headless:claude', 'headless:codex', 'headless:mock', 'mock', 'none'];
  let CATALOG = { models: [], embed: [], providers: PROVIDERS, embed_providers: [] };
  // 역할별 정책 편집 열. 비워 두면 전역값(또는 단계 기본값)을 상속한다.
  const POLICY_COLS = [['timeout_s', '타임아웃(초)'], ['retries', '재시도'], ['backoff_s', '대기(초)'],
                       ['budget_s', '총예산(초)'], ['max_tokens', '출력토큰']];
  // 모델 드롭다운: models.json 카탈로그(사람이 추가/삭제) + 현재 값 + '직접 입력'. 역할이 주어지면 그 역할용 모델만 (roles 가 빈 항목은 전 역할).
  function modelSelect(id, cur, role, allowInherit, placeholderText) {
    const list = (CATALOG.models || []).filter((m) => m.enabled !== false && (!role || !(m.roles || []).length || (m.roles || []).indexOf(role) >= 0));
    const ids = list.map((m) => m.id);
    let opts = (allowInherit ? `<option value="">(상속${placeholderText ? ': ' + esc(placeholderText) : ''})</option>` : '');
    opts += list.map((m) => `<option value="${esc(m.id)}"${m.id === cur ? ' selected' : ''}>${esc(m.id)} — ${esc(m.provider)}${m.label ? ' · ' + esc(m.label) : ''}</option>`).join('');
    if (cur && ids.indexOf(cur) < 0) opts += `<option value="${esc(cur)}" selected>${esc(cur)} — (카탈로그에 없음)</option>`;
    opts += '<option value="__custom__">직접 입력…</option>';
    return `<select id="${id}" data-model-select="1">${opts}</select>`;
  }
  function wireModelSelects(root) {
    $$('[data-model-select]', root || document).forEach((s2) => {
      if (s2.dataset.wired) return; s2.dataset.wired = '1';
      s2.addEventListener('change', () => {
        if (s2.value !== '__custom__') return;
        const v = (prompt('모델 id 를 직접 입력하세요 (카탈로그에 없어도 동작합니다; Settings › 카탈로그에 추가해 두면 목록에 나옵니다)', '') || '').trim();
        if (!v) { s2.value = ''; return; }
        const o = document.createElement('option'); o.value = v; o.textContent = v + ' — (직접 입력)'; s2.insertBefore(o, s2.lastElementChild); s2.value = v;
      });
    });
  }
  async function loadModels() {
    const j = await api('/api/models'); const p = j.providers, s = j.settings, cat = p.catalog; STATE.providers = p;
    CATALOG = j.catalog_models || (await api('/api/models/catalog')) || CATALOG;
    PROVIDERS = CATALOG.providers || PROVIDERS;
    STATE.rolePolicy = j.policy || {};
    STATE.roleAttrs = j.role_attrs || [];
    const embModels = [].concat.apply([], Object.keys(cat.embed).map((k) => cat.embed[k])).filter((m) => !m.startsWith('('));
    $('#embed-form').innerHTML = `<label>provider ${sel('m-embed-provider', ['auto', 'hash', 'voyage', 'openai', 'ollama', 'st'], s.embed_provider)}</label>` +
      `<label>model <input id="m-embed-model" list="dl-embed" value="${esc(s.embed_model)}" placeholder="hash: 비움 · voyage-3.5 · bge-m3 · nomic-embed-text"><datalist id="dl-embed">${embModels.map((m) => `<option value="${esc(m)}">`).join('')}</datalist></label>` +
      `<label>embed_dim (hash) <input id="m-embed-dim" type="number" value="${s.embed_dim}" style="width:90px" title="${esc(STATE.settingHelp.embed_dim || '')}"> <small>모든 차원 지원 · 변경 시 전체 리빌드</small></label>` +
      `<label>embed_store_dtype ${sel('m-embed-dtype', ['float32', 'float16'], s.embed_store_dtype)} <small>float16 = 저장/행렬 메모리 절반</small></label>` +
      `<label>embed_batch <input id="m-embed-batch" type="number" value="${s.embed_batch}" style="width:70px"> max <input id="m-embed-batch-max" type="number" value="${s.embed_batch_max}" style="width:70px"></label>` +
      `<div class="muted small">현재: <b>${p.embedder.name}</b> model=${esc(p.embedder.model)} dim=${p.embedder.dim} available=${p.embedder.available} · auto = VOYAGE_API_KEY 있으면 voyage → Ollama 에 bge-m3/nomic 있으면 ollama → hash.</div>`;
    $('#llm-form').innerHTML = `<label>llm_provider ${sel('m-llm-provider', PROVIDERS, s.llm_provider)}</label>` +
      `<label>llm_model ${modelSelect('m-llm-model', s.llm_model, '', false)}</label>` +
      `<label>llm_effort ${sel('m-llm-effort', cat.effort, s.llm_effort)} · answer_effort ${sel('m-answer-effort', cat.effort, s.answer_effort)}</label>` +
      `<label>ollama_url <input id="m-ollama-url" value="${esc(s.ollama_url)}"></label><label>ollama_model <input id="m-ollama-model" value="${esc(s.ollama_model)}"></label>` +
      `<label><input type="checkbox" id="m-fallbacks" ${s.llm_fallbacks ? 'checked' : ''}> llm_fallbacks (Anthropic server-side refusal fallback)</label>`;
    $('#endpoint-form').innerHTML = `<label>openai_base_url <input id="m-openai-url" value="${esc(s.openai_base_url)}" placeholder="https://gateway.corp/v1"> <small>키: .env OPENAI_API_KEY 또는 LLM_API_KEY (${p.openai && p.openai.key ? '설정됨' : '없음'}) · 사내 PAT 게이트웨이도 여기</small></label>` +
      `<label>openai_api_key_header ${sel('m-openai-key-header', ['authorization', 'api-key', 'x-api-key'], s.openai_api_key_header || 'authorization')} <small>authorization = "Bearer &lt;PAT&gt;", 그 외는 키 값 그대로</small></label>` +
      `<label>openai_extra_headers <input id="m-openai-extra" value="${esc(JSON.stringify(s.openai_extra_headers || {}))}" placeholder='{"X-Tenant":"modem"}' style="width:260px"> <small>JSON</small></label>` +
      `<label>openai_embed_base_url <input id="m-openai-embed-url" value="${esc(s.openai_embed_base_url || '')}" placeholder="(비우면 openai_base_url)"> openai_embed_model <input id="m-openai-embed" value="${esc(s.openai_embed_model)}" placeholder="text-embedding-3-small / bge-m3"></label>` +
      `<label>anthropic_base_url <input id="m-anthropic-url" value="${esc(s.anthropic_base_url || '')}" placeholder="(비우면 api.anthropic.com) https://gateway.corp"> <small>키: ANTHROPIC_API_KEY(x-api-key) 또는 ANTHROPIC_AUTH_TOKEN(Bearer PAT)</small></label>` +
      `<label>llm_timeout <input id="m-llm-timeout" type="number" value="${s.llm_timeout || 600}" style="width:80px"> retries <input id="m-llm-retries" type="number" value="${s.llm_retries}" style="width:60px"> backoff ${sel('m-llm-backoff', ['exponential', 'linear'], s.llm_retry_backoff || 'exponential')} <input id="m-llm-backoff-s" type="number" step="0.5" value="${s.llm_retry_backoff_s}" style="width:60px">초 <small>역할별로 다르게 하려면 오른쪽 표의 '재시도·타임아웃 열 보기'</small></label>` +
      `<label>llm_budget_s <input id="m-llm-budget" type="number" value="${s.llm_budget_s || 0}" style="width:70px" title="한 호출의 재시도 포함 총 시간 예산(0=무제한). 넘으면 대체 경로(추출식 답변)로"> 회로차단 <input id="m-llm-circuit" type="number" value="${s.llm_circuit_failures}" style="width:50px" title="연속 실패 n회 → cooldown 동안 즉시 실패"> / <input id="m-llm-cooldown" type="number" value="${s.llm_circuit_cooldown_s}" style="width:60px">초</label>` +
      `<label>rerank_url <input id="m-rerank-url" value="${esc(s.rerank_url)}" placeholder="http://host:8000/v1/rerank"> <small>비우면 api 리랭크 비활성</small></label>` +
      `<label>rerank_api_model <input id="m-rerank-model" value="${esc(s.rerank_api_model || '')}" placeholder="BAAI/bge-reranker-v2-m3"> style ${sel('m-rerank-style', ['cohere', 'voyage'], s.rerank_api_style)} <small>rerank_method(튜닝)=auto 면 URL 있을 때 api 우선</small></label>`;
    $('#models-note').innerHTML = `Python ${p.python} / SQLite ${p.sqlite}. headless 에이전트: ${Object.keys(p.agents || {}).map(esc).join(', ')} (agents.json).`;
    const showPol = $('#roles-show-policy') && $('#roles-show-policy').checked;
    const polHead = showPol ? POLICY_COLS.map((c) => `<th title="비우면 전역값 상속">${c[1]}</th>`).join('') : '';
    $('#roles-table').innerHTML = '<table class="roles"><tr><th>역할</th><th>용도</th><th>provider</th><th>model</th><th>effort</th>' + polHead + '<th>실제 인스턴스</th><th>상태</th><th></th></tr>' + j.roles.map((role) => {
      const r = p.roles[role]; const cfg = (s.llm_roles || {})[role] || {};
      const inh = `(상속: 전역 ${esc(s.llm_provider)})`;
      const eff = (j.policy || {})[role] || {};
      const polCells = showPol ? POLICY_COLS.map((c) => `<td><input id="r-${role}-${c[0]}" type="number" step="${c[0] === 'backoff_s' ? '0.5' : '1'}" value="${esc(cfg[c[0]] == null ? '' : cfg[c[0]])}" placeholder="${esc(eff[c[0]] == null ? '' : eff[c[0]])}" style="width:72px" title="비우면 전역값(${esc(eff[c[0]])}) 상속"></td>`).join('') : '';
      const circ = (r.circuit || {});
      const circTxt = circ.open_until && circ.open_until * 1000 > Date.now() ? `<br><span class="bad" title="연속 실패로 잠시 건너뜁니다">회로 차단</span>` : '';
      return `<tr data-role="${role}"><td><b>${role}</b></td><td class="muted small">${esc(cat.roles[role] || '')}</td><td><input id="r-${role}-provider" list="dl-providers" value="${esc(cfg.provider || '')}" placeholder="${inh}" title="비우면 전역 llm_provider(${esc(s.llm_provider)})를 상속" style="width:140px"></td><td>${modelSelect('r-' + role + '-model', cfg.model || '', role, true, s.llm_model)}</td><td>${sel('r-' + role + '-effort', cat.effort, cfg.effort || '', true)}</td>${polCells}<td class="small">${esc(r.name)}/${esc(r.model)}<br><span class="muted">effort=${r.configured.effort} · ${(r.policy || {}).timeout_s}s × ${(r.policy || {}).retries + 1}</span></td><td>${r.available ? '<span class="ok">available</span>' : '<span class="bad" title="' + esc(r.reason || '') + '">unavailable</span>'}${r.available ? '' : '<br><span class="reason small">' + esc(r.reason || '') + '</span>'}${circTxt}<br><span class="muted small">calls ${r.stats.calls || 0} · tok ${fmtK((r.stats.input_tokens || 0) + (r.stats.output_tokens || 0))}${r.stats.retries ? ' · 재시도 ' + r.stats.retries : ''}${r.stats.errors ? ' · 실패 ' + r.stats.errors : ''}</span></td><td><button class="mini secondary" data-test="${role}" title="이 행에 입력한(아직 저장 안 한) provider/model 로 연결 테스트">테스트</button></td></tr>`;
    }).join('') + `</table><datalist id="dl-providers">${PROVIDERS.map((x) => `<option value="${x}">`).join('')}</datalist>` +
      `<div class="muted small" style="margin-top:6px"><b>상속</b> = 비워 두면 위 "전역 LLM 기본값"(llm_provider / llm_model / llm_effort / llm_timeout …)을 그대로 씀. 역할별 타임아웃·재시도·backoff·총예산·<b>출력토큰(max_tokens)</b>은 '재시도·타임아웃 열 보기' 를 켜면 편집할 수 있고 <code>config.json llm_roles.&lt;role&gt;</code> 에 저장된다. 회색 글씨는 지금 상속 중인 값이다 — 비워 두면 그 값을 쓴다(출력토큰은 단계 기본값: 라우터 200 · 확장/리랭크 400 · 검증 1500 · 요약 800 · 포렌식 1200 · 리뷰/답변 3000 · 추출 4000). <b>auto</b> = ANTHROPIC_API_KEY 가 있으면 anthropic → 없으면 Ollama → 둘 다 없으면 none(추출식 답변). openai / headless 는 provider 에 직접 적는다. "테스트" 는 저장 전 입력값으로도 동작하며, 실제 적용은 "저장 &amp; 프로바이더 재로드". 최종 실패 시에는 추출식 답변·로컬 리랭크 등 대체 경로로 계속 동작합니다.</div>`;
    wireModelSelects($('#roles-table')); wireModelSelects($('#llm-form'));
    renderCatalog();
    $$('#roles-table [data-test]').forEach((b) => b.onclick = async () => {
      b.disabled = true;
      // 저장하지 않은 폼 값(전역 + 이 역할)을 요청 단위 오버라이드로 보내 실제 연결을 확인한다
      const role = b.dataset.test, ov = { llm_provider: $('#m-llm-provider').value, llm_model: $('#m-llm-model').value.trim(), ollama_url: $('#m-ollama-url').value.trim(), ollama_model: $('#m-ollama-model').value.trim(), openai_base_url: $('#m-openai-url').value.trim(), openai_api_key_header: $('#m-openai-key-header').value, anthropic_base_url: $('#m-anthropic-url').value.trim() };
      const pv = $('#r-' + role + '-provider').value.trim(), m = $('#r-' + role + '-model').value.trim();
      ov[role + '_provider'] = pv; ov[role + '_model'] = m === '__custom__' ? '' : m;
      const r = await api('/api/models/test', { which: [role], overrides: ov }); b.disabled = false; renderTest(r);
    });
    const dl = $('#dl-llm-models');
    if (dl) dl.innerHTML = (CATALOG.models || []).filter((m) => m.enabled !== false).map((m) => `<option value="${esc(m.id)}">${esc(m.provider)}</option>`).join('');
    const ag = await api('/api/agents'); $('#agents-json').value = JSON.stringify(ag.agents, null, 2);
  }
  // ---------------- 모델 카탈로그 (models.json) ----------------
  function renderCatalog() {
    const provSel = $('#cat-provider');
    if (provSel && !provSel.children.length) provSel.innerHTML = (CATALOG.providers || PROVIDERS).map((x) => `<option>${esc(x)}</option>`).join('');
    const t = $('#cat-table'); if (!t) return;
    const rows = (CATALOG.models || []).concat((CATALOG.embed || []).map((m) => Object.assign({ kind: 'embed' }, m)));
    t.innerHTML = '<table><tr><th>id</th><th>provider</th><th>설명</th><th>역할</th><th>사용</th><th></th></tr>' + rows.map((m) => `<tr><td class="mono small">${esc(m.id || '(hash)')}${m.kind === 'embed' ? ' <span class="pill">embed</span>' : ''}</td><td>${esc(m.provider)}</td><td class="small muted">${esc(m.label || '')}${m.notes ? ' — ' + esc(m.notes) : ''}</td><td class="small">${esc((m.roles || []).join(',') || '*')}</td><td>${m.enabled === false ? '✘' : '✔'}</td><td><button class="mini secondary" data-cat-del="${esc(m.id)}" data-cat-prov="${esc(m.provider)}">삭제</button></td></tr>`).join('') + '</table>' +
      `<div class="muted small">현재 설정이 쓰는 모델: ${Object.keys(CATALOG.in_use || {}).map((r) => esc(r + '=' + CATALOG.in_use[r].model)).join(', ')}${(CATALOG.unknown_in_use || []).length ? ' · <span class="warntxt">카탈로그에 없음: ' + CATALOG.unknown_in_use.map((u) => esc(u.role + '=' + u.model)).join(', ') + '</span>' : ''}<br>파일: <code>${esc(CATALOG.path || '')}</code> · CLI: <code>models list</code> · <code>models catalog add &lt;id&gt; --provider …</code></div>`;
    $$('#cat-table [data-cat-del]').forEach((b) => b.onclick = async () => {
      if (!confirm('카탈로그에서 ' + b.dataset.catDel + ' 을(를) 삭제합니다. (설정에서 이미 쓰고 있어도 동작에는 영향 없음)')) return;
      await api('/api/models/catalog', { action: 'remove', id: b.dataset.catDel, provider: b.dataset.catProv });
      CATALOG = await api('/api/models/catalog'); renderCatalog(); loadModels();
    });
  }
  if ($('#btn-cat-add')) $('#btn-cat-add').onclick = async () => {
    const id = $('#cat-id').value.trim(); if (!id) { toast('모델 id 를 입력하세요'); return; }
    const m = { id: id, provider: $('#cat-provider').value, label: $('#cat-label').value.trim() || id, roles: $('#cat-roles').value.split(',').map((x) => x.trim()).filter(Boolean), enabled: true };
    if ($('#cat-embed').checked) m.kind = 'embed';
    const j = await api('/api/models/catalog', { action: 'add', model: m });
    if (j && j.ok) { toast('카탈로그에 추가됨'); $('#cat-id').value = ''; $('#cat-label').value = ''; CATALOG = j.catalog || CATALOG; renderCatalog(); loadModels(); }
  };
  if ($('#btn-cat-refresh')) $('#btn-cat-refresh').onclick = async () => { CATALOG = await api('/api/models/catalog'); renderCatalog(); toast('카탈로그 새로고침'); };
  if ($('#btn-cat-discover')) $('#btn-cat-discover').onclick = async () => {
    $('#cat-discover').textContent = '조회 중…';
    const j = await api('/api/cli', { argv: ['models', 'discover', '--json'] });
    let d = {}; try { d = JSON.parse(j.output || '{}'); } catch (e) { $('#cat-discover').textContent = j.output || '조회 실패'; return; }
    $('#cat-discover').innerHTML = ['ollama', 'openai'].map((prov) => {
      const rows = d[prov] || []; const err = (d.errors || {})[prov];
      return `<div><b>${prov}</b> ${err ? '<span class="warntxt">' + esc(err) + '</span>' : rows.length + '개'}</div>` +
        rows.map((m) => `<span class="chip ${m.in_catalog ? 'on' : ''}" data-add="${esc(m.id)}" data-prov="${prov}" title="${m.in_catalog ? '이미 카탈로그에 있음' : '클릭하면 카탈로그에 추가'}">${esc(m.id)}</span>`).join('');
    }).join('');
    $$('#cat-discover [data-add]').forEach((c) => c.onclick = async () => {
      await api('/api/models/catalog', { action: 'add', model: { id: c.dataset.add, provider: c.dataset.prov, label: c.dataset.add, enabled: true } });
      CATALOG = await api('/api/models/catalog'); renderCatalog(); toast('추가됨: ' + c.dataset.add);
    });
  };
  if ($('#roles-show-policy')) $('#roles-show-policy').onchange = loadModels;
  function renderTest(r) {
    $('#models-test').innerHTML = '<table><tr><th>대상</th><th>provider/model</th><th>ok</th><th>ms</th><th>detail</th></tr>' + Object.keys(r).map((k) => { const x = r[k]; return `<tr><td><b>${k}</b></td><td>${esc(x.provider || x.url || '')}/${esc(x.model)}${x.dim ? ' d=' + x.dim : ''}</td><td>${x.ok ? '<span class="ok">✔</span>' : '<span class="bad">✘</span>'}</td><td class="num">${fmt(x.ms, 0)}</td><td class="small">${esc(x.detail || '')}${x.models ? '<br><span class="muted">models: ' + esc(x.models.slice(0, 12).join(', ')) + '</span>' : ''}${'live_ok' in x ? '<br><b>실제 호출:</b> ' + (x.live_ok ? '<span class="ok">✔</span>' : '<span class="bad">✘</span>') + ' ' + fmt(x.live_ms, 0) + 'ms ' + esc(x.live_detail || '') : ''}</td></tr>`; }).join('') + '</table>';
  }
  function modelsSettings() {
    const st = { embed_provider: $('#m-embed-provider').value, embed_model: $('#m-embed-model').value.trim(), embed_dim: parseInt($('#m-embed-dim').value, 10), embed_store_dtype: $('#m-embed-dtype').value, embed_batch: parseInt($('#m-embed-batch').value, 10), embed_batch_max: parseInt($('#m-embed-batch-max').value, 10),
      llm_provider: $('#m-llm-provider').value, llm_model: $('#m-llm-model').value.trim(), llm_effort: $('#m-llm-effort').value, answer_effort: $('#m-answer-effort').value,
      ollama_url: $('#m-ollama-url').value.trim(), ollama_model: $('#m-ollama-model').value.trim(), llm_fallbacks: $('#m-fallbacks').checked,
      openai_base_url: $('#m-openai-url').value.trim(), openai_api_key_header: $('#m-openai-key-header').value, openai_embed_base_url: $('#m-openai-embed-url').value.trim(), openai_embed_model: $('#m-openai-embed').value.trim(),
      anthropic_base_url: $('#m-anthropic-url').value.trim(), llm_timeout: parseInt($('#m-llm-timeout').value, 10) || 600,
      llm_retries: parseInt($('#m-llm-retries').value, 10), llm_retry_backoff: $('#m-llm-backoff').value, llm_retry_backoff_s: parseFloat($('#m-llm-backoff-s').value),
      llm_budget_s: parseInt($('#m-llm-budget').value, 10) || 0, llm_circuit_failures: parseInt($('#m-llm-circuit').value, 10), llm_circuit_cooldown_s: parseInt($('#m-llm-cooldown').value, 10),
      rerank_url: $('#m-rerank-url').value.trim(), rerank_api_model: $('#m-rerank-model').value.trim(), rerank_api_style: $('#m-rerank-style').value, llm_roles: {} };
    try { st.openai_extra_headers = JSON.parse($('#m-openai-extra').value.trim() || '{}'); } catch (e) { toast('openai_extra_headers JSON 오류 — 무시'); }
    STATE.roles.forEach((role) => {
      const c = {}; const pv = $('#r-' + role + '-provider').value.trim(), mEl = $('#r-' + role + '-model'), m = mEl ? mEl.value.trim() : '', e = $('#r-' + role + '-effort').value;
      if (pv) c.provider = pv;
      if (m && m !== '__custom__') c.model = m;
      if (e) c.effort = e;
      POLICY_COLS.forEach((col) => { const el = $('#r-' + role + '-' + col[0]); if (el && el.value !== '') c[col[0]] = col[0] === 'backoff_s' ? parseFloat(el.value) : parseInt(el.value, 10); });
      // 빈 칸으로 지우면 그 역할의 그 항목은 상속으로 되돌린다 (남아 있던 값 제거)
      POLICY_COLS.forEach((col) => { const el = $('#r-' + role + '-' + col[0]); if (el && el.value === '' && c[col[0]] !== undefined) delete c[col[0]]; });
      if (Object.keys(c).length) st.llm_roles[role] = c;
    });
    return st;
  }
  $('#btn-models-save').onclick = async () => { const j = await api('/api/models/set', { settings: modelsSettings() }); $('#models-msg').textContent = '저장됨 · answer=' + j.providers.roles.answer.name + '/' + j.providers.roles.answer.model + ' · embed=' + j.providers.embedder.name; loadModels(); loadStatus(); };
  $('#btn-models-test').onclick = async () => { $('#models-test').textContent = '테스트 중…'; renderTest(await api('/api/models/test', { overrides: modelsSettings() })); };
  if ($('#btn-models-test-live')) $('#btn-models-test-live').onclick = async () => { $('#models-test').textContent = '실제 호출 테스트 중… (역할별 provider/model 당 1회, 수십 초 걸릴 수 있음)'; renderTest(await api('/api/models/test', { overrides: modelsSettings(), live: true })); };
  $('#btn-models-reload').onclick = loadModels;
  $('#btn-agents-save').onclick = async () => { let a; try { a = JSON.parse($('#agents-json').value); } catch (e) { toast('JSON 오류'); return; } await api('/api/agents', { agents: a }); toast('agents.json 저장됨'); };
  loaders.models = loadModels;

  // ---------------- SECURITY · USERS · PERMISSIONS · API KEYS · SNAPSHOTS · AUDIT ----------------
  let SEC = { roles: ['viewer', 'class3', 'class2', 'class1', 'builder', 'admin'], perms: null };
  function roleSel(cur, attrs) { return `<select ${attrs || ''}>${SEC.roles.map((r) => `<option ${r === cur ? 'selected' : ''}>${r}</option>`).join('')}</select>`; }
  async function loadSecurity() {
    const me = await api('/api/auth/me'); const u = me.user || {};
    SEC.roles = me.roles || SEC.roles;
    const admin = me.mode === 'off' || u.role === 'admin';
    const rs = $('#su-role'); if (rs && rs.options.length !== SEC.roles.length) rs.innerHTML = SEC.roles.map((r) => `<option ${r === 'viewer' ? 'selected' : ''}>${r}</option>`).join('');
    $('#sec-summary').innerHTML = `<div class="stat"><b>${esc(me.mode)}</b>auth mode</div><div class="stat"><b>${me.local ? 'on' : 'off'}</b>로컬 로그인</div><div class="stat"><b>${me.sso ? esc(me.sso_type) : 'off'}</b>SSO</div><div class="stat"><b>${esc(me.anonymous_role || '로그인 필수')}</b>게스트(익명) 역할</div><div class="stat"><b>${esc(u.name || 'local')}</b>${esc(u.role || 'admin')} (${esc(u.via || 'off')})</div><div class="stat"><b>${esc(me.confirm_phrase)}</b>리빌드/파괴적 작업 확인 문구${me.require_reauth ? ' + 비밀번호' : ''}</div>`;
    const rl = me.role_labels || {};
    $('#sec-roles').innerHTML = '<table><tr><th>역할</th><th>할 수 있는 것 (누적)</th></tr>' + SEC.roles.map((r) => `<tr><td><b>${esc(r)}</b></td><td class="small">${esc(rl[r] || '')}</td></tr>`).join('') + '</table>';
    if (!admin) { $('#sec-users').innerHTML = '<div class="muted small">사용자 목록·권한 표·API 키는 admin 만 볼 수 있습니다.</div>'; $('#sec-perms').innerHTML = ''; $('#sec-keys').innerHTML = ''; $('#sec-snapshots').innerHTML = ''; $('#sec-audit').innerHTML = ''; return; }
    const [us, sec, sn, au, ak] = await Promise.all([api('/api/auth/users'), api('/api/security'), api('/api/snapshot'), api('/api/audit?n=60'), api('/api/apikeys')]);
    $('#sec-path').textContent = sec.path || '';
    $('#sec-users').innerHTML = '<table><tr><th>id</th><th>역할</th><th>표시</th><th>비밀번호</th><th></th></tr>' + (us.users || []).map((x) => `<tr><td><b>${esc(x.name)}</b></td><td>${roleSel(x.role, `data-role-of="${esc(x.name)}"`)}</td><td>${esc(x.display || '')}</td><td>${x.has_password ? '있음' : '<span class="muted">없음 (SSO)</span>'}</td><td><button class="mini secondary" data-pw-of="${esc(x.name)}">비밀번호</button> <button class="mini danger" data-del-of="${esc(x.name)}">삭제</button></td></tr>`).join('') + '</table>' + (!(us.users || []).length ? '<div class="muted small">사용자 없음 — 아래에서 admin 을 먼저 추가하세요 (또는 CLI: users add &lt;id&gt; --role admin)</div>' : '');
    $$('#sec-users [data-role-of]').forEach((s) => s.onchange = async () => { const r = await api('/api/auth/users', { action: 'set_role', name: s.dataset.roleOf, role: s.value }); if (r.ok) toast('역할 변경: ' + s.dataset.roleOf + ' → ' + s.value); loadSecurity(); });
    // ---- 권한 표 (등급별 최소 역할 + 개별 작업 오버라이드) ----
    SEC.perms = sec.permissions || { levels: {}, ops: {} };
    const lvls = sec.levels || Object.keys(SEC.perms.levels), ll = sec.level_labels || {}, dfl = sec.defaults || {};
    const cliCfg = (sec.security || {}).cli || {};
    $('#sec-perms').innerHTML = `<div class="muted small">등급마다 "최소 역할"을 정합니다 (역할은 누적: 높은 역할은 낮은 등급의 작업을 모두 할 수 있음). 개별 작업(op)은 감사 로그의 op 이름으로 예외를 둘 수 있고, <code>*</code> 접미로 접두 일치(예 <code>cli:trial*</code>). CLI: <code>security perms set run=viewer</code></div>` +
      '<table><tr><th>등급</th><th>뜻</th><th>최소 역할</th><th>기본</th></tr>' + lvls.map((lv) => `<tr><td><b>${esc(lv)}</b></td><td class="small">${esc(ll[lv] || '')}</td><td>${roleSel(SEC.perms.levels[lv], `data-lv="${lv}"`)}</td><td class="muted small">${esc(dfl[lv] || '')}</td></tr>`).join('') + '</table>' +
      `<div class="row" style="margin-top:6px"><label>익명(게스트) 역할 <select id="sec-anon"><option value="">(로그인 필수)</option>${SEC.roles.map((r) => `<option ${r === (me.anonymous_role || '') ? 'selected' : ''}>${r}</option>`).join('')}</select></label><label>CLI 기본 역할 <select id="sec-cli-role">${SEC.roles.map((r) => `<option ${r === (cliCfg.default_role || 'admin') ? 'selected' : ''}>${r}</option>`).join('')}</select></label><label class="inline"><input type="checkbox" id="sec-cli-login" ${cliCfg.require_login ? 'checked' : ''}> CLI 로그인 필수</label></div>` +
      `<h4 style="margin:10px 0 4px">개별 작업 오버라이드 <small class="muted">(op = 역할, 한 줄에 하나. 예 <code>/api/eval = class2</code>, <code>cli:trial run = class2</code>)</small></h4><textarea id="sec-ops" spellcheck="false" style="min-height:90px">${esc(Object.keys(SEC.perms.ops || {}).map((k) => k + ' = ' + SEC.perms.ops[k]).join('\n'))}</textarea>` +
      `<details><summary class="muted small">대표 op 목록 (현재 등급)</summary><div class="small mono">${(sec.ops_catalog || []).map((o) => esc(o.op) + ' <span class="muted">' + esc(o.level) + '</span>').join('<br>')}</div></details>` +
      `<div class="row" style="margin-top:6px"><button id="btn-perms-save" class="mini">권한 저장</button><button id="btn-perms-reset" class="mini secondary">기본값으로</button><span id="perms-msg" class="muted small"></span></div>`;
    $('#btn-perms-save').onclick = async () => {
      const levels = {}; $$('#sec-perms [data-lv]').forEach((s) => { levels[s.dataset.lv] = s.value; });
      const ops = {}; ($('#sec-ops').value || '').split('\n').map((x) => x.trim()).filter(Boolean).forEach((ln) => { const i = ln.lastIndexOf('='); if (i > 0) ops[ln.slice(0, i).trim()] = ln.slice(i + 1).trim(); });
      const r = await api('/api/security', { action: 'set_permissions', permissions: { levels, ops } });
      if (r.ok) { await api('/api/security', { action: 'set_anonymous', role: $('#sec-anon').value }); await api('/api/security', { action: 'set_cli', default_role: $('#sec-cli-role').value, require_login: $('#sec-cli-login').checked }); toast('권한 저장됨'); loadSecurity(); loadStatus(); }
    };
    $('#btn-perms-reset').onclick = async () => { if (!confirm('등급별 최소 역할과 개별 오버라이드를 기본값으로 되돌립니다.')) return; const lv = {}; Object.keys(dfl).forEach((k) => { lv[k] = dfl[k]; }); await api('/api/security', { action: 'set_permissions', permissions: { levels: lv, ops: {} } }); loadSecurity(); };
    // ---- API 키 ----
    $('#sec-keys').innerHTML = '<table><tr><th>id</th><th>이름</th><th>역할</th><th>생성</th><th>마지막 사용</th><th></th></tr>' + (ak.keys || []).map((k) => `<tr><td class="mono small">${esc(k.id)}</td><td>${esc(k.name)}</td><td>${esc(k.role)}</td><td class="small muted">${k.created ? LW.dt(k.created) : ''}</td><td class="small muted">${k.last_used ? LW.dt(k.last_used) : '-'}</td><td><button class="mini danger" data-key-del="${esc(k.id)}">삭제</button></td></tr>`).join('') + '</table>' +
      `<div class="row"><input id="ak-name" type="text" placeholder="키 이름 (예 claude-desktop-kim)" style="max-width:220px"> ${roleSel('viewer', 'id="ak-role"')} <button id="btn-ak-add" class="mini">발급</button><span class="muted small">MCP HTTP / curl 에서 <code>Authorization: Bearer &lt;token&gt;</code>. 토큰은 발급 직후 한 번만 표시됩니다.</span></div><pre id="ak-out" class="pre small hidden"></pre>`;
    $$('#sec-keys [data-key-del]').forEach((b) => b.onclick = async () => { if (!confirm('API 키 ' + b.dataset.keyDel + ' 를 삭제할까요? 이 키를 쓰는 클라이언트는 즉시 거부됩니다.')) return; await api('/api/apikeys', { action: 'remove', id: b.dataset.keyDel }); loadSecurity(); });
    $('#btn-ak-add').onclick = async () => { const r = await api('/api/apikeys', { action: 'add', name: $('#ak-name').value.trim(), role: $('#ak-role').value }); if (r.token) { const o = $('#ak-out'); o.classList.remove('hidden'); o.textContent = 'token (지금만 표시): ' + r.token + '\nMCP 원격 설정 예: {"type":"http","url":"' + location.origin + '/mcp","headers":{"Authorization":"Bearer ' + r.token + '"}}'; toast('API 키 발급'); } };
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

  // ---------------- SCHEDULE (schedule.json) ----------------
  let SCH = { tasks: [], action_types: [], help: {} };
  const ACTION_SAMPLE = {
    build: { type: 'build', full: false },
    fetch_url: { type: 'fetch_url', urls: ['https://intranet.example/notice.html'], dest: 'corpus/fetched', build_after: true },
    python: { type: 'python', script: 'tools/my_job.py', args: [], timeout_s: 600 },
    cli: { type: 'cli', argv: ['build', '--yes'] },
    query: { type: 'query', q: '지난주 리뷰한 CL 요약', out: 'logs/schedule/digest.md' },
    llm: { type: 'llm', role: 'answer', prompt_file: 'prompts/weekly_review.md', out: 'logs/schedule/review.md' },
    headless: { type: 'headless', agent: 'opencode', prompt_file: 'prompts/weekly_review.md', out: 'logs/schedule/review.md' },
    mcp_ingest: { type: 'mcp_ingest', sources: null, build_after: true },
    maintenance: { type: 'maintenance', action: 'wal_checkpoint' },
    http: { type: 'http', url: 'https://hooks.example/notify', method: 'POST', body: { text: 'llmwiki' } },
    evolve: { type: 'evolve', op: 'review' },
    memory: { type: 'memory', op: 'decay' },
    precompute: { type: 'precompute', op: 'run', from_log: 20 },
    eval: { type: 'eval', k: 5, out: 'logs/schedule/eval.md' },
    trial: { type: 'trial', name: 'nightly', preset: 'quality', k: 5 },
    snapshot: { type: 'snapshot', op: 'create', tag: 'auto:schedule', keep: 3 },
    wiki: { type: 'wiki', min_degree: 1 },
    forensic: { type: 'forensic', op: 'summary', out: 'logs/schedule/forensic.md' },
    embed_report: { type: 'embed_report', out: 'logs/schedule/embed.md' },
  };
  function whenText(t) {
    if (t.cron) return 'cron ' + t.cron;
    if (t.at) return '매일 ' + t.at + ((t.days || []).length ? ' (' + t.days.join(',') + ')' : '');
    return '매 ' + (t.every || '');
  }
  async function loadSchedule() {
    const j = await api('/api/schedule?n=30');
    if (!j || j.error) { $('#sch-table').innerHTML = `<div class="muted">${esc((j && j.error) || '조회 실패')}</div>`; return; }
    SCH = j; $('#sch-path').textContent = j.path || '';
    $('#sch-msg').innerHTML = j.running ? '' : '<span class="warntxt">스케줄러가 실행 중이 아닙니다 (serve 로 서버를 띄우면 동작). 목록 편집과 `schedule run` 은 지금도 가능합니다.</span>';
    const ty = $('#sch-type');
    if (ty && !ty.children.length) ty.innerHTML = (j.action_types || []).map((x) => `<option>${esc(x)}</option>`).join('');
    $('#sch-table').innerHTML = (j.tasks || []).length
      ? '<table><tr><th>사용</th><th>이름</th><th>시점</th><th>동작</th><th>다음 실행</th><th>마지막</th><th></th></tr>' + j.tasks.map((t) => {
        if (t.invalid) return `<tr class="skip"><td>✘</td><td>${esc(t.name)}</td><td colspan="4" class="warntxt small">설정 오류: ${esc(t.invalid)}</td><td></td></tr>`;
        return `<tr><td>${t.enabled === false ? '✘' : '✔'}</td><td><b>${esc(t.name)}</b>${t.running ? ' <span class="pill warn">실행 중</span>' : ''}${t.weight === 'exclusive' ? ' <span class="pill bad">배타</span>' : ''}</td><td class="small">${esc(whenText(t))}</td><td class="small">${esc((t.action || {}).type)}</td>` +
          `<td class="small">${t.next_run ? dt(t.next_run) : '-'}</td><td class="small">${t.last_run ? dt(t.last_run) + ' <span class="pill ' + (t.last_status === 'done' ? 'ok' : t.last_status === 'cancelled' ? 'warn' : 'bad') + '">' + esc(t.last_status || '') + '</span> ' + fmt(t.last_ms, 0) + 'ms' : '-'}${t.last_error ? '<br><span class="bad small">' + esc(String(t.last_error).slice(0, 70)) + '</span>' : ''}</td>` +
          `<td><button class="mini" data-run="${esc(t.name)}" title="지금 한 번 실행">▶</button><button class="mini secondary" data-edit="${esc(t.name)}">편집</button><button class="mini secondary" data-tog="${esc(t.name)}">${t.enabled === false ? '켜기' : '끄기'}</button><button class="mini secondary" data-del="${esc(t.name)}">삭제</button></td></tr>`;
      }).join('') + '</table>'
      : '<div class="muted">작업이 없습니다. "+ 작업 추가" 를 누르거나 setup/schedule.example.json 을 schedule.json 으로 복사하세요.</div>';
    $$('#sch-table [data-run]').forEach((b) => b.onclick = async () => { const r = await api('/api/schedule', { action: 'run', name: b.dataset.run }); toast(r && r.ok ? '실행 시작 (진행 중 작업 탭에서 확인)' : ('실패: ' + ((r && r.error) || ''))); setTimeout(loadSchedule, 1500); });
    $$('#sch-table [data-edit]').forEach((b) => b.onclick = () => openTask(b.dataset.edit));
    $$('#sch-table [data-tog]').forEach((b) => b.onclick = async () => { const t = (SCH.tasks || []).find((x) => x.name === b.dataset.tog); await api('/api/schedule', { action: t.enabled === false ? 'enable' : 'disable', name: b.dataset.tog }); loadSchedule(); });
    $$('#sch-table [data-del]').forEach((b) => b.onclick = async () => { if (!confirm('작업 ' + b.dataset.del + ' 을 삭제합니다.')) return; await api('/api/schedule', { action: 'remove', name: b.dataset.del }); loadSchedule(); });
    $('#sch-history').innerHTML = (j.history || []).length
      ? '<table><tr><th>시각</th><th>작업</th><th>결과</th><th>ms</th><th>내용</th></tr>' + j.history.map((h) => `<tr><td class="small">${dt(h.ts)}</td><td>${esc(h.name)}</td><td><span class="pill ${h.status === 'done' ? 'ok' : h.status === 'cancelled' ? 'warn' : 'bad'}">${esc(h.status)}</span></td><td class="num">${fmt(h.ms, 0)}</td><td class="small muted">${esc((h.error || JSON.stringify(h.result || {})).slice(0, 120))}</td></tr>`).join('') + '</table>'
      : '<div class="muted">이력 없음</div>';
  }
  function showWhen() {
    const k = $('#sch-when-kind').value;
    ['every', 'at', 'cron'].forEach((x) => $('#sch-when-' + x).classList.toggle('hidden', x !== k));
  }
  function openTask(name) {
    const t = (SCH.tasks || []).find((x) => x.name === name) || { name: '', enabled: true, every: '1h', action: ACTION_SAMPLE.build };
    $('#sch-editor').classList.remove('hidden');
    $('#sch-editor-title').textContent = name ? ('작업 편집: ' + name) : '새 작업';
    $('#sch-editor').dataset.orig = name || '';
    $('#sch-name').value = t.name || ''; $('#sch-enabled').checked = t.enabled !== false;
    $('#sch-when-kind').value = t.cron ? 'cron' : t.at ? 'at' : 'every';
    $('#sch-every').value = t.every || '1h'; $('#sch-at').value = t.at || '03:00'; $('#sch-days').value = (t.days || []).join(','); $('#sch-cron').value = t.cron || '0 3 * * *';
    $('#sch-type').value = (t.action || {}).type || 'build';
    $('#sch-timeout').value = t.timeout_s || 0; $('#sch-overlap').value = t.overlap || 'skip';
    $('#sch-action').value = JSON.stringify(t.action || ACTION_SAMPLE.build, null, 2);
    $('#sch-type-help').textContent = (SCH.help || {})[$('#sch-type').value] || '';
    showWhen();
  }
  function taskFromForm() {
    const t = { name: $('#sch-name').value.trim(), enabled: $('#sch-enabled').checked };
    const k = $('#sch-when-kind').value;
    if (k === 'every') t.every = $('#sch-every').value.trim();
    else if (k === 'at') { t.at = $('#sch-at').value.trim(); const d = $('#sch-days').value.split(',').map((x) => x.trim()).filter(Boolean); if (d.length) t.days = d; }
    else t.cron = $('#sch-cron').value.trim();
    const to = parseInt($('#sch-timeout').value, 10); if (to > 0) t.timeout_s = to;
    t.overlap = $('#sch-overlap').value;
    t.action = JSON.parse($('#sch-action').value);
    return t;
  }
  if ($('#btn-sch-refresh')) {
    $('#btn-sch-refresh').onclick = loadSchedule;
    $('#btn-sch-add').onclick = () => openTask('');
    $('#btn-sch-cancel').onclick = () => $('#sch-editor').classList.add('hidden');
    $('#sch-when-kind').onchange = showWhen;
    $('#sch-type').onchange = () => { $('#sch-action').value = JSON.stringify(ACTION_SAMPLE[$('#sch-type').value] || { type: $('#sch-type').value }, null, 2); $('#sch-type-help').textContent = (SCH.help || {})[$('#sch-type').value] || ''; };
    $('#btn-sch-save').onclick = async () => {
      let t; try { t = taskFromForm(); } catch (e) { toast('동작 설정 JSON 오류: ' + e); return; }
      if (!t.name) { toast('이름을 입력하세요'); return; }
      const orig = $('#sch-editor').dataset.orig;
      if (orig && orig !== t.name) await api('/api/schedule', { action: 'remove', name: orig });
      const j = await api('/api/schedule', { action: 'add', task: t });
      if (j && j.ok) { toast('저장됨'); $('#sch-editor').classList.add('hidden'); loadSchedule(); }
    };
    $('#btn-sch-test').onclick = async () => {
      let t; try { t = taskFromForm(); } catch (e) { toast('동작 설정 JSON 오류: ' + e); return; }
      if (!t.name) { toast('이름을 입력하세요'); return; }
      await api('/api/schedule', { action: 'add', task: t });
      const r = await api('/api/schedule', { action: 'run', name: t.name });
      toast(r && r.ok ? '실행 시작 — 진행 중 작업 탭에서 확인' : ('실패: ' + ((r && r.error) || '')));
      setTimeout(loadSchedule, 2000);
    };
  }
  loaders.schedule = loadSchedule;

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
