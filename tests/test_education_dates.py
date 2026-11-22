""" a company (SuccessFactors): a model typed education start/end months nobody had recorded. A submitted application cannot be edited, so
the final-submit check now refuses an education date that is not the one in cv/facts.yaml. A guard in code, not only an instruction."""
from apply import agent

EXP = {"education": [
    {"institution": "Larkmoor University, Westfield School of Statistics (WSS)", "degree": "MSc (Statistics)", "field": "Statistics",
     "year": "2022", "start": "Aug 2020", "end": "Jun 2022", "start_year": "2020"},
    {"institution": "Brightwater College of Science", "degree": "BSc (Mathematics)", "field": "Mathematics",
     "year": "2018", "start_year": "2015"}]}


def row(n, inst, **dates):
    els = [{"i": n * 10, "row": f"{n} of 2", "tag": "input", "label": "School or University", "value": inst}]
    for k, (lab, val) in enumerate(dates.values(), 1):
        els.append({"i": n * 10 + k, "row": f"{n} of 2", "tag": "input", "label": lab, "value": val})
    return els


def bad(els):
    return {(m.label, m.shown) for m in agent.education_row_mismatches(els, EXP)}


def test_her_recorded_dates_pass_in_every_common_format():
    for start, end in (("08/2020", "06/2022"), ("2020-08-01", "2022-06-01"), ("01/08/2020", "01/06/2022"), ("August 2020", "Jun 2022"),
                       ("2020", "2022")):
        assert not bad(row(1, "Larkmoor University", a=("Start Date", start), b=("End Date (Actual or Expected)", end))), (start, end)


def test_a_guessed_month_is_refused():
    assert bad(row(1, "Larkmoor University", a=("Start Date", "07/2020"), b=("End Date", "05/2022"))) == {("Start Date", "07/2020"), ("End Date", "05/2022")}
    assert bad(row(1, "Larkmoor University", a=("From", "September 2020"))) == {("From", "September 2020")}


def test_a_wrong_year_is_refused():
    assert bad(row(1, "Larkmoor University", a=("Graduation Year", "2023"))) == {("Graduation Year", "2023")}
    assert bad(row(2, "Brightwater College of Science", a=("Start Year", "2016"))) == {("Start Year", "2016")}


def test_only_a_year_recorded_judges_only_the_year():
    assert not bad(row(2, "Brightwater College of Science", a=("Start Date", "07/2015"), b=("Year of Passing", "2018")))


def test_no_recorded_date_means_any_typed_date_is_a_guess():
    exp = {"education": [{"institution": "Larkmoor University", "degree": "MSc", "field": "Statistics", "year": "2022"}]}
    got = agent.education_row_mismatches(row(1, "Larkmoor University", a=("Start Date", "08/2020")), exp)
    assert len(got) == 1 and got[0].fill is None and "ask her" in got[0].want


def test_a_row_that_is_not_hers_and_a_box_without_a_date_are_left_alone():
    assert not bad(row(1, "Some Other College", a=("Start Date", "01/2001")))
    assert not bad(row(1, "Larkmoor University", a=("Start Date", ""), b=("GPA to date", "8.1")))


def test_the_real_facts_file_carries_the_months():
    edu = agent._expected_identity()["education"]
    assert any(e.get("start") and e.get("end") for e in edu)


def test_her_linkedin_link_is_given_to_the_form_filler():
    """ a company: a form that requires a LinkedIn URL was abandoned as 'not provided in candidate profile' although data/answers.yaml has it."""
    assert "linkedin.com/in/asha-example" in agent._facts_compact()
