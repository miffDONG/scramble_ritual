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
tracker/tension.py          긴장도 수학 (MST/all/near/knn, fold, 오버레이)
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
