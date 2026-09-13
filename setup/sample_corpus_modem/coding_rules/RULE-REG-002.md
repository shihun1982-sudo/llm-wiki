---
schema_version: 1
doc_type: coding_rule
id: RULE-REG-002
title: 레지스터 접근 순서 규칙
date: 2026-06-26
author: sw.lead
status: active
tags: ["rule", "review"]
scope: ["drivers/*", "isr/*"]
related: {"issues": ["ISSUE-2003"]}
---

# RULE-REG-002 레지스터 접근 순서 규칙

## 규칙
리셋 해제 전에 CFG 계열 레지스터를 기록하지 않는다. 연속 기록 사이에는 HW 문서의 t_setup 을 만족하는 nop 을 둔다.

## 근거
ISSUE-2003 에서 규칙 위반으로 타이밍 위반이 발생했다.

## 예시
```c
// bad
while(!ready) {}
// good
schedule_work(&done_work);
```

## 예외
부트로더 초기화 구간은 예외 (리뷰어 승인 필요).
