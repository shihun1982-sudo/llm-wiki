# -*- coding: utf-8 -*-
"""한국어 상대/절대 시간 표현 → 날짜 범위.

  parse("지난주 리뷰한 CL")  → {"from": "2026-09-01", "to": "2026-09-07", "expr": "지난주", "query": "리뷰한 CL", "kind": "relative"}
지원: 오늘·어제·그저께·내일 · 이번주/지난주/다음주 · 이번달/지난달/다음달 · 올해/작년/내년 · N일전/N주전/N개월전/N년전 · 최근 N일/주/개월
      · 상반기/하반기 · N분기/Q1~Q4 · YYYY년 [M월 [D일]] · YYYY-MM[-DD] · M월 D일 · YYYY.MM.DD
지역: timezone(IANA, 기본 Asia/Seoul) · week_start(mon|sun). now 를 주입하면 결정적으로 테스트 가능.
"""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

try:
    from zoneinfo import ZoneInfo  # Python 3.9+
except ImportError:  # pragma: no cover
    ZoneInfo = None  # type: ignore

_KNUM = {"하루": 1, "이틀": 2, "사흘": 3, "나흘": 4, "닷새": 5, "일주일": 7, "한": 1, "두": 2, "세": 3, "네": 4, "다섯": 5, "여섯": 6, "일곱": 7, "여덟": 8, "아홉": 9, "열": 10}


def _today(timezone: str = "Asia/Seoul", now: Optional[datetime] = None) -> date:
    if now is not None:
        return now.date() if isinstance(now, datetime) else now
    if ZoneInfo is not None:
        try:
            return datetime.now(ZoneInfo(timezone)).date()
        except Exception:
            pass
    return date.today()


def _week_bounds(d: date, week_start: str = "mon") -> Tuple[date, date]:
    off = d.weekday() if week_start != "sun" else (d.weekday() + 1) % 7
    s = d - timedelta(days=off)
    return s, s + timedelta(days=6)


def _month_bounds(y: int, m: int) -> Tuple[date, date]:
    s = date(y, m, 1)
    e = date(y + (m // 12), (m % 12) + 1, 1) - timedelta(days=1)
    return s, e


def _shift_month(d: date, k: int) -> Tuple[int, int]:
    m = d.month - 1 + k
    return d.year + m // 12, m % 12 + 1


def _num(s: str) -> Optional[int]:
    s = s.strip()
    if s.isdigit():
        return int(s)
    return _KNUM.get(s)


_PATTERNS: List[Tuple[str, str]] = [
    (r"그저께|그제", "day-2"), (r"어제", "day-1"), (r"오늘", "day0"), (r"내일", "day+1"), (r"모레", "day+2"),
    (r"지난\s?주|저번\s?주|전주", "week-1"), (r"이번\s?주|금주", "week0"), (r"다음\s?주|차주", "week+1"),
    (r"지난\s?달|저번\s?달|전월", "month-1"), (r"이번\s?달|금월|당월", "month0"), (r"다음\s?달|익월", "month+1"),
    (r"작년|지난\s?해|전년", "year-1"), (r"올해|금년|이번\s?해", "year0"), (r"내년", "year+1"),
    (r"재작년", "year-2"),
    (r"최근\s?(\d+|[가-힣]{1,2})\s?(일|주|개월|달|년)(간|동안)?", "recent"), (r"(\d+|[가-힣]{1,2})\s?(일|주|개월|달|년)\s?(전|이전)", "ago"),
    (r"(지난|이번|올)\s?상반기", "h1"), (r"(지난|이번|올)\s?하반기", "h2"), (r"상반기", "h1"), (r"하반기", "h2"),
    (r"(\d{4})년\s?([1-4])\s?분기", "yq"), (r"([1-4])\s?분기", "q"), (r"\bQ([1-4])\b", "q"), (r"(\d{4})\s?Q([1-4])", "yq2"),
    (r"(20\d{2})년\s?(\d{1,2})월\s?(\d{1,2})일", "ymd"), (r"(20\d{2})[-./](\d{1,2})[-./](\d{1,2})", "ymd"),
    (r"(20\d{2})년\s?(\d{1,2})월", "ym"), (r"(20\d{2})[-./](\d{1,2})(?![-./\d])", "ym"),
    (r"(\d{1,2})월\s?(\d{1,2})일", "md"), (r"(20\d{2})년", "y"), (r"(\d{1,2})월(?!\s?\d{1,2}일)", "m"),
]


def parse(query: str, timezone: str = "Asia/Seoul", week_start: str = "mon", now: Optional[datetime] = None) -> Optional[Dict[str, Any]]:
    """시간 표현이 없으면 None. 있으면 {from, to, expr, query(표현 제거), kind}."""
    if not query:
        return None
    today = _today(timezone, now)
    for pat, kind in _PATTERNS:
        m = re.search(pat, query)
        if not m:
            continue
        s: Optional[date] = None
        e: Optional[date] = None
        try:
            if kind.startswith("day"):
                k = int(kind[3:])
                s = e = today + timedelta(days=k)
            elif kind.startswith("week"):
                k = int(kind[4:])
                ws, we = _week_bounds(today + timedelta(days=7 * k), week_start)
                s, e = ws, we
            elif kind.startswith("month"):
                k = int(kind[5:])
                y, mo = _shift_month(today, k)
                s, e = _month_bounds(y, mo)
            elif kind.startswith("year"):
                k = int(kind[4:])
                s, e = date(today.year + k, 1, 1), date(today.year + k, 12, 31)
            elif kind in ("recent", "ago"):
                n = _num(m.group(1)) or 1
                unit = m.group(2)
                days = {"일": 1, "주": 7, "개월": 30, "달": 30, "년": 365}[unit]
                if kind == "recent":
                    s, e = today - timedelta(days=n * days), today
                else:
                    if unit in ("개월", "달"):
                        y, mo = _shift_month(today, -n)
                        s, e = _month_bounds(y, mo) if n >= 1 else (today, today)
                        # 'N개월 전' 은 그 달 전체
                    elif unit == "년":
                        s, e = date(today.year - n, 1, 1), date(today.year - n, 12, 31)
                    elif unit == "주":
                        s, e = _week_bounds(today - timedelta(days=7 * n), week_start)
                    else:
                        s = e = today - timedelta(days=n)
            elif kind in ("h1", "h2"):
                y = today.year - (1 if (m.groups() and m.group(1) == "지난") else 0)
                s, e = (date(y, 1, 1), date(y, 6, 30)) if kind == "h1" else (date(y, 7, 1), date(y, 12, 31))
            elif kind in ("q", "yq", "yq2"):
                if kind == "q":
                    y, qn = today.year, int(m.group(1))
                elif kind == "yq":
                    y, qn = int(m.group(1)), int(m.group(2))
                else:
                    y, qn = int(m.group(1)), int(m.group(2))
                s, e = date(y, 3 * (qn - 1) + 1, 1), _month_bounds(y, 3 * qn)[1]
            elif kind == "ymd":
                s = e = date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
            elif kind == "ym":
                s, e = _month_bounds(int(m.group(1)), int(m.group(2)))
            elif kind == "md":
                s = e = date(today.year, int(m.group(1)), int(m.group(2)))
                if s > today:
                    s = e = date(today.year - 1, int(m.group(1)), int(m.group(2)))
            elif kind == "y":
                s, e = date(int(m.group(1)), 1, 1), date(int(m.group(1)), 12, 31)
            elif kind == "m":
                mo = int(m.group(1))
                if not 1 <= mo <= 12:
                    continue
                y = today.year if mo <= today.month else today.year - 1
                s, e = _month_bounds(y, mo)
        except (ValueError, OverflowError):
            continue
        if s is None or e is None:
            continue
        rest = (query[:m.start()] + " " + query[m.end():]).strip()
        rest = re.sub(r"^\s*(에|의|동안|간|중|부터|까지)\s+", "", rest)
        rest = re.sub(r"\s{2,}", " ", rest)
        return {"from": s.isoformat(), "to": e.isoformat(), "expr": m.group(0), "query": rest or query, "kind": kind,
                "from_ts": datetime(s.year, s.month, s.day).timestamp(), "to_ts": datetime(e.year, e.month, e.day, 23, 59, 59).timestamp()}
    return None


def in_range(doc_ts: float, scope: Dict[str, Any]) -> bool:
    return bool(doc_ts) and scope["from_ts"] <= doc_ts <= scope["to_ts"]
