---
schema_version: 1
doc_type: issue
id: ISSUE-2004
title: HARQ 재전송 시 버퍼 포인터 꼬임
date: 2026-07-01
author: park.mj
status: analyzing
severity: major
tags: ["harq", "harq", "modem-a2"]
module: ["harq"]
hw: {"chip": "MDM9x", "rev": "A2"}
related: {"cls": [], "issues": []}
---

# ISSUE-2004 HARQ 재전송 시 버퍼 포인터 꼬임

## 현상
HARQ 재전송 시 버퍼 포인터 꼬임. HARQ 버퍼 블록에서 재현되며 HW rev A2 에서 발생 빈도가 높다. 로그에 `HARQ_BUF_PTR` 레지스터 값 이상이 기록된다.

## 원인
ISR 내부에서 blocking 대기 사용. HARQ 버퍼 초기화 시퀀스 5단계에서 문제가 시작된다.

## 분석
HARQ 버퍼 파형을 캡처해 t_setup 을 측정했고, HARQ_BUF_PTR 를 읽어 harq 상태를 확인했다. 유사 이슈 없음 와 원인이 겹친다. 재현 TC 는 TC-HARQ-004.

## 수정
수정 CL 미정 (분석 중).

## 검증
VP(virtual platform) 에서 100회 반복 시험 통과.
