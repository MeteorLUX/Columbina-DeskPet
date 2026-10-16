# -*- coding: utf-8 -*-
"""
学习期间的特殊姿态回归测试
==========================
验证「学习时强制 work」不会盖掉更"当下"的表现：

  - 学习中被按住拖动   -> touch
  - 学习中触发重力下落 -> sleep（PHYS_FALL_POSE）
  - 落地 / 松手后      -> 回到 work

做法：真实 Tk + 手动驱动 _tick，不碰声卡与网络。
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
    """泵 Tk 事件 secs 秒（让 _tick 反复跑到）"""
    t0 = time.time()
    while time.time() - t0 < secs:
        pet.root.update()
        time.sleep(0.02)


def main():
    print('=' * 60)
    print('学习期间特殊姿态回归测试')
    print('=' * 60)

    pet = app.DesktopPet()
    pet.root.withdraw()
    pet._study_records = []

    # 取消启动问候：__init__ 里 root.after(500, self._greet)，而 _greet 会
    # `_set_pose('hi')` 再 after(1500) 切 smile。它的触发时刻（约 0.5~2.0s）
    # 会随机落在某条断言附近，把姿态改掉 —— 这是本测试约 50% 偶发失败的**真凶**。
    # 只靠"先 drive(2.6) 等它跑完"不可靠：那段等待是在 DesktopPet() 返回之后
    # 才开始算的，问候的 after 计时已经先启动了（而且是 root.after，越跑越快）。
    try:
        pet.root.after_cancel(pet._greet_job)
    except Exception:
        pass
    # 压住空闲闲聊，别让她中途自己说话改姿态
    pet.last_interaction_time = time.time() + 3600
    pet._last_idle_chat = time.time() + 3600

    # ---------------- 前提：学习中被强制 work ----------------
    print('\nT1 学习中默认保持 work')
    pet.study_start()
    drive(pet, 0.6)
    check('学习开始后姿态为 work', pet.current_pose == 'work',
          f'实际={pet.current_pose}')

    # 手动把姿态改掉，_tick 应该把它拉回 work
    pet._set_pose('smile')
    drive(pet, 0.6)
    check('学习中被改姿态会回到 work', pet.current_pose == 'work',
          f'实际={pet.current_pose}')

    # ---------------- 学习中拖动 -> touch ----------------
    print('\nT2 学习中被按住拖动 -> touch（不能被 work 盖掉）')
    pet._dragging = True
    pet._set_pose('touch')          # 模拟 on_motion 里做的事
    drive(pet, 0.8)                 # 让 _study_tick 跑很多轮
    check('拖动中保持 touch', pet.current_pose == 'touch',
          f'实际={pet.current_pose}')
    check('_study_pose_forced() 为 False', pet._study_pose_forced() is False)

    # 松手 -> 回到 work
    pet._dragging = False
    pet._pose_after_interaction()
    check('松手后回到 work', pet.current_pose == 'work',
          f'实际={pet.current_pose}')

    # ---------------- 学习中下落 -> sleep ----------------
    print('\nT3 学习中触发重力下落 -> sleep（不能被 work 盖掉）')
    pet._set_pose('work')
    # 模拟 physics 开始下落时做的事
    pet._physics_state = 'falling'
    pet._physics_v = 0.0
    pet._physics_t = time.time()
    pet._set_pose(config.PHYS_FALL_POSE)
    check('下落姿态用的是 PHYS_FALL_POSE', config.PHYS_FALL_POSE == 'sleep',
          f'PHYS_FALL_POSE={config.PHYS_FALL_POSE}')

    drive(pet, 0.8)                 # 关键：让 _study_tick 跑很多轮
    check('下落中保持 sleep', pet.current_pose == 'sleep',
          f'实际={pet.current_pose}')
    check('_study_pose_forced() 为 False', pet._study_pose_forced() is False)

    # ---------------- 落地 -> 回到 work ----------------
    print('\nT4 落地后回到 work')
    pet._physics_state = 'idle'
    pet._pose_after_interaction()
    check('落地后回到 work', pet.current_pose == 'work',
          f'实际={pet.current_pose}')
    drive(pet, 0.4)
    check('继续学习仍保持 work', pet.current_pose == 'work',
          f'实际={pet.current_pose}')

    # ---------------- 边缘走回中也不被 work 盖 ----------------
    print('\nT5 边缘走回（walking_in）中不被 work 盖掉')
    # 注意 1：physics 的 walking_in 在 walk_target_x 为 None 时会认为自己"走到了"
    #   而立刻转 idle，所以这里必须给一个真实目标，才能停在 walking_in 状态。
    # 注意 2：目标必须**足够远**。她实际走动很快（实测 ~560px/s），
    #   给 200px 会在 0.35 秒内走完 —— 到达后 physics 转 idle 并
    #   `after(200, _pose_after_interaction)` 把姿态恢复成 work（那是**正确**行为：
    #   走回完成后当然该回到 work）。于是 "!= work" 这条断言就成了时序竞态，
    #   约 50% 概率失败。给一个 0.6 秒内绝对走不到的距离才稳定。
    # 注意 3：_walk_next_trigger 推到很远，避免自主走动分支插进来改目标。
    #   目标要"屏内尽可能远"：给 +5000 会超出屏幕宽度，geometry 设置失败后
    #   physics 会直接把状态打成 idle（反而更不稳定）。
    # 注意 4：必须设 _walk_base_y！physics 用 `_walk_base_y + bob` 算纵坐标，
    #   不设就是旧值/垃圾值，geometry 会抛异常 -> walk_target_x 被清空、
    #   状态被打成 idle，断言随即失败（这才是本测试 50% 偶发的真凶）。
    pet._walk_next_trigger = time.time() + 3600
    _sw = pet.root.winfo_screenwidth()
    _x = pet.root.winfo_x()
    pet._walk_base_y = pet.root.winfo_y()
    pet.walk_target_x = (_sw - config.WIN_W - 20) if _x < _sw // 2 else 20
    pet._physics_state = 'walking_in'
    pet._set_pose('walk1')
    drive(pet, 0.6)
    check('走回中不被强制 work', pet.current_pose != 'work',
          f'实际={pet.current_pose}')
    check('走回中确实还在 walking_in',
          pet._physics_state == 'walking_in',
          f'实际={pet._physics_state} target={pet.walk_target_x}')
    pet._physics_state = 'idle'
    pet.walk_target_x = None

    # ---------------- 暂停学习时不该强制 work ----------------
    print('\nT6 暂停学习时不强制 work')
    pet.study_pause()
    pet._set_pose('smile')
    drive(pet, 0.5)
    check('暂停后保持 smile', pet.current_pose == 'smile',
          f'实际={pet.current_pose}')
    check('暂停时 _study_pose_forced() 为 False',
          pet._study_pose_forced() is False)

    # ---------------- 结束学习恢复常态 ----------------
    print('\nT7 结束学习恢复 smile')
    pet._study_elapsed = 65.0
    pet._study_start_ts = time.time()
    pet.study_stop()
    check('结束后姿态为 smile', pet.current_pose == 'smile',
          f'实际={pet.current_pose}')

    # 清理
    if os.path.exists(app.STUDY_PATH):
        try:
            os.remove(app.STUDY_PATH)
        except Exception:
            pass
    pet.root.destroy()

    print('\n' + '=' * 60)
    print(f'通过 {len(PASSED)} 项，失败 {len(FAILED)} 项')
    if FAILED:
        print('失败项：')
        for f in FAILED:
            print('  - ' + f)
    print('=' * 60)
    return 1 if FAILED else 0


if __name__ == '__main__':
    sys.exit(main())
