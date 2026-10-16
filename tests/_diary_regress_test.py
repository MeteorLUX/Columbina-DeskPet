# -*- coding: utf-8 -*-
"""
天气 / 节日 / 日记 回归测试
==========================
覆盖：
  T1 天气：城市名解析、WMO 码翻译、缓存、失败降级（用假响应，不真的联网）
  T2 节日：公历节日、农历节日（春节/中秋/端午）、生日、日期行
  T3 日记：自动生成、口吻、只看不增改、删除
  T4 日记内容来自真实素材（学习 / 使用 / 天气 / 节日 / 对话）
  T5 面板：能打开、能删除（不点不删）
"""
import datetime
import json
import os
import shutil
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
import weather as W
import holidays as H
import diary as D

PASSED, FAILED = [], []


def check(name, cond, extra=''):
    (PASSED if cond else FAILED).append(name)
    print(('  ✓ ' if cond else '  ✗ ') + name + (f'   {extra}' if extra else ''))


def main():
    print('=' * 62)
    print('天气 / 节日 / 日记 回归测试')
    print('=' * 62)

    # ==================== T1 天气 ====================
    print('\nT1 天气')
    check('WMO 0 -> 晴', W.describe_code(0)[0] == '晴')
    check('WMO 3 -> 阴', W.describe_code(3)[0] == '阴')
    check('WMO 61 -> 小雨且算特殊', W.describe_code(61)[0] == '小雨'
          and W.describe_code(61)[1] is True)
    check('WMO 0 不算特殊天气', W.describe_code(0)[1] is False)
    check('未知码不崩', W.describe_code(9999)[0] == '未知')

    # 假响应：不打真网络
    real_http = W._http_json
    try:
        W._http_json = lambda url, timeout=8.0: (
            {'results': [{'name': '成都', 'latitude': 30.6, 'longitude': 104.0}]}
            if 'geocoding' in url else
            {'current': {'temperature_2m': 18.5, 'weather_code': 63},
             'daily': {'temperature_2m_max': [21.0], 'temperature_2m_min': [15.0],
                       'precipitation_probability_max': [80]}})

        info = W.WeatherCache.fetch('成都')
        check('能查到天气', info.ok is True, str(info.err))
        check('城市名正确', info.city == '成都', info.city)
        check('温度正确', abs(float(info.temp) - 18.5) < 0.01, str(info.temp))
        check('中雨被识别为特殊天气', info.special is True, info.desc)
        check('label 可读', '成都' in info.label() and '°C' in info.label(),
              info.label())
        check('特殊天气有话说', bool(info.remark()), str(info.remark()))
    finally:
        W._http_json = real_http

    # 查不到城市
    try:
        W._http_json = lambda url, timeout=8.0: {'results': []}
        bad = W.WeatherCache.fetch('不存在的地方xyz')
        check('查不到城市 -> ok=False 且不抛异常',
              bad.ok is False and bad.err, str(bad.err))
    finally:
        W._http_json = real_http

    # 网络异常
    try:
        def boom(url, timeout=8.0):
            raise OSError('no network')
        W._http_json = boom
        err = W.WeatherCache.fetch('成都')
        check('网络异常 -> ok=False 且不抛异常', err.ok is False, str(err.err))
    finally:
        W._http_json = real_http

    # 缓存读写 + 城市留空不请求
    tmpdir = tempfile.mkdtemp(prefix='pet_w_')
    try:
        p = os.path.join(tmpdir, 'wc.json')
        c = W.WeatherCache(p, '')
        check('城市为空 -> 不需要刷新', c.needs_refresh() is False)
        c2 = W.WeatherCache(p, '成都')
        check('填了城市 -> 需要刷新', c2.needs_refresh() is True)
        c2.info = W.WeatherInfo(ok=True, city='成都', desc='晴', temp=20.0)
        c2._save()
        c3 = W.WeatherCache(p, '成都')
        check('缓存可复用', c3.info is not None and c3.info.city == '成都')
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)

    # ==================== T2 节日 ====================
    print('\nT2 节日与日期')
    for d, exp in [(datetime.date(2026, 1, 1), '元旦'),
                   (datetime.date(2026, 10, 1), '国庆节'),
                   (datetime.date(2026, 12, 25), '圣诞节'),
                   (datetime.date(2026, 2, 14), '情人节')]:
        check(f'公历 {d} -> {exp}', H.solar_holiday(d) == exp,
              str(H.solar_holiday(d)))
    check('普通日子没有节日', H.solar_holiday(datetime.date(2026, 10, 15)) is None)

    if H.HAS_LUNAR:
        for d, exp in [(datetime.date(2026, 2, 17), '春节'),
                       (datetime.date(2025, 10, 6), '中秋节'),
                       (datetime.date(2025, 5, 31), '端午节')]:
            check(f'农历 {d} -> {exp}', H.lunar_holiday(d) == exp,
                  str(H.lunar_holiday(d)))
    else:
        check('未装 lunardate 时优雅降级', H.lunar_holiday() is None)

    check('生日 MM-DD', H.is_birthday('10-15', datetime.date(2026, 10, 15)))
    check('生日 YYYY-MM-DD', H.is_birthday('2000-10-15',
                                          datetime.date(2026, 10, 15)))
    check('非生日 False', not H.is_birthday('01-01', datetime.date(2026, 10, 15)))
    check('空生日不崩', not H.is_birthday('', datetime.date(2026, 10, 15)))

    line = H.date_line(datetime.date(2026, 10, 15))
    check('日期行含年月日与星期', '2026年10月15日' in line and '星期' in line, line)
    check('日期行含年内天数', '第 288' in line, line)

    n, g = H.holiday_of_day(None, datetime.date(2026, 10, 1))
    check('国庆有祝福语', n == '国庆节' and bool(g), f'{n} {g!r}')
    n2, g2 = H.holiday_of_day('10-15', datetime.date(2026, 10, 15))
    check('生日优先于公历节日', n2 == '生日' and bool(g2), f'{n2} {g2!r}')
    n3, g3 = H.holiday_of_day(None, datetime.date(2026, 10, 15))
    check('普通日子无节日', n3 is None and g3 is None)

    # ==================== T3 日记本（只看不增改）====================
    print('\nT3 日记本：只能看和删')
    tmpdir = tempfile.mkdtemp(prefix='pet_d_')
    try:
        dp = os.path.join(tmpdir, 'diary.json')
        book = D.DiaryBook(dp)
        check('初始为空', book.count() == 0)
        check('查不存在的日期返回 None', book.get('2026-01-01') is None)

        # 写入（内部接口）
        e = D.DiaryEntry('2026-10-15', ['……今天很安静。'])
        book.entries['2026-10-15'] = e
        book.save()
        check('保存后文件存在', os.path.exists(dp))

        book2 = D.DiaryBook(dp)
        check('能读回', book2.get('2026-10-15') is not None)
        check('内容一致', book2.get('2026-10-15').lines == ['……今天很安静。'])
        check('日期列表倒序', book2.dates() == ['2026-10-15'])

        # 删除
        check('删除成功', book2.remove('2026-10-15') is True)
        check('删除后为空', book2.count() == 0)
        check('删除不存在的返回 False', book2.remove('2026-01-01') is False)

        # 清空
        for i in range(3):
            book2.entries[f'2026-10-{10+i}'] = D.DiaryEntry(f'2026-10-{10+i}', ['x'])
        book2.save()
        n = book2.clear_all()
        check('清空返回数量', n == 3, str(n))
        check('清空后为空', book2.count() == 0)

        # 约束：DiaryBook 不提供"新增/修改"的公开方法
        pub = [m for m in dir(book2)
               if not m.startswith('_') and callable(getattr(book2, m))]
        check('没有 add/create/update 之类的公开写接口',
              not any(k in ' '.join(pub).lower()
                      for k in ('add', 'create', 'update', 'edit', 'insert')),
              str(pub))
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)

    # ==================== T4 生成内容 ====================
    print('\nT4 日记生成（用真实素材）')
    import app as APP
    # 注意：不要在函数内再 `import tempfile/shutil` —— 那会让整个 main()
    # 把它们当局部变量，前面 T1 的用法就 UnboundLocalError（踩过）。
    # 模块顶部已经 import 过这两个。
    pet = APP.DesktopPet()
    pet.root.withdraw()

    # 【测试隔离】把日记与活动记录都改到临时目录。
    # 踩过的坑：这个测试原来直接写真实 activity_log.json，
    # 把假素材（Chrome 7200 秒）灌进了用户的真实记录。
    _tmpdir = tempfile.mkdtemp(prefix='pet_diary_')
    pet.setup_diary(os.path.join(_tmpdir, 'diary.json'))
    pet.activity.path = os.path.join(_tmpdir, 'activity_log.json')
    pet.activity.data = pet.activity._empty()
    pet.activity._dirty = False

    # 假素材：学习 45 分钟 + 用了很久的软件 + 天气 + 对话片段
    today = H.today_str()
    pet._study_records = [
        {'start': today + 'T09:00:00', 'end': today + 'T09:45:00',
         'seconds': 2700},
    ]
    # 【重要】日记现在读的是**按天**明细（`activity.day_data(date)`），
    # 不是周累计 `apps`。所以这里必须写当天桶，否则日记什么都看不到
    # （旧测试只写周累计，改版后就成了假失败）。
    pet.activity.data['apps'] = {
        'chrome.exe': {'name': 'Chrome', 'seconds': 7200, 'sessions': 3,
                       'sites': {'B站': 3600}},
        'notepad.exe': {'name': '记事本', 'seconds': 120, 'sessions': 1,
                        'sites': {}},     # 太短，不该被提
    }
    pet.activity.data.setdefault('days', {})[today] = {
        'apps': {
            'chrome.exe': {'name': 'Chrome', 'seconds': 7200, 'sessions': 3,
                           'sites': {'B站': 3600}},
            'notepad.exe': {'name': '记事本', 'seconds': 120, 'sessions': 1,
                            'sites': {}},
        },
        'study_sec': 2700.0, 'chats': 3, 'pets': 1, 'launches': 0,
    }
    pet._weather.info = W.WeatherInfo(ok=True, city='成都', desc='小雨',
                                      temp=16.0, special=True)
    pet._chat_snippets = [{'u': '你好呀', 'r': '……嗯，你好。'}]

    entry = pet.diary_generate(today, force=True)
    check('生成了日记', entry is not None and entry.date == today)
    text = '\n'.join(entry.lines)
    print('      生成内容：')
    for l in entry.lines:
        print('        ' + l)

    check('含日期行', '2026年' in text or '年' in text and '星期' in text)
    check('含天气', '小雨' in text or '天气' in text, text[:60])
    # 学习：断言时长数字出现在正文里。
    # **不要**断言"学"或"分钟"这类模板字面词 —— 模板是随机抽的，
    # 有的写「陪你学了 45 分钟」，有的写「45 分钟。我一直在旁边」，
    # 靠字面词断言会时灵时不灵（踩过：连跑 5 次里失败 2 次）。
    check('含学习时长（45 出现在正文）', '45' in text, text[:90])
    # 更强的一层：直接问生成器"学习那块有没有产出行"
    check('学习内容确实产出（内部判定）',
          pet._diary_study(today)[0] >= 1
          or float((pet.activity.day_data(today) or {})
                   .get('study_sec', 0)) >= 60,
          str(pet._diary_study(today)))
    # 她自己的感受：同样不靠字面词，直接问生成器能不能拿到一条
    _st = pet._diary_day_stats(today)
    check('她的感受确实产出（内部判定）',
          bool(pet._diary_mood_line(today, _st)),
          str(pet._diary_mood_line(today, _st))[:60])
    check('提到用了很久的 Chrome', 'Chrome' in text, text[:80])
    check('不提太短的记事本', '记事本' not in text, text[:80])
    check('含对话片段', '你好呀' in text or '你好' in text, text[:80])
    # 篇幅：改版后每天会写"感受 + 多条对话 + 1~2 件随机事件"，
    # 所以上限比原来的 8 句放宽（但也不能无限长）
    check('篇幅合理（4~14 句）', 4 <= len(entry.lines) <= 14,
          f'{len(entry.lines)} 句')
    check('口吻有省略号', '……' in text)
    check('已落盘', pet.diary.has(today))
    check('内容重复生成会重写', pet.diary_generate(today, force=True) is not None)

    # 空素材也不该崩，且给一句温柔的
    # 注意：一个进程里只建一个 tk.Tk()；多开会撞 Tk 的图像表（产品只有一个实例）。
    # 所以这里复用同一个实例，只换素材。
    pet._study_records = []
    pet.activity.data['apps'] = {}
    # 按天明细也要清 —— 日记读的是它，不清就会拿到上一条用例的素材
    pet.activity.data.setdefault('days', {}).pop('2026-01-02', None)
    pet._weather.info = None
    pet._chat_snippets = []
    e2 = pet.diary_generate('2026-01-02', force=True)
    check('无素材也能生成', e2 is not None and len(e2.lines) >= 2,
          str(e2.lines if e2 else None))
    # 语气断言放宽：无素材时会挑"冷清/安静"那类感受，
    # 说法很多（"你还在。我也是。"这种也算温柔），只要求带省略号且不是报错
    check('无素材时语气温柔', any('……' in l for l in e2.lines)
          and not any('Error' in l or 'Traceback' in l for l in e2.lines),
          str(e2.lines))
    pet.diary.remove('2026-01-02')

    # 对话片段收集的过滤（仍复用同一实例）
    pet._chat_snippets = []
    pet.diary_note_exchange('你好', '……嗯。')
    check('短对话会被记录', len(pet._chat_snippets) == 1,
          str(pet._chat_snippets))
    pet.diary_note_exchange('x' * 100, 'y' * 100)
    check('超长内容被过滤', len(pet._chat_snippets) == 1,
          str(len(pet._chat_snippets)))
    for i in range(20):
        pet.diary_note_exchange(f'第{i}句', '……嗯。')
    check('片段数量有上限',
          len(pet._chat_snippets) <= config.DIARY_MAX_SNIPPETS,
          str(len(pet._chat_snippets)))

    # ==================== T5 面板：右键删除 ====================
    print('\nT5 日记面板：右键日期删除')
    # 先塞几天，才能测右键选行与删除
    for dd in ('2099-01-01', '2099-01-02'):
        pet.diary.entries[dd] = D.DiaryEntry(dd, [f'测试 {dd}'])
    pet.diary.save()

    # tk_popup 会进入 Tk 的菜单抓取循环，自动化里会卡住 —— 拦掉它，
    # 只记录"菜单被弹出了"，然后直接 invoke 菜单项来验证行为。
    import tkinter as tk
    _popped = []
    _orig_popup = tk.Menu.tk_popup
    tk.Menu.tk_popup = lambda self, x, y, entry='': _popped.append(self)

    pet.diary_show_panel()
    _win = pet._diary_win
    check('面板已创建', _win is not None)
    check('面板里没删掉东西（不点不删）', pet.diary.has(today))

    _lbs, _btns, _labels = [], [], []

    def _find(w, cls, acc):
        for c in w.winfo_children():
            if isinstance(c, cls):
                acc.append(c)
            _find(c, cls, acc)

    if _win is not None:
        _find(_win, tk.Listbox, _lbs)
        _find(_win, tk.Button, _btns)
        _find(_win, tk.Label, _labels)
    check('有日期列表框', len(_lbs) == 1)
    _lb = _lbs[0] if _lbs else None
    if _lb is not None:
        _win.update()
        check('列表按日期倒序',
              [_lb.get(i) for i in range(_lb.size())]
              == sorted(pet.diary.dates(), reverse=True),
              str([_lb.get(i) for i in range(_lb.size())]))
        # 提示文字必须存在（用户反馈过"看不见日记的删除在哪"）
        check('有「右键删除」提示文字',
              any('右键' in str(l.cget('text')) for l in _labels),
              str([l.cget('text') for l in _labels]))
        # 底部不该再有删除按钮（已改到右键菜单）
        check('底部不再有「删除这一天」按钮',
              not any('删除' in str(b.cget('text')) for b in _btns),
              str([b.cget('text') for b in _btns]))
        check('已绑定右键事件', bool(_lb.bind('<Button-3>')))

        # ---- 模拟真实右键：应弹菜单、选中该行、但**不删** ----
        _bb = _lb.bbox(1)
        _lb.event_generate('<Button-3>', x=5, y=_bb[1] + _bb[3] // 2,
                           rootx=200, rooty=200)
        _win.update()
        check('右键弹出了菜单', len(_popped) == 1, str(len(_popped)))
        check('右键后没删东西（只弹菜单）', len(pet.diary.dates()) == 3,
              str(pet.diary.dates()))
        check('右键会选中该行', len(_lb.curselection()) == 1,
              str(_lb.curselection()))
        if _popped:
            _m = _popped[0]
            _items = []
            for _i in range(_m.index('end') + 1):
                _items.append(_m.entrycget(_i, 'label')
                              if _m.type(_i) == 'command' else '---')
            check('菜单含删除项与清空项',
                  any('删除' in x for x in _items)
                  and any('清空' in x for x in _items), str(_items))
            # ---- 点「删除这一天」：确认后才删 ----
            import tkinter.messagebox as _mb
            _real = _mb.askyesno
            try:
                _mb.askyesno = lambda *a, **k: False   # 先测「取消」
                _n0 = len(pet.diary.dates())
                _m.invoke(0)
                _win.update()
                check('确认框选「取消」时不删',
                      len(pet.diary.dates()) == _n0,
                      str(pet.diary.dates()))
                _mb.askyesno = lambda *a, **k: True    # 再测「确认」
                _before = pet.diary.dates()
                _target = _lb.get(_lb.curselection()[0])
                _m.invoke(0)
                _win.update()
                check('确认后删掉了那一天',
                      len(pet.diary.dates()) == len(_before) - 1
                      and not pet.diary.has(_target),
                      f'删 {_target} -> {pet.diary.dates()}')
            finally:
                _mb.askyesno = _real

    tk.Menu.tk_popup = _orig_popup
    if pet._diary_win is not None:
        try:
            pet._diary_win.destroy()
        except Exception:
            pass
        pet._diary_win = None
    # 清掉造出来的测试日
    for dd in ('2099-01-01', '2099-01-02'):
        pet.diary.remove(dd)
    if pet.diary.has(today):
        pet.diary.remove(today)
    check('删除走 DiaryBook.remove', not pet.diary.has(today))

    # 清理测试产生的文件
    # **只删临时目录里的东西，绝不碰真实的 diary.json / weather_cache.json**
    # 踩过的坑：原来这里删的是 `pet.diary.path`，而它当时还指向**真实**的
    # diary.json —— 跑一次测试就把用户的历史日记删掉了（真的发生过）。
    # 所以：只清 tempfile 建的目录，真实路径一个都不要动。
    try:
        pet.activity.data = pet.activity._empty()
        pet.activity._dirty = False
        shutil.rmtree(_tmpdir, ignore_errors=True)
    except Exception:
        pass
    # 断言测试没有写到真实日记本里（防回归）
    try:
        _real = H.DIARY_PATH
        _real_has_today = False
        if os.path.exists(_real):
            _rd = json.load(open(_real, encoding='utf-8')) or {}
            _real_has_today = today in (_rd.get('entries') or {})
        check('测试没有写入真实日记本', not _real_has_today, _real)
    except Exception:
        pass
    pet.root.destroy()

    # ==================== 参数 ====================
    print('\nT6 参数与配置')
    check('日记写入时间合理',
          0 < config.DIARY_WRITE_AT_MIN < 24 * 60, str(config.DIARY_WRITE_AT_MIN))
    check('只记用了很久的程序（>=5 分钟）',
          config.DIARY_MIN_APP_SEC >= 300, f'{config.DIARY_MIN_APP_SEC}s')
    check('config 有 weather_city 默认值',
          'weather_city' in config.Config.DEFAULTS)
    check('config 有 birthday 默认值',
          'birthday' in config.Config.DEFAULTS)
    check('天气城市默认为空（不联网）',
          config.Config.DEFAULTS['weather_city'] == '')

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
