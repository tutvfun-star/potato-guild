# -*- coding: utf-8 -*-
"""
데이터 수집 - 무료 소스만 사용 (API 키 불필요)

- 가격/재무: yfinance
- 뉴스: 무료 RSS 피드 (feedparser)
- 커뮤니티: 지금은 자리표시자(placeholder). 나중에 Reddit/디시 API로 교체 가능.
"""
import socket
import time
from dataclasses import dataclass, field

import feedparser
import yfinance as yf

# yfinance/feedparser는 내부적으로 요청에 타임아웃을 안 거는 경우가 있어서,
# 네트워크가 막혀있거나 응답이 없으면 프로그램이 무한정 멈출 수 있다.
# 프로세스 전체에 기본 소켓 타임아웃을 걸어서, 응답이 없으면 15초 후 에러를 내고
# 넘어가도록 강제한다 (무한 대기 방지).
socket.setdefaulttimeout(15)

RETRY_BACKOFF_SECONDS = [3, 7, 15]


def _with_retry(fn, label: str):
    """간헐적인 네트워크 끊김에 대비한 재시도 래퍼 (Gemini/Claude/텔레그램과 동일한 방식)."""
    last_error = None
    for attempt in range(1, len(RETRY_BACKOFF_SECONDS) + 2):
        try:
            return fn()
        except Exception as e:
            last_error = e
            if attempt <= len(RETRY_BACKOFF_SECONDS):
                wait = RETRY_BACKOFF_SECONDS[attempt - 1]
                print(f"   ⏳ {label} 조회 실패, {wait}초 후 재시도: {e}")
                time.sleep(wait)
    raise last_error

# 구글 뉴스 RSS - 키 없이 종목명으로 검색 가능
GOOGLE_NEWS_RSS = "https://news.google.com/rss/search?q={query}&hl=ko&gl=KR&ceid=KR:ko"


@dataclass
class StockSnapshot:
    ticker: str
    price: float | None = None
    change_pct: float | None = None
    pe_ratio: float | None = None
    pb_ratio: float | None = None
    roe: float | None = None
    rsi: float | None = None
    news_headlines: list[str] = field(default_factory=list)
    community_note: str = "커뮤니티 데이터 소스 미연동 (플레이스홀더)"


def _calc_rsi(closes, period: int = 14) -> float | None:
    if len(closes) < period + 1:
        return None
    deltas = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
    gains = [d for d in deltas[-period:] if d > 0]
    losses = [-d for d in deltas[-period:] if d < 0]
    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return round(100 - (100 / (1 + rs)), 2)


def fetch_snapshot(ticker: str, news_query: str | None = None) -> StockSnapshot:
    """한 종목의 가격/재무/뉴스 스냅샷을 가져온다."""
    snap = StockSnapshot(ticker=ticker)

    def _fetch_price_and_fundamentals():
        t = yf.Ticker(ticker)
        hist = t.history(period="3mo")
        info = t.info
        return hist, info

    try:
        hist, info = _with_retry(_fetch_price_and_fundamentals, label=f"{ticker} 가격/재무")
        if not hist.empty:
            closes = hist["Close"].tolist()
            snap.price = round(closes[-1], 2)
            snap.change_pct = round((closes[-1] / closes[-2] - 1) * 100, 2) if len(closes) > 1 else None
            snap.rsi = _calc_rsi(closes)

        snap.pe_ratio = info.get("trailingPE")
        snap.pb_ratio = info.get("priceToBook")
        snap.roe = info.get("returnOnEquity")
    except Exception as e:
        snap.community_note = f"가격/재무 데이터 조회 실패 (재시도 3회 모두 실패): {e}"

    def _fetch_news():
        query = news_query or ticker
        feed = feedparser.parse(GOOGLE_NEWS_RSS.format(query=query))
        if feed.bozo and not feed.entries:
            raise RuntimeError(f"뉴스 피드 파싱 실패: {feed.bozo_exception}")
        return feed

    try:
        feed = _with_retry(_fetch_news, label=f"{ticker} 뉴스")
        snap.news_headlines = [entry.title for entry in feed.entries[:5]]
    except Exception:
        snap.news_headlines = []

    return snap


def snapshot_to_context(snap: StockSnapshot) -> str:
    """분석가 프롬프트에 넣을 텍스트 컨텍스트로 변환."""
    lines = [
        f"종목: {snap.ticker}",
        f"현재가: {snap.price} (전일 대비 {snap.change_pct}%)" if snap.price else "현재가: 데이터 없음",
        f"PER: {snap.pe_ratio}, PBR: {snap.pb_ratio}, ROE: {snap.roe}",
        f"RSI(14): {snap.rsi}",
        "최근 뉴스 헤드라인:",
    ]
    lines += [f"  - {h}" for h in snap.news_headlines] or ["  (뉴스 없음)"]
    lines.append(f"커뮤니티: {snap.community_note}")
    return "\n".join(lines)
