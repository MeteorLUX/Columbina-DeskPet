# -*- coding: utf-8 -*-
"""
从 ASR 台词里挑适合闲聊的句子（_pick_dialogues.py）
==================================================
`voice-lines.json` 是切句 + ASR 的产物，里面有三类东西：
  1. **完整、适合闲聊的句子**  <- 要挑出来加进 DEFAULT_DIALOGUES
  2. 被切开的半句话           <- 丢掉（"就好像。" "但现在。"）
  3. ASR 错字 / 专有名词误识  <- 丢掉（"这笔看" "月举例" "绊了一觉"）

筛选规则（保守：宁可少挑，也不要把碎句塞进对话库）：
  · 长度 6~22 字（太短多为碎句，太长不适合随机闲聊）
  · 时长 1.0~4.5 秒
  · 必须含句末标点或问号（完整句特征）
  · 含人称/动词等成句要素，不是纯名词堆
  · 排除已知错字黑名单、排除含专有名词误识的
  · 与现有 DEFAULT_DIALOGUES 去重（归一化）

用法：
    python _pick_dialogues.py                 # 打印候选
    python _pick_dialogues.py --apply         # 写进 config.py 的 DEFAULT_DIALOGUES
    python _pick_dialogues.py --apply --limit 40
"""
import argparse
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import config
import speech as SP

# ASR 错字 / 碎句特征（保守黑名单）
BAD_PATTERNS = [
    r'^就好像', r'^但现在$', r'^不过$', r'^所以', r'^虽然', r'^如果$',
    r'^有一刻', r'^因为', r'^然后', r'^对着', r'^比如说',
    r'月举例', r'绊了一觉', r'这笔看', r'脑好像', r'月举里',
    r'双旗金', r'茨迹', r'阿里奇诺', r'希婆塞兰尼亚',
    # 实测发现的 ASR 错字（听着像真话，其实是识别错误）
    r'幽冥因', r'霜月而长鸣', r'恒月而翻涌', r'模划', r'掌签',
    r'亲情爱', r'嗤笑世界', r'乐之漫', r'月林',
    r'^这(个|些)?$', r'^那(个|些)?$', r'^(嗯|哦|啊|咦|唉|喂|哈)+[。，]?$',
]

# 只收录"能确认是正确台词"的句子（保守白名单）。
# 为什么不自动放开：ASR 错字（"幽冥因红月"应为"盈月"）读起来同样自然，
# 纯靠规则分不出来，只能人工确认。这是**文字**层面的挑选，
# 语音层面仍然是全自动的（403/227 段全部转写，无需标注）。
CURATED = [
    # —— 自我介绍 / 名字 ——
    '你可以叫我哥伦比亚，也可以叫我库塔尔月神大人。',
    '选你喜欢的吧。',
    '我习惯了有很多名字的日子。',
    '哥伦比亚、希坡塞兰尼亚。',
    # —— 赏月 / 摇椅 ——
    '想要赏月吗？',
    '你可以坐到我的摇椅上，找个舒服的角度。',
    # —— 月矩力 / 眼罩 ——
    '我的眼罩是镂空的。',
    '不觉得这种说法很有趣吗？',
    # —— 名字的意义 ——
    '在7月之夜，我得到了自己的名字。',
    # —— 唱歌 ——
    '我的歌并不为谁而唱。',
    '但如果有人驻足聆听，我希望他能从中获得片刻的安宁。',
    '我会尽量不在你睡着之后唱歌的。',
    # —— 陪伴 ——
    '陪你聊聊天，还是和你一起做游戏。',
    '你是要和我聊什么来着？',
    '有些不习惯，我应该做些什么呢？',
    '采一些你种的花草也没事吗？',
    '有没有推荐的书想拿来读给月灵们听？',
    '好吧，那我的确有想让你帮忙的事。',
    # —— 吃东西 ——
    '新鲜的水果蔬菜配上麦香味的面包。',
    # —— 与人相处 ——
    '他们眼中的世界很单纯，也很美丽。',
    '如果真的发生了的话。',
    '又或者他早已为自己选定了结局。',
    '但那晚过后，我明白了。',
    '我都能想象出他的声音了。',
    '像一尊还没来得及刻画表情的雕像。',
    '或许葬在了冰山之下。',
    '自己却从不微笑。',
    # —— 我离开了，但我很想你 ——
    '嗯，其实不太习惯。',
    '可能是习惯了吧。',
    '星月将注视并照亮月下的世界。',
    # —— 日常应答 ——
    '那就开始吧。',
    '你看很神奇吧。',
    '要不要给你也试试？',
    '恭喜你升职了，哥伦比亚小姐。',
]
# 碎句特征：以这些词结尾，说明话没说完
DANGLING_TAIL = ('就好像', '但现在', '不过', '所以', '虽然', '如果',
                 '因为', '然后', '有一刻', '比如说', '一般这种时候',
                 '虽然能够', '比如说')


def all_dialogue_lines():
    """现有 DEFAULT_DIALOGUES 的每一行（去掉注释与空行）"""
    return [l.strip() for l in config.DEFAULT_DIALOGUES.split('\n')
            if l.strip() and not l.strip().startswith('#')]


def is_complete(text):
    """看起来是完整句子（有句末标点，且不是碎句）"""
    t = (text or '').strip()
    if not t:
        return False
    for pat in BAD_PATTERNS:
        if re.search(pat, t):
            return False
    for tail in DANGLING_TAIL:
        if t.rstrip('。！？，') == tail:
            return False
    # 完整句通常有句末标点
    if not re.search(r'[。！？]$', t):
        return False
    # 必须有实义成分，不能只是"好的。""是的。"
    core = re.sub(r'[。！？，、…]+$', '', t)
    if len(core) <= 3:
        return False
    return True


def pick(lines, cur_norm, min_len=5, max_len=34, min_dur=0.6, max_dur=6.5):
    """挑适合闲聊的句子。

    切句加了"碎句合并"之后，句子变长变完整了（中位 5.68s），
    所以这里的长度上限也放宽；但仍要排除：
      · 多句挤在一起（一段里超过 2 个句末标点）—— 那是没切开的段落
      · 明显 ASR 错字
      · 结尾是逗号（话没说完）
    """
    out = []
    for t, r in lines.items():
        d = r.get('dur') or 0
        n = SP.normalize_text(t)
        if not n or n in cur_norm:
            continue
        if not (min_len <= len(t) <= max_len):
            continue
        if not (min_dur <= d <= max_dur):
            continue
        # 结尾必须是句末标点（否则是半句）
        if not re.search(r'[。！？]$', t.strip()):
            continue
        # 一段里最多两句话（再多说明没切开）
        if len(re.findall(r'[。！？]', t)) > 2:
            continue
        if not is_complete(t):
            continue
        out.append((t.strip(), r.get('id'), d))
    # 短的优先（更像随口闲聊）
    out.sort(key=lambda x: (len(x[0]), x[2]))
    return out


def apply_to_config(picked):
    """把挑出的句子插进 config.py 的 DEFAULT_DIALOGUES"""
    path = os.path.join(config.BASE_DIR, 'config.py')
    src = open(path, encoding='utf-8').read()
    anchor = 'DEFAULT_DIALOGUES = """'
    i = src.index(anchor)
    j = src.index('"""', i + len(anchor))
    block = src[i:j]
    have = {SP.normalize_text(l.strip())
            for l in block.split('\n') if l.strip()}
    add = [t for t, _id, _d in picked
           if SP.normalize_text(t) not in have]
    if not add:
        print('  没有需要新增的句子')
        return 0
    new_block = block.rstrip('\n') + '\n' + '\n'.join(add) + '\n'
    src = src[:i] + new_block + src[j:]
    open(path, 'w', encoding='utf-8').write(src)
    return len(add)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--apply', action='store_true')
    ap.add_argument('--auto', action='store_true',
                    help='用规则自动挑（默认用 CURATED 白名单）')
    ap.add_argument('--limit', type=int, default=0)
    ap.add_argument('--min-len', type=int, default=6)
    ap.add_argument('--max-len', type=int, default=30)
    args = ap.parse_args()

    print('=' * 68)
    print('从 ASR 台词挑闲聊句子')
    print('=' * 68)
    p = os.path.join(config.BASE_DIR, 'voice-lines.json')
    if not os.path.exists(p):
        print(f'  ✗ 缺少 {p}（先跑 _transcribe_voice.py）')
        return 1
    data = json.load(open(p, encoding='utf-8'))
    lines = data.get('lines') or {}
    cur = all_dialogue_lines()
    cur_norm = {SP.normalize_text(l) for l in cur}
    print(f'  ASR 台词 {len(lines)} 条，现有对话库 {len(cur)} 条')

    if args.auto:
        picked = pick(lines, cur_norm, args.min_len, args.max_len)
        print(f'  规则自动筛选: {len(picked)} 条')
    else:
        # 白名单模式：只保留**确实是原声台词**（在 ASR 结果里能找到）的句子
        norm2id = {}
        for t, r in lines.items():
            norm2id[SP.normalize_text(t)] = (t, r.get('id'), r.get('dur'))
        picked, missing = [], []
        for t in CURATED:
            hit = norm2id.get(SP.normalize_text(t))
            if hit:
                picked.append(hit)
            else:
                missing.append(t)
        print(f'  白名单 {len(CURATED)} 条 -> 命中 {len(picked)} 条')
        if missing:
            print(f'  ⚠ 未在 ASR 结果里找到 {len(missing)} 条（可能有错字或已切碎）:')
            for t in missing[:10]:
                print(f'      {t}')
        # 去掉已有
        picked = [x for x in picked if SP.normalize_text(x[0]) not in cur_norm]

    if args.limit:
        picked = picked[:args.limit]

    print()
    for t, vid, d in picked:
        print(f'    {vid}  {(d or 0):4.2f}s  {t}')

    if args.apply:
        n = apply_to_config(picked)
        print()
        print(f'  ✓ 已写入 config.py 的 DEFAULT_DIALOGUES（新增 {n} 条）')
        print('    提示：dialogues.txt 是运行时生成的；'
              '删掉它才会用新内容重新生成。')
    return 0


if __name__ == '__main__':
    sys.exit(main())
