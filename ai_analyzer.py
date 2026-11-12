"""Gemini API による回答生成モジュール。"""

import os
import time

from dotenv import load_dotenv
from google import genai

import config

load_dotenv()

_client = None
_client_initialized = False


def _build_http_options():
    try:
        from google.genai import types

        return types.HttpOptions(timeout=config.GEMINI_TIMEOUT_MS)
    except Exception:
        return None


def _get_client():
    """API クライアントを初回のみ生成して使い回す。"""
    global _client, _client_initialized
    if _client_initialized:
        return _client
    _client_initialized = True

    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        print("GEMINI_API_KEY が未設定のため AI 解析を無効にします。", flush=True)
        return None

    kwargs = {"api_key": api_key}
    http_options = _build_http_options()
    if http_options is not None:
        kwargs["http_options"] = http_options
    try:
        _client = genai.Client(**kwargs)
    except Exception as e:
        print(f"Gemini クライアントの初期化に失敗しました: {e}", flush=True)
        _client = None
    return _client


def get_ai_response(prompt):
    """プロンプトへの回答テキストを返す。失敗時は None。"""
    client = _get_client()
    if client is None:
        return None

    attempts = config.GEMINI_MAX_RETRIES + 1
    for attempt in range(1, attempts + 1):
        try:
            response = client.models.generate_content(
                model=config.GEMINI_MODEL,
                contents=prompt,
            )
            text = (response.text or "").strip()
            return text or None
        except Exception as e:
            print(f"AI 解析エラー ({attempt}/{attempts}): {e}", flush=True)
            if attempt < attempts:
                time.sleep(attempt)
    return None
