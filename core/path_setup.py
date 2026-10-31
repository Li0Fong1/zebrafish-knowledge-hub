# -*- coding: utf-8 -*-
"""项目路径初始化。"""
import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.environ["ZEBRAFISH_PROJECT_ROOT"] = PROJECT_ROOT
os.chdir(PROJECT_ROOT)

_MODULE_DIRS = ("core", "agent", "llm", "database", "rag", "api", "cli", "scripts")
for _name in _MODULE_DIRS:
    _path = os.path.join(PROJECT_ROOT, _name)
    if os.path.isdir(_path) and _path not in sys.path:
        sys.path.insert(0, _path)
