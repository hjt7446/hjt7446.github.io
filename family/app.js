const DAY = 86400000;
const TODAY = new Date();

TODAY.setHours(0, 0, 0, 0);

const $ = selector => document.querySelector(selector);

let performances = [];
let venues = [];
let filtered = [];
let config = {};
let meta = {};
let alerts = [];

const esc = value =>
  String(value ?? '').replace(
    /[&<>'"]/g,
    character =>
      ({
        '&': '&amp;',
        '<': '&lt;',
        '>': '&gt;',
        "'": '&#39;',
        '"': '&quot;'
      })[character]
  );

const parse = value => {
  if (!value) {
    return null;
  }

  const date = new Date(
    value.length === 10
      ? `${value}T00:00:00+09:00`
      : value
  );

  return Number.isNaN(date.getTime())
    ? null
    : date;
};

const fmt = new Intl.DateTimeFormat('ko-KR', {
  month: 'short',
  day: 'numeric'
});

const venueOf = performance =>
  venues.find(
    venue => venue.id === performance.venueId
  ) || {};

function normalizedRegion(venue) {
  const text = `
    ${venue.city || ''}
    ${venue.region || ''}
    ${venue.address || ''}
  `;

  if (/서울/.test(text)) {
    return '서울';
  }

  if (/인천/.test(text)) {
    return '인천';
  }

  if (
    /경기|부천|김포|고양|수원|성남|용인|안양|광명|시흥|군포|의왕|과천|하남|구리|남양주|파주|양주|의정부|동두천|포천|가평|양평|이천|여주|안성|평택|화성|오산/.test(
      text
    )
  ) {
    return '경기';
  }

  return venue.city || '지역 미정';
}

const days = (value, base = TODAY) => {
  const date = parse(value);

  if (!date) {
    return null;
  }

  return Math.ceil((date - base) / DAY);
};

const recent = value => {
  const date = parse(value);

  return (
    date &&
    (TODAY - date) / DAY <= Number(config.newDays || 7)
  );
};

function ticketStatus(performance) {
  const endDate = parse(performance.endDate);

  if (
    !endDate ||
    endDate < TODAY ||
    performance.ticketSaleStatus === 'ENDED'
  ) {
    return '종료';
  }

  if (performance.ticketSaleStatus === 'SOLD_OUT') {
    return '매진';
  }

  if (
    performance.ticketOpenDate &&
    parse(performance.ticketOpenDate) > TODAY
  ) {
    return '예매예정';
  }

  return '예매중/확인';
}

function badges(performance) {
  const output = [];

  const startDate = parse(performance.startDate);
  const endDate = parse(performance.endDate);

  if (
    recent(performance.firstSeenAt) ||
    recent(performance.updatedAt)
  ) {
    output.push(['NEW', 'new']);
  }

  if (
    startDate &&
    startDate.getTime() === TODAY.getTime()
  ) {
    output.push(['오늘 시작', 'urgent']);
  }

  if (
    endDate &&
    endDate.getTime() === TODAY.getTime()
  ) {
    output.push(['오늘 종료', 'urgent']);
  } else if (
    endDate &&
    endDate > TODAY &&
    (endDate - TODAY) / DAY <=
      Number(config.endingSoonDays || 7)
  ) {
    output.push(['곧 종료', 'urgent']);
  }

  if (
    performance.ticketOpenDate &&
    ticketStatus(performance) === '예매예정'
  ) {
    output.push([
      `예매 D-${days(performance.ticketOpenDate)}`,
      'ticket'
    ]);
  }

  if (performance.confidence?.level === '확인 필요') {
    output.push([
      '정보 확인 필요',
      'warn'
    ]);
  }

  return output.slice(0, 3);
}

/*
 * 아이와 함께 표시 기준
 *
 * 1. 가족추천후보 태그가 있음
 * 2. 관람연령이 확인됨
 * 3. 전체 관람가이거나 8세 이하 관람 가능
 */
function familyFit(performance) {
  const ageInfo = performance.ageInfo || {};

  if (
    !performance.familyTags?.includes('가족추천후보')
  ) {
    return false;
  }

  if (ageInfo.unknown) {
    return false;
  }

  const label = `
    ${ageInfo.label || ''}
    ${performance.age || ''}
  `
    .replace(/\s+/g, '')
    .toLowerCase();

  const minAge = Number(ageInfo.minAge);

  const isAllAges =
    minAge === 0 ||
    /전체관람가|전연령|모든연령|연령제한없음|누구나관람/.test(
      label
    );

  if (isAllAges) {
    return true;
  }

  const minimumAgeMatch = label.match(
    /(?:만)?(\d+)세이상/
  );

  if (minimumAgeMatch) {
    const minimumAge = Number(minimumAgeMatch[1]);

    return (
      Number.isFinite(minimumAge) &&
      minimumAge <= 8
    );
  }

  const monthMatch = label.match(
    /(\d+)개월이상/
  );

  if (monthMatch) {
    const minimumMonths = Number(monthMatch[1]);

    return (
      Number.isFinite(minimumMonths) &&
      minimumMonths <= 96
    );
  }

  if (
    Number.isFinite(minAge) &&
    minAge >= 0 &&
    minAge <= 8
  ) {
    return true;
  }

  return false;
}

function confidence(performance) {
  const confidenceData =
    performance.confidence || {};

  let className = 'low';

  if (confidenceData.level === '높음') {
    className = 'high';
  } else if (
    confidenceData.level === '보통'
  ) {
    className = 'mid';
  }

  return `
    <span class="confidence ${className}">
      신뢰도
      ${esc(
        confidenceData.level ||
        '확인 필요'
      )}
      ${confidenceData.score ?? '-'}
    </span>
  `;
}

function card(performance) {
  const venue = venueOf(performance);

  const poster = performance.poster
    ? `
      <img
        src="${esc(performance.poster)}"
        alt=""
        loading="lazy"
        referrerpolicy="no-referrer"
      >
    `
    : '<span class="emoji">🎭</span>';

  const startDate = parse(performance.startDate);
  const endDate = parse(performance.endDate);

  const dateText =
    startDate && endDate
      ? `${fmt.format(startDate)}–${fmt.format(endDate)}`
      : '공연 기간 확인 필요';

  return `
    <article
      class="performance-card"
      data-id="${esc(performance.id)}"
      tabindex="0"
    >
      <div class="poster">
        ${poster}

        <div class="badges">
          ${badges(performance)
            .map(
              ([text, className]) => `
                <span class="badge ${className}">
                  ${esc(text)}
                </span>
              `
            )
            .join('')}
        </div>
      </div>

      <div class="card-body">
        <h3>
          ${esc(performance.title)}
        </h3>

        <div class="meta">
          <span>
            ${esc(normalizedRegion(venue))}
            ${esc(venue.region || '')}
          </span>

          <span>${dateText}</span>
        </div>

        <p class="age">
          ${esc(
            performance.ageInfo?.label ||
            performance.age ||
            '관람연령 확인 필요'
          )}
        </p>

        <div class="card-foot">
          <b>
            ${esc(ticketStatus(performance))}
          </b>

          ${confidence(performance)}
        </div>
      </div>
    </article>
  `;
}

function render(selector, list) {
  const element = $(selector);

  if (!element) {
    return;
  }

  element.innerHTML = list.length
    ? list.map(card).join('')
    : `
      <div class="empty">
        조건에 맞는 공연이 없습니다.
      </div>
    `;
}

function active(performance) {
  const endDate = parse(performance.endDate);

  return endDate && endDate >= TODAY;
}

/*
 * 검색 전:
 * 관심 작품 → 아이와 함께 → 전체 공연
 *
 * 검색 중:
 * 관심 작품 → 검색된 공연 → 아이와 함께
 */
function updateSectionOrder(isSearching) {
  const watchSection = $('#watch');
  const familySection = $('#family');
  const allSection = $('#all');

  if (
    !watchSection ||
    !familySection ||
    !allSection
  ) {
    return;
  }

  if (isSearching) {
    watchSection.insertAdjacentElement(
      'afterend',
      allSection
    );
  } else {
    familySection.insertAdjacentElement(
      'afterend',
      allSection
    );
  }
}

function updateNavigation(isSearching) {
  const allLink = document.querySelector(
    'nav a[href="#all"]'
  );

  if (!allLink) {
    return;
  }

  allLink.textContent = isSearching
    ? '검색된 공연'
    : '전체 공연';
}

function applyFilters() {
  const searchValue =
    $('#searchInput').value.trim();

  const query =
    searchValue.toLowerCase();

  const region =
    $('#regionFilter').value;

  const ageFilter =
    $('#ageFilter').value;

  const selectedDateValue =
    $('#dateFilter').value;

  const selectedDate =
    parse(selectedDateValue);

  filtered = performances.filter(
    performance => {
      const venue = venueOf(performance);

      const searchableText = `
        ${performance.title || ''}
        ${performance.genre || ''}
        ${performance.description || ''}
        ${venue.name || ''}
        ${venue.address || ''}
      `.toLowerCase();

      const queryMatches =
        !query ||
        searchableText.includes(query);

      const regionMatches =
        !region ||
        normalizedRegion(venue) === region;

      const ageMatches =
        !ageFilter ||
        (
          ageFilter === 'family'
            ? familyFit(performance)
            : !performance.ageInfo?.unknown
        );

      const startDate =
        parse(performance.startDate);

      const endDate =
        parse(performance.endDate);

      const dateMatches =
        !selectedDateValue ||
        (
          selectedDate &&
          startDate &&
          endDate &&
          startDate <= selectedDate &&
          endDate >= selectedDate
        );

      return (
        active(performance) &&
        queryMatches &&
        regionMatches &&
        ageMatches &&
        dateMatches
      );
    }
  );

  const isSearching =
    searchValue.length > 0;

  $('#resultTitle').textContent =
    isSearching
      ? '검색된 공연'
      : '전체 공연';

  updateSectionOrder(isSearching);
  updateNavigation(isSearching);

  render('#allList', filtered);

  $('#resultCount').textContent =
    `${filtered.length}개 공연`;
}

function links(performance, venue) {
  const bookingLinks = (
    performance.bookingUrls || []
  )
    .filter(item => item?.url)
    .map(
      item => `
        <a
          target="_blank"
          rel="noopener"
          href="${esc(item.url)}"
        >
          ${esc(item.name || '예매')}
        </a>
      `
    )
    .join('');

  const mapLinks = venue.address
    ? `
      <a
        target="_blank"
        rel="noopener"
        href="https://www.google.com/maps/search/?api=1&query=${encodeURIComponent(
          venue.address
        )}"
      >
        Google 지도
      </a>

      <a
        target="_blank"
        rel="noopener"
        href="https://map.kakao.com/link/search/${encodeURIComponent(
          venue.address
        )}"
      >
        카카오맵
      </a>

      <a
        target="_blank"
        rel="noopener"
        href="https://map.naver.com/p/search/${encodeURIComponent(
          venue.address
        )}"
      >
        네이버 지도
      </a>
    `
    : '';

  const sourceLink =
    performance.sourceUrl
      ? `
        <a
          target="_blank"
          rel="noopener"
          href="${esc(performance.sourceUrl)}"
        >
          KOPIS 원문
        </a>
      `
      : '';

  return (
    bookingLinks +
    mapLinks +
    sourceLink
  );
}

function openDetail(id) {
  const performance =
    performances.find(
      item => item.id === id
    );

  if (!performance) {
    return;
  }

  const venue =
    venueOf(performance);

  const checks =
    performance.confidence?.checks || {};

  $('#detailContent').innerHTML = `
    <div class="detail-hero">
      <div class="badges">
        ${badges(performance)
          .map(
            ([text, className]) => `
              <span class="badge ${className}">
                ${esc(text)}
              </span>
            `
          )
          .join('')}
      </div>

      <h2>
        ${esc(performance.title)}
      </h2>

      <p>
        ${esc(performance.description || '')}
      </p>
    </div>

    <div class="detail-body">
      <div class="detail-grid">
        <div class="info-box">
          <b>공연 기간</b>

          <p>
            ${esc(
              performance.startDate ||
              '확인 필요'
            )}
            ~
            ${esc(
              performance.endDate ||
              '확인 필요'
            )}
          </p>
        </div>

        <div class="info-box">
          <b>관람 연령</b>

          <p>
            ${esc(
              performance.ageInfo?.label ||
              performance.age ||
              '확인 필요'
            )}
          </p>
        </div>

        <div class="info-box">
          <b>공연장</b>

          <p>
            ${esc(
              venue.name ||
              '미정'
            )}

            <br>

            ${esc(
              venue.address ||
              '주소 확인 필요'
            )}
          </p>
        </div>

        <div class="info-box">
          <b>가격·시간</b>

          <p>
            ${esc(
              performance.price ||
              '가격 확인 필요'
            )}

            <br>

            ${esc(
              performance.runtime ||
              '공연시간 확인 필요'
            )}
          </p>
        </div>
      </div>

      <section class="verification">
        <h3>정보 정확도</h3>

        ${confidence(performance)}

        <ul>
          <li>
            ${checks.officialSource ? '✓' : '!'}
            공식 KOPIS 출처
          </li>

          <li>
            ${checks.age ? '✓' : '!'}
            관람연령
            ${
              checks.age
                ? '확인됨'
                : '원문 확인 필요'
            }
          </li>

          <li>
            ${checks.booking ? '✓' : '!'}
            예매 링크
            ${
              checks.booking
                ? '확인됨'
                : '원문에서 확인 필요'
            }
          </li>

          <li>
            마지막 확인:
            ${esc(
              performance.lastCheckedAt ||
              '-'
            )}
          </li>
        </ul>
      </section>

      <div class="map-links">
        ${links(performance, venue)}
      </div>
    </div>
  `;

  injectLd(performance, venue);

  $('#detailDialog').showModal();
}

function injectLd(performance, venue) {
  $('#eventJsonLd')?.remove();

  const script =
    document.createElement('script');

  script.id = 'eventJsonLd';
  script.type = 'application/ld+json';

  script.textContent = JSON.stringify({
    '@context': 'https://schema.org',
    '@type': 'Event',
    name: performance.title,
    startDate: performance.startDate,
    endDate: performance.endDate,

    image: performance.poster
      ? [performance.poster]
      : undefined,

    location: {
      '@type': 'Place',
      name: venue.name,
      address: venue.address,

      geo: venue.latitude
        ? {
            '@type': 'GeoCoordinates',
            latitude: venue.latitude,
            longitude: venue.longitude
          }
        : undefined
    },

    offers: (
      performance.bookingUrls || []
    )[0]
      ? {
          '@type': 'Offer',
          url:
            performance.bookingUrls[0].url
        }
      : undefined
  });

  document.head.appendChild(script);
}

async function fetchJson(url, fallback) {
  const response = await fetch(url, {
    cache: 'no-store'
  });

  if (!response.ok) {
    return fallback;
  }

  return response.json();
}

async function init() {
  [
    config,
    venues,
    performances,
    meta,
    alerts
  ] = await Promise.all([
    fetchJson('config.json', {}),
    fetchJson('data/venues.json', []),
    fetchJson('data/performances.json', []),
    fetchJson('data/sync-meta.json', {}),
    fetchJson('data/alerts.json', [])
  ]);

  filtered =
    performances.filter(active);

  const updated =
    parse(meta.updatedAt);

  const elapsedHours = updated
    ? (
        Date.now() -
        updated.getTime()
      ) / 3600000
    : 999;

  $('#dataStatus').innerHTML = `
    <b>
      ${esc(
        meta.source ||
        '공식 데이터'
      )}
    </b>

    · ${
      meta.performanceCount ||
      performances.length
    }개

    · 마지막 확인
    ${
      updated
        ? updated.toLocaleString('ko-KR')
        : '알 수 없음'
    }

    ${
      elapsedHours >
      Number(config.staleHours || 30)
        ? '<strong>⚠ 갱신 지연</strong>'
        : '<span>정상 갱신</span>'
    }
  `;

  $('#watchKeywords').textContent =
    (
      config.watchKeywords || []
    ).join(' · ');

  $('#footerMeta').textContent = `
    마지막 데이터 확인:
    ${meta.updatedAt || '-'}
    / 출처:
    ${meta.source || '-'}
  `.trim();

  [
    '서울',
    '인천',
    '경기'
  ].forEach(region => {
    $('#regionFilter').insertAdjacentHTML(
      'beforeend',
      `
        <option value="${region}">
          ${region}
        </option>
      `
    );
  });

  const watchIds = new Set(
    alerts
      .filter(
        alert =>
          alert.type === 'WATCH_MATCH'
      )
      .map(
        alert =>
          alert.performanceId
      )
  );

  const watchPerformances =
    performances
      .filter(
        performance =>
          watchIds.has(performance.id) &&
          active(performance)
      )
      .slice(0, 12);

  const familyPerformances =
    performances
      .filter(
        performance =>
          active(performance) &&
          familyFit(performance)
      )
      .sort(
        (a, b) =>
          (
            b.confidence?.score ||
            0
          ) -
          (
            a.confidence?.score ||
            0
          )
      )
      .slice(0, 12);

  render(
    '#watchList',
    watchPerformances
  );

  render(
    '#familyList',
    familyPerformances
  );

  render(
    '#allList',
    filtered
  );

  $('#resultCount').textContent =
    `${filtered.length}개 공연`;

  [
    '#searchInput',
    '#regionFilter',
    '#ageFilter',
    '#dateFilter'
  ].forEach(selector => {
    $(selector).addEventListener(
      'input',
      applyFilters
    );
  });

  $('.dialog-close').addEventListener(
    'click',
    () => {
      $('#detailDialog').close();
    }
  );

  $('#detailDialog').addEventListener(
    'click',
    event => {
      if (
        event.target === $('#detailDialog')
      ) {
        $('#detailDialog').close();
      }
    }
  );

  document.addEventListener(
    'click',
    event => {
      const cardElement =
        event.target.closest('[data-id]');

      if (cardElement) {
        openDetail(
          cardElement.dataset.id
        );
      }
    }
  );

  document.addEventListener(
    'keydown',
    event => {
      if (
        event.key === 'Enter' &&
        event.target.matches('[data-id]')
      ) {
        openDetail(
          event.target.dataset.id
        );
      }
    }
  );
}

init().catch(error => {
  console.error(error);

  $('#dataStatus').textContent =
    '데이터를 불러오지 못했습니다. GitHub Actions 실행 상태를 확인하세요.';
});
