"""Local AI configuration shared by the web UI and the translation process."""

import json
import os
from pathlib import Path
from tempfile import NamedTemporaryFile
from urllib.parse import urlsplit

SETTINGS_FILE = Path(__file__).resolve().with_name(".ai-settings.json")
KEY_NAMES = {
    "gemini": "GEMINI_API_KEY",
    "openai": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "compatible": None,
}
DEFAULT_GEMINI_MODEL = "gemini-3.8-flash"


def read_settings():
    try:
        return json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"provider": "gemini", "profiles": {}}


def profile(settings, provider):
    saved = settings["profiles"].get(provider, {})
    model = os.getenv("GEMINI_MODEL", DEFAULT_GEMINI_MODEL) if provider == "gemini" else ""
    key_name = KEY_NAMES[provider]
    key = os.getenv(key_name, "").strip() if key_name else ""
    if key == "your_gemini_api_key_here":
        key = ""
    return {"model": model, "base_url": "", "api_key": key, **saved}


def public_settings(settings):
    profiles = {}
    for provider in KEY_NAMES:
        values = profile(settings, provider)
        profiles[provider] = {"model": values["model"], "base_url": values["base_url"],
                              "has_key": bool(values["api_key"])}
        if provider == "gemini":
            profiles[provider]["backup_key_count"] = len(values.get("backup_keys", []))
    return {"provider": settings["provider"], "profiles": profiles}


def requested_settings(body):
    if not isinstance(body, dict):
        raise ValueError("Cấu hình AI không hợp lệ.")
    provider = body.get("provider")
    if not isinstance(provider, str) or provider not in KEY_NAMES:
        raise ValueError("Nhà cung cấp AI không hợp lệ.")
    fields = {}
    for name in ("model", "base_url", "api_key"):
        value = body.get(name, "")
        if not isinstance(value, str) or len(value) > 2048 or (value and not value.isprintable()):
            raise ValueError("Cấu hình AI chứa giá trị không hợp lệ.")
        fields[name] = value.strip()
    if not fields["model"]:
        raise ValueError("Hãy nhập mã model.")
    if provider == "compatible":
        url = urlsplit(fields["base_url"])
        local = url.hostname in ("localhost", "127.0.0.1", "::1")
        if (not url.hostname or url.username is not None or url.query or url.fragment
                or (url.scheme != "https" and not (url.scheme == "http" and local))
                or any(c.isspace() for c in fields["base_url"])):
            raise ValueError("Base URL cần dùng HTTPS hoặc HTTP trên localhost.")
        fields["base_url"] = fields["base_url"].rstrip("/")
    else:
        fields["base_url"] = ""

    settings = read_settings()
    current = profile(settings, provider)
    clear_key = body.get("clear_key") is True
    if (provider == "compatible" and current["api_key"] and current["base_url"]
            and fields["base_url"] != current["base_url"] and not (clear_key or fields["api_key"])):
        raise ValueError("Khi đổi Base URL, hãy nhập key cho endpoint mới hoặc chọn Xóa key.")
    key = "" if clear_key else fields["api_key"] or current["api_key"]
    if key and (not key.isascii() or any(c.isspace() for c in key)):
        raise ValueError("API key chứa ký tự không hợp lệ.")
    if clear_key or fields["api_key"] or "api_key" in settings["profiles"].get(provider, {}):
        fields["api_key"] = key
    else:
        del fields["api_key"]
    if provider == "gemini":
        raw = body.get("backup_keys", "")
        if not isinstance(raw, str) or len(raw) > 20480:
            raise ValueError("Danh sách key dự phòng không hợp lệ.")
        keys = list(dict.fromkeys(value.strip() for value in raw.split(",") if value.strip()))
        if len(keys) > 10 or any(len(value) > 2048 or not value.isascii()
                                 or not value.isprintable() or any(c.isspace() for c in value) for value in keys):
            raise ValueError("Nhập tối đa 10 key dự phòng hợp lệ, cách nhau bằng dấu phẩy.")
        if body.get("clear_backup_keys") is True:
            keys = []
        elif not keys:
            keys = current.get("backup_keys", [])
        fields["backup_keys"] = [value for value in keys if value != key]
    return settings, provider, fields


def save_settings(body):
    settings, provider, fields = requested_settings(body)
    settings["profiles"][provider] = fields
    settings["provider"] = provider
    with NamedTemporaryFile(mode="w", encoding="utf-8", dir=SETTINGS_FILE.parent, prefix=".ai-settings-", delete=False) as tmp:
        temporary = Path(tmp.name)
        try:
            json.dump(settings, tmp, ensure_ascii=False)
            tmp.close()
            temporary.replace(SETTINGS_FILE)
        finally:
            temporary.unlink(missing_ok=True)
    return public_settings(settings)
