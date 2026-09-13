---
schema_version: 1
doc_type: issue
id: ISSUE-2006
title: ISR 진입 지연으로 타이밍 위반
date: 2026-07-19
author: hong.gd
status: fixed
severity: major
tags: ["isr", "인터럽트", "modem-b1"]
module: ["isr_core"]
hw: {"chip": "MDM9x", "rev": "B1"}
related: {"cls": ["CL-55306"], "issues": []}
---

# ISSUE-2006 ISR 진입 지연으로 타이밍 위반

## 현상
ISR 진입 지연으로 타이밍 위반. 인터럽트 코어 블록에서 재현되며 HW rev B1 에서 발생 빈도가 높다. 로그에 `ISR_MASK` 레지스터 값 이상이 기록된다.

## 원인
재전송 타이머 초기화 누락. 인터럽트 코어 초기화 시퀀스 3단계에서 문제가 시작된다.

## 분석
인터럽트 코어 파형을 캡처해 t_setup 을 측정했고, ISR_MASK 를 읽어 isr_core 상태를 확인했다. 유사 이슈 없음 와 원인이 겹친다. 재현 TC 는 TC-ISR-CORE-006.

## 수정
CL-55306 에서 재전송 타이머 초기화 누락 수정 반영. 상세는 CL 문서 참조.

## 검증
VP(virtual platform) 에서 100회 반복 시험 통과.
