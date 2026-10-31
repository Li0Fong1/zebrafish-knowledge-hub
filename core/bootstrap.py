# -*- coding: utf-8 -*-
"""
程序启动引导：必须在导入 torch / transformers 等库之前执行。
解决 Windows 控制台中文乱码，并开启中文静默模式（屏蔽第三方英文日志）。
"""
import io
import logging
import os
import sys
import warnings


def _fix_windows_console():
    os.environ["PYTHONIOENCODING"] = "utf-8"
    os.environ["PYTHONUTF8"] = "1"
    os.environ["PYTHONLEGACYWINDOWSSTDIO"] = "utf-8"

    if sys.platform != "win32":
        return

    try:
        import ctypes
        kernel32 = ctypes.windll.kernel32
        kernel32.SetConsoleOutputCP(65001)
        kernel32.SetConsoleCP(65001)
        title = os.environ.get("CONSOLE_TITLE", "").strip()
        if title:
            kernel32.SetConsoleTitleW(title)
    except Exception:
        pass

    try:
        os.system("chcp 65001 >nul 2>&1")
    except Exception:
        pass

    for name, stream in (("stdout", sys.stdout), ("stderr", sys.stderr), ("stdin", sys.stdin)):
        if stream is None:
            continue
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
            except Exception:
                pass
        elif hasattr(stream, "buffer"):
            try:
                wrapped = io.TextIOWrapper(
                    stream.buffer,
                    encoding="utf-8",
                    errors="replace",
                    line_buffering=True,
                    write_through=True,
                )
                setattr(sys, name, wrapped)
            except Exception:
                pass


def write_console(text: str, *, err: bool = False) -> None:
    """Windows 控制台 Unicode 输出，避免 UTF-8 字节流乱码。"""
    if sys.platform == "win32":
        try:
            import ctypes
            kernel32 = ctypes.windll.kernel32
            handle = kernel32.GetStdHandle(-12 if err else -11)
            mode = ctypes.c_ulong()
            if kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
                written = ctypes.c_ulong()
                if kernel32.WriteConsoleW(
                    handle, text, len(text), ctypes.byref(written), None
                ):
                    return
        except Exception:
            pass
    stream = sys.stderr if err else sys.stdout
    try:
        if hasattr(stream, "buffer"):
            stream.buffer.write(text.encode("utf-8", errors="replace"))
            stream.buffer.flush()
        else:
            stream.write(text)
            stream.flush()
    except Exception:
        pass


def _silence_third_party():
    warnings.filterwarnings("ignore")
    os.environ["TOKENIZERS_PARALLELISM"] = "false"
    os.environ["HF_HUB_DISABLE_PROGRESS_BARS"] = "1"
    os.environ["TQDM_DISABLE"] = "1"
    os.environ["MODELSCOPE_LOG_LEVEL"] = "40"
    os.environ["TRANSFORMERS_NO_ADVISORY_WARNINGS"] = "1"

    for name in (
        "transformers", "sentence_transformers", "faiss", "faiss.loader",
        "httpx", "huggingface_hub", "urllib3", "filelock", "llama_cpp",
        "modelscope", "langchain", "langchain_community", "torch",
    ):
        logging.getLogger(name).setLevel(logging.CRITICAL)

    try:
        from transformers.utils import logging as tf_logging
        tf_logging.set_verbosity_error()
        tf_logging.disable_progress_bar()
    except Exception:
        pass

    try:
        import tqdm
        _orig = tqdm.tqdm

        def _quiet_tqdm(*args, **kwargs):
            kwargs.setdefault("disable", True)
            return _orig(*args, **kwargs)

        tqdm.tqdm = _quiet_tqdm
    except Exception:
        pass


# 模块被 import 时立即执行
_fix_windows_console()
_silence_third_party()
