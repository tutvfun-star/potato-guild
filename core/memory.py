# -*- coding: utf-8 -*-
"""
과거 판단 회고(reflection) 메커니즘

TauricResearch/TradingAgents의 아이디어를 참고: 지난 매매 판단이 실제로 맞았는지를
가격으로 검증해서, 그 결과를 다음 판단의 참고 자료로 넘겨준다.
LLM이 스스로 "기억"하는 게 아니라, 코드가 과거 기록(JSON 파일)을 읽어서 계산하고
문장으로 만들어주는 방식이다 - 이 프로젝트 전체에서 지키는 원칙(돈과 관련된 계산은
LLM의 무작위성이 아니라 결정론적인 코드로 처리한다)과 동일하다.
"""
import json
from datetime import datetime, timezone
from pathlib import Path

MEMORY_PATH = Path(__file__).resolve().parent.parent / "data" / "memory.json"


def load_memory() -> list[dict]:
    if MEMORY_PATH.exists():
        with open(MEMORY_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    return []


def save_memory(records: list[dict]) -> None:
    MEMORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(MEMORY_PATH, "w", encoding="utf-8") as f:
        json.dump(records, f, ensure_ascii=False, indent=2)


def record_decision(records: list[dict], ticker: str, signal: str, score: int, price: float | None) -> None:
    """이번 판단을 기록에 추가한다 (다음번 같은 종목 회고에 쓰인다)."""
    records.append(
        {
            "ticker": ticker,
            "date": datetime.now(timezone.utc).isoformat(),
            "signal": signal,
            "score": score,
            "price": price,
        }
    )


def build_reflection(records: list[dict], ticker: str, current_price: float | None) -> str | None:
    """같은 종목의 과거 매수/매도 판단 중 가장 최근 것을 찾아, 지금 가격과 비교해서
    한 문단짜리 회고를 만든다. 참고할 과거 기록이 없으면 None을 반환한다
    (관망 판단은 실제로 매매가 없었으므로 회고 대상에서 제외한다).
    """
    if current_price is None:
        return None

    past = [
        r for r in records
        if r["ticker"] == ticker and r["signal"] in ("매수", "매도") and r.get("price")
    ]
    if not past:
        return None

    last = past[-1]
    change_pct = round((current_price / last["price"] - 1) * 100, 2)
    was_correct = (last["signal"] == "매수" and change_pct > 0) or (last["signal"] == "매도" and change_pct < 0)
    verdict_text = "적중" if was_correct else "빗나감"

    return (
        f"지난 {last['date'][:10]}에 {ticker}에 대해 '{last['signal']}' 신호(스코어 {last['score']})를 냈고, "
        f"그 이후 가격이 {change_pct:+.2f}% 변동했습니다 ({verdict_text})."
    )
