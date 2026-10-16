# -*- coding: utf-8 -*-
"""
说话输出（speech.py）
=====================
把气泡里的文字念出来。可开关，可换音色，可换引擎。

【为什么单独一层】
"文字 → 声音"有几种实现，音色和延迟差别巨大：
    hybrid  **只用原声**（默认）：有音频就出声，没音频就不出声
    local   本地原声（按精确文字查表）
    edge    Edge TTS 神经语音（联网，作为"想听个声"时的备选）
上层（speech_control.py）只认 SpeechEngine 接口，换引擎不用改 UI。

【关于语音克隆 —— 已从项目删除，记录原因以免重走】
  用户要求"有音频的出声，没音频的就不出声，不要克隆了"。
  之前试过两条合成路线，都不满意：
    · RVC 变声（晓晓 TTS -> 变声转换）：内容能保住 77%，但音色没转过来，
      听感"声音粘在一起"
    · XTTS v2 克隆（直接从参考音频克隆）：内容 100% 正确，
      但真实音色仍不像她
  最终取舍：**宁可安静，也不要用不像她的声音说话**。
  相关代码、模型与依赖均已移除。

【延迟策略】
  · 本地原声走"查表 + 解码"，实测 **0 延迟**（同采样率时无需重采样）
  · Edge TTS 需联网合成（约 1.5 秒/句），因此有句子级缓存与预取
"""
import hashlib
import os
import queue
import threading
import time

import numpy as np

from config import *          # noqa: F401,F403  常量、文案、Config

# ---- 可选依赖（缺失时优雅降级，不崩）----
try:
    import miniaudio
    HAS_MINIAUDIO = True
except ImportError:
    HAS_MINIAUDIO = False

try:
    import edge_tts
    HAS_EDGE_TTS = True
except ImportError:
    HAS_EDGE_TTS = False


def _char_ngrams(text, unigrams=False):
    """把文本转成 n-gram -> 次数。

    中文没有空格分词，所以用字符 n-gram（零依赖，不用装分词/向量库）。

    · **二元组**（默认）：捕捉"的世""世界"这类搭配，对整句相似度有效
    · **一元组**（unigrams=True）：单个汉字。加它是因为**关键词信号**
      常被二元组淹没 —— 实测 '我想听你唱歌' 只能匹配到 0.124，
      但库里明明有『我会尽量不在你睡着之后唱歌的』。
      加上"唱""歌"这类单字后，关键词就能把正确的句子顶上来。

    标点与空白先去掉，避免"。"单独造就假匹配。
    """
    s = ''.join(c for c in (text or '')
                if not c.isspace() and c not in '。，！？、…；：""''（）【】,.!?;:')
    out = {}
    if len(s) < 2:
        return {s: 1} if s else {}
    for i in range(len(s) - 1):
        g = '2:' + s[i:i + 2]
        out[g] = out.get(g, 0) + 1
    if unigrams:
        for c in s:
            g = '1:' + c
            out[g] = out.get(g, 0) + 1
    return out


# 保持向后兼容（旧调用方用 _char_bigrams）
_char_bigrams = _char_ngrams


# ==================== 统一接口 ====================
class SpeechEngine:
    """语音引擎接口。想接新引擎（含语音克隆）就继承它。

    实现要点：
      · synth() 返回 float32 单声道 numpy 数组（或 None 表示失败）
      · 抛异常没关系，上层会捕获并降级
    """
    name = 'base'
    label = '未命名'
    needs_network = False

    def available(self):
        """现在能不能用（依赖齐不齐）"""
        return False

    def voices(self):
        """可选音色列表 [(short_name, 中文描述), ...]"""
        return []

    def synth(self, text, voice=None, rate=None, volume=None):
        """合成一句话 -> (np.float32 单声道数组, 采样率)；失败返回 (None, 0)"""
        return None, 0


class EdgeTTSEngine(SpeechEngine):
    """Edge TTS 神经语音（微软，免费，需联网）"""

    name = 'edge'
    label = 'Edge 神经语音（需联网）'
    needs_network = True

    # 中文音色（列全一点，方便以后想换时直接挑）
    # 注意：用户明确"神经语音只要晓晓女声温柔自然"，
    # 所以 speech_control 的菜单默认**只展示晓晓**（见 SPEECH_VOICES_SHOWN）。
    ZH_VOICES = [
        ('zh-CN-XiaoxiaoNeural', '晓晓 · 女声 · 温柔自然'),
        ('zh-CN-XiaoyiNeural', '晓伊 · 女声 · 活泼'),
        ('zh-CN-YunxiNeural', '云希 · 男声 · 年轻'),
        ('zh-CN-YunyangNeural', '云扬 · 男声 · 播报'),
        ('zh-CN-YunjianNeural', '云健 · 男声 · 沉稳'),
        ('zh-CN-YunxiaNeural', '云夏 · 男声 · 少年'),
        ('zh-CN-liaoning-XiaobeiNeural', '晓北 · 女声 · 东北'),
        ('zh-CN-shaanxi-XiaoniNeural', '晓妮 · 女声 · 陕西'),
    ]

    def __init__(self):
        self._cache_dir = SPEECH_CACHE_DIR
        os.makedirs(self._cache_dir, exist_ok=True)

    def available(self):
        return HAS_EDGE_TTS

    def voices(self):
        """可选音色。用户只要晓晓，所以按 SPEECH_VOICES_SHOWN 过滤。"""
        shown = tuple(globals().get('SPEECH_VOICES_SHOWN') or ())
        if shown:
            return [v for v in self.ZH_VOICES if v[0] in shown]
        return list(self.ZH_VOICES)

    # ---------- 缓存 ----------
    def _cache_path(self, text, voice, rate, volume):
        key = f'{voice}|{rate}|{volume}|{text}'.encode('utf-8')
        return os.path.join(self._cache_dir, hashlib.md5(key).hexdigest() + '.mp3')

    # ---------- 合成 ----------
    def synth(self, text, voice=None, rate=None, volume=None):
        if not self.available():
            return None, 0
        voice = voice or SPEECH_VOICE
        rate = rate or SPEECH_RATE
        volume = volume or SPEECH_VOLUME
        text = (text or '').strip()
        if not text:
            return None, 0

        path = self._cache_path(text, voice, rate, volume)
        if not os.path.exists(path):
            try:
                import asyncio
                # 每次新建事件循环：合成在后台线程里跑，主线程的 loop 不能用
                asyncio.run(self._synth_to(edge_tts.Communicate(
                    text, voice, rate=rate, volume=volume), path))
            except Exception:
                return None, 0
        return decode_audio(path)

    @staticmethod
    async def _synth_to(comm, path):
        tmp = path + '.part'
        await comm.save(tmp)
        os.replace(tmp, path)


class LocalFileEngine(SpeechEngine):
    """本地原声引擎：优先按**台词文字**查 voice-lines.json（ASR 自动生成的映射）。

    映射怎么来的：`_transcribe_voice.py` 用项目内已装的 SenseVoice 模型
    把 `voice/` 下切好的原声段自动转成文字（403 段实测 100% 识别成功），
    产出 {台词文字: {id, dur}}。所以**不需要人工标注**。

    查找策略：
        1. 精确匹配台词文字 -> voice/<id>.wav
        2. 归一化后匹配（去标点、去空白、统一省略号）
        3. 退化：把传入的 text 当"台词 id"直接找同名文件
           （兼容早期用法 / 手动放的文件）
    """

    name = 'local'
    label = '本地原声（她的配音）'

    EXTS = ('.wav', '.mp3', '.flac', '.ogg', '.m4a')

    def __init__(self, folder=None, lines_path=None):
        self.folder = folder or SPEECH_VOICE_DIR
        self.lines_path = lines_path or SPEECH_LINES_PATH
        self._lines = None          # {原文: id}
        self._norm = None           # {归一化文字: id}
        self._bigrams = None        # 检索索引（惰性建）
        self._load_lines()

    # ---------- 台词索引 ----------
    def _load_lines(self):
        self._lines, self._norm = {}, {}
        self._bigrams = None        # 台词变了要重建检索索引
        try:
            if not os.path.exists(self.lines_path):
                return
            d = json.load(open(self.lines_path, encoding='utf-8')) or {}
            for text, rec in (d.get('lines') or {}).items():
                vid = rec.get('id') if isinstance(rec, dict) else rec
                if not vid:
                    continue
                self._lines[text] = vid
                self._norm[normalize_text(text)] = vid
        except Exception:
            self._lines, self._norm = {}, {}

    def reload_lines(self):
        self._load_lines()

    @property
    def line_count(self):
        return len(self._lines)

    def find_file(self, vid):
        for ext in self.EXTS:
            p = os.path.join(self.folder, vid + ext)
            if os.path.exists(p):
                return p
        return None

    def lookup(self, text):
        """按台词文字找语音文件路径；找不到返回 None"""
        t = (text or '').strip()
        if not t:
            return None
        vid = self._lines.get(t) or self._norm.get(normalize_text(t))
        if vid:
            p = self.find_file(vid)
            if p:
                return p
        # 退化：当作台词 id
        return self.find_file(t)

    # ---------- 检索式匹配（"只用原声说话"用这个）----------
    def _build_index(self):
        """为库里的每句台词建 n-gram 索引 + **IDF 权重**（只建一次）。

        为什么要 IDF：像"不错""的"这类字/词组在很多句里都出现，
        信息量低；而"唱歌""雪"这种只出现在少数句子里，才是真正的信号。
        IDF 把前者压下去、后者顶上来 —— 实测能让
        『我想听你唱歌』正确匹配到『我会尽量不在你睡着之后唱歌的』。
        """
        if self._bigrams is not None:
            return
        import math
        rows = []
        df = {}
        for text, vid in self._lines.items():
            g = _char_ngrams(text, unigrams=True)
            if g:
                rows.append((vid, text, g))
                for k in g:
                    df[k] = df.get(k, 0) + 1
        n = max(len(rows), 1)
        # 常见词降权但不为零（平滑）。IDF 存**原始值**，
        # 打分时按 (k, idf) 现查 —— 这样查询里的新字也能取到合理权重。
        idf = {k: math.log((n + 1) / (c + 0.5)) + 1.0 for k, c in df.items()}
        idx = []
        for vid, text, g in rows:
            # 库句侧存**原始 n-gram**，IDF 在打分时统一施加，
            # 避免"查询与库句用了不同 IDF 表"这种隐性不一致。
            idx.append((vid, text, set(g), len(g)))
        self._bigrams = idx
        self._df = df
        self._idf = idf
        self._n_docs = n

    def search(self, text):
        """在库里找**最贴近**的一句，返回 (vid, text, score)。

        score 是 **IDF 加权的 n-gram 覆盖率**（0~1，精确命中=1.0）。

        【为什么不是余弦相似度】（实测选型，见 _score_eval.py）
          第一版用 IDF 加权 n-gram 余弦，被两个缺陷坑了：
            · 余弦**偏向短句**（分母小）：「不错。」只有 2 字，
              却能从『今天天气不错我们出去走走吧』拿到 0.46 的高分，
              于是她会对一句无关的话回答"不错。"
            · 查询里的字若库里没有（如「累」），会被当成高信息量词，
              抬高查询范数、反而压低真实匹配的分数
          换成"**查询的 IDF 权重有多少落在这一句里**"（覆盖率）后：
            · 长短句失衡消失（分母是查询自身，与库句长短无关）
            · 2-gram 权重 0.75 / 1-gram 0.25 —— 连续的词更可信，
              单字只作补充（保证『我想听你唱歌』能命中『…唱歌的』）
          20 条带期望答案的评测里，覆盖率在关键词吻合上 10/10，
          且把『今天天气不错』从 0.46 正确压到 0.15。

        是否真的出声由调用方按阈值决定（SPEECH_RETRIEVAL_MIN）。
        """
        t = (text or '').strip()
        if not t or not self._lines:
            return None, None, 0.0
        # 先试精确命中（含归一化）
        exact = self._lines.get(t) or self._norm.get(normalize_text(t))
        if exact:
            return exact, t, 1.0
        self._build_index()
        if not self._bigrams:
            return None, None, 0.0
        g = _char_ngrams(t, unigrams=True)
        if not g:
            return None, None, 0.0
        # IDF 取值**必须用同一套公式现算**，不能对缺失词兜底成 1.0。
        # 踩过的坑：_idf 只收录语料里出现过的 n-gram，而查询里的新词
        # （如『代码』『写代』）不在表里，`.get(k, 1.0)` 会给它**弱权重 1.0**，
        # 可它其实很罕见、信息量最大。查询分母因此被压小，
        # 所有分数虚高约 2 倍（实测 0.234 -> 0.492），阈值就失效了。
        # 缺失词按 DF=0 现算，得到该公式下的最高权重（见 _idf_of）。
        q2 = sum(v * self._idf_of(k) for k, v in g.items()
                 if k.startswith('2:'))
        q1 = sum(v * self._idf_of(k) for k, v in g.items()
                 if k.startswith('1:'))
        tot = q2 * 0.75 + q1 * 0.25
        if tot <= 0:
            return None, None, 0.0
        best = (None, None, 0.0)
        for vid, line, w, wn in self._bigrams:
            # 库句侧同样用 **v * IDF** 计权（与 _score_eval.py 的 D 方案一致）
            c2 = sum(v * self._idf_of(k) for k, v in g.items()
                     if k.startswith('2:') and k in w)
            c1 = sum(v * self._idf_of(k) for k, v in g.items()
                     if k.startswith('1:') and k in w)
            s = (c2 * 0.75 + c1 * 0.25) / tot
            if s > best[2]:
                best = (vid, line, s)
        return best

    def _idf_of(self, key):
        """取某个 n-gram 的 IDF；**语料里没有的词按 DF=0 现算**（高权重）。

        不要写成 `self._idf.get(key, 1.0)` —— 那会让罕见的查询词拿到
        弱权重，把查询分母压小、分数整体虚高（实测差 2 倍）。
        """
        v = getattr(self, '_idf', {}).get(key)
        if v is not None:
            return v
        import math
        n = max(getattr(self, '_n_docs', 1), 1)
        return math.log((n + 1) / 0.5) + 1.0

    def search_audio(self, text, min_score=None):
        """检索并**直接返回音频**。低于阈值返回 (None, 0, vid, score)。

        这样调用方可以按"够接近才出声"的策略决定要不要真的说话。
        """
        if min_score is None:
            min_score = SPEECH_RETRIEVAL_MIN
        vid, line, score = self.search(text)
        if not vid or score < float(min_score):
            return None, 0, vid, score
        p = self.find_file(vid)
        if not p:
            return None, 0, vid, score
        d, sr = decode_audio(p)
        if d is None:
            return None, 0, vid, score
        return d, sr, vid, score

    def available(self):
        return os.path.isdir(self.folder)

    def voices(self):
        """把文件列表当"音色"展示，便于查看已有哪些"""
        try:
            names = sorted(os.path.splitext(f)[0]
                           for f in os.listdir(self.folder)
                           if f.lower().endswith(self.EXTS))
            return [(n, f'文件 {n}') for n in names]
        except Exception:
            return []

    def synth(self, text, voice=None, rate=None, volume=None):
        """text 既可以是**台词文字**，也可以是台词 id"""
        if not self.available():
            return None, 0
        p = self.lookup(text)
        if not p:
            return None, 0
        return decode_audio(p)


# ==================== 共享工具 ====================
def _ffmpeg_exe():
    """取项目内自带的 ffmpeg（imageio-ffmpeg，装在 vendor/，不写 C 盘）"""
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return None


def decode_with_ffmpeg(path, sr):
    """用 ffmpeg 解码成 float32 单声道（miniaudio 解不了时的兜底）。

    为什么需要：miniaudio 对某些**完全合法**的 WAV/M4A 会报
    'failed to decode file'（实测：ffmpeg 读同一个文件毫无错误）。
    所以解码失败要能切到 ffmpeg，而不是直接哑掉。
    """
    exe = _ffmpeg_exe()
    if not exe or not os.path.exists(exe):
        return None, 0
    import subprocess
    try:
        r = subprocess.run(
            [exe, '-v', 'error', '-i', path,
             '-f', 'f32le', '-ac', '1', '-ar', str(int(sr)), '-'],
            capture_output=True, timeout=120)
        if r.returncode != 0 or not r.stdout:
            return None, 0
        data = np.frombuffer(r.stdout, dtype='<f4').astype(np.float32).copy()
        if data.size == 0:
            return None, 0
        peak = float(np.abs(data).max())
        if peak > 0.98:
            data *= 0.98 / peak
        return data, int(sr)
    except Exception:
        return None, 0


def resample(data, sr_from, sr_to):
    """重采样（**抗混叠**）。sr 相同就原样返回。

    【为什么必须抗混叠 —— 实测数据，务必别退回线性插值】
      只要引擎输出采样率 ≠ 播放设备采样率（本机设备是 44.1kHz），就必须重采样。
      早期版本用线性插值，实测把 12k~22kHz 的**杂散能量做到 10.0%**
      （抗混叠只有 0.82%，线性插值是其 **12.2 倍**）。
      这些杂散频率听感是金属感/沙哑，用户描述为"说话粘在一起"。
      当时的线索是：只有输出 24kHz 的那个引擎听起来不对，
      而 44.1kHz 的音源采样率相同、根本不走这个函数。

    【实现】
      优先 scipy.signal.resample_poly（多相 FIR + 抗混叠，质量与速度都好）；
      没有 scipy 时退回线性插值（会有混叠，但至少不崩）。
    """
    if data is None or getattr(data, 'size', 0) == 0:
        return data
    sr_from, sr_to = int(sr_from), int(sr_to)
    if sr_from == sr_to or sr_from <= 0:
        return data
    n_out = int(len(data) * sr_to / sr_from)
    if n_out <= 1:
        return data

    arr = np.asarray(data, dtype=np.float32).reshape(-1)
    try:
        from scipy.signal import resample_poly
        import math
        g = math.gcd(sr_to, sr_from)
        up, down = sr_to // g, sr_from // g
        out = resample_poly(arr, up, down)
        return np.asarray(out, dtype=np.float32)[:n_out]
    except Exception:
        # 兜底：线性插值（无抗混叠，高频会有镜像）——只在没 scipy 时用
        idx = np.linspace(0, len(arr) - 1, n_out)
        return np.interp(idx, np.arange(len(arr)), arr).astype(np.float32)


def decode_audio(path, sample_rate=None):
    """解码音频文件 -> (float32 单声道数组, 采样率)。失败返回 (None, 0)

    注意 sample_rate 必须是**整数**。别把 SPEECH_RATE 传进来 ——
    那是 Edge TTS 的语速字符串（'+0%'），会让 miniaudio 抛
    TypeError: an integer is required。

    解码顺序：miniaudio（快）-> 失败则 ffmpeg 兜底（慢但兼容性好）。
    """
    sr = sample_rate or SPEECH_SAMPLE_RATE
    try:
        sr = int(sr)
    except (TypeError, ValueError):
        sr = SPEECH_SAMPLE_RATE
    if HAS_MINIAUDIO:
        try:
            pcm = miniaudio.decode_file(
                path, output_format=miniaudio.SampleFormat.FLOAT32,
                nchannels=1, sample_rate=sr)
            data = np.frombuffer(pcm.samples, dtype=np.float32).copy()
            if data.size:
                peak = float(np.abs(data).max())
                if peak > 0.98:
                    data *= 0.98 / peak
                return data, sr
        except Exception:
            pass
    # miniaudio 解不了（或有合法文件它挑食）-> 走 ffmpeg
    return decode_with_ffmpeg(path, sr)


def normalize_text(t):
    """归一化台词，提高匹配率。

    ASR 转出来的文字和代码里写的台词常在这些地方不一致：
      · 标点：'。' vs '．' vs 无标点；'……' vs '...'
      · 空白、全半角
    所以匹配前统一：去掉所有空白与标点、省略号归一、去尾部句号。
    """
    if not t:
        return ''
    s = str(t).strip()
    for a, b in (('……', ''), ('...', ''), ('…', ''),
                 ('，', ''), (',', ''), ('。', ''), ('.', ''),
                 ('！', ''), ('!', ''), ('？', ''), ('?', ''),
                 ('、', ''), ('「', ''), ('」', ''), ('『', ''),
                 ('』', ''), ('“', ''), ('”', ''), ('"', ''),
                 ("'", ''), ('　', ''), (' ', ''), ('\n', '')):
        s = s.replace(a, b)
    return s.lower()


# ==================== 引擎工厂 ====================
def make_engine(name=None):
    """按名字建引擎；未知名字回退到 edge

    'hybrid' = **只用原声**（默认）：有音频就出声，没音频就不出声。
        这是用户选定并保留的方案（原话："有音频的出声，没音频的就不出声了，
        不要克隆了"）。它内部用 LocalFileEngine + 检索，不做任何语音合成。
    'local'  = 本地原声（纯精确查表，不做检索）
    'edge'   = Edge TTS 神经语音（联网；想听个声时可切过去）
    'off'    = 关掉说话
    """
    name = (name or SPEECH_ENGINE or 'hybrid').lower()
    if name == 'local':
        return LocalFileEngine()
    if name == 'hybrid':
        return HybridEngine()
    if name == 'off':
        return None
    return EdgeTTSEngine()


def list_engines():
    """给菜单用：可选引擎（含不可用的，便于显示状态）。

    只有两个选项：
      · hybrid —— 本地原声（内含检索式匹配；没音频就不出声）【默认】
      · edge   —— Edge 神经语音（联网）
    'local'（纯精确查表，不检索）是 hybrid 的子集，属冗余旧选项，
    不再出现在菜单里；但 make_engine('local') 仍可用（老 config.json 兼容），
    且 HybridEngine 内部依旧复用 LocalFileEngine。
    """
    return [HybridEngine(), EdgeTTSEngine()]


class HybridEngine(SpeechEngine):
    """**只用原声**：有音频就出声，没音频就不出声。

    【用户的要求】
      "做有音频的出声（本地库可以多一点音频的句子…），
       没音频的就不出声了，不要克隆了"

    【为什么不做任何合成】（记录取舍原因，避免以后重走）
      之前试过两条克隆路线，都不满意：
        · RVC 变声（晓晓 TTS -> 变声）：内容能保住 77%，但音色没转过来
        · XTTS v2 克隆：内容 100% 正确，但真实音色仍不像她
      用户最终决定：**宁可安静，也不要用不像她的声音说话**。
      所以这里彻底不做合成，只播原声（相关代码与依赖已删除）。

    【和 LocalFileEngine 的区别】
      LocalFileEngine 是"按精确文字查表"，查不到就返回空。
      本类在此之上**加了检索**（`search()`）：文字不完全一样时，
      找库里最贴近的一句播出来。是否出声由相似度阈值决定，
      见 SPEECH_RETRIEVAL_MIN / SPEECH_RETRIEVAL_FLOOR。
    """

    name = 'hybrid'
    label = '只用原声（没音频就不出声）'

    def __init__(self):
        self.local = LocalFileEngine()

    # 兼容旧代码里对 .fallback 的访问（现在恒为 None）
    fallback = None

    @property
    def needs_network(self):
        return False                  # 完全不联网

    def available(self):
        return self.local.available()

    def voices(self):
        n = self.local.line_count
        return [('__local__', f'她的原声（{n} 句）')]

    def synth(self, text, voice=None, rate=None, volume=None):
        """有原声就返回，没有就 (None, 0) —— **不做任何合成**。

        注意这里**不加阈值**：能走到 synth 说明上层已经决定要出声了
        （气泡文字本来就在库里）。阈值判断在 `search_audio` / 上层
        `_local_reply` 里做，避免同一句话被判断两次。
        """
        d, sr = self.local.synth(text)
        if d is not None and getattr(d, 'size', 0) > 0:
            return d, sr
        return None, 0

    def search(self, text):
        """转给本地库的检索（`_local_reply` 用它按用户的话找台词）"""
        return self.local.search(text)

    def search_audio(self, text, min_score=None):
        """检索并返回音频；够不够近由调用方决定"""
        return self.local.search_audio(text, min_score=min_score)

    def hit_local(self, text):
        """这句话有没有**精确**的本地原声（给 UI/日志用）"""
        return self.local.lookup(text) is not None
