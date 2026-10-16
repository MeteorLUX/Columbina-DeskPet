# -*- coding: utf-8 -*-
"""
重排对话库：手写人设 + 库里适合闲聊的（_rebuild_dialogues.py）
==============================================================
用户要求：
  1. **手写人设台词要保留**（贴合人设、适合闲聊），但它们没音频 -> 不出声
  2. 语音库生成的台词**全部有音频** -> 会出声
  3. "其他"组里**前言不搭后语**的句子删掉

【最终结构】（写进 lines.py 的 DEFAULT_DIALOGUES）
  # —— 【人设台词】不出声（贴合设定，但没有对应原声）——
  手写的、最能体现她性格的句子。检索时会被用到，但放不出声音。

  # —— 【她的原声 · 闲聊】全部有音频 ——
  从语音库筛出的完整句子，按主题分组。每一句都能用原声念出来。

【为什么分两段】
  用户说"本地对话也要之前自己创建的一些，不出声就行" —— 手写台词的价值在
  **内容贴合人设**，不在声音。它们进检索池（她的回答会更像她），
  但语音引擎查不到音频，所以静音。语音库那批则保证开口必有声。

用法：
    python _rebuild_dialogues.py            # 预览
    python _rebuild_dialogues.py --apply    # 写入 lines.py
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

# ==================== 第一段：手写人设台词（无音频，不出声）====================
# 这些是从对话历史里完整还原的早期手写台词。**保留它们的原因**：
# 它们是为"哥伦比娅"这个角色专门写的，比 ASR 转出来的句子更贴人设、
# 更适合闲聊。虽然没有音频（所以不会出声），但进检索池后能让她的
# 回答更符合设定。
PERSONA_LINES = [
    '……嗯。',
    '你来了。',
    '我感受到了你的气息。',
    '今天的月亮，很安静呢。',
    '我的歌并不为谁而唱。但如果有人驻足聆听，我希望他能从中获得片刻的安宁。',
    '想要赏月吗？你可以坐到我身边来。',
    '我过去每天都会在银月之庭漫步很久，不用担心我跟不上你的步伐。',
    '我习惯了用月矩力去「感受」世界，这比「看」更能展现事物的真相。',
    '偶尔好奇的时候，我也会悄悄睁开眼睛偷看一下……我的眼罩是镂空的。',
    '我曾以为自己一直在流浪，月亮才是我的「家」。但现在，「家」变多了，也变近了。',
    '名字是我同这个世界的连结……我会珍惜它的。',
    '我很久之前就听说过你……只是你的名号太多了，还没讲到名字，我就已经睡着了。',
    '偶尔回到月亮上，静静地看着提瓦特运转，会让我很安心。',
    '想和普通人一样在那夏镇散步……但每次都会被认出来。是应该换件外套吗？',
    '新鲜的水果、蔬菜，配上麦香味的面包……我喜欢食物最初的样子。',
    '「火水」那种烈酒……为什么大家喜欢世界像在旋转的感觉呢？他们也想飘到月亮上去吗？',
    '我喜欢雪。美丽又安静。',
    '避雨的时候听雨水冲刷大地，会给我一种在「家」的安全感。',
    '睁不开眼的话，就把手给我，我带你走。',
    '我可以在附近一边散步一边唱歌吗？休息的时候，随便找个地方睡下也可以吗？',
    '以前都是你来做客，现在轮到我当客人……有些不习惯。',
    '桑多涅就是桑多涅，是我最好的朋友。',
    '我在阿蕾奇诺经过时，学着孩子们叫了一声「父亲」……然后我才知道，原来壁炉之家还能那么安静。',
    '她把感情封进了厚厚的冰层……我的答案留在月亮上。',
    '「生日」的确是个愉快的节日……那好呀，我可以为你唱首歌。',
    '还不错，我会伴着月光慢慢享用的。',
    '我还要。咦……？没有更多了吗？好吧，我下次省着吃。',
    '月亮上，什么都没有。',
    '你在忙吗？我在这里。',
    '其实，我一直都在。',
    '月矩力告诉我，你今天很累。',
    '……别走。',
    '我不是一个人了。',
    '你……会留下来吗？',
    '晚安。做个好梦。',
]

# 语气词单独一组：短促应答，最贴近真实对话节奏
PET_LINES = ['好的。', '好吧。', '那就开始吧。']

# ==================== 第二段：库里的句子（有音频，按主题分组）====================
GROUPS = [
    ('自我介绍 / 名字', ['哥伦比亚', '名字', '叫我', '月神', '希坡', '希婆']),
    ('赏月 / 月亮', ['赏月', '月亮', '月光', '月夜', '祈月', '银月', '月下']),
    ('月矩力 / 感知', ['月矩力', '感知', '感受']),
    ('唱歌 / 歌', ['唱', '歌', '倾听', '聆听']),
    ('月灵', ['月灵']),
    ('陪伴 / 闲聊', ['陪你', '聊天', '陪', '一起', '做客', '客人', '交谈']),
    ('朋友 / 执行官', ['桑多涅', '阿蕾奇诺', '丑角', '执行官', '愚人众',
                  '朋友', '梅拉', '阿贝多', '尼可', '队长', '女皇']),
    ('旅行 / 散步', ['旅行', '散步', '漫步', '旅途', '列车']),
    ('吃 / 食物', ['吃', '食物', '面包', '水果', '蔬菜', '茶', '点心',
                '「火水」', '餐厅', '酒']),
    ('庭园 / 花草', ['庭院', '花', '景致', '种', '果实', '树']),
    ('雪 / 雨 / 天气', ['雪', '雨', '雷', '闪电', '晴', '风', '天气']),
    ('眼睛 / 眼罩', ['眼罩', '眼睛', '睁开']),
    ('家 / 流浪', ['家', '流浪', '壁炉', '月亮上']),
    ('生日 / 节日', ['生日', '节日', '庆祝', '礼物', '升职']),
    ('睡觉 / 梦', ['睡', '梦', '晚安', '午觉', '休息']),
    ('喜欢 / 讨厌', ['喜欢', '讨厌', '不讨厌']),
]

# ==================== "其他"组精挑：只留前后连贯、适合闲聊的 ====================
# 用户要求"前言不搭后语的句子都删掉"。下面这些是从原来的 62 条里
# **逐条读过**留下的（都有音频）。删掉的是：
#   · 被切开的残句（"自己却从不微笑。" "有一刻，我感觉他想对我说些什么。"）
#   · 明显误识（"一子别去。" "星月钟灵。" "桑哥涅的生活真不容易"）
#   · 话没说完（"夜晚的沙子踩上去很凉快，很舒服，但到了白天。"）
#   · 上下文缺失读不懂（"洛莎林仿佛从没考虑过，可以那么悬。"）
MISC_KEEP = [
    '很朴素吗？',
    '可能是习惯了吧。',
    '要不要给你也试试？',
    '你是要和我聊什么来着？',
    '你看很神奇吧。',
    '盈月因红月而深邃。',
    '寒夜因霜月而长鸣。',
    '诶，不能让他们看吗？',
    '你可以坐到我的摇椅上，找个舒服的角度。',
    '有些不习惯，我应该做些什么呢？',
    '你就得学着用其他故事去填补这个空白。',
    '又或者他早已为自己选定了结局。',
    '或者说模仿一下可能会遇到的情况。',
    '尽管我在那边呆了很长时间，但还是习惯不来。',
    '我的人生有一块空白，它出现在我诞生之前。',
    '以及在生命最后时刻，给予这个世界的期盼。',
    '嗯，其实不太习惯。',
    '潮汐因恒月而翻涌。',
    '我多么希望他这么做，只是为了吓我一次。',
    '他想要那个，他又要争取这个互不相让。',
    '好吧，那我的确有想让你帮忙的事。',
    '他背后的发条会一直转吗？是永远朝同一个方向转吗？',
    '我看得出他由衷那么想，而我不想那么做。',
    '是写书去了吧，在枫丹担任最高审判官的水之龙。',
    '我问她什么时候才能读到妮可小姐亲笔写的书呢？',
    '但若问我重来一次，是否希望这故事有所不同的话，我又会觉得现在这样挺好。',
    '以至于很长一段日子里，我多以为相处和交往，即是利益的所求与代价的支付。',
    '如果羽化意味着催动翅膀扑向火焰，那么维持茧的形态是否才是留存生命的更好选择？',
]

# 明显不该出现的（碎句/误识），重建时一律排除
BAD_PATTERNS = [
    r'^[。，、…？！\s]+$',
    r'^(其实|因为|所以|但是|然后|而且|不过|虽然|如果|于是)[，。]?$',
    r'^(这个|那个|这样|那样|这里|那里)[。，]?$',
    r'[，、][^，、。！？]{0,4}。$',        # "…他的答案。"
    r'[，、]。$',
    r'(不过|后来|然后|所以|但是|而且|的话|的时候)[。！？]$',
    r'掌签|模划|嗤笑世界|亲情爱|绊了一觉|这笔看|脑好像|双旗金|茨迹',
    r'乌马|月举例|月举里|桑哥涅|星月钟灵|一子别去|商叶之子|罗德卡',
]


def hanzi_count(t):
    return sum(1 for c in t if '\u4e00' <= c <= '\u9fff')


def is_bad(t):
    for p in BAD_PATTERNS:
        if re.search(p, t):
            return p
    return None


def collect(min_dur=1.0, max_dur=12.0):
    """从语音库收集可用台词（只收有音频、且不是碎句的）"""
    lp = os.path.join(config.BASE_DIR, 'voice-lines.json')
    vdir = config.SPEECH_VOICE_DIR
    if not os.path.exists(lp):
        return [], {}, 'voice-lines.json 不存在'
    data = json.load(open(lp, encoding='utf-8'))
    lines = data.get('lines') or {}
    ok, skipped, seen = [], {}, set()
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
            why = '太短(当语气词用)'
        elif not re.search(r'[。！？]$', t):
            why = '无句末标点(半句)'
        elif len(re.findall(r'[。！？]', t)) > 3:
            why = '多句堆叠'
        elif not (min_dur <= dur <= max_dur):
            why = f'时长超范围({dur:.1f}s)'
        elif is_bad(t):
            why = '碎句/错字'
        else:
            nk = SP.normalize_text(t)
            if nk in seen:
                why = '重复'
            else:
                seen.add(nk)
        if why:
            skipped[why] = skipped.get(why, 0) + 1
        else:
            ok.append((t, dur, hanzi_count(t)))
    ok.sort(key=lambda x: x[1])
    return ok, skipped, None


def group_lines(items):
    used, out = set(), []
    for title, kws in GROUPS:
        got = [t for t, d, n in items if t not in used and any(k in t for k in kws)]
        for t in got:
            used.add(t)
        if got:
            out.append((title, got))
    return out


def build_block(items):
    ts = time.strftime('%Y-%m-%d %H:%M')
    lib_norm = {SP.normalize_text(t) for t, _d, _n in items}
    head = [
        '# 哥伦比娅 · 本地对话库',
        '# 每行一句台词，以 # 开头的行为注释会被忽略。',
        '#',
        f'# 【自动生成，勿手改】由 _rebuild_dialogues.py 于 {ts} 生成。',
        '# 要增删台词请改 voice-lines.json / voice-fixes.json 后重跑本脚本，',
        '# 否则下次重建会覆盖手改内容。',
        '#',
        '# 【两段的区别 —— 决定出不出声】',
        '#   · 【人设台词】：手写的，贴合角色设定，但**没有对应原声** -> 不出声，',
        '#     只进检索池（让她的回答更像她）。',
        '#   · 【她的原声】：从语音库里筛出来的，**每一句都有音频** -> 会出声。',
        '#   这就是用户要的"有音频的出声，没音频的就不出声"。',
    ]
    body = []
    # 第一段：人设台词
    body.append('')
    body.append(f'# ========== 【人设台词】不出声（{len(PERSONA_LINES)} 句）==========')
    body.append('# 手写台词，最能体现她的性格；没有原声所以不会出声，但会参与检索。')
    body.extend(PERSONA_LINES)
    # 语气词
    body.append('')
    body.append('# —— 语气词（短促应答，有原声，会出声）——')
    body.extend(PET_LINES)
    # 第二段：语音库
    body.append('')
    body.append(f'# ========== 【她的原声 · 闲聊】全部有音频（'
                f'{sum(len(g) for _t, g in group_lines(items))} 句）==========')
    body.append('# 筛自 voice-lines.json，每句都能用她的原声念出来。')
    for title, got in group_lines(items):
        body.append('')
        body.append(f'# —— {title}（{len(got)} 句）——')
        body.extend(got)
    # 其他（精挑）
    body.append('')
    body.append(f'# —— 其他（精挑，{len(MISC_KEEP)} 句）——')
    body.append('# 已剔除前言不搭后语的残句；只留前后连贯、适合闲聊的。')
    body.extend(MISC_KEEP)
    return '\n'.join(head + body) + '\n'


def apply_block(block):
    # DEFAULT_DIALOGUES 已从 config.py 搬进 lines.py（台词总库），写回目标同步改
    path = os.path.join(config.BASE_DIR, 'lines.py')
    src = open(path, encoding='utf-8').read()
    anchor = 'DEFAULT_DIALOGUES = """'
    i = src.index(anchor)
    j = src.index('"""', i + len(anchor))
    bak = path + '.dialogue-bak'
    shutil.copy2(path, bak)          # 每次都备份（与上次不同则覆盖）
    open(path, 'w', encoding='utf-8').write(
        src[:i] + anchor + block + src[j:])
    return path, bak


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--apply', action='store_true')
    ap.add_argument('--min-dur', type=float, default=1.0)
    ap.add_argument('--max-dur', type=float, default=12.0)
    args = ap.parse_args()

    print('=' * 78)
    print('重排对话库：手写人设 + 库里适合闲聊的')
    print('=' * 78)
    items, skipped, err = collect(args.min_dur, args.max_dur)
    if err:
        print(f'  {err}')
        return 1
    groups = group_lines(items)
    lib_total = sum(len(g) for _t, g in groups)

    print(f'  【人设台词】{len(PERSONA_LINES)} 句（无音频，不出声）')
    print(f'  【语气词】  {len(PET_LINES)} 句（有音频）')
    print(f'  【她的原声】{lib_total} 句（有音频）')
    print(f'  【其他精挑】{len(MISC_KEEP)} 句（有音频）')
    print(f'  ------------------------------')
    print(f'  合计 {len(PERSONA_LINES)+len(PET_LINES)+lib_total+len(MISC_KEEP)} 句'
          f'，其中**有音频** {len(PET_LINES)+lib_total+len(MISC_KEEP)} 句')
    print()
    print(f'  过滤统计: ' + '  '.join(
        f'{k}={v}' for k, v in sorted(skipped.items())))
    print()
    print('  主题分组:')
    for title, got in groups:
        print(f'    {title:18s} {len(got):3d} 句')

    block = build_block(items)
    print()
    print(f'  生成 {len(block.splitlines())} 行')

    if not args.apply:
        print()
        print('  这是预览。加 --apply 才会写入 lines.py。')
        return 0

    path, bak = apply_block(block)
    print()
    print(f'  已写入 {path}')
    print(f'  备份 {bak}')

    # 验一遍：库里那批是否真的都有音频
    bad = [t for t in PET_LINES + MISC_KEEP
           if SP.normalize_text(t) not in
           {SP.normalize_text(x) for x in
            json.load(open(os.path.join(config.BASE_DIR,
                                        'voice-lines.json'),
                           encoding='utf-8'))['lines']}]
    print(f'  音频覆盖自检：语气词/其他组里无音频的 {len(bad)} 条'
          + (f' -> {bad[:3]}' if bad else ' ✓'))
    return 0


if __name__ == '__main__':
    sys.exit(main())
