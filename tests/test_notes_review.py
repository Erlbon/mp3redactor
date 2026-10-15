"""Create Notes review page: the rules (core/notes_review.py) on real scanned folders and real engine proposals built
from the recorded fixtures. The writes go through a fake write function unless a test is about the real one."""

from core.notes_flow import STATUS_SKIPPED, Confidence, NotesEngine, build_proposals, scan, write_proposal
from core.notes_local import ALL_KINDS
from core.notes_review import (
    ERROR, EXISTS, PENDING, SKIPPED, VIEW_LIST, VIEW_ONE, WRITTEN, ReviewSession, choose_view,
)
from redactor_common.core.notes import NoteWriteResult
from tests.notes_helpers import make_mp3, ultravox_fetchers, ultravox_library


def targets_of(root, **kw):
    return scan([root], kinds=ALL_KINDS, **kw).targets


# -- which view -----------------------------------------------------------------------------------------------------

def test_one_artist_folder_with_its_albums_opens_one_at_a_time(tmp_path):
    ultravox_library(tmp_path)
    found = targets_of(tmp_path)
    assert sorted(t.kind for t in found) == ["album", "album", "artist"]
    assert choose_view(found) == VIEW_ONE


def test_one_album_alone_opens_one_at_a_time(tmp_path):
    artist = ultravox_library(tmp_path)
    assert choose_view(targets_of(artist / "Vienna (1980)")) == VIEW_ONE


def test_several_artist_folders_open_the_list(tmp_path):
    ultravox_library(tmp_path / "Rock")
    make_mp3(tmp_path / "Rock" / "Other Band" / "Debut (1990)" / "01 a.mp3", "a", "Other Band", "Debut", 1, "1990")
    found = targets_of(tmp_path)
    assert choose_view(found) == VIEW_LIST


def test_a_collection_with_its_albums_is_one_folder_but_a_collection_beside_an_artist_is_two(tmp_path):
    base = tmp_path / "Soundtracks"
    make_mp3(base / "Blade Runner (1982)" / "01 a.mp3", "a", "Vangelis", "Blade Runner", 1, "1982", albumartist="Various Artists")
    make_mp3(base / "Pulp Fiction (1994)" / "01 a.mp3", "a", "Dick Dale", "Pulp Fiction", 1, "1994", albumartist="Various Artists")
    assert choose_view(targets_of(tmp_path)) == VIEW_ONE
    ultravox_library(tmp_path)
    assert choose_view(targets_of(tmp_path)) == VIEW_LIST


def test_nothing_found_is_not_a_list():
    assert choose_view([]) == VIEW_ONE


# -- ticks, skip, write ---------------------------------------------------------------------------------------------

class FakeWriter:
    def __init__(self, status="written"):
        self.calls, self.status = [], status

    def __call__(self, proposal):
        self.calls.append(proposal.path)
        return NoteWriteResult(self.status, proposal.path, "" if self.status == "written" else "an existing note")


def mixed_session(tmp_path, writer):
    """Ultravox (confident artist + album notes) beside 'Nobody' (nothing found): a real engine run."""
    ultravox_library(tmp_path, with_quartet=False)
    make_mp3(tmp_path / "Nobody" / "Nothing (1999)" / "01 a.mp3", "a", "Nobody", "Nothing", 1, "1999")
    fetchers = ultravox_fetchers()
    fetchers.mb.routes.insert(0, (r"ws/2/artist\?query=artist%3A%22Nobody", {"artists": []}))
    fetchers.mb.routes.insert(0, (r"ws/2/release-group\?query=.*Nobody", {"release-groups": []}))
    engine = NotesEngine(fetchers)
    proposals = build_proposals(targets_of(tmp_path), engine=engine)
    return ReviewSession(proposals, write_fn=writer), engine


def test_confident_are_preticked_and_uncertain_or_nothing_found_cannot_be_ticked(tmp_path):
    session, _engine = mixed_session(tmp_path, FakeWriter())
    by_conf = {}
    for i, item in enumerate(session.items):
        by_conf.setdefault(item.proposal.confidence, []).append(i)
    assert by_conf[Confidence.CONFIDENT] and (by_conf.get(Confidence.UNCERTAIN) or by_conf.get(Confidence.NONE))
    for i in by_conf[Confidence.CONFIDENT]:
        assert session.items[i].ticked and session.items[i].tickable
    for conf in (Confidence.UNCERTAIN, Confidence.NONE):
        for i in by_conf.get(conf, []):
            assert not session.items[i].ticked
            assert session.set_ticked(i, True) is False and not session.items[i].ticked
    first = by_conf[Confidence.CONFIDENT][0]
    assert session.set_ticked(first, False) and not session.items[first].ticked
    assert session.set_ticked(first, True) and session.items[first].ticked


def test_write_ticked_writes_only_ticked_confident_through_the_write_function(tmp_path):
    writer = FakeWriter()
    session, _engine = mixed_session(tmp_path, writer)
    confident = [i for i, it in enumerate(session.items) if it.proposal.confidence == Confidence.CONFIDENT]
    session.set_ticked(confident[0], False)
    session.write_ticked()
    assert len(writer.calls) == len(confident) - 1
    assert session.items[confident[0]].state == PENDING  # unticked: untouched
    assert all(session.items[i].state == WRITTEN for i in confident[1:])
    assert not (tmp_path / "Ultravox" / "artist.md").exists()  # the fake writer wrote nothing
    totals = session.totals()
    assert totals.written == len(confident) - 1 and totals.pending_confident == 1


def test_a_proposal_that_is_not_confident_is_never_passed_to_the_write_function(tmp_path):
    writer = FakeWriter()
    session, _engine = mixed_session(tmp_path, writer)
    open_ones = [i for i, it in enumerate(session.items) if not it.proposal.writable]
    assert open_ones
    result = session.write(open_ones[0])
    assert result.status == STATUS_SKIPPED and writer.calls == []
    assert session.items[open_ones[0]].state == PENDING


def test_skip_marks_the_proposal_and_unticks_it(tmp_path):
    writer = FakeWriter()
    session, _engine = mixed_session(tmp_path, writer)
    i = next(i for i, it in enumerate(session.items) if it.tickable)
    session.skip(i)
    assert session.items[i].state == SKIPPED and not session.items[i].ticked
    session.write_ticked()
    assert writer.calls and session.items[i].proposal.path not in writer.calls
    assert session.totals().skipped == 1


def test_an_existing_note_that_blocks_the_write_is_reported_not_counted_as_written(tmp_path):
    session, _engine = mixed_session(tmp_path, FakeWriter(status="exists"))
    session.write_ticked()
    totals = session.totals()
    assert totals.written == 0 and totals.blocked >= 1
    assert any("an existing note" in m for m in totals.messages)
    assert all(it.state == EXISTS for it in session.items if it.proposal.writable)


def test_a_write_error_is_an_error_state(tmp_path):
    session, _engine = mixed_session(tmp_path, FakeWriter(status="error"))
    i = next(i for i, it in enumerate(session.items) if it.tickable)
    session.write(i)
    assert session.items[i].state == ERROR and session.totals().errors == 1


def test_next_pending_wraps_and_ends(tmp_path):
    session, _engine = mixed_session(tmp_path, FakeWriter())
    n = len(session.items)
    assert session.next_pending(-1) == 0
    for i in range(n):
        session.skip(i)
    assert session.next_pending(0) is None


def test_the_real_write_function_still_never_overwrites(tmp_path):
    ultravox_library(tmp_path, with_quartet=False)
    [artist] = [p for p in build_proposals(targets_of(tmp_path), engine=NotesEngine(ultravox_fetchers())) if p.kind == "artist"]
    session = ReviewSession([artist], write_fn=write_proposal)
    (tmp_path / "Ultravox" / "ARTIST.MD").write_text("my own note\n", encoding="utf-8")  # appears after the scan
    session.write(0)
    assert session.items[0].state == EXISTS
    assert (tmp_path / "Ultravox" / "ARTIST.MD").read_text(encoding="utf-8") == "my own note\n"


def test_replace_after_a_chosen_candidate_makes_it_tickable(tmp_path):
    base = tmp_path / "Ultravox & Friends"
    for album, year in (("Vienna", "1980"), ("Quartet", "1982")):
        make_mp3(base / f"{album} ({year})" / "01 a.mp3", "a", "Ultravox & Friends", album, 1, year)
    engine = NotesEngine(ultravox_fetchers())
    [artist] = [p for p in build_proposals(targets_of(tmp_path), engine=engine) if p.kind == "artist"]
    session = ReviewSession([artist], write_fn=FakeWriter())
    assert not session.items[0].tickable and session.set_ticked(0, True) is False
    item = session.replace(0, engine.rebuild(artist, candidate=artist.candidates[0]))
    assert item.tickable and item.ticked and item.user_resolved
