# -*- coding: utf-8 -*-
"""
鼠标交互（InteractionMixin）
========================
拖拽移动窗口、单击呼出/收起输入框、
双击摸头（加好感 + 随机台词）。

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


class InteractionMixin:
    """鼠标交互"""

    def _setup_drag(self):
        def on_press(e):
            self._press_x = e.x_root
            self._press_y = e.y_root
            self._off_x = e.x_root - self.root.winfo_x()
            self._off_y = e.y_root - self.root.winfo_y()
            self._dragging = False
            self._reset_idle_timer()

        def on_motion(e):
            dx = abs(e.x_root - self._press_x)
            dy = abs(e.y_root - self._press_y)
            if dx > 4 or dy > 4:
                self._dragging = True
            if self._dragging:
                nx = e.x_root - self._off_x
                ny = e.y_root - self._off_y
                self.root.geometry(f"+{nx}+{ny}")
                self._set_pose('touch')
                # 拖动时取消走动
                self.walk_target_x = None
                # 拖动时别说话（她在被搬动）
                try:
                    self.speech_stop()
                except Exception:
                    pass

        def on_release(e):
            if self._dragging:
                self.cfg['position'] = [self.root.winfo_x(), self.root.winfo_y()]
                self.cfg.save()

                # 物理接管（重力下落 / 边缘走回）还是普通恢复？
                self._physics_after_release()
                if self._physics_state == 'idle':
                    # 没触发物理：恢复走动节奏 + 恢复姿态
                    self.walk_target_x = None
                    self._walk_next_trigger = time.time() + random.uniform(WALK_MIN_PAUSE, WALK_MAX_PAUSE)
                    self.root.after(500, self._pose_after_interaction)

                # 学习计时中拖动：提醒认真学习（不停止计时、不结束学习）
                if self._study_state == 'studying':
                    self._speak(random.choice(STUDY_DRAG_LINES))
            else:
                # 单击 → 呼出/收起输入框
                self._toggle_input()

        self.canvas.bind('<Button-1>', on_press)
        self.canvas.bind('<B1-Motion>', on_motion)
        self.canvas.bind('<ButtonRelease-1>', on_release)
        # 双击 → 摸头
        self.canvas.bind('<Double-Button-1>', lambda e: self._on_pet())

    def _pose_after_interaction(self):
        """拖动/摸头结束后回到合适姿态：学习计时中回 work，其余回 smile"""
        if self._study_state == 'studying':
            self._set_pose('work')
        else:
            self._set_pose('smile')

    def _toggle_input(self):
        self._reset_idle_timer()
        if self._input_visible:
            self.input_holder.place_forget()
            self._input_visible = False
        else:
            self.input_holder.place(x=INPUT_X, y=INPUT_Y, width=INPUT_W, height=INPUT_H)
            self.input_holder.lift()
            self.entry.focus_set()
            self._input_visible = True


# ==================== 抚摸与待机计时 ====================
    def _on_pet(self):
        """抚摸互动"""
        self._reset_idle_timer()
        self._set_pose('touch')
        self._add_affinity(1)
        # 记一笔（日记里她会写"你今天碰了我几次"）
        try:
            self.activity.bump_day('pets', 1)
        except Exception:
            pass
        self.root.after(600, lambda: self._set_pose('happy'))
        self.root.after(1400, self._pose_after_interaction)
        # 随机回应一句
        line = random.choice(PET_LINES)
        self._speak(line, interrupt=True)

    def _reset_idle_timer(self):
        self.last_interaction_time = time.time()
        if self.current_pose == 'sleep':
            self._set_pose('smile')