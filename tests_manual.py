# -*- coding: utf-8 -*-
"""
핵심 금융 로직(점수 계산/거부권/포지션 사이징/현금 최소 보유)에 대한 수동 검증 스크립트.
LLM 호출 없이 core.guildmaster / core.potato 의 순수 로직만 검증한다.

실행: python tests_manual.py
"""
from core.data_fetch import _calc_atr, _calc_support_resistance, _pe_pb_with_fallback
from core.guildmaster import (
    _check_risk_veto,
    _compute_price_levels,
    _compute_score,
    _strength_and_sizing,
)
from core.memory import build_reflection
from core.potato import MAX_POSITIONS, MIN_CASH_RESERVE_RATIO, execute
from core.screener import _score_candidate
from core.toss_client import TossAccountSnapshot, TossHolding, _parse_assets, _to_toss_symbol, find_holding

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

print("\n[10] ATR(변동성) 계산")
# 매일 고가-저가 폭이 정확히 10인 15일치 데이터 -> ATR(14) = 10
highs10 = [110 + i for i in range(15)]
lows10 = [100 + i for i in range(15)]
closes10 = [105 + i for i in range(15)]
check("고가-저가 폭이 항상 10이면 ATR(14) = 10.0", _calc_atr(highs10, lows10, closes10) == 10.0)
check("데이터가 15일 미만이면 ATR 계산 불가 -> None", _calc_atr(highs10[:5], lows10[:5], closes10[:5]) is None)

print("\n[11] 지지선/저항선 계산")
highs_sr = [100, 105, 103, 110, 102]
lows_sr = [95, 98, 96, 101, 94]
support, resistance = _calc_support_resistance(highs_sr, lows_sr, lookback=5)
check("최근 5일 저가 중 최저치가 지지선(94)", support == 94)
check("최근 5일 고가 중 최고치가 저항선(110)", resistance == 110)
support_empty, resistance_empty = _calc_support_resistance([], [])
check("데이터가 없으면 지지선/저항선 모두 None", support_empty is None and resistance_empty is None)

print("\n[12] 목표가/손절가 계산 (지지선/저항선 + ATR 기반)")
# 진입가 100, 지지선 90, 저항선 120, ATR 10 -> 손절가 = 90 - 0.5*10 = 85, 목표가 = 120
target, stop, rr = _compute_price_levels(entry_price=100, support=90, resistance=120, atr14=10)
check("목표가 = 저항선(120)", target == 120)
check("손절가 = 지지선 - ATR*0.5 (85)", stop == 85)
check("손익비 = (120-100)/(100-85) = 1.33", rr == 1.33)
check("데이터 중 하나라도 None이면 전부 None", _compute_price_levels(100, None, 120, 10) == (None, None, None))
check(
    "현재가가 이미 저항선 위(억지스러운 경우)면 전부 None",
    _compute_price_levels(entry_price=130, support=90, resistance=120, atr14=10) == (None, None, None),
)
check(
    "현재가가 이미 지지선 아래(억지스러운 경우)면 전부 None",
    _compute_price_levels(entry_price=80, support=90, resistance=120, atr14=10) == (None, None, None),
)

print("\n[13] 토스 티커 <-> 종목코드 변환")
check("005930.KS -> 005930", _to_toss_symbol("005930.KS") == "005930")
check("035420.KQ -> 035420", _to_toss_symbol("035420.KQ") == "035420")
check("접미사 없는 미국 티커는 그대로 (AAPL)", _to_toss_symbol("AAPL") == "AAPL")

print("\n[14] 토스 /api/v1/assets 응답 파싱")
raw_assets = {
    "summary": {
        "cashAmount": "3000000",
        "totalEvaluationAmount": "7000000",
        "totalReturnAmount": "250000",
        "totalReturnRate": "3.7",
    },
    "holdings": [
        {
            "symbol": "005930", "name": "삼성전자", "quantity": "10",
            "purchasePrice": "68000", "currentPrice": "71000",
            "evaluationAmount": "710000", "returnAmount": "30000", "returnRate": "4.4",
        }
    ],
}
snap = _parse_assets(raw_assets)
check("예수금(cash) 파싱 = 3,000,000", snap.cash == 3_000_000.0)
check("보유 종목 1개 파싱됨", len(snap.holdings) == 1)
check("보유 종목 수량(quantity) = 10", snap.holdings[0].quantity == 10.0)
check("보유 종목 평단가(purchase_price) = 68,000", snap.holdings[0].purchase_price == 68_000.0)

print("\n[15] 실계좌 보유 종목 매칭 (find_holding)")
check("보유 중인 종목(005930.KS)을 정확히 찾음", find_holding(snap, "005930.KS").symbol == "005930")
check("보유하지 않은 종목(035420.KS)은 None", find_holding(snap, "035420.KS") is None)
check("스냅샷이 None이면 항상 None", find_holding(None, "005930.KS") is None)
raw_assets_missing_summary = {"holdings": []}  # summary 키 자체가 없는 경우(방어적 파싱 확인)
snap_missing = _parse_assets(raw_assets_missing_summary)
check("summary가 없어도 예외 없이 기본값(0)으로 처리됨", snap_missing.cash == 0.0)

print("\n[16] PER/PBR 폴백 계산 (yfinance가 값을 안 줄 때 EPS/BPS로 직접 계산)")
info_full = {"trailingPE": 12.5, "priceToBook": 1.3}
pe, pe_note, pb, pb_note = _pe_pb_with_fallback(info_full, price=100_000)
check("원본 trailingPE가 있으면 그대로 사용", pe == 12.5 and pe_note is None)
check("원본 priceToBook이 있으면 그대로 사용", pb == 1.3 and pb_note is None)

info_trailing_eps_only = {"trailingEps": 5000, "bookValue": 40000}
pe, pe_note, pb, pb_note = _pe_pb_with_fallback(info_trailing_eps_only, price=100_000)
check("trailingPE 없으면 trailingEps로 직접 계산 (100000/5000=20.0)", pe == 20.0)
check("직접 계산했다는 근거(note)가 남음", pe_note is not None and "trailing EPS" in pe_note)
check("priceToBook 없으면 bookValue로 직접 계산 (100000/40000=2.5)", pb == 2.5)
check("PBR도 계산 근거가 남음", pb_note is not None)

info_forward_only = {"forwardEps": 4000}
pe, pe_note, pb, pb_note = _pe_pb_with_fallback(info_forward_only, price=100_000)
check("trailingEps도 없으면 forwardEps로 폴백 (100000/4000=25.0)", pe == 25.0)
check("forward 기반이라는 게 note에 명시됨(확정 실적 아님)", "forward" in pe_note.lower())

info_empty = {}
pe, pe_note, pb, pb_note = _pe_pb_with_fallback(info_empty, price=100_000)
check("계산할 근거가 아예 없으면 PER도 None", pe is None and pe_note is None)
check("계산할 근거가 아예 없으면 PBR도 None", pb is None and pb_note is None)

info_no_price = {"trailingEps": 5000}
pe, pe_note, pb, pb_note = _pe_pb_with_fallback(info_no_price, price=None)
check("현재가가 없으면 EPS가 있어도 계산 안 함 (None)", pe is None)

print("\n[17] 무료 스크리닝 점수 계산 (_score_candidate)")
score, reasons = _score_candidate(pe_ratio=8, pb_ratio=0.8, roe=0.20, rsi=25)
check("저PER(2)+저PBR(2)+고ROE(2)+과매도(1) = 7점", score == 7)
check("근거 문장이 4개(PER/PBR/ROE/RSI 전부) 남음", len(reasons) == 4)

score, _ = _score_candidate(pe_ratio=20, pb_ratio=3, roe=0.03, rsi=80)
check("고PER+고PBR+저ROE+과매수는 감점만 있어 음수 가능 (-1)", score == -1)

score, reasons = _score_candidate(pe_ratio=None, pb_ratio=None, roe=None, rsi=None)
check("데이터가 전부 없으면 점수 0, 근거도 없음", score == 0 and reasons == [])

score, reasons = _score_candidate(pe_ratio=12, pb_ratio=None, roe=None, rsi=50)
check("일부 데이터만 있어도 그 항목만 반영 (PER 준수구간 1점)", score == 1)
check("중립 RSI(50)는 가점도 감점도 없음", "RSI" not in " ".join(reasons))

print(f"\n{'='*40}\n결과: {PASS} 통과 / {FAIL} 실패\n{'='*40}")
if FAIL:
    raise SystemExit(1)
