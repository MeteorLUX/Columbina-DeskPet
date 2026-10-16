# -*- coding: utf-8 -*-
"""
学习计时功能回归测试
====================
验证学习状态机、计时 UI、记录保存、打字/语音指令拦截。
做法：真实 Tk + 手动驱动 _tick，不碰声卡与网络。
"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import importlib
import config
importlib.reload(config)   # 确保拿到最新常量（如 STUDY_DRAG_LINES）
import app


def drive(pet, secs):
    """泵 Tk 事件 secs 秒"""
    t0 = time.time()
    while time.time() - t0 < secs:
        pet.root.update()
        time.sleep(0.02)


def main():
    passed, failed = [], []

    def check(name, cond):
        (passed if cond else failed).append(name)
        print(('  ✓ ' if cond else '  ✗ ') + name)

    pet = app.DesktopPet()
    pet.root.withdraw()

    # 清空学习记录，避免影响测试
    pet._study_records = []

    # ============ T1：开始学习 ============
    print('T1 开始学习')
    pet.study_start()
    check('状态变为 studying', pet._study_state == 'studying')
    drive(pet, 1.2)  # 让 _study_tick 跑几秒
    check('计时 UI 已显示', pet._study_ui_visible)
    check('计时文本非空', bool(pet._study_last_text))
    check('计时已渲染为图片（无粉边）', pet._study_img is not None)
    elapsed = pet._study_now_elapsed()
    check('计时在累计', 0.5 < elapsed < 3.0)

    # ============ T2：重复开始学习 ============
    print('T2 重复开始学习')
    old_start = pet._study_start_ts
    pet.study_start()
    check('重复开始被提示且不重置', pet._study_start_ts == old_start)
    check('状态仍是 studying', pet._study_state == 'studying')

    # ============ T3：暂停学习 ============
    print('T3 暂停学习')
    drive(pet, 1.0)
    pet.study_pause()
    check('状态变为 paused', pet._study_state == 'paused')
    drive(pet, 1.2)
    check('暂停 UI 显示"已暂停"', '已暂停' in pet._study_last_text)
    e1 = pet._study_now_elapsed()
    drive(pet, 1.0)
    e2 = pet._study_now_elapsed()
    check('暂停期间计时不累计', abs(e2 - e1) < 0.1)

    # ============ T4：暂停时再次暂停 ============
    print('T4 暂停时再次暂停')
    pet.study_pause()
    check('状态仍是 paused', pet._study_state == 'paused')

    # ============ T5：继续学习 ============
    print('T5 继续学习')
    pet.study_start()
    check('状态恢复 studying', pet._study_state == 'studying')
    drive(pet, 1.0)
    e3 = pet._study_now_elapsed()
    check('继续后从暂停处累计', e3 > e1)

    # ============ T6：结束学习并保存记录 ============
    print('T6 结束学习并保存记录')
    # 先让学习时长超过最小保存阈值（60秒），避免被判定为过短
    pet._study_elapsed = 65.0
    pet._study_start_ts = time.time()  # 重置本段起点
    pet.study_stop()
    check('状态变为 idle', pet._study_state == 'idle')
    check('计时 UI 已隐藏', not pet._study_ui_visible)
    check('记录已保存', len(pet._study_records) == 1)
    rec = pet._study_records[0]
    check('记录有开始时间', bool(rec.get('start')))
    check('记录有结束时间', bool(rec.get('end')))
    check('记录时长正确', rec.get('duration') == 65)
    check('JSON 文件已写入', os.path.exists(app.STUDY_PATH))

    # ============ T7：过短不记录 ============
    print('T7 过短不记录')
    pet._study_records = []
    pet.study_start()
    drive(pet, 0.5)
    pet.study_stop()
    check('过短不保存记录', len(pet._study_records) == 0)

    # ============ T8：未学习时暂停/结束 ============
    print('T8 未学习时暂停/结束')
    pet.study_pause()
    check('未学习时暂停提示', pet._study_state == 'idle')
    pet.study_stop()
    check('未学习时结束提示', pet._study_state == 'idle')

    # ============ T9：打字指令拦截 ============
    print('T9 打字指令拦截')
    pet.entry.insert(0, '开始学习')
    pet.on_send()
    check('打字"开始学习"生效', pet._study_state == 'studying')
    check('输入框已清空', pet.entry.get() == '')

    pet.entry.insert(0, '暂停学习')
    pet.on_send()
    check('打字"暂停学习"生效', pet._study_state == 'paused')

    pet.entry.insert(0, '结束学习')
    pet.on_send()
    check('打字"结束学习"生效', pet._study_state == 'idle')

    # ============ T10：语音指令拦截 ============
    print('T10 语音指令拦截')
    r = pet._v_user_final_gate('开始学习')
    check('语音"开始学习"被拦截', r is True)
    drive(pet, 0.1)  # 让 _drain_ui_q 消费
    check('语音开始生效', pet._study_state == 'studying')

    r = pet._v_user_final_gate('暂停学习')
    check('语音"暂停学习"被拦截', r is True)
    drive(pet, 0.1)
    check('语音暂停生效', pet._study_state == 'paused')

    r = pet._v_user_final_gate('结束学习')
    check('语音"结束学习"被拦截', r is True)
    drive(pet, 0.1)
    check('语音结束生效', pet._study_state == 'idle')

    # ============ T11：退出时保存进行中记录 ============
    print('T11 退出时保存进行中记录')
    pet._study_records = []
    pet.study_start()
    drive(pet, 0.5)
    pet._study_elapsed = 70.0
    pet._study_start_ts = time.time()
    pet._study_on_quit()
    check('退出时自动保存', len(pet._study_records) == 1)
    check('退出后状态为 idle', pet._study_state == 'idle')

    # ============ T12：记录加载与聚合 ============
    print('T12 记录加载与聚合')
    # 手动构造跨天记录测试聚合
    pet._study_records = [
        {'start': '2026-10-13T10:00:00', 'end': '2026-10-13T10:30:00', 'duration': 1800},
        {'start': '2026-10-13T14:00:00', 'end': '2026-10-13T14:45:00', 'duration': 2700},
        {'start': '2026-10-14T09:00:00', 'end': '2026-10-14T09:20:00', 'duration': 1200},
        {'start': '2026-10-15T08:00:00', 'end': '2026-10-15T08:30:00', 'duration': 1800},
        {'start': '2026-10-15T15:00:00', 'end': '2026-10-15T15:45:00', 'duration': 2700},
    ]
    pet._save_study_records()
    pet._load_study_records()
    check('记录加载成功', len(pet._study_records) == 5)

    # 测试周聚合（2026-10-15 是周四，本周一是 10-12）
    from datetime import datetime, timedelta
    now = datetime(2026, 10, 15, 12, 0, 0)
    monday = now - timedelta(days=now.weekday())
    week_start = monday.strftime('%Y-%m-%d')
    week_end = (monday + timedelta(days=6)).strftime('%Y-%m-%d')
    daily = {}
    for r in pet._study_records:
        d = r.get('start', '')[:10]
        if week_start <= d <= week_end:
            daily[d] = daily.get(d, 0) + r.get('duration', 0)
    check('周聚合：10-13 总计 4500 秒', daily.get('2026-10-13') == 4500)
    check('周聚合：10-14 总计 1200 秒', daily.get('2026-10-14') == 1200)
    check('周聚合：10-15 总计 4500 秒', daily.get('2026-10-15') == 4500)

    # 测试月聚合
    monthly = {}
    for r in pet._study_records:
        m = r.get('start', '')[:7]
        if m:
            monthly[m] = monthly.get(m, 0) + r.get('duration', 0)
    check('月聚合：2026-10 总计 10200 秒', monthly.get('2026-10') == 10200)

    # ============ T13b：计时条不被走路/切姿态删掉 + 学习期间 work 姿态 ============
    print('T13b 计时条抗 delete(all) 与学习姿态')
    pet._study_records = []
    pet.study_start()
    check('开始学习后姿态为 work', pet.current_pose == 'work')
    drive(pet, 1.2)
    check('计时条已显示', pet._study_ui_visible)

    # 模拟走路动画帧：delete('all') 重画立绘 → 计时条应被立刻补画
    pet.canvas.delete('all')
    pet.canvas.create_image(75, 72, image=pet.cur_photo)
    pet._study_ui_repaint()
    item_ok = pet.canvas.type(pet._study_timer_item) is not None
    check('delete(all) 后计时条立刻补画', item_ok)

    # 模拟未知路径删除（没有调 repaint）：下一次 UI 刷新应自愈
    pet.canvas.delete(pet._study_timer_item)
    drive(pet, 1.3)   # 等 _study_tick 每秒刷新触发自愈
    item_ok2 = pet.canvas.type(pet._study_timer_item) is not None
    check('异常删除后计时条自愈', item_ok2)

    # 学习期间不会切 sleep / 姿态轮换（守卫生效）
    pet.last_interaction_time = 0.0      # 假装很久没交互
    drive(pet, 0.5)
    check('学习期间不自动切 sleep', pet.current_pose != 'sleep')

    pet._study_elapsed = 65.0
    pet._study_start_ts = time.time()
    pet.study_stop()
    check('结束学习后姿态恢复 smile', pet.current_pose == 'smile')
    check('结束后计时条消失', not pet._study_ui_visible)

    # 姿态规则：只有 studying 是 work，paused 必须是 smile
    pet.study_start()
    check('学习中为 work', pet.current_pose == 'work')
    pet.study_pause()
    check('暂停后变为 smile', pet.current_pose == 'smile')
    # 拖动/摸头结束回调：studying 回 work，paused/idle 回 smile
    pet.study_start()                    # 继续学习
    pet._set_pose('touch')               # 模拟拖动中
    pet._pose_after_interaction()
    check('拖动结束(学习中)回 work', pet.current_pose == 'work')
    pet.study_pause()
    pet._set_pose('touch')
    pet._pose_after_interaction()
    check('拖动结束(暂停中)回 smile', pet.current_pose == 'smile')
    check('拖动提醒文案存在', len(config.STUDY_DRAG_LINES) >= 3)
    pet._study_elapsed = 65.0
    pet._study_start_ts = time.time()
    pet.study_stop()

    # ============ T13：学习/唱歌面板与记录窗口 ============
    print('T13 学习/唱歌面板与记录窗口')
    pet.study_show_panel()
    check('学习面板已创建（月夜风格）', pet._study_win is not None)
    pet._study_win.destroy()
    pet._study_win = None

    pet.song_show_panel()
    check('唱歌面板已创建（月夜风格）', pet._song_win is not None)
    pet._song_win.destroy()
    pet._song_win = None

    pet.study_show_records()
    check('记录窗口已创建', pet._study_rec_win is not None)
    pet._study_rec_win.destroy()
    pet._study_rec_win = None

    # 清理测试文件
    pet.root.destroy()
    try:
        if os.path.exists(app.STUDY_PATH):
            os.remove(app.STUDY_PATH)
    except Exception:
        pass

    print()
    print(f'通过 {len(passed)} 项，失败 {len(failed)} 项')
    if failed:
        print('失败项：')
        for n in failed:
            print('  - ' + n)
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
