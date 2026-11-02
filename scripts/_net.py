# -*- coding: utf-8 -*-
"""公共 HTTP 请求：只对网络类错误重试，HTTP 4xx 直接给出“原因 / 影响 / 下一步”。

此前 bili_fetch / bili_transcribe / bili_screenshot 各有一份相同的 http_get：
HTTPError 是 URLError 的子类，412 之类的风控拦截也会被重试 3 次，最后以英文堆栈退出。
这里统一成一份实现：
    - HTTP 4xx：不重试，立即抛 explain_http 生成的 HintError
    - HTTP 5xx、超时、连接被重置：按 1s / 2s 退避重试，用尽后抛 HintError
"""
import time  # 重试退避
import urllib.error  # HTTPError / URLError
import urllib.request  # 发请求

from _hints import PLATFORM_NAMES, HintError, explain_http  # 平台中文名与三段式提示

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "  # 浏览器 UA，与各脚本一致
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
BILI_REFERER = "https://www.bilibili.com/"  # B站接口和视频流都要求带 Referer
DEFAULT_RETRIES = 3  # 默认最多尝试 3 次
NETWORK_NEXT_STEP = "检查网络连接（开了代理的话确认代理可用）后重试"  # 网络类失败的统一下一步


def bili_headers(cookie: str = "") -> dict:  # B站请求头：UA + Referer，可选登录态
    headers = {"User-Agent": UA, "Referer": BILI_REFERER}  # 基础请求头
    if cookie:  # 填了 SESSDATA
        headers["Cookie"] = f"SESSDATA={cookie}"  # 带上登录态
    return headers  # 返回请求头


def _network_error(platform_name: str, impact: str, attempts: int, exc: Exception) -> HintError:  # 网络失败 → 提示
    return HintError(f"连不上{platform_name}（重试 {attempts} 次仍超时或连接被中断）",  # 原因
                     impact, NETWORK_NEXT_STEP, f"{type(exc).__name__}: {exc}")  # 影响、下一步、原始错误


def _open(url: str, headers: dict, timeout: int, retries: int, platform: str, impact: str):  # 带重试地打开连接
    name = PLATFORM_NAMES.get(platform, platform)  # 例如 bilibili → B站
    attempts = max(1, retries)  # 至少尝试一次
    last_exc = None  # 最近一次异常
    for attempt in range(attempts):  # 逐次尝试
        try:  # 发请求
            req = urllib.request.Request(url, headers=headers)  # 构造请求
            return urllib.request.urlopen(req, timeout=timeout)  # 成功则返回响应对象
        except urllib.error.HTTPError as e:  # 服务器返回了状态码（必须先于 URLError 捕获）
            if e.code < 500:  # 4xx：风控、权限、不存在，重试没有意义
                raise explain_http(e.code, platform, impact) from e  # 直接给出三段式提示
            last_exc = e  # 5xx：服务器临时故障，可以重试
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:  # 超时、DNS、连接重置
            last_exc = e  # 记下后重试
        if attempt < attempts - 1:  # 还有重试机会
            time.sleep(attempt + 1)  # 1s、2s 退避
    if isinstance(last_exc, urllib.error.HTTPError):  # 5xx 重试用尽
        raise explain_http(last_exc.code, platform, impact) from last_exc  # 按状态码解释
    raise _network_error(name, impact, attempts, last_exc) from last_exc  # 网络类错误重试用尽


def http_get(url: str, headers: dict, retries: int = DEFAULT_RETRIES, timeout: int = 60,  # 读取完整响应体
             platform: str = "bilibili", impact: str = "") -> bytes:
    """GET 并返回响应体；失败时抛 HintError（RuntimeError 子类，现有 except Exception 照常捕获）。"""
    with _open(url, headers, timeout, retries, platform, impact) as resp:  # 打开连接
        return resp.read()  # 读完整响应


def download_to(url: str, headers: dict, dest, retries: int = 1, timeout: int = 120,  # 流式下载到文件
                platform: str = "bilibili", impact: str = "", chunk: int = 1 << 20):
    """按 1MB 分块写入 dest；默认只尝试一次（与改造前一致），错误同样转成 HintError。"""
    with _open(url, headers, timeout, retries, platform, impact) as resp, open(dest, "wb") as f:  # 连接与文件
        try:  # 读取过程中也可能断线
            while data := resp.read(chunk):  # 分块读取
                f.write(data)  # 写入文件
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:  # 下载到一半断开
            name = PLATFORM_NAMES.get(platform, platform)  # 平台中文名
            raise _network_error(name, impact, 1, e) from e  # 转成三段式提示
    return dest  # 返回目标路径
