Scramble Ritual

- ScrambleRitual_display : **현행** 전시 시스템 (2026-11-01 `ScrambleRitual_Reactivision`에서 이름 변경). 마커 트래커(reacTIVision 또는 Ritualvision) → TUIO → 긴장도(MST) → OSC(SuperCollider·TouchDesigner).
  실행: `ScrambleRitual_display\scripts\exhibition_start.bat` (`--tracker ritualvision | reactivision`). 상세는 폴더 안 README와 `docs/exhibition.md`.
- Ritualvision : 엣지 대시 마커용 트래커. reacTIVision과 같은 설정 파일·TUIO 출력, 인식부만 교체. `Ritualvision.exe`는 `build.bat`으로 빌드(git 제외).
- ScrambleRitual_Morse : 이전 버전(모스 부호 패턴 인식 파이프라인). 기록 보존용, 더 이상 개발하지 않음. 태그 `morse-final` = 마지막 상태.
- reactivision : reacTIVision 1.5.1 win64 배포판 복사본(git 제외). 서버가 여기의 camera.xml/reacTIVision.xml을 생성한다.
- sampleVideo : 테스트 이미지 및 영상
