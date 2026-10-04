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

인식 방식은 reacTIVision 하나뿐이다. 서버는 카메라를 직접 열지 않는다.

## 실행

```bat
scripts\exhibition_start.bat            :: http://localhost:8765 (첫 실행 시 .venv 생성·설치)
scripts\exhibition_start.bat --no-rtv   :: reacTIVision을 직접 띄울 때
```

수동: `.venv\Scripts\python -m webui.exhibit_server [--port 8765] [--rtv-exe PATH] [--no-rtv]`

### 테스트 (가상 오브제, reacTIVision 없음)

```bat
scripts\test_start.bat                 :: Windows  http://localhost:8765  (= exhibit_server --test)
scripts/test_start.command             :: macOS    (Finder 더블클릭 / 터미널)
python3 scripts/launch.py test         :: 어느 OS든 직접
```

세 진입점 모두 `scripts/launch.py`로 넘어간다: OS(Windows/macOS/Linux) 판별 → `.venv` 유무와
requirements 설치 여부 확인(`.venv/.requirements.stamp`) → 없거나 `requirements.txt`가 바뀌었으면
OS에 맞는 레이아웃(`Scripts\python.exe` / `bin/python3`)으로 생성·설치 → 브라우저 열고 서버 실행.
Python 3.10+가 시스템에 있어야 한다(Windows: python.org 설치 시 "Add to PATH", macOS: `brew install python`).

- reacTIVision을 실행하지도, TUIO(3333)를 받지도 않는다. 카메라 없이 동작.
- 웹 UI 1번 패널이 **테스트 오브제**로 바뀐다: 6각형 오브제 추가/삭제, ID **0~24**(총 25개, 중복 불가) 수정,
  미리보기에서 드래그로 위치 이동(테이블 영역 안으로 제한), 클릭해서 ID·기울기 편집.
- 가상 오브제는 카메라 fps(`rtv_fps`, 기본 120)마다 한 프레임으로 **전시와 같은 경로**(영역 → 긴장도 → OSC)를 탄다.
  설정 파일(`webui/exhibit_config.json`), OSC 대상·형식·프리픽스가 전시와 같다.
- 6각형 크기는 긴장도 1이 되는 접촉 거리에서 변끼리 맞닿도록 그린다. 배치는 `webui/test_objects.json`에 저장.

- reacTIVision 1.5.1 win64 배포판은 프로젝트 **밖** `C:\Users\baksh\scramble_ritual\reactivision\`에 둔다.
  없으면 첫 실행 때 `~/Downloads/reacTIVision-1.5.1-win64/…`에서 자동 복사한다.
- 서버가 그 폴더의 `camera.xml`/`reacTIVision.xml`을 웹 설정으로 생성한다(원본은 `*.orig`).
- 설정은 `webui/exhibit_config.json`에 자동 저장, 이름별 프로파일은 `configs/exhibit/<이름>.json`.

## 웹 UI

| 패널 | 내용 |
| --- | --- |
| 1 전시 카메라 | 카메라 → 해상도(카메라 지원 목록) → fps(해상도별 목록, 기본 120), 노출·게인 등 camera.xml, gradient·tile·TUIO 포트 등 reacTIVision.xml, 적용(재시작)/시작/정지/XML 가져오기, 상태·로그 |
| 2 테이블 영역·오브제 크기 | 미리보기 드래그로 ROI, 한 변 측정(긴장도 기준) |
| 3 긴장도 | 연결(**MST** 기본 / all / near / knn), 결합(max/avg/min), 그래프 표시 |
| 4 OSC 송신 | 마스터 on/off, prefix, 대상 목록(이름/host/port/형식/on) |
| 5 프로파일 | 이름 저장 / 불러오기 / 기본값 |
| 모니터 | 합성 오버레이(TUIO 값으로 그림), 오브제 표, OSC 모니터 |

## OSC

주소 `/scramble/obj` 하나, 오브제 1개당 1메시지, 카메라 프레임(TUIO fseq)마다.
기본 형식은 **dict**(키, 값 번갈아: `"id", 12, "bits", "00001100", "x", …, "tension", …`),
대상별로 list(위 순서의 위치 인자) / json(인자 1개 = JSON 문자열)으로 바꿀 수 있다. 정의는 `docs/osc-values.md`.

## 긴장도 — MST 연결 그래프 (기본)

오브제 중심거리로 **최소 신장 트리**를 만들어(n−1 간선) 모든 오브제를 하나의 나무로 잇고,
각 오브제는 자기 트리 이웃과의 쌍 긴장도를 fold(기본 max)한다. 오버레이의 연결선이 곧 이 트리다.
`all`(n−1 전부) / `near`(반경) / `knn`(최근접 k) 방식도 UI에서 고를 수 있다.

## 구성

```
webui/exhibit_server.py     Flask 서버 + 파이프라인 (TUIO → ROI → 긴장도 → OSC)
webui/static/exhibit.html   웹 UI (단일 파일)
tracker/reactivision.py     reacTIVision 자식 프로세스, -l 파싱, XML 생성/가져오기, TUIO 수신
tracker/tension.py          긴장도 수학 (MST/all/near/knn, fold, 오버레이)
tracker/virtual.py          테스트 모드 가상 오브제 (ID 0~24) + TUIO 대체 프레임 소스
conductor/, firmware/       144ch 릴레이 매트릭스 (별도 하드웨어 서브시스템)
docs/exhibition.md          구조·설정 키 ↔ XML 매핑·문제 해결
docs/osc-values.md          OSC 값 정의
docs/wiring.md              릴레이 배선
```

## 테스트

```bat
.venv\Scripts\python -m unittest discover -s tests -q
```

## 설치 (수동)

```bat
py -3 -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
```
