"""Updating notes made by an older version, only when asked: every note we write is stamped (generator, note-format,
body-sha); the scan lists 'outdated-untouched' and 'legacy' notes as UPDATE proposals only with include_outdated;
'edited', 'foreign' and 'current' notes are never listed or touched; an update goes through replace_note (the old
note to the Recycle Bin); the review page shows Before / after and never pre-ticks an update; the CLI's --update."""

import json
import os

import pytest

from core.notes_flow import (
    Confidence, Fetchers, NotesEngine, build_proposals, scan, summarize, write_proposal,
)
from core.notes_local import KIND_ALBUM, KIND_ARTIST
from core.notes_render import CURRENT_NOTE_FORMAT
from core.notes_review import ReviewSession, note_diff
from redactor_common.core.note_text import credit_footer
from redactor_common.core.notes import (
    STATE_CURRENT, STATE_EDITED, STATE_FOREIGN, STATE_LEGACY, STATE_OUTDATED, NoteFrontMatter, note_status,
    parse_front_matter, render_note,
)
from tests.notes_helpers import make_mp3, ultravox_fetchers, ultravox_library

GEN = "mp3redactor 2026-10-01#01"


def our_note(title, fmt=CURRENT_NOTE_FORMAT, text="Old text."):
    return render_note(NoteFrontMatter(title=title, generator=GEN, note_format=fmt), f"# {title}\n\n{text}")


def legacy_note(title, text="Old text."):
    return render_note(NoteFrontMatter(title=title), f"# {title}\n\n{text}\n\n" + credit_footer(["Facts from MusicBrainz"]))


def put(path, text):
    path.write_text(text, encoding="utf-8", newline="\n")
    return path


def five_states(root):
    """Five artist folders, each with one album folder and an artist.md in one of the five states."""
    notes = {
        "Cur": our_note("Cur"),
        "Old": our_note("Old", fmt=1),
        "Edit": our_note("Edit").replace("Old text.", "My own words."),
        "Leg": legacy_note("Leg"),
        "Foreign": "---\ntitle: Foreign\n---\n\nWritten by hand.\n",
    }
    for name, text in notes.items():
        make_mp3(root / name / f"{name} Album (2000)" / "01 a.mp3", "a", name, f"{name} Album", 1, "2000")
        put(root / name / "artist.md", text)
    return root


def names(found, attr="targets"):
    return sorted(t.folder.name for t in getattr(found, attr))


# -- the stamp ---------------------------------------------------------------------------------------------------

def test_every_note_the_engine_writes_is_stamped_and_current(tmp_path):
    ultravox_library(tmp_path)
    proposals = build_proposals(scan([tmp_path], kinds=(KIND_ARTIST, KIND_ALBUM)).targets, ultravox_fetchers())
    assert proposals and all(p.writable for p in proposals)
    for proposal in proposals:
        meta, _body = parse_front_matter(proposal.note_text)
        assert meta["generator"].startswith("mp3redactor ") and meta["note-format"] == str(CURRENT_NOTE_FORMAT)
        assert len(meta["body-sha"]) == 16 and meta["status"] == "draft"
        assert write_proposal(proposal).ok
        assert note_status(proposal.path, CURRENT_NOTE_FORMAT).kind == STATE_CURRENT


# -- the scan ----------------------------------------------------------------------------------------------------

def test_the_scan_lists_no_update_unless_asked(tmp_path):
    five_states(tmp_path)
    found = scan([tmp_path], kinds=(KIND_ARTIST,))
    assert found.targets == [] and len(found.skipped) == 5 and all(s.state == "" for s in found.skipped)


def test_with_include_outdated_only_outdated_and_legacy_notes_become_updates(tmp_path):
    five_states(tmp_path)
    found = scan([tmp_path], kinds=(KIND_ARTIST,), include_outdated=True)
    assert names(found) == ["Leg", "Old"]
    states = {t.folder.name: t.update.kind for t in found.targets}
    assert states == {"Leg": STATE_LEGACY, "Old": STATE_OUTDATED}
    assert {s.folder.name: s.state for s in found.skipped} == {
        "Cur": STATE_CURRENT, "Edit": STATE_EDITED, "Foreign": STATE_FOREIGN}


def test_a_note_of_another_kind_in_the_folder_is_never_an_update(tmp_path):
    make_mp3(tmp_path / "A" / "Album (2000)" / "01 a.mp3", "a", "A", "Album", 1, "2000")
    put(tmp_path / "A" / "about.md", legacy_note("A"))  # the folder's note is about.md: not an artist note
    found = scan([tmp_path], kinds=(KIND_ARTIST,), include_outdated=True)
    assert found.targets == [] and found.skipped[0].state == ""


def test_the_note_name_may_be_in_any_letter_case(tmp_path):
    make_mp3(tmp_path / "A" / "Album (2000)" / "01 a.mp3", "a", "A", "Album", 1, "2000")
    put(tmp_path / "A" / "ARTIST.MD", our_note("A", fmt=1))
    [target] = scan([tmp_path], kinds=(KIND_ARTIST,), include_outdated=True).targets
    assert target.update.kind == STATE_OUTDATED and os.path.basename(target.update.path) == "ARTIST.MD"


# -- the engine and the writing ---------------------------------------------------------------------------------

def ultravox_with_old_notes(tmp_path, artist_note, album_note):
    artist = ultravox_library(tmp_path)
    put(artist / "artist.md", artist_note)
    put(artist / "Vienna (1980)" / "album.md", album_note)
    return artist


def test_an_update_proposal_carries_the_old_text_and_the_new_draft_and_replaces_through_the_bin(tmp_path, recycle_bin):
    artist = ultravox_with_old_notes(tmp_path, our_note("Ultravox", fmt=1), legacy_note("Vienna"))
    found = scan([tmp_path], kinds=(KIND_ARTIST, KIND_ALBUM), include_outdated=True)
    proposals = build_proposals(found.targets, ultravox_fetchers())
    updates = {p.kind: p for p in proposals if p.is_update}
    assert sorted(updates) == ["album", "artist"] and all(p.confidence == Confidence.CONFIDENT for p in updates.values())
    artist_update = updates["artist"]
    assert "Old text." in artist_update.old_text and "Ultravox were a British new wave band" in artist_update.note_text
    assert artist_update.to_dict()["action"] == "update" and artist_update.to_dict()["old_state"] == STATE_OUTDATED
    assert "-Old text." in note_diff(artist_update.old_text, artist_update.note_text)
    assert summarize(proposals).updates == 2

    result = write_proposal(artist_update)
    assert result.ok and result.replaced.endswith("artist.md")
    assert note_status(str(artist / "artist.md"), CURRENT_NOTE_FORMAT).kind == STATE_CURRENT
    assert "Ultravox were a British new wave band" in (artist / "artist.md").read_text(encoding="utf-8")
    assert len(recycle_bin.trashed) == 1  # the old note went to the (fake) Recycle Bin
    old = next(recycle_bin.folder.iterdir())
    assert "Old text." in old.read_text(encoding="utf-8") and not (artist / "artist.md.old").exists()


def test_an_update_never_overwrites_a_note_edited_after_it_was_looked_at(tmp_path, recycle_bin):
    artist = ultravox_with_old_notes(tmp_path, our_note("Ultravox", fmt=1), our_note("Vienna", fmt=1))
    [artist_target] = scan([tmp_path], kinds=(KIND_ARTIST,), include_outdated=True).targets
    [proposal] = build_proposals([artist_target], ultravox_fetchers())
    put(artist / "artist.md", our_note("Ultravox", fmt=1, text="I rewrote it just now."))  # the race
    result = write_proposal(proposal)
    assert result.status == "changed" and not result.ok and recycle_bin.trashed == []
    assert "I rewrote it just now." in (artist / "artist.md").read_text(encoding="utf-8")


def test_a_rebuild_after_choosing_a_candidate_keeps_it_an_update(tmp_path):
    ultravox_with_old_notes(tmp_path, our_note("Ultravox", fmt=1), our_note("Vienna", fmt=1))
    [target] = scan([tmp_path], kinds=(KIND_ARTIST,), include_outdated=True).targets
    engine = NotesEngine(ultravox_fetchers())
    proposal = engine.build_proposal(target)
    rebuilt = engine.rebuild(proposal, candidate=proposal.candidates[0])
    assert rebuilt.is_update and rebuilt.old_state.kind == STATE_OUTDATED


# -- the review session ------------------------------------------------------------------------------------------

def test_updates_are_never_preticked_and_write_all_remaining_skips_them(tmp_path, recycle_bin):
    artist = ultravox_with_old_notes(tmp_path, our_note("Ultravox", fmt=1), our_note("Vienna", fmt=1))
    make_mp3(tmp_path / "Ultravox" / "Quartet (1982)" / "02 Hymn.mp3", "Hymn", "Ultravox", "Quartet", 2, "1982")
    found = scan([tmp_path], kinds=(KIND_ARTIST, KIND_ALBUM), include_outdated=True)
    proposals = build_proposals(found.targets, ultravox_fetchers())
    session = ReviewSession(proposals)
    by_kind = {(i.proposal.kind, i.proposal.folder.name): i for i in session.items}
    update = by_kind[("artist", "Ultravox")]
    fresh = by_kind[("album", "Quartet (1982)")]  # a folder with no note at all: a normal new note
    assert update.proposal.writable and update.proposal.is_update and not update.ticked
    assert fresh.ticked and not fresh.proposal.is_update
    session.write_remaining_confident()
    assert (artist / "Quartet (1982)" / "album.md").exists()  # the new note was written
    assert "Old text." in (artist / "artist.md").read_text(encoding="utf-8")  # the update was not
    assert recycle_bin.trashed == []
    session.set_ticked(session.items.index(update), True)
    results = session.write_ticked()
    assert [r.ok for r in results] == [True] and len(recycle_bin.trashed) == 1


# -- the command line --------------------------------------------------------------------------------------------

@pytest.fixture
def cli(monkeypatch, capsys, tmp_path):
    from core.settings import Settings
    from mp3cli import cmd_notes, files as cli_files
    from mp3cli.main import main

    monkeypatch.setattr(cli_files, "load_settings", lambda *a, **k: Settings())
    state = {"fetchers": ultravox_fetchers()}
    monkeypatch.setattr(cmd_notes, "make_fetchers", lambda use: state["fetchers"])

    def run(*argv):
        state["fetchers"] = ultravox_fetchers()
        code = main(["notes", *argv, "--json", "--no-cache"])
        return code, json.loads(capsys.readouterr().out)

    return run


def old_ultravox(tmp_path, artist_note=None):
    artist = ultravox_library(tmp_path)
    put(artist / "artist.md", artist_note or our_note("Ultravox", fmt=1))
    return artist


def rows(document):
    return {(r["kind"], r["folder"].replace("\\", "/").split("/")[-1]): r for r in document["results"]}


def test_cli_without_update_leaves_every_existing_note_alone(tmp_path, cli):
    artist = old_ultravox(tmp_path)
    before = (artist / "artist.md").read_text(encoding="utf-8")
    code, document = cli(str(tmp_path), "--kind", "artist", "--write")
    assert code == 0 and (artist / "artist.md").read_text(encoding="utf-8") == before
    assert rows(document)[("artist", "Ultravox")]["status"] == "has-note"


def test_cli_update_reports_and_with_write_replaces_only_outdated_untouched_notes(tmp_path, cli, recycle_bin):
    artist = old_ultravox(tmp_path)
    code, document = cli(str(tmp_path), "--kind", "artist", "--update")
    row = rows(document)[("artist", "Ultravox")]
    assert code == 0 and row["status"] == "would-update" and row["action"] == "update"
    assert "Old text." in (artist / "artist.md").read_text(encoding="utf-8")  # a report: nothing changed
    assert document["updates"] == 1 and document["left_alone"] == 0
    code, document = cli(str(tmp_path), "--kind", "artist", "--update", "--write")
    assert rows(document)[("artist", "Ultravox")]["status"] == "updated"
    assert "Ultravox were a British new wave band" in (artist / "artist.md").read_text(encoding="utf-8")
    assert note_status(str(artist / "artist.md"), CURRENT_NOTE_FORMAT).kind == STATE_CURRENT
    assert len(recycle_bin.trashed) == 1
    # a second run: it is current now, nothing to update
    code, again = cli(str(tmp_path), "--kind", "artist", "--update", "--write")
    assert again["updates"] == 0 and again["already_current"] == 1 and len(recycle_bin.trashed) == 1


def test_cli_legacy_notes_need_the_extra_flag(tmp_path, cli, recycle_bin):
    artist = old_ultravox(tmp_path, legacy_note("Ultravox"))
    code, document = cli(str(tmp_path), "--kind", "artist", "--update")
    assert rows(document)[("artist", "Ultravox")]["old_state"] == STATE_LEGACY
    code, document = cli(str(tmp_path), "--kind", "artist", "--update", "--write")
    row = rows(document)[("artist", "Ultravox")]
    assert row["status"] == "legacy" and "--update-legacy" in row["message"]
    assert "Old text." in (artist / "artist.md").read_text(encoding="utf-8") and recycle_bin.trashed == []
    code, document = cli(str(tmp_path), "--kind", "artist", "--update-legacy", "--write")
    assert rows(document)[("artist", "Ultravox")]["status"] == "updated" and len(recycle_bin.trashed) == 1
    assert note_status(str(artist / "artist.md"), CURRENT_NOTE_FORMAT).kind == STATE_CURRENT


def test_cli_never_touches_an_edited_or_a_foreign_note_even_with_every_flag(tmp_path, cli, recycle_bin):
    edited = our_note("Ultravox", fmt=1).replace("Old text.", "My own words.")
    artist = old_ultravox(tmp_path, edited)
    code, document = cli(str(tmp_path), "--kind", "artist", "--update", "--update-legacy", "--write")
    assert (artist / "artist.md").read_text(encoding="utf-8") == edited and recycle_bin.trashed == []
    assert document["left_alone"] == 1 and rows(document)[("artist", "Ultravox")]["status"] == "left-alone"
    foreign = "---\ntitle: Mine\n---\n\nmine\n"
    put(artist / "artist.md", foreign)
    code, document = cli(str(tmp_path), "--kind", "artist", "--update", "--update-legacy", "--write")
    assert (artist / "artist.md").read_text(encoding="utf-8") == foreign and document["left_alone"] == 1


def test_cli_update_records_the_replacement_in_the_opt_in_history(tmp_path, cli, journal):
    from core import history

    old_ultravox(tmp_path)
    cli(str(tmp_path), "--kind", "artist", "--update", "--write")
    assert journal.entries() == []  # history is off: nothing recorded
    old_ultravox(tmp_path)
    put(tmp_path / "Ultravox" / "artist.md", our_note("Ultravox", fmt=1))
    history.set_history_enabled(True)
    cli(str(tmp_path), "--kind", "artist", "--update", "--write")
    [entry] = journal.entries()
    assert entry.kind == "note-update" and "Recycle Bin" in str(entry.changes)  # batch 4: a refresh has its own kind
    import os

    assert os.path.isfile(os.path.join(history.notes_backup_base(), "notes_backup", f"{entry.id}.md"))  # the older text, for the undo
