# -*- coding: utf-8 -*-
"""气泡分页 + 毛玻璃渲染 冒烟测试（临时）"""
import os, sys, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import app
from config import *

pet = app.DesktopPet()
pet.root.withdraw()
for _ in range(30): pet.root.update(); time.sleep(0.02)

ok = True
def check(name, cond, extra=''):
    global ok
    if not cond: ok = False
    print(f"  [{'PASS' if cond else 'FAIL'}] {name} {extra}")

print('=== 1. 分句/分页 ===')
S = pet._split_sentences
long_text = ("今天我们一起去了好远好远的地方，一路上月亮都跟着我们走，"
             "后来你在草地上睡着了，我就坐在旁边看着你，哪儿也没有去。")
pages = S(long_text)
print('  分页结果：')
for p in pages: print(f'    [{len(p):2d}] {p}')
check('每页不超过单屏容量', all(len(p) <= BUBBLE_PAGE_CHARS for p in pages))
check('长句按逗号切屏', len(pages) >= 2, f'共 {len(pages)} 页')
check('标点保留在页内', all(p[-1] in '，。！？；、：' for p in pages if len(p) > 2))
no_punct = '这是一段完全没有标点特别长的文本就为了测试硬切逻辑是否正常工作呀'
pages2 = S(no_punct)
check('无逗号硬切且不超容量', all(len(p) <= BUBBLE_PAGE_CHARS for p in pages2) and len(pages2) >= 2)
short = '……嗯。'
check('短句不拆', S(short) == [short])
check('空文本', S('  ') == [])

print('=== 2. 毛玻璃渲染 ===')
pil = pet._bubble_render_pil('你好呀，今天也要加油哦！', BUBBLE_FG)
check('渲染出图', pil is not None and pil.size == (BUBBLE_W, BUBBLE_H))
# 导出预览（品红底 → 换成白底目检圆角/星星/换行）
from PIL import Image
prev = Image.new('RGB', pil.size, (40, 35, 55))
prev.paste(pil, (0, 0), pil.point(lambda v: 0 if v == (255, 0, 255) else 255).convert('L').point(lambda a: 255 if a else 0))
# 简单：用 alpha 版直接贴
rgba = pet._bubble_render_pil('今天天气很好呀，我们一起去散步吧，顺便看看月亮！', BUBBLE_FG).convert('RGB')
mag = Image.new('RGB', rgba.size, (255, 0, 255))
diff = Image.new('L', rgba.size, 255)
px, pm = rgba.load(), mag.load()
for yy in range(rgba.size[1]):
    for xx in range(rgba.size[0]):
        if px[xx, yy] == (255, 0, 255): diff.putpixel((xx, yy), 0)
preview = Image.new('RGB', (BUBBLE_W * 2 + 30, BUBBLE_H + 20), (58, 52, 74))
preview.paste(rgba, (10, 10), diff)
rgba2 = pet._bubble_render_pil('……嗯，我在听。', BUBBLE_FG).convert('RGB')
preview.paste(rgba2, (BUBBLE_W + 20, 10), Image.eval(rgba2.point(lambda v: 0 if v == 255 else v).convert('L'), lambda a: a))
out = os.path.join(os.environ.get('TEMP', '.'), 'bubble_frost_preview.png')
preview.save(out)
print(f'  预览已导出: {out}')
check('长文本渲染不超 3 行',
      len(pet._wrap_bubble_text(None, '今' * 30, pet._bubble_font, BUBBLE_W - 14)) <= 3
      if False else True)  # 占位，下面用真实 draw 测
from PIL import ImageDraw, Image
d = ImageDraw.Draw(Image.new('RGB', (10, 10)))
lines = pet._wrap_bubble_text(d, '今' * 30, pet._bubble_font, BUBBLE_W - 14)
check('30 字无标点渲染 ≤ 3 行', len(lines) <= 3, f'实际 {len(lines)} 行')

print('=== 3. 真实打字链路（FrostBubbleLabel）===')
pet._speak(long_text, interrupt=True)
check('状态 typing', pet.dlg_state == 'typing')
check('cget(text) 返回前缀', len(pet.bubble_label.cget('text')) <= len(pages[0]))
check('队列是分页结果', pet.dlg_queue == pages[1:])
# 泵到打完第一页
t0 = time.time()
while pet.dlg_state != 'waiting' and time.time() - t0 < 5:
    pet.root.update(); time.sleep(0.02)
check('第一页打完文字完整', pet.bubble_label.cget('text') == pages[0])
# 淡出（fg 插值不炸）
pet.dlg_state = 'fading'; pet.dlg_fade_start = time.time() - 0.4
pet._update_dialogue()
pet.root.update()
check('淡出后气泡隐藏且回 idle', pet.dlg_state in ('idle',) )
check('cget(fg) 正常', pet.bubble_label.cget('fg') == BUBBLE_FG)
# 整段翻页跑完
pet._speak('第一句，第二句，第三句，第四句，第五句，这是一条特别长的测试文本用来确认逗号换屏能顺利翻页结束。', interrupt=True)
t0 = time.time()
while pet.dlg_state != 'idle' and time.time() - t0 < 40:
    pet.root.update(); time.sleep(0.02)
check('整段说完自动回 idle', pet.dlg_state == 'idle')
final_pages = pet._split_sentences('第一句，第二句，第三句，第四句，第五句，这是一条特别长的测试文本用来确认逗号换屏能顺利翻页结束。')
check('每页 ≤ 容量（长链路）', all(len(p) <= BUBBLE_PAGE_CHARS for p in final_pages))

pet.root.destroy()
print('\n结果:', 'ALL_PASS' if ok else 'SOME_FAILED')
sys.exit(0 if ok else 1)
