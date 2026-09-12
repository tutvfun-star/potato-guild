# -*- coding: utf-8 -*-
"""
LLM 클라이언트 - 비용 절감을 위한 모델 티어링

- gemini: 7명 분석가의 개별 분석 (무료 티어, 하루 1500회)
- claude: 멍거의 최종 브리핑 작성 (유료, 실행 신호가 나왔을 때만 호출)

API 키가 설정되어 있지 않으면 MOCK 모드로 동작해 구조를 테스트할 수 있게 한다.

참고: Gemini는 구글 공식 SDK(google-generativeai) 대신 requests로 REST API를 직접 호출한다.
그 SDK가 끌고 오는 grpcio/cryptography가 Windows ARM64 등 일부 환경에서 사전 빌드된
wheel이 없어 설치가 깨지는 경우가 많기 때문이다. requests는 이미 다른 곳에서도 쓰고 있어
추가 의존성이 없다.
"""
import json
import os
import random
import time

import requests

GEMINI_ENDPOINT = (
    "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
)

USE_MOCK = os.getenv("POTATO_GUILD_MOCK", "").lower() in ("1", "true", "yes")

MAX_RETRIES = 3
RETRY_BACKOFF_SECONDS = [10, 30, 65]


class _RetryableAPIError(Exception):
    """429(요청 한도 초과), 503(일시적 과부하)처럼 잠시 기다렸다가 다시 시도하면
    성공할 수 있는 오류."""


def _summarize_quota_error(resp_text: str) -> str:
    """429 응답 본문에서 '분당 한도'인지 '일일 한도'인지를 최대한 구체적으로 뽑아낸다."""
    try:
        data = json.loads(resp_text)
        details = data.get("error", {}).get("details", [])
        bits = []
        for d in details:
            if str(d.get("@type", "")).endswith("QuotaFailure"):
                for v in d.get("violations", []):
                    bits.append(f"{v.get('quotaId', '?')}(한도={v.get('quotaValue', '?')})")
        if bits:
            return "한도초과 항목: " + ", ".join(bits)
    except Exception:
        pass
    return resp_text[:600]


def _request_with_retry(fn, label: str):
    last_error = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            return fn()
        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError, _RetryableAPIError) as e:
            last_error = e
            if attempt < MAX_RETRIES:
                wait = RETRY_BACKOFF_SECONDS[min(attempt - 1, len(RETRY_BACKOFF_SECONDS) - 1)]
                print(f"   ⏳ {label} 오류, {wait}초 후 재시도 ({attempt}/{MAX_RETRIES}): {e}")
                time.sleep(wait)
    raise last_error


def _mock_analyst_response(persona_key: str) -> dict:
    opinions = ["매수", "매도", "관망"]
    rng = random.Random(persona_key)
    opinion = rng.choice(opinions)
    confidence = rng.randint(1, 5)
    reason = f"[MOCK] {persona_key} 페르소나의 임시 응답입니다. 실제 API 키를 설정하면 진짜 분석으로 교체됩니다."
    if persona_key == "taleb" and rng.random() < 0.3:
        reason = "[위험경고] " + reason
    return {"opinion": opinion, "confidence": confidence, "reason": reason}


def call_gemini_analyst(system_prompt: str, user_prompt: str, persona_key: str) -> dict:
    if USE_MOCK:
        return _mock_analyst_response(persona_key)

    api_key = (os.getenv("GEMINI_API_KEY") or "").strip()
    if not api_key:
        raise RuntimeError(
            "GEMINI_API_KEY가 설정되지 않았습니다. .env 파일을 확인하거나, "
            "테스트만 원한다면 POTATO_GUILD_MOCK=1 로 실행하세요."
        )

    url = GEMINI_ENDPOINT.format(model="gemini-3.6-flash")
    payload = {
        "system_instruction": {"parts": [{"text": system_prompt}]},
        "contents": [{"role": "user", "parts": [{"text": user_prompt}]}],
        "generationConfig": {"response_mime_type": "application/json"},
    }

    def _do_request():
        r = requests.post(url, params={"key": api_key}, json=payload, timeout=45)
        if r.status_code == 429:
            raise _RetryableAPIError(f"요청 한도 초과(429): {_summarize_quota_error(r.text)}")
        if r.status_code == 503:
            raise _RetryableAPIError(f"일시적 서비스 과부하(503): {r.text[:300]}")
        return r

    resp = _request_with_retry(_do_request, label=persona_key)
    if resp.status_code != 200:
        raise RuntimeError(f"Gemini API 오류 ({resp.status_code}): {_summarize_quota_error(resp.text)}")

    data = resp.json()
    try:
        text = data["candidates"][0]["content"]["parts"][0]["text"]
    except (KeyError, IndexError) as e:
        raise RuntimeError(f"Gemini 응답 형식이 예상과 다릅니다: {data}") from e

    return json.loads(text)


CLAUDE_ENDPOINT = "https://api.anthropic.com/v1/messages"
CLAUDE_API_VERSION = "2023-06-01"


def call_claude_guildmaster(system_prompt: str, user_prompt: str) -> str:
    if USE_MOCK:
        return (
            "[MOCK] 멍거의 임시 브리핑입니다. 실제 ANTHROPIC_API_KEY를 설정하면 "
            "7명의 분석을 종합한 진짜 브리핑으로 교체됩니다."
        )

    api_key = (os.getenv("ANTHROPIC_API_KEY") or "").strip()
    if not api_key:
        raise RuntimeError(
            "ANTHROPIC_API_KEY가 설정되지 않았습니다. .env 파일을 확인하거나, "
            "테스트만 원한다면 POTATO_GUILD_MOCK=1 로 실행하세요."
        )

    headers = {
        "x-api-key": api_key,
        "anthropic-version": CLAUDE_API_VERSION,
        "content-type": "application/json",
    }
    payload = {
        "model": "claude-sonnet-4-5",
        "max_tokens": 500,
        "system": system_prompt,
        "messages": [{"role": "user", "content": user_prompt}],
    }

    def _do_request():
        r = requests.post(CLAUDE_ENDPOINT, headers=headers, json=payload, timeout=45)
        if r.status_code == 429:
            raise _RetryableAPIError(f"요청 한도 초과(429): {r.text[:200]}")
        return r

    resp = _request_with_retry(_do_request, label="멍거(Claude)")
    if resp.status_code != 200:
        raise RuntimeError(f"Claude API 오류 ({resp.status_code}): {resp.text[:300]}")

    data = resp.json()
    try:
        return data["content"][0]["text"]
    except (KeyError, IndexError) as e:
        raise RuntimeError(f"Claude 응답 형식이 예상과 다릅니다: {data}") from e
