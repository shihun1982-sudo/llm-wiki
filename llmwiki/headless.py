# -*- coding: utf-8 -*-
"""Generic Headless Agent Provider — opencode / claude / codex 같은 CLI 에이전트를 non-interactive 로 subprocess 실행해
LLM 역할(질의 확장·답변 합성·포렌식·자가진화 리뷰 …)에 쓴다.

agents.json (프로젝트 루트, 없으면 기본값 생성):
  {"opencode": {
     "command": ["opencode", "run", "--format", "json", "-m", "{model}", "{prompt}"],
     "prompt_mode": "stdin",            # stdin | arg | file  (표준입력 / {prompt} 자리에 인자 / 임시 파일 경로 {prompt_file})
                                        #   stdin 이 기본(코드·with_defaults 모두): opencode run · claude -p · codex exec 모두 파이프된 표준입력을 프롬프트로 받는다.
                                        #   arg 는 프롬프트(코퍼스 발췌 수만 자)가 프로세스 목록에 노출되고 Windows 32K 인자 한계(WinError 206)에 걸린다.
     "arg_max_chars": 30000,            # arg 모드 가드: argv 총 길이가 이를 넘으면 자동으로 stdin(템플릿에 {prompt_file} 이 있으면 file)으로 전환.
                                        #   Windows CreateProcess 한계 32,767 에서 여유를 둔 값. 0 = 가드 끔(넘치면 OSError WinError 206 으로 실패).
                                        #   전환하면 결과 dict/trace usage 에 prompt_mode_fallback="arg→stdin (N chars > arg_max_chars=30000)" 을 남기고 warning 로그 1줄.
     "files_flag": "-f",                # 첨부 파일마다 "-f <path>" (비우면 첨부 미지원 → 프롬프트에 인라인, inline_attach_chars 까지)
     "output": "ndjson",                # ndjson | json | text
     "text_paths": ["part.text", "text", "content", "message.content", "result"],   # 이벤트에서 텍스트를 뽑는 경로(점 표기)
     "usage_paths": {"input": ["usage.input_tokens", "tokens.input"], "output": ["usage.output_tokens", "tokens.output"]},
     "model": "",                       # 기본 모델 (역할 model 이 비면 사용)
     "timeout_s": 300,                  # 1회 실행 제한(초). 넘으면 프로세스를 죽이고 재시도
     "retries": 3,                      # transient 실패(retry_on) 재시도 횟수 → 최대 1+retries 회 실행
     "retry_backoff_s": 5,              # 재시도 사이 대기(초) × 시도 번호
     "retry_on": ["timeout", "exec", "exit", "empty", "stall"],   # 재시도 대상: 타임아웃 / 실행 실패 / 종료 코드≠0 / 빈 출력 / 무응답
     "env_passthrough": [...],          # 자식에게 넘길 환경변수 **허용 목록**(fnmatch 패턴 가능, "*" = 전부 — 안전하지 않음). 기본 ENV_PASSTHROUGH_DEFAULT
     "env": {},                         # 허용 목록과 별개로 항상 넘기는 값 (예 {"ANTHROPIC_API_KEY": "..."})
     "inline_attach_chars": 60000,      # files_flag 가 없을 때 첨부 파일을 프롬프트에 인라인할 때 파일당 최대 글자
     "cwd": "{project_root}", "max_output_chars": 400000 }}
provider 지정: llm_provider="headless:opencode" 또는 llm_roles.answer.provider="headless:opencode".
결과: 텍스트를 순서대로 이어붙여 반환 → 기존 parse_json 으로 구조화 결과 회수.
최종 실패는 LLMError(transient) 로 올라가고 BaseLLM 이 incident 로 기록 → 질의 결과 llm_report / 빌드 alerts 에 보고된다.
로그의 argv 는 프롬프트 인자를 `<prompt:N chars>` 로 가린다(코퍼스 발췌가 로그에 남지 않도록).
`python -m llmwiki.headless --mock` 은 테스트용 목업 에이전트(표준입력 프롬프트 → ndjson 이벤트). `--mock --sleep N` 은 N초 멈춤(타임아웃 테스트).
"""
from __future__ import annotations

import codecs
import fnmatch
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from typing import Any, Dict, List, Optional

from .config import ROOT, path_for
from . import progress as _pg
from .providers import BaseLLM, LLMError

# 자식 프로세스에 넘기는 환경변수 허용 목록의 기본값 (2026-09-18, CODE_REVIEW_0917 §2.2-3).
# 예전에는 os.environ 전체를 넘겼다 — .env 의 PAT · LLMWIKI_PASSWORD · OIDC secret 이 모두 에이전트 프로세스로 흘러갔다.
# 이제는 실행에 필요한 최소(경로·홈·임시·로케일·프록시)만 넘기고, 에이전트가 더 필요로 하는 것은
# agents.json 의 env_passthrough(패턴 가능: "OPENCODE_*", "ANTHROPIC_*") 또는 env(값 직접 지정)에 적는다.
ENV_PASSTHROUGH_DEFAULT: List[str] = [
    "PATH", "HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA", "TEMP", "TMP", "SYSTEMROOT", "COMSPEC",
    "LANG", "LC_ALL", "PYTHONIOENCODING", "HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY",
]

RETRY_DEFAULTS: Dict[str, Any] = {
    "timeout_s": 300,                 # 1회 실행의 전체 제한(초)
    "retries": 3,
    "retry_backoff_s": 5,
    "retry_on": ["timeout", "exec", "exit", "empty", "stall"],
    # ---- 무응답 대책 (2026-09-16) ----
    # opencode 는 붙었다가 **아무 것도 내놓지 않고 매달리는** 일이 잦다. 예전에는 timeout_s(기본 300초)를
    # 꼬박 기다린 뒤에야 실패했고, retries 까지 더하면 한 번의 질의가 20분을 날렸다.
    # 이제는 출력이 멎으면 그 자체로 실패로 보고 바로 다음 시도로 넘어간다.
    "stall_timeout_s": 60,            # 마지막 출력 뒤 이만큼 새 출력이 없으면 '멎었다'고 보고 죽인다 (0 = 끔)
    "first_output_timeout_s": 120,    # 첫 출력까지 기다리는 시간 (기동이 느린 에이전트용; 0 = stall_timeout_s 와 같게)
    "keep_partial_on_timeout": True,  # 멎기 전까지 쓸 만한 텍스트를 냈으면 버리지 않고 그것을 쓴다
    "failure_log_chars": 2000,        # 실패 시 로그에 남길 stdout/stderr 꼬리 길이
    # ---- 격리·크기 (2026-09-18) ----
    "env_passthrough": list(ENV_PASSTHROUGH_DEFAULT),   # 자식 환경변수 허용 목록 (fnmatch 패턴; "*" = 전부 = 예전 동작, 안전하지 않음)
    "inline_attach_chars": 60000,     # files_flag 가 없는 에이전트: 첨부 파일을 프롬프트에 인라인할 때 파일당 최대 글자
    # ---- arg 모드 길이 가드 (2026-09-18, IMPLEMENTATION_PLAN_0918_2 §2.1) ----
    # WinError 206 (The filename or extension is too long) = Windows CreateProcess 명령줄 32,767자 한계.
    # 운영자가 prompt_mode="arg" 를 고른 채 시스템 프롬프트 + 컨텍스트 수만 자가 오면 OSError 로 죽고 원인 문구가 없었다.
    # argv 총 길이(각 인자 길이 + 1 의 합)가 이 값을 넘으면 stdin(템플릿에 {prompt_file} 이 있으면 file)으로 자동 전환한다. 0 = 끔.
    "arg_max_chars": 30000,
}

DEFAULT_AGENTS: Dict[str, Dict[str, Any]] = {
    "opencode": {
        "desc": "OpenCode CLI (opencode run --format json). 모델은 provider/model 형식 (예 anthropic/claude-sonnet-4-5).",
        "command": ["opencode", "run", "--format", "json", "-m", "{model}", "{prompt}"],
        "prompt_mode": "stdin", "files_flag": "-f", "output": "ndjson",
        "text_paths": ["part.text", "text", "content", "message.content", "result"],
        "usage_paths": {"input": ["usage.input_tokens", "tokens.input", "part.tokens.input"], "output": ["usage.output_tokens", "tokens.output", "part.tokens.output"]},
        "model": "", "timeout_s": 300, "retries": 3, "retry_backoff_s": 5, "retry_on": ["timeout", "exec", "exit", "empty"],
        "cwd": "{project_root}", "env": {}, "max_output_chars": 400000,
    },
    "claude": {
        "desc": "Claude Code CLI headless (claude -p --output-format json).",
        "command": ["claude", "-p", "--output-format", "json", "--model", "{model}", "{prompt}"],
        "prompt_mode": "stdin", "files_flag": "", "output": "json",
        "text_paths": ["result", "content", "text"],
        "usage_paths": {"input": ["usage.input_tokens"], "output": ["usage.output_tokens"]},
        "model": "claude-sonnet-5", "timeout_s": 300, "retries": 3, "retry_backoff_s": 5, "retry_on": ["timeout", "exec", "exit", "empty"],
        "cwd": "{project_root}", "env": {}, "max_output_chars": 400000,
    },
    "codex": {
        "desc": "OpenAI Codex CLI (codex exec --json).",
        "command": ["codex", "exec", "--json", "-m", "{model}", "{prompt}"],
        "prompt_mode": "stdin", "files_flag": "", "output": "ndjson",
        "text_paths": ["item.text", "text", "content", "message"],
        "usage_paths": {"input": ["usage.input_tokens"], "output": ["usage.output_tokens"]},
        "model": "", "timeout_s": 300, "retries": 3, "retry_backoff_s": 5, "retry_on": ["timeout", "exec", "exit", "empty"],
        "cwd": "{project_root}", "env": {}, "max_output_chars": 400000,
    },
    "mock": {
        "desc": "테스트용 목업 에이전트 (네트워크 없음). 표준입력 프롬프트 → ndjson 이벤트. {python} = 현재 인터프리터(이식성).",
        "command": ["{python}", "-m", "llmwiki.headless", "--mock"],
        "prompt_mode": "stdin", "files_flag": "--file", "output": "ndjson",
        "text_paths": ["part.text", "text"],
        "usage_paths": {"input": ["usage.input_tokens"], "output": ["usage.output_tokens"]},
        "model": "mock", "timeout_s": 60, "retries": 1, "retry_backoff_s": 0, "retry_on": ["timeout", "exec", "exit", "empty"],
        "cwd": "{project_root}", "env": {}, "max_output_chars": 100000,
    },
}


def with_defaults(cfg: Dict[str, Any]) -> Dict[str, Any]:
    """agents.json 항목에 없는 재시도·격리 키를 기본값으로 채운다 (구 파일 호환).
    env_passthrough 는 명시된 빈 목록([])을 존중한다 — "agent env 외에는 아무것도 넘기지 않음" 이라는 뜻이므로 기본값으로 덮지 않는다."""
    out = dict(cfg or {})
    for k, v in RETRY_DEFAULTS.items():
        if k not in out or out[k] in (None, ""):
            out[k] = json.loads(json.dumps(v))
    if not out.get("prompt_mode"):
        out["prompt_mode"] = "stdin"
    # 'stall'(무응답)은 2026-09-16 에 생긴 재시도 사유다. 예전 파일의 retry_on 에는 없어서
    # 무응답을 잡아내고도 재시도하지 않게 되므로 여기서 채워 준다. 원하지 않으면 파일에서 지우면 되지만,
    # 그때는 retry_on 에 "-stall" 을 넣어 명시적으로 끈다.
    ro = out.get("retry_on")
    if isinstance(ro, list):
        if "-stall" in ro:
            out["retry_on"] = [x for x in ro if x not in ("-stall", "stall")]
        elif "stall" not in ro:
            out["retry_on"] = list(ro) + ["stall"]
    return out


def agents_path() -> str:
    return path_for("agents")


def load_agents() -> Dict[str, Dict[str, Any]]:
    p = agents_path()
    if not os.path.exists(p):
        save_agents(DEFAULT_AGENTS)
        return json.loads(json.dumps(DEFAULT_AGENTS))
    with open(p, "r", encoding="utf-8") as f:
        data = json.load(f)
    out = {k: with_defaults(v) for k, v in data.items() if not k.startswith("_") and isinstance(v, dict)}
    if "mock" not in out:   # 테스트/배선 확인용은 항상 제공
        out["mock"] = json.loads(json.dumps(DEFAULT_AGENTS["mock"]))
    return out


def save_agents(data: Dict[str, Dict[str, Any]]) -> str:
    """agents.json 을 쓴다. 모든 항목에 기본값을 채워 **명시적으로** 남긴다 — 다른 환경으로 옮길 때 파일만 보고 무엇을 바꿀지 알 수 있게."""
    p = agents_path()
    os.makedirs(os.path.dirname(p) or ".", exist_ok=True)
    out = {"_comment": "Headless agent 명령 템플릿. {model} {prompt} {prompt_file} {project_root} {python} 치환. provider 는 headless:<이름>. "
                       "command[0] 은 PATH 에서 찾는다(Windows 의 .cmd 셸 포함); 못 찾으면 절대 경로를 적는다. "
                       "prompt_mode: stdin(기본; opencode/claude/codex 모두 파이프 입력을 프롬프트로 받음) | arg({prompt} 인자 — 프로세스 목록 노출·Windows 32K 한계) | file({prompt_file} 임시 파일). "
                       "arg_max_chars(기본 30000): arg 모드에서 argv 총 길이가 넘으면 stdin(템플릿에 {prompt_file} 이 있으면 file)으로 자동 전환하고 "
                       "결과/trace 에 prompt_mode_fallback 을 남긴다(WinError 206 방지); 0 = 가드 끔. "
                       "재시도: timeout_s(1회 실행 전체 제한, 기본 300=5분) · retries(기본 3) · retry_backoff_s · retry_on[timeout|exec|exit|empty|stall]. "
                       "무응답 대책: stall_timeout_s(마지막 출력 뒤 이만큼 조용하면 죽이고 재시도, 기본 60; 바이트 단위로 감지) · "
                       "first_output_timeout_s(첫 출력까지, 기본 120) · keep_partial_on_timeout(멎기 전 받은 답을 쓸지, 기본 true) · "
                       "failure_log_chars(실패 로그에 남길 stdout/stderr 꼬리, 기본 2000). "
                       "격리: env_passthrough(자식에게 넘길 환경변수 허용 목록, fnmatch 패턴 가능, \"*\" = 전부 = 안전하지 않음; .env 의 PAT/비밀번호는 기본으로 넘어가지 않는다) · "
                       "env(항상 넘길 값) · inline_attach_chars(files_flag 없는 에이전트의 첨부 인라인 상한, 기본 60000). "
                       "최종 실패는 질의 결과 llm_report / 빌드 alerts 에 보고되고 답변은 추출식으로 대체된다. 설명: docs/BRINGUP_GUIDE.md §4.3"}
    for k, v in data.items():
        out[k] = with_defaults(v) if (isinstance(v, dict) and not k.startswith("_")) else v
    with open(p, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    return p


def _dig(obj: Any, path: str) -> Any:
    cur = obj
    for part in path.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            return None
    return cur


def extract_text(events: List[Any], text_paths: List[str]) -> str:
    """이벤트 목록에서 텍스트 조각을 순서대로 모은다. 같은 텍스트가 누적(delta)형이면 마지막 전체값을 쓴다."""
    parts: List[str] = []
    for ev in events:
        if isinstance(ev, str):
            parts.append(ev)
            continue
        if not isinstance(ev, dict):
            continue
        typ = str(ev.get("type", ""))
        if typ and any(x in typ for x in ("tool", "step", "reasoning", "thinking", "error", "session", "system")):
            # 텍스트가 아닌 이벤트는 건너뛴다 (단, error 는 별도 처리)
            if "error" in typ:
                parts.append("")
            continue
        for tp in text_paths:
            v = _dig(ev, tp)
            if isinstance(v, str) and v:
                parts.append(v)
                break
            if isinstance(v, list):   # content: [{type:text,text:..}]
                for item in v:
                    if isinstance(item, dict) and isinstance(item.get("text"), str):
                        parts.append(item["text"])
                break
    # delta 스트림(각 이벤트가 누적 텍스트)이면 마지막 것이 전체를 포함
    if len(parts) >= 2 and all(parts[i + 1].startswith(parts[i]) for i in range(len(parts) - 1)):
        return parts[-1]
    return "".join(parts)


def extract_usage(events: List[Any], usage_paths: Dict[str, List[str]]) -> Dict[str, int]:
    tin = tout = 0
    for ev in events:
        if not isinstance(ev, dict):
            continue
        for p in usage_paths.get("input", []):
            v = _dig(ev, p)
            if isinstance(v, (int, float)):
                tin = max(tin, int(v)) if tin else int(v)
                break
        for p in usage_paths.get("output", []):
            v = _dig(ev, p)
            if isinstance(v, (int, float)):
                tout = max(tout, int(v)) if tout else int(v)
                break
    return {"input_tokens": tin, "output_tokens": tout}


def parse_output(raw: str, mode: str) -> List[Any]:
    if mode == "text":
        return [raw]
    if mode == "json":
        try:
            return [json.loads(raw)]
        except Exception:
            pass
    events: List[Any] = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            events.append(json.loads(line))
        except Exception:
            events.append(line)
    return events


class HeadlessAgentLLM(BaseLLM):
    def __init__(self, agent: str, model: str, settings: Any = None):
        BaseLLM.__init__(self)
        self.agent = agent
        agents = load_agents()
        self.cfg = agents.get(agent)
        self.name = "headless:%s" % agent
        self.model = model or (self.cfg or {}).get("model") or ""
        self.available = self.cfg is not None and bool(self.cfg.get("command")) and self._exe_ok()
        self._files: List[str] = []
        # 재시도/타임아웃은 agents.json 항목이 우선 (make_llm 이 config.json 값으로 덮어쓰지 않도록 고정 표식)
        if self.cfg:
            try:
                self.retries = int(self.cfg.get("retries", 3))
                self._retries_fixed = True
            except (TypeError, ValueError):
                pass
            try:
                self.retry_backoff_s = float(self.cfg.get("retry_backoff_s", 5) or 0)
                self._backoff_fixed = True
            except (TypeError, ValueError):
                pass
            try:
                self._timeout_fixed = int(self.cfg.get("timeout_s") or 0) or None
                if self._timeout_fixed:
                    self.timeout = self._timeout_fixed
            except (TypeError, ValueError):
                self._timeout_fixed = None

    def _retry_on(self) -> List[str]:
        return [str(x) for x in ((self.cfg or {}).get("retry_on") or RETRY_DEFAULTS["retry_on"])]

    def _num(self, key: str) -> float:
        try:
            v = (self.cfg or {}).get(key, RETRY_DEFAULTS.get(key))
            return float(v if v not in (None, "") else (RETRY_DEFAULTS.get(key) or 0))
        except (TypeError, ValueError):
            return float(RETRY_DEFAULTS.get(key) or 0)

    def _run_streaming(self, args: List[str], stdin_text: Optional[str], cwd: Optional[str], env: Dict[str, str],
                       timeout_s: float, stall_s: float, first_s: float) -> Dict[str, Any]:
        """자식 프로세스를 띄우고 **출력을 흘려 받으며** 감시한다.

        예전에는 `subprocess.run(timeout=…)` 이라 (1) 출력이 멎어도 전체 제한까지 기다렸고,
        (2) 진행 상황이 보이지 않았으며, (3) 죽일 때 그때까지 받은 텍스트를 통째로 버렸다.
        활동 감지는 **바이트 단위**다 (2026-09-18): 예전에는 줄 단위(`for line in stream`)라 줄바꿈 없이 길게 이어지는
        출력(claude `--output-format json` 의 한 줄짜리 결과, 긴 텍스트 스트림)이 "무응답" 으로 오판돼 죽었다.
        반환: {rc, out, err, reason, waited_s, last_gap_s, lines, bytes}
          reason: "" 정상 종료 · "timeout" 전체 시간 초과 · "stall" 무출력 초과 · "cancelled" 사용자 취소
        """
        out_buf: List[str] = []
        err_buf: List[str] = []
        state = {"last": time.monotonic(), "lines": 0, "bytes": 0}
        lock = threading.Lock()
        # 바이너리 파이프 + bufsize=0: raw read(n) 은 n 바이트가 찰 때까지 기다리지 않고 **도착한 만큼** 바로 돌려준다.
        proc = subprocess.Popen(args, stdin=subprocess.PIPE if stdin_text is not None else subprocess.DEVNULL,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=0, cwd=cwd, env=env)

        def pump(stream, buf, count_it):
            dec = codecs.getincrementaldecoder("utf-8")(errors="replace")
            try:
                while True:
                    chunk = stream.read(65536)
                    if not chunk:
                        break
                    text = dec.decode(chunk)
                    with lock:
                        buf.append(text)
                        state["last"] = time.monotonic()
                        if count_it:
                            state["bytes"] += len(chunk)
                            state["lines"] += chunk.count(b"\n")
                tail = dec.decode(b"", final=True)
                if tail:
                    with lock:
                        buf.append(tail)
            except Exception:
                pass

        def feed():
            # 표준입력 쓰기는 별도 스레드: 프롬프트가 파이프 버퍼보다 크고 자식이 stdin 을 읽지 않으면
            # 여기서 영원히 막힌다 — 감시 루프가 timeout/stall/취소로 자식을 죽이면 파이프가 깨져 풀린다.
            try:
                proc.stdin.write(stdin_text.encode("utf-8", errors="replace"))
            except Exception:
                pass
            try:
                proc.stdin.close()
            except Exception:
                pass

        threads = [threading.Thread(target=pump, args=(proc.stdout, out_buf, True), daemon=True),
                   threading.Thread(target=pump, args=(proc.stderr, err_buf, False), daemon=True)]
        if stdin_text is not None:
            threads.append(threading.Thread(target=feed, daemon=True))
        for t in threads:
            t.start()

        t0 = time.monotonic()
        reason = ""
        noted = 0.0
        while True:
            if proc.poll() is not None:
                break
            now = time.monotonic()
            with lock:
                last, nbytes, lines = state["last"], state["bytes"], state["lines"]
            gap = now - last
            limit = (first_s if (nbytes == 0 and first_s > 0) else stall_s)
            if timeout_s > 0 and now - t0 >= timeout_s:
                reason = "timeout"
                break
            if limit > 0 and gap >= limit:
                reason = "stall"
                break
            try:
                if _pg.cancel_requested() is not None:
                    reason = "cancelled"
                    break
            except Exception:
                pass
            if now - noted >= 5.0:      # 진행 상황을 활동 보드/로그에 흘려 준다 (무응답인지 일하는 중인지 구분)
                noted = now
                try:
                    _pg.note("headless %s: %d바이트/%d줄 수신 · 마지막 출력 %.0fs 전 (제한 %.0fs)" % (self.agent, nbytes, lines, gap, limit or timeout_s))
                except Exception:
                    pass
            time.sleep(0.2)
        if reason:
            try:
                proc.terminate()
                proc.wait(timeout=5)
            except Exception:
                try:
                    proc.kill()
                    proc.wait(timeout=5)
                except Exception:
                    pass
        for t in threads:
            t.join(timeout=3)
        for s in (proc.stdout, proc.stderr, proc.stdin):   # 서버는 이 호출을 수천 번 한다 — 핸들을 남기지 않는다
            try:
                if s and not s.closed:
                    s.close()
            except Exception:
                pass
        with lock:
            return {"rc": proc.returncode, "out": "".join(out_buf), "err": "".join(err_buf), "reason": reason,
                    "waited_s": round(time.monotonic() - t0, 1), "last_gap_s": round(time.monotonic() - state["last"], 1),
                    "lines": state["lines"], "bytes": state["bytes"]}

    def _child_env(self) -> Dict[str, str]:
        """자식 프로세스 환경 = env_passthrough 허용 목록에 맞는 os.environ 항목 + 에이전트 env.
        "*" 가 목록에 있으면 전부 넘긴다(예전 동작; .env 의 PAT·비밀번호까지 넘어가므로 문서에 '안전하지 않음' 으로 적는다)."""
        allow = (self.cfg or {}).get("env_passthrough", RETRY_DEFAULTS["env_passthrough"])
        if isinstance(allow, str):
            allow = [allow]
        pats = [str(x) for x in (allow or []) if str(x)]
        env: Dict[str, str] = {}
        if "*" in pats:
            env = dict(os.environ)
        else:
            for k, v in os.environ.items():
                for pat in pats:
                    # Windows 는 환경변수 이름이 대소문자를 가리지 않는다 → 대문자로 맞춰 비교
                    if fnmatch.fnmatchcase(k.upper(), pat.upper()):
                        env[k] = v
                        break
        env.update({str(k): str(v) for k, v in ((self.cfg or {}).get("env") or {}).items()})
        return env

    @staticmethod
    def _mask_argv(args: List[str], prompt: str) -> List[str]:
        """로그용 argv: 프롬프트(또는 프롬프트+인라인 첨부)를 담은 인자를 `<prompt:N chars>` 로 바꾼다."""
        out: List[str] = []
        for i, a in enumerate(args):
            if i > 0 and prompt and (a == prompt or (len(prompt) >= 16 and prompt in a)):
                out.append("<prompt:%d chars>" % len(a))
            else:
                out.append(a[:120])
        return out

    def _exe(self) -> str:
        """command[0] 을 실제 실행 파일 경로로 해석. {python} → 현재 인터프리터.
        Windows 에서 npm/bun 이 설치한 opencode 는 opencode.cmd 셸 스크립트인데 CreateProcess 는 PATHEXT 를 보지 않으므로
        shutil.which 로 .cmd/.bat 까지 찾아 절대 경로로 실행해야 한다 (예전엔 ping 은 ok 인데 실제 호출은 WinError 2 로 실패)."""
        cmd = (self.cfg or {}).get("command") or []
        if not cmd:
            return ""
        exe = str(cmd[0]).replace("{python}", sys.executable).replace("{project_root}", ROOT)
        exe = os.path.expandvars(os.path.expanduser(exe))
        return shutil.which(exe) or (exe if os.path.exists(exe) else "")

    def _exe_ok(self) -> bool:
        return bool(self._exe())

    def ping(self) -> Dict[str, Any]:
        if not self.cfg:
            return {"ok": False, "ms": 0.0, "detail": "agent '%s' not in agents.json" % self.agent}
        exe = self.cfg["command"][0]
        path = self._exe()
        if not path:
            return {"ok": False, "ms": 0.0, "detail": "executable not found: %s (PATH 확인, 또는 agents.json command[0] 에 절대 경로)" % exe}
        return {"ok": True, "ms": 0.0, "detail": "%s found (%s); model=%s — 실제 실행 확인은 'models test --live'" % (exe, path, self.model or "(agent default)")}

    def _prompt_mode(self) -> str:
        """설정된 prompt_mode. 코드 기본값도 stdin — agents.json 을 거치지 않고 dict 로 만든 항목이 arg 를 타서 WinError 206 을 내지 않도록."""
        return str((self.cfg or {}).get("prompt_mode") or "stdin")

    def _render(self, tpl: List[str], prompt: str, prompt_file: str, mode: str = "") -> List[str]:
        """명령 템플릿 치환. mode(비우면 설정값)가 arg 가 아니면 {prompt} 인자는 버린다(프롬프트는 stdin/파일로 간다)."""
        mode = mode or self._prompt_mode()
        out: List[str] = []
        for i, a in enumerate(tpl):
            if i == 0:
                out.append(self._exe() or a)
                continue
            if a == "{prompt}" and mode != "arg":
                continue
            a = a.replace("{model}", self.model or "").replace("{prompt_file}", prompt_file).replace("{project_root}", ROOT).replace("{python}", sys.executable)
            if "{prompt}" in a:
                a = a.replace("{prompt}", prompt)
            if a == "" and "-m" in out[-1:] and not self.model:   # 모델 미지정이면 -m 플래그 제거
                out.pop()
                continue
            out.append(a)
        return out

    def _complete(self, system: str, user: str, max_tokens: int, effort: str, json_mode: bool) -> Dict[str, Any]:
        if not self.cfg:
            raise LLMError("headless agent '%s' 가 agents.json 에 없습니다" % self.agent)
        if not self._exe_ok():
            # 재시도해도 생기지 않는다 → transient=False. kind="exec" 는 그대로(호출부 분류 유지).
            raise LLMError("headless agent '%s' 실행 파일을 찾을 수 없습니다: command[0]=%r — 설치되지 않았거나 PATH 에 없음. "
                           "agents.json 의 command[0] 을 절대 경로로 적거나 서버를 실행하는 계정의 PATH 를 설정하세요 "
                           "(확인: python -m llmwiki health → headless_agents)" % (self.agent, self.cfg["command"][0]),
                           transient=False, kind="exec")
        prompt = system.strip() + "\n\n" + user.strip()
        if json_mode:
            prompt += "\n\n(출력은 지시된 JSON 만. 코드 블록·설명 없이 JSON 객체 하나만 출력하세요.)"
        files = list(self._files or [])
        tmp_prompt = ""
        tpl = list(self.cfg["command"])
        flag = self.cfg.get("files_flag") or ""
        file_args: List[str] = []
        if files and flag:
            for fp in files:
                file_args += [flag, fp]
        elif files:   # 첨부 미지원 → 인라인 (파일당 inline_attach_chars 까지). 모드와 무관하게 프롬프트 본문에 덧붙인다
            cap = int(self._num("inline_attach_chars") or 60000)
            inl = []
            for fp in files:
                try:
                    with open(fp, "r", encoding="utf-8", errors="ignore") as f:
                        inl.append("### FILE %s\n%s" % (fp, f.read()[:cap]))
                except OSError:
                    pass
            if inl:
                prompt += "\n\n" + "\n\n".join(inl)
        # ---- prompt_mode 결정 + arg 길이 가드 (WinError 206 방지) ----
        requested = self._prompt_mode()
        mode = requested
        fallback = ""
        args: List[str] = []
        if mode == "arg":
            args = self._render(tpl, prompt, "", mode="arg") + file_args
            argv_chars = sum(len(a) + 1 for a in args)      # Windows 명령줄 길이 근사(인자 사이 공백 포함)
            cap = int(self._num("arg_max_chars"))
            if cap > 0 and argv_chars > cap:
                mode = "file" if any("{prompt_file}" in str(a) for a in tpl) else "stdin"
                fallback = "arg→%s (%d chars > arg_max_chars=%d)" % (mode, argv_chars, cap)
                try:
                    from . import logging_setup as _ls
                    _ls.log("warning", "headless prompt_mode fallback: %s" % fallback, "llm", agent=self.agent, model=self.model,
                            role=getattr(self, "role", ""), prompt_chars=len(prompt),
                            hint="agents.json 의 prompt_mode 를 stdin 으로 두면 이 전환이 필요 없다 (Windows 명령줄 한계 32767자). "
                                 "가드를 끄려면 arg_max_chars=0")
                except Exception:
                    pass
        if mode == "file":
            fd, tmp_prompt = tempfile.mkstemp(prefix="llmwiki_prompt_", suffix=".md")
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(prompt)
            args = self._render(tpl, prompt, tmp_prompt, mode="file") + file_args
        elif mode != "arg":
            mode = "stdin"
            args = self._render(tpl, prompt, "", mode="stdin") + file_args
        env = self._child_env()
        argv_log = self._mask_argv(args, prompt)
        cwd = (self.cfg.get("cwd") or "{project_root}").replace("{project_root}", ROOT)
        timeout_s = float(self.cfg.get("timeout_s") or self.timeout or 300)
        stall_s = self._num("stall_timeout_s")
        first_s = self._num("first_output_timeout_s") or stall_s
        retry_on = self._retry_on()
        t0 = time.perf_counter()
        try:
            run = self._run_streaming(args, prompt if mode == "stdin" else None,
                                      cwd if os.path.isdir(cwd) else None, env, timeout_s, stall_s, first_s)
        except OSError as e:
            raise LLMError("headless agent exec failed: %s (argv=%s)" % (e, argv_log[:6]), transient=("exec" in retry_on), kind="exec")
        finally:
            if tmp_prompt:
                try:
                    os.remove(tmp_prompt)
                except OSError:
                    pass
        ms = (time.perf_counter() - t0) * 1000
        raw = (run["out"] or "")[: int(self.cfg.get("max_output_chars") or 400000)]
        stderr = run["err"] or ""
        events = parse_output(raw, self.cfg.get("output", "ndjson"))
        parsed = extract_text(events, self.cfg.get("text_paths") or ["text"]).strip()
        text = parsed or raw.strip()    # 파서가 못 찾으면 원문 (text_paths 보정 필요)

        def _fail(kind: str, msg: str):
            self._log_failure(kind, argv_log, run, msg)
            raise LLMError(msg, transient=(kind in retry_on), kind=kind)

        def _with_mode(r: Dict[str, Any], usage: Dict[str, Any]) -> Dict[str, Any]:
            """결과에 실제 사용한 prompt_mode 와 자동 전환 표시를 남긴다.
            usage 에도 넣는 이유: 질의 단계(answer/rerank/expand/compress)는 `st.note(usage=r.get("usage"))` 로 usage 만 trace meta 에
            옮기므로, 별도 배선 없이 trace 의 LLM 단계 meta 에서 전환 사실이 보이게 하려면 여기가 유일한 통로다 (estimated 표식과 같은 방식)."""
            r["prompt_mode"] = mode
            if fallback:
                r["prompt_mode_fallback"] = fallback
                usage["prompt_mode_fallback"] = fallback
            return r

        if run["reason"] == "cancelled":
            raise _pg.Cancelled("headless agent 취소됨 (%s)" % self.agent)
        if run["reason"] in ("timeout", "stall"):
            # 멎기 전에 쓸 만한 답을 이미 냈다면 버리지 않는다 — 버리면 재시도에 또 몇 분을 쓴다.
            # 단 **파서가 실제 텍스트를 찾았을 때만**. 원문 폴백(raw)은 대개 프로토콜 잡음이라
            # 그것을 답변으로 쓰면 "헛소리를 답으로 돌려주는" 더 나쁜 실패가 된다.
            text = parsed
            if text and self.cfg.get("keep_partial_on_timeout", True):
                self._log_failure(run["reason"], argv_log, run, "부분 출력을 사용합니다 (%d자)" % len(text), level="warning")
                usage = extract_usage(events, self.cfg.get("usage_paths") or {})
                if not usage["input_tokens"]:
                    usage = {"input_tokens": len(prompt) // 3, "output_tokens": len(text) // 3, "estimated": True}
                return _with_mode({"text": text, "usage": usage, "ms": ms, "model": self.model or self.agent, "exit_code": run["rc"],
                                   "events": len(events), "partial": True, "stop_reason": run["reason"],
                                   "note": "%s (%s초 무출력) 로 중단했지만 그때까지 받은 답을 사용했습니다" % (run["reason"], run["last_gap_s"])}, usage)
            if run["reason"] == "stall":
                _fail("stall", "headless agent 가 %.0f초 동안 아무 것도 내놓지 않았습니다 (%s, %d바이트/%d줄 수신, 전체 %.0fs). "
                               "agents.json 의 stall_timeout_s/first_output_timeout_s 로 조정합니다"
                               % (run["last_gap_s"], self.agent, run.get("bytes", 0), run["lines"], run["waited_s"]))
            _fail("timeout", "headless agent timeout (%.0fs, %s, %d바이트/%d줄 수신)" % (timeout_s, self.agent, run.get("bytes", 0), run["lines"]))
        if not text and run["rc"] not in (0, None):
            _fail("exit", "headless agent exit %s: %s" % (run["rc"], (stderr or raw)[:300]))
        if not text:
            _fail("empty", "headless agent returned empty output (exit %s): %s" % (run["rc"], stderr[:200]))
        usage = extract_usage(events, self.cfg.get("usage_paths") or {})
        if not usage["input_tokens"]:
            usage = {"input_tokens": len(prompt) // 3, "output_tokens": len(text) // 3, "estimated": True}
        try:
            from . import logging_setup as _ls
            _ls.log("debug", "headless agent run", "llm", agent=self.agent, argv=argv_log, exit=run["rc"], prompt_mode=mode,
                    prompt_mode_fallback=fallback or None,
                    ms=round(ms, 1), events=len(events), lines=run["lines"], bytes=run.get("bytes", 0), stderr=stderr[:300])
        except Exception:
            pass
        return _with_mode({"text": text, "usage": usage, "ms": ms, "model": self.model or self.agent, "exit_code": run["rc"], "events": len(events)}, usage)

    def _log_failure(self, kind: str, argv_log: List[str], run: Dict[str, Any], msg: str, level: str = "error") -> None:
        """실패를 **진단할 수 있을 만큼** 남긴다 — 예전에는 stderr 300자만, 그것도 debug 수준이라 운영에서 안 보였다.
        argv_log 는 _mask_argv 를 거친 것(프롬프트 인자는 `<prompt:N chars>`)."""
        n = int(self._num("failure_log_chars") or 2000)
        try:
            from . import logging_setup as _ls
            _ls.log(level, "headless agent %s: %s" % (kind, msg[:200]), "llm", agent=self.agent, model=self.model,
                    role=getattr(self, "role", ""), argv=list(argv_log), exit=run.get("rc"),
                    lines=run.get("lines"), bytes=run.get("bytes"), waited_s=run.get("waited_s"), last_gap_s=run.get("last_gap_s"),
                    stdout_tail=(run.get("out") or "")[-n:], stderr_tail=(run.get("err") or "")[-n:],
                    hint="agents.json 의 timeout_s/stall_timeout_s/first_output_timeout_s/retries 를 조정하거나 "
                         "`python -m llmwiki models test --live` 로 실제 호출을 확인하세요")
        except Exception:
            pass
        try:
            _pg.note("headless %s %s — %s" % (self.agent, kind, msg[:120]))
        except Exception:
            pass


def _mock_main() -> int:
    """`python -m llmwiki.headless --mock` : 표준입력 프롬프트를 읽어 MockLLM 과 같은 규칙으로 ndjson 이벤트 출력.
    테스트 옵션: --sleep N (N초 멈춤 → 타임아웃 재현) · --fail-times N --state FILE (처음 N번은 종료 코드 3 으로 실패 → 재시도 재현) · --empty (빈 출력)
      --stall N (몇 줄 내놓고 N초 조용 → **무응답** 재현) · --partial (부분 텍스트만 내고 멈춤)
      --fail (항상 종료 코드 2, 프롬프트를 되풀이하지 않는 stderr → 로그 마스킹 검증) · --dump-env (환경변수 **이름** 목록을 텍스트로 출력 → 허용 목록 검증)
      --prompt-file PATH (표준입력 대신 파일에서 프롬프트 → prompt_mode=file 검증) · --long-line N (줄바꿈 없이 N자를 천천히 → 바이트 단위 stall 감지 검증)
      --prompt-arg TEXT (인자로 받은 프롬프트 → prompt_mode=arg · arg_max_chars 전환 검증; 값이 비거나 없으면 opencode 처럼 표준입력으로 떨어진다)
    프롬프트 출처 우선순위: --prompt-file(값 있음) > --prompt-arg(값 있음) > 표준입력."""
    argv = sys.argv[1:]
    if "--fail" in argv:
        sys.stderr.write("mock forced failure (exit 2)\n")
        return 2
    if "--dump-env" in argv:
        sys.stdout.write("\n".join(sorted(os.environ.keys())) + "\n")
        return 0

    def _opt(name: str) -> str:
        """--name 뒤의 값. 없거나 비거나 다른 옵션이면 "" (arg→stdin/file 전환 뒤에는 {prompt} 인자가 빠져 값 없이 남는다)."""
        if name not in argv:
            return ""
        i = argv.index(name) + 1
        v = argv[i] if i < len(argv) else ""
        return "" if v.startswith("--") else v

    if _opt("--prompt-file"):
        with open(_opt("--prompt-file"), "r", encoding="utf-8") as f:
            prompt = f.read()
    elif _opt("--prompt-arg"):
        prompt = _opt("--prompt-arg")
    else:
        prompt = sys.stdin.read()
    files = []
    for i, a in enumerate(argv):
        if a == "--file" and i + 1 < len(argv):
            files.append(argv[i + 1])
    if "--long-line" in argv:
        # 줄바꿈 없이 한 줄을 조금씩 오래 내놓는다 — 줄 단위 감지였다면 stall 로 오판됐을 상황
        n = int(argv[argv.index("--long-line") + 1])
        sys.stdout.write('{"type":"text","part":{"text":"')
        sys.stdout.flush()
        for _ in range(n):
            sys.stdout.write("x")
            sys.stdout.flush()
            time.sleep(0.05)
        sys.stdout.write('"}}\n')
        sys.stdout.flush()
        return 0
    if "--stall" in argv:
        # 첫 줄(또는 부분 텍스트)만 내놓고 조용해진다 — opencode 가 붙었다가 매달리는 모습
        sys.stdout.write(json.dumps({"type": "step_start", "session": "mock"}) + "\n")
        if "--partial" in argv:
            sys.stdout.write(json.dumps({"type": "text", "part": {"text": "부분 답변입니다."}}, ensure_ascii=False) + "\n")
        sys.stdout.flush()
        time.sleep(float(argv[argv.index("--stall") + 1]))
        return 0
    if "--sleep" in argv:
        time.sleep(float(argv[argv.index("--sleep") + 1]))
    if "--fail-times" in argv:
        n = int(argv[argv.index("--fail-times") + 1])
        state = argv[argv.index("--state") + 1] if "--state" in argv else os.path.join(tempfile.gettempdir(), "llmwiki_mock_fail_state")
        cnt = 0
        try:
            with open(state, "r", encoding="utf-8") as f:
                cnt = int(f.read().strip() or 0)
        except Exception:
            cnt = 0
        with open(state, "w", encoding="utf-8") as f:
            f.write(str(cnt + 1))
        if cnt < n:
            sys.stderr.write("mock failure %d/%d\n" % (cnt + 1, n))
            return 3
    if "--empty" in argv:
        return 0
    from .providers import MockLLM
    sys_part, _, user_part = prompt.partition("\n\n")
    r = MockLLM()._complete(sys_part, user_part or prompt, 512, "low", "JSON" in prompt)
    text = r["text"]
    if files:
        text += " (files=%d)" % len(files)
    out = sys.stdout
    out.write(json.dumps({"type": "step_start", "session": "mock"}) + "\n")
    half = max(1, len(text) // 2)
    out.write(json.dumps({"type": "text", "part": {"text": text[:half]}}, ensure_ascii=False) + "\n")
    out.write(json.dumps({"type": "text", "part": {"text": text[half:]}}, ensure_ascii=False) + "\n")
    out.write(json.dumps({"type": "step_finish", "usage": {"input_tokens": len(prompt) // 3, "output_tokens": len(text) // 3}}) + "\n")
    out.flush()
    return 0


if __name__ == "__main__":
    if "--mock" in sys.argv:
        sys.exit(_mock_main())
    print("usage: python -m llmwiki.headless --mock  (테스트용 목업 에이전트)")
