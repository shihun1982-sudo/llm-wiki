# -*- coding: utf-8 -*-
"""답변 생성: LLM(인용 강제) 또는 추출식 폴백."""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

from .profiler import Profiler
from .providers import BaseLLM, LLMError
from .textutil import keywords, sentences, normalize_token, words
from . import tuning as _tuning
from . import ctxguard as _ctxguard

_LENGTH_HINT = {
    "short": "\n\n## 길이 지시\n핵심 답변과 근거 표만. 상세 설명은 2~3문장 이내.",
    "normal": "",
    "long": "\n\n## 길이 지시\n근거 문단에 있는 관련 사실을 빠짐없이 상세 설명에 풀어 쓰세요. 독자가 원문을 보지 않아도 이해할 수 있어야 합니다.",
}


def ANSWER_SYSTEM(length_target: str = "normal") -> str:   # prompts/answer_system.md + prompts/answer_guide.md
    from . import prompts as _prompts
    return _prompts.answer_system() + _LENGTH_HINT.get(length_target, "")


def ANSWER_SYSTEM_BEST_EFFORT(length_target: str = "normal") -> str:   # prompts/answer_best_effort.md (자체 뼈대 — answer_guide 를 붙이지 않는다)
    from . import prompts as _prompts
    return _prompts.get("answer_best_effort") + _LENGTH_HINT.get(length_target, "")


ANSWER_MODES = ("grounded", "best_effort")


def normalize_answer_mode(mode: Any) -> str:
    """Settings.answer_mode / 요청 오버라이드 값을 grounded | best_effort 로 정규화 (모르는 값은 grounded)."""
    m = str(mode or "grounded").strip().lower().replace("-", "_")
    return m if m in ANSWER_MODES else "grounded"


# 출력 모드 (docs/history/2026-09-18/IMPLEMENTATION_PLAN_0918_2.md §2.3): answer_mode 가 "어떻게 답할까" 라면 output_mode 는 "어디까지 만들고 무엇을 돌려줄까".
#   answer   = 끝까지 (기본)           fused    = 융합·부스트 뒤(리랭크 전) 후보를 그대로
#   reranked = 리랭크 뒤 후보를 그대로   context  = 컨텍스트([C#] 블록)까지 만들고 답변 LLM 은 부르지 않는다
OUTPUT_MODES = ("answer", "fused", "reranked", "context")
RESULT_CANDIDATES_FUSED = "candidates_fused"
RESULT_CANDIDATES_RERANKED = "candidates_reranked"
RESULT_CONTEXT = "context"
# output_mode → RoundConfig.stop_after (_retrieve 가 그 지점에서 멈춘다)
OUTPUT_STOP_AFTER = {"answer": None, "fused": "boost", "reranked": "rerank", "context": "context"}


def normalize_output_mode(mode: Any) -> str:
    """Settings.output_mode / 요청 오버라이드 값을 answer | fused | reranked | context 로 정규화 (모르는 값은 answer)."""
    m = str(mode or "answer").strip().lower().replace("-", "_")
    return m if m in OUTPUT_MODES else "answer"


def render_candidates_table(cands: List[Dict[str, Any]], title: str = "") -> str:
    """후보 목록(output_mode=fused|reranked 의 candidates[]) 을 마크다운 표로 — CLI 본문·Web 답변 칸·MCP text 가 같은 본문을 보이도록."""
    def cell(v: Any) -> str:
        return str("" if v is None else v).replace("|", "\\|").replace("\n", " ")
    lines = [title, ""] if title else []
    lines += ["| # | chunk_id | 문서 · ID | heading | fused | rerank | 채널(why) | boosts |", "|---:|---|---|---|---:|---:|---|---|"]
    for i, c in enumerate(cands):
        doc = c.get("ext_id") or c.get("doc_id") or ""
        if c.get("ext_id") and c.get("doc_id"):
            doc = "%s · %s" % (c["ext_id"], str(c["doc_id"]).rsplit("/", 1)[-1])
        b = c.get("boosts") or {}
        lines.append("| %d | %s | %s | %s | %.4f | %s | %s | %s |" % (
            i + 1, cell(c.get("chunk_id")), cell(doc), cell((c.get("heading") or "")[:60]), float(c.get("fused") or 0.0),
            "-" if c.get("rerank") is None else ("%.3f" % float(c["rerank"])), cell(" ".join(c.get("why") or [])),
            cell(" ".join("%s×%s" % (k, v) for k, v in b.items()))))
    if not cands:
        lines.append("| - | (후보 없음) | | | | | | |")
    return "\n".join(lines)


# ---------------------------------------------------------------- 반복 루프(LLM 고장) 탐지
_WS_RE = re.compile(r"\s+")


def find_repeat_loop(text: str, min_chars: int = 12, min_times: int = 4) -> Optional[Dict[str, Any]]:
    """같은 구절이 연속으로 되풀이되는 구간을 찾는다. 없으면 None.

    작은 모델이 긴 컨텍스트를 받으면 한 구절을 수십 번 반복하고 토큰 상한까지 가는 고장이 흔하다.
    (2026-09-16: llama3.1 이 'FIFO 임계값이 설정된 상태에서 RX DMA가 …' 를 수십 번 반복)
    스트리밍을 쓰지 않으므로 클라이언트 누적 오류는 아니고, 모델 출력 자체가 그렇다.

    되돌려주는 값: {"start": 반복이 시작된 위치, "period": 구절 길이, "times": 반복 횟수, "phrase": 구절}
    """
    if not text or min_times < 2:
        return None
    s = text.rstrip()
    n = len(s)
    if n < min_chars * min_times:
        return None
    # 1) 줄 단위 반복 (같은 줄이 계속 나오는 경우)
    lines = s.split("\n")
    i = 0
    while i < len(lines):
        cur = _WS_RE.sub(" ", lines[i]).strip()
        if len(cur) >= min_chars:
            j = i + 1
            while j < len(lines) and _WS_RE.sub(" ", lines[j]).strip() == cur:
                j += 1
            if j - i >= min_times:
                start = sum(len(x) + 1 for x in lines[:i + 1])     # 첫 줄은 남긴다
                return {"start": start, "period": len(cur), "times": j - i, "phrase": cur[:120], "kind": "line"}
            i = j
        else:
            i += 1
    # 2) 한 줄(또는 문단) 안에서 같은 구절이 반복되는 경우 — 끝부분의 주기를 찾는다
    tail = s[-8000:]
    m = len(tail)
    for p in range(min_chars, min(1200, m // min_times) + 1):
        seg = tail[m - p:]
        if seg != tail[m - 2 * p:m - p]:
            continue
        times = 2
        while (times + 1) * p <= m and tail[m - (times + 1) * p:m - times * p] == seg:
            times += 1
        if times >= min_times:
            # 앞쪽으로 더 거슬러 올라가 반복이 시작된 지점을 찾는다 (tail 밖까지)
            start_in_s = n - times * p
            while start_in_s - p >= 0 and s[start_in_s - p:start_in_s] == seg:
                start_in_s -= p
                times += 1
            return {"start": start_in_s + p,        # 한 번은 남기고 그 뒤를 자른다
                    "period": p, "times": times, "phrase": _WS_RE.sub(" ", seg).strip()[:120], "kind": "phrase"}
    return None


def guard_repeat(text: str, min_chars: int = 12, min_times: int = 4) -> Tuple[str, Optional[Dict[str, Any]]]:
    """반복 루프를 잘라내고 무슨 일이 있었는지 알리는 문구를 붙인다. (정리된 텍스트, 탐지정보)"""
    info = find_repeat_loop(text, min_chars, min_times)
    if not info:
        return text, None
    cut = text[:info["start"]].rstrip()
    note = ("\n\n> ⚠ **답변이 잘렸습니다** — 모델이 같은 구절을 %d번 되풀이하는 상태(반복 루프)에 빠져 그 지점부터 잘라냈습니다.\n"
            "> 되풀이된 구절: `%s…`\n"
            "> 작은 모델에서 컨텍스트가 길 때 생깁니다. 다시 질의하거나, 더 큰 answer 모델을 쓰거나, "
            "`token`/`speed` 프리셋으로 컨텍스트를 줄여 보세요." % (info["times"], info["phrase"][:80]))
    return (cut + note), info


def _trim_chunk(text: str, kws: List[str], max_chars: int) -> str:
    """질의 키워드를 포함한 문장을 우선 남기고(원래 순서 유지) max_chars 이내로 압축. 키워드 문장이 없으면 앞부분."""
    if len(text) <= max_chars:
        return text
    sents = sentences(text)
    scored = []
    for i, s in enumerate(sents):
        toks = set(normalize_token(w) for w in words(s))
        sc = sum(1 for k in kws if k in toks or k in s.lower())
        scored.append((sc, i, s))
    keep: List[int] = []
    total = 0
    for sc, i, s in sorted(scored, key=lambda x: (-x[0], x[1])):
        if sc == 0 and keep:
            break
        if total + len(s) > max_chars:
            continue
        keep.append(i)
        total += len(s)
    if not keep:
        return text[:max_chars]
    out = [sents[i] for i in sorted(keep)]
    return " … ".join(out)


def _tokset(text: str) -> set:
    return set(normalize_token(w) for w in words(text or "") if len(w) > 1)


def build_context(hits: List[Any], chunks: Dict[str, Any], graph: Optional[Dict[str, Any]], max_chars: int = 9000,
                  query: str = "", trim: bool = False, dedupe: bool = False, chunk_chars: int = 1200,
                  stage: Any = None, store: Any = None, neighbors: Optional[int] = None,
                  extra: Optional[List[Tuple[str, str, float]]] = None,
                  guard: bool = True, guard_mark: str = "‹") -> Dict[str, Any]:
    """컨텍스트 조립. tuning: context_neighbors(인접 청크), dedupe_similarity(토큰 Jaccard 중복), context_graph_relations.
    neighbors 를 주면 tuning context_neighbors 대신 사용 (fallback 라운드 확대용).
    extra: doc_expand 가 고른 [(chunk_id, parent_chunk_id, score)] — 부모 청크 바로 뒤에 kind=doc_expand 로 들어간다.
    guard: 문서 본문을 구획으로 감싸고 구조 흉내 조각의 표시를 바꾼다 (config `context_guard`, llmwiki/ctxguard.py)."""
    T = _tuning.T
    guard_on = bool(guard)
    guard_marks: List[str] = []
    parts: List[str] = []
    cites: List[Dict[str, Any]] = []
    used = 0
    kws = keywords(query) if trim else []
    seen_spans: List[Any] = []
    seen_toks: List[set] = []
    dropped: List[str] = []
    truncated: List[str] = []          # 상한에 걸려 **잘라서** 넣은 근거 (빠진 것과 구분해야 한다)
    trimmed = 0
    raw_chars = 0
    sim_thr = T.get("dedupe_similarity")
    min_fit = int(T.get("context_min_fit_chars"))
    # 인접 청크 확장 (상위 n개 청크의 앞/뒤) — 표/목록이 경계에서 잘린 경우 보완
    items: List[Any] = []
    n_nb = int(T.get("context_neighbors") if neighbors is None else neighbors)
    nb_added: List[str] = []
    have = {h.chunk_id for h in hits}
    extra_by_parent: Dict[str, List[Tuple[str, float]]] = {}
    for cid, parent, sc in (extra or []):
        extra_by_parent.setdefault(parent, []).append((cid, sc))
    ex_added: List[str] = []
    for idx, h in enumerate(hits):
        items.append((h.chunk_id, "hit", None))
        if store is not None and n_nb > 0 and idx < T.get("context_neighbor_top"):
            for nb in store.neighbor_chunks(h.chunk_id, n_nb):
                if nb["chunk_id"] not in have:
                    chunks[nb["chunk_id"]] = nb
                    have.add(nb["chunk_id"])
                    items.append((nb["chunk_id"], "neighbor", None))
                    nb_added.append(nb["chunk_id"])
        for cid, sc in extra_by_parent.get(h.chunk_id, []):
            if cid not in have and cid in chunks:
                have.add(cid)
                items.append((cid, "doc_expand", sc))
                ex_added.append(cid)
    for cid, kind, ex_score in items:
        c = chunks.get(cid)
        if not c:
            continue
        if dedupe:
            # (1) 같은 문서에서 오프셋이 겹치는(오버랩 청크) 후보는 앞선 것만 사용
            span = (c["doc_id"], int(c["start"] or 0), int(c["end"] or 0))
            dup = False
            for d_id, s0, e0 in seen_spans:
                if d_id == span[0] and min(e0, span[2]) - max(s0, span[1]) > 0.6 * max(1, span[2] - span[1]):
                    dup = True
                    break
            # (2) 다른 문서라도 토큰 집합이 거의 같은 문단(복붙 반복)은 제거
            tk = _tokset(c["text"]) if not dup else set()
            if not dup and tk:
                for prev in seen_toks:
                    inter = len(tk & prev)
                    if inter and inter / float(len(tk | prev)) >= sim_thr:
                        dup = True
                        break
            if dup:
                dropped.append(c["chunk_id"])
                continue
            seen_spans.append(span)
            seen_toks.append(tk)
        text = c["text"]
        raw_chars += len(text)
        if trim and len(text) > chunk_chars:
            text = _trim_chunk(text, kws, chunk_chars)
            trimmed += 1
        # 2026-09-19: 문서 본문은 **데이터**다. 구조를 흉내 내는 조각(## 질문 · system: · <<<…>>> · [C3])의
        # 표시를 바꾸고 구획으로 감싼다 (llmwiki/ctxguard.py). 내용은 지우지 않는다 — 근거가 줄면 답이 나빠진다.
        tag = "C%d" % (len(cites) + 1)
        if guard_on:
            text, gm = _ctxguard.neutralize(text, guard_mark)
            guard_marks.extend(gm)
            # 머리에 `[C1]` 도 그대로 남긴다 — 모델이 답변에 써야 하는 인용 표기가 그것이기 때문이다.
            # 구획 표시(<<<C1>>>)는 구조용, [C1] 은 인용용으로 역할이 다르다.
            block = _ctxguard.fence(tag, "[%s] (%s | %s)" % (tag, c["doc_id"], c["heading"][:80]), text)
        else:
            block = "[%s] (%s | %s)\n%s" % (tag, c["doc_id"], c["heading"][:80], text)
        if used + len(block) > max_chars:
            if kind in ("neighbor", "doc_expand"):
                dropped.append(c["chunk_id"] + " (max_chars)")
                continue        # 보조 청크가 상한을 넘기면 건너뛰고 다음 항목(다른 hit)은 계속 시도
            # 2026-09-19: 예전에는 여기서 **break** 했다. 그래서 2위에 큰 청크 하나가 있으면 3위 이하가
            # 들어갈 자리가 남아 있어도 통째로 빠졌다 — 상한에 걸린 이유가 순위가 아니라 **길이**인데도.
            # 이제 (a) 남은 공간이 쓸 만하면 잘라서 넣고, (b) 아니면 건너뛰고 다음 후보를 계속 본다.
            room = max_chars - used - (len(block) - len(text))     # 머리말·구획 표시를 뺀 본문 자리
            if min_fit > 0 and room >= min_fit:
                text = text[:room - 1].rstrip() + "…"
                block = (_ctxguard.fence(tag, "[%s] (%s | %s)" % (tag, c["doc_id"], c["heading"][:80]), text) if guard_on
                         else "[%s] (%s | %s)\n%s" % (tag, c["doc_id"], c["heading"][:80], text))
                truncated.append(c["chunk_id"])
            else:
                dropped.append(c["chunk_id"] + " (max_chars)")
                continue
        parts.append(block)
        cit: Dict[str, Any] = {"n": len(cites) + 1, "chunk_id": c["chunk_id"], "doc_id": c["doc_id"], "heading": c["heading"], "kind": kind}
        if kind == "doc_expand":
            cit["score"] = ex_score
            cit["parent"] = next((p_ for p_, lst in extra_by_parent.items() if any(x[0] == c["chunk_id"] for x in lst)), None)
        cites.append(cit)
        used += len(block)
    guard_info = _ctxguard.summarize(guard_marks) if guard_on else {"n": 0, "kinds": {}, "suspect": 0}
    if stage is not None:
        stage.note(text_chars=raw_chars, dropped_duplicates=len(dropped), trimmed_chunks=trimmed, neighbors_added=len(nb_added), doc_expand_added=len(ex_added),
                   truncated_chunks=len(truncated),
                   saved_chars=max(0, raw_chars - used), est_tokens=used // 3,
                   # 컨텍스트 경계: 문서가 구조를 흉내 낸 자리 수 (0 이 정상, 커지면 그 문서를 봐야 한다)
                   guard=guard_info if guard_on else "off")
        stage.debug(dropped=dropped, neighbors=nb_added, doc_expand=ex_added, truncated=truncated, guard_marks=guard_marks[:40])
    graph_txt = ""
    if graph and graph.get("relations") and T.get("context_graph_relations") > 0:
        lines = ["## 그래프 관계 (참고)"]
        for r in graph["relations"][:T.get("context_graph_relations")]:
            lines.append("- %s -[%s]-> %s%s%s" % (r["src"], r["rel"], r["dst"], (": " + r["description"][:90]) if r.get("description") else "",
                                                  (" (%s)" % r["provenance"]) if r.get("provenance") in ("explicit", "rule", "human") else ""))
        graph_txt = "\n".join(lines)
    if graph and graph.get("mcp_enrich"):
        lines = ["## 외부 소스 (MCP)"]
        for e in graph["mcp_enrich"][:5]:
            if e.get("error"):
                continue
            lines.append("- [%s] %s %s: %s" % (e.get("source"), e.get("id"), e.get("title"), (e.get("text") or "")[:300].replace("\n", " ")))
        graph_txt = (graph_txt + "\n\n" if graph_txt else "") + "\n".join(lines)
    body = "\n\n".join(parts) + ("\n\n" + graph_txt if graph_txt else "")
    # 컨텍스트 바로 앞에 한 줄 — 모델은 멀리 있는 시스템 규칙보다 가까운 지시를 잘 따른다
    if guard_on and parts:
        body = _ctxguard.guard_note() + "\n\n" + body
    return {"text": body, "citations": cites, "chars": used,
            "hits_used": [c["chunk_id"] for c in cites], "dropped": dropped, "neighbors": nb_added, "doc_expand": ex_added,
            "truncated": truncated, "guard": guard_info}


# ---------------------------------------------------------------- claim check (답변 검증)
_CITE_RE = re.compile(r"\[C(\d+)\]")
_BK_RE = re.compile(r"\[BK\]", re.I)     # best_effort 답변에서 '배경 지식' 문장 표시 — 근거 검증 대상이 아니다 (verdict=background)
_ID_RE = re.compile(r"\b(?:ISSUE|CL|TC|SWD|HWD|RULE|WR)[-_]?[A-Z0-9][A-Z0-9_-]*\b", re.I)
_NUM_RE = re.compile(r"\d+(?:[.,]\d+)*\s?(?:%|ms|us|ns|dB|dBm|MHz|kHz|GHz|Hz|V|mV|mA|byte|bytes|KB|MB|회|건|개|일|주|년|월)?", re.I)
_NONFACT = ("확인되지 않", "근거 부족", "제공된 문서", "추가 조사", "미확인", "알 수 없", "없습니다", "?")


def _is_cite_only(sent: str) -> bool:
    """`[C1] [C2]` 처럼 **인용 표시만** 남은 조각인가 (구두점·괄호는 무시)."""
    rest = _BK_RE.sub("", _CITE_RE.sub("", sent))
    return not rest.strip(" \t.,;:·-—()[]{}\"'")


def _classify(body: str, cites: List[int], background: bool) -> bool:
    """이 문장이 근거로 검증할 '사실 문장' 인가. 숫자/ID/고유 표현 또는 6토큰 이상 서술."""
    nonfact = any(x in body for x in _NONFACT) and not cites
    toks = [normalize_token(w) for w in words(body)]
    return (not nonfact) and (not background) and (bool(_ID_RE.search(body)) or bool(re.search(r"\d", body)) or len(toks) >= 6)


def split_claims(answer: str) -> List[Dict[str, Any]]:
    """답변을 문장(claim) 단위로 분해. 표 행·헤딩·근거 표는 검증 대상에서 제외. 사실 문장 판별: 숫자/ID/고유 표현 또는 6토큰 이상 서술.

    2026-09-19: **마침표 뒤에 붙인 인용을 잃지 않는다.** LLM 은 `문장입니다. [C1]` 처럼 쓰는 일이 아주 흔한데,
    예전에는 문장 분리에서 `[C1]` 이 8자 미만의 별개 조각이 되어 통째로 버려졌다. 그러면 제대로 인용한 답도
    '인용 없음(uncited)' 으로 깎이고, 반대로 **없는 번호를 인용해도 검사 대상이 되지 않았다**.
    인용만 남은 조각은 **앞 문장의 것**으로 합친다.
    """
    claims: List[Dict[str, Any]] = []
    in_ref_table = False
    for line in (answer or "").splitlines():
        s = line.strip()
        if not s:
            continue
        if s.startswith("#"):
            in_ref_table = "근거" in s or "reference" in s.lower()
            continue
        if s.startswith("|") or s.startswith("```") or in_ref_table:
            continue
        s = s.lstrip("-*•0123456789. ").strip()
        line_start = len(claims)          # 인용 조각은 **같은 줄의** 앞 문장에만 붙인다
        for sent in sentences(s):
            sent = sent.strip()
            if not sent:
                continue
            cites = [int(n) for n in _CITE_RE.findall(sent)]
            if cites and _is_cite_only(sent) and len(claims) > line_start:
                prev = claims[-1]
                prev["text"] = (prev["text"] + " " + sent).strip()
                prev["cites"] = list(dict.fromkeys(prev["cites"] + cites))
                prev["background"] = False        # 인용이 붙었으면 배경 지식 문장이 아니다
                prev["factual"] = _classify(prev["body"], prev["cites"], False)
                continue
            if len(sent) < 8:
                continue
            background = bool(_BK_RE.search(sent)) and not cites    # [BK] = 배경 지식 문장 (best_effort). [C#] 가 같이 있으면 문서 문장으로 본다
            body = _BK_RE.sub("", _CITE_RE.sub("", sent)).strip()
            claims.append({"i": len(claims), "text": sent, "body": body, "cites": cites,
                           "factual": _classify(body, cites, background), "background": background})
    return claims


def _key_tokens(text: str) -> List[str]:
    toks: List[str] = []
    for m in _ID_RE.finditer(text):
        toks.append(m.group(0).lower())
    for m in _NUM_RE.finditer(text):
        v = m.group(0).strip().lower()
        if re.search(r"\d", v) and v not in toks:
            toks.append(v)
    for w in words(text):
        n = normalize_token(w)
        if len(n) >= 2 and n not in STOPWORDS_CLAIM and not n.isdigit() and n not in toks:
            toks.append(n)
    return toks


STOPWORDS_CLAIM = {"있다", "없다", "이다", "한다", "된다", "하는", "되는", "있는", "없는", "것", "수", "등", "및", "또는", "그리고", "대한", "위한", "통해", "따라",
                   "때문", "경우", "이후", "이전", "관련", "해당", "the", "a", "an", "of", "is", "are", "to", "and", "or", "in", "on", "for", "with", "by", "from", "that", "this"}


def _support_ratio(claim: Dict[str, Any], evidence_text: str) -> float:
    keys = _key_tokens(claim["body"])
    if not keys:
        return 1.0
    et = evidence_text.lower()
    et_toks = set(normalize_token(w) for w in words(et))
    hit = 0
    for k in keys:
        kk = k.replace(" ", "")
        if k in et or kk in et.replace(" ", "") or k in et_toks:
            hit += 1
    return hit / float(len(keys))


def check_claims(answer: str, citations: List[Dict[str, Any]], chunks: Dict[str, Any], support_min: float = 0.5) -> Dict[str, Any]:
    """휴리스틱 claim 검증: 사실 문장마다 (1) 인용 존재 (2) 인용 근거에 핵심 토큰(ID·수치·명사)이 실제로 있는 비율 ≥ support_min."""
    cite_text: Dict[int, str] = {}
    for c in citations:
        ch = chunks.get(c["chunk_id"])
        if ch:
            cite_text[c["n"]] = (ch["heading"] + "\n" + ch["text"])
    all_text = "\n".join(cite_text.values())
    claims = split_claims(answer)
    n_fact = n_sup = n_partial = n_unsup = n_bg = n_bad = 0
    for cl in claims:
        if cl.get("background"):
            # best_effort 의 [BK] 문장: 문서 근거를 요구하지 않는다. 미지원으로 세지도, [미확인] 표기/제거 대상도 아니다.
            cl["verdict"] = "background"
            n_bg += 1
            continue
        if not cl["factual"]:
            cl["verdict"] = "n/a"
            continue
        n_fact += 1
        if not cl["cites"]:
            # 인용은 없지만 근거 전체에서 지지되는지 확인 (인용 누락 vs 근거 없음 구분)
            r = _support_ratio(cl, all_text)
            cl["support"] = round(r, 2)
            cl["verdict"] = "uncited" if r >= support_min else "unsupported"
            if cl["verdict"] == "uncited":
                n_partial += 1
            else:
                n_unsup += 1
            continue
        # 없는 번호를 인용했나. 인용이 **있다는 것**만으로 맞는 줄 알면 안 된다 — 모델이 지어낸 `[C99]` 는
        # 지금까지 "근거 본문이 비어 있다" 로만 취급돼 사람에게 그 이유가 보이지 않았다.
        bad = [n for n in cl["cites"] if n not in cite_text]
        if bad:
            cl["bad_cites"] = bad
            n_bad += len(bad)
        ev = "\n".join(cite_text.get(n, "") for n in cl["cites"])
        r = _support_ratio(cl, ev) if ev else 0.0
        cl["support"] = round(r, 2)
        if bad and not ev:
            cl["verdict"] = "fabricated_citation"     # 인용한 번호가 아예 없다 = 가장 나쁜 경우
            n_unsup += 1
        elif r >= support_min:
            cl["verdict"] = "supported"
            n_sup += 1
        elif r >= support_min * 0.6:
            cl["verdict"] = "partial"
            n_partial += 1
        else:
            cl["verdict"] = "unsupported"
            n_unsup += 1
    ground = (n_sup + 0.5 * n_partial) / float(n_fact) if n_fact else 1.0
    return {"claims": claims, "n_factual": n_fact, "supported": n_sup, "partial": n_partial, "unsupported": n_unsup, "background": n_bg,
            "bad_citations": n_bad, "max_citation": max(cite_text) if cite_text else 0,
            "groundedness": round(ground, 3), "citation_precision": round(n_sup / float(n_sup + n_unsup), 3) if (n_sup + n_unsup) else 1.0}


def check_claims_llm(llm: BaseLLM, query: str, claims: List[Dict[str, Any]], citations: List[Dict[str, Any]], chunks: Dict[str, Any],
                     effort: str = "low", max_tokens: int = 1500) -> Dict[str, Any]:
    from . import prompts as _prompts
    from .providers import parse_json
    lines = ["## 질문", query, "", "## 근거"]
    for c in citations:
        ch = chunks.get(c["chunk_id"])
        if ch:
            lines.append("[C%d] %s" % (c["n"], ch["text"][:1200].replace("\n", " ")))
    lines += ["", "## 문장(claims)"]
    fact = [cl for cl in claims if cl.get("factual")]
    for cl in fact:
        lines.append("(%d) %s" % (cl["i"], cl["text"]))
    r = llm.complete(_prompts.get("claim_check"), "\n".join(lines), max_tokens=max_tokens, effort=effort, json_mode=True)
    data = parse_json(r["text"]) or {}
    by_i = {int(x.get("i")): x for x in (data.get("claims") or []) if isinstance(x, dict) and str(x.get("i", "")).lstrip("-").isdigit()}
    changed = 0
    for cl in fact:
        v = by_i.get(cl["i"])
        if v and str(v.get("verdict", "")).lower() in ("supported", "partial", "unsupported"):
            if cl.get("verdict") != v["verdict"]:
                changed += 1
            cl["verdict"] = v["verdict"].lower()
            cl["llm_note"] = str(v.get("note") or "")[:200]
    n_sup = sum(1 for cl in fact if cl.get("verdict") == "supported")
    n_par = sum(1 for cl in fact if cl.get("verdict") in ("partial", "uncited"))
    n_un = sum(1 for cl in fact if cl.get("verdict") == "unsupported")
    return {"changed": changed, "supported": n_sup, "partial": n_par, "unsupported": n_un,
            "groundedness": round((n_sup + 0.5 * n_par) / float(len(fact)), 3) if fact else 1.0, "usage": r.get("usage"), "raw": r["text"][:600]}


def apply_claim_policy(answer: str, claims: List[Dict[str, Any]], policy: str = "mark") -> Tuple[str, int]:
    """미지원 문장 처리. mark: '[미확인]' 표기 · drop: 문장 제거. 반환 (답변, 처리 수)."""
    bad = [cl for cl in claims if cl.get("verdict") == "unsupported"]
    if not bad:
        return answer, 0
    out = answer
    n = 0
    for cl in bad:
        t = cl["text"]
        if t not in out:
            continue
        if policy == "drop":
            out = out.replace(t, "").replace("\n\n\n", "\n\n")
        else:
            out = out.replace(t, t + " [미확인: 근거에서 확인되지 않음]")
        n += 1
    return out, n


def refine_answer(llm: BaseLLM, query: str, ctx: Dict[str, Any], answer: str, claims: List[Dict[str, Any]], effort: str = "medium",
                  max_tokens: int = 3000) -> Dict[str, Any]:
    bad = [cl["text"] for cl in claims if cl.get("verdict") == "unsupported"]
    sys_p = ANSWER_SYSTEM(_tuning.T.get("answer_length_target")) + "\n\n## 재작성 지시\n아래 초안에서 근거가 지지하지 않는 문장은 제거하거나 '제공된 문서에서 확인되지 않음' 으로 바꾸세요. 나머지는 유지하고 인용을 보강하세요."
    user = "## 질문\n%s\n\n## 컨텍스트\n%s\n\n## 초안\n%s\n\n## 근거 없는 문장\n%s" % (query, ctx["text"], answer, "\n".join("- " + b for b in bad))
    r = llm.complete(sys_p, user, max_tokens=max_tokens, effort=effort)
    return {"answer": r["text"].strip(), "usage": r.get("usage"), "ms": r.get("ms")}


_MD_LINE_RE = re.compile(r"^\s*(?:#{1,6}\s+|[-*•]\s+|\d+[.)]\s+|\|)")
_ID_TOKEN_RE = re.compile(r"\b(?:ISSUE|CL|TC|SWD|HWD|RULE|WR)-[A-Z0-9][A-Z0-9_-]*\b", re.I)
_PER_DOC = {"short": 1, "normal": 3, "long": 5}


def _clean_sentence(s: str) -> str:
    """문장 앞의 마크다운 표식(헤딩 #, 불릿, 번호, 표 파이프)과 강조 기호를 제거."""
    s = _MD_LINE_RE.sub("", s.strip())
    s = s.strip("|").strip()
    s = re.sub(r"\*\*(.+?)\*\*", r"\1", s)
    return s.strip("-* \t")


def _section(heading: str) -> str:
    return (heading or "").split(">")[-1].strip()


_FUNC_ENDINGS = ("도", "나", "서", "면", "지", "요", "까", "게", "는", "은", "을", "를", "에", "의", "로", "와", "과", "다", "고", "며", "죠", "네", "냐", "니")


def _is_content_term(k: str) -> bool:
    """'미확인 용어' 후보로 쓸 만한 내용어인지 — 영문/숫자/ID 는 항상, 한글은 어미·조사처럼 보이는 끝을 제외."""
    if re.search(r"[a-z0-9]", k):
        return True
    return len(k) >= 2 and not k.endswith(_FUNC_ENDINGS)


def _doc_label(doc_id: str, dm: Dict[str, Any], heading: str) -> str:
    """근거 문서를 사람이 알아보는 이름으로: ext_id(ISSUE-2001) 또는 제목 또는 파일명."""
    ext = (dm or {}).get("ext_id")
    title = (heading or "").split(">")[0].strip()
    if ext and title and ext.lower() not in title.lower():
        return "%s · %s" % (ext, title[:60])
    return ext or title[:60] or doc_id.rsplit("/", 1)[-1]


def extractive_structured(query: str, ctx: Dict[str, Any], chunks: Dict[str, Any], meta: Optional[Dict[str, Any]] = None,
                          graph: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """LLM 없이 근거 문단만으로 answer_guide 구조(핵심 → 문서별 상세 → 관계 → 근거 표 → 미확인)를 만든다.
    생성은 하지 않고 원문 문장을 질의 키워드 일치 순으로 고른다. tuning: extractive_sentences(핵심 문장 수), extractive_min/max_len,
    answer_length_target(문서별 상세 문장 수 short 1 / normal 3 / long 5), context_graph_relations(관계 표시 수)."""
    T = _tuning.T
    meta = meta or {}
    kws = keywords(query)
    mn, mx = int(T.get("extractive_min_len")), int(T.get("extractive_max_len"))
    per_doc = _PER_DOC.get(str(T.get("answer_length_target")), 3)
    n_core = int(T.get("extractive_sentences"))

    # 문서 단위로 근거 청크와 후보 문장 수집 (컨텍스트 순서 = 순위 순서)
    docs: Dict[str, Dict[str, Any]] = {}
    order: List[str] = []
    all_text: List[str] = []
    body_first: Dict[int, Tuple[str, str]] = {}   # cite n → (섹션, 본문 첫 문장)
    for cite in ctx["citations"]:
        c = chunks.get(cite["chunk_id"])
        if not c:
            continue
        did = c["doc_id"]
        title = (c["heading"] or "").split(">")[0].strip().lower()
        sec = _section(c["heading"])
        ext = ((meta.get(did) or {}).get("ext_id") or "").lower()
        if did not in docs:
            docs[did] = {"cites": [], "headings": [], "sents": [], "body": [], "meta": meta.get(did) or {}}
            order.append(did)
        d = docs[did]
        d["cites"].append(cite["n"])
        d["headings"].append(c["heading"])
        all_text.append((c["heading"] + " " + c["text"]).lower())
        for raw in sentences(c["text"]):
            s = _clean_sentence(raw)
            if not s or len(s) < 4:
                continue
            low = s.lower()
            # 청크 본문에 포함된 헤딩 줄·문서 제목 줄은 문장이 아니므로 제외
            if low == sec.lower() or low == title or (ext and low.startswith(ext) and title in low) or low in (ext, ext + " " + title):
                continue
            toks = set(normalize_token(w) for w in words(s))
            sc = sum(1 for k in kws if k in toks or k in low)
            if mn <= len(s) <= mx:
                if cite["n"] not in body_first:
                    body_first[cite["n"]] = (sec, s)
                    d["body"].append((sec, s, cite["n"]))
                if sc > 0:
                    d["sents"].append((sc + 0.001 * len(s), s, cite["n"], sec))
    for d in docs.values():
        d["sents"].sort(key=lambda x: -x[0])
        seen: set = set()
        uniq = []
        for item in d["sents"]:
            key = item[1][:40]
            if key in seen:
                continue
            seen.add(key)
            uniq.append(item)
        d["sents"] = uniq

    lines: List[str] = ["> ℹ LLM 미사용 — 근거 문서의 원문 문장을 골라 구조화한 답변입니다. LLM 을 연결하면(`models test`) 같은 근거로 서술형 답변이 생성됩니다.", ""]
    # ---- 핵심: 문서마다 최고 문장 1개(키워드 문장 없으면 첫 본문 문장), 전체 n_core 개까지
    core: List[str] = []
    for did in order:
        d = docs[did]
        label = _doc_label(did, d["meta"], d["headings"][0])
        if d["sents"]:
            sc, s, n, _sec = d["sents"][0]
            core.append("- **%s** — %s [C%d]" % (label, s, n))
        elif d["body"]:
            sec, s, n = d["body"][0]
            core.append("- **%s** — (%s) %s [C%d]" % (label, sec or "본문", s, n))
        if len(core) >= n_core:
            break
    if len(core) < n_core:   # 남은 자리는 2순위 키워드 문장으로
        for did in order:
            for sc, s, n, _sec in docs[did]["sents"][1:2]:
                if len(core) >= n_core:
                    break
                core.append("- %s [C%d]" % (s, n))
    lines.append("## 핵심")
    lines.extend(core or ["- 질문 키워드와 직접 일치하는 문장이 없습니다. 아래 근거 문단과 근거 표를 확인하세요."])
    lines.append("")

    # ---- 문서별 상세: 키워드 문장 → 부족하면 인용 청크별 본문 첫 문장으로 보충
    lines.append("## 상세 (근거 문서별)")
    for did in order:
        d = docs[did]
        dm = d["meta"]
        ext = (dm.get("ext_id") or "").lower()
        title = (d["headings"][0] or "").split(">")[0].strip().lower()
        tag = " · ".join(x for x in (dm.get("doc_type"), dm.get("date"), ("status " + dm["status"]) if dm.get("status") else None) if x)
        cites = ",".join("C%d" % n for n in d["cites"])
        lines.append("### [%s] %s%s" % (cites, _doc_label(did, dm, d["headings"][0]), (" (%s)" % tag) if tag else ""))
        secs: List[str] = []
        for h in d["headings"]:
            sname = _section(h)
            if sname and sname.lower() != title and not (ext and sname.lower().startswith(ext)) and sname not in secs:
                secs.append(sname)
        if secs:
            lines.append("- 위치: " + " / ".join(secs[:6]))
        shown_keys: set = set()
        cnt = 0
        for sc, s, n, sec in d["sents"]:
            if cnt >= per_doc:
                break
            lines.append("- %s%s [C%d]" % (("(%s) " % sec) if sec and len(secs) > 1 else "", s, n))
            shown_keys.add(s[:40])
            cnt += 1
        for sec, s, n in d["body"]:
            if cnt >= per_doc:
                break
            if s[:40] in shown_keys:
                continue
            lines.append("- (%s) %s [C%d]" % (sec or "본문", s, n))
            shown_keys.add(s[:40])
            cnt += 1
        lines.append("")

    # ---- 그래프 관계: 근거 문서 ID 가 관련된 결정적 관계 우선, (src, rel, dst) 중복 제거
    rel_n = min(int(T.get("context_graph_relations")), 8)
    rels = list((graph or {}).get("relations") or [])
    if rels and rel_n > 0:
        ids = set()
        for did in order:
            ext = (docs[did]["meta"] or {}).get("ext_id")
            if ext:
                ids.add(ext.lower())
        ids.update(m.lower() for m in _ID_TOKEN_RE.findall(query))
        prov_rank = {"explicit": 0, "human": 0, "rule": 1, "llm": 2, "cooccur": 3}
        rels.sort(key=lambda r: (prov_rank.get(r.get("provenance") or "", 4), -(r.get("gain") or 0)))
        touching = [r for r in rels if str(r.get("src", "")).lower() in ids or str(r.get("dst", "")).lower() in ids]
        seen_rel: set = set()
        shown = []
        for r in touching + [r for r in rels if r not in touching]:
            key = (str(r.get("src")), str(r.get("rel")), str(r.get("dst")))
            if key in seen_rel:
                continue
            seen_rel.add(key)
            desc = str(r.get("description") or "")
            if desc.startswith("front matter"):
                desc = ""
            shown.append("- %s —[%s]→ %s%s%s" % (r["src"], r["rel"], r["dst"], (": " + desc[:80]) if desc else "",
                                                (" (%s)" % r["provenance"]) if r.get("provenance") else ""))
            if len(shown) >= rel_n:
                break
        if shown:
            lines.append("## 관련 관계 (지식 그래프)")
            lines.extend(shown)
            lines.append("")

    # ---- 근거 표
    lines.append("## 근거")
    lines.append("| 근거 | 문서 | 유형 | 위치 | 내용 |")
    lines.append("|---|---|---|---|---|")
    for cite in ctx["citations"]:
        c = chunks.get(cite["chunk_id"])
        if not c:
            continue
        dm = meta.get(c["doc_id"]) or {}
        d = docs.get(c["doc_id"], {})
        best = next((s for sc, s, n, sec in d.get("sents", []) if n == cite["n"]), None) or body_first.get(cite["n"], ("", ""))[1] or "(제목/헤딩만 있는 문단)"
        best = best.replace("|", "／")
        lines.append("| [C%d] | %s | %s | %s | %s |" % (cite["n"], _doc_label(c["doc_id"], dm, c["heading"]).replace("|", "／"), dm.get("doc_type") or "-",
                                                      _section(c["heading"]).replace("|", "／") or "-", best[:110] + ("…" if len(best) > 110 else "")))
    lines.append("")

    # ---- 미확인: 질의의 내용어 중 근거 어디에도 없는 것 (조사·어미가 붙은 기능어는 제외)
    blob = " ".join(all_text)
    missing = [k for k in kws if k not in blob and _is_content_term(k)]
    if missing:
        lines.append("## 미확인 · 추가 조사 필요")
        lines.append("- 근거 문서에 없는 질문 용어: " + ", ".join("`%s`" % m for m in missing[:8]) + " — 해당 용어를 다루는 문서가 코퍼스에 없거나 표기가 다를 수 있습니다 (`rules add synonym …` 또는 문서 추가).")
        lines.append("")
    text = "\n".join(lines).rstrip()
    return {"text": text, "n_docs": len(order), "core": len(core), "missing": missing, "keywords": kws}


def result_type_of(mode: str, answer_mode: str = "grounded") -> str:
    """답변 산출 방식(ans.mode) + 답변 모드 → 결과 유형. grounded | best_effort | extractive | insufficient | error."""
    if mode == "llm":
        return "best_effort" if normalize_answer_mode(answer_mode) == "best_effort" else "grounded"
    return mode if mode in ("extractive", "insufficient", "error") else "grounded"


# 앙상블 요약은 **프로바이더 한 곳**에서 만든다 — 역할 11개 어디에 켜도 같은 모양이 나오게.
# (여기에 두면 answer 역할에만 붙고 rerank·summary 등은 빠진다.)
from .providers import summarize_ensemble      # noqa: F401  (호환용 재노출)


def generate_answer(query: str, ctx: Dict[str, Any], llm: Optional[BaseLLM], use_llm: bool, prof: Profiler,
                    effort: str = "medium", chunks: Optional[Dict[str, Any]] = None, hits: Optional[List[Any]] = None,
                    max_tokens: int = 3000, meta: Optional[Dict[str, Any]] = None, graph: Optional[Dict[str, Any]] = None,
                    mode: str = "grounded", verdict: str = "sufficient", reasons: Optional[List[str]] = None,
                    degrade: bool = True) -> Dict[str, Any]:
    """답변 생성. 반환 {"answer","mode"(llm|extractive|error),"result_type","cited","model"[,"repeat_loop","error"]}.

    mode: grounded = answer_system.md + answer_guide.md · best_effort = answer_best_effort.md ([C#] + [BK]).
      best_effort 에서 근거 판정(verdict)이 sufficient 가 아니면 프롬프트 첫머리에 그 사실을 알려 배경 지식 사용을 허용한다.
    degrade: LLM 이 최종 실패했을 때 추출식 답변으로 이어갈지(toggles.degrade_on_llm_failure). False 면 mode=error 로 끝낸다 —
      incident 는 providers 가 이미 남겼으므로 llm_report 에는 그대로 나온다.
    """
    mode = normalize_answer_mode(mode)
    if use_llm and llm and llm.available:
        length_target = _tuning.T.get("answer_length_target")
        sys_p = ANSWER_SYSTEM_BEST_EFFORT(length_target) if mode == "best_effort" else ANSWER_SYSTEM(length_target)
        with prof.stage("answer_llm", model=getattr(llm, "model", llm.name), role=getattr(llm, "role", ""),
                        context_chars=ctx["chars"], effort=effort, max_tokens=max_tokens, length_target=length_target, answer_mode=mode) as st:
            head = ""
            if mode == "best_effort" and verdict != "sufficient":
                head = ("## 근거 판정\n검색된 문서 근거가 %s 로 판정되었습니다%s. 문서에 없는 부분은 배경 지식으로 보완하되 각 문장 끝에 [BK] 를 붙이고, "
                        "문서에서 확인된 문장에만 [C#] 를 붙이세요.\n\n" % ("불충분(insufficient)" if verdict == "insufficient" else "약함(weak)",
                                                                       (" — " + "; ".join(reasons)) if reasons else ""))
            prompt = "%s## 질문\n%s\n\n## 컨텍스트\n%s" % (head, query, ctx["text"])
            st.note(prompt_chars=len(prompt) + len(sys_p), est_input_tokens=(len(prompt) + len(sys_p)) // 3, verdict=verdict)
            st.sample(system=sys_p, prompt=prompt[:12000])
            try:
                r = llm.complete(sys_p, prompt, max_tokens=max_tokens, effort=effort)
                text = r["text"].strip()
                repeat = None
                if _tuning.T.get("answer_repeat_guard"):
                    text, repeat = guard_repeat(text, int(_tuning.T.get("answer_repeat_min_chars")),
                                                int(_tuning.T.get("answer_repeat_times")))
                cited = sorted(set(int(n) for n in re.findall(r"\[C(\d+)\]", text)))
                st.note(usage=r.get("usage"), ms_llm=round(r.get("ms", 0)), cited=cited, model=r.get("model"),
                        answer_chars=len(text), repeat_loop=repeat, background_marks=len(_BK_RE.findall(text)))
                st.sample(response=text[:6000])
                out = {"answer": text, "mode": "llm", "result_type": result_type_of("llm", mode), "cited": cited, "model": r.get("model")}
                if repeat:
                    # 반복 루프가 난 답변은 캐시에 넣지 않는다 (pipeline 이 이 표시를 본다)
                    out["repeat_loop"] = repeat
                return out
            except LLMError as e:
                if not degrade:
                    # degrade_on_llm_failure=false: 대체 경로 없이 오류로 끝낸다. incident 는 providers.complete 가 이미 기록했다.
                    st.note(error=str(e)[:300], fallback="none (degrade_on_llm_failure=false)")
                    text = ("LLM 호출 실패: %s\n\n답변을 만들지 않았습니다 (toggles.degrade_on_llm_failure=false). 추출식 답변으로 계속하려면 "
                            "이 토글을 켜거나, llm_report 의 원인(재시도 횟수·timeout)을 확인하세요." % str(e)[:300])
                    return {"answer": text, "mode": "error", "result_type": "error", "cited": [], "model": None, "error": str(e)[:300]}
                st.note(error=str(e)[:300], fallback="extractive")
    elif use_llm:
        prof.skipped("answer_llm", "LLM provider unavailable → extractive fallback")
    else:
        prof.skipped("answer_llm")
    with prof.stage("answer_extractive") as st:
        if not ctx["citations"]:
            text = "관련 문단을 찾지 못했습니다. 근거 표가 비어 있습니다."
            st.note(sentences=0, cited=[], docs=0)
            return {"answer": text, "mode": "extractive", "result_type": "extractive", "cited": [], "model": None}
        r = extractive_structured(query, ctx, chunks or {}, meta, graph)
        text = r["text"]
        cited = sorted(set(int(n) for n in re.findall(r"\[C(\d+)\]", text)))
        st.note(sentences=r["core"], docs=r["n_docs"], cited=cited, keywords=r["keywords"], missing_terms=r["missing"], answer_chars=len(text))
        return {"answer": text, "mode": "extractive", "result_type": "extractive", "cited": cited, "model": None}
