const $ = (selector) => document.querySelector(selector);

const state = {
  places: [],
  sources: [],
};

const REGION_ORDER = [
  "서울", "경기", "인천", "강원", "충북", "충남", "세종", "대전",
  "경북", "대구", "경남", "부산", "울산", "전북", "전남", "광주", "제주",
];

function unique(values) {
  return [...new Set(values.filter(Boolean))];
}

function escapeText(value) {
  return String(value ?? "");
}

function regionIndex(region) {
  const index = REGION_ORDER.indexOf(region);
  return index === -1 ? 999 : index;
}

function fillRegions() {
  const regions = unique(state.places.map((place) => place.region)).sort(
    (a, b) => regionIndex(a) - regionIndex(b) || a.localeCompare(b, "ko")
  );

  const previous = $("#region").value;
  $("#region").innerHTML = `
    <option value="all">전체</option>
    ${regions.map((region) => `<option value="${region}">${region}</option>`).join("")}
  `;

  const saved = localStorage.getItem("twoKidsRegion");
  const preferred = regions.includes(saved) ? saved : previous;
  $("#region").value = regions.includes(preferred) ? preferred : "all";
  fillDistricts();
}

function fillDistricts() {
  const region = $("#region").value;
  const districts = unique(
    state.places
      .filter((place) => region === "all" || place.region === region)
      .map((place) => place.district)
  ).sort((a, b) => a.localeCompare(b, "ko"));

  const previous = $("#district").value;
  $("#district").innerHTML = `
    <option value="all">전체</option>
    ${districts.map((district) => `<option value="${district}">${district}</option>`).join("")}
  `;
  $("#district").value = districts.includes(previous) ? previous : "all";
}

function filteredPlaces() {
  const region = $("#region").value;
  const district = $("#district").value;
  const age = $("#age").value;
  const keyword = $("#keyword").value.trim().toLowerCase();

  return state.places
    .filter((place) => {
      if (region !== "all" && place.region !== region) return false;
      if (district !== "all" && place.district !== district) return false;
      if (age !== "all") {
        const numericAge = Number(age);
        if (!(numericAge >= place.ageMin && numericAge <= place.ageMax)) return false;
      }
      if (
        keyword &&
        !`${place.name} ${place.region} ${place.district} ${place.category}`
          .toLowerCase()
          .includes(keyword)
      ) {
        return false;
      }
      return true;
    })
    .sort(
      (a, b) =>
        (a.rank ?? 999999) - (b.rank ?? 999999) ||
        regionIndex(a.region) - regionIndex(b.region) ||
        a.name.localeCompare(b.name, "ko")
    );
}

function renderSources() {
  $("#sourceStatus").innerHTML = state.sources
    .map(
      (source) =>
        `<span>${escapeText(source.source)} · ${source.ok ? `${source.count}곳` : "수집 실패"}</span>`
    )
    .join("");
}

function render() {
  const places = filteredPlaces();
  $("#count").textContent = places.length;
  $("#empty").hidden = places.length > 0;

  const list = $("#list");
  list.innerHTML = "";

  for (const place of places) {
    const node = $("#cardTemplate").content.cloneNode(true);
    const rank = Number.isFinite(place.rank) ? `#${place.rank}` : "인기";

    node.querySelector(".badge").textContent =
      `${rank} · ${place.region} ${place.district || ""}`.trim();
    node.querySelector(".ages").textContent = place.popularAge || `${place.ageMin}~${place.ageMax}세`;
    node.querySelector("h2").textContent = place.name;
    node.querySelector(".address").textContent = `${place.region} ${place.district || ""}`.trim();
    node.querySelector(".description").textContent =
      (place.categories && place.categories.length ? place.categories.join(" · ") : place.category) ||
      "맘맘 인기 랭킹 장소";
    node.querySelector(".official").textContent = "맘맘에서 보기";
    node.querySelector(".official").href = place.url;
    node.querySelector(".map").href =
      `https://map.kakao.com/link/search/${encodeURIComponent(`${place.name} ${place.region} ${place.district}`)}`;
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
    $("#updated").textContent = data.updatedAt
      ? `갱신 ${new Date(data.updatedAt).toLocaleString("ko-KR")}`
      : "아직 수집 전";

    fillRegions();
    renderSources();
    render();
  } catch (error) {
    console.error(error);
    state.places = [];
    $("#updated").textContent = "데이터를 불러오지 못했습니다";
    render();
  }
}

$("#region").addEventListener("change", () => {
  localStorage.setItem("twoKidsRegion", $("#region").value);
  fillDistricts();
  render();
});
$("#district").addEventListener("change", render);
$("#age").addEventListener("change", render);
$("#keyword").addEventListener("input", render);
$("#reset").addEventListener("click", () => {
  $("#region").value = "all";
  fillDistricts();
  $("#age").value = "all";
  $("#keyword").value = "";
  render();
});

init();
