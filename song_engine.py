# -*- coding: utf-8 -*-
"""
唱歌功能
========
解码歌曲、按小句切分、后台播放（支持暂停/续唱/切歌/句尾淡出）。
依赖 config.py 里的 SONG_* 常量与提示台词。
"""
import os
import random
import threading
import time
import queue

import numpy as np

try:
    import miniaudio
    import soundcard as sc
    HAS_SONG = True
except ImportError:
    HAS_SONG = False

from config import *          # noqa: F401,F403  常量与台词


def _load_song(path):
    """解码整首歌为 float32 单声道数组。失败返回 None。"""
    try:
        pcm = miniaudio.decode_file(path, output_format=miniaudio.SampleFormat.FLOAT32,
                                    nchannels=1, sample_rate=SONG_RATE)
        data = np.frombuffer(pcm.samples, dtype=np.float32).copy()
        if data.size == 0:
            return None
        peak = float(np.abs(data).max())
        if peak > 0.98:
            data *= 0.98 / peak
        return data
    except Exception:
        return None


def _analyze_phrases(data):
    """找乐句边界：40ms RMS 包络 → 0.4s 平滑 → 自适应静音谷。
    返回 [(center_sec, end_sec), ...]，center 用于淡出落点，end 用于续播起点。"""
    if data is None or data.size < SONG_RATE * 10:
        return []
    hop = int(SONG_RATE * 0.04)                    # 40ms
    n = data.size // hop
    if n < 10:
        return []
    env = np.sqrt(np.mean(data[:n*hop].reshape(n, hop) ** 2, axis=1))
    win = max(1, int(0.4 / 0.04))                  # 0.4s 平滑
    sm = np.convolve(env, np.ones(win) / win, mode='same')
    gpeak = float(sm.max())
    if gpeak <= 1e-6:
        return []
    valleys = []
    for i in range(2, n - 2):
        v = sm[i]
        if v != sm[i-2:i+3].min():                 # 局部极小
            continue
        lo = max(0, i - int(6/0.04))
        hi = min(n, i + int(6/0.04))
        lpeak = max(float(sm[lo:i].max()) if i > lo else 0.0,
                    float(sm[i+1:hi].max()) if hi > i+1 else 0.0)
        if v < gpeak * 0.30 and v < lpeak * 0.30:
            valleys.append(i * 0.04)
    # 去重：间隔至少 4 秒
    merged = []
    for c in valleys:
        if merged and c - merged[-1] < 4.0:
            continue
        merged.append(c)
    # 谷的右沿（声音重新起来）作为续播起点
    bounds = []
    for c in merged:
        ci = int(c / 0.04)
        end = c
        for j in range(ci, min(n, ci + int(3.0/0.04))):
            if sm[j] > gpeak * 0.15:
                end = j * 0.04
                break
        else:
            end = min(n * 0.04, c + 3.0)
        bounds.append((c, end))
    return bounds


# ==================== 歌曲播放引擎 ====================
class SongEngine:
    """流式播放引擎：独立线程 + 命令队列。渐强/按句停/渐弱/续播。"""

    CHUNK_SEC = 0.05
    QUEUE_AHEAD = 0.12

    def __init__(self, on_paused=None, on_finished=None, on_error=None):
        self._cmd = queue.Queue()
        self.on_paused = on_paused
        self.on_finished = on_finished
        self.on_error = on_error
        self.state = 'idle'
        self.pos = 0.0
        self._data = None
        self._bounds = []
        self._submit_pos = 0.0
        self._resume_pos = 0.0
        self._fi0 = self._fi1 = self._fo0 = self._fo1 = 0.0
        self._player = None
        self._ending = False
        self._drain_t0 = 0.0
        self._wall0 = 0.0
        self._chunk_frames = int(SONG_RATE * self.CHUNK_SEC)
        threading.Thread(target=self._run, name='song-engine', daemon=True).start()

    # ---------- 对外命令 ----------
    def start(self, data, bounds, pos=0.0, fade=SONG_FADE_IN):
        self._cmd.put(('start', (data, bounds, float(pos), float(fade))))

    def stop_at_boundary(self):
        self._cmd.put(('stopat', None))

    def quick_stop(self, fade=SONG_FADE_OUT):
        """立即淡出（不等句尾），用于切歌 / 停止口令"""
        self._cmd.put(('stopfade', float(fade)))

    def resume(self):
        self._cmd.put(('resume', None))

    def hard_stop(self):
        self._cmd.put(('hardstop', None))

    def shutdown(self):
        self._cmd.put(('quit', None))

    # ---------- 内部 ----------
    @staticmethod
    def _fire(cb, *args):
        if cb is not None:
            try: cb(*args)
            except Exception: pass

    def _open_player(self):
        if self._player is None:
            p = sc.default_speaker().player(samplerate=SONG_RATE)
            p.__enter__()               # soundcard 的流在 __enter__ 才真正打开
            self._player = p
        self._wall0 = time.time() - self._submit_pos

    def _close_player(self):
        p = self._player
        self._player = None
        if p is not None:
            try: p.close()
            except Exception:
                try: p.__exit__(None, None, None)
                except Exception: pass

    @staticmethod
    def _padding_sec(player):
        if player is None:
            return 0.0
        for obj in (player, getattr(player, '_player', None)):
            if obj is None:
                continue
            v = getattr(obj, 'currentpadding', None)
            if v is None:
                continue
            try:
                v = v() if callable(v) else v
                return max(0.0, float(v)) / SONG_RATE
            except Exception:
                pass
        return 0.0

    def _sounding_pos(self):
        if self._player is None:
            return self._submit_pos
        pad = self._padding_sec(self._player)
        if pad > 0:
            return max(0.0, self._submit_pos - pad)
        # 备选：按墙钟时间估
        return min(self._submit_pos, max(0.0, time.time() - self._wall0))

    def _gain_at(self, pos):
        g = np.ones_like(pos)
        if self._fi1 > self._fi0:
            g = np.minimum(g, np.clip((pos - self._fi0) / (self._fi1 - self._fi0), 0.0, 1.0))
        if self._fo1 > self._fo0:
            g = np.minimum(g, 1.0 - np.clip((pos - self._fo0) / (self._fo1 - self._fo0), 0.0, 1.0))
        return g

    def _pump(self):
        if self.state == 'stopping' and self._submit_pos >= self._fo1:
            self.state = 'draining'
            self._drain_t0 = time.time()
            return True
        if self._player is None or self._data is None:
            return False
        ahead = self._submit_pos - self._sounding_pos()
        if ahead > self.QUEUE_AHEAD:
            time.sleep(0.01)
            return True
        i0 = int(self._submit_pos * SONG_RATE)
        size = self._data.size
        if i0 >= size:
            return False
        i1 = min(i0 + self._chunk_frames, size)
        pos = np.arange(i0, i1, dtype=np.float64) / SONG_RATE
        gain = self._gain_at(pos) * SONG_VOL
        chunk = (self._data[i0:i1] * gain).astype(np.float32)
        self._player.play(chunk)
        self._submit_pos = i1 / SONG_RATE
        self.pos = self._sounding_pos()
        return True

    def _drain_step(self):
        pad = self._padding_sec(self._player)
        if pad <= self.CHUNK_SEC * 0.6 or time.time() - self._drain_t0 > 0.8:
            self._close_player()
            if self._ending:
                self.state = 'idle'
                self.pos = self._resume_pos = 0.0
                self._fire(self.on_finished)
            else:
                self.state = 'paused'
                self.pos = self._resume_pos
                self._fire(self.on_paused, self._resume_pos)
        else:
            time.sleep(0.02)

    def _dispatch(self, cmd, arg):
        if cmd == 'start':
            self._h_start(*arg)
        elif cmd == 'stopat':
            self._h_stopat()
        elif cmd == 'resume':
            self._h_resume()
        elif cmd == 'hardstop':
            self._close_player()
            self.state = 'idle'
        elif cmd == 'stopfade':
            self._h_stopfade(arg)

    def _h_start(self, data, bounds, pos, fade):
        self._close_player()
        self._data = data
        self._bounds = bounds
        self._submit_pos = max(0.0, min(pos, data.size / SONG_RATE))
        self._resume_pos = self._submit_pos
        self._fi0 = self._submit_pos
        self._fi1 = self._submit_pos + fade
        self._fo0 = self._fo1 = 0.0
        self._ending = False
        try:
            self._open_player()
        except Exception as e:
            self.state = 'error'
            self._fire(self.on_error, str(e))
            self.state = 'idle'
            return
        self.state = 'playing'

    def _h_stopat(self):
        if self.state not in ('playing',):
            return
        snd = self._sounding_pos()
        target = None
        for c, e in self._bounds:
            if c >= snd + SONG_STOP_MARGIN:
                target = (c, e)
                break
        if target is None:
            self._ending = True
            return
        c, e = target
        self._fo0 = max(c, snd + 0.05)
        self._fo1 = self._fo0 + SONG_FADE_OUT
        self._resume_pos = max(self._fo1, e)
        self.state = 'stopping'

    def _h_stopfade(self, fade):
        """立即从当前位置开始淡出（切歌/停止用）"""
        if self.state != 'playing':
            return
        snd = self._sounding_pos()
        self._fo0 = snd + 0.05
        self._fo1 = self._fo0 + max(0.15, float(fade))
        self._resume_pos = self._fo1
        self.state = 'stopping'

    def _h_resume(self):
        if self.state != 'paused' or self._data is None:
            return
        dur = self._data.size / SONG_RATE
        if self._resume_pos >= dur - 0.3:
            self.state = 'idle'
            self._fire(self.on_finished)
            return
        self._submit_pos = self._resume_pos
        self._fi0 = self._submit_pos
        self._fi1 = self._submit_pos + SONG_FADE_RESUME
        self._fo0 = self._fo1 = 0.0
        self._ending = False
        try:
            self._open_player()
        except Exception as e:
            self.state = 'error'
            self._fire(self.on_error, str(e))
            self.state = 'idle'
            return
        self.state = 'playing'

    def _run(self):
        while True:
            block = self.state in ('idle', 'paused')
            try:
                item = self._cmd.get(timeout=0.15) if block else self._cmd.get_nowait()
            except queue.Empty:
                item = None
            if item is not None:
                cmd, arg = item
                if cmd == 'quit':
                    break
                try:
                    self._dispatch(cmd, arg)
                except Exception as e:
                    self._fire(self.on_error, str(e))
            try:
                if self.state in ('playing', 'stopping'):
                    if not self._pump():
                        self._ending = True
                        self.state = 'draining'
                        self._drain_t0 = time.time()
                elif self.state == 'draining':
                    self._drain_step()
            except Exception as e:
                self._fire(self.on_error, str(e))
                self._close_player()
                self.state = 'idle'
        self._close_player()


