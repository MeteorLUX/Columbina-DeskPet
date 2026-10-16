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
import study as study_mod
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

    def check(name, cond, extra=''):
        """extra 是可选的补充信息（失败时方便定位）"""
        (passed if cond else failed).append(name)
        tail = f'   {extra}' if extra else ''
        print(('  ✓ ' if cond else '  ✗ ') + name + tail)

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

    # ==================== T9 学习记录持久化（用户报过的问题）====================
    # 用户反馈："我学了十分钟以上当日记录看不到，每次重启之前学习的记录也没有了"
    #
    # 三个根因（都已修）：
    #   1. 只在菜单退出时保存 -> 直接关程序整段丢失
    #   2. "最初开始时间"用 start_ts - elapsed 反推（继续时 start_ts 会被重置）
    #      -> 暂停/继续后开始时间错位，严重时落到前一天 -> 当日列表看不见
    #   3. 没有崩溃恢复：进行中的学习只存在内存里
    print('\nT9 学习记录持久化：退出保存 / 暂停继续 / 崩溃恢复')
    import datetime as _dt
    import json as _json
    import tempfile as _tempfile
    import shutil as _shutil

    _tmp = _tempfile.mkdtemp(prefix='pet_study_persist_')
    _rec = os.path.join(_tmp, 'study_records.json')
    # 【重要】STUDY_PATH 是 `from config import *` 的**导入快照**：
    # app.STUDY_PATH 和 study.STUDY_PATH 是两个独立名字。
    # 只改 app. 的话 study.py 里读写用的还是旧值 -> 文件写不出来（踩过）。
    _orig_study_path = app.STUDY_PATH
    _orig_study_path2 = study_mod.STUDY_PATH
    app.STUDY_PATH = _rec
    study_mod.STUDY_PATH = _rec
    _today = _dt.datetime.now().strftime('%Y-%m-%d')

    check('有自动落盘间隔常量（不能只靠退出保存）',
          hasattr(config, 'STUDY_AUTOSAVE_SEC')
          and config.STUDY_AUTOSAVE_SEC > 0,
          str(getattr(config, 'STUDY_AUTOSAVE_SEC', None)))

    # ---- 场景 1：学 12 分钟 -> 结束，记录应今日可见且时长正确 ----
    pet._study_records = []
    pet._study_state = 'idle'
    pet.study_start()
    pet._study_origin_ts -= 12 * 60
    pet._study_start_ts -= 12 * 60
    pet.study_stop()
    _r = pet._study_records[-1]
    check('结束后写入记录', len(pet._study_records) == 1, str(len(pet._study_records)))
    check('时长正确（12 分钟）', abs(_r['duration'] - 720) < 3, str(_r['duration']))
    check('记录的开始时间在**今天**（当日列表能看见）',
          _r['start'].startswith(_today),
          f"{_r['start']} vs {_today}")
    check('结束后文件里没有残留 live 状态',
          (_json.load(open(_rec, encoding='utf-8')).get('live') is None))

    # ---- 场景 2：暂停 -> 隔很久 -> 继续，开始时间必须仍是"最初开始" ----
    pet._study_records = []
    pet._study_state = 'idle'
    pet.study_start()
    pet._study_origin_ts -= 10 * 60      # 已学 10 分钟
    pet._study_start_ts -= 10 * 60
    pet.study_pause()
    check('暂停时已落盘', os.path.exists(_rec))
    _live = _json.load(open(_rec, encoding='utf-8')).get('live') or {}
    check('暂停状态被记录到 live', _live.get('state') == 'paused', str(_live))
    check('暂停时 live.elapsed 约 600 秒',
          abs(float(_live.get('elapsed') or 0) - 600) < 5,
          str(_live.get('elapsed')))
    # 模拟"隔了 2 小时"再继续，然后又学 5 分钟
    pet._study_origin_ts -= 2 * 3600
    pet.study_start()
    pet._study_start_ts -= 5 * 60
    pet.study_stop()
    _r = pet._study_records[-1]
    check('暂停+继续的时长是 15 分钟（累加正确）',
          abs(_r['duration'] - 900) < 5, str(_r['duration']))
    check('开始时间是**最初**那次（不是继续时刻）',
          _r['start'].startswith(_today),
          f"{_r['start']}")
    # 【断言修正】不能断言 (end - start) == duration ——
    # 中间"隔了 2 小时"的暂停不计入 duration，但墙上时钟会拉开。
    # 那样断言等于要求"暂停也算学习时长"，是错的（我第一版就这么写错了）。
    #
    # 真正要防的回归是：**开始时间被反推算法带偏**。
    # 旧 bug 里 start_ts 在继续时被重置、再减去 elapsed，
    # 会让开始时间漂到"继续时刻 - 全部有效时长"之前。
    # 所以断言：记录里的 start 不早于我们设定的真实 origin。
    _origin_dt = _dt.datetime.fromtimestamp(pet._study_origin_ts)
    _sd = _dt.datetime.strptime(_r['start'], '%Y-%m-%dT%H:%M:%S')
    check('开始时间没有被反推算法带偏（不早于真实 origin）',
          _sd >= _origin_dt - _dt.timedelta(seconds=2),
          f"记录 start={_r['start']}  vs 真实 origin="
          f"{_origin_dt.strftime('%Y-%m-%dT%H:%M:%S')}")
    check('暂停的 2 小时没有计入学习时长',
          _r['duration'] < 20 * 60,
          f"duration={_r['duration']}s（若把暂停算进去会 >7200）")

    # ---- 场景 3：正在学习时直接关程序（走 _study_on_quit 兜底）----
    pet._study_records = []
    pet._study_state = 'idle'
    pet.study_start()
    pet._study_origin_ts -= 11 * 60
    pet._study_start_ts -= 11 * 60
    pet._study_last_save = 0
    pet._study_tick(time.time())          # 触发周期落盘
    _live = _json.load(open(_rec, encoding='utf-8')).get('live') or {}
    check('学习中也周期性落盘（崩溃最多丢几秒）',
          _live.get('state') == 'studying', str(_live))
    check('live 里的时长含**进行中那一段**',
          float(_live.get('elapsed') or 0) > 600,
          str(_live.get('elapsed')))
    pet._study_on_quit()                  # 等价于 atexit 兜底
    check('退出兜底把进行中的学习结算成记录',
          len(pet._study_records) == 1, str(len(pet._study_records)))
    _r = pet._study_records[-1]
    check('结算时长约 11 分钟', abs(_r['duration'] - 660) < 20, str(_r['duration']))
    check('结算记录标记了 auto', _r.get('auto') is True, str(_r))
    check('结算后 live 清空',
          (_json.load(open(_rec, encoding='utf-8')).get('live') is None))

    # ---- 场景 4：崩溃后重启，未完成的学习应被恢复 ----
    _d = _json.load(open(_rec, encoding='utf-8'))
    _d['sessions'] = []
    _d['live'] = {'state': 'studying',
                  'origin_ts': time.time() - 20 * 60,
                  'start_ts': time.time() - 20 * 60,
                  'elapsed': 1200.0,
                  'saved_at': time.time() - 30}
    _json.dump(_d, open(_rec, 'w', encoding='utf-8'), ensure_ascii=False)
    # 直接调加载（不新建实例，避免一个进程两个 Tk）
    pet._study_state = 'idle'
    pet._study_elapsed = 0.0
    pet._load_study_records()
    check('重启后恢复了未完成的学习',
          pet._study_state == 'studying' and pet._study_elapsed >= 1200,
          f'state={pet._study_state} elapsed={pet._study_elapsed:.0f}')
    check('标记为"已恢复"（会告诉她一声）', pet._study_resumed is True)
    # 接着学 3 分钟再结束，总时长应是 23 分钟
    pet._study_start_ts -= 3 * 60
    pet.study_stop()
    _r = pet._study_records[-1]
    check('恢复后累加正确（20+3=23 分钟）',
          abs(_r['duration'] - 1380) < 20, str(_r['duration']))
    check('恢复后的记录也在今天', _r['start'].startswith(_today), _r['start'])

    # 还原
    app.STUDY_PATH = _orig_study_path
    study_mod.STUDY_PATH = _orig_study_path2
    pet._study_state = 'idle'
    pet._study_elapsed = 0.0
    pet._study_origin_ts = 0.0
    _shutil.rmtree(_tmp, ignore_errors=True)

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
