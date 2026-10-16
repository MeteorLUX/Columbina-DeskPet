# -*- coding: utf-8 -*-
"""
日记（DiaryMixin）
==================
每天自动写下"你和她做了什么"，用她的口吻，温柔简短。

素材来源（都是项目里已有的数据）：
    学习     study_records.json   今天学了多久、几段
    使用     activity_log.json    今天陪她用的软件（**只记用了很久的**）
    天气     weather_cache.json   今天什么天气、有没有特殊天气
    节日     holidays.py          今天是什么日子
    对话     运行时收集            今天你们聊过的只言片语

设计约束（按用户要求）：
    · **只能看和删，不能加不能改** —— 所以数据只由程序生成
    · 口吻是她的：平淡、简短、带省略号，不堆砌
    · 内容不用多：3~6 句
    · 用了很久的软件才提一句，细碎的不记
"""
import datetime
import json
import os
import random
import time

from config import *          # noqa: F401,F403  常量、文案、Config
from holidays import (today_str, date_line, weekday_cn,
                      holiday_of_day, is_birthday)


class DiaryEntry:
    """一天的日记"""
    __slots__ = ('date', 'lines', 'created', 'auto')

    def __init__(self, date, lines, created=None, auto=True):
        self.date = date
        self.lines = list(lines or [])
        self.created = created or time.time()
        self.auto = auto

    def to_dict(self):
        return {'date': self.date, 'lines': self.lines,
                'created': self.created, 'auto': self.auto}

    @classmethod
    def from_dict(cls, d):
        return cls(d.get('date'), d.get('lines'),
                   d.get('created'), d.get('auto', True))


class DiaryBook:
    """日记本：按日期存，支持查看 / 删除（**不支持手工新增或修改**）"""

    def __init__(self, path):
        self.path = path
        self.entries = {}          # {'YYYY-MM-DD': DiaryEntry}
        self._load()

    # ---------- 磁盘 ----------
    def _load(self):
        try:
            if not os.path.exists(self.path):
                return
            with open(self.path, 'r', encoding='utf-8') as f:
                raw = json.load(f) or {}
            items = raw.get('entries') or {}
            for d, e in items.items():
                self.entries[d] = DiaryEntry.from_dict(e)
        except Exception:
            self.entries = {}

    def save(self):
        try:
            tmp = self.path + '.tmp'
            with open(tmp, 'w', encoding='utf-8') as f:
                json.dump({'version': 1,
                           'entries': {d: e.to_dict()
                                       for d, e in sorted(self.entries.items())}},
                          f, ensure_ascii=False, indent=2)
            os.replace(tmp, self.path)
        except Exception:
            pass

    # ---------- 查询 / 删除（唯一允许的写操作）----------
    def get(self, date):
        return self.entries.get(date)

    def dates(self, newest_first=True):
        return sorted(self.entries, reverse=newest_first)

    def remove(self, date):
        e = self.entries.pop(date, None)
        if e is not None:
            self.save()
        return e is not None

    def clear_all(self):
        n = len(self.entries)
        self.entries = {}
        self.save()
        return n

    def has(self, date):
        return date in self.entries

    def count(self):
        return len(self.entries)


class DiaryMixin:
    """日记：自动生成 + 右键查看 / 删除"""

    def setup_diary(self, path=None):
        """初始化日记本。

        path 可覆盖存储位置 —— **测试必须传临时路径**。
        踩过的坑：日记回归测试直接用了真实 DesktopPet，并把假素材
        （Chrome 7200 秒等 fixture）写进了真实的 activity_log.json，
        污染了用户的真实记录。所以测试要能把存储位置改到临时目录。
        """
        self.diary = DiaryBook(path or DIARY_PATH)
        self._diary_win = None
        self._chat_snippets = []      # 今天聊过的只言片语
        self._chat_snippet_date = today_str()

    # ==================== 对话片段收集 ====================
    def diary_note_exchange(self, user_text, reply_text):
        """在每次对话结束时调用（`_on_reply` 里挂钩）。

        【为什么要放宽过滤】
          原来要求 ≤40 字、且不含「（）」和 http，结果几乎什么都留不下
          —— 用户反馈"没有我和她对话的记录"。现在：
            · 放宽到 60 字（她的台词本来就偏长）
            · 只真正过滤代码/网址这类明显不该进日记的
            · 一天最多留 DIARY_MAX_SNIPPETS 条
        """
        try:
            today = today_str()
            if today != self._chat_snippet_date:
                self._chat_snippet_date = today
                self._chat_snippets = []

            u = (user_text or '').strip()
            r = (reply_text or '').strip()

            def bad(t):
                """该丢掉的：空的 / 太长 / 明显是代码或链接"""
                if not t:
                    return True
                if len(t) > 60:
                    return True
                if any(c in t for c in ('{', '}', 'http', 'www.', '```')):
                    return True
                # 纯符号/纯英文代码行不要
                if not any(ch.isalnum() or '\u4e00' <= ch <= '\u9fff'
                           for ch in t):
                    return True
                return False

            if bad(u) and bad(r):
                return
            item = {}
            if not bad(u):
                item['u'] = u
            if not bad(r):
                item['r'] = r
            if item and len(self._chat_snippets) < DIARY_MAX_SNIPPETS:
                self._chat_snippets.append(item)
                # 顺手记一笔当天的对话轮数（日记靠它判断"今天聊得多不多"）
                try:
                    self.activity.bump_day('chats', 1)
                except Exception:
                    pass
        except Exception:
            pass

    # ==================== 素材收集 ====================
    def _diary_study(self, d):
        """今天的学习情况 -> (分钟, 段数)"""
        mins, segs = 0.0, 0
        for rec in (self._study_records or []):
            try:
                start = str(rec.get('start') or '')
                if not start.startswith(d):
                    continue
                secs = float(rec.get('seconds') or rec.get('duration') or 0)
                if secs <= 0:
                    continue
                mins += secs / 60.0
                segs += 1
            except Exception:
                continue
        return mins, segs

    def _diary_apps(self, d):
        """今天的电脑使用：只挑**用了很久**的，按分钟倒序。

        **必须查当天明细**（`activity.day_top_apps(d)`）。
        原来查的是 `activity.data['apps']` —— 那是**周累计**，
        所以日记里写"你今天用了 X 小时"其实是整周的，全是流水账。
        """
        out = []
        try:
            rows = self.activity.day_top_apps(
                d, n=DIARY_TOP_APPS, min_sec=DIARY_MIN_APP_SEC)
            out = [(name, secs) for name, secs, _sess in rows]
        except Exception:
            return out
        return out

    def _diary_day_stats(self, d):
        """收集当天可用于写日记的统计。全部来自**按天**数据。"""
        st = {'busy_sec': 0.0, 'chats': 0, 'pets': 0, 'launches': 0,
              'study_sec': 0.0, 'sites': [], 'apps': [], 'has': False}
        try:
            st['busy_sec'] = float(self.activity.day_busy_sec(d) or 0)
            st['has'] = bool(self.activity.has_day(d))
            day = self.activity.day_data(d) or {}
            st['chats'] = int(day.get('chats') or 0)
            st['pets'] = int(day.get('pets') or 0)
            st['launches'] = int(day.get('launches') or 0)
            st['study_sec'] = float(day.get('study_sec') or 0)
            st['sites'] = self.activity.day_top_sites(d, n=2)
            st['apps'] = self.activity.day_top_apps(
                d, n=DIARY_TOP_APPS, min_sec=DIARY_MIN_APP_SEC)
        except Exception:
            pass
        return st

    def _diary_mood_tag(self, d, st):
        """按当天情况挑一个"感受"分组名（对应 DIARY_MOODS 的 key）。

        判断顺序 = 从最具体到最笼统：
          天气特殊 -> 学习 -> 互动多 -> 聊得多 -> 很闲 -> 很忙 -> 冷清
        """
        # 1) 天气特殊优先（下雨下雪比"忙"更值得写）
        w = self._diary_weather()
        if w is not None and d == today_str():
            txt = _weather_word(w) or ''
            if '雪' in txt:
                return 'snow'
            if '雨' in txt:
                return 'rain'
            if w.temp is not None:
                if w.temp >= 32:
                    return 'hot'
                if w.temp <= 3:
                    return 'cold'

        # 2) 有学习
        if st['study_sec'] >= 60:
            return 'study'
        # 3) 被摸头多
        if st['pets'] >= 3:
            return 'petted'
        # 4) 聊得多 / 几乎没聊
        if st['chats'] >= 4:
            return 'chatty'
        if st['has'] and st['chats'] == 0 and st['busy_sec'] >= 1800:
            return 'lonely'
        # 5) 很闲 / 很忙
        if st['has'] and st['busy_sec'] < 900:
            return 'idle'
        if st['busy_sec'] >= 3600 * 3:
            return 'busy'
        # 6) 深夜
        try:
            if time.localtime().tm_hour >= 23 or time.localtime().tm_hour < 5:
                return 'latenight'
        except Exception:
            pass
        return 'idle' if st['has'] else 'lonely'

    def _diary_mood_line(self, d, st, skip_tags=None):
        """取一条"她自己的感受"。

        skip_tags：不要挑这些组（避免与已写的内容重复，比如学习）
        """
        try:
            tag = self._diary_mood_tag(d, st)
            if skip_tags and tag in skip_tags:
                # 换一个"通用"组，别让这块空着
                tag = None
                for alt in ('idle', 'busy', 'chatty', 'lonely'):
                    if alt not in (skip_tags or set()) and \
                            (DIARY_MOODS or {}).get(alt):
                        tag = alt
                        break
                if tag is None:
                    tag = 'idle'
            pool = (DIARY_MOODS or {}).get(tag) or []
            if not pool:
                return None
            line = random.choice(pool)
            # 占位符填充
            mins_txt = _fmt_min(st['study_sec'] / 60) if st['study_sec'] \
                else '一会儿'
            line = line.replace('{mins}', mins_txt)
            line = line.replace('{pets}', str(st['pets']))
            return line
        except Exception:
            return None

    def _diary_asides(self, d, st, k=2, avoid=None):
        """随机抽 1~2 件"小观察"（用户要的"随机事件，不固定套路"）。

        每天的素材池不一样（有什么就抽什么），所以不会每天一个样。
        avoid：已经提过的名字（软件/网站），不要再重复说。
        """
        avoid = {str(x) for x in (avoid or set())}
        cands = []
        try:
            # 她在看什么（已提过的名字就不要再抽）
            if st['apps']:
                name, secs, _s = st['apps'][0]
                if secs >= DIARY_MIN_APP_SEC and str(name) not in avoid:
                    cands.append(('app', {'app': name,
                                          'mins': _fmt_min(secs / 60)}))
            if st['sites']:
                site, secs = st['sites'][0]
                if str(site) not in avoid:
                    cands.append(('site', {'site': site,
                                           'mins': _fmt_min(secs / 60)}))
            if st['launches'] > 0:
                cands.append(('launch', {'n': st['launches']}))
            # 观察月亮/自己/你 基本总可以写
            cands.append(('moon', {'n': random.randint(12, 88)}))
            cands.append(('self', {}))
            if st['apps'] and str(st['apps'][0][0]) not in avoid:
                cands.append(('you', {'app': st['apps'][0][0]}))
            try:
                doy = datetime.date.fromisoformat(d).timetuple().tm_yday
                cands.append(('date', {'n': doy}))
            except Exception:
                pass
        except Exception:
            pass

        if not cands:
            return []
        random.shuffle(cands)
        out = []
        used_tags = set()
        used_words = set()      # 已经提过的软件/网站名，避免重复
        chosen_tags = []
        # 先把 app/site 这类"具体名词"的挑出来定下基调，
        # 再考虑 moon/self 这类泛泛的，避免同一个软件被说两遍
        # （踩过：日记里「Deepseek Harness」出现两次）
        cands.sort(key=lambda c: 0 if c[0] in ('app', 'site', 'launch') else 1)
        for tag, ph in cands:
            if tag in used_tags:
                continue
            # 这一条会用到某个具体名字吗？用过就跳过
            w = None
            for key in ('app', 'site'):
                if key in (ph or {}):
                    w = str(ph[key])
                    break
            if w and w in used_words:
                continue
            pool = (DIARY_ASIDES or {}).get(tag) or []
            if not pool:
                continue
            line = random.choice(pool)
            for k2, v2 in (ph or {}).items():
                line = line.replace('{' + k2 + '}', str(v2))
            out.append(line)
            used_tags.add(tag)
            chosen_tags.append(tag)
            if w:
                used_words.add(w)
            if len(out) >= k:
                break
        return out

    def _diary_weather(self):
        try:
            info = self._weather.info if self._weather else None
            return info if (info and info.ok) else None
        except Exception:
            return None

    # ==================== 生成 ====================
    def diary_generate(self, date=None, force=False):
        """生成（或重新生成）某天的日记。返回 DiaryEntry 或 None。

        注意：这是**内部**生成入口，界面上的用户不能手工新增/修改。
        """
        d = date or today_str()
        if self.diary.has(d) and not force:
            return self.diary.get(d)

        try:
            dt = datetime.date.fromisoformat(d)
        except Exception:
            dt = datetime.date.today()

        lines = []
        lines.append(f'{date_line(dt)}')

        # ---- 节日 ----
        name, greet = holiday_of_day(self.cfg['birthday'], dt)
        if name:
            lines.append(greet)

        # ---- 天气 ----
        w = self._diary_weather()
        if w is not None and d == today_str():
            seg = f'天气{_weather_word(w)}'
            if w.temp is not None:
                seg += f'，{round(w.temp)}°C'
            lines.append(f'……{seg}。')
            if w.special:
                r = w.remark()
                if r:
                    lines.append(r)

        # ---- 当天统计（全部来自**按天**数据，不是周累计）----
        st = self._diary_day_stats(d)

        # ---- 学习（优先用当天明细；没有就用学习记录文件兜底）----
        # 注意：学习只写**一次**。mood 分组里也有一组 study，所以要告诉
        # `_diary_mood_line` 别重复挑到它（踩过：日记里学习出现两遍）。
        mins, segs = self._diary_study(d)
        if st['study_sec'] >= 60 and st['study_sec'] / 60 > mins:
            mins = st['study_sec'] / 60
        wrote_study = False
        if mins >= 1:
            lines.append(random.choice([
                f'……今天一起学习了 {_fmt_min(mins)}。',
                f'……陪你学了 {_fmt_min(mins)}。你比昨天更安静了。',
                f'……{_fmt_min(mins)}。我一直在旁边，一动没动。',
                f'……你坐下来学的时候，我就把声音放轻了。{_fmt_min(mins)}，挺好的。',
            ]))
            wrote_study = True

        # ---- 电脑使用（只提用了很久的，且是**今天**的）----
        apps = st['apps']
        if apps:
            top = apps[0]
            if len(apps) == 1:
                lines.append(f'……你在「{top[0]}」里待了 {_fmt_min(top[1] / 60)}。')
            else:
                names = '、'.join(a[0] for a in apps[:3])
                lines.append(f'……今天大部分时间在{names}。')
                if top[1] >= 3600:
                    lines.append(f'……其中「{top[0]}」最久，'
                                 f'{_fmt_min(top[1] / 60)}。')

        # ---- 她自己的感受（按当天情况选组；学习已写过就跳过 study 组）----
        mood = self._diary_mood_line(d, st, skip_tags={'study'} if wrote_study
                                     else set())
        if mood:
            lines.append(mood)

        # ---- 对话片段（**多条**，不是只随机取一条）----
        snips = self._chat_snippets if d == today_str() else []
        if snips:
            # 最多写 3 组，避免日记太长
            pick_n = min(len(snips), 3)
            for pick in random.sample(snips, pick_n):
                u = _strip_end(pick.get('u'))
                r = _strip_end(pick.get('r'))
                if u:
                    lines.append(f'……你和我说过「{u}」。')
                if r:
                    lines.append(f'……我回了你：「{r}」。')
        elif st['chats'] > 0:
            # 有对话但片段都被过滤掉了（太长/带括号）：也要让日记体现出来
            lines.append(random.choice([
                f'……今天你跟我说了 {st["chats"]} 次话。',
                f'……今天你找了我 {st["chats"]} 次。我都应了。',
            ]))

        # ---- 随机小观察（1~2 件，不固定套路）----
        # 把上面已经提过的软件/网站名传进去，避免同一件事说两遍
        # （踩过：日记里「Deepseek Harness」出现两次）
        # 注意 apps 是**三元组** (name, seconds, sessions)，
        # sites 是二元组 (name, seconds) —— 别按一种解包（踩过 ValueError）。
        mentioned = {str(a[0]) for a in apps}
        mentioned |= {str(s[0]) for s in st['sites']}
        for a in self._diary_asides(d, st, k=random.choice([1, 2]),
                                   avoid=mentioned):
            lines.append(a)

        # ---- 收尾 ----
        if len(lines) <= 1:
            lines.append(random.choice([
                '……今天很安静。我数了月亮，也数了你路过的次数。',
                '……今天没发生什么。但你在，这就够了。',
                '……平淡的一天。我喜欢平淡。',
            ]))
        else:
            lines.append(random.choice([
                '……明天也在。',
                '……就这样吧。晚安。',
                '……记下来了。',
                '……这一天，我收好了。',
                '……写完了。你大概不会看，但我还是写。',
                '……好了。明天见。',
            ]))

        entry = DiaryEntry(d, lines)
        self.diary.entries[d] = entry
        self.diary.save()
        # 写入后清空当天片段缓存（已归档）
        if d == today_str():
            self._chat_snippets = []
        return entry

    # ==================== 自动写入 ====================
    def diary_tick(self, now):
        """在 _tick 里调用：
        1) 跨天时把**昨天**补写下来（程序没开一夜也没关系）
        2) 到点了把今天写下来
        """
        try:
            today = today_str()
            # ---- 补写昨天 ----
            y = (datetime.date.today() - datetime.timedelta(days=1)).isoformat()
            if not self.diary.has(y) and self._diary_should_write(y):
                self.diary_generate(y, force=True)

            # ---- 今天到点写 ----
            if self.diary.has(today):
                return
            lt = time.localtime(now)
            now_min = lt.tm_hour * 60 + lt.tm_min
            if now_min >= DIARY_WRITE_AT_MIN:
                self.diary_generate(today, force=True)
        except Exception:
            pass

    def _diary_should_write(self, date):
        """这一天值不值得写一条。

        用**当天明细**判断（原来只看周累计的 `apps`，所以只要这周用过
        电脑，任何一天都会被判定"值得写"，补写出来的日记就全是空话）。
        """
        mins, _ = self._diary_study(date)
        if mins >= 1:
            return True
        try:
            if self.activity.has_day(date):
                return True
            day = self.activity.day_data(date) or {}
            if int(day.get('chats') or 0) > 0:
                return True
        except Exception:
            pass
        return False

    # ==================== 界面：查看 / 删除 ====================
    def diary_show_panel(self):
        """右键菜单「📔 日记」：左侧日期列表，右侧正文；只能删不能改"""
        import tkinter as tk

        if self._diary_win is not None:
            try:
                self._diary_win.lift()
                self._diary_win.focus_force()
                return
            except Exception:
                self._diary_win = None

        win = tk.Toplevel(self.root)
        win.title('日记')
        win.attributes('-topmost', True)
        win.resizable(False, False)
        x = self.root.winfo_x() + WIN_W + 10
        y = self.root.winfo_y()
        sw = self.root.winfo_screenwidth()
        if x + 620 > sw:
            x = max(0, self.root.winfo_x() - 630)
        win.geometry(f'620x440+{x}+{y}')

        outer = tk.Frame(win, bg=BUBBLE_BG)
        outer.pack(fill='both', expand=True, padx=10, pady=10)

        tk.Label(outer, text='伴月纪闻', bg=BUBBLE_BG, fg='#6A4BA8',
                 font=('Microsoft YaHei', 11, 'bold')).pack(anchor='w')
        tk.Label(outer, text='今天怎么样',
                 bg=BUBBLE_BG, fg='#8A7BA8',
                 font=('Microsoft YaHei', 8)).pack(anchor='w', pady=(2, 8))

        body = tk.Frame(outer, bg=BUBBLE_BG)
        body.pack(fill='both', expand=True)

        # ---- 左：日期列表 ----
        left = tk.Frame(body, bg=BUBBLE_BG)
        left.pack(side='left', fill='y')
        sb1 = tk.Scrollbar(left)
        sb1.pack(side='right', fill='y')
        lb = tk.Listbox(left, width=16, bg='#FFFDF9', fg=BUBBLE_FG,
                        relief='flat', font=('Microsoft YaHei', 9),
                        yscrollcommand=sb1.set, activestyle='none')
        lb.pack(side='left', fill='y')
        sb1.config(command=lb.yview)

        # ---- 右：正文 ----
        right = tk.Frame(body, bg=BUBBLE_BG)
        right.pack(side='left', fill='both', expand=True, padx=(8, 0))
        txt = tk.Text(right, bg='#FFFDF9', fg=BUBBLE_FG, relief='flat',
                      font=('Microsoft YaHei', 10), wrap='word',
                      padx=10, pady=8, spacing1=3, spacing3=3)
        txt.pack(fill='both', expand=True)
        txt.tag_configure('d', foreground='#6A4BA8',
                          font=('Microsoft YaHei', 9, 'bold'))

        def refresh_list(select=None):
            lb.delete(0, 'end')
            for d in self.diary.dates():
                lb.insert('end', d)
            if select and select in self.diary.dates():
                i = self.diary.dates().index(select)
                lb.selection_clear(0, 'end')
                lb.selection_set(i)
                lb.see(i)

        def show(_evt=None):
            sel = lb.curselection()
            txt.config(state='normal')
            txt.delete('1.0', 'end')
            if not sel:
                txt.insert('end', '（左边选一天）')
                txt.config(state='disabled')
                return
            d = lb.get(sel[0])
            e = self.diary.get(d)
            if not e:
                txt.insert('end', '（这一天没有记录）')
                txt.config(state='disabled')
                return
            txt.insert('end', d + '\n', 'd')
            txt.insert('end', '\n')
            for line in e.lines:
                txt.insert('end', line + '\n')
            txt.config(state='disabled')

        lb.bind('<<ListboxSelect>>', show)

        # ---- 删除操作（先定义，右键菜单要用）----
        def delete_one(d=None):
            """删掉某一天。d 为空时用当前选中项。"""
            if d is None:
                sel = lb.curselection()
                if not sel:
                    return
                d = lb.get(sel[0])
            if not self.diary.has(d):
                return
            # 删之前确认 —— 这是**不可撤销**的操作，而且用户曾误删过
            from tkinter import messagebox
            if not messagebox.askyesno(
                    '删除这一天',
                    f'删掉 {d} 的那一页日记？\n这个操作不能撤销。',
                    parent=win):
                return
            if self.diary.remove(d):
                self._speak(f'……{d} 的那一页，我撕掉了。')
                refresh_list()
                txt.config(state='normal')
                txt.delete('1.0', 'end')
                txt.insert('end', '（已删除）')
                txt.config(state='disabled')

        def delete_all():
            from tkinter import messagebox
            n = self.diary.count()
            if n == 0:
                return
            if not messagebox.askyesno('清空日记',
                                       f'真的要删掉全部 {n} 篇日记吗？\n'
                                       '这个操作不能撤销。', parent=win):
                return
            gone = self.diary.clear_all()
            self._speak(f'……{gone} 篇。都忘掉了。')
            refresh_list()
            txt.config(state='normal')
            txt.delete('1.0', 'end')
            txt.insert('end', '（已清空）')
            txt.config(state='disabled')

        def tag_next_menu(menu):
            """把子菜单也一起套上字体/配色（tk 的 Menu 不继承父级样式）"""
            menu.config(font=('Microsoft YaHei', 9),
                        bg='#FFFDF9', fg=BUBBLE_FG,
                        activebackground='#E8E0F0',
                        activeforeground='#6A4BA8',
                        relief='flat', borderwidth=0)
            return menu

        # ---- 右键日期 → 删除菜单 ----
        # 用户反馈："看不见日记的删除在哪"。
        # 原来删除是窗口底部的两个小按钮，不显眼；改成在**日期上右键**，
        # 并加一条常驻提示文字（功能在但看不见 = 等于没有）。
        def on_right_click(evt):
            i = lb.nearest(evt.y)          # 离点击位置最近的行
            if i is None or i < 0 or i >= lb.size():
                return
            d = lb.get(i)
            # 右键先把这一行选中，让用户看清要删的是哪天
            lb.selection_clear(0, 'end')
            lb.selection_set(i)
            lb.see(i)
            show()

            menu = tag_next_menu(tk.Menu(win, tearoff=0))
            menu.add_command(
                label=f'🗑  删除「{d}」',
                command=lambda dd=d: delete_one(dd))
            menu.add_separator()
            menu.add_command(
                label=f'清空全部（{self.diary.count()} 篇）',
                command=delete_all)
            try:
                menu.tk_popup(evt.x_root, evt.y_root)
            finally:
                menu.grab_release()

        # Windows 右键是 Button-3；Button-2 兼容其它平台的三键鼠标
        lb.bind('<Button-3>', on_right_click)
        lb.bind('<Button-2>', on_right_click)

        # 双击日期也直接问要不要删（更符合"点它就是想删"的直觉）
        lb.bind('<Double-Button-1>',
                lambda _e: delete_one())

        # ---- 底部：常驻提示 + 关闭 ----
        btns = tk.Frame(outer, bg=BUBBLE_BG)
        btns.pack(fill='x', pady=(8, 0))
        tk.Label(btns,
                 text='💡 在某一天上右键即可删除',
                 bg=BUBBLE_BG, fg='#8A7BA8',
                 font=('Microsoft YaHei', 8)).pack(side='left', padx=2)

        def close():
            try:
                win.destroy()
            except Exception:
                pass
            self._diary_win = None

        tk.Button(btns, text='关闭', font=('Microsoft YaHei', 9),
                  bg='#6A4BA8', fg='white', relief='flat',
                  cursor='hand2', padx=12, pady=6,
                  command=close).pack(side='right', padx=3)

        win.protocol('WM_DELETE_WINDOW', close)
        refresh_list()
        # 默认选中最新一篇
        if self.diary.dates():
            lb.selection_set(0)
            show()
        self._diary_win = win


# ==================== 小工具 ====================
def _weather_word(w):
    """把天气描述压成一个词，用于'天气X'句式"""
    d = (w.desc or '').strip()
    return d if d else '未知'


def _fmt_min(minutes):
    """分钟 -> 人话时长"""
    m = int(round(float(minutes)))
    if m >= 60:
        h, r = divmod(m, 60)
        return f'{h} 小时' + (f' {r} 分' if r else '')
    return f'{m} 分钟'


def _strip_end(t):
    """去掉句末标点，供「」引号引用台词时用。

    台词本身可能已带「？」「。」，直接套「{t}」。会变成
    「想听我唱歌吗？」。 —— 多一个句号（踩过）。
    """
    s = (t or '').strip()
    return s.rstrip('。！？…，、；：')
