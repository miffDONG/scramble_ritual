# 웹 튜너에서 변경 가능한 옵션

웹 UI(`scripts\start.bat` → http://localhost:8765) 오른쪽 패널의 모든
옵션 목록. 슬라이더를 움직이면 즉시 카메라/영상 처리에 반영되고,
**[config.json 저장]** 을 눌러야 `tracker/config.json`에 남아서 CLI
(`python -m tracker.morse_run`)에도 적용된다.

옵션의 단일 소스는 `webui/schema.py`이며, 실제 기본값은
`tracker/morse.py`의 `DEFAULTS` / `tracker/config.json`이다.

> 여기 나오는 용어(aspect, circularity, extent, solidity, 면적/대비
> 게이트 등)의 정의와 현재 글리프 필터링 조건은 **`docs/glossary.md`** 참고.

## 종속 옵션 (보이는 것 = 지금 동작하는 것)

`webui/schema.py`의 `show_when`이 붙은 옵션은 **조건이 맞을 때만** 패널에
나타나고 조작 가능해진다. 추출 방식은 셋 중 하나만 동작하므로 고른 방식의
파라미터만 보이고, 전처리 필터의 파라미터는 그 필터를 켜야 보인다. 동작하지
않는 컨트롤을 남겨두면 "만졌는데 아무 일도 안 일어난다"는 오해가 되기
때문이다. 자세한 표는 **`docs/preprocessing-guide.md`** 참고.

## 입력 전처리 (camera / video / image 전용)

시뮬레이션·오브제 소스는 비교 기준 유지를 위해 전처리를 건너뛴다.
적용 순서는 `Autocontrast → Gamma → Bilateral → CLAHE → Unsharp`.

| 옵션 | 기본값 | 나타나는 조건 |
| --- | --- | --- |
| 전처리 전체 ON `preprocess_enabled` | 끔 | 항상 |
| Percentile Autocontrast `autocontrast_enabled` (+ 하위/상위 %) | 끔 | 전체 ON일 때 (파라미터는 필터도 켰을 때) |
| Gamma `gamma_enabled` (+ `gamma_value`) | 끔 / 1.0 | 〃 |
| Bilateral `bilateral_enabled` (+ `bilateral_d`, `bilateral_sigma`) | 끔 | 〃 |
| CLAHE `clahe_enabled` (+ `clahe_clip_limit`, `clahe_tile_grid`) | 끔 | 〃 |
| Unsharp `unsharp_enabled` (+ `unsharp_amount`) | 끔 | 〃 |

## Glyph 추출 방식 (택 1)

| 옵션 | 기본값 | 나타나는 조건 |
| --- | --- | --- |
| 추출 방식 `extraction_mode` | contour | 항상 |
| 선 overlap 기준 `method_line_score_min` | 0.22 | hough 또는 morphology |
| `hough_threshold` / `hough_min_line_length` / `hough_max_line_gap` / `hough_line_thickness` | 10 / 6 / 3 / 3 | hough |
| `morph_line_length` / `morph_line_thickness` | 9 / 1 | morphology |

방식이 각 blob을 선(1)/점(0) 중 무엇으로 판정했는지는 **`추출 진단` 탭**에서
영상 위 사각형 색과 숫자로 확인한다.

## 이진화 (마크 분리)

| 옵션 (config 키) | 기본값 | 설명 |
| --- | --- | --- |
| 어두운 마크 모드 `dark_on_light` | 켬 | 켬 = 밝은 배경 위 어두운 마크. 끔 = 어두운 배경 위 밝은 마크. **현장에서 각인이 배경보다 밝게 보이면 끄고, 어둡게 보이면 켠 채로 둔다.** |
| 조명 평탄화 `normalize_illumination` | 끔 | 큰 블러로 추정한 배경 조도로 나눠 화면을 균일하게 만든 뒤 이진화. 라이트 패널 중앙/가장자리 밝기 차가 클 때 켠다 |
| 평탄화 커널 비율 `normalize_kernel_frac` | 0.25 | 배경 추정 블러 크기(짧은 변 대비). 클수록 완만한 변화만 제거 |
| 임계값 모드 `threshold_mode` | percentile | `percentile` 전역 히스토그램 꼬리 / `adaptive` 국소 평균(백라이트 권장) / `otsu` 자동 / `fixed` 고정 |
| 고정 임계값 `threshold` | 0 | 1 이상이면 모드를 무시하고 이 밝기로 자름 (0 = 미사용) |
| 퍼센타일 `threshold_percentile` | 2.0 | percentile 모드: 마크가 차지하는 히스토그램 꼬리 % |
| adaptive 블록(px) `adaptive_block_px` | 0(자동) | adaptive 모드: 국소 평균 창 크기. 마크 크기의 2~4배 정도 |
| adaptive 오프셋 `adaptive_c` | 12 | adaptive 모드: 국소 평균 대비 요구되는 밝기 차. 내리면 흐린 각인까지 잡히지만 노이즈 증가 |
| 임계값 하한/상한 `threshold_clamp` | [40, 120] | percentile 모드에서 임계값이 움직일 수 있는 범위. **백라이트처럼 배경이 아주 밝으면 상한을 200까지 올려야 한다** |
| 블러(px) `blur_px` | 3 | 이진화 전 가우시안 블러 (홀수) |

## 형태학 (마스크 정리)

| 옵션 | 기본값 | 설명 |
| --- | --- | --- |
| 열림(px) `morph_open_px` | 1 | 점 노이즈 제거. 점(dot)이 사라지면 낮춘다 |
| 닫힘(px) `morph_close_px` | 2 | 마크 내부 구멍 메움. 각인 면이 갈라져 보이면 올린다 |

## 블롭 필터 (글리프 후보 거르기)

| 옵션 | 기본값 | 설명 |
| --- | --- | --- |
| 최소 면적(px) `min_glyph_area_px` | 45 | 이보다 작은 블롭 무시. 카메라가 멀면 25까지 낮춘다 |
| 최소 면적 비율 `min_glyph_area_frac` | 2e-5 | 해상도 스케일링용 최소 면적. 위 값과 둘 중 큰 쪽 적용 |
| 최대 면적 비율 `max_glyph_area_frac` | 0.03 | 화면 대비 이보다 큰 블롭 무시 (판 테두리, 손 등) |
| 최소 대비 `min_blob_contrast` | 50 | 배경 중앙값과 블롭 평균의 밝기 차. 각인 대비가 약한 현장에서는 15~25로 낮춘다 |

## 시작 마커 (앵커)

| 옵션 | 기본값 | 설명 |
| --- | --- | --- |
| 시작 마커 필요 `require_start` | 켬 | **켬** = 시작 마커(■/★/▲)를 찾고 그 뒤 심볼 체인을 읽음(실물 오브제 경로). **끔** = 앵커 없이 collinear 블롭 8개만으로 읽음. 시작 마커가 없는 카드/영상으로 마크 판독 자체를 먼저 테스트할 때. 끄면 읽기 방향이 관례로 고정되고(세로 카드는 위→아래), 코드북 매칭 시 역방향도 시도함 |
| 크기 배율 `square_area_ratio` | 1.8 | 시작 마커가 데이터 심볼 중앙 면적보다 커야 하는 배율 |
| 최대 종횡비 `square_aspect_max` | 1.9 | 앵커 후보 최대 가로세로비 |
| 최소 채움비 `square_min_extent` | 0.5 | 바운딩박스 대비 채움 비율. **★ 앵커는 0.35로 낮춘다** |
| 최대 꼭짓점 `square_max_vertices` | 6 | 다각형 근사 꼭짓점 상한. ■/▲는 6이면 되고 **★는 12로 올린다** |

## 점/선 분류

| 옵션 | 기본값 | 설명 |
| --- | --- | --- |
| 점 최대 종횡비 `compact_aspect_max` | 1.55 | 이보다 동그라면 점(0) 후보 |
| 선 최소 종횡비 `dash_aspect_min` | 1.85 | 이보다 길쭉하면 선(1) 후보 |
| 축방향 길이비 `dash_ratio_min` | 1.7 | 카드 축 기준 (진행/수직) 길이비가 이 이상이면 선(1) 판독 |

## 체인 기하 (카드 조립)

| 옵션 | 기본값 | 설명 |
| --- | --- | --- |
| 수직 허용폭 `chain_perp_tol` | 1.4 | 축에서 벗어날 수 있는 폭(√중앙면적 배수) |
| 최대 간격비 `chain_gap_ratio_max` | 2.4 | 심볼 간 최대/중앙 간격비. 옆 카드와 이어붙는 오류 차단 |
| 첫 간격비 `chain_first_gap_ratio_max` | 3.0 | 앵커→첫 심볼 허용 간격 |
| 방향 후보 수 `chain_neighbors` | 12 | 앵커당 체인 방향 가설 수 |
| 최대 카드 수 `max_cards` | 20 | 한 프레임에서 읽는 카드(오브제) 최대 개수. 테이블 정원만큼 잡는다. 이 값을 넘는 카드는 점수 낮은 순으로 버려지므로, 20개를 올릴 거면 20 이상이어야 한다 |

## 디코드

| 옵션 | 기본값 | 설명 |
| --- | --- | --- |
| 디코드 모드 `decode_mode` | codebook | `codebook` = 해밍거리 3 보호 20-ID 코드북 / `binary` = 8비트 원시값 |
| 심볼 수 `slots` | 8 | 앵커 뒤 데이터 심볼 개수 |
| 코드 수 `code_count` | 20 | 코드북 ID 개수 |
| 오류 정정 비트 `correction_distance` | 1 | 이 거리 안의 유일한 코드로 정정 |

## 실행 / 안정화 (웹 UI 전용, `config.json`의 `webui` 섹션)

| 옵션 | 기본값 | 설명 |
| --- | --- | --- |
| 처리 해상도 `proc_max_dim` | 1280 | 긴 변이 이 값을 넘으면 축소 후 처리. 인식이 아깝게 끊기면 1920으로 |
| 스트림 화질 `jpeg_quality` | 80 | 브라우저 MJPEG 품질 |
| 전/후 분할 `compare_split` | 0.5 | `원본 \| 전처리` 뷰의 세로 분할 위치. 0=전부 전처리, 1=전부 원본 (뷰 위 슬라이더) |
| 시간축 안정화 `stabilize` | 켬 | 프레임 간 비트 다수결 투표 (`tracker/stability.py`) |
| 투표 창 `stab_window` | 12 | 다수결에 참여하는 최근 프레임 수 |
| 최소 관측 수 `stab_min_votes` | 3 | 이 프레임 이상 보인 카드만 안정 ID 보고 |
| 유지 프레임 `stab_max_missed` | 10 | 카드가 가려져도 트랙을 유지하는 프레임 수 |

## 테이블 영역 (ROI, `config.json`의 `webui.roi`)

`roi`: 정규화 `[x, y, w, h]` (0~1) 또는 `null`. 웹 뷰에서 [테이블 영역
지정]을 켜고 드래그하면 설정된다. 이 사각형 밖은 배경 극성으로 채워져
검출·임계값 계산에서 제외되고, 결과도 ROI 안으로 필터된다. 정규화라
해상도가 달라도 그대로 적용되며 config 프로파일에 저장된다.

## OSC 송신 (웹 UI 전용 섹션, `config.json`의 `webui` 섹션)

| 옵션 | 기본값 | 설명 |
| --- | --- | --- |
| `osc_enabled` | 끔 | 안정화 ID를 OSC로 송신 |
| `osc_host` / `osc_port` | 127.0.0.1 / 9000 | 수신 측 (TouchDesigner 등) |
| `osc_prefix` | /scramble | 어드레스 프리픽스. `/count`, `/morse (id bits x y angle status)`, `/pair (i j dist relTilt)` |

## 상황 축 (조합 선택)

프리셋(여러 값을 한 번에 묶는 방식) 대신, **서로 독립적인 축**을 각각
드롭다운으로 골라 조합한다. 상황은 매번 하나가 아니기 때문에(예: 백라이트
+ 밝은 각인 + 무앵커) 축을 따로따로 테스트/조합할 수 있어야 한다. 각 축이
건드리는 config 키는 **축끼리 겹치지 않으므로** 어떤 조합이든 서로를
덮어쓰지 않는다. 축을 고르면 바뀌는 키/값이 `▸` 줄에 표시되고, 해당 옵션
슬라이더가 잠깐 하이라이트되어 무엇이 바뀌었는지 눈으로 확인할 수 있다.

| 축 | 선택지 | 담당 config 키 |
| --- | --- | --- |
| 앵커 | 앵커 사용 / 무앵커 (마크만) | `require_start` |
| 각인 색상 | 검정 마크 / 하양 마크(밝은 각인) | `dark_on_light` |
| 조명 | 상부 조명(균일) / 백라이트(불균일·손그림자) | `threshold_mode`, `threshold`, `normalize_illumination` |
| 마크 감도 | 보통 마크 / 작은·흐린 마크 | `min_glyph_area_px`, `min_blob_contrast`, `blur_px` |
| 체인 관용도 | 엄격 / 관대(마크 누락·휨 허용) | `chain_gap_ratio_max`, `chain_perp_tol` |

현재 config가 어떤 축 선택지와도 정확히 일치하지 않으면(수동으로 슬라이더를
만졌을 때) 그 축은 **"— 수동 (혼합)"** 으로 표시된다.

현장 도착 후 순서: **조명** 축을 상부/백라이트로 번갈아 → **마스크 탭**에서
마크가 하얗게 분리되는 쪽 선택 → **각인 색상**으로 극성 맞춤 → **마크
감도/체인 관용도**로 미세조정 → **이름으로 저장**.
