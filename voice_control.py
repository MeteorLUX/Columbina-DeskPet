# -*- coding: utf-8 -*-
"""
全双工语音对话（VoiceMixin）
============================
只负责"听"这条线：
  常驻收音 -> VAD 断句 -> 流式识别 -> 流式回复 -> 逐句打字上气泡
以及打断（用户再开口即中止上一轮）。

她只用气泡出字、不出声，所以不需要回声消除。

本模块以 Mixin 形式工作，直接使用 DesktopPet 的属性/方法
（self.root / self.bubble / self._set_pose / self._speak 等）。
"""
import os
import threading
import time

import tkinter as tk

from config import *          # noqa: F401,F403  常量与文案

try:
    import numpy as np
    import soundcard as sc
    HAS_MIC = True
except ImportError:
    HAS_MIC = False

try:
    import sherpa_onnx
    HAS_SHERPA = True
except ImportError:
    HAS_SHERPA = False

# 全双工语音模块（常驻收音 + VAD + 流式识别 + 流式回复）
try:
    from voice_engine import FullDuplexVoice, VoiceCallbacks
    HAS_FULL_DUPLEX = True
except ImportError:
    HAS_FULL_DUPLEX = False


class VoiceMixin:
    """全双工语音功能"""

    def setup_voice(self):
        """语音状态初始化（由 DesktopPet.__init__ 调用）"""
        # ---- 全双工语音（默认关闭，由右键菜单开关） ----
        self.voice = None            # FullDuplexVoice 实例，首次开启时创建
        self.voice_on = False
        self._voice_user_text = ''   # 保存这一轮识别到的用户原话
        # 流式回复的"攒字 + 逐句打字"缓冲
        self._rx_active = False      # 是否在接收/播放一轮回复
        self._rx_buf = ''            # 已收到的回复全文
        self._rx_pos = 0             # 已经切出去打字的字符位置
        self._rx_more = True         # 后面是否还会来字
        self._rx_sent = ''           # 当前正在打字的这一句
        self._rx_shown = 0           # 当前这句已打出的字数
        self._rx_queue = []          # 待打的完整句子
        self._rx_delay_ms = VOICE_RX_TICK_MS   # 打字机间隔；测试可设 None 手动驱动
        self._voice_last_update = 0.0          # 识别显示区最后更新时间
        self._v_listening = False              # 用户此刻是否正在说话
        self._v_utt = 0                        # 话头序号：兜底释放聆听标志用


    def _toggle_full_duplex(self):
        if not HAS_FULL_DUPLEX:
            self._speak("……（缺少 voice_engine.py，我听不到你。）")
            return
        if not HAS_MIC:
            self._speak("……（没有安装录音依赖 soundcard，我听不到你。）")
            return
        if not HAS_SHERPA:
            self._speak("……（没有安装 sherpa-onnx，我听不懂你。）")
            return

        # 惰性创建
        if self.voice is None:
            def _cb(fn):
                """把语音回调安全地抛回 Tk 主线程，并把异常记到日志。
                原来 lambda 里出错会被 Tk 静默吞掉，所以看不到任何报错。"""
                def _run(*args):
                    def _go():
                        try:
                            fn(*args)
                        except Exception:
                            self._voice_log_exc(fn.__name__)
                    try:
                        self.root.after(0, _go)
                    except Exception:
                        self._voice_log_exc(fn.__name__ + '/after')
                return _run

            self._cb_v_user_final = _cb(self._v_user_final)
            self.voice = FullDuplexVoice(
                BASE_DIR,
                callbacks=VoiceCallbacks(
                    on_partial=_cb(self._v_partial),
                    on_user_final=self._v_user_final_gate,
                    on_speech_start=_cb(self._v_speech_start),
                    on_speech_end=_cb(self._v_speech_end),
                    on_reply_delta=_cb(self._v_delta),
                    on_reply_done=_cb(self._v_done),
                    on_error=_cb(self._v_error),
                ),
                api_key=current_api_key(),
                system_prompt=self._build_system_prompt(),
                api_url=DEEPSEEK_API_URL,
            )

        if self.voice_on:
            ok, msg = self.voice.stop()
            self.voice_on = False
            self._v_listening = False
            self._speak("好，我不用月距力了。")
        else:
            # 每次开启都刷新提示词与 Key，这样改完称呼/好感度立即生效
            self.voice.system_prompt = self._build_system_prompt()
            self.voice.api_key = current_api_key()
            ok, msg = self.voice.start()
            self.voice_on = bool(ok)
            if ok:
                self._reset_idle_timer()
                # 开启后她照常自己活动（走动 + 原有待机轮换），
                # 只有真正说话/回话时才站定。
                self._set_pose('smile')
                self._speak("……嗯，我在听。你随时可以说。")
            else:
                self._speak(f"……（{msg}）")

    # ---------- 语音识别显示区（角色下方，与她的气泡分开） ----------
    def _voice_show(self, text):
        """在角色下方显示识别到的用户话。
        框只有两行大，太长就只留尾巴——最新识别到的字要永远看得见。"""
        self._voice_last_update = time.time()
        if text and len(text) > 22:
            text = '…' + text[-21:]
        self.voice_label.config(text=text)
        if not self._voice_shown:
            # 与输入框同一条位置，冲突时先把输入框收起
            if self._input_visible:
                self.input_holder.place_forget()
                self._input_visible = False
            self.voice_holder.place(x=INPUT_X, y=INPUT_Y, width=INPUT_W, height=INPUT_H)
            self.voice_holder.lift()
            self._voice_shown = True

    def _voice_clear(self):
        """隔一会儿再清掉，让用户看得见自己说了什么"""
        def _do():
            # 期间若又识别出新内容，本次就不清（新的 _voice_clear 会负责）
            if time.time() < self._voice_last_update + 3.4:
                return
            self.voice_holder.place_forget()
            self.voice_label.config(text="")
            self._voice_shown = False
        self.root.after(3500, _do)

    # ---------- 以下是语音回调，全部已在 Tk 主线程执行 ----------
    def _voice_log_exc(self, where):
        """把语音链路的异常写进日志文件（Tk 默认会静默吞掉回调异常）"""
        import traceback
        try:
            with open(os.path.join(BASE_DIR, 'voice-debug.log'), 'a', encoding='utf-8') as f:
                f.write(f"\n[异常] {time.strftime('%H:%M:%S')} 于 {where}\n")
                traceback.print_exc(file=f)
        except Exception:
            pass
        try:
            print(f'[voice 异常] {where}')
            traceback.print_exc()
        except Exception:
            pass

    def _voice_log(self, msg):
        """把语音链路的关键状态写进日志，方便定位"不回复"这类问题"""
        try:
            with open(os.path.join(BASE_DIR, 'voice-debug.log'), 'a', encoding='utf-8') as f:
                f.write(f"[{time.strftime('%H:%M:%S')}] {msg}\n")
        except Exception:
            pass

    def _emotion(self, text):
        """判断她这句话的情绪：'negative' / 'positive' / 'neutral'
        负面优先匹配——她说难过的话就该用难过的动作。"""
        t = (text or '').replace('…', '').replace(' ', '').replace('\n', '')
        if not t:
            return 'neutral'
        if any(w in t for w in EMO_NEGATIVE):
            return 'negative'
        if any(w in t for w in EMO_POSITIVE):
            return 'positive'
        return 'neutral'

    def _is_question(self, text):
        """判断用户这句话是不是问句（决定聆听时用不用 think）"""
        t = (text or '').strip()
        if not t:
            return False
        if any(m in t for m in QUESTION_MARKS):
            return True
        return any(w in t for w in QUESTION_WORDS)

    def _listen_pose(self, text):
        """按用户说的话选聆听姿态：问句用 think，其余正常对话用 smile"""
        self._set_pose('think' if self._is_question(text) else 'smile')

    def _voice_busy(self):
        """是不是"正在跟用户交互"（该站定）：
        用户正在说话，或她正在回话。她没在交互时应当能自己走动。
        注意：think 是"听到问题/有疑问"的专属动作，
        这里的空闲姿态由原有的待机轮换负责，不再另搞一套。"""
        return bool(getattr(self, '_v_listening', False)) or self._rx_active

    def _finish_pose(self):
        """她整轮说完之后的收尾动作：
        负面情绪 -> walk1（戴眼罩那个安静姿态）
        高兴的话 -> happy，然后回到 smile
        其余     -> smile"""
        emo = self._emotion(self._rx_buf)
        if emo == 'negative' and 'walk1' in self.images:
            self._set_pose('walk1')
        elif emo == 'positive':
            self._set_pose('happy')
            self.root.after(1200, lambda: self._set_pose('smile'))
        else:
            self._set_pose('smile')

    def _voice_snap(self):
        """状态快照。用 getattr 兜底：日志绝不能因为缺字段而把功能搞挂。"""
        g = lambda n, d='': getattr(self, n, d)
        try:
            bubble = self.bubble_label.cget('text')[:20]
        except Exception:
            bubble = '?'
        return (f"state={g('dlg_state')} rx_active={g('_rx_active')} "
                f"rx_more={g('_rx_more')} pos={g('_rx_pos')} "
                f"queue={len(g('_rx_queue', []))} sent={str(g('_rx_sent'))[:12]!r} "
                f"bubble={bubble!r} user={str(g('_voice_user_text'))[:16]!r}")

    def _v_speech_start(self):
        """用户开口：打断她正在说/正在打字的回复，切回聆听姿态"""
        if self._sing_manual and self._sing_state in ('asking', 'starting',
                                                      'playing', 'stopping'):
            return      # 播放器模式开唱前后/演唱中：歌声会触发 VAD，忽略"开口"，只认最终口令
        self._reset_idle_timer()
        # 用户在说话：先掐掉我们的语音，专注听
        try:
            self.speech_stop()
        except Exception:
            pass
        # 她刚问"想听我唱歌吗"就被打断：他在说话，先不唱了
        if self._sing_state == 'asking':
            self._hide_sing_options()
            self._sing_state = 'off'
            self._sing_ready_t = time.time() + 30.0
        self.dlg_queue = []
        # 打断：停掉逐句打字，气泡让位
        self._rx_active = False
        self._rx_buf = ''
        self._rx_queue = []
        self.dlg_state = 'idle'
        self._hide_bubble()
        # 听的时候：问句才用 think，其余正常对话就用 smile
        self._v_utt += 1
        self._v_listening = True
        self.walk_target_x = None       # 立刻停下正在走的路，站定听我说
        self._listen_pose(self._voice_user_text)
        self._voice_show('')
        self._voice_log('speech_start ' + self._voice_snap())

    def _v_partial(self, text):
        """识别中间结果：实时显示到角色下方（不进她的气泡）"""
        if self._sing_manual and self._sing_state in ('asking', 'starting',
                                                      'playing', 'stopping'):
            return      # 播放器模式开唱前后/演唱中：忽略歌词串扰的中间结果
        if not text:
            return
        self._voice_show(f"🎤 {text}")
        # 边听边判断：一旦听出是问句就换成 think，否则保持 smile
        if not self._rx_active:
            self._listen_pose(text)

    def _v_user_final_gate(self, text):
        """语音"这句话说完"的入口（在收音线程里同步执行）：
        播放器模式先拦口令（切歌/暂停/继续/停止），再拦点歌；
        演唱中与开唱前后吞掉其它话语（多为歌声串进麦克风的假识别）；
        暂停中等口令时也允许正常聊天。命中即返回 True，不再起 LLM 回复。"""
        try:
            t = text or ''
            if self._sing_manual and self._sing_state in (
                    'asking', 'starting', 'playing', 'stopping', 'paused'):
                cmd = self._song_cmd_match(t)
                if cmd is not None:
                    if self.voice is not None:
                        self.voice.cancel_generation()   # 可能已有早启的回复线程，毙掉
                    # 带上原文：主线程要显示识别结果并收尾聆听标志
                    self._ui_q.put(('song_cmd', (cmd, t)))
                    return True
                if self._sing_state != 'paused':
                    return True      # 演唱中/开唱前后吞掉其它话语
                # 暂停中：非口令照常走对话流程
            if SONG_REQ_PAT.search(t):
                if self.voice is not None:
                    self.voice.cancel_generation()   # 可能已有早启的回复线程，毙掉
                self._ui_q.put(('song_req', t))
                return True
            # 学习指令拦截：开始/暂停/结束/查看记录
            study_cmd = self._study_cmd_match(t)
            if study_cmd is not None:
                if self.voice is not None:
                    self.voice.cancel_generation()
                self._ui_q.put(('study_cmd', (study_cmd, t)))
                return True
            # 打开应用/网址：语音也要先经确认框（launcher 会弹窗），
            # 所以这里只把意图抛回主线程，不直接打开
            from banter import recognise_intent
            if recognise_intent(t):
                if self.voice is not None:
                    self.voice.cancel_generation()
                self._ui_q.put(('launch_req', t))
                return True
            # 时间/日期/天气：语音也走本地精准回答（0 延迟）
            # 注意：必须先排掉唱歌/学习口令！播放器模式暂停时，指令会
            # 掉出上面的口令分支走到这里，而"继续"这类短词可能被
            # facts_answer 的宽松正则误吞（"继续"-> 日期正则）。
            # 后果很严重：_sing_hold 永远清不掉，歌再也续不上。
            if not (self._song_cmd_match(t) or self._study_cmd_match(t)):
                if self.facts_answer(t):
                    if self.voice is not None:
                        self.voice.cancel_generation()
                    self._ui_q.put(('facts_req', t))
                    return True
        except Exception:
            pass
        cb = getattr(self, '_cb_v_user_final', None)
        if cb is not None:
            cb(text)
        else:
            self._v_user_final(text)
        return None

    def _v_user_final_song(self, text):
        """语音点歌：显示识别结果，清掉可能被早启回复弄脏的气泡，然后点歌"""
        self._v_listening = False            # 话头已被点歌流程接管，解除聆听站定
        self._voice_show(f"🎤 {text}")
        self._voice_clear()
        self._rx_active = False
        self._rx_buf = ''
        self._rx_queue = []
        if self.dlg_state in ('vtyping', 'vwait'):
            self.dlg_state = 'idle'
            self._hide_bubble()
        self._handle_song_request(text)

    def _v_user_final(self, text):
        """这句话说完，等她的回复"""
        self._voice_user_text = text
        self._voice_show(f"🎤 {text}")
        # 他说的话决定聆听姿态：问句 think，其余 smile
        self._listen_pose(text)
        self._voice_clear()
        self._voice_log(f'user_final {text!r} ' + self._voice_snap())

        # 稍久没等到回复就解除站定，免得她一直卡着不动
        self.root.after(15000, self._voice_release_busy)

    def _voice_release_busy(self):
        """兜底：回复迟迟不来时解除站定，让她能继续走动"""
        if not self._rx_active:
            self._v_listening = False

    def _v_speech_end(self):
        if self._sing_manual and self._sing_state in ('asking', 'starting',
                                                      'playing', 'stopping'):
            return      # 播放器模式下歌声触发的假"说完"不改变姿态
        # 说完了，姿态保持上面的判断结果，不要走动
        # 注意：这里不解除站定标志——她还要思考并回话，
        # 真正"忙完"是在整轮回复淡出结束时。
        self._listen_pose(self._voice_user_text)
        self._voice_log('speech_end ' + self._voice_snap())
        # 说完后识别框到点自己收掉；太短被丢弃（无 final）时尤需兜底
        self._voice_clear()
        utt = self._v_utt
        self.root.after(4000, lambda u=utt: self._voice_release_listening(u))

    def _voice_release_listening(self, utt):
        """兜底：说完话却没等到回复时，解除'我在听'的站定标志。
        也防止短句被丢弃（无 final）后 _v_listening 永远卡着。"""
        if utt != self._v_utt:
            return       # 新的话头已经进来，不干扰
        if self._v_listening and not self._rx_active:
            self._v_listening = False

    def _v_delta(self, delta, full):
        """她的回复流式到达 —— 不直接灌进气泡，
        而是攒起来，由 _rx_tick 按句逐句打字出来。
        这样既有流式的"首句快"，又保留逐句的节奏感。"""
        self._set_pose('smile')       # 她开始回话了，用 smile
        if not self._rx_active:
            # 注意：一轮回复里这里可能被多次进来（第一片之后 _rx_active
            # 可能已被释放），所以判重不靠这个标志，日志只记一次。
            if not self._rx_more:
                self._voice_log(f'first delta len={len(full)} rx_begin')
            self._rx_buf = full       # 先填缓冲，再启动（顺序不能反）
            self._rx_begin()
        else:
            self._rx_buf = full       # full 是累积全文，直接用最省事
        self._rx_more = True

    def _v_done(self, full):
        """她的回复全部生成完毕"""
        if not self._rx_active:
            self._rx_buf = full
            self._rx_begin()
        else:
            self._rx_buf = full
        self._rx_more = False
        self._voice_log(f'reply_done len={len(full)} ' + self._voice_snap())
        self._voice_log(f'reply_text {full!r}')
        # 留一条"今天聊过什么"的片段给日记用
        try:
            self.diary_note_exchange(self._voice_user_text, full)
        except Exception:
            pass

    def _v_error(self, msg):
        self._set_pose('smile')
        self._speak(msg, interrupt=True)

    # ---------- 气泡的"逐句打字"：攒全文 -> 一句一句打 ----------
    def _rx_after(self):
        """安排下一次打字机推进。测试可把 _rx_delay_ms 设为 None 改为手动驱动。"""
        if self._rx_delay_ms is not None:
            self.root.after(self._rx_delay_ms, self._rx_tick)

    def _rx_begin(self):
        """开始接收一轮回复（调用前请先把 _rx_buf 填好）"""
        self._rx_active = True
        self._rx_pos = 0             # 已经切出去打字的字符位置
        self._rx_more = True         # 后面还会有字过来
        self._rx_sent = ''           # 当前正在打的这句
        self._rx_shown = 0           # 当前这句打到第几个字
        self._rx_queue = []
        # 必须把状态清成 idle：否则上一轮若停在 vtyping，
        # 这一轮会变成"状态说在打字、但没有句子"而永远冻住。
        self.dlg_state = 'idle'
        self._rx_show_bubble()
        self.bubble_label.config(text="", fg=BUBBLE_FG)
        self._rx_after()

    def _rx_goto_idle(self):
        self._rx_active = False
        self.dlg_state = 'idle'

    def _rx_show_bubble(self):
        self.bubble.place(x=BUBBLE_X, y=BUBBLE_Y, width=BUBBLE_W, height=BUBBLE_H)
        self.bubble.lift()

    def _rx_tick(self, now=None):
        """推进打字机 / 句子切换。now 可注入，便于测试精确控制时间。"""
        if now is None:
            now = time.time()
        if not self._rx_active:
            return

        # ---- 1. 正在打字：一个字一个字往外吐 ----
        if self.dlg_state == 'vtyping':
            if self._rx_sent:
                if now - self.dlg_last_char_t >= TYPING_INTERVAL:
                    self.dlg_last_char_t = now
                    self._rx_shown += 1
                    self.bubble_label.config(text=self._rx_sent[:self._rx_shown],
                                             fg=BUBBLE_FG)
                    if self._rx_shown >= len(self._rx_sent):
                        # 这句打完了，转入等待（注意：仍要续上定时器！
                        # 否则链条断裂，永远不会刷下一句）
                        self.dlg_state = 'vwait'
                        self.dlg_wait_start = now
            self._rx_after()
            return

        # ---- 2. 等下一句：停 VOICE_HOLD_SEC 秒再刷 ----
        if self.dlg_state == 'vwait':
            if not self._rx_queue and not self._rx_next():
                if not self._rx_more:
                    # 生成也结束了 -> 最后一句多留一会儿再淡出
                    if now - self.dlg_wait_start >= VOICE_END_SEC:
                        # 交给 _update_dialogue 的 fading 分支去真正淡出。
                        # 注意：这里绝对不能顺手调 _rx_goto_idle()——
                        # 它会把 dlg_state 改回 'idle'，把刚设的 'fading'
                        # 覆盖掉，气泡就永远不消失了。
                        self.dlg_state = 'fading'
                        self.dlg_fade_start = now
                        self._rx_more = False    # 本轮生成已结束
                        # 整轮真正说完了，才给反馈
                        self._add_affinity(1)
                        self._finish_pose()
                    else:
                        self._rx_after()
                    return
                # 还在生成中，等字过来
                self._rx_after()
                return

            if now - self.dlg_wait_start >= VOICE_HOLD_SEC:
                # 这页语音还没念完就先不翻（见 app._update_dialogue 同款守卫）：
                # 神经语音播一页要好几秒，不等它就会"没说完就被下一页掐断"。
                if (now - self.dlg_wait_start < SPEECH_PAGE_MAX_HOLD
                        and self.speech_busy()):
                    self._rx_after()
                    return
                self._rx_start_next(now)
            self._rx_after()
            return

        # ---- 3. 正在淡出：交给 _update_dialogue 走完动画，不要插手 ----
        # 关键：fading 是 _update_dialogue 负责的状态。若在此处落到下面的
        # 兜底分支调 _rx_goto_idle()，就会把刚设好的 fading 覆盖成 idle，
        # 淡出动画永远跑不起来 —— 气泡就再也不会消失。
        if self.dlg_state == 'fading':
            return

        # ---- 4. 其它状态（含刚 _rx_begin 后的 'idle'）：启动首句 ----
        if self.dlg_state == 'idle':
            if self._rx_start_next(now):
                self._rx_after()
                return
        # 到这里说明是一轮回复的收尾（首句入队失败等），直接结束本轮的推进链
        self._rx_active = False

    def _rx_next(self):
        """从缓冲区里切出"下一句"。返回是否拿到了。
        不用 _split_sentences：它把连续省略号「……」拆成多个 '…'，
        那会毁掉她的语气停顿。这里自己扫描。"""
        rest = self._rx_buf[self._rx_pos:]
        if not rest.strip():
            return False
        text = rest.lstrip()
        skipped = len(rest) - len(text)
        cut = None
        i = 0
        n = len(text)
        while i < n:
            ch = text[i]
            if ch == '\n':
                cut = i + 1
                break
            if ch in '。！？!?；;':
                cut = i + 1
                # 把紧跟的收尾引号/省略号一起带上
                while cut < n and text[cut] in '」』"\'）)…':
                    cut += 1
                break
            if ch == '…':
                j = i
                while j < n and text[j] == '…':
                    j += 1
                if j < n and text[j] in '。！？!?；;\n':
                    cut = j
                    break
                i = j
                continue
            i += 1
        if cut is not None:
            sentence = text[:cut].strip()
            self._rx_pos += skipped + cut
            if sentence:
                self._rx_queue.append(sentence)
                return True
            return False
        # 没有句末标点：还在生成就再等，已结束就把剩下的都当一句
        if not self._rx_more:
            sentence = text.strip()
            self._rx_pos += skipped + len(text)
            if sentence:
                self._rx_queue.append(sentence)
                return True
        return False

    def _rx_start_next(self, now=None):
        if not self._rx_queue and not self._rx_next():
            return False
        if now is None:
            now = time.time()
        self._rx_sent = self._rx_queue.pop(0)
        self._rx_shown = 0
        self._rx_show_bubble()
        self.bubble_label.config(text="", fg=BUBBLE_FG)
        self.dlg_last_char_t = now - TYPING_INTERVAL   # 立刻开始打第一个字
        self.dlg_state = 'vtyping'
        return True
