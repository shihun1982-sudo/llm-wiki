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
    return {"n": len(rows), "hit@k": round(sum(1 for r in rows if r["hit"]) / n, 3),
            "mrr": round(sum(r["rr"] for r in rows) / n, 3),
            "term_recall": round(sum(r["term_recall"] for r in rows) / n, 3),
            "answer_term_recall": round(sum(r["answer_term_recall"] for r in rows) / n, 3)}
