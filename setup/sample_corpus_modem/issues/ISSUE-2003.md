---
schema_version: 1
doc_type: issue
id: ISSUE-2003
title: TX 전력이 목표 대비 1.5dB 낮음
date: 2026-06-22
author: lee.jh
status: closed
severity: minor
tags: ["tx", "tx", "modem-b1"]
module: ["tx_power"]
hw: {"chip": "MDM9x", "rev": "B1"}
related: {"cls": ["CL-55303"], "issues": []}
---

# ISSUE-2003 TX 전력이 목표 대비 1.5dB 낮음

## 현상
TX 전력이 목표 대비 1.5dB 낮음. TX 전력 제어 블록에서 재현되며 HW rev B1 에서 발생 빈도가 높다. 로그에 `TXPWR_GAIN_TBL` 레지스터 값 이상이 기록된다.

## 원인
레지스터 쓰기 순서 위반 (RST 전에 CFG 기록). TX 전력 제어 초기화 시퀀스 4단계에서 문제가 시작된다.

## 분석
TX 전력 제어 파형을 캡처해 t_setup 을 측정했고, TXPWR_GAIN_TBL 를 읽어 tx_power 상태를 확인했다. 유사 이슈 없음 와 원인이 겹친다. 재현 TC 는 TC-TX-POWER-003.

## 수정
CL-55303 에서 레지스터 쓰기 순서 위반 수정 반영. 상세는 CL 문서 참조.

## 검증
VP(virtual platform) 에서 100회 반복 시험 통과, 실기판 B1 에서 24시간 스트레스 테스트 통과.
