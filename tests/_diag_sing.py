# -*- coding: utf-8 -*-
"""
唱歌静音问题诊断（真引擎，全程记录状态变化）
============================================
现象：请她唱歌，放一下就静音；拖动她又响。
目的：抓出"静音时"她到底停在哪个状态、谁挡着她。

用法：python _diag_sing.py [观察秒数]
"""
import os
import sys
import time
import traceback

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app

SECS = float(sys.argv[1]) if len(sys.argv) > 1 else 75.0

pet = app.DesktopPet()
pet.root.withdraw()

# 覆盖引擎的回调，记录每次事件（不改行为，只旁听）
eng = pet.song
_orig = {}
for name in ('on_paused', 'on_finished', 'on_error'):
    _orig[name] = getattr(eng, name)


def wrap(name):
    def fn(*a):
        print(f'    [引擎事件] {name} {a}', flush=True)
        cb = _orig.get(name)
        if cb:
            try:
                cb(*a)
            except Exception:
                traceback.print_exc()
    return fn


eng.on_paused = wrap('on_paused')
eng.on_finished = wrap('on_finished')
eng.on_error = wrap('on_error')

print('=' * 70)
print('唱歌静音诊断')
print('=' * 70)
print(f'  walk_enabled     = {pet.cfg["walk_enabled"]}')
print(f'  dialogue_mode    = {pet.cfg["dialogue_mode"]}')
print(f'  _song_ok()       = {pet._song_ok()}')
print(f'  曲目             = {[os.path.basename(p) for p in pet._song_playlist()]}')
print()

# 模拟"打开面板请她唱歌"
pet._handle_song_request('我想听你唱歌了')
print(f'  点歌后: state={pet._sing_state} manual={pet._sing_manual} '
      f'hold={pet._sing_hold}')

last = None
t0 = time.time()
silent_since = None
silent_reported = None
while time.time() - t0 < SECS:
    pet.root.update()
    time.sleep(0.02)
    el = time.time() - t0
    # 只在**状态**变化时打印，pos 不进 snap（否则每帧刷屏）
    snap = (pet._sing_state, pet._sing_hold, pet._sing_manual,
            round(pet._sing_resume_at - time.time(), 1) if pet._sing_resume_at != float('inf') else 'inf',
            pet.dlg_state, pet.is_generating, pet._dragging,
            pet._v_listening, pet._rx_active, pet.current_pose,
            eng.state)
    if snap != last:
        print(f'  {el:6.1f}s sing={snap[0]:9s} hold={str(snap[1]):5s} '
              f'resume_in={str(snap[3]):>6s} dlg={snap[4]:8s} '
              f'gen={str(snap[5]):5s} drag={str(snap[6]):5s} '
              f'listen={str(snap[7]):5s} rx={str(snap[8]):5s} '
              f'pose={snap[9]:6s} | 引擎={snap[10]:9s} '
              f'pos={getattr(eng, "pos", 0):.1f}', flush=True)
        last = snap

    # 静音判定：她以为在唱，但引擎不在 playing/stopping
    # 给 2 秒宽限（start 指令到引擎真正 playing 有短暂延迟）
    if (pet._sing_state == 'playing'
            and eng.state not in ('playing', 'stopping')):
        if silent_since is None:
            silent_since = el
    else:
        if silent_since is not None and el - silent_since > 2.0:
            print(f'  >>> 【静音】{silent_since:.1f}s ~ {el:.1f}s '
                  f'（{el - silent_since:.0f} 秒）她认为在唱，'
                  f'引擎却停在 {eng.state}', flush=True)
            silent_reported = silent_since
        silent_since = None

print()
print('=' * 70)
print(f'  结束时: sing_state={pet._sing_state} hold={pet._sing_hold} '
      f'manual={pet._sing_manual}')
print(f'          引擎状态={eng.state} pos={getattr(eng, "pos", 0):.1f}')
if silent_reported:
    print(f'  ⚠ 复现静音：最早 {silent_reported:.1f}s 起她以为在唱但引擎已停')
else:
    print('  本次未复现静音（>2 秒的静音段）')
print('=' * 70)
pet.root.destroy()
