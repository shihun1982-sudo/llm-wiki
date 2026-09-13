---
schema_version: 1
doc_type: tc_list
id: TC-PHY-CTRL-002
title: PHY 컨트롤러 검증 TC
date: 2026-06-12
author: qa.team
status: active
tags: ["tc", "phy"]
module: ["phy_ctrl"]
related: {"issues": ["ISSUE-2002"]}
---

# TC-PHY-CTRL-002 PHY 컨트롤러 검증 TC

## 목적
PHY 컨트롤러 의 AGC 수렴이 3ms 이상 지연 시나리오 검증.

## 사전 조건
HW rev B1, FW 3.2 이상.

## 절차
1. 초기화 2. 부하 인가 3. `PHY_RST_REG` 값 기록

## 기대 결과
AGC 수렴이 3ms 이상 지연 미발생, 인터럽트 지연 < 1ms.
