# -*- coding: utf-8 -*-
"""统一失败提示：每条失败都说清“原因 / 影响 / 下一步”，不只丢一段报错堆栈。

用法（供各平台脚本导入）:
    from _hints import HintError, hint_exit, explain_http, explain_bili_code, explain_douyin_filter

    raise HintError("视频已删除", "无法生成这条笔记", "换一个链接再试")   # 可恢复：调用方可捕获后继续下一集
    hint_exit("链接里找不到 BV 号", "无法开始", "把完整的视频链接发过来")    # 不可恢复：直接人话退出，不打印堆栈

输出格式（三行，必要时附第四行“详情”）:
    原因：……
    影响：……
    下一步：……
"""

RISK_CONTROL_CODES = (412, 461, 471)  # 平台风控拦截常见的 HTTP 状态码（412 B站，461/471 小红书验证码）

# HTTP 状态码 → (原因, 下一步)；{platform} 会替换成平台中文名
HTTP_HINTS = {
    412: ("请求被{platform}风控拦截（云服务器 / 机房 IP 上常见）",  # B站对机房 IP 返回 412
          "换到家用网络或在本机运行；短时间内请求过多时等几分钟再试"),
    403: ("{platform}拒绝访问（可能需要登录、内容不公开，或接口规则已变）",  # 权限或接口变动
          "确认在浏览器里不登录也能看这个视频；仍不行请提 issue 附上链接"),
    404: ("{platform}上找不到这个内容（链接有误或已删除）",  # 内容不存在
          "在浏览器里打开链接确认还能看，再重新复制链接"),
    429: ("请求太频繁，被{platform}限流",  # 限流
          "等几分钟再试，批量处理时不要并发"),
    461: ("{platform}要求人机验证（触发风控）",  # 小红书验证码
          "稍后再试，或在 config.json 配置 xhs_cookie 后重跑"),
    471: ("{platform}要求人机验证（触发风控）",  # 小红书验证码
          "稍后再试，或在 config.json 配置 xhs_cookie 后重跑"),
}

PLATFORM_NAMES = {"bilibili": "B站", "douyin": "抖音", "youtube": "YouTube", "xiaohongshu": "小红书"}  # 平台中文名

# B站业务错误码 → (原因, 下一步)
BILI_CODES = {
    -404: ("视频不存在或已删除", "在浏览器里确认链接还能打开，再换一个链接"),  # 稿件不存在
    62002: ("稿件不可见（已删除、被下架或设为私密）", "换一个公开可见的视频链接"),  # 稿件不可见
    62004: ("稿件正在审核中", "等审核通过后再试"),  # 审核中
    62012: ("稿件仅 UP 主自己可见", "换一个公开可见的视频链接"),  # 仅自己可见
    -403: ("访问权限不足（可能是大会员、付费或充电专属内容）", "本工具不处理付费内容，请换一个公开视频"),  # 权限不足
    -412: ("请求被 B站风控拦截", "换到家用网络或在本机运行；等几分钟再试"),  # 风控
    -352: ("请求被 B站风控校验拦截", "换到家用网络或在本机运行；等几分钟再试"),  # 风控校验
    -101: ("这个操作需要登录 B站", "运行 python scripts/bili_fetch.py --login 扫码登录后重试"),  # 未登录
}

DEFAULT_IMPACT = "这一步没有完成"  # 调用方没给影响说明时的默认文案


class HintError(RuntimeError):  # 可恢复的失败：调用方可以捕获后继续（例如跳过这一集）
    def __init__(self, reason: str, impact: str, next_step: str, detail: str = ""):  # 三段式 + 可选详情
        self.reason = reason  # 原因
        self.impact = impact  # 影响
        self.next_step = next_step  # 下一步
        self.detail = detail  # 原始错误等补充信息
        super().__init__(format_hint(reason, impact, next_step, detail))  # str(e) 即完整提示

    def to_dict(self) -> dict:  # 供 JSON 摘要使用
        return {"reason": self.reason, "impact": self.impact,  # 原因与影响
                "next_step": self.next_step, "detail": self.detail}  # 下一步与详情


def format_hint(reason: str, impact: str, next_step: str, detail: str = "") -> str:  # 拼出三段式提示
    lines = [f"原因：{reason}", f"影响：{impact or DEFAULT_IMPACT}", f"下一步：{next_step}"]  # 三段
    if detail:  # 有补充信息
        lines.append(f"详情：{detail[:300]}")  # 截断后附在最后
    return "\n".join(lines)  # 换行拼接


def hint_exit(reason: str, impact: str, next_step: str, detail: str = ""):  # 不可恢复：人话退出
    raise SystemExit(format_hint(reason, impact, next_step, detail))  # SystemExit 只打印文案、不打印堆栈


def explain_http(code: int, platform: str, impact: str = "", detail: str = "") -> HintError:  # HTTP 状态码 → 提示
    name = PLATFORM_NAMES.get(platform, platform)  # 平台中文名
    reason, next_step = HTTP_HINTS.get(code, (f"{name}返回了 HTTP {code}",  # 未收录的状态码
                                              "稍后重试；持续出现请提 issue 附上链接和这段提示"))
    return HintError(reason.format(platform=name), impact, next_step.format(platform=name),  # 构造提示
                     detail or f"HTTP {code}")  # 详情默认写状态码


def explain_bili_code(code: int, message: str = "", impact: str = "") -> HintError:  # B站业务码 → 提示
    reason, next_step = BILI_CODES.get(code, (f"B站接口返回错误 code={code}",  # 未收录的业务码
                                              "稍后重试；持续出现请提 issue 附上链接和这段提示"))
    return HintError(reason, impact, next_step, f"code={code} {message}".strip())  # 详情保留原始码和信息


def explain_douyin_filter(data: dict, impact: str = "") -> HintError:  # 抖音详情接口没有 aweme_detail 时的解释
    filt = (data or {}).get("filter_detail") or {}  # 平台给出的过滤原因
    if filt:  # 平台明确说了作品被过滤（删除 / 私密 / 仅粉丝可见）
        return HintError("作品不可见（已删除、设为私密或仅粉丝可见）", impact,  # 不是签名问题
                         "在抖音 App 里确认这个作品能公开观看，或换一个链接",
                         str(filt.get("detail_msg") or ""))  # 平台原话作为详情
    return HintError("抖音详情接口没有返回作品数据，多半是签名算法已被平台升级", impact,  # 真正的签名失效
                     "按 README「已知限制」更新 abogus.py，或提 issue 告诉维护者",
                     str(data)[:300])  # 原始响应作为详情


def failure_record(exc: Exception, impact: str, rerun: str = "") -> dict:  # 单集失败 → 进度 JSON 里的结构化字段
    """保留原有 error 字段（兼容旧的解析方式），另附 reason / impact / next_step，交付回执可直接引用。"""
    if isinstance(exc, HintError):  # 已经是三段式
        reason, next_step, detail = exc.reason, exc.next_step, exc.detail  # 直接沿用
        impact = exc.impact or impact  # 抛出处给的影响优先
    else:  # 普通异常：原因取首行，下一步给通用建议
        reason = (str(exc).strip().splitlines() or [type(exc).__name__])[0][:200]  # 首行原文
        next_step, detail = "按补跑命令重试这一集；仍失败请提 issue 附上这段提示", type(exc).__name__  # 通用建议
    if rerun:  # 有补跑命令
        next_step = f"{next_step}；补跑：{rerun}"  # 附在下一步末尾
    return {"error": format_hint(reason, impact, next_step, detail),  # 兼容字段：完整三段文案
            "reason": reason, "impact": impact or DEFAULT_IMPACT,  # 原因与影响
            "next_step": next_step, "rerun": rerun}  # 下一步与补跑命令
