"""Global browsing only; all routes and reviews in these tests are synthetic."""
import os
import subprocess
import sys
from urllib.parse import urlencode

import pytest

from nnslr_tools import sign_catalog
from nnslr_tools.review_access import guest_rule
from review_helpers import OTHER_ROUTE, ROUTE, RunningServer, json_of, make_report, page_body, real_previews


def setup_catalog(root):
    one = make_report(root, keys=[(0, 0), (0, 1), (2, 1521)])
    two = make_report(root, route=OTHER_ROUTE)
    for route in (ROUTE, OTHER_ROUTE):
        real_previews(root, route)
    server = RunningServer(root)
    try:
        for route, row, value in ((ROUTE, one[0], 60), (ROUTE, one[2], 50), (OTHER_ROUTE, two[0], 60)):
            body = page_body(row, value_kph=value)
            if row is one[2]:
                body['detections'].append({'detection_id': 'manual-1', 'family': 'maximum_speed', 'value_kph': 60,
                                           'bbox_xyxy': [19, 5, 28, 14], 'applicability': 'own_road'})
            status, _, response = server.post_frame(route, row['frame_key'], body)
            assert status == 200, json_of(response)
        return server, one
    except BaseException:
        server.close()
        raise


def browse(server, **query):
    status, _, raw = server.request('GET', '/api/speed-review/sign-catalog?' + urlencode(query))
    assert status == 200
    return json_of(raw)


def test_catalog_uses_confirmed_boxes_across_routes_and_refreshes_after_edit(tmp_path, monkeypatch):
    monkeypatch.setattr(sign_catalog, 'PAGE_SIZE', 2)
    server, rows = setup_catalog(tmp_path)
    try:
        found = browse(server, group='speed:kmh:60')
        assert found['groups'] == {'speed:kmh:50': 1, 'speed:kmh:60': 3}
        assert found['pages'] == 2
        assert [(item['route'], item['frame']) for item in found['items']] == [(ROUTE, '2/1521'), (ROUTE, '0/0')]
        assert browse(server, group='speed:kmh:60', page=2)['items'][0]['route'] == OTHER_ROUTE
        hit = found['items'][0]
        assert hit['editor'] == f'/route/{ROUTE}/' and hit['box']['value_kph'] == 60
        thumb = f'/api/route/{ROUTE}/frame/{hit["frame"]}/classifier-crop?' + urlencode({
            'box': ','.join(map(str, hit['box']['bbox_xyxy'])),
        })
        status, response, data = server.request('GET', thumb)
        assert status == 200 and response.getheader('Content-Type') == 'image/png' and data[:4] == b'\x89PNG'
        # The last label for this frame wins immediately, without importing the dataset.
        body = page_body(rows[2], value_kph=50, base_revision=1)
        body['detections'].append({**hit['box'], 'value_kph': None, 'sign_value': None, 'value_state': 'unknown'})
        assert server.post_frame(ROUTE, '2/1521', body)[0] == 200
        found = browse(server, group='speed:kmh:60', page=2)
        assert found['page'] == found['pages'] == 1
        assert found['groups'] == {'speed:kmh:50': 1, 'speed:kmh:60': 2, 'speed:unknown': 1}
        assert server.request('GET', '/api/speed-review/sign-catalog?page=-1')[0] == 400
        assert guest_rule('GET', '/api/speed-review/sign-catalog') is None
        assert guest_rule('GET', '/sign-catalog') is None
        status, response, _ = server.request('GET', '/speed-review?catalog=speed%3Akmh%3A60&page=2')
        assert status == 303 and response.getheader('Location') == '/sign-catalog?catalog=speed%3Akmh%3A60&page=2'
    finally:
        server.close()


def test_catalog_links_to_the_selected_box_in_both_editors(tmp_path, monkeypatch):
    python = os.environ.get('NNSLR_TEST_BROWSER_PYTHON', sys.executable)
    chromium = os.environ.get('NNSLR_TEST_CHROMIUM')
    if not chromium or subprocess.run([python, '-c', 'import playwright.sync_api'], capture_output=True).returncode:
        pytest.skip('optional Playwright environment not configured')
    server, rows = setup_catalog(tmp_path)
    try:
        body = page_body(rows[0], base_revision=1, value_kph=60)
        body['detections'].append({'detection_id': 'manual-2', 'family': 'other_sign', 'sign_type': 'dangerous_bend_left',
                                   'value_kph': None, 'bbox_xyxy': [19, 3, 29, 17], 'applicability': 'own_road',
                                   'supplementary_panel': 'distance', 'plate_distance_m': 70})
        assert server.post_frame(ROUTE, '0/0', body)[0] == 200
        for width, embedded in ((820, False), (1180, True)):
            monkeypatch.setattr(server.app, 'opens_in_speed_review', lambda _route: embedded)
            result = subprocess.run([python, '-c', CATALOG_BROWSER, f'http://127.0.0.1:{server.port}', chromium,
                                     str(width), str(int(embedded))], capture_output=True, text=True, timeout=40)
            assert result.returncode == 0, result.stdout + '\n' + result.stderr
    finally:
        server.close()


CATALOG_BROWSER = r'''
import os
import sys
from playwright.sync_api import expect, sync_playwright

base, chromium, width, embedded = sys.argv[1:]
with sync_playwright() as p:
    browser = p.chromium.launch(executable_path=chromium, headless=True)
    page = browser.new_page(viewport={'width': int(width), 'height': 900})
    page.add_init_script("localStorage.setItem('nnslr-reviewer', 'Catalog test')")
    writes, fits, saves = [], [], []
    confirm = False
    def access(route):
        if route.request.method == 'GET':
            route.continue_()
        elif route.request.url.endswith('/ai-fit'):
            fits.append((route.request.url, route.request.post_data_json))
            route.fulfill(json={'ok': True, 'box': [17, 4, 30, 14], 'message': 'Synthetic proposal'})
        elif confirm and route.request.url.endswith('/frame/2/1521'):
            saves.append(route.request.post_data_json)
            route.continue_()
        else:
            writes.append(route.request.url)
            route.abort()
    page.route('**/*', access)
    page.goto(base + '/speed-review')
    page.get_by_role('link', name='Sign catalog', exact=True).click()
    expect(page).to_have_url(base + '/sign-catalog')
    expect(page.locator('.sct-index button[data-group]')).to_have_count(3)
    bend = page.locator('[data-group="kind:dangerous_bend_left"]')
    expect(bend).to_contain_text('Bend left')
    assert bend.locator('img').evaluate('(i)=>i.decode().then(()=>i.naturalWidth>0)')
    bend.click()
    expect(page.locator('.sct-tile')).to_have_count(1)
    page.get_by_role('button', name='All signs').click()
    expect(page.locator('[data-group="speed:kmh:60"] .sign')).to_have_text('60')
    page.locator('[data-group="speed:kmh:60"]').click()
    expect(page.locator('.sct-tile')).to_have_count(3)
    first = page.locator('.sct-tile').first
    assert '2%2F1521' in first.get_attribute('href')
    assert first.locator('img').evaluate('(img) => img.decode().then(() => img.naturalWidth > 0)')
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    if os.environ.get('NNSLR_CATALOG_SCREENSHOTS'):
        page.screenshot(path=f'/tmp/opencode/sign-catalog-{width}.png')
    first.click()
    expect(page.locator('#editorTitle')).to_have_text('Box 2')
    page.get_by_role('link', name='Sign catalog').click()
    page.get_by_label('Square fit').check()
    page.get_by_role('button', name='Fit with AI').first.click()
    expect(page.locator('#editorTitle')).to_have_text('Box 2')
    expect(page.get_by_text('AI fit preview. Confirm to save this box.', exact=True).first).to_be_visible()
    assert len(fits) == 1 and fits[0][0].endswith('/frame/2/1521/ai-fit')
    assert fits[0][1]['box'] == [19, 5, 28, 14]
    boxes = page.locator('rect[data-index="1"]').first
    expect(boxes).to_have_attribute('width', '13')
    expect(boxes).to_have_attribute('height', '13')
    assert ('/speed-review?' in page.url) == bool(int(embedded))
    expect(page.locator('#editorTitle')).to_have_text('Box 2')
    expect(page.locator('#quickValues [data-value="60"]')).to_have_attribute('aria-pressed', 'true')
    if int(embedded):
        confirm = True
        with page.expect_response(lambda r: r.request.method == 'POST' and r.url.endswith('/frame/2/1521')) as saved:
            page.locator('#confirm').click()
        assert saved.value.status == 200
        first_box, fitted = saves[0]['detections']
        assert first_box['bbox_xyxy'] == [1, 2, 15, 20] and first_box['value_kph'] == 50
        assert fitted['box_source'] == 'ai_fit' and fitted['bbox_xyxy'] == [17, 2.5, 30, 15.5]
        assert fitted['value_kph'] == 60
    else:
        page.keyboard.press('?')
        expect(page.locator('#quickValues [data-value="unknown"]')).to_have_attribute('aria-pressed', 'true')
    page.get_by_role('link', name='Sign catalog').click()
    assert page.url.startswith(base + '/sign-catalog?')
    expect(page.locator('#catalog-selection')).to_have_text('60 km/h')
    expect(page.locator('.sct-tile')).to_have_count(3)
    page.get_by_role('button', name='All signs').click()
    expect(page.locator('.sct-index button[data-group]')).to_have_count(3)
    assert writes == [], writes
'''
