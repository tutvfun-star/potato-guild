# -*- coding: utf-8 -*-
"""7명의 분석가를 실행해서 의견을 모으는 모듈."""
from config.personas import ANALYSTS
from core.llm_client import call_gemini_analyst

USER_PROMPT_TEMPLATE = (
    "아래는 분석 대상 종목의 최신 데이터입니다.\n\n"
    "{context}\n\n"
    "위 데이터를 바탕으로 당신의 페르소나 관점에서 판단하세요. "
    "반드시 아래 JSON 형식으로만, 다른 텍스트 없이 답하세요:\n"
    '{{"opinion": "매수 또는 매도 또는 관망", "confidence": 1~5 사이 정수, "reason": "2~3문장 근거"}}'
)


def run_all_analysts(context: str) -> list[dict]:
    """9인 중 7명의 분석가를 순서대로 호출해 의견 리스트를 반환."""
    results = []
    for key, persona in ANALYSTS.items():
        user_prompt = USER_PROMPT_TEMPLATE.format(context=context)
        try:
            raw = call_gemini_analyst(persona["system_prompt"], user_prompt, key)
        except Exception as e:
            raw = {"opinion": "관망", "confidence": 1, "reason": f"[오류] 분석 실패: {e}"}

        results.append(
            {
                "key": key,
                "display_name": persona["display_name"],
                "role": persona["role"],
                "emoji": persona["emoji"],
                "opinion": raw.get("opinion", "관망"),
                "confidence": int(raw.get("confidence", 1)),
                "reason": raw.get("reason", ""),
            }
        )
    return results
