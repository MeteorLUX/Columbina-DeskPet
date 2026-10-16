# -*- coding: utf-8 -*-
"""
屏幕感知（AwarenessMixin）
==========================
让桌宠知道"你现在在看什么 / 用什么软件"。

组成：
  1. WindowWatcher  后台线程，定时读 Windows 前台窗口（ctypes 零依赖）
  2. 网站识别        从浏览器窗口标题解析出"在看什么"
  3. ActivityLog     活动记录，落盘 JSON，**每周自动重置**

隐私与安全边界（重要）：
  · 只读「进程名 + 窗口标题」，不截图、不读文件内容、不记录键盘
  · 记录只写本地 activity_log.json，每周重置
  · 打开应用/网址必须经用户确认，见 launcher.py

技术说明：
  GetForegroundWindow/GetWindowTextW/QueryFullProcessImageNameW 都是
  user32/kernel32 的标准接口，权限要求低，自身进程也能读到。
"""
import ctypes
import ctypes.wintypes as wt
import json
import os
import queue
import threading
import time

from config import *          # noqa: F401,F403  常量、文案、Config

# ==================== Windows API ====================
_user32 = ctypes.windll.user32
_kernel32 = ctypes.windll.kernel32
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000

# 浏览器进程名 -> 显示名（用于标题解析）
_BROWSERS = {
    'chrome.exe': 'Chrome',
    'msedge.exe': 'Edge',
    'firefox.exe': 'Firefox',
    'brave.exe': 'Brave',
    'opera.exe': 'Opera',
    'vivaldi.exe': 'Vivaldi',
    '360se.exe': '360浏览器',
    '360chrome.exe': '360浏览器',
    'qqbrowser.exe': 'QQ浏览器',
    'sogouexplorer.exe': '搜狗浏览器',
    'iexplore.exe': 'IE',
}

# 常见网站域名 -> 友好名（用于从标题里"认出"她在看什么站）
_SITE_HINTS = [
    ('bilibili', 'B站'),
    ('哔哩哔哩', 'B站'),
    ('github', 'GitHub'),
    ('zhihu', '知乎'),
    ('weibo', '微博'),
    ('taobao', '淘宝'),
    ('jd.com', '京东'),
    ('youtube', 'YouTube'),
    ('stackoverflow', 'Stack Overflow'),
    ('csdn', 'CSDN'),
    ('cnblogs', '博客园'),
    ('juejin', '掘金'),
    ('douyin', '抖音'),
    ('xiaohongshu', '小红书'),
    ('qq.com', 'QQ'),
    ('weixin', '微信'),
    ('docin', '豆丁'),
    ('baidu', '百度'),
    ('google', 'Google'),
    ('openai', 'OpenAI'),
    ('deepseek', 'DeepSeek'),
]

# 自己/系统窗口：不作为"用户活动"，避免自我干扰
_IGNORE_PROC = {
    'oc.exe', 'python.exe', 'pythonw.exe', 'explorer.exe',
    'searchhost.exe', 'shellexperiencehost.exe', 'startmenuexperiencehost.exe',
    'textinputhost.exe', 'applicationframehost.exe',
}


# ==================== 前台窗口读取 ====================
class ForegroundInfo:
    """一次前台窗口快照"""
    __slots__ = ('proc', 'proc_path', 'title', 'app', 'site', 'hwnd')

    def __init__(self, proc, proc_path, title, app, site, hwnd):
        self.proc = proc              # 'chrome.exe'
        self.proc_path = proc_path    # 完整路径
        self.title = title            # 窗口标题
        self.app = app                # 友好名：'Chrome' / 'VS Code'
        self.site = site              # 站点名（能认出来才有）
        self.hwnd = hwnd

    @property
    def key(self):
        """用于判断"是否换了活动"的键：换了页面也算换"""
        return f'{self.proc}|{self.title}'

    def describe(self):
        """给气泡/日志用的一句话描述"""
        if self.site:
            return f'{self.site}（{self.app}）'
        return self.app

    def __repr__(self):
        return f'<ForegroundInfo {self.app} {self.title[:30]!r}>'


def _proc_name_of(hwnd):
    """取窗口所属进程名与完整路径"""
    pid = wt.DWORD()
    _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    if not pid.value:
        return '', ''
    h = _kernel32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value)
    if not h:
        return '', ''
    try:
        size = wt.DWORD(2048)
        buf = ctypes.create_unicode_buffer(2048)
        if _kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
            path = buf.value
            return os.path.basename(path), path
    finally:
        _kernel32.CloseHandle(h)
    return '', ''


def _title_of(hwnd):
    n = _user32.GetWindowTextLengthW(hwnd)
    if n <= 0:
        return ''
    buf = ctypes.create_unicode_buffer(n + 1)
    _user32.GetWindowTextW(hwnd, buf, n + 1)
    return buf.value


def _friendly_app(proc):
    """进程名 -> 友好显示名（命中 APP_FRIENDLY_NAMES 就用配置里的）"""
    low = proc.lower()
    if low in _BROWSERS:
        return _BROWSERS[low]
    stem = os.path.splitext(low)[0]
    if stem in APP_FRIENDLY_NAMES:
        return APP_FRIENDLY_NAMES[stem]
    if low in APP_FRIENDLY_NAMES:
        return APP_FRIENDLY_NAMES[low]
    # 兜底：把 exe 名首字母大写，去掉常见后缀词
    nice = stem.replace('-', ' ').replace('_', ' ').strip()
    return nice.title() if nice else proc


def _detect_site(title):
    """从窗口标题里认出在看哪个网站（认不出返回 None）"""
    t = (title or '').lower()
    for kw, name in _SITE_HINTS:
        if kw in t:
            return name
    return None


def read_foreground():
    """读一次前台窗口。读不到/是被忽略的程序就返回 None。"""
    try:
        hwnd = _user32.GetForegroundWindow()
        if not hwnd:
            return None
        proc, path = _proc_name_of(hwnd)
        if not proc:
            return None
        if proc.lower() in _IGNORE_PROC:
            return None
        title = _title_of(hwnd)
        if not title.strip():
            return None
        return ForegroundInfo(proc, path, title,
                              _friendly_app(proc), _detect_site(title), hwnd)
    except Exception:
        return None


# ==================== 活动记录（每周重置）====================
class ActivityLog:
    """活动记录：**同时按周和按天**分桶。

    文件结构：
        {
          "version": 2,
          "week": "2026-W42",          # ISO 周
          "week_start": "2026-10-12",
          "apps": {"chrome.exe": {"name": "Chrome", "seconds": 123, "sessions": 4,
                                  "sites": {"bilibili": 60}}},
          "last_week": {...} | null,   # 上一周的统计，供"上周回顾"
          "days": {                    # ⬅ 按天明细（日记要用）
            "2026-10-16": {
              "apps": {"chrome.exe": {"name": "Chrome", "seconds": 300,
                                      "sessions": 2, "sites": {...}}},
              "study_sec": 0,
              "chats": 3,              # 当天对话轮数
              "pets": 1,               # 被摸头次数
              "launches": 0            # 帮她打开东西的次数
            }
          }
        }

    【为什么要按天】（踩过的坑）
      原来只有 `apps` 的**周累计**（如 10-12~10-18 共用一份秒数），
      日记里写"你今天用了 X 小时"，实际拿的是**整周累计**，
      所以日记全是流水账式的时长数字；更糟的是**某天的数据在结构上
      压根不存在**，所以如果那天没写成日记，事后永远补不回来
      （用户就遇到过 10-14 那一天凭空消失）。
    """

    # 按天明细保留多少天（文件不能无限膨胀；60 天足够写日记与回顾）
    DAYS_KEEP = 60

    def __init__(self, path):
        self.path = path
        self.data = self._empty()
        self._dirty = False
        self._last_flush = 0.0
        self._load()
        # 退出时兜底落盘：落盘间隔是 60 秒，如果程序跑不到 60 秒就关掉，
        # 当天的记录会全留在内存里丢掉（用户遇到过"某天日记凭空消失"，
        # 这很可能是原因之一）。atexit 在解释器正常退出时一定会跑。
        try:
            import atexit
            atexit.register(self.flush, True)
        except Exception:
            pass

    # ---------- 周/日标识 ----------
    @staticmethod
    def current_week(ts=None):
        """ISO 周标识，如 '2026-W42'。
        注意：isocalendar 是 datetime 的方法（time 模块没有）。"""
        import datetime
        t = ts if ts is not None else time.time()
        iso = datetime.datetime.fromtimestamp(t).isocalendar()
        return f'{iso[0]}-W{iso[1]:02d}'

    @staticmethod
    def week_start_str(ts=None):
        """本周周一的日期字符串"""
        import datetime
        t = ts if ts is not None else time.time()
        d = datetime.datetime.fromtimestamp(t).date()
        monday = d - datetime.timedelta(days=d.weekday())    # Monday=0
        return monday.isoformat()

    @staticmethod
    def today_str(ts=None):
        """当天日期字符串 'YYYY-MM-DD'（与日记的 date 口径一致）"""
        import datetime
        t = ts if ts is not None else time.time()
        return datetime.datetime.fromtimestamp(t).date().isoformat()

    def _empty(self):
        return {
            'version': 2,
            'week': self.current_week(),
            'week_start': self.week_start_str(),
            'apps': {},
            'last_week': None,
            'days': {},
        }

    # ---------- 读写 ----------
    def _load(self):
        if not os.path.exists(self.path):
            return
        try:
            with open(self.path, 'r', encoding='utf-8') as f:
                old = json.load(f)
            if not isinstance(old, dict):
                return
            days = self._prune_days(old.get('days') or {})
            if old.get('week') != self.current_week():
                # 跨周了：把上一周统计留成 last_week，然后全新开始。
                # **但按天明细必须带过去** —— 否则跨周就把上周末尾的
                # 日记素材全丢了（这正是"某天日记写不出来"的成因之一）。
                self.data = self._empty()
                self.data['last_week'] = {
                    'week': old.get('week'),
                    'apps': old.get('apps') or {},
                }
                self.data['days'] = days
                self._dirty = True
                self.flush(force=True)
            else:
                self.data.update(old)
                self.data.setdefault('apps', {})
                self.data['days'] = days
                # ---- 版本升级要**立即落盘** ----
                # 踩过：原来只在"跨周"分支里 flush，所以同周内加载老文件
                # （version=1、没有 days）时，升级后的结构只留在内存里，
                # 文件还是老样子。表现就是 activity_log.json 死活不变 v2，
                # 按天明细也就一直没被建立。
                if int(old.get('version') or 1) < 2:
                    self.data['version'] = 2
                    self._dirty = True
                    self.flush(force=True)
        except Exception:
            self.data = self._empty()

    def _prune_days(self, days):
        """只保留最近 DAYS_KEEP 天，避免文件无限增长"""
        if not isinstance(days, dict) or not days:
            return {}
        keys = sorted(days)
        if len(keys) <= self.DAYS_KEEP:
            return days
        return {k: days[k] for k in keys[-self.DAYS_KEEP:]}

    def _day(self, date=None, create=False):
        """取（或建）某天的桶。date 默认今天。"""
        d = date or self.today_str()
        days = self.data.setdefault('days', {})
        if d not in days:
            if not create:
                return None
            days[d] = {'apps': {}, 'study_sec': 0.0,
                       'chats': 0, 'pets': 0, 'launches': 0}
        day = days[d]
        # 容错：老数据可能缺字段
        day.setdefault('apps', {})
        day.setdefault('study_sec', 0.0)
        for k in ('chats', 'pets', 'launches'):
            day.setdefault(k, 0)
        return day

    def flush(self, force=False):
        if not (self._dirty or force):
            return
        try:
            # 落盘前裁剪，避免长期运行后文件过大
            self.data['days'] = self._prune_days(self.data.get('days') or {})
            tmp = self.path + '.tmp'
            with open(tmp, 'w', encoding='utf-8') as f:
                json.dump(self.data, f, ensure_ascii=False, indent=2)
            os.replace(tmp, self.path)
            self._dirty = False
            self._last_flush = time.time()
        except Exception:
            pass

    # ---------- 记账 ----------
    def add(self, proc, app_name, seconds, site=None):
        """累加一次使用时长（**同时记入周累计与当天明细**）"""
        if not proc or seconds <= 0:
            return
        # 跨周检查（长时间开着程序时也能及时翻页）
        if self.data.get('week') != self.current_week():
            self._load()
        secs = float(seconds)
        self._bump(self.data['apps'], proc, app_name, secs, site)
        # ---- 当天明细 ----
        day = self._day(create=True)
        if day is not None:
            self._bump(day['apps'], proc, app_name, secs, site)
        self._dirty = True

    @staticmethod
    def _bump(bucket, proc, app_name, seconds, site=None):
        """往一个 apps 桶里累加（周桶和日桶共用这段逻辑）"""
        rec = bucket.get(proc)
        if rec is None:
            rec = {'name': app_name, 'seconds': 0.0, 'sessions': 0, 'sites': {}}
            bucket[proc] = rec
        rec['name'] = app_name
        rec['seconds'] = round(float(rec.get('seconds', 0)) + seconds, 1)
        if site:
            sites = rec.setdefault('sites', {})
            sites[site] = round(float(sites.get(site, 0)) + seconds, 1)

    def mark_session(self, proc):
        """记一次"打开/切到"该程序（周累计 + 当天明细）"""
        rec = self.data['apps'].get(proc)
        if rec is not None:
            rec['sessions'] = int(rec.get('sessions', 0)) + 1
            self._dirty = True
        day = self._day(create=False)
        if day is not None:
            drec = day['apps'].get(proc)
            if drec is not None:
                drec['sessions'] = int(drec.get('sessions', 0)) + 1
                self._dirty = True

    def bump_day(self, key, n=1, date=None):
        """给当天的计数加一（chats/pets/launches）。

        日记需要这些"互动量"来判断当天过得热闹还是冷清 ——
        只看软件时长是写不出"你今天几乎没跟我说话"这种感觉的。
        """
        day = self._day(date=date, create=True)
        if day is None:
            return
        day[key] = int(day.get(key, 0)) + int(n)
        self._dirty = True

    def add_day_study(self, seconds, date=None):
        """累加当天学习秒数（日记写"一起学习了多久"要用）"""
        if seconds <= 0:
            return
        day = self._day(date=date, create=True)
        if day is None:
            return
        day['study_sec'] = round(
            float(day.get('study_sec', 0)) + float(seconds), 1)
        self._dirty = True

    def maybe_flush(self, now=None):
        now = now if now is not None else time.time()
        if self._dirty and now - self._last_flush >= ACTIVITY_FLUSH_SEC:
            self.flush()

    # ---------- 查询 ----------
    def fmt_duration(self, seconds):
        seconds = int(seconds)
        h, rem = divmod(seconds, 3600)
        m, s = divmod(rem, 60)
        if h:
            return f'{h} 小时 {m} 分'
        if m:
            return f'{m} 分 {s} 秒'
        return f'{s} 秒'

    def top_apps(self, n=6, source=None):
        """时长最长的几个应用，返回 [(name, seconds, sessions), ...]"""
        apps = (source or self.data).get('apps') or {}
        rows = [(v.get('name', k), float(v.get('seconds', 0)),
                 int(v.get('sessions', 0))) for k, v in apps.items()]
        rows.sort(key=lambda r: -r[1])
        return rows[:n]

    def summary_line(self):
        """一句话概览，用于面板标题"""
        total = sum(float(v.get('seconds', 0))
                    for v in (self.data.get('apps') or {}).values())
        return f'本周共记录 {self.fmt_duration(total)}'

    # ---------- 按天查询（日记专用）----------
    def day_data(self, date=None):
        """取某天的明细；没有就返回 None（**不创建**）。

        返回 {'apps':..., 'study_sec':..., 'chats':..., 'pets':...,
              'launches':...}
        """
        days = self.data.get('days') or {}
        d = date or self.today_str()
        return days.get(d)

    def day_top_apps(self, date=None, n=3, min_sec=0):
        """某天时长最长的应用 [(name, seconds, sessions), ...]

        min_sec：低于它的不返回（日记里不必提"开了 2 分钟"的窗口）
        """
        day = self.day_data(date)
        if not day:
            return []
        rows = [(v.get('name', k), float(v.get('seconds', 0)),
                 int(v.get('sessions', 0)))
                for k, v in (day.get('apps') or {}).items()]
        rows = [r for r in rows if r[1] >= min_sec]
        rows.sort(key=lambda r: -r[1])
        return rows[:n]

    def day_top_sites(self, date=None, n=3, min_sec=60):
        """某天在浏览器里待得最久的站点 [(site, seconds), ...]"""
        day = self.day_data(date)
        if not day:
            return []
        agg = {}
        for v in (day.get('apps') or {}).values():
            for s, sec in (v.get('sites') or {}).items():
                agg[s] = agg.get(s, 0) + float(sec)
        rows = sorted(agg.items(), key=lambda x: -x[1])
        return [(s, v) for s, v in rows if v >= min_sec][:n]

    def day_busy_sec(self, date=None):
        """某天记录到的总时长（判断"有没有用电脑"用）"""
        day = self.day_data(date)
        if not day:
            return 0.0
        return sum(float(v.get('seconds', 0))
                   for v in (day.get('apps') or {}).values())

    def has_day(self, date=None):
        """某天有没有任何记录"""
        day = self.day_data(date)
        if not day:
            return False
        return bool(day.get('apps')) or float(day.get('study_sec') or 0) > 0


# ==================== 前台窗口监听线程 ====================
class WindowWatcher(threading.Thread):
    """后台轮询前台窗口，把"活动变化"推进队列。

    线程安全：只往 queue 里放，不碰 Tk。
    """

    def __init__(self, out_queue, interval=None, enabled=True):
        super().__init__(name='window-watcher', daemon=True)
        self.q = out_queue
        self.interval = interval or AWARENESS_POLL_SEC
        self.enabled = enabled
        self._stop = threading.Event()
        self._last_key = None
        self._last_info = None
        self._last_ts = time.time()

    def stop(self):
        self._stop.set()

    def run(self):
        while not self._stop.is_set():
            try:
                if self.enabled:
                    info = read_foreground()
                    if info is not None:
                        now = time.time()
                        # 先把"上一段"时长入账
                        if self._last_info is not None:
                            dur = now - self._last_ts
                            self.q.put(('tick', (self._last_info, dur)))
                        if info.key != self._last_key:
                            self._last_key = info.key
                            self.q.put(('changed', info))
                        self._last_info = info
                        self._last_ts = now
            except Exception:
                pass
            self._stop.wait(self.interval)


# ==================== Mixin ====================
class AwarenessMixin:
    """屏幕感知：让桌宠知道你正在用什么"""

    def setup_awareness(self):
        """状态初始化（由 DesktopPet.__init__ 调用）"""
        self._aware_on = bool(self.cfg['awareness_enabled'])
        self._aware_q = queue.Queue()      # 监听线程 -> 主线程
        self._aware_watcher = None
        self._aware_cur = None            # 当前 ForegroundInfo
        self._aware_cur_since = 0.0       # 当前活动开始时间
        self._aware_last_app = None       # 上一次"应用"（不是页面）
        self._aware_next_banter = 0.0     # 下次允许打趣的时间
        self._aware_seen = {}             # {页面键: 首次见到时间}，用于"停留多久"与去重
        self._aware_win = None            # 活动记录面板
        self.activity = ActivityLog(ACTIVITY_LOG_PATH)

    # ---------- 启停 ----------
    def _aware_start(self):
        if not self._aware_on:      # 守卫：关掉时别被 _aware_tick 又拉起来
            return
        if self._aware_watcher is not None:
            return
        self._aware_watcher = WindowWatcher(self._aware_q)
        self._aware_watcher.start()

    def _aware_stop(self):
        w = self._aware_watcher
        if w is not None:
            w.stop()
            self._aware_watcher = None
        self._aware_set(False)      # 关掉时同步配置与勾选框

    def _toggle_awareness(self):
        """菜单勾选框回调：以勾选框为准"""
        want = True
        var = getattr(self, 'aware_var', None)
        if var is not None:
            try:
                want = bool(var.get())
            except Exception:
                want = True
        self._aware_set(want)
        if want:
            self._aware_start()
            self._speak("……嗯，我开始留意你在做什么了。")
        else:
            self._speak("……好，我不看了。")

    def _aware_set(self, on):
        """设置开关并保持一致（勾选框 / 配置 / 实际状态）"""
        self._aware_on = bool(on)
        self.cfg['awareness_enabled'] = self._aware_on
        self.cfg.save()
        var = getattr(self, 'aware_var', None)
        if var is not None:
            try:
                var.set(self._aware_on)
            except Exception:
                pass

    # ---------- 主时钟 ----------
    def _aware_tick(self, now):
        """在 _tick 里调用：消费监听线程的事件"""
        if not self._aware_on:
            return
        if self._aware_watcher is None:
            self._aware_start()

        drained = 0
        while drained < 50:            # 每帧最多处理 50 条，别卡界面
            try:
                kind, payload = self._aware_q.get_nowait()
            except Exception:
                break
            drained += 1
            if kind == 'changed':
                self._aware_on_changed(payload, now)
            elif kind == 'tick':
                info, dur = payload
                self.activity.add(info.proc, info.app, dur, info.site)

        self.activity.maybe_flush(now)

    def _aware_on_changed(self, info, now):
        """活动变化：记账 + 决定要不要打趣"""
        # 先把上一段的时间落账
        if self._aware_cur is not None:
            dur = now - self._aware_cur_since
            self.activity.add(self._aware_cur.proc, self._aware_cur.app,
                              dur, self._aware_cur.site)
        self._aware_cur = info
        self._aware_cur_since = now

        app_changed = (info.proc != self._aware_last_app)
        if app_changed:
            self._aware_last_app = info.proc
            self.activity.mark_session(info.proc)

        # 被她自己打断时不要插嘴
        if self.dlg_state != 'idle' or self.is_generating:
            return
        if getattr(self, '_sing_state', 'off') != 'off':
            return
        if getattr(self, '_study_state', 'idle') == 'studying':
            return
        if now < self._aware_next_banter:
            return

        # 挑台词（banter 里自带"停留够久 / 概率 / 去重"三重克制）
        from banter import pick_banter
        line = pick_banter(info, now, self._aware_seen)
        if not line:
            return

        self._aware_next_banter = now + AWARENESS_BANTER_COOLDOWN
        self._speak(line)

    # ---------- 活动记录面板 ----------
    def aware_show_panel(self):
        """右键菜单「👀 她在看什么」：显示当前活动 + 本周统计 + 上周回顾"""
        import tkinter as tk

        if self._aware_win is not None:
            try:
                self._aware_win.lift()
                self._aware_win.focus_force()
                return
            except Exception:
                self._aware_win = None

        win = tk.Toplevel(self.root)
        win.title('她在看什么')
        win.attributes('-topmost', True)
        win.resizable(False, False)
        x = self.root.winfo_x() + WIN_W + 10
        y = self.root.winfo_y()
        sw = self.root.winfo_screenwidth()
        if x + 400 > sw:
            x = max(0, self.root.winfo_x() - 410)
        win.geometry(f'400x380+{x}+{y}')

        outer = tk.Frame(win, bg=BUBBLE_BG)
        outer.pack(fill='both', expand=True, padx=8, pady=8)

        # 顶部：当前活动
        cur = self._aware_cur
        head = tk.Frame(outer, bg=BUBBLE_BG)
        head.pack(fill='x')
        tk.Label(head, text='正在看：', bg=BUBBLE_BG, fg='#6A4BA8',
                 font=('Microsoft YaHei', 10, 'bold')).pack(side='left')
        tk.Label(head, text=(cur.describe() if cur else '（还没探测到）'),
                 bg=BUBBLE_BG, fg=BUBBLE_FG,
                 font=('Microsoft YaHei', 10)).pack(side='left')

        if cur and cur.title:
            tk.Label(outer, text=cur.title[:70], bg=BUBBLE_BG, fg='#8A7BA8',
                     font=('Microsoft YaHei', 8), wraplength=370,
                     justify='left', anchor='w').pack(fill='x', pady=(2, 0))

        tk.Label(outer, text=self.activity.summary_line(), bg=BUBBLE_BG,
                 fg=BUBBLE_FG, font=('Microsoft YaHei', 9),
                 anchor='w').pack(fill='x', pady=(6, 2))

        # 列表区（可滚动）
        mid = tk.Frame(outer, bg=BUBBLE_BG)
        mid.pack(fill='both', expand=True)
        sb = tk.Scrollbar(mid)
        sb.pack(side='right', fill='y')
        txt = tk.Text(mid, bg='#FFFDF9', fg=BUBBLE_FG, relief='flat',
                      font=('Microsoft YaHei', 9), wrap='word',
                      yscrollcommand=sb.set, padx=6, pady=4)
        txt.pack(side='left', fill='both', expand=True)
        sb.config(command=txt.yview)
        txt.tag_configure('h', foreground='#6A4BA8',
                          font=('Microsoft YaHei', 9, 'bold'))

        def put(s, tag=None):
            txt.insert('end', s + '\n', tag or '')

        put('本周用得最多', 'h')
        top = self.activity.top_apps(8)
        if top:
            for name, secs, sess in top:
                put(f'  {name} — {self.activity.fmt_duration(secs)}（{sess} 次）')
        else:
            put('  （还没有记录）')

        # 各应用的站点分布
        apps = self.activity.data.get('apps') or {}
        site_rows = []
        for proc, rec in apps.items():
            for site, secs in (rec.get('sites') or {}).items():
                site_rows.append((site, secs, rec.get('name', proc)))
        site_rows.sort(key=lambda r: -r[1])
        if site_rows:
            put('')
            put('看过的网站', 'h')
            for site, secs, app in site_rows[:8]:
                put(f'  {site} — {self.activity.fmt_duration(secs)}（{app}）')

        lw = self.activity.data.get('last_week')
        if lw and lw.get('apps'):
            put('')
            put(f"上周（{lw.get('week')}）", 'h')
            for name, secs, sess in self.activity.top_apps(5, source=lw):
                put(f'  {name} — {self.activity.fmt_duration(secs)}')

        put('')
        put(f'记录文件：{os.path.basename(ACTIVITY_LOG_PATH)}（每周自动重置）')
        txt.config(state='disabled')

        # 底部按钮
        btns = tk.Frame(outer, bg=BUBBLE_BG)
        btns.pack(fill='x', pady=(6, 0))

        def close():
            try:
                win.destroy()
            except Exception:
                pass
            self._aware_win = None

        def clear():
            self.activity.data['apps'] = {}
            self.activity.data['last_week'] = None
            self.activity.flush(force=True)
            close()
            self.aware_show_panel()

        for text, cmd in (('刷新', lambda: (close(), self.aware_show_panel())),
                          ('清空本周', clear),
                          ('关闭', close)):
            tk.Button(btns, text=text, font=('Microsoft YaHei', 9),
                      bg='#6A4BA8' if text == '关闭' else '#E8E0F0',
                      fg='white' if text == '关闭' else '#6A4BA8',
                      relief='flat', cursor='hand2', padx=12, pady=8,
                      command=cmd).pack(side='right', padx=4)

        win.protocol('WM_DELETE_WINDOW', close)
        self._aware_win = win

