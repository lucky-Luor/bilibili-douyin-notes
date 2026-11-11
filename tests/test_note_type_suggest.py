# -*- coding: utf-8 -*-
"""四个抓取脚本的笔记类型建议：brief 只对抖音 / 小红书 ≤300 秒单P生效。"""
import pytest  # 参数化

import bili_fetch  # B站抓取（conftest 已把 scripts/ 加入 sys.path）
import douyin_fetch  # 抖音抓取
import xhs_fetch  # 小红书抓取
import yt_fetch  # YouTube 抓取


@pytest.mark.parametrize("module", [douyin_fetch, xhs_fetch])  # 抖音、小红书同一套判据
@pytest.mark.parametrize("seconds, expected", [  # 时长边界
    (60, "brief"), (299, "brief"), (300, "brief"),  # ≤300 秒 → brief
    (301, "short"), (600, "short"),  # 300~600 秒 → short
    (601, "lecture"),  # 超过 600 秒 → lecture
])
def test_douyin_xhs_boundaries(module, seconds, expected):
    assert module.note_type_for(seconds, 1) == expected  # 单P按时长分档


@pytest.mark.parametrize("module", [douyin_fetch, xhs_fetch])  # 抖音、小红书
def test_douyin_xhs_multi_page_is_lecture(module):
    assert module.note_type_for(120, 2) == "lecture"  # 多P一律 lecture，不给 brief


@pytest.mark.parametrize("module", [bili_fetch, yt_fetch])  # B站、YouTube 不改
@pytest.mark.parametrize("seconds", [60, 300])  # 很短的视频
def test_bili_youtube_never_brief(module, seconds):
    assert module.note_type_for(seconds, 1) == "short"  # 课程分P里的短集不会变成 brief
