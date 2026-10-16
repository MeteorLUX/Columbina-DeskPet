# -*- coding: utf-8 -*-
"""
跑测试（tests/_run_tests.py）
=============================
**为什么需要它**：项目只允许一个 `tk.Tk()` 实例，同一进程里连跑多个套件
会撞 Tk 的内部图像表。所以这里**一个套件起一个子进程**。

用法：
    python tests/_run_tests.py                  # 全跑
    python tests/_run_tests.py speech diary     # 只跑名字匹配这两个关键词的
    python tests/_run_tests.py --list           # 列出所有套件
    python tests/_run_tests.py --fast           # 只跑最快的几个（改完代码先过一遍）

判定成败看输出里的两句话：
    `结果: ALL_PASS`              （气泡/姿态类套件的格式）
    `通过 N 项，失败 M 项`         （回归类套件的格式）
    `未发现未定义的全局名字`        （静态检查）
"""
import argparse
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
BASE = os.path.dirname(HERE)
PY = sys.executable or 'python'

# (文件名, 关键词, 说明)
SUITES = [
    ('_namecheck_test.py', 'static', '静态检查：引用了但没定义的全局名'),
    ('_speech_regress_test.py', 'speech', '说话：引擎/重采样/检索/不卡顿'),
    ('_diary_regress_test.py', 'diary', '日记/天气/节日'),
    ('_aware_regress_test.py', 'aware', '屏幕感知/打开东西'),
    ('_facts_regress_test.py', 'facts', '时间/日期/天气问答'),
    ('_sing_regress_test.py', 'sing', '唱歌与语音协调'),
    ('_sing_flow_test.py', 'sing', '唱歌续航'),
    ('_sing_ask_test.py', 'sing', '唱歌询问策略'),
    ('_study_regress_test.py', 'study', '学习计时'),
    ('_study_pose_test.py', 'study', '学习期姿态'),
    ('_duplex_test.py', 'voice', '全双工语音底座'),
    ('_bubble_test.py', 'ui', '气泡生命周期'),
    ('_bubble_frost_test.py', 'ui', '毛玻璃气泡'),
    ('_rhythm_test.py', 'ui', '打字节奏'),
    ('_pose_test.py', 'ui', '姿态规则'),
]

# --fast：改完代码先过这几个（都很快，且覆盖最容易坏的公共部分）
FAST = {'_namecheck_test.py', '_facts_regress_test.py',
        '_bubble_test.py', '_pose_test.py'}


def judge(out):
    """从输出判断结果，返回 (ok, 摘要)"""
    if '未发现未定义的全局名字' in out:
        return True, 'PASS'
    if '结果: ALL_PASS' in out:
        return True, 'ALL_PASS'
    import re
    m = None
    for m in re.finditer(r'通过 (\d+) 项，失败 (\d+) 项', out):
        pass
    if m:
        ok_n, bad_n = m.group(1), m.group(2)
        return (bad_n == '0'), f'通过 {ok_n}，失败 {bad_n}'
    return False, '没有识别到结果（可能崩了）'


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('keys', nargs='*', help='关键词过滤（如 speech diary）')
    ap.add_argument('--list', action='store_true')
    ap.add_argument('--fast', action='store_true')
    ap.add_argument('--timeout', type=int, default=300, help='单个套件超时秒数')
    args = ap.parse_args()

    if args.list:
        print(f'  {"文件":30s} {"关键词":8s} 说明')
        for fn, kw, desc in SUITES:
            print(f'  {fn:30s} {kw:8s} {desc}')
        return 0

    todo = SUITES
    if args.fast:
        todo = [s for s in SUITES if s[0] in FAST]
    elif args.keys:
        low = [k.lower() for k in args.keys]
        todo = [s for s in SUITES
                if any(k in s[0].lower() or k in s[1].lower() or k in s[2]
                       for k in low)]

    print('=' * 78)
    print(f'测试：{len(todo)} 个套件'
          + (f'（筛选：{" ".join(args.keys)}）' if args.keys else '')
          + ('（--fast）' if args.fast else ''))
    print('=' * 78)

    results = []
    t_all = time.time()
    for fn, kw, desc in todo:
        path = os.path.join(HERE, fn)
        if not os.path.exists(path):
            results.append((fn, None, '文件不存在'))
            print(f'  {fn:30s}   跳过（不存在）')
            continue
        t0 = time.time()
        try:
            p = subprocess.run([PY, '-X', 'utf8', path],
                               capture_output=True, text=True,
                               encoding='utf-8', errors='replace',
                               timeout=args.timeout, cwd=BASE)
            out = (p.stdout or '') + (p.stderr or '')
            ok, summary = judge(out)
        except subprocess.TimeoutExpired:
            ok, summary = False, f'超时（>{args.timeout}s）'
        dt = time.time() - t0
        results.append((fn, ok, summary, dt))
        flag = 'OK  ' if ok else 'FAIL'
        print(f'  {flag} {fn:30s} {summary:22s} {dt:5.1f}s')

    print('=' * 78)
    bad = [r for r in results if r[1] is False]
    n_ok = len([r for r in results if r[1] is True])
    print(f'  通过 {n_ok}/{len(results)}   总耗时 {time.time()-t_all:.1f}s')
    if bad:
        print('  失败：')
        for fn, _ok, summary, *_ in bad:
            print(f'    - {fn}: {summary}')
    else:
        print('  全部通过')
    print('=' * 78)
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())
