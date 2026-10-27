from types import SimpleNamespace
from unittest.mock import Mock, call

import pytest

from linkedin_interaction.config import Settings
from linkedin_interaction.session import FEED_URL, LOGIN_URL, LinkedInError, Session


@pytest.mark.parametrize("url", [
    "https://www.linkedin.com/login?session_redirect=/feed/",
    "https://www.linkedin.com/checkpoint/challenge?next=/feed/",
    None,
])
def test_login_does_not_accept_a_redirect_target_as_the_feed(url):
    session = Session(Settings())
    session._driver = SimpleNamespace(current_url=url)
    session.open = Mock()
    session.find_optional = Mock(return_value=None)

    with pytest.raises(LinkedInError, match="login did not reach"):
        session.login(timeout=0)

    assert session.open.call_args_list == [call(FEED_URL), call(LOGIN_URL)]


@pytest.mark.parametrize("url", ["https://www.linkedin.com/feed", FEED_URL, FEED_URL + "?trk=login"])
def test_login_accepts_the_feed(url):
    session = Session(Settings())
    session._driver = SimpleNamespace(current_url=url)
    session.open = Mock()

    session.login(timeout=0)

    session.open.assert_called_once_with(FEED_URL)
