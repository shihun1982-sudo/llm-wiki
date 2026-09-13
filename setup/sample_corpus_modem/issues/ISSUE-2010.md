---
schema_version: 1
doc_type: issue
id: ISSUE-2010
title: AGC 수렴이 3ms 이상 지연
date: 2026-08-24
author: choi.sw
status: open
severity: trivial
tags: ["agc", "agc", "modem-a2"]
module: ["agc"]
hw: {"chip": "MDM9x", "rev": "A2"}
related: {"cls": [], "issues": []}
---

# ISSUE-2010 AGC 수렴이 3ms 이상 지연

## 현상
AGC 수렴이 3ms 이상 지연. AGC 블록에서 재현되며 HW rev A2 에서 발생 빈도가 높다. 로그에 `AGC_LOOP_CFG` 레지스터 값 이상이 기록된다.

## 원인
gain 테이블 인덱스 off-by-one. AGC 초기화 시퀀스 3단계에서 문제가 시작된다.

## 분석
AGC 파형을 캡처해 t_setup 을 측정했고, AGC_LOOP_CFG 를 읽어 agc 상태를 확인했다. 유사 이슈 없음 와 원인이 겹친다. 재현 TC 는 TC-AGC-001.

## 수정
수정 CL 미정 (분석 중).

## 검증
VP(virtual platform) 에서 100회 반복 시험 통과.
