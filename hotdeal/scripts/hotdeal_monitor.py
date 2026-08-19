#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import html
import json
import os
import re
import sys
from dataclasses import dataclass, asdict
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup
try:
    from curl_cffi import requests as http_requests
    HAS_CURL_CFFI = True
except ImportError:
    import requests as http_requests
    HAS_CURL_CFFI = False

ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "config.json"
DATA_PATH = ROOT / "data" / "latest.json"
KST = timezone(timedelta(hours=9))
PRICE_RE = re.compile(r"(?<!\d)(\d{1,3}(?:,\d{3})+|\d{4,9})\s*원")
DETAIL_PATH_RE = re.compile(r"-[0-9a-fA-F]{4,8}/?$")


@dataclass
class Deal:
    id: str
    title: str
    url: str
    price: int | None = None
    category: str = ""
    store: str = ""
    community: str = ""
    rawText: str = ""


def load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def save_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")
    tmp.replace(path)


def normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip().lower()


def parse_price(text: str) -> int | None:
    values = []
    for match in PRICE_RE.findall(text or ""):
        try:
            values.append(int(match.replace(",", "")))
        except ValueError:
            pass
    return values[0] if values else None


def fetch_html(url: str) -> str:
    headers = {
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.7,en;q=0.6",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
        "Referer": "https://www.google.com/",
    }
    kwargs = {
        "headers": headers,
        "timeout": 30,
        "allow_redirects": True,
    }
    if HAS_CURL_CFFI:
        kwargs["impersonate"] = "chrome"
    response = http_requests.get(url, **kwargs)
    if response.status_code != 200:
        raise RuntimeError(f"hotdeal.zip HTTP {response.status_code}")
    text = response.text
    if len(text) < 1000:
        raise RuntimeError("응답 HTML이 비정상적으로 짧습니다")
    return text


def candidate_container(anchor):
    node = anchor
    best = anchor.parent
    for _ in range(7):
        if not node or not getattr(node, "parent", None):
            break
        node = node.parent
        text = normalize(node.get_text(" ", strip=True))
        if 20 <= len(text) <= 900:
            best = node
        if node.name in {"article", "li"}:
            return node
        classes = " ".join(node.get("class", [])) if getattr(node, "get", None) else ""
        if re.search(r"deal|item|card|post|list", classes, re.I) and len(text) <= 1200:
            return node
    return best


def title_from(anchor, container) -> str:
    for selector in ["h1", "h2", "h3", "h4", ".title", "[class*='title']"]:
        elem = container.select_one(selector) if hasattr(container, "select_one") else None
        if elem:
            title = normalize(elem.get_text(" ", strip=True))
            if len(title) >= 3:
                return elem.get_text(" ", strip=True)
    title_attr = (anchor.get("title") or "").strip()
    anchor_text = anchor.get_text(" ", strip=True)
    return title_attr or anchor_text


def infer_labels(container_text: str) -> tuple[str, str, str]:
    categories = ["PC", "가전", "식품", "생활용품", "게임", "의류", "기타"]
    communities = ["FM코리아", "퀘이사존", "아카라이브", "뽐뿌", "개드립", "루리웹", "클리앙"]
    category = next((v for v in categories if v.lower() in container_text.lower()), "")
    community = next((v for v in communities if v.lower() in container_text.lower()), "")

    store = ""
    tokens = re.split(r"\s+", container_text)
    known_stores = ["쿠팡", "네이버", "G마켓", "지마켓", "옥션", "11번가", "알리", "하이마트", "롯데온", "오늘의집", "SSG", "신세계", "홈플러스", "이마트"]
    for known in known_stores:
        if known.lower() in container_text.lower():
            store = known
            break
    return category, store, community


def parse_deals(source_html: str, base_url: str) -> list[Deal]:
    soup = BeautifulSoup(source_html, "html.parser")
    parsed_base = urlparse(base_url)
    deals: dict[str, Deal] = {}

    for anchor in soup.find_all("a", href=True):
        href = anchor.get("href", "").strip()
        absolute = urljoin(base_url, href)
        parsed = urlparse(absolute)
        if parsed.netloc != parsed_base.netloc:
            continue
        if parsed.path in {"", "/", "/index.php"}:
            continue
        if not DETAIL_PATH_RE.search(parsed.path):
            continue

        container = candidate_container(anchor)
        raw_text = container.get_text(" ", strip=True) if container else anchor.get_text(" ", strip=True)
        title = title_from(anchor, container)
        if not title or len(normalize(title)) < 3:
            continue
        if normalize(title) in {"원본글", "구매하기", "더보기", "신고하기"}:
            continue

        clean_url = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
        deal_id = parsed.path.rstrip("/").split("-")[-1]
        if not re.fullmatch(r"[0-9a-fA-F]{4,8}", deal_id):
            deal_id = hashlib.sha1(clean_url.encode()).hexdigest()[:12]
        category, store, community = infer_labels(raw_text)
        deal = Deal(
            id=deal_id,
            title=html.unescape(title).strip(),
            url=clean_url,
            price=parse_price(raw_text),
            category=category,
            store=store,
            community=community,
            rawText=raw_text[:1200],
        )
        previous = deals.get(deal_id)
        if not previous or len(deal.rawText) > len(previous.rawText):
            deals[deal_id] = deal

    if not deals:
        raise RuntimeError("핫딜 카드 링크를 찾지 못했습니다. 사이트 HTML 구조가 바뀌었을 수 있습니다")
    return list(deals.values())


def contains_any(text: str, words: list[str]) -> bool:
    return not words or any(normalize(word) in text for word in words if normalize(word))


def contains_all(text: str, words: list[str]) -> bool:
    return all(normalize(word) in text for word in words if normalize(word))


def matches_rule(deal: Deal, rule: dict[str, Any]) -> bool:
    if not rule.get("enabled", True):
        return False
    text = normalize(f"{deal.title} {deal.rawText}")
    if not contains_any(text, rule.get("keywordsAny", [])):
        return False
    if not contains_all(text, rule.get("keywordsAll", [])):
        return False
    if any(normalize(word) in text for word in rule.get("exclude", []) if normalize(word)):
        return False

    max_price = rule.get("maxPrice")
    if max_price not in (None, "", 0):
        if deal.price is None or deal.price > int(max_price):
            return False

    scopes = [
        ("categories", deal.category),
        ("stores", deal.store),
        ("communities", deal.community),
    ]
    for key, actual in scopes:
        wanted = [normalize(v) for v in rule.get(key, []) if normalize(v)]
        if wanted and normalize(actual) not in wanted:
            return False
    return True


def telegram_send(token: str, chat_id: str, deal: Deal, rule: dict[str, Any]) -> None:
    price = f"{deal.price:,}원" if deal.price else "가격 미상"
    lines = [
        "🔥 <b>핫딜 발견</b>",
        "",
        f"<b>{html.escape(deal.title)}</b>",
        f"💰 {price}",
        f"🎯 조건: {html.escape(str(rule.get('name') or rule.get('id') or ''))}",
    ]
    info = " · ".join(v for v in [deal.category, deal.store, deal.community] if v)
    if info:
        lines.append(f"🏷 {html.escape(info)}")
    lines.extend(["", f'<a href="{html.escape(deal.url)}">핫딜 보러가기</a>'])

    endpoint = f"https://api.telegram.org/bot{token}/sendMessage"
    response = http_requests.post(endpoint, json={
        "chat_id": chat_id,
        "text": "\n".join(lines),
        "parse_mode": "HTML",
        "disable_web_page_preview": False,
    }, timeout=20)
    if response.status_code != 200:
        raise RuntimeError(f"Telegram API 오류: HTTP {response.status_code} {response.text[:300]}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="텔레그램 전송 없이 파싱/매칭만 확인")
    parser.add_argument("--html-file", help="로컬 HTML 파일을 사용해 파서 테스트")
    args = parser.parse_args()

    config = load_json(CONFIG_PATH, {})
    previous = load_json(DATA_PATH, {"updatedAt": None, "sourceStatus": "", "matches": []})
    known_keys = {f"{m.get('ruleId','')}:{m.get('id','')}" for m in previous.get("matches", [])}
    first_run = not previous.get("updatedAt")
    source_url = config.get("sourceUrl", "https://hotdeal.zip/")

    try:
        if args.html_file:
            source_html = Path(args.html_file).read_text(encoding="utf-8")
        else:
            source_html = fetch_html(source_url)
        deals = parse_deals(source_html, source_url)

        now = datetime.now(KST).isoformat(timespec="seconds")
        found: list[dict[str, Any]] = []
        token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
        chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()

        for rule in config.get("rules", []):
            if not rule.get("enabled", True):
                continue
            for deal in deals:
                if not matches_rule(deal, rule):
                    continue
                key = f"{rule.get('id','')}:{deal.id}"
                if key in known_keys:
                    continue

                item = asdict(deal)
                item.pop("rawText", None)
                item.update({
                    "ruleId": rule.get("id", ""),
                    "ruleName": rule.get("name", rule.get("id", "")),
                    "detectedAt": now,
                })
                found.append(item)
                known_keys.add(key)

                should_notify = not args.dry_run and (not first_run or config.get("firstRunNotify", False))
                if should_notify:
                    if not token or not chat_id:
                        raise RuntimeError("TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID Secret이 설정되지 않았습니다")
                    telegram_send(token, chat_id, deal, rule)

        history = found + previous.get("matches", [])
        max_history = int(config.get("maxHistory", 100))
        payload = {
            "updatedAt": now,
            "sourceStatus": f"정상 · {len(deals)}개 글 확인 · 신규 조건일치 {len(found)}건",
            "matches": history[:max_history],
        }
        # 저장소가 5분마다 불필요한 커밋으로 쌓이지 않도록
        # 첫 실행 또는 신규 매치가 있을 때만 대시보드 파일을 갱신합니다.
        if first_run or found:
            save_json(DATA_PATH, payload)
        print(json.dumps({"deals": len(deals), "newMatches": len(found), "firstRun": first_run}, ensure_ascii=False))
        return 0
    except Exception as exc:
        now = datetime.now(KST).isoformat(timespec="seconds")
        payload = {
            "updatedAt": now,
            "sourceStatus": f"오류 · {exc}",
            "matches": previous.get("matches", []),
        }
        save_json(DATA_PATH, payload)
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
