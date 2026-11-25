"""Read-only catalog of the boxes confirmed in the current Sign Review reports."""
from __future__ import annotations

from collections import Counter
from threading import Lock

from nnslr_tools.drives import drive_of
from nnslr_tools.review_db import waits_for_a_person
from nnslr_tools.sign_types import ROAD_USERS

PAGE_SIZE = 24
SPEED_FAMILIES = ('maximum_speed', 'variable_display', 'zone')


def group_of(box: dict) -> str | None:
    family, kind = box.get('family'), box.get('sign_type')
    if not family or family == 'not_a_sign' or kind in ROAD_USERS:
        return None
    value = box.get('sign_value')
    if value is None:
        value = box.get('value_kph')
    if family in SPEED_FAMILIES:
        if value is None:
            return 'speed:unknown'
        unit = 'mph' if box.get('sign_unit') == 'mph' else 'kmh'
        return f'speed:{unit}:{value}'
    if family == 'other_sign':
        return f'kind:{kind or "other"}'
    return f'family:{family}'


def confirmed_boxes(store, route: str, run: str) -> list[dict]:
    items = []
    for review in store.reviews(route, run, confirmed_only=True):
        if waits_for_a_person(review):
            continue
        for box in review['payload'].get('detections', []):
            group = group_of(box)
            if group is None or not box.get('bbox_xyxy') or not box.get('detection_id'):
                continue
            x1, y1, x2, y2 = box['bbox_xyxy']
            items.append({'group': group, 'route': route, 'run': run, 'frame': review['frame_key'],
                          'kind': review['reviewer_kind'], 'width': x2 - x1, 'height': y2 - y1, 'box': box})
    return items


class SignCatalog:
    def __init__(self, root, store):
        self.root, self.store = root, store
        self._lock = Lock()
        self._routes = {}

    def view(self, group: str, page: int, signs_url, *, sequence: bool = False) -> dict:
        # Use the active reports, never prior runs or the append-only history. The per-run stamp changes on edit or
        # withdrawal, so returning from the editor refreshes that drive without reparsing the rest.
        from nnslr_tools.review import list_reports

        with self._lock:
            stamps = self.store.run_stamps()
            current = {}
            for route, directory in list_reports(self.root):
                run = directory.name
                stamp = (run, stamps.get((route, run)))
                cached = self._routes.get(route)
                current[route] = cached if cached is not None and cached[0] == stamp else (
                    stamp, confirmed_boxes(self.store, route, run) if stamp[1] is not None else [])
            self._routes = current
        counts = Counter()
        examples = {}
        selected = []
        for _stamp, items in current.values():
            for item in items:
                counts[item['group']] += 1
                if not group and not item['group'].startswith('speed:'):
                    previous = examples.get(item['group'])
                    if previous is None or min(item['width'], item['height']) > min(previous['width'], previous['height']):
                        examples[item['group']] = item
                if item['group'] == group:
                    selected.append(item)
        # Tiny boxes first; this is only a ranking, not a judgement that the number is unreadable.
        selected.sort(key=lambda item: (min(item['width'], item['height']), item['route'],
                                        tuple(map(int, item['frame'].split('/')))))
        pages = max(1, (len(selected) + PAGE_SIZE - 1) // PAGE_SIZE)
        page = min(max(1, page), pages)
        names = {}
        result = []
        for item in selected[(page - 1) * PAGE_SIZE:page * PAGE_SIZE]:
            route = item['route']
            if route not in names:
                drive = drive_of(self.root, route)
                names[route] = {'title': drive.get('title'), 'editor': signs_url(route)}
            result.append({**item, **names[route]})
        return {'ok': True, 'groups': dict(counts), 'items': result, 'page': page, 'pages': pages,
                **({'sequence': [[i['route'], i['run'], i['frame'], i['box']['detection_id']] for i in selected]}
                   if sequence and group else {}),
                'examples': {key: {name: item[name] for name in ('route', 'frame', 'run', 'box')}
                             for key, item in examples.items()}}
