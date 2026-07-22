#!/usr/bin/env python3
"""서울형 키즈카페 공식 목록을 수집해 places.json을 새로 생성합니다.

기존 places.json의 장소는 보존하지 않습니다. 수집 결과가 없거나 수집에
실패하면 places를 빈 배열로 저장하므로 웹에서는 '목록이 없습니다' 상태가
표시됩니다.
"""
from __future__ import annotations

import json
import re
import sys
from datetime import datetime
from pathlib import Path
from urllib.parse import urljoin, urlparse
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup, Tag

ROOT = Path(__file__).resolve().parents[1]
DATA_FILE = ROOT / "data" / "places.json"
OVERRIDES_FILE = ROOT / "data" / "overrides.json"
SOURCE_URL = "https://umppa.seoul.go.kr/icare/user/kidsCafe/BD_selectKidsCafeList.do"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; two-kids-place/3.0; "
        "+https://github.com/hjt7446/hjt7446.github.io)"
    )
}

SEOUL_DISTRICTS = (
    "종로|중구|용산|성동|광진|동대문|중랑|성북|강북|도봉|노원|은평|"
    "서대문|마포|양천|강서|구로|금천|영등포|동작|관악|서초|강남|송파|강동"
)

GENERIC_NAMES = {
    "서울형 키즈카페",
    "서울형키즈카페",
    "서울형 키즈카페 예약",
    "서울형 키즈카페 소개",
    "여기저기 서울형 키즈카페 소개",
    "일반형 키즈카페",
    "서울형키즈카페 소식",
    "서울형키즈카페머니 사용처",
}


def now_iso() -> str:
    return datetime.now(ZoneInfo("Asia/Seoul")).isoformat(timespec="seconds")


def clean(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def slug(value: str) -> str:
    return re.sub(r"[^0-9a-z가-힣]+", "-", value.lower()).strip("-")[:90]


def safe_http_url(href: str) -> str | None:
    if not href:
        return None
    href = href.strip()
    if href.lower().startswith(("javascript:", "#")):
        return None
    url = urljoin(SOURCE_URL, href)
    parsed = urlparse(url)
    return url if parsed.scheme in {"http", "https"} else None


def find_card(heading: Tag) -> Tag | None:
    node: Tag | None = heading
    for _ in range(8):
        if node is None:
            break
        text = clean(node.get_text(" ", strip=True))
        if "이용연령" in text and re.search(r"주\s*소", text) and len(text) < 3000:
            return node
        parent = node.parent
        node = parent if isinstance(parent, Tag) else None
    return None


def extract_detail_url(card: Tag) -> str:
    preferred_labels = {"이용안내", "오시는길", "예약 신청", "예약신청", "상세보기"}
    fallback_url: str | None = None

    for anchor in card.select("a[href]"):
        url = safe_http_url(anchor.get("href", ""))
        if not url:
            continue
        label = clean(anchor.get_text(" ", strip=True))
        if label in preferred_labels:
            return url
        if fallback_url is None:
            fallback_url = url

    return fallback_url or SOURCE_URL


def parse_facility(heading: Tag) -> dict | None:
    name = clean(heading.get_text(" ", strip=True))
    if not name.startswith("서울형 키즈카페"):
        return None
    if name in GENERIC_NAMES or len(name) > 100:
        return None

    card = find_card(heading)
    if card is None:
        return None

    text = clean(card.get_text(" ", strip=True))
    age_match = re.search(
        r"이용연령\s*(?:만\s*)?(\d{1,2})\s*(?:세)?\s*[~～\-]\s*"
        r"(?:만\s*)?(\d{1,2})\s*세",
        text,
    )
    address_match = re.search(
        r"주\s*소\s*(서울특별시\s+.+?)"
        r"(?=\s*전화번호|\s*연락처|\s*이용안내|\s*운영시간|\s*예약|$)",
        text,
    )

    if not age_match or not address_match:
        return None

    age_min, age_max = map(int, age_match.groups())
    if not 0 <= age_min <= age_max <= 18:
        return None

    address = clean(address_match.group(1))
    district_match = re.search(rf"({SEOUL_DISTRICTS})구", address)
    district = district_match.group(0) if district_match else ""

    return {
        "id": "kids-" + slug(name),
        "name": name,
        "region": "서울",
        "district": district,
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
        "scores": {
            "first": 88,
            "baby": 50,
            "solo": 55,
            "parent": 65,
        },
        "verified": False,
        "url": extract_detail_url(card),
    }


def crawl_seoul_kids_cafes() -> list[dict]:
    response = requests.get(SOURCE_URL, headers=HEADERS, timeout=40)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")

    facilities: dict[str, dict] = {}
    for heading in soup.find_all(["h2", "h3", "h4", "h5", "strong"]):
        if not isinstance(heading, Tag):
            continue
        place = parse_facility(heading)
        if place:
            facilities[place["id"]] = place

    return list(facilities.values())


def load_overrides() -> dict:
    if not OVERRIDES_FILE.exists():
        return {}
    try:
        value = json.loads(OVERRIDES_FILE.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (json.JSONDecodeError, OSError) as error:
        print(f"overrides.json 읽기 실패: {error}", file=sys.stderr)
        return {}


def deep_patch(original: dict, patch: dict) -> None:
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(original.get(key), dict):
            original[key].update(value)
        else:
            original[key] = value


def valid_place(place: dict) -> bool:
    for key in ("id", "name", "region", "address", "category"):
        if not isinstance(place.get(key), str) or not place[key].strip():
            return False
    if not isinstance(place.get("ageMin"), int):
        return False
    if not isinstance(place.get("ageMax"), int):
        return False
    if place["ageMin"] > place["ageMax"]:
        return False
    if not isinstance(place.get("features"), dict):
        return False
    if not isinstance(place.get("scores"), dict):
        return False
    if not safe_http_url(place.get("url", "")):
        return False
    return True


def write_places(places: list[dict], source_note: str) -> None:
    DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "updatedAt": now_iso(),
        "sourceNote": source_note,
        "places": places,
    }
    DATA_FILE.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def main() -> int:
    try:
        scraped = crawl_seoul_kids_cafes()
    except Exception as error:
        print(f"크롤링 실패: {error}", file=sys.stderr)
        write_places([], "공식 장소 목록을 불러오지 못했습니다. 현재 표시할 장소가 없습니다.")
        return 0

    overrides = load_overrides()
    for place in scraped:
        patch = overrides.get(place["id"])
        if isinstance(patch, dict):
            deep_patch(place, patch)

    places = [place for place in scraped if valid_place(place)]
    places.sort(key=lambda place: (place.get("region", ""), place.get("district", ""), place.get("name", "")))

    if places:
        note = (
            "공식 사이트에서 이용연령과 주소를 자동 수집했습니다. "
            "수유실·주차 등 편의정보는 방문 전 공식 페이지에서 확인하세요."
        )
    else:
        note = "공식 목록에서 표시할 수 있는 장소를 찾지 못했습니다."

    write_places(places, note)
    print(f"{len(places)}개 장소를 저장했습니다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
