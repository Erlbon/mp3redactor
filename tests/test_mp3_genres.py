from core.mp3_genres import exclude_hidden, merge_genres


def test_merge_genres_skips_duplicates_case_insensitively():
    defaults = ["Rock", "Jazz"]
    custom = ["rock", "Ambient"]
    assert merge_genres(defaults, custom) == ["Rock", "Jazz", "Ambient"]


def test_merge_genres_skips_blank_custom_entries():
    assert merge_genres(["Rock"], ["  ", ""]) == ["Rock"]


def test_exclude_hidden_genres_case_insensitive():
    defaults = ["Rock", "Jazz", "Polka"]
    assert exclude_hidden(defaults, ["polka"]) == ["Rock", "Jazz"]


def test_exclude_hidden_genres_empty_hidden_list_is_a_noop():
    defaults = ["Rock", "Jazz"]
    assert exclude_hidden(defaults, []) == defaults
