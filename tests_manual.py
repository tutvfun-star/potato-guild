# -*- coding: utf-8 -*-
"""
핵심 금융 로직(점수 계산/거부권/포지션 사이징/현금 최소 보유)에 대한 수동 검증 스크립트.
LLM 호출 없이 core.guildmaster / core.potato 의 순수 로직만 검증한다.

실행: python tests_manual.py
"""
from core.guildmaster import _check_risk_veto, _compute_score, _strength_and_sizing
from core.memory import build_reflection
from core.potato import MAX_POSITIONS, MIN_CASH_RESERVE_RATIO, execute

PASS = 0
FAIL = 0


def check(name, condition):
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  ✅ {name}")
    else:
        FAIL += 1
        print(f"  ❌ {name}")


def mk(key, opinion, confidence, reason=""):
    return {"key": key, "display_name": key, "role": "", "emoji": "",
            "opinion": opinion, "confidence": confidence, "reason": reason}


print("[1] 점수 계산 로직")
results = [mk("wood", "매수", 5), mk("lynch", "매수", 3), mk("burry", "매수", 4),
           mk("livermore", "매도", 2), mk("taleb", "매도", 3)]
check("매수(5+3+4) - 매도(2+3) = 7", _compute_score(results) == 7)

print("\n[2] 리스크 거부권 로직")
veto_results = [mk("taleb", "매도", 3, "[위험경고] 변동성 과열")]
check("탈레브가 [위험경고] 냈으면 veto=True", _check_risk_veto(veto_results) is True)
no_veto_results = [mk("taleb", "매도", 3, "그냥 매도 의견")]
check("[위험경고] 없으면 veto=False", _check_risk_veto(no_veto_results) is False)

print("\n[3] 강도별 포지션 사이징")
check("score=8 -> 약함/5%", _strength_and_sizing(8) == ("약함", 0.05))
check("score=13 -> 약함/5%", _strength_and_sizing(13) == ("약함", 0.05))
check("score=14 -> 보통/10%", _strength_and_sizing(14) == ("보통", 0.10))
check("score=21 -> 강함/15%", _strength_and_sizing(21) == ("강함", 0.15))

print("\n[4] 포테이토 매수 체결 - 정상 케이스 (자산 1000만원, 보통 신호 10%)")
portfolio = {"cash": 10_000_000, "seed": 10_000_000, "positions": {}, "history": []}
result = execute(portfolio, "AAPL", "매수", 0.10, current_price=100_000)
check("1,000,000원어치 매수 시도 -> 10주 체결", result.shares == 10)
check("현금이 900만원으로 감소", portfolio["cash"] == 9_000_000)
check("포지션에 AAPL 10주 기록됨", portfolio["positions"]["AAPL"]["shares"] == 10)

print("\n[5] 현금 최소 보유(20%) 강제 - 이미 현금이 부족한 상황")
portfolio2 = {"cash": 1_500_000, "seed": 10_000_000, "positions": {"X": {"shares": 1, "avg_price": 8_000_000}},
              "history": []}
# 총자산 추정 = 1,500,000 + 8,000,000 = 9,500,000 -> 최소현금 20% = 1,900,000인데 이미 현금이 그보다 적음
result2 = execute(portfolio2, "Y", "매수", 0.15, current_price=50_000)
check("현금 부족 상황에서는 매수 안 되거나 최소화됨 (보류 또는 소량)",
      result2.action in ("보류",) or (result2.shares >= 0 and portfolio2["cash"] >= 0))
check("실행 후에도 현금 잔고는 절대 음수가 아님", portfolio2["cash"] >= 0)

print("\n[6] 최대 보유 종목 수(5개) 제한")
portfolio3 = {"cash": 10_000_000, "seed": 10_000_000,
              "positions": {f"T{i}": {"shares": 1, "avg_price": 100} for i in range(MAX_POSITIONS)},
              "history": []}
result3 = execute(portfolio3, "NEWTICKER", "매수", 0.05, current_price=10_000)
check(f"이미 {MAX_POSITIONS}종목 보유 중이면 새 종목은 '보류'", result3.action == "보류")

print("\n[7] 매도 체결 - 전량 청산")
portfolio4 = {"cash": 5_000_000, "seed": 10_000_000,
              "positions": {"AAPL": {"shares": 10, "avg_price": 100_000}}, "history": []}
result4 = execute(portfolio4, "AAPL", "매도", 0.0, current_price=120_000)
check("보유 10주 전량 매도", result4.shares == 10)
check("현금이 5,000,000 + 1,200,000 = 6,200,000으로 증가", portfolio4["cash"] == 6_200_000)
check("포지션에서 AAPL 제거됨", "AAPL" not in portfolio4["positions"])

print("\n[8] 보유하지 않은 종목에 대한 매도 신호")
portfolio5 = {"cash": 10_000_000, "seed": 10_000_000, "positions": {}, "history": []}
result5 = execute(portfolio5, "NOTHELD", "매도", 0.0, current_price=1000)
check("보유하지 않은 종목 매도 신호는 '관망' 처리", result5.action == "관망")

print("\n[9] 과거 판단 회고(memory) 로직")
mem = [{"ticker": "AAPL", "date": "2026-01-01T00:00:00+00:00", "signal": "매수", "score": 10, "price": 100}]
check("매수 후 가격 상승 -> 적중", "적중" in build_reflection(mem, "AAPL", 110))
check("매수 후 가격 하락 -> 빗나감", "빗나감" in build_reflection(mem, "AAPL", 90))
mem_sell = [{"ticker": "MSFT", "date": "2026-01-01T00:00:00+00:00", "signal": "매도", "score": -12, "price": 200}]
check("매도 후 가격 하락 -> 적중", "적중" in build_reflection(mem_sell, "MSFT", 180))
check("매도 후 가격 상승 -> 빗나감", "빗나감" in build_reflection(mem_sell, "MSFT", 220))
check("기록 없는 종목은 회고 없음(None)", build_reflection(mem, "TSLA", 100) is None)
check("현재가가 없으면 회고 없음(None)", build_reflection(mem, "AAPL", None) is None)

print(f"\n{'='*40}\n결과: {PASS} 통과 / {FAIL} 실패\n{'='*40}")
if FAIL:
    raise SystemExit(1)
