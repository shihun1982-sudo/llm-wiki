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
  answer TEXT, scores TEXT, trace TEXT, feedback INTEGER, note TEXT
);
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
]


class Store:
    """SQLite 저장소.

    병렬 처리(2026-09-15): 연결은 스레드마다 따로 쓴다.
    - `conn` 은 현재 스레드가 session() 으로 빌린 연결(없으면 생성 스레드의 기본 연결)을 돌려준다. 같은 연결을 두 스레드가 나눠 쓰면
      한 스레드의 commit 이 다른 스레드의 반쯤 쓴 트랜잭션을 확정하는 사고가 나므로, 서버는 모든 요청·잡·워처를 `with store.session():` 로 감싼다.
    - WAL 모드라 여러 읽기 연결이 쓰기 연결과 동시에 동작하고, 쓰기끼리는 SQLite 의 busy_timeout 만큼 기다린다 (config db_busy_timeout_s).
    - 벡터 행렬·엔티티 인덱스·doc_meta 캐시는 인스턴스 하나에 공유되며(읽기 전용 numpy), 적재는 락으로 한 번만 한다.
    """

    def __init__(self, path: str, busy_timeout_s: float = 30.0, pool_size: int = 16):
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        self.path = path
        self.busy_timeout_s = float(busy_timeout_s or 30.0)
        self.pool_size = max(1, int(pool_size or 16))
        self._pool: List[sqlite3.Connection] = []
        self._pool_lock = threading.Lock()
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
        with self._pool_lock:
            c = self._pool.pop() if self._pool else None
            gen = self.generation
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
                    c.rollback()
            except Exception:
                pass
            with self._pool_lock:
                if not self.closed and self.generation == gen and len(self._pool) < self.pool_size:
                    self._pool.append(c)
                    c = None
            if c is not None:
                try:
                    c.close()
                except Exception:
                    pass

    def pool_info(self) -> Dict[str, Any]:
        with self._pool_lock:
            return {"idle": len(self._pool), "max": self.pool_size, "busy_timeout_s": self.busy_timeout_s}

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
            "period_from,period_to,summary,schema_version,inferred,lint_errors,lint_warnings,lint,extra) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (nm["doc_id"], nm.get("doc_type", ""), nm.get("ext_id", ""), nm.get("date", ""), float(nm.get("ts") or 0), nm.get("date_source", ""),
             nm.get("author", ""), nm.get("status", ""), json.dumps(nm.get("tags", []), ensure_ascii=False), json.dumps(nm.get("modules", []), ensure_ascii=False),
             nm.get("hw_chip", ""), nm.get("hw_rev", ""), json.dumps(nm.get("related", {}), ensure_ascii=False), nm.get("period_from", ""), nm.get("period_to", ""),
             nm.get("summary", ""), int(nm.get("schema_version") or 0), 1 if nm.get("inferred") else 0,
             sum(1 for x in lint if x["level"] == "error"), sum(1 for x in lint if x["level"] == "warn"),
             json.dumps(lint, ensure_ascii=False), json.dumps(nm.get("extra", {}), ensure_ascii=False, default=str)))

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
                                 "hw_rev": r["hw_rev"], "inferred": r["inferred"]}
                   for r in self.conn.execute("SELECT doc_id, doc_type, ext_id, date, ts, status, hw_rev, inferred FROM doc_meta")}
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
    def cache_get(self, provider: str, model: str, shas: List[str]) -> Dict[str, np.ndarray]:
        out: Dict[str, np.ndarray] = {}
        for i in range(0, len(shas), 500):
            part = shas[i:i + 500]
            qs = ",".join("?" * len(part))
            for r in self.conn.execute("SELECT sha, dim, vec FROM embedding_cache WHERE provider=? AND model=? AND sha IN (%s)" % qs, [provider, model] + part):
                out[r["sha"]] = self._decode_vec(r["vec"], int(r["dim"])).astype(np.float32)
        return out

    def cache_put(self, provider: str, model: str, items: List[Tuple[str, np.ndarray]], dtype: str = "float32") -> None:
        np_dt = np.float16 if dtype == "float16" else np.float32
        now = time.time()
        for sha, v in items:
            v = np.asarray(v, dtype=np_dt)
            self.conn.execute("INSERT OR REPLACE INTO embedding_cache(provider,model,sha,dim,vec,ts) VALUES(?,?,?,?,?,?)",
                              (provider, model, sha, int(v.shape[0]), v.tobytes(), now))

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
                    archive_dir: str = "") -> int:
        """요청 프로파일 기록. **관측용이므로 실패해도 명령/질의를 죽이지 않는다** — 다른 프로세스가 빌드 중이라
        쓰기 잠금을 못 얻으면 경고만 남기고 0 을 돌려준다 (2026-09-15: 서버 워처와 CLI 질의가 겹쳐 'database is locked' 로 질의가 실패했다)."""
        try:
            return self._log_request(kind, summary, trace, result, config, error, origin, keep, user, archive_dir)
        except (sqlite3.OperationalError, UnicodeEncodeError) as e:
            # UnicodeEncodeError: 경계에서 걸러지지만, 어떤 경로로든 SQLite 가 담지 못하는 문자열이
            # 들어와도 관측 기록 때문에 질의가 죽지는 않게 한다.
            self._warn_locked("요청 프로파일", e)
            return 0

    def _log_request(self, kind: str, summary: str, trace: Dict[str, Any], result: Any = None, config: Any = None,
                     error: Optional[str] = None, origin: str = "", keep: int = 2000, user: str = "",
                     archive_dir: str = "") -> int:
        sm = (trace or {}).get("summary") or {}
        llm = sm.get("llm") or {}
        if not origin or not user:
            try:
                from . import progress as _pg
                cl = (_pg.get(_pg.current_token() or "") or {}).get("client") or {}
                origin = origin or " ".join(x for x in (cl.get("origin"), cl.get("user")) if x)[:80]
                user = user or str(cl.get("user") or "")[:80]
            except Exception:
                pass
        cur = self.conn.execute(
            "INSERT INTO requests(ts,kind,summary,ms,llm_calls,input_tokens,output_tokens,sql_count,debug_level,config,result,trace,error,origin,run_id,user) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (time.time(), kind, summary[:300], float((trace or {}).get("ms", 0) or 0), int(llm.get("calls", 0) or 0),
             int(llm.get("input_tokens", 0) or 0), int(llm.get("output_tokens", 0) or 0), int(sm.get("sql_statements", 0) or 0),
             int((trace or {}).get("debug_level", 0) or 0), json.dumps(config, ensure_ascii=False) if config is not None else None,
             json.dumps(result, ensure_ascii=False) if result is not None else None,
             json.dumps(trace, ensure_ascii=False) if trace is not None else None, error, origin,
             str((trace or {}).get("run_id") or ""), user))
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
    def log_query(self, query: str, config: Dict[str, Any], top_chunks: List[str], answer: str,
                  scores: Dict[str, Any], trace: Dict[str, Any]) -> int:
        """질의 로그(자가진화 입력). 쓰기 잠금이면 건너뛴다 — 답변은 이미 만들어졌으므로 기록 실패로 질의를 실패시키지 않는다."""
        try:
            cur = self.conn.execute("INSERT INTO query_log(ts,query,config,top_chunks,answer,scores,trace,feedback,note) VALUES(?,?,?,?,?,?,?,?,?)",
                                    (time.time(), query, json.dumps(config, ensure_ascii=False), json.dumps(top_chunks), answer,
                                     json.dumps(scores, ensure_ascii=False), json.dumps(trace, ensure_ascii=False), None, ""))
            self.conn.commit()
            return int(cur.lastrowid)
        except (sqlite3.OperationalError, UnicodeEncodeError) as e:
            self._warn_locked("질의 로그", e)
            return 0

    def set_feedback(self, qid: int, feedback: int, note: str = "") -> None:
        self.conn.execute("UPDATE query_log SET feedback=?, note=? WHERE id=?", (feedback, note, qid))
        self.conn.commit()

    def queries(self, limit: int = 50) -> List[Dict[str, Any]]:
        rows = self.conn.execute("SELECT id,ts,query,config,top_chunks,answer,scores,feedback,note FROM query_log ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]

    def get_query(self, qid: int) -> Optional[Dict[str, Any]]:
        r = self.conn.execute("SELECT * FROM query_log WHERE id=?", (qid,)).fetchone()
        return dict(r) if r else None

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
