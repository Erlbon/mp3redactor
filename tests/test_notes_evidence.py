"""'What is in this folder': the Local files tab of the review page. A pure display model (LocalAlbum -> what is shown), the real
reading of a scratch album tagged in Cyrillic (embedded cover, folder.jpg, no cover, 70 tracks) and the real review dialog."""

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from mutagen.id3 import APIC, ID3  # noqa: E402
from PyQt6.QtCore import QBuffer, QByteArray, QIODevice, Qt  # noqa: E402
from PyQt6.QtGui import QColor, QImage  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

from core.notes_evidence import MAX_TRACKS, Evidence, album_evidence, evidence_lines, read_evidence, seconds_text  # noqa: E402
from core.notes_flow import Fetchers  # noqa: E402
from core.notes_local import LocalAlbum, LocalTrack, read_album  # noqa: E402
from gui import notes_evidence_view as view_module  # noqa: E402
from gui.notes_evidence_view import LocalEvidenceView, load_evidence, make_thumb  # noqa: E402
from tests.notes_helpers import FakeFetch, make_mp3, set_kinds  # noqa: E402
from tests.test_cover_gui import window  # noqa: E402,F401
from tests.test_notes_gui import boxes, click, review, wait  # noqa: E402,F401
from tests.test_notes_scope import drive, scope_dialog  # noqa: E402,F401

QApplication.instance() or QApplication([])
BAND, ALBUM = "Ленинград", "Дачники"
TRACKS = ["Мне хочется кричать", "Вояж", "Дачники"]


def png_bytes(width=300, height=200, color="#c04040"):
    image = QImage(width, height, QImage.Format.Format_RGB32)
    image.fill(QColor(color))
    raw = QByteArray()
    buffer = QBuffer(raw)
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    image.save(buffer, "PNG")
    return bytes(raw)


def embed(path: Path, data: bytes):
    tags = ID3(str(path))
    tags.add(APIC(encoding=3, mime="image/png", type=3, desc="", data=data))
    tags.save(str(path))


def cyrillic_album(root, with_cover="embedded", count=3, folder=f"{ALBUM} (2000)"):
    for i in range(count):
        title = TRACKS[i] if i < len(TRACKS) else f"Трек {i + 1}"
        path = make_mp3(root / BAND / folder / f"{i + 1:02d} t.mp3", title, BAND, ALBUM, i + 1, "2000")
        if with_cover == "embedded" and i == 0:
            embed(path, png_bytes())
    if with_cover == "folder":
        (root / BAND / folder / "folder.jpg").write_bytes(b"")
        image = QImage(120, 120, QImage.Format.Format_RGB32)
        image.fill(QColor("#4040c0"))
        assert image.save(str(root / BAND / folder / "folder.jpg"), "JPG")
    return root / BAND / folder


# -- the display model, pure -------------------------------------------------------------------------------------------

def test_seconds_text():
    assert seconds_text(None) == "" and seconds_text(0) == "" and seconds_text(65.4) == "1:05" and seconds_text(3600) == "60:00"


def local_album(count, folder="Дачники (2000)"):
    tracks = [LocalTrack(path=Path(f"{i}.mp3"), title=f"Трек {i}", track=i, seconds=61 + i, artist=BAND, album=ALBUM, year="2000")
              for i in range(1, count + 1)]
    return LocalAlbum(folder=Path(folder), title=ALBUM, year="2000", tag_titles=[ALBUM], artist=BAND, tracks=tracks)


def test_the_album_display_model_keeps_the_original_script_and_the_tags():
    model = album_evidence(local_album(3), (b"x", "embedded"))
    assert [t.title for t in model.tracks] == ["Трек 1", "Трек 2", "Трек 3"]
    assert (model.album_tag, model.artist_tag, model.year_tag, model.files) == (ALBUM, BAND, "2000", 3)
    assert model.tracks[0].number == "1" and model.tracks[0].length == "1:02" and model.cover_from == "embedded"
    lines = evidence_lines(Evidence(kind="album", folder_name="x", albums=[model]))
    assert lines[0] == "Folder: Дачники (2000)" and "album 'Дачники'" in lines[1] and f"artist '{BAND}'" in lines[1]
    assert lines[2] == "3 MP3 file(s)" and lines[3] == f"1. Трек 1 - {BAND} (1:02)"


def test_seventy_tracks_list_sixty_and_say_how_many_more():
    model = album_evidence(local_album(70))
    assert len(model.tracks) == MAX_TRACKS == 60 and model.more_tracks == 10 and model.files == 70
    lines = evidence_lines(Evidence(kind="album", albums=[model]))
    assert lines[-1] == "... and 10 more" and len(lines) == 3 + 60 + 1


def test_a_track_without_a_title_shows_its_file_name():
    local = local_album(1)
    local.tracks[0].title = ""
    local.tracks[0].path = Path("01 Вояж.mp3")
    assert album_evidence(local).tracks[0].title == "01 Вояж"


# -- reading real folders ---------------------------------------------------------------------------------------------

def test_an_embedded_cover_and_the_cyrillic_tracks_are_read(tmp_path):
    folder = cyrillic_album(tmp_path, "embedded")
    evidence = read_evidence(folder, "album")
    [album] = evidence.albums
    assert album.cover_from == "embedded" and album.cover and album.files == 3
    assert [t.title for t in album.tracks] == TRACKS and album.artist_tag == BAND and album.album_tag == ALBUM


def test_the_folder_image_is_used_when_nothing_is_embedded(tmp_path):
    folder = cyrillic_album(tmp_path, "folder")
    [album] = read_evidence(folder, "album").albums
    assert album.cover_from == "folder.jpg" and album.cover


def test_no_cover_is_none(tmp_path):
    folder = cyrillic_album(tmp_path, "none")
    [album] = read_evidence(folder, "album").albums
    assert album.cover is None and album.tracks


def test_the_thumbnail_is_small_keeps_the_aspect_ratio_and_a_bad_picture_is_none():
    thumb = make_thumb(png_bytes(640, 320))
    image = QImage.fromData(thumb)
    assert (image.width(), image.height()) == (160, 80) and len(thumb) < 30_000
    assert make_thumb(b"not a picture") is None


def test_an_artist_lists_its_albums_and_up_to_six_covers(tmp_path):
    for n in range(8):
        path = make_mp3(tmp_path / BAND / f"Альбом {n} (200{n})" / "01 a.mp3", "Т", BAND, f"Альбом {n}", 1, f"200{n}")
        make_mp3(tmp_path / BAND / f"Альбом {n} (200{n})" / "02 b.mp3", "Т2", BAND, f"Альбом {n}", 2, f"200{n}")
        if n % 2 == 0:
            embed(path, png_bytes())
    evidence = read_evidence(tmp_path / BAND, "artist")
    assert len(evidence.albums) == 8 and evidence.covers == 6
    assert [a.files for a in evidence.albums] == [2] * 8
    assert evidence.albums[0].cover and evidence.albums[1].cover is None and evidence.albums[7].album_tag == ""  # beyond 6: no read
    lines = evidence_lines(evidence)
    assert lines[1].startswith("8 album folder(s), 16 MP3 file(s)") and lines[2] == f"Альбом 0 (2000): 2 track(s) (tagged 'Альбом 0' by '{BAND}')"


def test_an_unreadable_folder_is_said_not_raised(tmp_path):
    evidence = read_evidence(tmp_path / "gone", "album")
    assert evidence.albums and evidence.albums[0].files == 0 or evidence.error


# -- the view -----------------------------------------------------------------------------------------------------------

def wait_for(view, qtbot_free=True, timeout=10):
    import time
    end = time.perf_counter() + timeout
    while view.evidence is None:
        QApplication.processEvents()
        assert time.perf_counter() < end, "the files were not read"
        time.sleep(0.01)


def test_the_view_shows_the_cover_and_the_text_and_reads_in_the_background_once(tmp_path):
    view_module.clear_cache()
    folder = cyrillic_album(tmp_path, "embedded")
    calls = []

    class Counting:
        def __call__(self, paths):
            from core.notes_local import default_loader
            calls.append(len(paths))
            return default_loader(paths)

    LocalEvidenceView.loader = Counting()
    try:
        view = LocalEvidenceView()
        view.show_folder(folder, "album")
        assert view.evidence is None and view.status.text()  # not blocked: the answer comes later
        wait_for(view)
        assert view.thumb_labels[0].property("has_cover") is True and view.thumb_labels[0].pixmap().width() <= 160
        text = view.text.toPlainText()
        assert "Мне хочется кричать" in text and BAND in text and "3 MP3 file(s)" in text
        view.show_folder(folder, "album")  # a second time: from the cache
        assert view.evidence is not None and calls == [3]
    finally:
        LocalEvidenceView.loader = None
        view_module.clear_cache()


def test_no_cover_says_so_and_an_artist_has_captions(tmp_path):
    view_module.clear_cache()
    cyrillic_album(tmp_path, "none")
    view = LocalEvidenceView()
    view.show_folder(tmp_path / BAND / f"{ALBUM} (2000)", "album")
    wait_for(view)
    assert view.thumb_labels[0].text() == "no cover" and view.thumb_labels[0].property("has_cover") is False
    view.show_folder(tmp_path / BAND, "artist")
    import time
    end = time.perf_counter() + 10
    while view.evidence is None or view.evidence.kind != "artist":
        QApplication.processEvents()
        assert time.perf_counter() < end
        time.sleep(0.01)
    assert [c.text() for c in view.caption_labels] == [ALBUM]
    view_module.clear_cache()


def test_a_slow_answer_for_a_row_left_behind_is_not_shown(tmp_path):
    view_module.clear_cache()
    cyrillic_album(tmp_path, "none")
    view = LocalEvidenceView()
    view.show_folder(tmp_path / BAND / f"{ALBUM} (2000)", "album")
    view.clear()  # another row (or none) was selected
    import time
    time.sleep(0.5)
    QApplication.processEvents()
    assert view.evidence is None and view.text.toPlainText() == ""
    view_module.clear_cache()


# -- the real review dialog ------------------------------------------------------------------------------------------------

def empty_fetchers():
    fake = FakeFetch([(r"ws/2/artist\?query", {"artists": []}), (r"ws/2/release-group\?query", {"release-groups": []}),
                      (r"wikidata\.org", {"entities": {}}), (r"search/title", {"pages": []}), (r"page/summary/", None)])
    return Fetchers(mb=fake, wiki=fake)


def run_dialog(window, root, checks):
    view_module.clear_cache()
    window.settings.last_directory = str(root)
    window.settings.include_subfolders = True
    window._load_paths([root / BAND])
    window.notes_fetchers = empty_fetchers
    seen = {}

    def scenario():
        yield from wait(lambda: scope_dialog() is not None, "the scope dialog")
        dlg = scope_dialog()
        set_kinds(dlg, "artist,album")
        next(r for r, p in dlg.radios if p == str(root)).click()
        click(dlg.continue_button)
        yield from wait(lambda: review() is not None, "the review page")
        page = review()
        yield from checks(page, seen)
        page.done(0)

    drive(scenario, window.action_registry["create_notes"].trigger)
    return seen


def select(page, name, kind):
    tree = page.tree
    index = next(i for i, it in enumerate(page.session.items)
                 if it.proposal.folder.name == name and it.proposal.kind == kind)
    for r in range(tree.topLevelItemCount()):
        row = tree.topLevelItem(r)
        if row.data(0, Qt.ItemDataRole.UserRole) == index:
            tree.setCurrentItem(row)
            return index
    raise AssertionError(f"{name} is not in the list")


def test_the_list_view_shows_the_cover_and_the_cyrillic_tracks_of_an_unidentified_album(window, tmp_path, boxes):
    root = tmp_path / "Music"
    cyrillic_album(root, "embedded")

    def checks(page, seen):
        select(page, f"{ALBUM} (2000)", "album")
        panel = page.list_panel
        seen["tab"] = (panel.tabs.currentIndex() == panel.evidence_tab, panel.tabs.isTabVisible(panel.evidence_tab))
        yield from wait(lambda: panel.evidence_view.evidence is not None, "the local files")
        view = panel.evidence_view
        grab = os.environ.get("NOTES_EVIDENCE_GRAB")  # a real-display run saves the page to look at
        if grab:
            QApplication.processEvents()
            page.grab().save(grab)
        seen["text"] = view.text.toPlainText()
        seen["cover"] = view.thumb_labels[0].property("has_cover")
        # the one-at-a-time view shows the same tab
        click(page.switch_button)
        QApplication.processEvents()
        if grab:
            page.grab().save(grab.replace(".png", "-2.png"))
        one = page.one_panel
        seen["one_tab"] = (one.tabs.isTabVisible(one.evidence_tab), one.tabs.currentIndex() == one.evidence_tab)
        yield from wait(lambda: one.evidence_view.evidence is not None, "the local files in the one-at-a-time view")
        seen["one_text"] = one.evidence_view.text.toPlainText()

    seen = run_dialog(window, root, checks)
    assert seen["tab"] == (True, True) and seen["cover"] is True
    assert "Мне хочется кричать" in seen["text"] and "Вояж" in seen["text"] and f"artist '{BAND}'" in seen["text"]
    assert seen["one_tab"] == (True, True) and "Дачники (2000): 3 track(s)" in seen["one_text"]  # (the one-at-a-time view starts at the first row: the artist)


def test_an_artist_row_shows_its_albums_and_a_folder_image_cover(window, tmp_path, boxes):
    root = tmp_path / "Music"
    cyrillic_album(root, "folder")
    make_mp3(root / BAND / "Бикини (2004)" / "01 a.mp3", "Бикини", BAND, "Бикини", 1, "2004")

    def checks(page, seen):
        select(page, BAND, "artist")
        view = page.list_panel.evidence_view
        yield from wait(lambda: view.evidence is not None, "the local files")
        seen["text"] = view.text.toPlainText()
        seen["covers"] = [l.property("has_cover") for l in view.thumb_labels]
        seen["captions"] = [c.text() for c in view.caption_labels]

    seen = run_dialog(window, root, checks)
    assert "Бикини (2004): 1 track(s)" in seen["text"] and "Дачники (2000): 3 track(s)" in seen["text"]
    assert sorted(seen["covers"]) == [False, True] and sorted(seen["captions"]) == ["Бикини", "Дачники"]


def test_a_seventy_track_album_lists_sixty_in_the_dialog(window, tmp_path, boxes):
    root = tmp_path / "Music"
    cyrillic_album(root, "none", count=70)

    def checks(page, seen):
        select(page, f"{ALBUM} (2000)", "album")
        view = page.list_panel.evidence_view
        yield from wait(lambda: view.evidence is not None, "the local files")
        seen["lines"] = view.text.toPlainText().splitlines()

    seen = run_dialog(window, root, checks)
    assert seen["lines"][-1] == "... and 10 more" and len(seen["lines"]) == 3 + 60 + 1


def test_a_genre_row_has_no_local_files_tab(window, tmp_path, boxes):
    root = tmp_path / "Music"
    cyrillic_album(root, "none")
    view_module.clear_cache()
    from gui.notes_dialog import ProposalPanel

    panel = ProposalPanel(can_choose=False)
    assert not panel.tabs.isTabVisible(panel.evidence_tab)
