# -*- coding: utf-8 -*-
"""
用语音库重建 DEFAULT_DIALOGUES（_rebuild_dialogues.py）
======================================================
【为什么要重建】
  `DEFAULT_DIALOGUES` 里 35 条是**句子合并之前**手写的，已经和语音库脱节：
    62 条里只有 27 条能查到音频，2 条要拆开才有，33 条完全没音频。
  而语音库有 217 句、其中 190 句根本没进对话库。
  结果：她大部分时候只冒气泡不出声。

【重建原则】
  1. **只收语音库里真实存在的句子** -> 每条都必然有音频，开口必有声
  2. 过滤碎句与明显 ASR 错字（错字已在 voice-fixes.json 修过）
  3. 按主题分组排列，便于阅读和维护
  4. 替换而不是追加（追加会让库越滚越乱）

【筛选规则】
  · 至少 3 个汉字（"嗯。""好的。"这类太短，单独成句不像话；
    它们已经放在 PET_LINES / STUDY_CHAT_LINES 里当语气词用）
  · 结尾要有句末标点（没有的话说明是被切开的半句）
  · 一句话里最多 3 个句末标点（再多说明是好几段挤在一起）
  · 不含已知错字模式

用法：
    python _rebuild_dialogues.py             # 预览（不改文件）
    python _rebuild_dialogues.py --apply     # 写进 config.py
    python _rebuild_dialogues.py --apply --min-dur 2.0 --max-dur 12.0
"""
import argparse
import json
import os
import re
import shutil
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import config
import speech as SP

# ---- 主题分组：按关键词归类，顺序 = 在对话库里的排列顺序 ----
# 每组是 (标题, [关键词])；关键词命中任一即归入该组。
GROUPS = [
    ('自我介绍 / 名字', ['哥伦比亚', '名字', '叫我', '月神', '希坡', '希婆']),
    ('赏月 / 月亮', ['赏月', '月亮', '月光', '月夜', '祈月', '银月', '月下']),
    ('月矩力 / 感知', ['月矩力', '感知', '感受']),
    ('唱歌 / 歌', ['唱', '歌', '倾听', '聆听']),
    ('月灵', ['月灵']),
    ('陪伴 / 闲聊', ['陪你', '聊天', '陪', '一起', '做客', '客人', '交谈']),
    ('朋友 / 执行官', ['桑多涅', '阿蕾奇诺', '丑角', '执行官', '愚人众',
                  '朋友', '梅拉', '阿贝多', '尼可', '队长', '女皇']),
    ('旅行 / 散步', ['旅行', '散步', '漫步', '旅途', '列车', '走']),
    ('吃 / 食物', ['吃', '食物', '面包', '水果', '蔬菜', '茶', '点心',
                '「火水」', '餐厅', '酒']),
    ('庭园 / 花草', ['庭院', '花', '景致', '种', '果实', '树']),
    ('雪 / 雨 / 天气', ['雪', '雨', '雷', '闪电', '晴', '风', '天气']),
    ('眼睛 / 眼罩', ['眼罩', '眼睛', '睁开', '闭', '睁']),
    ('家 / 流浪', ['家', '流浪', '壁炉', '月亮上']),
    ('生日 / 节日', ['生日', '节日', '庆祝', '礼物', '升职']),
    ('睡觉 / 梦', ['睡', '梦', '晚安', '午觉', '休息']),
    ('喜欢 / 讨厌', ['喜欢', '讨厌', '不讨厌', '钟意']),
    ('外表 / 穿着', ['外套', '发绳', '头发', '衣服', '眼罩是']),
]

# 已知错字/碎句模式（voice-fixes.json 修过的那些已不在库里）
BAD_PATTERNS = [
    r'^[。，、…？！\s]+$',
    r'没关系$',                      # 多为切碎的半句
    r'^(其实|因为|所以|但是|然后|而且|不过|虽然|如果|于是)[，。]?$',
    r'^(这个|那个|这样|那样|这里|那里)[。，]?$',
    r'模划|掌签|嗤笑世界|亲情爱|绊了一觉|这笔看|脑好像',
    r'双旗金|茨迹|希婆塞兰尼亚|乌马|掌中|月举例|月举里',
    # —— 残句检测（重要）——
    # 句子主体末尾是"逗号 + 很短的一截"，说明原本话没说完就被切了。
    # 例："我的答案留在月亮上，他的答案。" / "我只能通过感知月亮来估算
    # 自己的生日。不过。" / "你准备了茶水和点心，好吧，。"
    r'[，、][^，、。！？]{0,4}。$',
    r'[，、]。$',
    r'(不过|后来|然后|所以|但是|而且|的话|的时候|他的答案|这之后)[。！？]$',
]


def hanzi_count(t):
    return sum(1 for c in t if '\u4e00' <= c <= '\u9fff')


def is_bad(t):
    for p in BAD_PATTERNS:
        if re.search(p, t):
            return p
    return None


def collect(min_dur=1.0, max_dur=12.0):
    """从语音库收集可用台词，返回 (通过列表, 过滤统计)"""
    lp = os.path.join(config.BASE_DIR, 'voice-lines.json')
    vdir = config.SPEECH_VOICE_DIR
    if not os.path.exists(lp):
        return [], {}, 'voice-lines.json 不存在'
    data = json.load(open(lp, encoding='utf-8'))
    lines = data.get('lines') or {}

    ok, skipped = [], {}
    seen_norm = set()
    for text, rec in lines.items():
        vid = rec.get('id')
        dur = float(rec.get('dur') or 0)
        t = (text or '').strip()
        why = None
        if not vid or not t:
            why = '空'
        elif not os.path.exists(os.path.join(vdir, vid + '.wav')):
            why = '音频缺失'
        elif hanzi_count(t) < 3:
            why = '太短'          # 语气词另有归处（PET_LINES 等）
        elif not re.search(r'[。！？]$', t):
            why = '无句末标点(半句)'
        elif len(re.findall(r'[。！？]', t)) > 3:
            why = '多句堆叠'
        elif not (min_dur <= dur <= max_dur):
            why = f'时长超范围({dur:.1f}s)'
        elif is_bad(t):
            why = '疑似错字/碎句'
        else:
            nk = SP.normalize_text(t)
            if nk in seen_norm:
                why = '重复'
            else:
                seen_norm.add(nk)
        if why:
            skipped[why] = skipped.get(why, 0) + 1
        else:
            ok.append((t, dur, hanzi_count(t)))
    ok.sort(key=lambda x: x[1])
    return ok, skipped, None


def group_lines(items):
    """按主题归类；返回 [(标题, [句子])]，未归类的放最后"""
    used = set()
    out = []
    for title, kws in GROUPS:
        got = []
        for t, d, n in items:
            if t in used:
                continue
            if any(k in t for k in kws):
                got.append(t)
                used.add(t)
        if got:
            out.append((title, got))
    rest = [t for t, d, n in items if t not in used]
    if rest:
        out.append(('其他', rest))
    return out


def build_block(groups, total):
    ts = time.strftime('%Y-%m-%d %H:%M')
    head = [
        '# 哥伦比娅 · 本地对话库',
        '# 每行一句台词，以 # 开头的行为注释会被忽略。',
        '#',
        f'# 【自动生成，勿手改】由 _rebuild_dialogues.py 于 {ts} 生成。',
        f'# 来源：voice-lines.json 里**确实有音频**的 {total} 句台词。',
        '# 因此这里的每一句都能用她的原声念出来（"没音频就不出声"）。',
        '# 要增删请改 voice-lines.json / voice-fixes.json 后重跑本脚本，',
        '# 否则下次重建会覆盖手改内容。',
    ]
    body = []
    for title, got in groups:
        body.append('')
        body.append(f'# —— {title}（{len(got)} 句）——')
        body.extend(got)
    return '\n'.join(head + body) + '\n'


def apply_block(block):
    """把新内容写进 config.py 的 DEFAULT_DIALOGUES"""
    path = os.path.join(config.BASE_DIR, 'config.py')
    src = open(path, encoding='utf-8').read()
    anchor = 'DEFAULT_DIALOGUES = """'
    i = src.index(anchor)
    j = src.index('"""', i + len(anchor))
    # 备份一份，万一要回退
    bak = path + '.dialogue-bak'
    if not os.path.exists(bak):
        shutil.copy2(path, bak)
    new = src[:i] + anchor + block + src[j:]
    open(path, 'w', encoding='utf-8').write(new)
    return path, bak


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--apply', action='store_true')
    ap.add_argument('--min-dur', type=float, default=1.0)
    ap.add_argument('--max-dur', type=float, default=12.0)
    args = ap.parse_args()

    print('=' * 76)
    print('用语音库重建 DEFAULT_DIALOGUES')
    print('=' * 76)

    items, skipped, err = collect(args.min_dur, args.max_dur)
    if err:
        print(f'  ✗ {err}')
        return 1
    print(f'  可用台词 {len(items)} 句（每条都有音频）')
    print(f'  过滤统计: '
          + '  '.join(f'{k}={v}' for k, v in sorted(skipped.items())))
    print()

    groups = group_lines(items)
    print(f'  主题分组 {len(groups)} 组:')
    for title, got in groups:
        print(f'    {title:18s} {len(got):3d} 句')
    print()

    block = build_block(groups, len(items))
    preview = block.split('\n')
    print('  预览（前 30 行）:')
    for ln in preview[:30]:
        print(f'    {ln[:70]}')
    if len(preview) > 30:
        print(f'    ...（共 {len(preview)} 行）')

    print()
    old = [l.strip() for l in config.DEFAULT_DIALOGUES.split('\n')
           if l.strip() and not l.strip().startswith('#')]
    new_norm = {SP.normalize_text(t) for t, _d, _n in items}
    old_hit = sum(1 for l in old if SP.normalize_text(l) in new_norm)
    print(f'  旧对话库 {len(old)} 条（其中能查到音频的 {old_hit} 条）')
    print(f'  新对话库 {len(items)} 条（**全部有音频**）')

    if not args.apply:
        print()
        print('  这是预览。加 --apply 才会写入 config.py。')
        return 0

    path, bak = apply_block(block)
    print()
    print(f'  已写入 {path}')
    print(f'  原文件备份 {bak}（确认没问题可以删）')
    print('  提示：dialogues.txt 是运行时生成的，'
          '删掉它才会用新内容重新生成。')
    return 0


if __name__ == '__main__':
    sys.exit(main())
