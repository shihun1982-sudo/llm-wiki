---
schema_version: 1
doc_type: issue
id: ISSUE-2008
title: RX 경로 FIFO overflow
date: 2026-08-06
author: lee.jh
status: closed
severity: minor
tags: ["phy", "phy", "modem-b1"]
module: ["phy_ctrl"]
hw: {"chip": "MDM9x", "rev": "B1"}
related: {"cls": ["CL-55308"], "issues": []}
---

# ISSUE-2008 RX 경로 FIFO overflow

## 현상
RX 경로 FIFO overflow. PHY 컨트롤러 블록에서 재현되며 HW rev B1 에서 발생 빈도가 높다. 로그에 `PHY_RST_REG` 레지스터 값 이상이 기록된다.

## 원인
DMA descriptor 정렬 오류. PHY 컨트롤러 초기화 시퀀스 5단계에서 문제가 시작된다.

## 분석
PHY 컨트롤러 파형을 캡처해 t_setup 을 측정했고, PHY_RST_REG 를 읽어 phy_ctrl 상태를 확인했다. 유사 이슈 없음 와 원인이 겹친다. 재현 TC 는 TC-PHY-CTRL-008.

## 수정
CL-55308 에서 DMA descriptor 정렬 오류 수정 반영. 상세는 CL 문서 참조.

## 검증
VP(virtual platform) 에서 100회 반복 시험 통과, 실기판 B1 에서 24시간 스트레스 테스트 통과.
