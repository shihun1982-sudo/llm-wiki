# -*- coding: utf-8 -*-
"""단위 테스트 묶음 — **실행 전에 실사용 데이터와 격리**한다.

왜 여기서 하나
  `reqmgr.get_manager()` 는 프로세스당 하나의 RequestManager 를 만들어 두고 재사용한다
  (`data_dir` 은 **처음 만들 때만** 쓰인다). 그래서 테스트 한 개가 data_dir 없이 먼저 만들면
  그 뒤의 모든 테스트가 **프로젝트의 `data/ledger`** 에 기록한다. 실제로 한 번 돌릴 때마다
  700~800줄이 실사용 원장에 섞여 들어갔고, 그러면 원장으로 장애를 분석할 수 없다.

  개별 테스트마다 `ledger.dir` 을 챙기는 방식은 하나만 빠뜨려도 조용히 새므로,
  묶음 전체에 한 줄로 못을 박는다. 이미 환경변수가 지정돼 있으면 존중한다(CI 에서 따로 지정하는 경우).
"""
import os
import tempfile

os.environ.setdefault("LLMWIKI_LEDGER_DIR_PATH",
                      os.path.join(tempfile.gettempdir(), "llmwiki_test_ledger"))
# 로그도 같은 이유로 격리한다 (2026-09-24). 62개 테스트 모듈 중 18개만 LLMWIKI_LOGS_DIR_PATH 를 지정해서,
# 한 번 돌릴 때마다 mock 질의 로그 약 6,800줄이 프로젝트의 logs/llmwiki.log·error.log·query.log 에 섞여 들어갔다.
# **주의**: `python -m unittest discover -s tests` 는 이 __init__ 을 임포트하지 않는다(시작 폴더 = 최상위). 그 경로는
# 이름순으로 가장 먼저 임포트되는 tests/test_00_isolate.py 가 같은 설정을 건다 — 두 파일을 함께 고친다.
os.environ.setdefault("LLMWIKI_LOGS_DIR_PATH",
                      os.path.join(tempfile.gettempdir(), "llmwiki_test_logs"))
# 환경변수는 개별 테스트의 tearDown(`os.environ.pop`)에 지워진다 — pop 에 지워지지 않는 대체 기본값도 건다 (test_00_isolate.py 와 같다)
from llmwiki.config import set_path_fallback  # noqa: E402
set_path_fallback("logs_dir", os.path.join(tempfile.gettempdir(), "llmwiki_test_logs"))
