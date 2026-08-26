import React, { useEffect, useMemo, useRef, useState } from 'react';

const API_BASE_URL = 'http://127.0.0.1:8000';

const emptyRouteResult = {
  locations: [],
  distance: 0,
  duration: 0,
  legs: [],
};

// "HH:MM"(24시간제) -> 오전/오후·시(1~12)·분 드롭다운 상태로 분해.
// 값이 없으면 셋 다 빈 문자열(미선택).
const apptPartsFromHHMM = (hhmm) => {
  if (!hhmm || typeof hhmm !== 'string' || !hhmm.includes(':')) {
    return { appt_meridiem: '', appt_hour: '', appt_minute: '' };
  }
  const [h, m] = hhmm.split(':').map(Number);
  if (!Number.isFinite(h) || !Number.isFinite(m)) {
    return { appt_meridiem: '', appt_hour: '', appt_minute: '' };
  }
  const meridiem = h < 12 ? '오전' : '오후';
  const hour12 = h % 12 === 0 ? 12 : h % 12; // 0시->12(오전 12시), 12시->12(오후 12시=정오)
  return { appt_meridiem: meridiem, appt_hour: String(hour12), appt_minute: String(m) };
};

const toLocation = (place, task = '방문', priority = 3) => ({
  name: place.place_name,
  task,
  priority,
  lat: Number(place.y),
  lng: Number(place.x),
  address: place.road_address_name || place.address_name || '',
  duration_min: 0,
  appointment_time: null,
  // 약속 시각을 LLM이 산문에서 자동 추출했는지 표시(뱃지용). 사용자가 직접 수정하면 false로 내린다.
  appointment_from_ai: false,
  // 약속 시각 입력용 오전/오후·시·분 드롭다운 상태(출발 예정 시각과 동일한 방식).
  // appointment_time("HH:MM")은 이 셋에서 파생한다.
  appt_meridiem: '',
  appt_hour: '',
  appt_minute: '',
});

function LandingOverlay({ onStart }) {
  return (
    <div style={landingStyles.overlay}>
      <style>{`
        @keyframes wtgmateFadeUp {
          from { opacity: 0; transform: translateY(14px); }
          to { opacity: 1; transform: translateY(0); }
        }
        @media (prefers-reduced-motion: reduce) {
          .wtgmate-landing-anim { animation: none !important; }
        }
      `}</style>

      <div style={landingStyles.glow} />

      <div className="wtgmate-landing-anim" style={landingStyles.content}>
        <div style={landingStyles.icon}>R</div>
        <h1 style={landingStyles.title}>WTGMate</h1>
        <p style={landingStyles.subtitle}>Smart Route Planner</p>

        <button
          type="button"
          onClick={onStart}
          style={landingStyles.startButton}
          onMouseEnter={(e) => {
            e.currentTarget.style.transform = 'translateY(-1px)';
            e.currentTarget.style.boxShadow =
              '0 10px 26px rgba(99, 91, 255, 0.45)';
          }}
          onMouseLeave={(e) => {
            e.currentTarget.style.transform = 'translateY(0)';
            e.currentTarget.style.boxShadow =
              '0 8px 20px rgba(99, 91, 255, 0.35)';
          }}
        >
          시작하기
        </button>
      </div>
    </div>
  );
}

function PlaceCandidateList({ results, title, onSelect, onClose }) {
  if (!results?.length) {
    return (
      <div style={{ fontSize: 12, color: '#94a3b8', padding: '8px 2px' }}>
        검색 후보가 없습니다.
      </div>
    );
  }

  return (
    <div style={styles.candidateBox}>
      <div style={styles.candidateHeader}>
        <strong>📍 {title}</strong>
        {onClose && (
          <button type="button" onClick={onClose} style={styles.textButton}>
            닫기
          </button>
        )}
      </div>

      {results.map((place, index) => (
        <button
          type="button"
          key={`${place.id || place.place_name}-${index}`}
          onClick={() => onSelect(place)}
          style={styles.candidateButton}
        >
          <div style={{ fontWeight: 700, fontSize: 13 }}>
            {index + 1}. {place.place_name}
          </div>
          {place.category_name && (
            <div style={styles.categoryText}>{place.category_name}</div>
          )}
          <div style={styles.addressText}>
            📍 {place.road_address_name || place.address_name || '주소 정보 없음'}
          </div>
        </button>
      ))}
    </div>
  );
}

function PlaceSearchSelector({
  query,
  onQueryChange,
  results,
  onSearch,
  onSelect,
  onClose,
  placeholder,
  searching,
}) {
  return (
    <div>
      <div style={styles.searchRow}>
        <input
          value={query}
          onChange={(e) => onQueryChange(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter') onSearch();
          }}
          placeholder={placeholder}
          style={styles.input}
        />
        <button
          type="button"
          onClick={onSearch}
          disabled={searching}
          style={{ ...styles.primaryButton, width: 82 }}
        >
          {searching ? '검색 중' : '검색'}
        </button>
      </div>

      {results?.length > 0 && (
        <PlaceCandidateList
          results={results}
          title="출발지를 선택해주세요"
          onSelect={onSelect}
          onClose={onClose}
        />
      )}
    </div>
  );
}

function App() {
  const kakaoJavaScriptKey = import.meta.env.VITE_KAKAO_JAVASCRIPT_KEY;

  const [startInput, setStartInput] = useState('서울역');
  const [startLocation, setStartLocation] = useState({
    name: '서울역',
    task: '출발지',
    lat: 37.5547,
    lng: 126.9707,
    priority: 0,
    address: '',
  });

  // "일정 자체"를 보관한다. 현재 선택된 경로 순서와 분리한다.
  const [locations, setLocations] = useState([]);
  const [inputText, setInputText] = useState('');

  const [startSearchResults, setStartSearchResults] = useState([]);
  const [startSearching, setStartSearching] = useState(false);

  const [searchResultsMap, setSearchResultsMap] = useState({});
  const [placeSearchingMap, setPlaceSearchingMap] = useState({});
  const [isVerifying, setIsVerifying] = useState(false);

  const [loading, setLoading] = useState(false);
  const [etaLoading, setEtaLoading] = useState(false);
  const [isMockMode, setIsMockMode] = useState(false);

  const [travelMode, setTravelMode] = useState('car');
  const [activeRoute, setActiveRoute] = useState('ai');
  const [started, setStarted] = useState(false);

  // 예상 출발 시각("HH:MM", 24시간제). 비워두면 시간 제약/도착시각 계산을 하지 않는다.
  const [startTime, setStartTime] = useState('');
  // 출발 시각을 오전/오후 · 시(1~12) · 분(0~59) 세 드롭다운으로 입력받는다.
  // 셋 중 하나라도 미선택('')이면 startTime을 비워 시간 제약을 끈다.
  const [startMeridiem, setStartMeridiem] = useState(''); // '오전' | '오후'
  const [startHour, setStartHour] = useState('');         // '1'~'12'
  const [startMinute, setStartMinute] = useState('');     // '0'~'59'
  // 체크 시 출발 시각을 현재 PC 시각으로 계산한다. 기본은 미체크(현재시각을 몰래 강제하지 않음).
  // 미체크 + 출발시각 미선택이면 시간 계산 자체를 하지 않는다(도착시각/지각 표시 없음).
  const [useCurrentTime, setUseCurrentTime] = useState(false);

  // 세 결과는 서로 덮어쓰지 않는다.
  const [routeResults, setRouteResults] = useState({
    ai: null,
    shortest: null,
    priority: null,
  });

  const [isPriorityDirty, setIsPriorityDirty] = useState(false);

  const mapContainer = useRef(null);
  const mapInstance = useRef(null);
  const polylineInstance = useRef(null);
  const markersRef = useRef([]);

  // 일정이 바뀌면 이전 경로 캐시를 무효화한다.
  const scheduleKey = useMemo(() => {
    const startKey = [
      startLocation?.name || '',
      Number(startLocation?.lat || 0).toFixed(6),
      Number(startLocation?.lng || 0).toFixed(6),
    ].join('|');

    const locationsKey = locations
      .map((loc) =>
        [
          loc.name,
          Number(loc.lat || 0).toFixed(6),
          Number(loc.lng || 0).toFixed(6),
          loc.task || '',
          Number(loc.priority || 3),
        ].join('|')
      )
      .join('||');

    return `${startKey}###${locationsKey}`;
  }, [startLocation, locations]);

  // 두 경로가 "완전히 같은 방문 순서"인지 비교하기 위한 키.
  // 이름/좌표가 순서대로 전부 같으면 같은 경로로 취급한다.
  const routeSequenceKey = (locs) =>
    (locs || [])
      .map((loc) =>
        [
          loc.name,
          Number(loc.lat || 0).toFixed(6),
          Number(loc.lng || 0).toFixed(6),
        ].join('|')
      )
      .join('||');

  const previousScheduleKey = useRef(scheduleKey);

  useEffect(() => {
    if (previousScheduleKey.current === scheduleKey) return;

    previousScheduleKey.current = scheduleKey;

    setRouteResults({
      ai: null,
      shortest: null,
      priority: null,
    });

    setActiveRoute('ai');
    setIsPriorityDirty(false);
  }, [scheduleKey]);

  const currentRoute = routeResults[activeRoute] || emptyRouteResult;

  // -------------------------------
  // Kakao Map SDK
  // -------------------------------
  const initKakaoMap = () => {
    if (!mapContainer.current || mapInstance.current) return;

    if (window.kakao?.maps) {
      window.kakao.maps.load(() => {
        if (!mapContainer.current || mapInstance.current) return;

        mapInstance.current = new window.kakao.maps.Map(
          mapContainer.current,
          {
            center: new window.kakao.maps.LatLng(37.7634, 126.7746),
            level: 8,
          }
        );

        drawMapElements([startLocation, ...locations]);
      });
    }
  };

  useEffect(() => {
    if (!kakaoJavaScriptKey) {
      console.error('VITE_KAKAO_JAVASCRIPT_KEY가 .env에 없습니다.');
      return;
    }

    if (window.kakao?.maps) {
      initKakaoMap();
      return;
    }

    const existingScript = document.querySelector(
      'script[data-kakao-maps-sdk="true"]'
    );

    if (existingScript) {
      existingScript.addEventListener('load', initKakaoMap);
      return () => existingScript.removeEventListener('load', initKakaoMap);
    }

    const script = document.createElement('script');
    script.src =
      `https://dapi.kakao.com/v2/maps/sdk.js?appkey=${encodeURIComponent(
        kakaoJavaScriptKey
      )}&libraries=services&autoload=false`;
    script.async = true;
    script.dataset.kakaoMapsSdk = 'true';
    script.onload = initKakaoMap;
    script.onerror = () =>
      console.error('카카오맵 SDK를 불러오지 못했습니다.');

    document.head.appendChild(script);

    return () => {
      script.onload = null;
    };
  }, [kakaoJavaScriptKey]);

  useEffect(() => {
    if (mapInstance.current) {
      const hasRoute = currentRoute.locations?.length;
      drawMapElements(
        hasRoute ? currentRoute.locations : [startLocation, ...locations],
        hasRoute ? currentRoute.routePath : null
      );
    }
  }, [startLocation, locations, currentRoute]);

  // -------------------------------
  // Kakao 장소 검색
  // -------------------------------
  const searchKakaoPlaces = (keyword, onSuccess, onFail) => {
    if (!keyword.trim()) {
      onFail?.('검색어를 입력해 주세요.');
      return;
    }

    if (!window.kakao?.maps?.services) {
      onFail?.('카카오 장소 검색 서비스가 아직 준비되지 않았습니다.');
      return;
    }

    const ps = new window.kakao.maps.services.Places();

    ps.keywordSearch(
      keyword,
      (data, status) => {
        if (
          status === window.kakao.maps.services.Status.OK &&
          data.length > 0
        ) {
          onSuccess(data);
        } else {
          onFail?.(`"${keyword}"에 대한 검색 결과를 찾을 수 없습니다.`);
        }
      },
      { size: 10 }
    );
  };

  const handleSearchStartLocation = () => {
    if (!startInput.trim()) {
      alert('출발지명을 입력해 주세요.');
      return;
    }

    setStartSearching(true);
    setStartSearchResults([]);

    searchKakaoPlaces(
      startInput,
      (data) => {
        setStartSearchResults(data);
        setStartSearching(false);
      },
      (message) => {
        setStartSearching(false);
        alert(message);
      }
    );
  };

  const handleSelectStartPlace = (place) => {
    const newStart = toLocation(place, '출발지', 0);

    setStartLocation(newStart);
    setStartInput(place.place_name);
    setStartSearchResults([]);
  };

  // -------------------------------
  // Gemini 장소 추출
  // -------------------------------
  const handleParseText = async () => {
    if (!inputText.trim()) {
      alert('일정을 입력해 주세요.');
      return;
    }

    setLoading(true);
    setIsVerifying(false);
    setIsMockMode(false);

    try {
      const res = await fetch(`${API_BASE_URL}/api/parse-tasks`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ user_text: inputText }),
      });

      const data = await res.json();

      if (!res.ok) {
        throw new Error(data.detail || '장소 분석 실패');
      }

      if (data.status !== 'success') {
        throw new Error(data.message || '장소 분석 실패');
      }

      const parsedList = (Array.isArray(data.data) ? data.data : []).map((item) => ({
        ...item,
        // 체류 시간(분): 사용자가 확인 단계에서 조정. 기본 30분.
        duration_min: Number(item.duration_min) > 0 ? Number(item.duration_min) : 30,
        // 약속 시각: LLM이 추출했으면 그 값, 없으면 사용자가 확인 단계에서 입력.
        appointment_time: item.appointment_time || null,
        // 값이 LLM에서 왔으면 뱃지로 알려준다(사용자 수정 시 updateAppointmentPart에서 내림).
        appointment_from_ai: Boolean(item.appointment_time),
        // 드롭다운 상태도 LLM 값에서 분해해 채운다.
        ...apptPartsFromHHMM(item.appointment_time),
      }));

      if (!parsedList.length) {
        alert('방문할 장소를 찾지 못했습니다.');
        return;
      }

      setLocations(parsedList);
      setRouteResults({
        ai: null,
        shortest: null,
        priority: null,
      });
      setActiveRoute('ai');
      setIsPriorityDirty(false);

      if (data.is_mock) setIsMockMode(true);

      verifyExtractedLocations(parsedList);
    } catch (err) {
      console.error(err);
      alert(`장소 분석 오류: ${err.message}`);
    } finally {
      setLoading(false);
    }
  };

  const verifyExtractedLocations = (extractedList) => {
    if (!window.kakao?.maps?.services) {
      setIsVerifying(true);
      return;
    }

    const resultsMap = {};
    const searchingMap = {};

    extractedList.forEach((_, index) => {
      searchingMap[index] = true;
    });

    setSearchResultsMap({});
    setPlaceSearchingMap(searchingMap);

    let completed = 0;

    const finishOne = () => {
      completed += 1;

      if (completed === extractedList.length) {
        setSearchResultsMap({ ...resultsMap });
        setPlaceSearchingMap({ ...searchingMap });
        setIsVerifying(true);
      }
    };

    extractedList.forEach((item, index) => {
      searchKakaoPlaces(
        item.name,
        (data) => {
          resultsMap[index] = data;
          searchingMap[index] = false;
          finishOne();
        },
        () => {
          resultsMap[index] = [];
          searchingMap[index] = false;
          finishOne();
        }
      );
    });
  };

  const handleSelectPlaceCandidate = (index, selectedPlace) => {
    setLocations((prev) => {
      const updated = [...prev];
      const old = updated[index];

      updated[index] = {
        ...old,
        ...toLocation(
          selectedPlace,
          old.task || '방문',
          Number(old.priority) || 3
        ),
        // 후보 교체는 좌표/주소만 바꾸는 것이므로, 사용자가 입력한
        // 체류 시간과 약속 시각은 그대로 유지한다. (toLocation이 이 둘을
        // 기본값 0/null로 덮어쓰기 때문에 여기서 old 값으로 되살린다)
        duration_min: old.duration_min ?? 0,
        appointment_time: old.appointment_time ?? null,
        appointment_from_ai: old.appointment_from_ai ?? false,
        appt_meridiem: old.appt_meridiem ?? '',
        appt_hour: old.appt_hour ?? '',
        appt_minute: old.appt_minute ?? '',
      };

      return updated;
    });

    setSearchResultsMap((prev) => ({
      ...prev,
      [index]: [],
    }));
  };

  const handleConfirmLocations = () => {
    setIsVerifying(false);
  };

  // -------------------------------
  // ETA
  // -------------------------------
  const fetchRealEta = async (orderedList, mode = travelMode) => {
    setEtaLoading(true);

    try {
      const res = await fetch(`${API_BASE_URL}/api/route-eta`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          ordered_locations: orderedList,
          travel_mode: mode,
          start_time: effectiveStartTime(),
        }),
      });

      const data = await res.json();

      if (!res.ok || data.status !== 'success') {
        throw new Error(
          data.detail || data.message || '거리/시간 계산 실패'
        );
      }

      const legs = Array.isArray(data.legs) ? data.legs : [];
      // 자동차 실제 도로 좌표열을 leg 순서대로 이어붙인다([[lat,lng],...]).
      // 도보/대중교통은 path가 비어 있어 routePath도 빈 배열이 된다(→ 지도에서 직선 폴백).
      const routePath = legs.flatMap((l) => (Array.isArray(l.path) ? l.path : []));

      return {
        locations: orderedList,
        distance: Number(data.total_distance_km || 0),
        duration: Number(data.total_duration_min || 0),
        legs,
        routePath,
        estimated: Boolean(data.estimated),
        travelMode: mode,
        // 출발 시각을 입력했을 때만 채워지는 시간축 정보.
        startTimeUsed: data.start_time || null,
        finishTime: data.finish_time || null,
        totalElapsedMin: data.total_elapsed_min ?? null,
        // 출발시각 미입력(③)인데 약속이 있어 백엔드가 역산한 '추천 출발시각'.
        recommendedStartTime: data.recommended_start_time || null,
        recommendedFeasible: data.recommended_feasible ?? null,
        stops: Array.isArray(data.stops) ? data.stops : null,
        appointmentViolations: Array.isArray(data.appointment_violations)
          ? data.appointment_violations
          : [],
      };
    } catch (err) {
      console.error('ETA 계산 실패:', err);
      alert(`거리/예상시간 계산에 실패했습니다.\n${err.message}`);
      return null;
    } finally {
      setEtaLoading(false);
    }
  };

  // -------------------------------
  // 경로 계산 공통 함수
  // -------------------------------
  // travelModeArg: setTravelMode가 비동기라, 이동수단을 막 바꾼 직후 호출할 때
  // 최신 이동수단을 명시적으로 넘기기 위한 인자(생략하면 현재 상태값 사용).
  const calculateRoute = async (mode, { force = false, travelModeArg = travelMode } = {}) => {
    if (locations.length < 1) {
      alert('방문할 장소가 1개 이상 필요합니다.');
      return;
    }

    // 같은 일정 + 같은 이동수단에 대한 결과가 있으면 캐시 사용
    const cached = routeResults[mode];

    if (!force && cached) {
      setActiveRoute(mode);
      return;
    }

    setLoading(true);

    try {
      const payload = {
        start_location: {
          ...startLocation,
          lat: Number(startLocation.lat),
          lng: Number(startLocation.lng),
          priority: 0,
        },
        locations: locations.map((loc) => ({
          ...loc,
          lat: Number(loc.lat),
          lng: Number(loc.lng),
          priority: Number(loc.priority) || 3,
        })),
        travel_mode: travelModeArg,
        optimize_mode: mode,
        start_time: effectiveStartTime(),
      };

      const res = await fetch(`${API_BASE_URL}/api/optimize-route`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });

      const data = await res.json();

      if (!res.ok || data.status !== 'success') {
        throw new Error(
          data.detail || data.message || '경로 계산 실패'
        );
      }

      const orderedList = Array.isArray(data.optimized_locations)
        ? data.optimized_locations
        : [];

      if (orderedList.length < 2) {
        throw new Error('경로 결과가 올바르지 않습니다.');
      }

      // 다른 모드가 이미 완전히 동일한 방문 순서를 계산해뒀다면,
      // 카카오 실시간 API를 또 호출하지 않고 그 결과를 그대로 재사용한다.
      // (같은 순서인데 API를 두 번 따로 부르면 실시간 교통상황 반영 때문에
      //  거리/시간 숫자가 미세하게 달라지는 문제를 방지)
      const newSequenceKey = routeSequenceKey(orderedList);
      const reusableEntry = Object.entries(routeResults).find(
        ([otherMode, otherResult]) =>
          otherMode !== mode &&
          otherResult &&
          otherResult.travelMode === travelModeArg &&
          routeSequenceKey(otherResult.locations) === newSequenceKey
      );

      const result = reusableEntry
        ? reusableEntry[1]
        : await fetchRealEta(orderedList, travelModeArg);

      if (!result) return;

      setRouteResults((prev) => ({
        ...prev,
        [mode]: result,
      }));

      setActiveRoute(mode);

      if (mode === 'priority') {
        setIsPriorityDirty(false);
      }
    } catch (err) {
      console.error(err);
      alert(`${routeLabel(mode)} 계산 실패\n${err.message}`);
    } finally {
      setLoading(false);
    }
  };

  const handleAiRoute = () => calculateRoute('ai');

  const handleShortestRoute = () => calculateRoute('shortest');

  const handlePriorityRoute = () => {
    if (!isPriorityDirty) {
      setActiveRoute('priority');
      return;
    }

    calculateRoute('priority', { force: true });
  };

  // 이동수단이 바뀌면 최적 방문 순서 자체가 달라질 수 있으므로(예: 도보는 직선거리 기반),
  // 기존 순서에 ETA만 다시 구하지 않고, 캐시를 비운 뒤 현재 선택된 모드를 새 이동수단으로
  // "재최적화"한다. 나머지 모드는 캐시를 비워 다음에 선택할 때 새로 계산되게 한다.
  const handleTravelModeChange = async (newMode) => {
    if (newMode === travelMode) return;

    const modeToRecalc = activeRoute;
    const hadRoute = currentRoute?.locations?.length > 1;

    setTravelMode(newMode);
    setRouteResults({ ai: null, shortest: null, priority: null });
    // 캐시를 비웠으므로 우선순위 경로도 재계산 필요 상태로 둔다(선택 시 새로 계산되도록).
    // 아래에서 활성 모드가 priority면 calculateRoute가 다시 false로 되돌린다.
    setIsPriorityDirty(true);

    // 이미 경로를 계산해 보여주고 있었을 때만 즉시 재최적화한다.
    // (setTravelMode가 비동기이므로 최신 이동수단을 travelModeArg로 명시해서 넘긴다.)
    if (hadRoute) {
      await calculateRoute(modeToRecalc, { force: true, travelModeArg: newMode });
    }
  };

  // -------------------------------
  // 우선순위
  // -------------------------------
  const updatePriority = (index, value) => {
    const priority = Number(value);

    setLocations((prev) => {
      const updated = [...prev];

      if (!updated[index]) return prev;

      updated[index] = {
        ...updated[index],
        priority,
      };

      return updated;
    });

    // 사용자 우선순위 경로만 무효화.
    // AI/최단 결과는 그대로 보존한다.
    setRouteResults((prev) => ({
      ...prev,
      priority: null,
    }));

    setIsPriorityDirty(true);
    setActiveRoute('priority');
  };

  // 체류 시간(분)과 약속 시각은 모든 모드의 스케줄에 영향을 주므로,
  // 값이 바뀌면 캐시된 세 경로 결과를 전부 무효화한다.
  const invalidateAllRoutes = () => {
    setRouteResults({ ai: null, shortest: null, priority: null });
    setIsPriorityDirty(false);
  };

  // 오전/오후 · 시(1~12) · 분을 24시간제 "HH:MM"으로 합친다.
  // 하나라도 미선택이면 '' (시간 제약 없음).
  const composeStartTime = (meridiem, hour12, minute) => {
    if (!meridiem || hour12 === '' || minute === '') return '';
    let hour = Number(hour12) % 12;         // 12시 -> 0
    if (meridiem === '오후') hour += 12;     // 오후 -> +12 (오후 12시는 정오 12시)
    return `${String(hour).padStart(2, '0')}:${String(Number(minute)).padStart(2, '0')}`;
  };

  // 현재 PC 시각을 24시간제 "HH:MM"으로 반환.
  const currentHHMM = () => {
    const d = new Date();
    return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`;
  };

  // 백엔드에 보낼 출발 시각을 결정한다.
  // ① '현재 시간 기준' 체크 -> 현재 PC 시각
  // ② 미체크 + 출발시각 드롭다운 선택 -> 그 시각
  // ③ 미체크 + 미선택 -> null (도착시각/지각 계산 안 함. 단 약속시각 순서는 백엔드가 지켜줌)
  const effectiveStartTime = () => {
    if (useCurrentTime) return currentHHMM();
    return startTime || null;
  };

  const updateStartTimePart = (part, value) => {
    const next = {
      meridiem: startMeridiem,
      hour: startHour,
      minute: startMinute,
      [part]: value,
    };
    setStartMeridiem(next.meridiem);
    setStartHour(next.hour);
    setStartMinute(next.minute);
    setStartTime(composeStartTime(next.meridiem, next.hour, next.minute));
    invalidateAllRoutes();
  };

  const clearStartTime = () => {
    setStartMeridiem('');
    setStartHour('');
    setStartMinute('');
    setStartTime('');
    invalidateAllRoutes();
  };

  // 역산으로 추천된 출발 시각("HH:MM")을 실제 출발시각 드롭다운에 채워 확정한다(③ -> ②).
  const applyRecommendedStartTime = (hhmm) => {
    const parts = apptPartsFromHHMM(hhmm); // {appt_meridiem, appt_hour, appt_minute}
    if (!parts.appt_hour) return;
    setUseCurrentTime(false);
    setStartMeridiem(parts.appt_meridiem);
    setStartHour(parts.appt_hour);
    setStartMinute(parts.appt_minute);
    setStartTime(hhmm);
    invalidateAllRoutes(); // 새 출발시각으로 다시 계산하도록 캐시 비움
  };

  const updateDuration = (index, value) => {
    const duration = Math.max(0, Number(value) || 0);

    setLocations((prev) => {
      const updated = [...prev];
      if (!updated[index]) return prev;
      updated[index] = { ...updated[index], duration_min: duration };
      return updated;
    });

    invalidateAllRoutes();
  };

  // 약속 시각 드롭다운(오전오후/시/분) 한 칸을 바꾼다. 셋이 다 채워졌을 때만
  // appointment_time("HH:MM")이 만들어지고(composeStartTime 재사용), 하나라도 비면 null.
  const updateAppointmentPart = (index, part, value) => {
    setLocations((prev) => {
      const updated = [...prev];
      const loc = updated[index];
      if (!loc) return prev;
      const next = {
        appt_meridiem: loc.appt_meridiem ?? '',
        appt_hour: loc.appt_hour ?? '',
        appt_minute: loc.appt_minute ?? '',
        [part]: value,
      };
      updated[index] = {
        ...loc,
        ...next,
        appointment_time: composeStartTime(next.appt_meridiem, next.appt_hour, next.appt_minute) || null,
        // 사용자가 직접 손대면 더 이상 'AI 자동입력'이 아니므로 뱃지를 내린다.
        appointment_from_ai: false,
      };
      return updated;
    });

    invalidateAllRoutes();
  };

  const clearAppointment = (index) => {
    setLocations((prev) => {
      const updated = [...prev];
      if (!updated[index]) return prev;
      updated[index] = {
        ...updated[index],
        appointment_time: null,
        appointment_from_ai: false,
        appt_meridiem: '',
        appt_hour: '',
        appt_minute: '',
      };
      return updated;
    });

    invalidateAllRoutes();
  };

  // -------------------------------
  // 지도
  // -------------------------------
  const drawMapElements = (locs, routePath = null) => {
    if (!mapInstance.current || !window.kakao?.maps) return;

    markersRef.current.forEach((marker) => marker.setMap(null));
    markersRef.current = [];

    if (polylineInstance.current) {
      polylineInstance.current.setMap(null);
      polylineInstance.current = null;
    }

    const bounds = new window.kakao.maps.LatLngBounds();
    const linePath = [];

    locs.forEach((loc, idx) => {
      const lat = Number(loc.lat);
      const lng = Number(loc.lng);

      if (!Number.isFinite(lat) || !Number.isFinite(lng)) return;

      const position = new window.kakao.maps.LatLng(lat, lng);

      const marker = new window.kakao.maps.Marker({
        position,
        map: mapInstance.current,
        title:
          idx === 0
            ? `출발 | ${loc.name}`
            : `${idx}. ${loc.name}`,
      });

      markersRef.current.push(marker);
      linePath.push(position);
      bounds.extend(position);
    });

    // 자동차 실제 도로 좌표열(routePath)이 있으면 그걸 따라 그리고,
    // 없으면(도보/대중교통·경로 미계산) 지점 간 직선으로 폴백한다.
    const hasRealPath = Array.isArray(routePath) && routePath.length > 1;
    const drawPath = hasRealPath
      ? routePath
          .filter(
            (p) =>
              Array.isArray(p) && Number.isFinite(p[0]) && Number.isFinite(p[1])
          )
          .map(([lat, lng]) => new window.kakao.maps.LatLng(lat, lng))
      : linePath;

    if (drawPath.length > 1) {
      polylineInstance.current = new window.kakao.maps.Polyline({
        path: drawPath,
        strokeWeight: 5,
        strokeColor: '#635BFF',
        strokeOpacity: 0.8,
        strokeStyle: 'solid',
      });

      polylineInstance.current.setMap(mapInstance.current);
    }

    if (!bounds.isEmpty()) {
      mapInstance.current.setBounds(bounds, 60, 60, 60, 60);
    }
  };

  // -------------------------------
  // UI helpers
  // -------------------------------
  const routeLabel = (mode) => {
    if (mode === 'ai') return 'AI 추천 경로';
    if (mode === 'shortest') return '추천 최단시간 경로';
    return '우선순위 반영 경로';
  };

  const routeDescription = (mode) => {
    if (mode === 'ai') {
      return 'AI가 판단한 일정 중요도와 이동시간을 함께 고려합니다.';
    }

    if (mode === 'shortest') {
      return '우선순위를 무시하고 총 이동시간이 가장 짧은 경로를 찾습니다.';
    }

    return '사용자가 직접 설정한 현재 우선순위를 반영합니다.';
  };

  const formatDuration = (minutes) => {
    const value = Number(minutes || 0);

    if (value <= 0) return '계산 전';

    const hour = Math.floor(value / 60);
    const minute = value % 60;

    if (hour > 0) {
      return minute > 0 ? `${hour}시간 ${minute}분` : `${hour}시간`;
    }

    return `${minute}분`;
  };

  const getPriorityText = (priority) => {
    const map = {
      1: '매우 중요',
      2: '중요',
      3: '보통',
      4: '여유',
      5: '낮음',
    };

    return map[Number(priority)] || '보통';
  };

  const routeCards = [
    {
      id: 'ai',
      icon: '⚡',
      title: 'AI 추천',
      description: routeDescription('ai'),
    },
    {
      id: 'shortest',
      icon: '🏁',
      title: '최단시간',
      description: routeDescription('shortest'),
    },
    {
      id: 'priority',
      icon: '🎯',
      title: '내 우선순위',
      description: routeDescription('priority'),
    },
  ];

  return (
    <div style={styles.app}>
      {!started && <LandingOverlay onStart={() => setStarted(true)} />}

      <aside style={styles.sidebar}>
        <div
          style={{ ...styles.brand, cursor: 'pointer' }}
          onClick={() => window.location.reload()}
          title="새로고침"
        >
          <div style={styles.brandIcon}>R</div>
          <div>
            <div style={styles.brandName}>WTGMate</div>
            <div style={styles.brandSub}>Smart Route Planner</div>
          </div>
        </div>

        <div style={styles.scrollContent}>
          <section style={styles.section}>
            <div style={styles.sectionLabel}>01 · 출발지</div>
            <div style={styles.panel}>
              <PlaceSearchSelector
                query={startInput}
                onQueryChange={setStartInput}
                results={startSearchResults}
                onSearch={handleSearchStartLocation}
                onSelect={handleSelectStartPlace}
                onClose={() => setStartSearchResults([])}
                placeholder="예: 금촌역, 서울역"
                searching={startSearching}
              />

              <div style={styles.currentLocation}>
                <span>현재 출발지</span>
                <strong>{startLocation?.name || '설정되지 않음'}</strong>
              </div>
            </div>
          </section>

          <section style={styles.section}>
            <div style={styles.sectionLabel}>02 · 이동수단</div>
            <div style={styles.modeGrid}>
              {[
                ['car', '🚗', '자동차'],
                ['walk', '🚶', '도보'],
                ['transit', '🚌', '대중교통'],
              ].map(([value, icon, label]) => (
                <button
                  key={value}
                  type="button"
                  onClick={() => handleTravelModeChange(value)}
                  style={{
                    ...styles.modeButton,
                    ...(travelMode === value
                      ? styles.modeButtonActive
                      : {}),
                  }}
                >
                  <span>{icon}</span>
                  {label}
                </button>
              ))}
            </div>
          </section>

          <section style={styles.section}>
            <div style={styles.sectionLabel}>03 · 일정 입력</div>

            <div style={styles.panel}>
              <textarea
                rows={4}
                value={inputText}
                onChange={(e) => setInputText(e.target.value)}
                placeholder="예: 강남역에서 미팅하고 홍대에서 친구를 만난 뒤 서울역에서 KTX를 타야 해"
                style={styles.textarea}
              />

              <div style={styles.startTimeRow}>
                <span style={styles.startTimeLabel}>🕒 출발 예정 시각</span>
                <div style={styles.timeSelectGroup}>
                  <select
                    value={startMeridiem}
                    onChange={(e) => updateStartTimePart('meridiem', e.target.value)}
                    disabled={useCurrentTime}
                    style={{ ...styles.timeSelect, opacity: useCurrentTime ? 0.45 : 1 }}
                  >
                    <option value="">오전/오후</option>
                    <option value="오전">오전</option>
                    <option value="오후">오후</option>
                  </select>
                  <select
                    value={startHour}
                    onChange={(e) => updateStartTimePart('hour', e.target.value)}
                    disabled={useCurrentTime}
                    style={{ ...styles.timeSelect, opacity: useCurrentTime ? 0.45 : 1 }}
                  >
                    <option value="">시</option>
                    {Array.from({ length: 12 }, (_, i) => i + 1).map((h) => (
                      <option key={h} value={h}>{h}시</option>
                    ))}
                  </select>
                  <select
                    value={startMinute}
                    onChange={(e) => updateStartTimePart('minute', e.target.value)}
                    disabled={useCurrentTime}
                    style={{ ...styles.timeSelect, opacity: useCurrentTime ? 0.45 : 1 }}
                  >
                    <option value="">분</option>
                    {Array.from({ length: 60 }, (_, i) => i).map((m) => (
                      <option key={m} value={m}>{String(m).padStart(2, '0')}분</option>
                    ))}
                  </select>
                </div>
                {!useCurrentTime && startTime && (
                  <button
                    type="button"
                    onClick={clearStartTime}
                    style={styles.timeClearButton}
                  >
                    지우기
                  </button>
                )}
              </div>

              <label style={styles.currentTimeCheck}>
                <input
                  type="checkbox"
                  checked={useCurrentTime}
                  onChange={(e) => {
                    setUseCurrentTime(e.target.checked);
                    invalidateAllRoutes();
                  }}
                />
                <span>현재 시간을 기준으로 계산하기</span>
              </label>

              <div style={styles.startTimeHint}>
                {useCurrentTime
                  ? '지금 출발한다고 보고 도착 시각·약속 준수를 계산합니다.'
                  : startTime
                  ? '선택한 출발 시각을 기준으로 도착 시각·약속 준수를 계산합니다.'
                  : '출발 시각을 정하지 않아 도착 시각은 계산하지 않습니다. (약속 시각을 넣으면 그 순서는 지켜집니다)'}
              </div>

              <button
                type="button"
                onClick={handleParseText}
                disabled={loading}
                style={styles.primaryButton}
              >
                {loading ? '장소 분석 중...' : '✨ AI로 장소 추출하기'}
              </button>
            </div>
          </section>

          {isMockMode && (
            <div style={styles.warning}>
              <strong>테스트 데이터 사용 중</strong>
              <div>Gemini API 한도 또는 설정 문제로 Mock 데이터가 사용되었습니다.</div>
            </div>
          )}

          {isVerifying && locations.length > 0 && (
            <section style={styles.verifyPanel}>
              <div style={styles.verifyTitle}>🔍 장소 확인</div>
              <div style={styles.verifyDesc}>
                AI가 추출한 장소가 정확한지 확인해주세요. 다른 위치라면 검색 후보를 선택할 수 있습니다.
              </div>

              {locations.map((loc, idx) => (
                <div key={`${loc.name}-${idx}`} style={styles.verifyItem}>
                  <div style={styles.placeTitle}>
                    <span style={styles.numberBadge}>{idx + 1}</span>
                    <strong>{loc.name}</strong>
                    <span style={styles.taskText}>{loc.task}</span>
                  </div>

                  {loc.address && (
                    <div style={styles.smallText}>📍 {loc.address}</div>
                  )}

                  <div style={styles.timeFieldRow}>
                    <label style={styles.timeField}>
                      <span style={styles.timeFieldLabel}>체류 시간</span>
                      <span style={styles.durationInputWrap}>
                        <input
                          type="number"
                          min={0}
                          step={5}
                          value={loc.duration_min ?? 0}
                          onChange={(e) => updateDuration(idx, e.target.value)}
                          style={styles.durationInput}
                        />
                        <span style={styles.durationUnit}>분</span>
                      </span>
                    </label>

                    <label style={styles.timeField}>
                      <span style={styles.timeFieldLabel}>
                        약속 시각 (선택)
                        {loc.appointment_from_ai && loc.appointment_time && (
                          <span style={styles.aiBadge}>✨ AI 자동입력</span>
                        )}
                      </span>
                      <div style={styles.apptSelectGroup}>
                        <select
                          value={loc.appt_meridiem || ''}
                          onChange={(e) => updateAppointmentPart(idx, 'appt_meridiem', e.target.value)}
                          style={styles.apptSelect}
                        >
                          <option value="">오전/오후</option>
                          <option value="오전">오전</option>
                          <option value="오후">오후</option>
                        </select>
                        <select
                          value={loc.appt_hour || ''}
                          onChange={(e) => updateAppointmentPart(idx, 'appt_hour', e.target.value)}
                          style={styles.apptSelect}
                        >
                          <option value="">시</option>
                          {Array.from({ length: 12 }, (_, i) => i + 1).map((h) => (
                            <option key={h} value={h}>{h}시</option>
                          ))}
                        </select>
                        <select
                          value={loc.appt_minute || ''}
                          onChange={(e) => updateAppointmentPart(idx, 'appt_minute', e.target.value)}
                          style={styles.apptSelect}
                        >
                          <option value="">분</option>
                          {Array.from({ length: 60 }, (_, i) => i).map((m) => (
                            <option key={m} value={m}>{String(m).padStart(2, '0')}분</option>
                          ))}
                        </select>
                        {loc.appointment_time && (
                          <button
                            type="button"
                            onClick={() => clearAppointment(idx)}
                            style={styles.timeClearButton}
                          >
                            지우기
                          </button>
                        )}
                      </div>
                    </label>
                  </div>

                  {placeSearchingMap[idx] ? (
                    <div style={styles.smallText}>장소 후보 검색 중...</div>
                  ) : (
                    <PlaceCandidateList
                      results={searchResultsMap[idx] || []}
                      title="다른 위치라면 선택"
                      onSelect={(selected) =>
                        handleSelectPlaceCandidate(idx, selected)
                      }
                    />
                  )}
                </div>
              ))}

              <button
                type="button"
                onClick={handleConfirmLocations}
                style={styles.successButton}
              >
                장소 확정하기
              </button>
            </section>
          )}

          {!isVerifying && locations.length > 0 && (
            <>
              <section style={styles.section}>
                <div style={styles.sectionHeader}>
                  <div>
                    <div style={styles.sectionLabel}>04 · 경로 비교</div>
                    <h2 style={styles.sectionTitle}>어떤 경로가 좋을까요?</h2>
                  </div>
                </div>

                <div style={styles.routeTabs}>
                  {routeCards.map((card) => {
                    const result = routeResults[card.id];
                    const isActive = activeRoute === card.id;
                    const isDirty = card.id === 'priority' && isPriorityDirty;

                    return (
                      <button
                        key={card.id}
                        type="button"
                        onClick={() => {
                          if (card.id === 'priority' && isPriorityDirty) {
                            handlePriorityRoute();
                          } else {
                            calculateRoute(card.id);
                          }
                        }}
                        disabled={loading || etaLoading}
                        style={{
                          ...styles.routeTab,
                          ...(isActive ? styles.routeTabActive : {}),
                        }}
                      >
                        <div style={styles.routeTabTop}>
                          <span style={styles.routeIcon}>{card.icon}</span>
                          <span>{card.title}</span>
                          {isDirty && <span style={styles.dot}>!</span>}
                        </div>

                        <div style={styles.routeTabStatus}>
                          {result ? '계산 완료 · 다시 계산하지 않음' : '아직 계산하지 않음'}
                        </div>
                      </button>
                    );
                  })}
                </div>

                <div style={styles.routeHint}>
                  각 경로를 눌러 결과를 비교할 수 있습니다. 이미 계산한 경로는 일정이 바뀌기 전까지 다시 계산하지 않습니다.
                </div>

                {isPriorityDirty && (
                  <div style={styles.priorityAlert}>
                    <div>
                      <strong style={{ display: 'block' }}>   
                        우선순위가 변경되었습니다.  
                      </strong>
                      <span style={styles.priorityAlertText}>    
                        현재 설정을 반영하려면 다시 계산해주세요.  
                      </span>
                    </div>

                    <button
                      type="button"
                      onClick={handlePriorityRoute}
                      disabled={loading || etaLoading}
                      style={styles.priorityButton}
                    >
                      🔄 다시 계산
                    </button>
                  </div>
                )}
              </section>

              <section style={styles.resultCard}>
                <div style={styles.resultHeader}>
                  <div>
                    <div style={styles.resultEyebrow}>
                      {routeCards.find((x) => x.id === activeRoute)?.icon}{' '}
                      현재 선택된 추천
                    </div>
                    <h2 style={styles.resultTitle}>
                      {routeLabel(activeRoute)}
                    </h2>
                  </div>

                  {etaLoading && (
                    <span style={styles.loadingPill}>계산 중...</span>
                  )}
                </div>

                <div style={styles.metricGrid}>
                  <div style={styles.metric}>
                    <span>총 이동거리</span>
                    <strong>
                      {currentRoute.distance > 0
                        ? `${currentRoute.distance.toFixed(1)} km`
                        : '계산 전'}
                    </strong>
                  </div>

                  <div style={styles.metric}>
                    <span>예상 소요시간</span>
                    <strong>
                      {currentRoute.duration > 0
                        ? formatDuration(currentRoute.duration)
                        : '계산 전'}
                    </strong>
                  </div>
                </div>

                {currentRoute.finishTime && (
                  <div style={styles.scheduleSummary}>
                    <span style={styles.scheduleSummaryText}>
                      {currentRoute.recommendedStartTime ? (
                        <>🕒 추천 출발시각 <strong>{currentRoute.recommendedStartTime}</strong> → {currentRoute.finishTime} 종료</>
                      ) : (
                        <>🕒 {currentRoute.startTimeUsed} 출발 → {currentRoute.finishTime} 종료</>
                      )}
                      {currentRoute.totalElapsedMin != null && (
                        <> · 총 {formatDuration(currentRoute.totalElapsedMin)}(대기·체류 포함)</>
                      )}
                    </span>
                    {currentRoute.recommendedStartTime && (
                      <div style={styles.recommendRow}>
                        <span style={styles.recommendHint}>
                          {currentRoute.recommendedFeasible === false
                            ? '약속을 모두 지킬 수는 없어요. 위반을 최소로 하는 가장 늦은 출발 시각입니다.'
                            : '약속에 늦지 않게 도착하는 가장 늦은 출발 시각이에요.'}
                        </span>
                        <button
                          type="button"
                          onClick={() => applyRecommendedStartTime(currentRoute.recommendedStartTime)}
                          style={styles.recommendApplyBtn}
                        >
                          이 시각으로 설정
                        </button>
                      </div>
                    )}
                  </div>
                )}

                {currentRoute.appointmentViolations?.length > 0 && (
                  <div style={styles.violationNotice}>
                    ⚠ 약속 시각을 지킬 수 없는 장소: {currentRoute.appointmentViolations.join(', ')}
                  </div>
                )}

                <div style={styles.resultDescription}>
                  {routeDescription(activeRoute)}
                </div>

                {currentRoute.estimated && (
                  <div style={styles.estimatedNotice}>
                    현재 이동수단은 보정값을 이용한 예상 시간입니다.
                  </div>
                )}
              </section>

              <section style={styles.section}>
                <div style={styles.sectionLabel}>05 · 방문 순서</div>

                <div style={styles.timeline}>
                  {(
                    currentRoute.locations?.length
                      ? currentRoute.locations
                      : [startLocation, ...locations]
                  ).map((loc, idx) => (
                    <div
                      key={`${loc.name}-${idx}`}
                      style={styles.timelineItem}
                    >
                      <div style={styles.timelineMarker}>
                        {idx === 0 ? '🚩' : idx}
                      </div>

                      <div style={styles.timelineLine} />

                      <div style={styles.timelineContent}>
                        <div style={styles.timelineTop}>
                          <strong>{loc.name}</strong>
                          {idx > 0 && (
                            <span
                              style={{
                                ...styles.priorityPill,
                                ...(Number(loc.priority) === 1
                                  ? styles.priorityHigh
                                  : {}),
                              }}
                            >
                              {getPriorityText(loc.priority)}
                            </span>
                          )}
                          {currentRoute.stops?.[idx] && (
                            <span
                              style={{
                                ...styles.arrivalBadge,
                                ...(currentRoute.stops[idx].late
                                  ? styles.arrivalBadgeLate
                                  : {}),
                              }}
                            >
                              {idx === 0
                                ? `${currentRoute.stops[idx].depart_time} 출발`
                                : `${currentRoute.stops[idx].arrival_time} 도착`}
                            </span>
                          )}
                        </div>

                        {currentRoute.stops?.[idx] && idx > 0 && (
                          <div style={styles.scheduleLine}>
                            {currentRoute.stops[idx].appointment_time && (
                              <span
                                style={
                                  currentRoute.stops[idx].late
                                    ? styles.apptTagLate
                                    : styles.apptTag
                                }
                              >
                                약속 {currentRoute.stops[idx].appointment_time}
                                {currentRoute.stops[idx].late ? ' · 지각' : ''}
                              </span>
                            )}
                            <span>출발 {currentRoute.stops[idx].depart_time}</span>
                          </div>
                        )}

                        {idx === 0 ? (
                          <span style={styles.taskText}>출발지</span>
                        ) : (
                          <>
                            <span style={styles.taskText}>{loc.task}</span>

                            <div style={styles.priorityRow}>
                              <span>중요도</span>
                              <select
                                value={Number(loc.priority) || 3}
                                onChange={(e) =>
                                  updatePriority(
                                    locations.findIndex(
                                      (item) =>
                                        item.name === loc.name &&
                                        Number(item.lat) === Number(loc.lat) &&
                                        Number(item.lng) === Number(loc.lng)
                                    ),
                                    e.target.value
                                  )
                                }
                                style={styles.prioritySelect}
                              >
                                <option value={1}>★★★★★ 매우 중요</option>
                                <option value={2}>★★★★☆ 중요</option>
                                <option value={3}>★★★☆☆ 보통</option>
                                <option value={4}>★★☆☆☆ 여유</option>
                                <option value={5}>★☆☆☆☆ 낮음</option>
                              </select>
                            </div>
                          </>
                        )}
                      </div>
                    </div>
                  ))}
                </div>
              </section>
            </>
          )}
        </div>
      </aside>

      <main style={styles.mapArea}>
        <div ref={mapContainer} style={styles.map} />

        <div style={styles.mapOverlay}>
          <div style={styles.mapOverlayTitle}>WTGMate</div>
          <div style={styles.mapOverlayText}>
            {locations.length
              ? `${locations.length}개의 방문 장소`
              : '일정을 입력하면 추천 경로가 표시됩니다.'}
          </div>
        </div>
      </main>
    </div>
  );
}

const landingStyles = {
  overlay: {
    position: 'fixed',
    inset: 0,
    zIndex: 999,
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    overflow: 'hidden',
    background:
      'radial-gradient(circle at 50% 32%, #232c4a 0%, #172033 46%, #0f1526 100%)',
    fontFamily:
      'Inter, Pretendard, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif',
  },

  glow: {
    position: 'absolute',
    width: 520,
    height: 520,
    borderRadius: '50%',
    background: '#635bff',
    opacity: 0.22,
    filter: 'blur(120px)',
    pointerEvents: 'none',
  },

  content: {
    position: 'relative',
    display: 'flex',
    flexDirection: 'column',
    alignItems: 'center',
    textAlign: 'center',
    animation: 'wtgmateFadeUp 0.6s ease-out both',
  },

  icon: {
    width: 72,
    height: 72,
    borderRadius: 20,
    background: '#635bff',
    color: '#fff',
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    fontWeight: 800,
    fontSize: 32,
    letterSpacing: -0.5,
    boxShadow: '0 12px 32px rgba(99, 91, 255, 0.4)',
    marginBottom: 22,
  },

  title: {
    margin: 0,
    fontSize: 34,
    fontWeight: 800,
    letterSpacing: -1,
    color: '#fff',
  },

  subtitle: {
    margin: '8px 0 36px',
    fontSize: 13,
    fontWeight: 700,
    letterSpacing: 1.2,
    textTransform: 'uppercase',
    color: '#9aa3c0',
  },

  startButton: {
    minWidth: 180,
    padding: '14px 40px',
    border: 'none',
    borderRadius: 12,
    background: '#635bff',
    color: '#fff',
    fontSize: 15,
    fontWeight: 750,
    letterSpacing: -0.2,
    cursor: 'pointer',
    boxShadow: '0 8px 20px rgba(99, 91, 255, 0.35)',
    transition: 'transform 0.15s ease, box-shadow 0.15s ease',
  },
};

const styles = {
  app: {
    display: 'flex',
    width: '100vw',
    height: '100vh',
    overflow: 'hidden',
    background: '#f6f7fb',
    color: '#172033',
    fontFamily:
      'Inter, Pretendard, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif',
  },

  sidebar: {
    width: 470,
    minWidth: 470,
    height: '100vh',
    background: '#f8f9fc',
    borderRight: '1px solid #e5e7eb',
    display: 'flex',
    flexDirection: 'column',
    zIndex: 5,
  },

  brand: {
    height: 76,
    boxSizing: 'border-box',
    padding: '0 22px',
    display: 'flex',
    alignItems: 'center',
    gap: 12,
    background: '#fff',
    borderBottom: '1px solid #e5e7eb',
  },

  brandIcon: {
    width: 38,
    height: 38,
    borderRadius: 11,
    background: '#635bff',
    color: '#fff',
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    fontWeight: 800,
    fontSize: 18,
  },

  brandName: {
    fontSize: 18,
    fontWeight: 800,
    letterSpacing: -0.5,
  },

  brandSub: {
    fontSize: 11,
    color: '#98a2b3',
    marginTop: 2,
  },

  scrollContent: {
    overflowY: 'auto',
    padding: '20px 18px 36px',
  },

  section: {
    marginBottom: 20,
  },

  sectionLabel: {
    fontSize: 11,
    fontWeight: 800,
    color: '#7c8799',
    letterSpacing: 0.8,
    marginBottom: 8,
    textTransform: 'uppercase',
  },

  sectionTitle: {
    margin: 0,
    fontSize: 20,
    letterSpacing: -0.7,
  },

  sectionHeader: {
    display: 'flex',
    justifyContent: 'space-between',
    alignItems: 'end',
    marginBottom: 10,
  },

  panel: {
    background: '#fff',
    border: '1px solid #e6e9ef',
    borderRadius: 14,
    padding: 14,
    boxShadow: '0 3px 12px rgba(20, 30, 55, 0.04)',
  },

  searchRow: {
    display: 'flex',
    gap: 7,
  },

  input: {
    flex: 1,
    minWidth: 0,
    height: 40,
    boxSizing: 'border-box',
    padding: '0 11px',
    border: '1px solid #d9dee8',
    borderRadius: 9,
    outline: 'none',
    fontSize: 13,
  },

  textarea: {
    width: '100%',
    minHeight: 94,
    boxSizing: 'border-box',
    resize: 'vertical',
    padding: 12,
    border: '1px solid #d9dee8',
    borderRadius: 10,
    outline: 'none',
    fontSize: 13,
    lineHeight: 1.55,
    marginBottom: 9,
  },

  primaryButton: {
    width: '100%',
    minHeight: 40,
    border: 'none',
    borderRadius: 9,
    background: '#635bff',
    color: '#fff',
    fontWeight: 750,
    cursor: 'pointer',
    boxShadow: '0 5px 12px rgba(99, 91, 255, 0.2)',
  },

  successButton: {
    width: '100%',
    minHeight: 42,
    border: 'none',
    borderRadius: 9,
    background: '#16a36a',
    color: '#fff',
    fontWeight: 750,
    cursor: 'pointer',
  },

  currentLocation: {
    marginTop: 10,
    padding: '9px 10px',
    background: '#f7f8fb',
    borderRadius: 8,
    display: 'flex',
    justifyContent: 'space-between',
    gap: 10,
    fontSize: 11,
    color: '#7b8494',
  },

  modeGrid: {
    display: 'grid',
    gridTemplateColumns: 'repeat(3, 1fr)',
    gap: 7,
  },

  modeButton: {
    minHeight: 54,
    border: '1px solid #e0e4eb',
    borderRadius: 10,
    background: '#fff',
    color: '#5c6677',
    cursor: 'pointer',
    fontWeight: 650,
    fontSize: 12,
    display: 'flex',
    flexDirection: 'column',
    alignItems: 'center',
    justifyContent: 'center',
    gap: 3,
  },

  modeButtonActive: {
    border: '1.5px solid #635bff',
    background: '#f1f0ff',
    color: '#5149d8',
  },

  warning: {
    padding: '11px 12px',
    marginBottom: 16,
    borderRadius: 10,
    background: '#fff8e7',
    border: '1px solid #f6dfaa',
    color: '#8a6414',
    fontSize: 11,
    lineHeight: 1.5,
  },

  verifyPanel: {
    marginBottom: 20,
    padding: 14,
    borderRadius: 14,
    background: '#fffaf0',
    border: '1px solid #f1dfad',
  },

  verifyTitle: {
    fontSize: 15,
    fontWeight: 800,
    color: '#805d13',
  },

  verifyDesc: {
    marginTop: 5,
    marginBottom: 12,
    fontSize: 11,
    color: '#8a7a5c',
    lineHeight: 1.5,
  },

  verifyItem: {
    background: '#fff',
    border: '1px solid #eee5d1',
    borderRadius: 10,
    padding: 10,
    marginBottom: 8,
  },

  placeTitle: {
    display: 'flex',
    alignItems: 'center',
    gap: 7,
    fontSize: 13,
  },

  numberBadge: {
    width: 22,
    height: 22,
    borderRadius: '50%',
    background: '#635bff',
    color: '#fff',
    display: 'inline-flex',
    alignItems: 'center',
    justifyContent: 'center',
    fontSize: 11,
    fontWeight: 800,
  },

  taskText: {
    fontSize: 11,
    color: '#8b94a3',
  },

  smallText: {
    fontSize: 10,
    color: '#8b94a3',
    marginTop: 6,
  },

  candidateBox: {
    marginTop: 9,
    background: '#f8f9fc',
    border: '1px solid #e5e8ef',
    borderRadius: 9,
    padding: 8,
  },

  candidateHeader: {
    display: 'flex',
    justifyContent: 'space-between',
    alignItems: 'center',
    fontSize: 11,
    color: '#596579',
    marginBottom: 6,
  },

  candidateButton: {
    display: 'block',
    width: '100%',
    textAlign: 'left',
    padding: 9,
    marginBottom: 5,
    border: '1px solid #e0e4eb',
    borderRadius: 8,
    cursor: 'pointer',
    background: '#fff',
  },

  categoryText: {
    fontSize: 10,
    color: '#635bff',
    marginTop: 3,
  },

  addressText: {
    fontSize: 10,
    color: '#7b8494',
    lineHeight: 1.4,
    marginTop: 3,
  },

  textButton: {
    border: 'none',
    background: 'transparent',
    color: '#8b94a3',
    cursor: 'pointer',
    fontSize: 10,
  },

  routeTabs: {
    display: 'grid',
    gridTemplateColumns: 'repeat(3, 1fr)',
    gap: 7,
  },

  routeTab: {
    minWidth: 0,
    textAlign: 'left',
    border: '1px solid #e0e4eb',
    background: '#fff',
    borderRadius: 11,
    padding: '11px 9px',
    cursor: 'pointer',
    color: '#485366',
  },

  routeTabActive: {
    border: '1.5px solid #635bff',
    background: '#f5f4ff',
    color: '#4d45cc',
    boxShadow: '0 4px 12px rgba(99, 91, 255, 0.08)',
  },

  routeTabTop: {
    display: 'flex',
    alignItems: 'center',
    gap: 5,
    fontSize: 12,
    fontWeight: 800,
  },

  routeIcon: {
    fontSize: 14,
  },

  dot: {
    width: 15,
    height: 15,
    borderRadius: '50%',
    background: '#ef4444',
    color: '#fff',
    display: 'inline-flex',
    alignItems: 'center',
    justifyContent: 'center',
    fontSize: 9,
    marginLeft: 'auto',
  },

  routeTabStatus: {
    fontSize: 9,
    color: '#99a2b1',
    marginTop: 7,
    lineHeight: 1.35,
  },

  routeHint: {
    marginTop: 8,
    fontSize: 10,
    color: '#8a93a2',
    lineHeight: 1.5,
  },

  priorityAlert: {
    marginTop: 10,
    padding: 10,
    borderRadius: 10,
    background: '#fff5f1',
    border: '1px solid #ffd8cb',
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'space-between',
    gap: 8,
    fontSize: 11,
    color: '#914b39',
  },

  priorityAlertText: {
    display: 'block',
    marginTop: 3,
  },

  priorityButton: {
    flexShrink: 0,
    border: 'none',
    borderRadius: 8,
    padding: '8px 10px',
    background: '#ef6a4c',
    color: '#fff',
    fontWeight: 750,
    cursor: 'pointer',
    fontSize: 11,
  },

  resultCard: {
    background: '#172033',
    color: '#fff',
    borderRadius: 16,
    padding: 16,
    marginBottom: 20,
    boxShadow: '0 10px 25px rgba(20, 30, 55, 0.15)',
  },

  resultHeader: {
    display: 'flex',
    alignItems: 'start',
    justifyContent: 'space-between',
    gap: 10,
  },

  resultEyebrow: {
    fontSize: 10,
    color: '#aab3c5',
    marginBottom: 4,
  },

  resultTitle: {
    margin: 0,
    fontSize: 18,
    letterSpacing: -0.5,
  },

  loadingPill: {
    background: '#33405a',
    color: '#dbe1ed',
    borderRadius: 20,
    padding: '5px 8px',
    fontSize: 9,
  },

  metricGrid: {
    display: 'grid',
    gridTemplateColumns: '1fr 1fr',
    gap: 8,
    marginTop: 14,
  },

  metric: {
    padding: 11,
    borderRadius: 10,
    background: '#232e44',
    display: 'flex',
    flexDirection: 'column',
    gap: 4,
  },

  metricSpan: {
    color: '#9da7ba',
  },

  resultDescription: {
    marginTop: 10,
    fontSize: 10,
    color: '#aeb7c8',
    lineHeight: 1.45,
  },

  estimatedNotice: {
    marginTop: 8,
    fontSize: 9,
    color: '#f5d58a',
  },

  timeline: {
    background: '#fff',
    borderRadius: 14,
    border: '1px solid #e6e9ef',
    padding: '10px 12px',
  },

  timelineItem: {
    position: 'relative',
    display: 'flex',
    gap: 10,
    minHeight: 68,
  },

  timelineMarker: {
    width: 28,
    height: 28,
    flexShrink: 0,
    borderRadius: '50%',
    background: '#635bff',
    color: '#fff',
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    fontSize: 11,
    fontWeight: 800,
    zIndex: 2,
  },

  timelineLine: {
    position: 'absolute',
    left: 13,
    top: 28,
    bottom: -2,
    width: 2,
    background: '#e4e7ee',
  },

  timelineContent: {
    flex: 1,
    minWidth: 0,
    paddingBottom: 12,
  },

  timelineTop: {
    display: 'flex',
    alignItems: 'center',
    gap: 7,
    minHeight: 28,
  },

  priorityPill: {
    padding: '3px 6px',
    borderRadius: 20,
    background: '#f0f2f6',
    color: '#788294',
    fontSize: 9,
  },

  priorityHigh: {
    background: '#fff0ed',
    color: '#d94c32',
  },

  priorityRow: {
    marginTop: 6,
    display: 'flex',
    alignItems: 'center',
    gap: 6,
    fontSize: 10,
    color: '#8b94a3',
  },

  prioritySelect: {
    border: '1px solid #dfe3ea',
    borderRadius: 6,
    padding: '4px 5px',
    background: '#fff',
    color: '#4d5666',
    fontSize: 10,
  },

  // --- 출발 시각 입력 (03) ---
  startTimeRow: {
    display: 'flex',
    alignItems: 'center',
    flexWrap: 'wrap',
    gap: 8,
  },

  startTimeLabel: {
    fontSize: 12,
    fontWeight: 600,
    color: '#4d5666',
  },

  timeSelectGroup: {
    display: 'flex',
    alignItems: 'center',
    gap: 5,
  },

  timeSelect: {
    height: 34,
    boxSizing: 'border-box',
    padding: '0 8px',
    border: '1px solid #d9dee8',
    borderRadius: 8,
    outline: 'none',
    background: '#fff',
    color: '#172033',
    fontSize: 12,
    cursor: 'pointer',
  },

  timeClearButton: {
    border: 'none',
    background: 'transparent',
    color: '#98a2b3',
    fontSize: 11,
    cursor: 'pointer',
    padding: 2,
  },

  currentTimeCheck: {
    display: 'flex',
    alignItems: 'center',
    gap: 6,
    fontSize: 12,
    color: '#4b5563',
    marginTop: 8,
    cursor: 'pointer',
  },

  startTimeHint: {
    fontSize: 10,
    color: '#98a2b3',
    marginTop: 6,
    marginBottom: 10,
    lineHeight: 1.45,
  },

  // --- 장소별 체류시간 / 약속시각 (장소 확인) ---
  timeFieldRow: {
    display: 'flex',
    flexDirection: 'column',
    gap: 10,
    marginTop: 9,
  },

  timeField: {
    minWidth: 0,
    display: 'flex',
    flexDirection: 'column',
    gap: 4,
  },

  apptSelectGroup: {
    display: 'flex',
    alignItems: 'center',
    gap: 5,
    flexWrap: 'wrap',
  },

  apptSelect: {
    height: 32,
    boxSizing: 'border-box',
    padding: '0 6px',
    border: '1px solid #d9dee8',
    borderRadius: 8,
    outline: 'none',
    background: '#fff',
    color: '#172033',
    fontSize: 12,
    cursor: 'pointer',
  },

  timeFieldLabel: {
    fontSize: 10,
    color: '#8b94a3',
    display: 'flex',
    alignItems: 'center',
    gap: 5,
  },

  aiBadge: {
    fontSize: 9,
    fontWeight: 600,
    color: '#5b3fd6',
    background: '#efeafc',
    borderRadius: 5,
    padding: '1px 5px',
    whiteSpace: 'nowrap',
  },

  durationInputWrap: {
    display: 'flex',
    alignItems: 'center',
    gap: 4,
  },

  durationInput: {
    width: 60,
    height: 30,
    boxSizing: 'border-box',
    padding: '0 8px',
    border: '1px solid #d9dee8',
    borderRadius: 7,
    outline: 'none',
    fontSize: 12,
  },

  durationUnit: {
    fontSize: 11,
    color: '#8b94a3',
  },

  // --- 요약 카드(다크) 안의 시간 정보 ---
  scheduleSummary: {
    marginTop: 12,
    paddingTop: 12,
    borderTop: '1px solid rgba(255, 255, 255, 0.1)',
  },

  scheduleSummaryText: {
    fontSize: 12,
    color: '#cfd6e4',
    lineHeight: 1.5,
  },

  recommendRow: {
    marginTop: 8,
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'space-between',
    gap: 10,
    flexWrap: 'wrap',
  },

  recommendHint: {
    fontSize: 11,
    color: '#9aa4b8',
    lineHeight: 1.4,
    flex: 1,
    minWidth: 0,
  },

  recommendApplyBtn: {
    flexShrink: 0,
    border: '1px solid #635BFF',
    background: 'rgba(99, 91, 255, 0.15)',
    color: '#c3bdff',
    fontSize: 11,
    fontWeight: 600,
    borderRadius: 7,
    padding: '5px 10px',
    cursor: 'pointer',
  },

  violationNotice: {
    marginTop: 10,
    padding: '8px 10px',
    borderRadius: 8,
    background: 'rgba(217, 76, 50, 0.16)',
    color: '#ffb4a2',
    fontSize: 11,
    lineHeight: 1.45,
  },

  // --- 방문 순서 타임라인의 도착시각 ---
  arrivalBadge: {
    marginLeft: 'auto',
    padding: '3px 7px',
    borderRadius: 20,
    background: '#eef0f6',
    color: '#5a647a',
    fontSize: 10,
    fontWeight: 700,
    whiteSpace: 'nowrap',
  },

  arrivalBadgeLate: {
    background: '#fff0ed',
    color: '#d94c32',
  },

  scheduleLine: {
    marginTop: 5,
    display: 'flex',
    alignItems: 'center',
    gap: 8,
    fontSize: 10,
    color: '#8b94a3',
  },

  apptTag: {
    padding: '2px 6px',
    borderRadius: 5,
    background: '#eef1f8',
    color: '#5b6cc0',
    fontSize: 9,
    fontWeight: 700,
  },

  apptTagLate: {
    padding: '2px 6px',
    borderRadius: 5,
    background: '#fff0ed',
    color: '#d94c32',
    fontSize: 9,
    fontWeight: 700,
  },

  mapArea: {
    position: 'relative',
    flex: 1,
    minWidth: 0,
    height: '100vh',
    background: '#e8ebf0',
  },

  map: {
    width: '100%',
    height: '100%',
  },

  mapOverlay: {
    position: 'absolute',
    right: 22,
    top: 20,
    background: 'rgba(255,255,255,0.94)',
    backdropFilter: 'blur(10px)',
    border: '1px solid rgba(220,224,232,0.9)',
    borderRadius: 12,
    padding: '10px 13px',
    boxShadow: '0 5px 18px rgba(30,40,60,0.08)',
  },

  mapOverlayTitle: {
    fontWeight: 850,
    fontSize: 13,
  },

  mapOverlayText: {
    marginTop: 2,
    color: '#7d8797',
    fontSize: 10,
  },
};

export default App;
