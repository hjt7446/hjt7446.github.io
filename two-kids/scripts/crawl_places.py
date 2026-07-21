#!/usr/bin/env python3
"""서울형 키즈카페 공식 목록의 기본 정보를 수집해 places.json에 병합합니다.

사이트 개편으로 선택자가 바뀌면 빈 결과를 저장하지 않고 기존 데이터를 유지합니다.
시설 편의정보는 공식 상세 페이지에 일관된 구조가 없어 overrides.json에서 보정합니다.
"""
from __future__ import annotations
import json, re, sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
from urllib.parse import urljoin
import requests
from bs4 import BeautifulSoup

ROOT=Path(__file__).resolve().parents[1]
DATA=ROOT/'data'/'places.json'
OVERRIDES=ROOT/'data'/'overrides.json'
SOURCE='https://umppa.seoul.go.kr/icare/user/kidsCafe/BD_selectKidsCafeList.do'
HEADERS={'User-Agent':'Mozilla/5.0 (compatible; two-kids-place/1.0; +https://github.com/hjt7446/hjt7446.github.io)'}

def clean(s:str)->str:return re.sub(r'\s+',' ',s or '').strip()
def slug(s:str)->str:return re.sub(r'[^0-9a-z가-힣]+','-',s.lower()).strip('-')[:70]

def crawl()->list[dict]:
    r=requests.get(SOURCE,headers=HEADERS,timeout=25);r.raise_for_status()
    soup=BeautifulSoup(r.text,'html.parser'); found=[]; seen=set()
    for a in soup.select('a[href]'):
        text=clean(a.get_text(' ',strip=True)); href=a.get('href','')
        if not text or '키즈카페' not in text or len(text)>90: continue
        key=(text,href)
        if key in seen: continue
        seen.add(key)
        region='서울'
        m=re.search(r'(종로|중구|용산|성동|광진|동대문|중랑|성북|강북|도봉|노원|은평|서대문|마포|양천|강서|구로|금천|영등포|동작|관악|서초|강남|송파|강동)구',text)
        if m: region=f'서울 {m.group(0)}'
        found.append({'id':'kids-'+slug(text),'name':text,'region':region,'address':'공식 상세 페이지 확인','category':'kids-cafe','stayMinutes':120,'features':{'indoor':True,'stroller':True,'nursingRoom':False,'diaperTable':False,'parking':False,'elevator':False,'reservationFree':False,'free':True},'scores':{'first':90,'baby':65,'solo':62,'parent':72},'url':urljoin(SOURCE,href)})
    # 메뉴·안내 링크만 잡힌 경우를 방지
    unique={p['name']:p for p in found}
    return list(unique.values()) if len(unique)>=3 else []

def main()->int:
    current=json.loads(DATA.read_text(encoding='utf-8'))
    scraped=crawl()
    if not scraped:
        print('No reliable facility rows found; keeping existing data.',file=sys.stderr);return 0
    overrides=json.loads(OVERRIDES.read_text(encoding='utf-8')) if OVERRIDES.exists() else {}
    for p in scraped:
        if p['id'] in overrides:
            patch=overrides[p['id']]
            for k,v in patch.items():
                if isinstance(v,dict) and isinstance(p.get(k),dict):p[k].update(v)
                else:p[k]=v
    static=[p for p in current['places'] if p['category']!='kids-cafe' and not p['id'].startswith('kids-')]
    current['places']=static+scraped
    current['updatedAt']=datetime.now(ZoneInfo('Asia/Seoul')).isoformat(timespec='seconds')
    DATA.write_text(json.dumps(current,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(f'Wrote {len(current["places"])} places ({len(scraped)} crawled).')
    return 0
if __name__=='__main__':raise SystemExit(main())
