#!/usr/bin/env python3
"""가족용 공연 데이터 동기화.

공식 KOPIS API를 기준 데이터로 사용하고, 이전 데이터와 필드 단위 비교를 통해
신규/변경을 정확히 기록한다. 실패 시 정상 데이터 파일을 덮어쓰지 않는다.
"""
from __future__ import annotations

import hashlib, json, os, re, sys, time, urllib.parse, urllib.request
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
P_PATH, V_PATH = DATA / "performances.json", DATA / "venues.json"
HISTORY_PATH, ALERTS_PATH, META_PATH = DATA / "change-history.json", DATA / "alerts.json", DATA / "sync-meta.json"
CONFIG_PATH = ROOT / "config.json"
BASE = "https://www.kopis.or.kr/openApi/restful"
KEY = os.environ.get("KOPIS_API_KEY", "").strip()
DAYS_BACK = int(os.environ.get("SYNC_DAYS_BACK", "14"))
DAYS_FORWARD = int(os.environ.get("SYNC_DAYS_FORWARD", "180"))
PAGE_SIZE = min(int(os.environ.get("SYNC_PAGE_SIZE", "100")), 100)
MAX_PAGES = int(os.environ.get("SYNC_MAX_PAGES", "20"))
DELAY = float(os.environ.get("SYNC_REQUEST_DELAY", "0.05"))
TIMEOUT = int(os.environ.get("SYNC_TIMEOUT", "15"))
MIN_SUCCESS_RATIO = float(os.environ.get("MIN_SUCCESS_RATIO", "0.80"))
USER_AGENT = "family-performance-finder/2.0 (+https://hjt7446.github.io/)"
KST = timezone(timedelta(hours=9))
VOLATILE = {"lastCheckedAt", "confidence", "freshness", "changeSummary"}
COMPARE_FIELDS = ["title","venueId","startDate","endDate","genre","runtime","age","price","cast","poster","bookingUrls","sourceStatus"]
CAPITAL_REGIONS = {"서울": "11", "인천": "28", "경기": "41"}
TARGET_REGION_NAMES = set(CAPITAL_REGIONS)

def now_iso() -> str: return datetime.now(KST).isoformat(timespec="seconds")
def load(path: Path, default: Any):
    try: return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError): return default

def write_atomic(path: Path, value: Any):
    path.parent.mkdir(parents=True, exist_ok=True); tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"); tmp.replace(path)

def request_xml(path: str, params: dict[str, Any] | None = None) -> ET.Element:
    query = {"service": KEY, **{k:str(v) for k,v in (params or {}).items() if v not in (None,"")}}
    req = urllib.request.Request(f"{BASE}/{path}?{urllib.parse.urlencode(query)}", headers={"User-Agent":USER_AGENT,"Accept":"application/xml"})
    last = None
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as r: payload=r.read()
            root=ET.fromstring(payload)
            if root.find(".//returncode") is not None and root.findtext(".//returncode") not in (None,"00"): raise RuntimeError(root.findtext(".//errmsg") or "API error")
            return root
        except Exception as e: last=e; time.sleep(1.4*(attempt+1))
    raise RuntimeError(f"KOPIS request failed {path}: {last}")

def ymd(v: str) -> str | None:
    v=(v or "").strip().replace(".","-").replace("/","-")
    for f in ("%Y-%m-%d","%Y%m%d"):
        try:return datetime.strptime(v,f).date().isoformat()
        except ValueError:pass
    return None

def number(v: str):
    try:return float(v)
    except (TypeError,ValueError):return None

def stable(prefix: str, raw: str): return f"{prefix}-{hashlib.sha1(raw.encode()).hexdigest()[:14]}"
def split_region(addr: str):
    p=(addr or "").split(); return (p[0] if p else "", p[1] if len(p)>1 else "")

def text_only(value: str) -> str:
    return re.sub(r"<[^>]+>", " ", value or "").replace("&lt;","<").replace("&gt;",">").strip()

def extract_urls(detail: dict[str,str]) -> list[dict[str,str]]:
    raw = detail.get("relates", "")
    urls=[]
    try:
        node=ET.fromstring(f"<root>{raw}</root>")
        for rel in node.findall(".//relate"):
            url=(rel.findtext("relateurl") or "").strip(); name=(rel.findtext("relatenm") or "예매/공식 페이지").strip()
            if url.startswith("http"): urls.append({"name":name,"url":url,"source":"KOPIS"})
    except ET.ParseError:
        for u in re.findall(r"https?://[^\s<\"]+", raw): urls.append({"name":"예매/공식 페이지","url":u,"source":"KOPIS"})
    seen=set(); return [x for x in urls if not (x["url"] in seen or seen.add(x["url"]))]

def parse_age(raw: str) -> dict[str,Any]:
    s=(raw or "").strip(); lower=s.lower()
    if any(x in s for x in ["전체 관람가","전 연령","모든 연령"]): return {"label":s or "전체 관람가","minAge":0,"unknown":False}
    nums=[int(x) for x in re.findall(r"(\d+)\s*(?:세|개월)",s)]
    if nums:
        n=min(nums); months="개월" in s
        return {"label":s,"minAge":0 if months and n<36 else (n//12 if months else n),"unknown":False}
    if any(x in lower for x in ["미취학", "유아"]): return {"label":s,"minAge":3,"unknown":False}
    return {"label":s or "연령 정보 확인 필요","minAge":None,"unknown":True}

def family_tags(title: str, genre: str, age: dict[str,Any], description: str) -> list[str]:
    hay=f"{title} {genre} {description}".lower(); tags=[]
    if any(k in hay for k in ["아동","어린이","키즈","가족","패밀리","뮤지컬"]): tags.append("가족추천후보")
    if any(k in hay for k in ["뮤지컬"]): tags.append("뮤지컬")
    if age["minAge"] is not None and age["minAge"] <= 7: tags.append("어린이관람가능")
    if age["unknown"]: tags.append("연령확인필요")
    return tags

def meaningful_changes(old: dict[str,Any], new: dict[str,Any]) -> list[dict[str,Any]]:
    out=[]
    for f in COMPARE_FIELDS:
        if old.get(f) != new.get(f): out.append({"field":f,"before":old.get(f),"after":new.get(f)})
    return out

def confidence(perf: dict[str,Any], venue: dict[str,Any]) -> dict[str,Any]:
    checks={"officialSource":perf.get("source")=="KOPIS","date":bool(perf.get("startDate") and perf.get("endDate")),"venue":bool(venue.get("name") and venue.get("address")),"age":not perf.get("ageInfo",{}).get("unknown",True),"booking":bool(perf.get("bookingUrls"))}
    score=55 + sum([15 if checks["date"] else 0,10 if checks["venue"] else 0,10 if checks["age"] else 0,10 if checks["booking"] else 0])
    score=min(score,100); level="높음" if score>=85 else "보통" if score>=70 else "확인 필요"
    return {"score":score,"level":level,"checks":checks}

def fetch_list():
    start=date.today()-timedelta(days=DAYS_BACK)
    end=date.today()+timedelta(days=DAYS_FORWARD)
    rows=[]
    seen=set()
    print(f"[sync] target=서울/인천/경기 range={start}..{end}", flush=True)
    for region_name, region_code in CAPITAL_REGIONS.items():
        for page in range(1,MAX_PAGES+1):
            params={
                "stdate":start.strftime("%Y%m%d"),
                "eddate":end.strftime("%Y%m%d"),
                "cpage":page,
                "rows":PAGE_SIZE,
                "signgucode":region_code,
            }
            items=request_xml("pblprfr",params).findall(".//db")
            if not items:
                break
            added=0
            for item in items:
                row={c.tag:(c.text or "").strip() for c in item}
                pid=row.get("mt20id")
                if pid and pid not in seen:
                    seen.add(pid)
                    rows.append(row)
                    added+=1
            print(f"[sync] {region_name} page {page}: received={len(items)} added={added} total={len(rows)}", flush=True)
            if len(items)<PAGE_SIZE:
                break
            time.sleep(DELAY)
    return rows

def fetch_detail(pid):
    item=request_xml(f"pblprfr/{urllib.parse.quote(pid)}").find(".//db")
    return {c.tag:(c.text or "").strip() for c in item} if item is not None else {}

def fetch_venue(vid):
    item=request_xml(f"prfplc/{urllib.parse.quote(vid)}").find(".//db")
    return {c.tag:(c.text or "").strip() for c in item} if item is not None else {}

def main():
    if not KEY: raise SystemExit("KOPIS_API_KEY is required")
    old_list=load(P_PATH,[]); old={x.get("id"):x for x in old_list}; old_venues={x.get("id"):x for x in load(V_PATH,[])}
    history=load(HISTORY_PATH,[]); config=load(CONFIG_PATH,{})
    rows=fetch_list()
    if not rows: raise SystemExit("No list data; keeping previous dataset")
    stamp=now_iso(); venues=dict(old_venues); venue_raw_cache={}; results=[]; failures=0; changes=[]
    for i,brief in enumerate(rows,1):
        kid=brief.get("mt20id");
        if not kid: continue
        try: detail=fetch_detail(kid)
        except Exception as e: print(f"[sync] detail failed {kid}: {e}",file=sys.stderr); failures+=1; continue
        start,end=ymd(detail.get("prfpdfrom") or brief.get("prfpdfrom")),ymd(detail.get("prfpdto") or brief.get("prfpdto"))
        if not start or not end: failures+=1; continue
        kvid=detail.get("mt10id",""); vname=detail.get("fcltynm") or brief.get("fcltynm") or "공연장 미정"; vid=f"kopis-{kvid}" if kvid else stable("venue",vname)
        vr={}
        if kvid:
            try:
                if kvid not in venue_raw_cache: venue_raw_cache[kvid]=fetch_venue(kvid)
                vr=venue_raw_cache[kvid]
            except Exception as e: print(f"[sync] venue failed {kvid}: {e}",file=sys.stderr)
        prevv=venues.get(vid,{}); addr=vr.get("adres") or prevv.get("address",""); city,region=split_region(addr)
        venues[vid]={"id":vid,"name":vr.get("fcltynm") or vname,"address":addr,"city":city or prevv.get("city",""),"region":region or prevv.get("region",""),"latitude":number(vr.get("la")) if number(vr.get("la")) is not None else prevv.get("latitude"),"longitude":number(vr.get("lo")) if number(vr.get("lo")) is not None else prevv.get("longitude"),"homepage":vr.get("relateurl") or prevv.get("homepage",""),"parking":vr.get("parkinglot") or prevv.get("parking",""),"phone":vr.get("telno") or prevv.get("phone",""),"source":"KOPIS","sourceId":kvid or None,"lastCheckedAt":stamp}
        pid=f"kopis-{kid}"; prev=old.get(pid,{})
        title=detail.get("prfnm") or brief.get("prfnm") or "제목 없음"; age=parse_age(detail.get("prfage","")); desc=text_only(detail.get("sty")) or f"{title} 공연 정보"
        status=detail.get("prfstate") or brief.get("prfstate",""); booking=extract_urls(detail)
        base={"id":pid,"source":"KOPIS","sourceId":kid,"sourceUrl":f"https://www.kopis.or.kr/por/db/pblprfr/pblprfrView.do?menuId=MNU_00020&mt20Id={urllib.parse.quote(kid)}","title":title,"venueId":vid,"startDate":start,"endDate":end,"ticketOpenDate":prev.get("ticketOpenDate"),"ticketSaleStatus":"SOLD_OUT" if "매진" in status else ("ENDED" if "공연완료" in status else "ON_SALE"),"genre":detail.get("genrenm") or brief.get("genrenm","") ,"runtime":detail.get("prfruntime","") ,"age":detail.get("prfage","") ,"ageInfo":age,"price":detail.get("pcseguidance","") ,"cast":detail.get("prfcast","") ,"crew":detail.get("prfcrew","") ,"poster":detail.get("poster") or brief.get("poster","") ,"bookingUrls":booking,"description":desc,"familyTags":family_tags(title,detail.get("genrenm","") ,age,desc),"sourceStatus":status,"createdAt":prev.get("createdAt") or stamp,"firstSeenAt":prev.get("firstSeenAt") or stamp,"lastCheckedAt":stamp}
        diff=meaningful_changes(prev,base) if prev else []
        base["updatedAt"] = stamp if diff else (prev.get("updatedAt") or stamp)
        base["changeSummary"]=[x["field"] for x in diff]
        base["confidence"]=confidence(base,venues[vid])
        results.append(base)
        if prev and diff:
            evt={"performanceId":pid,"title":title,"detectedAt":stamp,"changes":diff}; changes.append(evt); history.append(evt)
        if i%20==0: print(f"[sync] details {i}/{len(rows)} successes={len(results)} failures={failures}", flush=True)
        time.sleep(DELAY)
    ratio=len(results)/max(len(rows),1)
    if ratio<MIN_SUCCESS_RATIO: raise SystemExit(f"Success ratio {ratio:.1%} below threshold; previous data preserved")
    # 목록에서 사라졌더라도 아직 미래/진행 공연이면 이전 데이터를 보존하고 stale 표시
    result_ids={x["id"] for x in results}
    for p in old_list:
        old_venue=old_venues.get(p.get("venueId"),{})
        old_region=(old_venue.get("city") or "").replace("특별시","").replace("광역시","").replace("도","")
        if p.get("id") not in result_ids and p.get("endDate","") >= date.today().isoformat() and old_region in TARGET_REGION_NAMES:
            q=dict(p); q["freshness"]="이번 수집에서 미확인"; q["confidence"]={**q.get("confidence",{}),"level":"확인 필요"}; results.append(q)
    results.sort(key=lambda p:(p.get("startDate",""),p.get("title","")))
    watch=[x.lower() for x in config.get("watchKeywords",[])]
    alerts=[]
    for p in results:
        matched=[k for k in watch if k in p.get("title","").lower()]
        if matched:
    alerts.append({
        "type": "WATCH_MATCH",
        "performanceId": p.get("id", ""),
        "title": p.get("title", "제목 없음"),
        "keywords": matched,
        "date": p.get("startDate", ""),
        "detectedAt": (
            p.get("firstSeenAt")
            or p.get("createdAt")
            or p.get("updatedAt")
            or stamp
        ),
        "message": f"관심 작품 발견: {p.get('title', '제목 없음')}",
    })
    for c in changes[-200:]: alerts.append({"type":"CHANGED","performanceId":c["performanceId"],"title":c["title"],"detectedAt":c["detectedAt"],"message":"공연 정보가 변경되었습니다.","changes":[x["field"] for x in c["changes"]]})
    used_venue_ids={p.get("venueId") for p in results}
    kept_venues=[v for key,v in venues.items() if key in used_venue_ids]
    write_atomic(P_PATH,results); write_atomic(V_PATH,sorted(kept_venues,key=lambda v:(v.get("city",""),v.get("name",""))))
    write_atomic(HISTORY_PATH,history[-1000:]); write_atomic(ALERTS_PATH,sorted(alerts,key=lambda x:x.get("detectedAt",""),reverse=True)[:300])
    write_atomic(META_PATH,{"updatedAt":stamp,"status":"ok","source":"KOPIS official API","performanceCount":len(results),"venueCount":len(kept_venues),"changedCount":len(changes),"failedCount":failures,"successRatio":round(ratio,4),"range":{"daysBack":DAYS_BACK,"daysForward":DAYS_FORWARD},"regions":list(CAPITAL_REGIONS.keys()),"dataNotes":["예매 오픈일은 공식 데이터에 없으면 표시하지 않음","예매 링크와 관람연령이 없는 공연은 확인 필요로 표시"]})
    print(f"[sync] done performances={len(results)} venues={len(kept_venues)} changes={len(changes)} failures={failures}", flush=True)

if __name__=="__main__": main()
