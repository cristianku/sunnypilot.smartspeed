"""Real page, synthetic server only; invoked by test_interventions.py."""
import sys
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

port, route, chromium, output = sys.argv[1:]
errors = []
with sync_playwright() as p:
    browser = p.chromium.launch(executable_path=chromium, headless=True)
    page = browser.new_page(viewport={'width': 1180, 'height': 960}, reduced_motion='reduce')
    page.set_default_timeout(7000)
    page.on('pageerror', lambda exc: errors.append(str(exc)))
    page.goto(f'http://127.0.0.1:{port}/speed-review')
    page.get_by_role('radio', name='Interventions', exact=True).click()
    page.get_by_role('button', name='Find interventions', exact=True).click()
    accelerator = page.get_by_role('link', name='Accelerator · 0:20', exact=False)
    expect(accelerator).to_be_visible(timeout=18000)
    page.screenshot(path=str(Path(output) / 'queue-1180.png'), animations='disabled')
    page.goto(f'http://127.0.0.1:{port}/speed-review?drive={route}&side=signs')
    sidebar = page.get_by_role('radiogroup', name='Drive review side')
    for width in (820, 1180):
        page.set_viewport_size({'width': width, 'height': 960})
        for name in ('Speed', 'Signs', 'Interventions'):
            expect(sidebar.get_by_role('radio', name=name, exact=True)).to_be_visible()
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    sidebar.get_by_role('radio', name='Interventions', exact=True).click()
    expect(page.get_by_role('heading', name='Interventions on this drive')).to_be_visible()
    page.reload()
    expect(sidebar.get_by_role('radio', name='Interventions', exact=True)).to_be_checked()
    accelerator.click()
    assert 'scope=drive' in page.url
    page.reload()
    expect(page.locator('.rt-pos b')).to_have_text('0:05')
    page.get_by_text('Go to intervention', exact=True).click()
    page.get_by_role('button', name='From here').click()
    page.get_by_role('button', name='On 5 seconds', exact=True).click()
    page.get_by_role('button', name='To here').click()
    page.get_by_role('button', name='Use my speed').click()
    expect(page.locator('#iv-event')).to_have_text('Brake · 0:44')
    expect(page.locator('.rt-pos b')).to_have_text('0:29')
    page.set_viewport_size({'width': 820, 'height': 1000})
    page.get_by_role('button', name='Go to intervention').click()
    page.get_by_role('button', name='From here').click()
    page.get_by_role('button', name='On 5 seconds', exact=True).click()
    page.get_by_role('button', name='To here').click()
    page.get_by_role('button', name='Correct with a block').click()
    page.get_by_role('spinbutton').fill('30')
    for width in (820, 1180):
        page.set_viewport_size({'width': width, 'height': 1000})
        page.screenshot(path=str(Path(output) / f'editor-{width}.png'))
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    page.get_by_role('button', name='Save speed block').click()
    expect(page.get_by_text('Every intervention is reviewed.')).to_be_visible()
    assert f'drive={route}' in page.url
    page.get_by_role('link', name='All drives').click()
    expect(page.get_by_role('heading', name='Manual interventions')).to_be_visible()
    page.get_by_role('radio', name='Reviewed', exact=True).click()
    page.set_viewport_size({'width': 820, 'height': 1000})
    page.screenshot(path=str(Path(output) / 'queue-820.png'))
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    page.get_by_role('link', name='Brake · 0:44', exact=False).click()
    page.reload()
    expect(page.get_by_role('spinbutton')).to_have_value('30')
    page.get_by_role('button', name='Skip · nothing to learn').click()
    expect(page.get_by_role('heading', name='Manual interventions')).to_be_visible()
    assert not errors, errors
    browser.close()
