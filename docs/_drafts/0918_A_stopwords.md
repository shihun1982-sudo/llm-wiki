# 불용어 파일 `stopwords.json` (2026-09-18 요청 2)

> 초안 — README §0 · BRINGUP §3.2 로 병합용. 설계 근거는 [IMPLEMENTATION_PLAN_0918.md §2.2](../IMPLEMENTATION_PLAN_0918.md).

## 무엇인가

질의 키워드 추출(`textutil.keywords()`)과 근거 토큰 비교(`retrieval`·`forensic` 의 `STOPWORDS`)에서 **제거할 단어 목록**이다.
예전에는 `llmwiki/textutil.py` 안에 하드코딩되어 있어 사내 환경에서 "우리 팀 질문에 흔한 군더더기(예: '확인', '부탁')" 를 빼려면 코드를 고쳐야 했다.
이제 프로젝트 루트의 `stopwords.json` 한 파일로 관리하고, 코드의 `DEFAULT_STOPWORDS` 는 파일이 없거나 깨졌을 때의 폴백이다.

질의 규칙(`query_rules.json`)과 파일을 나눈 이유: 질의 규칙은 검색어를 **확장**하고 불용어는 **제거**한다. 성격이 달라 운영자가 찾기 쉽도록 분리했다.

## 파일 형식과 키

```json
{
  "_comment": "설명 (무시됨)",
  "stopwords": ["a", "an", "무엇", "어떻게", "…"]
}
```

| 키 | 형 | 기본 | 뜻 |
|---|---|---|---|
| `stopwords` | 문자열 배열 | 코드 기본 목록 51개 (`textutil.DEFAULT_STOPWORDS`) | 제거할 토큰. 비교는 **소문자 · 조사 제거 뒤** 토큰과 하므로 소문자로 적는다. 앞뒤 공백은 잘리고 빈 문자열은 무시된다 |
| `_comment` | 문자열 | — | 메모. 읽지 않는다 |

배열 형식(`["a", "b"]`)만 있는 파일도 받아들이지만, 예제와 같은 객체 형식을 권장한다.

## 경로

| 항목 | 값 |
|---|---|
| 기본 경로 | `<프로젝트 루트>/stopwords.json` (경로 레지스트리 이름 `stopwords`) |
| 환경변수 | `LLMWIKI_STOPWORDS_PATH=<절대 또는 루트 기준 상대 경로>` |
| 확인 | `python -m llmwiki config paths` → `stopwords  …/stopwords.json` |
| 예제 | `setup/stopwords.example.json` (`copy setup\stopwords.example.json stopwords.json`) |

## 동작

- **없으면 생성**: 첫 질의(또는 `check_env.py`) 때 코드 기본 목록으로 파일을 만든다(`atomicio.write_json`, 원자적 저장). 복사는 선택.
- **재시작 없이 반영**: 파일 mtime 을 캐시하므로 저장하면 다음 `keywords()` 호출부터 새 목록을 쓴다(`prompts/*.md` 와 같은 방식). 서버·MCP 재시작 불필요.
- **깨진 파일**: JSON 오류, `stopwords` 가 배열이 아님, 문자열이 아닌 항목 → `logs/llmwiki.log` 에 `warning` 한 줄(같은 mtime 에 대해 한 번만) 남기고 **코드 기본 목록**으로 동작한다. 파일은 덮어쓰지 않으므로 고친 뒤 저장하면 바로 복구된다.
- **호환**: `from llmwiki.textutil import STOPWORDS` 로 쓰던 코드는 그대로 동작한다(`tok in STOPWORDS`·`len`·반복이 항상 현재 파일 집합을 본다).

## 검증

```powershell
python -m unittest tests.test_stopwords -v        # 생성 · 수정 반영 · 깨진 파일 폴백 3항목
python setup/check_env.py | Select-String stopwords   # [OK  ] stopwords.json 있음 — 불용어 51개 (코드 기본값)
python -c "from llmwiki.textutil import keywords; print(keywords('PDCCH 디코딩 실패는 무엇 때문인가'))"
```

`stopwords.json` 에 `"디코딩"` 을 넣고 마지막 명령을 다시 실행하면 결과에서 `디코딩` 이 빠져야 한다.

## 문제 해결

| 증상 | 원인 · 조치 |
|---|---|
| 넣은 단어가 안 빠진다 | 대문자로 적었거나 조사가 붙은 형태로 적음 → 소문자·어근으로 적는다(`keywords()` 결과를 보고 그 형태 그대로) |
| 로그에 `stopwords 파일 형식 오류` | `stopwords` 가 문자열 배열이 아님 → 예제 형식으로 고친다 |
| 로그에 `stopwords 파일 읽기 실패` | JSON 문법 오류(쉼표·따옴표) → `python -m json.tool stopwords.json` 으로 위치 확인 |
| 다른 위치의 파일을 쓰고 싶다 | `.env` 또는 셸에 `LLMWIKI_STOPWORDS_PATH` 설정 후 `config paths` 로 확인 |

## BRINGUP §3.2 설정표 행

| stopwords.json | stopwords | 코드 기본 목록 | 질의 키워드 추출에서 제거할 불용어 |

## README §0 문서 색인 행 (제안)

- `docs/STOPWORDS.md`(이 초안을 옮긴 파일) — 불용어 파일 형식·경로·재로딩·검증

## 바뀐 파일

- `llmwiki/textutil.py` — `DEFAULT_STOPWORDS`(폴백) · `load_stopwords()`(mtime 캐시, 없으면 생성, 오류 시 폴백) · `stopwords_path()` · 호환용 `STOPWORDS`
- `llmwiki/config.py` — `_PATH_DEFAULTS["stopwords"] = "stopwords.json"`
- `setup/check_env.py` — `stopwords.json` 존재·개수·기본값 여부 한 줄
- `stopwords.json` · `setup/stopwords.example.json` — 기본 목록
- `tests/test_stopwords.py`
