from linkedin_interaction.job_requirements import extract_requirements


def test_splits_required_and_preferred_and_stops_at_the_next_section() -> None:
    requirements = extract_requirements(
        "About the role\n\nYou will build things.\n\nMinimum Qualifications\n\n"
        "- 10+ years of experience with ML.\n- Python and PyTorch\n\n"
        "Experience as a mentor, tech lead or leading an engineering team\n\n"
        "Preferred Qualifications\n\n- PhD in AI.\n"
        "- Amazon is an equal opportunity employer and does not discriminate.\n\n"
        "Benefits\n\n- Free lunch\n"
    )
    assert requirements.required == (
        "10+ years of experience with ML.", "Python and PyTorch",
        "Experience as a mentor, tech lead or leading an engineering team",
    )
    assert requirements.preferred == ("PhD in AI.",)


def test_requirements_headings_win_over_role_and_about_headings() -> None:
    # "You will" and "About" open role and company sections, except in these requirements headings.
    thrive = extract_requirements("In This Role, You Will\n\n- Ship\n\nYou Will Thrive In This Role If You Have\n\n"
                                  "- Go experience\n\nAbout OpenAI\n\nWe are a lab.\n")
    assert thrive.required == ("Go experience",)
    assert extract_requirements("About You\n\n- Rust\n").required == ("Rust",)


def test_boilerplate_requirement_headings_are_not_requirements() -> None:
    requirements = extract_requirements(
        "Requirements\n\n- 5+ years in Go\n\nIn-Office Requirement Statement\n\n- Three days a week in the office\n"
    )
    assert requirements.required == ("5+ years in Go",)


def test_heading_glued_to_the_previous_sentence_still_opens_a_section() -> None:
    requirements = extract_requirements(
        "This position requires in-person interviews.Minimum qualifications:\n"
        "- Bachelor’s degree or equivalent practical experience.\n- 8 years of experience in software development.\n"
        "Preferred qualifications:\n- Master’s degree or PhD in Computer Science.\n"
    )
    assert requirements.required == ("Bachelor’s degree or equivalent practical experience.",
                                     "8 years of experience in software development.")
    assert requirements.degrees == ("BS",)
    assert requirements.preferred == ("Master’s degree or PhD in Computer Science.",)


def test_years_come_from_the_required_section_only() -> None:
    requirements = extract_requirements(
        "Basic Qualifications\n\n- 10+ years of software development\n- 5 years of ML experience\n\n"
        "Preferred Qualifications\n\n- 15 years leading teams\n\nAbout Us\n\nFounded 50 years ago.\n"
    )
    assert (requirements.min_years, requirements.max_years) == (5, 10)
    assert extract_requirements("You have\n\n- Taste\n").min_years is None


def test_salary_ranges_in_the_formats_postings_use() -> None:
    description = (
        "US: $207000 - $300000 (USD) + 20% bonus target\n"
        "$292,000—$365,000 USD\n"
        "Compensation Range: $230K - $385K\n"
        "The base salary range is 272,000 USD - 431,250 USD.\n"
        "USD $232,000 per year - USD $258,000 per year.\n"
        "$271,000/year to $347,000/year + bonus\n"
        "USA, WA, Seattle - 200,100.00 - 270,600.00 USD annually\n"
        "A weekly lunch stipend of $75.\n"
        "$292,000—$365,000 USD\n"
    )
    assert extract_requirements(description).salary_ranges == (
        "$207000 - $300000",
        "$292,000—$365,000 USD",
        "$230K - $385K",
        "272,000 USD - 431,250 USD",
        "USD $232,000 per year - USD $258,000 per year",
        "$271,000/year to $347,000/year",
        "200,100.00 - 270,600.00 USD annually",
    )
