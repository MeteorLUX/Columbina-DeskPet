# -*- coding: utf-8 -*-
"""
对话输入与大模型（DialogueMixin）
=========================
输入框回车/发送按钮的处理、唱歌口令拦截、
DeepSeek 调用（非流式）、本地台词库、系统提示词组装。

以 Mixin 形式工作，直接使用 DesktopPet 的属性与方法
（self.root / self.canvas / self.cfg / self._set_pose 等）。
"""
import os
import random
import threading
import time

# config 提供常量、文案、Config 与 HAS_* 能力标志
from config import *          # noqa: F401,F403
from lines import *          
try:
    import requests
except ImportError:
    requests = None


class DialogueMixin:
    """对话输入与大模型"""

    def on_send(self, event=None):
        text = self.entry.get().strip()
        if not text: return
        # 唱歌口令：上一首/下一首/暂停/继续/停止（生成中也能用，
        # 且只拦"当前状态真能执行"的口令，避免误吞正常聊天）
        if self._sing_state in ('playing', 'stopping', 'paused'):
            cmd = self._song_cmd_match(text)
            if cmd is not None and self._song_cmd_applicable(cmd):
                self.entry.delete(0, 'end')
                self._song_cmd_apply(cmd)
                return
        # 点歌拦截：不进 LLM，直接走唱歌流程（生成中也能点）
        if SONG_REQ_PAT.search(text):
            self.entry.delete(0, 'end')
            self._handle_song_request(text)
            return
        # 学习指令拦截：开始/暂停/结束/查看记录
        cmd = self._study_cmd_match(text)
        if cmd is not None:
            self.entry.delete(0, 'end')
            self._study_cmd_apply(cmd)
            return
        # 打开应用/网址拦截：只认"打开/启动/访问…"这类明确意图，
        # 且一定会弹确认框让你过目（见 launcher.py 的安全说明）
        if self.launch_handle_text(text):
            self.entry.delete(0, 'end')
            return
        # 时间/日期/天气：本地精准回答，不走大模型（0 延迟、不会瞎猜）
        fact = self.facts_answer(text)
        if fact:
            self.entry.delete(0, 'end')
            self._reset_idle_timer()
            self._speak(fact)
            return
        if self.is_generating: return
        self.entry.delete(0, 'end')
        self.is_generating = True
        self._reset_idle_timer()
        self._set_pose('work')
        self.walk_target_x = None

        self.messages.append({"role": "user", "content": text})
        if len(self.messages) > 20: self.messages = self.messages[-20:]

        mode = self.cfg['dialogue_mode']
        snapshot = list(self.messages)
        sys_prompt = self._build_system_prompt()

        def worker():
            self.root.after(0, lambda: self._set_pose('think'))
            try:
                # 神经语音什么文字都能念 -> 本地模式也不必迁就原声库：
                # 回复内容由人设+LLM 决定，语音直接念 LLM 输出。
                # 检索式回答只留给本地原声引擎（hybrid，有音频才出声）。
                use_llm = mode == 'deepseek' or \
                    getattr(self, '_speech_engine_name', '') == 'edge'
                reply = self._deepseek_call(sys_prompt, snapshot) \
                    if use_llm else self._local_reply()
            except Exception as e:
                reply = f"……（信号不太好。{e}）"
            self.root.after(0, lambda r=reply: self._on_reply(r))

        threading.Thread(target=worker, daemon=True).start()

    def _on_reply(self, reply):
        reply = (reply or '').strip()
        if reply:
            self.messages.append({"role": "assistant", "content": reply})
            if len(self.messages) > 20: self.messages = self.messages[-20:]
        self.is_generating = False
        self._set_pose('happy')
        self.root.after(800, lambda: self._set_pose('smile'))
        # 聊天也加好感
        self._add_affinity(1)
        # 留一条"今天聊过什么"的片段给日记用（内部会自己过滤长短）
        try:
            last_user = ''
            for m in reversed(self.messages):
                if m.get('role') == 'user':
                    last_user = m.get('content') or ''
                    break
            self.diary_note_exchange(last_user, reply)
        except Exception:
            pass
        self._speak(reply)

    def _build_system_prompt(self):
        master = self.cfg['master_name']
        a = int(self.cfg['affinity'])
        if a < 20:
            rel = f"你和「{master}」还比较生疏，保持一贯的疏离。"
        elif a < 50:
            rel = f"你对「{master}」已经稍微放下一点戒心。"
        elif a < 80:
            rel = f"你开始依赖「{master}」的陪伴，语气可以稍微柔和一些。"
        else:
            rel = f"「{master}」对你而言已经是「家」一样的存在，你愿意向他/她敞开心扉。"
        return (SYSTEM_PROMPT +
                f"\n\n【当前对话对象】\n你正在与「{master}」对话，请用「{master}」称呼对方。\n"
                f"【关系状态】好感度 {a}/100。{rel}"
                # 实时事实：不注入的话她答时间/天气只能靠猜（会笼统、会编造）
                + "\n\n" + self.facts_context())

    def _deepseek_call(self, sys_prompt, history):
        if not HAS_REQUESTS: return "……（没有安装 requests，我连接不上月亮。）"
        # 实时读 Key：from-import 拿到的是启动时快照，运行期间改 config.json 不生效
        api_key = current_api_key()
        if not api_key:
            return ("……（我还没有钥匙。请在 config.json 里填上 "
                    "deepseek_api_key，我才能听懂你。）")
        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
        payload = {"model": "deepseek-chat",
                   "messages": [{"role": "system", "content": sys_prompt}] + history,
                   "temperature": 0.95, "max_tokens": 200, "stream": False}
        try:
            resp = requests.post(DEEPSEEK_API_URL, headers=headers, json=payload, timeout=40)
            resp.raise_for_status()
            return resp.json()['choices'][0]['message']['content'].strip()
        except Exception as e:
            if getattr(e, 'response', None) is not None and e.response.status_code == 401:
                return ("……（这把钥匙好像失效了。请检查 config.json 里的 "
                        "deepseek_api_key 是不是填对了。）")
            return f"……（我找不到声音的来处。{e}）"

    # ---------- 本地对话 ----------
    def _ensure_dialog_file(self):
        if not os.path.exists(DIALOG_PATH):
            try:
                with open(DIALOG_PATH, 'w', encoding='utf-8') as f: f.write(DEFAULT_DIALOGUES)
            except Exception: pass

    def _load_local_dialogs(self):
        self._ensure_dialog_file()
        try:
            with open(DIALOG_PATH, 'r', encoding='utf-8') as f:
                return [l.strip() for l in f if l.strip() and not l.strip().startswith('#')]
        except Exception: return []

    def _local_reply(self):
        """本地模式回答：**拿用户说的话去语音库检索**（只用原声说话）。

        【调用前提】当前语音引擎是 hybrid（本地原声）。
          引擎是 Edge 神经语音时 on_send 不会走到这里——神经语音什么
          文字都能念，回复由人设+LLM 决定（见 on_send 的 use_llm 分流），
          不必再迁就"必须命中库里有音频的句子"。

        【为什么 hybrid 下不是随机挑】
          原来是 `random.choice(pool)`：随机挑一句固定台词。两个问题：
            1. 台词库里有相当一部分**没有对应音频** -> 她冒气泡但没声音
            2. 随机 = 答非所问（你问天气，她说"我习惯了有很多名字的日子"）

        【现在怎么做】
          用户要求"有音频的出声，没音频的就不出声，不要克隆"。
          所以把**用户说的话**直接拿去语音库做检索（字符二元组余弦相似度），
          返回最贴近的那句台词：
            · 相似度 >= SPEECH_RETRIEVAL_MIN -> 说这句（气泡文字 = 她说的那句）
            · 否则返回 '' -> 上层不出声、也不冒气泡

          这样**完全不联网、不碰 API key**，且气泡和声音必然一致。

        【返回 '' 的含义】
          调用方 `_on_reply` 遇到空串会跳过 `_speak`，所以既不冒气泡也不出声。
          这是有意设计：听不懂就安静，比答非所问好。
        """
        try:
            msgs = [m for m in self.messages if m.get('role') == 'user']
            user_text = (msgs[-1].get('content') or '').strip() if msgs else ''
        except Exception:
            user_text = ''

        if not user_text:
            # 没有用户输入（例如主动搭话）：从**有音频**的台词里随机挑一句
            return self._random_speakable()

        try:
            eng = self._speech if getattr(self, '_speech', None) is not None \
                else None
            if eng is None or not hasattr(eng, 'search'):
                return self._random_speakable()
            vid, line, score = eng.search(user_text)
        except Exception:
            return self._random_speakable()

        floor = float(SPEECH_RETRIEVAL_FLOOR)
        if line and score >= floor:
            self._last_match = (line, score)
            return line
        # 连最低线都没到 -> 什么都不说
        self._last_match = (None, score)
        return ''

    def _random_speakable(self):
        """从**确实有音频文件**的台词里随机挑一句（保证开口必有声）。

        为什么要过滤：DEFAULT_DIALOGUES 里有 69 句，但语音库只有一部分，
        不过滤的话会挑到没音频的句子 = 冒气泡没声音。
        """
        try:
            eng = getattr(self, '_speech', None)
            local = getattr(eng, 'local', None) or eng
            if local is None or not hasattr(local, 'search'):
                raise RuntimeError
            # 从语音库实际收录的台词里挑（这些一定有音频）
            texts = list(getattr(local, '_lines', {}) or {})
            if texts:
                return random.choice(texts)
        except Exception:
            pass
        pool = self._load_local_dialogs()
        return random.choice(pool) if pool else ''

    def _reload_dialogs(self):
        n = len(self._load_local_dialogs())
        self._speak(f"……嗯。我记住了 {n} 句话。")
