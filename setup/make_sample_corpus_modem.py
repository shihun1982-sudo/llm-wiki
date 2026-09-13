# -*- coding: utf-8 -*-
"""합성 모뎀 코퍼스 생성기 — 문서 계약(front matter) 을 따르는 6유형 샘플 + 평가셋.

  python setup/make_sample_corpus_modem.py [--out setup/sample_corpus_modem] [--scale 1]

--scale N 은 이슈/CL 을 N배로 늘려 규모 테스트(5,000 문서 시나리오)에 쓴다. 내용은 결정적(랜덤 시드 고정).
실제 사내 데이터가 아니라 형식·연결(CL↔Issue, 시간, 태그)을 보여주기 위한 가상 데이터다.
"""
from __future__ import annotations

import argparse
import json
import os
import random
from datetime import date, timedelta

# 인덱스 k 로 SYMPTOMS/CAUSES/MODULES/BLOCKS/REGS 가 서로 대응된다 (k=0 RX DMA, 1 AGC, 2 TX, 3 HARQ, 4 PDCCH, 5 ISR, 6 CLK, 7 PHY FIFO)
MODULES = ["rx_dma", "agc", "tx_power", "harq", "pdcch_dec", "isr_core", "clk_mgr", "phy_ctrl"]
BLOCKS = ["RX DMA", "AGC", "TX 전력 제어", "HARQ 버퍼", "PDCCH 디코더", "인터럽트 코어", "클럭 매니저", "PHY 컨트롤러"]
REGS = ["RX_DMA_CTRL", "AGC_LOOP_CFG", "TXPWR_GAIN_TBL", "HARQ_BUF_PTR", "PDCCH_CFG", "ISR_MASK", "CLK_DIV_SEL", "PHY_RST_REG"]
SW_DESIGN_IDX = [0, 7, 2, 1]   # SWD-RX-DMA-01, SWD-PHY-CTRL-02, SWD-TX-POWER-03, SWD-AGC-04
SYMPTOMS = ["DMA underrun 발생 후 PHY 재시작 실패", "AGC 수렴이 3ms 이상 지연", "TX 전력이 목표 대비 1.5dB 낮음", "HARQ 재전송 시 버퍼 포인터 꼬임",
            "PDCCH 디코딩 실패율 증가", "ISR 진입 지연으로 타이밍 위반", "클럭 전환 중 hang", "RX 경로 FIFO overflow"]
CAUSES = ["FIFO 임계값 설정 오류 (0x20 → 0x40 필요)", "gain 테이블 인덱스 off-by-one", "레지스터 쓰기 순서 위반 (RST 전에 CFG 기록)",
          "ISR 내부에서 blocking 대기 사용", "HW rev B1 에서 타이밍 마진 축소 (t_setup 12ns → 8ns)", "재전송 타이머 초기화 누락",
          "클럭 도메인 교차 시 동기화 없이 레지스터 접근", "DMA descriptor 정렬 오류"]
PEOPLE = ["hong.gd", "kim.ys", "lee.jh", "park.mj", "choi.sw"]


def d(day: int) -> str:
    return (date(2026, 6, 1) + timedelta(days=day)).isoformat()


def fm(meta: dict) -> str:
    lines = ["---"]
    for k, v in meta.items():
        if isinstance(v, (list, dict)):
            lines.append("%s: %s" % (k, json.dumps(v, ensure_ascii=False)))
        else:
            lines.append("%s: %s" % (k, json.dumps(v, ensure_ascii=False) if isinstance(v, str) and (":" in v or "#" in v) else v))
    lines.append("---")
    return "\n".join(lines) + "\n\n"


def write(path: str, text: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def gen(out: str, scale: int = 1) -> dict:
    rnd = random.Random(20260913)
    n_issue = 10 * scale
    n_cl = 10 * scale
    issues = []
    for i in range(n_issue):
        k = i % 8
        iid = "ISSUE-%04d" % (2001 + i)
        cl_id = "CL-%05d" % (55301 + i)
        day = 3 + (i * 9) % 95
        status = ["fixed", "verified", "closed", "analyzing", "open"][i % 5]
        sev = ["major", "critical", "minor", "major", "trivial"][i % 5]
        module = MODULES[k]
        rev = "B1" if i % 3 else "A2"
        related = {"cls": [cl_id] if status in ("fixed", "verified", "closed") else [], "issues": ["ISSUE-%04d" % (2001 + (i + 3) % n_issue)] if i % 4 == 0 else []}
        meta = {"schema_version": 1, "doc_type": "issue", "id": iid, "title": SYMPTOMS[k], "date": d(day), "author": PEOPLE[i % 5], "status": status,
                "severity": sev, "tags": [module.split("_")[0], BLOCKS[k].split()[0].lower(), "modem-" + rev.lower()], "module": [module],
                "hw": {"chip": "MDM9x", "rev": rev}, "related": related}
        body = (fm(meta) + "# %s %s\n\n## 현상\n%s. %s 블록에서 재현되며 HW rev %s 에서 발생 빈도가 높다. 로그에 `%s` 레지스터 값 이상이 기록된다.\n\n"
                "## 원인\n%s. %s 초기화 시퀀스 %d단계에서 문제가 시작된다.\n\n## 분석\n%s 파형을 캡처해 t_setup 을 측정했고, %s 를 읽어 %s 상태를 확인했다. "
                "유사 이슈 %s 와 원인이 겹친다. 재현 TC 는 TC-%s-%03d.\n\n## 수정\n%s\n\n## 검증\nVP(virtual platform) 에서 100회 반복 시험 통과%s.\n"
                % (iid, SYMPTOMS[k], SYMPTOMS[k], BLOCKS[k], rev, REGS[k], CAUSES[k], BLOCKS[k], 2 + i % 4, BLOCKS[k], REGS[k], module,
                   related["issues"][0] if related["issues"] else "없음", module.upper().replace("_", "-"), 1 + i % 9,
                   ("%s 에서 %s 수정 반영. 상세는 CL 문서 참조." % (cl_id, CAUSES[k].split(" (")[0])) if related["cls"] else "수정 CL 미정 (분석 중).",
                   ", 실기판 %s 에서 24시간 스트레스 테스트 통과" % rev if status in ("verified", "closed") else ""))
        write(os.path.join(out, "issues", iid + ".md"), body)
        issues.append((iid, cl_id, k, day, module, rev, status))
    for i in range(n_cl):
        iid, cl_id, k, day, module, rev, status = issues[i % n_issue]
        if status not in ("fixed", "verified", "closed"):
            cl_id = "CL-%05d" % (55301 + i)
        meta = {"schema_version": 1, "doc_type": "cl", "id": cl_id, "title": "%s 수정: %s" % (module, CAUSES[k].split(" (")[0]), "date": d(day + 2),
                "author": PEOPLE[(i + 1) % 5], "status": "merged" if i % 7 else "review", "tags": [module.split("_")[0]], "module": [module],
                "hw": {"chip": "MDM9x", "rev": rev}, "related": {"issues": [iid]}, "files": ["drivers/%s.c" % module, "include/%s.h" % module]}
        body = (fm(meta) + "# %s %s\n\n## 변경 내용\n%s 문제(%s)를 수정한다. `%s` 레지스터 접근 순서를 바로잡고 %s 를 %s 한다.\n\n"
                "## 영향 범위\n%s 모듈, HW rev %s. 코딩 규칙 RULE-%s 준수 확인.\n\n## 테스트\nTC-%s-%03d 통과, 회귀 TC 12건 통과.\n"
                % (cl_id, meta["title"], iid, SYMPTOMS[k], REGS[k], BLOCKS[k], ["재초기화", "동기화", "재설정"][i % 3], module, rev,
                   ["ISR-001", "REG-002"][i % 2], module.upper().replace("_", "-"), 1 + i % 9))
        write(os.path.join(out, "cls", cl_id + ".md"), body)
    # SW design
    for j, k in enumerate(SW_DESIGN_IDX):
        mod, blk, reg = MODULES[k], BLOCKS[k], REGS[k]
        sid = "SWD-%s-%02d" % (mod.upper().replace("_", "-"), j + 1)
        meta = {"schema_version": 1, "doc_type": "sw_design", "id": sid, "title": "%s 드라이버 설계" % blk, "date": d(1 + j), "author": PEOPLE[j],
                "status": "approved", "tags": [mod.split("_")[0], "design"], "module": [mod], "hw": {"chip": "MDM9x", "rev": "B1"},
                "related": {"docs": ["HWD-PHY-TIMING-B1"], "rules": ["RULE-ISR-001"]}}
        body = (fm(meta) + "# %s %s\n\n## 개요\n%s 드라이버는 `%s` 레지스터로 %s 를 제어한다.\n\n## 구조\n- init(): 클럭 enable → 리셋 해제 → CFG 기록 순서를 지킨다\n- isr(): 인터럽트 컨텍스트에서는 blocking 금지 (RULE-ISR-001)\n- code map: drivers/%s.c (핵심 로직), include/%s.h (레지스터 맵), test/tc_%s.py (TC)\n\n"
                "## 인터페이스\n%s_start(), %s_stop(), %s_get_status()\n\n## 동작 흐름\n1. 상위 계층이 start 요청 → 2. 파라미터 검증 → 3. `%s` 기록 → 4. 완료 인터럽트 대기 (타임아웃 5ms)\n\n## 제약\nHW rev B1 에서는 t_setup 8ns 를 만족해야 하므로 연속 레지스터 기록 사이에 2 사이클 nop 필요.\n"
                % (sid, meta["title"], blk, reg, blk, mod, mod, mod, mod, mod, mod, reg))
        write(os.path.join(out, "sw_design", sid + ".md"), body)
    # HW design
    for j, rev in enumerate(["A2", "B1", "B2"]):
        hid = "HWD-PHY-TIMING-%s" % rev
        meta = {"schema_version": 1, "doc_type": "hw_design", "id": hid, "title": "PHY 타이밍 및 레지스터 사양 rev %s" % rev, "date": d(2 + j * 30), "author": "hw.team",
                "status": "released", "tags": ["phy", "timing", "modem-" + rev.lower()], "hw": {"chip": "MDM9x", "rev": rev}, "related": {"docs": []}}
        body = (fm(meta) + "# %s %s\n\n## 개요\nMDM9x rev %s 의 PHY 블록 타이밍과 SW 제어 레지스터.\n\n## 레지스터\n| 이름 | 오프셋 | 설명 |\n|---|---|---|\n| PHY_RST_REG | 0x0000 | 리셋 제어 |\n| RX_DMA_CTRL | 0x0010 | DMA 임계값(FIFO_THR[7:0]) |\n| TXPWR_GAIN_TBL | 0x0100 | gain 테이블 32 entry |\n\n"
                "## 타이밍\n- t_setup: %dns\n- t_hold: 4ns\n- 리셋 해제 후 안정화: %dus\n\n## SW 제어 시퀀스\n1. CLK_DIV_SEL 설정 → 2. PHY_RST_REG=1 → 3. %dus 대기 → 4. RX_DMA_CTRL FIFO_THR=0x40 → 5. PHY_RST_REG=0\n\n"
                "## Revision History\n| rev | 날짜 | 변경 |\n|---|---|---|\n| A2 | 2026-06-03 | 초기 릴리스 |\n%s%s"
                % (hid, meta["title"], rev, 12 if rev == "A2" else 8, 50 if rev == "A2" else 30, 50 if rev == "A2" else 30,
                   "| B1 | 2026-07-03 | t_setup 12ns→8ns, FIFO 임계 기본값 0x20→0x40 |\n" if rev != "A2" else "",
                   "| B2 | 2026-08-02 | AGC 루프 게인 레지스터 추가 (AGC_LOOP_CFG bit[3]) |\n" if rev == "B2" else ""))
        write(os.path.join(out, "hw_design", hid + ".md"), body)
    # coding rules
    for j, (rid, title, rule) in enumerate([("RULE-ISR-001", "인터럽트 핸들러 규칙", "ISR 내부에서 blocking 대기(polling loop, sleep, mutex)를 사용하지 않는다. 지연 작업은 workqueue 로 넘긴다."),
                                            ("RULE-REG-002", "레지스터 접근 순서 규칙", "리셋 해제 전에 CFG 계열 레지스터를 기록하지 않는다. 연속 기록 사이에는 HW 문서의 t_setup 을 만족하는 nop 을 둔다.")]):
        meta = {"schema_version": 1, "doc_type": "coding_rule", "id": rid, "title": title, "date": d(5 + j * 20), "author": "sw.lead", "status": "active",
                "tags": ["rule", "review"], "scope": ["drivers/*", "isr/*"], "related": {"issues": ["ISSUE-2006"] if j == 0 else ["ISSUE-2003"]}}
        body = fm(meta) + "# %s %s\n\n## 규칙\n%s\n\n## 근거\n%s 에서 규칙 위반으로 타이밍 위반이 발생했다.\n\n## 예시\n```c\n// bad\nwhile(!ready) {}\n// good\nschedule_work(&done_work);\n```\n\n## 예외\n부트로더 초기화 구간은 예외 (리뷰어 승인 필요).\n" % (
            rid, title, rule, meta["related"]["issues"][0])
        write(os.path.join(out, "coding_rules", rid + ".md"), body)
    # weekly reports
    for w in range(4):
        wid = "WR-2026-W%02d" % (33 + w)
        start = date(2026, 8, 10) + timedelta(days=7 * w)
        end = start + timedelta(days=6)
        iss = ["ISSUE-%04d" % (2001 + (w * 3 + x) % n_issue) for x in range(3)]
        cls = ["CL-%05d" % (55301 + (w * 3 + x) % n_cl) for x in range(2)]
        meta = {"schema_version": 1, "doc_type": "weekly_report", "id": wid, "title": "주간 업무 보고 %s" % wid, "date": end.isoformat(), "author": "hong.gd",
                "period": {"from": start.isoformat(), "to": end.isoformat()}, "tags": ["weekly"], "related": {"issues": iss, "cls": cls}}
        body = (fm(meta) + "# %s (%s ~ %s)\n\n## 업무 요약\n- %s 분석 및 수정 진행\n- HW rev B1 타이밍 검증\n- VP 회귀 TC %d건 실행\n\n## 이슈 요약\n- %s: 분석 완료, %s 로 수정\n- %s: 재현 중\n- %s: 검증 대기\n\n"
                "## CL 리뷰 요약\n- %s: 승인 (RULE-REG-002 준수 확인)\n- %s: 리뷰 중\n\n## 다음 주 계획\n- AGC 수렴 지연 이슈 원인 분석\n- %s 검증\n"
                % (wid, start, end, iss[0], 40 + w * 5, iss[0], cls[0], iss[1], iss[2], cls[0], cls[1], iss[2]))
        write(os.path.join(out, "weekly", wid + ".md"), body)
    # TC lists
    for j, mod in enumerate(MODULES[:2]):
        tid = "TC-%s-%03d" % (mod.upper().replace("_", "-"), 1 + j)
        meta = {"schema_version": 1, "doc_type": "tc_list", "id": tid, "title": "%s 검증 TC" % BLOCKS[j], "date": d(10 + j), "author": "qa.team", "status": "active",
                "tags": ["tc", mod.split("_")[0]], "module": [mod], "related": {"issues": [issues[j][0]]}}
        body = fm(meta) + "# %s %s\n\n## 목적\n%s 의 %s 시나리오 검증.\n\n## 사전 조건\nHW rev B1, FW 3.2 이상.\n\n## 절차\n1. 초기화 2. 부하 인가 3. `%s` 값 기록\n\n## 기대 결과\n%s 미발생, 인터럽트 지연 < 1ms.\n" % (
            tid, meta["title"], BLOCKS[j], SYMPTOMS[j], REGS[j], SYMPTOMS[j])
        write(os.path.join(out, "tc", tid + ".md"), body)
    # 계약 없는 문서 (추론 대상)
    write(os.path.join(out, "misc", "meeting_2026-09-02.md"), "# 2026-09-02 주간 회의\n\n## 참석\nhong.gd, kim.ys\n\n## 논의\n- ISSUE-2002 AGC 수렴 지연: 원인은 gain 테이블 인덱스 off-by-one 로 추정\n- rev B2 보드 입고 예정\n")
    questions = [
        {"q": "ISSUE-2001 의 원인과 수정 CL 은?", "expect_docs": ["ISSUE-2001"], "expect_terms": ["FIFO", "CL-55301"]},
        {"q": "RX DMA underrun 발생 시 PHY 재시작 실패 원인", "expect_docs": ["ISSUE-2001"], "expect_terms": ["FIFO"]},
        {"q": "CL-55302 는 어떤 이슈를 수정했나?", "expect_docs": ["CL-55302"], "expect_terms": ["ISSUE-2002"]},
        {"q": "AGC 수렴 지연 이슈의 원인은?", "expect_docs": ["ISSUE-2002"], "expect_terms": ["off-by-one"]},
        {"q": "HW rev B1 에서 t_setup 은 몇 ns 인가?", "expect_docs": ["HWD-PHY-TIMING-B1"], "expect_terms": ["8ns"]},
        {"q": "PHY 리셋 해제 후 안정화 대기 시간 (rev A2)", "expect_docs": ["HWD-PHY-TIMING-A2"], "expect_terms": ["50us"]},
        {"q": "ISR 안에서 blocking 대기를 써도 되나?", "expect_docs": ["RULE-ISR-001"], "expect_terms": ["workqueue"]},
        {"q": "레지스터 접근 순서 규칙", "expect_docs": ["RULE-REG-002"], "expect_terms": ["t_setup"]},
        {"q": "RX DMA 드라이버의 code map", "expect_docs": ["SWD-RX-DMA-01"], "expect_terms": ["drivers/rx_dma.c"]},
        {"q": "지난주 주간 보고에서 리뷰한 CL", "expect_docs": ["WR-2026-W36"], "expect_terms": ["CL-"]},
        {"q": "WR-2026-W34 이슈 요약", "expect_docs": ["WR-2026-W34"], "expect_terms": ["ISSUE-"]},
        {"q": "TX 전력이 목표 대비 낮은 문제", "expect_docs": ["ISSUE-2003"], "expect_terms": ["1.5dB"]},
        {"q": "HARQ 재전송 버퍼 포인터 꼬임 수정", "expect_docs": ["ISSUE-2004", "CL-55304"], "expect_terms": ["ISR"]},
        {"q": "PDCCH 디코딩 실패율 증가 이슈", "expect_docs": ["ISSUE-2005"], "expect_terms": ["PDCCH"]},
        {"q": "rev B2 에서 추가된 레지스터", "expect_docs": ["HWD-PHY-TIMING-B2"], "expect_terms": ["AGC_LOOP_CFG"]},
        {"q": "FIFO 임계값 기본값이 바뀐 리비전", "expect_docs": ["HWD-PHY-TIMING-B1"], "expect_terms": ["0x40"]},
        {"q": "RX DMA 검증 TC 의 기대 결과", "expect_docs": ["TC-RX-DMA-001"], "expect_terms": ["1ms"]},
        {"q": "클럭 전환 중 hang 이슈 원인", "expect_docs": ["ISSUE-2007"], "expect_terms": ["동기화"]},
        {"q": "ISSUE-2006 관련 코딩 규칙", "expect_docs": ["RULE-ISR-001", "ISSUE-2006"], "expect_terms": ["blocking"]},
        {"q": "PHY 컨트롤러 드라이버 init 순서", "expect_docs": ["SWD-PHY-CTRL-02"], "expect_terms": ["리셋"]},
        {"q": "9월 초 회의에서 논의한 AGC 이슈", "expect_docs": ["meeting_2026-09-02"], "expect_terms": ["ISSUE-2002"]},
        {"q": "Physical Downlink Control Channel 디코더 문제", "expect_docs": ["ISSUE-2005"], "expect_terms": ["PDCCH"]},
        {"q": "재전송타이머 초기화 누락", "expect_docs": ["ISSUE-2006", "ISSUE-2004"], "expect_terms": ["재전송"]},
        {"q": "DMA descriptor 정렬 오류 수정 CL", "expect_docs": ["CL-55308", "ISSUE-2008"], "expect_terms": ["CL-55308"]},
        {"q": "TC-RX-DMA-001 사전 조건", "expect_docs": ["TC-RX-DMA-001"], "expect_terms": ["FW 3.2"]},
    ]
    write(os.path.join(out, "questions.json"), json.dumps(questions, ensure_ascii=False, indent=2))
    write(os.path.join(out, "README.md"), "# 합성 모뎀 코퍼스 (샘플)\n\n`python setup/make_sample_corpus_modem.py` 로 생성된 가상 데이터. 문서 계약(front matter, schemas/) 을 따르는\nissues/ cls/ sw_design/ hw_design/ coding_rules/ weekly/ tc/ 와 계약 없는 misc/ 를 포함한다. `questions.json` 은 평가셋.\n\n사용: config.json 의 corpus_dirs 에 이 폴더를 넣고 `build --full`, `eval --questions setup/sample_corpus_modem/questions.json`.\n")
    return {"issues": n_issue, "cls": n_cl, "questions": len(questions), "out": out}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "sample_corpus_modem"))
    ap.add_argument("--scale", type=int, default=1)
    a = ap.parse_args()
    print(json.dumps(gen(a.out, a.scale), ensure_ascii=False))
