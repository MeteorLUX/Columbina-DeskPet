# -*- coding: utf-8 -*-
"""
唱歌 + 语音协调 回归测试（重构后）
==================================
复现并守住用户反馈的三个问题：
  1. 手动点歌后没法唱 / 会自动暂停
  2. 语音说「暂停」后再说「继续」续不上（_v_listening 被吞掉的口令卡死）
  3. 语音识别绿框一直卡着不消失；识别文字超过绿框后看不到最新内容

做法：真实 Tk + 假 SongEngine（不碰声卡），手动驱动 _tick。
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np

import song_control
import voice_control

# ---- 让歌曲"加载"瞬间完成，不碰真实文件与声卡 ----
song_control._load_song = lambda path: np.zeros(44100 * 30, dtype=np.float32)
song_control._analyze_phrases = lambda data: [(10.0, 10.5), (20.0, 20.5)]

import app  # noqa: E402


class FakeEngine:
    """替代 SongEngine：记录命令，句尾停/快速停立即回 paused 事件"""

    def __init__(self, pet):
        self.pet = pet
        self.state = 'idle'
        self.pos = 0.0
        self.cmds = []

    def start(self, data, bounds, pos=0.0, fade=1.0):
        self.cmds.append(('start', round(pos, 2)))
        self.state = 'playing'
        self.pos = pos

    def stop_at_boundary(self):
        self.cmds.append(('stopat',))
        self.state = 'stopping'
        self.pet._song_evt_q.put(('paused', self.pos + 5.0))
        # 真引擎发出 paused 事件时内部已真正停稳在 'paused'——
        # _resume_song 有引擎状态守卫（st not in ('paused','idle') 则拦截），
        # 假引擎必须对齐，否则所有续唱路径都会被守卫拦死。
        self.state = 'paused'

    def quick_stop(self, fade=0.45):
        self.cmds.append(('quickstop',))
        self.pet._song_evt_q.put(('paused', self.pos + 1.0))
        self.state = 'paused'   # 同上：对齐真引擎停稳状态

    def resume(self):
        self.cmds.append(('resume',))
        self.state = 'playing'

    def hard_stop(self):
        self.cmds.append(('hardstop',))
        self.state = 'idle'

    def shutdown(self):
        pass


def drive(pet, secs, probe=None):
    """泵 Tk 事件 secs 秒（_tick 由 root.after 自举，只调 update，
    直接调 _tick 会让 after 回调越积越多）；probe 每轮收集一次快照"""
    t0 = time.time()
    snaps = []
    while time.time() - t0 < secs:
        pet.root.update()
        if probe:
            snaps.append(probe())
        time.sleep(0.02)
    return snaps


def wait_state(pet, want, timeout=15.0):
    t0 = time.time()
    while time.time() - t0 < timeout:
        pet.root.update()
        if pet._sing_state == want:
            return True
        time.sleep(0.02)
    return False


def main():
    passed, failed = [], []

    def check(name, cond):
        (passed if cond else failed).append(name)
        print(('  ✓ ' if cond else '  ✗ ') + name)

    pet = app.DesktopPet()
    pet.root.withdraw()                      # 测试时不弹窗
    pet.song = FakeEngine(pet)               # 换掉真引擎（不碰声卡）
    pet._sing_ready_t = 0.0
    # 测试必须自给自足：walk_enabled 在真实 config.json 里可能是 False
    # （用户可能关掉了自由走动），依赖它会偶发失败。
    # 本套件要验证"暂停时她照常走动"，所以这里强制打开。
    pet.cfg['walk_enabled'] = True
    # 取消启动问候：__init__ 里 root.after(500, self._greet) 会在稍后弹一句
    # 问候气泡，把 dlg_state 变成 typing/waiting —— 于是走动被挡、
    # 「继续」也被 _resume_song 的 dlg_state 守卫挡住（那是对的，不是 bug）。
    # 不取消就会偶发失败。同时压住空闲闲聊，别让她中途自己说话。
    try:
        pet.root.after_cancel(pet._greet_job)
    except Exception:
        pass
    pet.last_interaction_time = time.time() + 3600
    pet._last_idle_chat = time.time() + 3600

    # ============ T1：手动点歌 → 开唱并保持 ============
    print('T1 手动点歌应进入 playing 且不自动暂停')
    pet._handle_song_request('我想听你唱歌了')
    ok = wait_state(pet, 'playing', timeout=20.0)
    check('点歌后进入 playing', ok)
    check('进入播放器模式(_sing_manual)', pet._sing_manual)
    states = drive(pet, 8.0, probe=lambda: pet._sing_state)
    check('8 秒内一直保持 playing（无自动暂停）', all(s == 'playing' for s in states))
    check('引擎没有收到 stopat/quickstop',
          not any(c[0] in ('stopat', 'quickstop') for c in pet.song.cmds))

    # ============ T2：语音「暂停」→ 保持 → 语音「继续」 ============
    print('T2 语音口令：暂停保持、继续能续上')
    pet._v_speech_start()                    # playing 中开口应被忽略（防歌声串扰）
    check('playing 中 speech_start 被吞（_v_listening 仍 False）',
          pet._v_listening is False)
    r = pet._v_user_final_gate('暂停')
    check('playing 中「暂停」被口令拦截', r is True)
    ok = wait_state(pet, 'paused', timeout=5.0)
    check('「暂停」后停在句尾(paused)', ok and pet._sing_hold)
    states = drive(pet, 6.0, probe=lambda: pet._sing_state)
    check('6 秒内不自动续唱', all(s == 'paused' for s in states))

    # 暂停只停歌、不冻人：她应照常待机走动，且走动不会把歌拉起来
    pet._walk_next_trigger = 0.0
    t0 = time.time()
    while time.time() - t0 < 8.0 and pet.walk_target_x is None:
        pet.root.update()
        time.sleep(0.02)
    check('暂停期间她照常待机走动', pet.walk_target_x is not None)
    check('走动不会拉起被暂停的歌', pet._sing_state == 'paused')

    pet._v_speech_start()                    # 暂停中开口：要能被听到（口令）
    r = pet._v_user_final_gate('继续')
    check('paused 中「继续」被口令拦截', r is True)
    ok = wait_state(pet, 'playing', timeout=5.0)
    check('「继续」后恢复 playing', ok)
    check('口令吞掉后 _v_listening 不卡死', pet._v_listening is False)
    check('引擎收到了 resume', any(c[0] == 'resume' for c in pet.song.cmds))

    # ============ T3：歌声/噪声在 starting 间隙不得触发回复（自动暂停根源） ============
    print('T3 切歌间隙的串扰不得打断播放')
    pet.song.cmds.clear()
    pet._song_evt_q.put(('finished', None))  # 一首唱完 → starting 等下一首
    ok = wait_state(pet, 'starting', timeout=5.0)
    check('唱完自动接下一首(starting)', ok)
    pet._v_speech_start()                    # 间隙里的 VAD 误触发
    r = pet._v_user_final_gate('啦啦啦')      # 串进来的"歌词"
    check('starting 中非口令语音被吞掉', r is True)
    check('starting 中不产生回复(_rx_active False)', pet._rx_active is False)
    check('starting 中 _v_listening 不被置位', pet._v_listening is False)
    ok = wait_state(pet, 'playing', timeout=10.0)
    check('下一首正常开唱', ok)
    states = drive(pet, 5.0, probe=lambda: pet._sing_state)
    check('之后保持 playing 不自动暂停', all(s == 'playing' for s in states))

    # ============ T4：绿框显示 ============
    print('T4 语音识别绿框')
    pet._sing_state = 'off'                  # 退出唱歌上下文
    pet._sing_manual = False
    pet._v_speech_start()
    check('平常开口时绿框出现', pet._voice_shown is True)
    long_text = '今天我们一起去很远很远的地方旅行吧你觉得怎么样我真的很好奇'
    pet._voice_show('🎤 ' + long_text)
    shown = pet.voice_label.cget('text')
    check('超长识别文字被截尾（保留最新内容）',
          len(shown) <= 24 and shown.endswith('好奇'))
    # 说完但识别为空（太短被丢弃）→ speech_end 也要负责清框
    pet._voice_show('🎤 嗯')
    pet._v_speech_end()
    drive(pet, 4.2)
    check('无 final 的短句后绿框自动消失', pet._voice_shown is False)

    # ============ T5：随机模式「走走唱唱」回归 ============
    print('T5 随机模式走走唱唱')
    import random as _rnd
    old_random = _rnd.random
    _rnd.random = lambda: 0.0                # 强制命中 SING_PROB
    try:
        pet.cfg['walk_enabled'] = True
        pet._sing_ready_t = 0.0
        pet._sing_next_roll = 0.0
        pet.dlg_state = 'idle'
        pet.walk_target_x = 500              # 正在走
        ok = wait_state(pet, 'asking', timeout=3.0)
        check('走路掷骰触发 asking', ok)
        # 新行为：随机询问后**必须等用户点「想」**才会开唱（不选则超时不开唱）。
        # 这里模拟用户点了「想」。
        pet._sing_choice(True)
        ok = wait_state(pet, 'playing', timeout=20.0)
        check('点了「想」之后开唱', ok and not pet._sing_manual)
        # 停下脚步 → 唱完这句停（同时推远下次走动，模拟"真的停下来了"）
        pet.walk_target_x = None
        pet._walk_next_trigger = time.time() + 60.0
        pet._sing_walk_grace = 0.0
        ok = wait_state(pet, 'paused', timeout=8.0)
        check('停下后停在句尾(paused)', ok)
        # 再次走动 → 续唱
        pet._walk_next_trigger = 0.0
        ok = wait_state(pet, 'playing', timeout=8.0)
        check('再次走动自动续唱', ok)
    finally:
        _rnd.random = old_random

    # ============ T6：随机询问的「想 / 不想」选项 ============
    print('T6 随机询问的想/不想选项')
    _rnd.random = lambda: 0.0                # 强制命中 SING_PROB
    try:
        # 选「不想」：不唱 + 10 分钟冷却
        pet._sing_state = 'off'
        pet._sing_manual = False
        pet._sing_ready_t = 0.0
        pet._sing_next_roll = 0.0
        pet.dlg_state = 'idle'
        pet.walk_target_x = 500
        ok = wait_state(pet, 'asking', timeout=3.0)
        check('再次掷骰触发 asking', ok)
        t0 = time.time()                     # 等问题打完字，选项弹出
        while time.time() - t0 < 5.0 and not pet._sing_opts_shown:
            pet.root.update()
            time.sleep(0.02)
        check('「想/不想」选项弹出', pet._sing_opts_shown)
        pet._sing_choice(False)
        check('选「不想」后不唱', pet._sing_state == 'off')
        check('选「不想」后 10 分钟内不再问',
              pet._sing_ready_t > time.time() + 590)
        check('选项已收起', not pet._sing_opts_shown)
        drive(pet, 5.0)                      # 让"那下次吧"气泡走完
        # 冷却期内掷骰不应触发
        pet._sing_next_roll = 0.0
        pet.dlg_state = 'idle'
        pet.walk_target_x = 500
        drive(pet, 1.0)
        check('冷却期内不再触发', pet._sing_state == 'off')
        # 选「想」：正常开唱
        pet._sing_ready_t = 0.0
        pet._sing_next_roll = 0.0
        pet.walk_target_x = 500
        ok = wait_state(pet, 'asking', timeout=3.0)
        check('冷却结束后能再次触发', ok)
        t0 = time.time()
        while time.time() - t0 < 5.0 and not pet._sing_opts_shown:
            pet.root.update()
            time.sleep(0.02)
        pet._sing_choice(True)
        ok = wait_state(pet, 'playing', timeout=15.0)
        check('选「想」后开唱', ok)
    finally:
        _rnd.random = old_random

    pet.root.destroy()

    print()
    print(f'通过 {len(passed)} 项，失败 {len(failed)} 项')
    if failed:
        print('失败项：')
        for n in failed:
            print('  - ' + n)
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
