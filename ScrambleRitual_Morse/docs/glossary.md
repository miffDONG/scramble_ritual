# 용어 정리 · 글리프 필터링 조건

이 문서에서 쓰는 `solidity`, `aspect`, `circularity`, `extent` 등은 **직접
만든 말이 아니라 OpenCV의 표준 컨투어(윤곽) 분석 용어**입니다(OpenCV
"Contour Features / Contour Properties" 문서의 개념 그대로). 그래서 용어는
그대로 두고, 여기서 각각의 뜻과 값이 의미하는 바, 그리고 현재 글리프
필터링이 어떤 순서·조건으로 도는지를 정리합니다.

관련 파일: `tracker/morse.py`의 `find_glyphs()`. 값을 웹에서 조절하는
슬라이더 설명은 `docs/options.md`.

---

## 0. 큰 그림 (한 프레임이 글리프가 되기까지)

```text
원본 프레임
  → 회색조 + 블러 (+조명 평탄화)          prepare_gray()
  → 이진화(threshold) → 흑백 마스크         preprocess()   ← "마스크" 탭
  → 흰 덩어리들의 윤곽선 추출               cv2.findContours
  → 각 덩어리의 기술자(descriptor) 계산     ┐
  → 면적/대비 게이트로 노이즈 제거          │ find_glyphs()
  → 남은 것을 형태(네모/점/선)로 분류        ┘  → "글리프"
  → 글리프들을 일직선 체인으로 조립          detect_cards()  ← "오버레이" 탭
```

"글리프(glyph)" = 마스크의 흰 덩어리 중 필터를 통과해 **마커 후보로 인정된 것**.
마스크에 하얗게 보여도 필터에 걸리면 글리프가 아니고 오버레이에도 안 뜹니다.

---

## 1. 기본 용어

| 용어 | 한글 | 정의 | 비고 |
| --- | --- | --- | --- |
| **contour** | 윤곽선 | 마스크에서 흰 덩어리의 **테두리 좌표열** | `cv2.findContours` |
| **area** | 면적 | 윤곽선이 감싸는 픽셀 넓이 | `cv2.contourArea`. 단위 px². **줌에 비례해 커짐(스케일 의존)** |
| **perimeter** | 둘레 | 윤곽선 길이 | `cv2.arcLength` |
| **bounding box (bbox)** | 경계 상자 | 덩어리를 감싸는 **축 정렬(수평/수직) 직사각형** | `cv2.boundingRect` → (x, y, w, h) |
| **minAreaRect** | 최소 회전 사각형 | 덩어리를 감싸는 **가장 작은 (기울어질 수 있는) 직사각형** | 장축·단축 길이와 각도를 줌 |
| **convex hull** | 볼록 껍질 | 덩어리를 감싸는 **가장 작은 볼록 다각형**(고무줄을 씌운 모양) | `cv2.convexHull` |
| **moments / centroid** | 모멘트 / 무게중심 | 면적 가중 평균 위치 (cx, cy) | `cv2.moments` |

---

## 2. 형태 기술자 (descriptor) — 전부 스케일 불변(비율)

아래 값들은 모두 **비율**이라 마커가 줌인/아웃해도 값이 거의 안 바뀝니다.
그래서 절대 픽셀(`area`)보다 형태 판별에 강합니다.

### aspect (종횡비)
- **정의**: minAreaRect의 `장축 / 단축`.
- **의미**: 얼마나 길쭉한가. **1.0 = 정사각/원**, **클수록 = 길쭉한 선**.
- 예: 점 ≈ 1.0~1.3, 선(dash) ≈ 3~60, 네모 ≈ 1.0.
- 코드: `aspect = major / minor` (`morse.py:280`).

### circularity (원형도)
- **정의**: `4π × area / perimeter²`.
- **의미**: 얼마나 완전한 원에 가까운가. **1.0 = 완전한 원**, 낮을수록
  울퉁불퉁하거나 길쭉함.
- 예: 점 ≈ 0.8~1.0, 선 ≈ 0.05~0.5, 손가락 가장자리 ≈ 0.02~0.1.
- 코드: `circularity = 4π·area / peri²` (`morse.py:265`).

### extent (채움도, 축 정렬 기준)
- **정의**: `area / (bbox 넓이)` = 면적 ÷ (w×h).
- **의미**: **축 정렬** 경계 상자를 얼마나 꽉 채우나. 1.0에 가까울수록 꽉 참.
- 주의: **기울어진** 선은 축 정렬 bbox가 커져서 extent가 낮게 나옴(회전에 민감).
- 예: 점 ≈ 0.6~0.7, 네모 ≈ 0.67~0.9(정면), 기운 선 ≈ 0.1~0.4.
- 코드: `extent = area / (w*h)` (`morse.py:266`).

### vertices (꼭짓점 수)
- **정의**: 윤곽선을 **둘레의 4%**(`0.04×perimeter`) 오차로 다각형 근사했을
  때 남는 꼭짓점 개수.
- **의미**: 세모 ≈ 3, 네모 ≈ 4, 별 ≈ 8~10, 원 → 둥글어 값이 들쭉날쭉.
- 근사 오차가 둘레 비율이라 **스케일 불변**.
- 코드: `vertices = len(approxPolyDP(cnt, 0.04*peri))` (`morse.py:264`).

### rect_angle (장축 방향)
- **정의**: minAreaRect 장축이 향하는 각도(도).
- **의미**: 선(dash)이 카드 축과 나란한지 확인하는 데 사용.

---

## 3. 대비 (contrast)

- **정의**: `배경 밝기 중앙값 − 블롭 평균 밝기` (밝은 마크 모드면 부호 반대).
- **의미**: 마크가 배경보다 얼마나 뚜렷이 어두운가(또는 밝은가). 흐린
  얼룩·그림자를 거르는 용도.
- 파라미터: `min_blob_contrast`. 코드: `morse.py:255-262`.

---

## 4. 현재 글리프 필터링 조건 (순서대로)

`find_glyphs()`가 각 윤곽선에 대해 이 순서로 판정합니다.

### 단계 1 — 면적 게이트 (형태 무관, 모든 덩어리 공통)
```text
min_area = max(min_glyph_area_px, min_glyph_area_frac × 프레임_픽셀수)
max_area = (max_glyph_area_px > 0) ? max_glyph_area_px
                                   : 프레임_픽셀수 × max_glyph_area_frac
area < min_area  또는  area > max_area  → 버림
```
- `min_glyph_area_px`: **절대 픽셀 하한**(노이즈 바닥). 형태 게이트(단계 2)가
  1차 필터라, 이건 낮게(예: 2) 둬서 작게 보이는 마커를 살립니다.
- `min_glyph_area_frac`: **해상도 비례 하한**. 위와 둘 중 **큰 값** 적용.
- `max_glyph_area_px`: **절대 픽셀 상한**. 설정하면(>0) **이 값이 우선**.
  고정 카메라 전시(줌아웃만)에서 안정적 — 처리 해상도(`proc_max_dim`) 기준.
- `max_glyph_area_frac`: **화면 대비 상한**. `max_glyph_area_px=0`일 때만 사용.

### 단계 2 — 형태 게이트 (노이즈 판별의 1차 기준, 스케일 불변)
```text
solidity        < min_solidity        → 버림
rectangularity  < min_rectangularity  → 버림
```
- **핵심 노이즈 필터**입니다. 카메라 거리·마커 px 크기가 안 정해진 지금,
  절대 크기(면적)보다 이 "모양 품질"이 더 믿을 만합니다.
- 진짜 마크(꽉 찬 잉크)는 볼록하고 사각을 채움 → 통과. 손가락 가장자리·
  구불거리는 줄무늬는 아님 → 제거. 실측: 손가락 슬리버 프레임당 24→12개
  제거, 진짜 마크(solidity 0.80~0.97)는 전부 유지.
- **주의**: 별(★)은 오목(solidity≈0.6), 세모(▲)는 rect≈0.5. 이 형태를 쓰면
  `min_solidity`/`min_rectangularity`를 그만큼 내려야 합니다.

### 단계 3 — 대비 게이트
```text
(배경 중앙값 − 블롭 평균) < min_blob_contrast  → 버림
```
흐린 그림자·얼룩 제거.

### 단계 4 — 종류 분류 (통과한 덩어리에 종류 부여)

**`shape_classify=False`(기본)**: "길쭉함"만으로 판정 — 안 길쭉하면 dot(0),
길쭉하면 dash(1). 그래서 **네모와 원이 똑같이 0으로** 읽힙니다. 시작 마커는
"데이터보다 큰가"(상대 크기)로 찾습니다.

**`shape_classify=True`**: 스케일 불변 기술자로 **5형태를 구분**합니다
(`classify_shape`). 순서대로:
```text
aspect ≥ dash_aspect_min          → line     (dash, 비트 1)
solidity < star_solidity_max      → star     (시작 마커)  ★는 오목
vertices ≤ triangle_max_vertices  → triangle (시작 마커)  세모=꼭짓점 3
vertices ≥ circle_min_vertices    → circle   (dot, 비트 0)  원≈꼭짓점 8
그 외 (꼭짓점 4~5)                 → square   (시작 마커)  네모=꼭짓점 4
```
꼭짓점 수(approxPolyDP, 둘레의 4% 오차)는 블러·스케일에 안정적이라 네모(4)와
원(8)을 확실히 가릅니다 — 예전 circularity 방식은 둘을 헷갈렸습니다.

**템플릿 매칭 (`shape_templates` 저장 시, 위 임계값 트리를 대체)**: 임계값
트리는 순서가 깨지기 쉽습니다 — 예로 `dash_aspect_min`이 낮으면 별(aspect
1.22)·세모(1.20)가 "선"으로 먼저 분류됩니다. 대신 **원하는 마커를 하나씩
드래그로 학습**해두면(웹 "마커 학습"), 각 블롭을 **가장 가까운 템플릿**으로
라벨링합니다:
```text
특징벡터 v = [circularity, solidity, rectangularity, 꼭짓점/12, min(aspect,6)/6]
블롭마다 v 계산 → 저장된 템플릿 중 최근접(유클리드 거리) 라벨 부여
거리 > template_max_dist(0.35) → 어떤 마커도 아님 = 노이즈로 버림
```
- 실측: `dash_aspect_min=1.2`인 상태에서도 별→star, 세모→triangle 정확 분류
  (임계값 트리는 둘 다 "line"으로 틀림). **파형처럼 어떤 템플릿과도 안
  가까운 블롭은 자동으로 버려집니다.**
- 템플릿은 config 프로파일에 저장(`shape_templates`). 웹에서 라벨(네모/세모/
  별/원/선) 고르고 마커를 드래그하면 그 자리 블롭의 특징을 저장.
- 원(●)→0, 선(─)→1, 네모/세모/별→시작 마커. **시작 마커를 크기가 아니라
  모양으로 찾으므로**, 26.07.20 오브제(앵커가 점과 비슷한 크기)도 앵커가
  인식됩니다. 켤 때는 `require_start`(앵커 사용)도 켜세요.
- 파형(waveform)은 뾰족해서 solidity가 낮아 star로 오검출될 수 있음 → ROI로
  체인 행만 잡으면 깨끗해집니다(3장 모두 앵커 정확 인식 확인).

### 단계 5 — (shape_classify=False 전용) 종류 분류
먼저 기준 크기 두 개를 잡습니다:
```text
dot_area    = compact(aspect ≤ compact_aspect_max)한 덩어리들의 면적 중앙값
square_area = max(min_area, dot_area × square_area_ratio)
```
그 다음 각 덩어리를:
```text
start(네모):  aspect ≤ square_aspect_max      그리고
              area   ≥ square_area            그리고
              vertices ≤ square_max_vertices  그리고
              extent ≥ square_min_extent
dash(선):     aspect ≥ dash_aspect_min
dot(점):      aspect ≤ compact_aspect_max
그 외:        unknown (체인에 안 쓰임)
```
코드: `morse.py:284-298`.

> 핵심: **크기 절대값은 형태 무관 공통 게이트(단계 1)** 이고, **형태별 크기
> 구분은 여기서 "그룹 중앙값 대비 상대"** 로 합니다(네모는 점 중앙값의
> `square_area_ratio`배 이상). 손그림처럼 크기 편차가 큰 마커에 강하도록
> 상대 방식을 씁니다.

### 단계 4 — 체인 조립 (detect_cards)
살아남은 글리프를 **일직선으로 늘어선 N개**로 묶어 카드로 만듭니다. 여기서
쓰는 상대 지표(간격 규칙성, 수직 허용폭 등)는 `docs/options.md`의 "체인 기하".

---

## 5. 형태별 대표 값 (morseCode.MOV 실측)

| 형태 | aspect | extent | circularity | vertices | solidity* | rectangularity* |
| --- | --- | --- | --- | --- | --- | --- |
| 점 ● | ~1.0–1.3 | 0.6–0.7 | 0.8–1.0 | 5–8 | **0.91–1.00** | 0.73–0.83 |
| 선 ─ | 3–60 | 0.1–0.4 | 0.05–0.5 | 2–4 | 0.85–0.93 | 0.6–0.75 |
| 네모 ■ | ~1.0 | 0.67–0.9 | 0.8+ | ~4 | ~0.95 | 0.75+ |
| **손가락 가장자리(오검출)** | 2–60 | 0.02–0.28 | 0.02–0.1 | 2–7 | **0.02–0.60** | 0.17–0.47 |

`*` solidity / rectangularity는 이제 **구현되어 단계 2 형태 게이트로 동작**(6절).
표는 왜 이 둘이 손가락을 잘 거르는지 보여주는 실측입니다.

---

## 6. 형태 지표 (구현됨) — 손가락 오검출 대응

예전 선(dash) 분류는 **aspect 하나만** 봐서 길쭉한 손가락 가장자리가 선으로
오검출됐습니다(면적은 min~max 사이라 안 걸림). 아래 두 "모양 품질" 지표가
단계 2 게이트로 추가되어 걸러집니다. 둘 다 **비율(스케일 불변)** 이라 손그림
크기 편차·줌과 무관합니다.

### solidity (볼록 충실도) — `min_solidity` 기본 0.7
- **정의**: `area / (convex hull 넓이)`.
- **의미**: 덩어리가 얼마나 **볼록하게 꽉 찼는가**. **1.0 = 완전 볼록**,
  낮을수록 오목/구불구불.
- 진짜 마크(꽉 찬 잉크) ≈ 0.9–1.0, 손가락 가장자리(구불구불한 윤곽) ≈ 0.2–0.6.
- 별(★)은 오목해서 낮음(≈0.6) → 별 앵커를 쓰면 `min_solidity`를 내려야 함.

### rectangularity (회전 사각 충실도) — `min_rectangularity` 기본 0.4
- **정의**: `area / (minAreaRect 넓이)`. extent의 **회전 사각형 버전**
  (extent는 축 정렬 bbox 기준이라 기운 선에서 과소평가됨).
- **의미**: **기울어짐과 무관하게** 직사각형을 얼마나 채우나. 실제 선은
  높고(≈0.6–0.8), 얇게 구불거리는 줄무늬는 낮음(≈0.2–0.5). 세모(▲)는 ≈0.5.

기본 게이트: `solidity < 0.7` 또는 `rectangularity < 0.4` 이면 버림. 현재 테스트
마커(네모·원·선)와 손그림 마크(solidity 0.80~0.97)는 전부 통과, 손가락
가장자리는 제거됩니다. 별·세모 앵커를 쓸 때만 두 값을 내리세요.
