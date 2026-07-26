#!/usr/bin/env python3
"""当日の残りタスク見積時間を集計して data.json を出力する。

Notion API を直接叩く（MCP は使わない）。launchd から定期実行する想定。

必要な設定:
  secrets.json に {"notion_token": "ntn_xxx"} を置く（.gitignore 済み）
  config.json に就寝時刻・除外ルール・DB ID を置く

集計ルール（本人確定）:
  - 実行日 = 今日
  - 状態が「完了」「実行せず」でないもの（= 実行ページの今日ビューに残っているもの）
  - 名前が excludeNames のもの（睡眠）は除外。バッファは含める
  - 行動DB と リピートタスクDB の「見積時間」を合算
"""

import json
import sys
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

BASE = Path(__file__).resolve().parent
NOTION_VERSION = "2025-09-03"  # data_sources エンドポイントに必要

# config.json の collection:// URL から取り出す data source ID
ACTION_DS = "161fee9b-63ac-8131-add8-000bb5978d4f"
REPEAT_DS = "070fee9b-63ac-824f-bfc3-079c5cae9a9b"


def load_json(name):
    with open(BASE / name, encoding="utf-8") as f:
        return json.load(f)


def notion_query(token, data_source_id, today):
    """指定 data source から今日の実行日を持つページを全件取得する。"""
    url = f"https://api.notion.com/v1/data_sources/{data_source_id}/query"
    headers = {
        "Authorization": f"Bearer {token}",
        "Notion-Version": NOTION_VERSION,
        "Content-Type": "application/json",
    }
    results = []
    cursor = None
    while True:
        body = {
            "filter": {"property": "実行日", "date": {"equals": today}},
            "page_size": 100,
        }
        if cursor:
            body["start_cursor"] = cursor
        req = urllib.request.Request(
            url, data=json.dumps(body).encode(), headers=headers, method="POST"
        )
        with urllib.request.urlopen(req, timeout=30) as res:
            payload = json.load(res)
        results.extend(payload.get("results", []))
        if not payload.get("has_more"):
            break
        cursor = payload.get("next_cursor")
    return results


def plain_title(props):
    for prop in props.values():
        if prop.get("type") == "title":
            return "".join(t.get("plain_text", "") for t in prop["title"])
    return ""


def status_name(props):
    prop = props.get("状態") or {}
    if prop.get("type") == "status" and prop.get("status"):
        return prop["status"].get("name", "")
    return ""


def estimate_min(props):
    prop = props.get("見積時間") or {}
    val = prop.get("number")
    return val if isinstance(val, (int, float)) else 0


def collect(token, today, cfg):
    exclude_names = set(cfg.get("excludeNames", []))
    exclude_status = set(cfg.get("excludeStatuses", []))

    total = 0
    kept = []
    skipped = []
    for ds in (ACTION_DS, REPEAT_DS):
        for page in notion_query(token, ds, today):
            props = page.get("properties", {})
            name = plain_title(props)
            status = status_name(props)
            minutes = estimate_min(props)

            if status in exclude_status:
                skipped.append((name, minutes, f"status={status}"))
                continue
            if name in exclude_names:
                skipped.append((name, minutes, "excluded name"))
                continue
            total += minutes
            kept.append((name, minutes))
    return total, kept, skipped


def main():
    verbose = "-v" in sys.argv

    cfg = load_json("config.json")
    try:
        token = load_json("secrets.json")["notion_token"]
    except FileNotFoundError:
        sys.exit(
            "secrets.json が見つかりません。"
            '{"notion_token": "ntn_..."} を time-widget/secrets.json に作成してください。'
        )

    now = datetime.now()
    today = now.strftime("%Y-%m-%d")

    try:
        total, kept, skipped = collect(token, today, cfg)
    except urllib.error.HTTPError as e:
        sys.exit(f"Notion API エラー {e.code}: {e.read().decode()[:300]}")

    out = {
        "taskRemainMin": round(total),
        "bedtime": cfg.get("bedtime", "22:00"),
        "taskCount": len(kept),
        "updatedAt": now.astimezone().isoformat(timespec="seconds"),
        "date": today,
    }
    (BASE / "data.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    print(f"{today}: 残り {out['taskRemainMin']}分 ({total/60:.1f}h) / {len(kept)}件")
    if verbose:
        print("\n--- 集計対象 ---")
        for name, minutes in sorted(kept, key=lambda x: -x[1]):
            print(f"  {minutes:>4}分  {name}")
        print("\n--- 除外 ---")
        for name, minutes, why in skipped:
            print(f"  {minutes:>4}分  {name}  ({why})")


if __name__ == "__main__":
    main()
