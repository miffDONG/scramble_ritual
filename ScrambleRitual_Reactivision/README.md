# Scramble Ritual — 전시 서버 (reacTIVision → OSC)

테이블 위 오브제(reacTIVision 피듀셜 마커)를 인식해 위치·기울기·**긴장도**를
SuperCollider(사운드)와 TouchDesigner(영상)에 OSC로 보내는 전시 시스템.

```
[USB 카메라] → reacTIVision.exe (자식 프로세스) ── TUIO/UDP 3333 ──▶ webui/exhibit_server.py
                                                                     │ 테이블(ROI) 정규화
                                                                     │ 긴장도 그래프 (MST 기본)
                                                                     ▼
                                              /scramble/obj ×N ──▶ SuperCollider 57120
                                                             └──▶ TouchDesigner 7000
```

전시 모드의 인식 방식은 reacTIVision이다. 서버는 카메라를 직접 열지 않는다.
테스트 모드는 카메라와 reacTIVision 없이 가상 오브제를 사용한다.

## 실행

```bat
scripts\exhibition_start.bat            :: http://localhost:8765 (첫 실행 시 .venv 생성·설치)
scripts\exhibition_start.bat --no-rtv   :: reacTIVision을 직접 띄울 때
```

수동: `.venv\Scripts\python -m webui.exhibit_server [--port 8765] [--rtv-exe PATH] [--no-rtv]`

- reacTIVision 1.5.1 win64 배포판은 프로젝트 **밖** `C:\Users\baksh\scramble_ritual\reactivision\`에 둔다.
  없으면 첫 실행 때 `~/Downloads/reacTIVision-1.5.1-win64/…`에서 자동 복사한다.
- 서버가 그 폴더의 `camera.xml`/`reacTIVision.xml`을 웹 설정으로 생성한다(원본은 `*.orig`).
- 설정은 `webui/exhibit_config.json`에 자동 저장, 이름별 프로파일은 `configs/exhibit/<이름>.json`.

### macOS 테스트 모드

Finder에서 `scripts/test_start.command`를 더블클릭하거나 터미널에서 실행한다.

```sh
./scripts/test_start.command
./scripts/test_start.command --port 8766
```

첫 실행 시 프로젝트 안의 `.bootstrap`에 `uv`와 Python 3.13을 설치하고,
`.venv`를 만든 뒤 `requirements.txt`를 설치한다. 인터넷 연결이 필요하다.
이후 브라우저에서 `http://127.0.0.1:8765`를 연다. 터미널에서 종료하려면 `Ctrl+C`.
직접 실행할 때는 `python -m webui.exhibit_server --test`를 사용할 수 있다.

- reacTIVision 프로세스를 실행하거나 TUIO 포트를 열지 않는다.
- 웹 UI의 첫 패널에서 가상 6각형 오브제를 추가·삭제하고, 미리보기에서 이동하거나 ID(0~24)와 기울기를 바꿀 수 있다.
- 가상 프레임은 전시 모드와 같은 ROI → 긴장도 → OSC 처리 경로를 사용한다. OSC 기본 형식은 두 모드 모두 JSON이다.
- 테스트 화면의 **4 OSC 송신**에서 최대 전송 FPS를 1~120으로 조절할 수 있다(기본 120). 입력 프레임과 미리보기는 유지하고 OSC 송신 프레임만 건너뛴다.
- 화면 상단의 **가상 FPS**는 실제 OSC 송신에 성공한 프레임의 측정 속도다. 미리보기 안의 `input`은 가상 입력 속도, `osc`는 OSC 송신 속도를 나타낸다.
- 오브제 배치는 `webui/test_objects.json`에 저장된다. 다른 전시 설정은 `webui/exhibit_config.json`을 공유한다.

## 웹 UI

| 패널 | 내용 |
| --- | --- |
| 1 전시 카메라 / 테스트 오브제 | 전시 모드: 카메라·reacTIVision 설정과 상태. 테스트 모드: 가상 오브제 배치와 편집 |
| 2 테이블 영역·오브제 크기 | 미리보기 드래그로 ROI, 한 변 측정(긴장도 기준) |
| 3 긴장도 | 연결(**MST** 기본 / all / near / knn), 결합(max/avg/min), 그래프 표시 |
| 4 OSC 송신 | 마스터 on/off, prefix, 대상 목록(이름/host/port/형식/on), 테스트 모드 최대 전송 FPS |
| 5 프로파일 | 이름 저장 / 불러오기 / 기본값 |
| 모니터 | 합성 오버레이(TUIO 값으로 그림), 오브제 표, OSC 모니터 |

## OSC

주소 `/scramble/obj` 하나, 송신하는 프레임에서 오브제 1개당 1메시지. 전시 모드는 TUIO 프레임마다 송신하고, 테스트 모드는 선택한 최대 OSC FPS에 맞춰 일부 가상 프레임을 건너뛴다.
기본 형식은 **JSON**(인자 1개 = `{"id":…, "bits":"…", "x":…, "y":…, "tilt":…, "tension":…}`),
대상별로 list(위 순서의 위치 인자) / dict(키, 값 번갈아)로 바꿀 수 있다. 정의는 `docs/osc-values.md`.

## 긴장도 — MST 연결 그래프 (기본)

오브제 중심거리로 **최소 신장 트리**를 만들어(n−1 간선) 모든 오브제를 하나의 나무로 잇고,
각 오브제는 자기 트리 이웃과의 쌍 긴장도를 fold(기본 max)한다. 오버레이의 연결선이 곧 이 트리다.
`all`(n−1 전부) / `near`(반경) / `knn`(최근접 k) 방식도 UI에서 고를 수 있다.

## 구성

```
webui/exhibit_server.py     Flask 서버 + 파이프라인 (TUIO → ROI → 긴장도 → OSC)
webui/static/exhibit.html   웹 UI (단일 파일)
tracker/reactivision.py     reacTIVision 자식 프로세스, -l 파싱, XML 생성/가져오기, TUIO 수신
tracker/virtual.py          테스트 모드 가상 오브제와 프레임 소스
tracker/tension.py          긴장도 수학 (MST/all/near/knn, fold, 오버레이)
conductor/, firmware/       144ch 릴레이 매트릭스 (별도 하드웨어 서브시스템)
docs/exhibition.md          구조·설정 키 ↔ XML 매핑·문제 해결
docs/osc-values.md          OSC 값 정의
docs/wiring.md              릴레이 배선
```

## 테스트

```sh
python -m unittest discover -s tests -q
```

## 설치 (수동)

```bat
py -3 -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
```
