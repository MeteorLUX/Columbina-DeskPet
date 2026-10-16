# -*- coding: utf-8 -*-
"""
右键菜单与设置项（MenuMixin）
================================
组装右键菜单（对话模式 / 语音 / 唱歌 / 走动 / 置顶 / 退出），
以及各设置项的处理逻辑、好感度与关于对话框。

菜单项对应的功能实现分散在各模块，这里只负责"菜单"这一层。
"""
import os
import time

import tkinter as tk
from tkinter import Menu, simpledialog

from config import *          # noqa: F401,F403  常量、文案、Config


class MenuMixin:
    """右键菜单与设置项"""

    def _setup_menu(self):
        self.menu = Menu(self.root, tearoff=0, bg='#2B2436', fg='#F0E8FF',
                         activebackground='#6A4BA8', activeforeground='white',
                         font=('Microsoft YaHei', 10))

        self.mode_var = tk.StringVar(value=self.cfg['dialogue_mode'])
        mode_menu = Menu(self.menu, tearoff=0, bg='#2B2436', fg='#F0E8FF',
                         activebackground='#6A4BA8', activeforeground='white',
                         font=('Microsoft YaHei', 10))
        mode_menu.add_radiobutton(label="本地对话（离线）", variable=self.mode_var, value='local', command=self._on_mode_change)
        mode_menu.add_radiobutton(label="DeepSeek 智能对话", variable=self.mode_var, value='deepseek', command=self._on_mode_change)
        self.menu.add_cascade(label="💬 对话模式", menu=mode_menu)

        self.menu.add_command(label="🔄 重新加载本地对话", command=self._reload_dialogs)
        self.menu.add_command(label="✏️ 修改你对我的称呼", command=self._change_master_name)
        self.menu.add_command(label="⌨️ 显示/隐藏输入框", command=self._toggle_input)
        self.menu.add_command(label="❤️ 查看好感度", command=self._show_affinity)
        self.menu.add_separator()

        # 🌊 全双工语音开关（默认关闭，点一次开，再点一次关）
        self.menu.add_command(label="🌊 语音对话",
                              command=self._toggle_full_duplex)
        self.menu.add_separator()

        # 🔊 说话输出：气泡里的字念出来（可关；也可换音色/引擎）
        self.speech_var = tk.BooleanVar(value=bool(self.cfg['speech_enabled']))
        self.menu.add_checkbutton(label="🔊 让她说出声",
                                  variable=self.speech_var,
                                  command=self.speech_toggle)
        voice_menu = tk.Menu(self.menu, tearoff=0)
        self.speech_voice_menu = voice_menu
        self._build_speech_menu(voice_menu)
        self.menu.add_cascade(label="🎙 说话音色", menu=voice_menu)
        self.menu.add_separator()

        # 🎵 唱歌 / 📖 学习：各合并为一个入口，点击弹出月夜风格面板
        self.menu.add_command(label="🎵 唱歌", command=self.song_show_panel)
        self.menu.add_command(label="📖 学习", command=self.study_show_panel)
        self.menu.add_separator()

        # 👀 屏幕感知：让她知道你正在用什么（可随时关掉）
        self.menu.add_command(label="👀 她在看什么", command=self.aware_show_panel)
        self.aware_var = tk.BooleanVar(value=bool(self.cfg['awareness_enabled']))
        self.menu.add_checkbutton(label="👁 让她知道我在用什么",
                                  variable=self.aware_var,
                                  command=self._toggle_awareness)
        # 🔗 软件路径：说「打开 XX」时她按这里的路径启动
        self.menu.add_command(label="🔗 管理软件路径", command=self.paths_show_panel)
        self.menu.add_separator()

        # 📔 日记：她每天自己写，你只能看和删
        self.menu.add_command(label="📔 日记", command=self.diary_show_panel)
        self.menu.add_separator()

        self.walk_var = tk.BooleanVar(value=bool(self.cfg['walk_enabled']))
        self.menu.add_checkbutton(label="🚶 允许自由走动", variable=self.walk_var, command=self._toggle_walk)
        self.topmost_var = tk.BooleanVar(value=bool(self.cfg['topmost']))
        self.menu.add_checkbutton(label="📌 窗口置顶", variable=self.topmost_var, command=self._toggle_topmost)
        self.menu.add_separator()
        self.menu.add_command(label="📖 关于哥伦比娅", command=self._show_about)
        self.menu.add_command(label="❌ 退出", command=self._on_quit)

        for w in (self.canvas, self.bubble, self.bubble_label, self.affinity_label):
            w.bind('<Button-3>', self._show_menu)

    def _show_menu(self, event):
        try: self.menu.tk_popup(event.x_root, event.y_root)
        finally: self.menu.grab_release()

    def _on_mode_change(self):
        mode = self.mode_var.get()
        self.cfg['dialogue_mode'] = mode
        self.cfg.save()
        self._speak("……嗯，我试着听听你的声音。" if mode == 'deepseek' else "……我在这里。")

    def _toggle_walk(self):
        val = bool(self.walk_var.get())
        self.cfg['walk_enabled'] = val
        self.cfg.save()
        if not val:
            self.walk_target_x = None

    def _toggle_topmost(self):
        val = bool(self.topmost_var.get())
        self.cfg['topmost'] = val
        self.cfg.save()
        self.root.attributes('-topmost', val)

    def _change_master_name(self):
        try:
            name = simpledialog.askstring("称呼设定", "你希望我怎么称呼你？",
                                          initialvalue=self.cfg['master_name'], parent=self.root)
        except Exception: return
        if name and name.strip():
            self.cfg['master_name'] = name.strip()
            self.cfg.save()
            self._speak(f"……{name.strip()}。嗯，我记住了。")

    def _show_affinity(self):
        a = int(self.cfg['affinity'])
        if a < 20:
            mood = "你对我来说……还是陌生人。"
        elif a < 50:
            mood = "……嗯，算熟悉了一点。"
        elif a < 80:
            mood = "你在这里的时候，月亮好像更亮一些。"
        else:
            mood = "……你是我找到的，家。"
        self._speak(f"好感度：{a} / 100。\n{mood}")

    def _show_about(self):
        self._speak("我的名字是……哥伦比娅·希珀塞莱尼娅")

    def _on_quit(self):
        try:
            self._study_on_quit()
        except Exception: pass
        try:
            if self.song is not None:
                self.song.shutdown()
        except Exception: pass
        try:
            self.cfg['position'] = [self.root.winfo_x(), self.root.winfo_y()]
            self.cfg.save()
        except Exception: pass
        self.root.destroy()

    # ====================================================
    #  对话状态机
    # ====================================================

    # ====================================================
    #  主时钟
