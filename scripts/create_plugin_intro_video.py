# -*- coding: utf-8 -*-
"""Build a narrated introduction video for astrbot_plugin_private_companion.

The script uses the local AstrBot Fish Audio provider configuration at runtime,
but never writes the API key to an output file.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import httpx
import msgpack
from PIL import Image, ImageDraw, ImageFont, ImageFilter


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "video"
FONT = Path(r"C:\Windows\Fonts\NotoSansSC-VF.ttf")
FFMPEG = Path(r"C:\Users\99505\.astrbot\data\tools\bin\ffmpeg.exe")
CONFIG = Path(r"C:\Users\99505\.astrbot\data\cmd_config.json")

SCENES = [
    ("01_title", "把陪伴做成一段连续的生活", "astrbot_plugin_private_companion  ·  v6.6.0", "欢迎来到“我会永远陪着你”。它不是一组孤立的问候命令，而是让 AstrBot 拥有连续生活上下文的陪伴核心。"),
    ("02_context", "同一套上下文，贯穿每次互动", "状态  ·  日程  ·  关系  ·  场景", "插件把角色状态、日程、关系和当前场景放在同一条链路里。每次回复和主动消息，都先有依据，再决定是否开口。"),
    ("03_proactive", "主动消息，也有具体由头", "主动候选  →  额度与时机  →  发送前复核", "它会根据临近日程、刚发生的小事和共同话题生成候选，再经过免打扰时段、额度、冷却和隐私边界检查。合适才发，不合适就安静。"),
    ("04_group", "群聊不再只有关键词触发", "气氛  ·  群友  ·  话题线  ·  片段记忆", "在群聊里，插件可以观察群气氛、识别群友和黑话，跟住话题线，再决定自然续接还是保持沉默。"),
    ("05_memory", "记忆、日程与生活内容", "长期记忆可选  ·  日记  ·  梦境  ·  便签", "日程、睡眠、天气、梦境、日记和便签都可以成为生活线索。安装 Memory Companion 后，再把授权范围内的长期记忆接进来。"),
    ("06_multimodal", "文字之外，还有更多表达方式", "图片  ·  GIF 抽帧  ·  生图  ·  语音  ·  识屏", "多模态能力按需启用。图片、生图、识屏、设备和外部服务都要经过明确授权；扩展未安装时，入口会降级或隐藏。"),
    ("07_fish", "Fish Audio：让语音带着情绪", "S2.1 / S2 / S1  ·  自然平衡 / 更有表现力 / 手动", "TTS 强化负责选择朗读内容、分段和交付方式，Fish Audio 负责合成。S2.1 和 S2 使用方括号自然语言控制，S1 使用官方圆括号标签，控制词不会出现在聊天正文里。"),
    ("08_start", "从基础陪伴开始，逐项打开能力", "安装  →  配置主要用户  →  选择 TTS  →  按需启用扩展", "安装插件后，先配置主要用户、人格、日程和关系，再选择 Fish Audio 声线。最后按需要开启记忆、生图、识屏或其他扩展，这就是完整的陪伴工作流。"),
]


def font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    path = FONT if FONT.exists() else Path(r"C:\Windows\Fonts\simhei.ttf")
    return ImageFont.truetype(str(path), size)


def rounded(draw: ImageDraw.ImageDraw, box, radius, fill, outline=None, width=1):
    draw.rounded_rectangle(box, radius=radius, fill=fill, outline=outline, width=width)


def text_block(draw, xy, text, size, fill=(240, 244, 250), max_width=1120, line_gap=12, bold=False):
    f = font(size, bold)
    words = list(text)
    lines, line = [], ""
    for ch in words:
        test = line + ch
        if draw.textbbox((0, 0), test, font=f)[2] > max_width and line:
            lines.append(line)
            line = ch
        else:
            line = test
    if line:
        lines.append(line)
    x, y = xy
    for item in lines:
        draw.text((x, y), item, font=f, fill=fill)
        y += size + line_gap
    return y


def make_scene(index: int, slug: str, title: str, subtitle: str, narration: str, path: Path):
    w, h = 1920, 1080
    img = Image.new("RGB", (w, h), (15, 19, 26))
    draw = ImageDraw.Draw(img)
    # restrained, non-gradient background with a warm accent rail
    draw.rectangle((0, 0, 20, h), fill=(238, 138, 86))
    draw.rectangle((20, 0, w, 10), fill=(55, 67, 84))
    draw.ellipse((1460, -260, 2040, 320), fill=(29, 39, 52))
    draw.ellipse((1560, 760, 2140, 1340), fill=(27, 35, 45))
    draw.text((120, 100), f"{index:02d} / 08", font=font(26), fill=(238, 138, 86))
    draw.text((120, 190), title, font=font(72, True), fill=(247, 249, 252))
    draw.text((120, 300), subtitle, font=font(30), fill=(170, 184, 201))
    text_block(draw, (120, 410), narration, 35, max_width=980, line_gap=16)

    # right-side product-style diagram
    panel = (1190, 150, 1770, 900)
    rounded(draw, panel, 18, (27, 34, 44), outline=(76, 92, 112), width=2)
    draw.text((1240, 205), "陪伴面板 · 功能示意", font=font(28, True), fill=(247, 249, 252))
    if slug == "01_title":
        draw.text((1240, 330), "我会永远陪着你", font=font(48, True), fill=(238, 138, 86))
        draw.text((1240, 410), "持续型 AI 陪伴核心", font=font(30), fill=(210, 220, 232))
        rounded(draw, (1240, 520, 1695, 600), 12, (41, 51, 65), outline=(85, 103, 126))
        draw.text((1270, 542), "状态 · 日程 · 关系 · 记忆", font=font(25), fill=(210, 220, 232))
    else:
        labels = {
            "02_context": ["当前状态", "今日计划", "关系阶段", "场景快照"],
            "03_proactive": ["候选: 分享窗外的雨", "额度检查 ✓", "免打扰检查 ✓", "发送前复核 ✓"],
            "04_group": ["群气氛  活跃", "话题线  周末安排", "群友识别  12", "自然续接  可用"],
            "05_memory": ["今日状态", "梦境碎片", "日记 / 便签", "长期记忆  已授权"],
            "06_multimodal": ["文字  ✓", "图片  按需", "生图  扩展", "识屏  授权"],
            "07_fish": ["Provider  Fish Audio", "模型  s2.1-pro-free", "情绪  自然平衡", "输出  语音 + 文字"],
            "08_start": ["1  安装插件", "2  配置主要用户", "3  选择 TTS 声线", "4  按需启用扩展"],
        }.get(slug, ["可配置", "可审计", "可降级", "可扩展"])
        y = 300
        for n, label in enumerate(labels):
            color = (238, 138, 86) if n == 0 else (78, 154, 125)
            rounded(draw, (1240, y, 1695, y + 80), 12, (39, 49, 62), outline=color, width=2)
            draw.ellipse((1265, y + 27, 1289, y + 51), fill=color)
            draw.text((1310, y + 22), label, font=font(25), fill=(226, 233, 241))
            y += 105
    draw.text((120, 970), "astrbot_plugin_private_companion  ·  公开功能导览", font=font(22), fill=(125, 140, 158))
    img.save(path, quality=95)


def load_provider() -> dict:
    data = json.loads(CONFIG.read_text(encoding="utf-8-sig"))
    for item in data.get("provider", []):
        if item.get("id") == "fishaudio_tts2-zh":
            return item
    raise RuntimeError("未找到本地 Fish Audio Provider: fishaudio_tts2-zh")


def synthesize(text: str, provider: dict, out_path: Path) -> str:
    payload = {
        "text": text,
        "chunk_length": 200,
        "format": "wav",
        "mp3_bitrate": 128,
        "references": [],
        "reference_id": provider.get("fishaudio-tts-reference-id"),
        "normalize": True,
        "latency": "normal",
    }
    headers = {
        "Authorization": f"Bearer {provider['api_key']}",
        "model": provider.get("model", "s2.1-pro-free"),
        "content-type": "application/msgpack",
    }
    try:
        with httpx.Client(base_url=provider.get("api_base", "https://api.fish.audio/v1"), timeout=8) as client:
            response = client.post("/tts", headers=headers, content=msgpack.packb(payload, use_bin_type=True))
            response.raise_for_status()
            if not response.headers.get("content-type", "").startswith("audio/"):
                raise RuntimeError(f"Fish Audio 返回非音频: {response.text[:200]}")
            out_path.write_bytes(response.content)
            return "Fish Audio"
    except Exception as exc:
        # Keep the video reproducible when an offline workstation cannot reach Fish Audio.
        ps = (
            "Add-Type -AssemblyName System.Speech; "
            "$s=New-Object System.Speech.Synthesis.SpeechSynthesizer; "
            "$s.SelectVoice('Microsoft Huihui Desktop'); "
            f"$s.SetOutputToWaveFile('{str(out_path).replace(chr(39), chr(39)+chr(39))}'); "
            f"$s.Speak('{text.replace(chr(39), chr(39)+chr(39))}'); $s.Dispose()"
        )
        subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        print(f"Fish Audio 不可达，已回退 Microsoft Huihui: {type(exc).__name__}")
        return "Microsoft Huihui (offline fallback)"


def run(output: Path):
    output.parent.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="private_companion_video_"))
    try:
        provider = load_provider()
        image_paths, audio_paths, sources = [], [], []
        for idx, (slug, title, subtitle, narration) in enumerate(SCENES, 1):
            image = work / f"{slug}.png"
            audio = work / f"{slug}.wav"
            make_scene(idx, slug, title, subtitle, narration, image)
            sources.append(synthesize(narration, provider, audio))
            image_paths.append(image)
            audio_paths.append(audio)
        # Concatenate narration first, then use the same durations for each still.
        audio_list = work / "audio.txt"
        audio_list.write_text("\n".join(f"file '{p.as_posix()}'" for p in audio_paths), encoding="utf-8")
        full_audio = work / "narration.wav"
        subprocess.run([str(FFMPEG), "-y", "-f", "concat", "-safe", "0", "-i", str(audio_list), "-ar", "44100", "-ac", "2", str(full_audio)], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        # Hold each frame for its narration duration, with a small readable tail.
        durations = []
        for audio in audio_paths:
            probe = subprocess.run([str(FFMPEG), "-i", str(audio)], capture_output=True, text=True)
            import re
            match = re.search(r"Duration: (\d+):(\d+):(\d+\.\d+)", probe.stderr)
            seconds = int(match.group(1)) * 3600 + int(match.group(2)) * 60 + float(match.group(3)) if match else 8.0
            durations.append(max(seconds + 0.35, 3.0))
        concat = work / "slides.txt"
        lines = []
        for image, duration in zip(image_paths, durations):
            lines += [f"file '{image.as_posix()}'", f"duration {duration:.3f}"]
        lines.append(f"file '{image_paths[-1].as_posix()}'")
        concat.write_text("\n".join(lines), encoding="utf-8")
        subprocess.run([str(FFMPEG), "-y", "-f", "concat", "-safe", "0", "-i", str(concat), "-i", str(full_audio), "-vf", "format=yuv420p", "-r", "30", "-c:v", "libx264", "-c:a", "aac", "-b:a", "192k", "-shortest", str(output)], check=True)
        # Keep a reviewable copy of the exact script inputs, without secrets.
        manifest = output.with_suffix(".json")
        manifest.write_text(json.dumps({"title": "我会永远陪着你：插件完整介绍", "scenes": [{"slug": s[0], "title": s[1], "subtitle": s[2], "narration": s[3], "audio_source": sources[i]} for i, s in enumerate(SCENES)], "tts": {"provider": "Fish Audio", "model": provider.get("model"), "character": provider.get("fishaudio-tts-character"), "reference_id": provider.get("fishaudio-tts-reference-id")}}, ensure_ascii=False, indent=2), encoding="utf-8")
    finally:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=OUT / "private_companion_intro.mp4")
    args = parser.parse_args()
    run(args.output)
