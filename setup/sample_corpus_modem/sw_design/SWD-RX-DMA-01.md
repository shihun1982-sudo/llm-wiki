---
schema_version: 1
doc_type: sw_design
id: SWD-RX-DMA-01
title: RX DMA 드라이버 설계
date: 2026-06-02
author: hong.gd
status: approved
tags: ["rx", "design"]
module: ["rx_dma"]
hw: {"chip": "MDM9x", "rev": "B1"}
related: {"docs": ["HWD-PHY-TIMING-B1"], "rules": ["RULE-ISR-001"]}
---

# SWD-RX-DMA-01 RX DMA 드라이버 설계

## 개요
RX DMA 드라이버는 `RX_DMA_CTRL` 레지스터로 RX DMA 를 제어한다.

## 구조
- init(): 클럭 enable → 리셋 해제 → CFG 기록 순서를 지킨다
- isr(): 인터럽트 컨텍스트에서는 blocking 금지 (RULE-ISR-001)
- code map: drivers/rx_dma.c (핵심 로직), include/rx_dma.h (레지스터 맵), test/tc_rx_dma.py (TC)

## 인터페이스
rx_dma_start(), rx_dma_stop(), rx_dma_get_status()

## 동작 흐름
1. 상위 계층이 start 요청 → 2. 파라미터 검증 → 3. `RX_DMA_CTRL` 기록 → 4. 완료 인터럽트 대기 (타임아웃 5ms)

## 제약
HW rev B1 에서는 t_setup 8ns 를 만족해야 하므로 연속 레지스터 기록 사이에 2 사이클 nop 필요.
