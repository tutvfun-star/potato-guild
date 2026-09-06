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
RETRY_BACKOFF_SECONDS = [3, 7, 15]  # 시도별 대기 시간 (점점 늘어남)


def _request_with_retry(fn, label: str):
    """네트워크가 간헐적으로 끊기는 환경(백신/방화벽 SSL 검사 등)에 대비한 재시도 래퍼."""
    last_error = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            return fn()
        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as e:
            last_error = e
            if attempt < MAX_RETRIES:
                wait = RETRY_BACKOFF_SECONDS[min(attempt - 1, len(RETRY_BACKOFF_SECONDS) - 1)]
                print(f"   ⏳ {label} 네트워크 오류, {wait}초 후 재시도 ({attempt}/{MAX_RETRIES}): {e}")
                time.sleep(wait)
    raise last_error


def _mock_analyst_response(persona_key: str) -> dict:
    """API 키 없이 파이프라인 구조를 검증하기 위한 가짜 응답 생성기."""
    opinions = ["매수", "매도", "관망"]
    # 시드를 페르소나별로 고정해서 매 실행마다 결과가 완전히 무작위로 날뛰지 않게 함
    rng = random.Random(persona_key)
    opinion = rng.choice(opinions)
    confidence = rng.randint(1, 5)
    reason = f"[MOCK] {persona_key} 페르소나의 임시 응답입니다. 실제 API 키를 설정하면 진짜 분석으로 교체됩니다."
    if persona_key == "taleb" and rng.random() < 0.3:
        reason = "[위험경고] " + reason
    return {"opinion": opinion, "confidence": confidence, "reason": reason}


def call_gemini_analyst(system_prompt: str, user_prompt: str, persona_key: str) -> dict:
    """분석가 1명을 Gemini REST API로 호출하고 {opinion, confidence, reason} dict를 반환."""
    if USE_MOCK:
        return _mock_analyst_response(persona_key)

    api_key = os.getenv("GEMINI_API_KEY")
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
    resp = _request_with_retry(
        lambda: requests.post(url, params={"key": api_key}, json=payload, timeout=45),
        label=persona_key,
    )
    if resp.status_code != 200:
        raise RuntimeError(f"Gemini API 오류 ({resp.status_code}): {resp.text[:300]}")

    data = resp.json()
    try:
        text = data["candidates"][0]["content"]["parts"][0]["text"]
    except (KeyError, IndexError) as e:
        raise RuntimeError(f"Gemini 응답 형식이 예상과 다릅니다: {data}") from e

    return json.loads(text)


def call_claude_guildmaster(system_prompt: str, user_prompt: str) -> str:
    """멍거의 최종 브리핑을 Claude로 호출하고 텍스트를 반환."""
    if USE_MOCK:
        return (
            "[MOCK] 멍거의 임시 브리핑입니다. 실제 ANTHROPIC_API_KEY를 설정하면 "
            "7명의 분석을 종합한 진짜 브리핑으로 교체됩니다."
        )

    try:
        import anthropic
    except ImportError as e:
        raise RuntimeError(
            "anthropic 패키지가 설치되어 있지 않습니다. "
            "pip install -r requirements.txt 를 먼저 실행하세요."
        ) from e

    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError(
            "ANTHROPIC_API_KEY가 설정되지 않았습니다. .env 파일을 확인하거나, "
            "테스트만 원한다면 POTATO_GUILD_MOCK=1 로 실행하세요."
        )

    client = anthropic.Anthropic(api_key=api_key)

    last_error = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            message = client.messages.create(
                model="claude-sonnet-4-5",
                max_tokens=500,
                system=system_prompt,
                messages=[{"role": "user", "content": user_prompt}],
            )
            return message.content[0].text
        except (anthropic.APIConnectionError, anthropic.APITimeoutError) as e:
            last_error = e
            if attempt < MAX_RETRIES:
                wait = RETRY_BACKOFF_SECONDS[min(attempt - 1, len(RETRY_BACKOFF_SECONDS) - 1)]
                print(f"   ⏳ 멍거 네트워크 오류, {wait}초 후 재시도 ({attempt}/{MAX_RETRIES}): {e}")
                time.sleep(wait)
    raise last_error
