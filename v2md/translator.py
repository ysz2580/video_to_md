"""双语第二轨翻译器：把英文(或其它语言)主轨字幕批量译成中文。

仅用于「原音为英文(非中文)且要双语」的情况——whisper 的 task=translate 只能译成英文，
做不了英→中，故用 OpenAI 兼容 chat API 批量翻译。

中文原音的中→英走 whisper translate（见 subtitle.py），不经过本模块。

配置见 config.py：TRANSLATE_BASE_URL / TRANSLATE_API_KEY / TRANSLATE_MODEL。
未配置 KEY 时 is_available()=False，调用方应跳过（仅生成原音主轨）。
"""
from __future__ import annotations

import logging
import re
from typing import Callable, Optional

import config
from v2md.models import SubtitleSegment

log = logging.getLogger(__name__)


def is_available() -> bool:
    return bool(getattr(config, "TRANSLATE_API_KEY", None))


def _chat(messages: list[dict]) -> str:
    """调用 OpenAI 兼容 /chat/completions，返回内容文本。"""
    import httpx
    base = (config.TRANSLATE_BASE_URL or "").rstrip("/")
    url = base + "/chat/completions"
    headers = {"Authorization": f"Bearer {config.TRANSLATE_API_KEY}",
               "Content-Type": "application/json"}
    body = {"model": config.TRANSLATE_MODEL, "messages": messages, "temperature": 0.2}
    with httpx.Client(timeout=120) as cli:
        r = cli.post(url, headers=headers, json=body)
        r.raise_for_status()
    data = r.json()
    return data["choices"][0]["message"]["content"].strip()


def translate_to_zh(segs: list[SubtitleSegment],
                    on_progress: Optional[Callable[[int, int], None]] = None
                    ) -> list[str]:
    """把字幕段批量译成中文，返回与 segs 等长的中文文本列表（保持时间戳不变）。

    分批（每批 ~60 段）调用，提示模型逐行翻译、严格保持行数与顺序。
    """
    n = len(segs)
    out: list[str] = [""] * n
    if n == 0:
        return out
    chunk = 60
    for start in range(0, n, chunk):
        batch = segs[start:start + chunk]
        src_lines = [s.text for s in batch]
        prompt_src = "\n".join(f"{i+1}. {t}" for i, t in enumerate(src_lines))
        messages = [
            {"role": "system", "content": "你是专业字幕翻译。把用户给的每行字幕译成简体中文，"
             "保持编号与顺序，每行输出对应中文译文，格式 '编号. 译文'，不要多余解释。"
             "若原文已是中文则原样返回。"},
            {"role": "user", "content": prompt_src},
        ]
        text = _chat(messages)
        # 解析 '编号. 译文'
        lines = {}
        for ln in text.splitlines():
            m = re.match(r"\s*(\d+)\.\s*(.+)", ln)
            if m:
                lines[int(m.group(1))] = m.group(2).strip()
        for i in range(len(batch)):
            out[start + i] = lines.get(i + 1, "") or batch[i].text
        if on_progress:
            on_progress(start + len(batch), n)
    return out
