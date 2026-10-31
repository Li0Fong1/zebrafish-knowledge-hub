# -*- coding: utf-8 -*-
"""Root shim: re-export core.config including underscored names."""
from core import config as _cfg

globals().update({k: getattr(_cfg, k) for k in dir(_cfg) if not k.startswith("__")})
