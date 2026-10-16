# -*- coding: utf-8 -*-
"""验证新的姿态规则（临时）
聆听：问句 -> think；其余 -> smile
回话：默认 smile；负面 -> walk1；高兴 -> happy
"""
import os, sys, time
# 本文件在 tests/ 下，所以项目根要**往上退一层**
BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)
import tkinter as tk
import app as oc
import config as _cfg

OK = True
def check(label, cond, extra=''):
    global OK
    if not cond: OK = False
    print(f"  [{'PASS' if cond else 'FAIL'}] {label} {extra}")

app = oc.DesktopPet.__new__(oc.DesktopPet)
app.root = tk.Tk(); app.root.withdraw()
# 新结构：状态初始化已移到各功能模块的 setup_* 里
app.voice = None; app.voice_on = False
app.setup_voice()
app.setup_song()
app.setup_study()      # 学习计时状态（新增功能，别忘了）
app.setup_physics()    # 物理下落状态（新增功能，别忘了）
app.cfg = _cfg.Config()

poses = []
app.current_pose = 'smile'
app.images = {'walk1': object(), 'smile': object(), 'think': object(), 'happy': object()}
app._set_pose = lambda n: (poses.append(n), setattr(app, 'current_pose', n))
app._add_affinity = lambda d: None
app._hide_bubble = lambda: None
app._reset_idle_timer = lambda n=None: None
app._voice_show = lambda t: None
app._voice_clear = lambda: None
app._voice_log = lambda m: None
app.bubble = tk.Frame(app.root); app.bubble_label = tk.Label(app.bubble); app.bubble_label.pack()
app._rx_show_bubble = lambda: None
app._rx_active = False; app._rx_buf = ''; app._rx_pos = 0; app._rx_more = False
app._rx_sent = ''; app._rx_shown = 0; app._rx_queue = []
app._rx_delay_ms = None
app._voice_user_text = ''; app._voice_last_update = 0.0
app.voice_on = True
app._dragging = False; app.is_generating = False
app.dlg_state = 'idle'; app.dlg_queue = []; app.dlg_wait_start = 0.0
app.dlg_fade_start = 0.0; app.dlg_last_char_t = 0.0

print('=== 1. 问句判断（决定聆听时用不用 think）===')
q_cases = [
    ('你吃饭了吗？', True),
    ('今天天气怎么样', True),
    ('你为什么睡不着', True),
    ('这是什么呀', True),
    ('你几点起床呢', True),
    ('你好', False),
    ('我今天很开心', False),
    ('早点休息', False),
    ('月亮很安静', False),
]
for text, expect in q_cases:
    got = app._is_question(text)
    check(f'{"问句" if expect else "陈述"}: {text!r} -> {got}', got == expect,
          '' if got == expect else f'(期望 {expect})')

print('\n=== 2. 聆听姿态：问句 think / 陈述 smile ===')
for text, expect_pose in [('你吃饭了吗？', 'think'), ('今天天气怎么样', 'think'),
                          ('我今天很开心', 'smile'), ('早点休息', 'smile')]:
    poses.clear()
    app._v_speech_start()          # 开口（此时还不知道内容）
    app._v_partial(text)           # 识别中间结果 -> 应据此定姿态
    got = app.current_pose
    check(f'{text!r} -> {got}', got == expect_pose,
          '' if got == expect_pose else f'(期望 {expect_pose})')

poses.clear()
app._v_user_final('你今天过得怎么样')
check('说完时按问句定姿态 think', app.current_pose == 'think', f'={app.current_pose}')

print('\n=== 3. 回话姿态：默认 smile ===')
poses.clear()
app._v_delta(None, '……嗯。')
check('她开始回话 -> smile', 'smile' in poses, f'poses={poses}')

print('\n=== 4. 情绪三分类 ===')
emo_cases = [
    ('月亮上，什么都没有。', 'neutral'),
    ('……嗯。', 'neutral'),
    ('我很高兴见到你。', 'positive'),
    ('欢迎你回来。', 'positive'),
    ('谢谢你陪我。', 'positive'),
    ('（轻声哼唱）♪ 这样很好呢~', 'positive'),
    ('有点难过。', 'negative'),
    ('我也很孤独。', 'negative'),
    ('对不起。', 'negative'),
    ('不用了，我自己来。', 'negative'),
]
for text, expect in emo_cases:
    got = app._emotion(text)
    check(f'{text[:20]!r} -> {got}', got == expect,
          '' if got == expect else f'(期望 {expect})')

print('\n=== 5. 整轮结束后的收尾动作 ===')
def run_round(reply, label):
    poses.clear()
    app._rx_active = False; app._rx_buf = ''; app._rx_pos = 0
    app._rx_more = False; app._rx_sent = ''; app._rx_shown = 0
    app._rx_queue = []; app.dlg_state = 'idle'; app.current_pose = 'think'
    app._v_delta(None, reply)
    app._v_done(reply)
    t = time.time()
    app._rx_tick(now=t)
    for i in range(1, 300):
        app._rx_tick(now=t + i * 0.05)
        if app.dlg_state == 'idle' and not app._rx_active:
            break
    return poses[:]

p_neutral = run_round('月亮上，什么都没有。', '中性')
check('中性 -> smile', p_neutral and p_neutral[-1] == 'smile', f'poses={p_neutral}')

p_pos = run_round('我很高兴见到你。', '高兴')
check('高兴 -> happy', 'happy' in p_pos, f'poses={p_pos}')

p_neg = run_round('有点难过，月亮上只有我一个人。', '负面')
check('负面 -> walk1', p_neg and p_neg[-1] == 'walk1', f'poses={p_neg}')

print('\n=== 6. 回话过程中不应该是 think（她答话时用 smile）===')
poses.clear()
app._rx_active = False; app._rx_buf = ''; app._rx_pos = 0
app._rx_more = False; app._rx_sent = ''; app._rx_shown = 0
app._rx_queue = []; app.dlg_state = 'idle'; app.current_pose = 'think'
app._v_delta(None, '好的，我知道了。')
mid_pose = app.current_pose
check('第一片到达时机态变为 smile', mid_pose == 'smile', f'={mid_pose}')

# ================================================ 行走守卫
print('\n=== 7. 什么时候走 / 什么时候站定 ===')
import random as _r
app.root.winfo_screenwidth = lambda: 1920
app.root.winfo_x = lambda: 800
app.root.winfo_y = lambda: 100
app.cfg['walk_enabled'] = True
app._walk_next_trigger = 0
app._walk_base_y = 100
app._walk_last_step_t = 0.0
app._walk_r = []; app._walk_l = []; app._walk_anim_i = 0
app._walk_anim_cycle = None

_orig = _r.randint
_r.randint = lambda a, b: 1500     # 固定目标，避免"太近被推迟"的假失败


def walk_now(voice_on, listening, rx_active, dlg_state='idle'):
    app.voice_on = voice_on
    app._v_listening = listening
    app._rx_active = rx_active
    app.dlg_state = dlg_state
    app.walk_target_x = None
    app._walk_next_trigger = 0
    app._update_walk(now=time.time())
    return app.walk_target_x


check('全双工关闭时待机 -> 走动',
      walk_now(False, False, False) == 1500)
check('全双工开着、没人在说话 -> 仍然走动（她要自己干自己的事）',
      walk_now(True, False, False) == 1500)
check('用户正在说话 -> 站定不动',
      walk_now(True, True, False) is None)
check('她正在回话 -> 站定不动',
      walk_now(True, False, True) is None)
check('回话中(即使 listening 已释放) -> 站定不动',
      walk_now(True, False, True) is None)
_r.randint = _orig

print('\n=== 8. 空闲时不该出现 think（think 只属于"听到问题/有疑问"）===')
# 全双工开着但没人在说话：think 只能是"上一轮聆听问句"留下的，
# 不该由任何轮换逻辑主动产生。
app.voice_on = True
app._v_listening = False
app._rx_active = False
app.dlg_state = 'idle'
app.current_pose = 'smile'
poses.clear()

# 模拟主时钟跑 30 秒（含原有待机轮换）
app._idle_switch_next = 0
app.walk_target_x = None          # 轮换条件要求没在走路
app._dragging = False
app.is_generating = False
t = time.time()
for i in range(1, 600):
    now = t + i * 0.05
    # 复刻 _tick 里的待机轮换分支
    if (now > app._idle_switch_next
            and app.current_pose in ('smile', 'walk1')
            and 'walk1' in app.images
            and app.dlg_state == 'idle'
            and not app._dragging
            and not app.is_generating
            and app.walk_target_x is None):
        app._idle_switch_next = now + 9.0
        app._set_pose('walk1' if app.current_pose == 'smile' else 'smile')

check('空闲轮换不产生 think', 'think' not in poses, f'poses={set(poses)}')
check('空闲时在 smile/walk1 之间轮换',
      poses and set(poses) <= {'smile', 'walk1'}, f'poses={set(poses)}')

# 问句才应触发 think
poses.clear()
app._listen_pose('你吃饭了吗？')
check('听到问句 -> think', app.current_pose == 'think', f'={app.current_pose}')
poses.clear()
app._listen_pose('今天天气不错')
check('听到陈述 -> smile', app.current_pose == 'smile', f'={app.current_pose}')

app.root.destroy()
print('\n结果:', 'ALL_PASS' if OK else 'SOME_FAILED')
