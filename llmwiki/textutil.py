# -*- coding: utf-8 -*-
"""한국어/영문 혼용 텍스트 유틸: 토크나이저, n-gram, 조사 제거.

외부 형태소 분석기 없이 동작하도록 (1) 공백/기호 분리 (2) 흔한 조사 제거 (3) 문자 bigram 을 함께 색인한다.
FTS5 unicode61 토크나이저는 한국어 조사 결합 단어를 분리하지 못하므로, 사전 토큰화된 문자열을 FTS 컬럼에 저장한다.
"""
from __future__ import annotations

import os
import re
import hashlib
from typing import Any, Dict, FrozenSet, Iterator, List, Optional, Tuple

_JOSA = [
    "으로부터", "에서는", "에게서", "으로써", "으로서", "이라고", "라고", "에서", "에게", "으로", "까지", "부터",
    "처럼", "보다", "마다", "조차", "이나", "이란", "이며", "이다", "은", "는", "이", "가", "을", "를", "의", "에",
    "와", "과", "도", "로", "만", "께", "랑", "나", "며", "다",
]
_JOSA.sort(key=len, reverse=True)

_TOKEN_RE = re.compile(r"[0-9]+(?:[.,][0-9]+)*%?|[A-Za-z][A-Za-z0-9\-\+]*|[가-힣]+|[一-龥]+", re.UNICODE)
_HANGUL = re.compile(r"^[가-힣]+$")


def strip_josa(tok: str) -> str:
    if not _HANGUL.match(tok) or len(tok) < 3:
        return tok
    for j in _JOSA:
        if tok.endswith(j) and len(tok) - len(j) >= 2:
            return tok[: -len(j)]
    return tok


def words(text: str) -> List[str]:
    return [t for t in _TOKEN_RE.findall(text or "")]


def normalize_token(t: str) -> str:
    t = t.lower().replace(",", "")
    return strip_josa(t)


def char_ngrams(tok: str, n: int = 2) -> List[str]:
    if len(tok) < n:
        return []
    return [tok[i:i + n] for i in range(len(tok) - n + 1)]


# ---------------------------------------------------------------- 한국어 분석 플러그인 (복합어 사전 · 형태소 분석기)
# 색인(tokenize_for_fts)과 질의(keywords/fts_query)가 같은 규칙을 쓰도록 여기서만 관리한다.
COMPOUNDS: Dict[str, List[str]] = {}        # "재전송타이머" -> ["재전송", "타이머"]  (query_rules.json 의 compound)
_TOKENIZER: Dict[str, Any] = {"name": "heuristic", "kiwi": None, "tried": False}


def set_compounds(d: Dict[str, List[str]]) -> None:
    COMPOUNDS.clear()
    for k, v in (d or {}).items():
        parts = [p.strip().lower() for p in (v if isinstance(v, (list, tuple)) else str(v).replace("+", " ").split()) if p.strip()]
        if k and parts:
            COMPOUNDS[k.strip().lower()] = parts


def set_tokenizer(name: str) -> str:
    """heuristic | kiwi | auto. kiwi 는 kiwipiepy 가 설치된 경우에만, 아니면 heuristic 으로 폴백."""
    name = (name or "heuristic").lower()
    if name in ("kiwi", "auto"):
        if _TOKENIZER["kiwi"] is None and not _TOKENIZER["tried"]:
            _TOKENIZER["tried"] = True
            try:
                from kiwipiepy import Kiwi  # type: ignore
                _TOKENIZER["kiwi"] = Kiwi()
            except Exception:
                _TOKENIZER["kiwi"] = None
        _TOKENIZER["name"] = "kiwi" if _TOKENIZER["kiwi"] is not None else "heuristic"
    else:
        _TOKENIZER["name"] = "heuristic"
    return _TOKENIZER["name"]


def tokenizer_name() -> str:
    return _TOKENIZER["name"]


_KIWI_TAGS = ("NNG", "NNP", "NNB", "SL", "SN", "XR", "SH")


def _kiwi_tokens(text: str) -> List[str]:
    kw = _TOKENIZER.get("kiwi")
    if kw is None or not text:
        return []
    out: List[str] = []
    try:
        for t in kw.tokenize(text):
            if t.tag in _KIWI_TAGS and len(t.form) >= 2:
                out.append(t.form.lower())
    except Exception:
        return []
    return out


def compound_parts(tok: str) -> List[str]:
    """복합어 사전 분리 (사전에 있으면 부분, 없으면 부분 문자열이 사전 항목이면 그것)."""
    lt = tok.lower()
    if lt in COMPOUNDS:
        return COMPOUNDS[lt]
    parts: List[str] = []
    if len(lt) >= 4 and _HANGUL.match(lt):
        for k, v in COMPOUNDS.items():
            if k in lt and k != lt:
                parts.extend(v)
    return parts


def analyze(text: str) -> List[str]:
    """정규화 토큰 (소문자 · 조사 제거 · 복합어 부분 · 형태소 명사). 중복 제거, 순서 유지. 색인과 질의가 공유."""
    out: List[str] = []
    seen = set()

    def push(t: str) -> None:
        if t and t not in seen:
            seen.add(t)
            out.append(t)
    for w in words(text):
        n = normalize_token(w)
        push(n)
        for p in compound_parts(n):
            push(strip_josa(p))
    if _TOKENIZER["name"] == "kiwi":
        for t in _kiwi_tokens(text):
            push(normalize_token(t))
    return out


def tokenize_for_fts(text: str, with_bigrams: bool = True) -> str:
    """FTS 색인/질의용 토큰 문자열. 원형 토큰 + 조사제거 토큰 + (한글) 문자 bigram + 복합어 부분 + (kiwi) 형태소."""
    out: List[str] = []
    for w in words(text):
        lw = w.lower()
        out.append(lw)
        n = normalize_token(w)
        if n != lw:
            out.append(n)
        for p in compound_parts(n):
            out.append(p)
        if with_bigrams and _HANGUL.match(n) and len(n) >= 3:
            out.extend("_" + g for g in char_ngrams(n, 2))
    if _TOKENIZER["name"] == "kiwi":
        base = set(out)
        for t in _kiwi_tokens(text):
            if t not in base:
                out.append(t)
    return " ".join(out)


def fts_query(text: str, mode: str = "OR") -> str:
    """사용자 질의 -> FTS5 MATCH 식. 토큰은 큰따옴표로 감싸 특수문자 안전 처리."""
    toks: List[str] = []
    seen = set()
    for w in words(text):
        for cand in {w.lower(), normalize_token(w)}:
            if len(cand) < 1 or cand in seen:
                continue
            seen.add(cand)
            toks.append('"%s"' % cand.replace('"', ""))
    if not toks:
        return '""'
    return (" %s " % mode).join(toks)


# ---------------------------------------------------------------- 불용어 (stopwords.json)
# 질의 키워드 추출(keywords)·근거 토큰 비교(retrieval/forensic)에서 제거할 단어. 코드 기본값은 DEFAULT_STOPWORDS 이고,
# 실제 목록은 <루트>/stopwords.json (경로 레지스트리 "stopwords", 환경변수 LLMWIKI_STOPWORDS_PATH) 에서 읽는다.
# 파일이 없으면 기본값으로 생성하고, mtime 캐시로 수정 즉시 반영된다(prompts 와 같은 방식). 깨진 파일은 기본값으로 폴백.
DEFAULT_STOPWORDS: FrozenSet[str] = frozenset({
    "무엇", "어떤", "어떻게", "왜", "언제", "누가", "누구", "대해", "관련", "알려", "설명", "정리", "요약",
    "the", "a", "an", "of", "is", "are", "what", "which", "who", "when", "how", "why", "and", "or", "to",
    "있", "있는", "하는", "되는", "이후", "이전", "것", "수", "등", "및", "각", "때", "중", "무슨", "해줘",
    "주세요", "인가", "인가요", "입니까", "뭐야", "뭔가", "얼마", "어디",
})
STOPWORDS_COMMENT = ("질의 키워드 추출에서 제거할 불용어 목록. 소문자·조사 제거 뒤의 토큰과 비교한다. "
                     "수정하면 재시작 없이 다음 질의부터 반영된다. 삭제하면 코드 기본 목록으로 다시 생성된다. "
                     "경로: config paths 의 stopwords (환경변수 LLMWIKI_STOPWORDS_PATH).")
_STOP_CACHE: Dict[str, Tuple[Optional[float], FrozenSet[str]]] = {}   # path -> (mtime, set)


def stopwords_path() -> str:
    from .config import path_for   # 순환 import 회피 (config 가 textutil 을 간접 import 할 수 있음)
    return path_for("stopwords")


def _warn(msg: str, **data: Any) -> None:
    try:
        from .logging_setup import log
        log("warning", msg, "textutil", **data)
    except Exception:
        pass


def _parse_stopwords(obj: Any, path: str) -> Optional[FrozenSet[str]]:
    """{"stopwords": [...]} 또는 [...] 를 집합으로. 형식이 틀리면 None."""
    items = obj.get("stopwords") if isinstance(obj, dict) else obj
    if not isinstance(items, list) or not all(isinstance(x, str) for x in items):
        _warn("stopwords 파일 형식 오류 — 기본 목록 사용", path=path, hint='{"stopwords": ["단어", ...]} 형식이어야 함')
        return None
    return frozenset(x.strip().lower() for x in items if x and x.strip())


def load_stopwords() -> FrozenSet[str]:
    """불용어 집합. 파일이 있으면 파일(mtime 캐시), 없으면 기본값으로 파일을 만든 뒤 반환, 오류면 기본값."""
    try:
        p = stopwords_path()
    except Exception:
        return DEFAULT_STOPWORDS
    try:
        mt: Optional[float] = os.path.getmtime(p)
    except OSError:
        mt = None
        try:
            from .atomicio import write_json
            write_json(p, {"_comment": STOPWORDS_COMMENT, "stopwords": sorted(DEFAULT_STOPWORDS)})
            mt = os.path.getmtime(p)
        except Exception as e:
            _warn("stopwords 파일 생성 실패 — 기본 목록 사용", path=p, error=str(e)[:200])
            return DEFAULT_STOPWORDS
    c = _STOP_CACHE.get(p)
    if c and c[0] == mt:
        return c[1]
    try:
        from .atomicio import read_text
        import json
        text = read_text(p)
        words_ = _parse_stopwords(json.loads(text), p) if text is not None else None
    except Exception as e:
        _warn("stopwords 파일 읽기 실패 — 기본 목록 사용", path=p, error=str(e)[:200])
        words_ = None
    result = DEFAULT_STOPWORDS if words_ is None else words_
    _STOP_CACHE[p] = (mt, result)   # 깨진 파일도 mtime 으로 기억해 경고를 한 번만 남긴다
    return result


class _LiveStopwords:
    """`from textutil import STOPWORDS` 후 `tok in STOPWORDS` 로 쓰던 기존 호출부 호환 — 매번 현재 파일 집합을 본다."""
    __slots__ = ()

    def __contains__(self, item: object) -> bool:
        return item in load_stopwords()

    def __iter__(self) -> Iterator[str]:
        return iter(load_stopwords())

    def __len__(self) -> int:
        return len(load_stopwords())

    def __repr__(self) -> str:
        return "STOPWORDS(%d)" % len(self)


STOPWORDS = _LiveStopwords()


def keywords(text: str, min_len: int = 2) -> List[str]:
    """질의에서 의미 있는 키워드(조사 제거, 중복 제거, 불용어 제거, 복합어 부분·형태소 포함)."""
    stop = load_stopwords()
    out: List[str] = []
    for n in analyze(text):
        if len(n) < min_len or n in stop:
            continue
        if n not in out:
            out.append(n)
    return out


def sha1(s: str) -> str:
    return hashlib.sha1(s.encode("utf-8")).hexdigest()


def stable_hash(s: str, mod: int) -> int:
    return int(hashlib.md5(s.encode("utf-8")).hexdigest()[:8], 16) % mod


def sentences(text: str) -> List[str]:
    parts = re.split(r"(?<=[.!?。])\s+|\n+", text or "")
    return [p.strip() for p in parts if p and p.strip()]
