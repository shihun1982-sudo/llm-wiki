---
schema_version: 1
doc_type: issue
id: ISSUE-2001
title: DMA underrun 발생 후 PHY 재시작 실패
date: 2026-06-04
author: hong.gd
status: fixed
severity: major
tags: ["rx", "rx", "modem-a2"]
module: ["rx_dma"]
hw: {"chip": "MDM9x", "rev": "A2"}
related: {"cls": ["CL-55301"], "issues": ["ISSUE-2004"]}
---

# ISSUE-2001 DMA underrun 발생 후 PHY 재시작 실패

## 현상
DMA underrun 발생 후 PHY 재시작 실패. RX DMA 블록에서 재현되며 HW rev A2 에서 발생 빈도가 높다. 로그에 `RX_DMA_CTRL` 레지스터 값 이상이 기록된다.

## 원인
FIFO 임계값 설정 오류 (0x20 → 0x40 필요). RX DMA 초기화 시퀀스 2단계에서 문제가 시작된다.

## 분석
RX DMA 파형을 캡처해 t_setup 을 측정했고, RX_DMA_CTRL 를 읽어 rx_dma 상태를 확인했다. 유사 이슈 ISSUE-2004 와 원인이 겹친다. 재현 TC 는 TC-RX-DMA-001.

## 수정
CL-55301 에서 FIFO 임계값 설정 오류 수정 반영. 상세는 CL 문서 참조.

## 검증
VP(virtual platform) 에서 100회 반복 시험 통과.
