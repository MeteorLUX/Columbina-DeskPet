# -*- coding: utf-8 -*-
"""
配置与文案常量
==============
所有路径、数值常量、提示词、台词、情绪/疑问词表都集中在这里，
其它模块只从这里取，改参数不用翻业务代码。

这里还负责探测"可选依赖装了没有"（HAS_* 标志），
业务模块通过 `from config import *` 拿到，不必各自重复 try/except。
"""
import json
import os
import re
import sys

# ==================== 第三方包路径引导 ====================
# 本项目把第三方包装在项目内 vendor/（不写 C 盘用户目录）。
# **必须在任何 import edge_tts 之类的语句之前生效**，所以放在最顶部。
# 注意 BASE_DIR 在这之后才定义，这里用 __file__ 现算。
_PET_DIR = os.path.dirname(os.path.abspath(__file__))
_VENDOR = os.path.join(_PET_DIR, 'vendor')
if os.path.isdir(_VENDOR) and _VENDOR not in sys.path:
    sys.path.insert(0, _VENDOR)

# 台词总库：她说的一切话（人设提示词、本地对话库、摸头/唱歌/学习/打趣/
# 打开应用台词、日记素材……）都集中在 lines.py——要删直接删、改字直接改。
# 这里再导出一次，其它模块照旧 `from config import *` 就能拿到这些名字。
from lines import *          # noqa: E402,F401,F403

# ==================== 可选依赖探测 ====================
try:
    from PIL import Image, ImageTk, ImageOps
    HAS_PIL = True
except ImportError:
    HAS_PIL = False

try:
    import requests
    HAS_REQUESTS = True
except ImportError:
    HAS_REQUESTS = False

try:
    import numpy as np
    import soundcard as sc
    HAS_MIC = True
except ImportError:
    HAS_MIC = False

try:
    import sherpa_onnx
    HAS_SHERPA = True
except ImportError:
    HAS_SHERPA = False

try:
    import miniaudio
    HAS_SONG = True
except ImportError:
    HAS_SONG = False

# 全双工语音底座是否可用
try:
    from voice_engine import FullDuplexVoice, VoiceCallbacks
    HAS_FULL_DUPLEX = True
except ImportError:
    HAS_FULL_DUPLEX = False

# 说话输出（Edge TTS）是否可用 —— 装在项目内 vendor/，已在文件顶部加进 sys.path
try:
    import edge_tts
    HAS_EDGE_TTS = True
except ImportError:
    HAS_EDGE_TTS = False

# ==================== 路径 ====================
if getattr(sys, 'frozen', False):
    BASE_DIR = os.path.dirname(sys.executable)
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))

CONFIG_PATH = os.path.join(BASE_DIR, 'config.json')
CONFIG_KEY_FIELD = 'deepseek_api_key'      # API Key 存在 config.json 的这个字段里
DIALOG_PATH = os.path.join(BASE_DIR, 'dialogues.txt')
IMAGE_DIR   = os.path.join(BASE_DIR, 'images')
VOICE_MODEL_DIR = os.path.join(BASE_DIR, 'voice-model')
if not os.path.exists(IMAGE_DIR):
    IMAGE_DIR = BASE_DIR


# ==================== DeepSeek ====================
DEEPSEEK_API_URL = "https://api.deepseek.com/chat/completions"

# -------------------- 本地模型（不花钱的那条路）--------------------
# 只要本地跑着一个 **OpenAI 兼容**接口的服务，就能用它对话，不需要 API key。
#
#   Ollama      : ollama serve            -> http://127.0.0.1:11434/v1
#                 ollama pull qwen2.5:7b
#   LM Studio   : 启动 Server              -> http://127.0.0.1:1234/v1
#   llama.cpp   : llama-server -m xx.gguf  -> http://127.0.0.1:8080/v1
#
# 这三个的 /v1/chat/completions 请求体与 DeepSeek **完全一样**
# （都是 OpenAI 格式），所以调用代码可以共用一套，只是换个 URL + 模型名。
LOCAL_LLM_API_URL = "http://127.0.0.1:11434/v1/chat/completions"
LOCAL_LLM_MODEL   = "qwen2.5:7b"     # 按你 pull 的模型改
LOCAL_LLM_TIMEOUT = 120.0            # 本地模型冷启动可能慢，给足时间
LOCAL_LLM_TEMP    = 0.8

# 支持的对话模式（菜单用）：值 -> 显示名
DIALOGUE_MODES = (
    ('deepseek', 'DeepSeek 云端'),
    ('local_llm', '本地模型（Ollama/LM Studio）'),
    ('local', '本地台词库（离线，不调模型）'),
)



def _read_api_key():
    """按 config.json -> 环境变量 的顺序取 API Key，没有就返回 None"""
    import json
    try:
        if os.path.exists(CONFIG_PATH):
            with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
                v = (json.load(f) or {}).get(CONFIG_KEY_FIELD)
            if isinstance(v, str) and v.strip():
                return v.strip()
    except Exception:
        pass
    v = os.environ.get('DEEPSEEK_API_KEY')
    return v.strip() if v and v.strip() else None


DEEPSEEK_API_KEY = _read_api_key()


def current_api_key():
    """实时读取 API Key。
    注意：不要用「from config import DEEPSEEK_API_KEY」拿到的值——
    那是模块导入时刻的快照，运行期间改 config.json 不会生效（401 的坑）。
    本函数每次都重新读文件；LLM 请求频率极低，开销可忽略。"""
    return _read_api_key()


# ==================== 以下为原有常量与文案（原样搬运） ====================
TRANSPARENT     = '#FF00FF'
BUBBLE_BG       = '#F6F1FC'   # 毛玻璃底色（选项条等同色跟随）
BUBBLE_FG       = '#43395C'   # 正文：深紫灰（配淡紫毛玻璃底）
BUBBLE_BORDER   = '#CFC2E6'   # 柔和紫灰描边
# 毛玻璃气泡：主气泡由 PIL 预渲染成整图（圆角+渐变+星星），
# 见 ui_widgets.FrostBubbleLabel——透明窗直接画文字/贴半透明图会泛粉边（见 error.md）
BUBBLE_FROST_TOP    = '#FBF8FE'   # 渐变顶部（高光，更亮）
BUBBLE_FROST_BOTTOM = '#EFE8F9'   # 渐变底部（雾面）
BUBBLE_STAR         = '#B9A5DC'   # 星星装饰色
BUBBLE_PAGE_CHARS   = 30          # 气泡单屏最大字数（约 3 行），超出在逗号处换下一屏

# ⬇️ 窗口尺寸（气泡在头顶，输入框默认隐藏、独立于角色下方）
WIN_W, WIN_H    = 150, 250
IMG_H           = 144
BUBBLE_X, BUBBLE_Y, BUBBLE_W, BUBBLE_H = 5, 18, 140, 52
INPUT_X,  INPUT_Y,  INPUT_W,  INPUT_H  = 5, 222, 140, 24

TYPING_INTERVAL = 0.035
CLICK_COOLDOWN  = 0.5
WAIT_NEXT       = 2.0
WAIT_LAST       = 3.0
FADE_DURATION   = 0.35
VOICE_HOLD_SEC  = 2.0      # 每句话打完停多久再刷下一句（她要的节奏）
VOICE_END_SEC   = 3.0      # 最后一句打完，留多久再让气泡消失
VOICE_RX_TICK_MS = 30      # 语音回复打字机的推进间隔（毫秒）
# 气泡翻页等待语音的兜底上限（秒）：开着说话时，这一页的语音没播完就先不翻页，
# 避免神经语音"第一页没说完就被掐断"。但合成线程万一卡死也不能永远不翻页，
# 超过这个时长照样翻（用户点击气泡始终可以直接跳下一页）。
SPEECH_PAGE_MAX_HOLD = 30.0

# ---- 姿态规则 ----
# 聆听：只有"他问的是问句"才用 think，其余正常对话都用 smile
# 回话：默认 smile；她说负面情绪的话用 walk1；说了高兴的话用 happy
QUESTION_MARKS = ('?', '？', '吗', '呢', '嘛', '么')
QUESTION_WORDS = ('为什么', '怎么', '怎样', '如何', '什么', '多少', '哪儿', '哪里',
                  '哪个', '哪一', '是否', '能不能', '可不可以', '好不好', '行不行',
                  '有没有', '知不知道', '记不记得')

# 情绪词表：先匹配负面，再匹配正面；都用"出现即判定"的简单规则。
EMO_NEGATIVE = (
    '难过', '孤独', '寂寞', '悲伤', '伤心', '痛苦', '冷漠', '抱歉', '对不起',
    '讨厌', '不喜欢', '不愿意', '不想', '不要', '不用', '不必', '算了', '别',
    '没什么', '不重要', '不认识', '不知道', '不能', '不行', '不适', '不耐烦',
    '懒得', '累了', '疲倦', '哭泣', '眼泪', '离开', '走了', '再见', '睡不着',
    '雨', '阴天', '一个人',
)
EMO_POSITIVE = (
    '高兴', '开心', '愉快', '真好', '好呀', '欢迎', '喜欢', '愿意', '期待',
    '谢谢你', '谢谢', '陪你', '安慰', '安心', '温暖', '温柔', '甜甜', '幸福',
    '可爱', '好听', '真棒', '太好了', '哈哈', '嘻', '♪', '我来', '陪你一起',
)

# ⬇️ 行走参数
WALK_STEP_SEC   = 0.05     # 每 50ms 走一步
WALK_STEP_PX    = 2        # 每步移动像素（慢一点，飘着走）
WALK_BOB_PX     = 2        # 走路时上下浮动幅度
WALK_BOB_SPEED  = 2.5      # 浮动角速度（帧切换与落地同步）
WALK_MIN_PAUSE  = 5.0      # 停下一秒最小
WALK_MAX_PAUSE  = 15.0     # 停下最大秒数

# ---- 物理：重力下落 + 边缘走回 ----
PHYS_GRAVITY         = 280   # px/s²，更柔的漂感（从 200→790 地面约 4 秒）
PHYS_AIR_DRAG        = 0.9  # 空气阻力系数（每秒速度 × 0.85 衰减，0 无阻力，1 完全粘住）
PHYS_MAX_FALL_V      = 200   # 最大下落速度 px/s（终端速度，别让她飞得太快）
PHYS_FALL_POSE       = 'sleep'  # 下落姿态：用 sleep 图（闭眼往下漂）
PHYS_EDGE_RATIO      = 0.30   # 角色超出屏幕边缘的比例 >= 此值 → 判定被遮挡
PHYS_SAFE_MARGIN_X   = 20     # 地面水平两侧预留
PHYS_WALKIN_FACTOR   = 0.30   # 左边出来 → 目标点在屏幕 30% 处；右边 → 70% 处

# ⬇️ 空闲闲聊
IDLE_CHAT_SEC   = 60      # 1 分钟无操作触发闲聊
SLEEP_SEC       = 90      # 3 分钟无操作切 sleep 图

# ⬇️ 睡眠摇篮晃动（以当前位置为支点的扇形摆动）
SLEEP_SWING_RADIUS = 48    # 摆臂半径（像素）：决定横向摆幅 ≈ RADIUS*sin(ANGLE)
SLEEP_SWING_ANGLE  = 0.30  # 最大摆角（弧度，约 17°）
SLEEP_SWING_SPEED  = 1.2   # 摆动角速度（弧度/秒）：周期 ≈ 5.2 秒

# ⬇️ 待机时睁眼(smile)/戴眼罩(walk1)轮换间隔
IDLE_SWITCH_MIN = 12.0
IDLE_SWITCH_MAX = 30.0

# ⬇️ 唱歌（移动途中随机唱 / 打字或语音点歌）
MUSIC_DIR        = os.path.join(BASE_DIR, 'music')
SONG_EXTS        = ('.mp3', '.wav', '.flac', '.ogg', '.m4a')
SONG_RATE        = 44100    # 统一解码到这个采样率
SONG_VOL         = 0.9      # 歌曲音量（0~1）
SONG_FADE_IN     = 1.2      # 起唱淡入（秒）
SONG_FADE_RESUME = 0.7      # 续播淡入（秒）
SONG_FADE_OUT    = 0.45     # 句尾淡出（秒）
SONG_STOP_MARGIN = 1.0      # 收到停唱后，至少再唱 1 秒才落到句尾
SING_ROLL_SEC    = 5.0      # 走路时每 5 秒掷一次"要不要唱歌"
SING_PROB        = 0.04     # 每次掷骰的概率
SING_COOLDOWN    = 600.0    # 一首唱完后多久内不再随机开唱
SING_DECLINE_BLOCK = 600.0  # 询问时选"不想"后，多久内不再随机问（10 分钟）
SING_FIRST_DELAY = 30.0     # 启动后先安静 30 秒再考虑随机唱歌
SING_GAP_MIN     = 3.0      # 句尾停止 → 再次续唱的间隔（3~5 秒）
SING_GAP_MAX     = 5.0
# 点歌说法：唱首歌 / 我想听你唱歌了 / 来首歌 / 听你唱 / 点歌 / sing a song
SONG_REQ_PAT     = re.compile(
    r"唱.{0,3}歌|想听.{0,4}(唱|歌)|来.{0,2}歌|听你唱|给我唱|点.{0,2}歌|sing\s*a?\s*song", re.I)
# 播放器口令（手动点歌后进入播放器模式时可用）
SONG_STOP_PAT    = re.compile(
    r"停止|别唱了|不要唱了|先别唱了|不用唱了|停了吧|stop(\s+singing)?\b", re.I)
SONG_PAUSE_PAT   = re.compile(r"暂停|pause", re.I)
SONG_NEXT_PAT    = re.compile(r"下一首|换一首|切歌|next\s*song", re.I)
SONG_PREV_PAT    = re.compile(r"上一首|前一首|上一曲|prev", re.I)
SONG_RESUME_PAT  = re.compile(r"继续唱|接着唱|唱吧|继续|resume", re.I)



# 提示词里用的时段词
FACTS_PERIODS = (
    (5, '凌晨'), (9, '早上'), (12, '上午'), (13, '中午'),
    (18, '下午'), (19, '傍晚'), (23, '晚上'), (24, '深夜'),
)





# 询问「想/不想」的等待策略
SING_ASK_TIMEOUT  = 10.0   # 弹出选项后等你多久（秒）；超时=默认不唱
SING_ASK_HOLD_SEC = 8.0    # 问句气泡淡出后，选项再多留一会儿（别刚冒出来就消失）
SING_TIMEOUT_BLOCK = 300.0  # 超时没选后，多久内不再随机问（5 分钟）

# ==================== 学习计时 ====================
STUDY_PATH          = os.path.join(BASE_DIR, 'study_records.json')
STUDY_REMIND_MIN    = 45          # 学习多少分钟后提醒休息
STUDY_CHAT_MIN      = 120.0       # 学习中随机对话最小间隔（秒）
STUDY_CHAT_MAX      = 300.0       # 学习中随机对话最大间隔（秒）
STUDY_TICK_SEC      = 1.0         # 计时 UI 刷新间隔（秒）
STUDY_PAUSE_REMIND_SEC = 600.0    # 暂停多久后提醒"还继续学习吗"（10 分钟）
STUDY_MIN_SAVE_SEC  = 60          # 有效学习少于多少秒不保存记录
STUDY_AUTOSAVE_SEC  = 15.0        # 学习期间每隔多少秒把状态落盘一次
                                  # （崩溃/直接关程序最多丢这么多秒；
                                  #   只靠退出时保存会整段丢失，踩过）

# 学习指令意图正则（打字/语音共用）
STUDY_START_PAT  = re.compile(r"开始学习|开始计时|学习计时|start\s*study", re.I)
STUDY_PAUSE_PAT  = re.compile(r"暂停学习|暂停计时|pause\s*study", re.I)
STUDY_END_PAT    = re.compile(r"结束学习|停止学习|结束计时|stop\s*study|end\s*study", re.I)
STUDY_RECORD_PAT = re.compile(r"学习记录|查看记录|study\s*record", re.I)



# ==================== 屏幕感知（她在看什么）====================
ACTIVITY_LOG_PATH   = os.path.join(BASE_DIR, 'activity_log.json')
AWARENESS_POLL_SEC  = 2.0      # 每隔几秒看一眼前台窗口
ACTIVITY_FLUSH_SEC  = 60.0     # 活动记录落盘间隔（秒），避免频繁写盘
AWARENESS_BANTER_COOLDOWN = 180.0  # 两次打趣之间至少间隔（秒）
AWARENESS_SAME_COOLDOWN   = 900.0  # 同一个页面/程序多久内不重复打趣（15 分钟）
AWARENESS_BANTER_PROB     = 0.35   # 切到新应用时开口的概率（"中等"档）
AWARENESS_MIN_DWELL_SEC   = 20.0   # 至少停留这么久才值得开口，避免一闪而过的窗口



# ==================== 打开应用 / 网址 ====================
# 识别"帮我打开 X"的意图。注意：
#   1. 排除"打开了/开着/开过"这类把"开"当动词补语的用法
#      （否则"我打开了心结"会被误判成打开指令）
#   2. 绝不从窗口标题里取指令（防止网页内容注入）
LAUNCH_PAT = re.compile(
    r"(?:帮我|给我|替我)?\s*"
    r"(?:打开|开启|启动|运行|访问|前往|open|launch|start)\s*"
    r"(?!了|着|过|的)"
    r"[「『\"']?(.{1,80}?)[」』\"']?\s*$",
    re.I)
# 网址识别（纯域名也算）
URL_PAT = re.compile(
    r"^(?:https?://)?(?:[\w-]+\.)+[a-zA-Z]{2,}(?:/[^\s]*)?$")
# 拒绝的危险字符（命令行注入 / 链式命令 / 重定向）
LAUNCH_FORBIDDEN = (';', '&&', '||', '|', '>', '<', '`', '$(',
                    '\n', '\r', '%COMSPEC%', 'cmd.exe', 'powershell')
# 常见中文名 -> 可执行名（用户说"打开微信"也能命中）
LAUNCH_ALIASES = {
    '浏览器': 'msedge',
    '谷歌浏览器': 'chrome',
    'chrome': 'chrome',
    'edge': 'msedge',
    '火狐': 'firefox',
    '微信': 'weixin',
    'qq': 'QQ',
    '记事本': 'notepad',
    '计算器': 'calc',
    '画图': 'mspaint',
    '资源管理器': 'explorer',
    '终端': 'wt',
    '命令提示符': 'cmd',
    '任务管理器': 'taskmgr',
    '网易云音乐': 'cloudmusic',
    '哔哩哔哩': 'bilibili',
    'steam': 'steam',
    'vscode': 'code',
    'vs code': 'code',
    'trae': 'trae',
    '学习': 'study',
    '唱歌': 'sing',
}


# ==================== 软件路径表（右键菜单「管理软件路径」维护）====================
# 你说"打开微信"，她先从这张表里找；找不到就明确告诉你，
# 不再把名字丢给系统（那样会弹"系统找不到指定文件"）。
APP_PATHS_PATH = os.path.join(BASE_DIR, 'app_paths.json')

# 首次运行自动填进去的常见软件路径（只填**确实存在**的，避免垃圾数据）
APP_PATHS_SEED = {
    'chrome': r'C:\Program Files\Google\Chrome\Application\chrome.exe',
    'edge': r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe',
    'firefox': r'C:\Program Files\Mozilla Firefox\firefox.exe',
    'vscode': r'%LOCALAPPDATA%\Programs\Microsoft VS Code\Code.exe',
    'trae': r'%LOCALAPPDATA%\Programs\Trae\Trae.exe',
    'notepad': r'C:\Windows\System32\notepad.exe',
    'calc': r'C:\Windows\System32\calc.exe',
    'mspaint': r'C:\Windows\System32\mspaint.exe',
    'explorer': r'C:\Windows\explorer.exe',
    'taskmgr': r'C:\Windows\System32\Taskmgr.exe',
    'cmd': r'C:\Windows\System32\cmd.exe',
    'powershell': r'C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe',
    'wt': r'%LOCALAPPDATA%\Microsoft\WindowsApps\wt.exe',
    'wechat': r'C:\Program Files (x86)\Tencent\WeChat\WeChat.exe',
    'weixin': r'C:\Program Files\Tencent\Weixin\Weixin.exe',
    'qq': r'C:\Program Files (x86)\Tencent\QQ\Bin\QQScLauncher.exe',
    'steam': r'C:\Program Files (x86)\Steam\steam.exe',
    'typora': r'%LOCALAPPDATA%\Programs\Typora\Typora.exe',
}

# 「从浏览器打开 / 搜索一下」的意图
SEARCH_PAT = re.compile(
    r"(?:帮我|给我|替我)?\s*"
    r"(?:在浏览器(?:里|中)?)?\s*"
    r"(?:百度一下|谷歌一下|百度|谷歌|搜索|搜一下|查一下|查查|搜|search|google)\s*"
    r"[「『\"']?(.{1,80}?)[」』\"']?\s*$",
    re.I)
# 明确要求"从浏览器打开"：这类即使像程序名也按网址/搜索处理
BROWSER_OPEN_PAT = re.compile(
    r"(?:从|用|在)?\s*浏览器\s*(?:里|中)?\s*"
    r"(?:打开|开启|搜索|搜一下|查一下)\s*"
    r"[「『\"']?(.{1,80}?)[」』\"']?\s*$",
    re.I)
# 泛指词 -> 常见可执行名（"打开浏览器"时用来猜一个）
# 注意：这里写的是**路径表的键**（edge / chrome…），不是 exe 名（msedge.exe）
BROWSER_CANDIDATES = {
    '浏览器': ('edge', 'chrome', 'firefox', 'brave', 'opera', 'iexplore'),
}
# 搜索引擎（想换默认搜索改这里）
SEARCH_ENGINE_URL = 'https://www.baidu.com/s?wd={q}'



# ==================== 天气 ====================
# 你填城市名，她自己去查（全部走免密钥的 open-meteo）。
# 留空 = 不联网、不查天气，只报日期与节日。
# 注意：自动定位（按 IP 猜城市）在国内不可用，所以必须手填。
WEATHER_CACHE_PATH = os.path.join(BASE_DIR, 'weather_cache.json')


# ==================== 日记 ====================
DIARY_PATH         = os.path.join(BASE_DIR, 'diary.json')
DIARY_WRITE_AT_MIN = 23 * 60 + 30   # 每天 23:30 自动写当天日记
DIARY_MIN_APP_SEC  = 600            # 只记"用了很久"的程序（10 分钟以上）
DIARY_TOP_APPS     = 3              # 最多提几个程序
DIARY_MAX_SNIPPETS = 6              # 每天最多留几条对话片段


# ==================== 说话输出（文字 -> 语音）====================
# 引擎：'hybrid'（本地原声+检索，**默认**，菜单里就是"本地语音"）/
#       'edge'（Edge 神经语音，联网）/ 'off'（不说话，由「让她说出声」开关管）
# 'local'（纯精确查表）是 hybrid 的子集，不再出现在菜单里，仅作旧配置兼容保留。
SPEECH_VOICE_DIR   = os.path.join(BASE_DIR, 'voice')          # 本地原声文件
SPEECH_CACHE_DIR   = os.path.join(BASE_DIR, 'voice-cache')    # 合成缓存
# ASR 自动生成的"台词文字 -> 原声文件"映射（_transcribe_voice.py 产出）
SPEECH_LINES_PATH  = os.path.join(BASE_DIR, 'voice-lines.json')
# 语音模型目录（**只有 ASR 用的 SenseVoice**：model.int8.onnx + tokens.txt）
# 原先这里还放着克隆模型（hubert/rmvpe/Columbina/xtts），用户决定不做克隆了，
# 那些已删除。要重新训练/克隆的话请重建该目录，不要往这里塞别的模型。
SPEECH_MODEL_DIR   = os.path.join(BASE_DIR, 'voice-model')
SPEECH_RATE        = '+0%'    # 语速偏移（Edge TTS 参数，注意**不是采样率**）
SPEECH_VOLUME      = '+0%'    # 音量偏移（Edge TTS 参数）
# 音频采样率。必须单独一个常量：SPEECH_RATE 是 '+0%' 这种字符串，
# 传给 miniaudio.decode_file(sample_rate=...) 会 TypeError: an integer is required。
# （踩过：解码静默失败，表现为"合成失败"，其实是这里类型不对。）
SPEECH_SAMPLE_RATE = 44100
# 默认音色。注意：这里必须有**模块级**默认值 —— 运行期真实值在
# Config.DEFAULTS['speech_voice']，但 speech.py 的合成要在拿到 cfg 之前
# 也能工作，缺了它会 AttributeError（被 except 静默吞掉，表现为"合成失败"）。
SPEECH_VOICE       = 'zh-CN-XiaoxiaoNeural'
SPEECH_ENGINE      = 'hybrid'
# 菜单里展示的神经音色。用户只要晓晓（温柔自然），所以默认只留她；
# 想放开其他音色就往这里加（speech.EdgeTTSEngine.ZH_VOICES 里有全部 8 个）。
SPEECH_VOICES_SHOWN = ('zh-CN-XiaoxiaoNeural',)
SPEECH_MAX_QUEUE   = 6        # 待播队列上限，超出丢弃（避免堆积后突然连播）

# ---- 检索式回答（只用原声说话）----
# 用户要求："做有音频的出声，没音频的就不出声了，不要克隆了"
# 做法：拿用户说的话去语音库检索最贴近的一句台词。
#   · 相似度 >= 阈值  -> 让她说这句（真·她的原声）
#   · 相似度 <  阈值  -> 不出声（避免答非所问）
# 调低 = 话多但容易答非所问；调高 = 贴切但常常安静。
SPEECH_RETRIEVAL_MIN = 0.35

# 兜底最低相似度：即便如此也没匹配上时的硬下限。
# 低于它一律静音 —— 防止 0.1 的噪声匹配也被当成"她听懂了"。
SPEECH_RETRIEVAL_FLOOR = 0.18


# 第三方包目录（项目内，避免写 C 盘用户目录；顶部已自动加入 sys.path）
VENDOR_DIR = _VENDOR


# ==================== 配置读写 ====================
class Config:
    DEFAULTS = {
        "master_name": "旅行者",
        # 对话内容从哪来：
        #   'deepseek'  = DeepSeek 云端（要 key）
        #   'local_llm' = 本地模型（Ollama / LM Studio，**不花钱、不用 key**）
        #   'local'     = 本地台词库（离线，**不调模型**，只从库里挑）
        "dialogue_mode": "local",
        # 本地模型端点（'local_llm' 模式用）。改这里就能切成别的本地服务
        "local_llm_url": "http://127.0.0.1:11434/v1/chat/completions",
        "local_llm_model": "qwen2.5:7b",
        "position": [900, 150],
        "topmost": True,
        "affinity": 0,
        "walk_enabled": True,
        "awareness_enabled": True,     # 屏幕感知（她知道你在用什么）
        "weather_city": "",            # 城市名，如 "成都"。留空=不查天气
        "birthday": "",                # 你的生日，填 "MM-DD" 或 "YYYY-MM-DD"
        "speech_enabled": True,        # 说话时念出来（可随时用菜单关）
        # 'hybrid' = 只用原声（有音频才出声）/ 'edge' = Edge 神经语音（什么都能念）
        "speech_engine": "hybrid",
        "speech_voice": "zh-CN-XiaoxiaoNeural",   # 用户只要晓晓（温柔自然）
    }

    def __init__(self):
        self.data = dict(self.DEFAULTS)
        self.load()

    def load(self):
        if os.path.exists(CONFIG_PATH):
            try:
                with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
                    self.data.update(json.load(f))
            except Exception:
                pass
        # 启动时把 API Key 读进内存（供 DeepSeek 调用使用）
        self._file_key = self.data.get(CONFIG_KEY_FIELD)
        global DEEPSEEK_API_KEY
        DEEPSEEK_API_KEY = _read_api_key()

    def save(self):
        try:
            # 保存前先合并磁盘上的最新内容：这样即使程序运行期间你手动
            # 编辑了 config.json（比如填 API Key），也不会被这次保存覆盖掉。
            disk = {}
            if os.path.exists(CONFIG_PATH):
                try:
                    with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
                        disk = json.load(f) or {}
                except Exception:
                    disk = {}
            # 我没动过 Key 的话，保留磁盘上的值；避免把空 Key 写回去
            mem_key = self.data.get(CONFIG_KEY_FIELD)
            if mem_key and mem_key != self._file_key:
                disk[CONFIG_KEY_FIELD] = mem_key
            elif not (isinstance(disk.get(CONFIG_KEY_FIELD), str)
                      and disk[CONFIG_KEY_FIELD].strip()):
                disk.pop(CONFIG_KEY_FIELD, None)
            merged = dict(disk)
            merged.update(self.data)
            self.data = merged
            with open(CONFIG_PATH, 'w', encoding='utf-8') as f:
                json.dump(merged, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    def __getitem__(self, k): return self.data.get(k)
    def __setitem__(self, k, v): self.data[k] = v
