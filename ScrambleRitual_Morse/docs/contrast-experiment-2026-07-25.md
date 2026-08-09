# 2026-07-25 스냅샷 대비·노이즈 실험

## 결론

현재 5장에서는 **CLAHE 단독**을 실시간 기본 후보로, **1–99% percentile autocontrast**를 저비용 대안으로 권장한다. 조명 평탄화(flat-field)+CLAHE와 Retinex는 장면을 보기 좋게 만들 수 있으나, 모스 판독 관점에서는 아크릴 외곽선·손·라이트박스 테두리·센서 입자를 함께 증폭한다.

최종 판독 파이프라인의 추천 시작점은 다음과 같다.

1. grayscale
2. bilateral filter `(d=5, sigmaColor=22, sigmaSpace=22)`
3. CLAHE `(clipLimit=2.0, tileGridSize=12×12)`
4. local Gaussian adaptive threshold, dark-on-light, block size `min(width,height)/18`, `C=9`
5. morphology open `3×3`, close `3×3`
6. 삼각형 내부 ROI 또는 검출된 모스 체인 주변에서만 connected-component/chain 판정

특히 6번이 중요하다. 대비 강화만으로는 모스와 아크릴 테두리를 구분할 수 없다. 삼각 오브제 검출 → 내부 마스크 축소(erode) → 모스 체인 검출 순서로 공간적 노이즈를 먼저 제거해야 한다.

## 비교한 방법

| 방법 | 관찰 결과 | 판정 |
|---|---|---|
| baseline | 깨끗하지만 희미한 패턴 일부가 끊김 | 기준선 |
| autocontrast | 전역 밝기 범위를 안정적으로 확대, 잡음 증가가 비교적 작음 | 추천(저비용) |
| gamma + stretch | 암부는 살아나지만 프레임별 밝기 변화에 민감 | 보류 |
| CLAHE | 조명 띠가 있어도 약한 점/대시의 국부 대비가 가장 안정적 | **추천** |
| flat-field + CLAHE | 저주파 조명 보정은 강하지만 외곽/입자도 과증폭 | 제한적 사용 |
| Retinex + CLAHE | 큰 조명 변화는 완화하나 희미한 회색 모스 자체도 사라지는 사례 | 비추천 |

## 평가 방법과 주의점

모든 방법에 동일한 이진화와 morphology를 적용했다. CSV에는 entropy, local contrast, foreground ratio, connected components, plausible-size components, tiny specks, review score를 기록했다.

`review_score`는 local contrast를 높이고 마스크 범람과 작은 speck를 벌점 처리한 **후보 정렬용 휴리스틱**이다. 정답 라벨이 없는 현재 데이터에서는 검출 정확도가 아니며, flat-field처럼 큰 구조 경계를 과증폭한 결과가 높은 점수를 받을 수 있다. 최종 비교 기준은 다음 순서로 육안 검토해야 한다.

1. 시작 마커와 8개 점/대시가 끊기지 않는가
2. 인접한 점이 합쳐지거나 대시가 분절되지 않는가
3. 아크릴 삼각 외곽과 나사 구멍이 체인 후보에 섞이지 않는가
4. 손이 들어온 프레임에서도 비가림되지 않은 오브제의 결과가 유지되는가

## 실제 프로젝트 디코더의 ID 결과

각 enhanced 이미지에는 `scripts/start.bat`이 사용하는 `tracker/config.json`과 동일하게 resize(`proc_max_dim=1280`), table ROI, `tracker.morse.detect_cards()`, 20-ID 보호 codebook을 적용했다. 결과는 `metrics.csv`의 `detected_cards`, `object_ids`, `bits`, `statuses`, `card_scores`와 `decoded/`, `sheets/*_decoded.jpg`에서 확인할 수 있다.

| snapshot | baseline | autocontrast | gamma | CLAHE | flat-field+CLAHE | Retinex+CLAHE |
|---|---|---|---|---|---|---|
| 09-47-35 | ID7 | ID10, ID1 | ID7 | ID19, ID10, ID1 | ID10, ID10, ID1 | ID1 |
| 09-47-40 | 없음 | ID11, ID7 | ID3 | ID5, ID1 | ?, ID5, ID10, ID1 | ID1 |
| 09-47-42 | 없음 | ID11, ID7 | ID10 | ID10, ID7 | ID10 | ID7 |
| 09-48-39 | 없음 | 없음 | 없음 | 없음 | 없음 | 없음 |
| 09-51-35 | 없음 | 없음 | 없음 | 없음 | 없음 | 없음 |

ID는 검출된 bit열을 codebook으로 해석한 결과이지 아직 정답 판정은 아니다. 현재 설정은 `require_start=false`인 anchorless 모드라서 파형·문자·아크릴 경계의 8개 blob이 일렬로 놓이면 잘못된 ID 체인도 만들 수 있다. 특히 flat-field 결과의 많은 ID는 높은 recall이 아니라 과증폭에 따른 false positive일 가능성이 높다. 반대로 09-47-35/40에서 여러 알고리즘에 반복적으로 나타나는 ID1, 09-47-40/42에서 반복되는 ID7/ID10은 우선 검증할 안정 후보이다.

09-48-39와 09-51-35는 어느 방법에서도 ID가 나오지 않았다. 대비만의 문제가 아니라 작은 모스 크기, 비가림, 삼각형 회전, ROI/면적·체인 기하 조건이 함께 병목인 것으로 보인다.

## 산출물

- `diag/contrast_experiment/index.html`: 이미지별 enhanced/mask 비교 갤러리와 전체 지표 순위
- `diag/contrast_experiment/metrics.csv`: 5장 × 6알고리즘 = 30개 결과 지표
- `diag/contrast_experiment/enhanced/`: 전체 해상도 대비 강화 결과
- `diag/contrast_experiment/masks/`: 동일 후처리를 적용한 이진 마스크
- `diag/contrast_experiment/sheets/`: 이미지별 2열 비교 시트
- `tools/contrast_experiment.py`: 재현 및 신규 스냅샷 추가 실험 스크립트

재실행 예:

```powershell
.\.venv\Scripts\python.exe tools\contrast_experiment.py `
  ..\sampleVideo\Snapshot_*.png `
  --out diag\contrast_experiment
```

## 다음 실험

정확한 최적값을 고르려면 5장 각각에서 모스 체인 bounding box와 기대 bit string을 소량 라벨링한다. 그 뒤 `CLAHE clipLimit`, tile 크기, adaptive block/C를 grid search하고, 픽셀 대비 점수가 아니라 **8-symbol chain recall / false chain 수 / bit accuracy**로 선택한다. 프레임 간에는 단일 프레임 결과보다 기존 `stability.py`의 투표를 이용해 순간적인 점·대시 누락을 흡수하는 편이 안전하다.
