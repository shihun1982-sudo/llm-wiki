"""질의 **단계 재실행** — 저장해 둔 중간 결과로 특정 단계부터 다시 돌린다.

왜 있나
------
한 질의의 53초 중 `answer_llm` 이 61%, `claim_check` 가 25% 다. 프롬프트 한 줄이나 튜닝 값
하나를 바꿔 볼 때마다 검색부터 전부 다시 도는 것은 시간 낭비일 뿐 아니라 **원인을 흐린다** —
LLM 확장·리랭크가 매번 미묘하게 달라지므로, 답이 바뀐 이유가 내가 바꾼 값 때문인지 검색이
달라져서인지 알 수 없다. 앞 단계를 **그대로 재생**하면 바꾼 값의 효과만 분리해서 볼 수 있다.

어떻게 — '이어서'가 아니라 '재생(replay)'
------------------------------------------
중간 상태를 통째로 복원해 파이프라인 중간부터 이어 붙이는 방식(resume)은 `run()` 을 단계별
함수로 쪼개는 큰 수술이 필요하고, 그 수술은 제품의 가장 뜨거운 경로에 회귀를 부른다.
그래서 여기서는 **파이프라인은 처음부터 다시 돌되, 재시작점 앞의 단계는 계산 대신 저장해 둔
값을 돌려주는** 방식을 쓴다. 이 선택의 이득:

- `run()` 구조를 바꾸지 않는다 (각 단계 앞에 "재생할 값이 있으면 그것을 쓴다" 분기만 넣는다).
- trace 가 온전히 남는다 — 어떤 단계를 재생했고(`replayed: true`) 어떤 단계를 다시 계산했는지
  화면에서 그대로 보인다.
- 재시작점 **뒤의** 단계는 지금 설정으로 돈다. 그게 목적이다.

무엇을 저장하나
---------------
청크 본문은 저장하지 않는다 (색인에서 id 로 다시 읽으면 된다). 저장하는 것은 **순서와 점수**,
그리고 색인에 없는 것들(외부 RAG 가상 청크, 만들어진 컨텍스트 텍스트, 답변)뿐이다.
따라서 파일 하나가 보통 수십 KB 다.

색인이 다시 만들어지면 청크 id 가 더 이상 유효하지 않을 수 있다. 그래서 `build_version` 을
함께 적어 두고, 다를 때는 재생을 **거부**한다 (엉뚱한 근거로 답을 만들어 내는 것보다 낫다).

설정 (`config.json`)
--------------------
| 키 | 기본 | 뜻 |
|---|---|---|
| `toggles.rerun_capture` | `true` | 질의마다 재실행용 중간 결과를 남긴다 |
| `rerun_dir` | `data/reruns` | 저장 폴더 |
| `rerun_keep` | `50` | 최근 몇 건을 남길지 (넘으면 오래된 것부터 지움) |
| `rerun_max_mb` | `4` | 한 건의 상한. 넘으면 저장하지 않는다 (trace 에 이유가 남는다) |
"""
from __future__ import annotations

import json
import os
import time
from typing import Any, Dict, List, Optional, Tuple

VERSION = 1

# ---------------------------------------------------------------- 재시작점
# (id, 라벨, 이 지점부터 다시 돌 때 **재생**해야 하는 것)
POINTS: List[Dict[str, Any]] = [
    {"id": "plan", "label": "계획부터 (질의 규칙 · 라우터 · 질의 확장)",
     "note": "질의 문자열만 재사용한다. 사실상 전체 재실행이다.", "needs": []},
    {"id": "retrieve", "label": "검색부터 (FTS · 벡터 · 그래프)",
     "note": "계획(확장 질의·라우팅·pin)을 재생하고 검색부터 다시 한다.", "needs": ["plan"]},
    {"id": "rrf_fuse", "label": "융합부터 (RRF)",
     "note": "채널별 후보 목록을 재생한다. 융합 가중치·방식을 바꿔 볼 때.", "needs": ["plan", "lists"]},
    # 뒤로 갈수록 재생하는 것이 **누적**된다. 예를 들어 '리랭크부터' 는 융합 결과가 어차피 부스트 결과로
    # 덮어써지므로 융합까지 재생한다 — 그러지 않으면 결과에 쓰이지도 않을 융합을 다시 계산하게 된다.
    {"id": "boost", "label": "부스트부터",
     "note": "융합 결과를 재생한다. 시간·문서종류·pin 가중을 바꿔 볼 때.", "needs": ["plan", "lists", "fused"]},
    {"id": "rerank", "label": "리랭크부터",
     "note": "부스트까지의 순위를 재생한다. 리랭크 모델·후보 수를 바꿔 볼 때.", "needs": ["plan", "lists", "fused", "boosted"]},
    {"id": "doc_expand", "label": "문서 단위 확장부터",
     "note": "리랭크 결과를 재생한다.", "needs": ["plan", "lists", "fused", "boosted", "reranked"]},
    {"id": "context", "label": "컨텍스트 구성부터",
     "note": "최종 근거 목록을 재생한다. 컨텍스트 길이·이웃 청크를 바꿔 볼 때.", "needs": ["plan", "lists", "fused", "boosted", "reranked"]},
    {"id": "answer_llm", "label": "답변 생성부터",
     "note": "검색·컨텍스트를 모두 재생하고 **답변만** 다시 만든다. 프롬프트·모델·effort 실험에 가장 쓸모 있다.",
     "needs": ["plan", "ctx"]},
    {"id": "claim_check", "label": "근거 검증부터",
     "note": "답변까지 재생하고 검증만 다시 한다.", "needs": ["plan", "ctx", "answer"]},
]
POINT_IDS = [p["id"] for p in POINTS]
_ORDER = {p["id"]: i for i, p in enumerate(POINTS)}

# trace 의 단계 이름 → 재시작점. 화면의 ⟲ 버튼이 이 표로 "이 단계부터" 를 정한다.
STAGE_POINT: Dict[str, str] = {
    "time_scope": "plan", "query_rules": "plan", "router": "plan", "query_expand": "plan", "pins": "plan",
    "expansion_profile": "retrieve",
    "fts_search": "retrieve", "fts_search_rules": "retrieve", "fts_search_alt": "retrieve",
    "fts_search_related": "retrieve", "vector_search": "retrieve", "graph_search": "retrieve",
    "doc_vector_search": "retrieve", "external_rag": "retrieve", "mcp_enrich": "retrieve",
    "rrf_fuse": "rrf_fuse",
    "boost": "boost", "external_inject": "boost",
    "rerank": "rerank", "rerank_llm": "rerank",
    # 2026-09-18 RRF 뒤 LLM 두 단계. 둘 다 '리랭크부터' 에서 **다시 계산**된다 — `boosted` 저장본은 순수 부스트 결과이고
    # fusion_llm 의 효과는 그 뒤의 `reranked`/`final` 저장본 안에 들어 있으므로, 문서 확장/컨텍스트부터 재실행할 때는
    # 두 단계 모두 재생(replayed)으로 표시된다 (query_engine._fusion_llm / _rerank_review_llm 참고).
    "fusion_llm": "rerank", "rerank_review_llm": "rerank",
    "doc_expand": "doc_expand",
    "context": "context", "evidence_compress": "context",
    "evidence_check": "answer_llm", "answer_llm": "answer_llm", "answer": "answer_llm",
    "answer_insufficient": "answer_llm", "generate_answer": "answer_llm",
    "claim_check": "claim_check",
}


def point_for_stage(stage: str) -> Optional[str]:
    """trace 단계 이름 → 재시작점 id. 모르는 단계는 None (그 줄에는 ⟲ 를 달지 않는다)."""
    s = str(stage or "")
    if s in STAGE_POINT:
        return STAGE_POINT[s]
    for pre in ("fts_search_alt", "fts_search_rel", "vector_alt", "evidence_check_", "fallback"):
        if s.startswith(pre):
            return "retrieve" if not s.startswith("evidence_check") else "answer_llm"
    return None


def point_info(pid: str) -> Optional[Dict[str, Any]]:
    return next((p for p in POINTS if p["id"] == pid), None)


def at_or_after(point: str, other: str) -> bool:
    """`point` 에서 재시작할 때 `other` 단계가 재시작점 **이후**(= 다시 계산해야 하는지)인가."""
    return _ORDER.get(other, 99) >= _ORDER.get(point, 99)


def replays(point: str, what: str) -> bool:
    """`point` 에서 재시작할 때 `what`(= POINTS[].needs 의 항목)을 재생해야 하는가."""
    p = point_info(point)
    return bool(p and what in p["needs"])


# ---------------------------------------------------------------- 캡처
class Capture:
    """한 질의가 도는 동안 중간 결과를 모은다. 실패해도 질의를 망가뜨리지 않는다 (모든 기록은 best-effort)."""

    def __init__(self, query: str, build_version: str = "", run_id: str = ""):
        self.d: Dict[str, Any] = {"version": VERSION, "query": query, "build_version": build_version,
                                  "run_id": run_id, "created": time.time()}
        self.ok = True

    def put(self, key: str, value: Any) -> None:
        try:
            self.d[key] = value
        except Exception:
            self.ok = False

    def hits(self, key: str, hits: Any) -> None:
        """Hit 목록을 순서·점수만 남겨 저장한다 (본문은 색인에서 다시 읽는다)."""
        try:
            self.d[key] = [h.to_dict() for h in hits]
        except Exception:
            self.ok = False

    def to_json(self) -> Dict[str, Any]:
        return self.d


def _hit_from_dict(d: Dict[str, Any]):
    from .retrieval import Hit
    h = Hit(str(d.get("chunk_id") or ""))
    h.scores = dict(d.get("scores") or {})
    h.ranks = dict(d.get("ranks") or {})
    h.fused = float(d.get("fused") or 0.0)
    h.why = list(d.get("why") or [])
    h.rerank = d.get("rerank")
    h.boosts = dict(d.get("boosts") or {})
    return h


def hits_from(data: Dict[str, Any], key: str) -> List[Any]:
    return [_hit_from_dict(x) for x in (data.get(key) or []) if isinstance(x, dict)]


# ---------------------------------------------------------------- 저장·조회
def rerun_dir(s: Any) -> str:
    """저장 폴더의 절대 경로. `Settings.rerun_capture_dir()` 을 따른다 (`data/…` → data_dir 아래)."""
    fn = getattr(s, "rerun_capture_dir", None)
    if callable(fn):
        return fn() or os.path.join(getattr(s, "data_dir", "data"), "reruns")
    return os.path.join(getattr(s, "data_dir", "data"), "reruns")


def path_for(s: Any, request_id: Any) -> str:
    return os.path.join(rerun_dir(s), "req_%s.json" % request_id)


def save(s: Any, request_id: Any, cap: Capture) -> Dict[str, Any]:
    """중간 결과를 파일로. 반환값은 trace 에 적을 요약(저장했는지·왜 안 했는지)."""
    if request_id is None:
        return {"saved": False, "reason": "request_id 없음 (기록하지 않는 질의)"}
    try:
        blob = json.dumps(cap.to_json(), ensure_ascii=False)
    except Exception as e:
        return {"saved": False, "reason": "직렬화 실패: %s" % str(e)[:80]}
    limit = int(float(getattr(s, "rerun_max_mb", 4) or 4) * 1024 * 1024)
    if len(blob.encode("utf-8", "ignore")) > limit:
        return {"saved": False, "reason": "%.1fMB > rerun_max_mb" % (len(blob) / 1e6)}
    d = rerun_dir(s)
    try:
        os.makedirs(d, exist_ok=True)
        p = os.path.join(d, "req_%s.json" % request_id)
        tmp = p + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(blob)
        os.replace(tmp, p)
    except OSError as e:
        return {"saved": False, "reason": "쓰기 실패: %s" % str(e)[:80]}
    pruned = prune(s)
    return {"saved": True, "bytes": len(blob), "pruned": pruned}


def load(s: Any, request_id: Any) -> Optional[Dict[str, Any]]:
    try:
        with open(path_for(s, request_id), encoding="utf-8") as f:
            d = json.load(f)
    except (OSError, ValueError):
        return None
    return d if isinstance(d, dict) and d.get("version") == VERSION else None


def have(s: Any, request_id: Any) -> bool:
    return os.path.exists(path_for(s, request_id))


def prune(s: Any) -> int:
    """최근 `rerun_keep` 건만 남긴다. 지운 개수를 돌려준다."""
    keep = int(getattr(s, "rerun_keep", 50) or 0)
    if keep <= 0:
        return 0
    d = rerun_dir(s)
    try:
        files = [(os.path.getmtime(os.path.join(d, n)), os.path.join(d, n))
                 for n in os.listdir(d) if n.startswith("req_") and n.endswith(".json")]
    except OSError:
        return 0
    files.sort(reverse=True)
    n = 0
    for _, p in files[keep:]:
        try:
            os.remove(p)
            n += 1
        except OSError:
            pass
    return n


def list_saved(s: Any, limit: int = 50) -> List[Dict[str, Any]]:
    d = rerun_dir(s)
    out: List[Dict[str, Any]] = []
    try:
        names = [n for n in os.listdir(d) if n.startswith("req_") and n.endswith(".json")]
    except OSError:
        return out
    for n in names:
        p = os.path.join(d, n)
        try:
            out.append({"request_id": n[4:-5], "bytes": os.path.getsize(p), "mtime": os.path.getmtime(p)})
        except OSError:
            pass
    out.sort(key=lambda x: -x["mtime"])
    return out[:limit]


# ---------------------------------------------------------------- 재생 계획
class Resume:
    """한 번의 재실행 동안 '무엇을 재생할지' 를 들고 다니는 객체.

    `QueryEngine` 은 각 단계 앞에서 `use("lists")` 같은 질문을 던지고, 돌려받은 값이 있으면
    계산을 건너뛴다. 첫 검색 라운드에만 적용된다 — fallback 라운드는 **다시 계산**해야
    바뀐 설정이 반영되기 때문이다.
    """

    def __init__(self, point: str, data: Dict[str, Any]):
        self.point = point
        self.data = data or {}
        self.used: Dict[str, bool] = {}
        self.round = 0            # _retrieve 호출 횟수 (0 번째에만 재생)

    @property
    def query(self) -> str:
        return str(self.data.get("query") or "")

    def wants(self, what: str) -> bool:
        return replays(self.point, what)

    def get(self, what: str, key: Optional[str] = None) -> Any:
        """재생할 값. 재생 대상이 아니거나 저장돼 있지 않으면 None."""
        if not self.wants(what):
            return None
        v = self.data.get(key or what)
        if v is not None:
            self.used[what] = True
        return v

    def summary(self) -> Dict[str, Any]:
        p = point_info(self.point) or {}
        return {"from": self.point, "label": p.get("label", self.point), "replayed": sorted(self.used)}


def check_compatible(data: Dict[str, Any], build_version: str) -> Tuple[bool, str]:
    """저장된 중간 결과를 지금 색인에 쓸 수 있는가."""
    if not data:
        return False, "저장된 중간 결과가 없습니다 (질의 당시 rerun_capture 가 꺼져 있었거나 정리되었습니다)"
    bv = str(data.get("build_version") or "")
    if bv and build_version and bv != build_version:
        return False, ("색인이 그때와 다릅니다 (저장 %s → 현재 %s). 청크 id 가 달라졌을 수 있어 재생하지 않습니다 — "
                       "'계획부터' 로 전체 재실행하세요." % (bv[:12], build_version[:12]))
    return True, ""
