# -*- coding: utf-8 -*-
"""
说话控制（speech_control.py）
=============================
把气泡里的每一句念出来，并按句缓存 + 预取来压低首字延迟。

【挂载点只有一个】
    chat.py 的 `_begin_sentence()` 会调 `self.speech_say(sentence)`。
气泡本来就是"一句一句打"的，所以声音天然跟着句子走，不需要改 chat 的逻辑。
（历史遗留的 `speech_note()` 是旧接口，保留为别名，别再挂第二次。）

【打断规则】（与唱歌/学习一致的思路）
    · 新一句话来了 -> 立刻掐掉正在播的旧音频（用世代计数器，不共享 cancel 标志）
    · 她开始唱歌   -> 停止说话（歌声优先）
    · 用户在说话   -> 停止说话（专注听）
    · 拖动她/睡觉  -> 停止说话
    · 静音开关     -> 直接不出声

【为什么用世代计数器】
和全双工语音那边同一套做法：每个播放任务带一个世代号，
换代后旧任务发现自己是旧的就不再出声，避免"线程还在播但已被打断"的竞态。
"""
import os
import queue
import threading
import time

import numpy as np

from config import *          # noqa: F401,F403  常量、文案、Config
from speech import (make_engine, decode_audio, list_engines, resample,
                    HAS_MINIAUDIO)

try:
    import soundcard as sc
    HAS_SOUNDCARD = True
except ImportError:
    HAS_SOUNDCARD = False


class SpeechPlayer:
    """后台播放器：把 (音频数组, 采样率) 排队顺序播出去。

    单独一条线程 + 自己的 Player 设备，与唱歌引擎互不干扰。
    """

    def __init__(self, samplerate=None):
        self.samplerate = int(samplerate or SONG_RATE)
        self._q = queue.Queue()
        self._stop = threading.Event()
        self._gen = 0
        self._lock = threading.Lock()
        self._thread = None
        self._busy = threading.Event()
        self._chunk = int(self.samplerate * 0.05)      # 50ms 一块

    # ---------- 世代（打断） ----------
    def new_generation(self):
        with self._lock:
            self._gen += 1
            return self._gen

    def _is_current(self, gen):
        with self._lock:
            return gen == self._gen

    # ---------- 投递 ----------
    def submit(self, data, gen=None, sr=None):
        """把音频数组排进播放队列。

        sr：这段数据的真实采样率。与播放设备采样率不一致时会**自动重采样** ——
        必须做，否则 24kHz 的内容被当 44.1kHz 播 = 语速飞快 + 声调高。
        """
        if data is None or getattr(data, 'size', 0) == 0:
            return
        # 防呆：采样率对不上就重采样（设备固定按 self.samplerate 打开）
        if sr is not None:
            try:
                sr = int(sr)
            except (TypeError, ValueError):
                sr = self.samplerate
            if sr != self.samplerate:
                data = resample(data, sr, self.samplerate)
        gen = gen if gen is not None else self.new_generation()
        try:
            self._q.put_nowait((gen, data))
        except queue.Full:
            pass
        self._ensure_thread()

    def clear(self):
        """清空待播队列并换代（打断当前播放）"""
        self.new_generation()
        while True:
            try:
                self._q.get_nowait()
            except queue.Empty:
                break

    @property
    def busy(self):
        return self._busy.is_set() or not self._q.empty()

    # ---------- 线程 ----------
    def _ensure_thread(self):
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name='speech-player',
                                        daemon=True)
        self._thread.start()

    def _loop(self):
        while not self._stop.is_set():
            try:
                gen, data = self._q.get(timeout=0.2)
            except queue.Empty:
                return                      # 没活了，线程退出（下次自动重启）
            if not self._is_current(gen):
                continue
            try:
                self._play_one(data, gen)
            except Exception:
                pass

    def _play_one(self, data, gen):
        """推送一段音频并等它播完。

        【为什么不做手动节流】
        曾经这里按"已推送时长 - 已播时长"来 sleep 控速，但 soundcard 的
        `currentpadding` 在本机恒返回 0，导致算出来的余量恒等于已推送时长，
        于是"推 50ms 音频 -> 睡 100ms"，缓冲被反复排空 —— 听感就是**一卡一卡**。
        （实测 4.32 秒的语音花了 8.91 秒才放完，被 sleep 阻塞 77 次。）

        正确做法：**不要手动节流**。soundcard 的 player.play() 在内部缓冲满时
        会自己阻塞，馈数据这一个循环天然就是流控。
        播完只需按墙体时钟等"剩余音频时长"即可，不需要查询填充量。

        注意 `_Player` 没有 close()，要用 __exit__() 释放。
        """
        player = None
        n = len(data)
        try:
            if not HAS_SOUNDCARD:
                return
            self._busy.set()
            player = sc.default_speaker().player(samplerate=self.samplerate)
            player.__enter__()
            i = 0
            step = self._chunk
            t0 = time.time()
            while i < n:
                if not self._is_current(gen) or self._stop.is_set():
                    return
                j = min(i + step, n)
                player.play(np.ascontiguousarray(data[i:j], dtype=np.float32))
                i = j
            # feed 循环返回后，还有一部分仍在设备缓冲里 -> 按时钟等它放完
            remaining = (n / self.samplerate) - (time.time() - t0)
            limit = time.time() + max(0.0, remaining) + 0.25   # 0.25s 余量防截断
            while time.time() < limit:
                if not self._is_current(gen) or self._stop.is_set():
                    return
                time.sleep(0.02)
        finally:
            if player is not None:
                try:
                    player.__exit__(None, None, None)
                except Exception:
                    try:
                        player.close()
                    except Exception:
                        pass
            self._busy.clear()

    def shutdown(self):
        self._stop.set()
        self.clear()


class SpeechMixin:
    """说话输出：气泡 → 语音"""

    def setup_speech(self):
        self._speech_on = bool(self.cfg['speech_enabled'])
        self._speech_engine_name = self.cfg['speech_engine'] or 'edge'
        self._speech = make_engine(self._speech_engine_name)
        self._speech_player = SpeechPlayer() if HAS_MINIAUDIO else None
        self._speech_voice_cache = {}     # 句子 -> (data, sr)，句子级缓存
        self._speech_prefetch = {}        # 正在后台合成的句子
        self._speech_lock = threading.Lock()
        self._speech_said_gen = 0
        self._speech_win = None
        self._speech_pending = []      # 合成好的音频，等主线程取（见 speech_pump）
        self._speech_warm_started = False

    def speech_warmup_async(self):
        """后台预热语音引擎（**XTTS 冷启动要 20 秒左右**，必须提前做）。

        为什么需要：XTTS 第一次合成要先加载模型（实测 21.8s），
        之后每句只要 0.6~0.7s。如果不预热，用户听到的第一句话会卡很久。

        做法：启动后台守护线程调 `warmup()`。不动 Tk（不在线程里碰 UI），
        失败也只是没预热成功，后续合成仍会按需加载。
        """
        if self._speech_warm_started:
            return
        self._speech_warm_started = True
        eng = getattr(self, '_speech', None)
        if eng is None or not hasattr(eng, 'warmup'):
            return
        if not self._speech_on:
            return

        def work():
            try:
                eng.warmup()
            except Exception:
                pass

        threading.Thread(target=work, name='speech-warmup', daemon=True).start()

    # ==================== 对外接口 ====================
    def speech_say(self, text, interrupt=True):
        """把一句话念出来（由 chat.py 的 _begin_sentence 调用）。

        这里**不做**任何合成，只投递任务；合成在后台线程里做，
        绝不能让 Tk 主线程等网络。
        """
        if not self._speech_ready():
            return
        text = (text or '').strip()
        if not text:
            return
        # 纯符号/装饰不念
        if not any(ch.isalnum() or '\u4e00' <= ch <= '\u9fff' for ch in text):
            return

        # 缓存命中：立刻播
        hit = self._speech_voice_cache.get(self._speech_key(text))
        if hit is not None:
            self._speech_play(hit, interrupt=interrupt)
            return

        # 未命中：后台合成，完成后回主线程投递播放
        gen = self._speech_new_gen() if interrupt else self._speech_said_gen
        key = self._speech_key(text)
        if key in self._speech_prefetch:
            return
        self._speech_prefetch[key] = gen

        def work(k=key, t=text, g=gen):
            try:
                data, sr = self._speech.synth(
                    t, voice=self.cfg['speech_voice'])
            except Exception:
                data, sr = None, 0
            self._speech_prefetch.pop(k, None)
            if data is None or getattr(data, 'size', 0) == 0:
                return
            self._speech_voice_cache[k] = (data, sr)
            # 交回主线程播放。
            # **不要用 self.root.after()** —— Tk 不是线程安全的，
            # 从工作线程调 after 会静默丢回调（踩过：合成成功但永远不播）。
            # 用锁保护的待播队列，由 _tick 里的 speech_pump() 取走。
            with self._speech_lock:
                self._speech_pending.append(((data, sr), g))

        threading.Thread(target=work, name='speech-synth', daemon=True).start()

    def speech_pump(self):
        """在 _tick 里调用：把后台合成好的音频交给播放器（主线程）"""
        if not self._speech_pending:
            return
        with self._speech_lock:
            items = self._speech_pending[:]
            self._speech_pending.clear()
        for pair, gen in items:
            if not getattr(self, '_speech_on', False):
                continue
            # 合成期间可能已经被打断（换了世代）或开始唱歌了
            if not self._speech_ready() and getattr(self, '_sing_state',
                                                    'off') != 'off':
                continue
            self._speech_play(pair, interrupt=False, gen=gen)

    def speech_stop(self):
        """立刻停止说话（打断/唱歌/拖动/用户开口时调用）"""
        try:
            if self._speech_player is not None:
                self._speech_player.clear()
        except Exception:
            pass

    def speech_busy(self):
        """当前这句的语音还在合成/排队/播放中吗（主线程调用）。

        气泡翻页前要看它：神经语音从合成到播完远超翻页间隔，
        不等它就会"第一页的话没说完就被下一页掐断"。
        """
        if not getattr(self, '_speech_on', False):
            return False
        if getattr(self, '_speech_prefetch', None):     # 还在后台合成
            return True
        with self._speech_lock:
            if self._speech_pending:                    # 合成好待主线程取走
                return True
        p = getattr(self, '_speech_player', None)
        return p is not None and p.busy                 # 正在播

    def _speech_ready(self):
        """现在该不该出声（综合开关、引擎可用性、歌曲/学习互斥）"""
        if not getattr(self, '_speech_on', False):
            return False
        if self._speech is None or not self._speech.available():
            return False
        if self._speech_player is None:
            return False
        # 唱歌优先：她正在唱就别念台词
        if getattr(self, '_sing_state', 'off') != 'off':
            return False
        # 用户在说话：先听
        if getattr(self, '_v_listening', False):
            return False
        # 拖动中不念
        if getattr(self, '_dragging', False):
            return False
        return True

    # ==================== 内部 ====================
    @staticmethod
    def _speech_key(text):
        return text.strip()

    def _speech_new_gen(self):
        if self._speech_player is not None:
            self._speech_said_gen = self._speech_player.new_generation()
        return self._speech_said_gen

    def _speech_play(self, pair, interrupt=True, gen=None):
        if self._speech_player is None:
            return
        data, sr = pair
        if interrupt:
            self._speech_player.clear()
            gen = self._speech_player.new_generation()
        # 把真实采样率一起交出去：设备采样率不一致时它负责重采样
        self._speech_player.submit(data, gen=gen, sr=sr)

    # ==================== 开关与音色 ====================
    def speech_toggle(self):
        """菜单：开/关说话"""
        self._speech_set(not self._speech_on)
        if self._speech_on:
            self._speak("……嗯，我说话了。听得到吗。")
        else:
            self.speech_stop()

    def _speech_set(self, on):
        self._speech_on = bool(on)
        self.cfg['speech_enabled'] = self._speech_on
        self.cfg.save()
        var = getattr(self, 'speech_var', None)
        if var is not None:
            try:
                var.set(self._speech_on)
            except Exception:
                pass
        if not self._speech_on:
            self.speech_stop()

    def speech_set_engine(self, name):
        """换引擎（'hybrid' 本地原声 / 'edge' 神经语音；菜单只有这两项）"""
        self._speech_engine_name = name
        self.cfg['speech_engine'] = name
        self.cfg.save()
        self._speech = make_engine(name)
        self._speech_voice_cache.clear()
        # 音色跟着引擎走：hybrid 的唯一音色叫 '__local__'，edge 不认识这个名字；
        # 不适配的话切过去会拿非法音色名合成失败（表现为"切了引擎她不说话"）。
        if self._speech is not None and self._speech.available():
            names = [n for n, _ in (self._speech.voices() or [])]
            if names and self.cfg['speech_voice'] not in names:
                self.cfg['speech_voice'] = names[0]
                self.cfg.save()
        if self._speech is None or not self._speech.available():
            label = getattr(self._speech, 'label', name)
            self._speak(f"……（{label} 现在还不能用。）")
            return
        self._speak("……好，换成这个声音了。")

    def speech_set_voice(self, voice):
        """换音色"""
        self.cfg['speech_voice'] = voice
        self.cfg.save()
        self._speech_voice_cache.clear()
        self._speak("……这个声音，可以吗。")

    def speech_test(self):
        """菜单：试听当前音色"""
        import datetime
        now = datetime.datetime.now()
        self.speech_say(f'……现在是 {now.hour} 点 {now.minute:02d} 分。'
                        f'我是哥伦比娅。', interrupt=True)

    # ==================== 菜单 ====================
    def _build_speech_menu(self, voice_menu):
        """填充「🎙 说话音色」子菜单：引擎 / 音色 / 试听"""
        import tkinter as tk

        def clear():
            voice_menu.delete(0, 'end')

        def rebuild():
            clear()
            eng = self._speech
            status = '可用' if (eng is not None and eng.available()) else '不可用'
            voice_menu.add_command(
                label=f'当前引擎：{getattr(eng, "label", self._speech_engine_name)}（{status}）',
                state='disabled')
            voice_menu.add_separator()
            # 引擎切换
            for e in list_engines():
                ok = e.available()
                mark = '● ' if e.name == self._speech_engine_name else '   '
                label = f'{mark}{e.label}' + ('' if ok else '（未就绪）')
                voice_menu.add_command(
                    label=label,
                    command=(lambda n=e.name: self.speech_set_engine(n))
                    if ok else None,
                    state=('normal' if ok else 'disabled'))
            voice_menu.add_separator()
            # 音色列表
            voices = eng.voices() if eng is not None else []
            cur = self.cfg['speech_voice']
            if not voices:
                voice_menu.add_command(label='（没有可选音色）', state='disabled')
            for name, desc in voices:
                mark = '● ' if name == cur else '   '
                voice_menu.add_command(
                    label=f'{mark}{desc}',
                    command=lambda n=name: self.speech_set_voice(n))
            voice_menu.add_separator()
            voice_menu.add_command(label='▶ 试听', command=self.speech_test)

        rebuild()

    def speech_show_panel(self):
        """右键「🔊 说话」面板：开关 / 引擎 / 音色 一目了然"""
        import tkinter as tk
        if getattr(self, '_speech_win', None) is not None:
            try:
                self._speech_win.lift()
                self._speech_win.focus_force()
                return
            except Exception:
                self._speech_win = None

        win = tk.Toplevel(self.root)
        win.title('说话')
        win.attributes('-topmost', True)
        win.resizable(False, False)
        x = self.root.winfo_x() + WIN_W + 10
        y = self.root.winfo_y()
        sw = self.root.winfo_screenwidth()
        if x + 340 > sw:
            x = max(0, self.root.winfo_x() - 350)
        win.geometry(f'340x300+{x}+{y}')

        outer = tk.Frame(win, bg=BUBBLE_BG)
        outer.pack(fill='both', expand=True, padx=10, pady=10)
        tk.Label(outer, text='让她说出声', bg=BUBBLE_BG, fg='#6A4BA8',
                 font=('Microsoft YaHei', 11, 'bold')).pack(anchor='w')
        tk.Label(outer, text='气泡里的每句话都会念出来（唱歌时自动让位）',
                 bg=BUBBLE_BG, fg='#8A7BA8',
                 font=('Microsoft YaHei', 8)).pack(anchor='w', pady=(2, 8))

        var = tk.BooleanVar(value=bool(self._speech_on))
        self.speech_var = var

        def on_toggle():
            self._speech_set(var.get())
            refresh()

        tk.Checkbutton(outer, text='🔊 开启', variable=var, command=on_toggle,
                       bg=BUBBLE_BG, fg=BUBBLE_FG, selectcolor='#FFFDF9',
                       activebackground=BUBBLE_BG,
                       font=('Microsoft YaHei', 9)).pack(anchor='w')

        info = tk.Label(outer, text='', bg=BUBBLE_BG, fg=BUBBLE_FG,
                        font=('Microsoft YaHei', 9), justify='left',
                        anchor='w', wraplength=300)
        info.pack(fill='x', pady=(6, 6))

        holder = tk.Frame(outer, bg=BUBBLE_BG)
        holder.pack(fill='both', expand=True)

        def refresh():
            for w in holder.winfo_children():
                w.destroy()
            eng = self._speech
            ok = eng is not None and eng.available()
            info.config(text=(f'引擎：{getattr(eng, "label", "无")}\n'
                              f'状态：{"可用" if ok else "不可用"}'
                              + (f'\n音色：{self.cfg["speech_voice"]}' if ok else '')))
            if not ok:
                tk.Label(holder, text='（引擎未就绪。Edge TTS 装在 vendor/，\n'
                                      '联网即可用；也可切到「本地语音文件」。）',
                         bg=BUBBLE_BG, fg='#8A7BA8',
                         font=('Microsoft YaHei', 8), justify='left',
                         anchor='w').pack(fill='x')
                return
            for name, desc in eng.voices():
                mark = '● ' if name == self.cfg['speech_voice'] else '○ '
                b = tk.Button(holder, text=mark + desc, anchor='w',
                              bg='#E8E0F0', fg='#6A4BA8', relief='flat',
                              font=('Microsoft YaHei', 9), cursor='hand2',
                              padx=8, pady=4,
                              command=lambda n=name: (self.speech_set_voice(n),
                                                      refresh()))
                b.pack(fill='x', pady=1)

        btns = tk.Frame(outer, bg=BUBBLE_BG)
        btns.pack(fill='x', pady=(8, 0))

        def close():
            try:
                win.destroy()
            except Exception:
                pass
            self._speech_win = None

        tk.Button(btns, text='试听', font=('Microsoft YaHei', 9),
                  bg='#E8E0F0', fg='#6A4BA8', relief='flat', cursor='hand2',
                  padx=12, pady=6,
                  command=self.speech_test).pack(side='left', padx=3)
        tk.Button(btns, text='关闭', font=('Microsoft YaHei', 9),
                  bg='#6A4BA8', fg='white', relief='flat', cursor='hand2',
                  padx=12, pady=6, command=close).pack(side='right', padx=3)

        win.protocol('WM_DELETE_WINDOW', close)
        refresh()
        self._speech_win = win
