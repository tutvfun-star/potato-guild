# -*- coding: utf-8 -*-
"""
포테이토 길드 - 실행 진입점

사용법:
  python main.py 005930.KS 035420.KS      # 특정 종목을 지정해서 9인 전문가 딥다이브(유료)
  python main.py --screen                 # 코스피/코스닥 대형주 전체를 무료로 스캔해서 추천 후보만 텔레그램으로 받기
  POTATO_GUILD_MOCK=1 python main.py AAPL # API 키 없이 구조만 테스트

종목 코드 형식(yfinance 기준):
  한국 주식: 005930.KS (삼성전자), 035420.KS (NAVER) 등
  미국 주식: AAPL, TSLA 등

--screen 모드는 Claude를 전혀 호출하지 않는 무료 기능이다 (core/screener.py 참고).
매일 자동으로 "어떤 종목을 봐야 할지" 추천만 받고, 그중 더 알고 싶은 종목만 위처럼
종목코드를 직접 지정해서 유료 딥다이브를 돌리는 2단계 방식을 쓰면 비용을 최소화할 수 있다.
"""
import sys

from dotenv import load_dotenv

from config.universe import FULL_UNIVERSE
from core.analysts import run_all_analysts, run_bull_bear_debate
from core.data_fetch import fetch_snapshot, snapshot_to_context
from core.guildmaster import deliberate
from core.memory import build_reflection, load_memory, record_decision, save_memory
from core.notify import build_report_message, build_screening_message, send_telegram
from core.potato import execute, load_portfolio, performance_summary, save_portfolio
from core.screener import screen_universe
from core.site_data import save_screening_snapshot, save_verdict_snapshot
from core.toss_client import fetch_account_snapshot, find_holding

load_dotenv()

SCREENING_TOP_N = 10  # 스크리닝 결과로 텔레그램에 보여줄 상위 후보 개수


def run_cycle(ticker: str) -> None:
    print(f"\n{'='*50}\n🥔 포테이토 길드 소집 - {ticker}\n{'='*50}")

    print("1) 데이터 수집 중...")
    snap = fetch_snapshot(ticker)
    context = snapshot_to_context(snap)
    print(context)

    memory = load_memory()
    reflection = build_reflection(memory, ticker, snap.price)
    if reflection:
        print(f"\n[과거 판단 회고] {reflection}")

    print("\n2) 7명의 전문가 소집 중 (Claude Haiku)...")
    analyst_results = run_all_analysts(context)
    for r in analyst_results:
        print(f"   {r['emoji']} {r['display_name']}: {r['opinion']}(확신도 {r['confidence']}) - {r['reason']}")

    print("\n2.5) 강세(린치) vs 약세(버리) 토론 중...")
    analyst_results = run_bull_bear_debate(context, analyst_results)
    for key in ("lynch", "burry"):
        r = next((x for x in analyst_results if x["key"] == key), None)
        if r:
            print(f"   (토론 후) {r['emoji']} {r['display_name']}: {r['opinion']}(확신도 {r['confidence']}) - {r['reason']}")

    print("\n3) 멍거 종합 판단 중 (Claude)...")
    verdict = deliberate(ticker, context, analyst_results, snap, reflection=reflection)
    print(f"   종합 스코어: {verdict.score} / 신호: {verdict.signal}")
    print(f"   브리핑: {verdict.briefing}")

    print("\n4) 포테이토 모의 매매 실행 중...")
    portfolio = load_portfolio()
    trade_result = execute(portfolio, ticker, verdict.signal, verdict.position_pct, snap.price)
    perf = performance_summary(portfolio, price_lookup={ticker: snap.price} if snap.price else {})
    # 웹 대시보드(index.html)는 백엔드 없이 이 JSON 파일을 그대로 fetch해서 읽기 때문에,
    # "이 분석 시점 기준" 계좌 현황(perf)을 portfolio.json 안에 같이 저장해둔다.
    portfolio["last_performance"] = perf
    save_portfolio(portfolio)
    print(f"   실행 결과: {trade_result.action} {trade_result.ticker} - {trade_result.note}")
    print(f"   계좌 현황: {perf}")

    record_decision(memory, ticker, verdict.signal, verdict.score, snap.price)
    save_memory(memory)

    print("\n4.5) 토스 실계좌 조회 중 (선택, 미설정이면 건너뜀)...")
    toss_snapshot = fetch_account_snapshot()
    toss_holding = find_holding(toss_snapshot, ticker)
    if toss_snapshot is not None:
        print(f"   예수금: {toss_snapshot.cash:,.0f}원 / 이 종목 보유: "
              f"{toss_holding.quantity if toss_holding else 0}주")

    print("\n5) 알림 발송 중...")
    message = build_report_message(verdict, trade_result, perf, toss_snapshot, toss_holding)
    send_telegram(message)

    print("\n5.5) 웹 대시보드 데이터 저장 중...")
    save_verdict_snapshot(verdict)


def run_screening() -> None:
    """코스피/코스닥 대형주 전체(config/universe.py)를 무료로 스캔해서 상위 후보만
    텔레그램으로 보내는 모드. LLM을 전혀 호출하지 않으므로 몇 번을 돌려도 비용이 0원이다."""
    print(f"\n{'='*50}\n🔍 포테이토 길드 무료 스크리닝 - {len(FULL_UNIVERSE)}종목 스캔\n{'='*50}")
    print("(PER/PBR/ROE/RSI 숫자만으로 점수를 매기는 방식이라 Claude를 호출하지 않습니다)")

    candidates = screen_universe(FULL_UNIVERSE, top_n=SCREENING_TOP_N)
    print(f"\n스캔 완료 - 상위 {len(candidates)}개 후보:")
    for i, c in enumerate(candidates, start=1):
        print(f"   {i}. {c['name']} ({c['ticker']}) - 점수 {c['score']}")

    message = build_screening_message(candidates, universe_size=len(FULL_UNIVERSE))
    send_telegram(message)

    save_screening_snapshot(candidates, universe_size=len(FULL_UNIVERSE))


def main():
    args = sys.argv[1:]
    if not args:
        print("사용법: python main.py <종목코드1> <종목코드2> ...   (특정 종목 딥다이브)")
        print("       python main.py --screen                     (무료 전체 스캔)")
        print("예시:   python main.py 005930.KS AAPL")
        sys.exit(1)

    if args == ["--screen"]:
        run_screening()
        return

    for ticker in args:
        run_cycle(ticker)


if __name__ == "__main__":
    main()
