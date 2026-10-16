# -*- coding: utf-8 -*-
"""
唱歌询问「想/不想」等待策略 回归测试
====================================
核心要求（用户反馈）：
  随机问「想听我唱歌吗？」后，**不选就默认不唱**（以前会自己开唱）
  并且等待时间要够长

覆盖：
  T1 询问后不会自动开唱（气泡说完也不唱）
  T2 点「想」才会开唱
  T3 点「不想」不开唱，且一段时间内不再问
  T4 一直不选 -> 超时后默认不唱（而不是开唱）
  T5 选项在气泡淡出后仍然可见（不会刚冒出来就消失）
  T6 手动点歌不受影响（它本来就不该问）
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


def drive(pet, secs):
    t0 = time.time()
    while time.time() - t0 < secs:
        pet.root.update()
        time.sleep(0.02)


def main():
    print('=' * 62)
    print('唱歌询问等待策略 回归测试')
    print('=' * 62)

    pet = app.DesktopPet()
    pet.root.withdraw()
    # 拦掉真正开唱：只关心"会不会被推进到开唱"
    started = []
    pet._start_song = lambda: started.append(time.time())

    # ---------- T1 询问后不自动开唱 ----------
    print('\nT1 随机询问后，不选 -> 不该开唱')
    pet._ask_to_sing()
    check('进入 asking 状态', pet._sing_state == 'asking', pet._sing_state)
    check('初始未答应', pet._sing_accepted is False)
    if hasattr(pet, '_sing_opts_shown'):
        pass
    drive(pet, 2.0)          # 让气泡打字 + 淡出都走完
    started.clear()
    pet._on_dialog_done()    # 模拟气泡淡出结束的钩子
    drive(pet, 1.5)
    check('气泡说完也没开唱', pet._sing_state != 'starting', pet._sing_state)
    check('没有真的开唱', started == [], str(started))

    # ---------- T2 点「想」才开唱 ----------
    print('\nT2 明确点「想」-> 开唱')
    pet._ask_to_sing()
    drive(pet, 0.5)
    pet._sing_choice(True)
    check('标记为已答应', pet._sing_accepted is True)
    drive(pet, 2.0)
    check('推进到开唱', pet._sing_state in ('starting', 'playing'), pet._sing_state)
    check('真的调用了开唱', len(started) >= 1, str(len(started)))

    # 收拾干净
    pet._sing_state = 'off'
    started.clear()

    # ---------- T3 点「不想」 ----------
    print('\nT3 点「不想」-> 不开唱 + 冷却')
    pet._ask_to_sing()
    drive(pet, 0.5)
    t_before = time.time()
    pet._sing_choice(False)
    check('回到 off', pet._sing_state == 'off', pet._sing_state)
    check('未标记答应', pet._sing_accepted is False)
    check('设了冷却（不再马上问）',
          pet._sing_ready_t >= t_before + config.SING_DECLINE_BLOCK - 1,
          f'剩余 {pet._sing_ready_t - t_before:.0f}s')
    drive(pet, 1.0)
    check('没有开唱', started == [], str(started))

    # ---------- T4 一直不选 -> 超时默认不唱 ----------
    print('\nT4 一直不选 -> 超时后默认不唱（关键）')
    pet._sing_ready_t = 0
    pet._ask_to_sing()
    drive(pet, 0.5)
    check('在等选择', pet._sing_state == 'asking', pet._sing_state)
    # 把"问"的时间往前挪，模拟已经等了很久
    pet._sing_ask_t -= (config.SING_ASK_TIMEOUT + 5)
    pet.dlg_fade_start = min(pet.dlg_fade_start, pet._sing_ask_t) \
        if pet.dlg_fade_start else pet._sing_ask_t
    started.clear()
    drive(pet, 1.0)
    check('超时后回到 off', pet._sing_state == 'off', pet._sing_state)
    check('超时后没有开唱', started == [], str(started))
    check('超时后设了冷却',
          pet._sing_ready_t > time.time() + config.SING_TIMEOUT_BLOCK - 5,
          f'剩余 {pet._sing_ready_t - time.time():.0f}s')

    # ---------- T5 选项在气泡淡出后仍可见 ----------
    print('\nT5 选项不会刚冒出来就消失')
    pet._sing_ready_t = 0
    pet._ask_to_sing()
    # 模拟气泡已经淡出（dlg_state=idle），但还没等够 HOLD
    pet.dlg_state = 'idle'
    pet._sing_ask_t = time.time() - 1.0        # 只过了 1 秒
    pet.dlg_fade_start = time.time()
    pet._update_song(time.time())
    check('HOLD 期内不显示选项（还没到时机）',
          not getattr(pet, '_sing_opts_shown', False), '预期 False')
    # 超过 HOLD 秒后应该显示
    pet._sing_ask_t = time.time() - (config.SING_ASK_HOLD_SEC + 2)
    pet.dlg_fade_start = pet._sing_ask_t
    pet._update_song(time.time())
    check('超过 HOLD 后选项可见', pet._sing_opts_shown is True,
          f'_sing_opts_shown={getattr(pet, "_sing_opts_shown", None)}')
    pet._hide_sing_options()
    pet._sing_state = 'off'

    # ---------- T6 手动点歌不受影响 ----------
    print('\nT6 手动点歌：不该被"要等选择"拦住')
    started.clear()
    pet._sing_ready_t = 0
    # 手动点歌走 _handle_song_request；这里只验证它不会进 asking 等选择
    pet._sing_manual = True
    pet._sing_accepted = True
    pet._sing_state = 'asking'
    pet._on_dialog_done()
    check('手动模式的气泡说完直接开唱',
          pet._sing_state in ('starting', 'playing'), pet._sing_state)
    pet._sing_state = 'off'
    pet._sing_manual = False
    pet._sing_accepted = False

    # ---------- 参数合理性 ----------
    print('\nT7 等待参数合理性')
    # 注意：等待时长是可调参数（用户可能按自己手感调整），
    # 所以这里只查"是否设了值 + 行为是否正确"，不锁定具体数字。
    check('设了等待时长', config.SING_ASK_TIMEOUT > 0,
          f'{config.SING_ASK_TIMEOUT}s')
    check('气泡淡出后选项仍保留一会儿', config.SING_ASK_HOLD_SEC >= 1,
          f'{config.SING_ASK_HOLD_SEC}s')
    check('超时后有冷却', config.SING_TIMEOUT_BLOCK >= 60,
          f'{config.SING_TIMEOUT_BLOCK}s')
    check('有超时台词', bool(config.SING_TIMEOUT_LINE), config.SING_TIMEOUT_LINE)

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
