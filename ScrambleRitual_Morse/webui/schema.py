"""
Option schema for the live-tuning web UI.

Single source of truth for what the UI shows: every entry maps one
tracker/morse.py DEFAULTS key (or one runtime key) to a widget. The docs
in docs/options.md mirror this list.

`show_when` makes an option DEPENDENT: the UI shows and enables it only while
every listed key holds one of the listed values. Options that do nothing in
the current state must not be reachable — a Hough slider sitting there while
Morphology is selected reads as a knob that works, and moving it changes
nothing. The value may be a scalar or a list of accepted values, and its key
may live in either scope (keys are unique across cfg and runtime).
"""

_HOUGH = {"extraction_mode": "hough"}
_MORPH = {"extraction_mode": "morphology"}
_LINE_MAP = {"extraction_mode": ["hough", "morphology"]}
_PP = {"preprocess_enabled": True}


def _pp_filter(key):
    """Preprocessing sub-parameter: needs the master switch AND its filter."""
    return dict(_PP, **{key: True})


# kind: bool | int | float | select
# scope: morse (detection cfg) | runtime (pipeline-level)
SCHEMA = [
    # Input contrast lab (real camera/video/image sources only). Every stage
    # is gated on its own switch so the panel only ever shows live knobs.
    dict(key="preprocess_enabled", scope="morse", kind="bool", group="입력 전처리", label="전처리 전체 ON", help="실제 이미지·비디오·카메라 입력에만 적용합니다. 시뮬레이션은 변경하지 않습니다. 끄면 아래 필터가 모두 숨겨집니다."),
    dict(key="autocontrast_enabled", scope="morse", kind="bool", group="입력 전처리", label="Percentile Autocontrast", show_when=_PP, help="극단 암부/명부를 제외한 밝기 범위를 0~255로 늘립니다."),
    dict(key="autocontrast_low_pct", scope="morse", kind="float", group="입력 전처리", label="↳ 하위 %", min=0.0, max=10.0, step=0.1, show_when=_pp_filter("autocontrast_enabled"), help="이 percentile 아래를 검정으로 클리핑합니다."),
    dict(key="autocontrast_high_pct", scope="morse", kind="float", group="입력 전처리", label="↳ 상위 %", min=90.0, max=100.0, step=0.1, show_when=_pp_filter("autocontrast_enabled"), help="이 percentile 위를 흰색으로 클리핑합니다."),
    dict(key="gamma_enabled", scope="morse", kind="bool", group="입력 전처리", label="Gamma", show_when=_PP, help="중간 밝기를 비선형 조정합니다."),
    dict(key="gamma_value", scope="morse", kind="float", group="입력 전처리", label="↳ Gamma 값", min=0.3, max=3.0, step=0.05, show_when=_pp_filter("gamma_enabled"), help="1은 변화 없음, 1보다 작으면 밝아지고 크면 어두워집니다."),
    dict(key="bilateral_enabled", scope="morse", kind="bool", group="입력 전처리", label="Bilateral 노이즈 제거", show_when=_PP, help="점/대시 경계를 보존하며 센서 입자를 줄입니다."),
    dict(key="bilateral_d", scope="morse", kind="int", group="입력 전처리", label="↳ 직경", min=1, max=15, step=2, show_when=_pp_filter("bilateral_enabled"), help="작은 glyph에는 3~7부터 시험합니다."),
    dict(key="bilateral_sigma", scope="morse", kind="float", group="입력 전처리", label="↳ sigma", min=1.0, max=100.0, step=1.0, show_when=_pp_filter("bilateral_enabled"), help="색상/공간 평활 강도입니다."),
    dict(key="clahe_enabled", scope="morse", kind="bool", group="입력 전처리", label="CLAHE 국부 대비", show_when=_PP, help="백라이트 띠가 있는 장면에서 작은 모스의 국부 대비를 높입니다."),
    dict(key="clahe_clip_limit", scope="morse", kind="float", group="입력 전처리", label="↳ clip limit", min=0.2, max=8.0, step=0.1, show_when=_pp_filter("clahe_enabled"), help="높을수록 대비와 노이즈가 함께 증가합니다."),
    dict(key="clahe_tile_grid", scope="morse", kind="int", group="입력 전처리", label="↳ tile 수", min=2, max=32, step=1, show_when=_pp_filter("clahe_enabled"), help="작은 모스는 8~16부터 비교합니다."),
    dict(key="unsharp_enabled", scope="morse", kind="bool", group="입력 전처리", label="Unsharp 경계 강화", show_when=_PP, help="점/대시 경계를 강화하지만 아크릴 외곽도 강화할 수 있습니다."),
    dict(key="unsharp_amount", scope="morse", kind="float", group="입력 전처리", label="↳ 강도", min=0.0, max=2.0, step=0.05, show_when=_pp_filter("unsharp_enabled"), help="0은 변화 없음입니다."),

    # One method is active at a time, so one method's parameters are shown at
    # a time — all in this single group, no dead sibling panels.
    dict(key="extraction_mode", scope="morse", kind="select", group="Glyph 추출 방식", label="추출 방식 (택 1)", options=["contour", "hough", "morphology"], help="상호배타적 단일 선택입니다. Contour=기하 특성, Hough=직선 검출, Morphology=방향성 opening으로 선/점을 분리합니다. 고른 방식의 옵션만 아래에 나타납니다."),
    dict(key="method_line_score_min", scope="morse", kind="float", group="Glyph 추출 방식", label="선 overlap 기준", min=0.02, max=0.9, step=0.02, show_when=_LINE_MAP, help="선 마스크가 glyph 면적에서 차지하는 비율이 이 값 이상이면 dash(1). Contour에는 없는 기준입니다."),
    dict(key="hough_threshold", scope="morse", kind="int", group="Glyph 추출 방식", label="Hough vote", min=2, max=80, step=1, show_when=_HOUGH, help="낮출수록 짧고 약한 선을 더 많이 찾습니다."),
    dict(key="hough_min_line_length", scope="morse", kind="int", group="Glyph 추출 방식", label="Hough 최소 선 길이(px)", min=2, max=100, step=1, show_when=_HOUGH, help="작은 dash의 실제 픽셀 길이보다 작게 시작합니다."),
    dict(key="hough_max_line_gap", scope="morse", kind="int", group="Glyph 추출 방식", label="Hough 최대 선 간격(px)", min=0, max=30, step=1, show_when=_HOUGH, help="갈라진 dash를 하나의 선으로 연결하는 허용 간격입니다."),
    dict(key="hough_line_thickness", scope="morse", kind="int", group="Glyph 추출 방식", label="Hough 선 두께(px)", min=1, max=15, step=1, show_when=_HOUGH, help="검출선과 원래 glyph의 overlap을 계산할 마스크 두께입니다."),
    dict(key="morph_line_length", scope="morse", kind="int", group="Glyph 추출 방식", label="Morph 커널 길이(px)", min=3, max=51, step=2, show_when=_MORPH, help="dot 지름보다 크고 dash 길이보다 작게 설정합니다. 너무 작으면 모든 blob이 선으로 남습니다."),
    dict(key="morph_line_thickness", scope="morse", kind="int", group="Glyph 추출 방식", label="Morph 커널 두께(px)", min=1, max=9, step=1, show_when=_MORPH, help="dash 두께에 맞춥니다."),
    # --- 이진화 -------------------------------------------------------
    dict(key="dark_on_light", scope="morse", kind="bool", group="이진화",
         label="어두운 마크 모드",
         help="켬=밝은 배경 위 검은 마크(실내 테스트), 끔=어두운 배경 위 밝은 마크(백라이트 각인)"),
    dict(key="normalize_illumination", scope="morse", kind="bool", group="이진화",
         label="조명 평탄화",
         help="배경 조도를 나눠 균일하게 만든 뒤 이진화. 라이트 패널 밝기가 불균일할 때 켜세요"),
    dict(key="normalize_kernel_frac", scope="morse", kind="float", group="이진화",
         label="평탄화 커널 비율", min=0.05, max=0.5, step=0.01,
         help="배경 추정 블러 크기(화면 짧은 변 대비 비율). 클수록 완만한 조도 변화만 제거"),
    dict(key="threshold_mode", scope="morse", kind="select", group="이진화",
         label="임계값 모드", options=["percentile", "otsu", "adaptive", "fixed"],
         help="percentile=히스토그램 꼬리(권장), otsu=자동 이분, adaptive=국소 평균, fixed=고정값"),
    dict(key="threshold", scope="morse", kind="int", group="이진화",
         label="고정 임계값", min=0, max=255, step=1,
         help="0이면 사용 안 함. 1 이상이면 다른 모드를 무시하고 이 값으로 이진화"),
    dict(key="threshold_percentile", scope="morse", kind="float", group="이진화",
         label="퍼센타일", min=0.2, max=20.0, step=0.2,
         help="마크 픽셀이 차지하는 히스토그램 꼬리 비율(%). 마크가 화면에서 클수록 올리세요"),
    dict(key="adaptive_block_px", scope="morse", kind="int", group="이진화",
         label="adaptive 블록(px)", min=0, max=401, step=10,
         help="국소 평균 창 크기. 0=자동(화면 짧은 변/12). 마크 크기의 2~4배 정도가 적당"),
    dict(key="adaptive_c", scope="morse", kind="int", group="이진화",
         label="adaptive 오프셋", min=1, max=40, step=1,
         help="국소 평균보다 이만큼 어두워야(밝아야) 마크로 인정. 내리면 흐린 각인까지 잡고 노이즈 증가"),
    dict(key="threshold_clamp_lo", scope="morse", kind="int", group="이진화",
         label="임계값 하한", min=0, max=255, step=1,
         help="퍼센타일 모드에서 임계값이 내려갈 수 있는 최소 밝기"),
    dict(key="threshold_clamp_hi", scope="morse", kind="int", group="이진화",
         label="임계값 상한", min=0, max=255, step=1,
         help="퍼센타일 모드에서 임계값이 올라갈 수 있는 최대 밝기"),
    dict(key="blur_px", scope="morse", kind="int", group="이진화",
         label="블러(px)", min=1, max=15, step=2,
         help="이진화 전 가우시안 블러. 노이즈가 많으면 올리고, 마크가 뭉개지면 내리세요"),

    # --- 형태학 -------------------------------------------------------
    dict(key="morph_open_px", scope="morse", kind="int", group="형태학",
         label="열림(px)", min=0, max=9, step=1,
         help="작은 점 노이즈 제거. 점(dot)이 사라지면 내리세요"),
    dict(key="morph_close_px", scope="morse", kind="int", group="형태학",
         label="닫힘(px)", min=0, max=9, step=1,
         help="마크 내부 구멍 메움. 각인 면이 갈라져 보일 때 올리세요"),

    # --- 형태 필터 (노이즈 판별의 1차 기준, 스케일 불변) ----------------
    dict(key="min_solidity", scope="morse", kind="float", group="형태 필터",
         label="최소 solidity", min=0.0, max=1.0, step=0.02,
         help="면적/볼록껍질. 볼록하게 꽉 찬 정도. 진짜 마크≈0.9+, 손가락 가장자리≈0.2~0.6. "
              "★ 별 앵커는 오목해서 낮으니(≈0.5) 별을 쓰면 이 값을 내리세요"),
    dict(key="min_rectangularity", scope="morse", kind="float", group="형태 필터",
         label="최소 rectangularity", min=0.0, max=1.0, step=0.02,
         help="면적/최소회전사각형. 기울어짐 무관하게 사각을 채우는 정도. 진짜 선≈0.6+, "
              "얇게 구불거리는 줄무늬≈0.2~0.5. 세모 앵커는 ≈0.5이니 0.45 이하로 두세요"),

    # --- 형태 분류 (5형태 식별: 네모/세모/별/원/선) -------------------
    dict(key="shape_classify", scope="morse", kind="bool", group="형태 분류",
         label="형태 분류 사용",
         help="켬=기술자로 5형태를 구분(원→0, 선→1, 네모/세모/별→시작 마커). 26.07.20 "
              "오브제처럼 시작 마커가 데이터와 비슷한 크기여도 모양으로 앵커를 찾음. "
              "끔=예전처럼 '길쭉함'만으로 점/선 판정(네모가 원처럼 0으로 읽힘). "
              "켤 때는 require_start(시작 마커 필요)도 켜세요"),
    dict(key="star_solidity_max", scope="morse", kind="float", group="형태 분류",
         label="별 solidity 상한", min=0.3, max=0.9, step=0.02,
         help="이보다 solidity가 낮으면 별(★)로 판정. 별은 오목해서 낮음(≈0.6)"),
    dict(key="triangle_max_vertices", scope="morse", kind="int", group="형태 분류",
         label="세모 꼭짓점 상한", min=3, max=5, step=1,
         help="꼭짓점 수가 이 이하면 세모(▲). 세모=3개(블러에도 안정적)"),
    dict(key="circle_min_vertices", scope="morse", kind="int", group="형태 분류",
         label="원 꼭짓점 하한", min=5, max=10, step=1,
         help="꼭짓점 수가 이 이상이면 원(●), 그 사이(4~5)면 네모(■). "
              "네모=4개, 원≈8개. 별은 solidity로 먼저 걸러짐"),

    # --- 블롭 필터 (크기/대비) ----------------------------------------
    dict(key="min_glyph_area_px", scope="morse", kind="int", group="블롭 필터",
         label="최소 면적(px)", min=2, max=800, step=1,
         help="이보다 작은 블롭은 무시. 형태 필터가 1차라 이건 낮게 둬도 됨(작게 보이는 마커 대비)"),
    dict(key="min_glyph_area_frac", scope="morse", kind="float", group="블롭 필터",
         label="최소 면적 비율", min=0.0, max=0.0005, step=0.00001,
         help="해상도 대비 최소 면적(프레임 픽셀수 x 비율). 최소 면적(px)과 둘 중 큰 값이 실제 하한. "
              "지금 몇 px인지는 영상 아래 '면적' 칩에서 확인. 절대 px만 쓰려면 0으로"),
    dict(key="max_glyph_area_px", scope="morse", kind="int", group="블롭 필터",
         label="최대 면적(px)", min=0, max=60000, step=500,
         help="이보다 큰 블롭은 무시(손·판 테두리 컷). 0=끔(비율 사용). "
              "설정하면 이 절대값이 우선. 고정 카메라 전시에 권장(처리 해상도 proc_max_dim 기준)"),
    dict(key="max_glyph_area_frac", scope="morse", kind="float", group="블롭 필터",
         label="최대 면적 비율", min=0.005, max=0.2, step=0.005,
         help="화면 대비 이 비율보다 큰 블롭은 무시. 최대 면적(px)이 0일 때만 사용. "
              "실제 상한 px은 영상 아래 '면적' 칩에서 확인"),
    dict(key="min_blob_contrast", scope="morse", kind="int", group="블롭 필터",
         label="최소 대비", min=0, max=120, step=5,
         help="배경 중앙값과 블롭 평균의 밝기 차 최소값. 흐린 얼룩을 걸러냄. 각인 대비가 약하면 내리세요"),

    # --- 시작 마커 ----------------------------------------------------
    dict(key="require_start", scope="morse", kind="bool", group="시작 마커",
         label="시작 마커 필요",
         help="켬=시작 마커(■/★/▲)를 찾고 그 뒤 심볼 체인을 읽음(실물 오브제). "
              "끔=앵커 없이 collinear 블롭 8개만으로 읽음. 시작 마커 없는 카드/영상으로 "
              "마크 판독 자체를 먼저 테스트할 때. 방향은 관례로 고정(코드북 매칭 시 역방향도 시도)"),
    dict(key="square_area_ratio", scope="morse", kind="float", group="시작 마커",
         label="크기 배율", min=1.0, max=4.0, step=0.1,
         help="시작 마커가 데이터 심볼보다 커야 하는 배율"),
    dict(key="square_aspect_max", scope="morse", kind="float", group="시작 마커",
         label="최대 종횡비", min=1.0, max=3.0, step=0.05,
         help="시작 마커 후보의 최대 가로세로 비율"),
    dict(key="square_min_extent", scope="morse", kind="float", group="시작 마커",
         label="최소 채움비", min=0.2, max=0.9, step=0.05,
         help="바운딩 박스 대비 채워진 비율. 별(★) 앵커는 0.35 정도로 내려야 함"),
    dict(key="square_max_vertices", scope="morse", kind="int", group="시작 마커",
         label="최대 꼭짓점", min=3, max=16, step=1,
         help="다각형 근사 꼭짓점 수 상한. ■/▲=6이면 충분, ★ 앵커는 12로 올리세요"),

    # --- 점/선 분류 ---------------------------------------------------
    dict(key="compact_aspect_max", scope="morse", kind="float", group="점/선 분류",
         label="점 최대 종횡비", min=1.0, max=2.5, step=0.05,
         help="이보다 동그란 블롭은 점(0) 후보"),
    dict(key="dash_aspect_min", scope="morse", kind="float", group="점/선 분류",
         label="선 최소 종횡비", min=1.2, max=4.0, step=0.05,
         help="이보다 길쭉한 블롭은 선(1) 후보"),
    dict(key="dash_ratio_min", scope="morse", kind="float", group="점/선 분류",
         label="축방향 길이비", min=1.2, max=3.0, step=0.05,
         help="카드 축 기준 (진행방향 길이/수직 길이)가 이 값 이상이면 선(1)으로 판독"),

    # --- 체인 기하 ----------------------------------------------------
    dict(key="chain_perp_tol", scope="morse", kind="float", group="체인 기하",
         label="수직 허용폭", min=0.5, max=3.0, step=0.1,
         help="체인 축에서 벗어날 수 있는 폭(√중앙면적 배수). 손그림/왜곡이 크면 올리세요"),
    dict(key="chain_gap_ratio_max", scope="morse", kind="float", group="체인 기하",
         label="최대 간격비", min=1.2, max=4.0, step=0.1,
         help="심볼 간 최대 간격/중앙 간격. 낮을수록 다른 카드와 연결되는 것을 강하게 차단"),
    dict(key="chain_first_gap_ratio_max", scope="morse", kind="float", group="체인 기하",
         label="첫 간격비", min=1.2, max=5.0, step=0.1,
         help="시작 마커와 첫 심볼 사이 허용 간격(중앙 간격 배수)"),
    dict(key="chain_neighbors", scope="morse", kind="int", group="체인 기하",
         label="방향 후보 수", min=3, max=24, step=1,
         help="시작 마커당 시도하는 체인 방향 가설 수. 글리프가 많은 장면에서 올리면 안정적"),

    # --- 디코드 -------------------------------------------------------
    dict(key="decode_mode", scope="morse", kind="select", group="디코드",
         label="디코드 모드", options=["codebook", "binary"],
         help="codebook=해밍거리 3 보호 코드북(권장), binary=8비트 원시값"),
    dict(key="slots", scope="morse", kind="int", group="디코드",
         label="심볼 수", min=4, max=12, step=1,
         help="시작 마커 뒤 데이터 심볼 개수. 현재 시안은 8"),
    dict(key="code_count", scope="morse", kind="int", group="디코드",
         label="코드 수", min=2, max=20, step=1,
         help="코드북 ID 개수 (8비트/거리3에서 최대 20)"),
    dict(key="correction_distance", scope="morse", kind="int", group="디코드",
         label="오류 정정 비트", min=0, max=2, step=1,
         help="이 비트 수 이내로 다른 유일한 코드가 있으면 정정 (권장 1)"),

    # --- 실행/안정화 (runtime) ---------------------------------------
    dict(key="proc_max_dim", scope="runtime", kind="int", group="실행",
         label="처리 해상도", min=480, max=2560, step=80,
         help="긴 변이 이 값을 넘으면 축소 후 처리. 낮추면 빠르고, 올리면 작은 마크까지 인식"),
    dict(key="jpeg_quality", scope="runtime", kind="int", group="실행",
         label="스트림 화질", min=40, max=95, step=5,
         help="브라우저 MJPEG 스트림 JPEG 품질"),
    dict(key="stabilize", scope="runtime", kind="bool", group="안정화",
         label="시간축 안정화",
         help="여러 프레임의 비트를 다수결 투표해 흔들림/한 프레임 오독을 걸러낸 안정 ID를 출력"),
    dict(key="stab_window", scope="runtime", kind="int", group="안정화",
         label="투표 창(프레임)", min=3, max=40, step=1,
         help="다수결에 참여하는 최근 프레임 수. 길수록 안정적이지만 카드 교체 반응이 느려짐"),
    dict(key="stab_min_votes", scope="runtime", kind="int", group="안정화",
         label="최소 관측 수", min=1, max=15, step=1,
         help="이 프레임 수 이상 관측된 카드만 안정 ID로 보고"),
    dict(key="stab_max_missed", scope="runtime", kind="int", group="안정화",
         label="유지 프레임", min=1, max=60, step=1,
         help="카드가 안 보여도 트랙을 유지하는 프레임 수 (손이 가리는 상황 대비)"),
]

RUNTIME_DEFAULTS = {
    "proc_max_dim": 1280,
    "jpeg_quality": 80,
    # '원본 | 전처리' 뷰의 세로 분할 위치 (0=전부 전처리, 1=전부 원본).
    # 슬라이더를 끝까지 밀면 그 자체로 전/후 전환이 된다.
    "compare_split": 0.5,
    "stabilize": True,
    "stab_window": 12,
    "stab_min_votes": 3,
    "stab_max_missed": 10,
    # OSC 송신 (웹 UI의 전용 섹션에서 제어; SCHEMA 옵션 패널에는 안 나옴)
    "osc_enabled": False,
    "osc_host": "127.0.0.1",
    "osc_port": 9000,
    "osc_prefix": "/scramble",
    # OSC 값 표현 방식 (메시지 1개 = 오브제 1개, 주소는 /scramble/obj):
    #  "list" = 위치 기반 리스트. 순서는 OSC_OBJ_FIELDS
    #           /scramble/obj  [10001110, 0.35, 0.45, 30.0, 0.66, 0, -5]
    #  "dict" = 키-값을 번갈아 (OSC엔 map 타입이 없어 평평한 배열)
    #           /scramble/obj  ["bits",10001110, "x",0.35, ...]
    #  "json" = 인자 1개 = JSON 문자열. 파싱하면 진짜 {키:값}
    #           /scramble/obj  ["{\"bits\":\"10001110\",\"x\":0.35,...}"]
    "osc_format": "list",
    # 테이블 영역(ROI): 정규화 [x, y, w, h] (0~1) 또는 None. 이 영역 밖은
    # 배경으로 채워 검출에서 제외한다. 뷰에서 드래그로 지정, 프로파일에 저장됨.
    "roi": None,
    # 카메라 노출 제어 (플리커 대응). 백라이트 깜빡임(한국 50Hz→100Hz)과
    # 셔터가 안 맞으면 가로 밴딩이 생김 → 자동노출 끄고 노출을 10ms 배수로.
    "cam_auto_exposure": True,   # 끄면(False) 수동 노출 사용
    "cam_exposure": -6,          # DShow 로그2 초 단위(음수). -7≈1/128s 등
    "cam_powerline": 0,          # 1=50Hz, 2=60Hz, 0=미설정(드라이버 지원 시)
    # 오브제 시뮬레이션: 'objects' 소스에서 검출 없이 사운드/OSC를 개발하기 위한
    # 그라운드 트루스 오브제 목록. 각 원소:
    #   {code_id:int, x:0~1, y:0~1(삼각형 중심), tilt:deg, flip:bool,
    #    shape:"triangle"|"square"|"pentagon"|"hexagon"|"star",
    #    pitch:float|None(세미톤 밴드)}
    "sim_objects": [],
    "sim_spin": 0.0,             # 자동 회전 속도(도/초). 0=끔. 서버가 돌려서
                                 # 브라우저와 무관하게 부드럽게 회전한다
    "sim_detect": True,          # objects 소스에서도 실제 추론을 돌릴지
    # 긴장도(per-object). 거리는 테이블(ROI) 긴 변으로 정규화한다.
    #   tension=1 시작(d_near) = 변끼리 맞닿는 중심거리 = 한 변/sqrt(3) → 오브제
    #     크기에서 유도. object_side_px = 관측되는 삼각형 한 변 길이(px).
    #       0이면 objects 소스는 시뮬 값에서 자동, 그 외 소스는 tension_contact 폴백.
    #       카메라/비디오/이미지는 한 변을 드래그로 측정해 지정한다.
    #   tension=0 기점(d_far) = 긴 변의 절반(정규화 0.5, 고정). 정사각 크롭이면
    #     한 변의 절반, 직사각이면 긴 변의 절반. 겹쳐도 1 유지(0으로 치환 안 함).
    "object_side_px": 0.0,
    "tension_contact": 0.06,     # (폴백) 크기 미지정 시 쓰는 d_near 정규화 거리
    # 각 오브제의 tension = 본인 제외 n-1개와의 거리로 계산한 pair_tension 을 fold.
    # 무대에서 방식을 바꿔 비교할 수 있도록 런타임 옵션으로 둔다.
    "tension_connect": "all",    # 연결 노드: all(n-1) | near(반경 이내) | knn
    "tension_fold": "max",       # 결합: max | avg | min
    "tension_knn": 3,            # connect=knn일 때 최근접 개수
    # near의 연결 반경(테이블 긴 변 정규화). d_far(0.5)와 분리 — 작게 둘수록
    # near가 먼 링크를 실제로 잘라내 all과 차이가 난다. max fold에선 여전히
    # 최근접이 대표라 무의미하니, 차이를 보려면 fold=avg/min과 함께 쓸 것.
    "tension_link_radius": 0.2,
    "show_graph": True,          # 오버레이에 tension 그래프(노드 연결) 표시
}

# 상황 축(AXES): 하나로 묶인 프리셋 대신, 서로 독립적인 축을 각각
# 드롭다운으로 골라 조합한다. 각 축이 건드리는 cfg 키는 축끼리 겹치지
# 않으므로(직교) 어떤 조합이든 서로를 덮어쓰지 않는다. 프런트엔드는 축을
# 고를 때 바뀌는 키/값을 표시하고 해당 옵션 슬라이더를 하이라이트한다.
#
# 축별 담당 키 (겹침 없음):
#   anchor      -> require_start
#   mark_color  -> dark_on_light
#   lighting    -> threshold_mode, threshold, normalize_illumination
#   mark_size   -> min_glyph_area_px, min_blob_contrast, blur_px
#   chain_tol   -> chain_gap_ratio_max, chain_perp_tol
#   shape_mode  -> shape_classify
AXES = [
    {
        "key": "shape_mode", "label": "형태 인식",
        "help": "길쭉함만=점(0)/선(1)만 구분(네모가 원처럼 0으로 읽힘). "
                "5형태=기술자로 네모/세모/별/원/선 구분, 시작 마커를 모양으로 찾음 "
                "(앵커 축도 '앵커 사용'으로)",
        "options": [
            {"value": "elongation", "label": "길쭉함만 (점/선)",
             "cfg": {"shape_classify": False}},
            {"value": "shapes", "label": "5형태 구분 (네모/세모/별/원/선)",
             "cfg": {"shape_classify": True}},
        ],
    },
    {
        "key": "anchor", "label": "앵커",
        "help": "시작 마커(■/★/▲)를 요구할지. 무앵커는 앵커 없이 collinear "
                "블롭 8개만 읽음 — 시작 마커 없는 카드/영상 테스트용",
        "options": [
            {"value": "anchored", "label": "앵커 사용",
             "cfg": {"require_start": True}},
            {"value": "anchorless", "label": "무앵커 (마크만)",
             "cfg": {"require_start": False}},
        ],
    },
    {
        "key": "mark_color", "label": "각인 색상",
        "help": "마크가 배경보다 어두운지(검정 펜) 밝은지(흰색 각인)",
        "options": [
            {"value": "black", "label": "검정 마크",
             "cfg": {"dark_on_light": True}},
            {"value": "white", "label": "하양 마크 (밝은 각인)",
             "cfg": {"dark_on_light": False}},
        ],
    },
    {
        "key": "lighting", "label": "조명",
        "help": "이진화 전략. 상부=균일 배경이라 전역 퍼센타일, 백라이트="
                "불균일/손그림자라 국소(adaptive)",
        "options": [
            {"value": "top", "label": "상부 조명 (균일 배경)",
             "cfg": {"threshold_mode": "percentile", "threshold": 0,
                     "normalize_illumination": False}},
            {"value": "backlit", "label": "백라이트 (불균일·손그림자)",
             "cfg": {"threshold_mode": "adaptive", "threshold": 0,
                     "normalize_illumination": False}},
        ],
    },
    {
        "key": "mark_size", "label": "마크 감도",
        "help": "작은/흐린 마크까지 잡을지. 카메라가 멀거나 각인이 약하면 "
                "'작은/흐린' 쪽",
        "options": [
            {"value": "normal", "label": "보통 마크",
             "cfg": {"min_glyph_area_px": 45, "min_blob_contrast": 50,
                     "blur_px": 3}},
            {"value": "small", "label": "작은/흐린 마크",
             "cfg": {"min_glyph_area_px": 15, "min_blob_contrast": 12,
                     "blur_px": 3}},
        ],
    },
    {
        "key": "chain_tol", "label": "체인 관용도",
        "help": "마크가 하나 빠지거나 카드가 휘어도 체인으로 인정할지. "
                "노이즈 많은 영상은 '관대'",
        "options": [
            {"value": "strict", "label": "엄격",
             "cfg": {"chain_gap_ratio_max": 2.4, "chain_perp_tol": 1.4}},
            {"value": "loose", "label": "관대 (마크 누락·휨 허용)",
             "cfg": {"chain_gap_ratio_max": 3.5, "chain_perp_tol": 2.0}},
        ],
    },
]
