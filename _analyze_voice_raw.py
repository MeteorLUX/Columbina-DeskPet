# -*- coding: utf-8 -*-
"""
素材分析（_analyze_voice_raw.py）
=================================
目的：在花 2.5GB 下分离模型**之前**，先判断这份原声素材：
  1. 能不能解码
  2. BGM 有多重（信噪比、静音段电平）
  3. 语音之间有没有静音间隔可以自动切句
  4. 频谱上 BGM 是否有明显可滤的频段（若能，就不用下模型）

用法：python _analyze_voice_raw.py [音频路径]
"""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import config
import miniaudio

SR = 16000          # 分析用采样率，够看语音特征且省内存


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
        config.BASE_DIR, 'voice-raw', 'BV1KkXPBREY2.m4a')
    print('=' * 68)
    print('素材分析')
    print('=' * 68)
    if not os.path.exists(path):
        print(f'  找不到文件: {path}')
        return 1
    print(f'  文件: {os.path.basename(path)}  {os.path.getsize(path)/1048576:.1f} MB')

    print('  解码中（27 分钟，请稍等）...')
    try:
        pcm = miniaudio.decode_file(path,
                                    output_format=miniaudio.SampleFormat.FLOAT32,
                                    nchannels=1, sample_rate=SR)
    except Exception as e:
        print(f'  ✗ 解码失败: {e}')
        return 1
    a = np.frombuffer(pcm.samples, dtype=np.float32).copy()
    dur = len(a) / SR
    print(f'  ✓ 解码成功  {SR}Hz  时长 {dur/60:.2f} 分钟  '
          f'样本 {len(a)}')
    print(f'  整体: 峰值 {np.abs(a).max():.3f}  RMS {np.sqrt((a**2).mean()):.4f}')

    # ---------- 1) 能量分布：找静音段 ----------
    win = int(SR * 0.02)                       # 20ms 一帧
    nf = len(a) // win
    frames = a[:nf * win].reshape(nf, win)
    rms = np.sqrt((frames ** 2).mean(axis=1))
    db = 20 * np.log10(np.maximum(rms, 1e-9))

    print()
    print('  【能量分布】')
    for q in (1, 5, 10, 25, 50, 75, 90, 99):
        print(f'    {q:2d}% 分位: {np.percentile(db, q):7.1f} dBFS')
    floor = np.percentile(db, 5)
    speech = np.percentile(db, 90)
    print(f'    底噪(5%) {floor:.1f} dBFS   语音(90%) {speech:.1f} dBFS   '
          f'动态范围 {speech-floor:.1f} dB')

    # 静音判定：比"语音电平"低 25dB 以下
    thr = speech - 25
    silent = db < thr
    print(f'    静音阈值 {thr:.1f} dBFS  ->  静音占比 {silent.mean()*100:.1f}%')

    # ---------- 2) 找静音段（可切句的间隔）----------
    print()
    print('  【静音间隔】（>=0.35s 的停顿，用来切句）')
    gaps = []
    i = 0
    while i < len(silent):
        if silent[i]:
            j = i
            while j < len(silent) and silent[j]:
                j += 1
            seg = (j - i) * 0.02
            if seg >= 0.35:
                gaps.append((i * 0.02, seg))
            i = j
        else:
            i += 1
    print(f'    找到 {len(gaps)} 处 >=0.35s 的停顿')
    if gaps:
        gs = np.array([g[1] for g in gaps])
        print(f'    停顿长度: 中位 {np.median(gs):.2f}s  最长 {gs.max():.2f}s')
        print(f'    前 8 处的位置/长度:')
        for pos, ln in gaps[:8]:
            print(f'      {pos/60:6.2f} 分  {ln:.2f}s')

    # ---------- 3) BGM 线索：静音段的频谱 ----------
    print()
    print('  【静音段里还剩什么】（这就是 BGM 的痕迹）')
    if silent.sum() > 100:
        # 取所有静音帧拼一段，做频谱
        idx = np.where(silent)[0][:400]
        seg = frames[idx].reshape(-1)
        if len(seg) > SR // 2:
            f = np.abs(np.fft.rfft(seg[:SR * 2] * np.hanning(min(len(seg), SR * 2)))) \
                if len(seg) >= SR * 2 else np.abs(np.fft.rfft(seg))
            freqs = np.fft.rfftfreq(len(f) * 2 - 2, 1 / SR)
            # 分频段看能量
            bands = [(0, 200, '低频(鼓/贝斯)'), (200, 800, '中低(人声基频)'),
                     (800, 2000, '中(人声清晰度)'), (2000, 6000, '中高(齿音)'),
                     (6000, 8000, '高频(镲/空气)')]
            tot = f.sum() + 1e-9
            for lo, hi, name in bands:
                m = (freqs >= lo) & (freqs < hi)
                print(f'    {name:18s} {f[m].sum()/tot*100:5.1f}%')
        print(f'    静音段 RMS: {rms[silent].mean():.5f} '
              f'({20*np.log10(max(rms[silent].mean(),1e-9)):.1f} dBFS)')
        print(f'    语音段 RMS: {rms[~silent].mean():.5f} '
              f'({20*np.log10(max(rms[~silent].mean(),1e-9)):.1f} dBFS)')
        snr = 20 * np.log10(max(rms[~silent].mean(), 1e-9)
                            / max(rms[silent].mean(), 1e-9))
        print(f'    => 信噪比约 {snr:.1f} dB')

    # ---------- 4) 结论 ----------
    print()
    print('=' * 68)
    print('  结论：')
    if silent.sum() > 100:
        snr = 20 * np.log10(max(rms[~silent].mean(), 1e-9)
                            / max(rms[silent].mean(), 1e-9))
        if snr > 25:
            print(f'    · 信噪比 {snr:.1f} dB 偏高 -> BGM 明显，**建议做人声分离**')
        else:
            print(f'    · 信噪比 {snr:.1f} dB -> BGM 较轻，可先试"不分离"直接切句')
    print(f'    · 可切出约 {len(gaps)} 段（按 >=0.35s 停顿）')
    print('=' * 68)
    return 0


if __name__ == '__main__':
    sys.exit(main())
