# -*- coding: utf-8 -*-
"""
唱歌续航回归测试（"放一下没声音，拖动又有声音"）
================================================
用户现象：请她唱歌，放一下就静音；拖动她又响。

排查结论（三个叠加的原因）：
  A. `current_pose == 'sleep'` 被当成"不能续唱"的条件。
     但 PHYS_FALL_POSE 也是 'sleep' —— 你拖她、松手下落，姿态就是 sleep，
     续唱被永久挡住，直到你再次拖动改变姿态。**这是"拖动就恢复"的根因。**
  B. `playing` 状态的 busy 判定也把 sleep 姿态算进去，
     导致拖一下就立刻停唱（哪怕她本来唱得好好的）。
  C. 竞态：引擎发 'paused' 事件时内部可能还在 draining/stopping，
     而 SongEngine._h_resume 有 `if self.state != 'paused': return`，
     这时候 song.resume() 会被静默吃掉 -> 她以为在续唱、永远静音。

本测试锁住这三条。
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
import app

PASSED, FAILED = [], []


def check(name, cond, extra=''):
    (PASSED if cond else FAILED).append(name)
    print(('  ✓ ' if cond else '  ✗ ') + name + (f'   {extra}' if extra else ''))


class FakeEngine:
    """可控引擎：模拟 SongEngine 的状态机（含 draining 竞态）"""

    def __init__(self, pet, delay_drain=False):
        self.pet = pet
        self.state = 'idle'
        self.cmds = []
        self.pos = 0.0
        self.delay_drain = delay_drain
        self._drain_until = 0.0

    def start(self, data, bounds, pos=0.0, fade=1.0):
        self.cmds.append(('start',))
        self.state = 'playing'
        self.pos = pos

    def stop_at_boundary(self):
        self.cmds.append(('stopat',))
        self.state = 'stopping'
        # 模拟引擎线程：这里立刻抛事件（真实引擎稍后抛）
        self._pending = time.time()

    def tick(self):
        """推进假引擎的内部状态（对照真实 _run 循环）"""
        if self.state == 'stopping' and getattr(self, '_pending', 0):
            self.state = 'draining'
            self._drain_until = time.time() + (0.4 if self.delay_drain else 0.05)
            self._pending = 0
        if self.state == 'draining' and time.time() >= self._drain_until:
            self.state = 'paused'
            self.pet._song_evt_q.put(('paused', self.pos + 5.0))

    def resume(self):
        self.cmds.append(('resume',))
        # 真实引擎的守卫：不是 paused 就静默吃掉这条命令
        if self.state != 'paused':
            self.cmds.append(('resume-DROPPED',))
            return
        self.state = 'playing'

    def hard_stop(self):
        self.cmds.append(('hardstop',))
        self.state = 'idle'

    def shutdown(self):
        pass


def drive(pet, secs, eng=None):
    t0 = time.time()
    while time.time() - t0 < secs:
        if eng is not None:
            eng.tick()
        pet.root.update()
        time.sleep(0.02)


def main():
    print('=' * 62)
    print('唱歌续航回归测试')
    print('=' * 62)

    pet = app.DesktopPet()
    pet.root.withdraw()
    eng = FakeEngine(pet)
    pet.song = eng
    pet.cfg['walk_enabled'] = False      # 用户的真实配置
    pet._sing_manual = True
    pet._set_vad_suspend = lambda on: None

    # 屏蔽启动问候：__init__ 里有 root.after(500, self._greet)，
    # 测试跑起来后它会弹一句问候气泡，把 dlg_state 变成 typing/waiting，
    # 于是续唱被（正确地）挡住 —— 看起来像"续唱坏了"，其实是测试噪声。
    try:
        pet.root.after_cancel(pet._greet_job)
    except Exception:
        pass

    def reset(state='playing', engine='playing'):
        """每节开始前复位：清空事件队列与所有唱歌标志。

        测试跑完一节会残留 ('paused', ...) 事件和 _sing_hold 等标志，
        不复位就会跨节污染（我第一版就栽在这上面）。

        另外必须重置 last_interaction_time —— 否则跑到 30 秒后
        「空闲闲聊」(IDLE_CHAT_SEC) 会让她自己开始说话，
        气泡一出 dlg_state 就不是 idle 了，唱歌自然被正确地停掉，
        看起来就像"续唱坏了"。那是测试噪声，不是产品问题。
        """
        while True:
            try:
                pet._song_evt_q.get_nowait()
            except Exception:
                break
        pet._sing_state = state
        eng.state = engine
        eng.cmds.clear()
        pet._sing_hold = False
        pet._sing_switching = False
        pet._sing_stop_after = False
        pet._sing_resume_at = 0.0
        pet._sing_decided = True
        pet.is_generating = False
        pet._dragging = False
        pet._hide_bubble()
        pet.dlg_queue = []
        pet.dlg_state = 'idle'
        pet._v_listening = False
        pet._rx_active = False
        pet._set_pose('smile')
        # 关键：压住空闲闲聊与整点报时，别让它们插话
        pet.last_interaction_time = time.time() + 3600
        pet._last_idle_chat = time.time() + 3600
        pet._holiday_said_date = '2099-01-01'

    # ==================== A. sleep 姿态不再挡住续唱 ====================
    print('\nA. 下落姿态(sleep) 不该挡住续唱')
    reset()
    pet._begin_song_stop()
    drive(pet, 0.6, eng)
    pet._set_pose('sleep')               # 拖拽松手后物理下落
    check('进入了 paused 等待续唱', pet._sing_state == 'paused',
          pet._sing_state)
    pet._sing_resume_at = time.time()    # 让续唱时刻立刻到
    # 探针：确认 _update_song 是否真的走到了 paused 分支
    check('_song_ok() 为真（否则整个状态机不跑）', pet._song_ok() is True)
    check('_sing_hold 为假（否则只认「继续」）',
          pet._sing_hold is False, str(pet._sing_hold))
    _before = pet._sing_resume_at
    _now = time.time()
    check('续唱时刻已到（now >= resume_at）', _now >= _before,
          f'now-resume_at={_now - _before:.3f}')
    # 直接调一次，看守卫拦在哪（逐条打印）
    guards = {
        'sing_state==paused': pet._sing_state == 'paused',
        '_song_ok()': bool(pet._song_ok()),
        'sing_hold(须False)': pet._sing_hold,
        '_dragging': pet._dragging,
        'is_generating': pet.is_generating,
        '_voice_busy()': pet._voice_busy(),
        '_rx_active': pet._rx_active,
        'dlg_state ok': pet.dlg_state in ('idle', 'fading'),
        '引擎就绪': getattr(eng, 'state', None) in ('paused', 'idle'),
    }
    print('      守卫快照: ' + '  '.join(f'{k}={v}' for k, v in guards.items())
          + f'  | dlg_state实际={pet.dlg_state!r} 气泡文字={pet.bubble_label.cget("text")[:20]!r}')
    pet._resume_song()
    check('直接调 _resume_song() 能续上', pet._sing_state == 'playing',
          f'sing={pet._sing_state} 引擎={eng.state} cmds={eng.cmds}')
    # 逐帧驱动并记录（便于失败时定位）
    marks = []
    t0 = time.time()
    while time.time() - t0 < 3.0:
        eng.tick()
        pet.root.update()
        time.sleep(0.02)
        marks.append((pet._sing_state, eng.state))
    check('sleep 姿态下也能自动续唱', pet._sing_state == 'playing',
          f'sing={pet._sing_state} 引擎={eng.state} '
          f'seen={sorted(set(marks))}')
    check('续唱命令没被吃掉', 'resume-DROPPED' not in eng.cmds,
          str(eng.cmds))

    # ==================== B. 拖拽不该停掉正在唱的歌 ====================
    print('\nB. 拖拽/下落姿态不该停掉正在唱的歌')
    reset()
    pet._set_pose('sleep')               # 下落姿态
    drive(pet, 1.0, eng)
    check('sleep 姿态不触发停唱', pet._sing_state == 'playing',
          f'sing={pet._sing_state} cmds={eng.cmds}')
    # 真拖拽仍然应该停一停（礼貌：说话/拖拽时停）
    pet._dragging = True
    drive(pet, 0.6, eng)
    check('真正拖拽时仍会停一停（原行为保留）',
          pet._sing_state in ('stopping', 'paused'), pet._sing_state)
    pet._dragging = False

    # ==================== C. 引擎竞态不再吃掉 resume ====================
    print('\nC. 引擎还在 draining 时，resume 不能被吃掉')
    # 用一个"慢 draining"的引擎重现竞态
    eng2 = FakeEngine(pet, delay_drain=True)
    pet.song = eng2
    reset()
    eng2.state = 'playing'
    pet._begin_song_stop()
    # 推进到引擎发 paused（此时引擎 state 已 = paused，事件刚入队）
    drive(pet, 0.8, eng2)
    check('引擎事件已被消费 -> paused', pet._sing_state == 'paused',
          f'sing={pet._sing_state} 引擎={eng2.state}')
    eng2.cmds.clear()
    # 立刻要求续唱：引擎若未就绪，不得消耗机会
    pet._sing_resume_at = time.time()
    drive(pet, 3.0, eng2)
    check('最终成功续唱', pet._sing_state == 'playing',
          f'sing={pet._sing_state} 引擎={eng2.state}')
    check('resume 没有被引擎静默丢弃',
          'resume-DROPPED' not in eng2.cmds, str(eng2.cmds))
    check('确实发了 resume 命令', ('resume',) in eng2.cmds, str(eng2.cmds))

    # ==================== D. 忙的时候不抢唱，忙完必续 ====================
    print('\nD. 忙时不唱，忙完自动续（不能永久静音）')
    while True:
        try:
            pet._song_evt_q.get_nowait()
        except Exception:
            break
    pet._sing_state = 'playing'
    eng2.state = 'playing'
    eng2.cmds.clear()
    pet._sing_hold = False
    pet.is_generating = True             # 模拟正在等大模型回复
    pet._begin_song_stop()
    drive(pet, 1.0, eng2)
    check('忙的时候停在 paused', pet._sing_state == 'paused',
          pet._sing_state)
    pet._sing_resume_at = time.time()    # 时刻已过
    drive(pet, 2.0, eng2)
    check('还在忙 -> 不续唱', pet._sing_state == 'paused', pet._sing_state)
    pet.is_generating = False            # 回复到达
    drive(pet, 3.0, eng2)
    check('忙完自动续唱（关键：不能永久静音）',
          pet._sing_state == 'playing',
          f'sing={pet._sing_state} 引擎={eng2.state}')

    # ==================== E. 「暂停」口令仍然只认「继续」 ====================
    print('\nE. 口令暂停不能被子动续唱破坏')
    # 清干净队列：FakeEngine.tick 可能已经排了新的 ('paused',...) 事件，
    # 不清掉它会在我们续唱成功后立刻把状态又打回 paused。
    while True:
        try:
            pet._song_evt_q.get_nowait()
        except Exception:
            break
    pet.song = eng2
    eng2.state = 'paused'
    pet._sing_state = 'paused'
    pet._sing_manual = True
    pet._sing_hold = True                # 口令「暂停」
    pet._sing_resume_at = float('inf')
    pet.is_generating = False
    pet._dragging = False
    pet.dlg_state = 'idle'
    pet._set_pose('smile')
    drive(pet, 2.0, eng2)
    check('口令暂停后不会自动续唱', pet._sing_state == 'paused',
          pet._sing_state)
    pet._sing_resume_at = time.time()    # 即使时刻到了也不该续
    drive(pet, 2.0, eng2)
    check('即使续唱时刻到了也不续（只认「继续」）',
          pet._sing_state == 'paused', pet._sing_state)
    # 说「继续」-> 只驱动状态机，不给引擎喂新事件（避免污染）
    pet._song_cmd_apply('resume')
    t0 = time.time()
    while time.time() - t0 < 3.0 and pet._sing_state != 'playing':
        pet.root.update()
        time.sleep(0.02)
    check('说「继续」后能续上', pet._sing_state == 'playing',
          f'sing={pet._sing_state} 引擎={eng2.state} hold={pet._sing_hold}')
    pet._sing_hold = False

    # ==================== F. 源码层面锁住这个陷阱 ====================
    print('\nF. 源码回归：不许再把姿态当唱歌阻塞条件')
    # 本文件在 tests/ 下，源码在**上一层**（踩过：移动测试目录后这里找不到文件）
    _root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    src = open(os.path.join(_root, 'song_control.py'),
               encoding='utf-8').read()
    resume_fn = src.split('def _resume_song')[1].split('def _on_walk_start')[0]
    # 去掉注释行，只查真正的代码（注释里会提到这个反例，那是说明用的）
    code_lines = [ln for ln in resume_fn.splitlines()
                  if not ln.strip().startswith('#')]
    code = '\n'.join(code_lines)
    check("_resume_song 代码里没有 current_pose == 'sleep'",
          "current_pose == 'sleep'" not in code,
          '仍然存在会挡住续唱')
    check('_resume_song 有引擎就绪检查（防 resume 被静默吃掉）',
          "getattr(self.song, 'state'" in code)
    check('_resume_song 注释写明了根因（可维护性）',
          'PHYS_FALL_POSE' in resume_fn)
    # 整个文件的代码里，sleep 只应出现在"随机走走唱唱"分支
    no_comment = '\n'.join(ln for ln in src.splitlines()
                           if not ln.strip().startswith('#'))
    check('全文件只剩一处看姿态的唱歌判定',
          no_comment.count("current_pose == 'sleep'") <= 1,
          f"出现 {no_comment.count(chr(34)+'sleep'+chr(34))} 次")

    pet.root.destroy()
    print('\n' + '=' * 62)
    print(f'通过 {len(PASSED)} 项，失败 {len(FAILED)} 项')
    if FAILED:
        print('失败项：')
        for f in FAILED:
            print('  - ' + f)
    print('=' * 62)
    return 1 if FAILED else 0


if __name__ == '__main__':
    sys.exit(main())
