# 已知坑与修复记录

这个文件记录**踩过的坑、根因、修法和验证方式**。
按"值得反复提醒"排序，每条都尽量给出**可复现的判据**而不是感觉。

| # | 坑 | 一句话 |
|---|---|---|
| 1 | [透明窗文字渲染](#透明窗文字渲染粉色杂边--黑色遮挡--豆腐框) | 品红透明窗上不能用 `Label(text=)` / `create_text` |
| 2 | [`from config import KEY` 快照](#from-config-import-deepseek_api_key-快照陷阱http-401) | 导入的快照不会跟着 `config.json` 变 |
| 3 | [`geometry()` 延迟一帧](#tk-geometry-设置与-winfo_xy-有-1-步延迟) | 设完位置立刻读 `winfo_x()` 还是旧值 |
| 4 | [物理下落被 swing 覆盖](#物理状态用-sleep-姿态会触发-_update_sleep_swing-冲突) | 会调 `geometry` 的模块都要看 `physics_state` |
| 5 | [VAD 常开挡住续唱](#播放器模式下-vad-常开导致-_resume_song-被永久挡住) | 长期开 VAD 时守卫必须分模式 |
| 6 | [**语音克隆的两条失败路线**](#语音克隆的两条失败路线rvc--xtts) | RVC / XTTS 都试过，最终放弃；含"手写 HuBERT 失败"教训 |
| 7 | [**线性插值重采样**](#线性插值重采样没有抗混叠说话粘一起的真凶) | 无抗混叠 → 10% 杂散 → "说话粘一起" |
| 8 | [**活动记录只有周累计**](#activity_logjson-只有周累计--日记只能写流水账) | 日记只能写流水账；某天数据事后补不回来 |
| 9 | [跨周重置清掉按天明细](#跨周重置会清掉整个文件连按天明细一起丢) | 加字段时要检查所有"重置"路径 |
| 10 | [版本升级没落盘](#版本升级只在跨周分支落盘--文件死活不变-v2) | 结构迁移必须显式 `flush` |
| 11 | [**测试删掉真实日记**](#测试删掉了用户的真实日记测试污染真实数据) | 清理代码删了真实路径；测试必须隔离 |
| 12 | [vendor 清理误删必需包](#vendor-清理误删必需包三次) | 包名 ≠ 导入名；原生 DLL 不在 import 闭包里 |
| 13 | [PowerShell 改中文源码乱码](#powershell-写文件把中文搞成乱码) | 别用 PowerShell 批量改含中文的源码 |
| 14 | [`except Exception` 吞掉 `NameError`](#except-exception-吞掉-nameerror静态检查的必要性) | 静态检查兜底，否则降级变静默失效 |
| 15 | [**功能藏起来等于没有**](#功能藏起来等于没有日记删除按钮不可见) | 删除按钮在窗口底部没人看见 → 改成右键日期 |
| 16 | [**语音引擎决定了对话内容**](#语音引擎决定了对话内容导致只能配对不能真对话) | 两个正交的维度被耦合 → 切了 LLM 也用不上 |

---

## 透明窗文字渲染：粉色杂边 / 黑色遮挡 / 豆腐框

**根因**：主窗口用 `-transparentcolor #FF00FF`（品红）实现透明。
该机制只透「纯品红」像素，而 Tk 在 Label / Canvas 上直接渲染文字时，
文字抗锯齿边缘是「文字色 × 品红底」的混合色——既不会被透掉，
又会泛出粉色/紫黑杂边；叠在立绘上时，深色混合像素看起来像"黑色遮挡"。
若字体缺字形，还会画出空心豆腐框（紫框）。

**通用解法（唯一推荐）**：PIL 预渲染成图片再显示——

1. 大号字形渲染到 RGBA 图（2~3 倍目标尺寸）
2. LANCZOS 缩小到目标尺寸（平滑采样）
3. alpha 通道二值化（阈值 110：要么全透要么不透，无中间色）
4. `ImageTk.PhotoImage(img)` **直接转，保留透明通道**
   （禁止贴品红底——贴了会"打洞"透出桌面，且叠在立绘上变黑块）

**出现与修复记录**：

| 时间 | 位置 | 现象 | 修复 |
|---|---|---|---|
| 2026-10-15 | 唱歌音符（`ui_widgets.py _render_note_images` / `walk.py`） | canvas 直接画 ♪ 泛粉边；改贴品红底后变黑底紫框，黑底遮挡立绘；msyh.ttc 缺 ♪ 字形画成豆腐框 | 按通用解法重写；字体优先 `seguisym.ttf`，并用实心像素占比检测剔除豆腐框字体 |
| 2026-10-15 | 学习计时悬浮 UI（`study.py _study_ui_update`） | 计时/「已暂停」文字底部粉色、深色杂边遮挡立绘 | PIL 预渲染图片替代 Label 直接显示文字 |
| 2026-10-15 | 学习计时悬浮 UI 第二版（`study.py _render_study_text` + Label 贴图） | Label(image=...) 路径仍出现黑底遮挡角色 | 弃用 Label，改为 `_render_study_timer` 渲染整张「月夜竖条」+ `canvas.create_image`（与音符同路径，已验证无黑底） |

**新 UI 规范**：主窗口（品红透明窗）上要显示的任何文字 / 符号 / 图形，
一律 PIL 预渲染成图片后用 **`canvas.create_image`** 显示——
不要用 `Label(text=...)`、`canvas.create_text()`，**也不要用 `Label(image=...)` 贴图**
（Label 贴图的透明区域合成路径在 transparentcolor 窗口下仍可能出黑底）。
Toplevel 子窗口不受 `transparentcolor` 影响，可正常渲染文字（月夜面板即如此）。

---

## `from config import DEEPSEEK_API_KEY` 快照陷阱（HTTP 401）

**根因**：`config.py` 的 `DEEPSEEK_API_KEY` 在模块导入时读一次。
`voice_control.py`、`dialogue_llm.py` 用 `from config import *` 拿到的是导入时刻的**快照字符串对象**，
之后即使用户在 `config.json` 里填了新 key（或改了 key），各模块里的变量仍是旧值 →
发给 DeepSeek 的请求仍是空/错的 key → **HTTP 401 Unauthorized**。

> `Config.save()` 里虽然 `global DEEPSEEK_API_KEY` 刷新了 config 模块自己的全局，
> 但已经 `import` 到其它模块命名空间里的名字**不会跟着变**。

**出现与修复记录**：

| 时间 | 现象 | 修复 |
|---|---|---|
| 2026-10-15 | 全双工对话报 HTTP 401；文本对话也可能出现 | `config.py` 新增 `current_api_key()` 函数（每次调用实时读 `config.json`）；`voice_control.py` / `dialogue_llm.py` 改用 `current_api_key()` 替代快照；`voice_engine.py` 捕获 401 给出友好气泡提示 |

**规范**：用到 API Key 的地方一律调用 `current_api_key()`，禁止在发起请求的代码里使用导入快照（`from config import DEEPSEEK_API_KEY` 仅限启动初始化等不依赖后续变更的场景使用）。

---

## Tk `geometry()` 设置与 `winfo_x/y()` 有 1 步延迟

**根因**：`root.geometry(f"+{x}+{y}")` 设置窗口位置后，**立即**读取 `root.winfo_x()` / `winfo_y()`
返回的仍然是**设置前的值**（Tk 的 geometry 应用有一帧延迟）。

**坑场景**：
- 物理 `walking_in` 分支每步用 `cur_x += 2` 推进位置，但读完 geometry 就立即用
  `winfo_x()` 做下一步基准——永远追不上，窗口看起来"卡住不动"
- 修复：**每步都以 `winfo_x()` 真实位置为基准**，而不是在上一步值上累加：
  `cur_x = self.root.winfo_x()` → `cur_x += step` → geometry → 下一轮再 winfo_x()

**验证**（手工测试）：
```python
# 放屏幕外
r.geometry('+-200+500'); r.update(); time.sleep(0.3)
# 往里挪
r.geometry('+-198+500'); r.update()
# 此时 r.winfo_x() 可能还是 -200，不是 -198！
```

**出现记录**：

| 时间 | 位置 | 修复 |
|---|---|---|
| 2026-10-15 | `physics.py _update_physics walking_in` | 改为每步读 `winfo_x()` 真实位置做基准 |

**通用教训**：任何需要**连续多次精确调整窗口位置**的场景（物理、走路帧），
都必须在每次 geometry 设置后用 `winfo_x/y()` 读真实值作为下一次计算的基准——
不能假设 geometry 立即生效。

---

## 物理状态用 `sleep` 姿态会触发 `_update_sleep_swing` 冲突

**根因**：物理下落（`physics_state='falling'`）把姿态设成了 `sleep`，
而 `walk.py _update_sleep_swing` 只检查 `current_pose != 'sleep'` 就 return，
所以物理下落期间 swing 一直在跑——它按自己的支点位置反复调 `geometry`，
**覆盖了 physics 精心计算的下落位移**，导致窗口"看起来没在掉"。

**出现与修复记录**：

| 时间 | 现象 | 修复 |
|---|---|---|
| 2026-10-15 | 重力下落 2.5 秒后 y 几乎没动，实际被 swing 拉回去 | `_update_sleep_swing` 加守卫：`physics_state != 'idle'` 时清支点 + return |

**规范**：任何会调 `geometry` 的模块（走动 / swing / physics / interaction），
都必须在入口检查 `physics_state != 'idle'` 并让物理优先——
因为 physics 有自己完整的状态机，它接管期间其他物理模块一律让路。

---

## 播放器模式下 VAD 常开导致 `_resume_song` 被永久挡住

**根因**：manual 播放器模式下 VAD 必须常开（为了听"暂停/继续/停止"口令），
所以 `_voice_busy()` 常返回 True（`_v_listening=True` 来自歌声残响/环境噪声）。
而 `_resume_song` 的守卫条件里有 `_voice_busy()`——于是**句尾 gap 过后续唱
永远被挡** → 用户体感："放了一小段就停了"。

**出现与修复记录**：

| 时间 | 现象 | 修复 |
|---|---|---|
| 2026-10-15 | 语音/手动点歌后唱一小段就停，不接下一首 | manual 模式下 `_resume_song` 忽略 `_voice_busy()`（歌声残响不是真的有人在说话） |

**教训**：需要长期开启 VAD 的场景（播放器模式听口令），
**所有依赖 `_voice_busy()` / `_v_listening` 的守卫**都必须加模式区分——
否则这些标志会被"不该触发"的噪声持续置位，挡住所有自动路径。

---

## 语音克隆的两条失败路线（RVC / XTTS）

> **结论先说**：两条路都试过，用户都不满意，**最终决定不做克隆**——
> "有音频的出声，没音频的就不出声，不要克隆了"。
> 相关代码、模型（约 3.2 GB）与依赖（`vendor/` 里 202 个包、6.3 GB）**已全部删除**。
> 这一节保留下来，是为了**别再重走**。

### 路线 A：RVC 变声（晓晓 TTS → 变声转换）

**做法**：文字 →(Edge TTS 晓晓)→ 中性语音 →(RVC 哥伦比娅模型)→ 她的音色。

**结果**：内容能保住，但**音色没转过来**：

```
频谱包络距离（越小 = 越像她）
  晓晓 TTS 原始   vs 她: 2.18      ← 基线
  RVC 克隆输出    vs 她: 2.65      ← 反而更远
  RVC 克隆输出    vs 晓晓: 0.27    ← 几乎没改变音色
```

用户听感：**"完全把声音粘一起了，都不像正常人说话"**。

**过程中修好的问题**（这些经验仍然有用）：

| 问题 | 根因 | 修法 |
|---|---|---|
| `fairseq` 装不上 | **没有 Windows 轮子**（连 cp38 都只有 macOS/Linux），从源码装要 MSVC 编译器 | 用 **torchaudio 的网络定义 + 官方 `_map_key` 键映射**加载 fairseq 权重 |
| RVC 用 PyAV 报 `mode must be 'r'` | 新版 PyAV 不再接受 `"rb"`，而 `rvc` 锁 `av<12`（cp312 无轮子） | 用项目自己的解码器替换 `rvc.lib.audio.load_audio` |
| 输出**削波 128062 个样本** | RVC 返回的是 **int16 量级**数组（RMS≈3600、峰值≈16000），而项目其它引擎都是 `[-1,1]` | 归一化到 `[-1,1]` |

### 路线 B：XTTS v2 克隆（直接从参考音频克隆）

**做法**：XTTS 从参考音频提取音色潜向量，直接合成任意文字。

**结果**：内容 **100% 正确**，但**真实音色仍不像她**。

参数扫描（7 个参考 × 4 组参数 = 28 组）**ASR 重合率全部 100%**，
说明问题**不在文字正确性，而在感知层面**——我的指标测不出来。

**过程中发现的两个真问题**：

| 问题 | 实测数据 | 修法 |
|---|---|---|
| **参考音频带 BGM** | 参考的频谱平坦度 **0.210**，比原始素材整体（0.30）还低 —— 音乐成分很重。XTTS 会把参考的噪声/音乐一起克隆 | 换成**单句无 BGM** 的片段（平坦度 0.355），并写出 XTTS 期望的 **22050Hz** |
| **重采样无抗混叠** | 见下一节 | 换 `scipy.signal.resample_poly` |

### 为什么手写 HuBERT 失败过（重要教训）

`fairseq` 在 Windows 装不上，所以我**按 fairseq 源码手写了一份 HuBERT**
（`fairseq_stub.py`）：形状对、权重键名对、帧数也对齐，**看起来完全正确**。

但转换出来**内容是坏的**：

```
输入: '今天天气不错，我们出去走走吧。'
输出: '嘿嘿嘿嘿嘿嘿'          ← 音素被破坏
```

换成 **torchaudio 官方维护的 fairseq 兼容层**后，内容重合率 **0% → 77%**。

**教训**：**能复用经过验证的实现时，不要自己重写。**
形状/权重/维度全对，不代表前向细节一致（卷积 padding、位置卷积的
`weight_norm` 重建、层归一化位置…任何一处错了都会静默产出垃圾）。

### 为什么最终放弃克隆

即使内容 100% 正确，**音色仍不像她**。而用户要的是"像她"。
既然手上已经有 **217 句真人原声**，那就没有理由用合成的假声——
**宁可安静，也不要用不像她的声音说话。**

---

## 线性插值重采样没有抗混叠（"说话粘一起"的真凶）

**根因**：`resample()` 早期用**线性插值**，没有低通滤波。

只要**引擎输出采样率 ≠ 播放设备采样率**（本机设备 44.1 kHz）就必须重采样。
XTTS 输出 24 kHz，于是：

```
12k~22kHz 不该存在的杂散能量占比
  线性插值:   9.977%
  抗混叠:     0.821%     ← 线性插值是它的 12.2 倍
```

这些杂散频率听感是**金属感、沙哑**，用户描述为"**说话粘在一起**"。

**定位线索**（关键）：**只有输出 24 kHz 的那个引擎听起来不对**，
而 44.1 kHz 的音源（晓晓、本地原声）采样率相同、**根本不走这个函数**——
顺着"为什么只有它有问题"这条线才找到的。

**修法**：优先 `scipy.signal.resample_poly`（多相 FIR + 抗混叠），
没有 scipy 才退回线性插值。

**通用教训**：**采样率不匹配时的重采样必须抗混叠**。
线性插值在"变速变调"这种粗用途上够用，但**会毁掉音质**。

---

## `activity_log.json` 只有周累计 → 日记只能写流水账

**根因**：活动记录原来是**按周分桶**的：

```json
"week": "2026-W42", "week_start": "2026-10-12",
"apps": { "DeepSeek Harness.exe": { "seconds": 4847.5 } }   ← 整周累计
```

日记读的就是这个，所以写"你今天用了 X 小时"其实是**整周数字** ——
用户反馈："**全是在用什么时间最长**"。

**更糟的后果**：**某天的数据在结构上压根不存在**。
所以那一天要是没写成日记，**事后永远补不回来**（用户遇到过某一天凭空消失）。

**修法**：改成**按周 + 按天双份**（`version 2`）：

```json
"days": {
  "2026-10-16": {
    "apps": { "chrome.exe": {"name":"Chrome","seconds":420,"sites":{"B站":420}} },
    "study_sec": 1800, "chats": 3, "pets": 2, "launches": 1
  }
}
```

| 时间 | 现象 | 修复 |
|---|---|---|
| 2026-10-16 | 日记全是"用了 X 小时"；某天的日记补不回来 | `add()` 同时写周桶与日桶；新增 `day_data()` / `day_top_apps()` / `day_top_sites()` / `day_busy_sec()` / `bump_day()` / `add_day_study()` |

**通用教训**：**做统计功能时先问"这条数据能不能按天还原"**。
只有累计值的数据，事后想做逐日分析是**不可能**的——必须一开始就按天存。

---

## 跨周重置会清掉整个文件（连按天明细一起丢）

**根因**：`_load()` 发现跨周时执行 `self.data = self._empty()` ——
**整个文件重置**。原来只有周数据，这样没问题；但加了按天明细后，
**上周末尾的日记素材会被一起丢掉**。

**另一个相关隐患**：落盘间隔是 60 秒，而**退出时没有落盘处理**。
程序跑不到 60 秒就关掉 → 当天记录**全留在内存里丢掉**。
（这也可能是"某天日记凭空消失"的原因之一。）

**修法**：

```python
# 跨周：只清周累计，按天明细带过去
self.data = self._empty()
self.data['last_week'] = {...}
self.data['days'] = days        # ⬅ 关键

# 退出兜底落盘
import atexit
atexit.register(self.flush, True)
```

**验证**（`_aware_regress_test.py` 里有对应断言）：

```
跨周后本周累计清空  ✓
跨周后**按天明细保留**  ✓
```

**通用教训**：**给已有数据结构加字段时，要检查所有"重置/清空"路径**
会不会把它一起干掉。

---

## 版本升级只在"跨周"分支落盘 → 文件死活不变 v2

**根因**：`_load()` 里只有跨周分支调 `flush()`。同周内加载老文件
（`version=1`、没有 `days`）时，升级后的结构**只留在内存里**，
文件还是老样子。

**表现**：`activity_log.json` 改了代码后**依然是 `version=1 days=0`** ——
按天明细也就一直没被建立。

**修法**：

```python
if int(old.get('version') or 1) < 2:
    self.data['version'] = 2
    self._dirty = True
    self.flush(force=True)      # ⬅ 立即落盘
```

**教训**：**数据结构迁移必须显式落盘**，不能依赖"下次反正会写"。

---

## 测试删掉了用户的真实日记（测试污染真实数据）

这条最该记：**跑一次测试，把用户的历史日记删了。**

**根因**（两个独立问题叠在一起）：

```python
# 问题 1：测试复用了真实 DesktopPet，
#         假素材（Chrome 7200 秒等 fixture）写进了真实 activity_log.json
pet.activity.data['apps'] = {'chrome.exe': {'seconds': 7200, ...}}

# 问题 2：T1 的清理代码删的是 pet.diary.path —— 而它指向**真实文件**
for path in (pet.diary.path, config.WEATHER_CACHE_PATH):
    if os.path.exists(path):
        os.remove(path)          # ⬅ 删掉了用户的 diary.json
```

**修法**：

1. `setup_diary(path=None)` 支持覆盖存储位置；测试传临时路径
2. 测试里把 `activity.path` 也改到临时目录
3. 清理只 `shutil.rmtree(_tmpdir)`，**真实路径一个都不动**
4. 加**防回归断言**：测试跑完检查真实 `diary.json` 里没有今天的条目

**验证**（哈希对比）：

```
测试前 diary.json  MD5 = A15DA0AFEFCA865ABD308CD19E81BA44
跑日记测试 4 次
测试后 diary.json  MD5 = A15DA0AFEFCA865ABD308CD19E81BA44   ✓ 未被改动
```

**教训**：

- **测试绝不允许写用户的真实数据文件**。任何"清理"代码都要问：
  "这个路径是临时的还是真实的？"
- **清理代码比被测代码更危险**——它跑在 `finally`/收尾里，
  出错时不容易被注意到。

---

## `vendor/` 清理误删必需包（三次）

清理克隆依赖（6.3 GB）时做了"**先隔离 → 自检 → 通过才删**"，
这个流程**抓到了三个真问题**。三次都是**只看 `sys.modules` 不够**：

| # | 误删的东西 | 报错 | 根因 |
|---|---|---|---|
| 1 | `numpy.libs`（原生 DLL 目录）| `ImportError: DLL load failed while importing _multiarray_umath` | **原生 DLL 目录不在 import 闭包里**，追踪不到 |
| 2 | `typing_extensions.py`（单文件模块）| `ModuleNotFoundError: No module named 'typing_extensions'` | 名字解析漏了去 **`.py` 后缀** |
| 3 | `attr`（`attrs` 的导入名）| `ModuleNotFoundError: No module named 'attr'` → 连带 `edge_tts`/`aiohttp` 全挂 | **包名 ≠ 导入名**（`attrs` 导入叫 `attr`） |

**最终做法**：

1. **AST 解析**源码的真实 import（正则会误匹配，抽出过 `"try, soundcard, subprocess"` 这种垃圾）
2. **递归追踪**依赖闭包（对比 `sys.modules` 快照）
3. 加"**原生库目录永不隔离**"规则（`.libs` / `bin` / `share` / `*.pyd` / `*.dll`）
4. `_base_name()` 要处理 `.dist-info` / `.egg-info` / **`.py`** 三种形态
5. 隔离前后各跑一次**导入自检**，失败就 `--restore`

**通用教训**：**包名、导入名、目录名是三件不同的事。**
判定"这个包有没有被用到"，**必须看真实的 import 图，不能猜名字**。

---

## PowerShell 写文件把中文搞成乱码

**现象**：用 `(Get-Content x.py -Raw) -replace ... | Set-Content x.py -Encoding UTF8`
改含中文的 Python 文件后，文件变成乱码：

```
鐢熸垚 XTTS 鍙傝€冮煶棰?...
SyntaxError: invalid character '€' (U+20AC)
```

**根因**：Windows PowerShell 的 `Set-Content -Encoding UTF8` 会**加 BOM**，
且在某些版本下按 ANSI 读取源文件——中文就毁了。

**修法**：**不要用 PowerShell 批量改含中文的源码。**

- 用本工具（`edit` / `write`）改
- 或让 **Python 脚本**做替换（Python 的 `open(..., encoding='utf-8')` 不会骗你）

**教训**：处理非 ASCII 源码时，**编码边界越少越好**。

---

## `except Exception` 吞掉 `NameError`：静态检查的必要性

**根因**：项目里大量使用"依赖缺失就降级"的写法：

```python
try:
    import edge_tts
    HAS_EDGE_TTS = True
except ImportError:
    HAS_EDGE_TTS = False
```

但 `except Exception` 写在业务逻辑外层时，会把 **`NameError`
（写错变量名）也一起吞掉**，表现为"功能默默不工作"。

**实例**：`synth()` 用了 `SPEECH_VOICE`，但 config 里只有
`Config.DEFAULTS['speech_voice']`，**没有模块级同名常量** →
`AttributeError` 被吞 → 表现是"合成失败"，其实 mp3 已经生成了。

**修法**：

1. 把**所有** `HAS_*` 依赖探测**集中到 `config.py` 一处**
   （拆分时正是因为分散，导致过 `NameError: name 'HAS_PIL' is not defined`）
2. **`_namecheck_test.py`**：静态找出各模块"引用了但没定义"的全局名
   —— 这类错误运行时会被 `except` 掩盖，只有静态检查能一次抓全

**教训**：**`except Exception` 是必要的，但要配合静态检查兜底。**
否则"优雅降级"会变成"静默失效"。

---

## 功能藏起来等于没有（日记删除按钮不可见）

**用户反馈**：**"我看不见日记的删除在哪。"**

**根因**：删除功能**一直都在**，但做成了日记面板**左下角的两个小按钮**：

```
┌──────────────┬─────────────────────┐
│ 日期列表      │  正文                │
│              │                     │
├──────────────┴─────────────────────┤
│ [删除这一天] [清空全部]      [关闭]  │   ← 用户没注意到这里
└────────────────────────────────────┘
```

用户的目标是"删某一天的日记"，视线自然落在**日期列表**上，
不会去扫窗口底部的一排按钮。**功能存在 ≠ 用户找得到。**

**修法**：改成**在日期上右键**，并加常驻提示：

```python
def on_right_click(evt):
    i = lb.nearest(evt.y)          # 离点击位置最近的行
    if i is None or i < 0 or i >= lb.size():
        return
    d = lb.get(i)
    lb.selection_clear(0, 'end')   # 先选中，让用户看清删的是哪天
    lb.selection_set(i)
    show()
    menu = tk.Menu(win, tearoff=0)
    menu.add_command(label=f'🗑  删除「{d}」',
                     command=lambda dd=d: delete_one(dd))
    menu.add_separator()
    menu.add_command(label=f'清空全部（{self.diary.count()} 篇）',
                     command=delete_all)
    menu.tk_popup(evt.x_root, evt.y_root)
```

配套改动：

| 改动 | 原因 |
|---|---|
| 底部加常驻提示 `💡 在某一天上右键即可删除` | **这是关键** —— 右键操作本身也不易发现 |
| 双击日期也问"要删吗" | 点它多半就是想删 |
| **加了确认框** | 不可撤销的操作 + 用户曾误删过一天 |
| `Button-3` 和 `Button-2` 都绑定 | Windows 右键是 `Button-3`，兼容其它平台 |

**踩到的测试问题**：`tk_popup()` 会进入 Tk 的**菜单抓取循环**，
自动化测试里**会卡住**。所以测试里拦掉 `tk_menu.tk_popup`，
只记录"菜单被弹出了"，再直接 `menu.invoke(0)` 验证行为：

```python
_popped = []
_orig = tk.Menu.tk_popup
tk.Menu.tk_popup = lambda self, x, y, entry='': _popped.append(self)
...
lb.event_generate('<Button-3>', x=5, y=行中心, rootx=200, rooty=200)
# 断言：菜单弹出、该行被选中、但**没有删掉任何东西**
# 然后 menu.invoke(0)：先测"取消"不删，再测"确认"才删
```

> 注意 `event_generate` 的参数是 `rootx` / `rooty`（不是 `x_root`），
> 而且**不允许 `Double` 修饰符**（`<Double-Button-1>` 会报
> `Double, Triple, or Quadruple modifier not allowed`）。

**教训**：

- **交互设计要顺着用户的视线**。删除针对"某一天"，
  入口就该在"那一天"上（右键/悬停/双击），而不是窗口角上的按钮。
- **快捷键/右键这类隐形交互必须配可见提示**，
  否则等于把它也藏起来。
- 不可撤销的操作**要有确认框**，尤其是用户已经误删过一次的时候。

---

## 语音引擎决定了对话内容（导致只能配对、不能真对话）

**用户反馈**：**"我现在本地应该能与本地 llm 模型对话，但是有个语音触发的
功能导致我不能和本地 llm 对话了，只能在本地语音库中配对。我不要这样配，
我要本地真实 llm 对话，本地库只是用于闲聊自动说的。"**

**根因**：`on_send` 里把两个**正交**的维度耦合在了一起：

```python
use_llm = mode == 'deepseek' or getattr(self, '_speech_engine_name', '') == 'edge'
reply = self._deepseek_call(...) if use_llm else self._local_reply()
```

原意是"神经语音什么文字都能念，所以本地模式也不必迁就原声库"。
但写成 `or` 之后语义变成：

| dialogue_mode | speech_engine | 实际走哪 |
|---|---|---|
| local | hybrid | **本地库配对** ← 用户遇到的 |
| local | edge | 真 LLM |
| deepseek | 任意 | 真 LLM |

于是**"出不出声"的选择（speech_engine）悄悄决定了"内容从哪来"**。
用户把引擎设成 `hybrid`（只用原声）之后，即使对话模式是 LLM 也被拉回配对。

**修法**：两个维度彻底拆开，各管各的：

| 维度 | 由谁决定 | 实现位置 |
|---|---|---|
| **对话内容**从哪来 | `dialogue_mode` | `DialogueMixin._llm_call()` |
| **出不出声** | `speech_engine` | `speech.py` 各引擎的 `synth()` |

```python
# 现在：内容只由 dialogue_mode 决定
llm_reply = self._llm_call(mode, sys_prompt, snapshot)   # local 模式返回 None
reply = self._local_reply() if llm_reply is None else llm_reply
```

顺带把对话模式扩成三个（并新增**本地模型**这条不花钱的路）：

| `dialogue_mode` | 内容来源 | 花钱 | 需要 key |
|---|---|---|---|
| `deepseek` | DeepSeek 云端 | 有免费额度 | 是 |
| `local_llm` | Ollama / LM Studio（本地）| **不花钱** | **否** |
| `local` | 台词库检索配对 | 不花钱 | 否 |

> 三个后端的 `/chat/completions` 都是 **OpenAI 兼容**格式，
> 请求体完全一样，所以调用代码共用一个 `_llm_call()`，
> 差别只有 URL / 模型名 / 要不要 key。

**配套修正**：

| 问题 | 修法 |
|---|---|
| LLM 回复没音频 → 静音，用户以为坏了 | 文档写清："只用原声"模式下 LLM 的话**只冒气泡不出声**，想听见就切 `edge` |
| 本地模型连不上时只报 HTTP 错误 | 给出人话提示（"确认 Ollama 已启动、模型名对不对"）+ 菜单里的「测试连接」按钮真的发一次请求 |
| `Config` 没有 `.get()` 方法 | 我第一版写了 `self.cfg.get('local_llm_url')` → `AttributeError: 'Config' object has no attribute 'get'`。改成 `self.cfg['...']`（`__getitem__` 内部就是 `dict.get`）|

**防复发测试**（`tests/_speech_regress_test.py` 的 T13，共 12 项）：

```
deepseek  + hybrid -> deepseek      ✓
deepseek  + edge   -> deepseek      ✓
local_llm + hybrid -> local_llm     ✓
local_llm + edge   -> local_llm     ✓
local     + hybrid -> local_lib     ✓
local     + edge   -> local_lib     ✓
worker 里不再引用 speech_engine       ✓
```

**教训**：

- **两个正交的概念不要写进同一个布尔表达式。**
  "内容来源"和"输出方式"是独立的，用 `or` 连起来就制造了隐形耦合。
- **`A or B` 型的条件要做决策矩阵。** 当时只想到"deepseek 要调 LLM"，
  没意识到 `speech_engine` 会反向影响 `dialogue_mode='local'` 的行为。
  列一张 (A, B) → 结果的表，6 个格子一眼就能看出问题。
- **测试要盯着"耦合"本身**，不只是功能是否可用。
  这个 bug 不会让任何功能报错，只是**静默走错分支** ——
  只有专门的"路由矩阵"测试能抓住。




