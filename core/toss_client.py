# -*- coding: utf-8 -*-
"""
토스증권 실계좌 연동 - 조회 전용, 절대 주문은 하지 않는다.

사용자와 합의한 연동 범위(2026-09): "매수/매도 신호 알림까지, 주문은 감자가 직접".
즉 이 모듈은 실계좌의 예수금(현금)과 보유 종목 현황을 "조회"만 해서 텔레그램 리포트에
참고 정보로 얹어줄 뿐, 실제 매수/매도 주문 API(POST /api/v1/orders)는 절대 호출하지
않는다. 주문 실행은 항상 사용자가 토스증권 앱에서 직접 한다.

인증 흐름 (OAuth 2.0 Client Credentials Grant):
  1) POST /oauth2/token          - client_id/client_secret으로 access_token 발급
  2) GET  /api/v1/accounts       - 계좌 목록 조회 -> accountSeq 확인
  3) GET  /api/v1/assets         - Authorization + X-Tossinvest-Account 헤더로 보유종목/예수금 조회

주의: 아래 응답 필드명(cashAmount, holdings, purchasePrice 등)은 토스증권 공식 OpenAPI
문서(openapi.tossinvest.com)를 기준으로 작성했지만, 실제 계좌로 최초 실행하기 전까지는
100% 검증된 상태가 아니다. 그래서 파싱은 최대한 방어적으로(.get() + 기본값) 작성했고,
실패하면 전체 파이프라인을 멈추지 않고 "이 기능만" 건너뛰도록 했다 - 실계좌 조회는
부가 정보이지 필수 기능이 아니기 때문이다. 만약 실제 실행 결과가 이상하면(예: 보유
종목이 항상 안 잡힘) raw 응답 로그를 보고 필드명을 맞춰야 할 수 있다.
"""
import os
import time
from dataclasses import dataclass, field

import requests

TOSS_API_BASE = "https://openapi.tossinvest.com"
RETRY_BACKOFF_SECONDS = [3, 7, 15]

# yfinance 형식 한국 종목 티커(005930.KS)에서 토스 종목 코드(005930)를 뽑아낼 때
# 떼어낼 접미사들.
_KR_SUFFIXES = (".KS", ".KQ")

# 프로세스 하나가 여러 종목을 순서대로 돌 때(main.py의 여러 티커 루프) 매번 토큰을 새로
# 발급받지 않도록 메모리에 캐시해둔다. access_token은 재발급 시 이전 토큰을 즉시
# 무효화시키므로, 여기서 재사용하지 않으면 오히려 불필요한 재발급이 반복된다.
_token_cache = {"access_token": None, "expires_at": 0.0}


def _with_retry(fn, label: str):
    """간헐적인 네트워크 끊김에 대비한 재시도 래퍼 (다른 모듈들과 동일한 방식)."""
    last_error = None
    for attempt in range(1, len(RETRY_BACKOFF_SECONDS) + 2):
        try:
            return fn()
        except Exception as e:
            last_error = e
            if attempt <= len(RETRY_BACKOFF_SECONDS):
                wait = RETRY_BACKOFF_SECONDS[attempt - 1]
                print(f"   ⏳ {label} 실패, {wait}초 후 재시도: {e}")
                time.sleep(wait)
    raise last_error


@dataclass
class TossHolding:
    symbol: str
    name: str
    quantity: float
    purchase_price: float | None
    current_price: float | None
    evaluation_amount: float | None
    return_amount: float | None
    return_rate: float | None


@dataclass
class TossAccountSnapshot:
    cash: float
    total_evaluation_amount: float
    total_return_amount: float
    total_return_rate: float
    holdings: list = field(default_factory=list)  # list[TossHolding]


def _get_env(name: str) -> str:
    return (os.getenv(name) or "").strip()


def is_configured() -> bool:
    """토스 연동에 필요한 키가 설정돼 있는지. 미설정이면 이 기능 전체를 조용히 건너뛴다."""
    return bool(_get_env("TOSS_CLIENT_ID") and _get_env("TOSS_CLIENT_SECRET"))


def _to_toss_symbol(ticker: str) -> str:
    """yfinance 형식 티커(005930.KS)를 토스 종목 코드(005930)로 변환.
    해당 접미사가 없으면(미국 주식 등) 그대로 반환한다."""
    for suffix in _KR_SUFFIXES:
        if ticker.upper().endswith(suffix):
            return ticker[: -len(suffix)]
    return ticker


def _to_float(value, default=None):
    if value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _fetch_access_token() -> str:
    now = time.time()
    # 만료 30초 전까지는 캐시된 토큰을 재사용한다.
    if _token_cache["access_token"] and now < _token_cache["expires_at"] - 30:
        return _token_cache["access_token"]

    client_id = _get_env("TOSS_CLIENT_ID")
    client_secret = _get_env("TOSS_CLIENT_SECRET")

    def _do_request():
        r = requests.post(
            f"{TOSS_API_BASE}/oauth2/token",
            data={
                "grant_type": "client_credentials",
                "client_id": client_id,
                "client_secret": client_secret,
            },
            timeout=20,
        )
        r.raise_for_status()
        return r.json()

    data = _with_retry(_do_request, label="토스 access_token 발급")
    token = data["access_token"]
    expires_in = data.get("expires_in", 3000)
    _token_cache["access_token"] = token
    _token_cache["expires_at"] = now + expires_in
    return token


def _fetch_account_seq(access_token: str) -> str:
    # 계좌가 하나뿐이라 accountSeq가 안 바뀌는 걸 알고 있다면, TOSS_ACCOUNT_SEQ 환경변수로
    # 목록 조회를 아예 건너뛸 수 있다(선택 사항).
    pinned = _get_env("TOSS_ACCOUNT_SEQ")
    if pinned:
        return pinned

    def _do_request():
        r = requests.get(
            f"{TOSS_API_BASE}/api/v1/accounts",
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=20,
        )
        r.raise_for_status()
        return r.json()

    accounts = _with_retry(_do_request, label="토스 계좌 목록 조회")
    if isinstance(accounts, dict):
        accounts = accounts.get("accounts") or accounts.get("data") or []
    if not accounts:
        raise RuntimeError("토스 계좌 목록이 비어 있습니다 (계좌 없음 또는 응답 형식이 예상과 다름)")
    account_seq = accounts[0].get("accountSeq")
    if not account_seq:
        raise RuntimeError(f"토스 계좌 응답에서 accountSeq를 찾을 수 없습니다. raw 응답: {accounts[0]}")
    return str(account_seq)


def _parse_assets(raw: dict) -> TossAccountSnapshot:
    """/api/v1/assets 응답을 TossAccountSnapshot으로 변환. 필드명이 문서와 다를 가능성에
    대비해 summary가 없으면 최상위 dict를 그대로 summary처럼 취급한다."""
    summary = raw.get("summary", raw)
    holdings_raw = raw.get("holdings", [])
    holdings = []
    for h in holdings_raw:
        holdings.append(
            TossHolding(
                symbol=str(h.get("symbol", "")),
                name=str(h.get("name", "")),
                quantity=_to_float(h.get("quantity"), 0.0),
                purchase_price=_to_float(h.get("purchasePrice")),
                current_price=_to_float(h.get("currentPrice")),
                evaluation_amount=_to_float(h.get("evaluationAmount")),
                return_amount=_to_float(h.get("returnAmount")),
                return_rate=_to_float(h.get("returnRate")),
            )
        )
    return TossAccountSnapshot(
        cash=_to_float(summary.get("cashAmount"), 0.0),
        total_evaluation_amount=_to_float(summary.get("totalEvaluationAmount"), 0.0),
        total_return_amount=_to_float(summary.get("totalReturnAmount"), 0.0),
        total_return_rate=_to_float(summary.get("totalReturnRate"), 0.0),
        holdings=holdings,
    )


def fetch_account_snapshot() -> "TossAccountSnapshot | None":
    """실계좌 예수금/보유종목 스냅샷을 가져온다.

    토스 키가 설정되지 않았거나 조회 중 어떤 이유로든 실패하면 None을 반환하고 콘솔에만
    이유를 남긴다 (예외를 위로 던지지 않는다) - 이 기능이 막힌다고 해서 7인 분석/매매
    파이프라인 전체가 멈추면 안 되기 때문이다.
    """
    if os.getenv("POTATO_GUILD_MOCK") == "1":
        return TossAccountSnapshot(
            cash=3_000_000,
            total_evaluation_amount=7_000_000,
            total_return_amount=250_000,
            total_return_rate=3.7,
            holdings=[
                TossHolding(
                    symbol="005930", name="삼성전자", quantity=10,
                    purchase_price=68_000, current_price=71_000,
                    evaluation_amount=710_000, return_amount=30_000, return_rate=4.4,
                )
            ],
        )

    if not is_configured():
        return None

    try:
        access_token = _fetch_access_token()
        account_seq = _fetch_account_seq(access_token)

        def _do_request():
            r = requests.get(
                f"{TOSS_API_BASE}/api/v1/assets",
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "X-Tossinvest-Account": account_seq,
                },
                timeout=20,
            )
            r.raise_for_status()
            return r.json()

        raw = _with_retry(_do_request, label="토스 실계좌 자산 조회")
        return _parse_assets(raw)
    except Exception as e:
        print(f"   ⚠️ 토스 실계좌 조회 실패 (이 정보 없이 계속 진행합니다): {e}")
        return None


def find_holding(snapshot: "TossAccountSnapshot | None", ticker: str) -> "TossHolding | None":
    """분석 중인 종목(yfinance 티커 형식)을 실계좌에서 이미 보유 중인지 찾는다."""
    if snapshot is None:
        return None
    symbol = _to_toss_symbol(ticker)
    for h in snapshot.holdings:
        if h.symbol == symbol or h.symbol.lstrip("0") == symbol.lstrip("0"):
            return h
    return None
