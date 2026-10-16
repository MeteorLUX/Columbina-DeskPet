# -*- coding: utf-8 -*-
"""
全双工语音对话模块  voice_engine.py
=========================================
独立于 main.py，可单独测试，也可整体开关。

设计要点（对应需求）：
  1. 连续收音 + VAD：一个常驻采集线程，永不关闭设备；
     Silero VAD 按 32ms 窗口判定，自动识别用户说话的开始/结束。
  2. 打断（barge-in）：用户再次开口时立即取消正在进行的
     LLM 流式生成，并把气泡切成新一轮聆听。
  3. 流式 ASR + 流式 LLM：识别与生成都边算边给，降低端到端延迟。
  4. 不需要 AEC —— 本桌宠只用气泡出字，扬声器不出声，没有回声。

线程模型：
  采集线程(capture) --100ms 音频块--> 环形缓冲
  VAD/ASR 在同一线程内按 512 样本(32ms)窗口推进
  事件通过回调抛回 Tk 主线程（调用方负责 root.after）

对外接口：
  voice = FullDuplexVoice(base_dir, callbacks, params)
  voice.start()      # 开始聆听
  voice.stop()       # 停止
  voice.toggle()
  voice.is_running
  voice.cancel_generation()   # 外部主动打断
"""

import json
import os
import queue
import threading
import time

try:
    import numpy as np
    HAS_NUMPY = True
except ImportError:
    HAS_NUMPY = False

try:
    import soundcard as sc
    HAS_SOUNDCARD = True
except ImportError:
    HAS_SOUNDCARD = False

try:
    import sherpa_onnx
    HAS_SHERPA = True
except ImportError:
    HAS_SHERPA = False

try:
    import requests
    HAS_REQUESTS = True
except ImportError:
    HAS_REQUESTS = False


# ==================== 默认参数 ====================
# 全部可在构造时覆盖，方便调优
DEFAULTS = {
    # ---- 音频采集 ----
    'sample_rate':    16000,     # 模型要求 16kHz
    'block_ms':       100,       # 每次向设备要 100ms，实测稳定
    'channels':       1,

    # ---- VAD（Silero，按概率判定，不是能量阈值） ----
    'vad_threshold':      0.45,  # 语音概率阈值（0.5 会漏掉轻声开头的字）
    'min_speech_ms':      250,   # 短于此不算一句话，滤掉咳嗽/键盘声
    'min_silence_ms':     700,   # 静音这么久判定"说完了"（原代码 1200ms）
    'max_speech_s':       20.0,  # 单句最长，防止无限累积
    # 保留触发前的音频，避免吃掉字头。要点：它必须**大于**"VAD 检出延迟 +
    # start_frames 确认延迟"——开口轻声时 VAD 要几百毫秒后才判定为语音，
    # pre_roll 只保留最后 N 毫秒，若 N 小于这个延迟，最早的字就被裁掉了。
    # 实测 300ms 时常丢前几个字，600ms 后轻声起头也能完整收进来。
    'pre_roll_ms':        600,
    'start_frames':       2,     # 连续 N 个 32ms 窗口为语音才认为开始（约 64ms）

    # ---- ASR ----
    'asr_threads':        4,
    'partial_min_chars':  1,     # 中间结果至少几个字才回调

    # ---- LLM 流式 ----
    'llm_model':      'deepseek-chat',
    'llm_timeout':    60,
    'llm_temperature': 0.95,
    'llm_max_tokens':  200,
    'llm_early_ms':   1200,      # 中间结果稳定这么久就提前起 LLM（抢时间）

    # ---- 其它 ----
    'debug':          False,
}


class VoiceCallbacks:
    """回调集合。所有回调都在采集线程里触发，调用方需自行切回 UI 线程。"""
    def __init__(self,
                 on_partial=None,        # (text) 识别中间结果
                 on_user_final=None,     # (text) 用户这句话说完的最终文本
                 on_speech_start=None,   # ()     检测到用户开口（用于打断）
                 on_speech_end=None,     # ()     检测到用户说完
                 on_reply_delta=None,    # (delta, full) LLM 流式增量
                 on_reply_done=None,     # (full) LLM 说完
                 on_status=None,         # (text) 状态变化，用于界面提示
                 on_error=None):         # (text) 出错
        self.on_partial = on_partial
        self.on_user_final = on_user_final
        self.on_speech_start = on_speech_start
        self.on_speech_end = on_speech_end
        self.on_reply_delta = on_reply_delta
        self.on_reply_done = on_reply_done
        self.on_status = on_status
        self.on_error = on_error

    def _call(self, name, *args):
        f = getattr(self, name, None)
        if f is None:
            return
        try:
            f(*args)
        except Exception:
            pass

    def _callv(self, name, *args):
        """同 _call，但把回调的返回值带给调用方（None = 未处理）"""
        f = getattr(self, name, None)
        if f is None:
            return None
        try:
            return f(*args)
        except Exception:
            return None


class FullDuplexVoice:
    """常驻收听 + VAD 断句 + 流式识别 + 流式回复"""

    def __init__(self, base_dir, callbacks=None, api_key=None,
                 system_prompt=None, api_url=None, params=None):
        self.base_dir = base_dir
        self.cb = callbacks or VoiceCallbacks()
        self.api_key = api_key
        self.api_url = api_url or 'https://api.deepseek.com/chat/completions'
        self.system_prompt = system_prompt or ''
        self.p = dict(DEFAULTS)
        if params:
            self.p.update(params)

        self.voice_dir = os.path.join(base_dir, 'voice-model')
        self.vad_model_path = os.path.join(self.voice_dir, 'vad', 'silero_vad.onnx')
        self.asr_dir = os.path.join(self.voice_dir,
                                    'sherpa-onnx-streaming-zipformer-zh-14M-2023-02-23')

        self._thread = None
        self._stop_evt = threading.Event()
        self._asr = None
        self._vad = None
        self._history = []

        # ---- 打断（barge-in）机制 ----
        # 用"代次"而不是共享标志位：每轮回复有唯一 gen，
        # 用户开口时 gen 自增，旧线程发现 gen 变了就自行中止。
        self._gen_lock = threading.Lock()
        self._gen = 0
        self._reply_thread = None

        # 统计
        self.stats = {
            'utterances': 0,
            'last_asr_ms': 0.0,
            'last_llm_first_ms': 0.0,
            'last_total_ms': 0.0,
        }

    # ------------------------------------------------ 日志
    def _log(self, *a):
        if self.p['debug']:
            print('[voice]', *a, flush=True)

    # ------------------------------------------------ 资源加载（惰性，只做一次）
    def load_models(self):
        """加载 VAD 与流式识别模型。返回 (ok, message)"""
        if not HAS_SHERPA:
            return False, '缺少 sherpa-onnx'
        if not HAS_NUMPY:
            return False, '缺少 numpy'
        if not os.path.exists(self.vad_model_path):
            return False, f'缺少 VAD 模型: {self.vad_model_path}'

        # --- VAD ---
        if self._vad is None:
            cfg = sherpa_onnx.VadModelConfig()
            cfg.silero_vad.model = self.vad_model_path
            cfg.silero_vad.threshold = self.p['vad_threshold']
            cfg.silero_vad.min_silence_duration = self.p['min_silence_ms'] / 1000.0
            cfg.silero_vad.min_speech_duration = self.p['min_speech_ms'] / 1000.0
            cfg.silero_vad.max_speech_duration = self.p['max_speech_s']
            cfg.sample_rate = self.p['sample_rate']
            cfg.num_threads = 1
            self._vad = sherpa_onnx.VadModel.create(cfg)
            if self._vad is None:
                return False, 'VAD 模型加载失败'

        # --- 流式识别 ---
        if self._asr is None:
            if not os.path.isdir(self.asr_dir):
                return False, f'缺少流式识别模型目录: {self.asr_dir}'
            files = os.listdir(self.asr_dir)

            def pick(kind):
                # 优先 fp32：实测 int8 量化明显掉精度（"开饭"->"放"），
                # 而两者都是几十倍实时，速度差异在实时场景里无感。
                # 没有 fp32 时退回 int8，保证仍能工作。
                for f in files:
                    if f.startswith(kind) and f.endswith('.onnx') and '.int8.' not in f:
                        return os.path.join(self.asr_dir, f)
                for f in files:
                    if f.startswith(kind) and f.endswith('.int8.onnx'):
                        return os.path.join(self.asr_dir, f)
                return None

            enc, dec, joi = pick('encoder'), pick('decoder'), pick('joiner')
            tok = os.path.join(self.asr_dir, 'tokens.txt')
            if not (enc and dec and joi and os.path.exists(tok)):
                return False, '流式模型文件不完整（需要 encoder/decoder/joiner/tokens）'

            self._asr = sherpa_onnx.OnlineRecognizer.from_transducer(
                tokens=tok, encoder=enc, decoder=dec, joiner=joi,
                num_threads=self.p['asr_threads'],
                sample_rate=self.p['sample_rate'],
                feature_dim=80,
                decoding_method='greedy_search',
                # 断句交给 VAD，这里把阈值放宽避免抢跑
                enable_endpoint_detection=False,
            )
            if self._asr is None:
                return False, '流式识别器创建失败'
        return True, 'ok'

    # ------------------------------------------------ 启停
    @property
    def is_running(self):
        return self._thread is not None and self._thread.is_alive()

    def start(self):
        if self.is_running:
            return True, '已在聆听'
        if not HAS_SOUNDCARD:
            return False, '缺少 soundcard，无法收音'
        ok, msg = self.load_models()
        if not ok:
            return False, msg
        self._stop_evt.clear()
        self._thread = threading.Thread(target=self._run, name='voice-loop', daemon=True)
        self._thread.start()
        return True, '开始聆听'

    def stop(self):
        self._stop_evt.set()
        self._bump_generation()          # 让正在生成的回复立刻中止
        t = self._thread
        if t is not None:
            t.join(timeout=3.0)
        self._thread = None
        rt = self._reply_thread
        if rt is not None and rt.is_alive():
            rt.join(timeout=3.0)
        self._reply_thread = None
        return True, '已停止聆听'

    def toggle(self):
        return self.stop() if self.is_running else self.start()

    def cancel_generation(self):
        """外部主动打断（用户开口时由内部调用，也可外部调用）"""
        return self._bump_generation()

    def clear_history(self):
        self._history = []

    # ------------------------------------------------ 代次管理
    def _bump_generation(self):
        """使代次自增 -> 正在跑的回复线程会发现自己过期并中止"""
        with self._gen_lock:
            self._gen += 1
            return self._gen

    def _current_gen(self):
        with self._gen_lock:
            return self._gen

    def _is_stale(self, gen):
        return gen != self._current_gen()

    # ------------------------------------------------ 主循环
    def _run(self):
        sr = self.p['sample_rate']
        block = int(sr * self.p['block_ms'] / 1000)
        win = self._vad.window_size()               # 512 = 32ms
        pre_roll_n = int(sr * self.p['pre_roll_ms'] / 1000)
        start_frames = int(self.p['start_frames'])

        self.cb._call('on_status', 'listening')

        try:
            mic = sc.default_microphone()
        except Exception as e:
            self.cb._call('on_error', f'找不到麦克风: {e}')
            return
        if mic is None:
            self.cb._call('on_error', '没有可用的麦克风')
            return

        try:
            # 常驻录音会话：只开一次，避免每次点击的设备初始化开销
            with mic.recorder(samplerate=sr, channels=self.p['channels'],
                              blocksize=block) as rec:
                self._loop(rec, sr, block, win, pre_roll_n, start_frames)
        except Exception as e:
            self.cb._call('on_error', f'录音失败: {type(e).__name__}: {e}')
        finally:
            self.cb._call('on_status', 'stopped')

    def _loop(self, rec, sr, block, win, pre_roll_n, start_frames):
        # VAD 需要连续的 512 样本窗口，这里用残留缓冲拼接
        carry = np.zeros(0, dtype=np.float32)
        pre_roll = []          # 触发前的音频，避免吃掉字头
        utt = []               # 当前这句话的音频
        in_speech = False
        speech_frames = 0
        silence_run = 0         # 连续静音样本数
        stream = None
        last_partial = ''
        asr_t0 = 0.0
        early_fired = False

        max_speech_n = int(sr * self.p['max_speech_s'])
        min_speech_n = int(sr * self.p['min_speech_ms'] / 1000)
        min_silence_n = int(sr * self.p['min_silence_ms'] / 1000)

        while not self._stop_evt.is_set():
            try:
                data = rec.record(numframes=block)
            except Exception as e:
                self.cb._call('on_error', f'读取音频失败: {e}')
                break
            mono = np.asarray(data, dtype=np.float32)
            if mono.ndim > 1:
                mono = mono.mean(axis=1)

            # ---------- 暂停聆听（例如她在唱歌时，避免把歌声当成人声） ----------
            if getattr(self, 'suspend_vad', False):
                carry = np.zeros(0, dtype=np.float32)
                pre_roll = []
                utt = []
                in_speech = False
                speech_frames = 0
                silence_run = 0
                stream = None
                last_partial = ''
                continue

            # ---------- 用 512 样本窗口推进 VAD ----------
            buf = np.concatenate([carry, mono]) if carry.size else mono
            n_win = len(buf) // win
            for k in range(n_win):
                w = buf[k * win:(k + 1) * win]
                is_speech = bool(self._vad.is_speech(w))

                if not in_speech:
                    # 维持一段 pre-roll，触发时把它补回句首
                    pre_roll.append(w)
                    pre_n = sum(len(x) for x in pre_roll)
                    while pre_n > pre_roll_n and len(pre_roll) > 1:
                        pre_n -= len(pre_roll.pop(0))
                    if is_speech:
                        speech_frames += 1
                        if speech_frames >= start_frames:
                            # ===== 说话开始 =====
                            in_speech = True
                            silence_run = 0
                            utt = list(pre_roll) + [w]
                            pre_roll = []
                            speech_frames = 0
                            stream = self._asr.create_stream()
                            for seg in utt[:-1]:
                                stream.accept_waveform(sr, seg)
                            stream.accept_waveform(sr, w)
                            last_partial = ''
                            early_fired = False
                            asr_t0 = time.perf_counter()
                            self.cb._call('on_speech_start')
                            # 打断：代次自增，正在生成的上一轮回复会自行中止
                            self._bump_generation()
                    else:
                        speech_frames = 0
                else:
                    utt.append(w)
                    utt_n = sum(len(x) for x in utt)

                    if stream is not None:
                        try:
                            stream.accept_waveform(sr, w)
                            while self._asr.is_ready(stream):
                                self._asr.decode_stream(stream)
                            txt = (self._asr.get_result(stream) or '').strip()
                            if txt and txt != last_partial and len(txt) >= self.p['partial_min_chars']:
                                last_partial = txt
                                self.cb._call('on_partial', txt)
                        except Exception as e:
                            self._log('ASR 解码异常:', e)

                    # ---------- 静音累积 / 超长保护 ----------
                    if is_speech:
                        silence_run = 0
                    else:
                        silence_run += win

                    too_long = utt_n >= max_speech_n
                    done = silence_run >= min_silence_n

                    if done or too_long:
                        if utt_n >= min_speech_n:
                            self._finish_utterance(stream, utt, sr, asr_t0,
                                                   reason='timeout' if too_long else 'silence')
                        else:
                            self._log('段太短，丢弃', utt_n / sr)
                        # 复位
                        in_speech = False
                        speech_frames = 0
                        silence_run = 0
                        utt = []
                        pre_roll = []
                        stream = None
                        last_partial = ''

            # 暂存不足一个窗口的尾巴
            rem = len(buf) % win
            carry = buf[len(buf) - rem:].copy() if rem else np.zeros(0, dtype=np.float32)

    def _finish_utterance(self, stream, utt, sr, asr_t0, reason='silence'):
        text = ''
        if stream is not None:
            try:
                stream.input_finished()
                while self._asr.is_ready(stream):
                    self._asr.decode_stream(stream)
                text = (self._asr.get_result(stream) or '').strip()
            except Exception as e:
                self._log('收尾解码异常:', e)
        asr_ms = (time.perf_counter() - asr_t0) * 1000
        self.stats['last_asr_ms'] = asr_ms
        self.stats['utterances'] += 1
        self._log(f'一句话结束({reason}) 识别="{text}" 耗时{asr_ms:.0f}ms')

        self.cb._call('on_speech_end')
        if not text:
            return
        # 调用方若消化了这句话（返回 True，例如识别出点歌请求），就不再生成回复
        handled = self.cb._callv('on_user_final', text)
        if handled:
            return
        # LLM 必须放独立线程：否则会阻塞采集线程，
        # 采集一停就再也检测不到用户开口，打断功能失效。
        self._reply_thread = threading.Thread(
            target=self._reply_stream, args=(text,), name='voice-reply', daemon=True)
        self._reply_thread.start()

    # ------------------------------------------------ 流式回复
    def _reply_stream(self, user_text):
        if not HAS_REQUESTS:
            self.cb._call('on_error', '缺少 requests')
            return
        if not self.api_key:
            self.cb._call('on_reply_done', '……（我还没有钥匙。请在 config.json 里填上 deepseek_api_key。）')
            return

        # 本轮回复的代次：用户一开口，代次就会变，本线程随即中止
        my_gen = self._bump_generation()

        self._history.append({'role': 'user', 'content': user_text})
        if len(self._history) > 20:
            self._history = self._history[-20:]

        messages = [{'role': 'system', 'content': self.system_prompt}] + list(self._history)
        payload = {
            'model': self.p['llm_model'],
            'messages': messages,
            'temperature': self.p['llm_temperature'],
            'max_tokens': self.p['llm_max_tokens'],
            'stream': True,
        }
        headers = {'Authorization': f'Bearer {self.api_key}',
                   'Content-Type': 'application/json'}

        full = []
        t0 = time.perf_counter()
        first_ms = None
        interrupted = False
        resp = None
        try:
            resp = requests.post(self.api_url, headers=headers, json=payload,
                                 stream=True, timeout=self.p['llm_timeout'])
            resp.raise_for_status()
            for raw in resp.iter_lines(decode_unicode=False):
                if self._is_stale(my_gen) or self._stop_evt.is_set():
                    interrupted = True
                    self._log('回复被用户打断，已中止')
                    break
                if not raw:
                    continue
                line = raw.decode('utf-8', 'ignore').strip()
                if not line.startswith('data:'):
                    continue
                data = line[5:].strip()
                if data == '[DONE]':
                    break
                try:
                    obj = json.loads(data)
                    delta = obj['choices'][0]['delta'].get('content') or ''
                except Exception:
                    continue
                if delta:
                    if first_ms is None:
                        first_ms = (time.perf_counter() - t0) * 1000
                        self.stats['last_llm_first_ms'] = first_ms
                    full.append(delta)
                    self.cb._call('on_reply_delta', delta, ''.join(full))
        except Exception as e:
            if not (self._is_stale(my_gen) or self._stop_evt.is_set()):
                r = getattr(e, 'response', None)
                if r is not None and r.status_code == 401:
                    self.cb._call('on_reply_done',
                                  '……（这把钥匙好像失效了。请检查 config.json 里的 '
                                  'deepseek_api_key 是不是填对了。）')
                else:
                    self.cb._call('on_error', f'回复失败: {type(e).__name__}: {e}')
        finally:
            if resp is not None:
                try:
                    resp.close()
                except Exception:
                    pass

        text = ''.join(full).strip()
        self.stats['last_total_ms'] = (time.perf_counter() - t0) * 1000
        if text:
            self._history.append({'role': 'assistant', 'content': text})
            if len(self._history) > 20:
                self._history = self._history[-20:]
            # 只有没被打断、且代次仍是最新的，才通知界面收尾
            if not interrupted and not self._is_stale(my_gen):
                self.cb._call('on_reply_done', text)
            else:
                self._log('回复不完整，已丢弃收尾回调')
