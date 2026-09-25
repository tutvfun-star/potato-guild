# -*- coding: utf-8 -*-
"""
다종목 자동 스크리닝 - 완전 무료(LLM 호출 없음).

기존 방식은 "종목 코드를 하나씩 직접 입력"해야만 분석이 시작됐는데, 이 모듈은 그 앞단에
"코스피/코스닥 대형주 전체를 훑어서 유망해 보이는 종목을 골라내는" 무료 사전 단계를 추가한다.

핵심 설계: 여기서는 Claude를 전혀 호출하지 않는다. yfinance에서 가져온 PER/PBR/ROE/RSI
같은 숫자만으로 규칙 기반 점수를 매기고 정렬한다 - 그래서 몇 종목을 스캔하든 추가 비용이
0원이다. 이 스크리닝에서 뽑힌 종목 중 더 자세히 알고 싶은 게 있으면, 기존처럼
`python main.py <종목코드>`로 9인 전문가 딥다이브(유료, 소액)를 별도로 돌리면 된다.

점수 산식은 가치투자 관점(저PER/저PBR/고ROE)에 RSI로 과매수/과매도 힌트를 더한 아주
단순한 규칙이다 - "예측"이 아니라 "1차로 걸러내기 위한 참고용 스코어"라는 점을 분명히
하기 위해 일부러 복잡하게 만들지 않았다.
"""
import socket
import time

import yfinance as yf

from core.data_fetch import _calc_rsi, _pe_pb_with_fallback

socket.setdefaulttimeout(15)

# 스크리닝은 100개가 넘는 종목을 훑기 때문에, 개별 종목 하나가 재시도를 반복하면 전체
# 실행 시간이 너무 길어진다. 그래서 core/data_fetch.py의 3회 재시도와 달리 여기서는
# 딱 1번만 더 시도하고, 그래도 실패하면 그 종목은 그냥 건너뛴다 - 스크리닝은 "최대한 많이
# 훑는 것"이 목적이지 종목 하나하나의 완벽한 신뢰성이 목적이 아니기 때문이다.
SCREENING_RETRY_WAIT_SECONDS = 3


def _fetch_screening_metrics(ticker: str) -> dict | None:
    """스크리닝용 가벼운 조회 - 뉴스(RSS)는 스킵하고 가격/PER/PBR/ROE/RSI만 가져온다.
    (뉴스까지 매번 100여 개 종목에 대해 가져오면 시간이 너무 오래 걸리고, 어차피 스크리닝
    단계에서는 숫자만으로 1차 필터링을 하기 때문에 필요 없다.)"""

    def _do_fetch():
        t = yf.Ticker(ticker)
        hist = t.history(period="3mo")
        info = t.info
        return hist, info

    for attempt in (1, 2):
        try:
            hist, info = _do_fetch()
            if hist.empty:
                return None
            closes = hist["Close"].tolist()
            price = round(closes[-1], 2)
            change_pct = round((closes[-1] / closes[-2] - 1) * 100, 2) if len(closes) > 1 else None
            rsi = _calc_rsi(closes)
            pe_ratio, pe_note, pb_ratio, pb_note = _pe_pb_with_fallback(info, price)
            roe = info.get("returnOnEquity")
            return {
                "price": price,
                "change_pct": change_pct,
                "pe_ratio": pe_ratio,
                "pe_ratio_note": pe_note,
                "pb_ratio": pb_ratio,
                "pb_ratio_note": pb_note,
                "roe": roe,
                "rsi": rsi,
            }
        except Exception:
            if attempt == 1:
                time.sleep(SCREENING_RETRY_WAIT_SECONDS)
    return None


def _score_candidate(pe_ratio, pb_ratio, roe, rsi) -> tuple[int, list[str]]:
    """저PER/저PBR/고ROE에 가점을, RSI 과매수엔 감점을 주는 아주 단순한 규칙 기반 점수.
    데이터가 없는 항목은 그냥 건너뛴다(가점도 감점도 안 함) - 값을 지어내지 않기 위함.
    반환: (점수, 점수 산정 근거 문장 리스트)"""
    score = 0
    reasons = []

    if pe_ratio is not None and pe_ratio > 0:
        if pe_ratio < 10:
            score += 2
            reasons.append(f"PER {pe_ratio} (저평가 구간, 10 미만)")
        elif pe_ratio < 15:
            score += 1
            reasons.append(f"PER {pe_ratio} (양호한 구간, 15 미만)")

    if pb_ratio is not None and pb_ratio > 0:
        if pb_ratio < 1:
            score += 2
            reasons.append(f"PBR {pb_ratio} (자산가치 대비 저평가, 1 미만)")
        elif pb_ratio < 2:
            score += 1
            reasons.append(f"PBR {pb_ratio} (양호한 구간, 2 미만)")

    if roe is not None:
        if roe > 0.15:
            score += 2
            reasons.append(f"ROE {roe*100:.1f}% (높은 자본 효율성, 15% 초과)")
        elif roe > 0.08:
            score += 1
            reasons.append(f"ROE {roe*100:.1f}% (양호한 수익성, 8% 초과)")

    if rsi is not None:
        if rsi < 30:
            score += 1
            reasons.append(f"RSI {rsi} (과매도 구간, 저가 매수 기회 가능성)")
        elif rsi > 75:
            score -= 1
            reasons.append(f"RSI {rsi} (과매수 구간, 단기 조정 위험)")

    return score, reasons


def screen_universe(universe: list[dict], top_n: int = 10) -> list[dict]:
    """universe(티커/이름 목록)를 전부 훑어서 점수 기준 상위 top_n개를 반환한다.

    데이터를 아예 못 가져온 종목(전부 None)은 순위에서 제외한다 - 점수 0점이 "데이터가
    없어서 0점"인지 "숫자는 있는데 특별히 저평가/고평가 신호가 없어서 0점"인지 섞이면
    결과를 신뢰하기 어렵기 때문이다.
    """
    candidates = []
    for entry in universe:
        ticker = entry["ticker"]
        metrics = _fetch_screening_metrics(ticker)
        if metrics is None:
            continue
        has_any_fundamental = any(
            metrics.get(k) is not None for k in ("pe_ratio", "pb_ratio", "roe")
        )
        if not has_any_fundamental:
            continue
        score, reasons = _score_candidate(
            metrics["pe_ratio"], metrics["pb_ratio"], metrics["roe"], metrics["rsi"]
        )
        candidates.append({
            "ticker": ticker,
            "name": entry.get("name", ticker),
            "score": score,
            "reasons": reasons,
            **metrics,
        })

    candidates.sort(key=lambda c: c["score"], reverse=True)
    return candidates[:top_n]
