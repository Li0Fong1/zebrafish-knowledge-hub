# -*- coding: utf-8 -*-
import bootstrap  # noqa: F401

import os
import re
import sys
import threading

from config import _BASE_DIR
os.environ["HF_ENDPOINT"] = "https://hf-mirror.com"
os.environ["PYTORCH_CUDA_ALLOC_CONF"] = "max_split_size_mb:512"

import torch
from common_utils import logger, clear_gpu_cache

# ===================== 模型配置 =====================
# 本地仅保留轻量 0.5B；重型推理统一走云端 API（.env: LLM_MODE / LLM_API_*）。
# 原 7B GGUF 档（Qwen-7B-Chat.Q4_K_M.gguf）已于 2026-10-31 移除，权重文件本就不存在。
MODEL_TIER_CONFIG = {
    "light": {
        "backend": "transformers",
        "model_path": os.path.join(_BASE_DIR, "models", "Qwen-0.5B-Chat"),
        "model_type": "qwen2",
        "min_ram_gb": 1.0,
        "min_total_ram_gb": 4,
        "description": "轻量快速应答模型（0.5B）"
    }
}

# 历史调用点仍会传 tier="heavy"（cli / common_utils / 老脚本），统一重定向到 light，
# 避免 KeyError，也不让它们静默拿到不存在的 7B。
TIER_ALIASES = {"heavy": "light", "7b": "light", "large": "light"}


def resolve_tier(tier: str) -> str:
    """把历史层级名解析为实际可用的层级。"""
    return TIER_ALIASES.get((tier or "").strip().lower(), (tier or "light").strip().lower())

GENERAL_CONFIG = {
    "max_new_tokens": int(os.getenv("LLM_MAX_NEW_TOKENS", "256")),
    "temperature": 0.1,
    "do_sample": False,
    "repetition_penalty": 1.1
}

# 全局缓存
_llm_instances = {}
_failed_models = set()
_api_llm_instance = None
_reasoning_llm_instance = None


def _api_key_configured() -> bool:
    return bool(os.getenv("LLM_API_KEY", "").strip())


def should_use_api_reasoning() -> bool:
    mode = os.getenv("LLM_MODE", "hybrid").lower()
    return mode in ("api", "hybrid") and _api_key_configured()


def should_skip_local_heavy() -> bool:
    """本地重型模型已移除，恒为 True（保留旧调用点兼容）。

    历史上用于「配置云端推理时跳过本地 7B 加载」，现在没有 7B 可加载了，
    依赖此函数的并发上限 / 预热分支会自动走云端或轻量单模型路径。
    """
    return True


def get_api_llm():
    global _api_llm_instance
    if _api_llm_instance is None:
        from llm_api import ApiLLM
        _api_llm_instance = ApiLLM()
    return _api_llm_instance


def get_reasoning_llm():
    """知识库合成 / 融合 / 数据库解读用的「聪明」模型。"""
    global _reasoning_llm_instance
    if _reasoning_llm_instance is not None:
        return _reasoning_llm_instance

    mode = os.getenv("LLM_MODE", "hybrid").lower()
    if mode in ("api", "hybrid") and _api_key_configured():
        api = get_api_llm()
        if mode == "api":
            _reasoning_llm_instance = api
        else:
            from llm_api import HybridReasoningLLM
            _reasoning_llm_instance = HybridReasoningLLM(api, get_llm(tier="light"))
        return _reasoning_llm_instance

    _reasoning_llm_instance = get_llm(tier="light")
    return _reasoning_llm_instance


def uses_remote_reasoning() -> bool:
    llm = get_reasoning_llm()
    return getattr(llm, "is_remote", False)


# ===================== 内存与文件预检 =====================
def _get_total_ram_gb() -> float:
    try:
        import psutil
        return psutil.virtual_memory().total / (1024 ** 3)
    except ImportError:
        return 999.0


def _get_available_ram_gb() -> float:
    try:
        import psutil
        return psutil.virtual_memory().available / (1024 ** 3)
    except ImportError:
        return 999.0


def _validate_tier_config(config: dict) -> bool:
    """检查本地模型目录是否可加载，返回 False 时 get_llm 会抛错。"""
    model_path = config["model_path"]

    # transformers 轻量模型：不因瞬时可用内存偏低而拒绝加载（0.5B 约需 1~2GB）
    avail_gb = _get_available_ram_gb()
    total_gb = _get_total_ram_gb()
    if avail_gb < 1.5:
        logger.warning(
            f"可用内存 {avail_gb:.1f}GB 偏低（物理 {total_gb:.1f}GB），"
            f"仍尝试加载 {config['description']}；若失败请关闭浏览器/其他 Python 进程后重试"
        )
    if not os.path.isdir(model_path):
        logger.error(f"模型目录不存在：{model_path}")
        return False
    return True

# ===================== 统一LLM抽象类 =====================
class BaseLLM:
    def generate(self, prompt: str, max_new_tokens: int = None) -> str:
        raise NotImplementedError


# ===================== Transformers后端实现 =====================
class TransformersLLM(BaseLLM):
    def __init__(self, model_path: str, model_type: str):
        self.model_path = model_path
        self.model_type = model_type
        # LLM_DEVICE=cpu|cuda 可强制；与大 embedding 同卡时建议轻量 LLM 走 CPU
        forced = (os.getenv("LLM_DEVICE") or "").strip().lower()
        if forced == "cpu":
            self.device = "cpu"
        elif forced == "cuda" and torch.cuda.is_available():
            self.device = "cuda"
        else:
            self.device = "cuda" if torch.cuda.is_available() else "cpu"
        self.tokenizer = None
        self.model = None
        self._load()

    def _load(self):
        from transformers import AutoTokenizer, AutoModelForCausalLM, AutoConfig

        clear_gpu_cache()

        # Qwen1系列处理远程代码路径
        if self.model_type == "qwen1":
            model_dir = os.path.abspath(self.model_path)
            if model_dir not in sys.path:
                sys.path.insert(0, model_dir)
                sys.path.insert(0, os.path.join(model_dir, "transformers_modules"))

        # 加载Tokenizer
        self.tokenizer = AutoTokenizer.from_pretrained(
            self.model_path,
            trust_remote_code=True if self.model_type == "qwen1" else False,
            padding_side="right",
            local_files_only=True,
        )
        if self.tokenizer.pad_token is None:
            self.tokenizer.pad_token = self.tokenizer.eos_token
            self.tokenizer.pad_token_id = self.tokenizer.eos_token_id

        # 加载模型配置
        config = AutoConfig.from_pretrained(
            self.model_path,
            trust_remote_code=True if self.model_type == "qwen1" else False,
            local_files_only=True,
        )

        # CPU关闭flash attention
        if self.device == "cpu" and hasattr(config, "use_flash_attn"):
            config.use_flash_attn = False

        load_kwargs = {
            "pretrained_model_name_or_path": self.model_path,
            "config": config,
            "trust_remote_code": True if self.model_type == "qwen1" else False,
            "local_files_only": True,
            "torch_dtype": torch.float32 if self.device == "cpu" else torch.bfloat16,
        }

        # GPU量化配置（需本机已安装 bitsandbytes；仅有 transformers 里的 Config 不够）
        bnb_config = None
        if self.device == "cuda":
            try:
                import bitsandbytes  # noqa: F401
                from transformers import BitsAndBytesConfig

                bnb_config = BitsAndBytesConfig(load_in_8bit=True)
            except Exception as e:
                logger.warning(f"关闭8bit量化（bitsandbytes 不可用：{e}）；改用 GPU bf16/fp16")

        # 修复：device_map 必须搭配 low_cpu_mem_usage=True
        if bnb_config is not None:
            load_kwargs["quantization_config"] = bnb_config
            load_kwargs["device_map"] = "auto"
            load_kwargs["low_cpu_mem_usage"] = True
        elif self.device == "cuda":
            load_kwargs["device_map"] = "auto"
            load_kwargs["low_cpu_mem_usage"] = True
        else:
            load_kwargs["low_cpu_mem_usage"] = True

        self.model = AutoModelForCausalLM.from_pretrained(**load_kwargs)
        # CPU模式手动移到设备，量化模型无需移动
        if self.device == "cpu" and bnb_config is None:
            self.model = self.model.to(self.device)

        self.model.eval()
        self.model.config.pad_token_id = self.tokenizer.pad_token_id
    @torch.no_grad()
    def generate(self, prompt: str, max_new_tokens: int = None) -> str:
        if not prompt:
            return "请输入有效问题"
        max_tokens = max_new_tokens if max_new_tokens else GENERAL_CONFIG["max_new_tokens"]

        # 有流式 sink 时走 TextIteratorStreamer 逐块推送
        from common_utils import get_stream_sink, emit_stream
        sink = get_stream_sink()
        if sink is not None:
            try:
                from transformers import TextIteratorStreamer

                inputs = self.tokenizer(
                    prompt,
                    return_tensors="pt",
                    padding=True,
                    truncation=True,
                    max_length=512
                ).to(self.device)
                streamer = TextIteratorStreamer(self.tokenizer, skip_prompt=True, skip_special_tokens=True)
                gen_kwargs = {
                    "max_new_tokens": max_tokens,
                    "do_sample": GENERAL_CONFIG["do_sample"],
                    "repetition_penalty": GENERAL_CONFIG["repetition_penalty"],
                    "streamer": streamer,
                }
                if GENERAL_CONFIG["do_sample"]:
                    gen_kwargs["temperature"] = GENERAL_CONFIG["temperature"]
                t = threading.Thread(
                    target=self.model.generate,
                    kwargs={**inputs, **gen_kwargs},
                    daemon=True,
                )
                t.start()
                parts = []
                for piece in streamer:
                    parts.append(piece)
                    emit_stream(piece)
                t.join(timeout=30)
                answer = clean_model_output("".join(parts))
                del inputs
                return answer if answer else "未生成有效回答"
            except Exception as e:
                logger.warning(f"[警告] 流式生成失败，回退普通生成：{e}")

        inputs = self.tokenizer(
            prompt,
            return_tensors="pt",
            padding=True,
            truncation=True,
            max_length=512
        ).to(self.device)

        gen_kwargs = {
            "max_new_tokens": max_tokens,
            "do_sample": GENERAL_CONFIG["do_sample"],
            "repetition_penalty": GENERAL_CONFIG["repetition_penalty"]
        }
        if GENERAL_CONFIG["do_sample"]:
            gen_kwargs["temperature"] = GENERAL_CONFIG["temperature"]

        outputs = self.model.generate(**inputs, **gen_kwargs)
        full_text = self.tokenizer.decode(outputs[0], skip_special_tokens=True)
        input_text = self.tokenizer.decode(inputs["input_ids"][0], skip_special_tokens=True)
        answer = clean_model_output(full_text[len(input_text):])
        del inputs, outputs
        return answer if answer else "未生成有效回答"


# ===================== 输出清理 =====================
import re

# 清理 Qwen 系输出中的 PAD/HTML 残留标记（GGUF 与 transformers 通用）
_PAD_TOKEN_RE = re.compile(r"\[PAD\d+\]")
_TAG_RE = re.compile(r"\[(?:H|/H)\]")


def clean_model_output(text: str) -> str:
    if not text:
        return ""
    text = _PAD_TOKEN_RE.sub("", text)
    text = _TAG_RE.sub("", text)
    return text.strip()


# ===================== 统一加载入口 =====================
def get_llm(tier: str = "light") -> BaseLLM:
    global _llm_instances, _failed_models

    tier = resolve_tier(tier)
    if tier not in MODEL_TIER_CONFIG:
        raise ValueError(f"不支持的模型层级：{tier}，可选：{list(MODEL_TIER_CONFIG.keys())}")

    config = MODEL_TIER_CONFIG[tier]
    model_key = tier

    # 已加载成功直接返回
    if model_key in _llm_instances:
        return _llm_instances[model_key]

    # 失败过的模型直接报错（本地仅剩 light，无更低层级可降级）
    if model_key in _failed_models:
        logger.warning(f"[警告] {config['description']}此前加载失败")
        raise RuntimeError("轻量模型加载失败，系统不可用")

    # 预检失败时不永久标记 light 为失败，便于释放内存后重试
    if not _validate_tier_config(config):
        raise RuntimeError(f"{config['description']} 不可用，系统无法启动")

    # 按后端加载（当前仅 transformers）
    try:
        if config["backend"] != "transformers":
            raise ValueError(f"不支持的后端：{config['backend']}")
        llm = TransformersLLM(config["model_path"], config["model_type"])

        _llm_instances[model_key] = llm
        logger.info(f"[成功] {config['description']}加载成功")
        return llm
    except Exception as e:
        logger.error(f"[错误] {config['description']}加载失败：{str(e)}")
        _failed_models.add(model_key)
        raise


def is_multi_model_ready() -> bool:
    """本地已无 7B 重型档，双模型融合不再可用（保留函数供健康检查调用）。"""
    return False


def _has_transformers_weights(model_dir: str) -> bool:
    if not os.path.isdir(model_dir):
        return False
    if os.path.isfile(os.path.join(model_dir, "config.json")):
        return True
    for root, _, files in os.walk(model_dir):
        for name in files:
            if name.endswith((".safetensors", ".bin")) and "metadata" not in name:
                return True
    return False


def get_model_status() -> dict:
    """模型文件与内存加载状态（供健康检查 / 前端展示）。

    heavy_* 字段保留为固定 False，仅用于兼容既有 API 消费方（前端 / 运维脚本）。
    """
    light_path = MODEL_TIER_CONFIG["light"]["model_path"]
    light_file = _has_transformers_weights(light_path)
    light_loaded = "light" in _llm_instances
    api_ok = _api_key_configured()
    mode = os.getenv("LLM_MODE", "hybrid").lower()
    remote = should_use_api_reasoning()

    return {
        "light_file": light_file,
        "heavy_file": False,
        "light_loaded": light_loaded,
        "heavy_loaded": False,
        "multi_model_ready": remote,
        "llm_mode": mode,
        "api_configured": api_ok,
        "api_model": os.getenv("LLM_API_MODEL", "deepseek-chat") if api_ok else None,
        "reasoning_remote": remote,
    }


def reset_failed_models():
    """清除失败缓存，便于释放内存后重新加载本地模型。"""
    global _failed_models, _llm_instances, _api_llm_instance, _reasoning_llm_instance
    _failed_models.clear()
    _llm_instances.pop("light", None)
    _api_llm_instance = None
    _reasoning_llm_instance = None


if __name__ == "__main__":
    light = get_llm(tier="light")
    print("轻量模型测试：", light.generate("斑马鱼是什么？"))
    print("推理模式：", "云端 API" if should_use_api_reasoning() else "本地轻量 0.5B")