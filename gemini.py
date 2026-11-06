"""AI subtitle translation with ID recovery and Gemini model fallback."""

import json
import os
import time
import urllib.error
import urllib.request
from typing import Dict, List

from ai_settings import KEY_NAMES, profile, read_settings
from subtitles import batch_items


def episode_batches(items):
    # Bound the estimated response size as well as the number of subtitle IDs.
    start = 0
    size = 0
    for index, item in enumerate(items):
        cost = len(item["text"]) + 24
        if index > start and (index - start >= 400 or size + cost > 14000):
            yield start, index
            start, size = index, 0
        size += cost
    if start < len(items):
        yield start, len(items)


def translation_profile():
    settings = read_settings()
    provider = settings["provider"]
    return provider, profile(settings, provider)


def translate_batch(batch: List[Dict], target_language: str = "Vietnamese", context=None,
                    connection=None) -> List[str]:
    provider, config = connection or translation_profile()
    translations_by_id = {}
    pending = batch
    batch_sizes = (len(batch), 80, 20, 5) if len(batch) > 80 else (max(1, len(batch)), 20, 5)
    if len(batch) > 80 and config.get("prefer_smaller_batches"):
        batch_sizes = batch_sizes[1:]
    for round_index, batch_size in enumerate(batch_sizes):
        for chunk in batch_items(pending, batch_size):
            request_context = context
            if round_index and (context or len(batch) > 80):
                translated = [item for item in batch if str(item["index"]) in translations_by_id]
                request_context = {**(context or {}), "retry_examples": [
                    {"source": item["text"], "translation": translations_by_id[str(item["index"])]}
                    for item in translated[-12:]
                ]}
            options = {"context": request_context} if request_context else {}
            translations_by_id.update(_request_translations(chunk, target_language, provider, config, **options))
        pending = [item for item in batch if str(item["index"]) not in translations_by_id]
        if not pending:
            return [translations_by_id[str(item["index"])] for item in batch]
        if round_index + 1 < len(batch_sizes):
            print(f"AI còn thiếu {len(pending)} câu; đang thử bổ sung theo nhóm nhỏ hơn...")

    missing = [str(item["index"]) for item in pending]
    raise RuntimeError(f"AI trả thiếu bản dịch sau {len(batch_sizes)} lượt cho ID: {', '.join(missing)}")


def _request_translations(batch, target_language, provider, config, context=None):
    api_keys = list(dict.fromkeys(key for key in [config["api_key"], *config.get("backup_keys", [])] if key))
    if not api_keys and provider != "compatible":
        raise ValueError(f"Chưa cấu hình {KEY_NAMES[provider]}. Hãy thêm API key trong phần Cài đặt AI.")

    configured_model = config["model"].strip()
    fallback_models = os.getenv("GEMINI_FALLBACK_MODELS", "gemini-3.5-flash-lite") if provider == "gemini" else ""
    candidate_models = list(dict.fromkeys(
        model.strip() for model in [configured_model, *fallback_models.split(",")] if model.strip()
    ))
    if not candidate_models:
        raise ValueError("Chưa cấu hình model AI.")
    preferred_model = config.get("preferred_model")
    if preferred_model in candidate_models:
        candidate_models.remove(preferred_model)
        candidate_models.insert(0, preferred_model)

    payload = [{"id": item["index"], "text": item["text"]} for item in batch]

    ids = [str(item["index"]) for item in batch]
    if len(batch) > 80:
        schema = {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"id": {"type": "string"}, "translation": {"type": "string"}},
                "required": ["id", "translation"],
            },
        }
        response_format = 'Return an array of objects with "id" and "translation" fields.'
    else:
        schema = {
            "type": "object",
            "properties": {item_id: {"type": "string"} for item_id in ids},
            "required": ids,
        }
        response_format = "Return a JSON object mapping each exact input ID to its translation string."

    terminology = "Keep exact cultivation ranks and consistent proper names. Do not invent names or relationships."
    if target_language.strip().casefold() == "vietnamese":
        terminology = (
            "For cultivation dialogue, keep exact ranks (炼气/煉氣 = Luyện Khí, 筑基/築基 = Trúc Cơ). "
            "Use consistent Sino-Vietnamese names for the same characters, sects and weapons. "
            "Use these readings where present: 曦月 = Hy Nguyệt, 青峦剑 = Thanh Loan Kiếm, "
            "青红缠绵剑 = Thanh Hồng Triền Miên Kiếm. Do not invent names or relationships."
        )
    prompt = f"""Translate the ordered subtitle lines into concise, natural spoken {target_language}. Read nearby lines for context.
Use nearby dialogue, titles and kinship terms for forms of address. If unsure, use neutral wording or omit pronouns.
{terminology}
Before translating, screen each line for OCR noise. Output "[Không rõ]" for isolated fragments or incoherent word combinations with no clear role in adjacent dialogue. A possible literal reading is insufficient. Keep clear short replies and lines that continue across IDs.
{response_format} Translate each Input ID exactly once. Output only JSON, without timestamps or stray escapes before punctuation.
"""
    if context:
        reference = json.dumps(context, ensure_ascii=False, separators=(",", ":"))
        prompt += f"Reference for continuity only (do not translate): {reference}\n"
    prompt += "Input:\n" + json.dumps(payload, ensure_ascii=False, separators=(",", ":"))

    last_error = None
    last_http_status = None
    data = None

    keys = api_keys or [""]
    key_positions = config.setdefault("key_positions", {})
    for model in candidate_models:
        position = key_positions.get(model, 0)
        attempted_keys = 1
        attempt = 0
        while attempt < 3:
            req = translation_request(provider, {**config, "api_key": keys[position]}, model, prompt, schema=schema)
            attempt += 1
            try:
                with open_translation(req, timeout=120) as resp:
                    resp_data = json.loads(resp.read().decode("utf-8"))
                    content_text, truncated = response_text(provider, resp_data)
                    if truncated:
                        print("Model chạm giới hạn đầu ra, thử nhóm nhỏ hơn.")
                        return {}
                    content_text = content_text.strip()
                    if content_text.startswith("```") and content_text.endswith("```"):
                        content_text = content_text.partition("\n")[2][:-3].strip()
                    data = json.loads(content_text)
                    if isinstance(data, dict):
                        data = [{"id": key, "translation": value} for key, value in data.items()]
                    if not isinstance(data, list):
                        raise ValueError("Phản hồi không phải đối tượng JSON")
                    key_positions[model] = position
                    config["preferred_model"] = model
                    break
            except urllib.error.HTTPError as e:
                e.close()
                last_error = f"Model {model} - HTTP {e.code}"
                last_http_status = e.code
                if e.code == 429 and provider == "gemini" and len(keys) > 1:
                    if attempted_keys == len(keys):
                        print(f"Model {model}: tất cả key Gemini đang bị giới hạn (HTTP 429).")
                        break
                    attempted_keys += 1
                    position = (position + 1) % len(keys)
                    attempt = 0
                    print("Gemini giới hạn quota (HTTP 429); thử key kế tiếp.")
                    continue
                if e.code == 429 and provider == "gemini" and model != candidate_models[-1]:
                    print(f"Model {model} bị giới hạn (HTTP 429), thử model dự phòng.")
                    break
                if e.code == 503 and provider == "gemini":
                    if model == candidate_models[-1] and len(batch) <= 80 and attempt == 1:
                        print(f"Model {model} đang bận (503), thử lại sau 1 giây.")
                        time.sleep(1)
                        continue
                    print(f"Model {model} chưa khả dụng (HTTP 503).")
                    break
                if e.code in (429, 500, 502, 503, 504):
                    if attempt < 3:
                        wait_sec = (2 ** (attempt - 1)) + ((attempt - 1) * 0.5)
                        print(f"Model {model} đang bận ({e.code}), chờ {wait_sec}s...")
                        time.sleep(wait_sec)
                        continue
                    print(f"Model {model} vẫn lỗi ({e.code}) sau 3 lần thử.")
                    break
                if e.code == 413 and len(batch) > 5:
                    return {}
                if e.code == 404 and provider == "gemini":
                    print(f"Model {model} không khả dụng, thử model dự phòng...")
                    break
                raise RuntimeError(f"AI HTTP {e.code}; kiểm tra cấu hình/quyền truy cập.") from None
            except (urllib.error.URLError, TimeoutError) as e:
                last_error = f"Model {model}: {type(e).__name__}"
                last_http_status = None
                if attempt < 3:
                    time.sleep(2 ** (attempt - 1))
                    continue
                break
            except (ValueError, KeyError, IndexError, TypeError, AttributeError):
                raise RuntimeError(f"Model {model}: phản hồi không hợp lệ hoặc không có bản dịch.") from None

        if data is not None:
            break

    if data is None:
        if len(batch) > 80 and last_http_status == 503:
            config["prefer_smaller_batches"] = True
            print("Cả lượt dịch lớn không thành công. Thử chia thành nhóm tối đa 80 câu.")
            return {}
        raise RuntimeError(f"Không thể dịch phụ đề qua API AI. Chi tiết: {last_error}")

    expected_ids = {str(item["index"]) for item in batch}
    translations_by_id = {}
    for row in data:
        if not isinstance(row, dict):
            continue
        item_id = str(row.get("id"))
        translation = row.get("translation")
        if item_id in expected_ids and isinstance(translation, str) and translation.strip():
            translations_by_id[item_id] = translation.strip().replace(r"\,", ",").replace(r"\.", ".")
    return translations_by_id

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def translation_request(provider, config, model, prompt, schema=None):
    headers = {"Content-Type": "application/json"}
    key = config["api_key"]
    if provider == "gemini":
        url = "https://generativelanguage.googleapis.com/v1/interactions"
        headers["x-goog-api-key"] = key
        model = model.removeprefix("models/")
        body = {"model": model, "input": prompt, "store": False}
        generation = {}
        if model in ("gemini-3.8-flash", "gemini-3.7-flash"):
            generation["thinking_level"] = "low"
        elif model == "gemini-3.5-flash-lite":
            generation["thinking_level"] = "minimal"
        if schema is None:
            generation["max_output_tokens"] = 1024
        else:
            body["response_format"] = {"type": "text", "mime_type": "application/json", "schema": schema}
        if generation:
            body["generation_config"] = generation
    else:
        body = {"model": model, "messages": [{"role": "user", "content": prompt}]}
        if provider == "anthropic":
            url = "https://api.anthropic.com/v1/messages"
            headers.update({"x-api-key": key, "anthropic-version": "2026-10-21"})
            body["max_tokens"] = 8192
        else:
            base_url = config["base_url"] if provider == "compatible" else "https://api.openai.com/v1"
            url = base_url + "/chat/completions"
            if key:
                headers["Authorization"] = f"Bearer {key}"
    return urllib.request.Request(url, data=json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8"), headers=headers, method="POST")


def response_text(provider, data):
    if provider == "gemini":
        if data["status"] == "incomplete":
            return "", True
        if data["status"] != "completed":
            raise ValueError("Gemini interaction did not complete")
        return "".join(part["text"] for step in data["steps"] if step["type"] == "model_output"
                       for part in step["content"] if part["type"] == "text"), False
    if provider == "anthropic":
        return "".join(block["text"] for block in data["content"] if block["type"] == "text"), data.get("stop_reason") == "max_tokens"
    choice = data["choices"][0]
    return choice["message"]["content"], choice.get("finish_reason") == "length"


open_translation = urllib.request.build_opener(NoRedirect()).open


def probe_connection(provider, config):
    if not config["api_key"] and provider != "compatible":
        raise ValueError("Chưa có API key. Hãy nhập key hoặc dùng key đã lưu.")
    prompt = "Reply with only the word OK."
    request = translation_request(provider, config, config["model"], prompt)
    try:
        with open_translation(request, timeout=30) as response:
            data = json.loads(response.read())
        text, _ = response_text(provider, data)
        if not isinstance(text, str) or not text.strip():
            raise ValueError("Empty model response")
    except urllib.error.HTTPError as error:
        with error:
            message = connection_error(provider, error)
        raise RuntimeError(message) from None
    except (TimeoutError, urllib.error.URLError) as error:
        if isinstance(error, TimeoutError) or isinstance(error.reason, TimeoutError):
            raise RuntimeError("API không phản hồi trong thời gian chờ.") from None
        raise RuntimeError("Không kết nối được API. Kiểm tra mạng, TLS hoặc Base URL.") from None
    except (ValueError, KeyError, IndexError, TypeError):
        raise RuntimeError("API đã phản hồi nhưng không có văn bản hợp lệ từ model.") from None


def connection_error(provider, error):
    try:
        details = json.loads(error.read(65536)).get("error", {})
        codes = [details.get("code"), details.get("type"), details.get("status")]
        codes.extend(item.get("reason") for item in details.get("details", []))
        parameter = details.get("param")
    except (ValueError, AttributeError, TypeError, OSError):
        codes, parameter = [], None

    status = error.code
    if status == 401 or any(code in ("API_KEY_INVALID", "API_KEY_EXPIRED", "invalid_api_key", "authentication_error") for code in codes):
        message = "API từ chối xác thực. Kiểm tra API key có đúng, còn hiệu lực và thuộc đúng nhà cung cấp."
    elif "model_not_found" in codes or parameter == "model" or (status == 404 and provider != "compatible"):
        message = "Không tìm thấy hoặc không có quyền dùng model. Kiểm tra mã model và quyền của tài khoản."
    elif status == 404:
        message = "Không tìm thấy endpoint hoặc model. Kiểm tra Base URL và mã model."
    elif status == 403:
        message = "API từ chối quyền truy cập. Kiểm tra quyền của key, dự án và model."
    elif status in (402, 429):
        message = "Kiểm tra hạn mức, tốc độ gọi API hoặc số dư của tài khoản."
    elif status >= 500:
        message = "Dịch vụ AI đang gặp lỗi. Hãy thử lại sau."
    elif 300 <= status < 400:
        message = "API yêu cầu chuyển hướng. Kiểm tra Base URL."
    else:
        message = "API từ chối yêu cầu. Kiểm tra mã model và cấu hình của nhà cung cấp."
    return f"{message} (HTTP {status})"
