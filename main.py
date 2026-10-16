# -*- coding: utf-8 -*-
"""
哥伦比娅 · 桌面宠物  —— 入口
================================
本文件只做一件事：把桌宠启动起来。
所有实现都在各自的模块里：

    app.py               主类（组合各功能模块 + 主时钟）
    ui_widgets.py        窗口与控件、立绘、好感度
    walk.py              自主走动与帧动画
    interaction.py       拖拽、摸头、输入框
    chat.py              气泡与对话状态机
    dialogue_llm.py      输入处理、DeepSeek 调用、本地台词
    hourly.py            整点报时
    menu.py              右键菜单与设置项
    voice_control.py      全双工语音
    voice_engine.py 语音底座（收音 + VAD + 流式识别）
    song_engine.py       唱歌引擎
    song_control.py      唱歌交互
    config.py            全部常量、提示词、台词

修改参数/提示词/台词请编辑 config.py，不用翻业务代码。
"""

import sys


def _check_deps():
    """启动前给一句人话提示（缺依赖时功能降级，而不是直接崩）"""
    notes = []
    try:
        import app as _app
        if not _app.HAS_PIL:
            notes.append("未检测到 Pillow：立绘无法显示，请运行 install.bat")
        if not _app.HAS_REQUESTS:
            notes.append("未检测到 requests：DeepSeek 智能对话不可用")
        if not _app.HAS_MIC or not _app.HAS_SHERPA:
            notes.append("语音依赖不完整（soundcard / sherpa-onnx）：语音对话不可用")
        if not _app.HAS_SONG:
            notes.append("未检测到 miniaudio：唱歌功能不可用")
    except ImportError as e:
        notes.append(f"模块导入失败：{e}")
    for n in notes:
        print("提示：" + n)


def main():
    _check_deps()
    try:
        from app import DesktopPet
        DesktopPet().run()
    except Exception:
        import traceback
        traceback.print_exc()
        input("程序出错，按回车退出...")


if __name__ == '__main__':
    main()
