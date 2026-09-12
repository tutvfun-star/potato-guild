# -*- coding: utf-8 -*-
"""
멍거(길드마스터) - 종합 판단 모듈

중요한 설계 원칙: 최종 매매 신호는 LLM이 "알아서" 정하지 않는다.
확정된 규칙(사용자와 합의한 다수결+거부권 시스템)을 코드로 그대로 계산하고,
Claude는 그 계산 결과를 사람이 이해하기 쉬운 문장으로 설명하는 역할만 한다.
(돈과 직결된 판단은 재현 가능하고 설명 가능해야 하므로, LLM의 무작위성에 맡기지 않는다)
"""
from dataclasses import dataclass, field

from config.personas import GUILDMASTER
from core.llm_client import call_claude_guildmaster

# --- 사용자와 합의한 실행 규칙 ---
MIN_EXECUTE_SCORE = 8      # 이 점수 미만이면 무조건 관망
WEAK_SCORE = 8              # 8~13   -> 약함 (5%)
MODERATE_SCORE = 14         # 14~20  -> 보통 (10%)
STRONG_SCORE = 21           # 21+    -> 강함 (15%)

SIZING_TABLE = {
    "약함": 0.05,
    "보통": 0.10,
    "강함": 0.15,
}


@dataclass
class GuildVerdict:
    ticker: str
    score: int
    signal: str            # "매수" | "매도" | "관망"
    strength: str | None   # "약함" | "보통" | "강함" | None(관망일 때)
    position_pct: float    # 0.0 ~ 0.15
    risk_veto: bool
    briefing: str = ""
    analyst_results: list = field(default_factory=list)


def _compute_score(analyst_results: list[dict]) -> int:
    score = 0
    for r in analyst_results:
        if r["opinion"] == "매수":
            score += r["confidence"]
        elif r["opinion"] == "매도":
            score -= r["confidence"]
    return score


def _check_risk_veto(analyst_results: list[dict]) -> bool:
    """탈레브가 [위험경고]를 냈으면 거부권 발동."""
    for r in analyst_results:
        if r["key"] == "taleb" and "[위험경고]" in r["reason"]:
            return True
    return False


def _strength_and_sizing(abs_score: int) -> tuple[str, float]:
    if abs_score >= STRONG_SCORE:
        return "강함", SIZING_TABLE["강함"]
    if abs_score >= MODERATE_SCORE:
        return "보통", SIZING_TABLE["보통"]
    return "약함", SIZING_TABLE["약함"]


def deliberate(
    ticker: str,
    context: str,
    analyst_results: list[dict],
    reflection: str | None = None,
) -> GuildVerdict:
    score = _compute_score(analyst_results)
    risk_veto = _check_risk_veto(analyst_results)

    if risk_veto:
        signal, strength, position_pct = "관망", None, 0.0
    elif score >= MIN_EXECUTE_SCORE:
        signal = "매수"
        strength, position_pct = _strength_and_sizing(score)
    elif score <= -MIN_EXECUTE_SCORE:
        signal = "매도"
        strength, position_pct = _strength_and_sizing(-score)
    else:
        signal, strength, position_pct = "관망", None, 0.0

    verdict = GuildVerdict(
        ticker=ticker,
        score=score,
        signal=signal,
        strength=strength,
        position_pct=position_pct,
        risk_veto=risk_veto,
        analyst_results=analyst_results,
    )

    # Claude에게는 "결정해달라"가 아니라 "이미 정해진 결과를 설명해달라"고만 요청한다.
    opinions_text = "\n".join(
        f"- {r['emoji']} {r['display_name']}({r['role']}): {r['opinion']} "
        f"(확신도 {r['confidence']}) - {r['reason']}"
        for r in analyst_results
    )
    reflection_section = f"[과거 판단 회고]\n{reflection}\n\n" if reflection else ""
    user_prompt = (
        f"[분석 대상]\n{context}\n\n"
        f"[7인 전문가 의견]\n{opinions_text}\n\n"
        f"{reflection_section}"
        f"[코드로 이미 계산된 결과]\n"
        f"종합 스코어: {score}\n"
        f"리스크 거부권 발동 여부: {'예 (탈레브 위험경고)' if risk_veto else '아니오'}\n"
        f"최종 신호: {signal}"
        + (f" (강도: {strength}, 포지션 비중: {position_pct*100:.0f}%)" if signal != "관망" else "")
        + "\n\n위 결과가 왜 이렇게 나왔는지 포테이토에게 브리핑하세요. "
        "반드시 4~6문장 이내, 마크다운 기호(#, **, - 등) 없이 순수 텍스트로만 답하세요."
    )
    verdict.briefing = call_claude_guildmaster(GUILDMASTER["system_prompt"], user_prompt)
    return verdict
