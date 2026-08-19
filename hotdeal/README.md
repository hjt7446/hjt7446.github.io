# 내 핫딜 알리미

`hotdeal.zip`의 최신 글을 GitHub Actions가 주기적으로 확인하고, `config.json` 조건에 맞는 새 글을 Telegram으로 보내는 개인용 모니터입니다.

## 1. 파일 배치

이 폴더를 저장소 루트의 `hotdeal/`에 넣고, 함께 제공되는 `.github/workflows/hotdeal-monitor.yml`을 저장소의 동일 경로에 넣습니다.

배포 후 대시보드 주소는 일반적으로 다음과 같습니다.

- `https://hjt7446.github.io/hotdeal/`

## 2. Telegram 봇 만들기

1. Telegram에서 `@BotFather`를 엽니다.
2. `/newbot`으로 봇을 생성하고 Bot Token을 복사합니다.
3. 만든 봇과 개인 채팅을 열고 `/start`를 한 번 보냅니다.
4. 브라우저에서 `https://api.telegram.org/bot<토큰>/getUpdates`를 열어 `message.chat.id` 값을 확인합니다.

## 3. GitHub Secrets 등록

저장소에서 `Settings → Secrets and variables → Actions → New repository secret`으로 다음 2개를 등록합니다.

- `TELEGRAM_BOT_TOKEN`: BotFather에서 받은 토큰
- `TELEGRAM_CHAT_ID`: 위에서 확인한 숫자 Chat ID

토큰은 절대로 `config.json`이나 JS 파일에 넣지 마세요. 이 저장소는 Public이므로 즉시 노출됩니다.

## 4. 감시 조건 수정

`hotdeal/config.json`의 `rules` 배열을 편집합니다.

```json
{
  "id": "monitor-1",
  "name": "RTX 5070 Ti",
  "enabled": true,
  "keywordsAny": ["5070 ti", "5070ti"],
  "keywordsAll": [],
  "exclude": ["중고", "리퍼", "채굴"],
  "maxPrice": 1100000,
  "categories": [],
  "stores": [],
  "communities": []
}
```

### 조건 의미

- `keywordsAny`: 이 중 하나 이상 포함. 빈 배열이면 제한 없음.
- `keywordsAll`: 적어둔 단어가 전부 포함되어야 함.
- `exclude`: 하나라도 포함되면 제외.
- `maxPrice`: 이 가격 이하만. `null` 또는 `0`이면 가격 제한 없음.
- `categories`: 예: `PC`, `가전`. 빈 배열이면 전체.
- `stores`: 예: `쿠팡`, `네이버`, `G마켓`. 빈 배열이면 전체.
- `communities`: 예: `FM코리아`, `퀘이사존`, `뽐뿌`. 빈 배열이면 전체.

가격 제한을 쓰면 게시글 카드에서 가격을 읽지 못한 글은 안전하게 제외합니다.

## 5. 첫 실행

GitHub의 `Actions → Hotdeal Monitor → Run workflow`를 눌러 수동 실행합니다.

기본값 `firstRunNotify: false`이므로 첫 실행에서 이미 올라와 있던 조건 일치 글은 기록만 하고 Telegram을 보내지 않습니다. 이후 새로 등장한 조건 일치 글부터 알림을 보냅니다.

처음부터 현재 글도 Telegram으로 받고 싶으면 `firstRunNotify`를 `true`로 바꾸세요.

## 6. 동작 주기

Workflow는 `*/5 * * * *`로 설정되어 있어 5분마다 실행됩니다. GitHub Actions의 예약 실행은 정확히 5분 정각을 보장하지 않으며 혼잡할 때 지연될 수 있습니다.

## 7. 사이트 차단/HTML 변경 시

`hotdeal.zip`은 일부 자동 HTTP 접근에 403을 반환할 수 있습니다. 이 프로젝트는 일반 `requests` 대신 Chrome TLS/헤더를 흉내 내는 `curl_cffi`를 사용합니다.

그래도 GitHub Actions에서 403이 발생하거나 사이트 HTML 구조가 크게 바뀌면 Actions 로그와 `hotdeal/data/latest.json`의 `sourceStatus`에 오류가 남습니다. 그 경우 브라우저 기반 Playwright 폴백을 추가하는 방식으로 대응할 수 있습니다.

## 8. 로컬 파서 테스트

저장한 HTML 파일로 외부 접속 없이 파서를 점검할 수 있습니다.

```bash
pip install -r hotdeal/requirements.txt
python hotdeal/scripts/hotdeal_monitor.py --dry-run --html-file sample.html
```
