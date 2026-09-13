/* Corpus — 빌드/상태/health/verify, 임베딩 coverage/precompute, 문서 계약 lint, MCP 소스. */
(function (LW) {
  'use strict';
  const { $, $$, esc, fmt, fmtK, dt, api, toast, STATE, overrides, renderTrace, pollJob, switchTab, switchGroup, loadStatus, loaders } = LW;

  // ---------------- BUILD ----------------
  function alertsHtml(al) { return (al || []).length ? `<div class="banner warn"><b>알림 ${al.length}</b><ul style="margin:4px 0 0 16px">${al.map((a) => `<li>[${esc(a.level)}] <b>${esc(a.check)}</b>: ${esc(a.detail)}${a.fix ? ' → <code>' + esc(a.fix) + '</code>' : ''}</li>`).join('')}</ul></div>` : ''; }
  async function loadBuildStatus() {
    const s = await api('/api/build/status');
    const lb = s.last_build || {}; const ep = s.embed_progress || {};
    $('#build-status').innerHTML = `<div class="stat"><b>${s.running ? '실행 중' : '대기'}</b>빌드${s.lock ? ' (pid ' + s.lock.pid + ')' : ''}</div><div class="stat"><b>${lb.ts ? dt(lb.ts) : '-'}</b>마지막 빌드 ${lb.mode || ''}</div><div class="stat"><b>${s.build_version}</b>build_version</div><div class="stat"><b>${lb.changed == null ? '-' : lb.changed}</b>changed</div>` +
      (ep.total ? `<div class="stat"><b>${ep.done}/${ep.total}</b>임베딩 ${esc(ep.status)}<div class="progress"><i style="width:${100 * (ep.done || 0) / Math.max(1, ep.total)}%"></i><span>${fmt(100 * (ep.done || 0) / Math.max(1, ep.total), 0)}% · batch ${ep.batch} · ${ep.rate_per_s || 0}/s${ep.eta_s ? ' · ETA ' + ep.eta_s + 's' : ''}${ep.failed ? ' · 실패 ' + ep.failed : ''}</span></div></div>` : '') +
      (s.lint_summary ? `<div class="stat"><b>${s.lint_summary.errors}/${s.lint_summary.warnings}</b>lint err/warn</div>` : '');
    $('#build-alerts').innerHTML = alertsHtml(lb.alerts);
    return s;
  }
  async function runBuild(full, reset) {
    $('#build-log').textContent = 'starting…'; $('#build-result').innerHTML = ''; $('#build-trace').innerHTML = '';
    const j = await api('/api/build', { full, reset: !!reset, overrides: overrides() });
    pollJob(j.job, $('#build-log'), (job) => {
      loadBuildStatus();
      if (job.status !== 'done') return;
      const r = job.result.result, s = r.stats, lc = ((job.result.trace.children || []).find((c) => c.name === 'load_corpus') || {}).meta || {};
      $('#build-result').innerHTML = alertsHtml(r.alerts) + `<div class="stat"><b>${r.mode}</b>mode</div><div class="stat"><b>${fmt(r.ms, 0)}</b>ms</div><div class="stat"><b>${r.docs}</b>docs</div><div class="stat"><b>${lc.skipped_by_stat || 0}</b>stat-skip</div><div class="stat"><b>${r.changed}</b>changed</div><div class="stat"><b>${r.removed}</b>removed</div><div class="stat"><b>${r.renamed || 0}</b>renamed</div><div class="stat"><b>${r.chunks_indexed}</b>chunks</div><div class="stat"><b>${r.embedded == null ? '-' : r.embedded}</b>embedded${r.embed ? ' (cache ' + r.embed.cache_hits + ', fail ' + r.embed.failed + ')' : ''}</div><div class="stat"><b>${s.entities}</b>entities</div><div class="stat"><b>${s.relations}</b>relations</div><div class="stat"><b>${(r.wiki || {}).pages == null ? '-' : r.wiki.pages}</b>wiki pages</div><div class="stat"><b>${r.lint ? r.lint.errors + '/' + r.lint.warnings : '-'}</b>lint err/warn</div><div class="stat"><b>${r.verify ? (r.verify.ok ? 'OK' : r.verify.problems.length + ' 문제') : '-'}</b>verify</div><div class="stat"><b>${fmtK((r.tokens || {}).total_tokens || 0)}</b>LLM tokens</div><div class="stat"><b>#${r.request_id || '-'}</b>request</div>` +
        (r.graph ? `<div class="muted small">graph: ${esc(JSON.stringify(r.graph))}</div>` : '');
      $('#cli-equiv').textContent = job.result.cli;
      renderTrace($('#build-trace'), job.result.trace);
      loadDocs();
    }, () => loadBuildStatus());
  }
  $('#btn-build').onclick = () => runBuild(false);
  $('#btn-build-full').onclick = () => { const reset = $('#build-reset').checked; if (confirm(reset ? '색인 테이블을 비우고 처음부터 다시 만듭니다. 질의 로그·제안·동의어·요청 이력·위키 편집 노트·임베딩 캐시는 보존됩니다. 진행할까요?' : '전체 리빌드는 그래프/임베딩을 모두 다시 만듭니다. 진행할까요?')) runBuild(true, reset); };
  $('#btn-scan').onclick = async () => { const r = await api('/api/watch', { action: 'scan' }); $('#scan-result').textContent = `scan ${r.scan_ms} ms · changed ${r.n_changed} · removed ${r.n_removed}` + (r.n_changed ? ' → ' + r.changed.slice(0, 5).join(', ') : ''); };
  $('#btn-health').onclick = async () => {
    $('#health-out').innerHTML = '검사 중…';
    const h = await api('/api/health');
    $('#health-out').innerHTML = `<h3>Health <span class="pill ${h.ok ? 'ok' : 'bad'}">${h.ok ? 'OK' : 'FAIL'}</span> <span class="muted small">fail ${h.fails} · warn ${h.warnings}</span></h3><table><tr><th></th><th>항목</th><th>상세</th><th>ms</th><th>조치</th></tr>` +
      h.checks.map((c) => `<tr><td>${c.ok ? '<span class="ok">✔</span>' : c.level === 'fail' ? '<span class="bad">✘</span>' : '<span class="warntxt">△</span>'}</td><td>${esc(c.name)}</td><td class="small">${esc(c.detail)}</td><td class="num">${fmt(c.ms, 0)}</td><td class="small muted">${esc(c.fix || '')}</td></tr>`).join('') + '</table>';
  };
  async function verify(fix) {
    $('#verify-out').innerHTML = '검증 중…';
    const v = fix ? await api('/api/build/verify', { fix: true }) : await api('/api/build/verify');
    $('#verify-out').innerHTML = `<h3>정합성 검증 <span class="pill ${v.ok ? 'ok' : 'bad'}">${v.ok ? 'OK' : v.problems + ' 문제'}</span>${fix ? ' <span class="muted small">fixed: ' + esc(JSON.stringify(v.fixed)) + '</span>' : ''}</h3><table><tr><th></th><th>검사</th><th>count</th><th>상세</th></tr>` +
      v.checks.map((c) => `<tr><td>${c.ok ? '<span class="ok">✔</span>' : '<span class="bad">✘</span>'}</td><td>${esc(c.name)}</td><td class="num">${c.count}</td><td class="small">${esc(c.detail)}${!c.ok && c.fixable ? ' <span class="pill">fix 가능</span>' : ''}</td></tr>`).join('') + `</table><div class="muted small">counts: ${esc(JSON.stringify(v.counts))}</div>`;
  }
  $('#btn-verify').onclick = () => verify(false);
  $('#btn-verify-fix').onclick = () => { if (confirm('댕글링/고아/stale 위키 페이지를 정리합니다. 진행할까요?')) verify(true); };
  let DOCS = [];
  async function loadDocs() {
    DOCS = await api('/api/docs'); renderDocs(); loadBuildStatus();
  }
  function renderDocs() {
    const f = ($('#docs-filter').value || '').toLowerCase();
    const rows = DOCS.filter((x) => !f || (x.doc_id + ' ' + (x.doc_type || '') + ' ' + (x.ext_id || '')).toLowerCase().includes(f));
    $('#docs-count').textContent = `${rows.length}/${DOCS.length} 문서`;
    $('#docs').innerHTML = '<table><tr><th>doc_id</th><th>유형</th><th>ID</th><th>날짜</th><th>title</th><th>chunks</th><th>size</th><th>mtime</th></tr>' + rows.slice(0, 500).map((x) => `<tr><td><a href="#" data-doc="${esc(x.doc_id)}">${esc(x.doc_id)}</a></td><td>${esc(x.doc_type || x.kind)}${x.inferred ? ' <small class="muted">(추론)</small>' : ''}</td><td>${esc(x.ext_id || '')}</td><td>${esc(x.date || '')}</td><td>${esc(x.title)}</td><td class="num">${x.n_chunks}</td><td class="num">${fmtK(x.size || 0)}</td><td class="muted">${x.mtime ? new Date(x.mtime * 1000).toLocaleString() : ''}</td></tr>`).join('') + '</table>';
    $$('#docs a').forEach((a) => a.onclick = async (e) => { e.preventDefault(); const ch = await api('/api/doc_chunks?id=' + encodeURIComponent(a.dataset.doc)); $('#console-out').textContent = ch.map((c) => `--- ${c.chunk_id} | ${c.heading}\n${c.text}\n`).join('\n'); switchGroup('observability'); switchTab('console'); });
  }
  $('#docs-filter').addEventListener('input', renderDocs);
  loaders.build = loadDocs;

  // ---------------- EMBED ----------------
  async function loadEmbed() {
    const r = await api('/api/embed/report');
    const cov = r.coverage; const pr = r.progress || {};
    $('#embed-progress').innerHTML = `<div class="statrow"><div class="stat"><b>${esc(r.embedder.provider)}/${esc(r.embedder.model || '-')}</b>임베더 d=${r.embedder.dim} ${esc(r.embedder.store_dtype)}</div><div class="stat"><b>${fmt(100 * cov.coverage, 1)}%</b>coverage (${cov.embedded}/${cov.chunks})</div><div class="stat"><b>${esc(pr.status || 'idle')}</b>마지막 실행</div><div class="stat"><b>${pr.batch || '-'}</b>batch</div><div class="stat"><b>${pr.failed || 0}</b>실패</div><div class="stat"><b>${pr.cache_hits || 0}</b>캐시 적중</div></div>` + (cov.coverage < 1 ? `<div class="banner warn">coverage ${fmt(100 * cov.coverage, 1)}% — 임베딩이 없는 청크는 벡터 채널에서 검색되지 않습니다. 다음 증분 빌드에서 자동 재개됩니다.</div>` : '');
    $('#embed-cov').innerHTML = '<table><tr><th>doc_type</th><th>chunks</th><th>embedded</th><th>coverage</th></tr>' + cov.by_doc_type.map((x) => `<tr><td>${esc(x.doc_type)}</td><td class="num">${x.chunks}</td><td class="num">${x.embedded}</td><td class="num"><div class="bar"><i style="width:${100 * x.coverage}%"></i></div>${fmt(100 * x.coverage, 1)}%</td></tr>`).join('') + '</table>' + (cov.missing_sample.length ? `<div class="muted small">missing: ${esc(cov.missing_sample.slice(0, 8).join(', '))}</div>` : '');
    $('#embed-runs').innerHTML = '<table><tr><th>run</th><th>status</th><th>total</th><th>done</th><th>fail</th><th>cache</th><th>batches</th><th>avg ms</th><th>final batch</th><th>alerts</th></tr>' + (r.runs || []).map((x) => `<tr><td class="mono small">${esc(x.run_id)}</td><td>${esc(x.status)}</td><td class="num">${x.total}</td><td class="num">${x.done}</td><td class="num">${x.failed}</td><td class="num">${x.cache_hits}</td><td class="num">${x.batches}</td><td class="num">${fmt(x.avg_batch_ms, 0)}</td><td class="num">${x.final_batch}</td><td class="small">${(x.alerts || []).map((a) => esc(a.msg)).join('<br>')}</td></tr>`).join('') + '</table>';
    $('#embed-cfg').textContent = JSON.stringify({ cache: r.cache, settings: r.settings, toggles: r.toggles }, null, 1);
    const pc = await api('/api/precompute'); $('#pc-status').textContent = `answer_cache: ${pc.entries} entries (현재 버전 ${pc.current_version}) · hits ${pc.hits}`;
  }
  $('#btn-embed-refresh').onclick = loadEmbed;
  $('#btn-embed-cache-clear').onclick = async () => { if (!confirm('임베딩 캐시(embedding_cache)를 비웁니다. 다음 전체 리빌드에서 모두 다시 임베딩합니다.')) return; toast(JSON.stringify(await api('/api/cli', { argv: 'embed clear-cache' }))); loadEmbed(); };
  $('#btn-docvec').onclick = async () => { toast(JSON.stringify(await api('/api/precompute', { action: 'doc_vectors' }))); };
  $('#btn-pc-run').onclick = async () => { const j = await api('/api/precompute', { action: 'run' }); $('#pc-log').textContent = 'running…'; pollJob(j.job, $('#pc-log'), () => loadEmbed()); };
  $('#btn-pc-clear').onclick = async () => { toast(JSON.stringify(await api('/api/precompute', { action: 'clear' }))); loadEmbed(); };
  loaders.embed = loadEmbed;

  // ---------------- CONTRACT / LINT ----------------
  let SCHEMAS = null;
  async function loadLint() {
    const j = await api('/api/corpus/lint?all=' + ($('#lint-all').checked ? 1 : 0));
    $('#lint-summary').textContent = `summary: ${JSON.stringify(j.summary)} · 유형: ${JSON.stringify(j.doc_types)}`;
    $('#lint-table').innerHTML = '<table><tr><th></th><th>doc_id</th><th>유형</th><th>ID</th><th>err</th><th>warn</th><th>소견</th></tr>' + j.rows.map((r) => `<tr><td>${r.lint_errors ? '<span class="bad">✘</span>' : r.lint_warnings ? '<span class="warntxt">△</span>' : '<span class="ok">✔</span>'}</td><td>${esc(r.doc_id)}</td><td>${esc(r.doc_type || '-')}${r.inferred ? ' <small class="muted">(추론)</small>' : ''}</td><td>${esc(r.ext_id || '')}</td><td class="num">${r.lint_errors}</td><td class="num">${r.lint_warnings}</td><td class="small">${(r.lint || []).filter((x) => x.level !== 'info').map((x) => `[${esc(x.level)}] ${esc(x.field)}: ${esc(x.msg)}`).join('<br>')}</td></tr>`).join('') + '</table>' + (j.rows.length ? '' : '<div class="muted">문제 없음</div>');
    if (!SCHEMAS) {
      SCHEMAS = await api('/api/corpus/types');
      const types = Object.keys(SCHEMAS.schemas).filter((k) => k !== 'common');
      $('#schema-types').innerHTML = '<table><tr><th>doc_type</th><th>설명</th><th>ID 규칙</th><th>필수</th><th>섹션</th></tr>' + types.map((t) => { const s = SCHEMAS.schemas[t]; return `<tr><td><b>${esc(t)}</b></td><td class="small">${esc(s.title || '')}</td><td class="mono small">${esc(s.id_pattern || '')}</td><td class="small">${Object.keys(s.fields || {}).filter((f) => s.fields[f].required).join(', ')}</td><td class="small muted">${((s.sections || {}).recommended || []).join(' · ')}</td></tr>`; }).join('') + `</table><div class="muted small">폴더: ${esc(SCHEMAS.dir)} — 새 유형은 JSON 파일 추가</div>`;
      $('#schema-example-type').innerHTML = types.map((t) => `<option>${t}</option>`).join('');
      $('#schema-example-type').onchange = () => { $('#schema-example').textContent = SCHEMAS.examples[$('#schema-example-type').value] || ''; };
      $('#schema-example-type').onchange();
    }
  }
  $('#btn-lint-refresh').onclick = loadLint; $('#lint-all').onchange = loadLint;
  loaders.contract = loadLint;

  // ---------------- MCP SOURCES ----------------
  async function loadSources() { const j = await api('/api/mcp_sources'); $('#src-json').value = JSON.stringify(j.sources, null, 2); $('#src-msg').textContent = `${j.path} · 토글 mcp_sources=${j.enabled}`; }
  $('#btn-src-test').onclick = async () => { $('#src-out').innerHTML = '테스트 중…'; const r = await api('/api/mcp_sources', { action: 'test' }); $('#src-out').innerHTML = '<table><tr><th>source</th><th>ok</th><th>tools</th><th>ms</th></tr>' + (r || []).map((x) => `<tr><td>${esc(x.name)}</td><td>${x.ok ? '<span class="ok">✔</span>' : '<span class="bad">✘ ' + esc(x.error || '') + '</span>'}</td><td class="small">${esc((x.tools || []).join(', '))}</td><td class="num">${x.ms}</td></tr>`).join('') + '</table>' + ((r || []).length ? '' : '<div class="muted">enabled 소스 없음</div>'); };
  $('#btn-src-ingest').onclick = async () => { $('#src-out').innerHTML = 'ingest 중…'; const r = await api('/api/mcp_sources', { action: 'ingest', dry_run: $('#src-dry').checked }); $('#src-out').innerHTML = `<pre class="pre">${esc(JSON.stringify(r, null, 1))}</pre>`; };
  $('#btn-src-save').onclick = async () => { let s; try { s = JSON.parse($('#src-json').value); } catch (e) { toast('JSON 오류'); return; } await api('/api/mcp_sources', { action: 'save', sources: s }); toast('저장됨'); };
  loaders.sources = loadSources;
  LW.runBuild = runBuild; LW.loadBuildStatus = loadBuildStatus;
})(window.LW);
