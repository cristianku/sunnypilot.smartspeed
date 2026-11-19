"""Manual longitudinal interventions. Finding events never supplies a training answer.

The queue and the reviewer's one selected interval per event live in speed_review.sqlite.
Find reads already-extracted logs, not videos or neural networks. Only the site's human
review endpoint may accept an interval. Half-open intervals use the original log clock.
"""
from __future__ import annotations

import bisect
import contextlib
import sqlite3

from nnslr_tools import drive_logs, drive_review, progress
from nnslr_tools.drive_episodes import BEFORE_NS, CANCEL_BUTTONS, Step
from nnslr_tools.speed_review import (MAX_KPH, MIN_BLOCK_SECONDS, NS, SpeedReviewError, SpeedReviewStore, _integer,
                                      dashcam_drive, speed_curve, store_for)

PRE_ROLL_NS = 15 * NS
MACHINES = ('openpilot', 'cruise')


def ownership(merged: dict) -> Step:
    """Who held longitudinal control, including explicit unknowns before a signal or in a log hole."""
    series = merged.get('series', {})
    longitudinal, cruise, gas = (Step(series.get(key)) for key in ('long_active', 'cruise_enabled', 'gas'))
    points = []
    for t in sorted(set(longitudinal.t) | set(cruise.t) | set(gas.t)):
        active, on, pressed = longitudinal.at(t), cruise.at(t), gas.at(t)
        if active is True:
            owner = 'openpilot'
        elif on is True and pressed is False:
            # "Cruise" records the signal that established ownership, not a guess at a Smartspeed release.
            owner = 'cruise'
        elif active is False and (on is False or pressed is True):
            owner = 'driver'
        else:
            owner = 'unknown'
        if not points or owner != points[-1][1]:
            points.append((t, owner))
    return Step(points)


def candidates(merged: dict) -> list[tuple]:
    """One clip per observed pedal onset or driver cancellation while a machine was holding speed.

    The 100 ms look-back handles controls dropping as the pedal is pressed. An automatic or unexplained
    disengagement, steering alone, a gas pedal held down from before engagement, and the first sample
    after a hole are not manual interventions.
    """
    owners = ownership(merged)
    found = {}
    series = merged.get('series', {})

    def add(t, kind):
        source = owners.at(t - 1)
        if source not in MACHINES:
            if source != 'driver':  # do not look back through missing state
                return
            recent = owners.between(t - BEFORE_NS, t - 1)
            if any(value not in (*MACHINES, 'driver') for _, value in recent):
                return
            source = next((value for _, value in reversed(recent) if value in MACHINES), None)
        if source in MACHINES:
            found[t, kind] = (f'{kind}-{t}', t, kind, source)

    for kind in ('gas', 'brake'):
        previous = None
        for t, pressed in series.get(kind, ()):
            if pressed is True and previous is False:
                add(t, kind)
            previous = pressed
    for item in merged.get('events', ()):
        if item[1] == 'button' and item[2] in CANCEL_BUTTONS and item[3] is True:
            add(item[0], 'cancel')
    # Some ports have no cruise buttons. Only a logged user disable attached to a real off edge counts;
    # a brake or a button already represents the same intervention.
    enabled = Step(series.get('sd_enabled'))
    onroad = Step([(e[0], e[3]) for e in merged.get('events', ()) if e[1] == 'onroad'])
    previous = None
    physical = sorted({t for t, _ in found})
    for t, current in enabled.changes():
        if (previous is True and current is False
                and bisect.bisect_left(physical, t - NS) == bisect.bisect_right(physical, t + BEFORE_NS)):
            flags = {flag for events in onroad.v[bisect.bisect_left(onroad.t, t - NS):
                                                 bisect.bisect_right(onroad.t, t + BEFORE_NS)]
                     for _, types in events for flag in types}
            if 'userDisable' in flags and not flags.intersection({'softDisable', 'immediateDisable'}):
                add(t, 'cancel')
        previous = current
    return [found[key] for key in sorted(found)]


def replace_candidates(store, route: str, found: list[tuple]) -> None:
    with contextlib.closing(store._connect()) as db, db:
        db.execute('BEGIN IMMEDIATE')
        db.execute('UPDATE speed_interventions SET active=0 WHERE route=?', (route,))
        db.executemany('INSERT INTO speed_interventions(route,event,at_ns,kind,source) VALUES(?,?,?,?,?) '
                       'ON CONFLICT(route,event) DO UPDATE SET active=1, source=excluded.source',
                       [(route, *row) for row in found])


def prepare(root, log=print) -> None:
    drives, store = drive_review.store_for(root), store_for(root)
    routes = [row['route'] for row in drives._rows("SELECT route FROM scans WHERE state='done' ORDER BY route")]
    count = 0
    for i, route in enumerate(routes, 1):
        summary = drives.summary(route)
        if summary and not dashcam_drive(summary):
            merged = drives.merged(route, keep=False)
            expected = {int(s) for s in summary['files']} - {row['segment'] for row in summary.get('left_out', ())}
            if not merged or not expected or set(merged.get('segments', ())) != expected:
                raise ValueError(f'{route}: scan extracts missing or incomplete; read its logs again.')
            events = candidates(merged)
            replace_candidates(store, route, events)
            count += len(events)
            log(f'{route}: {len(events)} manual interventions.')
        elif summary:
            replace_candidates(store, route, [])
        progress.report('interventions', i, len(routes), f'{i}/{len(routes)} drives · {count} interventions')
    log(f'{count} interventions in the queue from {len(routes)} scanned drives. Existing reviews kept.')


def queue(store: SpeedReviewStore, drives) -> dict:
    starts = {r['route']: r['t0'] for r in drives._rows("SELECT route,json_extract(summary,'$.t0_ns') AS t0 FROM scans")}
    return {'events': [{**row, 'second': (row['at_ns'] - starts[row['route']]) / NS} for row in store._rows(
        'SELECT route,event,at_ns,kind,source,active,decision,start_ns,end_ns,kph,reviewer,revision '
        "FROM speed_interventions WHERE active=1 OR decision!='pending' ORDER BY route DESC,at_ns,kind")
        if starts.get(row['route']) is not None]}


def detail(store, drives, route):
    revision = store.revision(route)
    merged = drives.merged(route)
    if not merged or merged.get('t0') is None:
        raise SpeedReviewError(404, 'not_scanned', 'Read the logs of this drive first.')
    span = (merged['t0'], merged['span'][1])
    owners = ownership(merged)
    series = merged['series']
    return {'revision': revision, 'span': span, 'owners': list(owners.changes()), 'speed': speed_curve(merged, span),
            'vision': [[t, v if v and v > 0 else None] for t, v in series.get('vis_limit', ())],
            'cruise': [[t, v if v and 0 < v < 250 else None] for t, v in series.get('v_cruise', ())],
            'pre_roll_ns': PRE_ROLL_NS}


def decide(store, drives, route: str, body: dict, who: str) -> dict:
    event, choice = body.get('event'), body.get('decision')
    if not isinstance(event, str) or choice not in ('driver', 'block', 'skip'):
        raise SpeedReviewError(400, 'bad_decision', 'Choose an intervention and how to review it.')
    summary = drives.summary(route)
    if not summary or dashcam_drive(summary):
        raise SpeedReviewError(409, 'not_assisted', 'This is not a scanned assisted drive.')
    revision = _integer(body.get('base_revision'), 'base_revision')
    event_revision = _integer(body.get('event_revision'), 'event_revision')
    start = end = kph = None
    if choice != 'skip':
        start, end = (_integer(body.get(k), k) for k in ('start_ns', 'end_ns'))
        if not summary['t0_ns'] <= start < end <= summary['span_ns'][1] or end - start < MIN_BLOCK_SECONDS * NS:
            raise SpeedReviewError(400, 'bad_interval', 'Select at least one second inside the drive.')
        if choice == 'block':
            kph = _integer(body.get('kph'), 'kph')
            if not 1 <= kph <= MAX_KPH:
                raise SpeedReviewError(400, 'bad_speed', f'Choose a speed from 1 to {MAX_KPH} km/h.')
        else:
            merged = drives.merged(route) or {}
            span = merged.get('span') or [None, None]
            if (None in span or not span[0] <= start < end <= span[1]
                    or any(owner != 'driver' for _, owner in ownership(merged).between(start, end - 1))):
                raise SpeedReviewError(400, 'not_driver', 'Use my speed needs a driver-controlled interval. '
                                       'Change its ends or set a speed block.')
            if any(v is None for _, v in Step(drive_logs.dashboard_speed(merged)).between(start, end - 1)):
                raise SpeedReviewError(400, 'no_speed', 'The recorded dashboard speed is missing in this interval.')
    with store._writing(route, revision) as (db, new):
        row = db.execute("SELECT at_ns,revision FROM speed_interventions WHERE route=? AND event=? AND (active=1 OR decision!='pending')",
                         (route, event)).fetchone()
        if not row:
            raise SpeedReviewError(404, 'no_event', 'This intervention is no longer in the queue.')
        if row['revision'] != event_revision:
            raise SpeedReviewError(409, 'stale_revision', 'This intervention was reviewed in another window.')
        if start is not None and db.execute(
                "SELECT 1 FROM speed_interventions WHERE route=? AND event!=? AND decision IN ('driver','block') "
                'AND start_ns < ? AND end_ns > ? LIMIT 1', (route, event, end, start)).fetchone():
            raise SpeedReviewError(409, 'overlap', 'Another reviewed intervention already covers part of this interval.')
        db.execute('UPDATE speed_interventions SET decision=?,start_ns=?,end_ns=?,kph=?,reviewer=?,revision=? '
                   'WHERE route=? AND event=?', (choice, start, end, kph, who, new, route, event))
        store._log(db, route, new, 'intervention_review', start if start is not None else row['at_ns'],
                   end if end is not None else row['at_ns'],
                   {'kph': kph, 'state': choice, 'known_ns': row['at_ns'], 'note': event}, who)
    return {'ok': True, 'revision': new}


def approved(db: sqlite3.Connection) -> dict[str, list[dict]]:
    """Snapshot for dataset readers, including older databases that have never opened the new tab."""
    if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='speed_interventions'").fetchone():
        return {}
    found: dict[str, list] = {}
    for row in db.execute("SELECT route,event,at_ns,start_ns,end_ns,kph,decision FROM speed_interventions "
                          "WHERE decision IN ('driver','block') ORDER BY route,start_ns"):
        found.setdefault(row['route'], []).append(dict(row))
    return found
