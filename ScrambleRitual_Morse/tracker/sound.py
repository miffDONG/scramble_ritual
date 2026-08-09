"""
씬 특징 -> 사운드 파라미터 매핑 + OSC 송출 (Max/MSP 대상).

매핑 철학: 오브제가 정렬·연결될수록 소리는 길고 안정된 입자(intact),
흩어지고 기울고 포개질수록 짧고 조밀한 파편(scramble)이 된다.

scramble = w_disorder*disorder + w_scatter*scatter + w_overlap*overlap_ratio
           - w_chain*chain                            (0~1 클립)

파라미터 (기본 범위는 config 로 조정):
    grain_ms      입자 길이      319ms(정렬) -> 135ms(흐트러짐)
    density_hz    입자 밀도      8 -> 80
    pitch_scatter 피치 산포      0 -> 1   (disorder 직결)
    spray_ms      재생 위치 산포  0 -> 250 (scatter 직결)
    tone          저역<->고역 틸트 -1 -> +1 (chain: 연결될수록 저역으로 가라앉음)

OSC 주소 체계는 docs/osc-map.md 참고. python-osc 미설치/미지정 시 stdout 폴백.
"""

import math

DEFAULTS = {
    "mix": {"disorder": 0.45, "scatter": 0.25, "overlap": 0.30, "chain": 0.20},
    "grain_ms": [319.0, 135.0],
    "density_hz": [8.0, 80.0],
    "spray_ms": [0.0, 250.0],
    "smooth_alpha": 0.35,        # 파라미터 EMA (사운드 급변 방지)
}


def lerp(lo_hi, t):
    return lo_hi[0] + (lo_hi[1] - lo_hi[0]) * t


class SoundMapper:
    def __init__(self, cfg=None):
        self.cfg = dict(DEFAULTS)
        if cfg:
            self.cfg.update(cfg)
        self._prev = None

    def map(self, feat: dict) -> dict:
        m = self.cfg["mix"]
        x = (m["disorder"] * feat["disorder"]
             + m["scatter"] * feat["scatter"]
             + m["overlap"] * feat["overlap_ratio"]
             - m["chain"] * feat["chain"])
        x = max(0.0, min(1.0, x))
        raw = {
            "scramble": x,
            "grain_ms": lerp(self.cfg["grain_ms"], x),
            "density_hz": lerp(self.cfg["density_hz"],
                               max(x, feat["contact_ratio"] * 0.5)),
            "pitch_scatter": feat["disorder"],
            "spray_ms": lerp(self.cfg["spray_ms"], feat["scatter"]),
            "tone": feat["chain"] * -1.0 + feat["disorder"] * 0.5,
            "n": float(feat["n"]),
        }
        # EMA 스무딩 — 프레임 노이즈가 사운드 지터로 번지는 것을 차단
        a = self.cfg["smooth_alpha"]
        if self._prev is None:
            self._prev = raw
        else:
            for k, v in raw.items():
                if k != "n":
                    raw[k] = self._prev[k] + (v - self._prev[k]) * a
            self._prev = raw
        return raw


class OscSender:
    """Max/MSP 로 송출. python-osc 없으면 콘솔 출력 폴백 (--osc 미지정 시도 동일)."""

    def __init__(self, host=None, port=9000, prefix="/scramble"):
        self.prefix = prefix
        self.client = None
        if host:
            from pythonosc.udp_client import SimpleUDPClient
            self.client = SimpleUDPClient(host, port)

    def send(self, params: dict, state, frame_size):
        w, h = frame_size
        if self.client:
            for k, v in params.items():
                self.client.send_message(f"{self.prefix}/param/{k}", float(v))
            for lid, d in state.objects:
                self.client.send_message(
                    f"{self.prefix}/obj",
                    [int(lid), d.cx / w, d.cy / h, d.angle])
            for a, b, (px, py) in state.contacts:
                self.client.send_message(
                    f"{self.prefix}/contact", [int(a), int(b), px / w, py / h])
            for ev, data in state.events:
                if ev in ("merge", "split", "touch", "release"):
                    self.client.send_message(
                        f"{self.prefix}/event/{ev}",
                        [int(i) for i in data["ids"]])
        return params
