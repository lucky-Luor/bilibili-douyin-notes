# -*- coding: utf-8 -*-
"""pytest 共享配置：把 scripts/ 加入 sys.path，并为缺失的重依赖打桩。

全部测试离线可跑：
    - faster-whisper / av / gmssl 均为惰性 import 或在此打桩，未安装也不影响；
    - 任何测试不得发起真实网络请求（涉及网络的函数一律 mock）。
"""
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))


def _stub_gmssl() -> None:
    """gmssl 未安装时提供最小桩（douyin_fetch -> abogus -> gmssl.sm3）。"""
    if "gmssl" in sys.modules:
        return
    try:
        import gmssl  # noqa: F401
        return
    except ImportError:
        pass
    gm = types.ModuleType("gmssl")
    sm3 = types.ModuleType("gmssl.sm3")
    func = types.ModuleType("gmssl.func")
    sm3.sm3_hash = lambda *a, **k: ""  # type: ignore[attr-defined]
    func.bytes_to_list = lambda b: list(b)  # type: ignore[attr-defined]
    gm.sm3 = sm3  # type: ignore[attr-defined]
    gm.func = func  # type: ignore[attr-defined]
    sys.modules["gmssl"] = gm
    sys.modules["gmssl.sm3"] = sm3
    sys.modules["gmssl.func"] = func


_stub_gmssl()
