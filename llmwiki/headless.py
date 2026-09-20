# -*- coding: utf-8 -*-
"""Generic Headless Agent Provider — opencode / claude / codex 같은 CLI 에이전트를 non-interactive 로 subprocess 실행해
LLM 역할(질의 확장·답변 합성·포렌식·자가진화 리뷰 …)에 쓴다.

agents.json (프로젝트 루트, 없으면 기본값 생성):
  {"opencode": {
     "command": ["opencode", "run", "--format", "json", "-m", "{model}", "{prompt}"],
     "prompt_mode": "arg",              # arg | stdin | file  ({prompt} 자리에 프롬프트 / 표준입력 / 임시 파일 경로 {prompt_file})
     "files_flag": "-f",                # 첨부 파일마다 "-f <path>" (비우면 첨부 미지원 → 프롬프트에 인라인)
     "output": "ndjson",                # ndjson | json | text
     "text_paths": ["part.text", "text", "content", "message.content", "result"],   # 이벤트에서 텍스트를 뽑는 경로(점 표기)
     "usage_paths": {"input": ["usage.input_tokens", "tokens.input"], "output": ["usage.output_tokens", "tokens.output"]},
     "model": "",                       # 기본 모델 (역할 model 이 비면 사용)
     "timeout_s": 300,                  # 1회 실행 제한(초). 넘으면 프로세스를 죽이고 재시도
     "retries": 3,                      # transient 실패(retry_on) 재시도 횟수 → 최대 1+retries 회 실행
     "retry_backoff_s": 5,              # 재시도 사이 대기(초) × 시도 번호
     "retry_on": ["timeout", "exec", "exit", "empty"],   # 재시도 대상: 타임아웃 / 실행 실패 / 종료 코드≠0 / 빈 출력
     "cwd": "{project_root}", "env": {}, "max_output_chars": 400000 }}
provider 지정: llm_provider="headless:opencode" 또는 llm_roles.answer.provider="headless:opencode".
결과: 텍스트를 순서대로 이어붙여 반환 → 기존 parse_json 으로 구조화 결과 회수.
최종 실패는 LLMError(transient) 로 올라가고 BaseLLM 이 incident 로 기록 → 질의 결과 llm_report / 빌드 alerts 에 보고된다.
`python -m llmwiki.headless --mock` 은 테스트용 목업 에이전트(표준입력 프롬프트 → ndjson 이벤트). `--mock --sleep N` 은 N초 멈춤(타임아웃 테스트).
"""
from __future__ import annotations

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
}

DEFAULT_AGENTS: Dict[str, Dict[str, Any]] = {
    "opencode": {
        "desc": "OpenCode CLI (opencode run --format json). 모델은 provider/model 형식 (예 anthropic/claude-sonnet-4-5).",
        "command": ["opencode", "run", "--format", "json", "-m", "{model}", "{prompt}"],
        "prompt_mode": "arg", "files_flag": "-f", "output": "ndjson",
        "text_paths": ["part.text", "text", "content", "message.content", "result"],
        "usage_paths": {"input": ["usage.input_tokens", "tokens.input", "part.tokens.input"], "output": ["usage.output_tokens", "tokens.output", "part.tokens.output"]},
        "model": "", "timeout_s": 300, "retries": 3, "retry_backoff_s": 5, "retry_on": ["timeout", "exec", "exit", "empty"],
        "cwd": "{project_root}", "env": {}, "max_output_chars": 400000,
    },
    "claude": {
        "desc": "Claude Code CLI headless (claude -p --output-format json).",
        "command": ["claude", "-p", "--output-format", "json", "--model", "{model}", "{prompt}"],
        "prompt_mode": "arg", "files_flag": "", "output": "json",
        "text_paths": ["result", "content", "text"],
        "usage_paths": {"input": ["usage.input_tokens"], "output": ["usage.output_tokens"]},
        "model": "claude-sonnet-5", "timeout_s": 300, "retries": 3, "retry_backoff_s": 5, "retry_on": ["timeout", "exec", "exit", "empty"],
        "cwd": "{project_root}", "env": {}, "max_output_chars": 400000,
    },
    "codex": {
        "desc": "OpenAI Codex CLI (codex exec --json).",
        "command": ["codex", "exec", "--json", "-m", "{model}", "{prompt}"],
        "prompt_mode": "arg", "files_flag": "", "output": "ndjson",
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
    """agents.json 항목에 없는 재시도 키를 기본값으로 채운다 (구 파일 호환)."""
    out = dict(cfg or {})
    for k, v in RETRY_DEFAULTS.items():
        if k not in out or out[k] in (None, ""):
            out[k] = json.loads(json.dumps(v))
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
    p = agents_path()
    os.makedirs(os.path.dirname(p) or ".", exist_ok=True)
    out = {"_comment": "Headless agent 명령 템플릿. {model} {prompt} {prompt_file} {project_root} {python} 치환. provider 는 headless:<이름>. "
                       "command[0] 은 PATH 에서 찾는다(Windows 의 .cmd 셸 포함); 못 찾으면 절대 경로를 적는다. "
                       "재시도: timeout_s(1회 실행 전체 제한, 기본 300=5분) · retries(기본 3) · retry_backoff_s · retry_on[timeout|exec|exit|empty|stall]. "
                       "무응답 대책: stall_timeout_s(마지막 출력 뒤 이만큼 조용하면 죽이고 재시도, 기본 60) · "
                       "first_output_timeout_s(첫 출력까지, 기본 120) · keep_partial_on_timeout(멎기 전 받은 답을 쓸지, 기본 true) · "
                       "failure_log_chars(실패 로그에 남길 stdout/stderr 꼬리, 기본 2000). "
                       "최종 실패는 질의 결과 llm_report / 빌드 alerts 에 보고되고 답변은 추출식으로 대체된다. 설명: docs/BRINGUP_GUIDE.md §4.3"}
    out.update(data)
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
        반환: {rc, out, err, reason, waited_s, last_gap_s, lines}
          reason: "" 정상 종료 · "timeout" 전체 시간 초과 · "stall" 무출력 초과 · "cancelled" 사용자 취소
        """
        out_buf: List[str] = []
        err_buf: List[str] = []
        state = {"last": time.monotonic(), "lines": 0}
        lock = threading.Lock()
        proc = subprocess.Popen(args, stdin=subprocess.PIPE if stdin_text is not None else subprocess.DEVNULL,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8",
                                errors="replace", bufsize=1, cwd=cwd, env=env)

        def pump(stream, buf, count_it):
            try:
                for line in stream:
                    with lock:
                        buf.append(line)
                        state["last"] = time.monotonic()
                        if count_it:
                            state["lines"] += 1
            except Exception:
                pass

        threads = [threading.Thread(target=pump, args=(proc.stdout, out_buf, True), daemon=True),
                   threading.Thread(target=pump, args=(proc.stderr, err_buf, False), daemon=True)]
        for t in threads:
            t.start()
        if stdin_text is not None:
            try:
                proc.stdin.write(stdin_text)
                proc.stdin.close()
            except Exception:
                pass

        t0 = time.monotonic()
        reason = ""
        noted = 0.0
        while True:
            if proc.poll() is not None:
                break
            now = time.monotonic()
            with lock:
                last, lines = state["last"], state["lines"]
            gap = now - last
            limit = (first_s if (lines == 0 and first_s > 0) else stall_s)
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
                    _pg.note("headless %s: %d줄 수신 · 마지막 출력 %.0fs 전 (제한 %.0fs)" % (self.agent, lines, gap, limit or timeout_s))
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
                    "lines": state["lines"]}

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

    def _render(self, tpl: List[str], prompt: str, prompt_file: str) -> List[str]:
        out: List[str] = []
        for i, a in enumerate(tpl):
            if i == 0:
                out.append(self._exe() or a)
                continue
            if a == "{prompt}" and self.cfg.get("prompt_mode", "arg") != "arg":
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
            raise LLMError("headless agent 실행 파일을 찾을 수 없습니다: %s (PATH 또는 agents.json command[0] 절대 경로)" % self.cfg["command"][0])
        prompt = system.strip() + "\n\n" + user.strip()
        if json_mode:
            prompt += "\n\n(출력은 지시된 JSON 만. 코드 블록·설명 없이 JSON 객체 하나만 출력하세요.)"
        files = list(self._files or [])
        tmp_prompt = ""
        mode = self.cfg.get("prompt_mode", "arg")
        if mode == "file":
            fd, tmp_prompt = tempfile.mkstemp(prefix="llmwiki_prompt_", suffix=".md")
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(prompt)
        args = self._render(list(self.cfg["command"]), prompt, tmp_prompt)
        flag = self.cfg.get("files_flag") or ""
        if files and flag:
            for fp in files:
                args += [flag, fp]
        elif files:   # 첨부 미지원 → 인라인
            inl = []
            for fp in files:
                try:
                    with open(fp, "r", encoding="utf-8", errors="ignore") as f:
                        inl.append("### FILE %s\n%s" % (fp, f.read()[:60000]))
                except OSError:
                    pass
            if inl and mode == "arg":
                args = [a.replace(prompt, prompt + "\n\n" + "\n\n".join(inl)) if a == prompt else a for a in args]
            elif inl:
                prompt += "\n\n" + "\n\n".join(inl)
        env = dict(os.environ)
        env.update({k: str(v) for k, v in (self.cfg.get("env") or {}).items()})
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
            raise LLMError("headless agent exec failed: %s" % e, transient=("exec" in retry_on), kind="exec")
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
            self._log_failure(kind, args, run, msg)
            raise LLMError(msg, transient=(kind in retry_on), kind=kind)

        if run["reason"] == "cancelled":
            raise _pg.Cancelled("headless agent 취소됨 (%s)" % self.agent)
        if run["reason"] in ("timeout", "stall"):
            # 멎기 전에 쓸 만한 답을 이미 냈다면 버리지 않는다 — 버리면 재시도에 또 몇 분을 쓴다.
            # 단 **파서가 실제 텍스트를 찾았을 때만**. 원문 폴백(raw)은 대개 프로토콜 잡음이라
            # 그것을 답변으로 쓰면 "헛소리를 답으로 돌려주는" 더 나쁜 실패가 된다.
            text = parsed
            if text and self.cfg.get("keep_partial_on_timeout", True):
                self._log_failure(run["reason"], args, run, "부분 출력을 사용합니다 (%d자)" % len(text), level="warning")
                usage = extract_usage(events, self.cfg.get("usage_paths") or {})
                if not usage["input_tokens"]:
                    usage = {"input_tokens": len(prompt) // 3, "output_tokens": len(text) // 3, "estimated": True}
                return {"text": text, "usage": usage, "ms": ms, "model": self.model or self.agent, "exit_code": run["rc"],
                        "events": len(events), "partial": True, "stop_reason": run["reason"],
                        "note": "%s (%s초 무출력) 로 중단했지만 그때까지 받은 답을 사용했습니다" % (run["reason"], run["last_gap_s"])}
            if run["reason"] == "stall":
                _fail("stall", "headless agent 가 %.0f초 동안 아무 것도 내놓지 않았습니다 (%s, %d줄 수신, 전체 %.0fs). "
                               "agents.json 의 stall_timeout_s/first_output_timeout_s 로 조정합니다"
                               % (run["last_gap_s"], self.agent, run["lines"], run["waited_s"]))
            _fail("timeout", "headless agent timeout (%.0fs, %s, %d줄 수신)" % (timeout_s, self.agent, run["lines"]))
        if not text and run["rc"] not in (0, None):
            _fail("exit", "headless agent exit %s: %s" % (run["rc"], (stderr or raw)[:300]))
        if not text:
            _fail("empty", "headless agent returned empty output (exit %s): %s" % (run["rc"], stderr[:200]))
        usage = extract_usage(events, self.cfg.get("usage_paths") or {})
        if not usage["input_tokens"]:
            usage = {"input_tokens": len(prompt) // 3, "output_tokens": len(text) // 3, "estimated": True}
        try:
            from . import logging_setup as _ls
            _ls.log("debug", "headless agent run", "llm", agent=self.agent, argv=[a[:80] for a in args], exit=run["rc"],
                    ms=round(ms, 1), events=len(events), lines=run["lines"], stderr=stderr[:300])
        except Exception:
            pass
        return {"text": text, "usage": usage, "ms": ms, "model": self.model or self.agent, "exit_code": run["rc"], "events": len(events)}

    def _log_failure(self, kind: str, args: List[str], run: Dict[str, Any], msg: str, level: str = "error") -> None:
        """실패를 **진단할 수 있을 만큼** 남긴다 — 예전에는 stderr 300자만, 그것도 debug 수준이라 운영에서 안 보였다."""
        n = int(self._num("failure_log_chars") or 2000)
        try:
            from . import logging_setup as _ls
            _ls.log(level, "headless agent %s: %s" % (kind, msg[:200]), "llm", agent=self.agent, model=self.model,
                    role=getattr(self, "role", ""), argv=[a[:120] for a in args], exit=run.get("rc"),
                    lines=run.get("lines"), waited_s=run.get("waited_s"), last_gap_s=run.get("last_gap_s"),
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
      --stall N (몇 줄 내놓고 N초 조용 → **무응답** 재현) · --partial (부분 텍스트만 내고 멈춤)"""
    prompt = sys.stdin.read()
    files = []
    argv = sys.argv[1:]
    for i, a in enumerate(argv):
        if a == "--file" and i + 1 < len(argv):
            files.append(argv[i + 1])
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
