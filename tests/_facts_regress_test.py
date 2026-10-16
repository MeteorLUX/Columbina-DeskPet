# -*- coding: utf-8 -*-
"""
事实问答（时间/日期/天气）回归测试
==================================
用户反馈：问她时间只会笼统回答，要准确回答。

根因两层：
  1. 系统提示词里**没有**实时时间/天气 -> 大模型只能猜（笼统/编造）
  2. 问事实也走大模型 -> 有延迟、可能答错

修复两层：
  A. facts_context() 把真实时间/日期/节日/天气注入提示词
  B. facts_answer() 对时间/日期/天气**本地精准回答**，不走大模型

测试重点：
  · 回答必须精确（含分钟 / 含年月日 / 含温度）
  · **绝不能误吞闲聊**（"今天心情不好"不能当问日期）
  · 天气没配城市时老实说不知道，不编造
  · 提示词注入内容正确
"""
import datetime
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
import weather as W
import facts as F

PASSED, FAILED = [], []


def check(name, cond, extra=''):
    (PASSED if cond else FAILED).append(name)
    print(('  ✓ ' if cond else '  ✗ ') + name + (f'   {extra}' if extra else ''))


def main():
    print('=' * 62)
    print('事实问答（时间/日期/天气）回归测试')
    print('=' * 62)

    import app as APP
    pet = APP.DesktopPet()
    pet.root.withdraw()

    now = datetime.datetime.now()
    today = now.date()

    # ==================== T1 时间：必须精确到分钟 ====================
    print('\nT1 问时间 -> 精确到分钟')
    for q in ['现在几点了', '几点了', '现在什么时间', '现在什么时候了',
              'what time is it', '现在几点', '几时了', '现在多晚了']:
        a = pet.facts_answer(q)
        ok = bool(a) and (f'{now.hour:02d}:{now.minute:02d}' in a
                          or f'{now.minute:02d}分' in a)
        check(f'{q!r} -> 含分钟', ok, repr(a))

    # 必须不是笼统说法
    a = pet.facts_answer('现在几点了')
    check('不是笼统回答（不含"大概/应该/可能"）',
          not re.search(r'(大概|应该|可能|差不多|或许是)', a), a)
    check('回答简短（<=60 字）', len(a) <= 60, f'{len(a)} 字')

    # ==================== T2 日期：必须有年月日 ====================
    print('\nT2 问日期 -> 含年月日')
    for q in ['今天几号', '今天几月几号', '今天日期', '今天是什么日子',
              'today date', '今天星期几']:
        a = pet.facts_answer(q)
        ok = bool(a) and (f'{today.year}年' in a or '星期' in a)
        check(f'{q!r} -> 含年月日或星期', ok, repr(a))

    a = pet.facts_answer('今天几号')
    check('含年', f'{today.year}年' in a, a)
    check('含月日', f'{today.month}月' in a and f'{today.day}日' in a, a)
    check('含星期', '星期' in a, a)

    # 只问星期几 -> 只答星期，不啰嗦
    a = pet.facts_answer('今天星期几')
    check('只问星期就只答星期（简洁）', a.count('。') <= 2 and '星期' in a, a)

    # 昨天/明天
    a = pet.facts_answer('明天几号')
    tom = today + datetime.timedelta(days=1)
    check('支持"明天几号"', f'{tom.month}月' in (a or ''), repr(a))
    a = pet.facts_answer('昨天几号')
    yes = today - datetime.timedelta(days=1)
    check('支持"昨天几号"', f'{yes.month}月' in (a or ''), repr(a))

    # ==================== T3 天气 ====================
    print('\nT3 问天气')
    # 没配城市：老实说不知道，并给指引
    pet.cfg['weather_city'] = ''
    pet._weather.info = None
    a = pet.facts_answer('今天天气怎么样')
    check('没配城市时不编造', bool(a) and ('不知道' in a or '设置' in a), repr(a))

    # 配了城市但还没数据
    pet.cfg['weather_city'] = '成都'
    pet._weather.info = None
    a = pet.facts_answer('今天天气怎么样')
    check('有城市但没数据时说明情况',
          bool(a) and ('成都' in a or '还没查到' in a), repr(a))

    # 有数据：必须含城市、描述、温度
    pet._weather.info = W.WeatherInfo(
        ok=True, city='成都', desc='小雨', temp=16.4,
        tmax=19.0, tmin=13.0, rain_prob=85, special=True)
    a = pet.facts_answer('今天天气怎么样')
    check('含城市', '成都' in a, a)
    check('含天气描述', '小雨' in a, a)
    check('含温度（取整）', '16' in a, a)
    check('含温度范围', '13' in a and '19' in a, a)
    check('含降水概率', '85' in a, a)
    check('特殊天气补一句关心', '雨' in a and '\n' in a, repr(a))

    # 各种问法都能命中
    for q in ['外面下雨了吗', '冷不冷', '今天多少度', '要带伞吗', '气温多少']:
        a = pet.facts_answer(q)
        check(f'{q!r} -> 命中天气', bool(a) and '成都' in a, repr(a))

    # ==================== T4 绝不能误吞闲聊 ====================
    print('\nT4 不能误吞闲聊（关键）')
    chat = [
        '今天心情不好',
        '今天过得很累',
        '今天做了什么',
        '天气不错啊',
        '今天天气真好',
        '我想听你唱歌',
        '你好呀',
        '给我讲个故事',
        '学习记录',
        '你为什么总是这样',
        '今天打算做什么',
        '我们一起唱歌吧',
        # 长句是在聊天，不是问事实
        '我们要不要一起出去走走看看天气',
        '关于今天的时间安排你有什么想法吗我们聊聊',
    ]
    for q in chat:
        a = pet.facts_answer(q)
        check(f'{q!r} 不被拦截', a is None, repr(a))

    # 长句不该被当事实问题
    long_q = '我现在想问你一个比较长的问题关于我们今天要不要一起出去走走看看天气'
    check('超长句不拦截', pet.facts_answer(long_q) is None)

    # ==================== T5 提示词注入 ====================
    print('\nT5 系统提示词注入实时事实')
    pet.cfg['weather_city'] = '成都'
    pet._weather.info = W.WeatherInfo(ok=True, city='成都', desc='小雨',
                                      temp=16.0, special=True)
    pet.cfg['birthday'] = ''
    sp = pet._build_system_prompt()
    check('提示词含"此刻的真实信息"', '此刻的真实信息' in sp)
    check('提示词含当前时间', f'{now.hour:02d}:{now.minute:02d}' in sp)
    check('提示词含今天日期', f'{today.year}年' in sp)
    check('提示词含星期', '星期' in sp)
    check('提示词含天气', '成都' in sp and '小雨' in sp)
    check('提示词含使用要求（用准确数据）',
          '必须用上面的准确数据回答' in sp)
    check('提示词仍保留人设', '哥伦比娅' in sp)

    # 没配城市时，提示词里应说明"不知道"
    pet.cfg['weather_city'] = ''
    pet._weather.info = None
    sp2 = pet._build_system_prompt()
    check('没配城市时提示词说明不知道',
          '不知道' in sp2 and 'weather_city' in sp2, sp2[-220:])

    # 节日注入
    ctx = pet.facts_context()
    check('context 有节日行', '· 节日：' in ctx, ctx[:200])
    check('context 有天气行', '· 天气：' in ctx)

    # ==================== T6 边界与稳健性 ====================
    print('\nT6 边界情况')
    check('空字符串不崩', pet.facts_answer('') is None)
    check('None 不崩', pet.facts_answer(None) is None)
    check('纯符号不崩', pet.facts_answer('。。。') is None)

    # 天气模块整体坏掉时不该让问答崩
    old = pet._weather
    pet._weather = None
    try:
        pet.cfg['weather_city'] = '成都'
        a = pet.facts_answer('今天天气怎么样')
        check('weather 对象缺失时不崩', isinstance(a, str), repr(a))
    finally:
        pet._weather = old

    # 时段函数覆盖 0~23 点
    periods = [pet._facts_period(h) for h in range(24)]
    check('时段函数覆盖全部 24 小时', all(periods),
          str(sorted(set(periods))))
    check('中午 12 点是"中午"', pet._facts_period(12) == '中午',
          pet._facts_period(12))
    check('凌晨 3 点是"凌晨"', pet._facts_period(3) == '凌晨',
          pet._facts_period(3))

    pet.root.destroy()
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
