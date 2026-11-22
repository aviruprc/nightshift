"""Resolve a board listing to the real application page. Easy Apply is detected and avoided ."""
import re

LI_APPLY = re.compile(r"^\s*apply( now)?\s*$", re.I)

# What the signed-in page says about competition (seen: "Reposted 1 day ago · Over 100 people clicked apply").
_OVER = re.compile(r"\bover\s+(\d[\d,]*)\s+(?:people clicked apply|applicants?)", re.I)
_FIRST = re.compile(r"\bbe among the first\s+(\d+)\s+applicants?", re.I)
_EXACT = re.compile(r"\b(\d[\d,]*)\s+(?:people clicked apply|applicants?)\b", re.I)
LAST = {"applicants": None, "kind": "unknown", "text": ""}   # the reading from the most recent resolve_linkedin call


def read_applicants(body: str) -> dict:
    """{'kind': 'exact'|'over'|'first'|'unknown', 'applicants': int|None, 'text': the phrase}. Only 'exact' is a number she can rely
    on: 'Over 100' and 'Be among the first 25' are bounds, and a page that says nothing is unknown - never read as 'few'."""
    body = body or ""
    m = _OVER.search(body)
    if m:
        return {"kind": "over", "applicants": int(m.group(1).replace(",", "")), "text": m.group(0)}
    m = _FIRST.search(body)
    if m:
        return {"kind": "first", "applicants": int(m.group(1)), "text": m.group(0)}
    m = _EXACT.search(body)
    if m:
        return {"kind": "exact", "applicants": int(m.group(1).replace(",", "")), "text": m.group(0)}
    return {"kind": "unknown", "applicants": None, "text": ""}


def low_competition(reading: dict, limit: int) -> bool:
    """True only for an exact count below `limit`."""
    return reading.get("kind") == "exact" and reading.get("applicants") is not None and reading["applicants"] < limit


def resolve_linkedin(ctx, url: str, max_applicants: int | None = None):
    """Signed-in LinkedIn job page -> ('external', company_url) | ('easy_apply', None) | ('closed', None) | ('error', reason)
    | ('crowded', None). `max_applicants`: when set, a page whose count is not an EXACT number below it returns 'crowded' BEFORE
    anything is clicked; the reading is left in `LAST` either way."""
    pg = ctx.new_page()
    LAST.update(applicants=None, kind="unknown", text="")
    try:
        pg.goto(url, wait_until="domcontentloaded", timeout=45000)
        pg.wait_for_timeout(3500)
        body = pg.inner_text("body")[:4000]
        if re.search(r"no longer accepting applications|job is no longer available|no longer available", body, re.I):
            return "closed", None
        LAST.update(read_applicants(body))
        if max_applicants is not None and not low_competition(LAST, max_applicants):
            return "crowded", None
        btn = None
        for el in pg.locator("button:visible, a:visible").all()[:150]:
            try:
                txt = (el.inner_text() or "").strip()  # exact text only: 'Easy Apply' inside a similar-jobs card must not count
                if re.match(r"^easy apply$", txt, re.I):
                    return "easy_apply", None
                if btn is None and LI_APPLY.match(txt):
                    btn = el
            except Exception:  # noqa: BLE001
                continue
        if btn is None:
            pg.reload(wait_until="domcontentloaded")  # one retry, then call it closed: a LinkedIn page with no Apply/Easy Apply is a closed job
            pg.wait_for_timeout(4000)
            for el in pg.locator("button:visible, a:visible").all()[:150]:
                try:
                    txt = (el.inner_text() or "").strip()
                    if re.match(r"^easy apply$", txt, re.I):
                        return "easy_apply", None
                    if LI_APPLY.match(txt):
                        btn = el
                        break
                except Exception:  # noqa: BLE001
                    continue
            if btn is None:
                return "closed", None
        try:
            with ctx.expect_page(timeout=9000) as newp:
                btn.click()
            ext = newp.value
        except Exception:  # noqa: BLE001
            for b in pg.get_by_role("button", name=re.compile("^(continue|continue to apply)$", re.I)).all():
                try:
                    with ctx.expect_page(timeout=9000) as newp:
                        b.click()
                    ext = newp.value
                    break
                except Exception:  # noqa: BLE001
                    ext = None
            else:
                ext = None
        if ext is None:
            return "error", "Apply did not open an external page"
        try:
            ext.wait_for_load_state("domcontentloaded", timeout=20000)
            ext.wait_for_timeout(2500)
        except Exception:  # noqa: BLE001
            pass
        final = ext.url
        ext.close()
        if "linkedin.com" in final:
            return "easy_apply", None
        return "external", final
    except Exception as e:  # noqa: BLE001
        return "error", f"{type(e).__name__}: {str(e)[:100]}"
    finally:
        try:
            pg.close()
        except Exception:  # noqa: BLE001
            pass
