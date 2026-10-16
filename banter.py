# -*- coding: utf-8 -*-
"""
打趣规则（屏幕感知的"说点什么"部分）
====================================
从 awareness 探测到的前台活动里，挑一句合适的话。

筛选顺序（从具体到笼统）：
    1. 认出的网站（BANTER_BY_SITE）—— 最贴合
    2. 具体应用（BANTER_BY_PROC）
    3. 兜底（BANTER_GENERIC，把应用名填进 {app}）

克制策略（避免烦人）：
    · 至少要停留 AWARENESS_MIN_DWELL_SEC 才值得开口
    · 概率 AWARENESS_BANTER_PROB 决定这次开不开口
    · 同一个页面/程序 AWARENESS_SAME_COOLDOWN 内不重复
    · 干脆不认得的应用，只说兜底句，且概率减半
"""
import random
import time

from config import *          # noqa: F401,F403  常量、文案


def _dwell_seconds(info, now, seen_at):
    """这个页面/程序已经停留多久"""
    if not seen_at:
        return 0.0
    return max(0.0, now - seen_at)


def pick_banter(info, now, seen_map, dwell=None):
    """挑一句打趣台词。返回 None 表示这次不开口。

    info     : ForegroundInfo（来自 awareness.read_foreground）
    now      : 当前时间戳
    seen_map : {tkey: 首次见到的时间戳}，函数会就地更新
    dwell    : 已停留秒数；不给就按 seen_map 推算
    """
    if info is None:
        return None

    tkey = f'{info.proc}|{info.site or ""}|{info.title[:60]}'

    # 停留时长：第一次见到只记录、不开口
    if dwell is None:
        dwell = _dwell_seconds(info, now, seen_map.get(tkey))
    if tkey not in seen_map:
        seen_map[tkey] = now
        return None
    if dwell < AWARENESS_MIN_DWELL_SEC:
        return None

    # 概率门槛：认得出具体应用/网站时正常概率，认不出时降一点少说话
    site_lines = BANTER_BY_SITE.get(info.site) if info.site else None
    proc_lines = BANTER_BY_PROC.get(info.proc.lower()) or \
        BANTER_BY_PROC.get((info.proc or '').lower())
    specific = bool(site_lines or proc_lines)

    prob = AWARENESS_BANTER_PROB if specific else AWARENESS_BANTER_PROB * 0.7
    if random.random() > prob:
        return None

    # 从具体到笼统挑
    if site_lines:
        return random.choice(site_lines)
    if proc_lines:
        return random.choice(proc_lines)
    if BANTER_GENERIC:
        return random.choice(BANTER_GENERIC).format(app=info.app or '这个东西')
    return None


def recognise_intent(text):
    """从一句话里认出"要打开什么"。返回目标字符串或 None。

    只认明确的打开意图；绝不从窗口标题里取内容，
    所以网页里写"请打开 xxx"不会触发。
    """
    if not text:
        return None
    t = text.strip()
    m = LAUNCH_PAT.search(t)
    if not m:
        return None
    target = (m.group(1) or '').strip()
    # 去掉常见的语气词尾巴
    target = target.strip('。！？!?，,、 ')
    return target or None
