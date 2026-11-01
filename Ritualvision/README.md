# Ritualvision

reacTIVision을 복제한 구조에서 **마커 인식 & 분류부만 교체한** 트래커입니다. ScrambleRitual 접힌 선 + 엣지 대시 마커(ID 0–24)를 인식하고, reacTIVision과 같은 **TUIO 1.1 `/tuio/2Dobj`**로 보냅니다. 그래서 받는 쪽(전시 서버의 `TuioReceiver`)은 바꿀 필요가 없습니다.

## 카메라 테스트 (바로 실행)

1. IR 카메라를 연결합니다.
2. `Ritualvision_camera_test.bat`을 더블클릭합니다.
   - 먼저 연결된 카메라 목록을 보여줍니다 (`camera 0: DSHOW 1280x800` 등).
   - 이어서 미리보기 창이 열립니다. 인식된 마커에는 초록 접힌 선, 주황 대시 점, ID 숫자, 방향 화살표가 그려집니다.
3. 카메라 번호가 0이 아니면 `camera.xml`의 `<camera id="0">`를 바꾸거나, 명령줄에서 `Ritualvision.exe --camera 1`로 실행합니다.

### 미리보기 창 단축키

| 키 | 동작 |
|---|---|
| `t` / `Tab` (`1` 카메라, `2` 마스크) | 카메라 영상 ↔ 선 마스크(인식기가 보는 흑백) 전환. Caps Lock·한글 입력 상태에서도 동작 |
| `r` | 최소 크기 필터(단독 테스트용). off에서 누르면 **등록 모드**: 가장 작은 오브제를 올려두고 `r`을 한 번 더 누르면 그 크기의 80% 미만을 noise로 무시(on). on에서 누르면 off. 등록 중 `Esc`는 취소. 설정은 `ritualvision_state.json`에 저장되어 재시작·`-n` 모드에도 적용. **전시 서버가 크기 범위를 정해 두었으면(ritualvision.xml `size_on="true"`) 그 값이 우선**이고 `r`은 동작하지 않음 |
| `[` `]` | offset −/+ : 선으로 판단하는 어두움 기준. 선이 끊기면 내리고, 잡음이 많으면 올림 |
| `,` `.` | window −/+ : 국소 임계값 창 크기 (기본 15px) |
| `c` | 카메라 드라이버 설정 창 (노출·게인·포커스를 손으로 조절) |
| `s` | 스냅샷 저장 → `snapshots/` (원본 프레임 + 오버레이). 인식이 안 될 때 이 파일을 보내주면 원인 분석이 가능합니다 |
| `p` | 일시정지 |
| `h` | 도움말 표시/숨김 |
| `q` / `Esc` | 종료 (종료 시 빈 alive를 보내 받는 쪽 객체를 모두 지움) |

미리보기에는 접힌 선에서 추정한 육각형(바깥: 타일 가장자리, 안쪽 회색: ID 선이 놓이는 고리)과 ID 번호, 대시 조합, 회전각, 측정한 접힌 선 각도(fold)가 표시됩니다. 접힌 선은 두 팔에 맞춘 직선 사이 각이 120° ± 6° 이내일 때만 인정합니다(실제 카메라 측정 119.0–120.2°).

빨간 육각형과 `?`는 마커 모양은 찾았지만 대시 조합이 ID 표에 없는 경우입니다. 다른 ID로 착각하지 않고 보내지 않습니다.

## 명령줄

```
Ritualvision.exe                     카메라 + 미리보기 창 + TUIO
Ritualvision.exe -n                  창 없이 (reacTIVision -n과 같음)
Ritualvision.exe --list-cameras      카메라 번호 확인
Ritualvision.exe --camera 1          camera.xml 대신 1번 카메라
Ritualvision.exe --port 3334         TUIO 포트 변경
Ritualvision.exe --source 사진.png   카메라 대신 이미지/영상 파일
Ritualvision.exe --source 사진.png --once   한 장 인식 결과를 JSON으로 출력하고 종료
```

## 설정 파일 (exe 옆)

- `camera.xml`: reacTIVision과 같은 형식. `<camera id>`, `<capture width height fps compress>`, `<settings brightness gain exposure focus …>`, `<frame …>`(ROI).
- `reacTIVision.xml`: `<tuio host port>`, `<image display>`. 전시 서버가 웹 UI 설정으로 다시 씁니다.
- `ritualvision.xml`: Ritualvision 전용 `<recognizer block offset invert hold ids>`. reacTIVision.xml 다음에 읽으므로 서버가 덮어써도 유지됩니다.
- `ids.json`: ID ↔ 대시 조합 표 (UR, R, LR, LL). 25개 마커와 같은 순서.
- `markers/`: 인쇄용 PNG/SVG (ID 0–24).

## 구조 (모듈)

| 모듈 | 파일 | 역할 |
|---|---|---|
| 카메라 리소스 연결부 | `ritualvision/camera.py` | DirectShow(→ Media Foundation) 카메라 열기, 별도 스레드로 최신 프레임 유지, 이미지/영상 파일 소스 |
| 카메라 옵션 설정부 | `ritualvision/camera_options.py` | camera.xml 설정 적용, ROI 자르기 |
| 마커 패턴 인식 & 분류부 | `ritualvision/recognizer.py` | 국소 임계값 → 영역 분리 → 접힌 선 검출(중심·크기·대략 방향) → 대시 축으로 정밀 각도 → 변별 대시 수 → ID |
| 추적 | `ritualvision/tracker.py` | 세션 ID, 속도, 놓친 프레임 유지(hold) |
| 출력 | `ritualvision/tuio.py` | TUIO 1.1 번들 (source, alive, set, fseq). 외부 라이브러리 없음 |
| 미리보기 | `ritualvision/display.py` | 오버레이 창 |

각도 규칙: Figma 도면 그대로 놓인 상태 = 0°, 화면 기준 시계 방향 +. TUIO `a`는 라디안.

## 검증 (합성 IR 프레임, 25개 동시, 타일끼리 맞닿음 포함)

| 타일 크기 (꼭짓점 간) | 정확 | 다른 ID 오인 | 각도 오차 |
|---|---|---|---|
| 150 px | 75/75 | 0 | 최대 0.6° |
| 120 px | 75/75 | 0 | 최대 0.6° |
| 100 px | 71/75 | 0 | 최대 0.9° |
| 90 px | 62/75 | 0 | 최대 1.0° |

처리 속도: 1280×800, 마커 25개에서 약 20 ms/프레임. 실제 IR 카메라로는 아직 검증 전입니다.

## 다시 빌드

```
python -m venv .venv
.venv\Scripts\python -m pip install numpy opencv-python pyinstaller
build.bat
```

`tools/make_test_frame.py`로 정답이 있는 합성 테스트 프레임을 만들 수 있습니다.

## 전시 서버 연동

`ScrambleRitual_display\scripts\exhibition_start.bat --tracker ritualvision` (되돌리기: `--tracker reactivision`), 또는 웹 UI "1 전시 카메라 · 트래커"의 전환 버튼 + **적용 (재시작)**. 선택은 `webui/exhibit_config.json`의 `tracker`에 저장되어, 옵션 없이 실행하면 마지막 선택을 그대로 씁니다. 서버는 이 폴더의 Ritualvision.exe를 그 자리에서 실행하고(복사 안 함), camera.xml·reacTIVision.xml·**ritualvision.xml**을 웹 UI 설정으로 써 줍니다(처음 쓸 때 원본은 `*.orig`로 보관). 종료할 때는 exe의 프로세스 트리 전체를 정리합니다.

크기 정보: Ritualvision은 TUIO 번들에 마커마다 `/ritualvision/size s rc W H`(접힌 선 팔 길이 px)를 함께 보냅니다. 웹 UI에서 **접촉 거리 측정**을 하면 서버가 같은 두 마커의 크기로 기준값을 정하고(중심거리/크기 = 1.97 ± 6%로 맞닿음 확인), ritualvision.xml의 `size_ref size_lo size_hi size_on`에 써서 Ritualvision을 자동으로 다시 시작합니다. 범위(기본 ±10%) 밖의 접힌 선은 TUIO로 보내지 않습니다.
