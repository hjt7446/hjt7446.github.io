#!/usr/bin/env python3
"""맘맘 아이와 가볼 만한 곳 전체 랭킹을 무한 스크롤로 수집합니다.

수집 원칙
- 전체 지역 랭킹을 끝까지 스크롤합니다.
- 장소 상세 링크, 순위, 장소명, 지역, 세부 지역, 인기 연령, 카테고리만 저장합니다.
- 사진, 리뷰, 꿀팁 본문은 복제하지 않습니다.
- 기존 places.json 장소는 보존하지 않고 매 실행 결과로 교체합니다.
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from urllib.parse import urljoin
from zoneinfo import ZoneInfo

from playwright.sync_api import Browser, Page, TimeoutError as PlaywrightTimeoutError, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
DATA_FILE = ROOT / "data" / "places.json"
BASE_URL = "https://mom-mom.net"
RANKING_URL = (
    "https://mom-mom.net/travel/places/rankings"
    "?childAges=AFTER_12M_24M%2CAFTER_24M_48M"
)

PLACE_HREF = re.compile(r"^/travel/places/([0-9a-f]{24})(?:[/?#].*)?$")
LOCATION_RE = re.compile(
    r"(서울|경기|인천|대구|경북|부산|울산|경남|대전|세종|충북|충남|광주|전북|전남|강원|제주)"
    r"\s+([가-힣0-9]+(?:시|군|구))"
)
AGE_RE = re.compile(r"(~\s*12개월|12\s*~\s*24개월|24\s*~\s*48개월|48개월\s*이상)")
RANK_RE = re.compile(r"^\s*(\d{1,5})\s+")

CATEGORY_NAMES = (
    "박물관/체험관",
    "공연/전시/축제",
    "키즈풀/공간대여",
    "캠핑/글램핑",
    "워터파크/스파",
    "동식물친구들",
    "스포츠/게임",
    "서점/도서관",
    "복합문화공간",
    "체험/클래스",
    "야외나들이",
    "테마파크",
    "키즈카페",
    "놀이공원",
    "아쿠아리움",
    "카페",
    "식당",
    "숙박",
)

REGION_ORDER = {
    name: index
    for index, name in enumerate(
        [
            "서울", "경기", "인천", "강원", "충북", "충남", "세종", "대전",
            "경북", "대구", "경남", "부산", "울산", "전북", "전남", "광주", "제주",
        ]
    )
}


def now_iso() -> str:
    return datetime.now(ZoneInfo("Asia/Seoul")).isoformat(timespec="seconds")


def clean(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def age_range(text: str) -> tuple[int, int, str]:
    match = AGE_RE.search(text)
    label = clean(match.group(1)) if match else "연령 정보 없음"

    if label.startswith("~"):
        return 0, 1, label
    if label.startswith("12"):
        return 1, 2, label
    if label.startswith("24"):
        return 2, 4, label
    if label.startswith("48"):
        return 4, 12, label
    return 0, 12, label


def extract_categories(text: str) -> list[str]:
    return [name for name in CATEGORY_NAMES if name in text]


def extract_name(record: dict, location_start: int | None) -> str:
    candidates = [
        clean(record.get("heading", "")),
        clean(record.get("imageAlt", "")),
        clean(record.get("ariaLabel", "")),
        clean(record.get("title", "")),
    ]

    blocked = {"", "이미지", "상세보기", "더보기", "지도에서 보기"}
    for candidate in candidates:
        if candidate not in blocked and 2 <= len(candidate) <= 120:
            return candidate

    text = clean(record.get("text", ""))
    text = RANK_RE.sub("", text)
    if location_start is not None:
        text = text[:location_start]

    # 카드 설명이 장소명 뒤에 이어지는 경우가 많아 첫 줄 또는 짧은 앞부분을 우선 사용합니다.
    original_lines = [clean(line) for line in str(record.get("rawText", "")).splitlines() if clean(line)]
    for line in original_lines:
        line = RANK_RE.sub("", line)
        if 2 <= len(line) <= 100 and not AGE_RE.search(line) and not LOCATION_RE.search(line):
            return line

    return clean(text[:100]) or "이름 확인 필요"


def parse_record(record: dict) -> dict | None:
    href = str(record.get("href", ""))
    href_match = PLACE_HREF.match(href)
    if not href_match:
        return None

    text = clean(record.get("text", ""))
    location = LOCATION_RE.search(text)
    if not location:
        return None

    region, district = location.groups()
    rank_match = RANK_RE.search(text)
    rank = int(rank_match.group(1)) if rank_match else 999999
    minimum, maximum, age_label = age_range(text)
    categories = extract_categories(text)
    name = extract_name(record, location.start())

    if name in {"이름 확인 필요", "전체", "더보기"}:
        return None

    place_id = href_match.group(1)
    return {
        "id": f"mommom-{place_id}",
        "name": name,
        "region": region,
        "district": district,
        "address": f"{region} {district}",
        "category": categories[0] if categories else "인기 장소",
        "categories": categories,
        "ageMin": minimum,
        "ageMax": maximum,
        "popularAge": age_label,
        "rank": rank,
        "description": "맘맘 아이와 가볼 만한 곳 인기 랭킹 수록 장소",
        "url": urljoin(BASE_URL, href),
        "source": "맘맘 인기 랭킹",
    }


def collect_visible_places(page: Page) -> list[dict]:
    return page.eval_on_selector_all(
        'a[href^="/travel/places/"]',
        """
        (anchors) => anchors.map((a) => {
          const heading = a.querySelector('h1,h2,h3,h4,h5,h6,strong,[class*=title],[class*=name]');
          const image = a.querySelector('img');
          return {
            href: a.getAttribute('href') || '',
            text: (a.innerText || '').replace(/\\s+/g, ' ').trim(),
            rawText: a.innerText || '',
            heading: heading ? (heading.innerText || '').trim() : '',
            imageAlt: image ? (image.getAttribute('alt') || '').trim() : '',
            ariaLabel: (a.getAttribute('aria-label') || '').trim(),
            title: (a.getAttribute('title') || '').trim(),
          };
        })
        """,
    )


def dismiss_popups(page: Page) -> None:
    labels = ["닫기", "오늘 그만 보기", "확인", "나중에", "취소"]
    for label in labels:
        try:
            locator = page.get_by_text(label, exact=True)
            for index in range(min(locator.count(), 3)):
                if locator.nth(index).is_visible():
                    locator.nth(index).click(timeout=800)
        except Exception:
            pass


def scroll_all(page: Page) -> dict[str, dict]:
    collected: dict[str, dict] = {}
    stable_rounds = 0
    previous_count = 0
    previous_height = 0

    # 안전장치: 최대 600회 스크롤. 일반적인 랭킹 규모에서는 그 전에 종료됩니다.
    for round_number in range(1, 601):
        dismiss_popups(page)

        for record in collect_visible_places(page):
            href = record.get("href", "")
            if PLACE_HREF.match(href):
                collected[href] = record

        current_height = page.evaluate("document.documentElement.scrollHeight")
        current_count = len(collected)

        if current_count == previous_count and current_height == previous_height:
            stable_rounds += 1
        else:
            stable_rounds = 0

        if round_number % 10 == 0:
            print(
                f"scroll={round_number}, places={current_count}, "
                f"height={current_height}, stable={stable_rounds}",
                flush=True,
            )

        # 8회 연속 새 링크와 문서 높이 변화가 없으면 끝으로 판단합니다.
        if stable_rounds >= 8:
            break

        previous_count = current_count
        previous_height = current_height

        page.evaluate("window.scrollTo(0, document.documentElement.scrollHeight)")
        page.mouse.wheel(0, 5000)
        page.wait_for_timeout(1400)

    # 마지막 로딩분까지 한 번 더 수집합니다.
    for record in collect_visible_places(page):
        href = record.get("href", "")
        if PLACE_HREF.match(href):
            collected[href] = record

    return collected


def launch_browser(playwright) -> Browser:
    return playwright.chromium.launch(
        headless=True,
        args=[
            "--disable-dev-shm-usage",
            "--no-sandbox",
            "--disable-blink-features=AutomationControlled",
        ],
    )


def crawl() -> list[dict]:
    with sync_playwright() as playwright:
        browser = launch_browser(playwright)
        context = browser.new_context(
            locale="ko-KR",
            timezone_id="Asia/Seoul",
            viewport={"width": 1440, "height": 1200},
            user_agent=(
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
            ),
        )
        page = context.new_page()
        page.set_default_timeout(15000)

        try:
            page.goto(RANKING_URL, wait_until="domcontentloaded", timeout=60000)

            # 링크가 DOM에 붙기만 하면 충분합니다. 첫 번째 링크가 숨겨져 있어도
            # wait_for_selector의 기본 visible 조건 때문에 실패하지 않도록 합니다.
            place_links = page.locator('a[href^="/travel/places/"]')
            deadline_ms = 30000
            elapsed_ms = 0
            while place_links.count() == 0 and elapsed_ms < deadline_ms:
                page.wait_for_timeout(500)
                elapsed_ms += 500

            if place_links.count() == 0:
                raise RuntimeError("장소 링크가 DOM에 생성되지 않았습니다.")

            print(f"초기 장소 링크 {place_links.count()}개 확인", flush=True)
            page.wait_for_timeout(2500)
            records = scroll_all(page)
        finally:
            browser.close()

    parsed: dict[str, dict] = {}
    for record in records.values():
        place = parse_record(record)
        if place:
            parsed[place["id"]] = place

    places = list(parsed.values())
    places.sort(
        key=lambda item: (
            item.get("rank", 999999),
            REGION_ORDER.get(item.get("region", ""), 999),
            item.get("district", ""),
            item.get("name", ""),
        )
    )
    return places


def write_result(places: list[dict], ok: bool, message: str) -> None:
    DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
    region_counts: dict[str, int] = {}
    for place in places:
        region_counts[place["region"]] = region_counts.get(place["region"], 0) + 1

    payload = {
        "updatedAt": now_iso(),
        "sourceNote": (
            "맘맘 아이와 가볼 만한 곳 전체 인기 랭킹의 공개 카드 정보만 자동 수집합니다. "
            "사진·리뷰·꿀팁 본문은 저장하지 않으며 상세 내용은 원문에서 확인하세요."
        ),
        "sources": [
            {
                "region": "전국",
                "source": "맘맘 인기 랭킹 무한 스크롤",
                "url": RANKING_URL,
                "ok": ok,
                "count": len(places),
                "message": message,
            }
        ],
        "regionCounts": region_counts,
        "places": places,
    }
    DATA_FILE.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def main() -> int:
    try:
        places = crawl()
    except (PlaywrightTimeoutError, Exception) as error:
        print(f"수집 실패: {error}", file=sys.stderr)
        write_result([], False, f"수집 실패: {type(error).__name__}")
        return 1

    if not places:
        print("수집 결과가 없습니다.", file=sys.stderr)
        write_result([], False, "수집 결과 0개")
        return 1

    write_result(places, True, f"전체 랭킹 {len(places)}개 수집")
    counts: dict[str, int] = {}
    for place in places:
        counts[place["region"]] = counts.get(place["region"], 0) + 1
    print(f"총 {len(places)}개 저장: {counts}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
