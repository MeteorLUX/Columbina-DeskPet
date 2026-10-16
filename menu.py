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
        # 三个模式的区别（决定对话**内容**从哪来）：
        #   DeepSeek 云端 / 本地模型  -> 真实 LLM 对话
        #   本地台词库              -> 不调模型，只从库里挑（离线）
        for _val, _label in DIALOGUE_MODES:
            mode_menu.add_radiobutton(label=_label, variable=self.mode_var,
                                      value=_val,
                                      command=self._on_mode_change)
        mode_menu.add_separator()
        mode_menu.add_command(label="⚙️ 设置本地模型…",
                              command=self._set_local_llm)
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
        # 按模式给不同反馈，让用户知道"现在到底是真对话还是挑台词"
        if mode == 'local':
            self._speak("……我在这里。现在只用台词库，不连模型。")
        elif mode == 'local_llm':
            model = self.cfg.get('local_llm_model') or '?'
            self._speak(f"……好，我连本地模型。{model}。")
        else:
            self._speak("……嗯，我试着听听你的声音。")

    def _set_local_llm(self):
        """设置本地模型端点与模型名（Ollama / LM Studio）。

        为什么让用户填而不是写死：不同人装的本地服务端口和模型名都不一样
        （Ollama 默认 11434 + qwen2.5:7b，LM Studio 默认 1234 + 别的名字）。
        """
        import tkinter as tk
        from tkinter import simpledialog

        cur_url = (self.cfg.get('local_llm_url')
                   or 'http://127.0.0.1:11434/v1/chat/completions')
        cur_model = self.cfg.get('local_llm_model') or 'qwen2.5:7b'

        win = tk.Toplevel(self.root)
        win.title('本地模型设置')
        win.attributes('-topmost', True)
        win.resizable(False, False)
        frm = tk.Frame(win, padx=14, pady=12)
        frm.pack()

        tk.Label(frm, text='接口地址（OpenAI 兼容的 /chat/completions）',
                 anchor='w').grid(row=0, column=0, sticky='w')
        url_var = tk.StringVar(value=cur_url)
        tk.Entry(frm, textvariable=url_var, width=52).grid(
            row=1, column=0, pady=(2, 10))

        tk.Label(frm, text='模型名', anchor='w').grid(row=2, column=0, sticky='w')
        model_var = tk.StringVar(value=cur_model)
        tk.Entry(frm, textvariable=model_var, width=52).grid(
            row=3, column=0, pady=(2, 6))

        tk.Label(frm, justify='left', anchor='w',
                 text=('Ollama：ollama serve，然后 ollama pull qwen2.5:7b\n'
                       '      地址 http://127.0.0.1:11434/v1/chat/completions\n'
                       'LM Studio：启动 Server\n'
                       '      地址 http://127.0.0.1:1234/v1/chat/completions\n'
                       '本地模型不需要 API key，也不花钱。'),
                 fg='#666').grid(row=4, column=0, sticky='w', pady=(6, 10))

        def do_save():
            u = url_var.get().strip()
            m = model_var.get().strip()
            if u:
                self.cfg['local_llm_url'] = u
            if m:
                self.cfg['local_llm_model'] = m
            self.cfg.save()
            self._speak(f"……记住了。{m}。")
            try:
                win.destroy()
            except Exception:
                pass

        btns = tk.Frame(frm)
        btns.grid(row=5, column=0, sticky='e')
        tk.Button(btns, text='测试连接', command=lambda: self._test_local_llm(
            url_var.get().strip(), model_var.get().strip(), win)).pack(
                side='left', padx=4)
        tk.Button(btns, text='保存', command=do_save).pack(side='left', padx=4)

    def _test_local_llm(self, url, model, parent=None):
        """真的发一次请求，把结果直接告诉用户（别让他猜为什么没反应）"""
        from tkinter import messagebox
        import threading
        try:
            import requests
        except ImportError:
            messagebox.showerror('本地模型', '缺少 requests，无法测试。',
                                 parent=parent)
            return
        if not url:
            messagebox.showwarning('本地模型', '请先填接口地址。', parent=parent)
            return

        def work():
            ok, msg = False, ''
            try:
                r = requests.post(
                    url, json={'model': model,
                               'messages': [{'role': 'user',
                                             'content': '说一句「在」就行'}],
                               'max_tokens': 20, 'stream': False},
                    timeout=30)
                if r.status_code == 200:
                    txt = r.json()['choices'][0]['message']['content'].strip()
                    ok, msg = True, f'连接成功！它回答：{txt}'
                else:
                    msg = f'HTTP {r.status_code}：{r.text[:200]}'
            except Exception as e:
                msg = (f'{type(e).__name__}: {e}\n\n'
                       '常见原因：本地服务没启动 / 端口不对 / 模型名写错。')
            self.root.after(0, lambda: (
                messagebox.showinfo('本地模型', msg, parent=parent) if ok
                else messagebox.showerror('本地模型', msg, parent=parent)))

        threading.Thread(target=work, daemon=True).start()

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
