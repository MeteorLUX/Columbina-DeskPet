# -*- coding: utf-8 -*-
"""
静态检查：找出各模块里"引用了但没定义"的全局名字
=================================================
做法：用 symtable 找出每个模块读取的全局名（不含函数内局部变量），
再在「模块自身定义的名字 + 内置名 + 其它模块 via `import *`」里查。
对 `from config import *` 这类通配导入，把 config 的公开名字并入候选。
"""
import builtins
import os
import symtable

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MODULES = ['config', 'ui_widgets', 'walk', 'interaction', 'chat', 'dialogue_llm',
           'hourly', 'menu', 'voice_control', 'voice_engine', 'song_engine',
           'song_control', 'study', 'physics', 'awareness', 'banter',
           'weather', 'holidays', 'diary', 'facts', 'speech', 'speech_control',
           'launcher', 'app', 'main', 'lines']

# 先用 import 把各模块真正加载起来（能立刻暴露 ImportError）
import sys
sys.path.insert(0, BASE)
loaded = {}
for m in MODULES:
    try:
        loaded[m] = __import__(m)
    except Exception as e:
        print(f'  [导入失败] {m}: {type(e).__name__}: {e}')

print(f'已导入 {len(loaded)} 个模块\n')

BUILTINS = set(dir(builtins))


def collect_module_names(mod):
    """模块里定义／导入的所有顶层名字"""
    names = set()
    d = getattr(mod, '__dict__', {})
    names |= set(d.keys())
    return names


# config 的公开名字（供 from config import * 使用）
config_names = {n for n in collect_module_names(loaded['config'])
                if not n.startswith('_')}

problems = []
for mname in MODULES:
    path = os.path.join(BASE, mname + '.py')
    if not os.path.exists(path):
        continue
    src = open(path, encoding='utf-8').read()
    try:
        top = symtable.symtable(src, path, 'exec')
    except SyntaxError as e:
        problems.append((mname, '<语法错误>', str(e)))
        continue

    # 本模块自己定义/导入的名字 + 内置 + config 星号 + 本模块运行时的名字
    local = {s.get_name() for s in top.get_symbols()}
    candidate = local | BUILTINS | config_names
    candidate |= collect_module_names(loaded.get(mname)) if mname in loaded else set()

    # 递归收集所有 scope 里"被读取的全局名"
    used = set()

    def walk(table):
        for sym in table.get_symbols():
            if sym.is_global() and sym.is_referenced():
                used.add(sym.get_name())
        for child in table.get_children():
            walk(child)

    walk(top)
    missing = sorted(n for n in used if n not in candidate)
    if missing:
        problems.append((mname, '未定义全局名', missing))

print('=' * 66)
if not problems:
    print('未发现未定义的全局名字 ✓')
else:
    for m, kind, detail in problems:
        print(f'  {m}.py  {kind}: {detail}')
print('=' * 66)
