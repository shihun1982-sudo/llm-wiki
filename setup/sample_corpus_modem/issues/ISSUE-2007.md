---
schema_version: 1
doc_type: issue
id: ISSUE-2007
title: 클럭 전환 중 hang
date: 2026-07-28
author: kim.ys
status: verified
severity: critical
tags: ["clk", "클럭", "modem-a2"]
module: ["clk_mgr"]
hw: {"chip": "MDM9x", "rev": "A2"}
related: {"cls": ["CL-55307"], "issues": []}
---

# ISSUE-2007 클럭 전환 중 hang

## 현상
클럭 전환 중 hang. 클럭 매니저 블록에서 재현되며 HW rev A2 에서 발생 빈도가 높다. 로그에 `CLK_DIV_SEL` 레지스터 값 이상이 기록된다.

## 원인
클럭 도메인 교차 시 동기화 없이 레지스터 접근. 클럭 매니저 초기화 시퀀스 4단계에서 문제가 시작된다.

## 분석
클럭 매니저 파형을 캡처해 t_setup 을 측정했고, CLK_DIV_SEL 를 읽어 clk_mgr 상태를 확인했다. 유사 이슈 없음 와 원인이 겹친다. 재현 TC 는 TC-CLK-MGR-007.

## 수정
CL-55307 에서 클럭 도메인 교차 시 동기화 없이 레지스터 접근 수정 반영. 상세는 CL 문서 참조.

## 검증
VP(virtual platform) 에서 100회 반복 시험 통과, 실기판 A2 에서 24시간 스트레스 테스트 통과.
