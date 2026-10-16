# -*- coding: utf-8 -*-
"""
节日与日期增强（HolidaysMixin）
================================
提供：
  · 今天是星期几 / 几月几日 / 一年中的第几天
  · 公历节日（元旦、劳动节、国庆、圣诞、情人节…）
  · 农历节日（春节、元宵、端午、中秋、七夕、重阳、腊八）—— 需 lunardate
  · 节日祝福语（用她的人设口吻）
  · 生日（你在 config 里填 WEATHER_CITY 旁边那个 birthday）

农历库是可选的：没装 lunardate 就自动降级为只认公历节日，
不会报错、不会影响其它功能。
"""
import datetime
import random

from config import *          # noqa: F401,F403  常量、文案、Config

try:
    from lunardate import LunarDate
    HAS_LUNAR = True
except ImportError:
    HAS_LUNAR = False

_WEEKDAY_CN = ('星期一', '星期二', '星期三', '星期四', '星期五', '星期六', '星期日')

# 公历节日：(月, 日) -> 名称
SOLAR_HOLIDAYS = {
    (1, 1):   '元旦',
    (2, 14):  '情人节',
    (3, 8):   '妇女节',
    (3, 12):  '植树节',
    (4, 1):   '愚人节',
    (5, 1):   '劳动节',
    (5, 4):   '青年节',
    (6, 1):   '儿童节',
    (7, 1):   '建党节',
    (8, 1):   '建军节',
    (9, 10):  '教师节',
    (10, 1):  '国庆节',
    (12, 24): '平安夜',
    (12, 25): '圣诞节',
    (12, 31): '跨年夜',
}

# 农历节日：(月, 日) -> 名称
LUNAR_HOLIDAYS = {
    (1, 1):  '春节',
    (1, 15): '元宵节',
    (5, 5):  '端午节',
    (7, 7):  '七夕',
    (8, 15): '中秋节',
    (9, 9):  '重阳节',
    (12, 8): '腊八节',
}

# 节日祝福（她的人设口吻：平淡、简短、带省略号）
HOLIDAY_LINES = {
    '元旦':    ["……新的一年了。今年，我也会在。",
                "……元旦快乐。虽然月亮上不过这个节。"],
    '春节':    ["……春节。外面很吵吧。……新年好。",
                "……过年了。你要回家吗？……那我也算在家吧。"],
    '元宵节':  ["……元宵。月亮今天很圆，和我见过的一样圆。"],
    '情人节':  ["……情人节。这种节日，我从前是不懂的。"],
    '妇女节':  ["……今天是妇女节。嗯，我知道了。"],
    '植树节':  ["……植树节。种下去的东西，会慢慢长。"],
    '愚人节':  ["……愚人节。今天说的话，可以不算数吗。"],
    '劳动节':  ["……劳动节。那就……休息一下吧。"],
    '青年节':  ["……青年节。你还很年轻呢。"],
    '儿童节':  ["……儿童节。……你小时候是什么样子的？"],
    '端午节':  ["……端午。粽子是甜的还是咸的？……我没有立场。"],
    '建党节':  ["……七月一日。"],
    '七夕':    ["……七夕。传说这一天，他们会见面。",
                "……七夕。你想看星星吗？我可以陪你。"],
    '建军节':  ["……八月一日。"],
    '中秋节':  ["……中秋。月饼……我可以要一块吗。",
                "……中秋快乐。今天的月亮，是主角。"],
    '教师节':  ["……教师节。要谢谢教过你的人吗。"],
    '重阳节':  ["……重阳。要记得回去看看长辈。"],
    '国庆节':  ["……国庆。外面很热闹。我就在家里等你。"],
    '腊八节':  ["……腊八。天冷了，喝点热的。"],
    '平安夜':  ["……平安夜。嗯，平安就好。"],
    '圣诞节':  ["……圣诞。这个节日，是雪的味道。"],
    '跨年夜':  ["……一年要结束了。谢谢你陪我走过。",
                "……跨年夜。明年也……算了，先过完今天。"],
}

# 生日祝福（特殊，单独一套）
BIRTHDAY_LINES = [
    "……今天是你生日。我记得的。",
    "……生日快乐。……我没准备什么，只有月亮。",
    "……生日啊。那就……祝你今天比昨天好一点。",
]

# 普通日子的开场白（按星期几/时段微调）
WEEKDAY_LINES = {
    0: "……星期一。慢慢来就好。",
    1: "……星期二。",
    2: "……星期三。一周过了一半了。",
    3: "……星期四。",
    4: "……星期五。快到周末了。",
    5: "……星期六。今天可以晚一点睡。",
    6: "……星期日。明天又要开始了。",
}


def today_str(d=None):
    return (d or datetime.date.today()).isoformat()


def weekday_cn(d=None):
    return _WEEKDAY_CN[(d or datetime.date.today()).weekday()]


def solar_holiday(d=None):
    """公历节日名，没有返回 None"""
    d = d or datetime.date.today()
    return SOLAR_HOLIDAYS.get((d.month, d.day))


def lunar_holiday(d=None):
    """农历节日名，没有（或没装库）返回 None"""
    if not HAS_LUNAR:
        return None
    d = d or datetime.date.today()
    try:
        ld = LunarDate.fromSolarDate(d.year, d.month, d.day)
        return LUNAR_HOLIDAYS.get((ld.month, ld.day))
    except Exception:
        # lunardate 只支持 1900-2099，超出范围就放弃
        return None


def is_birthday(birthday, d=None):
    """birthday 形如 'MM-DD' 或 'YYYY-MM-DD'"""
    if not birthday:
        return False
    d = d or datetime.date.today()
    b = str(birthday).strip()
    try:
        if len(b) >= 10:                      # YYYY-MM-DD
            bd = datetime.date.fromisoformat(b[:10])
            return (bd.month, bd.day) == (d.month, d.day)
        if len(b) == 5 and b[2] == '-':        # MM-DD
            return (int(b[:2]), int(b[3:])) == (d.month, d.day)
    except Exception:
        pass
    return False


def holiday_of_day(birthday=None, d=None):
    """今天是什么节日（生日优先）。返回 (名称, 祝福语) ；没有返回 (None, None)"""
    d = d or datetime.date.today()
    if is_birthday(birthday, d):
        return '生日', random.choice(BIRTHDAY_LINES)
    for name in (solar_holiday(d), lunar_holiday(d)):
        if name and name in HOLIDAY_LINES:
            return name, random.choice(HOLIDAY_LINES[name])
        if name:
            return name, f'……今天是{name}。'
    return None, None


def date_line(d=None):
    """一行日期描述：'2026年10月15日 星期四（今年第 288 天）'"""
    d = d or datetime.date.today()
    doy = d.timetuple().tm_yday
    total = 366 if _is_leap(d.year) else 365
    return f'{d.year}年{d.month}月{d.day}日 {weekday_cn(d)}（今年第 {doy}/{total} 天）'


def _is_leap(y):
    return (y % 4 == 0 and y % 100 != 0) or (y % 400 == 0)


class HolidaysMixin:
    """节日与日期：启动祝福、每日一句、节日提醒"""

    def setup_holidays(self):
        self._holiday_said_date = ''      # 今天已经祝福过（避免反复说）

    def holidays_tick(self, now):
        """每天首次打开（或跨天）时，说一句节日/日期祝福"""
        today = today_str()
        if self._holiday_said_date == today:
            return
        if self.dlg_state != 'idle' or self.is_generating:
            return
        if getattr(self, '_sing_state', 'off') != 'off':
            return
        if getattr(self, '_study_state', 'idle') == 'studying':
            return

        name, line = holiday_of_day(self.cfg['birthday'])
        if name:
            self._holiday_said_date = today
            self._speak(line, interrupt=False)
        else:
            # 普通日子不主动唠叨，只在问候时体现（见 _greet 的增强）
            self._holiday_said_date = today
