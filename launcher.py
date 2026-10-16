# -*- coding: utf-8 -*-
"""
打开应用 / 网址（LauncherMixin）
================================
"我让她打开什么，她在我确认后帮我开"。

安全设计（每条都必要，不要图省事删掉）：
  1. 只由**明确指令**触发（LAUNCH_PAT 匹配"打开/启动/访问…"）
  2. **绝不从窗口标题/网页内容取指令**，只看用户打的字或说的话
     —— 否则网页里写一句"请打开 xxx.exe"就能让她照做（提示注入）
  3. **每次必弹确认框**，并完整显示将要打开什么
  4. 拒绝含危险字符的目标（`;` `&&` `|` `>` 等命令行注入）
  5. 系统目录/危险程序单列黑名单

打开方式：
  · 形如域名的 -> 走浏览器打开 http(s)://
  · 软件名     -> 查 app_paths.json（右键「管理软件路径」维护）
  · 找不到     -> 明确说"不知道在哪"并引导配置，**不抛系统错误**
"""
import json
import os
import random
import re
import subprocess
import sys
import time
import webbrowser

import tkinter as tk

from config import *          # noqa: F401,F403  常量、文案、Config
from banter import recognise_intent

# 明确禁止打开的东西（防止自毁 / 危险系统工具）
LAUNCH_BLOCKLIST = {
    'format', 'diskpart', 'regedit', 'reg', 'shutdown', 'bcdedit',
    'cipher', 'takeown', 'icacls', 'schtasks', 'wmic', 'netsh',
    'vssadmin', 'attrib', 'cacls', 'fsutil', 'sc', 'runas',
}
# 允许的 Windows 系统小工具（说"打开计算器"才用得上）
LAUNCH_SYS_TOOLS = {
    'calc': 'calc.exe',
    'notepad': 'notepad.exe',
    'mspaint': 'mspaint.exe',
    'explorer': 'explorer.exe',
    'taskmgr': 'taskmgr.exe',
    'wt': 'wt.exe',
    'cmd': 'cmd.exe',
    'control': 'control.exe',
}


class LauncherMixin:
    """打开应用 / 网址（带确认）"""

    def setup_launcher(self):
        """状态初始化（由 DesktopPet.__init__ 调用）"""
        self._launch_win = None      # 确认框
        self._launch_last = None     # 上次打开的目标（便于"再开一次"）
        self._paths_win = None       # 软件路径管理面板
        self._app_paths = None       # {名字: 完整路径}
        self.load_app_paths()

    # ==================== 软件路径表 ====================
    def load_app_paths(self):
        """读取 app_paths.json；首次运行用 APP_PATHS_SEED 播种（只填真实存在的）"""
        data = {}
        try:
            if os.path.exists(APP_PATHS_PATH):
                with open(APP_PATHS_PATH, 'r', encoding='utf-8') as f:
                    data = json.load(f) or {}
        except Exception:
            data = {}
        if not isinstance(data, dict):
            data = {}

        # 首次：把确实存在的常见软件填进去，省得你手动配
        seeded = False
        for name, path in APP_PATHS_SEED.items():
            key = name.lower()
            if key in data:
                continue
            full = os.path.expandvars(path)
            if os.path.exists(full):
                data[key] = full
                seeded = True

        self._app_paths = data
        if seeded or not os.path.exists(APP_PATHS_PATH):
            self.save_app_paths()
        return self._app_paths

    def save_app_paths(self):
        try:
            tmp = APP_PATHS_PATH + '.tmp'
            with open(tmp, 'w', encoding='utf-8') as f:
                json.dump(self._app_paths or {}, f, ensure_ascii=False, indent=2)
            os.replace(tmp, APP_PATHS_PATH)
        except Exception:
            pass

    def find_app_path(self, name):
        """按名字查路径表。支持大小写、空格、带/不带 .exe，
        也支持用**文件名**反查（说 'msedge' 能命中键 'edge'）。"""
        if not name:
            return None
        table = getattr(self, '_app_paths', None) or {}
        key = name.strip().lower()
        if key in table:
            return table[key]
        k2 = key[:-4] if key.endswith('.exe') else key
        if k2 in table:
            return table[k2]
        k3 = key.replace(' ', '')
        if k3 in table:
            return table[k3]
        # 归一化比对 + 按文件名反查
        target = k3[:-4] if k3.endswith('.exe') else k3
        for k, v in table.items():
            kn = k.replace(' ', '').lower()
            if kn == k3 or kn == target:
                return v
            stem = os.path.splitext(os.path.basename(str(v)))[0].lower()
            if stem and stem == target:
                return v
        return None

    # ==================== 意图识别入口 ====================
    def launch_handle_text(self, text):
        """从一句话里认出"要打开什么 / 要搜什么"。

        返回 True 表示这句话已被处理（不要再交给大模型闲聊）。
        """
        raw = (text or '').strip()

        # --- 优先：明确说"从浏览器打开/搜索" → 走浏览器 ---
        m = BROWSER_OPEN_PAT.search(raw)
        if m:
            what = (m.group(1) or '').strip().strip('。！？!?，,、 ')
            if what:
                self.launch_browser(what)
                return True

        # --- 其次：搜索意图（百度一下/搜索/查一下）---
        m = SEARCH_PAT.search(raw)
        if m:
            what = (m.group(1) or '').strip().strip('。！？!?，,、 ')
            if what:
                self.launch_browser(what, force_search=True)
                return True

        # --- 然后：打开意图 ---
        target = recognise_intent(raw)
        if not target:
            return False

        # "打开学习"/"打开唱歌" 这类是本地功能，不弹系统确认框
        low = target.lower()
        if low in ('学习', 'study', '学习计时'):
            self.study_show_panel()
            return True
        if low in ('唱歌', 'sing'):
            self.song_show_panel()
            return True
        if low in ('她在看什么', '活动记录', 'activity'):
            self.aware_show_panel()
            return True
        if low in ('软件路径', '路径管理', 'app_paths'):
            self.paths_show_panel()
            return True

        # 路径表里有的，直接用完整路径（这是"我给她路径"的意义）
        p = self.find_app_path(target)
        if p:
            self.launch_ask(p)
            return True

        self.launch_ask(target)
        return True

    # ==================== 浏览器打开 / 搜索 ====================
    def launch_browser(self, what, force_search=False):
        """从浏览器打开：像网址就直接开，否则在浏览器里搜"""
        what = (what or '').strip()
        if not what:
            return
        is_url = bool(URL_PAT.match(what))
        if is_url and not force_search:
            url = what if what.lower().startswith(('http://', 'https://')) \
                else 'https://' + what
            self._browser_open(url, what, '网址')
        else:
            import urllib.parse
            url = SEARCH_ENGINE_URL.format(q=urllib.parse.quote(what))
            self._browser_open(url, what, '搜索', show_kind=True)

    def _browser_open(self, url, what, kind, show_kind=False):
        """走确认框再开浏览器（保持一致的安全习惯）"""
        import tkinter as tk
        if self._launch_win is not None:
            try:
                self._launch_win.destroy()
            except Exception:
                pass
            self._launch_win = None

        win = tk.Toplevel(self.root)
        win.title('要用浏览器打开吗')
        win.attributes('-topmost', True)
        win.resizable(False, False)
        x = self.root.winfo_x() + WIN_W + 10
        y = self.root.winfo_y()
        sw = self.root.winfo_screenwidth()
        if x + 360 > sw:
            x = max(0, self.root.winfo_x() - 370)
        win.geometry(f'360x215+{x}+{y}')

        outer = tk.Frame(win, bg=BUBBLE_BG)
        outer.pack(fill='both', expand=True, padx=10, pady=10)
        title = '要搜索这个吗？' if kind == '搜索' else '要用浏览器打开吗？'
        tk.Label(outer, text=title, bg=BUBBLE_BG, fg='#6A4BA8',
                 font=('Microsoft YaHei', 11, 'bold')).pack(anchor='w')
        tk.Label(outer, text=f'类型：{kind}', bg=BUBBLE_BG, fg='#8A7BA8',
                 font=('Microsoft YaHei', 8)).pack(anchor='w', pady=(4, 0))

        holder = tk.Frame(outer, bg='#FFFDF9',
                          highlightthickness=1, highlightbackground='#D9C9B8')
        holder.pack(fill='both', expand=True, pady=6)
        txt = tk.Text(holder, height=3, bg='#FFFDF9', fg=BUBBLE_FG,
                      relief='flat', font=('Microsoft YaHei', 9), wrap='char',
                      padx=6, pady=4)
        txt.pack(fill='both', expand=True)
        txt.insert('end', url)
        txt.config(state='disabled')

        btns = tk.Frame(outer, bg=BUBBLE_BG)
        btns.pack(fill='x')

        def close():
            try:
                win.destroy()
            except Exception:
                pass
            self._launch_win = None

        def go():
            close()
            try:
                webbrowser.open(url)
                self._reset_idle_timer()
                self._speak(random.choice(LAUNCH_SEARCH_LINES))
            except Exception as e:
                self._speak(f'……（浏览器没打开。{e}）')

        for text, cmd, primary in (('打开', go, True), ('取消', close, False)):
            tk.Button(btns, text=text, font=('Microsoft YaHei', 9),
                      bg='#6A4BA8' if primary else '#E8E0F0',
                      fg='white' if primary else '#6A4BA8',
                      relief='flat', cursor='hand2', padx=14, pady=8,
                      command=cmd).pack(side='right', padx=4)

        win.protocol('WM_DELETE_WINDOW', close)
        self._launch_win = win

    # ---------- 安全校验 ----------
    def _launch_check(self, target):
        """校验目标是否允许打开。返回 (ok, 说明, 规范化目标)

        检查顺序（很关键，别随手调换）：
            1 危险字符 -> 2 黑名单 -> 3 网址 -> 4 已存在路径
            5 软件路径表（含中文别名）-> 6 系统小工具 -> 7 系统 PATH
            8 找不到 -> 明确报告，绝不交给系统（否则弹"找不到指定文件"）
        """
        t = (target or '').strip().strip('「」『』"\'')
        if not t:
            return False, '没有说要打开什么', ''

        low = t.lower()

        # 1) 危险字符（命令行注入 / 链式命令 / 重定向）
        for bad in LAUNCH_FORBIDDEN:
            if bad in low:
                return False, f'含有不允许的字符：{bad}', ''

        # 2) 黑名单（按第一段词判断，避免误伤路径里的同名片段）
        head = re.sub(r'\.exe$', '', re.split(r'[\\/\s]', low)[0])
        if head in LAUNCH_BLOCKLIST:
            return False, f'「{head}」属于危险系统工具，我不会打开它', ''

        # 3) 网址 —— 必须排在别名之前！
        #    否则 "B站" 这类别名会先命中，把域名抢走。
        if URL_PAT.match(t):
            url = t if low.startswith(('http://', 'https://')) else 'https://' + t
            return True, '网址', url

        # 4) 已经是存在的路径
        if os.path.exists(t):
            if os.path.isdir(t):
                return True, '文件夹', t
            ext = os.path.splitext(t)[1].lower()
            if ext in ('.exe', '.bat', '.cmd', '.lnk', '.url'):
                return True, '程序', t
            return True, '文件', t          # 文档等交给系统默认程序

        # 5) 软件路径表 —— 用户配置优先，这就是「我给她路径」的意义。
        p = self.find_app_path(t)
        if p and os.path.exists(p):
            return True, '已配置的软件', p
        alias = LAUNCH_ALIASES.get(low)
        if alias and alias not in ('study', 'sing'):
            p2 = self.find_app_path(alias)
            if p2 and os.path.exists(p2):
                return True, '已配置的软件', p2

        # 6) 系统小工具白名单（用户没配过就用系统的）
        #    这一步必须排在"别名报缺失"之前：否则「记事本」→notepad
        #    会因为路径表没配而误报"找不到"。
        #    注意：白名单的键是英文，所以中文名要先过一遍别名
        #    （「记事本」→ notepad），否则永远命中不了。
        sys_key = alias or head
        if sys_key in LAUNCH_SYS_TOOLS:
            return True, '系统工具', LAUNCH_SYS_TOOLS[sys_key]

        # 6b) 别名（含"浏览器"这类泛指）都试过还是没路径 -> 明确报缺失
        if alias and alias not in ('study', 'sing'):
            for cand in BROWSER_CANDIDATES.get(low, ()):
                p3 = self.find_app_path(cand)
                if p3 and os.path.exists(p3):
                    return True, '已配置的软件', p3
            return False, 'NOT_FOUND', alias

        # 7) 系统 PATH 里能找到的可执行文件（python / git / node …）
        import shutil
        for cand in (t, t + '.exe'):
            found = shutil.which(cand)
            if found:
                return True, '系统命令', found

        # 8) 找不到：**不再交给系统** —— 那正是"系统找不到指定文件"的来源
        return False, 'NOT_FOUND', t

    # ---------- 确认框 ----------
    def launch_ask(self, target):
        """弹确认框：明确显示要打开什么，用户点确认才真的开"""
        import tkinter as tk

        ok, kind, norm = self._launch_check(target)
        if not ok:
            if kind == 'NOT_FOUND':
                # 找不到：说清楚 + 引导配置，绝不抛系统错误
                name = os.path.basename(norm) or norm
                self._speak(random.choice(LAUNCH_NOT_FOUND_LINES).format(name=name))
                self._speak(LAUNCH_NOT_FOUND_HINT)
                self._launch_unknown = norm
            else:
                self._speak(random.choice(LAUNCH_DENY_LINES))
                self._speak(f'……（{norm}）')
            return

        if self._launch_win is not None:
            try:
                self._launch_win.destroy()
            except Exception:
                pass
            self._launch_win = None

        win = tk.Toplevel(self.root)
        win.title(LAUNCH_CONFIRM_TITLE)
        win.attributes('-topmost', True)
        win.resizable(False, False)
        x = self.root.winfo_x() + WIN_W + 10
        y = self.root.winfo_y()
        sw = self.root.winfo_screenwidth()
        if x + 340 > sw:
            x = max(0, self.root.winfo_x() - 350)
        win.geometry(f'340x210+{x}+{y}')

        outer = tk.Frame(win, bg=BUBBLE_BG)
        outer.pack(fill='both', expand=True, padx=10, pady=10)

        tk.Label(outer, text='要打开这个吗？', bg=BUBBLE_BG, fg='#6A4BA8',
                 font=('Microsoft YaHei', 11, 'bold')).pack(anchor='w')

        tk.Label(outer, text=f'类型：{kind}', bg=BUBBLE_BG, fg='#8A7BA8',
                 font=('Microsoft YaHei', 8)).pack(anchor='w', pady=(4, 0))

        # 完整目标（换行显示，绝不截断——用户要看清才知道自己在确认什么）
        holder = tk.Frame(outer, bg='#FFFDF9',
                          highlightthickness=1, highlightbackground='#D9C9B8')
        holder.pack(fill='both', expand=True, pady=6)
        txt = tk.Text(holder, height=3, bg='#FFFDF9', fg=BUBBLE_FG,
                      relief='flat', font=('Microsoft YaHei', 9), wrap='char',
                      padx=6, pady=4)
        txt.pack(fill='both', expand=True)
        txt.insert('end', norm)
        txt.config(state='disabled')

        btns = tk.Frame(outer, bg=BUBBLE_BG)
        btns.pack(fill='x')

        def close():
            try:
                win.destroy()
            except Exception:
                pass
            self._launch_win = None

        def do_open():
            close()
            self.launch_do(norm, kind)

        def do_copy():
            try:
                self.root.clipboard_clear()
                self.root.clipboard_append(norm)
                self._speak('……嗯，复制好了。')
            except Exception:
                pass
            close()

        for text, cmd, primary in (('打开', do_open, True),
                                   ('复制', do_copy, False),
                                   ('取消', close, False)):
            tk.Button(btns, text=text, font=('Microsoft YaHei', 9),
                      bg='#6A4BA8' if primary else '#E8E0F0',
                      fg='white' if primary else '#6A4BA8',
                      relief='flat', cursor='hand2', padx=14, pady=8,
                      command=cmd).pack(side='right', padx=4)

        win.protocol('WM_DELETE_WINDOW', close)
        self._launch_win = win

    # ---------- 真正打开 ----------
    def launch_do(self, target, kind=''):
        """执行打开。只在用户点过确认后调用。

        注意：走到这里的东西都已经过 _launch_check 确认**存在**，
        所以不该再出现"系统找不到指定文件"。
        """
        try:
            if kind == '网址':
                webbrowser.open(target)
            elif kind in ('程序', '系统工具', '别名', '系统命令',
                          '已配置的软件'):
                self._spawn(target)
            elif kind in ('文件夹', '文件'):
                os.startfile(target)
            else:
                # 兜底：存在的路径就直接交给系统，否则报告找不到
                if os.path.exists(target):
                    os.startfile(target)
                else:
                    self._speak(random.choice(LAUNCH_NOT_FOUND_LINES)
                                .format(name=os.path.basename(target) or target))
                    return
            self._launch_last = target
            self._reset_idle_timer()
            # 记一笔（日记里她会写"你让我帮你开了几次东西"）
            try:
                self.activity.bump_day('launches', 1)
            except Exception:
                pass
            self._speak(random.choice(LAUNCH_OK_LINES))
        except FileNotFoundError:
            # 理论上不该走到这里（_launch_check 已确认存在），
            # 但环境变量展开等边界情况仍可能发生 —— 给人话，不给系统错误。
            self._launch_fail_human(target, '路径不存在')
        except Exception as e:
            self._launch_fail_human(target, e)

    def _launch_fail_human(self, target, err):
        """打开失败：说人话，并把细节写日志（而不是把系统错误糊到气泡上）"""
        name = os.path.basename(target) or target
        self._speak(random.choice(LAUNCH_FAIL_LINES))
        self._speak(f'……（{name}）')
        try:
            with open(os.path.join(BASE_DIR, 'launch-debug.log'),
                      'a', encoding='utf-8') as f:
                f.write(f'[{time.strftime("%H:%M:%S")}] 打开失败 '
                        f'kind={target!r}: {err}\n')
        except Exception:
            pass

    @staticmethod
    def _spawn(exe):
        """启动进程。Windows 下用 DETACHED，避免桌宠退出时连带被杀。"""
        flags = 0
        if sys.platform == 'win32':
            DETACHED_PROCESS = 0x00000008
            CREATE_NEW_PROCESS_GROUP = 0x00000200
            flags = DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
        subprocess.Popen([exe], shell=False, close_fds=True,
                         creationflags=flags)

    # ==================== 软件路径管理面板 ====================
    def paths_show_panel(self):
        """右键菜单「🔗 管理软件路径」：增删改「名字 -> 完整路径」"""
        import tkinter as tk

        if self._paths_win is not None:
            try:
                self._paths_win.lift()
                self._paths_win.focus_force()
                return
            except Exception:
                self._paths_win = None

        win = tk.Toplevel(self.root)
        win.title('管理软件路径')
        win.attributes('-topmost', True)
        win.resizable(False, False)
        x = self.root.winfo_x() + WIN_W + 10
        y = self.root.winfo_y()
        sw = self.root.winfo_screenwidth()
        if x + 560 > sw:
            x = max(0, self.root.winfo_x() - 570)
        win.geometry(f'560x420+{x}+{y}')

        outer = tk.Frame(win, bg=BUBBLE_BG)
        outer.pack(fill='both', expand=True, padx=10, pady=10)

        tk.Label(outer, text='说「打开 XX」，她就按这里的路径启动',
                 bg=BUBBLE_BG, fg='#6A4BA8',
                 font=('Microsoft YaHei', 10, 'bold')).pack(anchor='w')
        tk.Label(outer, text='名字随便写（中文也行），路径要填完整的 .exe 路径',
                 bg=BUBBLE_BG, fg='#8A7BA8',
                 font=('Microsoft YaHei', 8)).pack(anchor='w', pady=(2, 8))

        # ---- 列表 ----
        mid = tk.Frame(outer, bg=BUBBLE_BG)
        mid.pack(fill='both', expand=True)
        sb = tk.Scrollbar(mid)
        sb.pack(side='right', fill='y')
        lb = tk.Listbox(mid, bg='#FFFDF9', fg=BUBBLE_FG, relief='flat',
                        font=('Microsoft YaHei', 9),
                        yscrollcommand=sb.set, activestyle='none')
        lb.pack(side='left', fill='both', expand=True)
        sb.config(command=lb.yview)

        def refresh():
            lb.delete(0, 'end')
            table = getattr(self, '_app_paths', None) or {}
            for name in sorted(table):
                p = table[name]
                mark = '' if os.path.exists(p) else '  ⚠ 路径不存在'
                lb.insert('end', f'{name}   →   {p}{mark}')

        # ---- 输入区 ----
        form = tk.Frame(outer, bg=BUBBLE_BG)
        form.pack(fill='x', pady=(8, 0))
        tk.Label(form, text='名字', bg=BUBBLE_BG, fg=BUBBLE_FG,
                 font=('Microsoft YaHei', 9)).grid(row=0, column=0, sticky='w')
        name_var = tk.StringVar()
        tk.Entry(form, textvariable=name_var, width=14,
                 font=('Microsoft YaHei', 9)).grid(row=0, column=1, padx=4)
        tk.Label(form, text='路径', bg=BUBBLE_BG, fg=BUBBLE_FG,
                 font=('Microsoft YaHei', 9)).grid(row=0, column=2, sticky='w')
        path_var = tk.StringVar()
        tk.Entry(form, textvariable=path_var, width=34,
                 font=('Microsoft YaHei', 9)).grid(row=0, column=3, padx=4)

        def pick_file():
            from tkinter import filedialog
            p = filedialog.askopenfilename(
                title='选择程序',
                filetypes=[('可执行文件', '*.exe'), ('所有文件', '*.*')])
            if p:
                path_var.set(os.path.normpath(p))
                if not name_var.get().strip():
                    name_var.set(os.path.splitext(os.path.basename(p))[0].lower())

        def on_select(_evt=None):
            sel = lb.curselection()
            if not sel:
                return
            line = lb.get(sel[0])
            if '   →   ' in line:
                nm, rest = line.split('   →   ', 1)
                name_var.set(nm.strip())
                path_var.set(rest.replace('  ⚠ 路径不存在', '').strip())

        lb.bind('<<ListboxSelect>>', on_select)

        def add_or_update():
            name = name_var.get().strip().lower()
            path = path_var.get().strip().strip('"')
            if not name or not path:
                self._speak('……名字和路径都要填。')
                return
            self._app_paths[name] = os.path.normpath(path)
            self.save_app_paths()
            refresh()
            self._speak(f'……嗯，「{name}」我记住了。')

        def remove():
            sel = lb.curselection()
            if not sel:
                return
            line = lb.get(sel[0])
            nm = line.split('   →   ')[0].strip()
            self._app_paths.pop(nm, None)
            self.save_app_paths()
            refresh()
            name_var.set('')
            path_var.set('')
            self._speak(f'……「{nm}」我忘掉了。')

        def test_open():
            sel = lb.curselection()
            if not sel:
                return
            nm = lb.get(sel[0]).split('   →   ')[0].strip()
            p = self._app_paths.get(nm)
            self.launch_ask(p or nm)

        btns = tk.Frame(outer, bg=BUBBLE_BG)
        btns.pack(fill='x', pady=(8, 0))

        def mk(text, cmd, primary=False):
            return tk.Button(btns, text=text, font=('Microsoft YaHei', 9),
                             bg='#6A4BA8' if primary else '#E8E0F0',
                             fg='white' if primary else '#6A4BA8',
                             relief='flat', cursor='hand2', padx=10, pady=6,
                             command=cmd)

        mk('浏览…', pick_file).pack(side='left', padx=3)
        mk('保存', add_or_update, True).pack(side='left', padx=3)
        mk('删除', remove).pack(side='left', padx=3)
        mk('试打开', test_open).pack(side='left', padx=3)

        def close():
            try:
                win.destroy()
            except Exception:
                pass
            self._paths_win = None

        mk('关闭', close).pack(side='right', padx=3)
        win.protocol('WM_DELETE_WINDOW', close)

        refresh()
        self._paths_win = win

