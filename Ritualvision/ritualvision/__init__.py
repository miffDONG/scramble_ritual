"""Ritualvision: reacTIVision-compatible tracker for the ScrambleRitual edge-dash markers.

Modules (same split as reacTIVision, recognition swapped):
  camera          카메라 리소스 연결부   open / grab frames (threaded, latest frame wins)
  camera_options  카메라 옵션 설정부     camera.xml settings, ROI crop
  recognizer      마커 패턴 인식 & 분류부  folded-anchor + edge dashes -> id, x, y, angle
  tracker         session ids and velocities across frames
  tuio            TUIO 1.1 /tuio/2Dobj sender (same wire format as reacTIVision)
  display         preview window
"""

__version__ = "0.1.0"
