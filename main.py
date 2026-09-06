# -*- coding: utf-8 -*-
"""
포테이토 길드 - 실행 진입점

사용법:
  python main.py 005930.KS 035420.KS      # 여러 종목을 한 사이클 돌림
  POTATO_GUILD_MOCK=1 python main.py AAPL # API 키 없이 구조만 테스트

종목 코드 형식(yfinance 기준):
  한국 주식: 005930.KS (삼성전자), 035420.KS (NAVER) 등
  미국 주식: AAPL, TSLA 등
"""
import sys

from dotenv import load_dotenv

from core.analysts import run_all_analysts
from core.data_fetch import fetch_snapshot, snapshot_to_context
from core.guildmaster import deliberate
from core.notify import build_report_message, send_telegram
from core.potato import execute, load_portfolio, performance_summary, save_portfolio

load_dotenv()


def run_cycle(ticker: str) -> None:
    print(f"\n{'='*50}\n🥔 포테이토 길드 소집 - {ticker}\n{'='*50}")

    print("1) 데이터 수집 중...")
    snap = fetch_snapshot(ticker)
    context = snapshot_to_context(snap)
    print(context)

    print("\n2) 7명의 전문가 소집 중 (Gemini)...")
    analyst_results = run_all_analysts(context)
    for r in analyst_results:
        print(f"   {r['emoji']} {r['display_name']}: {r['opinion']}(확신도 {r['confidence']}) - {r['reason']}")

    print("\n3) 멍거 종합 판단 중 (Claude)...")
    verdict = deliberate(ticker, context, analyst_results)
    print(f"   종합 스코어: {verdict.score} / 신호: {verdict.signal}")
    print(f"   브리핑: {verdict.briefing}")

    print("\n4) 포테이토 모의 매매 실행 중...")
    portfolio = load_portfolio()
    trade_result = execute(portfolio, ticker, verdict.signal, verdict.position_pct, snap.price)
    save_portfolio(portfolio)
    perf = performance_summary(portfolio, price_lookup={ticker: snap.price} if snap.price else {})
    print(f"   실행 결과: {trade_result.action} {trade_result.ticker} - {trade_result.note}")
    print(f"   계좌 현황: {perf}")

    print("\n5) 알림 발송 중...")
    message = build_report_message(verdict, trade_result, perf)
    send_telegram(message)


def main():
    tickers = sys.argv[1:]
    if not tickers:
        print("사용법: python main.py <종목코드1> <종목코드2> ...")
        print("예시:   python main.py 005930.KS AAPL")
        sys.exit(1)

    for ticker in tickers:
        run_cycle(ticker)


if __name__ == "__main__":
    main()
