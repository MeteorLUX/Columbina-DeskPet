# -*- coding: utf-8 -*-
"""
唱歌功能（SongMixin）
=====================
曲目挑选、询问开唱、口令控制（暂停/继续/切歌/停止）、
走路时随机哼唱、句尾淡出收尾。

依赖：
  song_engine.py  —— 解码与播放引擎
  config.py       —— SONG_* 常量与提示台词

本模块以 Mixin 形式工作，直接使用 DesktopPet 的属性/方法。
"""
import os
import queue
import random
import threading
import time

from config import *          # noqa: F401,F403  常量、文案、HAS_* 标志

try:
    from song_engine import SongEngine, _load_song, _analyze_phrases
except ImportError:
    pass


class SongMixin:
    """唱歌功能"""

    def setup_song(self):
        """唱歌状态初始化（由 DesktopPet.__init__ 调用）"""
        # ---- 唱歌状态 ----
        # 'off' 没在唱 | 'asking' 问句/答应气泡中 | 'starting' 等开唱
        # 'playing' 正在唱 | 'stopping' 等句尾 | 'paused' 停在句尾
        self.song = None
        self._song_evt_q = queue.Queue()     # 引擎线程 → 主线程的事件队列
        if HAS_SONG:
            try:
                self.song = SongEngine(
                    on_paused=lambda pos: self._song_evt_q.put(('paused', pos)),
                    on_finished=lambda: self._song_evt_q.put(('finished', None)),
                    on_error=lambda m: self._song_evt_q.put(('error', m)),
                )
            except Exception:
                self.song = None
        self._sing_state = 'off'
        self._sing_ask_t = 0.0
        self._sing_start_at = 0.0
        self._sing_resume_at = float('inf')
        self._sing_walk_grace = 0.0
        self._sing_ready_t = time.time() + SING_FIRST_DELAY
        self._sing_next_roll = 0.0
        self._song_last_path = None
        self._song_load_pending = None       # {'path':..., 'data':..., 'bounds':...}
        self._sing_manual = False            # 播放器模式（手动点歌）：列表循环 + 口令控制
        self._sing_accepted = False          # 随机询问后，用户是否已明确点「想」
        self._sing_decided = False           # 用户是否已做过选择（想/不想）
        self._sing_hold = False              # 口令"暂停"：停在句尾等口令，不自动续
        self._sing_switching = False         # 切歌中：淡出收住后直接接上一/下一首
        self._sing_stop_after = False        # 口令"停止"：淡出收住后转 off
        self._song_pl_idx = -1               # 播放器模式的曲目位置
        self._ui_q = queue.Queue()           # 收音线程 → 主线程的口令/点歌队列
        self._song_win = None                # 唱歌面板 Toplevel

    def song_show_panel(self):
        """右键菜单「🎵 唱歌」：弹出月夜风格唱歌控制面板"""
        if self._song_win is not None:
            try:
                self._song_win.lift()
                self._song_win.focus_force()
            except Exception:
                self._song_win = None
            else:
                return

        def _closed():
            self._song_win = None
        self._song_win = self._moon_panel('唱歌', [
            ('请她唱首歌', lambda: self._handle_song_request(None)),
            ('上一首', lambda: self._song_cmd_apply('prev')),
            ('下一首', lambda: self._song_cmd_apply('next')),
            ('暂停 / 继续', self._song_toggle_pause),
            ('先别唱了', self._stop_song_manual),
        ], on_close=_closed)


    def _song_ok(self):
        return HAS_SONG and self.song is not None

    def _set_vad_suspend(self, on):
        """唱歌时暂停全双工的聆听，免得把她的歌声误当成人声"""
        if self.voice is not None:
            try: self.voice.suspend_vad = bool(on)
            except Exception: pass

    def _song_playlist(self):
        """music 目录下的全部歌曲（按文件名排序，作为播放器列表）"""
        try:
            files = [os.path.join(MUSIC_DIR, f) for f in os.listdir(MUSIC_DIR)
                     if f.lower().endswith(SONG_EXTS)]
        except Exception:
            return []
        return sorted(files)

    def _pick_song(self):
        """播放器模式按列表序取歌；随机模式随机选（尽量避开上一首）"""
        files = self._song_playlist()
        if not files:
            return None
        if self._sing_manual and 0 <= self._song_pl_idx < len(files):
            return files[self._song_pl_idx]
        pool = [f for f in files if f != self._song_last_path] or files
        return random.choice(pool)

    # ---------- 触发入口 ----------
    def _ask_to_sing(self):
        """随机触发：先冒气泡问一句，气泡打完弹出「想 / 不想」选项。

        重要：**必须等用户明确点「想」才开唱**。
        以前这里是"气泡说完就自动开唱"，用户没选也会唱 —— 已改掉。
        """
        self._sing_state = 'asking'
        self._sing_ask_t = time.time()
        self._sing_accepted = False          # 是否已明确答应
        self._sing_decided = False           # 还没做选择
        self._sing_manual = False            # 随机询问（手动点歌会给 True）
        self._hide_sing_options()
        self._speak(SING_ASK_LINE, interrupt=True)

    # ---------- 询问选项「想 / 不想」 ----------
    def _show_sing_options(self):
        if self._sing_opts_shown:
            return
        self._sing_opts_shown = True
        self.sing_choice_holder.place(x=BUBBLE_X, y=BUBBLE_Y + BUBBLE_H + 2)
        self.sing_choice_holder.lift()

    def _hide_sing_options(self):
        if not self._sing_opts_shown:
            return
        self._sing_opts_shown = False
        self.sing_choice_holder.place_forget()

    def _sing_choice(self, want):
        """点了「想」→ 开唱；点了「不想」→ 一段时间内不再随机问"""
        if self._sing_state != 'asking':
            self._hide_sing_options()
            return
        self._hide_sing_options()
        self._reset_idle_timer()
        self._sing_decided = True           # 选过了：不要再把选项弹回来
        if want:
            self._sing_accepted = True      # 明确答应，允许开唱
            # 已经答应了，就不必再等问句气泡播完（它被拆成好几句，
            # 要十几秒）——直接收掉气泡进入开唱，否则点完像没反应。
            self._hide_bubble()
            self.dlg_queue = []
            self.dlg_state = 'idle'
            self._on_dialog_done()
        else:
            self._sing_accepted = False
            self._sing_state = 'off'
            self._sing_ready_t = time.time() + SING_DECLINE_BLOCK
            self._speak(SING_DECLINE_LINE, interrupt=True)

    def _handle_song_request(self, text=None):
        """手动点歌（打字 / 语音 / 菜单共用）→ 进入播放器模式：
        列表循环连唱，可用"上一首/下一首/暂停/继续/停止"口令控制"""
        self._reset_idle_timer()
        if not self._song_ok():
            self._speak("……（这里没有歌声。music 文件夹或 miniaudio 还没准备好。）")
            return
        st = self._sing_state
        if st in ('asking', 'starting'):
            return
        if st == 'playing':
            if not self._sing_manual:
                self._sing_manual = True     # 随机唱被点名 → 转为播放器模式
            self._speak(random.choice(SING_BUSY_LINES))
            return
        if st in ('paused', 'stopping'):
            # 正停在句尾：答应一声，提前续唱
            self._speak(random.choice(SING_HOLD_LINES))
            self._sing_hold = False
            self._sing_stop_after = False       # 若正要收场，改为继续唱这首
            self._sing_resume_at = min(self._sing_resume_at, time.time() + 1.5)
            return
        # 静止时：先播放气泡回应再开唱；移动中：同一流程，气泡说完边走边唱
        self._sing_manual = True
        self._sing_hold = self._sing_switching = self._sing_stop_after = False
        files = self._song_playlist()
        if files:
            pool = [i for i, f in enumerate(files) if f != self._song_last_path] \
                   or list(range(len(files)))
            self._song_pl_idx = random.choice(pool)
        self._sing_state = 'asking'
        self._sing_ask_t = time.time()
        self._speak(random.choice(SING_ACK_LINES), interrupt=True)

    # ---------- 播放器口令 ----------
    def _song_cmd_match(self, text):
        """把一句话解析成播放器口令：stop/pause/next/prev/resume 或 None"""
        t = text or ''
        if SONG_STOP_PAT.search(t):    return 'stop'
        if SONG_PAUSE_PAT.search(t):   return 'pause'
        if SONG_NEXT_PAT.search(t):    return 'next'
        if SONG_PREV_PAT.search(t):    return 'prev'
        if SONG_RESUME_PAT.search(t):  return 'resume'
        return None

    def _drain_ui_q(self):
        """收音线程抛上来的播放器口令 / 语音点歌 / 学习指令（_tick 里消费）"""
        while True:
            try:
                kind, arg = self._ui_q.get_nowait()
            except queue.Empty:
                return
            if kind == 'song_cmd':
                cmd, text = arg if isinstance(arg, tuple) else (arg, '')
                # 语音口令被门禁吞掉（不走正常回复流程），这里代为收尾：
                # 解除聆听站定（否则"继续"会被 _voice_busy 永远挡住）、
                # 把识别到的口令显示一下再定时清掉（否则绿框一直卡着）。
                self._v_listening = False
                if text:
                    self._voice_show(f"🎤 {text}")
                    self._voice_clear()
                self._song_cmd_apply(cmd)
            elif kind == 'song_req':
                self._v_user_final_song(arg)
            elif kind == 'study_cmd':
                cmd, text = arg if isinstance(arg, tuple) else (arg, '')
                self._v_listening = False
                if text:
                    self._voice_show(f"🎤 {text}")
                    self._voice_clear()
                self._study_cmd_apply(cmd)
            elif kind == 'launch_req':
                # 语音说"打开 xxx"：显示识别结果后弹确认框（不直接打开）
                text = arg if isinstance(arg, str) else ''
                self._v_listening = False
                if text:
                    self._voice_show(f"🎤 {text}")
                    self._voice_clear()
                self.launch_handle_text(text)
            elif kind == 'facts_req':
                # 语音问时间/日期/天气：本地精准回答
                text = arg if isinstance(arg, str) else ''
                self._v_listening = False
                if text:
                    self._voice_show(f"🎤 {text}")
                    self._voice_clear()
                ans = self.facts_answer(text)
                if ans:
                    self._speak(ans)

    def _song_cmd_applicable(self, cmd):
        """这个口令在当前状态下是否可执行（决定打字口令要不要拦截）"""
        st = self._sing_state
        if cmd in ('next', 'prev', 'stop'):
            return st in ('playing', 'stopping', 'paused')
        if cmd == 'pause':
            return st == 'playing'
        if cmd == 'resume':
            return st == 'paused'
        return False

    def _song_cmd_apply(self, cmd):
        """执行播放器口令（主线程）"""
        if not self._song_ok():
            return
        st = self._sing_state
        if cmd in ('next', 'prev'):
            if st in ('playing', 'stopping', 'paused'):
                self._song_switch(1 if cmd == 'next' else -1)
        elif cmd == 'pause':
            if st == 'playing':
                self._sing_hold = True
                self._begin_song_stop()          # 这句唱完渐弱收住
        elif cmd == 'resume':
            if st == 'paused' and self._sing_manual:
                self._sing_hold = False
                self._sing_resume_at = time.time()   # 下个 tick 续唱（若在忙则忙完续）
                # 她走起来，边走边唱
                self._walk_next_trigger = min(self._walk_next_trigger,
                                              time.time() + 1.0)
        elif cmd == 'stop':
            self._song_stop_by_cmd()

    def _song_toggle_pause(self):
        """菜单：播放中→暂停；暂停中→继续"""
        if self._sing_state == 'playing':
            self._song_cmd_apply('pause')
        elif self._sing_state == 'paused':
            self._song_cmd_apply('resume')

    def _song_switch(self, delta):
        """上一首 / 下一首：播放中快速淡出后接新歌；暂停中直接换"""
        files = self._song_playlist()
        if not files:
            return
        if not self._sing_manual:
            self._sing_manual = True             # 随机唱中切歌 → 转为播放器模式
            try:
                self._song_pl_idx = files.index(self._song_last_path)
            except ValueError:
                self._song_pl_idx = 0
        self._song_pl_idx = (self._song_pl_idx + delta) % len(files)
        self._sing_hold = False
        if self._sing_state == 'paused':
            self._sing_state = 'starting'
            self._sing_start_at = time.time() + 0.4
        elif self._sing_state in ('playing', 'stopping'):
            if self._sing_state == 'playing':
                self._sing_state = 'stopping'
                self.song.quick_stop()           # 快速淡出，不用等句尾
            self._sing_switching = True          # 收住后自动接新歌

    def _song_stop_by_cmd(self):
        """口令/菜单"停止"：快速淡出收住 → 退出播放器模式"""
        st = self._sing_state
        if st == 'off':
            return
        if st in ('asking', 'starting'):
            self._song_load_pending = None
            self._finish_manual_stop()
            return
        if st == 'paused':
            self.song.hard_stop()
            self._finish_manual_stop(say=True)
            return
        if st == 'playing':                      # playing / stopping
            self._sing_state = 'stopping'
            self.song.quick_stop()
        self._sing_stop_after = True             # 淡出收住后转 off

    def _finish_manual_stop(self, say=False):
        self._sing_state = 'off'
        self._sing_manual = False
        self._sing_hold = False
        self._sing_switching = False
        self._sing_stop_after = False
        self._sing_resume_at = float('inf')
        self._sing_ready_t = time.time() + 10.0
        self._set_vad_suspend(False)
        if say:
            self._speak("……好。")

    def _stop_song_manual(self):
        """菜单"先别唱了"：等同于口令"停止" """
        self._song_stop_by_cmd()

    # ---------- 开唱 / 续唱 / 停唱 ----------
    def _on_dialog_done(self):
        """一整段气泡说完（淡出结束）的钩子。

        只有**明确答应过**（或手动点歌）才开唱；
        随机询问后用户没选 -> 什么都不做，继续等，超时则由 _update_song
        统一处理成"默认不唱"。
        """
        if self._sing_state != 'asking':
            return
        if not (self._sing_manual or self._sing_accepted):
            return                      # 还没答应：不动，等用户选或等超时
        self._hide_sing_options()
        self._sing_state = 'starting'
        self._sing_start_at = time.time() + 0.5     # 气泡说完短暂停顿
        # 恢复走动，让她边走边唱
        if self.cfg['walk_enabled'] and not self._dragging:
            self._start_walk_target()

    def _start_walk_target(self):
        sw = self.root.winfo_screenwidth()
        x = self.root.winfo_x()
        target = random.randint(20, sw - WIN_W - 20)
        if abs(target - x) >= 100:
            self.walk_target_x = target
            self._walk_base_y = self.root.winfo_y()
            self._on_walk_start()
        else:
            self._walk_next_trigger = time.time() + random.uniform(3, 6)

    def _start_song(self):
        if self._sing_state != 'starting' or self._song_load_pending is not None:
            return
        self._sing_start_at = float('inf')          # 防止每个 tick 重复进
        path = self._pick_song()
        if not path:
            self._finish_manual_stop()
            self._speak("……（music 文件夹里还没有歌。）")
            return
        self._song_load_pending = {'path': path}
        def work():
            data = _load_song(path)
            bounds = _analyze_phrases(data) if data is not None else []
            self._song_load_pending = {'path': path, 'data': data, 'bounds': bounds}
        threading.Thread(target=work, daemon=True).start()

    def _on_song_loaded(self, path, data, bounds):
        if self._sing_state != 'starting':
            return                      # 等待期间被打断/取消
        if data is None:
            self._finish_manual_stop()
            self._speak("……（这首歌我唱不了。）")
            return
        self._song_last_path = path
        self.song.start(data, bounds, pos=0.0, fade=SONG_FADE_IN)
        self._sing_state = 'playing'
        self._sing_walk_grace = time.time() + 2.5
        self._set_vad_suspend(not self._sing_manual)
        # 唱歌优先：把正在播的说话语音掐掉，别和歌声重叠
        try:
            self.speech_stop()
        except Exception:
            pass

    def _begin_song_stop(self):
        """请求句尾停：引擎会把当前这句唱完再渐弱收住"""
        if self._sing_state != 'playing':
            return
        self._sing_state = 'stopping'
        self.song.stop_at_boundary()

    def _on_song_paused(self, resume_pos):
        """引擎淡出收住了（引擎线程回调，经队列抛回主线程）"""
        self._set_vad_suspend(False)
        if self._sing_state not in ('stopping', 'playing'):
            return
        now = time.time()
        if self._sing_stop_after:                # 口令"停止"：就此收场
            self._finish_manual_stop(say=True)
            return
        if self._sing_switching:                 # 切歌：直接接上一/下一首
            self._sing_switching = False
            self._sing_state = 'starting'
            self._sing_start_at = now + 0.4
            return
        self._sing_state = 'paused'
        if self._sing_manual and self._sing_hold:    # 口令"暂停"：停住等口令
            self._sing_resume_at = float('inf')
            # 只停歌，不冻人：她照常待机/走动，说「继续」才续唱
            self._speak("♪ 先停在这儿。说「继续」，我就接着唱。")
            return
        self._sing_resume_at = now + random.uniform(SING_GAP_MIN, SING_GAP_MAX)
        # 让下一次走动早点来，好把歌接上（3~5 秒，不用等平时那么久的停顿）
        if self.cfg['walk_enabled']:
            self._walk_next_trigger = min(self._walk_next_trigger,
                                          now + random.uniform(SING_GAP_MIN, SING_GAP_MAX))

    def _on_song_finished(self):
        self._set_vad_suspend(False)
        if self._sing_state == 'off':
            return
        if self._sing_manual and self._sing_hold:
            # 口令"暂停"后恰好唱到曲尾：同样停住，不接下一首（也不冻结走动）
            self._sing_state = 'paused'
            self._sing_resume_at = float('inf')
            return
        if self._sing_manual:
            # 播放器模式：列表循环，自动接下一首
            files = self._song_playlist()
            if files:
                try:
                    cur = files.index(self._song_last_path) if self._song_last_path \
                          else self._song_pl_idx
                except ValueError:
                    cur = self._song_pl_idx
                if not (0 <= cur < len(files)):
                    cur = 0
                self._song_pl_idx = (cur + 1) % len(files)
            self._sing_state = 'starting'
            self._sing_start_at = time.time() + 2.0      # 歌与歌之间歇口气
            return
        self._sing_state = 'off'
        self._sing_ready_t = time.time() + SING_COOLDOWN
        self._speak(random.choice(SING_DONE_LINES))

    def _on_song_error(self, msg):
        self._set_vad_suspend(False)
        # 出错后不关 manual——播放器模式保持，让用户能重试（重新点歌/换一首）
        self._sing_state = 'off'
        self._sing_hold = False
        self._sing_switching = False
        self._sing_stop_after = False
        self._sing_resume_at = float('inf')
        self._sing_ready_t = time.time() + SING_COOLDOWN
        self._speak(f"……抱歉，歌声好像出了点问题。{msg[:30]}")

    def _resume_song(self):
        if self._sing_state != 'paused' or not self._song_ok():
            return
        # 口令"暂停"停住的：任何自动路径（走动/计时）都不得续唱，只认"继续"口令
        if self._sing_manual and self._sing_hold:
            return
        # 正忙着说话 / 被拖拽 / 正在生成回复时先不续，
        # _update_song 每个 tick 都会再试，忙完自然接上。
        #
        # 注意：**不要**把 current_pose == 'sleep' 当阻塞条件！
        # 她"睡着"只在 _sing_state=='off' 时才发生，唱歌期间不会睡；
        # 而 PHYS_FALL_POSE 也是 'sleep' —— 也就是说你拖她一下、松手让她
        # 往下掉，姿态就变成 sleep，续唱就会被永久挡住，直到你再次拖动
        # 改变姿态。这正是"放一下没声音，拖动又有声音"的根因。
        #
        # manual 播放器模式下 VAD 常开（为了听口令），歌声残响会让
        # _voice_busy() 一直 True——这不是真的有人在说话，别挡住续唱。
        voice_busy = self._voice_busy()
        if self._sing_manual:
            voice_busy = False
        if (self._dragging or self.is_generating
                or voice_busy or self._rx_active
                or self.dlg_state not in ('idle', 'fading')):
            return
        # 关键：必须确认**引擎**也已经真正停在 paused。
        # 引擎收到 'paused' 事件时内部可能还处在 draining/stopping，
        # 而 SongEngine._h_resume 有 `if self.state != 'paused': return` ——
        # 这时候发 resume 会被静默吃掉，她以为在续唱、实际永远静音
        # （"放一下没声音，拖动又有声音"的元凶之一）。
        # 所以引擎没就绪就返回，让 _update_song 下个 tick 重试。
        st = getattr(self.song, 'state', None)
        if st is not None and st not in ('paused', 'idle'):
            return
        self._sing_state = 'playing'
        self._sing_walk_grace = time.time() + 2.5
        self.song.resume()
        self._set_vad_suspend(not self._sing_manual)

    def _on_walk_start(self):
        """走动开始（含停后再走）：若歌正停在句尾，接着唱"""
        if self._sing_state == 'paused':
            self._resume_song()

    # ---------- 主时钟里的唱歌状态机 ----------
    def _update_song(self, now):
        if not self._song_ok():
            return
        # 先处理引擎抛回来的事件（paused / finished / error）
        while True:
            try:
                evt, arg = self._song_evt_q.get_nowait()
            except queue.Empty:
                break
            if evt == 'paused':
                self._on_song_paused(arg)
            elif evt == 'finished':
                self._on_song_finished()
            elif evt == 'error':
                self._on_song_error(arg)
        st = self._sing_state
        if st == 'asking':
            asked_at = self._sing_ask_t
            # 随机询问：问题打完字后弹出「想 / 不想」（手动点歌的应答不给选项）
            if not self._sing_manual:
                if not self._sing_decided and (
                        self.dlg_state in ('waiting', 'fading')
                        or now > asked_at + SING_ASK_HOLD_SEC):
                    # 气泡已经开始播、或者已经等够 HOLD 秒 —— 都该把选项摆出来，
                    # 别让气泡一淡出选项就跟着消失。
                    # 注意 _sing_decided：选过之后绝不能再弹回来，
                    # 否则气泡永远等不到淡出，_on_dialog_done 也就不触发，
                    # 点了「想」反而卡住不开唱。
                    self._show_sing_options()
                # 已经开始等选择了吗？（气泡播完之后的等待窗口才开始算"默认不唱"）
                waiting_since = max(asked_at, self.dlg_fade_start or asked_at)
                if not self._sing_accepted and now > waiting_since + SING_ASK_TIMEOUT:
                    # 超时没选 -> **默认不唱**（以前这里会推进到开唱）
                    self._hide_sing_options()
                    self._sing_state = 'off'
                    self._sing_ready_t = now + SING_TIMEOUT_BLOCK
                    self._speak(SING_TIMEOUT_LINE, interrupt=True)
            return
        if st == 'starting':
            if now >= self._sing_start_at:
                self._start_song()
            p = self._song_load_pending
            if p is not None and 'data' in p:
                self._song_load_pending = None
                self._on_song_loaded(p['path'], p['data'], p['bounds'])
            return
        if st == 'paused':
            if now >= self._sing_resume_at:
                self._resume_song()
            return
        if st == 'off':
            # 随机想唱：只在走路、且全双工没开时掷骰
            if (self.cfg['walk_enabled'] and not self.voice_on
                    and self.walk_target_x is not None
                    and now >= self._sing_ready_t and now >= self._sing_next_roll
                    and self.dlg_state == 'idle' and not self.is_generating
                    and not self._dragging and self.current_pose != 'sleep'
                    and not self._voice_busy() and not self._rx_active):
                self._sing_next_roll = now + SING_ROLL_SEC
                if random.random() < SING_PROB:
                    self._ask_to_sing()
            return
        if st == 'playing':
            # 开始说话/生成/拖拽 → 把这句唱完就停一停（忙完自动续）
            busy = (self._dragging or self.is_generating
                    or self.dlg_state not in ('idle', 'fading'))
            if self._sing_manual:
                # 播放器模式：站着也照唱（音乐播放器），只有忙时才停一停。
                # **不看姿态**：清空姿势/下落姿态都是 'sleep'，而那是物理
                # 状态，和"要不要唱歌"无关。把它算进 busy 会导致拖她一下
                # 就停唱、且再也不会自己续上（见 _resume_song 的注释）。
                if busy:
                    self._begin_song_stop()
            else:
                # 随机走走唱唱：语音交互或停下脚步 → 同样唱完这句再停
                busy = busy or self._voice_busy() or self._rx_active
                not_walking = (self.cfg['walk_enabled']
                               and self.walk_target_x is None
                               and now >= self._sing_walk_grace)
                # 随机模式才有"睡着了不唱"的语义（她本来就不该在睡觉时唱歌）
                busy = busy or self.current_pose == 'sleep'
                if busy or not_walking:
                    self._begin_song_stop()

    # ====================================================
