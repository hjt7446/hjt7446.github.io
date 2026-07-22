const $ = (s) => document.querySelector(s);
const state = { places: [], sources: [] };
const regionOrder = ["인천", "경기", "서울"];

function unique(values) { return [...new Set(values.filter(Boolean))]; }
function escapeText(value) { return String(value ?? ""); }

function fillRegions() {
  const regionSelect = $("#region");

  regionSelect.innerHTML = `
    <option value="all">전체</option>
    <option value="인천">인천</option>
    <option value="경기">경기</option>
    <option value="서울">서울</option>
  `;

  const preferred =
    localStorage.getItem("twoKidsRegion") ||
    "인천";

  regionSelect.value = [
    "all",
    "인천",
    "경기",
    "서울",
  ].includes(preferred)
    ? preferred
    : "인천";

  fillDistricts();
}

function fillDistricts() {
  const region = $("#region").value;

  const districts = unique(
    state.places
      .filter(
        (place) =>
          region === "all" ||
          place.region === region
      )
      .map((place) => place.district)
      .filter(
        (district) =>
          district &&
          !["서울", "경기", "인천"].includes(
            district
          )
      )
  ).sort((a, b) =>
    a.localeCompare(b, "ko")
  );

  const current =
    $("#district").value;

  $("#district").innerHTML = `
    <option value="all">전체</option>
    ${districts
      .map(
        (district) =>
          `<option value="${district}">${district}</option>`
      )
      .join("")}
  `;

  $("#district").value =
    districts.includes(current)
      ? current
      : "all";
}

function filteredPlaces() {
  const region = $("#region").value;
  const district = $("#district").value;
  const age = $("#age").value;
  const keyword = $("#keyword").value.trim().toLowerCase();
  return state.places.filter((p) => {
    if (region !== "all" && p.region !== region) return false;
    if (district !== "all" && p.district !== district) return false;
    if (age !== "all" && !(Number(age) >= p.ageMin && Number(age) <= p.ageMax)) return false;
    if (keyword && !`${p.name} ${p.address}`.toLowerCase().includes(keyword)) return false;
    return true;
  }).sort((a,b) => regionOrder.indexOf(a.region)-regionOrder.indexOf(b.region) || a.district.localeCompare(b.district,"ko") || a.name.localeCompare(b.name,"ko"));
}

function renderSources() {
  $("#sourceStatus").innerHTML = state.sources.map((s) => `<span class="source ${s.ok ? "ok" : "error"}">${escapeText(s.region)} ${s.ok ? `${s.count}곳` : "수집 실패"}</span>`).join("");
}

function render() {
  const places = filteredPlaces();
  $("#count").textContent = places.length;
  $("#empty").hidden = places.length > 0;
  const list = $("#list"); list.innerHTML = "";
  for (const place of places) {
    const node = $("#cardTemplate").content.cloneNode(true);
    node.querySelector(".badge").textContent = `${place.region} ${place.district || ""}`.trim();
    node.querySelector(".ages").textContent = `${place.ageMin}~${place.ageMax}세`;
    node.querySelector("h2").textContent = place.name;
    node.querySelector(".address").textContent = place.address;
    node.querySelector(".description").textContent = place.description || "운영시간과 예약 여부는 공식 페이지에서 확인하세요.";
    node.querySelector(".official").href = place.url;
    node.querySelector(".map").href = `https://map.kakao.com/link/search/${encodeURIComponent(`${place.name} ${place.address}`)}`;
    list.appendChild(node);
  }
}

async function init() {
  try {
    const response = await fetch(`./data/places.json?v=${Date.now()}`, { cache: "no-store" });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const data = await response.json();
    state.places = Array.isArray(data.places) ? data.places : [];
    state.sources = Array.isArray(data.sources) ? data.sources : [];
    $("#updated").textContent = data.updatedAt ? `갱신 ${new Date(data.updatedAt).toLocaleString("ko-KR")}` : "아직 수집 전";
    fillRegions(); renderSources(); render();
  } catch (error) {
    console.error(error);
    state.places = [];
    $("#updated").textContent = "데이터를 불러오지 못했습니다";
    render();
  }
}

$("#region").addEventListener("change", () => { localStorage.setItem("twoKidsRegion", $("#region").value); fillDistricts(); render(); });
$("#district").addEventListener("change", render);
$("#age").addEventListener("change", render);
$("#keyword").addEventListener("input", render);
$("#reset").addEventListener("click", () => { $("#region").value = "all"; fillDistricts(); $("#age").value = "all"; $("#keyword").value = ""; render(); });
init();
