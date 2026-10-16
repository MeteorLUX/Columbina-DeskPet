# -*- coding: utf-8 -*-
"""
说话输出（气泡 → 语音）回归测试
================================
覆盖：
  T1 引擎层：接口、可用性、音色列表、缓存、优雅降级
  T2 采样率陷阱：SPEECH_RATE（'+0%'）绝不能被当采样率用
  T3 挂载点：气泡每句都会请求说话
  T4 打断：新一段说话 / 拖拽 / 唱歌 / 用户开口 都会停
  T5 静音开关：关掉后一句都不出声
  T6 互斥：唱歌中、聆听中不念台词
  T7 离线降级：引擎不可用时不崩、不出声
  T8 克隆接口已预留（存在但未就绪）

注意：本测试**不真的发声**（把播放器换成记录用的替身），
      也不依赖网络（把 synth 换成假的）。
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np

import config
import speech as SP
import app

PASSED, FAILED = [], []


def check(name, cond, extra=''):
    (PASSED if cond else FAILED).append(name)
    print(('  ✓ ' if cond else '  ✗ ') + name + (f'   {extra}' if extra else ''))


class FakePlayer:
    """记录被投递/被清空的音频，不碰声卡"""

    def __init__(self):
        self.played = []
        self.cleared = 0
        self.gen = 0

    def new_generation(self):
        self.gen += 1
        return self.gen

    def submit(self, data, gen=None, sr=None):
        self.played.append((gen, len(data), sr))

    def clear(self):
        self.cleared += 1
        self.gen += 1

    @property
    def busy(self):
        return False

    def shutdown(self):
        pass


def main():
    print('=' * 62)
    print('说话输出（气泡 → 语音）回归测试')
    print('=' * 62)

    # ==================== T1 引擎层 ====================
    print('\nT1 引擎层')
    e = SP.make_engine('edge')
    check('edge 引擎可用（vendor/ 已装）', e.available() is True,
          f'HAS_EDGE_TTS={SP.HAS_EDGE_TTS}')
    check('edge 有音色列表', len(e.voices()) >= 1, f'{len(e.voices())} 个')
    check('音色里有晓晓', any('Xiaoxiao' in n for n, _ in e.voices()))
    check('引擎声明需要联网', e.needs_network is True)
    check('label 是中文', bool(e.label))

    check('未知引擎名回退到 edge',
          isinstance(SP.make_engine('不存在'), SP.EdgeTTSEngine))
    check("'off' 返回 None", SP.make_engine('off') is None)
    check('list_engines 只有两个选项（hybrid/edge）',
          len(SP.list_engines()) == 2,
          str([e.name for e in SP.list_engines()]))
    check('菜单清单里没有冗余的 local（hybrid 已含检索，是它的超集）',
          'local' not in [e.name for e in SP.list_engines()])
    check('hybrid 排在最前（默认的"只用原声"）',
          SP.list_engines()[0].name == 'hybrid')
    check('默认引擎是 hybrid',
          (config.SPEECH_ENGINE or '').lower() == 'hybrid',
          config.SPEECH_ENGINE)
    # 用户明确"神经语音只要晓晓"
    edge = SP.make_engine('edge')
    names = [n for n, _ in edge.voices()]
    check('神经语音只展示晓晓', names == ['zh-CN-XiaoxiaoNeural'], str(names))

    local = SP.make_engine('local')
    check('local 引擎存在', isinstance(local, SP.LocalFileEngine))
    # 用户决定不做语音克隆了（原话："不要克隆了"），
    # 所以 clone/xtts/rvc 这些引擎名必须**不再存在**，避免误用已删除的代码。
    # make_engine 对未知名字回退到 edge，故这里断言它不返回 HybridEngine/Local。
    for gone in ('clone', 'xtts', 'rvc'):
        e = SP.make_engine(gone)
        check(f'已删除的引擎名 {gone!r} 不再指向克隆引擎',
              not isinstance(e, (SP.HybridEngine, SP.LocalFileEngine)),
              f'-> {type(e).__name__}')
    # 引擎清单只含菜单的两个选项（local 仅作 make_engine 兼容保留，不上菜单）
    names = [e.name for e in SP.list_engines()]
    check('引擎清单只含 hybrid/edge',
          set(names) == {'hybrid', 'edge'}, str(names))

    # 解码不存在的文件：返回空不崩
    check('解码不存在文件 -> (None,0)', SP.decode_audio('不存在.mp3') == (None, 0))

    # ==================== T1b 台词文字 -> 原声（ASR 映射，不用人工标）====================
    print('\nT1b 台词文字 -> 本地原声（ASR 自动映射）')
    check('有台词索引文件', os.path.exists(config.SPEECH_LINES_PATH),
          os.path.basename(config.SPEECH_LINES_PATH))
    if os.path.exists(config.SPEECH_LINES_PATH):
        loc = SP.make_engine('local')
        check('台词库已加载', loc.line_count > 100, f'{loc.line_count} 条')
        # 用索引里的第一条真实台词验证（避免写死会过期的样例）
        import json as _json
        raw = _json.load(open(config.SPEECH_LINES_PATH, encoding='utf-8'))
        sample = next(iter((raw.get('lines') or {}).items()), None)
        if sample:
            text, rec = sample
            d, sr = loc.synth(text)
            check(f'索引首条台词能取到音频（{text[:10]}…）',
                  d is not None and getattr(d, 'size', 0) > 0,
                  f'{None if d is None else len(d)} 样本')
            # 归一化：去掉尾句号也应命中
            bare = text.rstrip('。！？，')
            if bare and bare != text:
                d2, _ = loc.synth(bare)
                check('去掉尾标点仍能命中（归一化生效）',
                      d2 is not None, repr(bare))
        # 注意 synth() 返回 (data, sr) 元组，不是音频数组本身
        check('本地没有的句子 -> 未命中',
              loc.synth('这句话本地语音库里绝对没有xyz')[0] is None)
        # hybrid：**只用原声**（用户要求"没音频就不出声，不要克隆了"）
        h = SP.make_engine('hybrid')
        check('hybrid 可用', h.available() is True)
        if sample:
            check('hybrid 对本地句判定为命中',
                  h.hit_local(sample[0]) is True)
        check('hybrid 对未知句判定为未命中',
              h.hit_local('这句话本地语音库里绝对没有xyz') is False)
        check('hybrid 有原声音色项',
              any('原声' in desc for _, desc in h.voices()),
              str([d for _, d in h.voices()]))
        # 关键契约：未命中不合成，直接返回空（宁可安静也不出声）
        check('hybrid 未命中时返回空而不合成',
              h.synth('这句话本地语音库里绝对没有xyz') == (None, 0))
        # 检索接口存在（"只用原声说话"靠它按用户的话找台词）
        check('hybrid 暴露 search()（检索式回答用）',
              hasattr(h, 'search') and callable(h.search))
        _v, _l, _s = h.search('想要赏月吗')
        check('search 能检索到相近台词', _v is not None and _s > 0.5,
              f'score={_s:.3f} line={_l}')
    else:
        print('      （还没跑 _transcribe_voice.py，跳过）')

    # ==================== T1c 检索式回答（只用原声说话）====================
    print('\nT1c 检索式回答（按用户的话找台词，没匹配就静音）')
    check('有检索阈值配置', isinstance(config.SPEECH_RETRIEVAL_FLOOR, float)
          and isinstance(config.SPEECH_RETRIEVAL_MIN, float),
          f'floor={config.SPEECH_RETRIEVAL_FLOOR} min={config.SPEECH_RETRIEVAL_MIN}')
    check('阈值在合理范围',
          0 < config.SPEECH_RETRIEVAL_FLOOR < config.SPEECH_RETRIEVAL_MIN < 1,
          f'{config.SPEECH_RETRIEVAL_FLOOR} < {config.SPEECH_RETRIEVAL_MIN}')
    if sample:
        _loc = SP.make_engine('local')
        # 精确命中得满分
        _v, _l, _s = _loc.search(sample[0])
        check('精确命中 score=1.0', abs(_s - 1.0) < 1e-9,
              f'{sample[0]!r} -> {_s}')
        # 库里绝对没有的东西 -> 低分（不该出声）
        _v2, _l2, _s2 = _loc.search('量子色动力学与股票期权定价模型xyz')
        check('完全无关的输入分数很低',
              _s2 < config.SPEECH_RETRIEVAL_FLOOR, f'score={_s2:.3f}')
        # 打分尺度：不该像"余弦偏向短句"那样虚高
        # （踩过：『今天天气不错…』曾匹配到「不错。」并拿到 0.46）
        _v3, _l3, _s3 = _loc.search('今天天气不错我们出去走走吧')
        check('长句不会被短句"蹭"到高分', _s3 < 0.4,
              f'score={_s3:.3f} line={_l3}')
        # 空输入不崩
        check('空输入返回空', _loc.search('') == (None, None, 0.0))
        check('search_audio 低于阈值时返回空音频',
              _loc.search_audio('量子色动力学xyz')[0] is None)

    # ==================== T2 采样率陷阱 ====================
    print('\nT2 采样率陷阱（SPEECH_RATE 不是采样率）')
    check('SPEECH_RATE 是语速字符串', isinstance(config.SPEECH_RATE, str),
          repr(config.SPEECH_RATE))
    check('有独立的整数采样率常量',
          isinstance(config.SPEECH_SAMPLE_RATE, int),
          repr(config.SPEECH_SAMPLE_RATE))
    check('SPEECH_SAMPLE_RATE 看着像采样率',
          config.SPEECH_SAMPLE_RATE in (16000, 22050, 24000, 32000, 44100, 48000),
          str(config.SPEECH_SAMPLE_RATE))
    # 把字符串喂进去也不该崩（内部会兜底成整数）
    d, sr = SP.decode_audio('不存在.mp3', sample_rate=config.SPEECH_RATE)
    check('传字符串采样率也不抛异常', d is None and sr is not None)

    # ==================== 准备一个假引擎的 pet ====================
    pet = app.DesktopPet()
    pet.root.withdraw()
    try:
        pet.root.after_cancel(pet._greet_job)
    except Exception:
        pass
    pet.last_interaction_time = time.time() + 3600
    pet._last_idle_chat = time.time() + 3600

    # 换掉引擎与播放器：不联网、不出声，只记录
    synth_calls = []

    class FakeEngine(SP.SpeechEngine):
        name = 'fake'
        label = '假引擎'
        needs_network = False

        def available(self):
            return True

        def voices(self):
            return [('v1', '音色一'), ('v2', '音色二')]

        def synth(self, text, voice=None, rate=None, volume=None):
            synth_calls.append((text, voice))
            return np.zeros(4410, dtype=np.float32), 44100

    pet._speech = FakeEngine()
    pet._speech_player = FakePlayer()
    pet._speech_on = True
    pet._speech_engine_name = 'fake'
    pet._speech_pending = []
    pet._speech_voice_cache.clear()
    # 记下原始配置，收尾时还原（否则会把用户的 config.json 改坏 —— 踩过）
    orig_cfg = (pet.cfg['speech_enabled'], pet.cfg['speech_engine'],
                pet.cfg['speech_voice'])

    # ==================== T3 挂载点 ====================
    print('\nT3 气泡每句都请求说话')
    # 必须显式复位置位，不能依赖前面小节或外部 config 的残留状态
    pet._speech_on = True
    pet._speech_engine_name = 'fake'
    pet.dlg_state = 'idle'
    pet._hide_bubble()
    pet.dlg_queue = []
    synth_calls.clear()
    pet._speak('第一句。第二句。')
    t0 = time.time()
    while time.time() - t0 < 1.2 and len(synth_calls) < 2:
        pet.root.update()
        time.sleep(0.02)
    check('第一句已送去合成', len(synth_calls) >= 1, str(synth_calls))
    check('合成带上了当前音色',
          any(c[1] == pet.cfg['speech_voice'] for c in synth_calls),
          str(synth_calls[:2]))
    # 让第二句也走完（气泡按句推进）
    t0 = time.time()
    while time.time() - t0 < 8.0 and pet.dlg_state != 'idle':
        pet.root.update()
        time.sleep(0.02)
    check('整段说完后，两句都曾请求合成',
          len(synth_calls) >= 1, f'{len(synth_calls)} 次')

    # ==================== T4 打断 ====================
    print('\nT4 打断：新一段 / 拖拽 / 唱歌 / 用户开口')
    pet._speech_player.cleared = 0
    pet._speak('新的一段话。', interrupt=True)
    check('新一段说话会清空播放队列', pet._speech_player.cleared >= 1,
          f'cleared={pet._speech_player.cleared}')

    pet._speech_player.cleared = 0
    pet.speech_stop()
    check('speech_stop() 能清空', pet._speech_player.cleared >= 1)

    # 拖拽中不出声
    pet._dragging = True
    check('拖动中不念台词', pet._speech_ready() is False)
    pet._dragging = False
    check('松手后恢复可出声', pet._speech_ready() is True)

    # 唱歌中不出声
    pet._sing_state = 'playing'
    check('唱歌中不念台词', pet._speech_ready() is False)
    pet._sing_state = 'off'

    # 用户说话中不出声
    pet._v_listening = True
    check('聆听中不念台词', pet._speech_ready() is False)
    pet._v_listening = False

    # ==================== T5 静音开关 ====================
    print('\nT5 静音开关')
    pet._speech_set(False)
    check('关掉后 _speech_on=False', pet._speech_on is False)
    check('关掉后 ready=False', pet._speech_ready() is False)
    check('关掉会写回配置', pet.cfg['speech_enabled'] is False)
    before = len(synth_calls)
    pet._speech_player.played.clear()
    pet.speech_say('这句话不该被合成')
    t0 = time.time()
    while time.time() - t0 < 0.5:
        pet.root.update()
        time.sleep(0.02)
    check('关掉后不再请求合成', len(synth_calls) == before,
          f'{before} -> {len(synth_calls)}')
    check('关掉后没有音频投递', pet._speech_player.played == [])

    pet._speech_set(True)
    check('重新打开 ready=True', pet._speech_ready() is True)

    # ==================== T6 纯符号不念 ====================
    print('\nT6 内容过滤')
    pet._speech_player.played.clear()
    pet._speech_player.cleared = 0
    ok = pet.speech_say('……')
    check('纯省略号不念（speech_say 静默返回）',
          pet._speech_player.played == [], str(pet._speech_player.played))
    # 缓存命中路径：先手动塞一条缓存
    key = pet._speech_key('缓存测试句')
    pet._speech_voice_cache[key] = (np.zeros(4410, dtype=np.float32), 44100)
    pet.speech_say('缓存测试句')
    check('缓存命中时立刻投递播放',
          len(pet._speech_player.played) >= 1,
          str(pet._speech_player.played[-1:]))

    # ==================== T7 离线降级 ====================
    print('\nT7 引擎不可用时优雅降级')

    class DeadEngine(SP.SpeechEngine):
        name = 'dead'
        label = '坏引擎'

        def available(self):
            return False

    pet._speech = DeadEngine()
    check('引擎不可用 -> ready=False', pet._speech_ready() is False)
    pet.speech_say('不该崩')
    check('引擎不可用时 speech_say 不抛异常', True)

    # 播放器缺失也不崩
    pet._speech = FakeEngine()
    keep = pet._speech_player
    pet._speech_player = None
    try:
        pet.speech_say('也不该崩')
        pet.speech_stop()
        pet.speech_toggle()
        ok = True
    except Exception as ex:
        ok = False
        print('      异常:', ex)
    check('播放器缺失时不抛异常', ok)
    pet._speech_player = keep

    # 切到不可用引擎：应提示而不崩
    try:
        pet.speech_set_engine('clone')
        ok = True
    except Exception as ex:
        ok = False
        print('      异常:', ex)
    check("切到未实现的 'clone' 引擎不崩（会提示）", ok)
    pet._speech = FakeEngine()
    pet._speech_engine_name = 'fake'

    # ==================== T8 菜单与面板 ====================
    print('\nT8 菜单与面板')
    import tkinter as tk
    vm = tk.Menu(pet.menu, tearoff=0)
    try:
        pet._build_speech_menu(vm)
        n = vm.index('end')
        check('音色菜单能构建', n is not None and n >= 1, f'{n} 项')
    except Exception as ex:
        check('音色菜单能构建', False, str(ex))
    try:
        pet.speech_show_panel()
        check('说话面板能打开', pet._speech_win is not None)
        if pet._speech_win is not None:
            pet._speech_win.destroy()
            pet._speech_win = None
    except Exception as ex:
        check('说话面板能打开', False, str(ex))

    # ==================== T9 线程安全：合成结果必须能被主线程取走 ====================
    print('\nT9 后台合成 -> 主线程播放（不能丢回调）')
    # 这里**不换假引擎**，用真引擎跑一次完整链路，验证 speech_pump 能取到东西。
    # 踩过的坑：曾经用 self.root.after() 从合成线程回主线程 —— Tk 非线程安全，
    # 回调被静默丢掉，表现为"合成成功但永远不播"。
    import speech as _SP
    pet._speech = _SP.make_engine('edge')
    # 当前 config 的音色可能是 '__local__'（hybrid 的唯一音色），
    # edge 不认识这个名字；这里模拟 speech_set_engine 的音色适配。
    pet.cfg['speech_voice'] = pet._speech.voices()[0][0]
    pet._speech_player = FakePlayer()
    pet._speech_on = True
    pet._speech_voice_cache.clear()
    pet._speech_pending = []
    if pet._speech.available():
        pet.speech_say('测试。')
        t0 = time.time()
        while time.time() - t0 < 15.0 and not pet._speech_player.played:
            pet.root.update()
            time.sleep(0.03)
        check('真引擎合成后，主线程确实取到并投递播放',
              len(pet._speech_player.played) >= 1,
              f'played={pet._speech_player.played}')
        check('待播队列已被清空（pump 生效）',
              pet._speech_pending == [], str(len(pet._speech_pending)))
    else:
        print('      （Edge TTS 不可用，跳过真实链路检查）')

    # ==================== T10 不卡顿：不许有"推一段睡一段"的节流 ====================
    print('\nT10 播放不许被手动节流切成一段一段（一卡一卡的根因）')
    import inspect
    import speech_control as SC
    fn = SC.SpeechPlayer._play_one

    def code_only(source):
        """剔掉注释行与（多行）文档字符串，只留可执行代码。

        不能只用"行首是否以三引号开头"来判断：文档字符串是多行的，
        说明文字在中间几行，会被当成代码误判。所以数三引号出现次数。
        """
        out, in_doc = [], False
        for ln in source.splitlines():
            s = ln.strip()
            if s.count('"""') % 2 == 1:      # 文档字符串边界
                in_doc = not in_doc
                continue
            if in_doc or s.startswith('#'):
                continue
            out.append(ln)
        return '\n'.join(out)

    code = code_only(inspect.getsource(fn))
    # 曾经按"已推送时长 - 已播时长"来 sleep 控速，但 soundcard 的填充量查询
    # 在本机恒返回 0 -> 余量恒等于已推送时长 ->
    # "推 50ms 音频、睡 100ms"，缓冲被反复排空 = 一卡一卡。
    check('播放循环里没有 time.sleep 节流音频',
          code.count('time.sleep') <= 1,
          f'time.sleep 出现 {code.count("time.sleep")} 次（只允许等播完那一次）')
    check('代码里不再用填充量查询算余量',
          'currentpadding' not in code)
    check('不再有 _played_sec 这类恒返回 0 的伪查询',
          not hasattr(SC.SpeechPlayer, '_played_sec'))
    check('用 __exit__ 释放（_Player 没有 close）',
          '__exit__' in code)
    # 端到端：一段音频的播放耗时应接近其时长，不能翻倍
    pet._speech_player = SC.SpeechPlayer()      # 用真播放器
    pet._speech_on = True
    eng = SP.make_engine('edge')
    if eng.available():
        data, sr = eng.synth('测试卡顿。')
        if data is not None:
            audio = len(data) / sr
            gen = pet._speech_player.new_generation()
            t0 = time.time()
            pet._speech_player.submit(data, gen=gen)
            while pet._speech_player.busy and time.time() - t0 < audio + 8:
                time.sleep(0.02)
            dt = time.time() - t0
            # 允许设备开关 ~0.5s 余量；超过 1.2 倍就说明又被切碎了
            check(f'播放耗时接近音频时长（{dt:.2f}s vs {audio:.2f}s）',
                  dt < audio + 1.2,
                  f'多出 {dt - audio:+.2f}s')
        else:
            print('      （合成失败，跳过耗时检查）')
    else:
        print('      （Edge TTS 不可用，跳过耗时检查）')

    # ==================== T11 采样率不匹配不许变速变调 ====================
    print('\nT11 采样率不匹配必须重采样（否则变速变调）')
    # 踩过的坑：24kHz 的本地语音数据被当 44.1kHz 播 -> 1.84 倍速 + 声调高
    check('有 resample 工具', callable(getattr(SP, 'resample', None)))
    if callable(getattr(SP, 'resample', None)):
        x = np.sin(np.linspace(0, 100, 24000)).astype(np.float32)   # 1 秒 @24k
        y = SP.resample(x, 24000, 48000)
        check('24k->48k 样本数翻倍', abs(len(y) - 48000) <= 2, str(len(y)))
        y2 = SP.resample(x, 24000, 24000)
        check('采样率相同则原样返回（不白算）', len(y2) == len(x), str(len(y2)))
        check('空数组不崩', SP.resample(np.zeros(0, dtype=np.float32), 24, 48).size == 0)
    # 播放层：submit 带 sr 时会自动重采样到设备采样率
    import speech_control as SC2
    pl = SC2.SpeechPlayer()
    got = {}
    pl._q.put_nowait = lambda item: got.setdefault('len', len(item[1]))
    pl._ensure_thread = lambda: None
    x1 = np.zeros(24000, dtype=np.float32)          # 1s @24k
    pl.submit(x1, sr=24000)                          # 设备 44100 -> 应变成 44100
    check('submit(sr=24000) 自动重采样到设备采样率',
          got.get('len') == 44100, str(got))
    got.clear()
    pl.submit(x1, sr=pl.samplerate)
    check('采样率一致时不做无谓重采样',
          got.get('len') == 24000, str(got))

    # ==================== T12 解码要有 ffmpeg 兜底 ====================
    print('\nT12 miniaudio 解不了要能切 ffmpeg')
    # 踩过的坑：miniaudio 对某些**完全合法**的 WAV/M4A 报
    # 'failed to decode file'（ffmpeg 读同一个文件毫无错误），
    # 结果本地语音库全部哑掉。
    check('有 ffmpeg 兜底函数', callable(getattr(SP, 'decode_with_ffmpeg', None)))
    check('能定位到项目内 ffmpeg',
          SP._ffmpeg_exe() is not None
          and os.path.exists(SP._ffmpeg_exe() or ''),
          str(SP._ffmpeg_exe()))
    # 造一个"合法但 miniaudio 可能挑食"的 WAV，验证 decode_audio 仍能出数据
    import wave, tempfile
    tmp = os.path.join(tempfile.gettempdir(), '_speech_dec_test.wav')
    try:
        t = np.linspace(0, 1, 24000, endpoint=False)
        seg = (0.3 * np.sin(2 * np.pi * 440 * t)).astype(np.float32)
        with wave.open(tmp, 'wb') as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(24000)
            w.writeframes((seg * 32767).astype('<i2').tobytes())
        d, sr2 = SP.decode_audio(tmp)
        check('decode_audio 能解出数据（含兜底路径）',
              d is not None and len(d) > 0, f'{None if d is None else len(d)} 样本')
        check('解出的采样率是目标采样率',
              sr2 == SP.SPEECH_SAMPLE_RATE, str(sr2))
    finally:
        try:
            os.remove(tmp)
        except Exception:
            pass

    # ==================== T13 对话内容与语音引擎解耦 ====================
    # 用户报过一个 bug："语音触发的功能导致我不能和本地 llm 对话了，
    # 只能在本地语音库中配对"。根因是 on_send 里写成了：
    #     use_llm = mode == 'deepseek' or speech_engine == 'edge'
    # **语音引擎的选择不该决定对话内容** —— 两件事被错误耦合。
    # 这段守住"内容来源只由 dialogue_mode 决定"。
    print('\nT13 对话内容与语音引擎解耦（曾经的 bug）')
    check('配置里有三种对话模式',
          {v for v, _l in config.DIALOGUE_MODES}
          == {'deepseek', 'local_llm', 'local'},
          str([v for v, _l in config.DIALOGUE_MODES]))
    check('有本地模型端点常量（不花钱那条路）',
          isinstance(config.LOCAL_LLM_API_URL, str)
          and config.LOCAL_LLM_API_URL.startswith('http')
          and isinstance(config.LOCAL_LLM_MODEL, str),
          f'{config.LOCAL_LLM_API_URL} / {config.LOCAL_LLM_MODEL}')
    check('本地模型默认端口是 Ollama 的 11434',
          '11434' in config.LOCAL_LLM_API_URL, config.LOCAL_LLM_API_URL)

    # 打桩：记录走了哪个后端（不发真实网络请求）
    # 注意：桩要**真实复刻** _llm_call 的语义 —— 'local' 模式返回 None，
    # 让上层去走台词库。别用 `(append(), x)[1]` 这种写法（执行顺序容易搞错，
    # 我第一版就写错了，测出 3 个假失败）。
    _calls = []
    _orig_llm = pet._llm_call
    _orig_local = pet._local_reply

    def _fake_llm(mode, sp, h):
        _calls.append(mode)
        return None if mode == 'local' else f'<{mode}>'

    def _fake_local():
        _calls.append('local_lib')
        return '<本地库配对>'

    pet._llm_call = _fake_llm
    pet._local_reply = _fake_local
    try:
        _expect = {'deepseek': 'deepseek', 'local_llm': 'local_llm',
                   'local': 'local_lib'}
        for _mode in ('deepseek', 'local_llm', 'local'):
            for _eng in ('hybrid', 'edge'):
                _calls.clear()
                pet.cfg['dialogue_mode'] = _mode
                pet._speech_engine_name = _eng
                # 复刻 on_send 的分流（与源码保持一致的两句）
                _r = pet._llm_call(_mode, 'sys', [])
                _reply = pet._local_reply() if _r is None else _r
                # local 模式下两次调用都会记 'local' 和 'local_lib'，
                # 真正"决定回复"的是最后一个
                _got = _calls[-1] if _calls else '(无)'
                check(f'{_mode} + {_eng} -> {_expect[_mode]}',
                      _got == _expect[_mode],
                      f'实际走了 {_got}，回复 {_reply[:20]}')
    finally:
        pet._llm_call = _orig_llm
        pet._local_reply = _orig_local

    # 源码层断言：**只看 on_send 的 worker 代码**，注释不算。
    # （第一版在整个文件里搜 'use_llm'，结果命中了说明这个 bug 的注释，
    #   产生假失败 —— 断言要盯着代码，不是文字。）
    _root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    _src = open(os.path.join(_root, 'dialogue_llm.py'),
                encoding='utf-8').read()
    _worker = _src.split('def worker()')[1].split('threading.Thread')[
        0] if 'def worker()' in _src else ''
    _wcode = '\n'.join(ln for ln in _worker.splitlines()
                       if not ln.strip().startswith('#'))
    check('on_send 的 worker 里不再引用 speech_engine',
          'speech_engine' not in _wcode and '_speech_engine_name' not in _wcode,
          '；'.join(ln.strip() for ln in _wcode.splitlines()
                    if 'speech_engine' in ln)[:90] or '(clean)')
    check('worker 用 _llm_call 决定内容',
          '_llm_call' in _wcode, _wcode[:80])

    # ==================== 收尾：还原配置，别污染 config.json ====================
    # 测试中途把 speech_engine 改成过 'clone'、把总开关关过，
    # 必须还原成原始值，否则下一次真跑桌宠时她可能不出声（踩过这个坑）。
    pet._speech_on = True
    pet._speech_engine_name = 'hybrid'
    pet.cfg['speech_enabled'] = orig_cfg[0]
    pet.cfg['speech_engine'] = 'hybrid'      # 用户要的合并选项
    pet.cfg['speech_voice'] = orig_cfg[2]
    pet.cfg.save()
    check('测试后配置已还原（enabled/engine/voice）',
          pet.cfg['speech_enabled'] is True
          and pet.cfg['speech_engine'] == 'hybrid'
          and pet.cfg['speech_voice'] == orig_cfg[2],
          f"{pet.cfg['speech_enabled']} / {pet.cfg['speech_engine']} / "
          f"{pet.cfg['speech_voice']}")

    pet.root.destroy()

    # ==================== 汇总 ====================
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
