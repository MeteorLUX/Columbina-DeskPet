# -*- coding: utf-8 -*-
"""复现：气泡在她说完后是否消失（临时）
完整序列：开启全双工 -> 她打招呼 -> 用户说话 -> 她的回复 -> 看气泡
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
app.root = tk.Tk()
app.root.withdraw()
# 新结构：状态初始化已移到各功能模块的 setup_* 里
app.voice = None; app.voice_on = False
app.setup_voice()
app.setup_song()
app.setup_study()      # 学习计时状态（新增功能，别忘了）
app.setup_physics()    # 物理下落状态（新增功能，别忘了）
app.cfg = _cfg.Config()
app.images = {'walk1': object(), 'smile': object(), 'think': object(),
              'happy': object(), 'work': object()}
app.current_pose = 'smile'
app._set_pose = lambda n: setattr(app, 'current_pose', n)
app._add_affinity = lambda d: None
app._reset_idle_timer = lambda n=None: None
app._voice_log = lambda m: None
app._voice_last_update = 0.0
app._voice_shown = False
app._input_visible = False
app._rx_active = False; app._rx_buf = ''; app._rx_pos = 0; app._rx_more = False
app._rx_sent = ''; app._rx_shown = 0; app._rx_queue = []
app._rx_delay_ms = None            # 手动驱动
# 本测试用假控件替换了 _build_ui，所以「由 _build_ui 初始化」的界面状态
# 需要在此补齐（真实程序里由 _build_ui 负责）。界面新增状态时记得同步这里。
app._sing_opts_shown = False       # 唱歌「想/不想」选项是否弹出（见 ui_widgets._build_ui）
app._voice_user_text = ''
app.voice_on = True
app._dragging = False; app.is_generating = False
app.dlg_state = 'idle'; app.dlg_queue = []; app.dlg_current = ''
app.dlg_index = 0; app.dlg_last_char_t = 0.0; app.dlg_wait_start = 0.0
app.dlg_fade_start = 0.0
app.walk_target_x = None; app._walk_next_trigger = 0
app.last_interaction_time = time.time(); app._last_idle_chat = time.time()
app._idle_switch_next = time.time() + 999; app._last_hourly_key = None

app.bubble = tk.Frame(app.root, bg=oc.BUBBLE_BG)
app.bubble_label = tk.Label(app.bubble, text="")
app.bubble_label.pack()
app.voice_holder = tk.Frame(app.root)
app.voice_label = tk.Label(app.voice_holder, text="")
app.voice_label.pack()
app.input_holder = tk.Frame(app.root)
app.entry = tk.Entry(app.input_holder)
app.affinity_label = tk.Label(app.root, text="")
app.canvas = tk.Canvas(app.root, width=10, height=10)

# 用真实 _hide_bubble 但记录调用
hide_calls = []


def hide():
    hide_calls.append(app._vt)
    print(f'      >>> _hide_bubble() 被调用 (t={app._vt-1000000:.2f}s)')
    oc.DesktopPet._hide_bubble(app)


app._hide_bubble = hide

bubble_placed = []
_orig_place = app.bubble.place
def place(**kw):
    bubble_placed.append(1)
    return _orig_place(**kw)
app.bubble.place = place
_orig_forget = app.bubble.place_forget
forgot = []
def forget():
    forgot.append(1)
    return _orig_forget()
app.bubble.place_forget = forget


def is_visible():
    try:
        return bool(app.bubble.winfo_manager())
    except Exception:
        return False


def drive(seconds, step=0.02, t0=None):
    """推进虚拟时钟。关键：把 time.time 也一起替换，
    否则 _rx_tick(注入时间) 和 _update_dialogue(真实时间) 会混用不同时间源。"""
    n = int(seconds / step)
    real_time = oc.time.time
    try:
        for i in range(n):
            app._vt += step
            oc.time.time = lambda: app._vt      # 让所有代码看到同一个虚拟时钟
            app._rx_tick(now=app._vt)
            app._update_dialogue()
    finally:
        oc.time.time = real_time
    return app._vt

app._vt = 1000000.0

print('=== 序列：她打招呼（气泡出现）===')
app._speak('……嗯，我在听。你随时可以说。')
drive(1.5)
print(f'  dlg_state={app.dlg_state}  气泡可见={is_visible()}  '
      f'文字={app.bubble_label.cget("text")[:20]!r}')
check('打招呼后气泡可见', is_visible())

print('\n=== 用户说话（应收起气泡）===')
app._v_speech_start()
print(f'  dlg_state={app.dlg_state}  气泡可见={is_visible()}')
check('用户开口后气泡收起', not is_visible())

app._v_partial('你好')
app._v_user_final('你好')
drive(0.3)
print(f'  说完: dlg_state={app.dlg_state} 气泡可见={is_visible()}')

print('\n=== 她的回复到达 ===')
app._v_delta(None, '……嗯。今天的月亮很安静呢。')
drive(0.5)
print(f'  首片后: dlg_state={app.dlg_state} 气泡可见={is_visible()} '
      f'文字={app.bubble_label.cget("text")!r}')
app._v_done('……嗯。今天的月亮很安静呢。')

# 逐帧追踪状态变化，找出到底有没有进 fading
prev = app.dlg_state
trace = []
real_time = oc.time.time
_orig_upd = app._update_dialogue


def traced_upd():
    """包一层，记录进入/退出时的状态与是否报错"""
    before = app.dlg_state
    try:
        _orig_upd()
    except Exception as e:
        print(f'      !!! _update_dialogue 抛异常: {type(e).__name__}: {e}')
        raise
    after = app.dlg_state
    if before == 'fading':
        print(f'      [upd] 进入时=fading 退出时={after} '
              f'fade_start={app.dlg_fade_start-1000000:.2f} '
              f'now={oc.time.time()-1000000:.2f} '
              f'elapsed={oc.time.time()-app.dlg_fade_start:.3f}')


app._update_dialogue = traced_upd

_orig_tick = app._rx_tick if hasattr(app, '_rx_tick') else None
from types import MethodType
_real_tick = oc.DesktopPet._rx_tick


def traced_tick(self, now=None):
    before = self.dlg_state
    oc.DesktopPet._rx_tick(self, now)
    after = self.dlg_state
    if before == 'fading' or after == 'fading':
        print(f'      [tick] {before} -> {after}  (rx_active={self._rx_active})')


app._rx_tick = MethodType(traced_tick, app)

for i in range(600):
    app._vt += 0.05
    oc.time.time = lambda: app._vt
    app._rx_tick(now=app._vt)
    app._update_dialogue()
    oc.time.time = real_time
    if app.dlg_state != prev:
        trace.append((round(app._vt - 1000000.0, 2), prev, app.dlg_state,
                      is_visible()))
        prev = app.dlg_state

print('\n  状态变化轨迹 (t, 从, 到, 气泡可见):')
for t, a, b, v in trace:
    print(f'    {t:7.2f}s  {a:8s} -> {b:8s}  可见={v}')
print(f'  最终: dlg_state={app.dlg_state} 气泡可见={is_visible()} '
      f'文字={app.bubble_label.cget("text")[:24]!r}')
print(f'  是否出现过 fading: {any(b == "fading" for _, _, b, _ in trace)}')

check('最终 dlg_state 回到 idle', app.dlg_state == 'idle', f'={app.dlg_state}')
check('最终气泡已消失', not is_visible(), f'可见={is_visible()}')
check('_rx_active 已释放', app._rx_active is False)

print(f'\n  统计: 气泡 place 了 {len(bubble_placed)} 次, '
      f'forget 了 {len(forgot)} 次, hide 调用了 {len(hide_calls)} 次')
print(f'  最后 3 次 hide 调用于 t={[round(t-1000000,2) for t in hide_calls[-3:]]}')
print(f'  bubble.winfo_manager()={app.bubble.winfo_manager()!r}')

app.root.destroy()
print('\n结果:', 'ALL_PASS' if OK else 'SOME_FAILED')
