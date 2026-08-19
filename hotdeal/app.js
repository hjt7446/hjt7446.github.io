const $ = selector => document.querySelector(selector);

const esc = value => String(value ?? '').replace(/[&<>'"]/g, ch => ({
  '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;'
})[ch]);

const won = value => {
  const n = Number(value);
  return Number.isFinite(n) && n > 0 ? `${n.toLocaleString('ko-KR')}원 이하` : '가격 제한 없음';
};

function ruleCard(rule) {
  const any = (rule.keywordsAny || []).join(', ') || '없음';
  const all = (rule.keywordsAll || []).join(', ') || '없음';
  const exclude = (rule.exclude || []).join(', ') || '없음';
  const scopes = [
    ...(rule.categories || []).map(v => `카테고리:${v}`),
    ...(rule.stores || []).map(v => `쇼핑몰:${v}`),
    ...(rule.communities || []).map(v => `커뮤니티:${v}`)
  ].join(', ') || '전체';

  return `
    <article class="rule-card ${rule.enabled ? '' : 'off'}">
      <div class="rule-title">
        <h3>${esc(rule.name || rule.id)}</h3>
        <span class="badge ${rule.enabled ? '' : 'off'}">${rule.enabled ? '감시중' : '꺼짐'}</span>
      </div>
      <div class="rule-meta">
        <div><b>하나라도 포함</b> · ${esc(any)}</div>
        <div><b>모두 포함</b> · ${esc(all)}</div>
        <div><b>제외</b> · ${esc(exclude)}</div>
        <div><b>가격</b> · ${esc(won(rule.maxPrice))}</div>
        <div><b>범위</b> · ${esc(scopes)}</div>
      </div>
    </article>`;
}

function dealCard(deal) {
  const meta = [deal.ruleName, deal.category, deal.store, deal.community, deal.detectedAt]
    .filter(Boolean)
    .join(' · ');
  const price = deal.price ? `${Number(deal.price).toLocaleString('ko-KR')}원` : '가격 미상';
  return `
    <a class="deal" href="${esc(deal.url)}" target="_blank" rel="noopener">
      <div>
        <h3>${esc(deal.title)}</h3>
        <div class="deal-meta">${esc(meta)}</div>
      </div>
      <div class="price">${esc(price)} ↗</div>
    </a>`;
}

async function load() {
  try {
    const [configResponse, dataResponse] = await Promise.all([
      fetch(`config.json?v=${Date.now()}`),
      fetch(`data/latest.json?v=${Date.now()}`)
    ]);
    if (!configResponse.ok || !dataResponse.ok) throw new Error('fetch failed');

    const config = await configResponse.json();
    const data = await dataResponse.json();
    const rules = config.rules || [];
    const matches = data.matches || [];

    document.title = config.siteName || '내 핫딜 알리미';
    $('#ruleCount').textContent = `${rules.filter(v => v.enabled).length}개 감시중`;
    $('#ruleGrid').innerHTML = rules.length ? rules.map(ruleCard).join('') : '<div class="empty">등록된 감시 조건이 없습니다.</div>';

    $('#matchCount').textContent = `${matches.length}건`;
    $('#dealList').innerHTML = matches.length ? matches.map(dealCard).join('') : '<div class="empty">아직 발견된 핫딜이 없습니다.</div>';

    $('#sourceStatus').textContent = data.sourceStatus || '상태 정보 없음';
    $('#updatedAt').textContent = data.updatedAt ? `최근 실행 ${new Date(data.updatedAt).toLocaleString('ko-KR')}` : '아직 실행 기록이 없습니다.';
    const ok = /정상|성공|완료/i.test(data.sourceStatus || '');
    $('#statusDot').classList.add(ok ? 'ok' : (data.updatedAt ? 'bad' : ''));
  } catch (error) {
    $('#sourceStatus').textContent = '데이터를 불러오지 못했습니다';
    $('#updatedAt').textContent = 'GitHub Pages 배포 상태를 확인하세요.';
    $('#statusDot').classList.add('bad');
  }
}

load();
