# -*- coding: utf-8 -*-
"""Self-evolving 레이어.

목표: 사용 중 발견되는 데이터/인덱스 결함을 *제안(proposal)* 으로 축적하고, 통제된 절차로 반영한다.

  capture  : 질의 실행 시 자동 — 검색 점수 낮음 / 그래프 미매칭 / 인용 없음 등을 '갭' 으로 기록하고 제안 생성
  feedback : 사용자 👍/👎 + 정정 텍스트 → 정정 텍스트는 wiki 편집 노트(overlay 문서) 제안
  llm_review (옵션) : LLM 이 질의 로그를 검토해 별칭/동의어/관계 제안 생성
  apply    : 제안 적용 = (1) 스냅샷 (2) 스테이징 적용 (3) 회귀 평가 (4) 악화 시 롤백, 개선/동등 시 승격
             기본은 human-in-the-loop (status=proposed → 사용자가 approve) 이며 evolve_auto_apply 가 켜지면
             confidence ≥ evolve_min_confidence 인 제안만 자동 적용된다.

제안 kind
  synonym        {"term","expansion"}                → FTS 질의 확장 (synonyms 테이블)
  alias          {"entity","alias"}                  → 규칙 사전(data/rules.json) 별칭 추가 → 그래프 재빌드 필요
  entity         {"name","type","aliases"}           → 규칙 사전에 새 엔티티 추가
  relation       {"src","dst","rel","description"}   → 그래프에 관계 직접 추가 (source=evolve)
  wiki_note      {"page","note"}                     → wiki 페이지 편집 노트 추가 → 다음 빌드에서 overlay 색인
  chunk_params   {"chunk_max_chars","chunk_overlap_chars"} → 설정 변경 + 전체 리빌드

보안 원칙: 제안은 *데이터* 만 바꾼다(코드/프롬프트 불변). 모든 적용은 evolution_log 에 체크섬과 함께 남고 롤백 가능.
"""
from __future__ import annotations

import json
import os
import shutil
import time
from typing import Any, Dict, List, Optional

from .config import save_settings
from .graph_rules import load_rules, save_rules, entity_id_for
from .providers import parse_json, LLMError
from .textutil import keywords, sha1
from .wiki import NOTE_MARK, slug

def REVIEW_SYSTEM() -> str:   # prompts/review.md
    from . import prompts as _prompts
    return _prompts.get("review")


# ------------------------------------------------------------------ capture
def capture_query(pipe, q: str, result: Dict[str, Any], final: List[Any]) -> List[int]:
    """질의 결과에서 갭을 탐지해 제안을 만든다. 반환: 생성된 proposal id 목록."""
    store, s = pipe.store, pipe.s
    ids: List[int] = []
    top = final[0].fused if final else 0.0
    kws = keywords(q)
    # 1) 그래프가 켜져 있는데 시드 엔티티가 하나도 없으면 → 질의 키워드를 새 엔티티/별칭 후보로
    if s.toggles.graph and not result.get("graph", {}).get("seeds"):
        ent_names = {e["name"].lower(): e for e in store.entity_index()}
        for k in kws[:4]:
            if len(k) < 2 or k.isdigit():
                continue
            # 이미 있는 엔티티의 부분 문자열이면 alias 제안, 아니면 entity 후보 (낮은 confidence)
            parent = next((e for n, e in ent_names.items() if k in n and k != n), None)
            if parent:
                ids.append(store.add_proposal("alias", {"entity": parent["name"], "alias": k},
                                              "질의 '%s' 가 그래프 엔티티에 매칭되지 않음; '%s' 는 '%s' 의 별칭일 가능성" % (q, k, parent["name"]),
                                              0.55, "capture"))
            elif _term_in_corpus(store, k):
                ids.append(store.add_proposal("entity", {"name": k, "type": "concept", "aliases": []},
                                              "질의 키워드 '%s' 가 코퍼스에 존재하나 엔티티 사전에 없음" % k, 0.45, "capture"))
    # 2) 검색 점수가 임계 이하 → synonym 후보 (질의 키워드 ↔ 상위 청크 헤딩 키워드)
    if final and top < s.evolve_low_score_threshold and len(final[0].ranks) <= 1:
        c = store.get_chunk(final[0].chunk_id)
        if c:
            hk = [w for w in keywords(c["heading"]) if w not in kws][:2]
            for k in kws[:2]:
                for h in hk:
                    ids.append(store.add_proposal("synonym", {"term": k, "expansion": h},
                                                  "낮은 융합 점수(%.3f): '%s' 질의가 헤딩 '%s' 문단에서만 약하게 매칭" % (top, k, c["heading"][:40]),
                                                  0.35, "capture"))
    # 3) 답변에 인용이 없음 → 데이터 갭 기록 (wiki_note 제안: 사람이 채우도록)
    if result.get("answer_mode") == "llm" and not result.get("cited"):
        ids.append(store.add_proposal("wiki_note", {"page": "_gaps", "note": "질문 '%s' 에 대한 근거 문서가 없거나 인용되지 않음. 관련 자료를 보강하세요." % q},
                                      "근거 인용 없는 답변", 0.5, "capture"))
    if s.toggles.evolve_auto_apply:
        for pid in ids:
            p = store.get_proposal(pid)
            if p and p["status"] == "proposed" and (p["confidence"] or 0) >= s.evolve_min_confidence:
                apply_proposal(pipe, pid, auto=True)
    return ids


def _term_in_corpus(store, term: str) -> bool:
    return bool(store.fts_search('"%s"' % term.replace('"', ""), 1))


# ------------------------------------------------------------------ feedback
def record_feedback(pipe, qid: int, feedback: int, note: str = "") -> Dict[str, Any]:
    store = pipe.store
    store.set_feedback(qid, feedback, note)
    out: Dict[str, Any] = {"query_id": qid, "feedback": feedback, "proposals": []}
    qrow = store.get_query(qid)
    if not qrow:
        return out
    try:
        from . import memory as _mem
        out["episode"] = _mem.on_feedback(store, qid, feedback)
    except Exception:
        pass
    if feedback < 0:
        # 부정 피드백: 정정 텍스트가 있으면 wiki 편집 노트 제안 (사람이 준 정보 = 높은 신뢰도)
        if note.strip():
            page = _page_for_query(pipe, qrow["query"])
            out["proposals"].append(store.add_proposal("wiki_note", {"page": page, "note": "[정정 %s] Q: %s\n%s" % (
                time.strftime("%Y-%m-%d"), qrow["query"], note.strip())}, "사용자 부정 피드백 + 정정 텍스트", 0.9, "feedback"))
        else:
            out["proposals"].append(store.add_proposal("wiki_note", {"page": "_gaps", "note": "[검토 필요] Q: %s — 사용자가 답변을 부정 평가" % qrow["query"]},
                                                       "사용자 부정 피드백", 0.5, "feedback"))
    else:
        # 긍정 피드백: 질의 키워드 ↔ 상위 청크 엔티티 연결 강화 (synonym 제안, 중간 신뢰도)
        top = json.loads(qrow["top_chunks"] or "[]")[:1]
        if top:
            c = store.get_chunk(top[0])
            if c:
                hk = keywords(c["heading"])[:1]
                for k in keywords(qrow["query"])[:1]:
                    if hk and hk[0] != k:
                        out["proposals"].append(store.add_proposal("synonym", {"term": k, "expansion": hk[0]},
                                                                   "긍정 피드백: '%s' → '%s' 문단이 정답" % (k, c["heading"][:40]), 0.6, "feedback"))
    return out


def _page_for_query(pipe, q: str) -> str:
    ents = pipe.store.entities(5000)
    ql = q.lower()
    best = None
    for e in ents:
        if e["type"] in ("date", "amount", "document"):
            continue
        if e["name"].lower() in ql and (best is None or len(e["name"]) > len(best)):
            best = e["name"]
    return slug(best) if best else "_gaps"


# ------------------------------------------------------------------ llm review
def llm_review(pipe, limit: int = 30) -> Dict[str, Any]:
    llm = pipe.llm_for("review")
    if not llm.available:
        return {"error": "LLM provider unavailable", "proposals": []}
    qs = pipe.store.queries(limit)
    lines = []
    for r in qs:
        sc = json.loads(r["scores"] or "{}")
        lines.append("- Q: %s | top_fused=%.3f | feedback=%s | note=%s | A: %s" % (
            r["query"], sc.get("top_fused", 0), r["feedback"], (r["note"] or "")[:80], (r["answer"] or "")[:160].replace("\n", " ")))
    ents = ", ".join(e["name"] for e in pipe.store.entities(80))
    user = "## 기존 엔티티(일부)\n%s\n\n## 질의 로그\n%s" % (ents, "\n".join(lines))
    try:
        r = llm.complete(REVIEW_SYSTEM(), user, max_tokens=pipe.s.role_max_tokens("review", 3000),
                         effort=pipe.s.role_llm("review")["effort"], json_mode=True)
    except LLMError as e:
        return {"error": str(e), "proposals": []}
    data = parse_json(r["text"]) or {}
    ids = []
    for p in data.get("proposals", []):
        if p.get("kind") in ("synonym", "alias", "entity", "relation") and isinstance(p.get("payload"), dict):
            ids.append(pipe.store.add_proposal(p["kind"], p["payload"], p.get("reason", "llm review"),
                                               float(p.get("confidence", 0.5)), "llm_review"))
    return {"proposals": ids, "usage": r.get("usage"), "raw": r["text"][:2000]}


# ------------------------------------------------------------------ apply / rollback
def apply_proposal(pipe, pid: int, auto: bool = False, evaluate: bool = True) -> Dict[str, Any]:
    """스냅샷 → 적용 → (리빌드) → 회귀평가 → 악화 시 롤백."""
    store, s = pipe.store, pipe.s
    p = store.get_proposal(pid)
    if not p:
        return {"error": "no such proposal"}
    if p["status"] == "applied":
        return {"error": "already applied"}
    if p["kind"] == "corpus_gap":
        # 문서 추가로만 해결되는 제안 — 스냅샷/롤백 없이 실패 처리 (사람이 문서를 넣은 뒤 reject 또는 그대로 두기)
        store.set_proposal_status(pid, "failed")
        store.log_evolution(pid, "fail", {"kind": "corpus_gap", "payload": p["payload"], "error": "corpus_gap 은 자동 적용 불가 (문서 추가 필요)"}, "")
        return {"status": "failed", "error": "corpus_gap 은 문서 추가로 해결하는 제안입니다 (자동 적용 불가). 주제: %s" % (p["payload"] or {}).get("topic")}
    before = None
    if evaluate:
        before = pipe.evaluate(log=False)[0]["summary"]
    snap = _snapshot(pipe, pid)
    detail: Dict[str, Any] = {"kind": p["kind"], "payload": p["payload"], "auto": auto}
    need_rebuild = False
    try:
        k, pl = p["kind"], p["payload"]
        if k == "synonym":
            store.add_synonym(pl["term"], pl["expansion"], "evolve:%d" % pid)
            store.commit()
        elif k == "alias":
            rules = load_rules()
            ent = rules["entities"].get(pl["entity"])
            if ent is None:
                raise ValueError("unknown entity %s" % pl["entity"])
            if pl["alias"] not in ent["aliases"]:
                ent["aliases"].append(pl["alias"])
            save_rules(rules)
            need_rebuild = True
        elif k == "entity":
            rules = load_rules()
            rules["entities"].setdefault(pl["name"], {"type": pl.get("type", "concept"), "aliases": list(pl.get("aliases", []))})
            save_rules(rules)
            need_rebuild = True
        elif k == "relation":
            sid, did = _ensure_entity(store, pl["src"]), _ensure_entity(store, pl["dst"])
            store.add_relation(sid, did, pl.get("rel", "related_to"), pl.get("description", ""), float(pl.get("weight", 0.8)),
                               "evolve", float(p["confidence"] or 0.7), "")
            store.update_degrees()
            store.commit()
        elif k == "wiki_note":
            _append_note(s.wiki_dir, pl["page"], pl["note"])
            need_rebuild = True
        elif k == "chunk_params":
            for key in ("chunk_max_chars", "chunk_overlap_chars"):
                if key in pl:
                    setattr(s, key, int(pl[key]))
            save_settings(s)
            need_rebuild = True
        elif k == "pin":       # forensic expect 제안: 문서/청크 고정 근거
            from . import pins as _pins
            _pins.add_pin(doc=pl.get("doc"), chunk=pl.get("chunk"), query=pl.get("query") if not pl.get("keywords") else None, keywords_=pl.get("keywords") or None,
                          always=bool(pl.get("always")), doc_types=pl.get("doc_types"), weight=float(pl.get("weight") or 1.0), note=pl.get("note", "evolve"), source="evolve:%d" % pid)
        elif k == "query_rule":   # forensic 제안: query_rules.json 사전 항목
            from . import query_rules as _qr
            _qr.add_rule(pl.get("type", "synonym"), pl["term"], list(pl.get("values") or ([pl["value"]] if pl.get("value") else [])), "evolve:%d" % pid)
            pipe.reload_tuning()
        elif k == "tuning":       # forensic 제안: 튜닝 값 (config 항목이면 config.json)
            from . import tuning as _tn
            from .config import apply_overrides
            key, val = pl.get("key"), pl.get("value")
            if val is None:
                raise ValueError("tuning 제안에 value 가 없음 (수동 적용: tuning set %s=…)" % key)
            spec = _tn._INDEX.get(key)
            if not spec:
                raise ValueError("unknown tunable %s" % key)
            if spec["source"] == "config":
                apply_overrides(s, {key: val})
                save_settings(s)
            else:
                _tn.T.set(key, val)
                _tn.save_tuning(_tn.T)
            pipe.reload_tuning()
        elif k == "corpus_gap":
            raise ValueError("corpus_gap 은 문서 추가로 해결하는 제안입니다 (자동 적용 불가). 주제: %s" % pl.get("topic"))
        else:
            raise ValueError("unknown kind " + k)
        if need_rebuild:
            pipe.reload()
            pipe.build(full=(k == "chunk_params"))
        after = pipe.evaluate(log=False)[0]["summary"] if evaluate else None
        detail["eval_before"], detail["eval_after"] = before, after
        regressed = bool(before and after and (after["hit@k"] + after["term_recall"]) < (before["hit@k"] + before["term_recall"]) - 1e-9)
        if regressed:
            _restore(pipe, snap)
            store.set_proposal_status(pid, "rejected_regression", before, after)
            store.log_evolution(pid, "rollback", detail, sha1(json.dumps(detail, ensure_ascii=False)))
            return {"status": "rejected_regression", "before": before, "after": after}
        store.set_proposal_status(pid, "applied", before, after)
        store.log_evolution(pid, "apply", detail, sha1(json.dumps(detail, ensure_ascii=False)))
        return {"status": "applied", "before": before, "after": after, "rebuilt": need_rebuild}
    except Exception as e:  # 적용 실패 → 롤백 (복원 후에는 pipe.store 가 새 연결이므로 그것을 쓴다)
        _restore(pipe, snap)
        pipe.store.set_proposal_status(pid, "failed")
        pipe.store.log_evolution(pid, "fail", dict(detail, error=str(e)), "")
        return {"status": "failed", "error": str(e)}


def reject_proposal(pipe, pid: int, note: str = "") -> Dict[str, Any]:
    pipe.store.set_proposal_status(pid, "rejected")
    pipe.store.log_evolution(pid, "reject", {"note": note}, "")
    return {"status": "rejected"}


def _ensure_entity(store, name: str) -> str:
    eid = entity_id_for(name)
    if not store.get_entity(eid):
        hits = store.entity_fts('"%s"' % name.replace('"', ""), 1)
        if hits and hits[0][1] > 3.0:
            return hits[0][0]
        store.upsert_entity(eid, name, "concept", "", [name], "evolve", 0.6)
    return eid


def _append_note(wiki_dir: str, page: str, note: str) -> None:
    os.makedirs(wiki_dir, exist_ok=True)
    path = os.path.join(wiki_dir, slug(page) + ".md")
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            txt = f.read()
        if NOTE_MARK not in txt:
            txt += "\n\n" + NOTE_MARK + "\n\n"
        txt = txt.replace("(이 섹션은 빌드 시 보존됩니다. 사람이 수정/보충한 내용을 적으면 다음 빌드에서 색인됩니다.)", "")
        txt = txt.rstrip() + "\n\n" + note.strip() + "\n"
    else:
        txt = "# %s\n\n%s\n\n%s\n" % (page, NOTE_MARK, note.strip())
    with open(path, "w", encoding="utf-8") as f:
        f.write(txt)


def _snapshot(pipe, pid: int) -> Dict[str, str]:
    """DB / rules.json / wiki / config 스냅샷 (롤백용)."""
    s = pipe.s
    snap_dir = os.path.join(s.data_dir, "snapshots", "p%d_%d" % (pid, int(time.time())))
    os.makedirs(snap_dir, exist_ok=True)
    pipe.store.conn.commit()
    pipe.store.conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    shutil.copy2(s.db_path, os.path.join(snap_dir, "db.sqlite3"))
    from .graph_rules import rules_path as _rules_path
    rules_path = _rules_path()
    if os.path.exists(rules_path):
        shutil.copy2(rules_path, os.path.join(snap_dir, "rules.json"))
    if os.path.isdir(s.wiki_dir):
        shutil.copytree(s.wiki_dir, os.path.join(snap_dir, "wiki"))
    from .config import CONFIG_PATH
    if os.path.exists(CONFIG_PATH):
        shutil.copy2(CONFIG_PATH, os.path.join(snap_dir, "config.json"))
    return {"dir": snap_dir}


def _restore(pipe, snap: Dict[str, str]) -> None:
    """스냅샷으로 되돌린다 (DB 파일 교체 + rules/wiki/config 복원).

    DB 파일 교체는 Store.reopen(before=…) 안에서 한다: 모든 연결을 닫고 → 파일 교체(Windows 잠금 때문에 짧게 재시도) → 다시 연다.
    Store 인스턴스를 갈아끼우지 않으므로, 교체가 실패하더라도 서버는 원래 DB 로 계속 동작한다(예전에는 여기서 실패하면 모든 요청이 죽었다)."""
    s = pipe.s
    d = snap["dir"]

    def swap() -> None:
        shutil.copy2(os.path.join(d, "db.sqlite3"), s.db_path)
        for suffix in ("-wal", "-shm"):
            p = s.db_path + suffix
            if os.path.exists(p):
                os.remove(p)
    pipe.store.reopen(before=swap)
    if os.path.exists(os.path.join(d, "rules.json")):
        from .graph_rules import rules_path as _rules_path
        shutil.copy2(os.path.join(d, "rules.json"), _rules_path())
    if os.path.isdir(os.path.join(d, "wiki")):
        shutil.rmtree(s.wiki_dir, ignore_errors=True)
        shutil.copytree(os.path.join(d, "wiki"), s.wiki_dir)
    from .config import CONFIG_PATH, load_settings
    if os.path.exists(os.path.join(d, "config.json")):
        shutil.copy2(os.path.join(d, "config.json"), CONFIG_PATH)
        ns = load_settings()
        for k, v in ns.to_dict().items():
            if k != "toggles":
                setattr(s, k, v)
    pipe.reload()


def status(pipe) -> Dict[str, Any]:
    st = pipe.store
    return {"pending": st.proposals("proposed"), "recent_log": st.evolution_log(30),
            "applied": st.proposals("applied", 30), "synonyms": st.synonyms(),
            "auto_apply": pipe.s.toggles.evolve_auto_apply, "min_confidence": pipe.s.evolve_min_confidence}
