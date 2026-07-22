#!/usr/bin/env python3
"""서울·경기·인천 공식 페이지를 수집해 places.json을 새로 생성합니다.

기존 장소 데이터는 읽거나 보존하지 않습니다. 각 실행 결과만 저장합니다.
어느 지역의 수집이 실패해도 다른 지역 결과는 저장하며, 지역별 상태를
sources 배열에 기록합니다.
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from urllib.parse import urljoin, urlparse
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup, Tag

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "data" / "places.json"
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; two-kids-crawler/4.0; +https://github.com/hjt7446/hjt7446.github.io)"}
TIMEOUT = 45

@dataclass(frozen=True)
class Source:
    region: str
    name: str
    url: str
    parser: str

SOURCES = [
    Source("서울", "서울형 키즈카페", "https://umppa.seoul.go.kr/icare/user/kidsCafe/BD_selectKidsCafeList.do", "seoul"),
    Source("경기", "놀이(체험)실·아이사랑놀이터", "https://gyeonggi.childcare.go.kr/lgyeonggi/d10_20000/d10_20061/d10_20062.jsp", "gyeonggi"),
    Source("인천", "아이사랑꿈터", "https://www.incheon.go.kr/earlychild/EC040102", "incheon"),
]

BLOCKED = ("공지", "안내", "소개", "예약 순차", "소식", "이벤트", "사용처", "이용수칙")
REGION_PATTERN = re.compile(r"(서울특별시|서울시|경기도|인천광역시|인천시)\s*")
DISTRICT_PATTERN = re.compile(r"([가-힣]{1,12}(?:시|군|구))")
AGE_RANGE = re.compile(r"(?:만\s*)?(\d{1,2})\s*(?:세)?\s*[~～\-]\s*(?:만\s*)?(\d{1,2})\s*세")
ADDRESS_PATTERNS = [
    re.compile(r"(서울특별시\s+[^\n|]{5,140})"),
    re.compile(r"(경기도\s+[^\n|]{5,140})"),
    re.compile(r"(인천광역시\s+[^\n|]{5,140})"),
]


def clean(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip(" \t\r\n|·")


def now_iso() -> str:
    return datetime.now(ZoneInfo("Asia/Seoul")).isoformat(timespec="seconds")


def absolute_url(base: str, href: str | None) -> str:
    value = urljoin(base, href or "")
    return value if urlparse(value).scheme in {"http", "https"} else base


def make_id(region: str, name: str, address: str) -> str:
    digest = hashlib.sha1(f"{region}|{name}|{address}".encode("utf-8")).hexdigest()[:12]
    return f"{region}-{digest}"


def district_from(region: str, address: str, fallback: str = "") -> str:
    text = REGION_PATTERN.sub("", address)
    matches = DISTRICT_PATTERN.findall(text)
    if not matches:
        return clean(fallback)
    if region == "경기":
        return next((m for m in matches if m.endswith(("시", "군"))), matches[0])
    return next((m for m in matches if m.endswith("구")), matches[0])


def age_from(text: str, default: tuple[int, int]) -> tuple[int, int]:
    match = AGE_RANGE.search(text)
    if not match:
        return default
    low, high = map(int, match.groups())
    return (low, high) if 0 <= low <= high <= 18 else default


def address_from(text: str) -> str:
    for pattern in ADDRESS_PATTERNS:
        match = pattern.search(text)
        if match:
            value = clean(match.group(1))
            value = re.split(r"(?:전화|연락처|운영시간|이용시간|예약|문의)\s*[:：]?", value)[0]
            return clean(value)
    return ""


def place(source: Source, name: str, address: str, url: str, age: tuple[int, int], description: str = "", district: str = "") -> dict:
    name, address = clean(name), clean(address)
    return {
        "id": make_id(source.region, name, address),
        "name": name,
        "region": source.region,
        "district": district_from(source.region, address, district),
        "address": address,
        "category": "public-play-space",
        "ageMin": age[0],
        "ageMax": age[1],
        "description": clean(description)[:220],
        "url": url,
        "source": source.name,
    }


def fetch(source: Source) -> BeautifulSoup:
    response = requests.get(source.url, headers=HEADERS, timeout=TIMEOUT)
    response.raise_for_status()
    response.encoding = response.apparent_encoding or response.encoding
    return BeautifulSoup(response.text, "html.parser")


def parse_seoul(source: Source, soup: BeautifulSoup) -> list[dict]:
    results: dict[str, dict] = {}
    for heading in soup.find_all(["h2", "h3", "h4", "h5", "strong"]):
        if not isinstance(heading, Tag):
            continue
        name = clean(heading.get_text(" ", strip=True))
        if not name.startswith("서울형 키즈카페") or any(word in name for word in BLOCKED) or len(name) > 90:
            continue
        node: Tag | None = heading
        card = None
        for _ in range(9):
            if node is None:
                break
            text = clean(node.get_text(" ", strip=True))
            if "이용연령" in text and "서울특별시" in text and len(text) < 3500:
                card = node
                break
            node = node.parent if isinstance(node.parent, Tag) else None
        if card is None:
            continue
        text = clean(card.get_text(" ", strip=True))
        address = address_from(text)
        if not address:
            continue
        detail = source.url
        for anchor in card.select("a[href]"):
            href = anchor.get("href")
            candidate = absolute_url(source.url, href)
            if "KidsCafeView" in candidate or "fcltyId" in candidate:
                detail = candidate
                break
        item = place(source, name, address, detail, age_from(text, (0, 12)), "서울시 공식 서울형 키즈카페")
        results[item["id"]] = item
    return list(results.values())


def row_cells(row: Tag) -> list[str]:
    return [clean(cell.get_text(" ", strip=True)) for cell in row.find_all(["th", "td"], recursive=False)]


def parse_incheon(source: Source, soup: BeautifulSoup) -> list[dict]:
    results: dict[str, dict] = {}
    for row in soup.select("table tr"):
        cells = row_cells(row)
        joined = " | ".join(cells)
        if "아이사랑꿈터" not in joined:
            continue
        address = address_from(joined)
        if not address:
            continue
        name = next((c for c in cells if "아이사랑꿈터" in c and len(c) < 80), "")
        if not name or any(word in name for word in BLOCKED):
            continue
        link = row.find("a", href=True)
        item = place(source, name, address, absolute_url(source.url, link.get("href") if link else None), age_from(joined, (0, 5)), "인천형 공동육아나눔터")
        results[item["id"]] = item
    if results:
        return list(results.values())
    for text_node in soup.find_all(string=re.compile("아이사랑꿈터")):
        parent = text_node.parent if isinstance(text_node.parent, Tag) else None
        if parent is None:
            continue
        node = parent
        for _ in range(6):
            text = clean(node.get_text(" ", strip=True))
            address = address_from(text)
            if address and len(text) < 1800:
                candidates = [clean(x) for x in re.split(r"[|\n]", text) if "아이사랑꿈터" in x]
                name = min(candidates, key=len) if candidates else ""
                if name and not any(word in name for word in BLOCKED):
                    link = node.find("a", href=True)
                    item = place(source, name, address, absolute_url(source.url, link.get("href") if link else None), age_from(text, (0, 5)), "인천형 공동육아나눔터")
                    results[item["id"]] = item
                break
            node = node.parent if isinstance(node.parent, Tag) else None
            if node is None:
                break
    return list(results.values())


def parse_gyeonggi(source: Source, soup: BeautifulSoup) -> list[dict]:
    results: dict[str, dict] = {}
    keywords = ("아이사랑놀이터", "놀이체험실", "놀이 체험실", "놀이실")
    for row in soup.select("table tr"):
        cells = row_cells(row)
        joined = " | ".join(cells)
        if not any(keyword in joined for keyword in keywords):
            continue
        address = address_from(joined)
        if not address:
            continue
        name = next((c for c in cells if any(k in c for k in keywords) and len(c) < 100), "")
        if not name or any(word in name for word in BLOCKED):
            continue
        link = row.find("a", href=True)
        item = place(source, name, address, absolute_url(source.url, link.get("href") if link else None), age_from(joined, (0, 6)), "경기도 육아종합지원센터 놀이공간")
        results[item["id"]] = item
    if results:
        return list(results.values())
    for node in soup.find_all(["li", "div", "p", "dd"]):
        text = clean(node.get_text(" ", strip=True))
        if not any(keyword in text for keyword in keywords) or len(text) > 1600:
            continue
        address = address_from(text)
        if not address:
            continue
        chunks = [clean(x) for x in re.split(r"[|\n]", text)]
        name = next((c for c in chunks if any(k in c for k in keywords) and 3 < len(c) < 100), "")
        if not name or any(word in name for word in BLOCKED):
            continue
        link = node.find("a", href=True)
        item = place(source, name, address, absolute_url(source.url, link.get("href") if link else None), age_from(text, (0, 6)), "경기도 육아종합지원센터 놀이공간")
        results[item["id"]] = item
    return list(results.values())


PARSERS = {"seoul": parse_seoul, "incheon": parse_incheon, "gyeonggi": parse_gyeonggi}


def valid(item: dict) -> bool:
    return all(isinstance(item.get(k), str) and item[k].strip() for k in ("id", "name", "region", "address", "url")) and isinstance(item.get("ageMin"), int) and isinstance(item.get("ageMax"), int)


def main() -> int:
    all_places: dict[str, dict] = {}
    statuses: list[dict] = []
    for source in SOURCES:
        try:
            soup = fetch(source)
            items = [item for item in PARSERS[source.parser](source, soup) if valid(item)]
            for item in items:
                all_places[item["id"]] = item
            statuses.append({"region": source.region, "source": source.name, "url": source.url, "ok": True, "count": len(items)})
            print(f"{source.region}: {len(items)}개")
        except Exception as error:
            statuses.append({"region": source.region, "source": source.name, "url": source.url, "ok": False, "count": 0, "error": str(error)[:180]})
            print(f"{source.region} 수집 실패: {error}", file=sys.stderr)

    places = sorted(all_places.values(), key=lambda x: ({"인천": 0, "경기": 1, "서울": 2}.get(x["region"], 9), x.get("district", ""), x["name"]))
    payload = {
        "updatedAt": now_iso(),
        "sourceNote": "서울·경기·인천 공식 페이지를 자동 수집한 결과입니다. 기존 데이터는 보존하지 않습니다.",
        "sources": statuses,
        "places": places,
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"총 {len(places)}개 장소 저장")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
