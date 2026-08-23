# OSC 명세 (현행) — 웹 튜너 → Max/TouchDesigner

웹 UI의 **OSC 송신**을 켜면(호스트/포트/프리픽스 지정) 매 프레임 아래를 보낸다.
좌표는 정규화(0~1). 프리픽스 기본 `/scramble`.

**오브제 소스(`objects`)** 에서는 검출 없이 그라운드 트루스로 계산해 보낸다 —
카메라 없이 사운드를 개발하기 위한 모드. 카메라/영상 소스에서도 같은 주소로
나가되 `flip`은 0, `freq`는 ID 파생값이 된다.

> **실제 송신은 `/scramble/obj` 한 종류뿐이다.** 아래 `/count`·`/tension`·
> `/param/*`·`/event/*`는 코드에 없다(구 설계 잔재). 정확한 현행은
> `docs/osc-values.md`.

## 개별 오브제 — `/scramble/obj` (오브제 1개당 1메시지)
list 형식 기준 인자 순서(`OSC_OBJ_FIELDS`):

| 순서 | 인자 | 범위 | 의미 |
|---|---|---|---|
| 0 | `string bits` | | 8비트 모스 코드 = **오브제 식별자** → 사운드 조각 선택 |
| 1 | `float x` | 0~1 | **개별 오브제 x** (오브제 판 중심, ROI 폭 정규화) → 패닝 |
| 2 | `float y` | 0~1 | **개별 오브제 y** (ROI 높이 정규화) |
| 3 | `float tilt` | −180~180 | **개별 오브제의 기울기**(도). 차이가 아니라 그 오브제 자체의 각도 |
| 4 | `float tension` | 0~1 | **그 오브제에 걸리는 긴장도** — 기본 MST에서 자신에게 직접 연결된 간선 tension을 fold |
| 5 | `int flip` | 0/1 | **뒤집힘** → 1이면 역재생 |
| 6 | `float freq` | −12~12 | **오브제별 주파수**(세미톤). 미지정 시 ID 파생 밴드 |

**각도 규약**: 이미지 좌표(+y가 아래)라 **화면상 시계방향이 +**.
오른쪽=0°, 아래=+90°, 위=−90°, 왼쪽=±180°. 항상 −180~180으로 감김.

**긴장도(`tension`)**: 중심거리를 **테이블 긴 변**으로 정규화한 값으로 곡선을 계산 —
멀면(≥ 긴 변의 절반) 0, 변이 닿는 거리(`한 변/√3`)에서 1, 그보다 가까우면(겹침) 1 유지.
기본 `tension_connect=mst`는 거리 가중치로 전체 n−1개 간선을 선택하고, 오브제별로
자신에게 직접 연결된 간선만 `tension_fold`(max/avg/min)로 접는다. 기존
all/near/knn도 선택할 수 있다. 상세·곡선·옵션은 `docs/osc-values.md` 참고.

> **`/pair`는 더 이상 보내지 않는다.** tension은 위처럼 각 `/obj`에 실린다.

## 이벤트 — 변화 순간에만 1회
| 주소 | 인자 | 트리거 |
|---|---|---|
| `/scramble/event/touch` | `int i, int j, float x, float y` | 쌍이 접촉 진입(dist ≤ contact) |
| `/scramble/event/release` | `int i, int j` | 접촉 해제 |
| `/scramble/event/flip` | `int idx, int flip` | 오브제 뒤집힘 토글 |

---

# (구) OSC 명세 — 트래커 → Max/MSP

트래커 실행 시 `--osc HOST:PORT` 지정 (예: `--osc 127.0.0.1:9000`).
모든 메시지는 매 프레임(기본 30fps) 송출. 좌표는 프레임 크기로 정규화(0.0~1.0).

## 전역 파라미터 — `/scramble/param/<이름>` (float 1개)

| 주소 | 범위 | 의미 / 권장 매핑 |
|---|---|---|
| `/scramble/param/scramble` | 0~1 | 종합 흐트러짐. 0=정렬·안정, 1=해체 |
| `/scramble/param/grain_ms` | 319~135 | 그래뉼러 입자 길이 (ms) |
| `/scramble/param/density_hz` | 8~80 | 입자 발생 밀도 (Hz) |
| `/scramble/param/pitch_scatter` | 0~1 | 피치 산포 (기울기 무질서도 직결) |
| `/scramble/param/spray_ms` | 0~250 | 재생 위치 산포 (배열 비선형성 직결) |
| `/scramble/param/tone` | -1~+1 | 스펙트럼 틸트. 음수=저역(연결·체인), 양수=고역(무질서) |
| `/scramble/param/n` | 0~16 | 보이는 오브제 수 |

파라미터는 호스트에서 EMA 스무딩(α=0.35) 후 송출 — 수신측 추가 라인 보간은 ~50ms 권장.

## 오브제별 — `/scramble/obj` (int id, float cx, float cy, float angle)

- `cx, cy`: 정규화 중심 좌표 → 8채널 공간 패닝
- `angle`: -90~+90도 (장축 기울기) → 개별 보이스 디튠/필터

## 관계 — 매 프레임

| 주소 | 인자 | 의미 |
|---|---|---|
| `/scramble/contact` | int a, int b, float x, float y | a-b가 맞닿음. (x,y)=접점 → 접점 위치로 패닝된 클릭/마찰음 |

## 이벤트 — 상태 변화 순간에만 1회

| 주소 | 인자 | 트리거 권장 |
|---|---|---|
| `/scramble/event/touch` | int a, int b | 접촉 시작 — 어택 트랜지언트 |
| `/scramble/event/release` | int a, int b | 접촉 해제 — 릴리즈 꼬리 |
| `/scramble/event/merge` | int ids... | 겹침 시작 — 보이스 병합/드론 진입 |
| `/scramble/event/split` | int ids... | 분리 — 보이스 재분기 |

merge 이벤트의 면적비(콘솔 로그 `area_ratio`)는 1.0≈옆으로 맞닿아 합쳐짐,
낮을수록 위로 포개진 적층 — 필요 시 OSC 인자로 추가 가능.

## Max 수신 예시

```
[udpreceive 9000]
 |
[route /scramble/param/scramble /scramble/param/grain_ms /scramble/obj /scramble/event/touch]
 |            |                  |                         |
[line~ 50]   [line~ 50]         [unpack i f f f]          [t b] → 트랜지언트 트리거
```

---

# 브링업 OSC 명세 — `tracker.bringup` (단계 4~5)

단계별 도입 검증 도구는 매핑을 거치지 않은 **생 기하 정보**를 `/sr/...` 로 보낸다.
수신 패치는 `docs/bringup.maxpat` (열고 [udpreceive 9000] 포함, edit lock 해제 후 사용).

| 주소 | 인자 | 의미 |
|---|---|---|
| `/sr/n` | int | 보이는 오브제 수 |
| `/sr/obj` | int id, float cx, float cy, float angle, sym frag | 오브제별 — 좌표 정규화(0~1), 각도 -90~+90도, **조각 라벨**(A0..D3) |
| `/sr/dist` | int a, int b, float px, float norm | 쌍 거리 (픽셀 / 프레임 대각선 정규화) |
| `/sr/nearest` | int a, int b, float norm | 최근접 쌍과 그 정규화 거리 |
| `/sr/param/<name>` | float | **단계5 조합 사운드 파라미터** — scramble·grain_ms·density_hz·pitch_scatter·spray_ms·tone·n |
| `/sr/sound` | float 0~1 | 종합 scramble (0=온전한 루프, 1=파편 콜라주) |

### 조합(composition) 사운드 — 단계5

오브제별 `/sr/obj` 의 `frag` 라벨 = 그 웨이브폼이 든 사운드 조각. **좌→우 위치 순서가
재생 순서**(테이프 재조립)이고, `cx` 가 그 조각의 스테레오 팬. `/sr/param/*` 가 정렬·기울기·
맞닿음에 따라 입자를 변조한다(정렬=긴 입자 온전, 흐트러짐=짧은 파편). Max 는 `frag` 로
보이스를 고르고 `cx` 로 패닝, `/sr/param/*` 로 그래뉼러 처리하면 동일 조합이 재현된다.

- 단계4: `--osc HOST:PORT` → 위 기하 메시지가 매 프레임 송출(소리 없음).
- 단계5: **내장 신스로 바로 듣기** = `--sound` (OSC 없이도 됨, 조합 완성).
  **Max 에서 합성** = `--osc ...` (`--sound` 생략) → `/sr/obj` frag + `/sr/param/*` 수신.
  실음원 쓰려면 `--wav a.wav b.wav c.wav d.wav` (4종 → 각 4조각 = 16조각).
