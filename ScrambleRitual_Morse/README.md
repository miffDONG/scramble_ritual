# Scramble Ritual Morse

Morse-style code markers identify physical objects and select matching
sound fragments.

The old `ScrambleRitual_Wave` version identified sound fragments from
waveform silhouettes. This folder switches that identity layer to a
hand-drawn marker protocol (matched to the real acrylic cards, see
`../sampleVideo/IMG_0343.JPG` / `IMG_0344.JPG`):

```text
■  ○ ○ ─ ─ ○ ─ ─ ○
S  b1 b2 b3 b4 b5 b6 b7 b8
```

- `■`: start marker, a filled square (noticeably bigger than the dots).
- `○`: data bit 0, a filled dot.
- `─`: data bit 1, a filled dash drawn along the line.
- There is **no end marker**. The reader anchors on the square and grows
  a straight chain of exactly 8 symbols away from it.
- The read direction is not screen left-to-right. It is the direction
  from the square toward the symbols, so upside-down cards still decode.
- The fitted chain line doubles as the object's reference axis: per-card
  tilt, center, and pairwise spacing/relative-tilt are exported over OSC.
- The default codebook uses 8 slots and 20 IDs with minimum Hamming
  distance 3, so a one-symbol error does not silently become another ID.
  The two reference cards are pinned: `00110110` = ID 1 (IMG_0343),
  `01001101` = ID 2 (IMG_0344).

## Setup (venv)

```bat
cd ScrambleRitual_Morse
py -3 -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
```

macOS/Linux: `python3 -m venv .venv && .venv/bin/pip install -r requirements.txt`

`scripts\start.bat`을 처음 실행하면 위 과정을 자동으로 수행한다.

## Web Tuner (현장 실시간 튜닝)

브라우저에서 카메라를 보면서 모든 인식 옵션을 실시간으로 바꾸는 웹 UI.
2026-07-20 현장 테스트(백라이트 패널, 흰색 각인)용 상황 축 조합 포함.

```bat
scripts\start.bat                 :: 더블클릭 가능. 시뮬레이션으로 시작
scripts\start.bat --camera 0      :: 카메라 0으로 시작
scripts\start.bat --video ..\sampleVideo\morseCode.MOV
```

실행하면 http://localhost:8765 가 자동으로 열린다. 수동 실행은:

```bash
.venv\Scripts\python -m webui.server --camera 0
```

화면 흐름은 위→아래 2단계다 (second-taste/sensing 컨트롤 패널 구성을 이식):

1. **소스** — 상단 **소스 타입** 드롭다운(카메라 / 비디오 / 이미지 /
   시뮬레이션)에서 고르면, 그 아래에 해당하는 선택 드롭다운이 이어진다.
   - 카메라 → 장치명 드롭다운(DirectShow 이름). 선택 즉시 연결.
   - 비디오 → `../sampleVideo` 스캔 드롭다운(또는 직접 경로). 선택 즉시
     재생되고 영상 아래 전송 바에서 재생/일시정지·1프레임 스텝·처음부터·
     시크가 된다 (일시정지 중에도 옵션 변경은 계속 반영).
   - 이미지 → 파일 드롭다운(또는 직접 경로).
   - 시뮬레이션 → 검정 마커 / 밝은 각인(백라이트) 드롭다운.
2. **패턴 옵션 (공통)** — 소스와 무관하게 이진화·블롭·앵커·체인·디코드
   옵션이 즉시 반영 (`docs/options.md`에 전체 목록).

그 외:

- **오버레이/마스크/원본** 탭: 인식 결과와 이진화 마스크를 실시간 확인.
- **테이블 영역(ROI)**: [테이블 영역 지정] 을 켜고 영상 위를 드래그하면
  그 사각형 안에서만 추론한다. 영역 밖(손, 배경 클러터, 판 밖 파형 각인
  등)은 배경으로 채워져 검출·임계값 계산에서 제외된다. 세 뷰 모두에
  노란 박스로 표시되고, 영역은 config 프로파일에 저장된다. [영역 지우기]
  로 해제.
- **추론 정지/시작** (헤더): 파이프라인만 멈추고 스트림은 유지.
- **상황 축**: 프리셋 대신 독립적인 축(앵커 / 각인 색상 / 조명 / 마크
  감도 / 체인 관용도)을 각각 드롭다운으로 골라 조합한다. 축을 고르면
  바뀌는 값이 표시되고 해당 슬라이더가 하이라이트된다
  (`docs/options.md`). 현장 튜닝 절차는 `docs/recognition-plan.md`.
- **안정화 ID**: 프레임 간 비트 다수결 투표(`tracker/stability.py`)를
  거친 안정 ID. 한 프레임 오독이 ID를 흔들지 않는다.
- **OSC 송신**: 안정화 ID를 `/scramble/count`, `/scramble/morse`
  `(id bits x y angle status)`, `/scramble/pair` `(i j dist relTilt)`로
  송신. 호스트/포트/프리픽스 변경 가능, 보낸 메시지는 OSC 모니터 표에
  표시.
- **config 프로파일 (이름별 저장/불러오기)**: 튜닝 결과를 이름을 붙여
  `configs/<이름>.json`으로 저장하고, 드롭다운에서 골라 불러온다. 실내/
  현장/무앵커 등 세팅을 여러 개 만들어 현장에서 즉시 전환할 수 있다.
  저장·불러오기 시 `tracker/config.json`에도 미러링돼 CLI 실행과 서버
  재시작에 그대로 적용된다. **[기본값 복원]** 은 파일이 아니라 코드
  기본값으로 되돌린다.

### 카메라가 안 잡힐 때 (Windows)

OpenCV의 Windows 기본 백엔드인 MSMF는 장치를 "열기"까지는 성공해 놓고
프레임은 하나도 주지 않는 경우가 있다. 이때 콘솔에 다음이 반복된다:

```
[ WARN] global cap_msmf.cpp CvCapture_MSMF::grabFrame videoio(MSMF):
        can't grab frame. Error: -2147483638
```

`-2147483638` = `0x8000000A`("데이터가 아직 준비되지 않음"). Elgato 캡처
장치와 일부 노트북 내장캠에서 재현된다. 그래서 `tracker/camera.py`가
**DirectShow → MSMF → 기본** 순으로 열어 보고, `isOpened()` 가 아니라
**실제 프레임이 나오는 백엔드**만 채택한다. 어떤 백엔드로 붙었는지는
소스 표시줄에 `camera:2(dshow)` 처럼 나온다.

그래도 안 되면 대개 **다른 앱이 장치를 점유**한 것이다:

- **Elgato Camera Hub**를 종료하거나, Facecam 대신 Camera Hub가 만들어
  주는 **Elgato Virtual Camera** 장치를 고른다 (Hub의 보정을 그대로 탄다).
- Windows 카메라 앱 / 화상회의 앱을 닫는다.
- 설정 > 개인 정보 및 보안 > 카메라 에서 "데스크톱 앱이 카메라에
  액세스하도록 허용"을 켠다.

## Quick Start

```bash
cd ScrambleRitual_Morse

# Synthetic Morse cards, no camera needed
python -m tracker.morse_run --sim --show

# One-shot diagnostic on a still photo (saves overlay + mask to diag/morse)
python -m tracker.morse_run --image ../sampleVideo/IMG_0343.JPG

# Provided test video
python -m tracker.morse_run --video ../sampleVideo/morseCode.MOV --show

# Run video and save one overlay per second
python -m tracker.morse_run --video ../sampleVideo/morseCode.MOV --snapshot diag/morse

# Camera
python -m tracker.morse_run --camera 0 --show

# Orbbec RGB-D camera
python -m pip install pyorbbecsdk2
python -m tracker.morse_run --orbbec --show
# Note: Orbbec's macOS SDK wheel is ARM64. Intel Macs need UVC mode
# or a supported Windows/Linux/Apple Silicon machine.

# Built-in granular monitor
python -m tracker.morse_run --camera 0 --sound
python -m tracker.morse_run --orbbec --sound

# Use raw binary value instead of the protected 20-ID codebook
python -m tracker.morse_run --video ../sampleVideo/morseCode.MOV --decode binary --show
```

If you use the project virtualenv, replace `python` with the venv Python
for your machine.

## Sound Matching

`tracker.morse_run` reads all visible valid cards, sorts them left-to-right,
and sends their matched fragments to the existing granular engine.

Default mapping:

```text
Morse ID 01 -> fragment 0 / bank label A0
Morse ID 02 -> fragment 1 / bank label B0
...
```

When the default synth bank has fewer fragments than visible IDs, IDs wrap
around. For a custom table, pass JSON:

```json
{
  "1": "A0",
  "2": "B0",
  "7": 12
}
```

Then run:

```bash
python -m tracker.morse_run --camera 0 --map morse-map.json --sound
```

## Recognition Pipeline

```text
camera/video frame
  -> dark-percentile threshold (pen ink only; the white engraved
     waveform and mid-gray shadows never survive)
  -> per-blob contrast gate against the frame's median brightness
  -> contour glyph candidates: square anchors + data marks
  -> for each square: grow a one-sided collinear chain, refine the
     axis with a straight-line fit, keep exactly 8 ordered symbols
  -> gap-regularity / perpendicular-band / square-size gates
  -> classify each symbol dot vs dash by projection onto the axis
  -> decode bits through codebook (1-bit correction)
  -> Morse ID -> sound fragment; axis/spacing measurements -> OSC
```

Everything that is not a square-anchored chain of 8 marks is treated as
noise. Each card is decoded independently from its own anchor, so
multiple nearby cards do not concatenate; two cards on the same line are
split by the gap-regularity gate (keep cards at least ~3 symbol-gaps
apart).

## Important Parameters

전체 옵션 표와 현장 권장값은 `docs/options.md`, 인식 강화 계획과 현장
튜닝 절차는 `docs/recognition-plan.md`, 용어(aspect·circularity·solidity
등) 정의와 글리프 필터링 조건은 `docs/glossary.md` 참고. 웹 튜너에서
저장하면 config 파일이 갱신된다.

Edit `tracker/config.json`:

- `morse.slots`: default `8`.
- `morse.code_count`: default `20`.
- `morse.min_hamming`: default `3`.
- `morse.correction_distance`: default `1`.
- `morse.threshold_mode`: `percentile` (default) | `otsu` | `fixed` |
  `adaptive`; `morse.threshold > 0` forces a fixed 0-255 threshold.
- `morse.threshold_percentile` / `morse.threshold_clamp`: how deep into
  the dark tail of the histogram the pen threshold sits.
- `morse.min_blob_contrast`: raise to reject faint shadows, lower if the
  pen ink is faint.
- `morse.dark_on_light`: `true` for black pen on bright material.
- `morse.min_glyph_area_px` / `morse.min_glyph_area_frac`: dust floor
  (the fractional one scales with resolution).
- `morse.square_area_ratio`: the square must be this much bigger than
  its chain's median glyph (hand-drawn reference cards measure ~2.4-3.3x).
- `morse.chain_perp_tol`, `morse.chain_gap_ratio_max`,
  `morse.chain_first_gap_ratio_max`: chain straightness and spacing
  gates; widen when handwriting gets looser.
- `morse.max_cards`: default `20`, the table's capacity. Cards past this
  many are dropped from the frame (lowest chain score first), so raise it
  with the number of objects the table actually holds.
- `morse.dash_ratio_min`: along/across projection ratio separating dash
  from dot.

## Tests

```bash
.venv\Scripts\python -m pytest tests -q
```

The Morse tests render synthetic rotated cards (including the 26.07.20
■/★/▲ anchor variants and bright-marks-on-backlight polarity), verify
the protected codebook spacing, check that multiple cards do not get
mixed, and cover the temporal bit-voting stabilizer
(`tests/test_stability.py`).

## Video Note

`../sampleVideo/morseCode.MOV` was shot with an earlier card revision
(9 slots + end circle, faint ink on a backlit table, hand occlusion), so
zero detections on it are expected with the current 8-slot protocol. The
reference inputs for the current protocol are the two still photos.

Some headless OpenCV builds cannot decode iPhone/QuickTime `.MOV` files.
If `--video ../sampleVideo/morseCode.MOV` prints `cannot open video`,
convert it to an OpenCV-readable `.mp4` with your local media tool and run
the same command against that converted file.
