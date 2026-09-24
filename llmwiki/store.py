# -*- coding: utf-8 -*-
"""SQLite 저장소: 문서/청크/FTS5/임베딩/엔티티/관계/멘션/커뮤니티/질의로그/피드백/자가진화 제안.

단일 파일 DB 로 FTS + Vector + Graph 를 모두 담아 이식성과 디버깅 편의를 우선했다.
"""
from __future__ import annotations

import contextlib
import json
import os
import sqlite3
import threading
import time
from typing import Any, Dict, Iterable, List, Optional, Tuple

import numpy as np

from .profiler import count as _count

SCHEMA = """
CREATE TABLE IF NOT EXISTS docs (
  doc_id TEXT PRIMARY KEY, path TEXT, title TEXT, kind TEXT, hash TEXT, meta TEXT,
  n_chunks INTEGER DEFAULT 0, built_at REAL
);
CREATE TABLE IF NOT EXISTS chunks (
  chunk_id TEXT PRIMARY KEY, doc_id TEXT, ordinal INTEGER, heading TEXT, text TEXT,
  start INTEGER, end INTEGER
);
CREATE INDEX IF NOT EXISTS idx_chunks_doc ON chunks(doc_id);
CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
  chunk_id UNINDEXED, doc_id UNINDEXED, heading, body, tokens, tokenize='unicode61'
);
CREATE TABLE IF NOT EXISTS embeddings (
  chunk_id TEXT PRIMARY KEY, provider TEXT, dim INTEGER, vec BLOB
);
CREATE TABLE IF NOT EXISTS entities (
  entity_id TEXT PRIMARY KEY, name TEXT, type TEXT, description TEXT, aliases TEXT,
  source TEXT, confidence REAL, community INTEGER, degree INTEGER DEFAULT 0
);
CREATE VIRTUAL TABLE IF NOT EXISTS entities_fts USING fts5(entity_id UNINDEXED, name, aliases, description, tokenize='unicode61');
CREATE TABLE IF NOT EXISTS relations (
  rel_id TEXT PRIMARY KEY, src TEXT, dst TEXT, rel TEXT, description TEXT, weight REAL,
  source TEXT, confidence REAL, chunk_id TEXT
);
CREATE INDEX IF NOT EXISTS idx_rel_src ON relations(src);
CREATE INDEX IF NOT EXISTS idx_rel_dst ON relations(dst);
-- 문서를 다시 색인할 때 그 문서의 관계를 지운다. 이 인덱스가 없으면 청크마다 relations 전체를 훑어
-- 빌드가 청크 수의 제곱에 비례해 느려진다 (2026-09-15 측정).
CREATE INDEX IF NOT EXISTS idx_rel_chunk ON relations(chunk_id);
CREATE TABLE IF NOT EXISTS mentions (
  entity_id TEXT, chunk_id TEXT, doc_id TEXT, count INTEGER, source TEXT,
  PRIMARY KEY (entity_id, chunk_id)
);
CREATE INDEX IF NOT EXISTS idx_mention_chunk ON mentions(chunk_id);
CREATE TABLE IF NOT EXISTS communities (
  community INTEGER PRIMARY KEY, size INTEGER, top_entities TEXT, summary TEXT, source TEXT
);
CREATE TABLE IF NOT EXISTS query_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, query TEXT, config TEXT, top_chunks TEXT,
  answer TEXT, scores TEXT, trace TEXT, feedback INTEGER, note TEXT,
  -- 2026-09-19: 누가 물었나. requests 와 같은 필드 이름을 쓴다 (Observability 질의·로그의 '사용자' 열).
  user TEXT DEFAULT '', role TEXT DEFAULT '', origin TEXT DEFAULT '', via TEXT DEFAULT '',
  ip TEXT DEFAULT '', agent TEXT DEFAULT '', request_id INTEGER DEFAULT 0
);
-- 2026-09-23: 질의 피드백만 따로, **영구 보존**.
--   `requests` 는 keep_requests(기본 2000행 ≈ 실측 8일)로 잘리는데, 피드백은 사람이 남긴 유일한 학습 신호라
--   같이 잘리면 안 된다. 행 하나가 수십 바이트이고 피드백이 달린 질의만 들어가므로(실측 819건 중 4건)
--   무제한으로 둬도 부담이 없다. query_log 를 은퇴시키면서 그 역할 중 **이것만** 남긴 것이다.
CREATE TABLE IF NOT EXISTS query_feedback (
  request_id INTEGER PRIMARY KEY, ts REAL, query TEXT, user TEXT DEFAULT '',
  feedback INTEGER, note TEXT DEFAULT '', legacy_qid INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_query_feedback_ts ON query_feedback(ts);
CREATE TABLE IF NOT EXISTS proposals (
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, kind TEXT, payload TEXT, reason TEXT,
  confidence REAL, status TEXT, origin TEXT, applied_at REAL, eval_before TEXT, eval_after TEXT
);
CREATE TABLE IF NOT EXISTS evolution_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, proposal_id INTEGER, action TEXT, detail TEXT, checksum TEXT
);
CREATE TABLE IF NOT EXISTS synonyms (term TEXT, expansion TEXT, source TEXT, PRIMARY KEY(term, expansion));
CREATE TABLE IF NOT EXISTS kv (k TEXT PRIMARY KEY, v TEXT);
CREATE TABLE IF NOT EXISTS requests (
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, kind TEXT, summary TEXT, ms REAL,
  llm_calls INTEGER, input_tokens INTEGER, output_tokens INTEGER, sql_count INTEGER,
  debug_level INTEGER, config TEXT, result TEXT, trace TEXT, error TEXT, origin TEXT
);
CREATE INDEX IF NOT EXISTS idx_requests_kind ON requests(kind, id);
-- v3: 문서 메타(front matter 정규화) · 임베딩 캐시/실행 이력 · 포렌식 · 에피소드 · trial · 답변 캐시
CREATE TABLE IF NOT EXISTS doc_meta (
  doc_id TEXT PRIMARY KEY, doc_type TEXT, ext_id TEXT, date TEXT, ts REAL, date_source TEXT, author TEXT, status TEXT,
  tags TEXT, modules TEXT, hw_chip TEXT, hw_rev TEXT, related TEXT, period_from TEXT, period_to TEXT, summary TEXT,
  schema_version INTEGER, inferred INTEGER, lint_errors INTEGER DEFAULT 0, lint_warnings INTEGER DEFAULT 0, lint TEXT, extra TEXT
);
CREATE INDEX IF NOT EXISTS idx_doc_meta_type ON doc_meta(doc_type);
CREATE INDEX IF NOT EXISTS idx_doc_meta_ext ON doc_meta(ext_id);
CREATE INDEX IF NOT EXISTS idx_doc_meta_ts ON doc_meta(ts);
CREATE TABLE IF NOT EXISTS embedding_cache (
  provider TEXT, model TEXT, sha TEXT, dim INTEGER, vec BLOB, ts REAL, PRIMARY KEY (provider, model, sha)
);
CREATE TABLE IF NOT EXISTS embed_runs (
  run_id TEXT PRIMARY KEY, ts_start REAL, ts_end REAL, provider TEXT, model TEXT, dim INTEGER, total INTEGER, done INTEGER,
  failed INTEGER, cache_hits INTEGER, batches INTEGER, avg_batch_ms REAL, final_batch INTEGER, status TEXT, alerts TEXT, failed_ids TEXT
);
CREATE TABLE IF NOT EXISTS forensics (
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, request_id INTEGER, run_id TEXT, query TEXT, verdict TEXT, groundedness REAL,
  findings TEXT, suggestions TEXT, topics TEXT, origin TEXT
);
CREATE INDEX IF NOT EXISTS idx_forensics_req ON forensics(request_id);
CREATE TABLE IF NOT EXISTS episodes (
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, request_id INTEGER, query TEXT, kind TEXT, outcome TEXT, chunks TEXT,
  feedback INTEGER, strength REAL, last_reinforced REAL, topics TEXT, detail TEXT
);
CREATE TABLE IF NOT EXISTS trials (
  trial_id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, ts REAL, build_version INTEGER, config TEXT, questions_hash TEXT,
  summary TEXT, rows TEXT, note TEXT, request_id INTEGER
);
CREATE TABLE IF NOT EXISTS answer_cache (
  key TEXT PRIMARY KEY, build_version INTEGER, query TEXT, result TEXT, ts REAL, hits INTEGER DEFAULT 0, source TEXT
);
CREATE TABLE IF NOT EXISTS doc_vectors (
  doc_id TEXT PRIMARY KEY, provider TEXT, dim INTEGER, vec BLOB, card TEXT
);
"""

# 기존 DB 에 없는 컬럼을 추가하는 마이그레이션 (ALTER TABLE ADD COLUMN 은 SQLite 에서 즉시 완료)
MIGRATIONS = [
    ("docs", "mtime", "REAL DEFAULT 0"),
    ("docs", "size", "INTEGER DEFAULT 0"),
    ("entities", "doc_refs", "TEXT DEFAULT ''"),
    ("entities", "n_docs", "INTEGER DEFAULT 0"),
    ("entities", "n_mentions", "INTEGER DEFAULT 0"),
    ("requests", "run_id", "TEXT DEFAULT ''"),      # logs/ 의 run_id 와 연결
    # 2026-09-16: 누가 낸 요청인지. 예전에는 origin 에 "web kh82.kim" 처럼 **한 문자열로** 들어가서
    # '내 요청만 보기' 를 걸 수 없었고 인덱스도 못 걸었다.
    ("requests", "user", "TEXT DEFAULT ''"),
    ("requests", "file", "TEXT DEFAULT ''"),        # 결과/trace 를 따로 보관한 파일 경로 (data/requests/…)
    ("relations", "provenance", "TEXT DEFAULT ''"),  # explicit | rule | cooccur | llm | human
    ("proposals", "strength", "REAL DEFAULT 1.0"),   # memory decay
    ("proposals", "last_reinforced", "REAL DEFAULT 0"),
    ("proposals", "hits", "INTEGER DEFAULT 1"),
    # 2026-09-19: 질의 로그에도 '누가' 를 남긴다. 예전에는 requests 에만 있어서 Observability 질의·로그에서
    # 어떤 사용자가 무엇을 물었는지 볼 수 없었고, 피드백(👍/👎)이 누구 것인지도 추적할 수 없었다.
    ("query_log", "user", "TEXT DEFAULT ''"),
    ("query_log", "role", "TEXT DEFAULT ''"),
    ("query_log", "origin", "TEXT DEFAULT ''"),    # web | api | cli | mcp | schedule | watch
    ("query_log", "via", "TEXT DEFAULT ''"),       # 로그인 방법: local | oidc | header | apikey | anon
    ("query_log", "ip", "TEXT DEFAULT ''"),
    ("query_log", "agent", "TEXT DEFAULT ''"),     # User-Agent 앞 100자
    ("query_log", "request_id", "INTEGER DEFAULT 0"),   # requests 테이블의 같은 실행
    # 2026-09-19: 문서 스스로 매긴 접근 등급 (front matter `acl:`). extra JSON 에 두지 않고 컬럼으로 뽑는 이유는
    # doc_meta_map() 이 문서 수천 개의 값을 매 질의마다 읽기 때문이다 — JSON 을 전부 파싱하면 접근 제어가 비싸진다.
    ("doc_meta", "acl", "TEXT DEFAULT ''"),
    # 2026-09-23: 질의 로그(query_log) 를 requests 로 합친다. 두 표는 질문·답변·근거·판정·trace 가
    # **내용까지 같았다**(trace 는 바이트까지 동일, 합계 34MB 중복). requests 가 이미 result 를 통째로
    # 담고 있으므로 query_log 의 고유한 값은 피드백뿐이었다 — 그것만 아래 두 열과 query_feedback 으로 옮긴다.
    ("requests", "feedback", "INTEGER"),
    ("requests", "note", "TEXT DEFAULT ''"),
    ("requests", "role", "TEXT DEFAULT ''"),      # 질의 로그가 갖고 있던 '누가' 필드를 requests 로
    ("requests", "via", "TEXT DEFAULT ''"),
    ("requests", "ip", "TEXT DEFAULT ''"),
    ("requests", "agent", "TEXT DEFAULT ''"),
]


class Store:
    """SQLite 저장소.

    병렬 처리(2026-09-15): 연결은 스레드마다 따로 쓴다.
    - `conn` 은 현재 스레드가 session() 으로 빌린 연결(없으면 생성 스레드의 기본 연결)을 돌려준다. 같은 연결을 두 스레드가 나눠 쓰면
      한 스레드의 commit 이 다른 스레드의 반쯤 쓴 트랜잭션을 확정하는 사고가 나므로, 서버는 모든 요청·잡·워처를 `with store.session():` 로 감싼다.
    - WAL 모드라 여러 읽기 연결이 쓰기 연결과 동시에 동작하고, 쓰기끼리는 SQLite 의 busy_timeout 만큼 기다린다 (config db_busy_timeout_s).
    - 벡터 행렬·엔티티 인덱스·doc_meta 캐시는 인스턴스 하나에 공유되며(읽기 전용 numpy), 적재는 락으로 한 번만 한다.
    """

    #: 허용하는 synchronous 값 (config db_synchronous). 모르는 값이면 NORMAL 로 떨어진다.
    SYNCHRONOUS = ("OFF", "NORMAL", "FULL", "EXTRA")

    def __init__(self, path: str, busy_timeout_s: float = 30.0, pool_size: int = 16,
                 max_live: int = 64, wait_timeout_s: float = 2.0, synchronous: str = "NORMAL"):
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        self.path = path
        self.busy_timeout_s = float(busy_timeout_s or 30.0)
        sync = str(synchronous or "NORMAL").strip().upper()
        self.synchronous = sync if sync in self.SYNCHRONOUS else "NORMAL"
        #: 커밋 없이 세션을 벗어난 횟수 (0-C). 0 이 정상 — 늘면 "쓰고 커밋 안 한 코드" 가 있다는 뜻이고,
        #  그 코드는 그동안 SQLite 쓰기 잠금을 쥐고 있었다 (2026-09-23: embed_query 가 그랬다).
        self.uncommitted_exits = 0
        self.pool_size = max(1, int(pool_size or 16))
        # 2026-09-19: pool_size 는 **놀고 있는** 연결만 제한했다. 빌려 간 연결 수에는 상한이 없어서,
        # 세션을 닫지 않는 코드가 하나라도 생기면 연결이 무한히 늘어나도 아무도 몰랐다(메모리·fd).
        # max_live 는 **부드러운** 상한이다 — 넘으면 반납을 잠깐 기다리고, 그래도 없으면 새로 만들되
        # overflow 로 센다. 질의를 실패시키지는 않는다(가용성 우선). 0 = 상한 없음(예전 동작).
        self.max_live = max(0, int(max_live or 0))
        self.wait_timeout_s = max(0.0, float(wait_timeout_s or 0.0))
        self._pool: List[sqlite3.Connection] = []
        self._pool_lock = threading.Condition(threading.Lock())
        self._live = 0            # 지금 빌려 나가 있는 연결 수
        self._peak_live = 0       # 최고 기록 (누수 진단의 핵심 — 부하가 끝나도 0 으로 안 돌아오면 샌 것이다)
        self._created = 0         # 새로 만든 연결 수 (풀 적중률)
        self._overflow = 0        # 상한을 넘겨서 만든 횟수
        self._tl = threading.local()
        self._cache_lock = threading.RLock()
        self.generation = 0      # reopen() 마다 증가 — 이전 세대의 빌린 연결은 풀에 돌려보내지 않고 닫는다
        self._main = self._connect()
        self._main.executescript(SCHEMA)
        self._migrate()
        self._vec_cache: Optional[Tuple[List[str], np.ndarray, str, int]] = None
        self._ent_cache: Optional[Tuple[int, List[Dict[str, Any]]]] = None   # (build_version, entities)
        self.sql_count = 0
        self.closed = False

    # ---------- 연결 관리 ----------
    def _connect(self) -> sqlite3.Connection:
        c = sqlite3.connect(self.path, check_same_thread=False, timeout=self.busy_timeout_s)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA busy_timeout=%d" % int(self.busy_timeout_s * 1000))
        # WAL + NORMAL: 커밋마다 fsync 하지 않고 체크포인트에서만 한다. DB 손상은 없고(WAL 의 보장),
        # 정전 시 최근 몇 건의 커밋만 날아간다. 기본값 FULL 은 질의 1건이 커밋을 여러 번 하는
        # 이 파이프라인에서 동시 질의 지연으로 그대로 나타난다 (config db_synchronous).
        c.execute("PRAGMA synchronous=%s" % self.synchronous)
        c.set_trace_callback(self._on_sql)   # 단계별 SQL 문 수 집계 (profiler 스레드 로컬 카운터 'sql')
        return c

    @property
    def conn(self) -> sqlite3.Connection:
        c = getattr(self._tl, "conn", None)
        return c if c is not None else self._main

    @conn.setter
    def conn(self, c: sqlite3.Connection) -> None:   # 구 코드 호환 (직접 대입)
        self._main = c

    def in_session(self) -> bool:
        return getattr(self._tl, "conn", None) is not None

    @contextlib.contextmanager
    def session(self):
        """현재 스레드 전용 연결을 빌린다 (풀에서 꺼내거나 새로 연결). 중첩 호출은 바깥 세션을 그대로 쓴다.
        끝나면 커밋되지 않은 트랜잭션을 롤백하고 풀에 돌려준다."""
        if getattr(self._tl, "conn", None) is not None or self.closed:
            yield self
            return
        deadline = time.time() + self.wait_timeout_s
        with self._pool_lock:
            gen = self.generation
            c = self._pool.pop() if self._pool else None
            # 상한을 넘었으면 누가 반납하기를 잠깐 기다린다 (버스트를 평평하게 만든다).
            while (c is None and self.max_live and self._live >= self.max_live
                   and not self.closed and time.time() < deadline):
                self._pool_lock.wait(min(0.05, max(0.0, deadline - time.time())))
                c = self._pool.pop() if self._pool else None
            if c is None and self.max_live and self._live >= self.max_live:
                self._overflow += 1      # 기다려도 안 나왔다 — 만들어서라도 진행한다 (요청을 죽이지 않는다)
            self._live += 1
            self._peak_live = max(self._peak_live, self._live)
            if c is None:
                self._created += 1
        if c is None:
            c = self._connect()
        self._tl.conn = c
        try:
            yield self
        finally:
            c = getattr(self._tl, "conn", None) or c    # reopen() 이 이 스레드의 연결을 갈아끼웠을 수 있다
            self._tl.conn = None
            try:
                if c.in_transaction:
                    # 2026-09-23: 예전에는 조용히 rollback 만 했다. 그래서 `cache_put()` 처럼 **커밋 없이 INSERT 하고
                    # 나가는 코드**가 있어도 아무도 몰랐다 — 그 코드는 INSERT 시점부터 여기까지(질의 1건 = 최대 수십 초)
                    # SQLite 쓰기 잠금을 쥐고 있었고, 다른 동시 질의는 db_busy_timeout_s 만큼 기다리다 실패했다.
                    # 이제 경고를 남긴다: 쓴 내용이 버려진다는 사실 자체도 알려야 한다.
                    self.uncommitted_exits += 1
                    c.rollback()
                    self._warn_uncommitted()
            except Exception:
                pass
            with self._pool_lock:
                self._live = max(0, self._live - 1)
                if not self.closed and self.generation == gen and len(self._pool) < self.pool_size:
                    self._pool.append(c)
                    c = None
                self._pool_lock.notify()
            if c is not None:
                try:
                    c.close()
                except Exception:
                    pass

    def _warn_uncommitted(self) -> None:
        """커밋 없이 세션을 벗어난 쓰기를 알린다 (첫 5회만 — 폭주해도 로그를 덮지 않게)."""
        if self.uncommitted_exits > 5:
            return
        try:
            import traceback
            from . import logging_setup as _ls
            _ls.log("warning", "커밋하지 않은 쓰기가 롤백됐습니다 (%d번째). 그동안 SQLite 쓰기 잠금을 쥐고 있었습니다."
                    % self.uncommitted_exits, "query",
                    hint="세션 안에서 INSERT/UPDATE 후 commit() 을 부르지 않은 코드입니다. 아래 호출 위치를 보세요.",
                    stack="".join(traceback.format_stack()[-6:-1]))
        except Exception:
            pass

    def pool_info(self) -> Dict[str, Any]:
        """연결 풀 상태. `live` 가 부하가 끝난 뒤에도 0 으로 안 떨어지면 세션을 닫지 않는 코드가 있는 것이다."""
        with self._pool_lock:
            return {"idle": len(self._pool), "max": self.pool_size, "busy_timeout_s": self.busy_timeout_s,
                    "synchronous": self.synchronous,
                    "live": self._live, "peak_live": self._peak_live, "max_live": self.max_live,
                    "created": self._created, "overflow": self._overflow,
                    # 0 이 정상. 늘면 "쓰고 커밋 안 한 코드" 가 있다는 뜻 (그동안 쓰기 잠금을 쥔다)
                    "uncommitted_exits": self.uncommitted_exits}

    def _on_sql(self, _stmt: str) -> None:
        self.sql_count += 1
        _count("sql", 1)

    def _migrate(self) -> None:
        added = []
        for table, col, decl in MIGRATIONS:
            cols = [r[1] for r in self.conn.execute("PRAGMA table_info(%s)" % table)]
            if col not in cols:
                self.conn.execute("ALTER TABLE %s ADD COLUMN %s %s" % (table, col, decl))
                added.append((table, col))
        if ("requests", "user") in added:
            # 예전 행의 origin("web kh82.kim" / "cli") 에서 사용자 이름만 뽑아 채운다.
            self.conn.execute("UPDATE requests SET user = TRIM(SUBSTR(origin, INSTR(origin, ' ') + 1)) "
                              "WHERE (user IS NULL OR user='') AND origin LIKE '% %'")
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_requests_user ON requests(user, id)")
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_requests_kind_id ON requests(kind, id)")
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_query_log_user ON query_log(user, id)")
        if ("requests", "feedback") in added:
            # query_log 의 피드백을 영구 표로 옮긴다 (한 번만). 사람이 남긴 유일한 학습 신호라
            # requests 의 2000행 상한에 같이 잘리면 안 된다.
            self.conn.execute(
                "INSERT OR IGNORE INTO query_feedback(request_id, ts, query, user, feedback, note, legacy_qid) "
                "SELECT COALESCE(NULLIF(request_id,0), -id), ts, query, user, feedback, COALESCE(note,''), id "
                "FROM query_log WHERE feedback IS NOT NULL")
            # requests 에도 바로 보이게 (목록에서 피드백 열을 그리려고 매번 조인하지 않도록)
            self.conn.execute(
                "UPDATE requests SET feedback = (SELECT ql.feedback FROM query_log ql WHERE ql.request_id = requests.id "
                "AND ql.feedback IS NOT NULL ORDER BY ql.id DESC LIMIT 1) "
                "WHERE EXISTS (SELECT 1 FROM query_log ql WHERE ql.request_id = requests.id AND ql.feedback IS NOT NULL)")
            # 누가 물었나: query_log 에만 있던 role/via/ip/agent 를 requests 로
            self.conn.execute(
                "UPDATE requests SET role = COALESCE((SELECT ql.role FROM query_log ql WHERE ql.request_id=requests.id ORDER BY ql.id DESC LIMIT 1), role), "
                "via = COALESCE((SELECT ql.via FROM query_log ql WHERE ql.request_id=requests.id ORDER BY ql.id DESC LIMIT 1), via), "
                "ip = COALESCE((SELECT ql.ip FROM query_log ql WHERE ql.request_id=requests.id ORDER BY ql.id DESC LIMIT 1), ip), "
                "agent = COALESCE((SELECT ql.agent FROM query_log ql WHERE ql.request_id=requests.id ORDER BY ql.id DESC LIMIT 1), agent) "
                "WHERE EXISTS (SELECT 1 FROM query_log ql WHERE ql.request_id = requests.id)")
        if ("relations", "provenance") in added or self.conn.execute("SELECT 1 FROM relations WHERE provenance='' LIMIT 1").fetchone():
            # 구버전 관계에 provenance 백필: source 기준
            self.conn.execute("UPDATE relations SET provenance = CASE WHEN rel IN ('co_occurs','mentions','mentions_date','mentions_amount') THEN 'cooccur' "
                              "WHEN source LIKE '%llm%' THEN 'llm' WHEN source LIKE '%evolve%' OR source LIKE '%human%' THEN 'human' "
                              "WHEN source LIKE '%explicit%' THEN 'explicit' ELSE 'rule' END WHERE provenance='' OR provenance IS NULL")
        self.conn.commit()

    # ---------- generic ----------
    def close(self) -> None:
        self.closed = True
        self._drop_connections()

    def _drop_connections(self) -> None:
        with self._pool_lock:
            self.generation += 1
            pool, self._pool = self._pool, []
            self._pool_lock.notify_all()     # 닫히는 중에 연결을 기다리던 스레드를 깨운다 (닫힘이면 바로 진행한다)
        for c in pool:
            try:
                c.close()
            except Exception:
                pass
        mine = getattr(self._tl, "conn", None)
        if mine is not None:
            try:
                mine.close()
            except Exception:
                pass
            self._tl.conn = None
        try:
            self._main.close()
        except Exception:
            pass

    def reopen(self, before=None, path: Optional[str] = None, retries: int = 20, wait_s: float = 0.25) -> None:
        """모든 연결을 닫고 (선택) before() 로 DB 파일을 바꾼 뒤 다시 연다 — 스냅샷 복원·롤백용.

        Store 객체는 그대로 두므로 이 인스턴스를 참조하는 다른 스레드/코드가 계속 유효하다 (예전에는 pipe.store 를 통째로 갈아끼워
        파일 교체가 실패하면 서버 전체가 'Cannot operate on a closed database' 로 죽었다).
        Windows 는 연결을 닫아도 잠금 해제가 약간 늦을 수 있어 before() 를 짧게 재시도한다."""
        in_sess = self.in_session()
        self._drop_connections()
        err: Optional[Exception] = None
        if before is not None:
            for i in range(max(1, retries)):
                try:
                    before()
                    err = None
                    break
                except OSError as e:          # WinError 32: 다른 프로세스/연결이 파일을 아직 잡고 있음
                    err = e
                    time.sleep(wait_s * (1 + i * 0.5))
        if path:
            self.path = path
        self.closed = False
        self._main = self._connect()
        self._main.executescript(SCHEMA)
        self._migrate()
        self.invalidate_caches()
        self._meta_cache = None
        self._tri_checked = None
        if in_sess:
            self._tl.conn = self._connect()   # 복원을 요청한 스레드는 남은 작업을 계속해야 한다
        if err is not None:
            raise err

    def kv_get(self, k: str, default: Any = None) -> Any:
        r = self.conn.execute("SELECT v FROM kv WHERE k=?", (k,)).fetchone()
        return json.loads(r["v"]) if r else default

    def kv_set(self, k: str, v: Any) -> None:
        self.conn.execute("INSERT OR REPLACE INTO kv(k,v) VALUES(?,?)", (k, json.dumps(v, ensure_ascii=False)))
        self.conn.commit()

    def stats(self) -> Dict[str, Any]:
        q = lambda sql: self.conn.execute(sql).fetchone()[0]  # noqa: E731
        return {
            "docs": q("SELECT COUNT(*) FROM docs"),
            "chunks": q("SELECT COUNT(*) FROM chunks"),
            "embeddings": q("SELECT COUNT(*) FROM embeddings"),
            "entities": q("SELECT COUNT(*) FROM entities"),
            "relations": q("SELECT COUNT(*) FROM relations"),
            "mentions": q("SELECT COUNT(*) FROM mentions"),
            "communities": q("SELECT COUNT(*) FROM communities"),
            "queries": q("SELECT COUNT(*) FROM query_log"),
            "requests": q("SELECT COUNT(*) FROM requests"),
            "proposals_pending": q("SELECT COUNT(*) FROM proposals WHERE status='proposed'"),
            "synonyms": q("SELECT COUNT(*) FROM synonyms"),
            "db_bytes": os.path.getsize(self.path) if os.path.exists(self.path) else 0,
            "last_build": self.kv_get("last_build"),
        }

    # ---------- docs / chunks ----------
    def doc_hashes(self) -> Dict[str, str]:
        return {r["doc_id"]: r["hash"] for r in self.conn.execute("SELECT doc_id, hash FROM docs")}

    def doc_stats(self) -> Dict[str, Dict[str, Any]]:
        """stat_skip 용: doc_id -> {hash, mtime, size, title, kind}"""
        return {r["doc_id"]: {"hash": r["hash"], "mtime": r["mtime"] or 0, "size": r["size"] or 0, "title": r["title"], "kind": r["kind"]}
                for r in self.conn.execute("SELECT doc_id, hash, mtime, size, title, kind FROM docs")}

    def delete_doc(self, doc_id: str, fts_cleared: bool = False) -> None:
        """문서 하나와 그에 딸린 모든 행을 지운다.

        **청크마다 지우지 않는다.** chunks_fts / chunks_tri 는 chunk_id 가 UNINDEXED 라
        `WHERE chunk_id=?` 가 매번 FTS 전체 스캔이 된다. 청크 N개면 N번 스캔 → 빌드가 N² 로 느려진다
        (2026-09-15 측정: 청크 8,000개 기준 행별 삭제 64초 vs 일괄 0.7초, trigram 이면 129초).
        그래서 doc_id 로 한 번에 지운다. 일반 테이블은 인덱스를 타도록 서브쿼리로 묶는다.

        fts_cleared=True: 호출자가 이미 FTS 테이블을 통째로 비웠으므로 건너뛴다 (전체 리빌드).
        """
        c = self.conn
        sub = "SELECT chunk_id FROM chunks WHERE doc_id=?"        # idx_chunks_doc 사용
        if not fts_cleared:
            c.execute("DELETE FROM chunks_fts WHERE doc_id=?", (doc_id,))
            if self._has_trigram():
                c.execute("DELETE FROM chunks_tri WHERE doc_id=?", (doc_id,))
        c.execute("DELETE FROM embeddings WHERE chunk_id IN (%s)" % sub, (doc_id,))   # chunk_id PK
        c.execute("DELETE FROM mentions WHERE chunk_id IN (%s)" % sub, (doc_id,))     # idx_mention_chunk
        c.execute("DELETE FROM relations WHERE chunk_id IN (%s)" % sub, (doc_id,))    # idx_rel_chunk
        c.execute("DELETE FROM chunks WHERE doc_id=?", (doc_id,))
        c.execute("DELETE FROM docs WHERE doc_id=?", (doc_id,))
        c.execute("DELETE FROM doc_meta WHERE doc_id=?", (doc_id,))
        c.execute("DELETE FROM doc_vectors WHERE doc_id=?", (doc_id,))
        self._vec_cache = None

    def clear_fts(self, trigram: bool = False) -> None:
        """FTS 채널을 통째로 비운다 (전체 리빌드 시작 시 한 번). 문서별로 지우는 것보다 수십 배 빠르다."""
        self.conn.execute("DELETE FROM chunks_fts")
        if trigram:
            self.ensure_trigram()
            self.conn.execute("DELETE FROM chunks_tri")
        elif self._has_trigram():
            self.drop_trigram()

    def upsert_doc(self, doc, chunks, tokens_fn, extra_tokens: str = "", trigram: bool = False, fts: bool = True,
                   fts_cleared: bool = False) -> None:
        """fts=False 면 chunks 만 쓰고 FTS 행은 만들지 않는다 (toggles.build_fts off → 나중에 `build fts` 로 재색인).

        FTS 의 기존 행은 **문서 단위로 한 번만** 지운다 — chunk_id 는 UNINDEXED 라 청크마다 지우면
        매번 전체 스캔이 되어 빌드가 청크 수의 제곱으로 느려진다. fts_cleared=True 면 그것도 건너뛴다.
        """
        c = self.conn
        c.execute("INSERT OR REPLACE INTO docs(doc_id,path,title,kind,hash,meta,n_chunks,built_at,mtime,size) VALUES(?,?,?,?,?,?,?,?,?,?)",
                  (doc.doc_id, doc.path, doc.title, doc.kind, doc.hash, json.dumps(doc.meta, ensure_ascii=False),
                   len(chunks), time.time(), float(getattr(doc, "mtime", 0) or 0), int(getattr(doc, "size", 0) or 0)))
        if trigram and fts:
            self.ensure_trigram()
        if not fts_cleared:      # 이 문서의 기존 FTS 행을 한 번에 (청크마다 지우면 매번 전체 스캔)
            c.execute("DELETE FROM chunks_fts WHERE doc_id=?", (doc.doc_id,))
            if self._has_trigram():
                c.execute("DELETE FROM chunks_tri WHERE doc_id=?", (doc.doc_id,))
        for ch in chunks:
            c.execute("INSERT OR REPLACE INTO chunks(chunk_id,doc_id,ordinal,heading,text,start,end) VALUES(?,?,?,?,?,?,?)",
                      (ch.chunk_id, ch.doc_id, ch.ordinal, ch.heading, ch.text, ch.start, ch.end))
            if not fts:
                continue
            toks = tokens_fn(ch.heading + "\n" + ch.text)
            if extra_tokens:
                toks = toks + " " + extra_tokens
            c.execute("INSERT INTO chunks_fts(chunk_id,doc_id,heading,body,tokens) VALUES(?,?,?,?,?)",
                      (ch.chunk_id, ch.doc_id, ch.heading, ch.text, toks))
            if trigram:
                c.execute("INSERT INTO chunks_tri(chunk_id,doc_id,body) VALUES(?,?,?)", (ch.chunk_id, ch.doc_id, ch.heading + "\n" + ch.text))
        self._vec_cache = None

    def reindex_fts(self, tokens_fn, meta_tokens_fn=None, trigram: bool = False, doc_ids: Optional[List[str]] = None, progress=None) -> Dict[str, Any]:
        """FTS 채널만 다시 만든다 (`build fts`): chunks 테이블은 읽기만 하고 chunks_fts(+chunks_tri) 행을 전부(또는 doc_ids 만) 다시 쓴다.
        embeddings / entities / relations / mentions 는 건드리지 않는다. 반환: {docs, chunks}."""
        c = self.conn
        if doc_ids is None:
            c.execute("DELETE FROM chunks_fts")
            if trigram:
                self.ensure_trigram()
                c.execute("DELETE FROM chunks_tri")
            elif self._has_trigram():
                self.drop_trigram()
            docs = [r["doc_id"] for r in c.execute("SELECT doc_id FROM docs ORDER BY doc_id")]
        else:
            docs = list(doc_ids)
            has_tri = self._has_trigram()
            for d in docs:      # 문서 단위로 한 번씩만 (chunk_id 는 UNINDEXED 라 청크별 삭제는 전체 스캔)
                c.execute("DELETE FROM chunks_fts WHERE doc_id=?", (d,))
                if has_tri:
                    c.execute("DELETE FROM chunks_tri WHERE doc_id=?", (d,))
            if trigram:
                self.ensure_trigram()
        n = 0
        for i, d in enumerate(docs):
            extra = ""
            if meta_tokens_fn is not None:
                nm = self.get_doc_meta(d)
                if nm:
                    try:
                        extra = meta_tokens_fn(nm) or ""
                    except Exception:
                        extra = ""
            for r in c.execute("SELECT chunk_id, doc_id, heading, text FROM chunks WHERE doc_id=? ORDER BY ordinal", (d,)).fetchall():
                toks = tokens_fn((r["heading"] or "") + "\n" + (r["text"] or ""))
                if extra:
                    toks = toks + " " + extra
                c.execute("INSERT INTO chunks_fts(chunk_id,doc_id,heading,body,tokens) VALUES(?,?,?,?,?)", (r["chunk_id"], r["doc_id"], r["heading"], r["text"], toks))
                if trigram:
                    c.execute("INSERT INTO chunks_tri(chunk_id,doc_id,body) VALUES(?,?,?)", (r["chunk_id"], r["doc_id"], (r["heading"] or "") + "\n" + (r["text"] or "")))
                n += 1
            if progress and (i % 50 == 0 or i == len(docs) - 1):
                progress(i + 1, len(docs), d)
            if i % 200 == 199:
                c.commit()
        c.commit()
        return {"docs": len(docs), "chunks": n}

    # ---------- trigram 폴백 FTS (한글 부분 문자열) ----------
    _tri_checked: Optional[bool] = None

    def _has_trigram(self) -> bool:
        if self._tri_checked is None:
            self._tri_checked = bool(self.conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='chunks_tri'").fetchone())
        return self._tri_checked

    def ensure_trigram(self) -> bool:
        if self._has_trigram():
            return True
        try:
            self.conn.execute("CREATE VIRTUAL TABLE IF NOT EXISTS chunks_tri USING fts5(chunk_id UNINDEXED, doc_id UNINDEXED, body, tokenize='trigram')")
            self._tri_checked = True
            return True
        except sqlite3.OperationalError:
            self._tri_checked = False
            return False

    def drop_trigram(self) -> None:
        self.conn.execute("DROP TABLE IF EXISTS chunks_tri")
        self._tri_checked = False

    def trigram_search(self, text: str, k: int) -> List[Tuple[str, float, str]]:
        if not self._has_trigram():
            return []
        toks = [t for t in text.replace('"', " ").split() if len(t) >= 3]
        if not toks:
            return []
        match = " OR ".join('"%s"' % t for t in toks)
        try:
            rows = self.conn.execute("SELECT chunk_id, bm25(chunks_tri) s, snippet(chunks_tri, 2, '[', ']', '…', 18) snip FROM chunks_tri WHERE chunks_tri MATCH ? ORDER BY s LIMIT ?",
                                     (match, k)).fetchall()
        except sqlite3.OperationalError:
            return []
        return [(r["chunk_id"], -float(r["s"]), r["snip"]) for r in rows]

    # ---------- doc_meta ----------
    def upsert_doc_meta(self, nm: Dict[str, Any], lint: Optional[List[Dict[str, str]]] = None) -> None:
        lint = lint or []
        self.conn.execute(
            "INSERT OR REPLACE INTO doc_meta(doc_id,doc_type,ext_id,date,ts,date_source,author,status,tags,modules,hw_chip,hw_rev,related,"
            "period_from,period_to,summary,schema_version,inferred,lint_errors,lint_warnings,lint,extra,acl) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (nm["doc_id"], nm.get("doc_type", ""), nm.get("ext_id", ""), nm.get("date", ""), float(nm.get("ts") or 0), nm.get("date_source", ""),
             nm.get("author", ""), nm.get("status", ""), json.dumps(nm.get("tags", []), ensure_ascii=False), json.dumps(nm.get("modules", []), ensure_ascii=False),
             nm.get("hw_chip", ""), nm.get("hw_rev", ""), json.dumps(nm.get("related", {}), ensure_ascii=False), nm.get("period_from", ""), nm.get("period_to", ""),
             nm.get("summary", ""), int(nm.get("schema_version") or 0), 1 if nm.get("inferred") else 0,
             sum(1 for x in lint if x["level"] == "error"), sum(1 for x in lint if x["level"] == "warn"),
             json.dumps(lint, ensure_ascii=False), json.dumps(nm.get("extra", {}), ensure_ascii=False, default=str),
             # front matter `acl:` — 문자열/목록 모두 쉼표 구분 문자열로 저장한다 (llmwiki/docacl.py 가 그대로 읽는다)
             (",".join(str(x).strip() for x in nm["acl"] if str(x).strip())
              if isinstance(nm.get("acl"), (list, tuple)) else str(nm.get("acl") or "").strip())))

    def get_doc_meta(self, doc_id: str) -> Optional[Dict[str, Any]]:
        r = self.conn.execute("SELECT * FROM doc_meta WHERE doc_id=?", (doc_id,)).fetchone()
        return self._meta_row(r) if r else None

    @staticmethod
    def _meta_row(r: sqlite3.Row) -> Dict[str, Any]:
        d = dict(r)
        for k in ("tags", "modules", "related", "lint", "extra"):
            try:
                d[k] = json.loads(d.get(k) or ("[]" if k in ("tags", "modules", "lint") else "{}"))
            except Exception:
                pass
        return d

    def doc_meta_map(self) -> Dict[str, Dict[str, Any]]:
        """doc_id -> {doc_type, ext_id, date, ts, tags…} (build_version 별 캐시)."""
        v = self.build_version()
        cache = getattr(self, "_meta_cache", None)
        if cache and cache[0] == v:
            return cache[1]
        with self._cache_lock:
            cache = getattr(self, "_meta_cache", None)
            if cache and cache[0] == v:
                return cache[1]
            out = {r["doc_id"]: {"doc_type": r["doc_type"], "ext_id": r["ext_id"], "date": r["date"], "ts": r["ts"] or 0.0, "status": r["status"],
                                 "hw_rev": r["hw_rev"], "inferred": r["inferred"], "acl": r["acl"] or ""}
                   for r in self.conn.execute("SELECT doc_id, doc_type, ext_id, date, ts, status, hw_rev, inferred, acl FROM doc_meta")}
            self._meta_cache = (v, out)
        return out

    def docs_by_ext_id(self, ext_id: str) -> List[str]:
        return [r["doc_id"] for r in self.conn.execute("SELECT doc_id FROM doc_meta WHERE ext_id=?", (ext_id,))]

    def doc_type_counts(self) -> Dict[str, int]:
        return {(r["doc_type"] or "(none)"): int(r["n"]) for r in self.conn.execute("SELECT doc_type, COUNT(*) n FROM doc_meta GROUP BY doc_type")}

    def lint_rows(self, only_problems: bool = True, limit: int = 500) -> List[Dict[str, Any]]:
        sql = "SELECT doc_id, doc_type, ext_id, inferred, lint_errors, lint_warnings, lint FROM doc_meta"
        if only_problems:
            sql += " WHERE lint_errors > 0 OR lint_warnings > 0"
        sql += " ORDER BY lint_errors DESC, lint_warnings DESC, doc_id LIMIT ?"
        out = []
        for r in self.conn.execute(sql, (limit,)):
            d = dict(r)
            try:
                d["lint"] = json.loads(d["lint"] or "[]")
            except Exception:
                d["lint"] = []
            out.append(d)
        return out

    # ---------- embedding cache / runs ----------
    @staticmethod
    def norm_model(model: str) -> str:
        """캐시 키로 쓸 모델 이름을 정규화한다 (2026-09-23).

        Ollama 는 같은 모델을 `bge-m3` 와 `bge-m3:latest` 두 이름으로 부른다. 캐시 키에 이름이 그대로 들어가 있어서
        표기가 한 번 바뀌자 **전체가 캐시 미스가 되어 처음부터 다시 임베딩**됐고, 두 벌이 남아 65MB 를 낭비했다
        (실측: 16,654개 sha 가 중복). `:latest` 는 태그가 없을 때의 기본값이므로 같은 것으로 본다.
        """
        m = str(model or "").strip()
        return m[: -len(":latest")] if m.lower().endswith(":latest") else m

    def cache_get(self, provider: str, model: str, shas: List[str]) -> Dict[str, np.ndarray]:
        """캐시 조회. 정규화된 이름과 **원래 이름을 함께** 본다 — 예전에 `:latest` 로 저장된 행도 그대로 쓴다
        (정규화만 하고 끝내면 기존 캐시가 전부 미스가 되어 재임베딩이 일어난다)."""
        names = [self.norm_model(model)]
        if model and model not in names:
            names.append(model)
        out: Dict[str, np.ndarray] = {}
        for i in range(0, len(shas), 500):
            part = shas[i:i + 500]
            qs = ",".join("?" * len(part))
            ns = ",".join("?" * len(names))
            for r in self.conn.execute("SELECT sha, dim, vec FROM embedding_cache WHERE provider=? AND model IN (%s) AND sha IN (%s)"
                                       % (ns, qs), [provider] + names + part):
                out.setdefault(r["sha"], self._decode_vec(r["vec"], int(r["dim"])).astype(np.float32))
        return out

    def cache_put(self, provider: str, model: str, items: List[Tuple[str, np.ndarray]], dtype: str = "float32",
                  commit: bool = False) -> None:
        """임베딩 캐시에 넣는다. 새 행은 **정규화된 모델 이름**으로 쓴다.

        `commit=True` 를 쓰는 곳: **질의 경로**(`retrieval.embed_query`). 이유는 이렇다 —
        Python sqlite3 는 INSERT 앞에서 트랜잭션을 암묵적으로 열고, SQLite 는 첫 쓰기에서 WRITER 잠금을 잡아
        COMMIT 까지 놓지 않는다. 질의 경로에서는 이 INSERT 가 **벡터 검색 초반**에 일어나고 다음 커밋은
        **질의 맨 끝**이라, 질의 1건이 자기 수명(실측 98~118초) 내내 쓰기 잠금을 혼자 쥐고 있었다.
        다른 동시 질의는 db_busy_timeout_s(60초)까지 기다린 뒤 'database is locked' 로 실패했다 (2026-09-23).
        빌드(`embed_run`)는 배치 단위로 커밋하므로 기본값 False 를 그대로 쓴다.
        """
        np_dt = np.float16 if dtype == "float16" else np.float32
        now = time.time()
        name = self.norm_model(model)
        for sha, v in items:
            v = np.asarray(v, dtype=np_dt)
            self.conn.execute("INSERT OR REPLACE INTO embedding_cache(provider,model,sha,dim,vec,ts) VALUES(?,?,?,?,?,?)",
                              (provider, name, sha, int(v.shape[0]), v.tobytes(), now))
        if commit:
            self.conn.commit()

    def cache_merge_models(self, dry_run: bool = False) -> Dict[str, Any]:
        """모델 이름 표기가 갈려 중복 저장된 캐시 행을 정규화된 이름으로 합친다 (`maintenance cache_merge`).

        `bge-m3:latest` 의 행 중 정규화 이름(`bge-m3`)에 같은 sha 가 이미 있으면 **버리고**, 없으면 **이름만 바꾼다**.
        벡터 값은 같은 모델이므로 어느 쪽을 남겨도 같다. 실패해도 최악은 '다음 빌드에서 다시 임베딩' 이다.
        """
        rows = self.conn.execute("SELECT provider, model, COUNT(*) n FROM embedding_cache GROUP BY provider, model").fetchall()
        plan = [(r["provider"], r["model"], self.norm_model(r["model"]), int(r["n"]))
                for r in rows if r["model"] != self.norm_model(r["model"])]
        out: Dict[str, Any] = {"groups": [{"provider": p, "from": m, "to": t, "rows": n} for p, m, t, n in plan],
                               "moved": 0, "dropped": 0, "dry_run": bool(dry_run)}
        if dry_run or not plan:
            return out
        for prov, old, new, _n in plan:
            cur = self.conn.execute(
                "DELETE FROM embedding_cache WHERE provider=? AND model=? AND sha IN "
                "(SELECT sha FROM embedding_cache WHERE provider=? AND model=?)", (prov, old, prov, new))
            out["dropped"] += int(cur.rowcount or 0)
            cur = self.conn.execute("UPDATE embedding_cache SET model=? WHERE provider=? AND model=?", (new, prov, old))
            out["moved"] += int(cur.rowcount or 0)
        self.conn.commit()
        return out

    def cache_stats(self) -> Dict[str, Any]:
        rows = self.conn.execute("SELECT provider, model, dim, COUNT(*) n FROM embedding_cache GROUP BY provider, model, dim").fetchall()
        return {"entries": sum(int(r["n"]) for r in rows), "by_model": [dict(r) for r in rows]}

    def cache_clear(self, provider: Optional[str] = None) -> int:
        cur = self.conn.execute("DELETE FROM embedding_cache" + (" WHERE provider=?" if provider else ""), (provider,) if provider else ())
        self.conn.commit()
        return cur.rowcount

    def put_embed_run(self, r: Dict[str, Any]) -> None:
        self.conn.execute("INSERT OR REPLACE INTO embed_runs(run_id,ts_start,ts_end,provider,model,dim,total,done,failed,cache_hits,batches,avg_batch_ms,final_batch,status,alerts,failed_ids) "
                          "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                          (r["run_id"], r.get("ts_start"), r.get("ts_end"), r.get("provider"), r.get("model"), r.get("dim"), r.get("total", 0), r.get("done", 0),
                           r.get("failed", 0), r.get("cache_hits", 0), r.get("batches", 0), r.get("avg_batch_ms", 0.0), r.get("final_batch", 0), r.get("status", ""),
                           json.dumps(r.get("alerts", []), ensure_ascii=False), json.dumps(r.get("failed_ids", [])[:500])))

    def embed_runs(self, limit: int = 20) -> List[Dict[str, Any]]:
        out = []
        for r in self.conn.execute("SELECT * FROM embed_runs ORDER BY ts_start DESC LIMIT ?", (limit,)):
            d = dict(r)
            for k in ("alerts", "failed_ids"):
                try:
                    d[k] = json.loads(d[k] or "[]")
                except Exception:
                    d[k] = []
            out.append(d)
        return out

    def embed_coverage(self, provider: str) -> Dict[str, Any]:
        total = self.conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
        have = self.conn.execute("SELECT COUNT(*) FROM chunks c JOIN embeddings e ON c.chunk_id=e.chunk_id WHERE e.provider=?", (provider,)).fetchone()[0]
        by_type = []
        for r in self.conn.execute(
                "SELECT COALESCE(m.doc_type,'(none)') dt, COUNT(*) n, SUM(CASE WHEN e.chunk_id IS NULL THEN 0 ELSE 1 END) have FROM chunks c "
                "LEFT JOIN doc_meta m ON c.doc_id=m.doc_id LEFT JOIN embeddings e ON c.chunk_id=e.chunk_id AND e.provider=? GROUP BY dt", (provider,)):
            by_type.append({"doc_type": r["dt"], "chunks": int(r["n"]), "embedded": int(r["have"] or 0),
                            "coverage": round(float(r["have"] or 0) / max(1, r["n"]), 3)})
        missing = [r["chunk_id"] for r in self.conn.execute(
            "SELECT c.chunk_id FROM chunks c LEFT JOIN embeddings e ON c.chunk_id=e.chunk_id AND e.provider=? WHERE e.chunk_id IS NULL LIMIT 50", (provider,))]
        return {"provider": provider, "chunks": int(total), "embedded": int(have), "coverage": round(float(have) / max(1, total), 4),
                "by_doc_type": by_type, "missing_sample": missing}

    def commit(self) -> None:
        self.conn.commit()

    def get_chunk(self, chunk_id: str) -> Optional[sqlite3.Row]:
        return self.conn.execute("SELECT * FROM chunks WHERE chunk_id=?", (chunk_id,)).fetchone()

    def get_chunks(self, ids: Iterable[str]) -> Dict[str, sqlite3.Row]:
        ids = list(ids)
        if not ids:
            return {}
        out: Dict[str, sqlite3.Row] = {}
        for i in range(0, len(ids), 500):
            part = ids[i:i + 500]
            qs = ",".join("?" * len(part))
            for r in self.conn.execute("SELECT * FROM chunks WHERE chunk_id IN (%s)" % qs, part):
                out[r["chunk_id"]] = r
        return out

    def all_chunks(self, doc_id: Optional[str] = None) -> List[sqlite3.Row]:
        if doc_id:
            return self.conn.execute("SELECT * FROM chunks WHERE doc_id=? ORDER BY ordinal", (doc_id,)).fetchall()
        return self.conn.execute("SELECT * FROM chunks ORDER BY doc_id, ordinal").fetchall()

    def list_docs(self) -> List[Dict[str, Any]]:
        return [dict(r) for r in self.conn.execute("SELECT doc_id,title,kind,hash,n_chunks,built_at,path,mtime,size FROM docs ORDER BY doc_id")]

    def chunk_ids_of_docs(self, doc_ids: Iterable[str]) -> List[str]:
        ids = list(doc_ids)
        out: List[str] = []
        for i in range(0, len(ids), 500):
            part = ids[i:i + 500]
            qs = ",".join("?" * len(part))
            out.extend(r["chunk_id"] for r in self.conn.execute("SELECT chunk_id FROM chunks WHERE doc_id IN (%s)" % qs, part))
        return out

    def fts_optimize(self) -> None:
        """FTS5 세그먼트 병합 + 통계 갱신 (전체 빌드 후 1회; 질의 속도 유지)."""
        self.conn.execute("INSERT INTO chunks_fts(chunks_fts) VALUES('optimize')")
        self.conn.execute("INSERT INTO entities_fts(entities_fts) VALUES('optimize')")
        try:
            self.conn.execute("PRAGMA optimize")
        except Exception:
            pass
        self.conn.commit()

    def wal_checkpoint(self) -> None:
        try:
            self.conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        except Exception:
            pass

    # ---------- FTS ----------
    def fts_search(self, match: str, k: int, weights: Tuple[float, float, float] = (2.0, 1.0, 1.5),
                   snippet_tokens: int = 18) -> List[Tuple[str, float, str]]:
        """returns [(chunk_id, bm25_score(양수, 클수록 좋음), snippet)]. weights = (heading, body, tokens) BM25 컬럼 가중치."""
        wh, wb, wt = (float(x) for x in weights)
        sql = ("SELECT chunk_id, bm25(chunks_fts, 0, 0, %f, %f, %f) AS s, "
               "snippet(chunks_fts, 3, '[', ']', '…', %d) AS snip FROM chunks_fts WHERE chunks_fts MATCH ? ORDER BY s LIMIT ?"
               % (wh, wb, wt, int(snippet_tokens)))
        try:
            rows = self.conn.execute(sql, (match, k)).fetchall()
        except sqlite3.OperationalError:
            return []
        return [(r["chunk_id"], -float(r["s"]), r["snip"]) for r in rows]

    def neighbor_chunks(self, chunk_id: str, n: int = 1) -> List[sqlite3.Row]:
        """같은 문서의 앞/뒤 n개 청크 (ordinal 기준)."""
        c = self.get_chunk(chunk_id)
        if not c or n <= 0:
            return []
        return self.conn.execute("SELECT * FROM chunks WHERE doc_id=? AND ordinal BETWEEN ? AND ? AND chunk_id != ? ORDER BY ordinal",
                                 (c["doc_id"], int(c["ordinal"]) - n, int(c["ordinal"]) + n, chunk_id)).fetchall()

    # ---------- embeddings ----------
    def put_embeddings(self, provider: str, items: List[Tuple[str, np.ndarray]], dtype: str = "float32") -> None:
        """dtype: float32 | float16 (저장 크기 절반; 행 길이로 dtype 을 역추론하므로 혼재 가능)."""
        np_dt = np.float16 if dtype == "float16" else np.float32
        for cid, v in items:
            v = np.asarray(v, dtype=np_dt)
            self.conn.execute("INSERT OR REPLACE INTO embeddings(chunk_id,provider,dim,vec) VALUES(?,?,?,?)",
                              (cid, provider, int(v.shape[0]), v.tobytes()))
        self._vec_cache = None

    @staticmethod
    def _decode_vec(blob: bytes, dim: int) -> np.ndarray:
        if len(blob) == dim * 2:
            return np.frombuffer(blob, dtype=np.float16)
        return np.frombuffer(blob, dtype=np.float32)

    def missing_embeddings(self, provider: str) -> List[str]:
        return [r["chunk_id"] for r in self.conn.execute(
            "SELECT c.chunk_id FROM chunks c LEFT JOIN embeddings e ON c.chunk_id=e.chunk_id "
            "WHERE e.chunk_id IS NULL OR e.provider != ?", (provider,))]

    # ---------- build version (다른 프로세스의 빌드 감지용) ----------
    def build_version(self) -> int:
        return int(self.kv_get("build_version", 0) or 0)

    def bump_build_version(self) -> int:
        v = self.build_version() + 1
        self.kv_set("build_version", v)
        self._vec_cache = None
        return v

    def prune_orphan_entities(self, return_names: bool = False) -> Any:
        """멘션도 관계도 없는 엔티티 제거 (evolve 가 직접 만든 노드는 유지). return_names 면 (n, [name]) 반환."""
        rows = self.conn.execute(
            "SELECT e.entity_id, e.name FROM entities e WHERE e.source NOT LIKE '%evolve%' "
            "AND NOT EXISTS (SELECT 1 FROM mentions m WHERE m.entity_id=e.entity_id) "
            "AND NOT EXISTS (SELECT 1 FROM relations r WHERE r.src=e.entity_id OR r.dst=e.entity_id)").fetchall()
        for r in rows:
            self.conn.execute("DELETE FROM entities WHERE entity_id=?", (r[0],))
            self.conn.execute("DELETE FROM entities_fts WHERE entity_id=?", (r[0],))
        if rows:
            self._ent_cache = None
        if return_names:
            return len(rows), [r[1] for r in rows]
        return len(rows)

    # ---------- 정합성 검증 ----------
    def verify(self, embed_provider: Optional[str] = None, fix: bool = False, wiki_dir: Optional[str] = None) -> Dict[str, Any]:
        """색인 정합성 검사. fix=True 면 안전한 정리(댕글링·고아·n_chunks·doc_refs)를 수행."""
        c = self.conn
        q = lambda sql, *a: c.execute(sql, a).fetchone()[0]  # noqa: E731
        checks: List[Dict[str, Any]] = []

        def add(name: str, bad: int, detail: str, fixable: bool = False) -> None:
            checks.append({"name": name, "ok": bad == 0, "count": int(bad), "detail": detail, "fixable": fixable})

        n_chunks = q("SELECT COUNT(*) FROM chunks")
        n_fts = q("SELECT COUNT(*) FROM chunks_fts")
        add("fts_rows", abs(n_fts - n_chunks), "chunks=%d chunks_fts=%d" % (n_chunks, n_fts), True)
        add("fts_orphans", q("SELECT COUNT(*) FROM chunks_fts WHERE chunk_id NOT IN (SELECT chunk_id FROM chunks)"), "chunks 에 없는 FTS 행", True)
        add("fts_missing", q("SELECT COUNT(*) FROM chunks WHERE chunk_id NOT IN (SELECT chunk_id FROM chunks_fts)"), "FTS 에 없는 청크 (재색인 필요)", False)
        add("chunk_orphans", q("SELECT COUNT(*) FROM chunks WHERE doc_id NOT IN (SELECT doc_id FROM docs)"), "docs 에 없는 청크", True)
        add("doc_nchunks", q("SELECT COUNT(*) FROM docs d WHERE n_chunks != (SELECT COUNT(*) FROM chunks c WHERE c.doc_id=d.doc_id)"), "docs.n_chunks 불일치", True)
        add("embedding_orphans", q("SELECT COUNT(*) FROM embeddings WHERE chunk_id NOT IN (SELECT chunk_id FROM chunks)"), "청크 없는 임베딩", True)
        if embed_provider:
            miss = q("SELECT COUNT(*) FROM chunks c WHERE NOT EXISTS (SELECT 1 FROM embeddings e WHERE e.chunk_id=c.chunk_id AND e.provider=?)", embed_provider)
            add("embedding_coverage", miss, "provider=%s missing=%d/%d" % (embed_provider, miss, n_chunks), False)
            other = q("SELECT COUNT(*) FROM embeddings WHERE provider != ?", embed_provider)
            add("embedding_other_provider", other, "다른 프로바이더 벡터 %d (prune 대상)" % other, True)
        add("mention_dangling", q("SELECT COUNT(*) FROM mentions WHERE chunk_id NOT IN (SELECT chunk_id FROM chunks)"), "청크 없는 멘션", True)
        add("mention_no_entity", q("SELECT COUNT(*) FROM mentions WHERE entity_id NOT IN (SELECT entity_id FROM entities)"), "엔티티 없는 멘션", True)
        add("relation_dangling", q("SELECT COUNT(*) FROM relations WHERE chunk_id != '' AND chunk_id NOT IN (SELECT chunk_id FROM chunks)"), "청크 없는 관계", True)
        add("relation_no_entity", q("SELECT COUNT(*) FROM relations WHERE src NOT IN (SELECT entity_id FROM entities) OR dst NOT IN (SELECT entity_id FROM entities)"), "엔티티 없는 관계", True)
        add("entity_orphans", q("SELECT COUNT(*) FROM entities e WHERE e.source NOT LIKE '%evolve%' AND NOT EXISTS (SELECT 1 FROM mentions m WHERE m.entity_id=e.entity_id) "
                                "AND NOT EXISTS (SELECT 1 FROM relations r WHERE r.src=e.entity_id OR r.dst=e.entity_id)"), "멘션/관계 없는 엔티티", True)
        add("entity_fts", abs(q("SELECT COUNT(*) FROM entities") - q("SELECT COUNT(*) FROM entities_fts")), "entities ↔ entities_fts 행수", True)
        add("doc_meta_missing", q("SELECT COUNT(*) FROM docs WHERE doc_id NOT IN (SELECT doc_id FROM doc_meta) AND doc_id NOT LIKE 'wiki/%'"), "doc_meta 없는 문서 (구버전 색인 → build --full)", False)
        add("doc_meta_orphans", q("SELECT COUNT(*) FROM doc_meta WHERE doc_id NOT IN (SELECT doc_id FROM docs)"), "docs 없는 doc_meta", True)
        n_comm = q("SELECT COUNT(*) FROM communities")
        if n_comm:
            add("community_unassigned", q("SELECT COUNT(*) FROM entities WHERE community IS NULL AND type NOT IN ('document','date','amount')"),
                "커뮤니티 미배정 엔티티 (증분 빌드 후 정상; build --full 또는 incremental_communities)", False)
        if self._has_trigram():
            add("trigram_rows", abs(q("SELECT COUNT(*) FROM chunks_tri") - n_chunks), "chunks_tri ↔ chunks 행수", True)
        stale_pages: List[str] = []
        if wiki_dir and os.path.isdir(wiki_dir):
            from .wiki import slug, _read_note
            names = {slug(r["name"]) + ".md" for r in c.execute("SELECT name FROM entities WHERE type NOT IN ('date','amount')")}
            for fn in os.listdir(wiki_dir):
                if fn.endswith(".md") and fn != "INDEX.md" and fn not in names and not _read_note(os.path.join(wiki_dir, fn)):
                    stale_pages.append(fn)
            add("wiki_stale_pages", len(stale_pages), "엔티티가 사라진 위키 페이지(편집 노트 없음)", True)
        fixed: Dict[str, int] = {}
        if fix:
            fixed["fts_orphans"] = c.execute("DELETE FROM chunks_fts WHERE chunk_id NOT IN (SELECT chunk_id FROM chunks)").rowcount
            fixed["chunk_orphans"] = c.execute("DELETE FROM chunks WHERE doc_id NOT IN (SELECT doc_id FROM docs)").rowcount
            fixed["doc_nchunks"] = c.execute("UPDATE docs SET n_chunks = (SELECT COUNT(*) FROM chunks c WHERE c.doc_id=docs.doc_id)").rowcount
            fixed.update(self.prune_dangling())
            fixed["mention_no_entity"] = c.execute("DELETE FROM mentions WHERE entity_id NOT IN (SELECT entity_id FROM entities)").rowcount
            fixed["relation_no_entity"] = c.execute("DELETE FROM relations WHERE src NOT IN (SELECT entity_id FROM entities) OR dst NOT IN (SELECT entity_id FROM entities)").rowcount
            fixed["entity_orphans"] = self.prune_orphan_entities()
            fixed["doc_meta_orphans"] = c.execute("DELETE FROM doc_meta WHERE doc_id NOT IN (SELECT doc_id FROM docs)").rowcount
            if embed_provider:
                fixed["embedding_other_provider"] = self.prune_embeddings(embed_provider)
            c.execute("DELETE FROM entities_fts WHERE entity_id NOT IN (SELECT entity_id FROM entities)")
            if self._has_trigram():
                fixed["trigram_rows"] = c.execute("DELETE FROM chunks_tri WHERE chunk_id NOT IN (SELECT chunk_id FROM chunks)").rowcount
            self.refresh_doc_refs(None)
            for fn in stale_pages:
                try:
                    os.remove(os.path.join(wiki_dir, fn))
                    fixed["wiki_stale_pages"] = fixed.get("wiki_stale_pages", 0) + 1
                except OSError:
                    pass
            self.commit()
            self.invalidate_caches()
            after = self.verify(embed_provider, fix=False, wiki_dir=wiki_dir)
            after["fixed"] = {k: v for k, v in fixed.items() if v}
            after["before"] = [x for x in checks if not x["ok"]]
            return after
        problems = [x for x in checks if not x["ok"]]
        return {"ok": not problems, "problems": len(problems), "checks": checks, "fixed": fixed,
                "counts": {"docs": q("SELECT COUNT(*) FROM docs"), "chunks": n_chunks, "entities": q("SELECT COUNT(*) FROM entities"),
                           "relations": q("SELECT COUNT(*) FROM relations"), "embeddings": q("SELECT COUNT(*) FROM embeddings")}}

    def prune_embeddings(self, keep_provider: str) -> int:
        cur = self.conn.execute("DELETE FROM embeddings WHERE provider != ?", (keep_provider,))
        self._vec_cache = None
        return cur.rowcount

    def prune_dangling(self) -> Dict[str, int]:
        """chunks 에 없는 chunk_id 를 참조하는 행 정리 (안전망)."""
        out = {}
        for t in ("chunks_fts", "embeddings", "mentions"):
            cur = self.conn.execute("DELETE FROM %s WHERE chunk_id NOT IN (SELECT chunk_id FROM chunks)" % t)
            out[t] = cur.rowcount
        cur = self.conn.execute("DELETE FROM relations WHERE chunk_id != '' AND chunk_id NOT IN (SELECT chunk_id FROM chunks)")
        out["relations"] = cur.rowcount
        self._vec_cache = None
        return out

    def vector_cache_info(self) -> Dict[str, Any]:
        if not self._vec_cache:
            return {"loaded": False}
        ids, mat, prov, ver = self._vec_cache
        return {"loaded": True, "provider": prov, "n": len(ids), "dim": int(mat.shape[1]) if mat.size else 0,
                "bytes": int(mat.nbytes), "build_version": ver}

    def vector_matrix(self, provider: str) -> Tuple[List[str], np.ndarray]:
        vc = self._vec_cache
        if vc and vc[2] == provider and vc[3] == self.build_version():
            return vc[0], vc[1]
        with self._cache_lock:   # 동시에 여러 질의가 캐시 미스를 보면 한 스레드만 적재하고 나머지는 결과를 공유
            vc = self._vec_cache
            v = self.build_version()
            if vc and vc[2] == provider and vc[3] == v:
                return vc[0], vc[1]
            self.last_vec_load_ms = 0.0
            t0 = time.perf_counter()
            rows = self.conn.execute("SELECT chunk_id, dim, vec FROM embeddings WHERE provider=?", (provider,)).fetchall()
            if not rows:
                self._vec_cache = ([], np.zeros((0, 1), dtype=np.float32), provider, v)
                return [], self._vec_cache[1]
            dim = rows[0]["dim"]
            rows = [r for r in rows if r["dim"] == dim]
            ids = [r["chunk_id"] for r in rows]
            vecs = [self._decode_vec(r["vec"], dim) for r in rows]
            # 모두 float16 이면 행렬도 float16 으로 유지(메모리 절반; 검색은 블록 단위 float32 변환), 아니면 float32
            if vecs and all(v_.dtype == np.float16 for v_ in vecs):
                mat = np.vstack(vecs)
            else:
                mat = np.vstack([v_.astype(np.float32) for v_ in vecs])
            self._vec_cache = (ids, mat, provider, v)
            self.last_vec_load_ms = (time.perf_counter() - t0) * 1000
            return ids, mat

    # ---------- graph ----------
    def clear_graph_for_chunks(self, chunk_ids: Iterable[str]) -> None:
        for cid in chunk_ids:
            self.conn.execute("DELETE FROM mentions WHERE chunk_id=?", (cid,))
            self.conn.execute("DELETE FROM relations WHERE chunk_id=?", (cid,))

    def clear_graph(self) -> None:
        for t in ("entities", "entities_fts", "relations", "mentions", "communities"):
            self.conn.execute("DELETE FROM %s" % t)
        self._ent_cache = None

    def upsert_entity(self, entity_id: str, name: str, etype: str, description: str, aliases: List[str],
                      source: str, confidence: float) -> None:
        r = self.conn.execute("SELECT * FROM entities WHERE entity_id=?", (entity_id,)).fetchone()
        if r:
            old_al = set(json.loads(r["aliases"] or "[]"))
            aliases = sorted(old_al | set(aliases))
            description = description if len(description or "") > len(r["description"] or "") else r["description"]
            src = r["source"] if source in (r["source"] or "") else (r["source"] or "") + "+" + source
            confidence = max(confidence, r["confidence"] or 0)
        else:
            src = source
        # INSERT OR REPLACE 는 행을 지우고 다시 넣으므로 doc_refs/n_docs/n_mentions 를 보존해 넘긴다 (finalize 에서 갱신됨)
        self.conn.execute("INSERT OR REPLACE INTO entities(entity_id,name,type,description,aliases,source,confidence,community,degree,doc_refs,n_docs,n_mentions) "
                          "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                          (entity_id, name, etype, description or "", json.dumps(aliases, ensure_ascii=False), src, confidence,
                           r["community"] if r else None, r["degree"] if r else 0,
                           (r["doc_refs"] if r else "") or "", (r["n_docs"] if r else 0) or 0, (r["n_mentions"] if r else 0) or 0))
        self.conn.execute("DELETE FROM entities_fts WHERE entity_id=?", (entity_id,))
        self.conn.execute("INSERT INTO entities_fts(entity_id,name,aliases,description) VALUES(?,?,?,?)",
                          (entity_id, name, " ".join(aliases), description or ""))
        self._ent_cache = None

    def add_mention(self, entity_id: str, chunk_id: str, doc_id: str, count: int, source: str) -> None:
        self.conn.execute("INSERT OR REPLACE INTO mentions(entity_id,chunk_id,doc_id,count,source) VALUES(?,?,?,?,?)",
                          (entity_id, chunk_id, doc_id, count, source))

    PROVENANCE_OF_REL = {"co_occurs": "cooccur", "mentions": "cooccur", "mentions_date": "cooccur", "mentions_amount": "cooccur"}

    def add_relation(self, src: str, dst: str, rel: str, description: str, weight: float, source: str,
                     confidence: float, chunk_id: str, provenance: str = "") -> None:
        """provenance: explicit(front matter related) | rule(정규식/ID 규칙) | cooccur(공동출현) | llm | human(승인된 제안·편집)"""
        if not provenance:
            provenance = self.PROVENANCE_OF_REL.get(rel) or ("llm" if "llm" in source else "human" if ("evolve" in source or "human" in source) else "rule")
        rel_id = "%s|%s|%s|%s" % (src, rel, dst, chunk_id)
        old = self.conn.execute("SELECT confidence, provenance FROM relations WHERE rel_id=?", (rel_id,)).fetchone()
        if old and old["provenance"] and old["provenance"] != provenance:
            # 같은 관계를 다른 출처가 다시 관측 → 신뢰도 결합(최대 + 보너스), provenance 는 더 강한 쪽 유지
            rank = {"explicit": 5, "human": 4, "rule": 3, "llm": 2, "cooccur": 1}
            confidence = min(1.0, max(float(confidence), float(old["confidence"] or 0)) + 0.05)
            provenance = provenance if rank.get(provenance, 0) >= rank.get(old["provenance"], 0) else old["provenance"]
        self.conn.execute("INSERT OR REPLACE INTO relations(rel_id,src,dst,rel,description,weight,source,confidence,chunk_id,provenance) "
                          "VALUES(?,?,?,?,?,?,?,?,?,?)", (rel_id, src, dst, rel, description, weight, source, confidence, chunk_id, provenance))

    def provenance_counts(self) -> Dict[str, int]:
        return {(r["provenance"] or "?"): int(r["n"]) for r in self.conn.execute("SELECT provenance, COUNT(*) n FROM relations GROUP BY provenance")}

    def get_entity(self, entity_id: str) -> Optional[Dict[str, Any]]:
        r = self.conn.execute("SELECT * FROM entities WHERE entity_id=?", (entity_id,)).fetchone()
        return dict(r) if r else None

    def entities(self, limit: int = 100000) -> List[Dict[str, Any]]:
        return [dict(r) for r in self.conn.execute("SELECT * FROM entities ORDER BY degree DESC LIMIT ?", (limit,))]

    def entity_index(self) -> List[Dict[str, Any]]:
        """질의 시 반복 사용되는 경량 엔티티 목록(id, name, aliases(list, lower), degree, type) — build_version 별 캐시."""
        v = self.build_version()
        ec = self._ent_cache
        if ec and ec[0] == v:
            return ec[1]
        with self._cache_lock:
            ec = self._ent_cache
            if ec and ec[0] == v:
                return ec[1]
            out: List[Dict[str, Any]] = []
            for r in self.conn.execute("SELECT entity_id, name, type, aliases, degree FROM entities"):
                names = [r["name"].lower()] + [a.lower() for a in json.loads(r["aliases"] or "[]")]
                out.append({"entity_id": r["entity_id"], "name": r["name"], "type": r["type"], "degree": float(r["degree"] or 1),
                            "names": [n for n in names if len(n) >= 2]})
            self._ent_cache = (v, out)
        return out

    def invalidate_caches(self) -> None:
        self._vec_cache = None
        self._ent_cache = None

    def refresh_doc_refs(self, entity_ids: Optional[Iterable[str]] = None) -> int:
        """엔티티 노드에 문서 참조(doc_refs: [{doc_id, mentions, chunks, first_chunk}]) 를 비정규화해 저장.
        entity_ids 가 None 이면 전체. 반환: 갱신된 엔티티 수."""
        if entity_ids is None:
            targets = [r["entity_id"] for r in self.conn.execute("SELECT entity_id FROM entities")]
        else:
            targets = list(dict.fromkeys(entity_ids))
        n = 0
        for i in range(0, len(targets), 400):
            part = targets[i:i + 400]
            qs = ",".join("?" * len(part))
            rows = self.conn.execute(
                "SELECT entity_id, doc_id, SUM(count) m, COUNT(*) c, MIN(chunk_id) first_chunk FROM mentions "
                "WHERE entity_id IN (%s) GROUP BY entity_id, doc_id ORDER BY entity_id, m DESC" % qs, part).fetchall()
            per: Dict[str, List[Dict[str, Any]]] = {}
            for r in rows:
                per.setdefault(r["entity_id"], []).append({"doc_id": r["doc_id"], "mentions": int(r["m"] or 0),
                                                            "chunks": int(r["c"] or 0), "first_chunk": r["first_chunk"]})
            for eid in part:
                refs = per.get(eid, [])[:50]
                self.conn.execute("UPDATE entities SET doc_refs=?, n_docs=?, n_mentions=? WHERE entity_id=?",
                                  (json.dumps(refs, ensure_ascii=False), len(per.get(eid, [])),
                                   sum(x["mentions"] for x in per.get(eid, [])), eid))
                n += 1
        self._ent_cache = None
        return n

    def entities_for_chunks(self, chunk_ids: Iterable[str]) -> List[str]:
        ids = list(chunk_ids)
        out: List[str] = []
        for i in range(0, len(ids), 500):
            part = ids[i:i + 500]
            qs = ",".join("?" * len(part))
            out.extend(r["entity_id"] for r in self.conn.execute(
                "SELECT DISTINCT entity_id FROM mentions WHERE chunk_id IN (%s)" % qs, part))
            out.extend(r["e"] for r in self.conn.execute(
                "SELECT src e FROM relations WHERE chunk_id IN (%s) UNION SELECT dst e FROM relations WHERE chunk_id IN (%s)" % (qs, qs), part + part))
        return list(dict.fromkeys(out))

    def entity_fts(self, match: str, k: int = 10) -> List[Tuple[str, float]]:
        try:
            rows = self.conn.execute("SELECT entity_id, bm25(entities_fts, 0, 3.0, 2.0, 0.5) s FROM entities_fts WHERE entities_fts MATCH ? "
                                     "ORDER BY s LIMIT ?", (match, k)).fetchall()
        except sqlite3.OperationalError:
            return []
        return [(r["entity_id"], -float(r["s"])) for r in rows]

    def neighbors(self, entity_ids: Iterable[str]) -> List[Dict[str, Any]]:
        ids = list(entity_ids)
        if not ids:
            return []
        qs = ",".join("?" * len(ids))
        rows = self.conn.execute("SELECT * FROM relations WHERE src IN (%s) OR dst IN (%s)" % (qs, qs), ids + ids).fetchall()
        return [dict(r) for r in rows]

    def relations_all(self) -> List[Dict[str, Any]]:
        return [dict(r) for r in self.conn.execute("SELECT * FROM relations")]

    def relations_of(self, entity_id: str) -> List[Dict[str, Any]]:
        return [dict(r) for r in self.conn.execute("SELECT * FROM relations WHERE src=? OR dst=? ORDER BY weight DESC", (entity_id, entity_id))]

    def chunks_for_entities(self, entity_ids: Iterable[str], limit: int = 50) -> List[Tuple[str, float]]:
        ids = list(entity_ids)
        if not ids:
            return []
        qs = ",".join("?" * len(ids))
        rows = self.conn.execute("SELECT chunk_id, SUM(count) c, COUNT(DISTINCT entity_id) n FROM mentions WHERE entity_id IN (%s) "
                                 "GROUP BY chunk_id ORDER BY n DESC, c DESC LIMIT ?" % qs, ids + [limit]).fetchall()
        return [(r["chunk_id"], float(r["n"]) + 0.1 * float(r["c"])) for r in rows]

    def mentions_of(self, entity_id: str) -> List[Dict[str, Any]]:
        return [dict(r) for r in self.conn.execute("SELECT * FROM mentions WHERE entity_id=? ORDER BY count DESC", (entity_id,))]

    def update_degrees(self) -> None:
        # 단일 SQL (엔티티 수만큼 UPDATE 를 반복하지 않음 — 대규모 그래프에서 수십 배 빠름)
        self.conn.execute(
            "UPDATE entities SET degree = COALESCE((SELECT COUNT(*) FROM (SELECT src e FROM relations UNION ALL SELECT dst e FROM relations) t "
            "WHERE t.e = entities.entity_id), 0)")
        self._ent_cache = None

    def set_community(self, entity_id: str, community: int) -> None:
        self.conn.execute("UPDATE entities SET community=? WHERE entity_id=?", (community, entity_id))

    def put_community(self, community: int, size: int, top_entities: List[str], summary: str, source: str) -> None:
        self.conn.execute("INSERT OR REPLACE INTO communities(community,size,top_entities,summary,source) VALUES(?,?,?,?,?)",
                          (community, size, json.dumps(top_entities, ensure_ascii=False), summary, source))

    def communities_all(self) -> List[Dict[str, Any]]:
        return [dict(r) for r in self.conn.execute("SELECT * FROM communities ORDER BY size DESC")]

    # ---------- synonyms ----------
    def synonyms(self) -> Dict[str, List[str]]:
        out: Dict[str, List[str]] = {}
        for r in self.conn.execute("SELECT term, expansion FROM synonyms"):
            out.setdefault(r["term"], []).append(r["expansion"])
        return out

    def add_synonym(self, term: str, expansion: str, source: str) -> None:
        self.conn.execute("INSERT OR REPLACE INTO synonyms(term,expansion,source) VALUES(?,?,?)", (term, expansion, source))

    # ---------- requests (모든 요청의 프로파일/디버그 trace) ----------
    def _warn_locked(self, what: str, e: Exception) -> None:
        try:
            from . import logging_setup as _ls
            _ls.log("warning", "%s 기록을 건너뜁니다 (DB 쓰기 잠금): %s" % (what, str(e)[:160]), "query",
                    hint="다른 프로세스(서버 워처·CLI 빌드)가 쓰는 중. config.json db_busy_timeout_s 를 늘리거나 빌드 시간을 피하세요")
        except Exception:
            pass

    def log_request(self, kind: str, summary: str, trace: Dict[str, Any], result: Any = None, config: Any = None,
                    error: Optional[str] = None, origin: str = "", keep: int = 2000, user: str = "",
                    archive_dir: str = "", commit: bool = True) -> int:
        """요청 프로파일 기록. **관측용이므로 실패해도 명령/질의를 죽이지 않는다** — 다른 프로세스가 빌드 중이라
        쓰기 잠금을 못 얻으면 경고만 남기고 0 을 돌려준다 (2026-09-15: 서버 워처와 CLI 질의가 겹쳐 'database is locked' 로 질의가 실패했다)."""
        try:
            return self._log_request(kind, summary, trace, result, config, error, origin, keep, user, archive_dir, commit)
        except (sqlite3.OperationalError, UnicodeEncodeError) as e:
            # UnicodeEncodeError: 경계에서 걸러지지만, 어떤 경로로든 SQLite 가 담지 못하는 문자열이
            # 들어와도 관측 기록 때문에 질의가 죽지는 않게 한다.
            self._warn_locked("요청 프로파일", e)
            return 0

    def _log_request(self, kind: str, summary: str, trace: Dict[str, Any], result: Any = None, config: Any = None,
                     error: Optional[str] = None, origin: str = "", keep: int = 2000, user: str = "",
                     archive_dir: str = "", commit: bool = True) -> int:
        sm = (trace or {}).get("summary") or {}
        llm = sm.get("llm") or {}
        # 누가 어디서 물었나 — 진행 레지스트리의 client(서버 핸들러 `_client()` 가 넣는다)에서 읽는다.
        # 2026-09-24: 예전에는 origin·user 만 썼고 role·via·ip·agent 는 **옛 행 마이그레이션에서만** 채워져,
        # 09-23 에 질의 로그 원천을 requests 로 합친 뒤 admin 화면의 IP·에이전트 열이 새 행부터 비었다 (verify_web 이 잡음).
        cl: Dict[str, Any] = {}
        try:
            from . import progress as _pg
            cl = (_pg.get(_pg.current_token() or "") or {}).get("client") or {}
        except Exception:
            cl = {}
        if not origin or not user:
            origin = origin or " ".join(x for x in (cl.get("origin"), cl.get("user")) if x)[:80]
            user = user or str(cl.get("user") or "")[:80]
        role, via = str(cl.get("role") or "")[:40], str(cl.get("via") or "")[:40]
        ip, agent = str(cl.get("ip") or "")[:64], str(cl.get("agent") or "")[:100]
        cur = self.conn.execute(
            "INSERT INTO requests(ts,kind,summary,ms,llm_calls,input_tokens,output_tokens,sql_count,debug_level,config,result,trace,error,origin,run_id,user,"
            "role,via,ip,agent) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (time.time(), kind, summary[:300], float((trace or {}).get("ms", 0) or 0), int(llm.get("calls", 0) or 0),
             int(llm.get("input_tokens", 0) or 0), int(llm.get("output_tokens", 0) or 0), int(sm.get("sql_statements", 0) or 0),
             int((trace or {}).get("debug_level", 0) or 0), json.dumps(config, ensure_ascii=False) if config is not None else None,
             json.dumps(result, ensure_ascii=False) if result is not None else None,
             json.dumps(trace, ensure_ascii=False) if trace is not None else None, error, origin,
             str((trace or {}).get("run_id") or ""), user, role, via, ip, agent))
        rid = int(cur.lastrowid)
        # 결과 원본을 DB 밖 파일로도 남긴다 — keep_requests 로 DB 행이 잘려도 "그때 그 답" 을 다시 볼 수 있게.
        if archive_dir:
            try:
                path = self.archive_request(archive_dir, rid, {
                    "id": rid, "ts": time.time(), "kind": kind, "summary": summary[:300], "user": user, "origin": origin,
                    "run_id": str((trace or {}).get("run_id") or ""), "ms": float((trace or {}).get("ms", 0) or 0),
                    "error": error, "config": config, "result": result, "trace": trace})
                self.conn.execute("UPDATE requests SET file=? WHERE id=?", (path, rid))
            except Exception as e:
                self._warn_locked("요청 결과 파일 보관", e)
        if keep and rid % 50 == 0:  # 주기적으로 오래된 요청 정리 (파일은 requests_keep_days 로 따로 정리)
            self.conn.execute("DELETE FROM requests WHERE id <= ?", (rid - keep,))
        if commit:
            self.conn.commit()
        return rid

    # ---------- 요청 결과 보관 파일 (data/requests/<yyyy-mm>/req_<id>.json) ----------
    @staticmethod
    def archive_request(base_dir: str, rid: int, payload: Dict[str, Any]) -> str:
        sub = os.path.join(base_dir, time.strftime("%Y-%m"))
        os.makedirs(sub, exist_ok=True)
        path = os.path.join(sub, "req_%d.json" % rid)
        from . import atomicio
        atomicio.write_json(path, payload)
        return path

    @staticmethod
    def read_archived_request(path: str) -> Optional[Dict[str, Any]]:
        if not path or not os.path.exists(path):
            return None
        try:
            from . import atomicio
            d = atomicio.read_json(path)
            return d if isinstance(d, dict) else None
        except Exception:
            return None

    @staticmethod
    def prune_request_archive(base_dir: str, keep_days: int) -> Dict[str, Any]:
        """보관 파일 정리. keep_days <= 0 이면 아무 것도 지우지 않는다."""
        if not base_dir or keep_days <= 0 or not os.path.isdir(base_dir):
            return {"removed": 0, "kept": 0, "skipped": True}
        cutoff = time.time() - keep_days * 86400
        removed = kept = 0
        for root, _dirs, files in os.walk(base_dir):
            for fn in files:
                if not fn.startswith("req_") or not fn.endswith(".json"):
                    continue
                fp = os.path.join(root, fn)
                try:
                    if os.path.getmtime(fp) < cutoff:
                        os.remove(fp)
                        removed += 1
                    else:
                        kept += 1
                except OSError:
                    pass
        return {"removed": removed, "kept": kept, "dir": base_dir, "keep_days": keep_days}

    def request_by_run(self, run_id: str) -> Optional[Dict[str, Any]]:
        r = self.conn.execute("SELECT id FROM requests WHERE run_id=? ORDER BY id DESC LIMIT 1", (run_id,)).fetchone()
        return self.get_request(int(r["id"])) if r else None

    def requests(self, kind: Optional[str] = None, limit: int = 100, user: Optional[str] = None,
                 q: Optional[str] = None) -> List[Dict[str, Any]]:
        """요청 목록. user 를 주면 그 사람 것만, q 를 주면 요약에서 찾는다."""
        sql = "SELECT id,ts,kind,summary,ms,llm_calls,input_tokens,output_tokens,sql_count,debug_level,error,origin,run_id,user,file FROM requests"
        where: List[str] = []
        args: List[Any] = []
        if kind:
            where.append("kind=?")
            args.append(kind)
        if user:
            where.append("user=?")
            args.append(user)
        if q:
            where.append("summary LIKE ?")
            args.append("%" + q + "%")
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY id DESC LIMIT ?"
        args.append(limit)
        return [dict(r) for r in self.conn.execute(sql, args)]

    def get_request(self, rid: int, archive_dir: str = "") -> Optional[Dict[str, Any]]:
        r = self.conn.execute("SELECT * FROM requests WHERE id=?", (rid,)).fetchone()
        if not r:
            # DB 행이 keep_requests 로 잘렸어도 보관 파일이 있으면 그것으로 답한다
            # (사용자에게는 "그때 그 답이 사라졌다" 가 가장 나쁜 실패다).
            if archive_dir:
                for sub in sorted(os.listdir(archive_dir), reverse=True) if os.path.isdir(archive_dir) else []:
                    d = self.read_archived_request(os.path.join(archive_dir, sub, "req_%d.json" % rid))
                    if d:
                        d["from_archive"] = True
                        return d
            return None
        d = dict(r)
        for k in ("config", "result", "trace"):
            try:
                d[k] = json.loads(d[k]) if d[k] else None
            except Exception:
                pass
        return d

    def request_series(self, kind: str, limit: int = 50) -> List[Dict[str, Any]]:
        return [dict(r) for r in self.conn.execute(
            "SELECT id, ts, ms, llm_calls, input_tokens, output_tokens, summary FROM requests WHERE kind=? ORDER BY id DESC LIMIT ?", (kind, limit))][::-1]

    # ---------- query log / proposals ----------
    def current_actor(self) -> Dict[str, str]:
        """지금 요청을 낸 사람 (진행 레지스트리의 client). 없으면 빈 값.

        Web·MCP·CLI·스케줄러 모두 `reqmgr.ticket(client=…)` 으로 같은 모양의 dict 를 남기므로
        여기 한 곳에서 읽으면 어느 창구로 들어온 질의든 '누가' 를 알 수 있다.
        """
        try:
            from . import progress as _pg
            cl = (_pg.get(_pg.current_token() or "") or {}).get("client") or {}
        except Exception:
            cl = {}
        return {"user": str(cl.get("user") or "")[:80], "role": str(cl.get("role") or "")[:40],
                "origin": str(cl.get("origin") or "")[:20], "via": str(cl.get("via") or "")[:20],
                "ip": str(cl.get("ip") or "")[:64], "agent": str(cl.get("agent") or "")[:100]}

    def log_query(self, query: str, config: Dict[str, Any], top_chunks: List[str], answer: str,
                  scores: Dict[str, Any], trace: Dict[str, Any], actor: Optional[Dict[str, Any]] = None,
                  request_id: int = 0, commit: bool = True) -> int:
        """질의 로그(자가진화 입력). 쓰기 잠금이면 건너뛴다 — 답변은 이미 만들어졌으므로 기록 실패로 질의를 실패시키지 않는다.

        actor 를 주지 않으면 진행 레지스트리에서 지금 요청의 클라이언트를 읽어 '누가 물었나' 를 함께 남긴다.
        """
        a = dict(actor or self.current_actor())
        # 2026-09-23: `trace` 는 **더 이상 여기에 저장하지 않는다.**
        # 같은 trace 가 requests 에도 통째로 들어가 바이트까지 같았다 (실측: 43,072 / 43,072 바이트,
        # 전체로는 requests 66.2MB + query_log 33.9MB = 34MB 중복). 질의 1건당 직렬화도 두 번 했다.
        # 읽는 쪽(`get_query`)이 `request_id` 로 requests.trace 를 가져오므로 보이는 정보는 그대로다.
        # 옛 행에는 값이 남아 있고 `maintenance trim_query_log` 로 회수한다.
        try:
            cur = self.conn.execute(
                "INSERT INTO query_log(ts,query,config,top_chunks,answer,scores,trace,feedback,note,"
                "user,role,origin,via,ip,agent,request_id) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (time.time(), query, json.dumps(config, ensure_ascii=False), json.dumps(top_chunks), answer,
                 json.dumps(scores, ensure_ascii=False), None, None, "",
                 str(a.get("user") or ""), str(a.get("role") or ""), str(a.get("origin") or ""),
                 str(a.get("via") or ""), str(a.get("ip") or ""), str(a.get("agent") or ""), int(request_id or 0)))
            if commit:
                self.conn.commit()
            return int(cur.lastrowid)
        except (sqlite3.OperationalError, UnicodeEncodeError) as e:
            self._warn_locked("질의 로그", e)
            return 0

    @contextlib.contextmanager
    def write_bundle(self, what: str = "질의 기록"):
        """끝에 하는 관측 쓰기들을 **한 트랜잭션**으로 묶는다 (2026-09-23).

        왜: 질의 1건이 끝날 때 `proposals` · `query_log` · `requests` · `query_log UPDATE` · `episodes` 를
        **각각 커밋**했다. SQLite 쓰기는 WAL 에서도 한 번에 하나라, 동시 질의 8건이 비슷한 시점에 끝나면
        40~48개의 쓰기 트랜잭션이 한 줄로 선다. 게다가 중간 커밋이 실패하면 그 뒤 연결(`set_query_request_id`)이
        끊겨 **`query_log.request_id` 가 비어 버렸다** — 실측 805건 중 63건(8%)만 채워져 있었다.

        묶으면 잠금 획득이 1회로 줄고, **id 연결이 같은 트랜잭션 안에서 끝나므로 100% 채워진다.**
        실패해도 관측 기록일 뿐이므로 예외를 올리지 않고 경고만 남긴다(질의는 이미 답을 냈다).
        """
        conn = self.conn
        try:
            yield conn
            conn.commit()
        except (sqlite3.OperationalError, UnicodeEncodeError) as e:
            try:
                conn.rollback()
            except Exception:
                pass
            self._warn_locked(what, e)

    def set_query_request_id(self, qid: int, request_id: int, commit: bool = True) -> None:
        """질의 로그 행을 requests 행과 연결한다 (질의 로그가 먼저 쓰이므로 뒤에 채운다).

        `commit=False` 는 `write_bundle()` 안에서 부를 때 — 그때는 묶음이 한 번에 커밋하므로
        **연결이 같은 트랜잭션에서 끝나** 비어 있는 일이 없다.
        """
        if not qid or not request_id:
            return
        try:
            self.conn.execute("UPDATE query_log SET request_id=? WHERE id=?", (int(request_id), int(qid)))
            if commit:
                self.conn.commit()
        except sqlite3.OperationalError as e:
            self._warn_locked("질의 로그 request_id", e)

    def set_feedback(self, qid: int, feedback: int, note: str = "") -> None:
        """질의에 피드백을 단다. `qid` 는 **requests.id**(신규) 또는 예전 query_log.id 둘 다 받는다.

        피드백은 `query_feedback` 에 **영구 보존**한다 — requests 는 keep_requests(2000행 ≈ 8일)로 잘리는데
        사람이 남긴 유일한 학습 신호가 같이 잘리면 안 된다. 목록에서 바로 보이도록 requests 에도 함께 적는다.
        """
        qid = int(qid or 0)
        if not qid:
            return
        row = self.conn.execute("SELECT id, ts, summary, user FROM requests WHERE id=?", (qid,)).fetchone()
        legacy = None
        if row is None:      # 예전 화면이 준 query_log.id 일 수 있다 (하위 호환)
            legacy = self.conn.execute("SELECT id, ts, query, user, request_id FROM query_log WHERE id=?", (qid,)).fetchone()
        rid = int((row and row["id"]) or (legacy and legacy["request_id"]) or 0) or (-qid if legacy else qid)
        ts = float((row and row["ts"]) or (legacy and legacy["ts"]) or time.time())
        text = str((row and row["summary"]) or (legacy and legacy["query"]) or "")
        who = str((row and row["user"]) or (legacy and legacy["user"]) or "")
        self.conn.execute(
            "INSERT INTO query_feedback(request_id, ts, query, user, feedback, note, legacy_qid) VALUES(?,?,?,?,?,?,?) "
            "ON CONFLICT(request_id) DO UPDATE SET feedback=excluded.feedback, note=excluded.note, ts=excluded.ts",
            (rid, ts, text[:300], who, feedback, note or "", qid if legacy else 0))
        if row is not None:
            self.conn.execute("UPDATE requests SET feedback=?, note=? WHERE id=?", (feedback, note or "", qid))
        if legacy is not None:
            self.conn.execute("UPDATE query_log SET feedback=?, note=? WHERE id=?", (feedback, note or "", qid))
        self.conn.commit()

    #: `queries()` 가 돌려주는 모양 — 예전 query_log 행과 **같은 키**를 쓴다. 읽는 곳이 14군데라
    #: 모양을 바꾸면 전부 손봐야 하므로, 원천만 requests 로 바꾸고 모양은 유지한다.
    def _query_row(self, r: sqlite3.Row) -> Dict[str, Any]:
        try:
            res = json.loads(r["result"]) if r["result"] else {}
        except Exception:
            res = {}
        ev = res.get("evidence") or {}
        return {
            "id": int(r["id"]), "request_id": int(r["id"]), "ts": r["ts"],
            "query": res.get("query") or r["summary"], "answer": res.get("answer") or "",
            "config": r["config"], "ms": r["ms"],
            "top_chunks": json.dumps([h.get("chunk_id") for h in (res.get("hits_brief") or [])], ensure_ascii=False),
            "scores": json.dumps({"n_hits": len(res.get("hits_brief") or []), "verdict": ev.get("verdict"),
                                  "groundedness": res.get("groundedness")}, ensure_ascii=False),
            "feedback": r["feedback"], "note": r["note"] or "",
            "user": r["user"] or "", "role": r["role"] or "", "origin": r["origin"] or "",
            "via": r["via"] or "", "ip": r["ip"] or "", "agent": r["agent"] or "",
        }

    def queries(self, limit: int = 50, user: Optional[str] = None, q: Optional[str] = None,
                origin: Optional[str] = None) -> List[Dict[str, Any]]:
        """'누가 무엇을 물었나'. 2026-09-23부터 **requests(kind='query')** 가 원천이다.

        예전에는 query_log 를 읽었는데, 두 표가 질문·답변·근거·판정·trace 를 **내용까지 똑같이** 들고 있었다
        (trace 는 바이트까지 동일). 원천을 하나로 모으고 모양은 그대로 둔다 — 부르는 곳이 14군데라서다.
        피드백은 잘려 나가지 않는 `query_feedback` 을 우선한다.
        """
        sql = ("SELECT r.id, r.ts, r.summary, r.result, r.config, r.ms, r.user, r.role, r.origin, r.via, r.ip, r.agent, "
               "COALESCE(f.feedback, r.feedback) AS feedback, COALESCE(NULLIF(f.note,''), r.note, '') AS note "
               "FROM requests r LEFT JOIN query_feedback f ON f.request_id = r.id WHERE r.kind='query'")
        args: List[Any] = []
        if user:
            sql += " AND r.user=?"
            args.append(user)
        if origin:
            # requests.origin 은 'web kh82.kim' 처럼 사용자까지 붙은 옛 형식이 섞여 있다 → 앞부분으로 비교
            sql += " AND (r.origin=? OR r.origin LIKE ?)"
            args += [origin, origin + " %"]
        if q:
            sql += " AND r.summary LIKE ?"
            args.append("%" + q + "%")
        sql += " ORDER BY r.id DESC LIMIT ?"
        args.append(int(limit))
        out = [self._query_row(r) for r in self.conn.execute(sql, args).fetchall()]
        # 전환기: requests 에 짝이 없는 **옛 query_log 행**도 함께 보여 준다.
        # (1) 이미 쌓인 이력(실측 826행, 그중 742행은 연결 컬럼이 생기기 전의 것)이 화면에서 사라지면 안 되고
        # (2) query_log 에만 넣는 옛 코드·테스트가 남아 있을 수 있다.
        # requests 에 있는 것은 위에서 이미 나왔으므로 request_id 로 중복을 걸러 낸다.
        if len(out) < int(limit):
            seen = {r["id"] for r in out}
            lsql = ("SELECT id,ts,query,config,top_chunks,answer,scores,feedback,note,"
                    "user,role,origin,via,ip,agent,request_id FROM query_log "
                    "WHERE (request_id IS NULL OR request_id=0 OR request_id NOT IN (SELECT id FROM requests))")
            largs: List[Any] = []
            if user:
                lsql += " AND user=?"
                largs.append(user)
            if origin:
                lsql += " AND origin=?"
                largs.append(origin)
            if q:
                lsql += " AND query LIKE ?"
                largs.append("%" + q + "%")
            lsql += " ORDER BY id DESC LIMIT ?"
            largs.append(int(limit) - len(out))
            for r in self.conn.execute(lsql, largs).fetchall():
                d = dict(r)
                if d["id"] not in seen:
                    d["legacy"] = True      # 화면이 '옛 기록' 으로 구분할 수 있게
                    out.append(d)
            out.sort(key=lambda d: float(d.get("ts") or 0), reverse=True)
        return out[:int(limit)]

    def query_users(self, limit: int = 50) -> List[Dict[str, Any]]:
        """사용자별 질의 집계 (누가 얼마나 물었고 최근은 언제인가). 원천은 requests(kind='query')."""
        rows = self.conn.execute(
            "SELECT COALESCE(NULLIF(r.user,''),'(기록 없음)') AS user, COUNT(*) AS n, MAX(r.ts) AS last_ts, "
            "SUM(CASE WHEN COALESCE(f.feedback, r.feedback) > 0 THEN 1 ELSE 0 END) AS up, "
            "SUM(CASE WHEN COALESCE(f.feedback, r.feedback) < 0 THEN 1 ELSE 0 END) AS down "
            "FROM requests r LEFT JOIN query_feedback f ON f.request_id = r.id "
            "WHERE r.kind='query' GROUP BY 1 ORDER BY n DESC LIMIT ?", (int(limit),)).fetchall()
        agg = {r["user"]: dict(r) for r in rows}
        # 전환기: requests 에 짝이 없는 옛 query_log 행도 합산한다 (§queries 와 같은 이유)
        for r in self.conn.execute(
                "SELECT COALESCE(NULLIF(user,''),'(기록 없음)') AS user, COUNT(*) AS n, MAX(ts) AS last_ts, "
                "SUM(CASE WHEN feedback > 0 THEN 1 ELSE 0 END) AS up, "
                "SUM(CASE WHEN feedback < 0 THEN 1 ELSE 0 END) AS down FROM query_log "
                "WHERE (request_id IS NULL OR request_id=0 OR request_id NOT IN (SELECT id FROM requests)) "
                "GROUP BY 1").fetchall():
            cur = agg.get(r["user"])
            if cur is None:
                agg[r["user"]] = dict(r)
            else:
                cur["n"] += r["n"]
                cur["up"] += r["up"]
                cur["down"] += r["down"]
                cur["last_ts"] = max(float(cur["last_ts"] or 0), float(r["last_ts"] or 0))
        return sorted(agg.values(), key=lambda d: -int(d["n"]))[:int(limit)]

    def get_query(self, qid: int) -> Optional[Dict[str, Any]]:
        """질의 한 건 (`queries()` 와 같은 모양 + `trace`). `qid` 는 **requests.id**.

        2026-09-23: 원천이 query_log → requests 로 바뀌었다. 예전 화면·스크립트가 주는 query_log.id 도
        받아 준다(하위 호환) — 그 행의 `request_id` 로 넘어가고, 그것도 없으면 옛 행을 그대로 돌려준다.
        """
        qid = int(qid or 0)
        r = self.conn.execute(
            "SELECT r.id, r.ts, r.summary, r.result, r.config, r.ms, r.trace, r.user, r.role, r.origin, r.via, r.ip, r.agent, "
            "COALESCE(f.feedback, r.feedback) AS feedback, COALESCE(NULLIF(f.note,''), r.note, '') AS note "
            "FROM requests r LEFT JOIN query_feedback f ON f.request_id = r.id WHERE r.id=?", (qid,)).fetchone()
        if r is not None:
            d = self._query_row(r)
            d["trace"] = r["trace"]
            return d
        # 하위 호환: 예전 query_log.id
        old = self.conn.execute("SELECT * FROM query_log WHERE id=?", (qid,)).fetchone()
        if not old:
            return None
        d = dict(old)
        if d.get("request_id"):
            row = self.conn.execute("SELECT trace FROM requests WHERE id=?", (int(d["request_id"]),)).fetchone()
            if row and row["trace"] and not d.get("trace"):
                d["trace"] = row["trace"]
                d["trace_from"] = "requests"
        return d

    def trim_query_log(self, dry_run: bool = False) -> Dict[str, Any]:
        """query_log 에 남아 있는 옛 `trace` 를 지운다 (`maintenance trim_query_log`).

        같은 내용이 requests 에 있으므로 정보 손실이 없다. **requests 에 짝이 없는 행은 건드리지 않는다** —
        그런 행은 그 trace 가 유일한 사본이기 때문이다(keep_requests 로 잘려 나간 오래된 질의).
        """
        n_all = self.conn.execute("SELECT COUNT(*) FROM query_log WHERE trace IS NOT NULL").fetchone()[0]
        row = self.conn.execute(
            "SELECT COUNT(*), COALESCE(SUM(LENGTH(trace)),0) FROM query_log ql WHERE ql.trace IS NOT NULL "
            "AND EXISTS (SELECT 1 FROM requests r WHERE r.id = ql.request_id AND r.trace IS NOT NULL)").fetchone()
        n, nbytes = int(row[0]), int(row[1])
        out = {"with_trace": n_all, "safe_to_clear": n, "bytes": nbytes, "mb": round(nbytes / 1048576.0, 1),
               "kept_unique": n_all - n, "dry_run": bool(dry_run)}
        if dry_run or not n:
            return out
        self.conn.execute(
            "UPDATE query_log SET trace = NULL WHERE trace IS NOT NULL "
            "AND EXISTS (SELECT 1 FROM requests r WHERE r.id = query_log.request_id AND r.trace IS NOT NULL)")
        self.conn.commit()
        out["cleared"] = n
        return out

    def add_proposal(self, kind: str, payload: Dict[str, Any], reason: str, confidence: float, origin: str) -> int:
        # 동일 payload 중복 방지
        pj = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        r = self.conn.execute("SELECT id FROM proposals WHERE kind=? AND payload=? AND status IN ('proposed','applied')", (kind, pj)).fetchone()
        if r:
            return int(r["id"])
        cur = self.conn.execute("INSERT INTO proposals(ts,kind,payload,reason,confidence,status,origin) VALUES(?,?,?,?,?,?,?)",
                                (time.time(), kind, pj, reason, confidence, "proposed", origin))
        self.conn.commit()
        return int(cur.lastrowid)

    def proposals(self, status: Optional[str] = None, limit: int = 200) -> List[Dict[str, Any]]:
        if status:
            rows = self.conn.execute("SELECT * FROM proposals WHERE status=? ORDER BY id DESC LIMIT ?", (status, limit)).fetchall()
        else:
            rows = self.conn.execute("SELECT * FROM proposals ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["payload"] = json.loads(d["payload"] or "{}")
            out.append(d)
        return out

    def get_proposal(self, pid: int) -> Optional[Dict[str, Any]]:
        r = self.conn.execute("SELECT * FROM proposals WHERE id=?", (pid,)).fetchone()
        if not r:
            return None
        d = dict(r)
        d["payload"] = json.loads(d["payload"] or "{}")
        return d

    def set_proposal_status(self, pid: int, status: str, eval_before: Any = None, eval_after: Any = None) -> None:
        self.conn.execute("UPDATE proposals SET status=?, applied_at=?, eval_before=COALESCE(?, eval_before), eval_after=COALESCE(?, eval_after) WHERE id=?",
                          (status, time.time() if status == "applied" else None,
                           json.dumps(eval_before, ensure_ascii=False) if eval_before is not None else None,
                           json.dumps(eval_after, ensure_ascii=False) if eval_after is not None else None, pid))
        self.conn.commit()

    def log_evolution(self, proposal_id: int, action: str, detail: Dict[str, Any], checksum: str) -> None:
        self.conn.execute("INSERT INTO evolution_log(ts,proposal_id,action,detail,checksum) VALUES(?,?,?,?,?)",
                          (time.time(), proposal_id, action, json.dumps(detail, ensure_ascii=False), checksum))
        self.conn.commit()

    def evolution_log(self, limit: int = 100) -> List[Dict[str, Any]]:
        return [dict(r) for r in self.conn.execute("SELECT * FROM evolution_log ORDER BY id DESC LIMIT ?", (limit,))]
