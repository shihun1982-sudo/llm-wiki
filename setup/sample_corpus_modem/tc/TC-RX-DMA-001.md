---
schema_version: 1
doc_type: tc_list
id: TC-RX-DMA-001
title: RX DMA 검증 TC
date: 2026-06-11
author: qa.team
status: active
tags: ["tc", "rx"]
module: ["rx_dma"]
related: {"issues": ["ISSUE-2001"]}
---

# TC-RX-DMA-001 RX DMA 검증 TC

## 목적
RX DMA 의 DMA underrun 발생 후 PHY 재시작 실패 시나리오 검증.

## 사전 조건
HW rev B1, FW 3.2 이상.

## 절차
1. 초기화 2. 부하 인가 3. `RX_DMA_CTRL` 값 기록

## 기대 결과
DMA underrun 발생 후 PHY 재시작 실패 미발생, 인터럽트 지연 < 1ms.
