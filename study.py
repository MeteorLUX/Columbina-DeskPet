# -*- coding: utf-8 -*-
"""
学习计时功能（StudyMixin）
=========================
类似番茄钟的轻量学习计时：
  右键菜单「学习」弹出面板：开始学习 / 暂停学习 / 结束学习 / 查看学习记录
  学习中在角色左侧显示分:秒计时
  随机冒学习相关气泡台词
  45 分钟提醒、暂停超时提醒
  结束自动保存记录（开始/结束/有效时长）
  记录查看：日列表 / 周柱状图 / 月柱状图

数据持久化：study_records.json（与 config.json 同目录）

本模块以 Mixin 形式工作，直接使用 DesktopPet 的属性/方法。
"""

import json
import os
import random
import time
from datetime import datetime, timedelta

import tkinter as tk

from config import *          # noqa: F401,F403


class StudyMixin:
    """学习计时功能"""

    def setup_study(self):
        """学习状态初始化（由 DesktopPet.__init__ 调用）"""
        self._study_state = 'idle'      # idle / studying / paused
        self._study_start_ts = 0.0      # 本次（或本次继续后）学习开始时间戳
        self._study_origin_ts = 0.0     # **最初**开始学习的时间戳（不受暂停/继续影响）
        self._study_pause_ts = 0.0      # 本次暂停开始时间戳
        self._study_elapsed = 0.0       # 已累计的有效学习秒数（暂停时保存）
        self._study_remind45_done = False
        self._study_pause_remind_done = False
        self._study_next_chat = 0.0     # 下次随机学习对话时间
        self._study_last_tick = 0.0     # 上次计时 UI 刷新时间
        self._study_win = None          # 学习面板 Toplevel
        self._study_rec_win = None      # 记录查看窗口
        self._study_records = []        # 学习记录列表（已完成）
        self._study_resumed = False     # 本次是不是"重启后恢复"的
        self._study_last_save = 0.0     # 上次落盘时间（限流用）
        self._load_study_records()
        # 退出兜底：即使不是从菜单退出，也把当前学习状态存下来。
        # 踩过的坑：原来只在菜单「退出」里调 _study_on_quit，
        # 直接关掉程序 -> 本次学习全丢（用户报"重启后记录没了"）。
        try:
            import atexit
            atexit.register(self._study_save_all)
        except Exception:
            pass

        # 计时悬浮 UI：角色左侧的「月夜竖条」（深紫圆角底 + 月牙星星 +
        # 时/分/秒三行竖排）。主窗口是品红透明窗，文字/图形一律 PIL 预渲染
        # 成图片后用 canvas.create_image 显示（与唱歌音符同路径；
        # Label 直接显示文字或贴图都会泛粉边/黑底，见 error.md）。
        self._study_timer_item = None  # canvas item id
        self._study_ui_visible = False
        self._study_img = None         # 当前计时图片（持引用防 GC）
        self._study_last_text = ''     # 最近一次计时文本（测试断言用）

    # ---------- 计时 UI ----------
    def _study_ui_show(self):
        # 走路动画 / 切姿态都会 canvas.delete('all')，计时条可能被连带删掉；
        # 这里探测失效 id 并重建，保证"除了结束学习，计时条一直在"
        ok = False
        if self._study_timer_item is not None:
            try:
                ok = self.canvas.type(self._study_timer_item) is not None
            except Exception:
                ok = False
        if not ok:
            self._study_timer_item = self.canvas.create_image(3, 6, anchor='nw')
            if self._study_img is not None:
                self.canvas.itemconfig(self._study_timer_item,
                                       image=self._study_img)
        self.canvas.tag_raise(self._study_timer_item)
        self._study_ui_visible = True

    def _study_ui_repaint(self):
        """canvas 被 delete('all') 重绘后调用：立刻补画计时竖条（不闪）"""
        if not self._study_ui_visible or self._study_img is None:
            return
        self._study_timer_item = self.canvas.create_image(
            3, 6, anchor='nw', image=self._study_img)

    def _study_ui_hide(self):
        if self._study_timer_item is not None:
            try:
                self.canvas.delete(self._study_timer_item)
            except Exception:
                pass
            self._study_timer_item = None
        self._study_ui_visible = False

    def _study_ui_update(self, text):
        """text: 'HH:MM:SS' 或 '已暂停\\nHH:MM:SS'（暂停时整体转灰）"""
        self._study_last_text = text
        img = self._render_study_timer(text)
        if img is not None:
            self._study_img = img      # 持引用防 GC
            self._study_ui_show()
            self.canvas.itemconfig(self._study_timer_item, image=img)
        # PIL 不可用则静默不显示（透明窗上直接画文字必然泛边，宁缺毋滥）

    def _render_study_timer(self, text):
        """把学习计时渲染成「月夜竖条」图片：
        深紫圆角底 + 顶部小月牙与星星 + 时/分/秒三行竖排（行间小圆点分隔），
        暂停时数字转灰、底部标「暂停」。
        渲染管线与唱歌音符一致：大字形 → LANCZOS 缩小 → alpha 二值化 →
        RGBA 直转 PhotoImage（保留透明通道，不贴品红底，见 error.md）。"""
        if not HAS_PIL:
            return None
        try:
            from PIL import Image, ImageDraw, ImageFont, ImageTk
            paused = text.startswith('已暂停')
            parts = text.split('\n')[-1].split(':')
            if len(parts) == 3:
                hh, mm, ss = parts
            elif len(parts) == 2:
                hh, mm, ss = '00', parts[0], parts[1]
            else:
                return None

            SC = 2.2                     # 渲染放大倍数（缩小后更平滑）
            W, H = 38, 96                # 最终尺寸（竖条）
            w, h = int(W * SC), int(H * SC)
            img = Image.new('RGBA', (w, h), (0, 0, 0, 0))
            d = ImageDraw.Draw(img)

            BG   = (43, 36, 54, 255)     # #2B2436 面板深紫
            EDGE = (106, 75, 168, 255)   # #6A4BA8 描边紫
            FG   = (240, 232, 255, 255)  # #F0E8FF 数字白
            GOLD = (240, 216, 138, 255)  # #F0D88A 月牙金
            STAR = (184, 168, 216, 255)  # #B8A8D8 星星淡紫
            DIM  = (138, 132, 148, 255)  # #8A8494 暂停灰
            num_col  = DIM if paused else FG
            moon_col = DIM if paused else GOLD

            # 圆角背景 + 细描边
            d.rounded_rectangle([1, 1, w - 2, h - 2], radius=int(9 * SC),
                                fill=BG, outline=EDGE, width=max(1, int(SC)))

            # 顶部小月牙：金圆 + BG 色圆偏移遮盖（纯绘制，无豆腐框风险）
            mr = int(4.5 * SC)
            mcx, mcy = w // 2, int(9 * SC)
            d.ellipse([mcx-mr, mcy-mr, mcx+mr, mcy+mr], fill=moon_col)
            ox, oy = int(2.2 * SC), int(-1.2 * SC)
            d.ellipse([mcx-mr+ox, mcy-mr+oy, mcx+mr+ox, mcy+mr+oy], fill=BG)
            # 星星点缀（月亮两侧）
            for sx, sy, sr in ((int(6*SC), int(6*SC), 1.2*SC),
                               (w-int(6*SC), int(9*SC), 1.4*SC),
                               (int(8*SC), int(13*SC), 1.0*SC)):
                sr = int(sr)
                d.ellipse([sx-sr, sy-sr, sx+sr, sy+sr], fill=STAR)

            # 时 / 分 / 秒 三行竖排
            font = None
            for fn in ('msyhbd.ttc', 'arialbd.ttf', 'msyh.ttc'):
                try:
                    font = ImageFont.truetype(fn, int(15 * SC))
                    break
                except Exception:
                    continue
            if font is None:
                return None
            y = 20 * SC
            for i, val in enumerate((hh, mm, ss)):
                d.text((w / 2, y), val, font=font, fill=num_col, anchor='ma')
                y += 19 * SC
                if i < 2:                # 行间分隔小圆点
                    dy = y - 2.8 * SC
                    r = int(0.9 * SC)
                    for ddx in (-2.2 * SC, 2.2 * SC):
                        cx = w / 2 + ddx
                        d.ellipse([cx-r, dy-r, cx+r, dy+r], fill=EDGE)

            # 底部：暂停小字 / 两颗小星星
            if paused:
                small = None
                for fn in ('msyhbd.ttc', 'msyh.ttc'):
                    try:
                        small = ImageFont.truetype(fn, int(7 * SC))
                        break
                    except Exception:
                        continue
                if small is not None:
                    d.text((w / 2, 80 * SC), '暂停', font=small,
                           fill=DIM, anchor='ma')
            else:
                for ddx in (-3 * SC, 3 * SC):
                    cx = w / 2 + ddx
                    r = int(1.1 * SC)
                    cy = 83 * SC
                    d.ellipse([cx-r, cy-r, cx+r, cy+r], fill=STAR)

            img = img.resize((W, H), Image.LANCZOS)
            alpha = img.getchannel('A').point(lambda a: 255 if a >= 110 else 0)
            img.putalpha(alpha)
            return ImageTk.PhotoImage(img)
        except Exception:
            return None

    # ---------- 状态查询 ----------
    def _study_now_elapsed(self, now=None):
        """当前有效学习秒数（不含暂停）"""
        if now is None:
            now = time.time()
        if self._study_state == 'idle':
            return 0.0
        if self._study_state == 'paused':
            return self._study_elapsed
        # studying：已保存的累计 + 本段计时
        return self._study_elapsed + (now - self._study_start_ts)

    def _study_format_time(self, secs):
        m = int(secs) // 60
        s = int(secs) % 60
        return f"{m // 60:02d}:{m % 60:02d}:{s:02d}"

    # ---------- 主时钟接入 ----------
    def _study_pose_forced(self):
        """现在是否该由"学习"强制 work 立绘。

        以下情况要让位给更"当下"的表现，不能被 work 盖掉：
          - 正被鼠标按住拖动      -> 用 touch 图
          - 重力下落中           -> 用 PHYS_FALL_POSE（sleep 图）
          - 边缘走回屏幕中        -> 由走动帧驱动
          - 有走路目标且正在走     -> 同上（**不能只看 _physics_state**：
            物理的 walking_in 到达目标时会先转 idle，而 walk.py 还在
            跑走路帧，这个时间差会让 work 把走路帧盖掉）
        落地 / 松手 / 停下后会自然回到 work。
        """
        if self._study_state != 'studying':
            return False
        if getattr(self, '_dragging', False):
            return False
        if getattr(self, '_physics_state', 'idle') != 'idle':
            return False
        # 正在走动（含物理走回）也不该被 work 盖
        if getattr(self, 'walk_target_x', None) is not None:
            return False
        # 当前就是"走路帧接管"状态，同样让位。
        # 注意 'walking' 是**合成姿态**（不在 images/ 里，由 walk.py:138 /
        # physics.py:196 直接赋值，表示正在跑走路帧动画），必须一并挡掉，
        # 否则 work 会把走路帧盖掉（这个漏判会让相关测试约 50% 概率失败）。
        if self.current_pose in ('walking', 'walk1', 'walk7', 'walk8'):
            return False
        return True

    def _study_tick(self, now):
        """_tick 里调用：更新计时 UI、随机对话、45 分钟提醒、暂停超时提醒"""
        if self._study_state == 'idle':
            return

        # 学习计时中：持续强制 work 姿态（防止被拖动松手、对话结束等切回 smile）
        # 但拖动中 / 重力下落中 / 边缘走回中要让位，见 _study_pose_forced()
        if self._study_pose_forced() and self.current_pose != 'work':
            self._set_pose('work')
            # 走路守卫已加：studying 时不走

        # 计时 UI 刷新（每秒一次）
        if now - self._study_last_tick >= STUDY_TICK_SEC:
            self._study_last_tick = now
            elapsed = self._study_now_elapsed(now)
            if self._study_state == 'paused':
                self._study_ui_update(f"已暂停\n{self._study_format_time(elapsed)}")
            else:
                self._study_ui_update(self._study_format_time(elapsed))
            # ---- 周期性落盘 ----
            # 每次 tick（1 秒）都写盘太费；但只在退出时写又会丢数据
            # （用户报"重启后学习记录没了"）。所以按固定间隔写一次，
            # 崩溃最多丢 STUDY_AUTOSAVE_SEC 秒。
            if now - self._study_last_save >= STUDY_AUTOSAVE_SEC:
                self._save_study_records()

        if self._study_state == 'studying':
            # 45 分钟提醒
            if not self._study_remind45_done:
                elapsed = self._study_now_elapsed(now)
                if elapsed >= STUDY_REMIND_MIN * 60:
                    self._study_remind45_done = True
                    self._speak(random.choice(STUDY_REMIND_45_LINES), interrupt=True)

            # 随机学习对话
            if now >= self._study_next_chat:
                self._study_next_chat = now + random.uniform(STUDY_CHAT_MIN, STUDY_CHAT_MAX)
                # 只在空闲时冒泡，不打断正在进行的对话
                if (self.dlg_state == 'idle' and not self.is_generating
                        and self._sing_state == 'off'
                        and not self._voice_busy() and not self._rx_active):
                    self._speak(random.choice(STUDY_CHAT_LINES))

        elif self._study_state == 'paused':
            # 暂停超时提醒
            if not self._study_pause_remind_done:
                if now - self._study_pause_ts >= STUDY_PAUSE_REMIND_SEC:
                    self._study_pause_remind_done = True
                    self._speak(random.choice(STUDY_PAUSE_REMIND_LINES), interrupt=True)

    # ---------- 指令匹配 ----------
    def _study_cmd_match(self, text):
        """把一句话解析成学习指令：start/pause/end/record 或 None"""
        t = text or ''
        if STUDY_START_PAT.search(t):  return 'start'
        if STUDY_PAUSE_PAT.search(t):  return 'pause'
        if STUDY_END_PAT.search(t):    return 'end'
        if STUDY_RECORD_PAT.search(t): return 'record'
        return None

    def _study_cmd_apply(self, cmd):
        """执行学习指令（主线程）"""
        self._reset_idle_timer()
        if cmd == 'start':
            self.study_start()
        elif cmd == 'pause':
            self.study_pause()
        elif cmd == 'end':
            self.study_stop()
        elif cmd == 'record':
            self.study_show_records()

    # ---------- 核心操作 ----------
    def study_start(self):
        """开始学习 / 继续学习"""
        if self._study_state == 'studying':
            self._speak(random.choice(STUDY_ALREADY_LINES))
            return
        if self._study_state == 'paused':
            # 继续学习：重置**本段**计时的起点。
            # 注意 _study_origin_ts（最初开始时刻）**不动** —— 它是拿来
            # 写记录的，一旦被重置，记录的开始时间就会错（原 bug）。
            now = time.time()
            self._study_start_ts = now
            self._study_state = 'studying'
            self._study_pause_remind_done = False
            self._study_next_chat = now + random.uniform(STUDY_CHAT_MIN, STUDY_CHAT_MAX)
            self._set_pose('work')     # 学习期间保持 work 立绘
            self._speak(random.choice(STUDY_RESUME_LINES))
            self._save_study_records()          # 状态变了立刻落盘
            return
        # 全新开始
        now = time.time()
        self._study_state = 'studying'
        self._study_start_ts = now
        self._study_origin_ts = now        # 记住最初开始时刻（写记录用）
        self._study_elapsed = 0.0
        self._study_remind45_done = False
        self._study_pause_remind_done = False
        self._study_next_chat = now + random.uniform(STUDY_CHAT_MIN, STUDY_CHAT_MAX)
        self._study_last_tick = 0.0   # 立刻刷新 UI
        self._set_pose('work')         # 学习期间保持 work 立绘
        # 如果本次是"上次中断后恢复"的，告诉她一声（否则用户不知道计时接上了）
        if not self._study_note_resume_on_start():
            self._speak(random.choice(STUDY_START_LINES))
        self._save_study_records()              # 状态变了立刻落盘

    def study_pause(self):
        """暂停学习"""
        if self._study_state == 'idle':
            self._speak(random.choice(STUDY_NOT_STUDYING_LINES))
            return
        if self._study_state == 'paused':
            # 已暂停，提示但不重复操作
            self._speak(random.choice(STUDY_PAUSE_LINES))
            return
        # studying -> paused：把本段计时存入累计
        now = time.time()
        self._study_elapsed += now - self._study_start_ts
        self._study_state = 'paused'
        self._study_pause_ts = now
        self._study_pause_remind_done = False
        self._set_pose('smile')        # 暂停时恢复常态立绘（只有计时中才是 work）
        self._speak(random.choice(STUDY_PAUSE_LINES))
        self._save_study_records()     # 状态变了立刻落盘

    def study_stop(self):
        """结束学习"""
        if self._study_state == 'idle':
            self._speak(random.choice(STUDY_NOT_STUDYING_LINES))
            return
        now = time.time()
        if self._study_state == 'studying':
            self._study_elapsed += now - self._study_start_ts
        # paused 时 _study_elapsed 已经算好了

        duration = self._study_elapsed
        minutes = int(duration) // 60

        # 过短不记录
        if duration < STUDY_MIN_SAVE_SEC:
            self._speak(STUDY_TOO_SHORT_LINE)
        else:
            # 保存记录
            # 【修过的 bug】原来用 `self._study_start_ts - self._study_elapsed`
            # **反推**最初开始时间。但 start_ts 在"继续学习"时会被重置成
            # 继续那一刻，而 elapsed 是累计的真实学习秒数（不含暂停）——
            # 两者相减得到的根本不是真实开始时间，暂停过就会错位，
            # 严重时 start 落到前一天 -> 面板按当天过滤就"看不见今日记录"。
            # 现在直接用一个专门记住"最初开始时刻"的 _study_origin_ts。
            first_start_ts = self._study_origin_ts or self._study_start_ts
            start_dt = datetime.fromtimestamp(first_start_ts)
            end_dt = datetime.fromtimestamp(now)
            record = {
                'start': start_dt.strftime('%Y-%m-%dT%H:%M:%S'),
                'end': end_dt.strftime('%Y-%m-%dT%H:%M:%S'),
                'duration': int(duration),
            }
            self._study_records.append(record)
            self._save_study_records()
            line = random.choice(STUDY_STOP_LINES).format(minutes=minutes)
            self._speak(line)

        # 重置状态
        self._study_state = 'idle'
        self._study_ui_hide()
        self._study_elapsed = 0.0
        self._study_origin_ts = 0.0
        self._set_pose('smile')        # 结束学习，恢复常态立绘
        # 状态变了就落盘（清掉 live，避免下次启动误以为还在学习）
        self._save_study_records()

    def _study_on_quit(self):
        """程序退出时：把当前学习**结算成记录**并保存。

        【设计变更】这里以前是唯一的保存点，导致直接关程序就丢数据。
        现在每次状态变化都会落盘（见 `_study_save_all`），这里只做
        "把进行中的学习收尾成一条正式记录"，并且**只做一次**。
        """
        if self._study_state not in ('studying', 'paused'):
            self._save_study_records()      # 兜底：至少把已有记录写稳
            return
        now = time.time()
        if self._study_state == 'studying':
            self._study_elapsed += now - self._study_start_ts
            self._study_start_ts = now     # 防止重复累加
        duration = self._study_elapsed
        if duration >= STUDY_MIN_SAVE_SEC:
            first_start_ts = self._study_origin_ts or self._study_start_ts
            record = {
                'start': datetime.fromtimestamp(first_start_ts)
                                 .strftime('%Y-%m-%dT%H:%M:%S'),
                'end': datetime.fromtimestamp(now)
                               .strftime('%Y-%m-%dT%H:%M:%S'),
                'duration': int(duration),
                'auto': True,              # 标记：退出时自动收尾的
            }
            self._study_records.append(record)
        self._study_state = 'idle'
        self._study_elapsed = 0.0
        self._study_origin_ts = 0.0
        self._save_study_records()

    # ---------- 记录存储 ----------
    #
    # 【为什么这么设计】
    # 旧版只把"已完成的学习"存文件，而且**只在退出时**写一次。
    # 结果：直接关程序 / 崩溃 -> 整段学习凭空消失（用户真的遇到了）。
    #
    # 现在文件里有两块：
    #   sessions  已完成的学习记录（面板读它）
    #   live      当前**正在进行**的学习状态（每次变化就写）
    # 启动时若发现 live 还在，就把它恢复成"正在学习"，并接上累计时长。
    def _study_file_payload(self):
        live = None
        if self._study_state in ('studying', 'paused'):
            # elapsed 要存**含当前进行中那一段**的总时长。
            # 只存 self._study_elapsed 的话，正在学习时会少算"从上次
            # 暂停/开始到现在"这一截（踩过：live 里 elapsed=0 看着像没学）。
            elapsed = self._study_elapsed
            if self._study_state == 'studying':
                elapsed += max(0.0, time.time() - self._study_start_ts)
            live = {
                'state': self._study_state,
                'origin_ts': self._study_origin_ts,
                'start_ts': self._study_start_ts,
                'elapsed': round(elapsed, 1),
                'saved_at': time.time(),
            }
        return {'version': 2, 'sessions': self._study_records, 'live': live}

    def _study_save_all(self):
        """把「已完成记录 + 当前进行中的状态」一起落盘。

        这是**唯一的**写盘入口，任何状态变化（开始/暂停/继续/结束）和
        退出（含 atexit 兜底）都走它。
        """
        try:
            data = self._study_file_payload()
            tmp = STUDY_PATH + '.tmp'
            with open(tmp, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            os.replace(tmp, STUDY_PATH)      # 原子替换，避免写一半坏掉
            self._study_last_save = time.time()
        except Exception:
            pass

    def _save_study_records(self):
        """保留旧名字（别处可能还在用），内部走统一入口"""
        self._study_save_all()

    def _load_study_records(self):
        """从 study_records.json 加载记录，并恢复未完成的学习"""
        self._study_records = []
        if not os.path.exists(STUDY_PATH):
            return
        try:
            with open(STUDY_PATH, 'r', encoding='utf-8') as f:
                data = json.load(f)
        except Exception:
            return

        if isinstance(data, list):           # 很老的格式：直接是数组
            self._study_records = data
            return
        if not isinstance(data, dict):
            return
        self._study_records = data.get('sessions') or []

        # ---- 恢复未完成的学习 ----
        live = data.get('live')
        if not isinstance(live, dict):
            return
        try:
            state = live.get('state')
            if state not in ('studying', 'paused'):
                return
            origin = float(live.get('origin_ts') or 0)
            elapsed = float(live.get('elapsed') or 0)
            saved_at = float(live.get('saved_at') or 0)
            if origin <= 0:
                return

            if state == 'studying':
                # 程序关掉的这段时间**算不算学习**？不算。
                # 只在"上次落盘之后到关闭之前"这一段是真实学过的，
                # 但那段已经包含在 elapsed 里了（tick 会更新），
                # 所以恢复时把 start_ts 设为现在，时长接上 elapsed。
                now = time.time()
                self._study_state = 'studying'
                self._study_origin_ts = origin
                self._study_start_ts = now
                self._study_elapsed = elapsed
                self._study_next_chat = now + random.uniform(
                    STUDY_CHAT_MIN, STUDY_CHAT_MAX)
                self._study_resumed = True
            else:                            # paused
                self._study_state = 'paused'
                self._study_origin_ts = origin
                self._study_start_ts = saved_at or time.time()
                self._study_elapsed = elapsed
                self._study_pause_ts = saved_at or time.time()
                self._study_resumed = True
        except Exception:
            pass

    def _study_note_resume_on_start(self):
        """如果本次是"上次中断后恢复"的，开口告诉她一声"""
        if not self._study_resumed:
            return False
        mins = int(self._study_elapsed) // 60
        try:
            if self._study_state == 'studying':
                self._speak(f"……上次学到 {mins} 分钟。我接着往下计时了。")
            else:
                self._speak(f"……上次学到 {mins} 分钟，是暂停着的。"
                            "要继续就说「继续学习」。")
        except Exception:
            pass
        self._study_resumed = False
        return True

    # ---------- 学习面板 ----------
    def study_show_panel(self):
        """右键菜单「学习」：弹出月夜风格学习面板"""
        if self._study_win is not None:
            try:
                self._study_win.lift()
                self._study_win.focus_force()
            except Exception:
                self._study_win = None
            else:
                return

        def _closed():
            self._study_win = None
        self._study_win = self._moon_panel('学习计时', [
            ('开始学习', self.study_start),
            ('暂停学习', self.study_pause),
            ('结束学习', self.study_stop),
            ('查看学习记录', self.study_show_records),
        ], on_close=_closed)

    # ---------- 记录查看 ----------
    def study_show_records(self):
        """查看学习记录：日 / 周 / 月"""
        if self._study_rec_win is not None:
            try:
                self._study_rec_win.lift()
                self._study_rec_win.focus_force()
            except Exception:
                self._study_rec_win = None
            else:
                return

        win = tk.Toplevel(self.root)
        win.title("学习记录")
        win.attributes('-topmost', True)
        win.resizable(False, False)
        x = self.root.winfo_x() + WIN_W + 10
        y = self.root.winfo_y()
        sw = self.root.winfo_screenwidth()
        if x + 420 > sw:
            x = self.root.winfo_x() - 430
        win.geometry(f"420x360+{x}+{y}")

        frame = tk.Frame(win, bg=BUBBLE_BG)
        frame.pack(fill='both', expand=True, padx=8, pady=8)

        # 顶部切换按钮
        tab_frame = tk.Frame(frame, bg=BUBBLE_BG)
        tab_frame.pack(fill='x', pady=(0, 6))

        content = tk.Frame(frame, bg=BUBBLE_BG)
        content.pack(fill='both', expand=True)

        tab_buttons = {}

        def switch(tab):
            for t, b in tab_buttons.items():
                if t == tab:
                    b.config(bg='#6A4BA8', fg='white')
                else:
                    b.config(bg='#E8E0F0', fg='#6A4BA8')
            for w in content.winfo_children():
                w.destroy()
            if tab == 'day':
                self._rec_show_day(content)
            elif tab == 'week':
                self._rec_show_week(content)
            else:
                self._rec_show_month(content)

        for text, tab in (("日记录", 'day'), ("周记录", 'week'), ("月记录", 'month')):
            b = tk.Button(tab_frame, text=text, font=('Microsoft YaHei', 9),
                          bg='#6A4BA8' if tab == 'day' else '#E8E0F0',
                          fg='white' if tab == 'day' else '#6A4BA8',
                          relief='flat', cursor='hand2', padx=10, pady=2,
                          command=lambda t=tab: switch(t))
            b.pack(side='left', padx=4)
            tab_buttons[tab] = b

        def on_close():
            self._study_rec_win = None
            win.destroy()
        win.protocol("WM_DELETE_WINDOW", on_close)
        self._study_rec_win = win

        switch('day')

    def _rec_show_day(self, parent):
        """日记录：当天每次学习的详细列表"""
        today = datetime.now().strftime('%Y-%m-%d')
        sessions = [r for r in self._study_records
                    if r.get('start', '').startswith(today)]

        if not sessions:
            tk.Label(parent, text="今天还没有学习记录。",
                     bg=BUBBLE_BG, fg=BUBBLE_FG,
                     font=('Microsoft YaHei', 10)).pack(expand=True)
            return

        # 列表头
        header = tk.Frame(parent, bg=BUBBLE_BG)
        header.pack(fill='x', pady=(0, 4))
        for text, w in (("开始", 10), ("结束", 10), ("时长", 12)):
            tk.Label(header, text=text, bg=BUBBLE_BG, fg='#8A8494',
                     font=('Microsoft YaHei', 8, 'bold'), width=w, anchor='w').pack(side='left')

        # 列表体（带滚动条）
        canvas = tk.Canvas(parent, bg=BUBBLE_BG, highlightthickness=0)
        sb = tk.Scrollbar(parent, orient='vertical', command=canvas.yview)
        inner = tk.Frame(canvas, bg=BUBBLE_BG)
        inner.bind('<Configure>', lambda e: canvas.configure(scrollregion=canvas.bbox('all')))
        canvas.create_window((0, 0), window=inner, anchor='nw')
        canvas.configure(yscrollcommand=sb.set)
        canvas.pack(side='left', fill='both', expand=True)
        sb.pack(side='right', fill='y')

        for r in reversed(sessions):   # 最新的在上面
            row = tk.Frame(inner, bg=BUBBLE_BG)
            row.pack(fill='x', pady=1)
            start = r.get('start', '')[11:16]   # HH:MM
            end = r.get('end', '')[11:16]
            dur = r.get('duration', 0)
            if dur < 3600:
                dur_str = f"{dur // 60}分{dur % 60}秒"
            else:
                dur_str = f"{dur // 3600}时{(dur % 3600) // 60}分"
            for text, w in ((start, 10), (end, 10), (dur_str, 12)):
                tk.Label(row, text=text, bg=BUBBLE_BG, fg=BUBBLE_FG,
                         font=('Microsoft YaHei', 8), width=w, anchor='w').pack(side='left')

        # 底部总计
        total = sum(r.get('duration', 0) for r in sessions)
        tk.Label(parent, text=f"今日总计：{total // 60} 分钟",
                 bg=BUBBLE_BG, fg='#6A4BA8',
                 font=('Microsoft YaHei', 9, 'bold')).pack(pady=(6, 0))

    def _rec_show_week(self, parent):
        """周记录：本周每天总时长柱状图"""
        now = datetime.now()
        monday = now - timedelta(days=now.weekday())
        week_start = monday.strftime('%Y-%m-%d')
        week_end = (monday + timedelta(days=6)).strftime('%Y-%m-%d')

        # 按天聚合
        daily = {}
        for r in self._study_records:
            d = r.get('start', '')[:10]
            if week_start <= d <= week_end:
                daily[d] = daily.get(d, 0) + r.get('duration', 0)

        days = []
        for i in range(7):
            d = (monday + timedelta(days=i)).strftime('%Y-%m-%d')
            days.append((d, daily.get(d, 0)))

        if not any(v > 0 for _, v in days):
            tk.Label(parent, text="本周还没有学习记录。",
                     bg=BUBBLE_BG, fg=BUBBLE_FG,
                     font=('Microsoft YaHei', 10)).pack(expand=True)
            return

        self._draw_bar_chart(parent, days, unit='分钟', divisor=60)

    def _rec_show_month(self, parent):
        """月记录：每月总时长柱状图（最近 6 个月）"""
        monthly = {}
        for r in self._study_records:
            m = r.get('start', '')[:7]   # YYYY-MM
            if m:
                monthly[m] = monthly.get(m, 0) + r.get('duration', 0)

        if not monthly:
            tk.Label(parent, text="还没有学习记录。",
                     bg=BUBBLE_BG, fg=BUBBLE_FG,
                     font=('Microsoft YaHei', 10)).pack(expand=True)
            return

        # 取最近 6 个月
        months = sorted(monthly.keys())[-6:]
        data = [(m, monthly[m]) for m in months]

        self._draw_bar_chart(parent, data, unit='小时', divisor=3600)

    def _draw_bar_chart(self, parent, data, unit, divisor):
        """在 parent 上画柱状图。data: [(label, seconds), ...]"""
        tk.Label(parent, text=f"总时长（{unit}）", bg=BUBBLE_BG, fg=BUBBLE_FG,
                 font=('Microsoft YaHei', 9, 'bold')).pack(pady=(0, 4))

        canvas = tk.Canvas(parent, bg=BUBBLE_BG, highlightthickness=0,
                           width=380, height=240)
        canvas.pack(fill='both', expand=True)

        w, h = 380, 240
        margin_l, margin_r, margin_t, margin_b = 40, 10, 10, 30
        chart_w = w - margin_l - margin_r
        chart_h = h - margin_t - margin_b

        max_val = max((v for _, v in data), default=0)
        if max_val <= 0:
            max_val = 1

        n = len(data)
        bar_w = min(30, chart_w // (n * 2))
        gap = (chart_w - n * bar_w) // (n + 1)

        # Y 轴刻度（3 档）
        for i in range(4):
            val = max_val * i / 3 / divisor
            y = margin_t + chart_h - (chart_h * i / 3)
            canvas.create_line(margin_l, y, w - margin_r, y, fill='#E0D8CC', width=1)
            canvas.create_text(margin_l - 4, y, text=f"{val:.0f}",
                               font=('Microsoft YaHei', 7), fill='#8A8494', anchor='e')

        # 柱子
        for i, (label, secs) in enumerate(data):
            x0 = margin_l + gap * (i + 1) + bar_w * i
            val = secs / divisor
            bar_h = chart_h * secs / max_val if max_val else 0
            y0 = margin_t + chart_h - bar_h
            x1 = x0 + bar_w
            y1 = margin_t + chart_h

            color = '#6A4BA8' if secs > 0 else '#D9D0E0'
            canvas.create_rectangle(x0, y0, x1, y1, fill=color, outline='')

            # 数值标签
            if secs > 0:
                canvas.create_text((x0 + x1) / 2, y0 - 6, text=f"{val:.1f}",
                                   font=('Microsoft YaHei', 7), fill='#6A4BA8')

            # X 轴标签
            short = label[5:] if len(label) > 5 else label   # MM-DD 或 YYYY-MM
            canvas.create_text((x0 + x1) / 2, y1 + 10, text=short,
                               font=('Microsoft YaHei', 7), fill='#8A8494')
