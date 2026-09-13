---
schema_version: 1
doc_type: coding_rule
id: RULE-ISR-001
title: 인터럽트 핸들러 규칙
date: 2026-06-06
author: sw.lead
status: active
tags: ["rule", "review"]
scope: ["drivers/*", "isr/*"]
related: {"issues": ["ISSUE-2006"]}
---

# RULE-ISR-001 인터럽트 핸들러 규칙

## 규칙
ISR 내부에서 blocking 대기(polling loop, sleep, mutex)를 사용하지 않는다. 지연 작업은 workqueue 로 넘긴다.

## 근거
ISSUE-2006 에서 규칙 위반으로 타이밍 위반이 발생했다.

## 예시
```c
// bad
while(!ready) {}
// good
schedule_work(&done_work);
```

## 예외
부트로더 초기화 구간은 예외 (리뷰어 승인 필요).
