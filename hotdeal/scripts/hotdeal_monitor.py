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
from urllib.parse import urljoin, urlparse, unquote

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

PRICE_RE = re.compile(
    r"(?<!\d)(\d{1,3}(?:,\d{3})+|\d{4,9})\s*원"
)

DETAIL_PATH_RE = re.compile(
    r"-[0-9a-fA-F]{4,8}/?$"
)


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
        json.dump(
            data,
            f,
            ensure_ascii=False,
            indent=2,
        )
        f.write("\n")

    tmp.replace(path)


def normalize(text: str) -> str:
    return re.sub(
        r"\s+",
        " ",
        text or "",
    ).strip().lower()


def parse_price(text: str) -> int | None:
    values = []

    for match in PRICE_RE.findall(text or ""):
        try:
            values.append(
                int(match.replace(",", ""))
            )
        except ValueError:
            pass

    return values[0] if values else None


def fetch_html_http(url: str) -> str:
    headers = {
        "Accept": (
            "text/html,application/xhtml+xml,"
            "application/xml;q=0.9,"
            "image/avif,image/webp,*/*;q=0.8"
        ),
        "Accept-Language": (
            "ko-KR,ko;q=0.9,en-US;q=0.7,en;q=0.6"
        ),
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

    response = http_requests.get(
        url,
        **kwargs,
    )

    if response.status_code != 200:
        raise RuntimeError(
            f"hotdeal.zip HTTP {response.status_code}"
        )

    text = response.text

    if len(text) < 1000:
        raise RuntimeError(
            "응답 HTML이 비정상적으로 짧습니다"
        )

    return text


def looks_like_deal_list(
    source_html: str,
    base_url: str,
) -> bool:

    soup = BeautifulSoup(
        source_html,
        "html.parser",
    )

    base_host = urlparse(base_url).netloc

    for anchor in soup.find_all(
        "a",
        href=True,
    ):
        parsed = urlparse(
            urljoin(
                base_url,
                anchor.get("href", ""),
            )
        )

        if (
            parsed.netloc == base_host
            and DETAIL_PATH_RE.search(parsed.path)
        ):
            return True

    return False


def fetch_html_browser(url: str) -> str:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise RuntimeError(
            "Playwright가 설치되지 않았습니다"
        ) from exc

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=[
                "--disable-blink-features=AutomationControlled"
            ],
        )

        context = browser.new_context(
            locale="ko-KR",
            timezone_id="Asia/Seoul",
            viewport={
                "width": 1365,
                "height": 900,
            },
            user_agent=(
                "Mozilla/5.0 "
                "(X11; Linux x86_64) "
                "AppleWebKit/537.36 "
                "(KHTML, like Gecko) "
                "Chrome/139.0.0.0 "
                "Safari/537.36"
            ),
        )

        page = context.new_page()

        page.goto(
            url,
            wait_until="domcontentloaded",
            timeout=45000,
        )

        try:
            page.wait_for_load_state(
                "networkidle",
                timeout=15000,
            )
        except Exception:
            pass

        try:
            page.wait_for_function(
                """
                () => Array.from(
                    document.querySelectorAll('a[href]')
                ).some(
                    a => /-[0-9a-fA-F]{4,8}\/?(?:[?#].*)?$/.test(a.href)
                )
                """,
                timeout=15000,
            )
        except Exception:
            page.wait_for_timeout(5000)

        content = page.content()

        browser.close()

        return content


def fetch_html(url: str) -> str:
    http_error = None

    try:
        text = fetch_html_http(url)

        if looks_like_deal_list(
            text,
            url,
        ):
            print(
                "INFO: HTTP HTML에서 핫딜 링크 확인"
            )
            return text

        print(
            "INFO: 초기 HTML에 핫딜 링크가 없어 "
            "브라우저 렌더링으로 전환"
        )

    except Exception as exc:
        http_error = exc

        print(
            f"INFO: HTTP 수집 실패({exc}); "
            "브라우저 렌더링으로 전환"
        )

    rendered = fetch_html_browser(url)

    if not looks_like_deal_list(
        rendered,
        url,
    ):
        suffix = (
            f"; HTTP 오류: {http_error}"
            if http_error
            else ""
        )

        raise RuntimeError(
            "브라우저 렌더링 후에도 핫딜 링크를 "
            f"찾지 못했습니다{suffix}"
        )

    print(
        "INFO: 브라우저 렌더링 HTML에서 핫딜 링크 확인"
    )

    return rendered


def candidate_container(anchor):
    """
    해당 링크가 속한 '한 게시물' 범위를 찾는다.

    기존 코드는 최대 7단계까지 올라가면서
    여러 게시물이 들어있는 부모 리스트 전체를
    잡는 경우가 있었다.

    여기서는 가능한 작은 단위만 사용한다.
    """

    node = anchor

    for _ in range(5):
        if not node:
            break

        if node.name in {
            "article",
            "li",
        }:
            return node

        classes = " ".join(
            node.get("class", [])
        ) if getattr(node, "get", None) else ""

        if re.search(
            r"deal|item|card|post",
            classes,
            re.I,
        ):
            text = normalize(
                node.get_text(
                    " ",
                    strip=True,
                )
            )

            # 지나치게 큰 부모는 게시글 카드가 아니라
            # 게시글 목록일 가능성이 크므로 제외
            if len(text) <= 700:
                return node

        node = getattr(
            node,
            "parent",
            None,
        )

    # 안전하게 링크의 바로 위쪽 요소까지만 사용
    parent = getattr(
        anchor,
        "parent",
        None,
    )

    return parent or anchor


def title_from_url(url: str) -> str:
    """
    hotdeal.zip URL slug에서 제목을 복구하는 fallback.

    예:
    /신라면-20봉-1234abcd
    ->
    신라면 20봉
    """

    try:
        path = unquote(
            urlparse(url).path
        ).strip("/")

        if not path:
            return ""

        slug = path.split("/")[-1]

        slug = re.sub(
            r"-[0-9a-fA-F]{4,8}$",
            "",
            slug,
        )

        slug = slug.replace(
            "-",
            " ",
        )

        slug = normalize(slug)

        return slug.strip()

    except Exception:
        return ""


def title_from(
    anchor,
    container,
    absolute_url: str = "",
) -> str:
    """
    제목은 반드시 해당 링크 자체를 우선한다.

    부모 컨테이너의 첫 번째 h2/h3를 먼저 읽으면
    같은 목록 안의 모든 링크 제목이 똑같아지는
    문제가 발생할 수 있다.
    """

    # 1. 링크 title 속성
    title_attr = (
        anchor.get("title")
        or ""
    ).strip()

    if len(
        normalize(title_attr)
    ) >= 3:

        return html.unescape(
            title_attr
        ).strip()

    # 2. 링크 aria-label
    aria_label = (
        anchor.get("aria-label")
        or ""
    ).strip()

    if len(
        normalize(aria_label)
    ) >= 3:

        return html.unescape(
            aria_label
        ).strip()

    # 3. 링크 자체의 텍스트
    anchor_text = anchor.get_text(
        " ",
        strip=True,
    )

    if len(
        normalize(anchor_text)
    ) >= 3:

        ignored = {
            "원본글",
            "구매하기",
            "더보기",
            "신고하기",
        }

        if normalize(anchor_text) not in {
            normalize(v)
            for v in ignored
        }:
            return html.unescape(
                anchor_text
            ).strip()

    # 4. 부모 안에서 이 링크와 가까운 제목 탐색
    if container:
        selectors = [
            "h1",
            "h2",
            "h3",
            "h4",
            ".title",
            "[class*='title']",
        ]

        for selector in selectors:
            elements = container.select(
                selector
            ) if hasattr(
                container,
                "select",
            ) else []

            # 여러 제목이 존재하면
            # 큰 게시글 목록을 잘못 잡았을 가능성이 있으므로
            # 첫 번째 것을 무조건 쓰지 않는다.
            if len(elements) != 1:
                continue

            elem = elements[0]

            candidate = elem.get_text(
                " ",
                strip=True,
            )

            if len(
                normalize(candidate)
            ) >= 3:

                return html.unescape(
                    candidate
                ).strip()

    # 5. 마지막 fallback: URL slug
    url_title = title_from_url(
        absolute_url
    )

    if len(
        normalize(url_title)
    ) >= 3:
        return url_title

    return ""


def infer_labels(
    container_text: str,
) -> tuple[str, str, str]:

    categories = [
        "PC",
        "가전",
        "식품",
        "생활용품",
        "게임",
        "의류",
        "기타",
    ]

    communities = [
        "FM코리아",
        "퀘이사존",
        "아카라이브",
        "뽐뿌",
        "개드립",
        "루리웹",
        "클리앙",
    ]

    normalized = normalize(
        container_text
    )

    category = next(
        (
            value
            for value in categories
            if normalize(value) in normalized
        ),
        "",
    )

    community = next(
        (
            value
            for value in communities
            if normalize(value) in normalized
        ),
        "",
    )

    store = ""

    known_stores = [
        "쿠팡",
        "네이버",
        "G마켓",
        "지마켓",
        "옥션",
        "11번가",
        "알리",
        "하이마트",
        "롯데온",
        "오늘의집",
        "SSG",
        "신세계",
        "홈플러스",
        "이마트",
    ]

    for known in known_stores:
        if normalize(known) in normalized:
            store = known
            break

    return (
        category,
        store,
        community,
    )


def parse_deals(
    source_html: str,
    base_url: str,
) -> list[Deal]:

    soup = BeautifulSoup(
        source_html,
        "html.parser",
    )

    parsed_base = urlparse(
        base_url
    )

    deals: dict[str, Deal] = {}

    for anchor in soup.find_all(
        "a",
        href=True,
    ):
        href = (
            anchor.get("href", "")
            or ""
        ).strip()

        absolute = urljoin(
            base_url,
            href,
        )

        parsed = urlparse(
            absolute
        )

        if parsed.netloc != parsed_base.netloc:
            continue

        if parsed.path in {
            "",
            "/",
            "/index.php",
        }:
            continue

        if not DETAIL_PATH_RE.search(
            parsed.path
        ):
            continue

        clean_url = (
            f"{parsed.scheme}://"
            f"{parsed.netloc}"
            f"{parsed.path}"
        )

        container = candidate_container(
            anchor
        )

        title = title_from(
            anchor,
            container,
            clean_url,
        )

        if not title:
            continue

        if len(
            normalize(title)
        ) < 3:
            continue

        if normalize(title) in {
            "원본글",
            "구매하기",
            "더보기",
            "신고하기",
        }:
            continue

        # rawText는 가격/판매처 등 참고 정보용이다.
        # 키워드 필터에는 절대 사용하지 않는다.
        if container:
            raw_text = container.get_text(
                " ",
                strip=True,
            )
        else:
            raw_text = anchor.get_text(
                " ",
                strip=True,
            )

        # 지나치게 큰 부모 DOM을 잡았을 경우
        # 주변 게시물 내용이 섞이는 것을 최소화한다.
        if len(raw_text) > 700:
            raw_text = anchor.get_text(
                " ",
                strip=True,
            )

        deal_id = (
            parsed.path
            .rstrip("/")
            .split("-")[-1]
        )

        if not re.fullmatch(
            r"[0-9a-fA-F]{4,8}",
            deal_id,
        ):
            deal_id = hashlib.sha1(
                clean_url.encode()
            ).hexdigest()[:12]

        category, store, community = infer_labels(
            raw_text
        )

        deal = Deal(
            id=deal_id,
            title=html.unescape(
                title
            ).strip(),
            url=clean_url,
            price=parse_price(
                raw_text
            ),
            category=category,
            store=store,
            community=community,
            rawText=raw_text[:700],
        )

        previous = deals.get(
            deal_id
        )

        if not previous:
            deals[deal_id] = deal
            continue

        # 같은 게시글 URL이 여러 번 HTML에 나타날 경우
        # 제목 자체가 더 구체적인 항목을 우선한다.
        if len(
            normalize(deal.title)
        ) > len(
            normalize(previous.title)
        ):
            deals[deal_id] = deal

    if not deals:
        raise RuntimeError(
            "핫딜 카드 링크를 찾지 못했습니다. "
            "사이트 HTML 구조가 바뀌었을 수 있습니다"
        )

    return list(
        deals.values()
    )


def contains_any(
    text: str,
    words: list[str],
) -> bool:

    normalized_words = [
        normalize(word)
        for word in words
        if normalize(word)
    ]

    if not normalized_words:
        return True

    return any(
        word in text
        for word in normalized_words
    )


def contains_all(
    text: str,
    words: list[str],
) -> bool:

    normalized_words = [
        normalize(word)
        for word in words
        if normalize(word)
    ]

    return all(
        word in text
        for word in normalized_words
    )


def matches_rule(
    deal: Deal,
    rule: dict[str, Any],
) -> bool:

    if not rule.get(
        "enabled",
        True,
    ):
        return False

    # ★ 핵심 수정 ★
    #
    # 기존:
    # deal.title + deal.rawText
    #
    # rawText에는 주변 게시글의 텍스트가 섞일 수 있으므로
    # 라면 게시물 주변에 있는 카메라/체중계까지
    # 라면 조건으로 매칭되는 문제가 발생했다.
    #
    # 반드시 '해당 게시물 제목'으로만 키워드를 검사한다.
    text = normalize(
        deal.title
    )

    if not contains_any(
        text,
        rule.get(
            "keywordsAny",
            [],
        ),
    ):
        return False

    if not contains_all(
        text,
        rule.get(
            "keywordsAll",
            [],
        ),
    ):
        return False

    exclude_words = [
        normalize(word)
        for word in rule.get(
            "exclude",
            [],
        )
        if normalize(word)
    ]

    if any(
        word in text
        for word in exclude_words
    ):
        return False

    max_price = rule.get(
        "maxPrice"
    )

    if max_price not in (
        None,
        "",
        0,
    ):
        if deal.price is None:
            return False

        if deal.price > int(
            max_price
        ):
            return False

    scopes = [
        (
            "categories",
            deal.category,
        ),
        (
            "stores",
            deal.store,
        ),
        (
            "communities",
            deal.community,
        ),
    ]

    for key, actual in scopes:
        wanted = [
            normalize(value)
            for value in rule.get(
                key,
                [],
            )
            if normalize(value)
        ]

        if (
            wanted
            and normalize(actual)
            not in wanted
        ):
            return False

    return True


def telegram_send(
    token: str,
    chat_id: str,
    deal: Deal,
    rule: dict[str, Any],
) -> None:

    price = (
        f"{deal.price:,}원"
        if deal.price
        else "가격 미상"
    )

    lines = [
        "🔥 <b>핫딜 발견</b>",
        "",
        f"<b>{html.escape(deal.title)}</b>",
        f"💰 {price}",
        (
            "🎯 조건: "
            + html.escape(
                str(
                    rule.get("name")
                    or rule.get("id")
                    or ""
                )
            )
        ),
    ]

    info = " · ".join(
        value
        for value in [
            deal.category,
            deal.store,
            deal.community,
        ]
        if value
    )

    if info:
        lines.append(
            f"🏷 {html.escape(info)}"
        )

    lines.extend(
        [
            "",
            (
                f'<a href="{html.escape(deal.url)}">'
                "핫딜 보러가기"
                "</a>"
            ),
        ]
    )

    endpoint = (
        "https://api.telegram.org/"
        f"bot{token}/sendMessage"
    )

    response = http_requests.post(
        endpoint,
        json={
            "chat_id": chat_id,
            "text": "\n".join(lines),
            "parse_mode": "HTML",
            "disable_web_page_preview": False,
        },
        timeout=20,
    )

    if response.status_code != 200:
        raise RuntimeError(
            "Telegram API 오류: "
            f"HTTP {response.status_code} "
            f"{response.text[:300]}"
        )


def main() -> int:
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--dry-run",
        action="store_true",
        help=(
            "텔레그램 전송 없이 "
            "파싱/매칭만 확인"
        ),
    )

    parser.add_argument(
        "--html-file",
        help=(
            "로컬 HTML 파일을 사용해 "
            "파서 테스트"
        ),
    )

    args = parser.parse_args()

    config = load_json(
        CONFIG_PATH,
        {},
    )

    previous = load_json(
        DATA_PATH,
        {
            "updatedAt": None,
            "sourceStatus": "",
            "matches": [],
        },
    )

    known_keys = {
        f"{match.get('ruleId', '')}:"
        f"{match.get('id', '')}"
        for match
        in previous.get(
            "matches",
            [],
        )
    }

    first_run = not previous.get(
        "updatedAt"
    )

    source_url = config.get(
        "sourceUrl",
        "https://hotdeal.zip/",
    )

    try:
        if args.html_file:
            source_html = Path(
                args.html_file
            ).read_text(
                encoding="utf-8"
            )
        else:
            source_html = fetch_html(
                source_url
            )

        deals = parse_deals(
            source_html,
            source_url,
        )

        now = datetime.now(
            KST
        ).isoformat(
            timespec="seconds"
        )

        found: list[
            dict[str, Any]
        ] = []

        token = os.getenv(
            "TELEGRAM_BOT_TOKEN",
            "",
        ).strip()

        chat_id = os.getenv(
            "TELEGRAM_CHAT_ID",
            "",
        ).strip()

        for rule in config.get(
            "rules",
            [],
        ):
            if not rule.get(
                "enabled",
                True,
            ):
                continue

            for deal in deals:
                if not matches_rule(
                    deal,
                    rule,
                ):
                    continue

                key = (
                    f"{rule.get('id', '')}:"
                    f"{deal.id}"
                )

                if key in known_keys:
                    continue

                item = asdict(
                    deal
                )

                item.pop(
                    "rawText",
                    None,
                )

                item.update(
                    {
                        "ruleId": rule.get(
                            "id",
                            "",
                        ),
                        "ruleName": rule.get(
                            "name",
                            rule.get(
                                "id",
                                "",
                            ),
                        ),
                        "detectedAt": now,
                    }
                )

                found.append(
                    item
                )

                known_keys.add(
                    key
                )

                should_notify = (
                    not args.dry_run
                    and (
                        not first_run
                        or config.get(
                            "firstRunNotify",
                            False,
                        )
                    )
                )

                if should_notify:
                    if (
                        not token
                        or not chat_id
                    ):
                        raise RuntimeError(
                            "TELEGRAM_BOT_TOKEN / "
                            "TELEGRAM_CHAT_ID Secret이 "
                            "설정되지 않았습니다"
                        )

                    telegram_send(
                        token,
                        chat_id,
                        deal,
                        rule,
                    )

        history = (
            found
            + previous.get(
                "matches",
                [],
            )
        )

        max_history = int(
            config.get(
                "maxHistory",
                100,
            )
        )

        payload = {
            "updatedAt": now,
            "sourceStatus": (
                f"정상 · {len(deals)}개 글 확인 · "
                f"신규 조건일치 {len(found)}건"
            ),
            "matches": history[
                :max_history
            ],
        }

        # 첫 실행 또는 신규 매치가 있을 때만 저장
        if first_run or found:
            save_json(
                DATA_PATH,
                payload,
            )

        print(
            json.dumps(
                {
                    "deals": len(deals),
                    "newMatches": len(found),
                    "firstRun": first_run,
                },
                ensure_ascii=False,
            )
        )

        return 0

    except Exception as exc:
        now = datetime.now(
            KST
        ).isoformat(
            timespec="seconds"
        )

        payload = {
            "updatedAt": now,
            "sourceStatus": (
                f"오류 · {exc}"
            ),
            "matches": previous.get(
                "matches",
                [],
            ),
        }

        save_json(
            DATA_PATH,
            payload,
        )

        print(
            f"ERROR: {exc}",
            file=sys.stderr,
        )

        return 1


if __name__ == "__main__":
    raise SystemExit(
        main()
    )
