---
schema_version: 1
doc_type: hw_design
id: HWD-PHY-TIMING-A2
title: PHY 타이밍 및 레지스터 사양 rev A2
date: 2026-06-03
author: hw.team
status: released
tags: ["phy", "timing", "modem-a2"]
hw: {"chip": "MDM9x", "rev": "A2"}
related: {"docs": []}
---

# HWD-PHY-TIMING-A2 PHY 타이밍 및 레지스터 사양 rev A2

## 개요
MDM9x rev A2 의 PHY 블록 타이밍과 SW 제어 레지스터.

## 레지스터
| 이름 | 오프셋 | 설명 |
|---|---|---|
| PHY_RST_REG | 0x0000 | 리셋 제어 |
| RX_DMA_CTRL | 0x0010 | DMA 임계값(FIFO_THR[7:0]) |
| TXPWR_GAIN_TBL | 0x0100 | gain 테이블 32 entry |

## 타이밍
- t_setup: 12ns
- t_hold: 4ns
- 리셋 해제 후 안정화: 50us

## SW 제어 시퀀스
1. CLK_DIV_SEL 설정 → 2. PHY_RST_REG=1 → 3. 50us 대기 → 4. RX_DMA_CTRL FIFO_THR=0x40 → 5. PHY_RST_REG=0

## Revision History
| rev | 날짜 | 변경 |
|---|---|---|
| A2 | 2026-06-03 | 초기 릴리스 |
