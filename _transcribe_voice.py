# -*- coding: utf-8 -*-
"""
原声自动转文字（_transcribe_voice.py）
======================================
用 SenseVoice（项目内已有的 228MB 离线模型）把切好的语音段**自动转成文字**，
产出"文字 -> 语音文件"的映射表。

这就是"不想自己标"的答案：
    原来要给 403 段原声人工标注台词，现在跑一遍 ASR 自动得到。
    以后说某句台词时，本地有对应原声就直接用原声（真·她配音），
    没有就走克隆/TTS。

用法：
    python _transcribe_voice.py                # 全部转写（403 段，约几分钟）
    python _transcribe_voice.py --limit 5      # 先试 5 段
    python _transcribe_voice.py --show         # 只打印结果，不写映射文件
    python _transcribe_voice.py --min-chars 2  # 过滤掉识别结果太短的
"""
import argparse
import json
import os
import sys
import time

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import config

SR = 16000          # SenseVoice 要 16k


def load_recognizer():
    """加载 SenseVoice 离线识别器（228MB int8 模型）"""
    import sherpa_onnx
    model = os.path.join(config.SPEECH_MODEL_DIR, 'model.int8.onnx')
    tokens = os.path.join(config.SPEECH_MODEL_DIR, 'tokens.txt')
    if not (os.path.exists(model) and os.path.exists(tokens)):
        return None
    try:
        return sherpa_onnx.OfflineRecognizer.from_sense_voice(
            model=model, tokens=tokens, num_threads=4,
            language='zh', use_itn=True, debug=False)
    except TypeError:
        # 老版本参数不同
        return sherpa_onnx.OfflineRecognizer.from_sense_voice(
            model=model, tokens=tokens, num_threads=4)


def read_wav_16k(path):
    """读音频 -> 16k float32 单声道（复用 speech.decode_audio，自带 ffmpeg 兜底）"""
    import speech as SP
    d, sr = SP.decode_audio(path, sample_rate=SR)
    return d


def transcribe(rec, wav_path):
    d = read_wav_16k(wav_path)
    if d is None or d.size == 0:
        return ''
    s = rec.create_stream()
    s.accept_waveform(SR, d)
    rec.decode_stream(s)
    return (getattr(s.result, 'text', '') or '').strip()


def clean(text):
    """去掉 SenseVoice 可能带的情感/事件标记（如 <|NEUTRAL|>、<|Speech|>）"""
    import re
    t = re.sub(r'<\|[^|]*\|>', '', text or '')
    t = t.replace(' ', '')
    return apply_corrections(t.strip())


# ==================== ASR 勘误 ====================
# SenseVoice 对**专有名词**很容易听错（它对游戏人名/设定没有领域知识）。
# 这些错字会直接进气泡显示，所以必须修。
#
# 勘误表放在 voice-fixes.json，**不是硬编码在这里**：
#   · 你可以随时加条目，不用改代码
#   · 每条都写清"原文 -> 修正 + 依据"，避免误改
# 格式：{"原文": "修正后"}
#
# 修正发生在**转写时**，因此 voice-lines.json 里存的就是正确文字，
# 检索、显示、台词库三处口径一致（不会出现"文字对了音频却对不上"）。
FIXES_PATH = os.path.join(config.BASE_DIR, 'voice-fixes.json')


def load_fixes():
    """读勘误表；没有就返回空（不报错，正常跑）"""
    try:
        if os.path.exists(FIXES_PATH):
            d = json.load(open(FIXES_PATH, encoding='utf-8'))
            # 允许带注释字段（以 _ 开头），只取字符串对
            return {k: v for k, v in d.items()
                    if not k.startswith('_') and isinstance(v, str)}
    except Exception as e:
        print(f'  ⚠ 勘误表读取失败（忽略）: {e}')
    return {}


_FIXES = None


def apply_corrections(text):
    """对转写结果套用勘误表（全局替换，长词优先避免部分覆盖）"""
    global _FIXES
    if _FIXES is None:
        _FIXES = load_fixes()
        if _FIXES:
            print(f'  已加载 ASR 勘误表 {len(_FIXES)} 条')
    if not text or not _FIXES:
        return text
    # 长 key 先替换，避免"双月之子"里的"月之子"被短 key 先改掉
    for wrong in sorted(_FIXES, key=len, reverse=True):
        if wrong in text:
            text = text.replace(wrong, _FIXES[wrong])
    return text


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--limit', type=int, default=0)
    ap.add_argument('--show', action='store_true')
    ap.add_argument('--min-chars', type=int, default=1)
    ap.add_argument('--index', default=os.path.join(config.BASE_DIR,
                                                    'voice-index.json'))
    ap.add_argument('--out', default=os.path.join(config.BASE_DIR,
                                                  'voice-lines.json'))
    ap.add_argument('--threads', type=int, default=4)
    args = ap.parse_args()

    print('=' * 68)
    print('原声自动转文字（SenseVoice）')
    print('=' * 68)

    if not os.path.exists(args.index):
        print(f'  ✗ 缺少索引 {args.index}（先跑 _split_voice.py）')
        return 1
    idx = json.load(open(args.index, encoding='utf-8'))
    segs = idx.get('segments') or []
    print(f'  索引: {len(segs)} 段（源 {idx.get("source")}）')
    if not segs:
        return 1

    print('  加载 SenseVoice 模型（228MB，首次约 5~15 秒）...')
    t0 = time.time()
    rec = load_recognizer()
    if rec is None:
        print('  ✗ 模型加载失败（检查 voice-model/model.int8.onnx 与 tokens.txt）')
        return 1
    print(f'  ✓ 模型就绪 {time.time()-t0:.1f}s')

    todo = segs[:args.limit] if args.limit else segs
    print(f'  开始转写 {len(todo)} 段...')
    out = []
    t0 = time.time()
    ok = 0
    for k, seg in enumerate(todo, 1):
        path = os.path.join(config.SPEECH_VOICE_DIR, seg['id'] + '.wav')
        if not os.path.exists(path):
            continue
        try:
            raw = transcribe(rec, path)
        except Exception as e:
            raw = ''
            if k <= 3:
                print(f'    [{k}] 转写异常: {type(e).__name__}: {e}')
        text = clean(raw)
        rec_item = {'id': seg['id'], 'text': text, 'dur': seg.get('dur'),
                    'start': seg.get('start')}
        out.append(rec_item)
        if text and len(text) >= args.min_chars:
            ok += 1
        if k % 20 == 0 or k == len(todo):
            el = time.time() - t0
            eta = el / k * (len(todo) - k)
            print(f'    {k:4d}/{len(todo)}  已用 {el:5.1f}s  '
                  f'预计还需 {eta:5.1f}s  有效 {ok}')
        if args.show and k <= 20:
            print(f'      {seg["id"]}: {text!r}')

    el = time.time() - t0
    print()
    print(f'  完成：{len(out)} 段，{el:.1f}s（平均 {el/max(1,len(out)):.2f}s/段）')
    eff = [r for r in out if r['text'] and len(r['text']) >= args.min_chars]
    print(f'  有文字: {len(eff)}/{len(out)} 段 '
          f'({len(eff)*100//max(1,len(out))}%)')

    if args.show:
        return 0

    # 按文字建映射（同一句可能有多段，保留时长最长的那段）
    mapping = {}
    for r in eff:
        t = r['text']
        if t not in mapping or (r.get('dur') or 0) > (mapping[t].get('dur') or 0):
            mapping[t] = r
    data = {'source': idx.get('source'), 'mode': idx.get('mode'),
            'count': len(mapping), 'lines': mapping}
    json.dump(data, open(args.out, 'w', encoding='utf-8'),
              ensure_ascii=False, indent=1)
    print(f'  映射表 -> {os.path.basename(args.out)}（{len(mapping)} 条唯一台词）')
    print()
    print('  样例（前 12 条）:')
    for i, (t, r) in enumerate(list(mapping.items())[:12]):
        print(f'    {r["id"]}  {r.get("dur")}s  {t!r}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
