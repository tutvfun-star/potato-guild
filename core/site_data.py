# -*- coding: utf-8 -*-
"""
웹 대시보드(GitHub Pages, 루트의 index.html)가 읽어가는 JSON 스냅샷을 만드는 모듈.

index.html은 별도 백엔드 없이 정적 파일만으로 동작한다 (GitHub Pages는 정적 파일만
서빙할 수 있기 때문). 그래서 main.py가 스크리닝/딥다이브를 돌릴 때마다 이 모듈로 결과를
data/ 폴더에 JSON으로 남기고, GitHub Actions가 그 JSON을 portfolio.json/memory.json과
함께 커밋해서 웹페이지가 항상 최신 상태를 보여주게 한다. index.html은 이 파일들을
fetch()로 그냥 읽기만 한다.
"""
import json
from datetime import datetime, timezone
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
SCREENING_PATH = DATA_DIR / "screening_latest.json"
VERDICTS_PATH = DATA_DIR / "verdicts.json"

# 웹페이지에 너무 오래된 딥다이브 기록까지 무한정 쌓이지 않도록 최근 N개만 보관한다.
MAX_VERDICT_HISTORY = 50


def save_screening_snapshot(candidates: list[dict], universe_size: int) -> None:
    """core/screener.py의 무료 스크리닝 결과를 웹페이지용으로 저장 (매번 덮어쓰기 - 스크리닝은
    "지금 시점의 스냅샷"이라는 의미가 크기 때문에 히스토리를 쌓지 않는다)."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    snapshot = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "universe_size": universe_size,
        "candidates": candidates,
    }
    with open(SCREENING_PATH, "w", encoding="utf-8") as f:
        json.dump(snapshot, f, ensure_ascii=False, indent=2)


def _verdict_to_dict(verdict) -> dict:
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "ticker": verdict.ticker,
        "score": verdict.score,
        "signal": verdict.signal,
        "strength": verdict.strength,
        "position_pct": verdict.position_pct,
        "risk_veto": verdict.risk_veto,
        "target_price": verdict.target_price,
        "stop_loss": verdict.stop_loss,
        "risk_reward": verdict.risk_reward,
        "briefing": verdict.briefing,
        "analyst_results": [
            {
                "key": r["key"],
                "display_name": r["display_name"],
                "role": r["role"],
                "emoji": r["emoji"],
                "opinion": r["opinion"],
                "confidence": r["confidence"],
                "reason": r["reason"],
            }
            for r in verdict.analyst_results
        ],
    }


def save_verdict_snapshot(verdict) -> None:
    """9인 전문가 딥다이브 결과 하나를 verdicts.json 맨 앞(최신순)에 추가한다.
    기존 기록은 그대로 두고 누적하되, MAX_VERDICT_HISTORY개를 넘으면 오래된 것부터 자른다."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    history = []
    if VERDICTS_PATH.exists():
        with open(VERDICTS_PATH, "r", encoding="utf-8") as f:
            history = json.load(f)

    history.insert(0, _verdict_to_dict(verdict))
    history = history[:MAX_VERDICT_HISTORY]

    with open(VERDICTS_PATH, "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, indent=2)
