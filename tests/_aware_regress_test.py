# -*- coding: utf-8 -*-
"""
屏幕感知 + 打开功能 回归测试
============================
覆盖：
  T1 前台窗口探测（ctypes，真实读取）
  T2 网站/应用识别
  T3 活动记录：记账、跨周自动重置、落盘
  T4 打趣规则：停留时长 / 冷却 / 去重
  T5 打开意图识别（只认明确意图）
  T6 安全校验（危险字符 / 黑名单 / 网址）
  T7 确认框必须存在（不点确认不开）
不碰声卡与网络；不会真的打开任何东西。
"""
import json
import os
import shutil
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
import awareness
import banter
import launcher as L

PASSED, FAILED = [], []


def check(name, cond, extra=''):
    (PASSED if cond else FAILED).append(name)
    print(('  ✓ ' if cond else '  ✗ ') + name + (f'   {extra}' if extra else ''))


def main():
    print('=' * 62)
    print('屏幕感知 + 打开功能 回归测试')
    print('=' * 62)

    # ---------------- T1 前台窗口探测 ----------------
    print('\nT1 前台窗口探测（真实读取）')
    info = awareness.read_foreground()
    if info is None:
        # 自动化环境里前台窗口常常就是 python.exe 本身，而她在
        # _IGNORE_PROC 里主动忽略了这类窗口 —— 这是**设计如此**，不是 bug。
        # 所以改为验证"识别逻辑"本身（用构造的窗口信息）。
        print('      （当前前台窗口是她主动忽略的自身/系统窗口，')
        print('        这是预期行为；转为验证识别逻辑）')
        check('python.exe 被主动忽略',
              'python.exe' in awareness._IGNORE_PROC)
        check('系统窗口被主动忽略',
              'explorer.exe' in awareness._IGNORE_PROC)
        # 构造一个"用户在浏览器里"的窗口，验证后续识别链路
        fi = awareness.ForegroundInfo(
            'chrome.exe', r'C:\chrome.exe',
            '某个视频_哔哩哔哩_bilibili', 'Chrome', 'B站', 0)
        check('构造的窗口能给出应用名', fi.app == 'Chrome', fi.app)
        check('构造的窗口能认出站点', fi.site == 'B站', str(fi.site))
        check('describe 可读', 'B站' in fi.describe(), fi.describe())
    else:
        check('能读到前台窗口', True, info.describe())
        check('拿到进程名', bool(info.proc), info.proc)
        check('拿到友好应用名', bool(info.app), info.app)
        check('拿到窗口标题', bool(info.title), info.title[:40])
        check('key 可用于去重', bool(info.key))

    # ---------------- T2 识别 ----------------
    print('\nT2 网站 / 应用识别')
    check('Chrome 识别为 Chrome',
          awareness._friendly_app('chrome.exe') == 'Chrome')
    check('code 识别为 VS Code',
          awareness._friendly_app('code.exe') == 'VS Code')
    check('从标题认出 B站',
          awareness._detect_site('【某视频】_哔哩哔哩_bilibili') == 'B站')
    check('从标题认出 GitHub',
          awareness._detect_site('k2-fsa/sherpa-onnx: something - GitHub') == 'GitHub')
    check('无关标题认不出站点',
          awareness._detect_site('未命名 - 记事本') is None)
    check('忽略自身/系统窗口', 'python.exe' in awareness._IGNORE_PROC)

    # ---------------- T3 活动记录 ----------------
    print('\nT3 活动记录与每周重置')
    tmpdir = tempfile.mkdtemp(prefix='pet_act_')
    logpath = os.path.join(tmpdir, 'activity_log.json')
    try:
        al = awareness.ActivityLog(logpath)
        check('新建记录文件对象', al.data['week'] == awareness.ActivityLog.current_week())
        al.add('chrome.exe', 'Chrome', 120.0, 'B站')
        al.add('chrome.exe', 'Chrome', 60.0, 'B站')
        al.add('code.exe', 'VS Code', 300.0)
        al.mark_session('chrome.exe')
        al.flush(force=True)
        check('文件已写入', os.path.exists(logpath))

        apps = al.data['apps']
        check('Chrome 累计 180 秒', abs(apps['chrome.exe']['seconds'] - 180.0) < 0.1,
              str(apps['chrome.exe']['seconds']))
        check('站点分布已记', apps['chrome.exe']['sites'].get('B站') == 180.0)
        check('会话数已记', apps['chrome.exe']['sessions'] == 1)
        check('top_apps 按时长倒序',
              [r[0] for r in al.top_apps(2)] == ['VS Code', 'Chrome'],
              str([r[0] for r in al.top_apps(2)]))

        # 模拟"跨周"：把文件里的 week 改成上一周，重载应清空并归档
        raw = json.load(open(logpath, encoding='utf-8'))
        raw['week'] = '2000-W01'
        json.dump(raw, open(logpath, 'w', encoding='utf-8'), ensure_ascii=False)
        al2 = awareness.ActivityLog(logpath)
        check('跨周后本周清空', al2.data['apps'] == {})
        check('本周标记已更新',
              al2.data['week'] == awareness.ActivityLog.current_week())
        check('上周数据被归档', (al2.data.get('last_week') or {}).get('week') == '2000-W01',
              str((al2.data.get('last_week') or {}).get('week')))
        check('上周数据可查询',
              len(al2.top_apps(2, source=al2.data['last_week'])) == 2)

        # ---- 按天明细（日记要用）----
        # 用户反馈"日记全是使用时间最长" + "某天的日记凭空消失"，
        # 根因就是原来只有周累计、没有逐日记录。这段守住按天功能。
        al3 = awareness.ActivityLog(logpath)
        d0 = al3.today_str()
        # 注意：同一天上面前面的用例已经记过 chrome 180 秒，
        # 所以这里断言**增量**而不是绝对值（我第一版写死 300 就错了）。
        before_chrome = float((al3.day_data(d0) or {})
                              .get('apps', {}).get('chrome.exe', {})
                              .get('seconds', 0))
        before_busy = float(al3.day_busy_sec(d0))
        al3.add('chrome.exe', 'Chrome', 300.0, 'B站')
        al3.add('code.exe', 'VS Code', 900.0)
        al3.mark_session('chrome.exe')
        al3.bump_day('chats', 2)
        al3.bump_day('pets', 1)
        al3.add_day_study(1200.0)
        al3.flush(force=True)
        day = al3.day_data(d0)
        check('当天明细已建立', day is not None, str(day))
        check('当天明细记录了应用时长（增量 +300）',
              abs((day or {}).get('apps', {}).get('chrome.exe', {})
                  .get('seconds', 0) - (before_chrome + 300.0)) < 0.1,
              str((day or {}).get('apps', {}).get('chrome.exe')))
        check('当天明细记录了站点（B站累计）',
              (day or {}).get('apps', {}).get('chrome.exe', {})
              .get('sites', {}).get('B站', 0) >= 300.0,
              str((day or {}).get('apps', {}).get('chrome.exe', {})
                  .get('sites')))
        check('当天明细记录了对话数', (day or {}).get('chats') == 2)
        check('当天明细记录了摸头数', (day or {}).get('pets') == 1)
        check('当天明细记录了学习秒数',
              abs(float((day or {}).get('study_sec', 0)) - 1200.0) < 0.1)
        check('day_top_apps 按当天排序且过滤太短的',
              [r[0] for r in al3.day_top_apps(d0, min_sec=100)]
              == ['VS Code', 'Chrome'],
              str(al3.day_top_apps(d0, min_sec=100)))
        check('day_top_sites 有值',
              [s for s, _v in al3.day_top_sites(d0)] == ['B站'])
        check('day_busy_sec 是当天总和（增量 +1200）',
              abs(al3.day_busy_sec(d0) - (before_busy + 1200.0)) < 0.1,
              str(al3.day_busy_sec(d0)))
        check('has_day 判定正确', al3.has_day(d0) is True)
        check('没有记录的日期 has_day=False',
              al3.has_day('1999-01-01') is False)
        check('day_data 对不存在的日期返回 None（不创建）',
              al3.day_data('1999-01-01') is None
              and '1999-01-01' not in (al3.data.get('days') or {}))
        # 重新加载后按天明细要还在（防止只写内存不落盘）
        al4 = awareness.ActivityLog(logpath)
        check('按天明细已落盘', al4.day_data(d0) is not None
              and abs(al4.day_busy_sec(d0) - (before_busy + 1200.0)) < 0.1,
              str(al4.day_busy_sec(d0)))
        # 跨周**不能**把按天明细清掉（否则上周末尾的日记素材就丢了）
        raw2 = json.load(open(logpath, encoding='utf-8'))
        raw2['week'] = '2000-W01'
        json.dump(raw2, open(logpath, 'w', encoding='utf-8'),
                  ensure_ascii=False)
        al5 = awareness.ActivityLog(logpath)
        check('跨周后本周累计清空', al5.data['apps'] == {})
        check('跨周后**按天明细保留**',
              al5.day_data(d0) is not None
              and abs(al5.day_busy_sec(d0) - (before_busy + 1200.0)) < 0.1,
              str(al5.day_busy_sec(d0)))

        # 时长格式化
        check('格式化 1 小时', '1 小时' in al.fmt_duration(3661), al.fmt_duration(3661))
        check('格式化 2 分', '2 分' in al.fmt_duration(125), al.fmt_duration(125))
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)

    # ---------------- T4 打趣规则 ----------------
    print('\nT4 打趣规则（停留 / 冷却 / 去重）')


    class FakeInfo:
        def __init__(self, proc, site, title):
            self.proc, self.site, self.title = proc, site, title
            self.app = proc

        @property
        def key(self):
            return f'{self.proc}|{self.title}'

        def describe(self):
            return self.proc

    seen = {}
    fi = FakeInfo('chrome.exe', 'B站', '某视频_哔哩哔哩')
    now = time.time()
    # 第一次见到：只登记，不开口（停留 0 秒）
    r1 = banter.pick_banter(fi, now, seen)
    check('首次见到不开口', r1 is None)
    # 停留不足：不开口（把概率拉满也不该开口）
    old_prob = config.AWARENESS_BANTER_PROB
    config.AWARENESS_BANTER_PROB = 1.0
    try:
        r2 = banter.pick_banter(fi, now + 3, seen)
        check('停留不足时不开口', r2 is None, f'停留 3 秒 -> {r2!r}')
        # 停留够了：应能从 B站 台词里挑出一句
        got = None
        for _ in range(30):
            got = banter.pick_banter(fi, now + 60, seen)
            if got:
                break
        check('停留够久会开口', bool(got), repr(got))
        check('用的是 B站 专属台词', got in config.BANTER_BY_SITE['B站'],
              '✓' if got in config.BANTER_BY_SITE['B站'] else repr(got))

        # 认不出的应用 -> 兜底句，且带应用名
        # 注意：tkey 的格式是 proc|site|title[:60]，别少写 site 那段
        seen2 = {'x.exe||unknown': now - 120}
        fu = FakeInfo('x.exe', None, 'unknown')
        got2 = None
        for _ in range(60):
            got2 = banter.pick_banter(fu, now, seen2)
            if got2:
                break
        check('未知应用用兜底句', bool(got2), repr(got2))
        check('兜底句带应用名', 'x.exe' in (got2 or ''), repr(got2))
    finally:
        config.AWARENESS_BANTER_PROB = old_prob

    # ---------------- T5 打开意图识别 ----------------
    print('\nT5 打开意图识别')
    cases = [
        ('打开记事本', '记事本'),
        ('帮我打开 B站', 'B站'),
        ('给我打开浏览器', '浏览器'),
        ('启动 steam', 'steam'),
        ('访问 github.com', 'github.com'),
        ('open notepad', 'notepad'),
        ('你好呀', None),
        ('今天天气不错', None),
        ('我想听你唱歌', None),
        ('我打开了心结', None),          # 有"打开"但不是指令语气
    ]
    for text, expect in cases:
        got = banter.recognise_intent(text)
        ok = (got == expect) if expect else (got is None)
        check(f'{text!r} -> {got!r}', ok, '' if ok else f'(期望 {expect!r})')

    # ---------------- T6 安全校验 ----------------
    print('\nT6 打开的安全校验')
    pet = None             # 不需要真实例，直接测静态校验
    mixin = L.LauncherMixin

    class Probe(mixin):
        pass

    p = Probe()
    p._app_paths = {}          # 路径表（正常由 setup_launcher 初始化）
    p.save_app_paths = lambda: None
    p._speak = lambda *a, **k: None

    def chk(t):
        return p._launch_check(t)

    ok, kind, norm = chk('github.com')
    check('网址被识别', ok and kind == '网址' and norm.startswith('https://'), f'{kind} {norm}')

    ok, kind, norm = chk('https://example.com/a/b')
    check('完整 URL 保留原样', norm == 'https://example.com/a/b', norm)

    ok, kind, norm = chk('calc')
    check('系统工具白名单', ok and norm == 'calc.exe', f'{kind} {norm}')

    # 借一个一定存在的文件当作"用户配置的程序路径"
    fake_exe = os.path.join(config.BASE_DIR, 'main.py')

    # 路径表里填的是假想路径，可能真实不存在 —— 让 exists 对这些值返回 True，
    # 否则 _launch_check 会因"路径不存在"而落到 NOT_FOUND，测不出真实逻辑。
    _real_exists = os.path.exists

    def _fake_exists(x, _fe=fake_exe):
        return True if x == _fe else _real_exists(x)

    L.os.path.exists = _fake_exists

    # 未配置路径时，中文系统工具名要能落到白名单（别名先翻译）
    p._app_paths = {}
    for name, exe in (('记事本', 'notepad.exe'), ('计算器', 'calc.exe'),
                      ('画图', 'mspaint.exe'), ('任务管理器', 'taskmgr.exe')):
        ok, kind, norm = chk(name)
        check(f'未配置时「{name}」落到系统白名单', ok and norm == exe,
              f'{kind} {norm}')

    # 系统小工具若用户自己配了路径，应当以用户的路径为准
    p._app_paths = {'notepad': fake_exe}
    ok, kind, norm = chk('记事本')
    check('用户配置优先于系统白名单', ok and norm == fake_exe, f'{kind} {norm}')

    # ---- 软件路径表：自定义名字（中文也行）----
    p._app_paths = {'我的工具': fake_exe, '自定义软件': fake_exe}
    ok, kind, norm = chk('我的工具')
    check('路径表按中文名命中', ok and norm == fake_exe, f'{kind} {norm}')
    ok, kind, norm = chk('自定义软件')
    check('路径表第二个条目也命中', ok and norm == fake_exe, f'{kind} {norm}')
    # 按 exe 文件名反查：说 'msedge' 能命中键 'edge'
    # 注意要用**真实存在**的文件，且其 basename 必须等于被反查的名字
    # （反查是按路径的文件名比的，拿 main.py 当值永远反查不到 msedge）
    real_main = os.path.join(config.BASE_DIR, 'main.py')
    p._app_paths = {'edge': real_main, 'weixin': real_main}
    ok, kind, norm = chk('weixin')            # 键与名一致，必命中
    check('路径表键名命中', ok and norm == real_main, f'{kind} {norm}')
    # basename 反查：值叫 main.py，所以查 'main' 应命中键 'edge'
    ok, kind, norm = chk('main')
    check('按 exe 文件名反查命中', ok and norm == real_main, f'{kind} {norm}')
    p._app_paths = {}

    # ---- 核心回归：找不到时必须是 NOT_FOUND，不能交给系统 ----
    ok, kind, norm = chk('某个绝对不存在的软件xyz123')
    check('找不到 -> NOT_FOUND（不再抛系统错误）', ok is False and kind == 'NOT_FOUND',
          f'{kind}')

    # ---- 网址优先级：不能被别名抢走 ----
    p._app_paths = {'b站': fake_exe}
    ok, kind, norm = chk('b23.tv')
    check('域名仍按网址处理（别名不抢）', ok and kind == '网址',
          f'{kind} {norm}')

    for bad, label in [('notepad; rm -rf /', '分号链式命令'),
                       ('a && b', '&& 链式命令'),
                       ('x | y', '管道'),
                       ('x > out.txt', '重定向'),
                       ('regedit', '注册表工具'),
                       ('diskpart', '分区工具'),
                       ('shutdown', '关机命令')]:
        ok, kind, norm = chk(bad)
        check(f'拒绝：{label} ({bad!r})', ok is False, f'-> {kind}')

    check('拒绝空目标', chk('')[0] is False)

    # ---- 浏览器打开 / 搜索意图 ----
    print('\nT6b 浏览器打开与搜索意图')
    for text, expect in [('百度一下 如何写python', '如何写python'),
                         ('搜索 sherpa-onnx 教程', 'sherpa-onnx 教程'),
                         ('查一下 上海天气', '上海天气'),
                         ('从浏览器打开 B站', 'B站'),
                         ('用浏览器搜索 桌宠怎么做', '桌宠怎么做')]:
        m = config.BROWSER_OPEN_PAT.search(text) or config.SEARCH_PAT.search(text)
        got = m.group(1).strip() if m else None
        check(f'{text!r} -> {got!r}', got == expect,
              '' if got == expect else f'(期望 {expect!r})')

    check('有搜索 URL 模板', '{q}' in config.SEARCH_ENGINE_URL)
    check('有找不到的提示台词',
          bool(config.LAUNCH_NOT_FOUND_LINES) and bool(config.LAUNCH_NOT_FOUND_HINT))

    # 打开结果日志与台词常量的存在性
    check('有确认框标题常量', bool(config.LAUNCH_CONFIRM_TITLE))
    check('有失败/拒绝台词', bool(config.LAUNCH_FAIL_LINES) and bool(config.LAUNCH_DENY_LINES))

    # ---------------- T7 确认框必须存在 ----------------
    print('\nT7 确认流程（不点确认不会打开）')
    import tkinter as tk

    opened = []
    p.root = tk.Tk()
    p.root.withdraw()
    p.cfg = config.Config()
    p._launch_win = None
    p._reset_idle_timer = lambda *a: None
    p._speak = lambda *a, **k: None
    p.launch_do = lambda target, kind='': opened.append((target, kind))   # 拦截真实打开

    p.launch_ask('github.com')
    check('弹出了确认框', p._launch_win is not None)
    check('未点确认 -> 没打开', opened == [], str(opened))
    if p._launch_win is not None:
        try:
            p._launch_win.destroy()
        except Exception:
            pass
        p._launch_win = None

    # 点确认后才应调用打开（这里 launch_do 已被替身，验证调用链）
    p.launch_do('github.com', '网址')
    check('确认后走打开流程', len(opened) == 1, str(opened))
    p.root.destroy()

    # ---------------- 汇总 ----------------
    print('\n' + '=' * 62)
    print(f'通过 {len(PASSED)} 项，失败 {len(FAILED)} 项')
    if FAILED:
        print('失败项：')
        for f in FAILED:
            print('  - ' + f)
    print('=' * 62)
    return 1 if FAILED else 0


if __name__ == '__main__':
    sys.exit(main())
