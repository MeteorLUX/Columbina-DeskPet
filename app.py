# -*- coding: utf-8 -*-
"""
哥伦比娅 · 桌面宠物  —— 主类
================================
把各功能模块（Mixin）组合成 DesktopPet，并提供主时钟 _tick()。

模块划分（一个功能一个文件）：
    config.py            配置与文案常量 + 可选依赖探测（HAS_*）
    ui_widgets.py        窗口与控件、立绘、好感度
    walk.py              自主走动与帧动画
    interaction.py       拖拽、摸头、输入框
    chat.py              气泡与对话状态机
    dialogue_llm.py      输入处理、DeepSeek 调用、本地台词
    hourly.py            整点报时
    menu.py              右键菜单与设置项
    voice_control.py      全双工语音（聆听 / 打断 / 逐句打字）
    voice_engine.py 语音底座（常驻收音 + VAD + 流式识别 + 流式 LLM）
    song_engine.py       唱歌引擎（解码 / 切句 / 后台播放）
    song_control.py      唱歌交互（点歌 / 口令 / 随机哼唱）
    main.py              入口：只负责启动
"""

import math
import os
import queue
import random
import threading
import time

import tkinter as tk

# ---- 本项目模块 ----
# config 放在最前：HAS_* 能力标志、常量、文案、Config 都从这里来
from config import *                      # noqa: F401,F403
from config import Config, BASE_DIR, DEEPSEEK_API_KEY, DEEPSEEK_API_URL

from voice_control import VoiceMixin
from song_control import SongMixin
from physics import PhysicsMixin
from ui_widgets import UIWidgetsMixin
from walk import WalkMixin
from interaction import InteractionMixin
from chat import ChatMixin
from dialogue_llm import DialogueMixin
from hourly import HourlyMixin
from menu import MenuMixin
from study import StudyMixin
from awareness import AwarenessMixin
from launcher import LauncherMixin
from weather import WeatherMixin
from holidays import HolidaysMixin
from diary import DiaryMixin
from facts import FactsMixin
from speech_control import SpeechMixin


class DesktopPet(UIWidgetsMixin, WalkMixin, InteractionMixin, ChatMixin,
                 DialogueMixin, HourlyMixin, MenuMixin,
                 VoiceMixin, SongMixin, StudyMixin, PhysicsMixin,
                 AwarenessMixin, LauncherMixin,
                 WeatherMixin, HolidaysMixin, DiaryMixin, FactsMixin,
                 SpeechMixin):
    """桌宠主类：只负责组合各功能模块 + 主时钟

    功能模块（一个功能一个文件）：
        ui_widgets.py     窗口与控件、立绘、好感度显示
        walk.py           自主走动与走路帧动画
        interaction.py    拖拽、摸头、输入框
        chat.py           气泡与对话状态机
        dialogue_llm.py   输入处理、DeepSeek 调用、本地台词
        hourly.py         整点报时
        menu.py           右键菜单与设置项
        voice_control.py  全双工语音交互
        voice_engine.py   语音引擎（收音 / VAD / 流式识别）
        song_engine.py    唱歌引擎
        song_control.py   唱歌交互
        study.py          学习计时
        physics.py        重力下落与边缘走回
        awareness.py      屏幕感知（你在用什么）
        banter.py         打趣规则（感知到之后说什么）
        launcher.py       打开应用 / 网址（带确认）
        weather.py        天气（按城市名查 + 特殊天气提醒）
        holidays.py       节日与日期（公历 + 农历）
        diary.py          日记（每天自动记录，只能看和删）
        facts.py          事实问答（时间/日期/天气，本地精准回答）
        speech.py         说话引擎接口（edge / local / clone 可插拔）
        speech_control.py 说话输出（气泡 → 语音，按句缓存 + 打断）
        config.py         全部常量与文案入口
    """

    def __init__(self):
        self.cfg = Config()

        self.root = tk.Tk()
        self.root.title("哥伦比娅")
        self.root.overrideredirect(True)
        self.root.attributes('-topmost', bool(self.cfg['topmost']))
        try:
            self.root.attributes('-transparentcolor', TRANSPARENT)
        except tk.TclError:
            pass
        self.root.configure(bg=TRANSPARENT)

        x, y = self.cfg['position']
        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        x = max(0, min(int(x), sw - WIN_W))
        y = max(0, min(int(y), sh - WIN_H))
        self.root.geometry(f"{WIN_W}x{WIN_H}+{x}+{y}")

        self.images = {}
        self.cur_photo = None
        self.messages = []
        self.is_generating = False

        # 图片状态
        self.current_pose = None
        self.last_interaction_time = time.time()
        self._last_idle_chat = time.time()

        # 对话状态机
        self.dlg_state = 'idle'
        self.dlg_queue = []
        self.dlg_current = ""
        self.dlg_index = 0
        self.dlg_last_char_t = 0.0
        self.dlg_wait_start = 0.0
        self.dlg_fade_start = 0.0

        # 拖拽状态
        self._dragging = False
        self._press_x = 0
        self._press_y = 0
        self._off_x = 0
        self._off_y = 0

        # 输入框默认隐藏
        self._input_visible = False

        # 走动状态
        self.walk_target_x = None
        self._walk_next_trigger = time.time() + random.uniform(WALK_MIN_PAUSE, WALK_MAX_PAUSE)
        self._walk_last_step_t = 0.0
        # 走路帧动画
        self._walk_r = []
        self._walk_l = []
        self._note_imgs = []   # 唱歌音符（预渲染图片，避免透明窗上文字带粉色毛边）
        self._walk_anim_i = 0
        self._walk_anim_cycle = None   # 浮动周期计数（帧切换与落地同步）
        self._walk_base_y = 0   # 走路起点高度（浮动基于它，避免累积漂移）
        self._swing_base = None  # 睡眠摇篮摆动的支点 (x, y)，睡着时锚定

        # 整点报时
        self._last_hourly_key = None

        # 睁眼 / 戴眼罩待机轮换
        self._idle_switch_next = time.time() + random.uniform(IDLE_SWITCH_MIN, IDLE_SWITCH_MAX)

        # ---- 各功能模块自己的状态（见 voice_control.py / song_control.py / study.py）----
        self.setup_voice()
        self.setup_song()
        self.setup_study()
        self.setup_physics()
        self.setup_awareness()     # 屏幕感知
        self.setup_launcher()      # 打开应用/网址
        self.setup_weather()       # 天气
        self.setup_holidays()      # 节日与日期
        self.setup_diary()         # 日记
        self.setup_facts()         # 事实问答（时间/日期/天气）
        self.setup_speech()        # 说话输出（气泡 → 语音）
        # 后台预热语音引擎：XTTS 冷启动约 20 秒，提前加载好，
        # 否则用户听到的第一句话会明显卡顿。不阻塞启动。
        self.speech_warmup_async()

        self._build_ui()
        self._load_images()
        self._setup_drag()
        self._setup_menu()

        self._ensure_dialog_file()
        # 记下 job id：测试里可以取消它，避免问候气泡干扰其它断言
        self._greet_job = self.root.after(500, self._greet)
        self._tick()
    def _tick(self):
        now = time.time()

        try: self._update_dialogue()
        except Exception: pass

        try: self._update_physics(now)   # 重力下落 / 边缘走回（在走动前优先处理）
        except Exception: pass

        try: self._update_walk(now)
        except Exception: pass

        try: self._update_sleep_swing(now)  # 睡着时的摇篮晃动
        except Exception: pass

        try: self._drain_ui_q()          # 收音线程抛来的播放器口令/语音点歌
        except Exception: pass

        try: self._update_song(now)
        except Exception: pass

        try: self._check_hourly(now)
        except Exception: pass

        try: self._study_tick(now)       # 学习计时
        except Exception: pass

        try: self._aware_tick(now)       # 屏幕感知（你在用什么）
        except Exception: pass

        try: self.weather_tick(now)      # 天气刷新 + 特殊天气提醒
        except Exception: pass

        try: self.holidays_tick(now)     # 节日祝福
        except Exception: pass

        try: self.diary_tick(now)        # 日记自动写入
        except Exception: pass

        try: self.speech_pump()          # 说话：把后台合成好的音频交出去
        except Exception: pass

        # 空闲闲聊（唱歌期间不插嘴）
        try:
            if (self.dlg_state == 'idle'
                and not self.is_generating
                and self._sing_state == 'off'
                and now - self.last_interaction_time > IDLE_CHAT_SEC
                and now - self._last_idle_chat > IDLE_CHAT_SEC):
                self._last_idle_chat = now
                self._speak(self._local_reply())
        except Exception: pass

        # 空闲切 sleep（唱歌/学习期间不睡）
        try:
            if (now - self.last_interaction_time > SLEEP_SEC
                and self.current_pose not in ('sleep',)
                and not self._dragging
                and self._sing_state == 'off'
                and self._study_state == 'idle'
                and self.dlg_state == 'idle'):
                self._set_pose('sleep')
        except Exception: pass

        # 待机时睁眼(smile) / 戴眼罩(walk1)轮换（学习期间保持 work，不轮换）
        try:
            if (now > self._idle_switch_next
                and self.current_pose in ('smile', 'walk1')
                and 'walk1' in self.images
                and self.dlg_state == 'idle'
                and self._study_state == 'idle'
                and not self._dragging
                and not self.is_generating
                and self.walk_target_x is None):
                self._idle_switch_next = now + random.uniform(IDLE_SWITCH_MIN, IDLE_SWITCH_MAX)
                self._set_pose('walk1' if self.current_pose == 'smile' else 'smile')
        except Exception: pass

        self.root.after(30, self._tick)

    def _update_dialogue(self):
        now = time.time()
        # 语音回复的逐句打字由 _rx_tick 自己驱动，这里不插手
        if self.dlg_state in ('vtyping', 'vwait'):
            return
        if self.dlg_state == 'typing':
            if now - self.dlg_last_char_t >= TYPING_INTERVAL:
                self.dlg_last_char_t = now
                self.dlg_index += 1
                if self.dlg_index >= len(self.dlg_current):
                    self.bubble_label.config(text=self.dlg_current)
                    self.dlg_state = 'waiting'
                    self.dlg_wait_start = now
                else:
                    self.bubble_label.config(text=self.dlg_current[:self.dlg_index])
        elif self.dlg_state == 'waiting':
            elapsed = now - self.dlg_wait_start
            delay = WAIT_NEXT if self.dlg_queue else WAIT_LAST
            if elapsed >= delay:
                # 开着说话时：这句还没念完就先不翻下一页。
                # 神经语音（edge）合成 1.5~2.5 秒 + 播放数秒，固定 2 秒翻页
                # 会把上一页的话拦腰掐断；本地原声（hybrid）句子短，基本不受影响。
                # 只拦"有下一页"的情况——末页淡出不打断语音。
                if (self.dlg_queue and elapsed < SPEECH_PAGE_MAX_HOLD
                        and self.speech_busy()):
                    return
                self._advance()
        elif self.dlg_state == 'fading':
            elapsed = now - self.dlg_fade_start
            t = min(elapsed / FADE_DURATION, 1.0)
            fr, fg_, fb = 0x43, 0x39, 0x5C   # BUBBLE_FG
            tr, tg, tb = 0xEF, 0xE8, 0xF9    # BUBBLE_FROST_BOTTOM
            r, g, b = int(fr+(tr-fr)*t), int(fg_+(tg-fg_)*t), int(fb+(tb-fb)*t)
            try: self.bubble_label.config(fg=f'#{r:02x}{g:02x}{b:02x}')
            except Exception: pass
            if t >= 1.0:
                self._hide_bubble()
                self.dlg_state = 'idle'
                # 语音回复的淡出结束 -> 这一轮交互彻底结束：
                # 释放接收标志与站定标志，她可以继续自己走动
                if self._rx_active:
                    self._rx_active = False
                self._v_listening = False
                self._on_dialog_done()

    def _greet(self):
        self._set_pose('hi')
        self.root.after(1500, lambda: self._set_pose('smile'))

        h = time.localtime().tm_hour
        if 5 <= h < 11: greet = "早……头发有些乱……？在哪边？帮我梳梳。"
        elif 11 <= h < 18: greet = "呼……月亮还没升起来。"
        elif 18 <= h < 23: greet = "夜幕落下……就好像「夜晚」是被月亮绊了一跤，才掉下来的一样。"
        else: greet = "睡不着吗？"

        # 节日/生日优先：这是特殊的日子，值得单独说
        try:
            from holidays import holiday_of_day
            name, line = holiday_of_day(self.cfg['birthday'])
            if name:
                self._speak(line, interrupt=True)
                return
        except Exception:
            pass
        self._speak(greet)

    def run(self):
        self.bubble.bind('<Button-1>', self._on_bubble_click)
        self.bubble_label.bind('<Button-1>', self._on_bubble_click)
        try: self.root.focus_force()
        except Exception: pass
        self.root.mainloop()

