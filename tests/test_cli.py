import logging
from unittest.mock import MagicMock

import pytest

from linkedin_interaction import cli
from linkedin_interaction.config import JobSearchSettings, MatchingSettings, Settings


@pytest.mark.parametrize("failure", ["missing_resume", "output_is_directory"])
def test_job_match_reports_file_errors_and_closes_the_session(monkeypatch, tmp_path, caplog, failure):
    settings = Settings(
        jobs=JobSearchSettings(keywords="Python"),
        matching=MatchingSettings(resume=tmp_path / "missing-resume.txt", output=tmp_path),
    )
    monkeypatch.setattr(cli, "load_settings", lambda _: settings)
    context = MagicMock()
    monkeypatch.setattr(cli, "Session", lambda _: context)
    args = ["jobs", "match"]
    if failure == "output_is_directory":
        args.append("--no-rerank")
        monkeypatch.setattr(cli.job_matching, "match_jobs", lambda *a, **kw: [])

    with caplog.at_level(logging.ERROR, logger="linkedin_interaction"):
        assert cli.main(args) == 1

    assert str(settings.matching.resume if failure == "missing_resume" else tmp_path) in caplog.text
    context.__exit__.assert_called_once()
