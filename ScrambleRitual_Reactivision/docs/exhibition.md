# 전시 서버 — reacTIVision → OSC (SuperCollider · TouchDesigner)

마커 인식은 reacTIVision(피듀셜)이 맡고, 서버는 그 결과를 받아 테이블 기준으로
정규화하고 긴장도(기본: MST 연결 그래프)를 계산해 OSC로 뿌린다. 인식 방식은 이것 하나다.

```
scripts/exhibition_start.bat
        │
        ▼
webui/exhibit_server.py  (Flask, http://localhost:8765, UI = static/exhibit.html)
        │  1) C:\Users\baksh\scramble_ritual\reactivision\camera.xml, reacTIVision.xml 생성 (exe 폴더 안, 원본은 *.orig)
        │  2) C:\Users\baksh\scramble_ritual\reactivision\reacTIVision.exe 실행 (인자 없음, 자식 프로세스)
        ▼
[USB 카메라] → reacTIVision.exe ── TUIO 1.1 (/tuio/2Dobj, UDP 127.0.0.1:3333) ──▶ TuioReceiver
                                                                                     │
                       ROI 정규화 → tension_graph → /scramble/obj ×(오브제 수) ◀──────┘
                                                        │
                        ┌───────────────────────────────┴──────────────────────┐
                        ▼                                                      ▼
              SuperCollider 127.0.0.1:57120                        TouchDesigner 127.0.0.1:7000
```

- 서버는 **카메라를 직접 열지 않는다.** DirectShow는 배타 점유라 reacTIVision이 잡고 있는
  카메라를 다른 프로세스가 열 수 없다(웹 미리보기는 TUIO 값으로 그린 합성 화면).
- 긴장도 수학은 `tracker/tension.py`(MST / all / near / knn, fold, 오버레이). 값 정의는 `docs/osc-values.md`.

## 실행

1. reacTIVision은 프로젝트 **밖** `C:\Users\baksh\scramble_ritual\reactivision\`에 둔다(배포판 폴더 내용: `reacTIVision.exe`,
   `SDL2.dll`, `symbols/`, `calibration/`). 그 폴더가 없으면 서버가 첫 실행 때 `~/Downloads/reacTIVision-1.5.1-win64/…`
   에서 자동 복사한다. 프로젝트(OneDrive) 안에는 두지 않는다 — reacTIVision 소속이 아니고, OneDrive 동기화 잠금도 피한다.
2. `scripts/exhibition_start.bat` 더블클릭 → 브라우저 `http://localhost:8765`.
   - `--no-rtv` : reacTIVision을 자동 실행하지 않음(수동으로 띄울 때).
   - `--rtv-exe PATH` : 다른 위치의 exe.
3. 설정은 웹에서 편집. 바꾼 값은 항상 `webui/exhibit_config.json`에 저장되어 다음 실행에 자동 적용.
   이름별 프로파일은 `configs/exhibit/<이름>.json`.

최초 실행 시 설치 폴더의 `camera.xml`/`reacTIVision.xml`(덮어쓴 뒤에는 `*.orig`) 값을 그대로 읽어 온다.
단 해상도는 카메라 목록에서 **MJPG·120fps가 가능한 최대 해상도**를 자동 선택한다(`rtv_width=0`).
"XML 가져오기" 버튼으로 언제든 다시 읽을 수 있다.

## 웹 UI 패널

| 패널 | 내용 |
| --- | --- |
| 1 전시 카메라 | 카메라 → 해상도(지원 목록) → fps(해상도별 목록, 기본 120), camera.xml 설정, reacTIVision.xml 인식 설정, 적용(재시작)/시작/정지/XML 가져오기, 상태·로그 |
| 2 테이블 영역·오브제 크기 | 미리보기 위 드래그로 ROI 지정, 한 변 측정(긴장도 기준), 폴백 접촉거리 |
| 3 긴장도 | 연결(**MST** 기본 / all / near / knn), 결합(max/avg/min), k, 연결 반경, 그래프 표시 |
| 4 OSC 송신 | 마스터 on/off, prefix, 대상 목록(이름/host/port/형식/on) |
| 5 프로파일 | 이름 저장 / 불러오기 / 기본값 |
| 모니터 | 오브제 표(ID·세션·x·y·tilt·tension), OSC 모니터(대상·주소·값) |

## 설정 키 ↔ XML 매핑

`rtv_*` 키는 생성되는 XML 속성과 1:1이다.

| XML | 속성 | 키 | 기본 | UI |
| --- | --- | --- | --- | --- |
| camera.xml `<camera>` | id | `rtv_camera` | 0 | 카메라 |
| `<capture>` | width / height | `rtv_width` / `rtv_height` | 0 (자동) | 해상도 |
| | fps | `rtv_fps` | 120 | fps |
| | compress | `rtv_compress` | true (MJPG) | 해상도의 코덱 |
| `<settings>` | brightness … focus | `rtv_brightness` … `rtv_focus` | default (focus=min) | 카메라 설정 |
| `<frame>` | width / height / xoff / yoff | `rtv_frame_*`, `rtv_xoff`, `rtv_yoff` | max / 0 | frame w·h, xoff, yoff |
| reacTIVision.xml `<tuio>` | port | `rtv_tuio_port` | 3333 | TUIO port |
| `<finger>` | size / sensitivity | `rtv_finger_size` / `rtv_finger_sensitivity` | 0 / 75 | finger |
| `<fiducial>` | engine / tree | `rtv_engine` / `rtv_tree` | amoeba / "" (=default.trees) | engine, tree (`small` → `symbols/amoeba/small.trees`) |
| `<image>` | display / equalize / fullscreen | `rtv_display` / `rtv_equalize` / `rtv_fullscreen` | src / false / false | display, 배경 차감 |
| `<threshold>` | gradient / tile / threads | `rtv_gradient` / `rtv_tile` / `rtv_threads` | 32 / 10 / max | gradient, tile |
| `<calibration>` | file / invert | `rtv_calib_file` / `rtv_calib_invert` | default.grid / "" | calib file, invert |
| (실행) | `-n` | `rtv_no_window` | false | 창 없이 실행 |
| (실행) | | `rtv_autostart` | true | 서버 시작 시 자동 실행 |
| (파이프라인) | | `rtv_angle_offset` | 0 | tilt 0° 보정(도) |
| (파이프라인) | | `rtv_stale_s` | 1.0 | TUIO 끊김 판정 시간 |

생성 파일 위치: `C:\Users\baksh\scramble_ritual\reactivision\camera.xml`, `C:\Users\baksh\scramble_ritual\reactivision\reacTIVision.xml` — **exe 폴더 안**. 서버가 매 시작마다 덮어쓰고,
배포판 원본은 처음 한 번 `camera.xml.orig` / `reacTIVision.xml.orig`로 보관한다.

> 왜 exe 폴더인가: reacTIVision 1.5.1은 `<camera config="…">` 태그가 있으면(절대·상대 경로 무관) camera.xml을
> 열지 못하고, `-c`로 다른 폴더의 설정을 주면 tree 파일도 못 찾는다. 태그 없이 자기 폴더의 `camera.xml`을 읽게 하는
> 방식만 확실히 동작한다(실측). `tree` 속성은 exe 폴더 기준 상대 경로(`symbols/amoeba/small.trees`)여야 한다.
> `-n`(창 없음)은 다른 인자와 같이 쓰면 usage만 찍고 종료하므로 단독으로만 넘긴다.

카메라 모드 목록(`-l`)은 카메라가 사용 중이면(우리 자식 프로세스 포함) 비어서 오므로, 마지막 정상 목록을
`modes.json`에 보관해 두고 그때는 그것을 쓴다(UI에 "마지막 목록 사용" 표시). UI의 ↺는 자식 프로세스를 잠시 멈추고
다시 읽은 뒤 재시작한다. 그래도 비어 있으면 **다른 프로그램(실험 튜너, 다른 reacTIVision, 브라우저)이 카메라를 잡고
있는 것**이니 닫고 다시 ↺. 목록이 없을 때는 현재 설정된 모드만 드롭다운에 남는다.

## fps · 노출

- 카메라가 지원하는 모드는 `reacTIVision.exe -l`로 확인(UI의 ↺). 이 카메라(USB Camera)는
  MJPG에서 1280x800 / 800x600 / 640x400 / 320x240 각각 120·60·30·15 fps, YUY2(비압축)는 10~30 fps.
- 목록의 값만 쓸 수 있다(이산). 실제 fps는 **노출 시간**에도 막힌다: exposure는 DirectShow log2 초라
  `-5`=1/32s(최대 32fps), `-6`=1/64s, `-7`=1/128s. **120fps는 exposure ≤ −7**, 그만큼 IR 조명이 밝아야 한다.
- 창 제목 / HUD의 TUIO fps가 실제 처리 속도다.

## OSC

- 주소는 `/scramble/obj` 하나, 오브제 1개당 1메시지, **카메라 프레임(TUIO fseq)마다** 송신.
- 필드 순서(list 형식): `id, binary_id, x, y, tilt, tension, flip, freq` (`binary_id` = id의 8비트 이진 문자열)
  - `id` int — reacTIVision 피듀셜 심볼 번호(오브제 정체성). 세션 id는 보내지 않는다.
  - `x, y` 0~1 — ROI 지정 시 테이블 기준, 아니면 카메라 프레임 기준.
  - `tilt` −180~180 — 화면 시계방향 +(reacTIVision 각도를 도 단위로 wrap, `rtv_angle_offset`으로 0° 보정).
  - `tension` 0~1, `flip` 항상 0(reacTIVision은 뒤집힘 미검출), `freq` = `((id*5)%12)-6`.
- 대상마다 형식(list / dict / json)을 따로 고를 수 있고, 모든 대상이 같은 값을 받는다.
- 수신 테스트(두 터미널):
  ```
  .venv\Scripts\python -c "from pythonosc import dispatcher, osc_server; d=dispatcher.Dispatcher(); d.set_default_handler(lambda a,*v: print(a,v)); osc_server.BlockingOSCUDPServer(('127.0.0.1',57120),d).serve_forever()"
  ```
  (7000도 동일)

## 문제 해결

| 증상 | 확인 |
| --- | --- |
| 상태에 "즉시 종료" | 카메라를 다른 프로그램이 잡고 있음(다른 reacTIVision, 실험 튜너, 브라우저). 모두 닫고 시작 |
| exe 없음 | `C:\Users\baksh\scramble_ritual\reactivision\reacTIVision.exe` 확인(또는 Downloads에 배포판을 두면 자동 복사) 또는 exe 경로 입력 |
| TUIO 0fps | reacTIVision 창이 떠 있는데 0이면 TUIO 포트 불일치(UI의 TUIO port = reacTIVision.xml) 또는 3333을 다른 앱이 점유 |
| 오브제 0 | reacTIVision 창(`t` 키로 이진화 화면)에서 마커가 잡히는지 확인. 초점·마커 크기(한 변 ≥ 40px)·gradient |
| fps가 안 오름 | exposure를 낮추고(−7), MJPG 모드인지 확인 |

## 이전 구현

이전의 OpenCV 모스 카드/실루엣 인식 코드와 실험 튜너는 프로젝트에서 제거했다(2026-09-20).
백업: `C:\Users\baksh\scramble_ritual\legacy_morse_backup_2026-09-20.zip`.
