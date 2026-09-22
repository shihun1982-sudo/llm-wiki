/* Settings — 모델/프로바이더/엔드포인트/agents, 프리셋, 튜닝, 질의 규칙 사전, pin, 프롬프트, config. */
(function (LW) {
  'use strict';
  // dt(시각 포맷)는 스케줄 표의 '다음 실행'·'마지막'·실행 이력이 쓴다. 예전에는 가져오지 않아
  // 그 줄을 그리다 ReferenceError 가 나고, `innerHTML = …` 자체가 실패해 **표가 통째로 옛 내용으로 남았다**.
  // 저장은 서버에 됐는데 화면만 안 바뀌어서 "스케줄 저장이 안 된다" 로 보였다.
  // switchGroup/switchTab: 모델 표의 '앙상블 설정으로' 버튼이 🧭 Pipeline › 앙상블 로 건너뛴다 (2026-09-19).
  const { $, $$, esc, fmt, fmtK, fmtS, ts, dt, api, toast, STATE, setTogglesFrom, loadStatus, loaders, switchGroup, switchTab } = LW;

  // ---------------- MODELS ----------------
  function sel(id, opts, cur, allowEmpty) { return `<select id="${id}">${allowEmpty ? '<option value="">(상속)</option>' : ''}${opts.map((o) => `<option ${o === cur ? 'selected' : ''}>${esc(o)}</option>`).join('')}</select>`; }
  let PROVIDERS = ['auto', 'anthropic', 'openai', 'ollama', 'headless:opencode', 'headless:claude', 'headless:codex', 'headless:mock', 'mock', 'none'];
  let CATALOG = { models: [], embed: [], providers: PROVIDERS, embed_providers: [] };
  // 역할별 정책 편집 열. 비워 두면 전역값(또는 단계 기본값)을 상속한다.
  const POLICY_COLS = [['timeout_s', '타임아웃(초)'], ['retries', '재시도'], ['backoff_s', '대기(초)'],
                       ['budget_s', '총예산(초)'], ['max_tokens', '출력토큰']];
  // 모델 드롭다운: models.json 카탈로그(사람이 추가/삭제) + 현재 값 + '직접 입력'.
  // kind='llm'(기본)이면 역할 필터를 적용한다(roles 가 빈 항목은 전 역할). kind='embed'|'rerank' 는 그 절의 목록을 쓴다.
  // 예전에는 임베딩은 datalist, 리랭크 API 모델은 맨 텍스트 입력이라 "무엇을 고를 수 있는지" 가 화면에 안 보였다 (2026-09-16).
  // 연결 테스트로 알게 된 모델 상태: "<provider>/<id>" → {ok, detail}. 전체 카탈로그 테스트·역할 테스트가 채운다.
  // 이 정보로 **붙지 않는 모델을 회색 처리하고 고를 수 없게** 한다 (2026-09-19 요청).
  let MODEL_STATUS = {};
  function statusOf(provider, id) {
    if (!id) return null;
    return MODEL_STATUS[(provider || '') + '/' + id] || MODEL_STATUS['*/' + id] || null;
  }
  function noteStatus(provider, id, ok, detail) {
    if (!id) return;
    MODEL_STATUS[(provider || '') + '/' + id] = { ok: !!ok, detail: String(detail || '') };
  }
  /** 카탈로그 전체(비활성·다른 역할용 포함). 각 항목에 화면용 플래그를 붙여 준다. */
  function catalogAll(kind, role) {
    const src = kind === 'embed' ? (CATALOG.embed || []) : kind === 'rerank' ? (CATALOG.rerank || []) : (CATALOG.models || []);
    return src.map((m) => {
      const roles = m.roles || [];
      const forRole = (kind !== 'llm') || !role || !roles.length || roles.indexOf(role) >= 0;
      const st = statusOf(m.provider, m.id);
      const off = m.enabled === false;
      const failed = !!(st && st.ok === false);
      return Object.assign({}, m, { _forRole: forRole, _off: off, _failed: failed, _st: st,
        _blocked: off || failed });          // 고를 수 없는 것
    });
  }
  function optLabel(m) {
    const bits = [esc(m.id || '(비움)'), '·', esc(m.provider)];
    if (m.label) bits.push('· ' + esc(m.label));
    if (m.dim) bits.push('· ' + m.dim + 'd');
    if (m._off) bits.push('· 사용 안 함');
    if (m._failed) bits.push('· 연결 실패');
    return bits.join(' ');
  }
  function optTitle(m) {
    const t = [m.id + ' (' + m.provider + ')'];
    if (m.label) t.push(m.label);
    if (m.notes) t.push(m.notes);
    if (m._off) t.push('카탈로그에서 사용 안 함으로 꺼져 있습니다 — 고를 수 없습니다 (카탈로그에서 켜면 나옵니다)');
    if (m._failed) t.push('연결 테스트 실패: ' + ((m._st || {}).detail || '') + ' — 고를 수 없습니다');
    else if (m._st && m._st.ok) t.push('연결 확인됨');
    if (!m._forRole) t.push('이 역할용으로 등록된 모델은 아닙니다 (카탈로그의 역할 칸) — 골라도 동작합니다');
    return esc(t.join('\n'));
  }
  /** 모델 드롭다운. **카탈로그의 모든 모델**을 묶음으로 보여 주고, 못 쓰는 것은 회색 + 선택 불가. */
  function modelSelect(id, cur, role, allowInherit, placeholderText, kind) {
    const all = catalogAll(kind || 'llm', role);
    const ids = all.map((m) => m.id);
    const groups = [
      ['이 역할에 맞는 모델', all.filter((m) => m._forRole && !m._blocked)],
      ['다른 역할용 (골라도 동작)', all.filter((m) => !m._forRole && !m._blocked)],
      ['연결 실패 — 고를 수 없음', all.filter((m) => m._failed)],
      ['카탈로그에서 사용 안 함 — 고를 수 없음', all.filter((m) => m._off && !m._failed)],
    ];
    // allowInherit 가 문자열이면 빈 칸 옵션의 **라벨 전체**로 쓴다.
    // 앙상블 멤버 칸은 비워 두면 상속이 아니라 "이 멤버를 안 씀" 이라서 '(상속: …)' 이라고 쓰면 거짓말이 된다.
    const emptyLabel = typeof allowInherit === 'string'
      ? allowInherit
      : '(상속' + (placeholderText ? ': ' + placeholderText : '') + ')';
    let opts = (allowInherit ? `<option value="">${esc(emptyLabel)}</option>` : '');
    groups.forEach(([title, list]) => {
      if (!list.length) return;
      opts += `<optgroup label="${esc(title)}">` + list.map((m) =>
        `<option value="${esc(m.id)}"${m.id === cur ? ' selected' : ''}${m._blocked ? ' disabled class="opt-off"' : ''} title="${optTitle(m)}">${optLabel(m)}</option>`).join('') + '</optgroup>';
    });
    // 지금 값이 카탈로그에 없으면(직접 입력·다른 환경에서 온 설정) 그대로 살려 둔다 — 고른 값이 조용히 사라지면 안 된다
    if (cur && ids.indexOf(cur) < 0) opts += `<option value="${esc(cur)}" selected title="카탈로그에 없는 모델입니다. 동작은 하지만 Settings › 카탈로그에 추가해 두면 목록에 나옵니다">${esc(cur)} · (카탈로그에 없음)</option>`;
    opts += '<option value="__custom__">＋ 직접 입력…</option>';
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
  // ---------------- 앙상블: 한 역할에 LLM 을 최대 3개까지 병렬로 + 취합 LLM (요청 6) ----------------
  // 역할 행 아래에 접이식으로 붙인다. 저장은 `llm_roles.<role>.ensemble` 로 config.json 에 들어가고
  // CLI `models ensemble show|set <role>` 과 **같은 값**을 읽고 쓴다.
  let ENS = {}, ENS_DEF = { wait: 'all', timeout_s: 120, min_results: 1, prompt: 'ensemble_merge' }, ENS_MAX = 3;
  // server.json timeouts.query_s — 요청이 실제로 잘리는 지점. 앙상블 최악 소요와 견줘 경고한다.
  let QTO = 0;
  /** 모델·프로바이더 표의 앙상블 **상태 줄** (2026-09-19).
   *  여기서는 켜고 끄지 않는다 — 같은 값을 두 화면에서 받으면 어느 쪽이 적용됐는지 알 수 없기 때문이다.
   *  편집은 🧭 Pipeline › 앙상블 한 곳에서만 하고, 여기서는 "지금 어떤 상태인지" 와 "어디서 고치는지" 만 보여 준다. */
  function ensembleStatusRow(role) {
    const e = (ENS[role] || {}); const eff = e.effective || {};
    const on = !!eff.enabled;
    const mem = (eff.members || []);
    const flow = on
      ? `멤버 ${mem.length}개 동시 호출 → ${mem.length > 1 ? '성공 2개↑ 면 취합 LLM 1회' : '성공 1개면 취합 건너뜀'} → 최종`
      : '역할 모델 1회 → 최종';
    const tip = [
      '앙상블 = 이 역할 하나를 여러 LLM 에 동시에 물어보고 하나로 합치는 기능.',
      '',
      '켜면 위 줄의 역할 모델 대신 멤버들이 호출됩니다 (멤버가 비운 칸만 역할 값을 상속).',
      '멤버는 모두 그 단계의 같은 프롬프트를 받고, 자기 프롬프트를 쓰는 것은 취합 LLM 하나뿐입니다.',
      '성공한 멤버가 2개 이상일 때만 취합 LLM 이 한 번 더 돕니다.',
      '',
      '지금: ' + flow,
      '저장 위치: config.json → llm_roles.' + role + '.ensemble',
      'CLI: python -m llmwiki models ensemble show ' + role,
      '',
      '고치는 곳: 🧭 Pipeline › 앙상블 (편집은 그 한 곳에서만 합니다)',
    ].join('\n');
    return `<tr class="ens-row ens-status" data-ens-row="${role}"><td colspan="20">` +
      `<span class="ens-lbl" title="${esc(tip)}">앙상블</span> ` +
      (on ? `<span class="pill ok" title="${esc(tip)}">ON · 멤버 ${mem.length}</span>` : `<span class="pill" title="${esc(tip)}">off</span>`) +
      `<span class="muted small" title="${esc(tip)}"> — ${esc(flow)}</span>` +
      (on && mem.length ? `<span class="muted small"> · ${mem.map((m) => `<code>${esc(m.provider)}/${esc(m.model)}</code>`).join(' ')}</span>` : '') +
      `<button class="mini secondary" data-ens-goto="${esc(role)}" title="🧭 Pipeline › 앙상블 에서 이 역할을 고칩니다 (편집은 그 한 곳에서만)">앙상블 설정으로</button>` +
      `</td></tr>`;
  }
  function ensembleRow(role) {
    const e = (ENS[role] || {}); const eff = e.effective || {}; const raw = e.raw || {};
    const members = (raw.members || []).slice(0, ENS_MAX);
    while (members.length < ENS_MAX) members.push({ enabled: false, provider: '', model: '', weight: 1.0 });
    const on = !!raw.enabled;
    const span = 20;      // 남은 열을 모두 쓴다 (열 수가 옵션에 따라 달라지므로 넉넉히)
    // 유효 멤버는 "쓰이는 것만" 의 배열이라 화면의 #1~#3 과 index 가 다르다 → 모델 이름으로 짝지어
    // "이 줄이 실제로 어떤 provider/model 로 불리는지" 를 각 줄 끝에 붙인다.
    const effByModel = {};
    (eff.members || []).forEach((m, k) => { effByModel[String(m.model)] = Object.assign({ order: k + 1 }, m); });
    const rm = e.role || {};                     // 멤버가 비운 칸이 상속하는 역할 모델
    const rmTxt = (rm.provider || rm.model) ? `${rm.provider || '?'}/${rm.model || '?'}` : '';
    const bud = e.budget || null;                // 최악 소요 (재시도 × 타임아웃 + 폴백)
    const memberHtml = members.map((m, i) => {
      const model = String(m.model || '').trim();
      const use = !!m.enabled && !!model;
      const e2 = use ? effByModel[model] : null;
      const st = use ? statusOf((e2 && e2.provider) || m.provider, model) : null;
      // 「쓰기」를 켰는데 모델이 비면 그대로 빠진다 — 조용히 꺼져 있지 말고 이유를 말한다 (config.effective_ensemble 규칙)
      const needModel = !!m.enabled && !model;
      const actual = e2
        ? `<span class="ens-actual" title="이 멤버가 실제로 호출하는 provider/model (비운 칸은 상속으로 채워진 값)">→ <b>${esc(e2.provider)}</b>/${esc(e2.model)}${st ? (st.ok ? ' <span class="ok" title="연결 확인됨">✔</span>' : ' <span class="bad" title="연결 실패: ' + esc(st.detail) + '">✘</span>') : ''}</span>`
        : use ? '<span class="ens-actual muted" title="저장하면 유효값에 반영됩니다">→ 저장 후 반영</span>'
        : needModel ? '<span class="ens-actual need" title="모델 칸이 비어 있어 이 멤버는 호출되지 않습니다 — 모델을 고르세요">▲ 모델을 골라야 켜집니다</span>'
        : '<span class="ens-actual muted">✘ 사용 안 함</span>';
      return `<div class="ens-m ${use ? '' : 'off'}${needModel ? ' need' : ''}" data-ens-mrow="${i}">` +
        `<label class="c" title="이 멤버를 쓸지 — 켜고 모델까지 골라야 호출됩니다"><input type="checkbox" data-ens="${role}" data-ens-m="${i}" data-ens-f="enabled" ${use || needModel ? 'checked' : ''}></label>` +
        `<span class="n" title="호출 순서와 무관합니다 — 세 칸은 동시에 불립니다">#${i + 1}</span>` +
        `<input class="p" data-ens="${role}" data-ens-m="${i}" data-ens-f="provider" list="dl-providers" value="${esc(m.provider || '')}" placeholder="${esc(rm.provider ? '(역할 상속: ' + rm.provider + ')' : '(역할 상속)')}" title="비우면 카탈로그 → 역할 provider${rm.provider ? ' (' + esc(rm.provider) + ')' : ''} 를 따릅니다">` +
        modelSelect(`ens-${role}-${i}-model`, m.model || '', role,
          '(비움 — 이 멤버는 호출되지 않습니다)', '').replace('<select ', `<select class="m" data-ens="${role}" data-ens-m="${i}" data-ens-f="model" title="모델을 고르면 이 멤버가 켜집니다. 비우면 「쓰기」를 켜도 빠집니다${rmTxt ? ' (역할 모델은 ' + esc(rmTxt) + ')' : ''}" `) +
        `<input class="w" type="number" step="0.1" min="0" data-ens="${role}" data-ens-m="${i}" data-ens-f="weight" value="${m.weight == null ? 1 : m.weight}" title="취합 LLM 에게 알려 줄 신뢰 가중치 — prompts/${esc(eff.prompt || ENS_DEF.prompt)}.md 가 이 값을 어떻게 쓸지 정합니다">` +
        actual + '</div>';
    }).join('');
    const agg = raw.aggregator || {};
    const nUse = members.filter((m) => m.enabled && String(m.model || '').trim()).length;
    // 한 줄 흐름도 — 실제 동작 그대로 (2026-09-19 정정)
    //   OFF : 위 줄의 역할 모델 **1회**
    //   ON  : 멤버 N개를 **동시에** (역할 모델은 부르지 않는다. 멤버가 provider/model 을 비우면 역할 값을 상속할 뿐)
    //         성공 1개면 취합 없이 그 답, 2개 이상이면 취합 LLM **1회**
    const flow = on
      ? `<div class="ens-flow" title="앙상블이 켜져 있을 때 이 역할의 요청 하나가 어떻게 처리되는지">` +
        `<span class="step">${esc(role)} 요청</span><span class="ar">→</span>` +
        `<span class="step ${nUse ? 'on' : ''}">멤버 ${nUse}개 <b>동시 호출</b><small>같은 단계 프롬프트</small></span><span class="ar">→</span>` +
        `<span class="step">${nUse > 1 ? '성공 2개↑ 면 취합 LLM 1회' : '성공 1개면 취합 건너뜀'}<small>prompts/${esc(eff.prompt || ENS_DEF.prompt)}.md</small></span><span class="ar">→</span>` +
        `<span class="step">최종 결과</span></div>`
      : `<div class="ens-flow" title="앙상블이 꺼져 있을 때">` +
        `<span class="step">${esc(role)} 요청</span><span class="ar">→</span>` +
        `<span class="step on">역할 모델 <b>1회</b><small>위 줄의 provider/model</small></span><span class="ar">→</span>` +
        `<span class="step">최종 결과</span></div>`;
    const note = '<div class="muted small ens-note">앙상블을 켜면 <b>위 줄의 역할 모델 대신</b> 멤버들이 호출됩니다(멤버가 비운 칸만 역할 값을 상속). ' +
      '멤버는 모두 <b>그 단계의 같은 프롬프트</b>를 받고, 별도 프롬프트를 쓰는 것은 <b>취합 LLM 하나</b>뿐입니다. ' +
      '저장 위치는 <code>config.json → llm_roles.' + esc(role) + '.ensemble</code> (CLI <code>models ensemble show ' + esc(role) + '</code>).</div>';
    return `<tr class="ens-row" data-ens-row="${role}"><td colspan="${span}">` +
      `<details class="ens" ${on ? 'open' : ''}><summary title="한 역할에 여러 LLM 을 동시에 물어보고 하나로 합칩니다 (CLI: models ensemble show ${esc(role)})">` +
      `앙상블 ${on ? `<span class="pill ok">ON · 멤버 ${(eff.members || []).length}</span>` : '<span class="pill">off</span>'}` +
      `<span class="muted small"> — <b>${esc(role)}</b> 역할을 최대 ${ENS_MAX}개 모델에 동시에 물어보고 취합 LLM 이 하나로</span>` +
      // 펼치지 않고도 켜고 끌 수 있게 요약 줄에 스위치를 둔다 (접혀 있어 못 찾던 문제)
      `<label class="inline ens-sw" title="이 역할에 앙상블을 쓸지 — config.json llm_roles.${esc(role)}.ensemble.enabled"><input type="checkbox" data-ens="${role}" data-ens-f="enabled" ${on ? 'checked' : ''}> 사용</label>` +
      `</summary>` +
      `<div class="ens-body">` + note + flow +
      `<div class="ens-grid">` +
      `<div class="ens-h"><span class="c">쓰기</span><span class="n">번호</span><span class="p">provider</span><span class="m">모델</span><span class="w">가중치</span><span class="ens-actual">실제 호출</span></div>` +
      `<div class="ens-members">${memberHtml}</div></div>` +
      `<div class="ens-line"><b class="lbl">취합 LLM</b>` +
      `<input class="p" data-ens="${role}" data-ens-f="agg_provider" list="dl-providers" value="${esc(agg.provider || '')}" placeholder="(역할 상속)">` +
      // 비우면 **첫 멤버(#1)** 가 취합한다 — 역할 모델이 아니다 (providers.EnsembleLLM.__init__:
      //   aggregator if aggregator is not None else self.members[0][0]).
      //   화면이 "역할 모델이 취합" 이라고 적고 있었는데 사실과 달랐다 (2026-09-20).
      modelSelect(`ens-${role}-agg-model`, agg.model || '', role, true, '첫 멤버(#1)가 취합').replace('<select ', `<select class="m" data-ens="${role}" data-ens-f="agg_model" `) +
      `<span class="muted small">멤버 답을 하나로 합치는 모델. <b>비우면 첫 멤버(#1)가 취합합니다</b> — 역할 모델이 아닙니다. (성공한 결과가 1개면 취합 자체를 건너뜁니다.)</span></div>` +
      `<div class="ens-line"><b class="lbl">정책</b>` +
      `<label class="inline" title="all = 모든 멤버 응답을 기다림 · timeout = 제한 시간까지 온 것만으로 취합">대기 <select data-ens="${role}" data-ens-f="wait"><option value="">기본(${esc(ENS_DEF.wait)})</option><option value="all"${raw.wait === 'all' ? ' selected' : ''}>all · 전부 기다림</option><option value="timeout"${raw.wait === 'timeout' ? ' selected' : ''}>timeout · 온 것만</option></select></label>` +
      `<label class="inline" title="wait=timeout 일 때 기다리는 시간(초)">제한 <input type="number" data-ens="${role}" data-ens-f="timeout_s" value="${raw.timeout_s == null ? '' : raw.timeout_s}" placeholder="${ENS_DEF.timeout_s}" style="width:64px">초</label>` +
      `<label class="inline" title="이보다 적게 오면 앙상블 실패로 보고 단일 결과로 되돌립니다">최소 응답 <input type="number" data-ens="${role}" data-ens-f="min_results" value="${raw.min_results == null ? '' : raw.min_results}" placeholder="${ENS_DEF.min_results}" style="width:54px">개</label>` +
      // 프롬프트: 비우면 역할 전용 파일(ensemble_merge_<role>.md) → 없으면 공용 ensemble_merge.md
      `<label class="inline" title="취합 규칙을 적는 프롬프트 파일 (prompts/&lt;이름&gt;.md). 비우면 이 역할 전용 파일 prompts/ensemble_merge_${esc(role)}.md 를 쓰고, 그 파일이 없으면 공용 prompts/ensemble_merge.md 로 떨어집니다. 내용은 Settings › 프롬프트 에서 고칩니다.">프롬프트 <input data-ens="${role}" data-ens-f="prompt" value="${esc(raw.prompt || '')}" placeholder="${esc(eff.prompt || ('ensemble_merge_' + role))}" style="width:168px">.md` +
      `<button type="button" class="mini secondary" data-ens-prompt="${esc(eff.prompt || ('ensemble_merge_' + role))}" title="이 취합 프롬프트를 Settings › 프롬프트 에서 엽니다">편집</button></label>` +
      `</div>` +
      // 앙상블이 실패했을 때(min_results 미달) 역할 모델로 되돌아갈지 — 켜 두면 "앙상블을 켠 탓에 답이 아예 안 나오는" 일이 없다
      `<div class="ens-line"><b class="lbl">실패 시</b>` +
      `<label class="inline" title="멤버가 min_results 를 못 채우면 역할 모델(${esc(rmTxt || '미정')})로 한 번 더 부릅니다. 끄면 앙상블 실패로 끝나고 호출부의 대체 경로(answer 는 추출식 답변)로 갑니다. config.json → llm_roles.${esc(role)}.ensemble.fallback_role_model">` +
      `<input type="checkbox" data-ens="${role}" data-ens-f="fallback_role_model" ${raw.fallback_role_model === false ? '' : 'checked'}> 역할 모델로 되돌리기</label>` +
      `<label class="inline" title="되돌릴 때 역할 모델이 무엇을 받나.&#10;auto = 성공한 멤버 답이 있으면 그것들을 취합, 하나도 없으면 원래 프롬프트로 다시&#10;merge = 되도록 살아남은 답을 취합 (이미 쓴 토큰을 살리고 빠르다)&#10;rerun = 멤버 답을 쓰지 않고 항상 원래 프롬프트로 (깨끗하지만 컨텍스트를 다시 넣어 비용이 든다)">` +
      `받는 것 <select data-ens="${role}" data-ens-f="fallback_mode">` +
      `<option value="">기본(${esc(ENS_DEF.fallback_mode || 'auto')})</option>` +
      ['auto', 'merge', 'rerun'].map((m) => `<option value="${m}"${raw.fallback_mode === m ? ' selected' : ''}>${m}${m === 'auto' ? ' · 있으면 취합' : m === 'merge' ? ' · 되도록 취합' : ' · 항상 새 프롬프트'}</option>`).join('') +
      `</select></label>` +
      `<span class="muted small">${eff.fallback ? `실패하면 <b>${esc(eff.fallback.provider)}/${esc(eff.fallback.model)}</b> 가 대신 답합니다.` : '되돌리지 않습니다 — 앙상블이 실패하면 그 단계가 실패합니다.'}</span>` +
      `</div>` +
      // 최악 소요 — (1+retries)×timeout + 백오프, 실패하면 폴백이 한 번 더. 곱셈이 눈에 안 보인다.
      (bud && bud.enabled ? `<div class="ens-budget${(QTO && bud.worst_s > QTO) ? ' over' : ''}">` +
        `<b>최악 소요</b> ${fmtS(bud.worst_s)} <span class="muted">= 멤버 ${fmtS(bud.members_s)} + ${bud.fallback_s >= bud.aggregate_s ? '폴백' : '취합'} ${fmtS(Math.max(bud.aggregate_s, bud.fallback_s))}` +
        ` · 한 번 = ${bud.attempts}회 × ${bud.timeout_s}초 + 백오프</span>` +
        (bud.notes || []).map((n) => `<div class="muted small">· ${esc(n)}</div>`).join('') +
        ((QTO && bud.worst_s > QTO)
          ? `<div class="warn-line">▲ <b>server.json timeouts.query_s=${QTO}초</b> 가 먼저 요청을 끊습니다 — ` +
            `느린 멤버 때문에 실패하는 상황에서는 <b>폴백이 실행되기 전에 잘립니다.</b><br>` +
            `줄이려면 Settings › 모델 의 역할 표에서 <code>timeout_s</code>·<code>retries</code> 를 낮추거나, 위 「대기」를 <code>timeout</code> 으로 두세요.</div>`
          : '') +
        `</div>` : '') +
      (eff.enabled
        ? `<div class="ens-eff"><b>지금 유효</b> ${(eff.members || []).map((m, k) => `<span class="chip on" title="${k + 1}번째 멤버">${esc(m.provider)}/${esc(m.model)} <b>w${m.weight}</b></span>`).join('')}` +
          `<span class="ar">→</span><span class="chip" title="취합 LLM">${eff.aggregator && eff.aggregator.model ? esc(eff.aggregator.provider) + '/' + esc(eff.aggregator.model) : ((eff.members || [])[0] ? '첫 멤버가 취합: ' + esc(eff.members[0].provider) + '/' + esc(eff.members[0].model) : '첫 멤버가 취합')}</span>` +
          `<span class="muted small">· ${esc(eff.wait)}${eff.wait === 'timeout' ? ' ' + eff.timeout_s + 's' : ''} · 최소 ${eff.min_results}개 · prompts/${esc(eff.prompt)}.md</span></div>`
        // 스위치는 켰는데 쓸 멤버가 0개 → 앙상블은 **돌지 않는다**. 조용히 넘어가면 켠 줄 알고 쓰게 되므로 크게 알린다.
        : on
        ? `<div class="ens-eff warnbox" data-ens-empty="${esc(role)}">▲ <b>「사용」은 켜져 있지만 쓸 멤버가 0개</b>라 앙상블이 돌지 않습니다 — ` +
          `<b>역할 모델 ${esc(rmTxt || '(미정)')} 1회</b>로 동작합니다.<br>` +
          `멤버는 <b>모델 칸이 비면 「쓰기」와 무관하게 빠집니다</b>. 위 표의 모델을 고르세요.` +
          (rm.model ? ` <button type="button" class="mini" data-ens-fill="${esc(role)}" title="「쓰기」가 켜진 빈 멤버를 역할 모델(${esc(rmTxt)})로 채웁니다. 그 뒤 각 줄에서 다른 모델로 바꾸면 됩니다">빈 멤버를 ${esc(rmTxt)} 로 채우기</button>` : '') +
          `</div>`
        : '<div class="ens-eff muted">지금은 꺼져 있습니다 — 멤버의 모델을 고르고 「쓰기」를 켠 뒤 위쪽 <b>저장 &amp; 프로바이더 재로드</b>.</div>') +
      `</div></details></td></tr>`;
  }
  /** 멤버 줄의 지금 상태를 화면에 **즉시** 반영한다 (2026-09-20).
   *  예전에는 다시 그리기 전까지 줄이 흐린 채 "✘ 사용 안 함" 으로 남아 있어서,
   *  「쓰기」를 켜도 아무 반응이 없어 칸이 잠긴 것처럼 보였다. 실제 규칙은 config.effective_ensemble 과 같다:
   *  **모델이 비면 enabled 와 무관하게 그 멤버는 빠진다.** */
  function refreshEnsRole(role) {
    const scope = $(`#ens-roles [data-ens-card="${role}"]`) || document;
    const rm = ((ENS[role] || {}).role || {});
    const rmTxt = (rm.provider || rm.model) ? `${rm.provider || '?'}/${rm.model || '?'}` : '(미정)';
    let nUse = 0, nNeed = 0;
    for (let i = 0; i < ENS_MAX; i++) {
      const q = (f) => scope.querySelector(`[data-ens="${role}"][data-ens-m="${i}"][data-ens-f="${f}"]`);
      const row = scope.querySelector(`[data-ens-mrow="${i}"]`);
      if (!row) continue;
      const cb = q('enabled'), sel2 = q('model');
      const model = String((sel2 && sel2.value !== '__custom__' ? (sel2 || {}).value : '') || '').trim();
      const on2 = !!(cb && cb.checked);
      const use = on2 && !!model, need = on2 && !model;
      if (use) nUse++; if (need) nNeed++;
      row.classList.toggle('off', !use);
      row.classList.toggle('need', need);
      const act = row.querySelector('.ens-actual');
      if (act) {
        act.className = 'ens-actual' + (use ? ' muted' : need ? ' need' : ' muted');
        act.textContent = use ? '→ 저장 후 반영' : need ? '▲ 모델을 골라야 켜집니다' : '✘ 사용 안 함';
        act.title = need ? '모델 칸이 비어 있어 이 멤버는 호출되지 않습니다' : '';
      }
    }
    const sw = scope.querySelector(`[data-ens="${role}"][data-ens-f="enabled"]:not([data-ens-m])`);
    const on = !!(sw && sw.checked);
    const pill = scope.querySelector('summary .pill');
    if (pill) {
      pill.className = 'pill' + (on && nUse ? ' ok' : on ? ' warn' : '');
      pill.textContent = on ? (nUse ? `ON · 멤버 ${nUse} (저장 필요)` : 'ON 이지만 멤버 0 — 안 돕니다') : 'off';
    }
    const step = scope.querySelector('.ens-flow .step.on, .ens-flow .step:nth-child(3)');
    if (step && on) step.classList.toggle('on', nUse > 0);
    const box = scope.querySelector('[data-ens-empty]');
    if (box) box.style.display = (on && !nUse) ? '' : 'none';
    const msg = $('#ens-msg');
    if (msg) {
      msg.textContent = (on && !nUse)
        ? `${role}: 「사용」이 켜져 있지만 멤버가 0개입니다 — 모델을 고르지 않으면 역할 모델 ${rmTxt} 1회로만 돕니다.`
        : (nNeed ? `${role}: 모델이 비어 있는 멤버가 ${nNeed}개 있습니다 — 그 줄은 호출되지 않습니다.`
                 : '바뀐 값이 있습니다 — [저장 & 프로바이더 재로드] 를 누르세요.');
    }
  }
  /** 멤버 칸의 입력에 즉시 반응하게 묶는다 + 「쓰기」만 켠 빈 멤버는 역할 모델로 채워 준다. */
  function wireEnsMembers(root) {
    const host = root || $('#ens-roles'); if (!host) return;
    host.addEventListener('change', (ev) => {
      const el = ev.target.closest('[data-ens][data-ens-m]'); if (!el) return;
      const role = el.dataset.ens, i = el.dataset.ensM, f = el.dataset.ensF;
      if (f === 'enabled' && el.checked) {
        // 「쓰기」만 켜면 아무 일도 안 일어나던 자리 — 역할 모델을 바로 넣어 켜지게 한다.
        const sel2 = host.querySelector(`[data-ens="${role}"][data-ens-m="${i}"][data-ens-f="model"]`);
        const rm = ((ENS[role] || {}).role || {});
        if (sel2 && !String(sel2.value || '').trim() && rm.model) {
          const hit = Array.prototype.find.call(sel2.options, (o) => o.value === rm.model && !o.disabled);
          if (hit) { sel2.value = rm.model; toast(`#${Number(i) + 1} 을 역할 모델 ${rm.provider}/${rm.model} 로 채웠습니다 — 다른 모델로 바꿔도 됩니다`); }
          else { sel2.focus(); toast(`#${Number(i) + 1} 의 모델을 고르세요 — 모델이 비면 호출되지 않습니다`); }
        } else if (sel2 && !String(sel2.value || '').trim()) { sel2.focus(); }
      }
      refreshEnsRole(role);
    });
    host.addEventListener('click', (ev) => {
      const b = ev.target.closest('[data-ens-fill]'); if (!b) return;
      ev.preventDefault();
      const role = b.dataset.ensFill, rm = ((ENS[role] || {}).role || {});
      if (!rm.model) return;
      let n = 0;
      for (let i = 0; i < ENS_MAX; i++) {
        const cb = host.querySelector(`[data-ens="${role}"][data-ens-m="${i}"][data-ens-f="enabled"]`);
        const sel2 = host.querySelector(`[data-ens="${role}"][data-ens-m="${i}"][data-ens-f="model"]`);
        if (!cb || !sel2 || !cb.checked || String(sel2.value || '').trim()) continue;
        const hit = Array.prototype.find.call(sel2.options, (o) => o.value === rm.model && !o.disabled);
        if (hit) { sel2.value = rm.model; n++; }
      }
      refreshEnsRole(role);
      toast(n ? `빈 멤버 ${n}개를 ${rm.provider}/${rm.model} 로 채웠습니다 — [저장 & 프로바이더 재로드]` : '채울 빈 멤버가 없습니다 (「쓰기」부터 켜세요)');
    });
  }
  /** 화면의 앙상블 입력 → llm_roles.<role>.ensemble 로 저장할 dict (아무것도 안 건드렸으면 undefined). */
  function ensembleSettings(role) {
    const get = (f) => $(`[data-ens="${role}"][data-ens-f="${f}"]:not([data-ens-m])`);
    const en = get('enabled'); if (!en) return undefined;          // 이 역할 행이 화면에 없다
    const members = [];
    for (let i = 0; i < ENS_MAX; i++) {
      const q = (f) => $(`[data-ens="${role}"][data-ens-m="${i}"][data-ens-f="${f}"]`);
      const model = ((q('model') || {}).value || '').trim();
      const enabled = !!((q('enabled') || {}).checked);
      const prov = ((q('provider') || {}).value || '').trim();
      const w = parseFloat((q('weight') || {}).value);
      if (!model && !enabled && !prov) { members.push({ enabled: false, provider: '', model: '', weight: 1.0 }); continue; }
      members.push({ enabled: enabled, provider: prov, model: model === '__custom__' ? '' : model, weight: isNaN(w) ? 1.0 : w });
    }
    const num = (f) => { const v = ((get(f) || {}).value || '').trim(); return v === '' ? undefined : Number(v); };
    const out = { enabled: !!en.checked, members: members };
    const wait = ((get('wait') || {}).value || ''); if (wait) out.wait = wait;
    // 실패 시 폴백: 체크는 항상 보내고(끈 상태를 파일에 남겨야 한다), 모드는 비우면 상속
    const fbEl = get('fallback_role_model'); if (fbEl) out.fallback_role_model = !!fbEl.checked;
    const fbm = ((get('fallback_mode') || {}).value || ''); if (fbm) out.fallback_mode = fbm;
    const t = num('timeout_s'); if (t !== undefined) out.timeout_s = t;
    const mr = num('min_results'); if (mr !== undefined) out.min_results = mr;
    const pr = ((get('prompt') || {}).value || '').trim(); if (pr) out.prompt = pr;
    const am = ((get('agg_model') || {}).value || '').trim(), ap = ((get('agg_provider') || {}).value || '').trim();
    if (am || ap) out.aggregator = { provider: ap, model: am === '__custom__' ? '' : am };
    return out;
  }
  // 역할 표의 마지막 두 행 — 임베딩과 리랭크(API).
  // 이 둘은 LLM 역할이 아니라 별도 경로지만 **"지금 무슨 모델이 쓰이나"** 라는 질문에는 같이 답해야 하므로
  // 같은 표에 넣고, 이 표 하나에서 전부 고칠 수 있게 한다 (예전에는 왼쪽 폼과 위쪽 요약표로 흩어져 있었다).
  function embedRerankRows(s, p, showPol) {
    const span = 1 + (showPol ? POLICY_COLS.length : 0);      // effort 열 + (보이면) 정책 열들
    const emb = p.embedder || {};
    const rrOn = !!String(s.rerank_url || '').trim();
    const embRow =
      `<tr class="extra-row" data-role="embed"><td title="벡터 검색 임베딩 (빌드·질의)"><b>embed</b><br><span class="muted small ell2">벡터 검색 임베딩</span></td>` +
      `<td>${sel('m-embed-provider', CATALOG.embed_providers || ['auto', 'hash', 'voyage', 'openai', 'ollama', 'st'], s.embed_provider)}</td>` +
      `<td>${modelSelect('m-embed-model', s.embed_model, '', false, '', 'embed')}</td>` +
      `<td colspan="${span}" class="small">` +
      `dim <input id="m-embed-dim" type="number" value="${s.embed_dim}" style="width:78px" title="${esc(STATE.settingHelp.embed_dim || '모든 차원 지원 · 변경 시 전체 리빌드')}"> ` +
      `dtype ${sel('m-embed-dtype', ['float32', 'float16'], s.embed_store_dtype)} ` +
      `batch <input id="m-embed-batch" type="number" value="${s.embed_batch}" style="width:62px"> ` +
      `max <input id="m-embed-batch-max" type="number" value="${s.embed_batch_max}" style="width:62px">` +
      `<div class="muted small">embed URL: <input id="m-openai-embed-url" value="${esc(s.openai_embed_base_url || '')}" placeholder="(비우면 openai_base_url)" style="width:200px"> ` +
      `model <input id="m-openai-embed" value="${esc(s.openai_embed_model)}" placeholder="text-embedding-3-small / bge-m3" style="width:190px"></div></td>` +
      `<td class="small">${esc(emb.name || '-')}/${esc(emb.model || '')}<br><span class="muted">dim=${emb.dim} · 변경 시 전체 리빌드</span></td>` +
      `<td>${emb.available ? '<span class="ok">available</span>' : '<span class="bad">unavailable</span>'}</td>` +
      `<td><button class="mini secondary" data-test-extra="embed" title="지금 입력한 값으로 임베딩 연결 확인">테스트</button></td></tr>`;
    const rrRow =
      `<tr class="extra-row" data-role="rerank_api"><td title="전용 리랭크 엔드포인트 — 역할 rerank(LLM 리랭크)와 다른 경로입니다"><b>rerank(API)</b><br><span class="muted small ell2">전용 리랭크 엔드포인트</span></td>` +
      `<td>${sel('m-rerank-style', CATALOG.rerank_providers || ['cohere', 'voyage'], s.rerank_api_style)}</td>` +
      `<td>${modelSelect('m-rerank-model', s.rerank_api_model || '', '', false, '', 'rerank')}</td>` +
      `<td colspan="${span}" class="small">url <input id="m-rerank-url" value="${esc(s.rerank_url)}" placeholder="http://host:8000/v1/rerank" style="width:280px">` +
      `<div class="muted small">비우면 API 리랭크를 쓰지 않는다. 튜닝 <code>rerank_method</code>=auto 면 URL 이 있을 때 API 를 먼저 쓴다. 키: .env RERANK_API_KEY</div></td>` +
      `<td class="small">${rrOn ? esc(s.rerank_api_style) + '/' + esc(s.rerank_api_model || '(모델 미지정)') : '<span class="muted">사용 안 함</span>'}</td>` +
      `<td>${rrOn ? '<span class="ok">설정됨</span>' : '<span class="muted">꺼짐</span>'}</td>` +
      `<td><button class="mini secondary" data-test-extra="rerank" title="지금 입력한 값으로 리랭크 엔드포인트 확인">테스트</button></td></tr>`;
    return embRow + rrRow;
  }

  async function loadModels() {
    const j = await api('/api/models'); const p = j.providers, s = j.settings, cat = p.catalog; STATE.providers = p;
    CATALOG = j.catalog_models || (await api('/api/models/catalog')) || CATALOG;
    PROVIDERS = CATALOG.providers || PROVIDERS;
    STATE.rolePolicy = j.policy || {};
    STATE.roleAttrs = j.role_attrs || [];
    ENS = j.ensemble || {};
    ENS_DEF = Object.assign({ wait: 'all', timeout_s: 120, min_results: 1, prompt: 'ensemble_merge' }, j.ensemble_defaults || {});
    ENS_MAX = j.ensemble_max_members || 3;
    // 임베딩·리랭크(API) 는 이제 오른쪽 "역할별" 표 안에서 직접 고친다 (한 표에서 전부 제어).
    // 여기에는 되돌아보기용 요약만 남긴다 — 입력칸을 두 군데 두면 어느 쪽이 적용되는지 헷갈린다.
    $('#embed-form').innerHTML =
      `<div class="muted small">임베딩·리랭크(API) 설정은 왼쪽 <b>「모델 · 역할 전체」</b> 표의 <code>embed</code> · <code>rerank(API)</code> 행에서 바꿉니다.<br>` +
      `지금: <b>${esc(p.embedder.name)}</b> model=${esc(p.embedder.model)} dim=${p.embedder.dim} available=${p.embedder.available}<br>` +
      `auto = VOYAGE_API_KEY 있으면 voyage → Ollama 에 bge-m3/nomic 있으면 ollama → hash.</div>`;
    $('#llm-form').innerHTML = `<label>llm_provider ${sel('m-llm-provider', PROVIDERS, s.llm_provider)}</label>` +
      `<label>llm_model ${modelSelect('m-llm-model', s.llm_model, '', false)}</label>` +
      `<label>llm_effort ${sel('m-llm-effort', cat.effort, s.llm_effort)} · answer_effort ${sel('m-answer-effort', cat.effort, s.answer_effort)}</label>` +
      `<label>ollama_url <input id="m-ollama-url" value="${esc(s.ollama_url)}"></label><label>ollama_model <input id="m-ollama-model" value="${esc(s.ollama_model)}"></label>` +
      `<label><input type="checkbox" id="m-fallbacks" ${s.llm_fallbacks ? 'checked' : ''}> llm_fallbacks (Anthropic server-side refusal fallback)</label>`;
    $('#endpoint-form').innerHTML = `<label>openai_base_url <input id="m-openai-url" value="${esc(s.openai_base_url)}" placeholder="https://gateway.corp/v1"> <small>키: .env OPENAI_API_KEY 또는 LLM_API_KEY (${p.openai && p.openai.key ? '설정됨' : '없음'}) · 사내 PAT 게이트웨이도 여기</small></label>` +
      `<label>openai_api_key_header ${sel('m-openai-key-header', ['authorization', 'api-key', 'x-api-key'], s.openai_api_key_header || 'authorization')} <small>authorization = "Bearer &lt;PAT&gt;", 그 외는 키 값 그대로</small></label>` +
      `<label>openai_extra_headers <input id="m-openai-extra" value="${esc(JSON.stringify(s.openai_extra_headers || {}))}" placeholder='{"X-Tenant":"modem"}' style="width:260px"> <small>JSON</small></label>` +
      // openai_embed_base_url · openai_embed_model 은 아래 「모델 · 역할 전체」 표의 embed 행에만 둔다.
      // 예전에는 여기에도 **같은 id** 로 있어서 DOM 에 중복 id 가 생겼고, 저장할 때 어느 칸의 값이 쓰이는지 알 수 없었다 (2026-09-19 수정).
      `<div class="muted small">임베딩 엔드포인트(<code>openai_embed_base_url</code> · <code>openai_embed_model</code>)는 아래 「모델 · 역할 전체」 표의 <code>embed</code> 행에서 고칩니다.</div>` +
      `<label>anthropic_base_url <input id="m-anthropic-url" value="${esc(s.anthropic_base_url || '')}" placeholder="(비우면 api.anthropic.com) https://gateway.corp"> <small>키: ANTHROPIC_API_KEY(x-api-key) 또는 ANTHROPIC_AUTH_TOKEN(Bearer PAT)</small></label>` +
      `<label>llm_timeout <input id="m-llm-timeout" type="number" value="${s.llm_timeout || 600}" style="width:80px"> retries <input id="m-llm-retries" type="number" value="${s.llm_retries}" style="width:60px"> backoff ${sel('m-llm-backoff', ['exponential', 'linear'], s.llm_retry_backoff || 'exponential')} <input id="m-llm-backoff-s" type="number" step="0.5" value="${s.llm_retry_backoff_s}" style="width:60px">초 <small>역할별로 다르게 하려면 왼쪽 표의 '재시도·타임아웃 열 보기'</small></label>` +
      `<label>llm_budget_s <input id="m-llm-budget" type="number" value="${s.llm_budget_s || 0}" style="width:70px" title="한 호출의 재시도 포함 총 시간 예산(0=무제한). 넘으면 대체 경로(추출식 답변)로"> 회로차단 <input id="m-llm-circuit" type="number" value="${s.llm_circuit_failures}" style="width:50px" title="연속 실패 n회 → cooldown 동안 즉시 실패"> / <input id="m-llm-cooldown" type="number" value="${s.llm_circuit_cooldown_s}" style="width:60px">초</label>` +
      // 2026-09-19: 이 화면에서 못 고치던 LLM 동작 키를 마저 붙인다 (화면 감사에서 발견).
      // 반복 억제 3종은 작은 모델이 같은 구절을 수십 번 되풀이하는 고장을 줄이는 값이고,
      // http_retries·backoff_max_s 는 재시도 정책의 나머지 두 칸이라 같은 자리에 있어야 한다.
      `<label>backoff 상한 <input id="m-llm-backoff-max" type="number" step="0.5" value="${s.llm_retry_backoff_max_s}" style="width:70px" title="${esc(STATE.settingHelp.llm_retry_backoff_max_s || '')}">초 ` +
      `http 재시도 <input id="m-llm-http-retries" type="number" value="${s.llm_http_retries}" style="width:56px" title="${esc(STATE.settingHelp.llm_http_retries || '')}">회</label>` +
      `<label>반복 억제 <small>같은 구절이 되풀이될 때 올린다</small><br>` +
      `frequency <input id="m-llm-freq-pen" type="number" step="0.1" value="${s.llm_frequency_penalty}" style="width:64px" title="${esc(STATE.settingHelp.llm_frequency_penalty || '')}"> ` +
      `presence <input id="m-llm-pres-pen" type="number" step="0.1" value="${s.llm_presence_penalty}" style="width:64px" title="${esc(STATE.settingHelp.llm_presence_penalty || '')}"> ` +
      `repeat(ollama) <input id="m-llm-repeat-pen" type="number" step="0.05" value="${s.llm_repeat_penalty}" style="width:64px" title="${esc(STATE.settingHelp.llm_repeat_penalty || '')}"></label>` +
      `<div class="muted small">rerank(API) 의 URL·모델·style 은 왼쪽 「모델 · 역할 전체」 표의 <code>rerank(API)</code> 행에 있습니다 (역할 <b>rerank</b> = LLM 리랭크와 다른 경로). ` +
      `앙상블 기본값(<code>llm_ensemble_defaults</code>)과 역할별 앙상블은 <b>🧭 Pipeline › 앙상블</b> 에서 고칩니다.</div>`;
    $('#models-note').innerHTML = `Python ${p.python} / SQLite ${p.sqlite}. headless 에이전트: ${Object.keys(p.agents || {}).map(esc).join(', ')} (agents.json).`;
    const showPol = $('#roles-show-policy') && $('#roles-show-policy').checked;
    const polHead = showPol ? POLICY_COLS.map((c) => `<th title="비우면 전역값 상속">${c[1]}</th>`).join('') : '';
    // '용도' 는 열을 하나 더 차지해 표를 1800px 까지 넓혔다 → 역할 칸의 툴팁으로 접었다 (2026-09-19)
    $('#roles-table').innerHTML = '<table class="roles"><tr><th title="마우스를 올리면 그 역할이 무엇에 쓰이는지 나옵니다">역할</th><th>provider</th><th>model</th><th>effort</th>' + polHead + '<th>실제 인스턴스</th><th>상태</th><th></th></tr>' + j.roles.map((role) => {
      const r = p.roles[role]; const cfg = (s.llm_roles || {})[role] || {};
      const inh = `(상속: 전역 ${esc(s.llm_provider)})`;
      const eff = (j.policy || {})[role] || {};
      const polCells = showPol ? POLICY_COLS.map((c) => `<td><input id="r-${role}-${c[0]}" type="number" step="${c[0] === 'backoff_s' ? '0.5' : '1'}" value="${esc(cfg[c[0]] == null ? '' : cfg[c[0]])}" placeholder="${esc(eff[c[0]] == null ? '' : eff[c[0]])}" style="width:72px" title="비우면 전역값(${esc(eff[c[0]])}) 상속"></td>`).join('') : '';
      const circ = (r.circuit || {});
      const circTxt = circ.open_until && circ.open_until * 1000 > Date.now() ? `<br><span class="bad" title="연속 실패로 잠시 건너뜁니다">회로 차단</span>` : '';
      return `<tr data-role="${role}"><td title="${esc(cat.roles[role] || '')}"><b>${role}</b><br><span class="muted small ell2">${esc(cat.roles[role] || '')}</span></td><td><input id="r-${role}-provider" list="dl-providers" value="${esc(cfg.provider || '')}" placeholder="${inh}" title="비우면 전역 llm_provider(${esc(s.llm_provider)})를 상속" style="width:140px"></td><td>${modelSelect('r-' + role + '-model', cfg.model || '', role, true, s.llm_model)}</td><td>${sel('r-' + role + '-effort', cat.effort, cfg.effort || '', true)}</td>${polCells}<td class="small">${esc(r.name)}/${esc(r.model)}<br><span class="muted">effort=${r.configured.effort} · ${(r.policy || {}).timeout_s}s × ${(r.policy || {}).retries + 1}</span></td><td>${r.available ? '<span class="ok">available</span>' : '<span class="bad" title="' + esc(r.reason || '') + '">unavailable</span>'}${r.available ? '' : '<br><span class="reason small">' + esc(r.reason || '') + '</span>'}${circTxt}<br><span class="muted small">calls ${r.stats.calls || 0} · tok ${fmtK((r.stats.input_tokens || 0) + (r.stats.output_tokens || 0))}${r.stats.retries ? ' · 재시도 ' + r.stats.retries : ''}${r.stats.errors ? ' · 실패 ' + r.stats.errors : ''}</span></td><td><button class="mini secondary" data-test="${role}" title="이 행에 입력한(아직 저장 안 한) provider/model 로 연결 테스트">테스트</button></td></tr>` + ensembleStatusRow(role);
    }).join('') + embedRerankRows(s, p, showPol) + `</table><datalist id="dl-providers">${PROVIDERS.map((x) => `<option value="${x}">`).join('')}</datalist>` +
      `<div class="muted small" style="margin-top:6px"><b>상속</b> = 비워 두면 오른쪽 "전역 LLM 기본값"(llm_provider / llm_model / llm_effort / llm_timeout …)을 그대로 씀. 역할별 타임아웃·재시도·backoff·총예산·<b>출력토큰(max_tokens)</b>은 '재시도·타임아웃 열 보기' 를 켜면 편집할 수 있고 <code>config.json llm_roles.&lt;role&gt;</code> 에 저장된다. 회색 글씨는 지금 상속 중인 값이다 — 비워 두면 그 값을 쓴다(출력토큰은 단계 기본값: 라우터 200 · 확장/리랭크 400 · 검증 1500 · 요약 800 · 포렌식 1200 · 리뷰/답변 3000 · 추출 4000). <b>auto</b> = ANTHROPIC_API_KEY 가 있으면 anthropic → 없으면 Ollama → 둘 다 없으면 none(추출식 답변). openai / headless 는 provider 에 직접 적는다. "테스트" 는 저장 전 입력값으로도 동작하며, 실제 적용은 "저장 &amp; 프로바이더 재로드". 최종 실패 시에는 추출식 답변·로컬 리랭크 등 대체 경로로 계속 동작합니다.</div>`;
    wireModelSelects($('#roles-table')); wireModelSelects($('#llm-form'));
    wireModelSelects($('#embed-form')); wireModelSelects($('#endpoint-form'));
    renderCatalog();
    // 앙상블은 여기서 고치지 않는다 — 🧭 Pipeline › 앙상블 이 유일한 편집 자리다.
    $$('#roles-table [data-ens-goto]').forEach((b) => b.onclick = () => {
      switchGroup('pipeline'); switchTab('ensemble');
      setTimeout(() => {
        const row = document.querySelector(`#ens-roles [data-ens-card="${b.dataset.ensGoto}"]`);
        if (row) { row.scrollIntoView({ behavior: 'smooth', block: 'center' }); row.classList.add('flash'); setTimeout(() => row.classList.remove('flash'), 1500); }
      }, 400);
    });
    $$('#roles-table [data-test]').forEach((b) => b.onclick = async () => {
      b.disabled = true;
      // 저장하지 않은 폼 값(전역 + 이 역할)을 요청 단위 오버라이드로 보내 실제 연결을 확인한다
      const role = b.dataset.test, ov = { llm_provider: $('#m-llm-provider').value, llm_model: $('#m-llm-model').value.trim(), ollama_url: $('#m-ollama-url').value.trim(), ollama_model: $('#m-ollama-model').value.trim(), openai_base_url: $('#m-openai-url').value.trim(), openai_api_key_header: $('#m-openai-key-header').value, anthropic_base_url: $('#m-anthropic-url').value.trim() };
      const pv = $('#r-' + role + '-provider').value.trim(), m = $('#r-' + role + '-model').value.trim();
      ov[role + '_provider'] = pv; ov[role + '_model'] = m === '__custom__' ? '' : m;
      const r = await api('/api/models/test', { which: [role], overrides: ov }); b.disabled = false; renderTest(r);
    });
    // embed · rerank(API) 행의 테스트 — 저장하지 않은 입력값으로 확인한다 (역할 행과 같은 방식)
    $$('#roles-table [data-test-extra]').forEach((b) => b.onclick = async () => {
      b.disabled = true;
      const r = await api('/api/models/test', { which: [b.dataset.testExtra], overrides: modelsSettings() });
      b.disabled = false; renderTest(r);
    });
    // (dl-llm-models 채우기는 없앴다 — 사이드바의 프로바이더/모델 오버라이드와 함께 사라진 요소다.
    //  이 화면의 모델 선택은 modelSelect() 가 만드는 <select> 와 dl-providers 를 쓴다.)
    const ag = await api('/api/agents'); $('#agents-json').value = JSON.stringify(ag.agents, null, 2);
  }
  // ---------------- 모델 카탈로그 (models.json) ----------------
  function catProviders(kind) {
    if (kind === 'embed') return CATALOG.embed_providers || ['auto', 'hash', 'voyage', 'openai', 'ollama', 'st'];
    if (kind === 'rerank') return CATALOG.rerank_providers || ['cohere', 'voyage'];
    return CATALOG.providers || PROVIDERS;
  }
  function fillCatProviders() {
    const provSel = $('#cat-provider'); if (!provSel) return;
    const kind = ($('#cat-kind') && $('#cat-kind').value) || 'llm';
    provSel.innerHTML = catProviders(kind).map((x) => `<option>${esc(x)}</option>`).join('');
  }
  /** 지금 설정에 실제로 쓰이고 있는 모델 id 집합 (역할별 model + 전역 llm_model + 임베딩 모델). */
  function usedModelIds() {
    const s = (STATE.settings || {}), out = new Set();
    const add = (v) => { if (v) out.add(String(v)); };
    add(s.llm_model); add(s.embed_model); add(s.openai_embed_model); add(s.rerank_api_model);
    Object.values(s.llm_roles || {}).forEach((r) => add((r || {}).model));
    const p = STATE.providers || {};
    Object.values(p.roles || {}).forEach((r) => add((r || {}).model));
    if (p.embedder) add(p.embedder.model);
    return out;
  }
  function renderCatalog() {
    fillCatProviders();
    const t = $('#cat-table'); if (!t) return;
    const all = (CATALOG.models || []).map((m) => Object.assign({ kind: 'llm' }, m))
      .concat((CATALOG.embed || []).map((m) => Object.assign({ kind: 'embed' }, m)))
      .concat((CATALOG.rerank || []).map((m) => Object.assign({ kind: 'rerank' }, m)));
    // provider 필터 목록은 실제 있는 값으로 채운다 (선택은 유지)
    const pf = $('#cat-filter-prov');
    if (pf) {
      const provs = [...new Set(all.map((m) => m.provider).filter(Boolean))].sort();
      const cur = pf.value;
      pf.innerHTML = '<option value="">전체</option>' + provs.map((x) => `<option${x === cur ? ' selected' : ''}>${esc(x)}</option>`).join('');
    }
    const q = (($('#cat-q') || {}).value || '').trim().toLowerCase();
    const kind = (($('#cat-filter-kind') || {}).value || '');
    const prov = (($('#cat-filter-prov') || {}).value || '');
    const onlyUsed = !!($('#cat-only-used') || {}).checked, onlyEnabled = !!($('#cat-only-enabled') || {}).checked;
    const used = onlyUsed ? usedModelIds() : null;
    const rows = all.filter((m) => {
      if (kind && (m.kind || 'llm') !== kind) return false;
      if (prov && m.provider !== prov) return false;
      if (onlyEnabled && m.enabled === false) return false;
      if (used && !used.has(String(m.id || ''))) return false;
      if (q && !((String(m.id || '') + ' ' + (m.label || '') + ' ' + (m.notes || '')).toLowerCase().includes(q))) return false;
      return true;
    });
    const KIND_KO = { llm: 'LLM', embed: '임베딩', rerank: '리랭크' };
    // 설명은 한 줄로 줄이고 전문은 title 로 — 예전에는 이 열이 길어 표가 화면을 넘겼다 (2026-09-19)
    t.innerHTML = '<table class="cat"><tr><th>id</th><th>종류</th><th>provider</th><th>설명</th><th>역할</th><th title="카탈로그에서 사용(enabled) 표시">사용</th><th></th></tr>' +
      rows.map((m) => {
        const desc = (m.label || '') + (m.notes ? ' — ' + m.notes : '');
        const isUsed = usedModelIds().has(String(m.id || ''));
        return `<tr class="${isUsed ? 'in-use' : ''}"><td class="mono small" title="${esc(m.id || '')}${isUsed ? ' (지금 설정에서 사용 중)' : ''}">${esc(m.id || '(hash)')}${isUsed ? ' <span class="pill ok" title="지금 역할·전역 설정에서 쓰고 있습니다">쓰는 중</span>' : ''}</td>` +
          `<td class="small">${esc(KIND_KO[m.kind || 'llm'] || m.kind)}</td><td class="small">${esc(m.provider)}</td>` +
          `<td class="small muted ell" title="${esc(desc)}">${esc(desc)}</td>` +
          `<td class="small ell" title="${esc((m.roles || []).join(', ') || '모든 역할')}">${esc((m.roles || []).join(',') || '*')}</td>` +
          `<td class="c">${m.enabled === false ? '<span class="muted" title="사용 안 함">✘</span>' : '<span class="ok" title="사용">✔</span>'}</td>` +
          `<td class="c"><button class="mini secondary" data-cat-del="${esc(m.id)}" data-cat-prov="${esc(m.provider)}" title="카탈로그에서 이 줄만 지웁니다 (설정에서 쓰고 있어도 동작에는 영향 없음)">삭제</button></td></tr>`;
      }).join('') + '</table>' +
      (rows.length ? '' : '<div class="muted small">걸러진 결과가 없습니다 — 검색어나 필터를 지워 보세요.</div>') +
      `<div class="muted small">파일: <code>${esc(CATALOG.path || '')}</code> · CLI: <code>models list</code> · <code>models catalog add &lt;id&gt; --provider … [--kind embed|rerank]</code></div>`;
    const cnt = $('#cat-count');
    if (cnt) cnt.textContent = `${rows.length}/${all.length}개 표시 · LLM ${(CATALOG.models || []).length} · 임베딩 ${(CATALOG.embed || []).length} · 리랭크 ${(CATALOG.rerank || []).length}`;
    $$('#cat-table [data-cat-del]').forEach((b) => b.onclick = async () => {
      if (!confirm('카탈로그에서 ' + b.dataset.catDel + ' 을(를) 삭제합니다. (설정에서 이미 쓰고 있어도 동작에는 영향 없음)')) return;
      await api('/api/models/catalog', { action: 'remove', id: b.dataset.catDel, provider: b.dataset.catProv });
      CATALOG = await api('/api/models/catalog'); renderCatalog(); loadModels();
    });
  }
  if ($('#btn-cat-add')) $('#btn-cat-add').onclick = async () => {
    const id = $('#cat-id').value.trim(); if (!id) { toast('모델 id 를 입력하세요'); return; }
    const m = { id: id, provider: $('#cat-provider').value, label: $('#cat-label').value.trim() || id, roles: $('#cat-roles').value.split(',').map((x) => x.trim()).filter(Boolean), enabled: true };
    const kind = ($('#cat-kind') && $('#cat-kind').value) || 'llm';
    if (kind !== 'llm') m.kind = kind;
    const j = await api('/api/models/catalog', { action: 'add', model: m });
    if (j && j.ok) { toast('카탈로그에 추가됨'); $('#cat-id').value = ''; $('#cat-label').value = ''; CATALOG = j.catalog || CATALOG; renderCatalog(); loadModels(); }
  };
  if ($('#btn-cat-refresh')) $('#btn-cat-refresh').onclick = async () => { CATALOG = await api('/api/models/catalog'); renderCatalog(); toast('카탈로그 새로고침'); };
  // 카탈로그 걸러 보기 — 서버를 부르지 않고 이미 받은 목록만 다시 그린다 (2026-09-19)
  ['#cat-q', '#cat-filter-kind', '#cat-filter-prov', '#cat-only-used', '#cat-only-enabled'].forEach((sel) => {
    const el = $(sel); if (!el) return;
    el.addEventListener(el.tagName === 'INPUT' && el.type !== 'checkbox' ? 'input' : 'change', () => renderCatalog());
  });
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
    // 결과를 기억해 두었다가 모델 드롭다운에서 **붙지 않는 모델을 회색 처리**한다
    Object.keys(r).forEach((k) => { const x = r[k]; if (x && x.model) noteStatus(x.provider, x.model, x.ok !== false && x.live_ok !== false, x.detail || x.live_detail); });
    $('#models-test').innerHTML = '<table><tr><th>대상</th><th>provider/model</th><th>ok</th><th>ms</th><th>detail</th></tr>' + Object.keys(r).map((k) => { const x = r[k]; return `<tr><td><b>${k}</b></td><td>${esc(x.provider || x.url || '')}/${esc(x.model)}${x.dim ? ' d=' + x.dim : ''}</td><td>${x.ok ? '<span class="ok">✔</span>' : '<span class="bad">✘</span>'}</td><td class="num">${fmt(x.ms, 0)}</td><td class="small">${esc(x.detail || '')}${x.models ? '<br><span class="muted">models: ' + esc(x.models.slice(0, 12).join(', ')) + '</span>' : ''}${'live_ok' in x ? '<br><b>실제 호출:</b> ' + (x.live_ok ? '<span class="ok">✔</span>' : '<span class="bad">✘</span>') + ' ' + fmt(x.live_ms, 0) + 'ms ' + esc(x.live_detail || '') : ''}</td></tr>`; }).join('') + '</table>';
    renderCatalog();     // 회색 처리를 카탈로그 표·드롭다운에 반영
    if ($('#roles-show-policy')) loadModelsRedrawSelects();
  }
  /** 연결 상태가 바뀌었을 때 드롭다운만 다시 그린다 (값은 유지). */
  function loadModelsRedrawSelects() {
    $$('#roles-table select[data-model-select]').forEach((s2) => {
      const cur = s2.value, id = s2.id;
      const role = (s2.closest('tr[data-role]') || {}).dataset ? s2.closest('tr[data-role]').dataset.role : '';
      const kind = id.indexOf('embed') >= 0 ? 'embed' : id.indexOf('rerank') >= 0 ? 'rerank' : 'llm';
      const box = document.createElement('div');
      box.innerHTML = modelSelect(id, cur, kind === 'llm' ? role : '', true, '', kind);
      const fresh = box.firstElementChild;
      for (const a of s2.attributes) if (a.name.startsWith('data-ens')) fresh.setAttribute(a.name, a.value);
      s2.replaceWith(fresh);
    });
    wireModelSelects($('#roles-table'));
  }
  // 드롭다운의 '직접 입력…' 을 고르고 취소하면 값이 __custom__ 으로 남는다 — 설정에 그대로 저장되면 안 된다.
  function mval(id) { const e = $(id); const v = e ? String(e.value || '').trim() : ''; return v === '__custom__' ? '' : v; }
  function modelsSettings() {
    const st = { embed_provider: $('#m-embed-provider').value, embed_model: mval('#m-embed-model'), embed_dim: parseInt($('#m-embed-dim').value, 10), embed_store_dtype: $('#m-embed-dtype').value, embed_batch: parseInt($('#m-embed-batch').value, 10), embed_batch_max: parseInt($('#m-embed-batch-max').value, 10),
      llm_provider: $('#m-llm-provider').value, llm_model: mval('#m-llm-model'), llm_effort: $('#m-llm-effort').value, answer_effort: $('#m-answer-effort').value,
      ollama_url: $('#m-ollama-url').value.trim(), ollama_model: $('#m-ollama-model').value.trim(), llm_fallbacks: $('#m-fallbacks').checked,
      openai_base_url: $('#m-openai-url').value.trim(), openai_api_key_header: $('#m-openai-key-header').value, openai_embed_base_url: $('#m-openai-embed-url').value.trim(), openai_embed_model: $('#m-openai-embed').value.trim(),
      anthropic_base_url: $('#m-anthropic-url').value.trim(), llm_timeout: parseInt($('#m-llm-timeout').value, 10) || 600,
      llm_retries: parseInt($('#m-llm-retries').value, 10), llm_retry_backoff: $('#m-llm-backoff').value, llm_retry_backoff_s: parseFloat($('#m-llm-backoff-s').value),
      llm_budget_s: parseInt($('#m-llm-budget').value, 10) || 0, llm_circuit_failures: parseInt($('#m-llm-circuit').value, 10), llm_circuit_cooldown_s: parseInt($('#m-llm-cooldown').value, 10),
      llm_retry_backoff_max_s: parseFloat($('#m-llm-backoff-max').value), llm_http_retries: parseInt($('#m-llm-http-retries').value, 10),
      llm_frequency_penalty: parseFloat($('#m-llm-freq-pen').value), llm_presence_penalty: parseFloat($('#m-llm-pres-pen').value), llm_repeat_penalty: parseFloat($('#m-llm-repeat-pen').value),
      rerank_url: $('#m-rerank-url').value.trim(), rerank_api_model: mval('#m-rerank-model'), rerank_api_style: $('#m-rerank-style').value, llm_roles: {} };
    try { st.openai_extra_headers = JSON.parse($('#m-openai-extra').value.trim() || '{}'); } catch (e) { toast('openai_extra_headers JSON 오류 — 무시'); }
    STATE.roles.forEach((role) => {
      const c = {}; const pv = $('#r-' + role + '-provider').value.trim(), mEl = $('#r-' + role + '-model'), m = mEl ? mEl.value.trim() : '', e = $('#r-' + role + '-effort').value;
      if (pv) c.provider = pv;
      if (m && m !== '__custom__') c.model = m;
      if (e) c.effort = e;
      POLICY_COLS.forEach((col) => { const el = $('#r-' + role + '-' + col[0]); if (el && el.value !== '') c[col[0]] = col[0] === 'backoff_s' ? parseFloat(el.value) : parseInt(el.value, 10); });
      // 빈 칸으로 지우면 그 역할의 그 항목은 상속으로 되돌린다 (남아 있던 값 제거)
      POLICY_COLS.forEach((col) => { const el = $('#r-' + role + '-' + col[0]); if (el && el.value === '' && c[col[0]] !== undefined) delete c[col[0]]; });
      // 앙상블: 켜져 있거나, 껐더라도 멤버를 적어 뒀으면 그대로 보관한다 (껐다 켤 때 값이 날아가지 않게).
      // 2026-09-19: 편집기는 🧭 Pipeline › 앙상블 로 옮겼다. 이 화면에 입력칸이 없으면 **서버에서 읽은 원본을 그대로** 다시 보낸다 —
      // 그러지 않으면 모델 저장 한 번에 남의 화면에서 만든 앙상블 설정이 통째로 지워진다.
      const en = ensembleSettings(role) || ((ENS[role] || {}).raw);
      if (en && (en.enabled || (en.members || []).some((m) => m.model || m.provider))) c.ensemble = en;
      if (Object.keys(c).length) st.llm_roles[role] = c;
    });
    return st;
  }
  $('#btn-models-save').onclick = async () => {
    const j = await api('/api/models/set', { settings: modelsSettings() });
    const msg = '저장됨 · answer=' + j.providers.roles.answer.name + '/' + j.providers.roles.answer.model + ' · embed=' + j.providers.embedder.name;
    $('#models-msg').textContent = msg;
    if ($('#roles-msg')) $('#roles-msg').textContent = msg;
    // 역할별 모델은 config.json 값이다 → 🧭 Pipeline 단계 상세·시간 제한도 같은 값을 보게 방송한다
    loadModels(); LW.settingsChanged('models');
  };
  $('#btn-models-test').onclick = async () => { $('#models-test').textContent = '테스트 중…'; renderTest(await api('/api/models/test', { overrides: modelsSettings() })); };
  if ($('#btn-models-test-live')) $('#btn-models-test-live').onclick = async () => { $('#models-test').textContent = '실제 호출 테스트 중… (역할별 provider/model 당 1회, 수십 초 걸릴 수 있음)'; renderTest(await api('/api/models/test', { overrides: modelsSettings(), live: true })); };
  $('#btn-models-reload').onclick = loadModels;
  if ($('#cat-kind')) $('#cat-kind').onchange = fillCatProviders;
  // 카탈로그 전체 연결 테스트 (계획 0918 §0.1-d · CLI `models test --catalog [--live]` · POST /api/models/test_catalog).
  // 역할 표의 "연결 테스트" 는 역할에 **설정된** 짝만 보지만, 이것은 models.json 의 enabled 항목 전부를 (provider, model) 로 ping 해
  // "설정에 쓰기 전에 무엇이 실제로 연결되는지" 를 한 표로 보인다. 2026-09-18 까지는 버튼만 있고 핸들러가 없었다.
  function renderCatTest(j) {
    const out = $('#cat-test-result'); if (!out) return;
    if (!j || j.error) { out.textContent = (j && j.error) || '오류'; return; }
    const rows = j.rows || [];
    // 카탈로그 전체 테스트 결과 → 드롭다운 회색 처리의 근거
    rows.forEach((r) => noteStatus(r.provider, r.id, r.ok !== false && r.live_ok !== false, r.detail || r.live_detail));
    renderCatalog(); loadModelsRedrawSelects();
    const cell = (r) => {
      const ok = r.ok ? '<span class="ok">OK</span>' : '<span class="bad">FAIL</span>';
      const live = (r.live_ok === undefined) ? '' : (r.live_ok ? ' · live OK ' + (r.live_ms || 0) + 'ms' : ' · live FAIL ' + esc(r.live_detail || ''));
      return `<tr><td>${esc(r.kind || '')}</td><td>${esc(r.provider || '')}</td><td><code>${esc(r.id || '')}</code> ${esc(r.label || '')}</td><td>${ok}${live}</td><td>${r.ms !== undefined ? r.ms + 'ms' : ''}</td><td class="muted">${esc(r.detail || '')}</td></tr>`;
    };
    out.innerHTML = `<div class="muted">${j.ok_n || 0}/${j.n || rows.length} 연결 · ${j.live ? '실제 완성 호출 포함' : 'ping 만 (실제 호출까지는 --live)'} · ${esc(j.path || '')}</div>` +
      `<div class="tbl-wrap"><table class="tbl"><thead><tr><th>종류</th><th>provider</th><th>모델</th><th>결과</th><th>ms</th><th>상세</th></tr></thead><tbody>${rows.map(cell).join('')}</tbody></table></div>` +
      (rows.length ? '' : '<div class="muted">카탈로그에 enabled 항목이 없습니다 (models.json)</div>');
  }
  // 가용 모델 자동 연결 (2026-09-19): 카탈로그 전체 연결 테스트 → 연결되는 모델만 골라 역할에 배정.
  // 제안을 먼저 표로 보여 주고, 역할별 체크박스로 고른 것만 저장한다 — 눌렀더니 전부 바뀌어 있는 일이 없게.
  let AUTOMAP = null;
  function renderAutomap(j) {
    AUTOMAP = j;
    const box = $('#cat-automap'); if (!box) return;
    if (!j || j.error) { box.innerHTML = `<div class="bad">${esc((j && (j.detail || j.error)) || '자동 매핑 실패')}</div>`; return; }
    const pr = j.proposal || {};
    const keys = Object.keys(pr);
    const changed = keys.filter((k) => pr[k].changed && pr[k].ok);
    const row = (k) => {
      const x = pr[k];
      const cls = !x.ok ? 'bad' : x.changed ? 'ok' : 'muted';
      return `<tr class="${x.changed ? 'changed' : ''}">` +
        `<td>${x.ok && x.changed ? `<input type="checkbox" data-am="${esc(k)}" checked>` : ''}</td>` +
        `<td><b>${esc(k)}</b>${x.kind !== 'llm' ? ` <span class="pill">${esc(x.kind)}</span>` : ''}</td>` +
        `<td class="mono small muted">${esc(x.current || '')}</td>` +
        `<td class="mono small ${cls}">${x.ok ? esc(x.provider + '/' + (x.model || '(hash)')) : '연결되는 모델 없음'}${x.changed ? '' : ' <span class="muted">(그대로)</span>'}</td>` +
        `<td class="small muted">${esc(x.why || '')}${x.warn ? `<div class="warntxt">${esc(x.warn)}</div>` : ''}</td></tr>`;
    };
    box.innerHTML =
      `<div class="row" style="margin-top:6px"><b>자동 매핑 제안</b>` +
      `<span class="muted small">연결 OK: LLM ${j.candidates.llm} · 임베딩 ${j.candidates.embed} · 리랭크 ${j.candidates.rerank} · ${esc(j.note)}</span></div>` +
      '<div class="tbl-wrap"><table class="tbl"><thead><tr><th style="width:24px"></th><th>역할</th><th>지금</th><th>제안</th><th>이유</th></tr></thead><tbody>' +
      keys.map(row).join('') + '</tbody></table></div>' +
      (changed.length
        ? `<div class="row"><button class="mini" id="btn-am-apply">고른 ${changed.length}개를 config.json 에 저장 (admin)</button>` +
          '<span class="muted small">저장하면 모든 사용자의 서버 기본값이 바뀌고 프로바이더가 다시 만들어집니다.</span></div>'
        : '<div class="muted small">바꿀 것이 없습니다 — 이미 연결되는 모델이 들어 있습니다.</div>');
    const ap = $('#btn-am-apply');
    if (ap) ap.onclick = async () => {
      const pick = $$('#cat-automap [data-am]').filter((c) => c.checked).map((c) => c.dataset.am);
      if (!pick.length) { toast('저장할 역할을 고르세요'); return; }
      if (!confirm(`역할 ${pick.length}개의 모델을 config.json 에 저장합니다:\n\n` +
        pick.map((k) => `${k} → ${pr[k].provider}/${pr[k].model || '(hash)'}`).join('\n'))) return;
      // 고른 것만 반영한다: 서버의 apply 는 제안 전체를 쓰므로, 여기서는 models/set 으로 고른 역할만 보낸다
      const settings = { llm_roles: {} };
      pick.forEach((k) => {
        const x = pr[k];
        if (x.kind === 'llm') settings.llm_roles[k] = { provider: x.provider, model: x.model };
        else if (x.kind === 'embed') { settings.embed_provider = x.provider; settings.embed_model = x.model; }
        else if (x.kind === 'rerank') { settings.rerank_api_style = x.provider; settings.rerank_api_model = x.model; }
      });
      if (!Object.keys(settings.llm_roles).length) delete settings.llm_roles;
      const r = await api('/api/models/set', { settings: settings, _confirm: true });
      if (!r || r.error) { toast('저장 실패: ' + ((r && r.error) || '')); return; }
      toast('자동 연결 저장됨 (' + pick.length + '개)');
      await LW.settingsChanged('models');
      $('#cat-automap').innerHTML = '';
    };
  }
  if ($('#btn-cat-automap')) $('#btn-cat-automap').onclick = async () => {
    const live = !!($('#cat-test-live') && $('#cat-test-live').checked);
    $('#cat-automap').innerHTML = '<div class="muted small">' + (live ? '실제 호출까지 확인하며' : '연결을 확인하며') + ' 가용 모델을 고르는 중… (카탈로그 전체)</div>';
    renderAutomap(await api('/api/models/automap', { live: live }));
  };
  if ($('#btn-cat-test')) $('#btn-cat-test').onclick = async () => {
    const live = !!($('#cat-test-live') && $('#cat-test-live').checked);
    $('#cat-test-result').textContent = live ? '카탈로그 전체 실제 호출 테스트 중… (항목마다 완성 1회, 수십 초)' : '카탈로그 전체 ping 중…';
    renderCatTest(await api('/api/models/test_catalog', { live: live }));
  };
  // 파일을 서버 밖에서 고쳤을 때 — 디스크의 config.json 을 서버가 다시 읽는다 (표 위/아래 두 버튼이 같은 동작)
  async function reloadFromFile(msgSel) {
    const m = $(msgSel); if (m) m.textContent = 'config.json 을 다시 읽는 중…';
    const r = await api('/api/config', { action: 'reload' });
    if (r && r.ok) { if (m) m.textContent = 'config.json 을 다시 읽었습니다 · ' + (r.path || ''); toast('config.json 다시 읽음'); }
    else if (m) m.textContent = '';
    await loadModels(); await LW.settingsChanged('config');
  }
  if ($('#btn-models-reload-file')) $('#btn-models-reload-file').onclick = () => reloadFromFile('#models-msg');
  // 표 머리글의 같은 조작 (표만 보고 있을 때 위로 올라가지 않아도 되게)
  if ($('#btn-roles-reload')) $('#btn-roles-reload').onclick = loadModels;
  if ($('#btn-roles-reload-file')) $('#btn-roles-reload-file').onclick = () => reloadFromFile('#roles-msg');
  if ($('#btn-roles-save')) $('#btn-roles-save').onclick = () => $('#btn-models-save').click();
  $('#btn-agents-save').onclick = async () => { let a; try { a = JSON.parse($('#agents-json').value); } catch (e) { toast('JSON 오류'); return; } await api('/api/agents', { agents: a }); toast('agents.json 저장됨'); };
  loaders.models = loadModels;

  // ---------------- 🧭 Pipeline › 앙상블 (편집은 여기 한 곳) ----------------
  // 왜 Pipeline 아래인가: 앙상블은 "모델을 무엇으로 쓰나" 가 아니라 "이 역할의 요청 하나가 어떻게 처리되나" 라는
  // **흐름**의 문제다. 단계·토글·튜닝과 같은 자리에 두어야 "answer 단계가 3개 모델로 갈라진다" 가 한눈에 읽힌다.
  function ensDiagram() {
    return '<div class="ens-flow big">' +
      '<span class="step">역할 요청<small>answer · rerank …</small></span><span class="ar">→</span>' +
      '<span class="step off">앙상블 OFF<small>역할 모델 1회</small></span>' +
      '<span class="or">또는</span>' +
      '<span class="step on">앙상블 ON<small>멤버 최대 3개 <b>동시</b> 호출 · 같은 프롬프트</small></span><span class="ar">→</span>' +
      '<span class="step">성공 1개<small>그 답을 그대로 (취합 없음)</small></span>' +
      '<span class="or">또는</span>' +
      '<span class="step">성공 2개 이상<small>취합 LLM 1회 · prompts/ensemble_merge.md</small></span><span class="ar">→</span>' +
      '<span class="step">최종 결과</span></div>';
  }
  async function loadEnsemble() {
    const j = await api('/api/models');
    if (!j || j.error) { $('#ens-roles').innerHTML = `<div class="bad">${esc((j && j.error) || '읽지 못했습니다')}</div>`; return; }
    // /api/models 가 주는 카탈로그 키는 `catalog_models` 다 (`catalog` 는 없는 키였다).
    // 없는 키를 읽는 바람에 Settings › 모델 을 먼저 열지 않고 이 탭으로 바로 오면 CATALOG 가 빈 채로 남아
    // **멤버 모델 드롭다운에 고를 것이 하나도 없었다** — 화면은 "(비움)" 만 있는 잠긴 칸처럼 보였다 (2026-09-20).
    CATALOG = j.catalog_models || CATALOG; MODEL_STATUS = MODEL_STATUS || {};
    ENS = j.ensemble || {};
    ENS_DEF = Object.assign({ wait: 'all', timeout_s: 120, min_results: 1, prompt: 'ensemble_merge' }, j.ensemble_defaults || {});
    ENS_MAX = j.ensemble_max_members || 3;
    QTO = Number(j.query_timeout_s || 0) || 0;
    STATE.roles = j.roles || STATE.roles;
    $('#ens-diagram').innerHTML = ensDiagram();
    $('#ens-intro-note').innerHTML =
      '역할마다 따로 켭니다. 켜면 <b>그 역할의 모델 대신</b> 멤버들이 호출되고(멤버가 비운 칸만 역할 값을 상속), ' +
      '멤버는 모두 <b>그 단계의 같은 프롬프트</b>를 받습니다. 자기 프롬프트를 쓰는 것은 <b>취합 LLM 하나</b>뿐입니다(<code>prompts/ensemble_merge.md</code>). ' +
      '저장 위치는 <code>config.json → llm_roles.&lt;역할&gt;.ensemble</code> — CLI <code>models ensemble show|set &lt;역할&gt;</code>, ' +
      '자세한 설명은 <code>docs/ENSEMBLE.md</code>. 값을 바꾼 뒤 <b>저장 &amp; 프로바이더 재로드</b> 를 누르세요.';
    // 공통 기본값 (config.json llm_ensemble_defaults) — 역할에서 비워 둔 칸이 이 값을 따른다
    if ($('#ens-def-wait')) {
      $('#ens-def-wait').value = ENS_DEF.wait || 'all';
      $('#ens-def-timeout').value = ENS_DEF.timeout_s == null ? '' : ENS_DEF.timeout_s;
      $('#ens-def-min').value = ENS_DEF.min_results == null ? '' : ENS_DEF.min_results;
      $('#ens-def-prompt').value = ENS_DEF.prompt || 'ensemble_merge';
    }
    const onlyOn = $('#ens-only-on') && $('#ens-only-on').checked;
    const roles = (STATE.roles || []).filter((r) => !onlyOn || ((ENS[r] || {}).effective || {}).enabled);
    $('#ens-roles').innerHTML = roles.length
      ? '<table class="ens-tbl">' + roles.map((r) => `<tbody data-ens-card="${esc(r)}">` + ensembleRow(r) + '</tbody>').join('') + '</table>'
      : '<div class="muted">켜진 앙상블이 없습니다 — 체크를 풀면 모든 역할이 나옵니다.</div>';
    wireModelSelects($('#ens-roles'));
    wireEnsMembers($('#ens-roles'));
    $$('#ens-roles [data-ens-prompt]').forEach((b) => b.onclick = (e) => {
      e.preventDefault();
      if (LW.openPromptPage) LW.openPromptPage(b.dataset.ensPrompt);
    });
    $$('#ens-roles .ens-sw input[type="checkbox"], #ens-roles .ens-body input[data-ens-f="enabled"]:not([data-ens-m])').forEach((cb) => {
      cb.onclick = (e) => e.stopPropagation();
      cb.onchange = () => {
        const d = cb.closest('details'); if (d) d.open = cb.checked;
        // 같은 역할의 두 스위치(요약 줄 · 본문)를 함께 맞춘다
        const role = cb.dataset.ens;
        $$(`#ens-roles [data-ens="${role}"][data-ens-f="enabled"]:not([data-ens-m])`).forEach((x) => { x.checked = cb.checked; });
        // 멤버가 0개면 켜도 안 도는 것까지 한 번에 알려 준다 (스위치만 켜고 끝내던 자리)
        refreshEnsRole(role);
      };
    });
    $('#ens-msg').textContent = '';
  }
  async function saveEnsemble() {
    const st = { llm_roles: {} };
    (STATE.roles || []).forEach((role) => {
      const en = ensembleSettings(role);
      if (en === undefined) return;                 // 화면에 없는 역할은 건드리지 않는다
      st.llm_roles[role] = { ensemble: en };
    });
    if ($('#ens-def-wait')) {
      const d = { wait: $('#ens-def-wait').value };
      const t = parseInt($('#ens-def-timeout').value, 10); if (!isNaN(t)) d.timeout_s = t;
      const mr = parseInt($('#ens-def-min').value, 10); if (!isNaN(mr)) d.min_results = mr;
      const pr = $('#ens-def-prompt').value.trim(); if (pr) d.prompt = pr;
      st.llm_ensemble_defaults = d;
    }
    if (!Object.keys(st.llm_roles).length && !st.llm_ensemble_defaults) { toast('바꿀 값이 없습니다'); return; }
    $('#ens-msg').textContent = '저장 중…';
    const j = await api('/api/models/set', { settings: st, _confirm: true });
    if (!j || j.error) { $('#ens-msg').innerHTML = `<span class="bad">${esc((j && j.error) || '저장 실패')}</span>`; return; }
    $('#ens-msg').textContent = '저장됨 · 프로바이더를 다시 만들었습니다.';
    toast('앙상블 저장됨');
    await loadEnsemble();
    LW.settingsChanged('models');
  }
  if ($('#btn-ens-refresh')) $('#btn-ens-refresh').onclick = loadEnsemble;
  if ($('#btn-ens-save')) $('#btn-ens-save').onclick = saveEnsemble;
  if ($('#ens-only-on')) $('#ens-only-on').onchange = loadEnsemble;
  loaders.ensemble = loadEnsemble;

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
    // admin 이 아니면 추가 폼도 **감춘다**. 예전에는 폼이 그대로 보여서, 눌러 봐야 401/403 이 나고
    // "버튼이 죽었다" 처럼 보였다 (id 를 채워 넣어도 서버가 거절한다).
    if ($('#sec-add-form')) $('#sec-add-form').classList.toggle('hidden', !admin);
    if (!admin) { $('#sec-users').innerHTML = '<div class="muted small">사용자 목록·권한 표·API 키는 admin 만 볼 수 있습니다. 현재 <b>' + esc(u.role || 'viewer') + '</b> (' + esc(u.via || '') + ') 로 접속 중입니다 — 사용자 추가·API 키 발급은 admin 으로 로그인해야 합니다.</div>'; $('#sec-perms').innerHTML = ''; $('#sec-keys').innerHTML = ''; $('#sec-snapshots').innerHTML = ''; $('#sec-audit').innerHTML = ''; return; }
    const [us, sec, sn, au, ak] = await Promise.all([api('/api/auth/users'), api('/api/security'), api('/api/snapshot'), api('/api/audit?n=60'), api('/api/apikeys')]);
    $('#sec-path').textContent = sec.path || '';
    $('#sec-users').innerHTML = '<div class="tbl-wrap"><table><tr><th>id</th><th>역할</th><th>표시</th><th>비밀번호</th><th></th></tr>' + (us.users || []).map((x) => `<tr><td><b>${esc(x.name)}</b></td><td>${roleSel(x.role, `data-role-of="${esc(x.name)}"`)}</td><td>${esc(x.display || '')}</td><td>${x.has_password ? '있음' : '<span class="muted">없음 (SSO)</span>'}</td><td><button class="mini secondary" data-pw-of="${esc(x.name)}">비밀번호</button> <button class="mini danger" data-del-of="${esc(x.name)}">삭제</button></td></tr>`).join('') + '</table></div>' + (!(us.users || []).length ? '<div class="muted small">사용자 없음 — 아래에서 admin 을 먼저 추가하세요 (또는 CLI: users add &lt;id&gt; --role admin)</div>' : '');
    $$('#sec-users [data-role-of]').forEach((s) => s.onchange = async () => { const r = await api('/api/auth/users', { action: 'set_role', name: s.dataset.roleOf, role: s.value }); if (r.ok) toast('역할 변경: ' + s.dataset.roleOf + ' → ' + s.value); loadSecurity(); });
    // ---- 권한 표 (등급별 최소 역할 + 개별 작업 오버라이드) ----
    SEC.perms = sec.permissions || { levels: {}, ops: {} };
    SEC.minpw = Number((((sec.security || {}).local || {}).min_password_len) || 8);   // 짧은 비밀번호는 보내기 전에 걸러 준다
    if ($('#su-pass')) $('#su-pass').placeholder = '(비우면 SSO 전용 · ' + SEC.minpw + '자 이상)';
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
    // ---- 문서 접근 제어 (docacl.json) ----
    // 설계: 권한 표(무엇을 할 수 있나) 바로 아래에 둔다. 역할은 같은 축이고, 여기서 정하는 것은
    // "무엇을 **읽을** 수 있나" 이기 때문이다. 규칙을 잘못 넣으면 답변 품질이 조용히 떨어지므로
    // 저장 버튼 옆에 항상 '영향 확인'(가려질 문서 수)을 같이 둔다.
    await loadDocAcl();
    // ---- API 키 ----
    $('#sec-keys').innerHTML = '<div class="tbl-wrap"><table><tr><th>id</th><th>이름</th><th>역할</th><th>생성</th><th>마지막 사용</th><th></th></tr>' + (ak.keys || []).map((k) => `<tr><td class="mono small">${esc(k.id)}</td><td>${esc(k.name)}</td><td>${esc(k.role)}</td><td class="small muted">${k.created ? LW.dt(k.created) : ''}</td><td class="small muted">${k.last_used ? LW.dt(k.last_used) : '-'}</td><td><button class="mini danger" data-key-del="${esc(k.id)}">삭제</button></td></tr>`).join('') + '</table></div>' +
      `<div class="row"><input id="ak-name" type="text" placeholder="키 이름 (예 claude-desktop-kim)" style="max-width:220px"> ${roleSel('viewer', 'id="ak-role"')} <button id="btn-ak-add" class="mini">발급</button><span class="muted small">MCP HTTP / curl 에서 <code>Authorization: Bearer &lt;token&gt;</code>. 토큰은 발급 직후 한 번만 표시됩니다.</span></div><pre id="ak-out" class="pre small hidden"></pre>`;
    $$('#sec-keys [data-key-del]').forEach((b) => b.onclick = async () => { if (!confirm('API 키 ' + b.dataset.keyDel + ' 를 삭제할까요? 이 키를 쓰는 클라이언트는 즉시 거부됩니다.')) return; await api('/api/apikeys', { action: 'remove', id: b.dataset.keyDel }); loadSecurity(); });
    $('#btn-ak-add').onclick = async () => {
      const nm = $('#ak-name').value.trim();
      if (!nm) { toast('키 이름을 입력하세요 (예: claude-desktop-kim)'); $('#ak-name').focus(); return; }
      const r = await api('/api/apikeys', { action: 'add', name: nm, role: $('#ak-role').value });
      // 발급이 거절되면(권한·검증 실패) 예전에는 **아무 일도 일어나지 않았다** — 조용히 끝나지 않게 한다.
      if (!r.token) { if (!r.error && !r.cancelled) toast('API 키를 발급하지 못했습니다' + (r.forbidden ? ' (admin 권한 필요)' : '')); return; }
      const txt = 'token (지금만 표시): ' + r.token + '\nMCP 원격 설정 예: {"type":"http","url":"' + location.origin + '/mcp","headers":{"Authorization":"Bearer ' + r.token + '"}}';
      toast('API 키 발급: ' + r.name);
      // 표를 바로 다시 그린다. 그러지 않으면 새로고침 전까지 새 키가 목록에 없어 "발급이 안 됐다" 로 보인다.
      // (loadSecurity 가 #ak-out 을 새로 만들므로 토큰은 다시 그린 **뒤에** 넣는다.)
      await loadSecurity();
      const o = $('#ak-out'); if (o) { o.classList.remove('hidden'); o.textContent = txt; }
    };
    $$('#sec-users [data-pw-of]').forEach((b) => b.onclick = async () => { const pw = prompt(b.dataset.pwOf + ' 의 새 비밀번호'); if (!pw) return; const r = await api('/api/auth/users', { action: 'set_password', name: b.dataset.pwOf, password: pw }); if (r.ok) toast('비밀번호 변경됨'); });
    $$('#sec-users [data-del-of]').forEach((b) => b.onclick = async () => { if (!confirm(b.dataset.delOf + ' 사용자를 삭제할까요?')) return; const r = await api('/api/auth/users', { action: 'remove', name: b.dataset.delOf }); if (r.ok) toast('삭제됨'); loadSecurity(); });
    $('#sec-snapshots').innerHTML = '<div class="tbl-wrap"><table><tr><th>이름</th><th>tag</th><th>크기</th><th>내용</th><th></th></tr>' + (sn.snapshots || []).map((x) => `<tr><td class="mono small">${esc(x.name)}</td><td>${esc(x.tag || '')}</td><td class="num">${fmt((x.bytes || 0) / 1e6, 1)}MB</td><td class="small muted">${esc(JSON.stringify(x.counts || {}))} ${esc(x.reason || '')}</td><td>${x.has_db ? `<button class="mini danger" data-restore="${esc(x.name)}">복원</button>` : ''}</td></tr>`).join('') + '</table></div>' + (!(sn.snapshots || []).length ? '<div class="muted small">스냅샷 없음. 전체 초기화 시 자동 생성됩니다.</div>' : '');
    $$('#sec-snapshots [data-restore]').forEach((b) => b.onclick = async () => { const r = await api('/api/snapshot', { action: 'restore', name: b.dataset.restore }); if (r.restored) { toast('복원됨: ' + r.restored); loadStatus(); loadSecurity(); } });
    $('#sec-audit').innerHTML = '<div class="tbl-wrap"><table><tr><th>시각</th><th>사용자</th><th>역할</th><th>결과</th><th>등급</th><th>작업</th></tr>' + (au.rows || []).slice().reverse().map((r) => `<tr class="${r.ok ? '' : 'has-err'}"><td class="small">${esc(r.time)}</td><td>${esc(r.user || '')}</td><td class="small">${esc(r.role || '')}</td><td>${r.ok ? '<span class="ok">ok</span>' : '<span class="bad">DENY</span>'}</td><td class="small">${esc(r.level || '')}</td><td class="small">${esc(r.op || '')}${r.error ? ' <span class="errtxt">' + esc(r.error) + '</span>' : ''}</td></tr>`).join('') + '</table></div>';
  }
  // 문서 접근 제어: 서버의 docacl.json 이 유일한 진실이다. 화면은 읽어서 그리고, 저장하면 바로 다시 읽는다
  // (파일을 직접 고친 경우에도 '새로고침' 한 번으로 화면이 맞춰진다).
  async function loadDocAcl() {
    const box = $('#sec-docacl'); if (!box) return;
    const d = await api('/api/docacl');
    if (d.error) { box.innerHTML = '<div class="muted small">' + esc(d.error) + '</div>'; return; }
    const roles = d.roles || SEC.roles;
    const rsel = (cur, attr) => `<select ${attr || ''}>${roles.map((r) => `<option ${r === cur ? 'selected' : ''}>${r}</option>`).join('')}</select>`;
    const rules = d.rules || [];
    box.innerHTML =
      `<div class="muted small">문서의 front matter <code>acl: class1</code> 과 아래 경로 규칙 중 <b>더 높은 등급</b>이 적용됩니다. admin 은 항상 전부 봅니다. 규칙이 없고 기본 등급이 <code>viewer</code> 면 아무도 막지 않습니다(설치 직후 동작).<br>토글 <code>doc_acl</code> 이 <b>${d.toggle ? 'on' : 'off'}</b> — off 면 이 규칙 전체가 무시됩니다 (Settings › 토글 › ⑧ 보안). 파일: <code>${esc(d.path || '')}</code>${d.exists ? '' : ' <span class="muted">(아직 없음 — 저장하면 만들어집니다)</span>'}</div>` +
      `<div class="row" style="margin-top:6px"><label class="inline"><input type="checkbox" id="acl-enabled" ${d.enabled ? 'checked' : ''}> 사용</label>` +
      `<label>규칙에 없는 문서의 최소 역할 ${rsel(d.default_min_role || 'viewer', 'id="acl-default"')}</label>` +
      `<span class="muted small">viewer = 모두 공개(기본). 올리면 "규칙에 적힌 것만 공개" 가 됩니다.</span></div>` +
      '<div class="tbl-wrap"><table id="acl-rules"><tr><th>경로 접두사 (doc_id 앞부분)</th><th>최소 역할</th><th>메모</th><th></th></tr>' +
      rules.map((r, i) => `<tr data-i="${i}"><td><input type="text" class="mono" data-acl-prefix value="${esc(r.prefix || '')}" style="min-width:200px"></td><td>${rsel(r.min_role || 'viewer', 'data-acl-role')}</td><td><input type="text" data-acl-note value="${esc(r.note || '')}" style="min-width:140px"></td><td><button class="mini danger" data-acl-del="${i}">삭제</button></td></tr>`).join('') +
      '</table></div>' +
      `<div class="row" style="margin-top:6px"><button id="btn-acl-add" class="mini secondary">규칙 추가</button><button id="btn-acl-save" class="mini">저장</button>` +
      `<span class="muted small">CLI: <code>security docacl show</code> · <code>security docacl check --role viewer</code> · 예시 <code>setup/docacl.example.json</code></span></div>` +
      '<div id="acl-check" class="small"></div>';
    $('#btn-acl-add').onclick = () => {
      const t = $('#acl-rules'), i = t.rows.length - 1;
      const tr = t.insertRow(-1); tr.dataset.i = i;
      tr.innerHTML = `<td><input type="text" class="mono" data-acl-prefix value="corpus/" style="min-width:200px"></td><td>${rsel('class1', 'data-acl-role')}</td><td><input type="text" data-acl-note value="" style="min-width:140px"></td><td><button class="mini danger" data-acl-del="${i}">삭제</button></td>`;
      tr.querySelector('[data-acl-del]').onclick = () => tr.remove();
    };
    $$('#sec-docacl [data-acl-del]').forEach((b) => b.onclick = () => b.closest('tr').remove());
    $('#btn-acl-save').onclick = async () => {
      const out = [];
      $$('#acl-rules tr[data-i]').forEach((tr) => {
        const pfx = (tr.querySelector('[data-acl-prefix]').value || '').trim();
        if (!pfx) return;                      // 빈 줄은 '삭제' 로 본다
        out.push({ prefix: pfx, min_role: tr.querySelector('[data-acl-role]').value, note: (tr.querySelector('[data-acl-note]').value || '').trim() });
      });
      const r = await api('/api/docacl', { action: 'save', enabled: $('#acl-enabled').checked, default_min_role: $('#acl-default').value, rules: out });
      if (r.ok) { toast('문서 접근 제어 저장됨 (규칙 ' + out.length + '개)'); await loadDocAcl(); await aclCheck(); }
    };
  }
  async function aclCheck() {
    const box = $('#acl-check'); if (!box) return;
    box.innerHTML = '<span class="muted">확인 중…</span>';
    const rows = [];
    for (const role of (SEC.roles || [])) {
      const r = await api('/api/docacl', { action: 'check', role });
      if (r.error) { box.innerHTML = '<div class="muted small">' + esc(r.error) + '</div>'; return; }
      rows.push(r);
    }
    const ex = (rows.find((r) => (r.examples || []).length) || {}).examples || [];
    box.innerHTML = '<table style="margin-top:6px"><tr><th>역할</th><th class="num">보임</th><th class="num">가려짐</th></tr>' +
      rows.map((r) => `<tr><td>${esc(r.role)}</td><td class="num">${r.visible}</td><td class="num ${r.blocked ? 'bad' : ''}">${r.blocked}</td></tr>`).join('') + '</table>' +
      (ex.length ? '<details><summary class="muted small">가려지는 문서 예 (가장 낮은 역할 기준)</summary><div class="small mono">' +
        ex.slice(0, 30).map((x) => esc(x.doc_id) + ' <span class="muted">→ ' + esc(x.min_role) + ' (' + esc(x.why) + ')</span>').join('<br>') + '</div></details>' : '');
  }
  if ($('#btn-acl-check')) $('#btn-acl-check').onclick = aclCheck;
  $('#btn-sec-refresh').onclick = loadSecurity;
  $('#btn-sec-reload').onclick = async () => { const r = await api('/api/security', { action: 'reload' }); if (r.ok) toast('security.json 다시 읽음 (mode ' + r.mode + ')'); loadSecurity(); };
  $('#btn-snap-create').onclick = async () => { const tag = prompt('스냅샷 tag', 'manual'); if (tag === null) return; const r = await api('/api/snapshot', { action: 'create', tag }); if (r.name) toast('스냅샷 ' + r.name); loadSecurity(); };
  $('#btn-user-add').onclick = async () => {
    const name = $('#su-name').value.trim();
    // 예전에는 여기서 **말 없이** return 했다 — 눌러도 아무 반응이 없어 버튼이 죽은 것처럼 보였다.
    if (!name) { toast('사용자 id 를 입력하세요'); $('#su-name').focus(); return; }
    const pw = $('#su-pass').value;
    const min = Number(SEC.minpw || 8);
    if (pw && pw.length < min) { toast('비밀번호는 ' + min + '자 이상이어야 합니다 (비우면 SSO 전용 계정)'); $('#su-pass').focus(); return; }
    const r = await api('/api/auth/users', { action: 'add', name, role: $('#su-role').value, password: pw || null });
    if (r.ok) { toast('추가됨: ' + name); $('#su-name').value = ''; $('#su-pass').value = ''; }
    loadSecurity();
  };
  $('#btn-pw-change').onclick = async () => {
    const o = $('#pw-old').value, n = $('#pw-new').value;
    if (!o || !n) { toast('현재 비밀번호와 새 비밀번호를 모두 입력하세요'); (!o ? $('#pw-old') : $('#pw-new')).focus(); return; }
    const r = await api('/api/auth/password', { old: o, new: n });
    if (r.ok) { toast('비밀번호 변경됨'); $('#pw-old').value = ''; $('#pw-new').value = ''; }
  };
  loaders.security = loadSecurity;

  // ---------------- PRESETS ----------------
  async function loadPresets() {
    const j = await api('/api/presets'); const pr = j.presets;
    $('#presets-json').value = JSON.stringify(pr, null, 2);
    $('#preset-cards').innerHTML = '<div class="cards">' + Object.keys(pr).map((k) => `<div class="card"><h4>${esc(k)}</h4><div class="cdesc">${esc(pr[k].desc || '')}</div><div class="muted small">toggles ${Object.keys(pr[k].toggles || {}).length} · tuning ${Object.keys(pr[k].tuning || {}).length} · settings ${Object.keys(pr[k].settings || {}).length}</div><div class="row" style="margin:6px 0 0"><button class="mini secondary" data-diff="${esc(k)}">diff</button><button class="mini" data-apply="${esc(k)}">적용(저장)</button></div></div>`).join('') + '</div>';
    $$('#preset-cards [data-diff]').forEach((b) => b.onclick = async () => { const d = await api('/api/presets/diff?name=' + encodeURIComponent(b.dataset.diff)); $('#preset-diff').innerHTML = `<h3>${esc(b.dataset.diff)} 적용 시 변경</h3><table><tr><th>키</th><th>현재</th><th>프리셋</th></tr>` + d.map((r) => `<tr class="${r.changes ? 'changed' : ''}"><td class="mono small">${esc(r.key)}</td><td>${esc(JSON.stringify(r.current))}</td><td class="${r.changes ? 'ok' : 'muted'}">${esc(JSON.stringify(r.preset))}</td></tr>`).join('') + '</table>'; });
    $$('#preset-cards [data-apply]').forEach((b) => b.onclick = async () => { if (!confirm(`프리셋 ${b.dataset.apply} 를 config.json/tuning.json 에 저장 적용합니다.`)) return; const r = await api('/api/presets', { action: 'apply', names: b.dataset.apply, save: true }); toast(`적용: toggles ${Object.keys(r.toggles).length} tuning ${Object.keys(r.tuning).length} settings ${Object.keys(r.settings).length}`); STATE.settings = r.settings; setTogglesFrom(r.settings.toggles); LW.settingsChanged('preset'); });
  }
  $('#btn-preset-refresh').onclick = loadPresets;
  $('#btn-preset-save').onclick = async () => { let pr; try { pr = JSON.parse($('#presets-json').value); } catch (e) { toast('JSON 오류'); return; } await api('/api/presets', { action: 'save', presets: pr }); toast('presets.json 저장됨'); LW.settingsChanged('preset'); };
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
    if (!errs.length) toast('튜닝 저장됨'); loadTuning(); LW.settingsChanged('tuning');
  };
  $('#btn-tuning-reset').onclick = async () => { if (!confirm('tuning.json 의 모든 오버라이드를 지웁니다.')) return; await api('/api/tuning', { action: 'reset' }); loadTuning(); LW.settingsChanged('tuning'); };
  loaders.tuning = loadTuning;
  LW.renderTuning = renderTuning; LW.loadTuning = loadTuning;

  // ---------------- QUERY RULES ----------------
  // 유형 목록·설명은 **서버의 레지스트리**(llmwiki/query_rules.py RULE_TYPES)에서 온다.
  // 예전에는 화면이 6개를 하드코딩해서, 유형을 늘려도 드롭다운에 나타나지 않았다 (= 그 유형은 화면에서 없는 기능).
  async function loadQRules() {
    const j = await api('/api/query_rules');
    $('#qrules-json').value = JSON.stringify(j.rules, null, 2);
    $('#qr-msg').textContent = `${j.path} · ${JSON.stringify(j.stats)}`;
    const types = j.types || [];
    const sel = $('#qr-type');
    if (sel && types.length) {
      const cur = sel.value;
      sel.innerHTML = types.map((t) => `<option value="${esc(t.name)}" title="${esc(t.how)}">${esc(t.name)} — ${esc(t.label)}</option>`).join('');
      if (cur && types.some((t) => t.name === cur)) sel.value = cur;
      sel.onchange = () => {
        // 값 모양이 유형마다 다르다 — 입력칸 안내를 바꿔 준다 (cond/map 은 JSON 편집기로 보낸다)
        const t = types.find((x) => x.name === sel.value) || {};
        const vi = $('#qr-values');
        if (!vi) return;
        const hint = { list: '값 (쉼표 구분)', str: '정규 표기 하나 (예: MDM9x-B1)',
                       cond: '이 유형은 아래 JSON 편집기에서 [{"when":[…],"then":[…]}] 로 적습니다',
                       map: '이 유형은 아래 JSON 편집기에서 {"factor":1024,"base":"byte"} 로 적습니다' };
        vi.placeholder = hint[t.value] || '값 (쉼표 구분)';
        vi.disabled = (t.value === 'cond' || t.value === 'map');
        $('#btn-qr-add').disabled = vi.disabled;
      };
      sel.onchange();
    }
    const box = $('#qr-types');
    if (box) {
      box.innerHTML = '<div class="tbl-wrap"><table class="tbl"><thead><tr><th>유형</th><th>뜻</th><th>방향</th><th>값 모양</th><th>항목</th><th>어떻게 넓히나</th></tr></thead><tbody>' +
        types.map((t) => `<tr><td><span class="pill">${esc(t.name)}</span>${t.since ? ' <small class="muted">신규</small>' : ''}</td><td>${esc(t.label)}</td><td>${esc(t.direction)}</td><td class="mono small">${esc(t.value)}</td><td class="num">${t.count}</td><td class="muted small">${esc(t.how)}</td></tr>`).join('') +
        '</tbody></table></div>';
    }
  }
  $('#btn-qr-add').onclick = async () => { const vals = $('#qr-values').value.split(',').map((x) => x.trim()).filter(Boolean); const r = await api('/api/query_rules', { action: 'add', type: $('#qr-type').value, term: $('#qr-term').value.trim(), values: vals }); toast('추가: ' + JSON.stringify(r.values || r)); loadQRules(); };
  $('#btn-qr-test').onclick = async () => { const r = await api('/api/query_rules/test?q=' + encodeURIComponent($('#qr-test').value)); const el = $('#qr-test-out'); el.classList.remove('hidden'); el.textContent = JSON.stringify(r, null, 1); };
  $('#btn-qr-save').onclick = async () => { let r; try { r = JSON.parse($('#qrules-json').value); } catch (e) { toast('JSON 오류'); return; } const j = await api('/api/query_rules', { action: 'save', rules: r }); toast('저장됨 ' + JSON.stringify(j.stats)); };
  // "이 말은 어떻게 퍼지나" — 유형 · 방향 · 대표어 · 값 · 적용 방식 (요청 4, /api/query_rules/explain)
  async function explainTerm() {
    const term = $('#qr-explain').value.trim(); if (!term) { toast('용어를 입력하세요'); return; }
    const r = await api('/api/query_rules/explain?term=' + encodeURIComponent(term)); const el = $('#qr-explain-out'); el.classList.remove('hidden');
    if (r.error) { el.innerHTML = `<div class="bad">${esc(r.error)}</div>`; return; }
    const sym = r.related_symmetric ? 'true' : 'false';
    let h = `<div class="small"><b>${esc(r.term)}</b> 이(가) 질의에 있으면 → <span class="muted">related_symmetric=${sym}</span></div>`;
    if (!r.entries.length) h += '<div class="muted small">이 말로 발화하는 규칙 없음 (사전에 키 또는 양방향 값으로 없다)</div>';
    else h += '<table><tr><th>유형</th><th>방향</th><th>대표어</th><th>값</th><th>적용 방식</th></tr>' + r.entries.map((e) => `<tr><td><span class="pill">${esc(e.type)}</span></td><td>${esc(e.direction)}${e.reverse ? ' <small class="muted">(값 쪽에서 거꾸로)</small>' : ''}</td><td>${esc(e.canonical)}</td><td>${esc(e.values.join(', '))}</td><td class="muted small">${esc(e.how)}</td></tr>`).join('') + '</table>';
    if (r.expanded_from.length) h += '<div class="small" style="margin-top:6px"><b>이 말을 끌어오는 규칙</b> (값으로 적힌 곳)</div><table><tr><th>유형</th><th>방향</th><th>키</th><th>설명</th></tr>' + r.expanded_from.map((x) => `<tr><td><span class="pill">${esc(x.type)}</span></td><td>${esc(x.direction)}</td><td>${esc(x.key)}</td><td class="muted small">${esc(x.note)}</td></tr>`).join('') + '</table>';
    h += `<div class="muted small" style="margin-top:6px">${esc(r.note)}</div>`;
    el.innerHTML = h;
  }
  $('#btn-qr-explain').onclick = explainTerm;
  $('#qr-explain').addEventListener('keydown', (e) => { if (e.key === 'Enter') explainTerm(); });
  // 규칙 효과: 이 규칙이 실제로 답변 근거에 기여했나 (llmwiki/ruleeffect.py · CLI `rules effect`)
  async function loadRuleEffect() {
    const el = $('#qr-effect-out'); if (!el) return;
    const order = ($('#qr-effect-order') || {}).value || 'fired';
    el.classList.remove('hidden');
    el.innerHTML = '<div class="muted small">읽는 중…</div>';
    const r = await api('/api/query_rules/effect?order=' + encodeURIComponent(order) + '&limit=200');
    if (!r || r.error) { el.innerHTML = `<div class="bad">${esc((r && r.error) || '읽지 못했습니다')}</div>`; return; }
    const bar = (x) => `<span class="eff-bar" title="기여율 ${Math.round(100 * x.help_rate)}%"><i style="width:${Math.round(100 * x.help_rate)}%"></i></span>`;
    el.innerHTML =
      `<div class="muted small">규칙 ${r.n}개 기록 · 총 발화 ${r.total_fired}회 · ${esc(r.note)}</div>` +
      (r.never_helped ? `<div class="warntxt small">⚠ 3회 이상 걸렸는데 한 번도 기여하지 못한 규칙 ${r.never_helped}개 — 정렬을 '걸리기만 하고 기여 0' 으로 바꿔 보세요. 지울 후보입니다.</div>` : '') +
      '<div class="tbl-wrap"><table class="tbl"><thead><tr><th>규칙</th><th>유형</th><th title="이 규칙이 걸린 질의 수">발화</th><th title="그 규칙이 만든 검색이 후보를 가져온 질의 수">후보</th><th title="그 후보가 최종 컨텍스트에 들어간 질의 수">기여</th><th title="답변이 [C#] 로 인용한 질의 수">인용</th><th>기여율</th></tr></thead><tbody>' +
      (r.rows || []).map((x) => `<tr class="${x.fired >= 3 && !x.helped ? 'bad-row' : ''}"><td><code>${esc(x.term)}</code></td><td>${esc(x.type || '')}</td>` +
        `<td class="num">${x.fired}</td><td class="num">${x.cand}</td><td class="num">${x.helped}</td><td class="num">${x.cited}</td>` +
        `<td class="num">${Math.round(100 * x.help_rate)}% ${bar(x)}</td></tr>`).join('') +
      '</tbody></table></div>' +
      ((r.rows || []).length ? '' : '<div class="muted">아직 기록이 없습니다 — 질의를 몇 번 돌리면 쌓입니다.</div>');
  }
  if ($('#btn-qr-effect')) $('#btn-qr-effect').onclick = loadRuleEffect;
  if ($('#qr-effect-order')) $('#qr-effect-order').onchange = () => { if (!$('#qr-effect-out').classList.contains('hidden')) loadRuleEffect(); };
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
  // 다른 화면(🧭 Pipeline › 앙상블)에서 "이 취합 프롬프트 편집" 을 누르면 여기로 온다
  LW.openPromptPage = async (name) => {
    switchGroup('settings'); switchTab('prompts');
    await loadPrompts();
    if (name) await openPrompt(name);
    const el = document.querySelector(`#prompt-list div[data-p="${name}"]`);
    if (el) el.scrollIntoView({ behavior: 'smooth', block: 'center' });
  };

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
          `<td class="small">${t.next_run ? dt(t.next_run) : '-'}</td><td class="small">${t.last_run ? dt(t.last_run) + ' <span class="pill ' + (t.last_status === 'done' ? 'ok' : t.last_status === 'cancelled' ? 'warn' : 'bad') + '">' + esc(t.last_status || '') + '</span> ' + fmt(t.last_ms, 0) + 'ms' : '-'}${t.last_error ? '<br><span class="bad small">' + esc(String(t.last_error).slice(0, 70)) + '</span>' : ''}` +
          // '왜 안 돌았지' 의 답: 앞 실행이 아직 끝나지 않아 건너뛴 시각 (overlap=skip). 예전에는 logs/schedule.jsonl 을 열어야만 보였다.
          `${t.last_skipped ? '<br><span class="warntxt small" title="앞 실행이 끝나지 않아 건너뛰었습니다 (overlap=skip) — 주기를 늘리거나 timeout_s 를 줄이세요">건너뜀 ' + dt(t.last_skipped) + '</span>' : ''}</td>` +
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
  // 화면 → 작업 dict. **기존 작업의 모르는 필드는 그대로 남긴다** (2026-09-19).
  // 예전에는 폼 입력만으로 새로 만들어서, 편집 한 번에 `run_on_start` 와 각 샘플 작업의 `_note` 설명이 사라졌다.
  // 화면에 없는 필드(지금 것이든 나중에 생길 것이든)는 건드리지 않는 것이 맞다.
  function taskFromForm() {
    const orig = $('#sch-editor').dataset.orig;
    const base = orig ? ((SCH.tasks || []).find((x) => x.name === orig) || {}) : {};
    const t = Object.assign({}, base);
    delete t.invalid; delete t.running; delete t.weight;           // 서버가 붙인 표시용 필드
    delete t.next_run; delete t.last_run; delete t.last_status; delete t.last_ms; delete t.last_error;
    delete t.last_result; delete t.last_skipped;
    t.name = $('#sch-name').value.trim();
    t.enabled = $('#sch-enabled').checked;
    const k = $('#sch-when-kind').value;
    delete t.every; delete t.at; delete t.days; delete t.cron;      // 시점은 셋 중 정확히 하나만
    if (k === 'every') t.every = $('#sch-every').value.trim();
    else if (k === 'at') { t.at = $('#sch-at').value.trim(); const d = $('#sch-days').value.split(',').map((x) => x.trim()).filter(Boolean); if (d.length) t.days = d; }
    else t.cron = $('#sch-cron').value.trim();
    const to = parseInt($('#sch-timeout').value, 10);
    if (to > 0) t.timeout_s = to; else delete t.timeout_s;
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
      // 이름을 바꾸는 경우: **새 이름으로 먼저 넣고** 성공했을 때만 옛 이름을 지운다.
      // 예전에는 remove → add 순서라, add 가 검증에서 막히면 작업이 사라진 채로 남았다 (2026-09-19).
      const j = await api('/api/schedule', { action: 'add', task: t });
      if (!j || !j.ok) { toast('저장 실패: ' + ((j && j.error) || '')); return; }
      if (orig && orig !== t.name) await api('/api/schedule', { action: 'remove', name: orig });
      toast('저장됨'); $('#sch-editor').classList.add('hidden'); loadSchedule();
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
  async function loadConfig() { const j = await api('/api/status'); $('#config-json').value = JSON.stringify(j.settings, null, 2); $('#config-paths').textContent = '파일 위치: ' + Object.keys(j.paths || {}).map((k) => k + '=' + j.paths[k]).join(' · '); loadEnvPanel(); }
  // .env 가시성 (계획 0918 §2.10 · CLI `config env` · GET /api/env, admin). 값은 서버가 마스킹해서 준다 — 화면은 이름·설정 여부·출처만 보인다.
  // admin 이 아니면 서버가 401/403 을 내므로 안내 한 줄만 남긴다 (config 탭은 admin 화면이지만 👁 권한 보기로 낮춰 볼 때가 있다).
  async function loadEnvPanel() {
    const el = $('#env-panel'); if (!el) return;
    const j = await api('/api/env');
    if (!j || j.error || j.unauthorized) { el.innerHTML = `<span class="muted">${esc((j && j.error) || '.env 는 admin 만 볼 수 있습니다')}</span>`; return; }
    const keys = j.keys || [], ov = j.overrides || [];
    const krow = (k) => `<tr><td><code>${esc(k.name)}</code></td><td>${k.set ? '<span class="ok">설정됨</span>' : '<span class="muted">없음</span>'}</td><td><code>${esc(k.masked || '')}</code></td><td>${esc(k.source || '')}${k.file_empty ? ' <span class="muted">(파일에 빈 값)</span>' : ''}</td></tr>`;
    const orow = (o) => `<tr><td><code>${esc(o.env)}</code></td><td>${esc(o.key)}</td><td><code>${esc(o.masked || '')}</code></td></tr>`;
    el.innerHTML = `<div class="muted">${esc(j.path || '')} · ${j.exists ? '파일 있음' : '파일 없음 (setup/.env.example 을 복사)'}${j.mtime ? ' · 수정 ' + esc(String(j.mtime)) : ''} · 편집은 파일에서 하고 [.env 다시 읽기]</div>` +
      `<div class="tbl-wrap"><table class="tbl"><thead><tr><th>키</th><th>상태</th><th>값(마스킹)</th><th>출처</th></tr></thead><tbody>${keys.map(krow).join('')}</tbody></table></div>` +
      `<div style="margin-top:6px"><b>활성 LLMWIKI_* 오버라이드</b> <span class="muted">(${ov.length}개 — 환경변수가 config.json 값을 덮어쓰고 있는 키)</span></div>` +
      (ov.length ? `<div class="tbl-wrap"><table class="tbl"><thead><tr><th>환경변수</th><th>설정 키</th><th>값</th></tr></thead><tbody>${ov.map(orow).join('')}</tbody></table></div>` : '');
  }
  if ($('#btn-env-refresh')) $('#btn-env-refresh').onclick = loadEnvPanel;
  if ($('#btn-env-reload')) $('#btn-env-reload').onclick = async () => {
    const el = $('#env-panel'); if (el) el.textContent = '.env 를 다시 읽는 중…';
    const r = await api('/api/env', { action: 'reload' });
    if (r && r.ok) toast('.env 다시 읽음 · 파일에서 읽은 키 ' + ((r.reloaded || []).length) + '개');
    await loadEnvPanel(); loadConfig(); loadStatus();
  };
  $('#btn-config-reload').onclick = loadConfig;
  $('#btn-config-save').onclick = async () => {
    let s; try { s = JSON.parse($('#config-json').value); } catch (e) { toast('JSON 오류'); return; }
    const flat = Object.assign({}, s, s.toggles || {}); delete flat.toggles; delete flat.LLM_ROLES;
    const j = await api('/api/config', { settings: flat }); STATE.settings = j.settings; setTogglesFrom(j.settings.toggles);
    $('#config-msg').textContent = '저장됨 · answer=' + j.providers.roles.answer.name + '/' + j.providers.roles.answer.model;
    LW.settingsChanged('config');
  };
  // 다른 화면(🧭 Pipeline 의 단계 상세)에서 config·튜닝을 저장하면 여기 캐시도 버린다.
  LW.onSettingsChanged((what) => {
    if (what === 'tuning' || what === 'config' || what === 'preset') { TUN.edits = {}; }
    if (LW.tabVisible && LW.tabVisible('tuning')) loadTuning();
    if (LW.tabVisible && LW.tabVisible('config')) loadConfig();
    if (LW.tabVisible && LW.tabVisible('models')) loadModels();
    if (LW.tabVisible && LW.tabVisible('presets')) loadPresets();
  });
  $('#btn-config-effective').onclick = async () => { const rows = await api('/api/config/effective'); const el = $('#config-effective'); el.classList.toggle('hidden'); el.innerHTML = '<table><tr><th>키</th><th>값</th><th>기본</th><th>출처</th><th>env</th><th>설명</th></tr>' + rows.map((r) => `<tr class="${r.source === 'env' ? 'changed' : ''}"><td class="mono small">${esc(r.key)}</td><td class="small">${esc(JSON.stringify(r.value)).slice(0, 60)}</td><td class="small muted">${esc(JSON.stringify(r.default)).slice(0, 40)}</td><td><span class="pill">${esc(r.source)}</span></td><td class="mono small muted">${esc(r.env)}</td><td class="small muted">${esc((r.help || '').slice(0, 80))}</td></tr>`).join('') + '</table>'; };
  loaders.config = loadConfig;
})(window.LW);
