"""Her earlier answer bubble is never an option of the next question (a company, /19).
Evidence: a saved failure page - drawer lines below; the bot clicked 'English, Tamil' for the experience question."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from apply import answers_map, naukri_apply  # noqa: E402

LINES = ["Languages you speak and understand? Note: Eng & Tam is Mandatory - Mention other languages", "English, Tamil",
         "Your Experience - Quality Analyst (BPO) or Customer Support?", "Quality Analyst (BPO)", "Customer Support", "Skip this question"]


def test_the_previous_answer_bubble_is_not_a_chip():
    opts = ["Quality Analyst (BPO)", "Customer Support", "English, Tamil"]
    assert naukri_apply.drop_history_chips(LINES, 2, opts) == ["Quality Analyst (BPO)", "Customer Support"]


def test_a_chip_that_also_appears_below_the_question_is_kept():
    lines = ["Are you an immediate joiner?", "Yes", "Are you open to Bengaluru?", "Yes", "No"]
    assert naukri_apply.drop_history_chips(lines, 2, ["Yes", "No"]) == ["Yes", "No"]


def test_which_kind_of_experience_she_has_is_her_question():
    assert answers_map.needs_her("Your Experience - Quality Analyst (BPO) or Customer Support?")
    assert not answers_map.needs_her("Total experience?")


def test_a_self_rating_is_her_question():
    assert answers_map.needs_her("Please rate yourself out of 10 in SQL?")
    lines = ["Are you living in Bengaluru ?", "Yes", "How many years of experience do you have as BI Developer ?", "3-4 years",
             "Please rate yourself out of 10 in SQL?", "1", "2", "10"]
    assert "3-4 years" not in naukri_apply.drop_history_chips(lines, 4, ["1", "2", "10", "3-4 years"])
