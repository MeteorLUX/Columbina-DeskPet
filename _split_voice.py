# -*- coding: utf-8 -*-
"""
切句 + 可选去 BGM（_split_voice.py）
====================================
把原始语音素材按静音停顿切成一句一段，产出本地语音库文件。

去 BGM 有两档（都先看效果再定）：
    --mode off     不处理，只切句（BGM 原样保留）
    --mode hp      高通滤波（BGM 集中在低频时够用，零依赖、极快）
    --mode gate    高通 + 静音门限（把句子头尾的低电平噪声压掉）

用法：
    python _split_voice.py --probe            # 只列出会切出哪些段（不写文件）
    python _split_voice.py --mode hp --limit 5   # 先切 5 段试听
    python _split_voice.py --mode hp          # 全量切
"""
import argparse
import json
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import config

SR = 44100          # 语音库输出采样率（与播放设备一致，避免重采样损失）


# ==================== 读 WAV（不依赖 soundfile）====================
def read_wav_mono(path, target_sr=SR):
    """读 PCM WAV -> float32 单声道 + 重采样。只支持 16-bit PCM（ffmpeg 输出的就是）"""
    import wave
    with wave.open(path, 'rb') as w:
        ch, sw, sr, n = (w.getnchannels(), w.getsampwidth(),
                         w.getframerate(), w.getnframes())
        raw = w.readframes(n)
    if sw != 2:
        raise ValueError(f'只支持 16-bit PCM，实际 {sw*8}-bit')
    a = np.frombuffer(raw, dtype='<i2').astype(np.float32) / 32768.0
    if ch > 1:
        a = a.reshape(-1, ch).mean(axis=1)
    if sr != target_sr:
        # 线性插值重采样（够用；语音不上高保真）
        n_out = int(len(a) * target_sr / sr)
        idx = np.linspace(0, len(a) - 1, n_out)
        a = np.interp(idx, np.arange(len(a)), a).astype(np.float32)
    return a


def write_wav(path, data, sr=SR):
    import wave
    d = np.clip(data, -1.0, 1.0)
    pcm = (d * 32767.0).astype('<i2').tobytes()
    with wave.open(path, 'wb') as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(pcm)


# ==================== 处理 ====================
def highpass(a, sr, cutoff=150, order=2):
    """二阶高通（Butterworth 双线性，纯 numpy 实现，不依赖 scipy）"""
    from math import tan, pi
    wc = tan(pi * cutoff / sr)
    k = wc * wc
    norm = 1 + wc * (2 ** 0.5) + k
    b0, b1, b2 = 1 / norm, -2 / norm, 1 / norm
    a1, a2 = 2 * (k - 1) / norm, (1 - wc * (2 ** 0.5) + k) / norm
    # 应用两次（order=2）
    out = a.astype(np.float64)
    for _ in range(max(1, order)):
        y = np.empty_like(out)
        x1 = x2 = y1 = y2 = 0.0
        for i in range(len(out)):
            x0 = out[i]
            y0 = b0 * x0 + b1 * x1 + b2 * x2 - a1 * y1 - a2 * y2
            x2, x1 = x1, x0
            y2, y1 = y1, y0
            y[i] = y0
        out = y
    return out.astype(np.float32)


def highpass_fast(a, sr, cutoff=150):
    """高通（频域实现，比逐样本 IIR 快几百倍，适合长音频）"""
    n = len(a)
    if n < 64:
        return a
    nfft = 1 << (n - 1).bit_length()
    spec = np.fft.rfft(a, nfft)
    freqs = np.fft.rfftfreq(nfft, 1 / sr)
    # 平滑过渡：cutoff 以下线性衰减到 0
    gain = np.clip((freqs - cutoff * 0.5) / (cutoff * 0.5), 0.0, 1.0)
    out = np.fft.irfft(spec * gain, nfft)[:n]
    return out.astype(np.float32)


def find_segments(a, sr, min_sil=0.35, min_speech=0.30, pad=0.12,
                  rel_db=25.0, max_len=12.0):
    """按静音切句，返回 [(起, 止)]（秒）

    max_len：有些语音连在一起没有停顿（实测最长一段 82 秒），
    超长的要在"最安静的位置"再切开，否则一句 82 秒没法当语音库用。
    """
    win = int(sr * 0.02)
    nf = len(a) // win
    if nf == 0:
        return []
    frames = a[:nf * win].reshape(nf, win)
    rms = np.sqrt((frames ** 2).mean(axis=1))
    db = 20 * np.log10(np.maximum(rms, 1e-9))
    speech_db = np.percentile(db, 90)
    thr = speech_db - rel_db
    voiced = db >= thr

    segs = []
    i = 0
    while i < nf:
        if voiced[i]:
            j = i
            last_voiced = i
            while j < nf:
                if voiced[j]:
                    last_voiced = j
                elif (j - last_voiced) * 0.02 >= min_sil:
                    break
                j += 1
            start = max(0, i * 0.02 - pad)
            end = min(len(a) / sr, (last_voiced + 1) * 0.02 + pad)
            if end - start >= min_speech:
                segs.append((start, end))
            i = j + 1
        else:
            i += 1

    # 超长段：在最安静处递归切开
    out = []
    for s, e in segs:
        out.extend(_cut_long(a, sr, s, e, db, max_len, min_speech))
    # 合并"被切开的半句"：短停顿把一句话切成两段时，合回去
    out = _merge_fragments(out)
    return out


def _merge_fragments(segs, gap=0.75, max_len=12.0):
    """把"只是换气"而被切开的半句合回一整句。

    为什么需要：静音切句只看停顿、不看语义。实测会切出
    「自己一直在流浪。」「月亮才是我的家。」这种半句 —— 单独当台词很怪，
    合起来才是完整的一句。

    启发式（纯声学，不依赖 ASR）：相邻两段之间的静音 < gap 秒 ->
    认为只是换气，继续拼接；否则断开。合并后不得超过 max_len。
    """
    if len(segs) < 2:
        return segs
    merged = []
    cur_s, cur_e = segs[0]
    for s, e in segs[1:]:
        silence = s - cur_e
        too_long = (e - cur_s) > max_len
        if silence >= gap or too_long:
            merged.append((cur_s, cur_e))
            cur_s, cur_e = s, e
        else:
            cur_e = e          # 换气停顿：继续拼接
    merged.append((cur_s, cur_e))
    return merged


def _cut_long(a, sr, s, e, db, max_len, min_speech):
    """把超过 max_len 的段在"最安静的中间位置"切开（递归）"""
    if e - s <= max_len:
        return [(s, e)]
    win = int(sr * 0.02)
    i0, i1 = int(s * sr / win), int(e * sr / win)
    if i1 - i0 < 4:
        return [(s, e)]
    mid_lo = i0 + int((i1 - i0) * 0.30)
    mid_hi = i0 + int((i1 - i0) * 0.70)
    if mid_hi <= mid_lo:
        return [(s, e)]
    # 在中间 40% 区域里找能量最低的帧作为切点
    k = mid_lo + int(np.argmin(db[mid_lo:mid_hi]))
    cut = k * 0.02
    left = _cut_long(a, sr, s, cut, db, max_len, min_speech)
    right = _cut_long(a, sr, cut, e, db, max_len, min_speech)
    return [x for x in (left + right) if x[1] - x[0] >= min_speech]


def find_raw_audio():
    """在 voice-raw/ 里找第一个音频文件作为默认输入。

    以前这里写死了具体的素材文件名 —— 那属于**素材来源信息**，
    不适合公开（本项目会上传 GitHub）。改成自动探测。
    """
    d = os.path.join(config.BASE_DIR, 'voice-raw')
    if not os.path.isdir(d):
        return ''
    for name in sorted(os.listdir(d)):
        if os.path.splitext(name)[1].lower() in (
                '.wav', '.m4a', '.mp3', '.flac', '.ogg'):
            return os.path.join(d, name)
    return ''


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--src', default=None,
                    help='输入音频（默认取 voice-raw/ 里第一个）')
    ap.add_argument('--out', default=config.SPEECH_VOICE_DIR)
    ap.add_argument('--mode', default='hp', choices=['off', 'hp', 'gate'])
    ap.add_argument('--cutoff', type=float, default=150.0)
    ap.add_argument('--limit', type=int, default=0, help='只处理前 N 段（试听用）')
    ap.add_argument('--probe', action='store_true')
    ap.add_argument('--index', default=None, help='索引 json 输出路径')
    args = ap.parse_args()
    if not args.src:
        args.src = find_raw_audio()
    if not args.src:
        print('  voice-raw/ 里没有音频文件。请先放一个，或用 --src 指定。')
        return 1

    print('=' * 68)
    print('切句 + 去 BGM')
    print('=' * 68)
    if not os.path.exists(args.src):
        print(f'  ✗ 找不到 {args.src}')
        return 1
    print(f'  源: {os.path.basename(args.src)}   模式: {args.mode}')

    print('  读取中...')
    a = read_wav_mono(args.src, SR)
    print(f'  ✓ {SR}Hz  {len(a)/SR/60:.2f} 分钟')

    if args.mode == 'hp':
        print(f'  高通滤波 cutoff={args.cutoff:.0f}Hz ...')
        a = highpass_fast(a, SR, args.cutoff)
    elif args.mode == 'gate':
        print(f'  高通滤波 cutoff={args.cutoff:.0f}Hz + 静音门限 ...')
        a = highpass_fast(a, SR, args.cutoff)

    segs = find_segments(a, SR)
    print(f'  切出 {len(segs)} 段')
    if segs:
        lens = np.array([e - s for s, e in segs])
        print(f'    段长: 中位 {np.median(lens):.2f}s  最短 {lens.min():.2f}s  '
              f'最长 {lens.max():.2f}s  合计 {lens.sum()/60:.1f} 分钟')
        print(f'    前 5 段: ' + ', '.join(f'{s:.1f}~{e:.1f}s' for s, e in segs[:5]))
    if args.probe:
        return 0

    os.makedirs(args.out, exist_ok=True)
    todo = segs[:args.limit] if args.limit else segs
    index = []
    pad = 6
    for k, (s, e) in enumerate(todo, 1):
        seg = a[int(s * SR):int(e * SR)].copy()
        if args.mode == 'gate':
            # 头尾 30ms 淡入淡出，避免爆音
            f = int(SR * 0.03)
            if len(seg) > f * 2:
                seg[:f] *= np.linspace(0, 1, f)
                seg[-f:] *= np.linspace(1, 0, f)
        peak = float(np.abs(seg).max())
        if peak > 0.95:
            seg *= 0.95 / peak
        name = f'v{k:04d}'
        write_wav(os.path.join(args.out, name + '.wav'), seg)
        index.append({'id': name, 'start': round(s, 3), 'end': round(e, 3),
                      'dur': round(e - s, 3)})

    ipath = args.index or os.path.join(config.BASE_DIR, 'voice-index.json')
    try:
        old = {}
        if os.path.exists(ipath):
            old = json.load(open(ipath, encoding='utf-8')) or {}
        old.setdefault('segments', [])
        old['segments'] = (old['segments'] + index) if args.limit \
            else index
        old['source'] = os.path.basename(args.src)
        old['mode'] = args.mode
        json.dump(old, open(ipath, 'w', encoding='utf-8'),
                  ensure_ascii=False, indent=1)
        print(f'  索引 -> {os.path.basename(ipath)}（{len(index)} 条）')
    except Exception as ex:
        print(f'  索引写入失败: {ex}')

    print(f'  ✓ 写出 {len(index)} 个文件到 {os.path.basename(args.out)}/')
    return 0


if __name__ == '__main__':
    sys.exit(main())
