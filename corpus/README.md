# corpus/ — 내 문서 폴더

여기에 실제 문서(`.md .txt .csv .html .htm .pdf`)를 넣고 `config.json` 의 `corpus_dirs` 에 `"corpus"` 를 넣은 뒤 `run.bat build --full` 을 실행하세요 (하위 폴더 재귀).
여러 폴더를 동시에 색인할 수 있고, 절대 경로도 됩니다. 예: `"corpus_dirs": ["corpus", "D:/team/issues"]`

- 샘플(합성 모뎀 코퍼스 38문서)은 `setup/sample_corpus_modem/` 에 있으며 기본 `config.json` 이 그 폴더를 가리킵니다. 실제 문서로 바꿀 때 이 폴더로 교체하세요.
- 외부 MCP 소스에서 ingest 한 문서는 `data/mcp_cache/` 에 자동 저장되어 함께 색인됩니다 (여기 넣지 않아도 됨).
- 문서 형식(front matter, 유형별 필드)은 `docs/CORPUS_CONTRACT.md`, 검사는 `run.bat corpus lint`.
- 이 README.md 는 색인에 포함되므로 원하지 않으면 삭제해도 됩니다.
