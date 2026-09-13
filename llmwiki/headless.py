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
     "timeout_s": 300, "cwd": "{project_root}", "env": {}, "max_output_chars": 400000 }}
provider 지정: llm_provider="headless:opencode" 또는 llm_roles.answer.provider="headless:opencode".
결과: 텍스트를 순서대로 이어붙여 반환 → 기존 parse_json 으로 구조화 결과 회수.
`python -m llmwiki.headless --mock` 은 테스트용 목업 에이전트(표준입력 프롬프트 → ndjson 이벤트).
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from typing import Any, Dict, List, Optional

from .config import ROOT, path_for
from .providers import BaseLLM, LLMError

DEFAULT_AGENTS: Dict[str, Dict[str, Any]] = {
    "opencode": {
        "desc": "OpenCode CLI (opencode run --format json). 모델은 provider/model 형식 (예 anthropic/claude-sonnet-4-5).",
        "command": ["opencode", "run", "--format", "json", "-m", "{model}", "{prompt}"],
        "prompt_mode": "arg", "files_flag": "-f", "output": "ndjson",
        "text_paths": ["part.text", "text", "content", "message.content", "result"],
        "usage_paths": {"input": ["usage.input_tokens", "tokens.input", "part.tokens.input"], "output": ["usage.output_tokens", "tokens.output", "part.tokens.output"]},
        "model": "", "timeout_s": 300, "cwd": "{project_root}", "env": {}, "max_output_chars": 400000,
    },
    "claude": {
        "desc": "Claude Code CLI headless (claude -p --output-format json).",
        "command": ["claude", "-p", "--output-format", "json", "--model", "{model}", "{prompt}"],
        "prompt_mode": "arg", "files_flag": "", "output": "json",
        "text_paths": ["result", "content", "text"],
        "usage_paths": {"input": ["usage.input_tokens"], "output": ["usage.output_tokens"]},
        "model": "claude-sonnet-5", "timeout_s": 300, "cwd": "{project_root}", "env": {}, "max_output_chars": 400000,
    },
    "codex": {
        "desc": "OpenAI Codex CLI (codex exec --json).",
        "command": ["codex", "exec", "--json", "-m", "{model}", "{prompt}"],
        "prompt_mode": "arg", "files_flag": "", "output": "ndjson",
        "text_paths": ["item.text", "text", "content", "message"],
        "usage_paths": {"input": ["usage.input_tokens"], "output": ["usage.output_tokens"]},
        "model": "", "timeout_s": 300, "cwd": "{project_root}", "env": {}, "max_output_chars": 400000,
    },
    "mock": {
        "desc": "테스트용 목업 에이전트 (네트워크 없음). 표준입력 프롬프트 → ndjson 이벤트.",
        "command": [sys.executable, "-m", "llmwiki.headless", "--mock"],
        "prompt_mode": "stdin", "files_flag": "--file", "output": "ndjson",
        "text_paths": ["part.text", "text"],
        "usage_paths": {"input": ["usage.input_tokens"], "output": ["usage.output_tokens"]},
        "model": "mock", "timeout_s": 60, "cwd": "{project_root}", "env": {}, "max_output_chars": 100000,
    },
}


def agents_path() -> str:
    return path_for("agents")


def load_agents() -> Dict[str, Dict[str, Any]]:
    p = agents_path()
    if not os.path.exists(p):
        save_agents(DEFAULT_AGENTS)
        return json.loads(json.dumps(DEFAULT_AGENTS))
    with open(p, "r", encoding="utf-8") as f:
        data = json.load(f)
    out = {k: v for k, v in data.items() if not k.startswith("_")}
    if "mock" not in out:   # 테스트/배선 확인용은 항상 제공
        out["mock"] = json.loads(json.dumps(DEFAULT_AGENTS["mock"]))
    return out


def save_agents(data: Dict[str, Dict[str, Any]]) -> str:
    p = agents_path()
    os.makedirs(os.path.dirname(p) or ".", exist_ok=True)
    out = {"_comment": "Headless agent 명령 템플릿. {model} {prompt} {prompt_file} {project_root} 치환. provider 는 headless:<이름>."}
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

    def _exe_ok(self) -> bool:
        cmd = (self.cfg or {}).get("command") or []
        if not cmd:
            return False
        exe = cmd[0]
        return bool(shutil.which(exe) or os.path.exists(exe))

    def ping(self) -> Dict[str, Any]:
        if not self.cfg:
            return {"ok": False, "ms": 0.0, "detail": "agent '%s' not in agents.json" % self.agent}
        exe = self.cfg["command"][0]
        if not self._exe_ok():
            return {"ok": False, "ms": 0.0, "detail": "executable not found: %s (PATH 확인)" % exe}
        return {"ok": True, "ms": 0.0, "detail": "%s found (%s); model=%s" % (exe, shutil.which(exe) or exe, self.model or "(agent default)")}

    def _render(self, tpl: List[str], prompt: str, prompt_file: str) -> List[str]:
        out: List[str] = []
        for a in tpl:
            if a == "{prompt}" and self.cfg.get("prompt_mode", "arg") != "arg":
                continue
            a = a.replace("{model}", self.model or "").replace("{prompt_file}", prompt_file).replace("{project_root}", ROOT)
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
            raise LLMError("headless agent 실행 파일을 찾을 수 없습니다: %s" % self.cfg["command"][0])
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
        t0 = time.perf_counter()
        try:
            proc = subprocess.run(args, input=(prompt if mode == "stdin" else None), capture_output=True, text=True, encoding="utf-8",
                                  errors="replace", timeout=int(self.cfg.get("timeout_s") or 300), cwd=cwd if os.path.isdir(cwd) else None, env=env)
        except subprocess.TimeoutExpired:
            raise LLMError("headless agent timeout (%ss)" % self.cfg.get("timeout_s"))
        except OSError as e:
            raise LLMError("headless agent exec failed: %s" % e)
        finally:
            if tmp_prompt:
                try:
                    os.remove(tmp_prompt)
                except OSError:
                    pass
        ms = (time.perf_counter() - t0) * 1000
        raw = (proc.stdout or "")[: int(self.cfg.get("max_output_chars") or 400000)]
        events = parse_output(raw, self.cfg.get("output", "ndjson"))
        text = extract_text(events, self.cfg.get("text_paths") or ["text"]).strip()
        if not text and proc.returncode != 0:
            raise LLMError("headless agent exit %s: %s" % (proc.returncode, (proc.stderr or raw)[:300]))
        if not text:
            text = raw.strip()   # 파서가 못 찾으면 원문 (text_paths 보정 필요)
        usage = extract_usage(events, self.cfg.get("usage_paths") or {})
        if not usage["input_tokens"]:
            usage = {"input_tokens": len(prompt) // 3, "output_tokens": len(text) // 3, "estimated": True}
        try:
            from . import logging_setup as _ls
            _ls.log("debug", "headless agent run", "llm", agent=self.agent, argv=[a[:80] for a in args], exit=proc.returncode,
                    ms=round(ms, 1), events=len(events), stderr=(proc.stderr or "")[:300])
        except Exception:
            pass
        return {"text": text, "usage": usage, "ms": ms, "model": self.model or self.agent, "exit_code": proc.returncode, "events": len(events)}


def _mock_main() -> int:
    """`python -m llmwiki.headless --mock` : 표준입력 프롬프트를 읽어 MockLLM 과 같은 규칙으로 ndjson 이벤트 출력."""
    prompt = sys.stdin.read()
    files = []
    argv = sys.argv[1:]
    for i, a in enumerate(argv):
        if a == "--file" and i + 1 < len(argv):
            files.append(argv[i + 1])
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
