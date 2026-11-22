from apply import agent


def test_workday_completed_route_counts_only_through_final_page_confirmed():
    snap = {"url": "https://ms.wd5.myworkdayjobs.com/en-US/External/jobTasks/completed/application", "text": "Candidate Home My Applications Date Submitted", "els": []}
    assert agent.final_page_confirmed(snap) is True
    assert agent._confirmed(snap) is False   # the general rule stays strict


def test_ordinary_candidate_home_is_not_final_confirmation():
    snap = {"url": "https://x.wd5.myworkdayjobs.com/en-US/External/userHome", "text": "My Applications Date Submitted Application Received", "els": []}
    assert agent.final_page_confirmed(snap) is False
