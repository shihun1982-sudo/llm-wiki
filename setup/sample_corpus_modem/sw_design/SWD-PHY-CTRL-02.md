---
schema_version: 1
doc_type: sw_design
id: SWD-PHY-CTRL-02
title: PHY 컨트롤러 드라이버 설계
date: 2026-06-03
author: kim.ys
status: approved
tags: ["phy", "design"]
module: ["phy_ctrl"]
hw: {"chip": "MDM9x", "rev": "B1"}
related: {"docs": ["HWD-PHY-TIMING-B1"], "rules": ["RULE-ISR-001"]}
---

# SWD-PHY-CTRL-02 PHY 컨트롤러 드라이버 설계

## 개요
PHY 컨트롤러 드라이버는 `PHY_RST_REG` 레지스터로 PHY 컨트롤러 를 제어한다.

## 구조
- init(): 클럭 enable → 리셋 해제 → CFG 기록 순서를 지킨다
- isr(): 인터럽트 컨텍스트에서는 blocking 금지 (RULE-ISR-001)
- code map: drivers/phy_ctrl.c (핵심 로직), include/phy_ctrl.h (레지스터 맵), test/tc_phy_ctrl.py (TC)

## 인터페이스
phy_ctrl_start(), phy_ctrl_stop(), phy_ctrl_get_status()

## 동작 흐름
1. 상위 계층이 start 요청 → 2. 파라미터 검증 → 3. `PHY_RST_REG` 기록 → 4. 완료 인터럽트 대기 (타임아웃 5ms)

## 제약
HW rev B1 에서는 t_setup 8ns 를 만족해야 하므로 연속 레지스터 기록 사이에 2 사이클 nop 필요.
