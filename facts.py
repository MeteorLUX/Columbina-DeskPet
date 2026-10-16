# -*- coding: utf-8 -*-
"""
事实问答（FactsMixin）
======================
"现在几点""今天几号""天气怎么样"这类问题，**直接本地精准回答**，
不走大模型。

为什么不能靠大模型：
  · 提示词里没有实时数据时，她只能猜，答案笼统甚至编造（幻觉）
  · 即使注入了时间，大模型也可能算错、漏算、答得啰嗦
  · 这类问题有唯一正确答案，本地查表 0 延迟、100% 准确

设计原则：
  · **只拦真正的"问事实"**，绝不误吞闲聊
    （"今天几号"要拦，"今天心情不好"不能拦）
  · 回答用她的人设口吻，但事实部分精确到分钟
  · 天气没配城市 / 查不到时，老实说不知道，不编
"""
import datetime
import random
import re
import time

from config import *          # noqa: F401,F403  常量、文案、Config
from holidays import (holiday_of_day, weekday_cn, date_line,
                      solar_holiday, lunar_holiday)

# ==================== 意图正则 ====================
# 时间：现在几点 / 几点了 / 什么时间 / 现在时间 / what time
TIME_PAT = re.compile(
    r"(现在|此刻|当前|这会儿)?\s*"
    r"(几点|几时|什么时间|什么时候了|时间是多少|多晚了|"
    r"what\s*time|current\s*time|the\s*time)",
    re.I)

# 日期：今天几号 / 今天星期几 / 今天什么日子 / 几月几号 / 今天日期
DATE_PAT = re.compile(
    r"(今天|今日|现在|当前|明天|昨天)?\s*"
    r"(是)?\s*"
    r"(几号|几月几号|几月几日|什么日子|星期几|周几|礼拜几|"
    r"日期|几月|哪一年|哪年|"
    r"what\s*day|what'?s?\s*the\s*date|today'?s?\s*date)",
    re.I)

# 天气：天气 / 下雨吗 / 冷不冷 / 多少度 / 需要带伞吗 / 有没有雨
WEATHER_PAT = re.compile(
    r"(今天|现在|外面|明天|这边|那边)?\s*"
    r"(的)?\s*"
    r"(天气|气温|温度|多少度|几度|下雨|下雪|会下雨|要下雨|"
    r"有没有雨|有没有雪|会不会下雨|会不会下雪|"
    r"带伞|冷不冷|热不热|冷吗|热吗|"
    r"weather|temperature|rain|snow)",
    re.I)

# 这些是"提议 / 计划 / 闲聊"的语气，说明不是在问事实，而是想聊
# （"我们要不要一起出去走走看看天气" = 在约你出门，不是在问天气）
CHITCHAT_PAT = re.compile(
    r"(要不要|一起|打算|准备|想不想|陪我|带我去|约|"
    r"我觉得|我认为|感觉|心情|情绪|怎么办|聊聊|说说)", re.I)

# 排他：这些是在聊别的，别当"问日期"
DATE_EXCLUDE = re.compile(
    r"(心情|感觉|状态|怎么|为什么|如何|计划|打算|安排|"
    r"做了什么|发生|过得)", re.I)


class FactsMixin:
    """事实问答：时间 / 日期 / 天气"""

    def setup_facts(self):
        self._facts_last = {}      # 同类问题的最小间隔，防抖

    # ---------- 注入给大模型的实时事实 ----------
    def facts_context(self):
        """组装【此刻的真实信息】文本块，拼进系统提示词。

        这是"她知道自己现在几点"的关键：不注入的话，
        大模型只能猜，答案就会笼统甚至编造。
        """
        now = datetime.datetime.now()
        hh = now.hour
        period = self._facts_period(hh)
        date_txt = date_line(now.date())

        name, _line = holiday_of_day(self.cfg['birthday'], now.date())
        if name:
            holiday = name
        else:
            holiday = '（今天不是什么节日）'

        try:
            info = getattr(self, '_weather', None)
            info = info.info if info is not None else None
        except Exception:
            info = None
        city = (self.cfg['weather_city'] or '').strip()
        if not city:
            weather = '不知道（还没设置城市，请在 config.json 里填 weather_city）'
        elif info is None or not info.ok:
            weather = f'暂时没查到（城市：{city}）'
        else:
            weather = info.label()
            tmp = []
            if info.tmax is not None and info.tmin is not None:
                tmp.append(f'{round(info.tmin)}~{round(info.tmax)}°C')
            if info.rain_prob is not None:
                tmp.append(f'降水概率 {round(info.rain_prob)}%')
            if tmp:
                weather += f'（{"，".join(tmp)}）'
            if info.special:
                weather += ' ⚠特殊天气'

        return FACTS_PROMPT.format(
            time=f'{hh:02d}:{now.minute:02d}',
            period=period,
            date=date_txt,
            holiday=holiday,
            weather=weather).strip()

    @staticmethod
    def _facts_period(hh):
        """小时 -> 时段词（用 config 的表，保证与提示词一致）"""
        last = '深夜'
        for limit, word in FACTS_PERIODS:
            if hh < limit:
                return word
            last = word
        return last

    # ---------- 统一入口 ----------
    def facts_answer(self, text):
        """能精准回答就返回字符串；不是事实类问题返回 None。"""
        t = (text or '').strip()
        if not t:
            return None
        # 长句是"在聊天"，不是在问事实。
        # 但"关于XX时间"这种长句仍是在问，所以放宽到 40 字。
        if len(t) > 40:
            return None

        # 顺序要紧：先认"天气"，再"日期"，最后"时间"
        # （"今天天气" 同时含日期词，必须先被天气接住）
        if self._facts_is_weather(t):
            return self._facts_weather()
        if self._facts_is_date(t):
            return self._facts_date(t)
        if TIME_PAT.search(t):
            return self._facts_time()
        return None

    # ---------- 意图判别 ----------
    @staticmethod
    def _facts_is_weather(t):
        if not WEATHER_PAT.search(t):
            return False
        # "今天天气不错" 是在陈述，不是在问
        if re.search(r"(不错|很好|真好|糟糕|好极了|真好呀)", t):
            return False
        # 提议/计划/闲聊的语气 -> 她想聊，不是在问天气
        # （"我们要不要一起出去走走看看天气" = 约你出门）
        if CHITCHAT_PAT.search(t):
            return False
        return True

    @staticmethod
    def _facts_is_date(t):
        if not DATE_PAT.search(t):
            return False
        # 排除"今天心情不好"这类
        if DATE_EXCLUDE.search(t):
            return False
        return True

    # ---------- 时间 ----------
    def _facts_time(self):
        now = datetime.datetime.now()
        hh = now.hour
        mm = now.minute
        period = self._facts_period(hh)
        h12 = hh % 12 or 12
        exact = f'{hh:02d}:{mm:02d}'
        head = random.choice([
            f'……现在是 {exact}',
            f'……{period}{h12}点{mm:02d}分',
            f'……{exact}',
        ])
        return f'{head}。'

    # ---------- 日期 ----------
    def _facts_date(self, t):
        # 支持"明天/昨天"
        today = datetime.date.today()
        if '明天' in t:
            d = today + datetime.timedelta(days=1)
        elif '昨天' in t:
            d = today - datetime.timedelta(days=1)
        else:
            d = today

        name, line = holiday_of_day(self.cfg['birthday'], d)
        parts = [f'……{date_line(d)}。']

        # 只问"星期几"就只答星期，不用报全
        if re.search(r'(星期几|周几|礼拜几|what\s*day)', t, re.I) and not name:
            return f'……{weekday_cn(d)}。'

        if name:
            parts.append(line)
        else:
            # 提一句临近的农历信息（有农历库时）
            lh = lunar_holiday(d)
            if lh:
                parts.append(f'……今天是{lh}。')
        return ''.join(parts)

    # ---------- 天气 ----------
    def _facts_weather(self):
        info = None
        try:
            info = getattr(self, '_weather', None)
            if info is not None:
                info = info.info
        except Exception:
            info = None

        city = (self.cfg['weather_city'] or '').strip()
        if not city:
            return ('……我不知道外面是什么天气。\n'
                    '（右键我 →「⚙ 设置」里填上城市名，我就能看到了。）')
        if info is None or not info.ok:
            return f'……我还没查到「{city}」的天气。等一下再问我，好吗。'

        parts = [f'……{info.city}，{info.desc}']
        if info.temp is not None:
            parts.append(f'，{round(info.temp)}°C')
        line = ''.join(parts) + '。'

        # 附上范围与降水概率，问"天气"的人通常想知道这些
        extra = []
        try:
            if info.tmax is not None and info.tmin is not None:
                extra.append(f'{round(info.tmin)}~{round(info.tmax)}°C')
        except Exception:
            pass
        try:
            if info.rain_prob is not None and info.rain_prob >= 20:
                extra.append(f'降水概率 {round(info.rain_prob)}%')
        except Exception:
            pass
        if extra:
            line += f'（{"，".join(extra)}）'

        # 特殊天气补一句关心
        if info.special:
            r = info.remark()
            if r:
                line += '\n' + r
        return line
