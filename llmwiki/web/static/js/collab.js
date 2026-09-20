/* 협업 — 사이드바 고정 채팅(휘발성) + 게시판 + 접속자 캐릭터/말풍선.

   **부수 기능이다.** 서버가 404(토글 collab off)를 주면 조용히 사라지고, 이 파일에서 무슨 일이 나도
   질의·빌드 화면을 막지 않는다 (모든 폴링을 try/catch 로 감싸고, 실패가 이어지면 폴링을 멈춘다).
*/
(function (LW) {
  'use strict';
  const { $, $$, esc, ts, dt, api, toast, STATE, loaders, switchGroup, switchTab } = LW;

  let ON = null;              // null=모름 · true/false
  let SINCE = 0;
  let TIMER = null;
  let FAILS = 0;
  let CFG = {};
  let LAST = null;            // 가장 최근 메시지 (접힌 창의 한 줄)
  let ME = '';
  let REV = -1;               // 접속자 목록의 개정 번호 — 바뀌지 않았으면 서버가 목록을 생략한다
  let PEOPLE = [];
  // 아이콘 표시 여부는 **내 브라우저에만** 적용된다 (남의 화면에는 영향 없음).
  let SHOW_PEOPLE = true;
  // 위치 폴링 주기(ms). 다른 사람이 아이콘을 옮기면 **내 화면에서도 따라 움직여야** 하므로 짧게 잡는다.
  // 협업 API 는 읽기 슬롯을 잡지 않으므로(reqmgr.weight_for_level) 1초 폴링이 질의를 밀어내지 않는다.
  const POLL_MS = 1000;

  function setOn(v) {
    ON = v;
    const box = $('#chat-box'); if (box) box.classList.toggle('hidden', !v);
    const layer = $('#people-layer'); if (layer) layer.classList.toggle('hidden', !v);
    const tab = $$('.tabs button').find((b) => b.dataset.tab === 'board');
    if (tab) tab.classList.toggle('hidden', !v);
  }

  async function poll() {
    if (ON === false) return;
    try {
      // rev 를 같이 보내면 **접속자에 변화가 없을 때 서버가 목록을 생략**한다.
      // 30명이 1초마다 폴링해도 대부분의 응답이 작은 본문으로 끝난다.
      const j = await api('/api/collab?since=' + SINCE + (REV >= 0 ? '&rev=' + REV : ''));
      if (j && j.enabled === false) { setOn(false); stop(); return; }
      if (!j || j.error) { FAILS++; if (FAILS > 5) stop(); return; }
      FAILS = 0;
      if (ON !== true) setOn(true);
      CFG = j.config || CFG;
      SINCE = j.last_id || SINCE;
      if (typeof j.rev === 'number') REV = j.rev;
      ME = j.me || ME;
      const msgs = j.messages || [];
      if (msgs.length) LAST = msgs[msgs.length - 1];
      renderLast();
      if (j.people) {                       // 생략된 응답(people_unchanged)이면 화면을 그대로 둔다
        PEOPLE = j.people;
        renderPeople(PEOPLE, ME);
        const mine = PEOPLE.find((p) => p.user === ME);
        const eb = $('#chat-emoji'); if (eb && mine) eb.textContent = mine.emoji || '🛠';
      }
    } catch (e) { FAILS++; if (FAILS > 5) stop(); }
  }
  function stop() { if (TIMER) { clearInterval(TIMER); TIMER = null; } }

  function renderLast() {
    const le = $('#chat-last'); if (!le) return;
    // 1줄째는 **가장 최근 대화**만 계속 갱신된다 (목록으로 쌓지 않는다 — 대화는 아이콘 위 말풍선으로 본다)
    le.textContent = LAST ? (LAST.user + ': ' + String(LAST.text).slice(0, 80)) : '(대화 없음)';
    if (LAST) le.title = LAST.user + ' · ' + ts(LAST.ts) + '\n' + LAST.text;
  }

  // ---- 접속자 캐릭터: 드래그로 이동 · 말풍선은 아이폰 메시지처럼 아이콘 위에 ----
  // 내 것은 파란 말풍선(오른쪽 꼬리), 남의 것은 회색(왼쪽 꼬리). 머문 시간이 길수록 글씨가 커진다.
  let DRAG = null;
  function renderPeople(people, me) {
    const layer = $('#people-layer'); if (!layer) return;
    layer.classList.toggle('off', !SHOW_PEOPLE);
    if (!SHOW_PEOPLE) return;                 // 숨김 모드: 그리지 않는다 (서버 상태는 그대로)
    const seen = {};
    people.forEach((p) => {
      seen[p.user] = 1;
      let el = layer.querySelector('.person[data-user="' + cssEsc(p.user) + '"]');
      if (!el) {
        el = document.createElement('div');
        el.className = 'person' + (p.user === me ? ' me' : '');
        el.dataset.user = p.user;
        el.innerHTML = '<div class="bubble"></div><div class="avatar"></div>';
        layer.appendChild(el);
        if (p.user === me) wireDrag(el);
      }
      // 내가 지금 끌고 있는 아이콘은 서버 값으로 되돌리지 않는다 (끊겨 보인다)
      if (!(DRAG && DRAG.el === el)) {
        el.style.left = (p.x * 100).toFixed(2) + '%';
        el.style.top = (p.y * 100).toFixed(2) + '%';
      }
      el.querySelector('.avatar').textContent = p.emoji || '🙂';
      const b = el.querySelector('.bubble');
      b.textContent = p.last_text || '';
      b.style.fontSize = (p.font_px || 12) + 'px';
      b.classList.toggle('hidden', !p.last_text);
      el.title = p.user + ' · ' + p.minutes + '분째 · 글자 ' + p.font_px + 'px' + (p.user === me ? ' (끌어서 옮기기)' : '');
    });
    $$('.person', layer).forEach((el) => { if (!seen[el.dataset.user]) el.remove(); });
  }
  function cssEsc(s) { return String(s).replace(/["\\]/g, '\\$&'); }
  function wireDrag(el) {
    el.addEventListener('pointerdown', (e) => {
      DRAG = { el };
      try { el.setPointerCapture(e.pointerId); } catch (err) { /* ignore */ }
      e.preventDefault();
    });
  }
  document.addEventListener('pointermove', (e) => {
    if (!DRAG) return;
    DRAG.el.style.left = ((e.clientX / window.innerWidth) * 100).toFixed(2) + '%';
    DRAG.el.style.top = ((e.clientY / window.innerHeight) * 100).toFixed(2) + '%';
  });
  document.addEventListener('pointerup', async (e) => {
    if (!DRAG) return;
    const x = e.clientX / window.innerWidth, y = e.clientY / window.innerHeight;
    DRAG = null;
    try { await api('/api/collab', { action: 'touch', x, y }); } catch (err) { /* 부수 기능 — 조용히 무시 */ }
  });

  // ---- 보내기 (말풍선으로 뜬다) ----
  async function send() {
    const el = $('#chat-text'); const text = (el.value || '').trim();
    if (!text) return;
    el.value = '';
    const j = await api('/api/collab', { action: 'say', text });
    if (j && j.message) { LAST = j.message; SINCE = Math.max(SINCE, j.message.id); renderLast(); poll(); }
    if (j && j.command && j.command.name === 'post') openPostForm(j.command.title);   // /게시 도 그대로 동작
  }

  async function openPostForm(title) {
    const f = $('#post-modal'); if (!f) return;
    f.classList.remove('hidden');
    $('#cp-title').value = title || '';
    $('#cp-body').value = '';
    // 내 최근 작업을 골라 연결할 수 있게 (요청 이력과 이어 준다)
    const sel = $('#cp-request');
    sel.innerHTML = '<option value="">(작업 연결 안 함)</option>';
    try {
      const r = await api('/api/requests?limit=20');
      ((r && r.rows) || []).forEach((x) => {
        const o = document.createElement('option');
        o.value = x.id; o.textContent = `#${x.id} ${x.kind} · ${String(x.summary || '').slice(0, 40)} (${ts(x.ts)})`;
        sel.appendChild(o);
      });
    } catch (e) { /* 목록을 못 받아도 게시는 된다 */ }
    $('#cp-title').focus();
  }

  async function submitPost() {
    const body = $('#cp-body').value;
    const j = await api('/api/collab', {
      action: 'post', title: $('#cp-title').value, body,
      request_id: $('#cp-request').value ? parseInt($('#cp-request').value, 10) : null,
      kind: $('#cp-kind').value,
    });
    if (j && j.ok) {
      toast('게시판에 올렸습니다');
      $('#post-modal').classList.add('hidden');
      if (LW.tabVisible('board')) loadBoard();
    } else if (j && j.error) {
      toast('게시 실패: ' + j.error);
    }
  }

  // ---- 게시판 ----
  async function loadBoard() {
    const el = $('#board-list'); if (!el) return;
    const qs = new URLSearchParams({ limit: '100' });
    if ($('#board-q') && $('#board-q').value.trim()) qs.set('q', $('#board-q').value.trim());
    if ($('#board-unresolved') && $('#board-unresolved').checked) qs.set('unresolved', '1');
    const j = await api('/api/collab/board?' + qs.toString());
    if (!j || j.error) { el.innerHTML = `<div class="banner">${esc((j && j.error) || '게시판을 읽지 못했습니다')}</div>`; return; }
    let posts = j.posts || [];
    if ($('#board-mine') && $('#board-mine').checked && STATE.auth && STATE.auth.user) posts = posts.filter((p) => p.user === STATE.auth.user.name);
    el.innerHTML = posts.map((p) => `<details class="post${p.resolved ? ' resolved' : ''}" data-post="${esc(p.id)}">` +
      `<summary><span class="pill">${esc(p.kind || 'feedback')}</span> <b>${esc(p.title)}</b> ` +
      `<span class="muted small">${esc(p.user)} · ${dt(p.ts)}${p.request_id ? ' · 작업 #' + p.request_id : ''}${(p.replies || []).length ? ' · 댓글 ' + p.replies.length : ''}${p.resolved ? ' · ✔ 해결' : ''}</span></summary>` +
      `<div class="post-body">${esc(p.body || '').replace(/\n/g, '<br>')}</div>` +
      (p.request_id ? `<div class="muted small">연결된 작업: <a href="#" data-goreq="${p.request_id}">#${p.request_id}</a> ${esc(p.request_summary || '')}</div>` : '') +
      (p.replies || []).map((r) => `<div class="post-reply"><b>${esc(r.user)}</b> <span class="muted small">${dt(r.ts)}</span><div>${esc(r.text)}</div></div>`).join('') +
      `<div class="row"><input class="post-reply-in" type="text" placeholder="댓글"><button class="mini secondary" data-reply="${esc(p.id)}">댓글</button>` +
      `<button class="mini secondary" data-resolve="${esc(p.id)}">${p.resolved ? '미해결로' : '해결로 표시'}</button>` +
      `<button class="mini danger" data-del="${esc(p.id)}">삭제</button></div></details>`).join('') || '<div class="muted">글이 없습니다. 사이드바 대화창에서 <code>/게시 제목</code> 으로 올려 보세요.</div>';
    const note = $('#board-note'); if (note) note.textContent = `전체 ${j.total}건 · 파일: ${j.path}`;
    $$('#board-list [data-reply]').forEach((b) => b.onclick = async () => {
      const inp = b.parentElement.querySelector('.post-reply-in');
      if (!inp.value.trim()) return;
      await api('/api/collab', { action: 'reply', id: b.dataset.reply, text: inp.value });
      loadBoard();
    });
    $$('#board-list [data-resolve]').forEach((b) => b.onclick = async () => {
      const det = b.closest('.post');
      await api('/api/collab', { action: 'resolve', id: b.dataset.resolve, value: !det.classList.contains('resolved') });
      loadBoard();
    });
    $$('#board-list [data-del]').forEach((b) => b.onclick = async () => {
      if (!confirm('이 글을 지웁니다.')) return;
      // 내 글은 admin 이 아니어도 지울 수 있다(서버가 작성자를 확인) → 먼저 remove_mine, 안 되면 admin 경로
      let r = await api('/api/collab', { action: 'remove_mine', id: b.dataset.del });
      if (!r || !r.ok) r = await api('/api/collab', { action: 'remove', id: b.dataset.del });
      if (r && r.ok) loadBoard();
    });
    $$('#board-list [data-goreq]').forEach((a) => a.onclick = (e) => {
      e.preventDefault();
      switchGroup('ask'); switchTab('query');
      setTimeout(() => { const box = $('#myreq-box'); if (box) box.open = true; if (LW.loadMyRequests) LW.loadMyRequests(); }, 200);
    });
  }
  loaders.board = loadBoard;

  // ---- 배선 ----
  if ($('#btn-chat-send')) $('#btn-chat-send').onclick = send;
  if ($('#chat-text')) $('#chat-text').onkeydown = (e) => { if (e.key === 'Enter') send(); };
  // ---- '챗' 하나로 대화창(2줄째)과 접속자 아이콘을 **함께** 켜고 끈다 (내 브라우저에만 적용) ----
  function applyChatMode() {
    const b = $('#btn-chat-toggle');
    if (b) {
      b.classList.toggle('on', SHOW_PEOPLE);
      b.title = SHOW_PEOPLE ? '대화창과 접속자 아이콘 끄기' : '대화창과 접속자 아이콘 켜기';
    }
    const row2 = $('#chat-row2');
    if (row2) row2.classList.toggle('hidden', !SHOW_PEOPLE);
    const layer = $('#people-layer');
    if (layer) { layer.classList.toggle('off', !SHOW_PEOPLE); if (!SHOW_PEOPLE) layer.innerHTML = ''; }
  }
  if ($('#btn-chat-toggle')) $('#btn-chat-toggle').onclick = () => {
    SHOW_PEOPLE = !SHOW_PEOPLE;
    try { localStorage.setItem('llmwiki.showPeople', SHOW_PEOPLE ? '1' : '0'); } catch (e) { /* ignore */ }
    applyChatMode();
    // 다시 켤 때는 화면을 비웠으므로 목록을 통째로 다시 받아야 한다 (rev 를 버린다)
    if (SHOW_PEOPLE) { REV = -1; poll(); const t = $('#chat-text'); if (t) t.focus(); }
  };
  // '게시' 버튼 = 게시 대화상자 (예전에는 게시판 탭으로 이동만 했다)
  if ($('#btn-chat-board')) $('#btn-chat-board').onclick = () => openPostForm('');
  if ($('#btn-cp-submit')) $('#btn-cp-submit').onclick = submitPost;
  if ($('#btn-cp-cancel')) $('#btn-cp-cancel').onclick = () => $('#post-modal').classList.add('hidden');
  if ($('#btn-cp-board')) $('#btn-cp-board').onclick = () => { $('#post-modal').classList.add('hidden'); switchGroup('evolve'); switchTab('board'); };
  if ($('#post-modal')) $('#post-modal').onclick = (e) => { if (e.target === $('#post-modal')) $('#post-modal').classList.add('hidden'); };
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape' && $('#post-modal') && !$('#post-modal').classList.contains('hidden')) $('#post-modal').classList.add('hidden'); });
  if ($('#btn-board-refresh')) $('#btn-board-refresh').onclick = loadBoard;
  ['#board-q', '#board-mine', '#board-unresolved'].forEach((s) => { const e = $(s); if (e) e.onchange = loadBoard; });

  (LW.onReady = LW.onReady || []).push(() => {
    try {
      // 기본은 '챗' 켜짐 (아이콘과 입력칸이 보인다). 끄면 1줄만 남는다.
      SHOW_PEOPLE = localStorage.getItem('llmwiki.showPeople') !== '0';
    } catch (e) { /* ignore */ }
    applyChatMode();
    poll();
    TIMER = setInterval(poll, POLL_MS);
  });
})(window.LW);
