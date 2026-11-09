import unittest

from conductor.engine import Engine
from conductor.protocol import N_CELLS


def run_frames(eng, scramble, seconds, fps=20.0):
    dt = 1.0 / fps
    t = 0.0
    for _ in range(int(seconds * fps)):
        t += dt
        eng.update(scramble, dt)
        eng.generate(t)
    return t


class TestEngine(unittest.TestCase):
    def test_state_converges(self):
        eng = Engine()
        run_frames(eng, 1.0, seconds=2.0)
        self.assertGreater(eng.x, 0.95)
        run_frames(eng, 0.0, seconds=3.0)
        self.assertLess(eng.x, 0.05)

    def test_attack_faster_than_release(self):
        eng = Engine()
        eng.update(1.0, 0.135)              # tau_attack 1배 시간
        rise = eng.x
        eng2 = Engine()
        eng2.x = 1.0
        eng2.update(0.0, 0.135)             # 같은 시간에 release는 덜 내려와야 함
        fall = 1.0 - eng2.x
        self.assertGreater(rise, fall)

    def test_max_on_enforced(self):
        eng = Engine({"max_on": 30, "max_flips_per_frame": 144,
                      "relay_cooldown_s": 0.0})
        t = 0.0
        for _ in range(60):
            t += 0.05
            eng.update(1.0, 0.05)
            frame = eng.generate(t)
            self.assertLessEqual(sum(frame), 30)

    def test_max_on_holds_with_partial_commit(self):
        # 회귀 테스트: 토글 예산·쿨다운이 있는 기본 설정에서도
        # '래치된 프레임'의 ON 수가 상한을 절대 넘지 않아야 한다
        eng = Engine()                      # max_on=60, budget=24, cooldown=0.4
        t = 0.0
        for k in range(200):
            t += 0.05
            eng.update(0.5 if k % 40 < 20 else 1.0, 0.05)
            frame = eng.generate(t)
            self.assertLessEqual(sum(frame), eng.cfg["max_on"])

    def test_flip_budget(self):
        eng = Engine({"max_flips_per_frame": 10, "relay_cooldown_s": 0.0})
        before = eng.flips_total
        eng.update(1.0, 1.0)
        eng.generate(0.1)
        self.assertLessEqual(eng.flips_total - before, 10)

    def test_cooldown_blocks_retoggle(self):
        # 불변식: 어떤 릴레이도 cooldown 안에 두 번 토글되지 않는다
        cooldown = 1.0
        eng = Engine({"relay_cooldown_s": cooldown, "max_flips_per_frame": 144})
        last = [None] * N_CELLS
        prev = list(eng.frame)
        t = 0.0
        for _ in range(40):
            t += 0.05
            eng.update(0.7, 0.05)           # 혼합 상태 — 토글 다수 유발
            eng.generate(t)
            for i in range(N_CELLS):
                if eng.frame[i] != prev[i]:
                    if last[i] is not None:
                        self.assertGreaterEqual(t - last[i], cooldown - 1e-9)
                    last[i] = t
            prev = list(eng.frame)

    def test_all_off(self):
        eng = Engine()
        eng.update(1.0, 1.0)
        eng.generate(0.1)
        self.assertEqual(sum(eng.all_off()), 0)
        self.assertEqual(len(eng.frame), N_CELLS)


if __name__ == "__main__":
    unittest.main()
