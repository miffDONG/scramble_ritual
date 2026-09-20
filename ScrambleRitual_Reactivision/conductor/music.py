"""
Scramble Ritual — 오디오 입력 + 특징 추출 (numpy 필요, sounddevice 는 마이크/재생 시에만)

AudioAnalyzer.feed(samples, t, dt) -> feat dict:
    rms            : AGC 정규화된 음량 (0..1)
    bass           : 160Hz 미만 저역 에너지 (0..1) — sweep 진폭
    bands          : 로그 간격 12대역 레벨 (각 0..1) — spectrum 열 높이
    onset          : 이번 프레임에 타격(온셋)이 있었는가
    onset_strength : 온셋 세기 (0..1) — ripple 진폭·sweep 방향 회전

온셋 = 스펙트럴 플럭스(양의 스펙트럼 증가량 합)가 최근 이력 중앙값의
onset_thresh 배를 넘는 순간. onset_refractory_s 안에는 재발화하지 않는다.

AGC: 대역/음량마다 러닝 피크(agc_decay_s 감쇠)로 나눠 정규화 — 곡·볼륨이
바뀌어도 0..1 스케일이 유지된다. 피크에 절대 플로어를 둬 무음이 0이 되게 한다.

입력 소스 (공통 인터페이스 read(t, block) -> float mono 배열):
    WavSource : WAV 파일 (8/16/24/32bit PCM, float32). t = 재생 클럭과 동기
    SimSource : 합성 드럼 루프 (킥/스네어/햇/베이스) — 오디오 장치 없이 검증
    MicSource : sounddevice 실시간 입력 (마이크/라인인). t 무시, 최신 블록 반환
"""

import math
import wave as _wave
from collections import deque

import numpy as np

AUDIO_DEFAULTS = {
    "block": 2048,               # 분석 블록 (44.1kHz 에서 ~46ms)
    "n_bands": 12,
    "fmin": 40.0,
    "fmax": 8000.0,
    "bass_hz": 160.0,
    "onset_thresh": 1.8,         # 플럭스 / 중앙값 발화 비율
    "onset_refractory_s": 0.12,
    "agc_decay_s": 6.0,          # AGC 피크 감쇠 시간상수
    "rms_gate": 0.005,           # 이보다 조용하면 온셋 무시 (노이즈 게이트)
}


class AudioAnalyzer:
    def __init__(self, samplerate, cfg=None):
        self.cfg = dict(AUDIO_DEFAULTS)
        if cfg:
            self.cfg.update(cfg)
        self.sr = samplerate
        n = self.cfg["block"]
        self.block = n
        self.window = np.hanning(n)
        freqs = np.fft.rfftfreq(n, 1.0 / samplerate)
        edges = np.geomspace(self.cfg["fmin"], self.cfg["fmax"],
                             self.cfg["n_bands"] + 1)
        self._band_bins = []
        for i in range(self.cfg["n_bands"]):
            bins = np.where((freqs >= edges[i]) & (freqs < edges[i + 1]))[0]
            if len(bins) == 0:                 # 저역 대역이 빈당 폭보다 좁을 때
                bins = np.array([np.argmin(np.abs(freqs - edges[i]))])
            self._band_bins.append(bins)
        self._bass_mask = freqs < self.cfg["bass_hz"]

        self._prev_mag = None
        self._flux_hist = deque(maxlen=43)     # 20fps 기준 ~2초 이력
        self._band_peak = np.full(self.cfg["n_bands"], 1e-3)
        self._bass_peak = 1e-3
        self._rms_peak = 1e-2
        self._last_onset_t = -1e9

    def feed(self, samples, t, dt):
        n = self.block
        x = np.asarray(samples, dtype=np.float64)
        if len(x) < n:
            x = np.pad(x, (0, n - len(x)))
        elif len(x) > n:
            x = x[:n]

        rms_raw = float(np.sqrt(np.mean(x * x)))
        mag = np.abs(np.fft.rfft(x * self.window)) * (2.0 / n)

        # 스펙트럴 플럭스 — 이전 블록 대비 양의 증가량만
        flux = 0.0
        if self._prev_mag is not None:
            flux = float(np.sum(np.clip(mag - self._prev_mag, 0.0, None)))
        self._prev_mag = mag

        band_raw = np.array([float(mag[b].mean()) for b in self._band_bins])
        bass_raw = float(mag[self._bass_mask].mean()) if self._bass_mask.any() else 0.0

        # AGC — 러닝 피크로 정규화, 절대 플로어로 무음 억제
        decay = math.exp(-dt / max(self.cfg["agc_decay_s"], 1e-4))
        self._band_peak = np.maximum(band_raw, self._band_peak * decay)
        self._bass_peak = max(bass_raw, self._bass_peak * decay)
        self._rms_peak = max(rms_raw, self._rms_peak * decay)
        bands = np.clip(band_raw / np.maximum(self._band_peak, 1e-3), 0.0, 1.0)
        bass = min(1.0, bass_raw / max(self._bass_peak, 1e-3))
        rms = min(1.0, rms_raw / max(self._rms_peak, 1e-2))

        # 온셋 판정
        onset = False
        strength = 0.0
        thresh = self.cfg["onset_thresh"]
        if len(self._flux_hist) >= 8 and rms_raw >= self.cfg["rms_gate"]:
            # 중앙값 절대 플로어: 스테디 톤의 수치 지터가 비율을 폭주시키지 않게
            med = max(float(np.median(self._flux_hist)), 1e-4)
            ratio = flux / med
            if (ratio >= thresh
                    and (t - self._last_onset_t) >= self.cfg["onset_refractory_s"]):
                onset = True
                strength = max(0.0, min(1.0, (ratio - thresh) / (2.0 * thresh)))
                self._last_onset_t = t
        self._flux_hist.append(flux)

        return {
            "rms": rms,
            "bass": bass,
            "bands": bands.tolist(),
            "onset": onset,
            "onset_strength": strength,
            "flux": flux,
        }


# ── 입력 소스 ────────────────────────────────────────────────────────


class WavSource:
    """WAV 파일 전체를 float mono 로 로드. read(t, block) 는 t초 시점의 블록."""

    def __init__(self, path):
        with _wave.open(path, "rb") as w:
            nch = w.getnchannels()
            sw = w.getsampwidth()
            self.samplerate = w.getframerate()
            raw = w.readframes(w.getnframes())
        if sw == 2:
            data = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
        elif sw == 4:
            data = np.frombuffer(raw, dtype=np.int32).astype(np.float32) / 2147483648.0
        elif sw == 1:
            data = (np.frombuffer(raw, dtype=np.uint8).astype(np.float32) - 128.0) / 128.0
        elif sw == 3:                                     # 24bit — 상위 3바이트 조합
            b = np.frombuffer(raw, dtype=np.uint8).reshape(-1, 3).astype(np.int32)
            v = (b[:, 0] | (b[:, 1] << 8) | (b[:, 2] << 16))
            v = np.where(v & 0x800000, v - 0x1000000, v)
            data = v.astype(np.float32) / 8388608.0
        else:
            raise ValueError(f"지원하지 않는 샘플 폭: {sw*8}bit")
        if nch > 1:
            data = data.reshape(-1, nch).mean(axis=1)
        self.data = data
        self.duration = len(data) / self.samplerate

    def read(self, t, block):
        pos = max(0, int(t * self.samplerate))
        seg = self.data[pos:pos + block]
        if len(seg) < block:
            seg = np.pad(seg, (0, block - len(seg)))
        return seg


class SimSource:
    """합성 드럼 루프(4초, 120BPM) — 오디오 장치·파일 없이 전 경로 검증용."""

    def __init__(self, samplerate=44100, seed=319):
        self.samplerate = samplerate
        self.duration = None                              # 무한 루프
        sr = samplerate
        n = 4 * sr
        rng = np.random.default_rng(seed)
        tt = np.arange(n) / sr
        loop = np.zeros(n, dtype=np.float32)

        def burst(start, dur, sig):
            i0 = int(start * sr)
            i1 = min(n, i0 + int(dur * sr))
            loop[i0:i1] += sig[: i1 - i0].astype(np.float32)

        for beat in np.arange(0.0, 4.0, 0.5):             # 킥 (55Hz 감쇠 사인)
            tau = np.arange(int(0.25 * sr)) / sr
            burst(beat, 0.25, 0.9 * np.sin(2 * np.pi * 55 * tau) * np.exp(-tau / 0.12))
        for beat in np.arange(1.0, 4.0, 2.0):             # 스네어 (노이즈 + 200Hz)
            tau = np.arange(int(0.2 * sr)) / sr
            noise = rng.standard_normal(len(tau))
            burst(beat, 0.2, (0.5 * noise + 0.3 * np.sin(2 * np.pi * 200 * tau))
                  * np.exp(-tau / 0.08) * 0.6)
        for beat in np.arange(0.25, 4.0, 0.5):            # 햇 (짧은 노이즈)
            tau = np.arange(int(0.05 * sr)) / sr
            burst(beat, 0.05, 0.25 * rng.standard_normal(len(tau)) * np.exp(-tau / 0.02))
        loop += (0.15 * np.sin(2 * np.pi * 82 * tt)       # 베이스 페달
                 * (0.6 + 0.4 * np.sin(2 * np.pi * 0.25 * tt))).astype(np.float32)
        peak = float(np.abs(loop).max())
        self.data = loop / max(peak, 1e-9)

    def read(self, t, block):
        pos = int(t * self.samplerate) % len(self.data)
        idx = (pos + np.arange(block)) % len(self.data)
        return self.data[idx]


class MicSource:
    """sounddevice 실시간 입력. read() 는 항상 가장 최근 블록을 돌려준다."""

    def __init__(self, device=None, samplerate=None, block=2048):
        import sounddevice as sd
        info = sd.query_devices(device, "input")
        self.samplerate = int(samplerate or info["default_samplerate"])
        self.duration = None
        self.block = block
        self._buf = np.zeros(block * 4, dtype=np.float32)
        self._stream = sd.InputStream(
            device=device, channels=1, samplerate=self.samplerate,
            blocksize=0, dtype="float32", callback=self._cb)
        self._stream.start()

    def _cb(self, indata, frames, time_info, status):
        mono = indata[:, 0]
        k = min(len(mono), len(self._buf))
        self._buf = np.roll(self._buf, -k)
        self._buf[-k:] = mono[-k:]

    def read(self, t, block):
        return self._buf[-block:].copy()

    def close(self):
        self._stream.stop()
        self._stream.close()
