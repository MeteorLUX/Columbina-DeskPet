# -*- coding: utf-8 -*-
"""
桌面精灵物理（PhysicsMixin）
==========================
拖拽松开后的物理行为：
  - 屏幕中间释放 → 重力下落到底部任务栏上方
  - 左右边缘被遮挡 → 先重力下落（从当前位置），落地后自动
    水平走到屏幕安全位置（再飘一下？不，直接站到地面走回）

接入点：
  - interaction.py on_release → _physics_after_release()
  - app.py _tick → _update_physics(now)

状态机：
  'idle'         无物理效果，正常走动/对话
  'falling'      重力下落中（自驱，不走 _update_walk / _update_sleep_swing）
  'walking_in'   水平走回屏幕内（自驱，帧切换与自主走动完全同步）
"""
import ctypes
import math
import random
import time

from config import *          # noqa: F401,F403


class PhysicsMixin:
    """桌面精灵物理：重力下落 + 边缘走回"""

    def setup_physics(self):
        self._physics_state = 'idle'
        self._physics_v = 0.0         # 下落速度 px/s
        self._physics_t = 0.0         # 上次 tick 时间
        self._physics_ground = 0      # 缓存地面 y（窗口顶坐标），第一次探测后固定
        self._physics_fy = 0.0        # 下落中的浮点 y 位置（避免 int 截断每步丢位移）
        self._phys_pending_walk = None  # 落地后如果非 None → 自动切 walking_in 到此 target_x

    # ---------- 地面探测 ----------
    def _detect_workarea_bottom(self):
        """返回工作区底部 y（含任务栏）——窗口顶坐标。
        Windows 用 SPI_GETWORKAREA 拿到不含任务栏的工作区，
        任务栏高度 = screen_height - workarea_bottom。
        跨平台兜底：screen_height - 40。"""
        try:
            from ctypes import wintypes
            user32 = ctypes.windll.user32
            rect = wintypes.RECT()
            if user32.SystemParametersInfoW(48, 0, ctypes.byref(rect), 0):
                # rect.bottom 是工作区底边（相对屏幕左上角的 y），
                # 窗口底对齐它 → 窗口顶 y = rect.bottom - WIN_H
                ground = rect.bottom - WIN_H
                return ground
        except Exception:
            pass
        # 兜底：假定任务栏 40px 高
        return self.root.winfo_screenheight() - 40 - WIN_H

    def _get_ground_y(self):
        if self._physics_ground <= 0:
            self._physics_ground = self._detect_workarea_bottom()
        return self._physics_ground

    # ---------- 触发入口（拖拽松开时） ----------
    def _physics_after_release(self):
        """拖拽松开后判断：
        - 屏幕中间 + 在空中 → 重力下落
        - 屏幕中间 + 已在地面 → 什么都不做
        - 屏幕外（左/右被遮挡）→ 先重力下落，落地后自动走回屏幕内"""
        sw = self.root.winfo_screenwidth()
        x = self.root.winfo_x()
        y = self.root.winfo_y()

        # 角色被左右边缘遮挡？
        edge_px = WIN_W * PHYS_EDGE_RATIO
        left_blocked  = x < -edge_px
        right_blocked = x + WIN_W > sw + edge_px

        # 硬切对话——物理优先
        self.dlg_state = 'idle'
        self.walk_target_x = None

        # 计算走回目标（如果边缘被遮挡），先存起来等落地后用
        self._phys_pending_walk = None
        if left_blocked or right_blocked:
            if left_blocked:
                target_x = int(sw * PHYS_WALKIN_FACTOR - WIN_W / 2)
            else:
                target_x = int(sw * (1.0 - PHYS_WALKIN_FACTOR) - WIN_W / 2)
            target_x = max(PHYS_SAFE_MARGIN_X, min(sw - WIN_W - PHYS_SAFE_MARGIN_X, target_x))
            self._phys_pending_walk = target_x

        ground = self._get_ground_y()
        if y < ground - 2:
            # 从当前位置下落（不管中间/边缘）
            self._physics_state = 'falling'
            self._physics_v = 0.0
            self._physics_t = time.time()
            self._physics_fy = float(y)
            self._physics_fall_start_y = y
            self._set_pose(PHYS_FALL_POSE)
            return

        # 已在地面但边缘被遮挡 → 直接从地面走回
        if self._phys_pending_walk is not None:
            self._start_walking_in(self._phys_pending_walk)

    def _start_walking_in(self, target_x):
        """开始水平走回屏幕内（落地后自动触发 / 已在地面时触发）"""
        self.walk_target_x = target_x
        self._walk_base_y = self.root.winfo_y()    # 从当前（地面）高度出发
        self._physics_state = 'walking_in'
        self._physics_v = 0.0
        self._walk_next_trigger = float('inf')
        self._phys_walk_last_t = 0.0
        self._phys_walk_cycle = None               # 用于触底换帧（与自主走动同步）
        self._phys_pending_walk = None

    # ---------- 主时钟 ----------
    def _update_physics(self, now):
        st = self._physics_state
        if st == 'idle':
            return

        if st == 'falling':
            dt = now - self._physics_t
            self._physics_t = now
            if dt <= 0:
                return
            # 重力 + 空气阻力
            self._physics_v += PHYS_GRAVITY * dt
            self._physics_v *= pow(PHYS_AIR_DRAG, dt)
            if self._physics_v > PHYS_MAX_FALL_V:
                self._physics_v = PHYS_MAX_FALL_V
            self._physics_fy += self._physics_v * dt
            ground = self._get_ground_y()
            if self._physics_fy >= ground:
                self._physics_fy = ground
                try: self.root.geometry(f"+{self.root.winfo_x()}+{int(round(self._physics_fy))}")
                except Exception: pass
                # 落地后：如果之前边缘被遮挡 → 自动接 walking_in
                if self._phys_pending_walk is not None:
                    self._start_walking_in(self._phys_pending_walk)
                    return
                # 正常落地 → idle
                self._physics_state = 'idle'
                self._physics_v = 0.0
                self.cfg['position'] = [self.root.winfo_x(), int(round(self._physics_fy))]
                self.cfg.save()
                self.root.after(200, self._pose_after_interaction)
                self._walk_next_trigger = now + random.uniform(WALK_MIN_PAUSE, WALK_MAX_PAUSE)
                return
            try:
                self.root.geometry(f"+{self.root.winfo_x()}+{int(round(self._physics_fy))}")
            except Exception:
                self._physics_state = 'idle'
            return

        if st == 'walking_in':
            target = self.walk_target_x
            if target is None:
                # 到达 → idle（从地面直接进入正常待机）
                self._physics_state = 'idle'
                self.cfg['position'] = [self.root.winfo_x(), self.root.winfo_y()]
                self.cfg.save()
                self.root.after(200, self._pose_after_interaction)
                self._walk_next_trigger = now + random.uniform(WALK_MIN_PAUSE, WALK_MAX_PAUSE)
                return

            if now - getattr(self, '_phys_walk_last_t', 0) < WALK_STEP_SEC:
                return
            self._phys_walk_last_t = now

            prev_x = self.root.winfo_x()
            cur_x = prev_x
            bob = int(round(WALK_BOB_PX * math.sin(now * WALK_BOB_SPEED)))
            if abs(cur_x - target) <= WALK_STEP_PX:
                cur_x = target
                self.walk_target_x = None
            else:
                cur_x += WALK_STEP_PX if target > cur_x else -WALK_STEP_PX
            new_y = self._walk_base_y + bob
            try:
                self.root.geometry(f"+{int(cur_x)}+{int(new_y)}")
            except Exception:
                self._physics_state = 'idle'
                return

            # 走路帧切换：与自主走动完全同步——浮动到最低点（sin 最大）时换帧
            cycle = int((now * WALK_BOB_SPEED - math.pi / 2) // (2 * math.pi))
            if cycle != getattr(self, '_phys_walk_cycle', None):
                self._phys_walk_cycle = cycle
                direction = 1 if target > self.root.winfo_x() else -1
                frames = self._walk_r if direction > 0 else self._walk_l
                if frames:
                    self._walk_anim_i = (self._walk_anim_i + 1) % len(frames)
                    self.current_pose = 'walking'
                    self.canvas.delete('all')
                    self.cur_photo = frames[self._walk_anim_i]
                    self.canvas.create_image(WIN_W // 2, IMG_H // 2, image=self.cur_photo)
                    try: self._study_ui_repaint()
                    except Exception: pass
