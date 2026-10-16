# -*- coding: utf-8 -*-
"""
全双工语音模块 分层自测（临时验证用）
=====================================
不碰真实 API：LLM 流式解析用本地假 SSE 服务器验证。
覆盖：
  A. 代次/打断机制（barge-in 的核心）
  B. LLM 流式解析（SSE 增量 + 收尾）
  C. 打断时能真正中止流式读取
  D. VAD + 流式识别（需要模型，没有就跳过）
"""
import json
import os
import socket
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

# 本文件在 tests/ 下，所以项目根要**往上退一层**
BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

OK = True


def check(label, cond, extra=''):
    global OK
    if not cond:
        OK = False
    print(f"  [{'PASS' if cond else 'FAIL'}] {label} {extra}")
    return cond


# ======================================================== 本地假 SSE 服务器
class FakeSSE(BaseHTTPRequestHandler):
    """模拟 DeepSeek 的流式响应"""
    # 由测试设置
    pieces = ['……嗯。', '今天的月亮', '很安静呢。']
    delay = 0.06
    endless = False
    stop_after = None          # 到第几片后停住（测打断）

    def log_message(self, *a):
        pass

    def do_POST(self):
        n = int(self.headers.get('Content-Length', 0))
        try:
            self.rfile.read(n)
        except Exception:
            pass
        self.send_response(200)
        self.send_header('Content-Type', 'text/event-stream')
        self.send_header('Cache-Control', 'no-cache')
        self.end_headers()
        i = 0
        try:
            while True:
                if FakeSSE.stop_after is not None and i >= FakeSSE.stop_after:
                    time.sleep(0.02)
                    i += 1
                    if i > FakeSSE.stop_after + 400:      # 兜底，别真死循环
                        break
                    continue
                if FakeSSE.endless:
                    piece = f'字{i % 10}'
                else:
                    if i >= len(FakeSSE.pieces):
                        break
                    piece = FakeSSE.pieces[i]
                chunk = {'choices': [{'delta': {'content': piece}}]}
                self.wfile.write(f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n".encode('utf-8'))
                self.wfile.flush()
                time.sleep(FakeSSE.delay)
                i += 1
            self.wfile.write(b"data: [DONE]\n\n")
            self.wfile.flush()
        except Exception:
            # 客户端断开（打断）时会走到这里，属正常
            pass


def free_port():
    s = socket.socket()
    s.bind(('127.0.0.1', 0))
    p = s.getsockname()[1]
    s.close()
    return p


def start_fake_server():
    port = free_port()
    srv = HTTPServer(('127.0.0.1', port), FakeSSE)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    return srv, f'http://127.0.0.1:{port}/chat'


# ======================================================== A. 代次机制
def test_generation():
    print('=' * 62)
    print('A. 打断机制（代次 barge-in）')
    print('=' * 62)
    from voice_engine import FullDuplexVoice, VoiceCallbacks

    v = FullDuplexVoice(BASE, callbacks=VoiceCallbacks())
    g1 = v._current_gen()
    check('初始代次存在', isinstance(g1, int))

    g2 = v._bump_generation()
    check('代次自增', g2 > g1, f'{g1} -> {g2}')
    check('旧代次被判为过期', v._is_stale(g1) is True)
    check('新代次不是过期', v._is_stale(g2) is False)

    # cancel_generation 也应推进代次
    g3 = v.cancel_generation()
    check('cancel_generation 推进代次', g3 > g2, f'{g2} -> {g3}')


# ======================================================== B. SSE 流式解析
def test_llm_stream():
    print()
    print('=' * 62)
    print('B. LLM 流式解析（本地假服务器）')
    print('=' * 62)
    from voice_engine import FullDuplexVoice, VoiceCallbacks

    srv, url = start_fake_server()
    FakeSSE.pieces = ['……嗯。', '今天的月亮', '很安静呢。']
    FakeSSE.delay = 0.05
    FakeSSE.endless = False
    FakeSSE.stop_after = None

    deltas = []
    done = {}
    evt = threading.Event()

    def on_delta(d, full):
        deltas.append((d, full))

    def on_done(full):
        done['text'] = full
        evt.set()

    def on_err(m):
        done['error'] = m
        evt.set()

    v = FullDuplexVoice(BASE,
                        callbacks=VoiceCallbacks(on_reply_delta=on_delta,
                                                 on_reply_done=on_done,
                                                 on_error=on_err),
                        api_key='sk-fake-for-test',
                        system_prompt='你是哥伦比娅',
                        api_url=url,
                        params={'llm_timeout': 15})
    t0 = time.perf_counter()
    th = threading.Thread(target=v._reply_stream, args=('你好',), daemon=True)
    th.start()
    got = evt.wait(timeout=20)
    el = (time.perf_counter() - t0) * 1000

    check('收到回复完成回调', got, f'({el:.0f} ms)')
    check('没有报错', 'error' not in done, done.get('error', ''))
    expect = ''.join(FakeSSE.pieces)
    check('拼接结果正确', done.get('text') == expect, f'「{done.get("text")}」')
    check('增量逐片到达', len(deltas) == len(FakeSSE.pieces), f'{len(deltas)} 片')
    check('增量是累积的', deltas[-1][1] == expect)
    check('首字延迟已记录', v.stats['last_llm_first_ms'] > 0,
          f"{v.stats['last_llm_first_ms']:.0f} ms")
    print(f'       首片延迟 {v.stats["last_llm_first_ms"]:.0f} ms，全文耗时 {el:.0f} ms')
    srv.shutdown()
    return url


# ======================================================== C. 打断能真正中止
def test_barge_in():
    print()
    print('=' * 62)
    print('C. 打断能真正中止流式读取')
    print('=' * 62)
    from voice_engine import FullDuplexVoice, VoiceCallbacks

    srv, url = start_fake_server()
    FakeSSE.endless = True          # 无限输出，除非被打断
    FakeSSE.delay = 0.02
    FakeSSE.stop_after = None

    deltas = []
    done = {}
    errs = []
    started = threading.Event()

    def on_delta(d, full):
        deltas.append(full)
        started.set()

    v = FullDuplexVoice(BASE,
                        callbacks=VoiceCallbacks(on_reply_delta=on_delta,
                                                 on_reply_done=lambda f: done.setdefault('text', f),
                                                 on_error=lambda m: errs.append(m)),
                        api_key='sk-fake-for-test',
                        system_prompt='x', api_url=url,
                        params={'llm_timeout': 30})
    th = threading.Thread(target=v._reply_stream, args=('你好',), daemon=True)
    th.start()

    check('流式已开始', started.wait(timeout=10))
    time.sleep(0.4)
    n_before = len(deltas)
    v.cancel_generation()           # <<< 模拟用户开口打断
    th.join(timeout=6)
    time.sleep(0.3)

    check('打断后线程已结束', not th.is_alive())
    check('打断后不再产生增量', len(deltas) <= n_before + 2,
          f'打断前 {n_before} 片，最终 {len(deltas)} 片')
    check('被打断则不触发收尾回调', 'text' not in done)
    check('打断不算错误', not errs, str(errs[:1]))
    print(f'       打断前已收到 {n_before} 片，之后停在 {len(deltas)} 片')
    srv.shutdown()


# ======================================================== D. 真实模型链路
def test_real_models():
    print()
    print('=' * 62)
    print('D. VAD + 流式识别（真实模型）')
    print('=' * 62)
    import wave
    import numpy as np
    from voice_engine import FullDuplexVoice, VoiceCallbacks, HAS_SHERPA

    if not HAS_SHERPA:
        print('  [SKIP] 没有 sherpa-onnx')
        return
    v = FullDuplexVoice(BASE, callbacks=VoiceCallbacks())
    if not os.path.isdir(v.asr_dir):
        print(f'  [SKIP] 流式模型还没下载好: {v.asr_dir}')
        return
    ok, msg = v.load_models()
    check('模型加载成功', ok, msg)
    if not ok:
        return
    print(f'       VAD 窗口 {v._vad.window_size()} 样本')

    wav = os.path.join(BASE, 'voice-model', 'test_wavs', 'zh.wav')
    if not os.path.exists(wav):
        print('  [SKIP] 没有测试音频')
        return
    with wave.open(wav, 'rb') as w:
        sr = w.getframerate()
        n = w.getnframes()
        a = np.frombuffer(w.readframes(n), dtype='<i2').astype(np.float32) / 32768.0
    if sr != 16000:
        idx = np.linspace(0, len(a) - 1, int(len(a) * 16000 / sr))
        a = np.interp(idx, np.arange(len(a)), a).astype(np.float32)

    # 走一遍流式：边喂边取中间结果
    win = v._vad.window_size()
    stream = v._asr.create_stream()
    partials = []
    step = 1600
    t0 = time.perf_counter()
    for i in range(0, len(a), step):
        stream.accept_waveform(16000, a[i:i + step])
        while v._asr.is_ready(stream):
            v._asr.decode_stream(stream)
        txt = (v._asr.get_result(stream) or '').strip()
        if txt and (not partials or partials[-1] != txt):
            partials.append((i / 16000, txt))
    stream.input_finished()
    while v._asr.is_ready(stream):
        v._asr.decode_stream(stream)
    final = (v._asr.get_result(stream) or '').strip()
    el = time.perf_counter() - t0

    print(f'       解码 {len(a)/16000:.2f}s 音频耗时 {el*1000:.0f} ms '
          f'({len(a)/16000/max(el,1e-9):.1f}x 实时)')
    print('       中间结果:')
    for ts, t in partials[:8]:
        print(f'         [{ts:5.2f}s] {t}')
    print(f'       最终: 「{final}」')
    check('有中间结果（流式生效）', len(partials) >= 1, f'{len(partials)} 次')
    check('最终结果非空', bool(final))


def main():
    print('全双工语音模块 · 分层自测')
    print(f'Python: {sys.executable}')
    print()
    for fn in (test_generation, test_llm_stream, test_barge_in, test_real_models):
        try:
            fn()
        except Exception:
            import traceback
            print(f'  [异常] {fn.__name__}:')
            traceback.print_exc()
    print()
    print('=' * 62)
    print('结果:', 'ALL_PASS' if OK else 'SOME_FAILED')
    print('=' * 62)


if __name__ == '__main__':
    main()
