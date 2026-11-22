import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from cv import ats_check, tailor, vocab  # noqa: E402

JD = ("Senior Data Analyst. 3-6 years experience. Build Power BI dashboards, write advanced SQL, regulatory reporting, "
      "data quality and reconciliation. Python is a plus. Snowflake nice to have. Stakeholder management. "
      "You will partner with risk and finance teams to produce monthly KPI packs, validate source data, document controls, "
      "and present insights to senior stakeholders. Strong Excel skills and a track record of automating reports are expected.")


def test_vocab_gaps_and_owned():
    owned, lacked = vocab.jd_terms(JD)
    assert "Power BI" in owned and "SQL" in owned and "Regulatory Reporting Support" in owned
    assert "Python" in lacked and "Snowflake" in lacked


def test_tailor_reorders_but_never_adds():
    cv = tailor.tailor(JD, "data_bi", "Senior Data Analyst")
    blob = tailor.render_html(cv)
    assert "Python" not in blob and "Snowflake" not in blob
    first_tools = cv["skills"]["Tools"][:2]
    assert set(first_tools) <= {"SQL", "Power BI"}  # JD-matched tools float to the front
    from core import profile
    allowed = {b for e in profile.facts()["experience"] for b in e["bullets"]}
    assert all(b in allowed for e in cv["experience"] for b in e["bullets"])
    assert "Power BI" in cv["headline"] or "SQL" in cv["headline"]


MKT_JD = ("Marketing Analyst. 3-5 years experience. Build Power BI dashboards for campaign performance, write SQL on customer data, "
          "run A/B testing analysis and customer segmentation, and report KPI results to stakeholders. Google Analytics and Tableau "
          "experience expected; strong Excel. Python is a plus. You will partner with marketing and product teams to size promotions, "
          "track conversion and present insights in weekly reviews.")


def test_pdf_passes_ats(tmp_path):
    pdf, cv = tailor.build("testjob", MKT_JD, "data_bi", "Marketing Analyst")   # a JD that fits the invented test person
    res = ats_check.check(pdf, MKT_JD, "Marketing Analyst")
    assert res["ok"], res
    assert "Python" in res["gaps"]
