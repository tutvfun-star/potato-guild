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

# 손절가 = 지지선에서 ATR(변동성)의 이 비율만큼 더 내려간 지점. 종목의 원래 변동폭을
# 감안해 "정상적인 출렁임"에 손절당하지 않도록 여유를 둔다.
STOP_LOSS_ATR_BUFFER = 0.5


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
    target_price: float | None = None  # 매수 신호일 때만 계산 (최근 저항선 기준)
    stop_loss: float | None = None     # 매수 신호일 때만 계산 (지지선 - ATR 버퍼)
    risk_reward: float | None = None   # (목표가-진입가) / (진입가-손절가)


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


def _compute_price_levels(
    entry_price: float | None,
    support: float | None,
    resistance: float | None,
    atr14: float | None,
) -> tuple[float | None, float | None, float | None]:
    """매수 신호가 나왔을 때만 의미가 있는 목표가/손절가를 계산한다.

    "미래 가격을 예측"하는 게 아니라, 최근 가격 구간(지지선/저항선)과 변동성(ATR)을
    근거로 한 규칙 기반 가이드라인이다:
    - 목표가 = 최근 20일 저항선 (그동안 이 가격대에서 매도 압력이 있었던 지점)
    - 손절가 = 최근 20일 지지선에서 ATR의 절반만큼 더 내려간 지점 (정상적인 변동성으로
      인한 손절을 피하기 위한 여유)

    데이터가 없거나, 현재가가 이미 저항선 위 또는 지지선 아래라서 계산이 억지스러워지면
    (target <= entry 이거나 stop >= entry) 이상한 숫자를 보여주는 대신 전부 None을 반환한다.
    """
    if None in (entry_price, support, resistance, atr14):
        return None, None, None

    stop_loss = round(support - STOP_LOSS_ATR_BUFFER * atr14, 0)
    target_price = round(resistance, 0)

    if not (stop_loss < entry_price < target_price):
        return None, None, None

    risk = entry_price - stop_loss
    reward = target_price - entry_price
    risk_reward = round(reward / risk, 2) if risk > 0 else None
    return target_price, stop_loss, risk_reward


def deliberate(
    ticker: str,
    context: str,
    analyst_results: list[dict],
    snap=None,
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

    target_price = stop_loss = risk_reward = None
    if signal == "매수" and snap is not None:
        target_price, stop_loss, risk_reward = _compute_price_levels(
            snap.price, snap.support, snap.resistance, snap.atr14
        )

    verdict = GuildVerdict(
        ticker=ticker,
        score=score,
        signal=signal,
        strength=strength,
        position_pct=position_pct,
        risk_veto=risk_veto,
        analyst_results=analyst_results,
        target_price=target_price,
        stop_loss=stop_loss,
        risk_reward=risk_reward,
    )

    # Claude에게는 "결정해달라"가 아니라 "이미 정해진 결과를 설명해달라"고만 요청한다.
    opinions_text = "\n".join(
        f"- {r['emoji']} {r['display_name']}({r['role']}): {r['opinion']} "
        f"(확신도 {r['confidence']}) - {r['reason']}"
        for r in analyst_results
    )
    reflection_section = f"[과거 판단 회고]\n{reflection}\n\n" if reflection else ""
    price_levels_section = ""
    if target_price is not None:
        price_levels_section = (
            f"목표가: {target_price:,.0f} (최근 저항선 기준)\n"
            f"손절가: {stop_loss:,.0f} (최근 지지선 - 변동성 여유분)\n"
            f"손익비: 1 : {risk_reward}\n"
        )
    user_prompt = (
        f"[분석 대상]\n{context}\n\n"
        f"[7인 전문가 의견]\n{opinions_text}\n\n"
        f"{reflection_section}"
        f"[코드로 이미 계산된 결과]\n"
        f"종합 스코어: {score}\n"
        f"리스크 거부권 발동 여부: {'예 (탈레브 위험경고)' if risk_veto else '아니오'}\n"
        f"최종 신호: {signal}"
        + (f" (강도: {strength}, 포지션 비중: {position_pct*100:.0f}%)" if signal != "관망" else "")
        + "\n"
        + price_levels_section
        + "\n위 결과가 왜 이렇게 나왔는지 포테이토에게 브리핑하세요. 목표가/손절가가 "
        "주어졌다면 그 의미(예측이 아니라 최근 가격 구간과 변동성 기반의 가이드라인)도 "
        "한 문장으로 짚어주세요. "
        "반드시 4~6문장 이내, 마크다운 기호(#, **, - 등) 없이 순수 텍스트로만 답하세요."
    )
    verdict.briefing = call_claude_guildmaster(GUILDMASTER["system_prompt"], user_prompt)
    return verdict
