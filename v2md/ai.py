"""AI 供应商/模型抽象层：多供应商多模型，统一走 OpenAI 兼容 chat API。

设置存于 data/settings.json：
  providers: [{id,label,base_url,api_key,models:[{id,label}],default_model,type}]
  active_provider_id, active_model_id

translator.py 及后续 AI 功能（字幕清洗/章节/摘要/问答）都经本模块调用，
前端切换供应商/模型后即时生效（每次调用都现读 settings.json）。

内置模板 PRESETS：openai / deepseek / glm(智谱) / custom。
未配置任何供应商且有 legacy config.TRANSLATE_API_KEY 时，回退到单供应商旧配置。
"""
from __future__ import annotations

import json
import logging
import uuid
from typing import Optional

import config

log = logging.getLogger(__name__)

# 内置供应商模板：用户「添加供应商」时一键填好 base_url / 模型清单
PRESETS = [
    {"type": "openai", "label": "OpenAI",
     "base_url": "https://api.openai.com/v1",
     "models": [{"id": "gpt-4o-mini", "label": "GPT-4o mini"},
                {"id": "gpt-4o", "label": "GPT-4o"},
                {"id": "gpt-4.1-mini", "label": "GPT-4.1 mini"}],
     "default_model": "gpt-4o-mini"},
    {"type": "deepseek", "label": "DeepSeek",
     "base_url": "https://api.deepseek.com/v1",
     "models": [{"id": "deepseek-chat", "label": "DeepSeek Chat"},
                {"id": "deepseek-reasoner", "label": "DeepSeek R1"}],
     "default_model": "deepseek-chat"},
    {"type": "glm", "label": "智谱 GLM",
     "base_url": "https://open.bigmodel.cn/api/paas/v4",
     "models": [{"id": "glm-4-flash", "label": "GLM-4 Flash（免费档）"},
                {"id": "glm-4-air", "label": "GLM-4 Air"},
                {"id": "glm-4", "label": "GLM-4"}],
     "default_model": "glm-4-flash"},
    {"type": "custom", "label": "自定义",
     "base_url": "", "models": [], "default_model": ""},
]


def _settings_path():
    return config.DATA_DIR / "settings.json"


def _load() -> dict:
    p = _settings_path()
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save(d: dict) -> None:
    _settings_path().write_text(
        json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")


def providers() -> list[dict]:
    return _load().get("providers") or []


def active_provider() -> Optional[dict]:
    """当前生效供应商：按 active_provider_id 取，缺失则取第一个。"""
    d = _load()
    ps = d.get("providers") or []
    aid = d.get("active_provider_id")
    pr = next((p for p in ps if p.get("id") == aid), None)
    if not pr and ps:
        pr = ps[0]
    return pr


def active_model_id() -> Optional[str]:
    """当前生效模型：优先 active_model_id，其次 provider.default_model，再次第一个。"""
    d = _load()
    pr = active_provider()
    if not pr:
        return None
    mid = d.get("active_model_id")
    models = pr.get("models") or []
    if mid and any(m.get("id") == mid for m in models):
        return mid
    if pr.get("default_model"):
        return pr["default_model"]
    return models[0]["id"] if models else None


def active_config() -> Optional[dict]:
    """当前生效的 {base_url, api_key, model, label}；无供应商则回退 legacy config。"""
    pr = active_provider()
    if pr and pr.get("api_key"):
        return {"base_url": pr.get("base_url", ""),
                "api_key": pr["api_key"],
                "model": active_model_id(),
                "label": pr.get("label", "")}
    # 回退：单供应商旧配置（config.TRANSLATE_*，CLI 无 settings.json 时）
    if getattr(config, "TRANSLATE_API_KEY", None):
        return {"base_url": config.TRANSLATE_BASE_URL,
                "api_key": config.TRANSLATE_API_KEY,
                "model": config.TRANSLATE_MODEL,
                "label": "legacy"}
    return None


def is_available() -> bool:
    return active_config() is not None


def _mask(key: Optional[str]) -> Optional[str]:
    if not key:
        return None
    return key[:4] + "***" + key[-4:] + f"（{len(key)} 字符）"


def masked_providers() -> list[dict]:
    """供前端展示用：隐藏真实 key，仅给 masked。"""
    out = []
    for p in providers():
        q = dict(p)
        q["api_key_masked"] = _mask(p.get("api_key"))
        q.pop("api_key", None)
        out.append(q)
    return out


def new_id() -> str:
    return uuid.uuid4().hex[:10]


def chat(messages: list[dict], temperature: float = 0.2,
         timeout: int = 120) -> str:
    """调用当前生效供应商的 OpenAI 兼容 /chat/completions，返回内容文本。

    所有 AI 文本功能统一经此入口，切换供应商/模型即时生效。
    """
    cfg = active_config()
    if not cfg:
        raise RuntimeError("未配置 AI 供应商（设置面板添加）")
    if not cfg.get("model"):
        raise RuntimeError("当前供应商未选模型")
    import httpx
    base = (cfg["base_url"] or "").rstrip("/")
    url = base + "/chat/completions"
    headers = {"Authorization": f"Bearer {cfg['api_key']}",
               "Content-Type": "application/json"}
    body = {"model": cfg["model"], "messages": messages,
            "temperature": temperature}
    log.info("AI 调用 %s 模型 %s", cfg.get("label"), cfg["model"])
    with httpx.Client(timeout=timeout) as cli:
        r = cli.post(url, headers=headers, json=body)
        if r.status_code >= 400:
            raise RuntimeError(f"HTTP {r.status_code}: {r.text[:300]}")
    data = r.json()
    return data["choices"][0]["message"]["content"].strip()


def test_connection(provider: Optional[dict] = None) -> tuple[bool, str]:
    """对给定 provider（None=当前活动）发一条 max_tokens=1 的 ping，验证可用。

    provider.api_key 为空且 id 命中已存供应商时，借用已存 key 测试
    （供「编辑时未重输 key」场景）。
    """
    cfg = provider or active_config()
    if not cfg:
        return False, "未配置供应商"
    if not cfg.get("api_key"):
        # 编辑表单未重输 key：借用已存
        pid = cfg.get("id")
        old = next((p for p in providers() if p.get("id") == pid), None)
        if old and old.get("api_key"):
            cfg = {**cfg, "api_key": old["api_key"]}
        else:
            return False, "未填写 API Key"
    if not cfg.get("model"):
        return False, "未选模型"
    try:
        import httpx
        base = (cfg.get("base_url") or "").rstrip("/")
        url = base + "/chat/completions"
        headers = {"Authorization": f"Bearer {cfg['api_key']}",
                   "Content-Type": "application/json"}
        body = {"model": cfg["model"],
                "messages": [{"role": "user", "content": "ping"}],
                "max_tokens": 1}
        with httpx.Client(timeout=30) as cli:
            r = cli.post(url, headers=headers, json=body)
        if r.status_code >= 400:
            return False, f"HTTP {r.status_code}: {r.text[:200]}"
        return True, "连接正常"
    except Exception as e:
        return False, str(e)[:200]
