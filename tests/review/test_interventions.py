"""End-to-end intervention review on synthetic cached logs. No real reviews, videos or training."""
import contextlib
import gzip
import json
import os
import subprocess
from pathlib import Path

import pytest

from nnslr_tools import drive_logs, drive_review, interventions, speed_review
from nnslr_tools.speed_review import NS
from review_helpers import ROUTE, TOKEN, RunningServer, sign_in

T0 = 100 * NS


def t(s):
    return T0 + round(s * NS)


def signal(*changes):
    return [[t(s), v] for s, v in changes]


def log_data():
    return {'span': [T0, t(60)], 'meta': {'car': {'passive': False}}, 'events': [],
            'series': {'gas': signal((0, False), (20, True), (26, False)), 'brake': signal((0, False), (44, True)),
                       'long_active': signal((0, True), (20, False), (40, True), (44, False)),
                       'cruise_enabled': signal((0, False)), 'sd_enabled': signal((0, True), (44, False)),
                       'v_ego': signal((0, 40), (21, 45), (45, 35)), 'vis_limit': signal((0, 50)),
                       'v_cruise': signal((0, 55))}}


def test_only_manual_longitudinal_edges_with_known_ownership_are_proposed():
    data = log_data()
    data['series']['long_active'] = signal((0, True), (19.95, False), (40, True), (44, False))
    data['series']['cruise_enabled'] = signal((0, False), (30, True), (33, False))
    data['series']['gas'] += signal((46, None), (48, True), (50, False), (52, True))
    data['events'] = [[t(20), 'sign', 50], [t(32), 'button', 'cancel', True], [t(32.2), 'button', 'cancel', False],
                      [t(44), 'onroad', 'onroadEvents', [['pedalPressed', ['userDisable']]]]]
    rows = interventions.candidates(data)
    assert [(s, k, owner) for _, s, k, owner in rows] == [
        (t(20), 'gas', 'openpilot'), (t(32), 'cancel', 'cruise'), (t(44), 'brake', 'openpilot')]
    data['series']['brake'] = []
    assert interventions.candidates(data)[-1][2] == 'cancel'
    data['events'][-1][3] = [['fault', ['immediateDisable']]]
    assert all(row[1] != t(44) for row in interventions.candidates(data))
    # A hole immediately before the pedal is not evidence that longitudinal control was active.
    data['series']['long_active'].insert(1, [t(19.93), None])
    assert all(row[1] != t(20) for row in interventions.candidates(data))


@pytest.fixture
def indexed(tmp_path, monkeypatch):
    monkeypatch.setenv('COMMA_VIDEOS_DIR', str(tmp_path / 'empty'))
    monkeypatch.setenv('NNSLR_PROGRESS_FILE', str(tmp_path / 'progress.json'))
    route_root = tmp_path / 'raw/routes' / ROUTE
    route_root.mkdir(parents=True)
    data = log_data()
    path = drive_logs.extract_path(tmp_path, ROUTE, 0, 'abcd')
    path.parent.mkdir(parents=True)
    path.write_bytes(gzip.compress(json.dumps(data).encode()))
    drives = drive_review.store_for(tmp_path)
    files = {'0': {'sha256': 'abcd', 'kind': 'rlog'}}
    summary = {'t0_ns': T0, 'span_ns': data['span'], 'meta': data['meta']}
    drives._run([("INSERT INTO scans(route,state,files,summary) VALUES(?,'done',?,?)",
                  (ROUTE, json.dumps(files), json.dumps(summary)))])
    interventions.prepare(tmp_path)
    return speed_review.store_for(tmp_path), drives


def test_refresh_preserves_a_persons_decision_and_only_accepted_intervals_are_targets(tmp_path, indexed):
    store, drives = indexed
    path = f'/api/speed-review/drives/{ROUTE}/interventions'
    server = RunningServer(tmp_path, token=TOKEN, require_token=True)
    try:
        session = sign_in(server)
        server.app.panel.actions_enabled = True
        tok = {'X-Review-Token': TOKEN}
        def request(method, path, headers=session, body=None):
            status, _, data = server.request(method, path, body=body, headers=headers)
            return status, json.loads(data)

        events = request('GET', '/api/speed-review/interventions')[1]['events']
        gas, brake = events
        assert [(e['kind'], e['second'], e['decision']) for e in events] == [('gas', 20, 'pending'), ('brake', 44, 'pending')]
        detail = request('GET', path)[1]
        assert detail['vision'] == [[T0, 50]] and detail['pre_roll_ns'] == 15 * NS
        body = {'decision': 'driver', 'event': gas['event'], 'event_revision': 0, 'start_ns': t(21), 'end_ns': t(26),
                'base_revision': detail['revision']}
        assert request('POST', path, tok, body)[0] == 403
        assert request('POST', path, {**session, 'Origin': 'http://elsewhere.example'}, body)[0] == 403
        assert request('POST', path, body={**body, 'start_ns': t(18)})[1]['error'] == 'not_driver'
        # A person can correct the approach even while the machine controlled it.
        assert request('POST', path, body={**body, 'decision': 'block', 'event': brake['event'], 'kph': 30,
                                           'start_ns': t(40), 'end_ns': t(46)})[1]['revision'] == 1
        assert request('POST', path, body=body)[1]['error'] == 'stale_revision'
        assert request('POST', path, body={**body, 'base_revision': 1})[1]['revision'] == 2
        interventions.prepare(tmp_path)
        # A proposal going away on a later scan cannot retract a person's approved answer.
        interventions.replace_candidates(store, ROUTE, [])
        with contextlib.closing(store._connect()) as db:
            rows = interventions.approved(db)[ROUTE]
            assert [(r['decision'], r['start_ns'], r['end_ns'], r['kph']) for r in rows] == [
                ('driver', t(21), t(26), None), ('block', t(40), t(46), 30)]
            # Withdraw the target, then reopen the queue: no stale speed block survives it.
            assert request('POST', path, body={'event': brake['event'], 'decision': 'skip',
                                               'event_revision': 1, 'base_revision': 2})[1]['revision'] == 3
            assert len(interventions.approved(db)[ROUTE]) == 1
        assert store.history(ROUTE)[-1]['reviewer'] == 'admin'
        server.app.panel.actions_enabled = False
        assert request('POST', path, body=body)[1]['error'] == 'actions_disabled'
    finally:
        server.close()


@pytest.mark.skipif(not os.getenv('NNSLR_TEST_BROWSER_PYTHON') or not os.getenv('NNSLR_TEST_CHROMIUM'),
                    reason='needs the local Playwright browser')
def test_review_in_browser(tmp_path, indexed, monkeypatch):
    # The real UI and review API with isolated data. No media transcoding or GPU job.
    interventions.replace_candidates(indexed[0], ROUTE, [])
    server = RunningServer(tmp_path, token=TOKEN, require_token=False)
    try:
        page_test = Path(__file__).with_name('intervention_browser.py')
        monkeypatch.setattr(server.app.panel, 'activities', lambda: [])
        server.app.panel.actions_enabled = True
        subprocess.run([os.environ['NNSLR_TEST_BROWSER_PYTHON'], str(page_test), str(server.port), ROUTE,
                        os.environ['NNSLR_TEST_CHROMIUM'], str(tmp_path)], check=True, timeout=90)
        assert [r['state'] for r in indexed[0].history(ROUTE)] == ['driver', 'block', 'skip']
    finally:
        server.close()
