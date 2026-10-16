# -*- coding: utf-8 -*-
"""
自主走动（WalkMixin）
=================
桌面随机漫步：随机选目标、每步位移、上下浮动、
朝左/朝右走路帧动画、唱歌时头顶冒音符。
真正"什么时候该站定"的判定也在这里（说话/回话时不动；
唱歌口令暂停不影响待机走动——歌停它的，她照样活动）。

以 Mixin 形式工作，直接使用 DesktopPet 的属性与方法
（self.root / self.canvas / self.cfg / self._set_pose 等）。
"""
import math
import os
import random
import re
import threading
import time

import tkinter as tk
from tkinter import Menu, simpledialog

try:
    from PIL import Image, ImageTk, ImageOps
except ImportError:
    pass

try:
    import requests
except ImportError:
    requests = None

from config import *          # noqa: F401,F403  常量、文案、Config


class WalkMixin:
    """自主走动"""

    def _update_walk(self, now):
        # 物理接管（falling / walking_in）期间不走动——physics 自己驱动位置
        if getattr(self, '_physics_state', 'idle') != 'idle':
            return
        # 学习中：站定不动，只保持 work 姿态
        if getattr(self, '_study_state', 'idle') == 'studying':
            return
        if not self.cfg['walk_enabled']:
            return
        # 睡觉时不走动
        if self.current_pose == 'sleep':
            return
        # 拖拽、对话时不走
        if self._dragging or self.is_generating:
            return
        # 只有"正在听我说话 / 正在回我话"时才站定。
        # 不能因为 voice_on 就一直不走 —— 那样她就永远杵在那儿不动，
        # 看不出"自己在干自己的事"。
        if self._voice_busy() or self._rx_active:
            return
        if self.dlg_state != 'idle':
            return

        sw = self.root.winfo_screenwidth()
        x = self.root.winfo_x()
        y = self.root.winfo_y()

        if self.walk_target_x is None:
            # 等待下次触发
            if now < self._walk_next_trigger:
                return
            # 生成目标位置
            target = random.randint(20, sw - WIN_W - 20)
            if abs(target - x) < 100:
                self._walk_next_trigger = now + random.uniform(3, 6)
                return
            self.walk_target_x = target
            self._walk_base_y = y
            self._on_walk_start()      # 停后再走：若歌停在句尾，接着唱
            return

        # 移动
        if now - self._walk_last_step_t < WALK_STEP_SEC:
            return
        self._walk_last_step_t = now

        if abs(x - self.walk_target_x) <= WALK_STEP_PX:
            nx = self.walk_target_x
            ny = self._walk_base_y
            self.walk_target_x = None
            self._walk_next_trigger = now + random.uniform(WALK_MIN_PAUSE, WALK_MAX_PAUSE)
            self._set_pose('smile')
        else:
            direction = 1 if self.walk_target_x > x else -1
            nx = x + WALK_STEP_PX * direction
            # 以上下轻轻浮动，像不受地心引力（基于走路起点高度，不累积）
            ny = self._walk_base_y + int(round(WALK_BOB_PX * math.sin(now * WALK_BOB_SPEED)))
            self._anim_walk(now, direction)

        try:
            self.root.geometry(f"+{nx}+{ny}")
            self.cfg['position'] = [nx, self._walk_base_y]
        except Exception:
            self.walk_target_x = None

    def _update_sleep_swing(self, now):
        """睡着时的摇篮晃动：以睡着那一刻的位置为支点，
        按扇形轨迹（半径固定的钟摆）轻微左右来回摆。
        摆动不写入 cfg['position']，避免记住的位置跟着漂移。"""
        # 物理接管期间（重力下落 / 边缘走回）不摆动——physics 自己驱动位置
        if getattr(self, '_physics_state', 'idle') != 'idle':
            self._swing_base = None
            return
        if self.current_pose != 'sleep' or self._dragging:
            self._swing_base = None          # 醒了/被拖走：丢掉支点，下次睡着重新锚定
            return
        if self._swing_base is None:
            self._swing_base = (self.root.winfo_x(), self.root.winfo_y())
        bx, by = self._swing_base
        ang = SLEEP_SWING_ANGLE * math.sin(now * SLEEP_SWING_SPEED)
        nx = bx + int(round(SLEEP_SWING_RADIUS * math.sin(ang)))
        ny = by - int(round(SLEEP_SWING_RADIUS * (1.0 - math.cos(ang))))
        try:
            self.root.geometry(f"+{nx}+{ny}")
        except Exception:
            self._swing_base = None

    def _anim_walk(self, now, direction):
        """走路帧动画：按方向选朝右/朝左帧序列，每次浮动落地（最低点）时换帧"""
        frames = self._walk_r if direction > 0 else self._walk_l
        if not frames:
            self._set_pose('touch')
            return
        # 浮动到最低点（sin 取最大）算一次落地，落地才换帧
        cycle = int((now * WALK_BOB_SPEED - math.pi / 2) // (2 * math.pi))
        if self._walk_anim_cycle is not None and cycle == self._walk_anim_cycle:
            return
        self._walk_anim_cycle = cycle
        self._walk_anim_i = (self._walk_anim_i + 1) % len(frames)
        self.current_pose = 'walking'
        self.canvas.delete('all')
        self.cur_photo = frames[self._walk_anim_i]
        self.canvas.create_image(WIN_W // 2, IMG_H // 2, image=self.cur_photo)
        # 头顶交替冒音符：只在唱歌时；用预渲染图片，避免文字抗锯齿泛粉色毛边
        if self._sing_state in ('playing', 'stopping') and self._note_imgs:
            side = 26 if self._walk_anim_i % 2 == 0 else -26
            self.canvas.create_image(WIN_W // 2 + side, 14,
                                     image=self._note_imgs[self._walk_anim_i % 2])
        # delete('all') 把学习计时条也删了，补画
        try: self._study_ui_repaint()
        except Exception: pass
