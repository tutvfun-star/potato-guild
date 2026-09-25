# -*- coding: utf-8 -*-
"""텔레그램 알림 - 토큰이 없으면 콘솔 출력으로 대체(테스트 편의)."""
import os
import time
from datetime import datetime
from zoneinfo import ZoneInfo

import requests

RETRY_BACKOFF_SECONDS = [3, 7, 15]


def send_telegram(message: str) -> bool:
    # .strip(): Secret을 복사/붙여넣기할 때 끝에 줄바꿈이나 공백이 딸려 들어오는 실수를 방지
    token = (os.getenv("TELEGRAM_BOT_TOKEN") or "").strip()
    chat_id = (os.getenv("TELEGRAM_CHAT_ID") or "").strip()

    if not token or not chat_id:
        print("\n[알림 - 텔레그램 미설정, 콘솔에 대신 출력합니다]")
        print(message)
        return False

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    last_error = None
    for attempt in range(1, len(RETRY_BACKOFF_SECONDS) + 2):
        try:
            resp = requests.post(url, json={"chat_id": chat_id, "text": message}, timeout=20)
            resp.raise_for_status()
            return True
        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as e:
            last_error = e
            if attempt <= len(RETRY_BACKOFF_SECONDS):
                wait = RETRY_BACKOFF_SECONDS[attempt - 1]
                print(f"   ⏳ 텔레그램 전송 실패, {wait}초 후 재시도: {e}")
                time.sleep(wait)
        except Exception as e:
            last_error = e
            break

    print(f"[알림 발송 실패] {last_error}\n{message}")
    return False


def build_report_message(verdict, trade_result, perf: dict, toss_snapshot=None, toss_holding=None) -> str:
    # 실행 시각(한국시간)을 맨 위에 찍어서, 텔레그램에서 언제 생성된 리포트인지
    # 바로 알 수 있게 한다. GitHub Actions는 UTC로 돌기 때문에 반드시 타임존을
    # 명시해서 KST로 변환해야 한다.
    now_kst = datetime.now(ZoneInfo("Asia/Seoul")).strftime("%Y-%m-%d %H:%M:%S")
    lines = [
        f"🕒 {now_kst} (KST)",
        f"🥔 포테이토 길드 리포트 - {verdict.ticker}",
        "",
        f"👑 멍거(길드마스터): {verdict.briefing}",
        "",
        f"📊 종합 스코어: {verdict.score} / 최종 신호: {verdict.signal}"
        + (f" ({verdict.strength}, {verdict.position_pct*100:.0f}%)" if verdict.strength else ""),
    ]
    if verdict.risk_veto:
        lines.append("🛡️ 탈레브 위험 경고로 거부권이 발동되어 관망 처리되었습니다.")

    if verdict.target_price is not None:
        lines.append(
            f"🎯 목표가: {verdict.target_price:,.0f}원 / 🛑 손절가: {verdict.stop_loss:,.0f}원 "
            f"(손익비 1:{verdict.risk_reward}) — 예측이 아닌 최근 지지선/저항선·변동성 기반 가이드라인"
        )

    lines.append("")
    lines.append(f"💰 실행 결과: {trade_result.action} {trade_result.ticker}")
    if trade_result.shares:
        lines.append(f"   {trade_result.shares}주 @ {trade_result.price:,.0f}원 (총 {trade_result.amount:,.0f}원)")
    if trade_result.note:
        lines.append(f"   비고: {trade_result.note}")

    lines.append("")
    lines.append(
        f"📈 모의계좌 현황: 총자산 {perf['total_assets']:,}원 "
        f"(손익 {perf['pnl']:+,}원, {perf['pnl_pct']:+.2f}%) / 현금 {perf['cash']:,}원"
    )

    if toss_snapshot is not None:
        lines.append("")
        lines.append(
            f"🏦 토스 실계좌: 예수금 {toss_snapshot.cash:,.0f}원 / "
            f"총평가 {toss_snapshot.total_evaluation_amount:,.0f}원 "
            f"({toss_snapshot.total_return_amount:+,.0f}원, {toss_snapshot.total_return_rate:+.2f}%)"
        )
        if toss_holding is not None and toss_holding.quantity:
            avg = f"{toss_holding.purchase_price:,.0f}원" if toss_holding.purchase_price is not None else "정보 없음"
            eval_amt = (
                f", 평가손익 {toss_holding.return_amount:+,.0f}원 ({toss_holding.return_rate:+.2f}%)"
                if toss_holding.return_amount is not None
                else ""
            )
            lines.append(f"   이 종목 보유 중: {toss_holding.quantity:.0f}주 @ 평단가 {avg}{eval_amt}")
        else:
            lines.append("   이 종목은 실계좌에 보유하고 있지 않습니다.")

    lines.append("")
    lines.append("🗣️ 전문가 의견 요약:")
    for r in verdict.analyst_results:
        lines.append(
            f"  {r['emoji']} {r['display_name']}({r['role']}): "
            f"{r['opinion']}(확신도 {r['confidence']}) - {r['reason']}"
        )

    return "\n".join(lines)


def build_screening_message(candidates: list, universe_size: int) -> str:
    """core/screener.py의 무료 스크리닝 결과를 텔레그램 메시지로 변환.

    9인 전문가 딥다이브(build_report_message)와 달리 이 리포트는 LLM을 전혀 거치지 않은,
    순수 숫자 기반 점수라는 걸 메시지 안에서 분명히 밝힌다 - AI가 쓴 코멘트처럼 오해하지
    않도록 하기 위함이다."""
    now_kst = datetime.now(ZoneInfo("Asia/Seoul")).strftime("%Y-%m-%d %H:%M:%S")
    lines = [
        f"🕒 {now_kst} (KST)",
        f"🔍 포테이토 길드 무료 스크리닝 (코스피·코스닥 대형주 {universe_size}종목 훑음)",
        "",
        "※ 아래는 AI 판단이 아니라 PER/PBR/ROE/RSI 숫자만으로 매긴 규칙 기반 점수입니다.",
        "   관심 가는 종목은 `python main.py <종목코드>`로 9인 전문가 딥다이브를 따로 돌려보세요.",
        "",
    ]

    if not candidates:
        lines.append("이번 스캔에서는 데이터를 가져올 수 있는 종목이 없었습니다 (네트워크 문제일 수 있음).")
        return "\n".join(lines)

    for i, c in enumerate(candidates, start=1):
        price_text = f"{c['price']:,.0f}원" if c.get("price") is not None else "가격 정보 없음"
        lines.append(f"{i}. {c['name']} ({c['ticker']}) - 점수 {c['score']} / 현재가 {price_text}")
        if c.get("reasons"):
            for reason in c["reasons"]:
                lines.append(f"   - {reason}")
        else:
            lines.append("   - 특별히 저평가/과매도로 걸리는 지표는 없음 (중립)")

    return "\n".join(lines)
