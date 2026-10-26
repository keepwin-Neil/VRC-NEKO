"""长时间待机：同一待机意图内周期性回到早期稳定历史，接缝平滑。"""
import math
import numpy as np
import _bootstrap
from integrations.motion_service.timeline import MotionTimeline, blend_seam, _rot_exp, _rot_log


def rz(deg):
    return _rot_exp(np.array([0., 0., math.radians(deg)]))


class Generator:
    fps = 20
    frames = 40

    def __init__(self):
        self.histories = []
        self.calls = 0

    def generate_candidate(self, prompt, constraints, history=None):
        self.histories.append(None if history is None else list(history))
        self.calls += 1
        # 每个候选整体比上一个多转 5°，模拟漂移；首帧与上一候选末帧不连续。
        rot = rz(5. * self.calls).tolist()
        pose = {'local_rot_mats': [[rot, rot]] * 40, 'root_positions': [[0., .9, 0.]] * 40,
                'foot_contacts': [[1, 1, 1, 1]] * 40, 'root_heading': [0.] * 40}
        sample = list(history or []) + [(self.calls, i) for i in range(40)]
        return pose, sample

    def history_at(self, sample, frames):
        end = len(sample) - 40 + frames
        return sample[max(0, end - 20):end]


def test_rotation_log_exp_roundtrip():
    v = np.array([[.1, -.2, .3], [0., 0., 1e-9], [1.2, .4, -.7]])
    assert np.allclose(_rot_log(_rot_exp(v)), v, atol=1e-6)


def test_blend_seam_starts_at_previous_and_fades_out():
    pose = {'local_rot_mats': [[rz(20).tolist()]] * 40, 'root_positions': [[.1, .9, 0.]] * 40, 'root_heading': [.3] * 40}
    prev = {'local_rot_mat': [rz(0).tolist()], 'root_position': [0., .9, 0.], 'root_heading': 0.}
    out = blend_seam(pose, prev, 12)
    first = np.degrees(np.linalg.norm(_rot_log(np.array(out['local_rot_mats'][0][0]))))
    assert first < 20 / 12 + .5
    steps = [np.degrees(np.linalg.norm(_rot_log(np.array(out['local_rot_mats'][i][0]).T @ np.array(out['local_rot_mats'][i + 1][0]))))
             for i in range(39)]
    assert max(steps) < 4
    assert np.allclose(out['local_rot_mats'][12], pose['local_rot_mats'][12])
    assert abs(out['root_positions'][0][0]) < .02 and out['root_positions'][20] == [.1, .9, 0.]
    assert abs(out['root_heading'][0]) < .05 and out['root_heading'][20] == .3


def test_unbounded_idle_reanchors_to_early_history_within_same_intent():
    g = Generator()
    t = MotionTimeline(g, lambda *_: {}, future_limit=10 ** 6)
    t.idle_refresh_s = 10.  # 每 5 个候选
    t.idle_anchor_candidate = 2
    t.update('stand', {'mode': 'idle'}, source='base', duration_s=None)
    for _ in range(12):
        assert t.generate()
    anchor = g.histories[3]  # 候选 2 结束后的历史 = 候选 3 的输入
    assert g.histories[5] == anchor and g.histories[10] == anchor
    assert g.histories[6] != anchor
    assert t.idle_refreshes == 2
    blocks = list(t.future)
    seam = next(b for b in blocks if b['first_frame'] == 200)
    before = next(b for b in blocks if b['end_frame'] == 200)
    a = np.array(before['pose']['local_rot_mats'][-1][0]); b = np.array(seam['pose']['local_rot_mats'][0][0])
    assert np.degrees(np.linalg.norm(_rot_log(a.T @ b))) < 3


def test_bounded_or_moving_intents_are_not_reanchored():
    g = Generator()
    t = MotionTimeline(g, lambda *_: {}, future_limit=10 ** 6)
    t.idle_refresh_s = 4.
    t.update('wave', {'mode': 'free_action'}, duration_s=None)
    for _ in range(8):
        t.generate()
    assert t.idle_refreshes == 0
