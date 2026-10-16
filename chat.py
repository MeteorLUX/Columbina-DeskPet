# -*- coding: utf-8 -*-
"""
对话气泡与状态机（ChatMixin）
=====================
气泡的显示/逐字打字/等待/淡出，
以及点击气泡跳过打字、跳到下一句。
状态：idle / typing / waiting / fading / vtyping / vwait。

`vtyping` / `vwait` 是"语音回复的逐句打字"，由 voice_control.py 驱动。

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


class ChatMixin:
    """对话气泡与状态机"""

    @staticmethod
    def _split_sentences(text):
        """把一段话切成"气泡单屏"粒度：
        1) 先按句末强标点（。！？；\\n）切句（'…'是软停顿，不切）；
        2) 超过单屏容量（BUBBLE_PAGE_CHARS）的句子在逗号处切下一屏；
        3) 没有逗号的超长句按字数硬切。
        短句不会被强行拆开，只有放不下才换屏。"""
        text = (text or '').strip()
        if not text: return []
        maxn = BUBBLE_PAGE_CHARS
        result = []
        # 注意：'…' 是软停顿（且常连用'……'），不作为换屏切分点
        for s in (p.strip() for p in re.split(r'(?<=[。！？!?；;\n])', text)):
            if not s:
                continue
            if len(s) <= maxn:
                result.append(s)
                continue
            # 超长：按逗号切屏（标点跟随前段），无逗号的硬切
            cur = ''
            for seg in re.split(r'(?<=[，,、：])', s):
                while len(seg) > maxn:            # 无逗号的超长段 → 硬切
                    if cur:
                        result.append(cur)
                        cur = ''
                    result.append(seg[:maxn])
                    seg = seg[maxn:]
                if cur and len(cur) + len(seg) > maxn:
                    result.append(cur)
                    cur = seg
                else:
                    cur += seg
            if cur:
                result.append(cur)
        return result if result else [text]

    def _speak(self, text, interrupt=False):
        text = (text or '').strip()
        if not text: return
        sentences = self._split_sentences(text)
        if not sentences: return

        # 新一轮说话：先掐掉上一轮还在播的语音（否则两段语音会叠在一起）
        self._speak_hook_new_turn()

        if interrupt or self.dlg_state == 'idle':
            self.dlg_queue = sentences[1:]
            self._begin_sentence(sentences[0])
        else:
            self.dlg_queue.extend(sentences)

    def _speak_hook_new_turn(self):
        """开始新一轮说话：掐掉上一轮还在播的语音（世代换代）"""
        try:
            self.speech_stop()
        except Exception:
            pass

    def _begin_sentence(self, sentence):
        self.bubble_label.config(text="", fg=BUBBLE_FG)
        self.bubble.place(x=BUBBLE_X, y=BUBBLE_Y, width=BUBBLE_W, height=BUBBLE_H)
        self.bubble.lift()
        self.dlg_current = sentence
        self.dlg_index = 0
        self.dlg_state = 'typing'
        self.dlg_last_char_t = time.time() - TYPING_INTERVAL
        # 说话时停止走动
        self.walk_target_x = None
        # 念出来（没有装/关了静音/正在唱歌时它自己会忽略）
        # 挂在这里而不是 _speak：气泡本来就是一句一句打，声音跟着句子走
        try:
            self.speech_say(sentence)
        except Exception:
            pass

    def _advance(self):
        if self.dlg_queue:
            self._begin_sentence(self.dlg_queue.pop(0))
        else:
            self.dlg_state = 'fading'
            self.dlg_fade_start = time.time()

    def _hide_bubble(self):
        self.bubble.place_forget()
        self.bubble_label.config(text="", fg=BUBBLE_FG)

    def _on_bubble_click(self, event=None):
        self._reset_idle_timer()
        now = time.time()
        # 语音回复的逐句打字：点一下 = 立刻打完当前句 / 跳到下一句
        if self.dlg_state == 'vtyping':
            self._rx_shown = len(self._rx_sent)
            self.bubble_label.config(text=self._rx_sent, fg=BUBBLE_FG)
            self.dlg_state = 'vwait'
            self.dlg_wait_start = now
            return
        if self.dlg_state == 'vwait':
            if now - self.dlg_wait_start < CLICK_COOLDOWN:
                return
            if self._rx_queue or self._rx_next():
                self._rx_start_next(now)
            return
        if self.dlg_state == 'typing':
            self.dlg_index = len(self.dlg_current)
            self.bubble_label.config(text=self.dlg_current)
            self.dlg_state = 'waiting'
            self.dlg_wait_start = now
        elif self.dlg_state == 'waiting':
            if now - self.dlg_wait_start < CLICK_COOLDOWN: return
            self._advance()
        elif self.dlg_state == 'fading':
            self._hide_bubble()
            self.dlg_state = 'idle'
            self._on_dialog_done()
