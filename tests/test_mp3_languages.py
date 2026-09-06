from core.mp3_languages import exclude_hidden, merge_languages


def test_merge_languages_skips_duplicate_codes():
    defaults = [("eng", "English"), ("nor", "Norwegian")]
    custom = [("eng", "English (dup)"), ("isl", "Icelandic")]
    assert merge_languages(defaults, custom) == [
        ("eng", "English"),
        ("nor", "Norwegian"),
        ("isl", "Icelandic"),
    ]


def test_merge_languages_skips_blank_code_or_name():
    defaults = [("eng", "English")]
    custom = [("", "Nameless"), ("xx", "  ")]
    assert merge_languages(defaults, custom) == defaults


def test_exclude_hidden_languages_by_code():
    defaults = [("eng", "English"), ("nor", "Norwegian"), ("und", "Undetermined")]
    assert exclude_hidden(defaults, ["und"]) == [("eng", "English"), ("nor", "Norwegian")]


def test_exclude_hidden_languages_empty_hidden_list_is_a_noop():
    defaults = [("eng", "English")]
    assert exclude_hidden(defaults, []) == defaults
