# -*- coding: utf-8 -*-
"""Root shim: re-export core.bootstrap including underscored names."""
from core import bootstrap as _boot

globals().update({k: getattr(_boot, k) for k in dir(_boot) if not k.startswith("__")})
