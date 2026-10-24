import pytest

from linkedin_interaction.session import LinkedInError
from linkedin_interaction.social import _comments_from_page, awaiting_reply, find_comment

ME = "kennethbcwang"


def row(comment_id, name, username, left, text="hi"):
    return {
        "key": f"replaceableComment_urn:li:comment:(activity:7519138065618120704,{comment_id})",
        "href": f"https://www.linkedin.com/in/{username}/?miniProfileUrn=x",
        "label": f"View {name}’s profile",
        "text": text,
        "left": left,
    }


def comments(*rows):
    return _comments_from_page(list(rows), ME)


def test_indented_rows_are_replies_and_your_profile_is_mine():
    parsed = comments(row("1", "Taylor Kolasinski", "taylor-kolasinski", 389), row("2", "Kenneth W.", ME, 429))
    assert [(c.id, c.author, c.is_reply, c.mine) for c in parsed] == [
        ("1", "Taylor Kolasinski", False, False),
        ("2", "Kenneth W.", True, True),
    ]
    assert parsed[0].profile == "https://www.linkedin.com/in/taylor-kolasinski/"


def test_awaiting_reply_counts_only_your_replies_in_the_same_thread():
    parsed = comments(
        row("1", "Ann", "ann", 389),
        row("2", "Bob", "bob", 429),  # someone else's reply does not answer Ann for you
        row("3", "Cat", "cat", 389),
        row("4", "Kenneth W.", ME, 429),
        row("5", "Kenneth W.", ME, 389),  # your own top-level comment never awaits you
    )
    assert [c.id for c in awaiting_reply(parsed)] == ["1"]


def test_find_comment_by_id_or_unique_author():
    parsed = comments(row("1", "Ann", "ann", 389), row("2", "Bob", "bob", 429), row("3", "Ann", "ann", 389))
    assert find_comment(parsed, "2").author == "Bob"
    with pytest.raises(LinkedInError, match="pass the comment id"):
        find_comment(parsed, "ann")
    with pytest.raises(LinkedInError, match="No comment"):
        find_comment(parsed, "Bob")  # Bob only replied; names match top-level comments only
