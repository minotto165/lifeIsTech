"""回答生成モジュール（OpenCode Go / Gemini）。"""

import json
import os
import time
import urllib.error
import urllib.request
import uuid

from dotenv import load_dotenv
from google import genai

import config

load_dotenv()

_gemini_client = None
_gemini_initialized = False

# Go はセッション ID 単位でルーティングするため、プロセスで 1 つ持って使い回す。
_go_session = uuid.uuid4().hex
_go_disabled_reason = None


# --- OpenCode Go -----------------------------------------------------


def _opencode_go_key():
    key = (os.environ.get("OPENCODE_GO_API_KEY") or "").strip()
    if key:
        return key
    try:
        with open(os.path.expanduser(config.OPENCODE_GO_AUTH_PATH), encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return None
    entry = data.get("opencode-go") or {}
    return (entry.get("key") or "").strip() or None


def _post_json(url, payload, headers, timeout):
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def ask_opencode_go(prompt):
    """OpenCode Go で回答を生成する。使えないときは None。"""
    global _go_disabled_reason
    if _go_disabled_reason:
        return None

    key = _opencode_go_key()
    if not key:
        _go_disabled_reason = "API キーが見つかりません"
        print(
            "OpenCode Go の API キーが見つかりません。"
            ".env に OPENCODE_GO_API_KEY を設定してください。",
            flush=True,
        )
        return None

    payload = {
        "model": config.OPENCODE_GO_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0,
        "max_tokens": config.OPENCODE_GO_MAX_TOKENS,
    }
    if config.OPENCODE_GO_REASONING_EFFORT:
        payload["reasoning_effort"] = config.OPENCODE_GO_REASONING_EFFORT
    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "User-Agent": config.OPENCODE_GO_USER_AGENT,
        "x-opencode-session": _go_session,
    }

    attempts = config.OPENCODE_GO_MAX_RETRIES + 1
    for attempt in range(1, attempts + 1):
        try:
            data = _post_json(
                config.OPENCODE_GO_BASE_URL,
                payload,
                headers,
                config.OPENCODE_GO_TIMEOUT_SEC,
            )
            text = (data["choices"][0]["message"]["content"] or "").strip()
            return text or None
        except urllib.error.HTTPError as e:
            detail = ""
            try:
                detail = e.read().decode("utf-8", "replace")[:300]
            except Exception:
                pass
            if e.code in (401, 403):
                # キーが無効なら毎問リトライしても無駄なので、この実行では諦める。
                _go_disabled_reason = f"認証エラー {e.code}"
                print(f"OpenCode Go の認証に失敗しました（{e.code}）: {detail}", flush=True)
                print(
                    "https://opencode.ai/console/account#api-keys でキーを作り直し、"
                    ".env の OPENCODE_GO_API_KEY に設定してください。",
                    flush=True,
                )
                return None
            print(f"OpenCode Go エラー ({attempt}/{attempts}): {e.code} {detail}", flush=True)
        except Exception as e:
            print(f"OpenCode Go エラー ({attempt}/{attempts}): {e}", flush=True)
        if attempt < attempts:
            time.sleep(attempt)
    return None


# --- Gemini ----------------------------------------------------------


def _build_http_options():
    try:
        from google.genai import types

        return types.HttpOptions(timeout=config.GEMINI_TIMEOUT_MS)
    except Exception:
        return None


def _get_gemini_client():
    """Gemini クライアントを初回のみ生成して使い回す。"""
    global _gemini_client, _gemini_initialized
    if _gemini_initialized:
        return _gemini_client
    _gemini_initialized = True

    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        return None

    kwargs = {"api_key": api_key}
    http_options = _build_http_options()
    if http_options is not None:
        kwargs["http_options"] = http_options
    try:
        _gemini_client = genai.Client(**kwargs)
    except Exception as e:
        print(f"Gemini クライアントの初期化に失敗しました: {e}", flush=True)
        _gemini_client = None
    return _gemini_client


def ask_gemini(prompt):
    client = _get_gemini_client()
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


# --- 公開 API --------------------------------------------------------


def get_ai_response(prompt):
    """プロンプトへの回答テキストを返す。失敗時は None。"""
    if config.AI_PROVIDER == "opencode_go":
        text = ask_opencode_go(prompt)
        if text is not None:
            return text
        if not config.AI_FALLBACK_TO_GEMINI:
            return None
    return ask_gemini(prompt)
