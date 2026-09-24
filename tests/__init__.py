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
