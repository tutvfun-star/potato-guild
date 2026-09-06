# -*- coding: utf-8 -*-
"""
포테이토 - 모의투자 실행 엔진

LLM을 전혀 사용하지 않는다. 돈(비록 가상이지만)과 관련된 결정은 항상
결정론적인 규칙으로만 움직여야, 나중에 "왜 이렇게 됐지?"를 100% 재현하고
설명할 수 있기 때문이다.

규칙 (사용자와 합의한 내용):
  - 가상 시드머니: 10,000,000원
  - 동시 보유 최대 5종목 (분산 강제)
  - 항상 자산의 20% 이상은 현금으로 유지
  - 포지션 크기: 길드마스터가 정한 강도(약함/보통/강함)에 따라 자산의 5%/10%/15%
  - 매도 신호가 뜨면 보유 중인 해당 종목은 전량 매도 (초보자 기준 단순화)
"""
import json
import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

SEED_MONEY = 10_000_000
MAX_POSITIONS = 5
MIN_CASH_RESERVE_RATIO = 0.20

PORTFOLIO_PATH = Path(__file__).resolve().parent.parent / "data" / "portfolio.json"


@dataclass
class TradeResult:
    action: str          # "매수" | "매도" | "보류" | "관망"
    ticker: str
    shares: int = 0
    price: float = 0.0
    amount: float = 0.0
    note: str = ""


def load_portfolio() -> dict:
    if PORTFOLIO_PATH.exists():
        with open(PORTFOLIO_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"cash": SEED_MONEY, "seed": SEED_MONEY, "positions": {}, "history": []}


def save_portfolio(portfolio: dict) -> None:
    PORTFOLIO_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(PORTFOLIO_PATH, "w", encoding="utf-8") as f:
        json.dump(portfolio, f, ensure_ascii=False, indent=2)


def _total_assets(portfolio: dict, price_lookup: dict) -> float:
    total = portfolio["cash"]
    for ticker, pos in portfolio["positions"].items():
        price = price_lookup.get(ticker, pos["avg_price"])
        total += pos["shares"] * price
    return total


def execute(portfolio: dict, ticker: str, signal: str, position_pct: float, current_price: float) -> TradeResult:
    """길드의 신호를 받아 실제로 모의 매매를 체결하고 portfolio를 갱신한다."""
    price_lookup = {ticker: current_price}
    total_assets = _total_assets(portfolio, price_lookup)

    if signal == "관망" or current_price is None or current_price <= 0:
        return TradeResult(action="관망", ticker=ticker, note="신호 없음 또는 가격 데이터 없음")

    if signal == "매도":
        pos = portfolio["positions"].get(ticker)
        if not pos or pos["shares"] <= 0:
            return TradeResult(action="관망", ticker=ticker, note="보유 중이 아니라 매도할 수 없음")
        shares = pos["shares"]
        amount = shares * current_price
        portfolio["cash"] += amount
        del portfolio["positions"][ticker]
        result = TradeResult(action="매도", ticker=ticker, shares=shares, price=current_price, amount=amount)
        _log_history(portfolio, result)
        return result

    if signal == "매수":
        if ticker not in portfolio["positions"] and len(portfolio["positions"]) >= MAX_POSITIONS:
            return TradeResult(
                action="보류", ticker=ticker,
                note=f"이미 {MAX_POSITIONS}개 종목 보유 중 (분산 원칙상 매수 보류)",
            )

        target_amount = total_assets * position_pct
        min_cash_after = total_assets * MIN_CASH_RESERVE_RATIO
        max_spendable = max(0.0, portfolio["cash"] - min_cash_after)
        spend_amount = min(target_amount, max_spendable)

        shares_to_buy = math.floor(spend_amount / current_price)
        if shares_to_buy <= 0:
            return TradeResult(
                action="보류", ticker=ticker,
                note="현금 최소 보유 비율(20%)을 지키면 살 수 있는 수량이 없음",
            )

        cost = shares_to_buy * current_price
        portfolio["cash"] -= cost

        existing = portfolio["positions"].get(ticker)
        if existing:
            total_shares = existing["shares"] + shares_to_buy
            new_avg = (existing["shares"] * existing["avg_price"] + cost) / total_shares
            portfolio["positions"][ticker] = {"shares": total_shares, "avg_price": round(new_avg, 2)}
        else:
            portfolio["positions"][ticker] = {"shares": shares_to_buy, "avg_price": current_price}

        result = TradeResult(action="매수", ticker=ticker, shares=shares_to_buy, price=current_price, amount=cost)
        _log_history(portfolio, result)
        return result

    return TradeResult(action="관망", ticker=ticker, note=f"알 수 없는 신호: {signal}")


def _log_history(portfolio: dict, result: TradeResult) -> None:
    portfolio.setdefault("history", []).append(
        {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "ticker": result.ticker,
            "action": result.action,
            "shares": result.shares,
            "price": result.price,
            "amount": result.amount,
        }
    )


def performance_summary(portfolio: dict, price_lookup: dict | None = None) -> dict:
    price_lookup = price_lookup or {}
    total = _total_assets(portfolio, price_lookup)
    pnl = total - portfolio["seed"]
    pnl_pct = (pnl / portfolio["seed"]) * 100
    return {
        "cash": round(portfolio["cash"]),
        "total_assets": round(total),
        "pnl": round(pnl),
        "pnl_pct": round(pnl_pct, 2),
        "positions": portfolio["positions"],
    }
