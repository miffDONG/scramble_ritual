# 트래커 교체 구조 + 접촉 측정 기반 크기 기준 (설계)

상태: 설계안, 결정 사항 반영. 0단계(폴더 이름 변경) 완료. (2026-11-01)

확정된 결정
- 크기 허용 범위 기본값은 **기준값 ±10%**, UI에서 **수동으로** 조절 (자동으로 넓히지 않음)
- 접촉 측정 직후 Ritualvision **자동 재시작**, UI에는 "다시 시작하는 중" → "재생중"으로 상태만 표시
- 허용 범위 밖 크기의 마커는 **버림** (들림 표시 기능은 만들지 않음)
- 폴더 이름 변경을 **가장 먼저** 진행

목표
1. 전시 시스템 폴더 이름을 `ScrambleRitual_Reactivision` → `ScrambleRitual_display`로 바꾼다.
2. 전시 시스템 안에서 마커 트래커를 **reacTIVision ↔ Ritualvision**으로 갈아 끼울 수 있게 한다 (웹 UI "인식 설정" 패널에서).
3. Ritualvision 사용 시, **접촉 거리 측정**에서 마커 크기도 함께 재서 Ritualvision의 **크기 허용 범위**(크기 측정 오차범위)로 쓴다.

---

## 0. 지금 이미 되어 있는 것 (커밋 전 작업)

| 위치 | 내용 |
|---|---|
| `tracker/reactivision.py` | `tracker` 설정(`reactivision` \| `ritualvision`), `find_ritualvision`, `tracker_exe`, Ritualvision은 복사하지 않고 제자리 실행, `-l` 호출 안 함, 종료 시 프로세스 트리 정리(`_kill_tree`) |
| `webui/exhibit_server.py` | `--tracker` 인자, 선택값 저장(테스트 모드 제외) |
| `scripts/exhibition_start.bat` | `--tracker ritualvision / reactivision` |
| `tests/test_tracker_choice.py` | 위 동작 테스트 |
| `Ritualvision/ritualvision/size_gate.py` | `r` 키로 "가장 작은 오브제" 크기 등록 → 그 80% 미만은 노이즈. `ritualvision_state.json`에 저장 |

빠진 것: UI 전환, 엔진별 설정 필드 분리, 크기 정보의 서버 전달, 접촉 측정과 크기 기준의 연결, 상한(너무 큰 것) 필터.

---

## 1. 폴더 이름 변경: `ScrambleRitual_display`

git 저장소 루트는 `C:\Users\baksh\scramble_ritual\`이고 이 폴더는 그 하위 폴더다. 그래서 **`git mv`로 옮기면 이력이 유지**된다.

| 영향 | 조치 |
|---|---|
| `.venv` (절대 경로가 박혀 있음) | 이동 후 깨짐 → 새로 만들고 `requirements.txt`로 재설치 |
| 루트 `.gitignore`, 루트 `README.md`, `Ritualvision/README.md`의 경로 문자열 | 이름 교체 |
| `.claude/launch.json` (미리보기 서버 설정) | 경로가 상대 경로인지 확인, 절대 경로면 교체 |
| 바탕화면 바로가기, 작업 스케줄러 등 외부에서 bat을 가리키는 것 | 수동 확인 필요 (저장소 밖) |
| Claude 세션 기억 폴더 (`C--Users-baksh-scramble-ritual-ScrambleRitual-Reactivision`) | 경로 기준이라 새 폴더에서는 이어지지 않음. 필요한 메모는 상위 `scramble_ritual` 쪽으로 옮김 |
| 실행 중인 서버, 열린 세션 | 이름 변경 전에 모두 종료 |

순서: 서버/세션 종료 → 커밋되지 않은 작업 정리(커밋) → `git mv` → venv 재생성 → 테스트 → 커밋.

---

## 2. 모듈 구조: 트래커 엔진 교체

지금 `tracker/reactivision.py` 한 파일에 reacTIVision 전용 코드, Ritualvision 분기, 공통 코드(TUIO 수신, 프로세스 관리)가 섞여 있다. 엔진을 같은 인터페이스로 분리한다.

```
tracker/
  tuio.py                 TuioReceiver, ExhibitObject          (공통: 두 엔진 모두 TUIO 1.1 2Dobj)
  process.py              TrackerProcess: 실행/중지/로그/상태   (공통, 지금 ReactivisionProcess)
  engines/
    __init__.py           ENGINES = {"reactivision": ..., "ritualvision": ...}, get_engine(cfg)
    base.py               TrackerEngine 인터페이스
    reactivision.py       reacTIVision 전용 (exe 탐색·복사, -l 모드 목록, XML 작성/가져오기)
    ritualvision.py       Ritualvision 전용 (exe 탐색, XML 작성 + <recognizer>, 크기 기준)
  reactivision.py         호환용: 위 모듈들을 다시 export (기존 import 깨지지 않게, 이후 제거)
```

`TrackerEngine` 인터페이스 (엔진마다 다른 것만):

| 메서드 / 속성 | reacTIVision | Ritualvision |
|---|---|---|
| `name`, `label` | `reactivision`, "reacTIVision (amoeba)" | `ritualvision`, "Ritualvision (엣지 대시)" |
| `find_exe(cfg)` | 설치 폴더 → Downloads, 밖에 있으면 복사 | 지정 경로 → 기본 설치, 제자리 실행 |
| `list_modes(exe)` | `-l` 출력 파싱 | 없음 (또는 `--list-cameras` 파싱) |
| `write_configs(cfg, dir)` | camera.xml + reacTIVision.xml | camera.xml + reacTIVision.xml + `<recognizer>` |
| `import_xml(dir)` | 기존과 동일 | 기존 + `<recognizer>` 값 |
| `args(cfg)` | `[exe]` 또는 `[exe, "-n"]` (단독 인자) | `[exe]`, `-n` 등 |
| `fields` | 엔진 전용 설정 키 목록 (UI가 이걸 보고 보여줄 칸을 정함) | 〃 |
| `stop(proc)` | 프로세스 종료 | 프로세스 트리 종료 (원파일 exe는 자식 프로세스가 카메라를 잡고 있음) |

설정 키 정리 (기존 설정 파일과 호환 유지):
- 공통(그대로): `tracker`, `rtv_autostart`, `rtv_no_window`, 카메라 `rtv_camera/width/height/fps/compress/brightness…/frame_*`, `rtv_tuio_port`, `rtv_display`, `rtv_equalize`, `rtv_angle_offset`, `rtv_stale_s`. camera.xml 형식이 두 엔진이 같으므로 키 이름을 바꾸지 않는다.
- reacTIVision 전용(그대로): `rtv_exe`, `rtv_gradient`, `rtv_tile`, `rtv_threads`, `rtv_finger_*`, `rtv_engine`, `rtv_tree`, `rtv_calib_*`.
- Ritualvision 전용(신규, 접두사 `rv_`): `ritualvision_exe`(기존), `rv_block`(15), `rv_offset`(12), `rv_hold`(3), `rv_ids`("ids.json"), 크기 기준 `rv_size_ref_px`(0 = 없음), `rv_size_lo_pct`(10), `rv_size_hi_pct`(10), `rv_size_on`, `rv_size_src`(measured \| physical) (4장).

---

## 3. UI: "인식 설정" 패널에서 엔진 교체

캡처한 패널 기준.

```
▼ 인식 설정                                   ← 제목에서 "(reacTIVision.xml)" 제거
  트래커  [ reacTIVision | Ritualvision ]      ← 세그먼트 버튼, rt.tracker

  ── 공통 ──
  TUIO port  3333        display  src(원본) ▾
  ☐ 배경 차감(equalize)  ☐ 창 없이 실행(-n)  ☑ 서버 시작 시 자동 실행

  ── reacTIVision일 때만 ──
  gradient 32   tile 10   finger size 0   finger sens. 75
  engine amoeba ▾   tree (기본)   calib file default.grid   invert

  ── Ritualvision일 때만 ──
  window 15   offset 12   hold 3   ids ids.json
  크기 기준  57.4px (접촉 측정)   허용  −[10]%  +[10]%   ☑ 크기 필터 사용   [초기화]
             → 51.7 ~ 63.1px 만 마커로 인정

  [적용(재시작)] [시작] [정지] [XML 가져오기]
  실행중 · Ritualvision · pid 7788 · 1280x720@60 · Elgato Facecam 4K · TUIO :3333 · 59fps · 오브제 2
```

- 트래커를 바꾸면 아무것도 즉시 실행하지 않고 "적용(재시작)"을 눌렀을 때 현재 엔진을 멈추고 새 엔진을 시작한다. 카메라를 두 프로그램이 동시에 잡을 수 없기 때문이다.
- 서버 API: 기존 설정 저장 경로(`rt` 갱신 + 재시작)를 그대로 쓰고, `tracker` 값만 추가로 받는다. 화면에 보일 칸은 `GET /api/...`가 엔진별 `fields`를 함께 돌려주어 UI가 숨김/표시를 정한다.
- 상태줄에 엔진 이름을 넣는다. 지금은 어떤 프로그램이 실행 중인지 화면에서 알 수 없다.
- "XML 가져오기"는 현재 엔진의 폴더에서 가져온다.
- `exhibition_start.bat --tracker`와 UI 선택은 같은 `tracker` 값을 공유한다(마지막 선택 유지).

---

## 4. 접촉 거리 측정 → Ritualvision 크기 기준

### 4.1 왜 필요한가

Ritualvision은 접힌 선의 팔 길이 `rc`(px)로 각 마커의 크기를 잰다. 설치 환경에서는 모든 마커가 유리 위에 있어 `rc`가 거의 일정하다(스냅샷: 57.5px, 57.4px). 이 기준값이 있으면:

| 용도 | 효과 |
|---|---|
| 하한 (`rc < 기준 × (1 − lo%)`) | 작은 가짜 접힌 선(잡음) 제거. 지금의 `r` 키 필터와 같은 역할 |
| **상한** (`rc > 기준 × (1 + hi%)`) | 접힌 선이 외곽선·이웃 선과 붙어 한 덩어리가 되면 팔이 길어진다(외곽 꼭짓점까지 가면 약 +14%). 이런 덩어리를 버려서 **엉뚱한 ID 대신 "인식 안 됨"**이 되게 함. 굵은 외곽선 테스트에서 나온 오인식 유형을 막는 장치 |

범위 밖 마커는 **버린다**(TUIO로 보내지 않음). 들어 올린 마커도 범위 밖이면 버려진다.

### 4.2 측정값의 관계 (형상 검증)

정육각형 타일, 변끼리 맞닿은 두 마커:
- 중심 거리 `d = 2 × 변심거리 = √3 × R`  (R = 타일 꼭짓점 반지름)
- `rc = 선 위치 0.76R ÷ cos30° = 0.8776 × R`
- 따라서 **`d / rc = 1.974`** (인쇄 비율이 지금 마커 규격과 같을 때)

접촉 측정 한 번으로 `d`와 두 마커의 `rc`를 같이 얻으므로, 이 비율로 측정이 제대로 됐는지 검증할 수 있다.

| `d / rc` | 판단 |
|---|---|
| 1.974 ± 5% | 정상: 변끼리 맞닿음, 마커 규격 일치 |
| 크게 큼 | 두 마커가 떨어져 있거나 꼭짓점끼리 닿음 → 다시 측정 안내 |
| 크게 작음 | 크기 측정 오류(덩어리 붙음 등) 또는 마커 인쇄 비율이 규격과 다름 |

### 4.3 데이터 흐름

```
Ritualvision ──TUIO 번들(2Dobj) + /ritualvision/size──▶ 서버 TuioReceiver
                                                         │ ExhibitObject.size_px
   [접촉 거리 측정] 클릭 ───────────────────────────────▶│
                                                         ▼
                              가장 가까운 두 마커: d(px), 두 마커의 rc를 N프레임 수집
                              → tension_contact (기존과 동일)
                              → rv_size_ref_px = 두 마커 rc의 중앙값
                              → 형상 검증 d/rc, 흔들림(표준편차)
                                                         │
                     write_configs: reacTIVision.xml <recognizer size_ref size_lo size_hi size_on>
                                                         ▼
                              Ritualvision 자동 재시작 → 상·하한 필터 적용
                              UI: "다시 시작하는 중" → "재생중"
```

**(1) 크기 전송: Ritualvision → 서버**
- TUIO 1.1 `/tuio/2Dobj`에는 크기 칸이 없다. 같은 번들에 별도 OSC 메시지를 추가한다:
  `/ritualvision/size  s(session id)  rc(px, float)  W  H(프레임 크기, int)`
- 서버 `TuioReceiver`가 이 주소를 하나 더 받아 `ExhibitObject.size_px`에 넣는다. reacTIVision은 보내지 않으므로 값이 없을 뿐 동작은 그대로다.
- TUIO 표준 메시지를 건드리지 않으므로 다른 TUIO 수신기에도 영향이 없다.
- 크기는 **카메라 픽셀**(ROI 크롭 후 프레임) 그대로 보낸다. 서버 캔버스 크기와 섞지 않아 변환 오차가 없다.

**(2) 측정: 서버 `measure_contact`**
- 지금 로직(가장 가까운 두 마커 → `tension_contact`)은 그대로 둔다.
- 엔진이 Ritualvision이면 추가로:
  - 두 마커의 `size_px`를 약 1초(30프레임) 모아 중앙값 → `rv_size_ref_px`
  - 흔들림 `σ`(표준편차) 계산
  - 두 마커 중심 거리를 **카메라 픽셀**로 다시 계산(`hypot(dx·W, dy·H)`, TUIO 정규 좌표 사용)해서 `d/rc` 형상 검증
- 응답에 추가: `size_ref_px`, `size_sigma`, `shape_ratio`, `shape_ok`
- 형상 검증이 실패하면 `tension_contact`도 저장하지 않고 다시 측정하라고 안내한다(두 값 모두 같은 원인으로 틀렸을 가능성이 높음).

**(3) 허용 범위: 수동 조절**
- 기본: 기준값 **±10%** (`rv_size_lo_pct = 10`, `rv_size_hi_pct = 10`)
- 하한·상한 %를 UI에서 **따로** 입력한다. 자동으로 넓히지 않는다.
  - 상한을 너무 넓히면(+14% 이상) 덩어리가 붙은 접힌 선을 못 거른다. UI에서 상한 입력 옆에 "14% 이상이면 붙은 선을 거르지 못함" 안내를 둔다.
  - 하한을 넓히는 경우: 렌즈 가장자리에서 마커가 작게 찍혀 빠질 때
- 측정할 때의 흔들림(σ)은 **참고값으로만 표시**한다: "측정 흔들림 ±1.2% (현재 범위 ±10%로 충분)". 흔들림이 범위의 절반을 넘으면 "범위를 넓히는 것을 권장"으로 바꿔 표시한다.
- (선택) 테이블 모서리에서 한 번 더 접촉 측정하면 가운데와의 크기 차이(렌즈 왜곡)를 % 로 보여준다. 범위 조절은 사람이 한다.

**(4) 적용: 서버 → Ritualvision (자동 재시작)**
- 서버가 이미 설정 파일을 써서 엔진을 재시작하는 구조이므로 그 길을 그대로 쓴다:
  `<recognizer block="15" offset="12" hold="3" ids="ids.json" size_ref="57.4" size_lo="10" size_hi="10" size_on="true" />`
- 접촉 측정이 성공하면(형상 검증 통과) 서버가 **바로 XML을 쓰고 Ritualvision을 재시작**한다. 허용 범위 %를 바꾸거나 크기 필터를 켜고 끌 때도 같다.
- UI 상태 표시만 한다(사용자 조작 없음):
  - "다시 시작하는 중": 재시작을 시작한 순간부터
  - "재생중": 새 프로세스가 뜨고 **첫 TUIO 프레임(fseq)이 다시 들어온 순간** (프로세스 실행만으로는 카메라가 열렸는지 알 수 없으므로)
  - 일정 시간(예: 10초) 안에 프레임이 안 오면 "재시작 실패"와 로그 마지막 줄 표시
- 재시작 동안 약 1–2초 인식이 끊긴다. 설치 시점에 하는 작업이라 괜찮다. 접촉 측정에 쓴 값은 재시작 전에 이미 저장되어 있다.
- 우선순위: **서버가 쓴 XML 값 > Ritualvision 단독 `r` 키 값(`ritualvision_state.json`)**. `r` 키는 서버 없이 단독 테스트할 때만 쓴다. 미리보기 상태줄에 출처를 표시: `size 57.4px −10%/+10% [server]`.

**(5) 다른 접촉 측정 방법과의 관계**

| 방법 | 크기 기준 |
|---|---|
| ① 접촉 측정 | 위 흐름대로 측정 + 검증 |
| ② 실측 치수 (mm) | ROI의 카메라 px 길이와 `table_long_mm`로 px/mm를 구할 수 있으면 `rc = 0.8776 × 한 변(mm) × px/mm`로 계산 가능. 형상 검증이 없으므로 UI에 "검증 안 됨" 표시. 범위는 같은 기본값(±10%)에서 수동 조절 |
| 직접 입력 | 크기 기준 없음 (필터 끔) |

### 4.4 UI ("2 테이블 영역 · 접촉 거리" 패널)

```
① 접촉 측정 (기본) — 마커 2개를 변끼리 맞닿게 올린 뒤 클릭
[접촉 거리 측정]  (접촉 0.0683 (접촉 측정))
                  (크기 기준 57.4px · 형상 1.97 ✓ · 흔들림 ±1.2%)          ← Ritualvision일 때만
☑ Ritualvision 크기 필터에 적용     허용  −[10]%  +[10]%  (51.7 ~ 63.1px)
상태: 다시 시작하는 중 …  →  재생중
```

허용 % 입력은 3장 "인식 설정" 패널의 같은 값과 연결된다(어느 쪽에서 바꿔도 같은 설정).

- 형상 검증 실패 시: "두 마커가 변끼리 맞닿지 않은 것 같습니다 (비율 2.31, 기대 1.97). 다시 측정하세요."
- reacTIVision일 때는 크기 줄이 나타나지 않는다(크기 정보가 없음).

---

## 5. 작업 순서 (구현 시)

| 단계 | 내용 | 확인 방법 |
|---|---|---|
| 0 | 커밋되지 않은 작업 정리 → 폴더 이름 변경(`git mv`) → venv 재생성 | 기존 테스트 전부 통과, 서버 실행 |
| 1 | 엔진 분리 리팩터링 (동작 변화 없음), 호환 export | 기존 테스트 + `test_tracker_choice` 통과 |
| 2 | UI 트래커 전환, 엔진별 칸 표시, 상태줄 엔진 이름 | 웹 UI에서 전환 → 재시작 → 각 엔진으로 TUIO 수신 |
| 3 | Ritualvision이 `/ritualvision/size` 전송, 서버가 `size_px` 수신 | 단위 테스트(OSC 디코드), 실제 실행 |
| 4 | 접촉 측정에 크기 기준·형상 검증 추가, XML `<recognizer size_*>` 작성, 자동 재시작과 "다시 시작하는 중 → 재생중" 표시 | 합성 프레임 두 마커 맞닿게 → 기준값·비율 확인, 재시작 후 첫 프레임에서 "재생중" |
| 5 | Ritualvision이 XML 크기 범위(±%)로 상·하한 필터, 범위 밖은 버림, 우선순위·상태 표시 | 굵은 외곽선 합성 테스트: 오인식 → "인식 안 됨"으로 바뀌는지 |
| 6 | (선택) 모서리 추가 측정으로 렌즈 왜곡 % 표시 | 실제 카메라 |

## 6. 결정 기록

| 질문 | 결정 |
|---|---|
| 허용 범위 | 기준값 ±10% 기본, 하한·상한 수동 조절. 자동 확장 없음 (흔들림은 참고 표시) |
| 접촉 측정 후 반영 | 자동 재시작. UI는 "다시 시작하는 중" → "재생중" 표시만 |
| 범위 밖 마커 | 버림 |
| 폴더 이름 변경 | 가장 먼저 |
