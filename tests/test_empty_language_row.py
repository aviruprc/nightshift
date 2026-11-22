"""An empty optional Languages row is deleted, a filled one is kept (a company returnship). Real browser, local HTML."""
from apply import workday

PAGE = """<html><body>
<div class="row" id="r1"><div data-automation-id="formField-language"><label>Language*</label><button>Select One</button></div>
<div data-automation-id="formField-teach"><label>Able to Teach*</label><button>Select One</button></div><button>Delete</button></div>
<div class="row" id="r2"><div data-automation-id="formField-language2"><label>Language*</label><button>English</button></div><button>Delete</button></div>
<script>document.querySelectorAll('button').forEach(b => { if (b.innerText.trim() === 'Delete') b.onclick = () => b.closest('.row').remove(); });</script>
</body></html>"""


def test_the_empty_language_row_goes_and_the_filled_one_stays():
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        b = pw.chromium.launch()
        pg = b.new_page()
        pg.set_content(PAGE)
        assert workday.drop_empty_language_rows(pg) == 1
        assert pg.locator(".row").count() == 1 and "English" in pg.inner_text("body")
        assert workday.drop_empty_language_rows(pg) == 0
        b.close()


SITES = """<html><body>
<div role="group"><div><h5>Websites 1</h5><button>Delete</button></div><input name="url" value="https://github.com/x/y"></div>
<div role="group"><div><h5>Websites 2</h5><button>Delete</button></div><input name="url" value="https://www.linkedin.com/in/me/"></div>
<div role="group"><div><h5>Websites 3</h5><button>Delete</button></div><input name="url" value="https://www.linkedin.com/in/me"></div>
<div role="group"><div><h5>Websites 4</h5><button>Delete</button></div><input name="url" value=""></div>
<script>document.querySelectorAll('button').forEach(b => b.onclick = () => b.closest('[role=group]').remove());</script></body></html>"""


def test_empty_and_repeated_website_rows_are_deleted_and_distinct_ones_stay():
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        b = pw.chromium.launch()
        pg = b.new_page()
        pg.set_content(SITES)
        assert workday.drop_bad_website_rows(pg) == 2
        assert pg.locator("input[name=url]").count() == 2
        b.close()
