#!/usr/bin/env python3
"""가족용 공연 데이터 동기화.

공식 KOPIS API를 기준 데이터로 사용한다.

관람 가능 최소 연령이 설정된 최대 연령 이하인 공연만 저장한다.
기본 설정은 최소 관람 가능 연령 8세 이하이다.

실패 시 기존 정상 데이터 파일을 덮어쓰지 않는다.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"

P_PATH = DATA / "performances.json"
V_PATH = DATA / "venues.json"
HISTORY_PATH = DATA / "change-history.json"
ALERTS_PATH = DATA / "alerts.json"
META_PATH = DATA / "sync-meta.json"
CONFIG_PATH = ROOT / "config.json"

BASE = "https://www.kopis.or.kr/openApi/restful"
KEY = os.environ.get("KOPIS_API_KEY", "").strip()

DAYS_BACK = int(os.environ.get("SYNC_DAYS_BACK", "14"))
DAYS_FORWARD = int(os.environ.get("SYNC_DAYS_FORWARD", "90"))
PAGE_SIZE = min(int(os.environ.get("SYNC_PAGE_SIZE", "100")), 100)
MAX_PAGES = int(os.environ.get("SYNC_MAX_PAGES", "20"))
DELAY = float(os.environ.get("SYNC_REQUEST_DELAY", "0.05"))
TIMEOUT = int(os.environ.get("SYNC_TIMEOUT", "15"))
MIN_SUCCESS_RATIO = float(os.environ.get("MIN_SUCCESS_RATIO", "0.80"))

USER_AGENT = "family-performance-finder/2.0 (+https://hjt7446.github.io/)"
KST = timezone(timedelta(hours=9))

COMPARE_FIELDS = [
    "title",
    "venueId",
    "startDate",
    "endDate",
    "genre",
    "runtime",
    "age",
    "price",
    "cast",
    "poster",
    "bookingUrls",
    "sourceStatus",
]

CAPITAL_REGIONS = {
    "서울": "11",
    "인천": "28",
    "경기": "41",
}

TARGET_REGION_NAMES = set(CAPITAL_REGIONS)


def now_iso() -> str:
    return datetime.now(KST).isoformat(timespec="seconds")


def load(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def write_atomic(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)

    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    tmp.replace(path)


def request_xml(
    path: str,
    params: dict[str, Any] | None = None,
) -> ET.Element:
    query = {
        "service": KEY,
        **{
            key: str(value)
            for key, value in (params or {}).items()
            if value not in (None, "")
        },
    }

    url = f"{BASE}/{path}?{urllib.parse.urlencode(query)}"

    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/xml",
        },
    )

    last_error: Exception | None = None

    for attempt in range(4):
        try:
            with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
                payload = response.read()

            root = ET.fromstring(payload)

            return_code = root.findtext(".//returncode")

            if return_code not in (None, "00"):
                message = root.findtext(".//errmsg") or "API error"
                raise RuntimeError(message)

            return root

        except Exception as exc:
            last_error = exc
            time.sleep(1.4 * (attempt + 1))

    raise RuntimeError(f"KOPIS request failed {path}: {last_error}")


def ymd(value: str) -> str | None:
    normalized = (
        (value or "")
        .strip()
        .replace(".", "-")
        .replace("/", "-")
    )

    for fmt in ("%Y-%m-%d", "%Y%m%d"):
        try:
            return datetime.strptime(normalized, fmt).date().isoformat()
        except ValueError:
            pass

    return None


def number(value: str) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def stable(prefix: str, raw: str) -> str:
    digest = hashlib.sha1(raw.encode()).hexdigest()[:14]
    return f"{prefix}-{digest}"


def split_region(address: str) -> tuple[str, str]:
    parts = (address or "").split()

    city = parts[0] if parts else ""
    region = parts[1] if len(parts) > 1 else ""

    return city, region


def text_only(value: str) -> str:
    return (
        re.sub(r"<[^>]+>", " ", value or "")
        .replace("&lt;", "<")
        .replace("&gt;", ">")
        .replace("&amp;", "&")
        .strip()
    )


def extract_urls(detail: dict[str, str]) -> list[dict[str, str]]:
    raw = detail.get("relates", "")
    urls: list[dict[str, str]] = []

    try:
        node = ET.fromstring(f"<root>{raw}</root>")

        for relate in node.findall(".//relate"):
            url = (relate.findtext("relateurl") or "").strip()
            name = (
                relate.findtext("relatenm")
                or "예매/공식 페이지"
            ).strip()

            if url.startswith("http"):
                urls.append(
                    {
                        "name": name,
                        "url": url,
                        "source": "KOPIS",
                    }
                )

    except ET.ParseError:
        for url in re.findall(r'https?://[^\s<"]+', raw):
            urls.append(
                {
                    "name": "예매/공식 페이지",
                    "url": url,
                    "source": "KOPIS",
                }
            )

    seen: set[str] = set()
    unique: list[dict[str, str]] = []

    for item in urls:
        if item["url"] in seen:
            continue

        seen.add(item["url"])
        unique.append(item)

    return unique


def parse_age(raw: str) -> dict[str, Any]:
    value = (raw or "").strip()
    normalized = re.sub(r"\s+", "", value).lower()

    if any(
        text in normalized
        for text in [
            "전체관람가",
            "전연령",
            "모든연령",
            "연령제한없음",
        ]
    ):
        return {
            "label": value or "전체 관람가",
            "minAge": 0,
            "unknown": False,
        }

    month_matches = [
        int(item)
        for item in re.findall(r"(\d+)\s*개월", value)
    ]

    if month_matches:
        minimum_months = min(month_matches)

        return {
            "label": value,
            "minAge": minimum_months // 12,
            "minMonths": minimum_months,
            "unknown": False,
        }

    age_matches = [
        int(item)
        for item in re.findall(r"(\d+)\s*세", value)
    ]

    if age_matches:
        return {
            "label": value,
            "minAge": min(age_matches),
            "unknown": False,
        }

    grade_age_map = {
        "초등학생이상": 7,
        "초등학교이상": 7,
        "중학생이상": 13,
        "중학교이상": 13,
        "고등학생이상": 16,
        "고등학교이상": 16,
        "대학생이상": 19,
    }

    for keyword, minimum_age in grade_age_map.items():
        if keyword in normalized:
            return {
                "label": value,
                "minAge": minimum_age,
                "unknown": False,
            }

    if any(
        text in normalized
        for text in [
            "미취학",
            "유아",
        ]
    ):
        return {
            "label": value,
            "minAge": 3,
            "unknown": False,
        }

    return {
        "label": value or "연령 정보 확인 필요",
        "minAge": None,
        "unknown": True,
    }


def is_target_age(
    raw_age: str,
    age_info: dict[str, Any],
    max_audience_age: int,
    exclude_unknown: bool,
) -> bool:
    """최소 관람 가능 연령이 설정값 이하인 공연만 허용한다."""

    normalized = re.sub(r"\s+", "", raw_age or "").lower()

    adult_only_keywords = [
        "성인",
        "성인전용",
        "청소년관람불가",
        "청소년관람금지",
        "19세이상",
        "만19세이상",
        "18세이상",
        "만18세이상",
        "고등학생이상",
        "고등학교이상",
        "대학생이상",
    ]

    if any(
        keyword in normalized
        for keyword in adult_only_keywords
    ):
        return False

    if age_info.get("unknown", True):
        return not exclude_unknown

    minimum_age = age_info.get("minAge")

    if minimum_age is None:
        return not exclude_unknown

    try:
        return int(minimum_age) <= int(max_audience_age)
    except (TypeError, ValueError):
        return False


def family_tags(
    title: str,
    genre: str,
    age: dict[str, Any],
    description: str,
) -> list[str]:
    haystack = f"{title} {genre} {description}".lower()
    tags: list[str] = []

    if any(
        keyword in haystack
        for keyword in [
            "아동",
            "어린이",
            "키즈",
            "가족",
            "패밀리",
            "뮤지컬",
        ]
    ):
        tags.append("가족추천후보")

    if "뮤지컬" in haystack:
        tags.append("뮤지컬")

    minimum_age = age.get("minAge")

    if minimum_age is not None and minimum_age <= 8:
        tags.append("어린이관람가능")

    if age.get("unknown"):
        tags.append("연령확인필요")

    return tags


def meaningful_changes(
    old: dict[str, Any],
    new: dict[str, Any],
) -> list[dict[str, Any]]:
    changes: list[dict[str, Any]] = []

    for field in COMPARE_FIELDS:
        if old.get(field) != new.get(field):
            changes.append(
                {
                    "field": field,
                    "before": old.get(field),
                    "after": new.get(field),
                }
            )

    return changes


def confidence(
    performance: dict[str, Any],
    venue: dict[str, Any],
) -> dict[str, Any]:
    checks = {
        "officialSource": performance.get("source") == "KOPIS",
        "date": bool(
            performance.get("startDate")
            and performance.get("endDate")
        ),
        "venue": bool(
            venue.get("name")
            and venue.get("address")
        ),
        "age": not performance.get(
            "ageInfo",
            {},
        ).get("unknown", True),
        "booking": bool(performance.get("bookingUrls")),
    }

    score = 55 + sum(
        [
            15 if checks["date"] else 0,
            10 if checks["venue"] else 0,
            10 if checks["age"] else 0,
            10 if checks["booking"] else 0,
        ]
    )

    score = min(score, 100)

    level = (
        "높음"
        if score >= 85
        else "보통"
        if score >= 70
        else "확인 필요"
    )

    return {
        "score": score,
        "level": level,
        "checks": checks,
    }


def fetch_list() -> list[dict[str, str]]:
    start = date.today() - timedelta(days=DAYS_BACK)
    end = date.today() + timedelta(days=DAYS_FORWARD)

    rows: list[dict[str, str]] = []
    seen: set[str] = set()

    print(
        f"[sync] target=서울/인천/경기 range={start}..{end}",
        flush=True,
    )

    for region_name, region_code in CAPITAL_REGIONS.items():
        for page in range(1, MAX_PAGES + 1):
            params = {
                "stdate": start.strftime("%Y%m%d"),
                "eddate": end.strftime("%Y%m%d"),
                "cpage": page,
                "rows": PAGE_SIZE,
                "signgucode": region_code,
            }

            items = request_xml(
                "pblprfr",
                params,
            ).findall(".//db")

            if not items:
                break

            added = 0

            for item in items:
                row = {
                    child.tag: (child.text or "").strip()
                    for child in item
                }

                performance_id = row.get("mt20id")

                if (
                    performance_id
                    and performance_id not in seen
                ):
                    seen.add(performance_id)
                    rows.append(row)
                    added += 1

            print(
                f"[sync] {region_name} page {page}: "
                f"received={len(items)} "
                f"added={added} "
                f"total={len(rows)}",
                flush=True,
            )

            if len(items) < PAGE_SIZE:
                break

            time.sleep(DELAY)

    return rows


def fetch_detail(performance_id: str) -> dict[str, str]:
    item = request_xml(
        f"pblprfr/{urllib.parse.quote(performance_id)}"
    ).find(".//db")

    if item is None:
        return {}

    return {
        child.tag: (child.text or "").strip()
        for child in item
    }


def fetch_venue(venue_id: str) -> dict[str, str]:
    item = request_xml(
        f"prfplc/{urllib.parse.quote(venue_id)}"
    ).find(".//db")

    if item is None:
        return {}

    return {
        child.tag: (child.text or "").strip()
        for child in item
    }


def main() -> None:
    if not KEY:
        raise SystemExit("KOPIS_API_KEY is required")

    old_list = load(P_PATH, [])
    old = {
        item.get("id"): item
        for item in old_list
    }

    old_venues = {
        item.get("id"): item
        for item in load(V_PATH, [])
    }

    history = load(HISTORY_PATH, [])
    config = load(CONFIG_PATH, {})

    max_audience_age = int(
        config.get("maxAudienceAge", 8)
    )

    exclude_unknown_age = bool(
        config.get("excludeUnknownAge", True)
    )

    rows = fetch_list()

    if not rows:
        raise SystemExit(
            "No list data; keeping previous dataset"
        )

    stamp = now_iso()

    venues = dict(old_venues)
    venue_raw_cache: dict[str, dict[str, str]] = {}

    results: list[dict[str, Any]] = []
    changes: list[dict[str, Any]] = []

    failures = 0
    detail_successes = 0
    skipped_by_age = 0

    for index, brief in enumerate(rows, 1):
        kopis_id = brief.get("mt20id")

        if not kopis_id:
            continue

        try:
            detail = fetch_detail(kopis_id)
            detail_successes += 1

        except Exception as exc:
            print(
                f"[sync] detail failed {kopis_id}: {exc}",
                file=sys.stderr,
                flush=True,
            )
            failures += 1
            continue

        start = ymd(
            detail.get("prfpdfrom")
            or brief.get("prfpdfrom", "")
        )

        end = ymd(
            detail.get("prfpdto")
            or brief.get("prfpdto", "")
        )

        if not start or not end:
            failures += 1
            continue

        title = (
            detail.get("prfnm")
            or brief.get("prfnm")
            or "제목 없음"
        )

        raw_age = detail.get("prfage", "")
        age = parse_age(raw_age)

        if not is_target_age(
            raw_age=raw_age,
            age_info=age,
            max_audience_age=max_audience_age,
            exclude_unknown=exclude_unknown_age,
        ):
            skipped_by_age += 1

            print(
                f"[sync] age skipped: "
                f"{title} / "
                f"{raw_age or '연령 정보 없음'}",
                flush=True,
            )

            time.sleep(DELAY)
            continue

        kopis_venue_id = detail.get("mt10id", "")

        venue_name = (
            detail.get("fcltynm")
            or brief.get("fcltynm")
            or "공연장 미정"
        )

        venue_id = (
            f"kopis-{kopis_venue_id}"
            if kopis_venue_id
            else stable("venue", venue_name)
        )

        venue_raw: dict[str, str] = {}

        if kopis_venue_id:
            try:
                if kopis_venue_id not in venue_raw_cache:
                    venue_raw_cache[kopis_venue_id] = (
                        fetch_venue(kopis_venue_id)
                    )

                venue_raw = venue_raw_cache[kopis_venue_id]

            except Exception as exc:
                print(
                    f"[sync] venue failed "
                    f"{kopis_venue_id}: {exc}",
                    file=sys.stderr,
                    flush=True,
                )

        previous_venue = venues.get(venue_id, {})

        address = (
            venue_raw.get("adres")
            or previous_venue.get("address", "")
        )

        city, region = split_region(address)

        latitude = number(venue_raw.get("la", ""))
        longitude = number(venue_raw.get("lo", ""))

        venues[venue_id] = {
            "id": venue_id,
            "name": (
                venue_raw.get("fcltynm")
                or venue_name
            ),
            "address": address,
            "city": (
                city
                or previous_venue.get("city", "")
            ),
            "region": (
                region
                or previous_venue.get("region", "")
            ),
            "latitude": (
                latitude
                if latitude is not None
                else previous_venue.get("latitude")
            ),
            "longitude": (
                longitude
                if longitude is not None
                else previous_venue.get("longitude")
            ),
            "homepage": (
                venue_raw.get("relateurl")
                or previous_venue.get("homepage", "")
            ),
            "parking": (
                venue_raw.get("parkinglot")
                or previous_venue.get("parking", "")
            ),
            "phone": (
                venue_raw.get("telno")
                or previous_venue.get("phone", "")
            ),
            "source": "KOPIS",
            "sourceId": kopis_venue_id or None,
            "lastCheckedAt": stamp,
        }

        performance_id = f"kopis-{kopis_id}"
        previous = old.get(performance_id, {})

        description = (
            text_only(detail.get("sty", ""))
            or f"{title} 공연 정보"
        )

        status = (
            detail.get("prfstate")
            or brief.get("prfstate", "")
        )

        booking_urls = extract_urls(detail)

        performance: dict[str, Any] = {
            "id": performance_id,
            "source": "KOPIS",
            "sourceId": kopis_id,
            "sourceUrl": (
                "https://www.kopis.or.kr/por/db/pblprfr/"
                "pblprfrView.do?menuId=MNU_00020&mt20Id="
                f"{urllib.parse.quote(kopis_id)}"
            ),
            "title": title,
            "venueId": venue_id,
            "startDate": start,
            "endDate": end,
            "ticketOpenDate": previous.get(
                "ticketOpenDate"
            ),
            "ticketSaleStatus": (
                "SOLD_OUT"
                if "매진" in status
                else "ENDED"
                if "공연완료" in status
                else "ON_SALE"
            ),
            "genre": (
                detail.get("genrenm")
                or brief.get("genrenm", "")
            ),
            "runtime": detail.get("prfruntime", ""),
            "age": raw_age,
            "ageInfo": age,
            "price": detail.get("pcseguidance", ""),
            "cast": detail.get("prfcast", ""),
            "crew": detail.get("prfcrew", ""),
            "poster": (
                detail.get("poster")
                or brief.get("poster", "")
            ),
            "bookingUrls": booking_urls,
            "description": description,
            "familyTags": family_tags(
                title,
                detail.get("genrenm", ""),
                age,
                description,
            ),
            "sourceStatus": status,
            "createdAt": (
                previous.get("createdAt")
                or stamp
            ),
            "firstSeenAt": (
                previous.get("firstSeenAt")
                or stamp
            ),
            "lastCheckedAt": stamp,
        }

        diff = (
            meaningful_changes(previous, performance)
            if previous
            else []
        )

        performance["updatedAt"] = (
            stamp
            if diff
            else previous.get("updatedAt")
            or stamp
        )

        performance["changeSummary"] = [
            item["field"]
            for item in diff
        ]

        performance["confidence"] = confidence(
            performance,
            venues[venue_id],
        )

        results.append(performance)

        if previous and diff:
            event = {
                "performanceId": performance_id,
                "title": title,
                "detectedAt": stamp,
                "changes": diff,
            }

            changes.append(event)
            history.append(event)

        if index % 20 == 0 or index == len(rows):
            print(
                f"[sync] details {index}/{len(rows)} "
                f"kept={len(results)} "
                f"age_skipped={skipped_by_age} "
                f"failures={failures}",
                flush=True,
            )

        time.sleep(DELAY)

    ratio = detail_successes / max(len(rows), 1)

    if ratio < MIN_SUCCESS_RATIO:
        raise SystemExit(
            f"Success ratio {ratio:.1%} below threshold; "
            "previous data preserved"
        )

    result_ids = {
        item["id"]
        for item in results
    }

    for previous in old_list:
        previous_venue = old_venues.get(
            previous.get("venueId"),
            {},
        )

        old_region = (
            (previous_venue.get("city") or "")
            .replace("특별시", "")
            .replace("광역시", "")
            .replace("도", "")
        )

        old_age_raw = previous.get("age", "")
        old_age_info = (
            previous.get("ageInfo")
            or parse_age(old_age_raw)
        )

        old_age_allowed = is_target_age(
            raw_age=old_age_raw,
            age_info=old_age_info,
            max_audience_age=max_audience_age,
            exclude_unknown=exclude_unknown_age,
        )

        should_keep = (
            previous.get("id") not in result_ids
            and previous.get("endDate", "")
            >= date.today().isoformat()
            and old_region in TARGET_REGION_NAMES
            and old_age_allowed
        )

        if should_keep:
            preserved = dict(previous)
            preserved["freshness"] = "이번 수집에서 미확인"
            preserved["confidence"] = {
                **preserved.get("confidence", {}),
                "level": "확인 필요",
            }

            results.append(preserved)

    results.sort(
        key=lambda item: (
            item.get("startDate", ""),
            item.get("title", ""),
        )
    )

    watch_keywords = [
        str(item).lower()
        for item in config.get("watchKeywords", [])
        if str(item).strip()
    ]

    alerts: list[dict[str, Any]] = []

    for performance in results:
        title = performance.get("title", "")

        matched = [
            keyword
            for keyword in watch_keywords
            if keyword in title.lower()
        ]

        if matched:
            alerts.append(
                {
                    "type": "WATCH_MATCH",
                    "performanceId": performance.get(
                        "id",
                        "",
                    ),
                    "title": title or "제목 없음",
                    "keywords": matched,
                    "date": performance.get(
                        "startDate",
                        "",
                    ),
                    "detectedAt": (
                        performance.get("firstSeenAt")
                        or performance.get("createdAt")
                        or performance.get("updatedAt")
                        or stamp
                    ),
                    "message": (
                        f"관심 작품 발견: "
                        f"{title or '제목 없음'}"
                    ),
                }
            )

    for change in changes[-200:]:
        alerts.append(
            {
                "type": "CHANGED",
                "performanceId": change["performanceId"],
                "title": change["title"],
                "detectedAt": change["detectedAt"],
                "message": "공연 정보가 변경되었습니다.",
                "changes": [
                    item["field"]
                    for item in change["changes"]
                ],
            }
        )

    used_venue_ids = {
        item.get("venueId")
        for item in results
    }

    kept_venues = [
        venue
        for venue_id, venue in venues.items()
        if venue_id in used_venue_ids
    ]

    write_atomic(P_PATH, results)

    write_atomic(
        V_PATH,
        sorted(
            kept_venues,
            key=lambda item: (
                item.get("city", ""),
                item.get("name", ""),
            ),
        ),
    )

    write_atomic(
        HISTORY_PATH,
        history[-1000:],
    )

    write_atomic(
        ALERTS_PATH,
        sorted(
            alerts,
            key=lambda item: item.get(
                "detectedAt",
                "",
            ),
            reverse=True,
        )[:300],
    )

    write_atomic(
        META_PATH,
        {
            "updatedAt": stamp,
            "status": "ok",
            "source": "KOPIS official API",
            "performanceCount": len(results),
            "venueCount": len(kept_venues),
            "changedCount": len(changes),
            "failedCount": failures,
            "ageSkippedCount": skipped_by_age,
            "maxAudienceAge": max_audience_age,
            "excludeUnknownAge": exclude_unknown_age,
            "successRatio": round(ratio, 4),
            "range": {
                "daysBack": DAYS_BACK,
                "daysForward": DAYS_FORWARD,
            },
            "regions": list(CAPITAL_REGIONS.keys()),
            "dataNotes": [
                (
                    f"최소 관람 가능 연령이 "
                    f"{max_audience_age}세 이하인 공연만 수록"
                ),
                "관람 연령 정보가 없는 공연은 제외",
                "성인 및 청소년 관람불가 공연은 제외",
                (
                    "예매 오픈일은 공식 데이터에 없으면 "
                    "표시하지 않음"
                ),
            ],
        },
    )

    print(
        f"[sync] done "
        f"performances={len(results)} "
        f"venues={len(kept_venues)} "
        f"age_skipped={skipped_by_age} "
        f"changes={len(changes)} "
        f"failures={failures}",
        flush=True,
    )


if __name__ == "__main__":
    main()
