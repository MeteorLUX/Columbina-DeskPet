# -*- coding: utf-8 -*-
"""
唱歌静音诊断 v2：**直接测音频输出音量**
=======================================
v1 只看状态机，没能复现。这次换个更硬的判据：
每 0.25 秒统计一次真实播放的 RMS 音量，
出现「她以为在唱 + 音量连续为 0」就是真静音，
同时把当时的全部阻塞条件打出来，看是谁挡着。

用法：python _diag_sing2.py [观察秒数] [--talk]
  --talk  中途让她说气泡/生成回复，模拟真实干扰
"""
import os
import sys
import time
import traceback

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app
import song_engine as SE

SECS = 180.0
TALK = '--talk' in sys.argv
for a in sys.argv[1:]:
    try:
        SECS = float(a)
    except ValueError:
        pass

pet = app.DesktopPet()
pet.root.withdraw()

print('=' * 72)
print('唱歌静音诊断 v2（直接测音量）')
print('=' * 72)
print(f'  walk_enabled={pet.cfg["walk_enabled"]}  talk={TALK}  观察={SECS:.0f}s')
print(f'  曲目={[os.path.basename(p) for p in pet._song_playlist()]}')
print()

# ---- 音量探针：包一层 player.play，记录每块的真实能量 ----
energy = {'sum': 0.0, 'n': 0, 'last': 0.0}
_orig_open = SE.SongEngine._open_player


def patched_open(self):
    _orig_open(self)
    pl = self._player
    if pl is None:
        return
    _orig_play = pl.play

    def play(chunk):
        try:
            arr = np.asarray(chunk, dtype=np.float32)
            energy['sum'] += float(np.sqrt(np.mean(arr ** 2)) if arr.size else 0.0)
            energy['n'] += 1
        except Exception:
            pass
        return _orig_play(chunk)
    try:
        pl.play = play
    except Exception:
        pass


SE.SongEngine._open_player = patched_open

pet._handle_song_request('我想听你唱歌了')

last = None
silent_from = None
t0 = time.time()
talk_done = False
while time.time() - t0 < SECS:
    pet.root.update()
    time.sleep(0.02)
    el = time.time() - t0

    # 中途制造真实干扰：气泡 + 生成标志
    # 注意：真实流程里 _on_reply 会在回复到达时把 is_generating 复位，
    # 所以这里也要复位，否则测的就成了"永久生成中"这个不存在的场景。
    if TALK and not talk_done and 40.0 < el < 40.1:
        talk_done = True
        print(f'  {el:6.1f}s 【注入干扰】让她说一句话 + 生成中', flush=True)
        pet.is_generating = True
        pet._speak('……今天也很安静呢。')
    # 12 秒后回复到达（模拟 DeepSeek 返回），复位生成标志
    if TALK and talk_done and 52.0 < el < 52.1:
        print(f'  {el:6.1f}s 【生成结束】回复到达，is_generating=False', flush=True)
        pet.is_generating = False

    snap = (pet._sing_state, pet._sing_hold, pet._sing_manual,
            pet.dlg_state, pet.is_generating, pet._dragging,
            pet._v_listening, pet._rx_active, pet.current_pose,
            pet.song.state)
    if snap != last:
        print(f'  {el:6.1f}s sing={snap[0]:9s} hold={str(snap[1]):5s} '
              f'dlg={snap[3]:8s} gen={str(snap[4]):5s} drag={str(snap[5]):5s} '
              f'listen={str(snap[6]):5s} rx={str(snap[7]):5s} pose={snap[8]:6s}'
              f' | 引擎={snap[9]:9s}', flush=True)
        last = snap

    # 音量判定：每 0.5 秒结算一次
    if energy['n'] >= 20:
        avg = energy['sum'] / energy['n']
        she_thinks_playing = pet._sing_state == 'playing'
        engine_playing = pet.song.state in ('playing', 'stopping')
        if she_thinks_playing and engine_playing and avg < 1e-6:
            if silent_from is None:
                silent_from = el
        else:
            if silent_from is not None and el - silent_from > 1.5:
                print(f'  >>> 【真静音】{silent_from:.1f}s ~ {el:.1f}s '
                      f'（{el - silent_from:.0f}秒）她=playing 引擎=playing '
                      f'但音量=0  当前阻塞：dlg={pet.dlg_state} '
                      f'gen={pet.is_generating} drag={pet._dragging} '
                      f'listen={pet._v_listening} rx={pet._rx_active} '
                      f'pose={pet.current_pose}', flush=True)
            silent_from = None
        energy['sum'] = 0.0
        energy['n'] = 0

print()
print('=' * 72)
print(f'  结束: sing={pet._sing_state} 引擎={pet.song.state}')
print('=' * 72)
pet.root.destroy()
