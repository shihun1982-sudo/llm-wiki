# -*- coding: utf-8 -*-
"""실제 기술 문서 코퍼스 내려받기 — IETF RFC (직렬 회선 · 모뎀 · PPP · 링크 계층 중심).

왜 RFC 인가: 공개 문서이고, 평문이며, 이 프로젝트의 대상 도메인(모뎀 HW 제어 임베디드 SW)과 주제가 가깝고,
"Obsoletes / Updates" 헤더가 문서 사이의 **결정적 관계**를 만들어 주어 GraphRAG 검증에 쓰기 좋다.

사용:
    python tools/fetch_rfc_corpus.py --out corpus/rfc --max-mb 8
    python tools/fetch_rfc_corpus.py --out corpus/rfc --range 1661-1700 --max-mb 4
    python tools/fetch_rfc_corpus.py --out corpus/rfc --list      (내려받지 않고 대상만 표시)

내려받은 문서는 docs/CORPUS_CONTRACT.md 의 front matter 를 붙여 `<out>/SWD-RFC-<번호>.md` 로 저장한다
(doc_type=sw_design, id=SWD-RFC-nnnn, related.docs = Obsoletes/Updates 대상). 이미 있으면 건너뛴다.
이후: config.json 의 corpus_dirs 에 out 폴더를 넣고 `python -m llmwiki build --full`.
"""
from __future__ import annotations

import argparse
import os
import re
import sys
import time
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BASE = "https://www.rfc-editor.org/rfc/rfc%d.txt"
UA = "llmwiki-corpus-fetcher/1.0 (+local RAG evaluation)"

# 직렬 회선·모뎀·PPP·링크 계층 + 기본 인터넷 프로토콜 (주제가 이어져 그래프 관계가 생기도록 고름)
CURATED = [
    # 직렬 회선 / 압축 / SLIP
    1055, 1144, 2508, 3544, 2509, 1553,
    # PPP 코어와 확장
    1134, 1171, 1172, 1331, 1332, 1333, 1334, 1376, 1377, 1378, 1547, 1548, 1549, 1552, 1570,
    1618, 1619, 1638, 1661, 1662, 1663, 1762, 1763, 1764, 1841, 1962, 1963, 1968, 1969, 1973,
    1974, 1978, 1979, 1989, 1990, 1993, 1994, 2043, 2097, 2118, 2125, 2153, 2284, 2290, 2363,
    2364, 2420, 2433, 2472, 2484, 2516, 2615, 2686, 2687, 2688, 3241, 3518, 4638, 5072,
    # 모뎀 · 시리얼 포트 제어 · 전화망
    2217, 1079, 2807, 3054, 1663,
    # 링크 계층 / 이더넷 / 프레이밍
    826, 894, 895, 1042, 1055, 2464, 3232,
    # 인터넷 코어 (문맥 문서)
    768, 791, 792, 793, 1122, 1123, 1812, 2131, 2132, 1918, 3927, 4291, 8200,
    # 용어 / 규칙 (코딩 규칙 성격)
    2119, 8174, 7322, 7841,
    # 흐름 제어 · 혼잡 · 오류
    1191, 2018, 2582, 5681, 6298, 3168,
]

AREA_HINTS = [
    (r"\bPPP\b|Point-to-Point", "ppp"),
    (r"\bSLIP\b|Serial Line", "serial-line"),
    (r"modem|Com Port|telephone|dial", "modem"),
    (r"compress", "compression"),
    (r"Ethernet|ARP|link layer|Frame Relay|ATM|ISDN", "link-layer"),
    (r"TCP|congestion|retransmission|RTO", "tcp"),
    (r"IPv6|IPv4|Internet Protocol|ICMP|UDP|routing|Router", "ip"),
    (r"authentic|CHAP|PAP|EAP|encrypt|ECP", "security"),
    (r"MIB|management", "management"),
    (r"terminology|key words|requirement levels|style", "conventions"),
]


def fetch(n: int, timeout: float, retries: int = 2) -> str:
    last = None
    for i in range(retries + 1):
        try:
            req = urllib.request.Request(BASE % n, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            if e.code == 404:
                raise
            last = e
        except Exception as e:      # 네트워크 일시 오류
            last = e
        time.sleep(1.0 * (i + 1))
    raise last if last else RuntimeError("unknown")


def parse_header(text: str, n: int):
    """RFC 머리말에서 제목 · 날짜 · 저자 · Obsoletes/Updates · Category 를 뽑는다."""
    head = text[:4000]
    title = ""
    # 제목은 보통 머리말 블록 다음 줄들의 가운데 정렬된 대문자 줄
    lines = [ln.rstrip() for ln in head.splitlines()]
    for i, ln in enumerate(lines[:60]):
        s = ln.strip()
        if not s or ":" in s[:22]:
            continue
        if i > 2 and len(s) > 8 and s == s.upper() and re.search(r"[A-Z]{3}", s):
            title = s
            nxt = lines[i + 1].strip() if i + 1 < len(lines) else ""
            if nxt and nxt == nxt.upper() and len(nxt) > 8 and ":" not in nxt:
                title += " " + nxt
            break
    if not title:
        m = re.search(r"^\s{10,}(\S.{10,70})$", head, re.M)
        title = (m.group(1).strip() if m else "RFC %d" % n)
    title = re.sub(r"\s+", " ", title).strip()[:160]
    date = ""
    m = re.search(r"\b(January|February|March|April|May|June|July|August|September|October|November|December)\s+l?(\d{4})\b", head)
    if m:
        mon = ["january", "february", "march", "april", "may", "june", "july", "august", "september", "october", "november", "december"].index(m.group(1).lower()) + 1
        date = "%s-%02d-01" % (m.group(2), mon)
    author = ""
    m = re.search(r"^\s{30,}([A-Z]\.\s*[A-Za-z\-']+.*)$", head, re.M)
    if m:
        author = re.sub(r"\s+", " ", m.group(1)).strip()[:80]
    rel = {"obsoletes": [], "updates": [], "obsoleted_by": [], "updated_by": []}
    for key, pat in (("obsoletes", r"Obsoletes:\s*([^\n]+)"), ("updates", r"Updates:\s*([^\n]+)"),
                     ("obsoleted_by", r"Obsoleted by:\s*([^\n]+)"), ("updated_by", r"Updated by:\s*([^\n]+)")):
        m = re.search(pat, head)
        if m:
            rel[key] = ["SWD-RFC-%s" % x for x in re.findall(r"\d{3,5}", m.group(1))]
    cat = ""
    m = re.search(r"Category:\s*([A-Za-z ]+)", head)
    if m:
        cat = m.group(1).strip().lower()
    status = "deprecated" if rel["obsoleted_by"] else ("approved" if "standard" in cat or "best current" in cat else "draft")
    return title, date, author, rel, cat, status


def modules_for(title: str, body: str):
    hay = title + " " + body[:3000]
    mods = [m for pat, m in AREA_HINTS if re.search(pat, hay, re.I)]
    return sorted(set(mods)) or ["general"]


def to_document(n: int, text: str) -> str:
    title, date, author, rel, cat, status = parse_header(text, n)
    mods = modules_for(title, text)
    tags = sorted(set(["rfc"] + mods + ([cat.replace(" ", "-")] if cat else [])))
    related = {k: v for k, v in rel.items() if v}
    fm = ["---", "schema_version: 1", "doc_type: sw_design", "id: SWD-RFC-%d" % n,
          "title: %s" % _y(title or ("RFC %d" % n)), "date: %s" % (date or "1990-01-01")]
    if author:
        fm.append("author: %s" % _y(author))
    fm.append("status: %s" % status)
    fm.append("tags: [%s]" % ", ".join(tags))
    fm.append("module: [%s]" % ", ".join(mods))
    if related:
        fm.append("related:")
        for k, v in related.items():
            fm.append("  %s: [%s]" % (k, ", ".join(v)))
    fm.append("source: https://www.rfc-editor.org/rfc/rfc%d.txt" % n)
    fm.append("---")
    body = text.replace("\f", "\n")
    body = re.sub(r"\n{4,}", "\n\n\n", body)
    return "\n".join(fm) + "\n\n# RFC %d — %s\n\n## 개요\n\n%s\n" % (n, title or "", body)


def _y(s: str) -> str:
    s = s.replace('"', "'")
    return '"%s"' % s if re.search(r"[:#\[\]{}]", s) else s


def main() -> int:
    ap = argparse.ArgumentParser(description="IETF RFC 코퍼스 내려받기 (문서 계약 형식으로 변환)")
    ap.add_argument("--out", default=os.path.join("corpus", "rfc"), help="저장 폴더 (기본 corpus/rfc)")
    ap.add_argument("--max-mb", type=float, default=8.0, help="전체 크기 상한 MB (기본 8)")
    ap.add_argument("--range", dest="rng", default=None, help="RFC 번호 범위 (예 1661-1700). 없으면 내장 목록")
    ap.add_argument("--timeout", type=float, default=30.0)
    ap.add_argument("--delay", type=float, default=0.25, help="요청 사이 대기(초) — 서버 예의")
    ap.add_argument("--list", action="store_true", help="내려받지 않고 대상 목록만 출력")
    ns = ap.parse_args()
    try:
        from llmwiki import console as _c
        _c.setup()
    except Exception:
        pass

    if ns.rng:
        a, _, b = ns.rng.partition("-")
        nums = list(range(int(a), int(b or a) + 1))
    else:
        nums = list(dict.fromkeys(CURATED))
    out = ns.out if os.path.isabs(ns.out) else os.path.join(ROOT, ns.out)
    if ns.list:
        print("대상 %d개: %s" % (len(nums), ", ".join(str(x) for x in nums)))
        print("저장 위치:", out)
        return 0
    os.makedirs(out, exist_ok=True)
    budget = ns.max_mb * 1024 * 1024
    total = sum(os.path.getsize(os.path.join(out, f)) for f in os.listdir(out) if f.endswith(".md"))
    got = skipped = failed = 0
    t0 = time.time()
    for n in nums:
        path = os.path.join(out, "SWD-RFC-%d.md" % n)
        if os.path.exists(path):
            skipped += 1
            continue
        if total >= budget:
            print("크기 상한 %.1f MB 도달 — 중단" % ns.max_mb)
            break
        try:
            text = fetch(n, ns.timeout)
        except urllib.error.HTTPError as e:
            failed += 1
            print("  RFC %-5d 실패 HTTP %s" % (n, e.code))
            continue
        except Exception as e:
            failed += 1
            print("  RFC %-5d 실패 %s" % (n, str(e)[:80]))
            continue
        doc = to_document(n, text)
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write(doc)
        sz = os.path.getsize(path)
        total += sz
        got += 1
        print("  RFC %-5d %6.1f KB  누적 %5.2f MB" % (n, sz / 1024.0, total / 1e6))
        time.sleep(ns.delay)
    print("\n완료: 새로 %d · 건너뜀 %d · 실패 %d · 총 %.2f MB · %.0f초" % (got, skipped, failed, total / 1e6, time.time() - t0))
    print("폴더: %s" % out)
    print("다음: config.json 의 corpus_dirs 에 이 폴더를 넣고  python -m llmwiki build --full --yes")
    return 0


if __name__ == "__main__":
    sys.exit(main())
