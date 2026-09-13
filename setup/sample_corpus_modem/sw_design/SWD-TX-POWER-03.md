---
schema_version: 1
doc_type: sw_design
id: SWD-TX-POWER-03
title: TX 전력 제어 드라이버 설계
date: 2026-06-04
author: lee.jh
status: approved
tags: ["tx", "design"]
module: ["tx_power"]
hw: {"chip": "MDM9x", "rev": "B1"}
related: {"docs": ["HWD-PHY-TIMING-B1"], "rules": ["RULE-ISR-001"]}
---

# SWD-TX-POWER-03 TX 전력 제어 드라이버 설계

## 개요
TX 전력 제어 드라이버는 `TXPWR_GAIN_TBL` 레지스터로 TX 전력 제어 를 제어한다.

## 구조
- init(): 클럭 enable → 리셋 해제 → CFG 기록 순서를 지킨다
- isr(): 인터럽트 컨텍스트에서는 blocking 금지 (RULE-ISR-001)
- code map: drivers/tx_power.c (핵심 로직), include/tx_power.h (레지스터 맵), test/tc_tx_power.py (TC)

## 인터페이스
tx_power_start(), tx_power_stop(), tx_power_get_status()

## 동작 흐름
1. 상위 계층이 start 요청 → 2. 파라미터 검증 → 3. `TXPWR_GAIN_TBL` 기록 → 4. 완료 인터럽트 대기 (타임아웃 5ms)

## 제약
HW rev B1 에서는 t_setup 8ns 를 만족해야 하므로 연속 레지스터 기록 사이에 2 사이클 nop 필요.
