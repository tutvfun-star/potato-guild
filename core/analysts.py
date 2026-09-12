# -*- coding: utf-8 -*-
"""7명의 분석가를 실행해서 의견을 모으는 모듈.

TauricResearch/TradingAgents의 구조를 참고해, 강세(린치)/약세(버리) 두 분석가에게는
서로의 1차 의견을 보여주고 한 차례 반박할 기회를 주는 구조화된 토론을 추가했다
(run_bull_bear_debate). 다른 5명(리버모어/버핏/소로스/우드/탈레브)은 1차 의견이 최종이다.

원래는 7명 분석가를 Gemini 무료 티어로 호출했는데, 모델마다 무료 일일 한도가 제각각이고
예고 없이 바뀌는 문제(예: gemini-3.6-flash가 하루 20회로 확인됨)를 겪은 뒤 Claude Haiku로
옮겼다. Haiku는 Anthropic API 종량제라 무료 티어 특유의 RPM/RPD 눈치게임이 없어져서,
호출 사이에 일부러 간격을 두던 로직(GEMINI_CALL_DELAY_SECONDS)도 더 이상 필요 없다.
"""
from config.personas import ANALYSTS
from core.llm_client import call_claude_analyst

USER_PROMPT_TEMPLATE = (
    "아래는 분석 대상 종목의 최신 데이터입니다.\n\n"
    "{context}\n\n"
    "위 데이터를 바탕으로 당신의 페르소나 관점에서 판단하세요. "
    "반드시 아래 JSON 형식으로만, 다른 텍스트 없이 답하세요:\n"
    '{{"opinion": "매수 또는 매도 또는 관망", "confidence": 1~5 사이 정수, "reason": "2~3문장 근거"}}'
)

DEBATE_PROMPT_TEMPLATE = (
    "아래는 분석 대상 종목의 최신 데이터입니다.\n\n"
    "{context}\n\n"
    "[당신의 1차 의견]\n{my_reason}\n\n"
    "[{opponent_role}의 1차 의견]\n{opponent_reason}\n\n"
    "상대방의 주장을 검토한 뒤, 당신의 원래 입장을 유지하거나 필요하면 일부 수정해서 "
    "최종 의견을 다시 내세요. reason에는 상대 주장에 대한 짧은 반박이나 인정을 포함하세요. "
    "반드시 아래 JSON 형식으로만, 다른 텍스트 없이 답하세요:\n"
    '{{"opinion": "매수 또는 매도 또는 관망", "confidence": 1~5 사이 정수, "reason": "2~3문장 근거"}}'
)


def run_all_analysts(context: str) -> list[dict]:
    """9인 중 7명의 분석가를 순서대로 호출해 의견 리스트를 반환."""
    results = []
    for key, persona in ANALYSTS.items():
        user_prompt = USER_PROMPT_TEMPLATE.format(context=context)
        try:
            raw = call_claude_analyst(persona["system_prompt"], user_prompt, key)
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


def run_bull_bear_debate(context: str, results: list[dict]) -> list[dict]:
    """피터 린치(강세)와 마이클 버리(약세)에게 서로의 1차 의견을 보여주고
    한 차례 반박/재판단 기회를 준다."""
    lynch = next((r for r in results if r["key"] == "lynch"), None)
    burry = next((r for r in results if r["key"] == "burry"), None)
    if lynch is None or burry is None:
        return results

    lynch_r1, burry_r1 = lynch["reason"], burry["reason"]

    try:
        lynch_final = call_claude_analyst(
            ANALYSTS["lynch"]["system_prompt"],
            DEBATE_PROMPT_TEMPLATE.format(
                context=context, my_reason=lynch_r1,
                opponent_role="약세론자(버리)", opponent_reason=burry_r1,
            ),
            "lynch",
        )
        lynch["opinion"] = lynch_final.get("opinion", lynch["opinion"])
        lynch["confidence"] = int(lynch_final.get("confidence", lynch["confidence"]))
        lynch["reason"] = lynch_final.get("reason", lynch["reason"])
    except Exception as e:
        lynch["reason"] += f" [토론 단계 오류로 1차 의견 유지: {e}]"

    try:
        burry_final = call_claude_analyst(
            ANALYSTS["burry"]["system_prompt"],
            DEBATE_PROMPT_TEMPLATE.format(
                context=context, my_reason=burry_r1,
                opponent_role="강세론자(린치)", opponent_reason=lynch_r1,
            ),
            "burry",
        )
        burry["opinion"] = burry_final.get("opinion", burry["opinion"])
        burry["confidence"] = int(burry_final.get("confidence", burry["confidence"]))
        burry["reason"] = burry_final.get("reason", burry["reason"])
    except Exception as e:
        burry["reason"] += f" [토론 단계 오류로 1차 의견 유지: {e}]"

    return results
