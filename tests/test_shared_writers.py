"""Tags, ComicInfo.xml and zips are written only through redactor_common's shared writers (core/audio_write.py,
core/comicinfo_write.py, core/zip_rewrite.py; merged on 2026-11-04 from the apps' and Dossier's own). A mutagen save
anywhere else could quietly turn an ID3 v2.3 tag into v2.4 again: this test names the line that writes on its own."""
import os

from redactor_common.core.writer_guard import find_private_writers


def test_nothing_in_the_app_writes_outside_the_shared_writers():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    assert find_private_writers(root) == []
