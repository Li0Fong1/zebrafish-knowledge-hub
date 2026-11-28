# -*- coding: utf-8 -*-
"""OpenAI 兼容 API 后端（DeepSeek / 通义 / OpenAI 等）。"""
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request

from common_utils import logger, redact_secrets
from zebrafish_prompt import get_system_prompt, is_academic_mode

# 内网 / 环回 / 链路本地地址：默认拒绝，防止把带密钥的请求打到本机或内网（SSRF）。
_PRIVATE_HOST = re.compile(
    r"^(localhost|0\.0\.0\.0|127\.\d+\.\d+\.\d+|10\.\d+\.\d+\.\d+|"
    r"192\.168\.\d+\.\d+|169\.254\.\d+\.\d+|"
    r"172\.(?:1[6-9]|2\d|3[01])\.\d+\.\d+|::1|\[::1\])$"
)


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


_THINKING_EFFORTS = ("low", "high", "max")


def _thinking_mode() -> str:
    """归一化 thinking 开关：on（默认）/ off / auto。"""
    mode = os.getenv("LLM_API_THINKING", "on").strip().lower()
    if mode == "auto":
        return "auto"
    if mode in ("off", "disabled", "false", "0", "no"):
        return "off"
    return "on"


def _thinking_payload() -> dict:
    """构造 thinking 开关与推理强度字段。

    评测口径为「与对手公平对齐」：Kimi K3 / Qwen3.8-Max / Gemini 3.1 Pro 均无法
    关闭思考，故本平台不再以非推理模式参评，默认同样开启 thinking 并给出等的
    reasoning_effort（默认 high，即 DeepSeek 官方默认档）。

    LLM_API_THINKING:
      on（默认）→ {"thinking": {"type": "enabled"}, "reasoning_effort": <档位>}
      off        → {"thinking": {"type": "disabled"}}，temperature 正常生效
      auto       → 不下发该字段，交由上游默认（非 DeepSeek 后端用）

    注意：thinking 模式下 temperature / top_p / presence_penalty 均不生效，
    故 on 档位下不再下发 temperature（见 chat / generate_stream）。
    """
    mode = _thinking_mode()
    if mode == "auto":
        return {}
    if mode == "off":
        return {"thinking": {"type": "disabled"}}
    effort = os.getenv("LLM_API_REASONING_EFFORT", "high").strip().lower() or "high"
    if effort not in _THINKING_EFFORTS:
        effort = "high"
    return {"thinking": {"type": "enabled"}, "reasoning_effort": effort}


def _thinking_budget_floor() -> int:
    """thinking 模式下 max_tokens 的下限。

    CoT 与正文共用 max_tokens 预算，若沿用 512 会被推理过程吃光、正文变空。
    V4-Pro 在 effort=high 下推理链显著更长：实测一道多基因对比题
    reasoning_tokens=10,325、正文 4,474 字符，合计 11,428 —— 4096 预算下
    finish_reason=length 且正文为空。故下限抬到 16384。
    """
    if _thinking_mode() != "on":
        return 0
    return int(os.getenv("LLM_API_THINKING_MIN_TOKENS", "16384"))


def _sampling_plan(requested_tokens: int) -> tuple:
    """按单次请求的输出预算决定 thinking 字段、max_tokens 与是否下发 temperature。

    短结构化调用（如 SQL 生成，预算 ≤ LLM_API_THINKING_SHORT_MAX，默认 256）
    保持关闭思考：这点预算里 CoT 会把正文挤掉，且响应会明显变慢。
    其余请求按主口径开启 thinking，并把预算抬到下限之上。
    返回 (thinking 字段, 实际 max_tokens, 是否下发 temperature)。
    """
    mode = _thinking_mode()
    short_max = int(os.getenv("LLM_API_THINKING_SHORT_MAX", "256"))
    if mode == "on" and 0 < requested_tokens <= short_max:
        return {"thinking": {"type": "disabled"}}, requested_tokens, True
    return _thinking_payload(), max(requested_tokens, _thinking_budget_floor()), mode != "on"


def _validate_api_base(url: str) -> str:
    """校验上游地址：必须 https，且不得指向内网。

    要接本地推理服务（http://127.0.0.1:8000）时，显式设置
    ``LLM_API_ALLOW_PRIVATE=1`` 即可放行 —— 安全策略不阻断后续扩展。
    """
    if not _env_bool("LLM_API_STRICT", True):
        return url
    parsed = urllib.parse.urlparse(url)
    host = (parsed.hostname or "").lower()
    allow_private = _env_bool("LLM_API_ALLOW_PRIVATE", False)
    if parsed.scheme != "https" and not allow_private:
        raise ValueError(
            f"LLM_API_BASE 必须使用 https（当前 {parsed.scheme or '空'}），"
            "否则 API 密钥将以明文传输；如为本地服务请设 LLM_API_ALLOW_PRIVATE=1"
        )
    if _PRIVATE_HOST.match(host) and not allow_private:
        raise ValueError(
            f"LLM_API_BASE 指向内网/本机（{host}），已拒绝；如为本地服务请设 LLM_API_ALLOW_PRIVATE=1"
        )
    return url


class ApiLLM:
    """远程大模型推理，接口兼容 OpenAI Chat Completions。"""
    is_remote = True

    def __init__(self):
        self.base_url = _validate_api_base(
            os.getenv("LLM_API_BASE", "https://api.deepseek.com/v1").rstrip("/")
        )
        self.api_key = os.getenv("LLM_API_KEY", "").strip()
        self.model = os.getenv("LLM_API_MODEL", "deepseek-flash")
        self.timeout = int(os.getenv("LLM_API_TIMEOUT", "600"))
        if not self.api_key:
            raise ValueError("未配置 LLM_API_KEY")
        logger.info(f"[成功] 云端推理已配置：{self.model} @ {self.base_url}")

    def chat(self, messages: list, max_new_tokens: int = None) -> str:
        if not messages:
            return "请输入有效问题"
        req_tokens = max_new_tokens or int(os.getenv("LLM_MAX_NEW_TOKENS", "512"))
        thinking, max_tokens, use_temp = _sampling_plan(req_tokens)
        default_temp = "0.1" if is_academic_mode() else "0.3"
        payload = {
            "model": self.model,
            "messages": messages,
            "max_tokens": max_tokens,
            "stream": False,
            **thinking,
        }
        if use_temp:
            # thinking 模式下 temperature 无效，显式不下发，避免误读为已设定采样温度
            payload["temperature"] = float(
                os.getenv("LLM_API_TEMPERATURE", default_temp)
            )
        req = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                body = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            err_body = redact_secrets(e.read().decode("utf-8", errors="replace")[:500])
            raise RuntimeError(f"API HTTP {e.code}: {err_body}") from e
        except urllib.error.URLError as e:
            raise RuntimeError(f"API 网络错误：{e.reason}") from e

        choices = body.get("choices") or []
        if not choices:
            raise RuntimeError(f"API 返回无 choices：{redact_secrets(str(body))[:200]}")
        content = (choices[0].get("message") or {}).get("content") or ""
        content = content.strip()
        if not content:
            raise RuntimeError("API 返回空内容")
        return content

    def generate(self, prompt: str, max_new_tokens: int = None) -> str:
        if not prompt:
            return "请输入有效问题"
        # 有流式 sink 时自动逐 token 推送（SSE 流式输出）
        try:
            from common_utils import get_stream_sink
            if get_stream_sink() is not None:
                parts = []
                for piece in self.generate_stream(prompt, max_new_tokens=max_new_tokens):
                    parts.append(piece)
                return "".join(parts)
        except Exception as e:
            logger.warning(f"[警告] 流式生成失败，回退普通生成：{e}")
        return self.chat(
            [
                {"role": "system", "content": get_system_prompt()},
                {"role": "user", "content": prompt},
            ],
            max_new_tokens=max_new_tokens,
        )

    def generate_stream(self, prompt: str, max_new_tokens: int = None):
        """流式生成：逐 token yield（OpenAI 兼容 SSE）。异常在首个 next 抛出。"""
        from common_utils import emit_stream

        if not prompt:
            return
        req_tokens = max_new_tokens or int(os.getenv("LLM_MAX_NEW_TOKENS", "512"))
        thinking, max_tokens, use_temp = _sampling_plan(req_tokens)
        default_temp = "0.1" if is_academic_mode() else "0.3"
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": get_system_prompt()},
                {"role": "user", "content": prompt},
            ],
            "max_tokens": max_tokens,
            "stream": True,
            **thinking,
        }
        if use_temp:
            payload["temperature"] = float(
                os.getenv("LLM_API_TEMPERATURE", default_temp)
            )
        req = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
            method="POST",
        )
        try:
            resp = urllib.request.urlopen(req, timeout=self.timeout)
        except urllib.error.HTTPError as e:
            err_body = redact_secrets(e.read().decode("utf-8", errors="replace")[:500])
            raise RuntimeError(f"API HTTP {e.code}: {err_body}") from e
        except urllib.error.URLError as e:
            raise RuntimeError(f"API 网络错误：{e.reason}") from e

        try:
            with resp:
                for raw in resp:
                    line = raw.decode("utf-8", "replace").strip()
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        break
                    try:
                        obj = json.loads(data)
                    except ValueError:
                        continue
                    choices = obj.get("choices") or []
                    if not choices:
                        continue
                    delta = (choices[0].get("delta") or {}).get("content") or ""
                    if delta:
                        emit_stream(delta)
                        yield delta
        except urllib.error.URLError as e:
            raise RuntimeError(f"API 流式中断：{e.reason}") from e


class HybridReasoningLLM:
    """API 优先，失败时回退本地模型。"""
    is_remote = True

    def __init__(self, primary: ApiLLM, fallback):
        self.primary = primary
        self.fallback = fallback

    def generate(self, prompt: str, max_new_tokens: int = None) -> str:
        try:
            return self.primary.generate(prompt, max_new_tokens=max_new_tokens)
        except Exception as e:
            logger.warning(f"[警告] 云端 API 失败，回退本地模型：{e}")
            return self.fallback.generate(prompt, max_new_tokens=max_new_tokens)

    def generate_stream(self, prompt: str, max_new_tokens: int = None):
        """API 优先流式；失败则回退本地一次性 generate（整段作为单个 token）。"""
        from common_utils import emit_stream

        try:
            yield from self.primary.generate_stream(prompt, max_new_tokens=max_new_tokens)
            return
        except Exception as e:
            logger.warning(f"[警告] 云端 API 流式失败，回退本地模型：{e}")
        text = self.fallback.generate(prompt, max_new_tokens=max_new_tokens)
        if text:
            emit_stream(text)
            yield text
