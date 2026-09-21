# -*- coding: utf-8 -*-
"""회귀 평가: eval/questions.json 의 질문별 기대 문서(doc 부분 문자열) 를 top-k 에서 찾는 비율(hit@k, MRR)."""
from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional

from .config import ROOT, path_for

EVAL_PATH = path_for("eval")   # LLMWIKI_EVAL_PATH > <ROOT>/eval/questions.json
SAMPLE_QUESTIONS = os.path.join(ROOT, "setup", "sample_corpus_modem", "questions.json")

DEFAULT_QUESTIONS: List[Dict[str, Any]] = [
    {"q": "HBM4 캐파 확장 1차 투자 금액과 담당은?", "expect_docs": ["2026-05-20-capex"], "expect_terms": ["1,500억"]},
    {"q": "2,800억 추가 투자는 언제 재검토되나?", "expect_docs": ["2026-05-20-capex"], "expect_terms": ["5/22", "2026.05.22"]},
    {"q": "ROI 회수기간 가이드는 몇 년이고 누가 주장했나?", "expect_docs": ["2026-05-15-board-routine"], "expect_terms": ["4년"]},
    {"q": "SK하이닉스 HBM4 양산 시점과 수율은?", "expect_docs": ["sk_hynix"], "expect_terms": ["7월", "75%"]},
    {"q": "미국 수출통제 케이스 B 의 HBM 매출 영향은?", "expect_docs": ["us_export_control"], "expect_terms": ["-4.5%"]},
    {"q": "NVIDIA 추가 물량 협상은 캐파 확장 결정에 어떤 영향을 주나?", "expect_docs": ["2026-05-20-capex", "nvidia_demand"], "expect_terms": ["NVIDIA"]},
    {"q": "글로벌 발표 일정은 언제인가?", "expect_docs": ["2026-05-20-capex"], "expect_terms": ["7월 18일"]},
    {"q": "5월 21일 CFO 일정 중 NVIDIA 관련 미팅은?", "expect_docs": ["schedule/2026-05-21"], "expect_terms": ["11:30"]},
    {"q": "High-NA EUV 장비 가격과 처리량은?", "expect_docs": ["긴급브리핑", "논문1"], "expect_terms": ["3억"]},
    {"q": "GAA 도입에서 삼성과 TSMC 전략 차이는?", "expect_docs": ["긴급브리핑", "논문1"], "expect_terms": ["수율"]},
    {"q": "HfO2 유전막의 EOT 는 얼마인가?", "expect_docs": ["긴급브리핑", "논문3"], "expect_terms": ["2.5"]},
    {"q": "사외이사 박ㅇㅇ의 관심사는?", "expect_docs": ["2026-05-15-board-routine", "2026-05-22-launch-ir"], "expect_terms": ["가이던스", "ROI"]},
]


def load_questions(path: Optional[str] = None) -> List[Dict[str, Any]]:
    if path and not os.path.exists(path):
        raise FileNotFoundError("questions file not found: %s" % path)
    path = path or EVAL_PATH
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    # 평가셋이 없으면 샘플 모뎀 코퍼스 질문(있을 때) → 내장 기본 질문으로 생성
    qs: List[Dict[str, Any]] = list(DEFAULT_QUESTIONS)
    if os.path.exists(SAMPLE_QUESTIONS):
        try:
            with open(SAMPLE_QUESTIONS, "r", encoding="utf-8") as f:
                qs = json.load(f)
        except Exception:
            pass
    from . import atomicio
    atomicio.write_json(path, qs)
    return qs


def from_query_log(store, days: float = 7.0, limit: int = 30, only: str = "",
                   min_len: int = 4) -> Dict[str, Any]:
    """**실제 질의 이력**에서 문항을 만든다 (2026-09-20).

    왜 필요한가: 평가셋(`eval/questions.json`)은 사람이 미리 적어 둔 고정 목록이라
    (a) 실제로 사람들이 무엇을 묻는지와 다르고 (b) 이 저장소에서는 질문 파일 자체가 코퍼스에 색인돼 있어
    `hit@k` 가 "오염을 얼마나 피했나" 를 재고 있다(`eval --check`). 실제 질의로 돌리면 둘 다 피한다.

    **정답이 없다.** 그래서 만들어지는 문항에는 `expect_docs`/`expect_terms` 가 없고, hit@k·term_recall 은
    계산되지 않는다. 대신 지연·토큰·근거 부족률·단계별 비용을 진짜 질문 위에서 잰다.

    only: ""(전부) | "negative"(👎 만) | "feedback"(평가가 달린 것만) | "insufficient"(근거를 못 찾은 것만)
    같은 질문은 한 번만 넣고, 최근 것부터 고른다.
    """
    import time as _t
    since = _t.time() - max(0.0, float(days)) * 86400.0
    # 거르기를 걸었으면 **넓게 훑는다**. 문제 질의(👎·근거 약함·근거 못 찾음)는 드물어서 최근 200건
    # 안에 없을 수 있다 — 좁게 훑으면 "그런 질의가 없다" 고 잘못 답하게 된다 (2026-09-20).
    scan = max(limit * 8, 2000 if only else 200)
    rows = store.queries(scan)
    out: List[Dict[str, Any]] = []
    seen = set()
    for r in rows:
        if len(out) >= max(1, int(limit)):
            break
        if float(r.get("ts") or 0) < since:
            continue
        q = str(r.get("query") or "").strip()
        if len(q) < min_len:
            continue
        key = q.lower()
        if key in seen:
            continue
        fb = r.get("feedback")
        if only == "negative" and not (isinstance(fb, (int, float)) and fb < 0):
            continue
        if only == "feedback" and fb is None:
            continue
        if only == "insufficient" and _verdict_of(r) != "insufficient":
            continue
        if only == "weak" and _verdict_of(r) not in ("weak", "insufficient"):
            continue
        seen.add(key)
        out.append({"q": q, "from_query_id": r.get("id"), "feedback": fb, "ts": r.get("ts"),
                    "user": r.get("user"), "origin": r.get("origin"),
                    # 고르는 화면이 "이건 볼 만한 질의인가" 를 판단할 재료 (2026-09-20).
                    # `ms` 는 query_log 에 없다 — 넣어 봤자 빈 칸이라 화면이 고장난 것처럼 보인다.
                    "verdict": _verdict_of(r), "insufficient": _verdict_of(r) == "insufficient",
                    "groundedness": _scores_of(r).get("groundedness"),
                    "n_hits": _scores_of(r).get("n_hits")})
    return {"questions": out, "source": {"kind": "queries", "days": days, "only": only or "all",
                                         "n": len(out), "requested": int(limit),
                                         "note": "실제 질의 이력 — 정답이 없어 hit@k·mrr·term_recall 은 계산되지 않습니다"}}


def _scores_of(row) -> Dict[str, Any]:
    """질의 로그 행의 `scores` (문자열로 저장돼 있을 수 있다).

    실제로 들어 있는 키는 `top_fused` · `n_hits` · **`verdict`** · `groundedness` 다.
    `answer_mode` 는 **여기 없다** — 요청 결과(`requests.result`)에만 있다. 2026-09-20 에
    `only=insufficient` 필터가 `answer_mode` 를 보고 있어 **항상 0건**이었다(필터가 죽어 있었다).
    """
    sc = row.get("scores")
    if isinstance(sc, str):
        try:
            sc = json.loads(sc)
        except ValueError:
            sc = {}
    return sc if isinstance(sc, dict) else {}


def _verdict_of(row) -> str:
    """근거 판정 — `sufficient` | `weak` | `insufficient` (없으면 "")."""
    sc = _scores_of(row)
    return str(sc.get("verdict") or sc.get("answer_mode") or "")


def pick_questions(store, ids) -> Dict[str, Any]:
    """**사람이 직접 고른** 질의 이력으로 문항을 만든다 (query_log.id 목록).

    왜 필요한가 (2026-09-20 요청: *"과거 질의를 어디서 선택할 수 있어? 선택을 해야 비교를 하지"*):
    `from_query_log` 는 기간·건수·피드백으로 **뭉뚱그려** 고른다. 그런데 비교하고 싶은 질의는 대개
    몇 개로 정해져 있다 — "이 세 질문이 느린데 설정을 바꾸면 나아지나". 고르지 못하면 비교가
    내 관심사와 상관없는 문항 위에서 돌아간다.

    정답은 여전히 없다(질의 이력이므로) — `kind="list"` 로 표시해 화면·CLI 가 같은 안내를 낸다.
    """
    want = [int(x) for x in (ids or []) if str(x).strip()]
    if not want:
        return {"questions": [], "source": {"kind": "list", "n": 0, "note": "고른 질의가 없습니다"}}
    rows = {int(r["id"]): r for r in store.queries(5000) if r.get("id") is not None}
    out, missing = [], []
    for qid in want:
        r = rows.get(qid)
        if not r:
            missing.append(qid)
            continue
        out.append({"q": str(r.get("query") or "").strip(), "from_query_id": qid,
                    "feedback": r.get("feedback"), "ts": r.get("ts"),
                    "user": r.get("user"), "origin": r.get("origin"),
                    "verdict": _verdict_of(r), "insufficient": _verdict_of(r) == "insufficient",
                    "groundedness": _scores_of(r).get("groundedness")})
    return {"questions": out,
            "source": {"kind": "list", "n": len(out), "picked": want, "missing": missing,
                       "note": "직접 고른 질의 이력 — 정답이 없어 hit@k·mrr·term_recall 은 계산되지 않습니다"}}


def health(pipe, questions: Optional[List[Dict[str, Any]]] = None, probe: int = 12) -> Dict[str, Any]:
    """**이 평가 숫자를 믿어도 되나** — 점수를 내기 전에 평가셋과 색인의 상태를 점검한다 (2026-09-19).

    왜 필요한가: 평가는 "몇 점인가" 를 알려 주지만 "그 점수가 뜻이 있나" 는 알려 주지 않는다.
    실제로 이 저장소의 개발 환경에서 `hit@k` 가 0.6 으로 나왔는데, 원인은 검색 품질이 아니라
    **평가셋 파일 자신이 코퍼스에 색인돼 있어서** 질문과 글자 그대로 일치하는 그 파일이 정답 문서를
    밀어낸 것이었다(`corpus/imported/eval/NOTE-questions.md` 가 상위 6건을 독식). 그 상태에서 튜닝을
    시작하면 며칠을 허비한다. 그래서 점수 옆에 **신뢰도**를 함께 낸다.

    검사 넷
      contaminated : 질문 문장이 거의 그대로 들어 있는 문서가 색인에 있다 (평가셋 유출 = 치명적)
      missing_docs : 기대 문서가 색인에 없다 (그 문항은 영원히 실패한다 — 평가가 아니라 오류)
      missing_terms: 기대 용어가 코퍼스 어디에도 없다 (같은 이유)
      too_small    : 문항 수가 적어 지표 한 칸이 너무 크다 (1문항 = 1/n 이라 작은 차이를 구분 못 한다)

    반환: {ok, level(ok|warn|bad), n, issues:[{kind, level, detail, questions:[...]}], checked}
    """
    from .profiler import Profiler
    from .retrieval import fts_search
    from .textutil import keywords
    qs = questions or load_questions()
    store = pipe.store
    issues: List[Dict[str, Any]] = []
    n = len(qs)

    docs = [d["doc_id"] for d in store.list_docs()]
    meta = store.doc_meta_map()
    by_ext: Dict[str, List[str]] = {}
    for d, m in meta.items():
        if m.get("ext_id"):
            by_ext.setdefault(str(m["ext_id"]), []).append(d)

    def resolve(expect: str) -> List[str]:
        return by_ext.get(expect) or [d for d in docs if expect.lower() in d.lower()]

    miss_docs = [(q.get("q", ""), e) for q in qs for e in (q.get("expect_docs") or []) if not resolve(e)]
    if miss_docs:
        issues.append({"kind": "missing_docs", "level": "bad",
                       "detail": "기대 문서 %d개가 색인에 없습니다 — 그 문항은 무엇을 해도 실패합니다. 평가셋을 고치거나 문서를 색인하세요." % len(miss_docs),
                       "questions": ["%s → %s" % (a[:40], b) for a, b in miss_docs[:8]]})

    # 오염: 질문의 핵심 키워드가 **한 문서에** 몰려 있고 그 문서가 기대 문서가 아니면 평가셋 유출을 의심한다.
    prof = Profiler("evalcheck", log=False)
    contam: Dict[str, int] = {}
    hits_for: List[str] = []
    for q in qs[:probe]:
        text = str(q.get("q") or "")
        kws = keywords(text)[:8]
        if len(kws) < 2:
            continue
        want = {d for e in (q.get("expect_docs") or []) for d in resolve(e)}
        try:
            rows = fts_search(store, " ".join(kws), 3, {}, prof)
        except Exception:
            continue
        for cid, _sc, _sn in rows[:2]:
            doc = str(cid).rsplit("#", 1)[0]
            if doc in want:
                continue
            # 그 문서가 **질문 자체**를 담고 있는가 (질문 문장의 절반 이상이 그대로)
            try:
                c = store.get_chunk(cid)
                body = (dict(c).get("text") or "") if c else ""
            except Exception:
                body = ""
            if not body:
                continue
            same = sum(1 for w in kws if w.lower() in body.lower())
            if same >= max(2, int(len(kws) * 0.6)):
                contam[doc] = contam.get(doc, 0) + 1
                hits_for.append(text[:40])
    if contam:
        top = sorted(contam.items(), key=lambda kv: -kv[1])[:5]
        issues.append({"kind": "contaminated", "level": "bad",
                       "detail": "질문과 거의 같은 글이 들어 있는 문서가 검색 상위를 차지합니다 — **평가셋이 코퍼스에 색인된 것**으로 보입니다. "
                                 "이 상태의 hit@k·MRR 은 검색 품질이 아니라 '오염을 얼마나 벗어나는가' 를 잽니다. "
                                 "그 문서를 코퍼스에서 빼고(`corpus_exclude`) 다시 빌드한 뒤 평가하세요.",
                       "questions": ["%s (문항 %d개에서 상위)" % (d, c) for d, c in top]})

    if n < 30:
        issues.append({"kind": "too_small", "level": "warn" if n >= 15 else "bad",
                       "detail": "문항이 %d개뿐이라 지표 한 칸이 %.3f 입니다 — 이보다 작은 차이는 우연과 구분할 수 없습니다. "
                                 "튜닝 비교에 쓰려면 30문항 이상을 권합니다." % (n, 1.0 / max(1, n)),
                       "questions": []})

    level = "bad" if any(i["level"] == "bad" for i in issues) else ("warn" if issues else "ok")
    return {"ok": level == "ok", "level": level, "n": n, "issues": issues,
            "checked": {"docs_indexed": len(docs), "expect_docs": sum(len(q.get("expect_docs") or []) for q in qs),
                        "probed": min(probe, n)},
            "path": EVAL_PATH}


def discriminating(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """어떤 지표가 지금 **변별력이 있나** — 전 문항이 같은 값이면 그 지표로는 튜닝 효과를 볼 수 없다.

    실제로 이 저장소에서 `term_recall` 이 25문항 모두 1.0 이었다. 요약에 다른 지표와 같은 크기로 찍히니
    사람은 "용어 재현 100%" 를 성과로 읽지만, 실은 **무엇을 바꿔도 안 움직이는 눈금**이다.
    """
    out: Dict[str, Any] = {}
    for m in ("hit", "rr", "term_recall", "answer_term_recall"):
        vals = [r.get(m) for r in rows if r.get(m) is not None]
        if not vals:
            continue
        uniq = len({round(float(v), 4) if not isinstance(v, bool) else bool(v) for v in vals})
        out[m] = {"unique": uniq, "n": len(vals), "useful": uniq > 1,
                  "note": "" if uniq > 1 else "모든 문항이 같은 값 — 이 지표로는 변화를 볼 수 없습니다"}
    return out


AUX_WHY = ("doc_expand", "neighbor")


def primary_hits(hits: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """검색 순위(hit@k·MRR)에 쓰는 '주 후보' — doc_expand/neighbor 로 덧붙은 보조 청크는 순위에서 뺀다(부모 뒤에 끼어들어 k 를 잠식하지 않도록)."""
    return [h for h in hits if not any(w in AUX_WHY for w in (h.get("why") or []))]


def score_result(question: Dict[str, Any], hits: List[Dict[str, Any]], chunks: Dict[str, Any], answer: str, k: int = 5
                 ) -> Dict[str, Any]:
    """hit@k/MRR 은 주 후보 상위 k 로, term_recall 은 '주 후보 상위 k + 그 부모에 붙은 보조 청크(컨텍스트 포함)' 의 본문으로 계산한다."""
    exp_docs = question.get("expect_docs", [])
    prim = primary_hits(hits)
    rank = None
    for i, h in enumerate(prim[:k]):
        if any(e in h["chunk_id"] for e in exp_docs):
            rank = i + 1
            break
    top_ids = {h["chunk_id"] for h in prim[:k]}
    scope = list(prim[:k]) + [h for h in hits if any(w in AUX_WHY for w in (h.get("why") or [])) and h.get("in_context")
                              and (h.get("parent") in top_ids or h.get("parent") is None)]
    ctx_text = " ".join((chunks.get(h["chunk_id"], {}) or {}).get("text", "") if isinstance(chunks.get(h["chunk_id"]), dict)
                        else (chunks[h["chunk_id"]]["text"] if h["chunk_id"] in chunks else "") for h in scope)
    terms = question.get("expect_terms", [])
    term_hit = sum(1 for t in terms if t in ctx_text) / max(1, len(terms))
    ans_hit = sum(1 for t in terms if t in (answer or "")) / max(1, len(terms))
    return {"hit": rank is not None, "rank": rank, "rr": (1.0 / rank) if rank else 0.0, "term_recall": round(term_hit, 3),
            "answer_term_recall": round(ans_hit, 3)}


def aggregate(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    n = max(1, len(rows))

    def avg(key):
        # None 은 '뜻이 없음'(검색 전용 모드의 답변 지표) — 0 으로 세면 '나빠졌다' 로 잘못 읽힌다.
        vals = [float(r[key]) for r in rows if r.get(key) is not None]
        return round(sum(vals) / len(vals), 3) if vals else None
    return {"n": len(rows), "hit@k": round(sum(1 for r in rows if r["hit"]) / n, 3),
            "mrr": round(sum(r["rr"] for r in rows) / n, 3),
            "term_recall": avg("term_recall"),
            "answer_term_recall": avg("answer_term_recall")}
