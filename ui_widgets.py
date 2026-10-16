# -*- coding: utf-8 -*-
"""
窗口与控件搭建（UIWidgetsMixin）
=========================
创建主窗口、角色画布、好感度、语音识别显示区、
对话气泡、输入框等所有界面控件。

以 Mixin 形式工作，直接使用 DesktopPet 的属性与方法
（self.root / self.canvas / self.cfg / self._set_pose 等）。
"""
import os

import tkinter as tk

# config 提供常量、文案、Config 与 HAS_* 能力标志
from config import *          # noqa: F401,F403

try:
    from PIL import Image, ImageTk, ImageOps
except ImportError:
    pass


class FrostBubbleLabel(tk.Label):
    """毛玻璃气泡的文字标签。
    拦截 config(text=..., fg=...)，把文字用 PIL 画到预渲染的
    圆角渐变星星背景上，整图转 PhotoImage 显示——
    透明窗上直接画文字会泛粉边/黑遮挡（见 error.md），必须走图片管线。
    cget('text'/'fg') 仍返回逻辑值，现有调用点（chat/app/voice）零改动。"""

    def __init__(self, master, pet, **kw):
        self._pet = pet
        self._txt = ''
        self._fg = BUBBLE_FG
        self._img = None
        kw.setdefault('bg', TRANSPARENT)
        kw.setdefault('bd', 0)
        kw.setdefault('highlightthickness', 0)
        super().__init__(master, **kw)

    def config(self, cnf=None, **kw):
        if cnf:
            kw.update(cnf)
        if 'text' in kw:
            self._txt = kw.pop('text') or ''
        if 'fg' in kw:
            self._fg = kw.pop('fg') or self._fg
        if kw:
            tk.Label.configure(self, **kw)
        self._repaint()

    configure = config

    def _repaint(self):
        try:
            ph = self._pet._bubble_photo(self._txt, self._fg)
        except Exception:
            ph = None
        if ph is None:
            # 无 PIL / 渲染失败：退回普通文字 Label（方角，但功能不坏）
            tk.Label.configure(self, text=self._txt, fg=self._fg,
                               bg=BUBBLE_BG, image='')
            return
        tk.Label.configure(self, text='', image=ph, bg=TRANSPARENT)
        self._img = ph                     # 持引用防 GC

    def cget(self, key):
        if key == 'text':
            return self._txt
        if key == 'fg':
            return self._fg
        return tk.Label.cget(self, key)

    __getitem__ = lambda self, key: self.cget(key)


class UIWidgetsMixin:
    """窗口与控件搭建"""

    def _build_ui(self):
        # 角色画布夹在气泡和输入条之间，互不重叠
        self.canvas = tk.Canvas(self.root, width=WIN_W, height=IMG_H,
                                bg=TRANSPARENT, highlightthickness=0, bd=0)
        self.canvas.place(x=0, y=INPUT_Y - IMG_H - 4)

        # 好感度显示：默认隐藏，鼠标悬浮在角色上时才浮现
        self.affinity_label = tk.Label(
            self.root, text="", bg=TRANSPARENT, fg='#E8607A',
            font=('Microsoft YaHei', 7, 'bold')
        )
        self._update_affinity_display()
        self.canvas.bind('<Enter>', lambda e: self.affinity_label.place(x=4, y=1))
        self.canvas.bind('<Leave>', lambda e: self.affinity_label.place_forget())
        # 鼠标从角色移到心心上时不闪烁
        self.affinity_label.bind('<Enter>', lambda e: self.affinity_label.place(x=4, y=1))
        self.affinity_label.bind('<Leave>', lambda e: self.affinity_label.place_forget())

        # 🌊 语音识别显示区（角色下方）：只显示「我听到你说的话」
        # 与她自己的气泡完全分开，避免两条信息抢同一个气泡。
        self.voice_holder = tk.Frame(self.root, bg='#EFF3EC',
                                     highlightthickness=1, highlightbackground='#B9CDB0')
        self.voice_label = tk.Label(
            self.voice_holder, text="", bg='#EFF3EC', fg='#4A6B3E',
            font=('Microsoft YaHei', 8), wraplength=INPUT_W - 12,
            justify='left', anchor='nw', padx=5, pady=2)
        self.voice_label.pack(fill='both', expand=True)
        self._voice_shown = False

        # 对话框（头顶气泡，默认隐藏，说话时浮现）
        # 毛玻璃样式：整张气泡（圆角+渐变+星星）由 PIL 预渲染，
        # FrostBubbleLabel 拦截 config(text/fg) 自动重绘，
        # 现有 chat/app/voice 的调用点全部不用改。
        self.bubble = tk.Frame(self.root, bg=TRANSPARENT,
                               highlightthickness=0, bd=0)
        self._bubble_assets()
        self.bubble_label = FrostBubbleLabel(self.bubble, self, anchor='nw')
        self.bubble_label.pack(fill='both', expand=True)

        # 唱歌询问的选项「想 / 不想」：跟在气泡下方，默认隐藏
        self.sing_choice_holder = tk.Frame(
            self.root, bg=BUBBLE_BG,
            highlightthickness=1, highlightbackground=BUBBLE_BORDER)
        self.sing_choice_yes = tk.Label(
            self.sing_choice_holder, text="想", bg=BUBBLE_BG, fg='#6A4BA8',
            font=('Microsoft YaHei', 8, 'bold'), padx=10, pady=1, cursor='hand2')
        self.sing_choice_no = tk.Label(
            self.sing_choice_holder, text="不想", bg=BUBBLE_BG, fg='#8A8494',
            font=('Microsoft YaHei', 8), padx=10, pady=1, cursor='hand2')
        self.sing_choice_yes.pack(side='left', padx=(8, 2), pady=2)
        self.sing_choice_no.pack(side='left', padx=(2, 8), pady=2)
        self.sing_choice_yes.bind('<Button-1>', lambda e: self._sing_choice(True))
        self.sing_choice_no.bind('<Button-1>', lambda e: self._sing_choice(False))
        self._sing_opts_shown = False

        # 输入框（默认隐藏，点击角色呼出，位于角色下方独立区域）
        self.input_holder = tk.Frame(self.root, bg='#F3ECFA',
                                     highlightthickness=1, highlightbackground='#C9B8DC')

        self.entry = tk.Entry(self.input_holder, font=('Microsoft YaHei', 8),
                              bg='#F3ECFA', fg='#2B2436', insertbackground='#6A4BA8',
                              relief='flat', bd=0)
        self.entry.pack(side='left', fill='both', expand=True, padx=(6, 2), pady=2)
        self.entry.bind('<Return>', self.on_send)
        self.entry.bind('<Button-1>', lambda e: self._reset_idle_timer())

        self.send_btn = tk.Label(self.input_holder, text="发送", bg='#6A4BA8', fg='white',
                                 font=('Microsoft YaHei', 7, 'bold'), padx=4, pady=1, cursor='hand2')
        self.send_btn.pack(side='right', padx=(0, 3), pady=2)
        self.send_btn.bind('<Button-1>', self.on_send)


# ==================== 毛玻璃气泡渲染 ====================
    def _bubble_assets(self):
        """气泡字体与背景模板（一次性初始化，由 _build_ui 调用）"""
        self._bubble_font = None
        self._bubble_bg = None          # RGBA 背景模板（圆角+渐变+星星）
        self._bubble_cache = {}         # (text, fg) -> PhotoImage
        if not HAS_PIL:
            return
        from PIL import ImageFont
        for fn in ('msyh.ttc', 'msyh.ttf', 'simhei.ttf'):
            try:
                self._bubble_font = ImageFont.truetype(fn, 11)
                break
            except Exception:
                continue

    def _build_bubble_bg(self, w, h):
        """毛玻璃背景模板：圆角矩形 + 纵向柔光渐变 + 星星点缀"""
        from PIL import Image, ImageDraw
        img = Image.new('RGBA', (w, h), (0, 0, 0, 0))

        def hexrgb(s):
            return tuple(int(s[i:i + 2], 16) for i in (1, 3, 5))

        top, bot = hexrgb(BUBBLE_FROST_TOP), hexrgb(BUBBLE_FROST_BOTTOM)
        # 纵向渐变（毛玻璃雾面感）
        grad = Image.new('RGBA', (w, h), (0, 0, 0, 0))
        gd = ImageDraw.Draw(grad)
        for yy in range(2, h - 2):
            t = (yy - 2) / max(1, h - 5)
            c = tuple(int(top[i] + (bot[i] - top[i]) * t) for i in range(3)) + (255,)
            gd.line([(2, yy), (w - 3, yy)], fill=c)
        # 圆角遮罩：只保留圆角矩形内部的渐变
        mask = Image.new('L', (w, h), 0)
        md = ImageDraw.Draw(mask)
        try:
            md.rounded_rectangle((1, 1, w - 2, h - 2), radius=13, fill=255)
        except Exception:
            md.rectangle((1, 1, w - 2, h - 2), fill=255)
        img.paste(grad, (0, 0), mask)
        # 圆角描边
        d = ImageDraw.Draw(img)
        try:
            d.rounded_rectangle((1, 1, w - 2, h - 2), radius=13,
                                outline=BUBBLE_BORDER, width=1)
        except Exception:
            d.rectangle((1, 1, w - 2, h - 2), outline=BUBBLE_BORDER, width=1)
        # 星星装饰：四角闪（十字星光）+ 小圆点，只点边角空白，不压正文
        def sparkle(cx, cy, r, col):
            d.line([(cx - r, cy), (cx + r, cy)], fill=col, width=1)
            d.line([(cx, cy - r), (cx, cy + r)], fill=col, width=1)
            d.ellipse((cx - 1, cy - 1, cx + 1, cy + 1), fill=col)
        sparkle(w - 16, 10, 3, BUBBLE_STAR)
        sparkle(w - 27, 15, 2, BUBBLE_STAR)
        sparkle(13, h - 10, 2, BUBBLE_STAR)
        d.ellipse((w - 37, 11, w - 35, 13), fill=BUBBLE_STAR)
        d.ellipse((22, h - 12, 24, h - 10), fill=BUBBLE_STAR)
        return img

    @staticmethod
    def _wrap_bubble_text(draw, text, font, max_w):
        """按像素宽度手动换行（中文可任意断行）；行首不让标点孤立，
        标点跟随上一行末字下移。"""
        res = []
        for seg in (text or '').split('\n'):
            cur = ''
            for ch in seg:
                if draw.textlength(cur + ch, font=font) > max_w and cur:
                    if ch in '，。！？；：、）」』!?,.:;)]}' and len(cur) > 1:
                        last = cur[-1]
                        res.append(cur[:-1])
                        cur = last + ch
                    else:
                        res.append(cur)
                        cur = ch
                else:
                    cur += ch
            res.append(cur)
        return res

    def _bubble_render_pil(self, text, fg_hex):
        """渲染整张气泡（含背景与文字）→ 品红底 RGB 图。
        无 PIL / 无字体时返回 None（调用方退回普通文字显示）。"""
        if not HAS_PIL or self._bubble_font is None:
            return None
        from PIL import Image, ImageDraw
        w, h = BUBBLE_W, BUBBLE_H
        if self._bubble_bg is None:
            self._bubble_bg = self._build_bubble_bg(w, h)
        img = self._bubble_bg.copy()
        d = ImageDraw.Draw(img)
        lines = self._wrap_bubble_text(d, text, self._bubble_font, w - 14)[:3]
        y = 6
        for ln in lines:
            if ln:
                d.text((8, y), ln, font=self._bubble_font, fill=fg_hex)
            y += 14
        # 透明窗合成：alpha 二值化 + 透明区填纯品红（同立绘管线，防粉边）
        alpha = img.getchannel('A').point(lambda a: 255 if a >= 128 else 0)
        rgb = img.convert('RGB')
        out = Image.new('RGB', img.size, (255, 0, 255))
        out.paste(rgb, (0, 0), alpha)
        return out

    def _bubble_photo(self, text, fg_hex):
        """带缓存的 PhotoImage 版本（FrostBubbleLabel 每次打字都会调）"""
        if not HAS_PIL or self._bubble_font is None:
            return None
        key = (text, fg_hex)
        ph = self._bubble_cache.get(key)
        if ph is None:
            img = self._bubble_render_pil(text, fg_hex)
            if img is None:
                return None
            ph = ImageTk.PhotoImage(img)
            if len(self._bubble_cache) > 120:
                self._bubble_cache.clear()
            self._bubble_cache[key] = ph
        return ph

# ==================== 月夜风格面板 ====================
    def _moon_panel(self, title, buttons, on_close=None):
        """深色月夜风格弹窗（与右键菜单同款配色）：圆角背景、月亮星星装饰、
        圆角按钮。buttons: [(文字, 回调), ...]。返回 Toplevel。
        无边框自绘：四角露出窗口底色 #1A1626（与面板色相近，视觉即圆角）。"""
        W = 200
        BTN_H, BTN_GAP, BTN_MX = 34, 12, 24
        TOP_H, BOT_H = 58, 14
        H = TOP_H + len(buttons) * (BTN_H + BTN_GAP) - BTN_GAP + BOT_H

        win = tk.Toplevel(self.root)
        win.overrideredirect(True)
        win.attributes('-topmost', True)
        win.configure(bg='#1A1626')
        cv = tk.Canvas(win, width=W, height=H, bg='#1A1626',
                       highlightthickness=0, bd=0)
        cv.pack()

        BG, FG = '#2B2436', '#F0E8FF'
        ACC, ACC_HV = '#6A4BA8', '#8A6BC8'

        def rrect(x0, y0, x1, y1, r, **kw):
            pts = [x0+r, y0, x1-r, y0, x1, y0, x1, y0+r, x1, y1-r, x1, y1,
                   x1-r, y1, x0+r, y1, x0, y1, x0, y1-r, x0, y0+r, x0, y0]
            return cv.create_polygon(pts, smooth=True, **kw)

        # 圆角主背景
        rrect(2, 2, W-2, H-2, 16, fill=BG, outline='#3D3350', width=1)

        # 月亮：金色圆 + 背景色圆偏移遮盖出月牙（纯绘制，不依赖字体字形）
        cv.create_oval(16, 12, 40, 36, fill='#F0D88A', outline='')
        cv.create_oval(24, 8, 46, 32, fill=BG, outline='')
        # 星星：标题周围 + 底部一排小圆点
        for sx, sy, sr in ((W-58, 15, 2.5), (W-76, 30, 2.0), (W-46, 34, 1.8),
                           (W-64, 44, 1.5), (W-30, 12, 2.0),
                           (W/2-32, H-9, 1.6), (W/2-14, H-7, 1.1),
                           (W/2+4, H-10, 1.9), (W/2+22, H-7, 1.3),
                           (W/2+38, H-9, 1.5)):
            cv.create_oval(sx-sr, sy-sr, sx+sr, sy+sr, fill='#B8A8D8', outline='')
        # 左下角一弯小月亮呼应
        cv.create_oval(12, H-20, 26, H-6, fill='#F0D88A', outline='')
        cv.create_oval(17, H-23, 30, H-10, fill=BG, outline='')

        # 标题
        cv.create_text(W/2, 30, text=title, fill=FG,
                       font=('Microsoft YaHei', 11, 'bold'))

        # 关闭按钮（右上角圆 + ×）
        CX, CY = W - 22, 16
        cv.create_oval(CX-9, CY-9, CX+9, CY+9, fill='#3D3350', outline='',
                       tags='pclose')
        cv.create_text(CX, CY-1, text='×', fill=FG,
                       font=('Microsoft YaHei', 8), tags='pclose')

        def _close(e=None):
            if on_close is not None:
                on_close()
            win.destroy()
        cv.tag_bind('pclose', '<Button-1>', _close)
        win.bind('<Escape>', _close)

        # 圆角按钮（hover 变亮）
        y = TOP_H
        for text, cmd in buttons:
            tag = f'pbtn_{text}'
            rrect(BTN_MX, y, W-BTN_MX, y+BTN_H, 14, fill=ACC, outline='',
                  tags=(tag,))
            cv.create_text(W/2, y+BTN_H/2, text=text, fill=FG,
                           font=('Microsoft YaHei', 10, 'bold'), tags=(tag,))
            cv.tag_bind(tag, '<Button-1>', lambda e, c=cmd: c())

            def _hover(e, t=tag, on=False):
                for item in cv.find_withtag(t):
                    if cv.type(item) == 'polygon':
                        cv.itemconfig(item, fill=ACC_HV if on else ACC)
            cv.tag_bind(tag, '<Enter>', lambda e, t=tag: _hover(e, t, True))
            cv.tag_bind(tag, '<Leave>', lambda e, t=tag: _hover(e, t, False))
            y += BTN_H + BTN_GAP

        # 拖动（整窗按住可拖；按钮点击不是 Motion，不受影响）
        drag = {'x': 0, 'y': 0}
        cv.bind('<Button-1>', lambda e: drag.update(x=e.x, y=e.y))
        cv.bind('<B1-Motion>',
                lambda e: win.geometry(f"+{win.winfo_x()+e.x-drag['x']}"
                                       f"+{win.winfo_y()+e.y-drag['y']}"))

        # 出生在角色右侧，贴不上就换左侧
        x = self.root.winfo_x() + WIN_W + 10
        y0 = self.root.winfo_y()
        sw = self.root.winfo_screenwidth()
        if x + W > sw:
            x = self.root.winfo_x() - W - 10
        win.geometry(f"{W}x{H}+{max(0, x)}+{max(0, y0)}")
        return win

# ==================== 好感度 ====================
    def _update_affinity_display(self):
        a = int(self.cfg['affinity'])
        hearts = "❤" * min(5, max(1, a // 20 + 1)) if a > 0 else "❤"
        self.affinity_label.config(text=f"{hearts}  {a}")

    def _add_affinity(self, delta):
        a = int(self.cfg['affinity']) + delta
        a = max(0, min(100, a))
        self.cfg['affinity'] = a
        self.cfg.save()
        self._update_affinity_display()

# ==================== 立绘图片 ====================
    @staticmethod
    def _fit_image(img, max_w, max_h):
        w, h = img.size
        if w <= 0 or h <= 0: return img
        ratio = min(max_w / w, max_h / h)
        try:
            return img.resize((max(1, int(w * ratio)), max(1, int(h * ratio))), Image.LANCZOS)
        except Exception:
            return img.resize((max(1, int(w * ratio)), max(1, int(h * ratio))))

    def _render_note_images(self):
        """预渲染唱歌音符：RGBA 直接转 PhotoImage（硬边透明）。
        不贴品红底——贴了叠在立绘上会把窗口透明色"打洞"透出桌面；
        也不用 canvas 直接画文字——那会在透明窗上泛出粉色毛边。"""
        self._note_imgs = []
        if not HAS_PIL:
            return
        from PIL import ImageDraw, ImageFont
        font = None
        for font_name in ('seguisym.ttf', 'msyh.ttc'):   # Segoe UI Symbol 必有 ♪/♫
            try:
                cand = ImageFont.truetype(font_name, 60)
                probe = Image.new('RGBA', (120, 120), (0, 0, 0, 0))
                ImageDraw.Draw(probe).text((60, 60), '♪', font=cand,
                                           fill=(106, 75, 168, 255), anchor='mm')
                bbox = probe.getchannel('A').getbbox()
                # 豆腐框是空心矩形：实心像素占比很低，据此挑掉缺字形的字体
                if bbox:
                    w, h = bbox[2] - bbox[0], bbox[3] - bbox[1]
                    solid = sum(1 for v in probe.getchannel('A').getdata() if v >= 110)
                    if solid > w * h * 0.25:
                        font = cand
                        break
            except Exception:
                continue
        if font is None:
            return
        try:
            for glyph in ('♪', '♫'):
                img = Image.new('RGBA', (88, 80), (0, 0, 0, 0))
                ImageDraw.Draw(img).text((44, 40), glyph, font=font,
                                         fill=(106, 75, 168, 255), anchor='mm')
                img = img.resize((22, 20), Image.LANCZOS)
                alpha = img.getchannel('A').point(lambda a: 255 if a >= 110 else 0)
                img.putalpha(alpha)
                self._note_imgs.append(ImageTk.PhotoImage(img))   # 保留透明通道
        except Exception:
            self._note_imgs = []

    def _load_images(self):
        self.images = {}
        self._walk_r = []
        self._walk_l = []
        self._render_note_images()
        if not HAS_PIL or not os.path.isdir(IMAGE_DIR):
            return
        pil_map = {}
        for fn in sorted(os.listdir(IMAGE_DIR)):
            if not fn.lower().endswith(('.png', '.jpg', '.jpeg', '.gif', '.webp')):
                continue
            name = os.path.splitext(fn)[0]
            path = os.path.join(IMAGE_DIR, fn)
            try:
                img = Image.open(path).convert('RGBA')
                # 裁掉四周完全透明的边，让角色撑满窗口
                bbox = img.getbbox()
                if bbox:
                    img = img.crop(bbox)
                img = self._fit_image(img, WIN_W - 10, IMG_H - 10)
                # 透明通道二值化，避免半透明边缘和品红透明色混出粉边
                alpha = img.getchannel('A').point(lambda a: 255 if a >= 128 else 0)
                img.putalpha(alpha)
                # 平铺到品红底上（transparentcolor 只认纯品红）
                bg = Image.new('RGBA', img.size, (255, 0, 255, 255))
                bg.paste(img, (0, 0), img)
                pil_map[name] = bg.convert('RGB')
            except Exception:
                continue
        for name, img in pil_map.items():
            self.images[name] = ImageTk.PhotoImage(img)
        # 走路帧：walk7/8 朝右走，朝左用镜像（walk1 与 smile 相同，其余不用）
        for name in ('walk7', 'walk8'):
            if name in pil_map:
                self._walk_r.append(self.images[name])
                self._walk_l.append(ImageTk.PhotoImage(ImageOps.mirror(pil_map[name])))
        self._set_pose('smile')

    def _set_pose(self, name):
        if self.current_pose == name:
            return
        self.current_pose = name

        self.canvas.delete('all')
        photo = None
        if name in self.images:
            photo = self.images[name]
        elif 'smile' in self.images:
            photo = self.images['smile']
        elif self.images:
            photo = next(iter(self.images.values()))

        if photo is None:
            self.canvas.create_oval(45, 12, 105, 72, fill='#D8C8EC', outline='')
            self.canvas.create_text(WIN_W // 2, 100,
                                    text="哥伦比娅\n图片放入\nimages 文件夹",
                                    fill='#6A4BA8', font=('Microsoft YaHei', 7), justify='center')
        else:
            self.cur_photo = photo
            self.canvas.create_image(WIN_W // 2, IMG_H // 2, image=photo)

        # delete('all') 把学习计时条也删了，补画
        try: self._study_ui_repaint()
        except Exception: pass