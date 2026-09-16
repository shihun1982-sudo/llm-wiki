"""전체 리빌드의 색인 단계가 청크 수에 비례하는지(제곱이 아닌지) 측정한다.

왜 있나: chunks_fts · chunks_tri 는 chunk_id 가 UNINDEXED 라, 청크마다
`DELETE ... WHERE chunk_id=?` 를 하면 매번 FTS 전체를 훑는다. 청크 N개면 N번 훑으므로
빌드 시간이 N² 로 늘어난다. `fts_trigram` 을 켜면 trigram 색인이 본문의 2~3배라 더 심해진다.
2026-09-15 에 (1) 전체 리빌드는 통째로 비우기 (2) 증분은 문서 단위 삭제 로 바꿨고,
이 스크립트가 그 효과를 다시 잴 수 있게 남겨 둔 것이다.

실행:
    python tools/verify/bench_fts.py              # 기본 3단계
    python tools/verify/bench_fts.py --chunks 24000 --per-doc 48

판정: '수정 후' 열이 청크 수에 **비례**해야 한다. 청크를 2배로 했을 때 시간이 4배가 되면
어딘가에서 다시 청크별 삭제가 들어간 것이다.
"""
from __future__ import annotations

import argparse
import os
import random
import shutil
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from llmwiki.store import Store   # noqa: E402

WORDS = ["PDCCH", "디코더", "실패", "DMA", "underrun", "PHY", "재시작", "링크", "계층", "협상",
         "프레임", "버퍼", "인터럽트", "타이밍", "레지스터", "초기화", "모뎀", "채널", "추정", "보정"]


class _Doc:
    def __init__(self, i):
        self.doc_id, self.path, self.title = "d%05d.md" % i, "d%05d.md" % i, "문서 %d" % i
        self.kind, self.hash, self.meta, self.mtime, self.size = "md", "h%d" % i, {}, 0.0, 0


class _Chunk:
    def __init__(self, doc_id, i, rng):
        self.chunk_id, self.doc_id, self.ordinal = "%s#%d" % (doc_id, i), doc_id, i
        self.heading, self.start, self.end = "섹션 %d" % i, 0, 900
        self.text = " ".join(rng.choice(WORDS) for _ in range(150))


def _ident(s):
    return s


def run(n_docs: int, per_doc: int, trigram: bool, old_way: bool) -> float:
    rng = random.Random(11)
    d = tempfile.mkdtemp(prefix="llmwiki-bench-")
    try:
        st = Store(os.path.join(d, "wiki.db"))
        docs = [(_Doc(i), [_Chunk("d%05d.md" % i, j, rng) for j in range(per_doc)]) for i in range(n_docs)]
        for doc, chunks in docs:
            st.upsert_doc(doc, chunks, _ident, trigram=trigram, fts_cleared=True)
        st.conn.commit()

        t0 = time.time()
        if old_way:                       # 2026-09-15 이전 방식: 청크마다 삭제 (전체 스캔)
            c = st.conn
            for doc, chunks in docs:
                for ch in chunks:
                    c.execute("DELETE FROM chunks_fts WHERE chunk_id=?", (ch.chunk_id,))
                    if trigram:
                        c.execute("DELETE FROM chunks_tri WHERE chunk_id=?", (ch.chunk_id,))
                st.upsert_doc(doc, chunks, _ident, trigram=trigram, fts_cleared=True)
        else:                             # 지금 방식: 한 번 비우고 재삽입
            st.clear_fts(trigram=trigram)
            for doc, chunks in docs:
                st.delete_doc(doc.doc_id, fts_cleared=True)
                st.upsert_doc(doc, chunks, _ident, trigram=trigram, fts_cleared=True)
        st.conn.commit()
        el = time.time() - t0
        st.close()
        return el
    finally:
        shutil.rmtree(d, ignore_errors=True)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="전체 리빌드 색인 단계 속도 측정 (fts_trigram 포함)")
    ap.add_argument("--chunks", type=int, default=0, help="단일 측정: 총 청크 수 (생략하면 3단계 비교)")
    ap.add_argument("--per-doc", type=int, default=48, help="문서당 청크 수")
    ap.add_argument("--no-old", action="store_true", help="예전 방식은 재지 않는다 (빠름)")
    ns = ap.parse_args(argv)
    sizes = [ns.chunks] if ns.chunks else [2880, 5760, 11520]
    print("전체 리빌드의 chunk_index 단계 — SQLite %s" % __import__("sqlite3").sqlite_version)
    print("%-9s %-9s %10s %10s %8s" % ("청크", "trigram", "수정 전", "수정 후", "배수"))
    bad = 0
    prev = {}
    for total in sizes:
        n_docs = max(1, total // ns.per_doc)
        for tri in (False, True):
            a = 0.0 if ns.no_old else run(n_docs, ns.per_doc, tri, True)
            b = run(n_docs, ns.per_doc, tri, False)
            print("%-9d %-9s %8.1f s %8.1f s %7s" % (n_docs * ns.per_doc, "on" if tri else "off", a, b,
                                                     ("%.0f×" % (a / b)) if a and b else "-"))
            p = prev.get(tri)
            if p and p[1] > 0.2:
                grow = (b / p[1]) / (total / p[0])     # 청크 비례면 1 근처, 제곱이면 2 이상
                if grow > 1.8:
                    print("   ! 청크 %d→%d 에서 %.1f 배로 늘었다 — 청크별 삭제가 다시 들어갔는지 확인" % (p[0], total, b / p[1]))
                    bad += 1
            prev[tri] = (total, b)
    print("\nRESULT %s" % ("PROBLEMS" if bad else "OK"))
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
