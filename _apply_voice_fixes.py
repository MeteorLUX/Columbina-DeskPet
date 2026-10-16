# -*- coding: utf-8 -*-
"""
直改语音库勘误（_apply_voice_fixes.py）
======================================
把 voice-fixes.json 里的勘误直接套到 voice-lines.json 的键/文本上，秒级完成。

【为什么有这个工具】
  勘误的正常生效路径是重跑 `_transcribe_voice.py`（228MB 模型重新转写全部原声）。
  但只是补几条错别字时，重跑模型要几分钟，没必要——
  转写产出的文字和勘误套用是两步，这里只重做第二步，效果完全一致。

【用法】
  1. 往 voice-fixes.json 加一条 {"听错的原文": "正确文字"}；
     值写空串 "" 表示**删除整句**（键必须完全等于整句，防止子串误删）
  2. 跑 `python _apply_voice_fixes.py`
  3. 台词文字变了 -> 检索、气泡显示、精确匹配三处同步生效；
     若该句也在 lines.py 台词库里，记得把 lines.py 里的同一句改成一致。

【注意】
  配对是**子串替换**：尽量写长一点（如「和愚人中的两般经历」而不是「愚人中」），
  避免误伤「愚人中的桑多涅」这类正确用法。
"""
import json
import os
import sys

BASE = os.path.dirname(os.path.abspath(__file__))
LINES_PATH = os.path.join(BASE, 'voice-lines.json')
FIXES_PATH = os.path.join(BASE, 'voice-fixes.json')


def main():
    fixes = json.load(open(FIXES_PATH, encoding='utf-8'))
    # 跳过 _ 开头的注释键；长的先替换，避免短配对抢先破坏长配对的上下文
    pairs = sorted(
        ((k, v) for k, v in fixes.items()
         if not k.startswith('_') and k != v),
        key=lambda kv: -len(kv[0]))
    if not pairs:
        print('  勘误表为空，无事可做')
        return 0

    d = json.load(open(LINES_PATH, encoding='utf-8'))
    lines = d.get('lines') or {}

    changed = removed = 0
    for wrong, right in pairs:
        if right == '':       # 值为空 = 删除整键（只在键完全一致时删，避免子串误删）
            if wrong in lines:
                del lines[wrong]
                removed += 1
                print(f'  删除键: {wrong}')
            continue
        for text in list(lines.keys()):
            if wrong not in text:
                continue
            new = text.replace(wrong, right)
            rec = lines.pop(text)
            rec['text'] = new
            if new in lines:      # 目标键已存在（同名句）：保留库里的旧记录
                print(f'  ! {text!r} -> 与已有键重复，丢弃')
                continue
            lines[new] = rec
            changed += 1
            print(f'  {wrong} -> {right}')
            print(f'      {text}')
            print(f'   => {new}')

    if changed or removed:
        with open(LINES_PATH, 'w', encoding='utf-8') as f:
            json.dump(d, f, ensure_ascii=False, indent=1)
    print(f'  共修正 {changed} 键、删除 {removed} 键（现余 {len(lines)} 键）')
    return 0


if __name__ == '__main__':
    sys.exit(main())
