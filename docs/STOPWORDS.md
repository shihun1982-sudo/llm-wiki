# STOPWORDS — 불용어 파일 `stopwords.json` (질의 키워드에서 제거할 단어를 코드 밖으로)

> 파일: `<프로젝트 루트>/stopwords.json` (경로 이름 `stopwords`, 환경변수 `LLMWIKI_STOPWORDS_PATH`)
> 예제: `setup/stopwords.example.json` · 코드: `llmwiki/textutil.py` (`DEFAULT_STOPWORDS`, `load_stopwords()`)
> 설계 근거: [IMPLEMENTATION_PLAN_0918.md §2.2](history/2026-09-18/IMPLEMENTATION_PLAN_0918.md) · 검증: `tests/test_stopwords.py`

## 0. 한 장 요약

```
질의 "PDCCH 디코딩 실패는 무엇 때문인가"
  → textutil.analyze()  : 소문자 · 조사 제거 · 복합어 분리 · (kiwi) 형태소     → [pdcch, 디코딩, 실패, 무엇, 때문]
  → textutil.keywords() : len ≥ 2 이고 stopwords.json 집합에 없는 것만          → [pdcch, 디코딩, 실패, 때문]
```

| 항목 | 값 |
|---|---|
| 파일 | `stopwords.json` — `{"_comment": "…", "stopwords": ["a", "an", "무엇", …]}` (배열만 있는 `["a", …]` 도 허용) |
| 기본값 | 코드 `textutil.DEFAULT_STOPWORDS` **51개**(한국어 의문사·군더더기 + 영어 관사·의문사). 파일이 없으면 이 목록으로 **생성** |
| 반영 시점 | 파일 mtime 캐시 → 저장하면 **다음 `keywords()` 호출부터**. 서버·MCP 재시작 불필요 |
| 깨진 파일 | `logs/llmwiki.log`·`error.log` 에 warning 한 줄(같은 mtime 에 한 번) → 코드 기본 목록으로 동작. 파일은 덮어쓰지 않음 |
| 쓰는 곳 | `textutil.keywords()`(질의 키워드·FTS 커버리지·doc_expand·추출식 답변), `retrieval.py`(근거 토큰 비교), `forensic.py`(빠진 용어 추정) |
| 확인 | `python setup/check_env.py` 의 `stopwords.json` 줄 · `python -m llmwiki config paths` 의 `stopwords` 행 |

## 1. 왜 파일로 뺐나 (설계 근거)

예전에는 `llmwiki/textutil.py` 안의 상수였다. 사내 코퍼스마다 "우리 팀 질문에 흔한 군더더기"(예: `확인`, `부탁`, 팀 내부 접두어)가
다르고, 그것을 빼려면 코드를 고쳐야 했다 — 이식 원칙("설정은 파일로, 코드는 그대로 복사")에 어긋난다.

| 대안 | 왜 버렸나 |
|---|---|
| `query_rules.json` 안에 `stopwords` 절 추가 | 질의 규칙은 검색어를 **확장**(동의어·관련어·복합어)하고 불용어는 **제거**한다. 성격이 반대라 한 파일에 두면 운영자가 "어디를 고치지" 를 헷갈린다 |
| `config.json` 의 배열 키 | 51개 단어 목록이 설정 파일 한가운데 들어가 다른 키를 가린다. `config set` 로 배열을 고치기도 불편하다 |
| **별도 파일 + 없으면 생성 + mtime 재로딩** ✔ | `prompts/*.md` 와 같은 방식이라 운영자가 이미 아는 규칙. 파일 하나를 지우면 기본값으로 되돌아간다 |

`answer.py` 의 claim 검증(`STOPWORDS_CLAIM`)은 **별도 하드코딩 목록**이다 — 문장 단위 근거 비교용으로 조사·서술어(`있다`, `된다` …)가
들어 있어 질의 키워드 불용어와 목적이 다르다. 이 파일은 그것을 바꾸지 않는다.

## 2. 파일 형식과 키

```json
{
  "_comment": "질의 키워드 추출에서 제거할 불용어 목록. 소문자·조사 제거 뒤의 토큰과 비교한다. …",
  "stopwords": ["a", "an", "and", "are", "how", "…", "무엇", "어떤", "어떻게", "…"]
}
```

| 키 | 형 | 기본 | 뜻 |
|---|---|---|---|
| `stopwords` | 문자열 배열 | `DEFAULT_STOPWORDS` 51개 | 제거할 토큰. 비교는 **소문자 · 조사 제거 뒤** 토큰(`analyze()` 결과)과 하므로 소문자·어근으로 적는다. 앞뒤 공백은 잘리고 빈 문자열은 무시된다 |
| `_comment` | 문자열 | 생성 시 안내문 | 메모. 읽지 않는다 |

형식 검사: `stopwords` 가 배열이 아니거나 문자열이 아닌 항목이 있으면 `stopwords 파일 형식 오류 — 기본 목록 사용`(warning, `path`·`hint` 포함).
JSON 문법 오류는 `stopwords 파일 읽기 실패 — 기본 목록 사용`. 두 경우 모두 기본 목록으로 계속 동작한다.

## 3. 경로

| 항목 | 값 | 확인 |
|---|---|---|
| 기본 | `<루트>/stopwords.json` (`config._PATH_DEFAULTS["stopwords"] = "stopwords.json"`) | `python -m llmwiki config paths` → `stopwords  …\stopwords.json` |
| 환경변수 | `LLMWIKI_STOPWORDS_PATH=<절대 경로 또는 루트 기준 상대 경로>` (`.env` 가능) | 같은 명령의 경로가 바뀐다 |
| 예제 | `setup/stopwords.example.json` (기본 51개와 동일) | `Copy-Item setup\stopwords.example.json stopwords.json` — 없으면 자동 생성되므로 복사는 선택 |

## 4. 동작 (`textutil.load_stopwords()`)

1. `path_for("stopwords")` 로 경로를 정한다. 경로 해석 자체가 실패하면(초기화 전) 기본 목록.
2. 파일이 없으면 `atomicio.write_json` 으로 `{"_comment", "stopwords": sorted(DEFAULT_STOPWORDS)}` 를 **원자적으로 생성**한다. 생성 실패(권한 등)면 warning + 기본 목록.
3. mtime 이 캐시와 같으면 캐시 집합을 돌려준다. 다르면 다시 읽어 파싱한다.
4. 파싱 결과가 없으면(형식·문법 오류) 기본 목록을 쓰되, **그 mtime 을 캐시에 기억**해 같은 파일로 경고를 반복하지 않는다. 파일을 고쳐 저장하면 mtime 이 바뀌어 즉시 복구된다.
5. 호환: `from llmwiki.textutil import STOPWORDS` 로 쓰던 코드(`retrieval.py`, `forensic.py`)는 그대로 동작한다 — `STOPWORDS` 는 `in`·`len`·반복 때마다 현재 파일 집합을 보는 프록시(`_LiveStopwords`)다.

## 5. 세 창구

| 창구 | 내용 |
|---|---|
| CLI | `config paths`(경로) · `python setup/check_env.py`(존재·개수·기본값 여부 한 줄: `[OK  ] stopwords.json 있음 — 불용어 51개 (코드 기본값)`). 편집은 파일을 직접 고친다 |
| Web UI | 전용 편집 화면 **없음** (2026-09-18). 효과는 Ask 결과의 `route.keywords`·추출식 답변 키워드에서 보인다 |
| MCP | 전용 도구 없음. `wiki_query` 결과의 검색 키워드에 반영된다 |

## 6. 검증 명령

```powershell
python -m unittest tests.test_stopwords -v
#   test_path_registry · test_default_file_created_when_missing · test_edit_reloads_without_restart · test_broken_json_falls_back_to_defaults (4건)
python setup/check_env.py | Select-String stopwords
python -m llmwiki config paths | Select-String stopwords
python -c "from llmwiki.textutil import keywords; print(keywords('PDCCH 디코딩 실패는 무엇 때문인가'))"
```

`stopwords.json` 의 배열에 `"디코딩"` 을 넣고 마지막 명령을 다시 실행하면 결과에서 `디코딩` 이 빠져야 한다(재시작 없음).
되돌리려면 그 줄을 지우거나, 파일 자체를 지우면 다음 호출에서 기본 51개로 다시 생성된다.

## 7. 문제 해결

| 증상 | 원인 · 조치 |
|---|---|
| 넣은 단어가 안 빠진다 | 대문자로 적었거나 조사가 붙은 형태로 적음 → `python -c "from llmwiki.textutil import analyze; print(analyze('…'))"` 로 나온 형태 그대로(소문자·어근) 적는다. 2글자 미만 토큰은 불용어와 무관하게 이미 제외된다(`min_len=2`) |
| `error.log` 에 `stopwords 파일 형식 오류` | `stopwords` 가 문자열 배열이 아님(객체·숫자 포함) → §2 형식으로 고친다 |
| `error.log` 에 `stopwords 파일 읽기 실패` | JSON 문법 오류(쉼표·따옴표) → `python -m json.tool stopwords.json` 으로 위치 확인 |
| `stopwords 파일 생성 실패` | 루트 폴더 쓰기 권한 없음 → `LLMWIKI_STOPWORDS_PATH` 로 쓸 수 있는 위치를 지정 |
| 다른 위치의 파일을 쓰고 싶다 | `.env` 또는 셸에 `LLMWIKI_STOPWORDS_PATH` 설정 → `config paths` 로 확인 |
| 여러 서버가 같은 목록을 써야 한다 | 파일 하나를 배포 폴더에 넣어 복사(색인과 무관하므로 리빌드 불필요) |

## 8. 구현 파일

| 파일 | 내용 |
|---|---|
| `llmwiki/textutil.py` | `DEFAULT_STOPWORDS`(폴백 51개) · `STOPWORDS_COMMENT` · `stopwords_path()` · `_parse_stopwords()` · `load_stopwords()`(mtime 캐시·생성·폴백) · `_LiveStopwords`/`STOPWORDS`(호환 프록시) · `keywords()` |
| `llmwiki/config.py` | `_PATH_DEFAULTS["stopwords"] = "stopwords.json"` → `path_for("stopwords")`, `config paths` 출력 |
| `setup/check_env.py` | 존재·개수·기본값 여부 한 줄 (없으면 생성) |
| `stopwords.json` · `setup/stopwords.example.json` | 기본 목록 51개 (동일) |
| `llmwiki/retrieval.py` · `llmwiki/forensic.py` | `STOPWORDS` 소비자 (근거 토큰 비교 · 빠진 용어 추정) |
| `tests/test_stopwords.py` | 경로 레지스트리 · 자동 생성 · 수정 즉시 반영 · 깨진 파일 폴백 (4건, 임시 `LLMWIKI_STOPWORDS_PATH` 사용) |
