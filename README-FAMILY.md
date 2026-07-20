# /family 배포용

이 패키지는 기존 `hjt7446.github.io` 루트를 유지한 채 공연 사이트를 다음 주소에 배포합니다.

- https://hjt7446.github.io/family/

저장소 루트에 `family/` 폴더와 `.github/workflows/update-family-performances.yml`을 그대로 복사하세요.

Repository Secret은 저장소 전체에서 공유되므로 이름은 `KOPIS_API_KEY`로 한 번만 등록하면 됩니다.

## 수도권 수집 설정

공연 데이터는 KOPIS에서 서울, 인천, 경기만 수집합니다. 부천은 경기 지역에 포함됩니다.
기본 수집 범위는 과거 14일~향후 180일이며, 지역별 최대 20페이지를 조회합니다.
GitHub Actions에서는 `python -u`로 실행되어 진행 로그가 즉시 표시되고, 전체 작업은 30분 후 자동 종료됩니다.
