# -*- coding: utf-8 -*-
"""faster-whisper 与新版 PyAV 的兼容层。

faster-whisper 1.2.1 解码音频时调用 av.open(..., metadata_errors="ignore")，
PyAV 19 删掉了这个参数（实测 18.1 仍支持，19.0.x 报错），于是没有字幕的视频一转写就报
TypeError: open() got an unexpected keyword argument 'metadata_errors'。
上游已改成“带参数失败就去掉参数重试”，但尚未发布新版；这里用同样的办法兼容，
faster-whisper 发布修复版后本层不起作用，可以删除。
"""


class _AvProxy:  # 包一层 av 模块：只改 open，其余属性原样转发
    def __init__(self, real):  # 保存真正的 av 模块
        self._real = real  # 原始模块

    def __getattr__(self, name):  # 其他属性（audio、error 等）直接取原模块
        return getattr(self._real, name)  # 原样转发

    def open(self, *args, **kwargs):  # 兼容 metadata_errors 参数
        try:  # 先按调用方原样调用（老版 PyAV 支持该参数）
            return self._real.open(*args, **kwargs)  # 正常返回
        except TypeError:  # 新版 PyAV 不认识这个参数
            if "metadata_errors" not in kwargs:  # 不是这个参数引起的
                raise  # 原样抛出
            kwargs.pop("metadata_errors")  # 去掉不再支持的参数
            return self._real.open(*args, **kwargs)  # 重试


def ensure() -> bool:  # 给 faster_whisper.audio 装上兼容层；返回是否本次新装
    import faster_whisper.audio as fa  # 惰性导入：只在真正转写时才需要
    real = getattr(fa, "av", None)  # faster-whisper 解码用的 av 模块
    if real is None or isinstance(real, _AvProxy):  # 结构不同（如被测试打桩）或已经装过
        return False  # 不包装
    fa.av = _AvProxy(real)  # decode_audio 通过模块全局名 av 调用 open，替换后即生效
    return True  # 本次新装
