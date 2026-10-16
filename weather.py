# -*- coding: utf-8 -*-
"""
天气查询（WeatherMixin）
========================
按你填的城市名查天气，全部走免密钥的 open-meteo：
    城市名 --geocoding--> 坐标 --forecast--> 天气码 + 温度
返回 WMO 天气码，再翻成中文描述与"特殊天气"标签。

隐私说明：
  · 只发送**城市名**（geocoding）与**坐标**（forecast），不含任何个人信息
  · 结果缓存到 weather_cache.json，同一天不重复请求
  · 想彻底离线就把 config 的 WEATHER_CITY 留空，她会只报日期与节日

网络失败是常态（尤其国内），所有请求都有超时并静默降级，
绝不因为查不到天气就影响桌宠其它功能。
"""
import json
import os
import threading
import time
import urllib.parse
import urllib.request

from config import *          # noqa: F401,F403  常量、文案、Config

_UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) columbina-pet/1.0'
_GEOCODE_URL = ('https://geocoding-api.open-meteo.com/v1/search'
                '?name={q}&count=1&language=zh&format=json')
_FORECAST_URL = ('https://api.open-meteo.com/v1/forecast'
                 '?latitude={lat}&longitude={lon}'
                 '&current=temperature_2m,weather_code,is_day'
                 '&daily=temperature_2m_max,temperature_2m_min,'
                 'precipitation_probability_max,weather_code'
                 '&timezone=auto&forecast_days=2')

# WMO 天气码 -> (中文描述, 是否"特殊天气")
WMO_CODES = {
    0:  ('晴', False),
    1:  ('大致晴朗', False),
    2:  ('局部多云', False),
    3:  ('阴', False),
    45: ('有雾', True),
    48: ('雾凇', True),
    51: ('毛毛雨', True),
    53: ('小雨', True),
    55: ('中雨', True),
    56: ('冻毛毛雨', True),
    57: ('冻雨', True),
    61: ('小雨', True),
    63: ('中雨', True),
    65: ('大雨', True),
    66: ('冻雨', True),
    67: ('强冻雨', True),
    71: ('小雪', True),
    73: ('中雪', True),
    75: ('大雪', True),
    77: ('米雪', True),
    80: ('阵雨', True),
    81: ('强阵雨', True),
    82: ('暴雨', True),
    85: ('阵雪', True),
    86: ('强阵雪', True),
    95: ('雷雨', True),
    96: ('雷雨伴冰雹', True),
    99: ('强雷雨伴冰雹', True),
}

# 特殊天气 -> 她想说的话（她会主动提一句）
WEATHER_REMARKS = {
    '雨': ["……外面在下雨。雨声会让我安心。",
           "……下雨了。记得带伞，或者，早点回来。",
           "……听，雨。像有人在很远的地方说话。"],
    '雪': ["……下雪了。美丽又安静。",
           "……雪。我很久没见过了……不，我每天都见。",
           "……下雪的时候，世界会变轻。"],
    '雷': ["……打雷了。你要是不喜欢，可以离我近一点。",
           "…… thunder。……我用了你的词。打雷了。"],
    '雾': ["……起雾了。什么都看不清的时候，反而安静。"],
    '风': ["……风很大。窗户那边，别站太久。"],
    '热': ["……今天很热。别在外面待太久。",
           "……这么热。要不要进来歇一会儿。"],
    '冷': ["……今天很冷。多穿一点。",
           "……降温了。手会凉的。"],
}


def describe_code(code):
    """WMO 码 -> (中文描述, 是否特殊天气)"""
    return WMO_CODES.get(int(code), ('未知', False))


def _http_json(url, timeout=8.0):
    req = urllib.request.Request(url, headers={'User-Agent': _UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode('utf-8', 'ignore'))


class WeatherInfo:
    """一次天气快照"""
    __slots__ = ('city', 'temp', 'code', 'desc', 'special',
                 'tmax', 'tmin', 'rain_prob', 'ok', 'ts', 'err')

    def __init__(self, **kw):
        for k in self.__slots__:
            setattr(self, k, kw.get(k))
        if self.ok is None:
            self.ok = False

    def to_dict(self):
        return {k: getattr(self, k) for k in self.__slots__}

    @classmethod
    def from_dict(cls, d):
        return cls(**(d or {}))

    def label(self):
        """一句话天气，如 '上海 阴 21°C'"""
        if not self.ok:
            return ''
        parts = [self.city or '', self.desc or '']
        if self.temp is not None:
            parts.append(f'{round(self.temp)}°C')
        return ' '.join(p for p in parts if p)

    def remark(self):
        """按特殊天气挑一句她想说的话；没特别的返回 None"""
        if not self.ok:
            return None
        import random
        desc = self.desc or ''
        for kw in ('雷', '雪', '雨', '雾', '风'):
            if kw in desc:
                return random.choice(WEATHER_REMARKS[kw])
        # 温度极端也算"特殊"
        try:
            t = float(self.temp)
            if t >= 33:
                return random.choice(WEATHER_REMARKS['热'])
            if t <= 2:
                return random.choice(WEATHER_REMARKS['冷'])
        except Exception:
            pass
        return None


class WeatherCache:
    """天气缓存 + 后台刷新（不阻塞界面）"""

    def __init__(self, path, city):
        self.path = path
        self.city = (city or '').strip()
        self.info = None
        self._lock = threading.Lock()
        self._busy = False
        self._load()

    # ---------- 磁盘 ----------
    def _today(self):
        import datetime
        return datetime.date.today().isoformat()

    def _load(self):
        try:
            if os.path.exists(self.path):
                with open(self.path, 'r', encoding='utf-8') as f:
                    d = json.load(f) or {}
                if d.get('date') == self._today() and d.get('city') == self.city:
                    self.info = WeatherInfo.from_dict(d.get('info'))
        except Exception:
            self.info = None

    def _save(self):
        try:
            with open(self.path, 'w', encoding='utf-8') as f:
                json.dump({'date': self._today(), 'city': self.city,
                           'info': self.info.to_dict() if self.info else None},
                          f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    # ---------- 查询 ----------
    def needs_refresh(self):
        if not self.city:
            return False
        return self.info is None or not self.info.ok

    def refresh_async(self):
        """后台线程刷新，绝不阻塞 Tk 主线程"""
        if not self.city or self._busy:
            return
        with self._lock:
            if self._busy:
                return
            self._busy = True

        def work():
            try:
                info = self.fetch(self.city)
                if info is not None:
                    self.info = info
                    self._save()
            finally:
                self._busy = False

        threading.Thread(target=work, name='weather', daemon=True).start()

    # ---------- 实际请求 ----------
    @staticmethod
    def fetch(city):
        try:
            geo = _http_json(_GEOCODE_URL.format(
                q=urllib.parse.quote(city)))
            res = (geo or {}).get('results') or []
            if not res:
                return WeatherInfo(ok=False, city=city, err='找不到这个城市')
            g = res[0]
            lat, lon = g.get('latitude'), g.get('longitude')
            name = g.get('name') or city

            fc = _http_json(_FORECAST_URL.format(lat=lat, lon=lon))
            cur = (fc or {}).get('current') or {}
            daily = (fc or {}).get('daily') or {}
            code = cur.get('weather_code')
            desc, special = describe_code(code if code is not None else -1)

            tmax = tmin = rp = None
            try:
                tmax = daily['temperature_2m_max'][0]
                tmin = daily['temperature_2m_min'][0]
                rp = daily.get('precipitation_probability_max', [None])[0]
            except Exception:
                pass

            return WeatherInfo(ok=True, city=name, temp=cur.get('temperature_2m'),
                               code=code, desc=desc, special=special,
                               tmax=tmax, tmin=tmin, rain_prob=rp,
                               ts=time.time(), err=None)
        except Exception as e:
            return WeatherInfo(ok=False, city=city, err=f'{type(e).__name__}')


class WeatherMixin:
    """天气：按城市名查询 + 特殊天气提醒"""

    def setup_weather(self):
        self._weather = WeatherCache(WEATHER_CACHE_PATH,
                                     self.cfg['weather_city'] or '')
        self._weather_said = ''      # 今天已经提醒过的天气标签（避免重复唠叨）

    def weather_tick(self, now):
        """在 _tick 里调用：需要就后台刷新；特殊天气提醒一天一次"""
        w = getattr(self, '_weather', None)
        if w is None:
            return
        # 换城市了（改了 config）就重建
        want_city = (self.cfg['weather_city'] or '').strip()
        if want_city != w.city:
            self._weather = WeatherCache(WEATHER_CACHE_PATH, want_city)
            self._weather_said = ''
            w = self._weather

        if w.needs_refresh() and not w._busy:
            w.refresh_async()
            return

        info = w.info
        if not info or not info.ok:
            return
        # 特殊天气：每天最多主动说一次，且别在忙的时候插嘴
        tag = f'{info.city}|{info.desc}|{self._today_str()}'
        if info.special and tag != self._weather_said:
            if (self.dlg_state == 'idle' and not self.is_generating
                    and getattr(self, '_sing_state', 'off') == 'off'):
                line = info.remark()
                if line:
                    self._weather_said = tag
                    self._speak(line)

    @staticmethod
    def _today_str():
        import datetime
        return datetime.date.today().isoformat()
