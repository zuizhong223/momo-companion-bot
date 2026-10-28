# -*- coding: utf-8 -*-
"""历史瘦身（P1-9 费用）：把历史里内联的 base64 媒体块换成文字占位。

背景：2026-10-24 的三条用户消息把整张图片以 data:image/jpeg;base64 的形式存进了
conversations.content，历史体积从 ~18KB 涨到 532KB（≈26 万 token），每一轮请求
都要把这坨 base64 重新发给 deepseek-chat，既超上下文又白烧钱。

规则：
  - data: URL 且长度 > INLINE_LIMIT 的 image_url/audio_url 段 → 替换成 "[图片]"/"[语音]"
  - 普通 http(s) 链接或短 data URL → 原样保留
  - 一条消息里的媒体段被替换后如果没有任何文本，补一个占位文本，避免空消息
"""
from __future__ import annotations

INLINE_LIMIT = 1000
MEDIA_KINDS = {
    "image_url": ("[图片]", ("image_url", "image", "input_image")),
    "audio_url": ("[语音]", ("audio_url", "audio", "input_audio")),
}


def _part_text(part: dict) -> str:
    t = part.get("text")
    if isinstance(t, str):
        return t
    for key in ("image_url", "input_image", "audio_url", "input_audio"):
        v = part.get(key)
        if isinstance(v, dict):
            u = v.get("url") or v.get("data") or ""
            if isinstance(u, str) and u:
                return u
        elif isinstance(v, str):
            return v
    return ""


def _is_inline_heavy(part: dict) -> tuple[bool, str]:
    if not isinstance(part, dict):
        return False, ""
    ptype = part.get("type")
    for kind, (placeholder, _aliases) in MEDIA_KINDS.items():
        if ptype == kind or (isinstance(ptype, str) and ptype.startswith(kind)):
            blob = _part_text(part)
            if blob.startswith("data:") and len(blob) > INLINE_LIMIT:
                return True, placeholder
    # 某些实现把整段 base64 塞进 text
    if ptype in ("text", None):
        blob = _part_text(part)
        if blob.startswith("data:") and len(blob) > INLINE_LIMIT:
            return True, "[图片]"
    return False, ""


def strip_inline_media(messages: list) -> tuple[list, int]:
    """返回 (瘦身后的消息列表, 处理的段数)。不修改入参。"""
    out: list = []
    stripped = 0
    for msg in messages:
        if not isinstance(msg, dict):
            out.append(msg)
            continue
        content = msg.get("content")
        if not isinstance(content, list):
            out.append(msg)
            continue
        new_content: list = []
        replaced_any = False
        for part in content:
            hit, placeholder = _is_inline_heavy(part)
            if hit:
                new_content.append({"type": "text", "text": placeholder})
                replaced_any = True
                stripped += 1
            else:
                new_content.append(part)
        if replaced_any and not any(
            isinstance(p, dict) and p.get("type") == "text" and (p.get("text") or "").strip()
            for p in new_content
        ):
            new_content.append({"type": "text", "text": "[图片]"})
        out.append({**msg, "content": new_content})
    return out, stripped


def estimate_chars(messages: list) -> int:
    import json

    return len(json.dumps(messages, ensure_ascii=False))


def strip_excluded(messages: list) -> list:
    """过滤带 excluded 标记的消息（测试期间写入/作废的历史）。

    只影响“喂给模型”的上下文，**原文仍留在数据库里**（P1-5 的要求）。
    """
    if not isinstance(messages, list):
        return messages
    return [m for m in messages
            if not (isinstance(m, dict) and m.get("excluded"))]
