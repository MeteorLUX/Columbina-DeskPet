# -*- coding: utf-8 -*-
"""
整点报时（HourlyMixin）
===================
每到整点用气泡报一次时间，措辞按时段变化。

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


class HourlyMixin:
    """整点报时"""

    def _check_hourly(self, now):
        t = time.localtime(now)
        if t.tm_min != 0:
            return
        key = (t.tm_year, t.tm_mon, t.tm_mday, t.tm_hour)
        if self._last_hourly_key == key:
            return
        self._last_hourly_key = key

        h = t.tm_hour
        if 5 <= h < 8:
            msg = f"……{h} 点了。天快亮了。"
        elif 8 <= h < 12:
            msg = f"……{h} 点了。上午的时光。"
        elif 12 <= h < 14:
            msg = f"……{h} 点了。记得吃饭。"
        elif 14 <= h < 18:
            msg = f"……{h} 点了。下午了。"
        elif 18 <= h < 22:
            msg = f"……{h} 点了。月亮快出来了。"
        elif h == 23:
            msg = "……23 点了。该休息了。"
        else:
            msg = f"……{h} 点了。还不睡吗。"
        self._speak(msg, interrupt=False)
