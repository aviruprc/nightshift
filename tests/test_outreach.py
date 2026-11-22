"""Startup outreach: addresses come only from a company's own pages, nothing is sent, the email states only recorded facts."""
import yaml

from apply import outreach as o


def test_distinctive_token_is_the_longest_word():
    assert o._token("Mu Sigma") == "sigma"
    assert o._token("Fractal Analytics") == "fractal"
    assert o._token("Tiger Analytics") == "tiger"


def test_hiring_addresses_are_recognised_and_info_is_not():
    for a in ("careers", "jobs", "hr", "hiring", "talent", "recruitment", "careers.india", "dsrecruitment"):
        assert o.HIRING.match(a), a
    for a in ("info", "sales", "support", "hello", "press", "chrisjones"):
        assert not o.HIRING.match(a), a


def test_only_addresses_printed_on_the_site_are_returned(monkeypatch):
    pages = {"https://acme.com": '<a href="mailto:careers@acme.com">x</a> info@acme.com press@other.org logo@2x.png',
             "https://acme.com/careers": "Write to jobs@acme.com"}
    monkeypatch.setattr(o, "_get", lambda u: pages.get(u))
    monkeypatch.setattr(o.time, "sleep", lambda s: None)
    got = {e: (k, src) for e, k, src in o.find_emails("acme.com", pages["https://acme.com"])}
    assert got["careers@acme.com"] == ("hiring", "https://acme.com")
    assert got["jobs@acme.com"][1] == "https://acme.com/careers"
    assert got["info@acme.com"][0] == "general"
    assert "press@other.org" not in got and not any(e.endswith(".png") for e in got)   # other domains and image names are dropped


def test_a_site_that_does_not_name_the_company_is_rejected(monkeypatch):
    monkeypatch.setattr(o, "_get", lambda u, final=None: "<html><title>Buy domains</title>cheap parking page</html>")
    assert o.verify_domain("Fractal Analytics", "fractal.ai") is None
    monkeypatch.setattr(o, "_get", lambda u, final=None: "<html><title>Fractal | AI</title>Fractal builds</html>")
    assert o.verify_domain("Fractal Analytics", "fractal.ai")[0] == "fractal.ai"


def test_a_supplied_domain_with_a_tagline_title_is_accepted_but_a_guess_is_not(monkeypatch):
    # a company's real <title> is a tagline; a rebranded site (course5i.com -> c5i.ai) redirects. Both false 'no site' results happened.
    page = "<html><title>Data Analytics Services &amp; Consulting</title>" + "LatentView helps. " * 5 + "</html>"
    monkeypatch.setattr(o, "_get", lambda u, final=None: page)
    assert o.verify_domain("LatentView Analytics", "latentview.com")[0] == "latentview.com"
    assert o.verify_domain("LatentView Analytics", None) is None      # a guessed domain still needs the name in the <title>


def test_the_final_host_after_a_redirect_is_used(monkeypatch):
    def fake(u, final=None):
        if final is not None:
            final.append("https://www.mu-sigma.com/")
        return "<html>Mu Sigma is a company. About Mu Sigma. Mu Sigma careers.</html>"
    monkeypatch.setattr(o, "_get", fake)
    assert o.verify_domain("Mu Sigma", "mu-sigma.com")[0] == "mu-sigma.com"


def test_email_states_only_recorded_facts(tmp_path, monkeypatch):
    from core import profile
    cv = tmp_path / "resume.pdf"
    cv.write_bytes(b"%PDF-1.4 test")
    monkeypatch.setattr(o, "CV", cv)
    msg = o.compose("Acme", "careers@acme.com")
    body = msg.get_body().get_content()
    facts = yaml.safe_load(open(profile.FACTS, encoding="utf-8"))
    assert msg["To"] == "careers@acme.com" and msg["From"] == facts["personal"]["email"]
    for claim in ("Example Retail Co", "SQL", "Power BI", "Tableau", "4 years"):
        assert claim in body
    for never in ("Python", "Snowflake", "PySpark", "Databricks"):          # cv/facts.yaml not_claimable
        assert never not in body
    assert any(p.get_filename() == "Asha_Example_Resume.pdf" for p in msg.iter_attachments())


def test_mail_leaves_only_through_send_drafted():
    """Sending exists, but in exactly one function, and it sends the stored draft - it never composes."""
    import inspect
    src = open(o.__file__, encoding="utf-8").read()
    assert src.count("send_message(") == 1 and "S.send_message(msg)" in inspect.getsource(o.send_drafted)
    assert "compose(" not in inspect.getsource(o.send_drafted), "send_drafted must send the draft she could read, not rebuild it"
    assert "status='drafted'" in inspect.getsource(o.send_drafted)      # a sent row is never picked again
