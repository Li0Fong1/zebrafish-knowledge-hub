# -*- coding: utf-8 -*-
"""上传文件校验：扩展名白名单、魔数检测、内容扫描、路径安全。"""
from __future__ import annotations

import os
import re
from typing import Optional, Tuple

from common_utils import logger

# 仅允许纯文本类知识库入库
ALLOWED_UPLOAD_EXTENSIONS = frozenset({".pdf", ".txt", ".md"})

# 危险扩展名（含双扩展名检测）
BLOCKED_EXTENSIONS = frozenset({
    ".exe", ".bat", ".cmd", ".com", ".scr", ".pif", ".msi", ".dll", ".sys",
    ".sh", ".bash", ".zsh", ".ps1", ".vbs", ".wsf", ".js", ".mjs", ".jar", ".war",
    ".php", ".asp", ".aspx", ".jsp", ".cgi", ".pl", ".py", ".rb", ".lua",
    ".html", ".htm", ".xhtml", ".svg", ".xml", ".xsl",
    ".zip", ".rar", ".7z", ".tar", ".gz", ".bz2", ".xz",
    ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".odt",
    ".sql", ".db", ".sqlite", ".mdb",
    ".lnk", ".reg", ".inf", ".hta", ".cab", ".iso",
})

_BLOCKED_NAME_RE = re.compile(
    r"(?:\.\.|[\\/:<>|\"?\*]|%2e%2e|%00|\x00)",
    re.I,
)

_SCRIPT_PATTERNS = re.compile(
    r"(<script\b|javascript:|onerror\s*=|onload\s*=|<iframe\b|data:text/html)",
    re.I,
)


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


def upload_limits() -> dict:
    return {
        "max_bytes": _env_int("WEB_UPLOAD_MAX_BYTES", 5 * 1024 * 1024),
        "max_text_chars": _env_int("WEB_UPLOAD_MAX_TEXT_CHARS", 500_000),
        "max_pdf_pages": _env_int("WEB_UPLOAD_MAX_PDF_PAGES", 120),
        "max_files_in_library": _env_int("WEB_UPLOAD_MAX_FILES", 300),
        "allowed_extensions": sorted(ALLOWED_UPLOAD_EXTENSIONS),
    }


def uploads_enabled() -> bool:
    return os.getenv("WEB_ALLOW_UPLOAD", "1").lower() not in ("0", "false", "no")


def sanitize_filename(name: str, *, default: str = "upload.txt") -> str:
    """仅保留 basename，去除路径遍历与非法字符。"""
    if not name:
        return default
    base = os.path.basename(name.replace("\\", "/").split("\x00")[0].split("%00")[0])
    base = re.sub(r'[<>:"|?*\x00-\x1f]', "_", base).strip(". ")
    if not base or _BLOCKED_NAME_RE.search(base):
        return default
    if len(base) > 180:
        stem, ext = os.path.splitext(base)
        base = stem[:160] + ext[:20]
    return base or default


def _all_extensions(filename: str) -> list:
    """列出文件名中所有扩展名片段（用于拦截 document.pdf.exe）。"""
    base = sanitize_filename(filename)
    parts = base.lower().split(".")
    if len(parts) <= 1:
        return []
    return ["." + p for p in parts[1:]]


def validate_filename(filename: str) -> Tuple[Optional[str], Optional[str]]:
    """返回 (safe_name, error)。"""
    safe = sanitize_filename(filename)
    exts = _all_extensions(safe)
    if not exts:
        return None, "文件名须包含扩展名（仅支持 .pdf / .txt / .md）"
    final_ext = exts[-1]
    if final_ext not in ALLOWED_UPLOAD_EXTENSIONS:
        return None, f"不允许的文件类型：{final_ext}（仅 PDF/TXT/MD）"
    for ext in exts:
        if ext in BLOCKED_EXTENSIONS and ext not in ALLOWED_UPLOAD_EXTENSIONS:
            return None, f"检测到危险扩展名：{ext}"
    if len(exts) >= 2 and exts[-2] in BLOCKED_EXTENSIONS:
        return None, "检测到双扩展名伪装"
    return safe, None


def _magic_ok(data: bytes, ext: str) -> Tuple[bool, str]:
    if not data:
        return False, "文件为空"
    if ext == ".pdf":
        if not data.startswith(b"%PDF"):
            return False, "文件内容与 PDF 格式不符（魔数校验失败）"
        if len(data) > 512:
            head = data[:512].lower()
            if b"/javascript" in head or b"/js" in head or b"/launch" in head:
                return False, "PDF 含可疑脚本对象，已拒绝"
        return True, ""
    if ext in (".txt", ".md"):
        sample = data[:8192]
        if sample.count(b"\x00") > 0:
            return False, "文本文件含二进制空字节，已拒绝"
        try:
            sample.decode("utf-8")
        except UnicodeDecodeError:
            try:
                sample.decode("utf-8", errors="strict")
            except UnicodeDecodeError:
                return False, "文本文件须为 UTF-8 编码"
        return True, ""
    return False, "未知类型"


def scan_text_content(text: str) -> Optional[str]:
    if not text:
        return "提取文本为空"
    if len(text) > _env_int("WEB_UPLOAD_MAX_TEXT_CHARS", 500_000):
        return f"文本超过上限 {_env_int('WEB_UPLOAD_MAX_TEXT_CHARS', 500_000)} 字符"
    if _SCRIPT_PATTERNS.search(text[:50000]):
        return "文本含可疑脚本片段，已拒绝"
    return None


def validate_upload_bytes(
    filename: str,
    data: bytes,
    content_type: Optional[str] = None,
) -> Tuple[str, bytes, Optional[str]]:
    """
    完整校验上传内容。
    返回 (safe_filename, data, error)；error 非空时拒绝入库。
    """
    if not uploads_enabled():
        return "", b"", "文件上传已禁用（WEB_ALLOW_UPLOAD=0）"

    safe_name, err = validate_filename(filename)
    if err:
        return "", b"", err

    ext = os.path.splitext(safe_name)[1].lower()
    max_bytes = _env_int("WEB_UPLOAD_MAX_BYTES", 5 * 1024 * 1024)
    if len(data) > max_bytes:
        return "", b"", f"文件超过大小上限（{max_bytes // 1024 // 1024} MB）"

    ok, msg = _magic_ok(data, ext)
    if not ok:
        logger.warning(f"[安全] 上传魔数校验失败：{safe_name} — {msg}")
        return "", b"", msg

    if content_type:
        ct = content_type.split(";")[0].strip().lower()
        allowed_ct = {
            ".pdf": ("application/pdf", "application/x-pdf"),
            ".txt": ("text/plain", "application/octet-stream"),
            ".md": ("text/markdown", "text/plain", "application/octet-stream"),
        }
        if ext in allowed_ct and ct not in allowed_ct[ext] and ct != "application/octet-stream":
            return "", b"", f"Content-Type 与扩展名不匹配（{ct} / {ext}）"

    if ext in (".txt", ".md"):
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            return "", b"", "文本文件须为 UTF-8 编码"
        scan_err = scan_text_content(text)
        if scan_err:
            return "", b"", scan_err

    return safe_name, data, None


def check_library_quota(extra_dir: str) -> Optional[str]:
    """限制 knowledge.d 文件总数，防止磁盘打满。"""
    if not extra_dir or not os.path.isdir(extra_dir):
        return None
    max_files = _env_int("WEB_UPLOAD_MAX_FILES", 300)
    n = sum(
        1 for fn in os.listdir(extra_dir)
        if fn.endswith((".txt", ".md")) and os.path.isfile(os.path.join(extra_dir, fn))
    )
    if n >= max_files:
        return f"知识库文件数已达上限（{max_files}），请先清理 rag/knowledge.d"
    return None


def validate_path_param(value: str, *, label: str = "参数", max_len: int = 128) -> Tuple[str, Optional[str]]:
    """API 路径/查询参数防路径遍历与注入字符。"""
    v = (value or "").strip()
    if not v:
        return "", f"{label}不能为空"
    if len(v) > max_len:
        return "", f"{label}过长（上限 {max_len}）"
    if _BLOCKED_NAME_RE.search(v) or re.search(r"[;'\"\\]", v):
        return "", f"{label}含非法字符"
    return v, None
