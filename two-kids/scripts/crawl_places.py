#!/usr/bin/env python3
"""서울형 키즈카페 목록에서 실제 시설 카드만 수집해 places.json에 병합합니다.

시설명, 이용연령, 주소가 한 카드 안에 모두 있는 경우만 저장합니다.
사이트 개편이나 파싱 실패 시 기존 데이터를 유지해 잘못된 빈 데이터 배포를 막습니다.
편의시설 정보는 공식 목록에서 확인되지 않으므로 overrides.json으로 검증·보정합니다.
"""
from __future__ import annotations

import json
import re
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup, Tag

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data" / "places.json"
OVERRIDES = ROOT / "data" / "overrides.json"
SOURCE = "https://umppa.seoul.go.kr/icare/user/kidsCafe/BD_selectKidsCafeList.do"
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; two-kids-place/2.0; +https://github.com/hjt7446/hjt7446.github.io)"}
DISTRICTS = "종로|중구|용산|성동|광진|동대문|중랑|성북|강북|도봉|노원|은평|서대문|마포|양천|강서|구로|금천|영등포|동작|관악|서초|강남|송파|강동"
GENERIC_NAMES = {"서울형 키즈카페", "서울형 키즈카페 예약", "서울형 키즈카페 소개", "일반형 키즈카페", "여기저기 키즈카페"}


def clean(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def slug(value: str) -> str:
    return re.sub(r"[^0-9a-z가-힣]+", "-", value.lower()).strip("-")[:70]


def safe_http_url(href: str) -> str | None:
    if not href or href.lower().startswith(("javascript:", "#")):
        return None
    url = urljoin(SOURCE, href)
    return url if urlparse(url).scheme in {"http", "https"} else None


def find_card(heading: Tag) -> Tag | None:
    """이용연령과 주소를 함께 포함하는 가장 가까운 부모 요소를 찾습니다."""
    node: Tag | None = heading
    for _ in range(7):
        if node is None:
            break
        text = clean(node.get_text(" ", strip=True))
        if "이용연령" in text and re.search(r"주\s*소", text) and len(text) < 2500:
            return node
        parent = node.parent
        node = parent if isinstance(parent, Tag) else None
    return None


def parse_facility(heading: Tag) -> dict | None:
    name = clean(heading.get_text(" ", strip=True))
    if not name.startswith("서울형 키즈카페") or name in GENERIC_NAMES or len(name) > 80:
        return None
    card = find_card(heading)
    if card is None:
        return None
    text = clean(card.get_text(" ", strip=True))
    age_match = re.search(r"이용연령\s*(\d{1,2})\s*~\s*(\d{1,2})세", text)
    address_match = re.search(r"주\s*소\s*(서울특별시\s+.+?)(?=\s*전화번호|\s*이용안내|$)", text)
    if not age_match or not address_match:
        return None
    age_min, age_max = map(int, age_match.groups())
    if not (0 <= age_min <= age_max <= 18):
        return None
    address = clean(address_match.group(1))
    district_match = re.search(fr"({DISTRICTS})구", address)
    if not district_match:
        return None
    region = f"서울 {district_match.group(0)}"
    detail_url = None
    for anchor in card.select("a[href]"):
        label = clean(anchor.get_text(" ", strip=True))
        if label in {"이용안내", "오시는길", "예약 신청"}:
            detail_url = safe_http_url(anchor.get("href", ""))
            if detail_url:
                break
    return {
        "id": "kids-" + slug(name),
        "name": name,
        "region": region,
        "address": address,
        "category": "kids-cafe",
        "ageMin": age_min,
        "ageMax": age_max,
        "stayMinutes": 120,
        "features": {
            "indoor": True,
            "stroller": False,
            "nursingRoom": False,
            "diaperTable": False,
            "parking": False,
            "elevator": False,
            "reservationFree": False,
            "free": True,
        },
        "scores": {"first": 88, "baby": 50, "solo": 55, "parent": 65},
        "verified": False,
        "url": detail_url or SOURCE,
    }


def crawl() -> list[dict]:
    response = requests.get(SOURCE, headers=HEADERS, timeout=30)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")
    facilities: dict[str, dict] = {}
    for heading in soup.find_all(["h3", "h4", "h5", "strong"]):
        parsed = parse_facility(heading)
        if parsed:
            facilities[parsed["id"]] = parsed
    # 현재 첫 페이지에도 여러 실제 시설이 있으므로 3개 미만이면 파싱 실패로 판단합니다.
    return list(facilities.values()) if len(facilities) >= 3 else []


def deep_patch(place: dict, patch: dict) -> None:
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(place.get(key), dict):
            place[key].update(value)
        else:
            place[key] = value


def valid_place(place: dict) -> bool:
    url = safe_http_url(place.get("url", ""))
    return bool(
        place.get("id")
        and place.get("name")
        and place.get("address")
        and isinstance(place.get("ageMin"), int)
        and isinstance(place.get("ageMax"), int)
        and place["ageMin"] <= place["ageMax"]
        and url
    )


def main() -> int:
    current = json.loads(DATA.read_text(encoding="utf-8"))
    try:
        scraped = crawl()
    except Exception as exc:
        print(f"Crawl failed; keeping existing data: {exc}", file=sys.stderr)
        return 0
    if not scraped:
        print("No reliable facility cards found; keeping existing data.", file=sys.stderr)
        return 0
    overrides = json.loads(OVERRIDES.read_text(encoding="utf-8")) if OVERRIDES.exists() else {}
    for place in scraped:
        if place["id"] in overrides:
            deep_patch(place, overrides[place["id"]])
    scraped = [place for place in scraped if valid_place(place)]
    if len(scraped) < 3:
        print("Validation rejected scraped rows; keeping existing data.", file=sys.stderr)
        return 0
    static = [place for place in current["places"] if place.get("category") != "kids-cafe"]
    current["places"] = static + scraped
    current["updatedAt"] = datetime.now(ZoneInfo("Asia/Seoul")).isoformat(timespec="seconds")
    current["sourceNote"] = "공식 이용연령·주소를 자동 수집합니다. 수유실·주차 등 편의정보는 '확인 필요' 표시가 있으면 방문 전 공식 페이지에서 확인하세요."
    DATA.write_text(json.dumps(current, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {len(current['places'])} places ({len(scraped)} validated facilities).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
