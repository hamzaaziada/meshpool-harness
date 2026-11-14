from meshpool_harness import pace as P


def test_quick_questions_and_quick_research():
    for t in ("What's the capital of France?", "Do some quick research and tell me which CRM small agencies use",
              "Explain what a REST API is in two sentences.", "Compare Notion and Asana for my team",
              "give me 3 name ideas for a coffee shop"):
        assert P.classify(t) == "quick", t


def test_jobs_and_deep_requests_keep_full_effort():
    for t in ("Write a project proposal as a Word document", "fix the failing test in calc.py",
              "Build a revenue model in Excel", "create a landing page for my bakery", "hi"):
        assert P.classify(t) == "normal", t
    assert P.classify("Do a deep research report on the EV battery market") == "deep"
    assert P.classify("What is this?", has_files=True) == "normal"


def test_note_is_hidden_in_the_chat():
    assert P.NOTE.startswith("\n\n​")


def test_a_pasted_zero_width_space_is_user_text_not_a_note():
    # text pasted from the web often carries ​: only the exact "\n\n​" separator starts a hidden note
    assert P.classify("Can you​ write a project proposal as a Word document") == "normal"
    assert P.classify("What's the capital of France?" + P.NOTE) == "quick"
    assert P.classify("hi\n\n​Write a project proposal as a Word document") == "normal"
