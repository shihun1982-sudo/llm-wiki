---
schema_version: 1
doc_type: issue
id: ISSUE-2005
title: PDCCH 디코딩 실패율 증가
date: 2026-07-10
author: choi.sw
status: open
severity: trivial
tags: ["pdcch", "pdcch", "modem-b1"]
module: ["pdcch_dec"]
hw: {"chip": "MDM9x", "rev": "B1"}
related: {"cls": [], "issues": ["ISSUE-2008"]}
---

# ISSUE-2005 PDCCH 디코딩 실패율 증가

## 현상
PDCCH 디코딩 실패율 증가. PDCCH 디코더 블록에서 재현되며 HW rev B1 에서 발생 빈도가 높다. 로그에 `PDCCH_CFG` 레지스터 값 이상이 기록된다.

## 원인
HW rev B1 에서 타이밍 마진 축소 (t_setup 12ns → 8ns). PDCCH 디코더 초기화 시퀀스 2단계에서 문제가 시작된다.

## 분석
PDCCH 디코더 파형을 캡처해 t_setup 을 측정했고, PDCCH_CFG 를 읽어 pdcch_dec 상태를 확인했다. 유사 이슈 ISSUE-2008 와 원인이 겹친다. 재현 TC 는 TC-PDCCH-DEC-005.

## 수정
수정 CL 미정 (분석 중).

## 검증
VP(virtual platform) 에서 100회 반복 시험 통과.
