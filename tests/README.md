# 测试与踩坑记录

这个目录放**测试**和**已知坑记录**。运行时不依赖它们，可以直接删掉不影响桌宠。

```
tests/
├── README.md              ← 本文件：改哪测哪的对照表
├── error.md               ← 已知坑、根因、修法、验证方式（14 条）
└── _*_test.py             ← 15 个测试套件
    _diag_*.py             ← 2 个诊断工具（不是测试，是排查用的）
```

**从哪运行都行**（脚本自己会把项目根加进 `sys.path`）：

```bat
python tests\_speech_regress_test.py
```

---

## ⚠️ 别每次都全测 —— 改哪测哪

全跑一遍要 **6~8 分钟**（大量 Tk 窗口 + 音频 + 真实等待）。
日常改完只需要跑**相关的那一两个**：

### 对照表：改了哪些文件 → 跑哪个测试

| 你改了什么 | 该跑的测试 | 项数 |
|---|---|---|
| **`speech.py`** / `speech_control.py`（说话、原声、检索、重采样）| `_speech_regress_test.py` | 81 |
| **`diary.py`** / `weather.py` / `holidays.py`（日记、天气、节日）| `_diary_regress_test.py` | 72 |
| **`awareness.py`** / `banter.py` / `launcher.py`（屏幕感知、打开东西）| `_aware_regress_test.py` | 88 |
| **`facts.py`**（时间/日期/天气问答）| `_facts_regress_test.py` | 67 |
| **`song_control.py`** / `song_engine.py`（唱歌）| `_sing_regress_test.py` + `_sing_flow_test.py` | 35 + 23 |
| 唱歌的"想不想听"询问逻辑 | `_sing_ask_test.py` | 22 |
| **`study.py`**（学习计时）| `_study_regress_test.py` | 55 |
| 学习期间的特殊姿态 | `_study_pose_test.py` | 15 |
| **`ui_widgets.py`**（气泡、立绘、面板）| `_bubble_test.py` + `_bubble_frost_test.py` | — |
| 气泡打字节奏 | `_rhythm_test.py` | — |
| 姿态规则（问句→think 等）| `_pose_test.py` | — |
| **`voice_engine.py`** / `voice_control.py`（全双工语音）| `_duplex_test.py` | — |
| **`config.py`**（文案、常量、默认值）| `_namecheck_test.py` **+ 受影响的功能测试** | — |
| **`app.py`** / `walk.py` / `physics.py` / `interaction.py` | `_namecheck_test.py` + `_pose_test.py` + `_bubble_test.py` | — |
| **`menu.py`**（右键菜单）| `_namecheck_test.py` + 对应功能的测试 | — |
| **不确定 / 大面积改动 / 发版前** | **全跑** | 15 套件 |

### 推荐的最小检查顺序

```bat
:: 1. 永远先跑这个 —— 2 秒，能抓出"引用了没定义的全局名"
python tests\_namecheck_test.py

:: 2. 再跑你改动那一块的测试（见上表）
python tests\_speech_regress_test.py

:: 3. 只有动了公共部分（config / app / ui_widgets）才需要多跑几个
python tests\_bubble_test.py
python tests\_pose_test.py
```

> **`_namecheck_test.py` 的价值**：项目里大量 `except Exception` 做依赖降级，
> 写错变量名会被静默吞掉、表现为"功能默默不工作"。这个静态检查
> 2 秒就能把所有"引用了但没定义"的全局名一次找出来。

### 一次跑多个

```bat
python tests\_run_tests.py                  :: 全跑
python tests\_run_tests.py speech diary     :: 只跑匹配这两个关键词的
python tests\_run_tests.py --list           :: 列出所有套件
```

`_run_tests.py` 用一个**子进程跑一个套件**——因为项目只允许一个
`tk.Tk()` 实例，同进程连跑多个套件会撞 Tk 的内部图像表。
它会按 `结果: ALL_PASS` / `通过 N 项，失败 M 项` 判断成败并汇总。

---

## 各测试文件做什么

### 回归测试（15 个）

| 文件 | 覆盖 | 测什么 |
|---|---|---|
| **`_namecheck_test.py`** | 全部模块 | **静态检查**：AST 解析每个模块，找出"引用了但没定义"的全局名。不做运行时验证，但最快最能发现问题 |
| `_speech_regress_test.py` | `speech` `speech_control` | 引擎层、采样率陷阱、**抗混叠重采样**、**线程安全不丢回调**、**不卡顿**、打断、静音、**检索式回答（IDF 覆盖率的 2 倍偏差）**、解码降级、ffmpeg 兜底 |
| `_diary_regress_test.py` | `diary` `weather` `holidays` | WMO 天气码翻译、缓存降级、农历节日、日记**只看不增改**、素材合成、**感受与随机事件**、**测试不碰真实数据** |
| `_aware_regress_test.py` | `awareness` `banter` `launcher` | 前台窗口探测、网站识别、**按天记录**、**跨周保留明细**、打趣规则（停留/冷却/去重）、打开意图识别、安全校验、确认流程 |
| `_facts_regress_test.py` | `facts` `weather` | 时间精确到分钟、日期含年月日、天气、**不误吞闲聊**、提示词注入 |
| `_sing_regress_test.py` | `song_control` `voice_control` | 点歌连唱、语音口令暂停/继续、防歌声串扰、识别框显示 |
| `_sing_flow_test.py` | 唱歌续航 | 下落姿态不挡续唱、引擎竞态不吃 `resume`、忙完必续 |
| `_sing_ask_test.py` | 唱歌询问 | "想不想听"的等待策略：不选则默认不唱、超时、选项保留 |
| `_study_regress_test.py` | `study` | 状态机、暂停/继续、记录保存与聚合、打字/语音指令拦截 |
| `_study_pose_test.py` | 学习期姿态 | 拖动→touch、下落→sleep、落地→work，以及**走动中不被强制 work** |
| `_duplex_test.py` | `voice_engine` | VAD / 流式识别 / SSE 解析 / 打断（用假 HTTP 服务器，不联网）|
| `_bubble_test.py` | `ui_widgets` | 气泡生命周期：打字→保持→淡出→消失 |
| `_bubble_frost_test.py` | `ui_widgets` | 毛玻璃气泡：长句逗号分页、PIL 渲染出图、打字链路 |
| `_rhythm_test.py` | `chat` | 逐句打字节奏（每句停几秒）|
| `_pose_test.py` | `interaction` `walk` | 姿态规则：问句→think、陈述→smile、负面→walk1、走动守卫 |

### 诊断工具（不是测试，手动跑）

| 文件 | 用途 |
|---|---|
| `_diag_sing.py` / `_diag_sing2.py` | **唱歌静音诊断**：真引擎长跑，直接测音频 RMS 音量来定位"没声音"（见 `error.md`）|

---

## 写新测试时必读

### 1. 必须先掐掉启动问候

`app.py` 启动时会 `root.after(500, self._greet)`。这个定时器如果在断言期间
触发，会 `_set_pose('hi')` —— **把姿态测试搅乱**（曾经导致约 50% 概率的
随机失败）。

```python
pet = app.DesktopPet()
pet.root.withdraw()
try:
    pet.root.after_cancel(pet._greet_job)      # ⬅ 必须
except Exception:
    pass
pet.last_interaction_time = time.time() + 3600   # 压掉待机搭话
pet._last_idle_chat = time.time() + 3600
```

### 2. 一个进程只能建一个 `tk.Tk()`

多开会撞 Tk 的内部图像表。所以：
- 需要多个场景时，**复用同一个 pet 实例**，只换素材
- 或者像 `_run_tests.py` 那样**一个套件一个子进程**

### 3. 绝不允许写用户的真实数据文件 ⚠️

**这条是最严重的——真的发生过**：测试的清理代码删了 `pet.diary.path`，
而它指向**真实** `diary.json`，跑一次测试就把用户的历史日记删了。

```python
# 正确做法：把存储位置改到临时目录
_tmpdir = tempfile.mkdtemp(prefix='pet_diary_')
pet.setup_diary(os.path.join(_tmpdir, 'diary.json'))
pet.activity.path = os.path.join(_tmpdir, 'activity_log.json')

# 清理时只删临时目录，真实路径一个都不要动
shutil.rmtree(_tmpdir, ignore_errors=True)
```

要碰的真实文件：`diary.json`、`activity_log.json`、`config.json`、
`study_records.json`、`app_paths.json`、`weather_cache.json`。

### 4. 不要在"测试某个具体措辞"上写死字面词

她的话是从**素材库随机抽**的，同一份素材两次生成结果不同。
写 `assert '陪你学了' in text` 会时灵时不灵（踩过：连跑 5 次失败 2 次）。

**改成结构性断言**：

```python
# ✗ 脆弱：模板换一条就挂
check('含学习时长', '学' in text and '分钟' in text)

# ✓ 稳：断言"这类内容存在"
check('含学习时长', '45' in text)
check('学习内容确实产出', pet._diary_study(today)[0] >= 1)
```

### 5. 改完源码，顺手更新 `error.md`

踩到新坑就往 `error.md` 里加一条，格式照抄现有的：

```markdown
## 一句话说清是什么坑

**根因**：<为什么会发生>

**出现与修复记录**：

| 时间 | 现象 | 修复 |
|---|---|---|
| YYYY-MM-DD | <你看到的表象> | <怎么修的> |

**教训**：<下次怎么避免>
```

**判据要可复现**（给数字，别给感觉）——比如"杂散能量 9.977% → 0.821%"
就比"音质变好了"有用得多。

---

## 已知坑

见 [`error.md`](error.md)。收录 14 条，含两条最贵的教训：

- **语音克隆的两条失败路线**（RVC / XTTS 都试过，最终放弃；
  含"手写 HuBERT 形状权重全对但输出是垃圾"的教训）
- **测试删掉用户真实日记**（测试隔离的必要性）
