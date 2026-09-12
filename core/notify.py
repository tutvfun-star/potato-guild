# -*- coding: utf-8 -*-
"""텔레그램 알림 - 토큰이 없으면 콘솔 출력으로 대체(테스트 편의)."""
import os
import time
from datetime import datetime
from zoneinfo import ZoneInfo

import requests

RETRY_BACKOFF_SECONDS = [3, 7, 15]


def send_telegram(message: str) -> bool:
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


def build_report_message(verdict, trade_result, perf: dict) -> str:
    # 실행 시각(한국시간)을 맨 위에 찍어서, 텔레그램에서 언제 생성된 리포트인지
    # 바로 알 수 있게 한다. GitHub Actions는 UTC로 돌기 때문에 반드시 타임존을
    # 명시해서 KST로 변환해야 한다.
    now_kst = datetime.now(ZoneInfo("Asia/Seoul")).strftime("%Y-%m-%d %H:%M:%S")
    lines = [
        f"🕒 {now_kst} (KST)",
        f"🥔 포테이토 길드 리포트 - {verdict.ticker}",
        "",
        f"👑 멍거: {verdict.briefing}",
        "",
        f"📊 종합 스코어: {verdict.score} / 최종 신호: {verdict.signal}"
        + (f" ({verdict.strength}, {verdict.position_pct*100:.0f}%)" if verdict.strength else ""),
    ]
    if verdict.risk_veto:
        lines.append("🛡️ 탈레브 위험 경고로 거부권이 발동되어 관망 처리되었습니다.")

    lines.append("")
    lines.append(f"💰 실행 결과: {trade_result.action} {trade_result.ticker}")
    if trade_result.shares:
        lines.append(f"   {trade_result.shares}주 @ {trade_result.price:,.0f}원 (총 {trade_result.amount:,.0f}원)")
    if trade_result.note:
        lines.append(f"   비고: {trade_result.note}")

    lines.append("")
    lines.append(
        f"📈 계좌 현황: 총자산 {perf['total_assets']:,}원 "
        f"(손익 {perf['pnl']:+,}원, {perf['pnl_pct']:+.2f}%) / 현금 {perf['cash']:,}원"
    )

    lines.append("")
    lines.append("🗣️ 전문가 의견 요약:")
    for r in verdict.analyst_results:
        lines.append(f"  {r['emoji']} {r['display_name']}: {r['opinion']}(확신도 {r['confidence']}) - {r['reason']}")

    return "\n".join(lines)
