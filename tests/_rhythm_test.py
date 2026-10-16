# -*- coding: utf-8 -*-
"""
验证"逐句打字 + 每句停 2 秒"的节奏（临时）
==========================================
单一虚拟时钟，精确测量每句话的出现时刻与间隔。
"""
import os
import sys
import time

# 本文件在 tests/ 下，所以项目根要**往上退一层**
BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

import tkinter as tk
import app as oc
import config as _cfg

OK = True
def check(label, cond, extra=''):
    global OK
    if not cond:
        OK = False
    print(f"  [{'PASS' if cond else 'FAIL'}] {label} {extra}")


# ---------------- 最小环境 ----------------
app = oc.DesktopPet.__new__(oc.DesktopPet)
app.root = tk.Tk()
app.root.withdraw()
# 新结构：状态初始化已移到各功能模块的 setup_* 里
app.voice = None; app.voice_on = False
app.setup_voice()
app.setup_song()
app.setup_study()      # 学习计时状态（新增功能，别忘了）
app.setup_physics()    # 物理下落状态（新增功能，别忘了）
app.cfg = _cfg.Config()
app.dlg_state = 'idle'
app.dlg_queue = []
app.dlg_current = ""
app.dlg_index = 0
app.dlg_last_char_t = 0.0
app.dlg_wait_start = 0.0
app.dlg_fade_start = 0.0
app._rx_active = False
app._rx_buf = ''
app._rx_pos = 0
app._rx_more = True
app._rx_sent = ''
app._rx_shown = 0
app._rx_queue = []
app._rx_delay_ms = None          # 手动驱动
app._voice_last_update = 0.0
app._voice_user_text = ''
app.current_pose = 'smile'
app._dragging = False
app.is_generating = False
app.walk_target_x = None
app.images = {}
app._input_visible = False

app.bubble = tk.Frame(app.root, bg=oc.BUBBLE_BG)
app.bubble_label = tk.Label(app.bubble, text="")
app.bubble_label.pack()
app.voice_holder = tk.Frame(app.root)
app.voice_label = tk.Label(app.voice_holder, text="")
app.voice_label.pack()
app._voice_shown = False
app.input_holder = tk.Frame(app.root)
app.affinity_label = tk.Label(app.root, text="")
app._add_affinity = lambda d: None
app._set_pose = lambda n: None
app._hide_bubble = lambda: None
app._reset_idle_timer = lambda n=None: None

# 记录气泡文本变化
history = []
_orig_cfg = app.bubble_label.config
def spy_config(**kw):
    if 'text' in kw:
        history.append(kw['text'])
    return _orig_cfg(**kw)
app.bubble_label.config = spy_config

# ---------------- 单一虚拟时钟 ----------------
T0 = 1000000.0          # 任取一个起点
clock = [T0]

def advance(seconds, step=0.01):
    """把虚拟时钟往前推进 seconds，每 step 调一次 tick"""
    end = clock[0] + seconds
    while clock[0] < end:
        clock[0] += step
        app._rx_tick(now=clock[0])

def t_at():
    return clock[0] - T0


print('=' * 64)
print('场景：流式回复分 3 片到达，每片一句')
print('=' * 64)
sents = ['……嗯。', '今天的月亮很安静呢。', '你想坐到我旁边来吗？']

full = ''
first_seen = {}          # 句子 -> 首次完整出现的时刻
clock[0] = T0

# 第 1 片
full = sents[0]
app._v_delta(None, full)
check('开始接收', app._rx_active is True)
check('首句尚未启动（等第一次 tick）', app.dlg_state == 'idle', f'状态={app.dlg_state}')

advance(1.0)
for s in sents:
    for h in history:
        if h == s and s not in first_seen:
            first_seen[s] = None
# 精确记录：每 tick 后看是否等于完整句
clock[0] = T0
history.clear()
full = sents[0]
app._rx_active = False
app._v_delta(None, full)

marks = {}
while clock[0] < T0 + 1.2:
    clock[0] += 0.01
    app._rx_tick(now=clock[0])
    t = app.bubble_label.cget('text')
    for s in sents:
        if t == s and s not in marks:
            marks[s] = t_at()

print(f'  第 1 句完整出现于 {marks.get(sents[0], -1):.2f}s')
check('第 1 句在 1 秒内完整打出', sents[0] in marks, f'{marks.get(sents[0]):.2f}s')
check('第 1 句打完后处于等待', app.dlg_state == 'vwait', f'状态={app.dlg_state}')

# 第 2 片到达
full += sents[1]
app._v_delta(None, full)

t_first_done = clock[0] - T0
# 只推进 1.0 秒（< 2s 间隔），第二句不应完整出现
while clock[0] < T0 + t_first_done + 1.0:
    clock[0] += 0.01
    app._rx_tick(now=clock[0])
    t = app.bubble_label.cget('text')
    if t == sents[1] and sents[1] not in marks:
        marks[sents[1]] = t_at()
check('间隔不足 2 秒时第 2 句未完整出现', sents[1] not in marks,
      f'但已显示 {app.bubble_label.cget("text")!r}')

# 继续推进越过 2 秒
while clock[0] < T0 + t_first_done + 3.0:
    clock[0] += 0.01
    app._rx_tick(now=clock[0])
    t = app.bubble_label.cget('text')
    if t == sents[1] and sents[1] not in marks:
        marks[sents[1]] = t_at()

gap1 = marks.get(sents[1], -1) - marks.get(sents[0], -1)
print(f'  第 2 句完整出现于 {marks.get(sents[1], -1):.2f}s')
print(f'  句间隔（第1句完整 -> 第2句完整）: {gap1:.2f}s')

# 第 3 片 + 结束
full += sents[2]
app._v_delta(None, full)
app._v_done(full)
check('生成结束标记', app._rx_more is False)

while clock[0] < T0 + 30:
    clock[0] += 0.01
    app._rx_tick(now=clock[0])
    t = app.bubble_label.cget('text')
    if t == sents[2] and sents[2] not in marks:
        marks[sents[2]] = t_at()
    if app.dlg_state == 'idle' and sents[2] in marks:
        break

print()
for i, s in enumerate(sents, 1):
    check(f'第 {i} 句完整出现过', s in marks, f'于 {marks.get(s, -1):.2f}s  「{s}」')

if sents[1] in marks and sents[0] in marks:
    # 目标：打字时长 + 2 秒等待
    print(f'\n  第1句打字用时约 {(marks[sents[0]] - 0):.2f}s（{len(sents[0])} 字 x {_cfg.TYPING_INTERVAL}s）')
    print(f'  两段间隔 {gap1:.2f}s，设定等待 {_cfg.VOICE_HOLD_SEC}s + 打字时间')
    expect = _cfg.VOICE_HOLD_SEC + len(sents[1]) * _cfg.TYPING_INTERVAL
    check('间隔符合"停 2 秒 + 打字"的总时长', abs(gap1 - expect) <= 0.4,
          f'实测 {gap1:.2f}s vs 预期 {expect:.2f}s')

print('\n=== 打断 ===')
app._v_delta(None, '很长的一句话正在打')
advance(0.1)
app._v_speech_start()
check('打断后停止接收', app._rx_active is False)
check('打断后回到 idle', app.dlg_state == 'idle')
before = app.bubble_label.cget('text')
advance(5.0)
check('打断后 tick 不再改气泡', app.bubble_label.cget('text') == before)

app.root.destroy()
print('\n结果:', 'ALL_PASS' if OK else 'SOME_FAILED')
