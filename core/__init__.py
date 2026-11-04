"""
The app's core package. Importing it sets up the shared lookup clients (redactor_common.core.lookups) the way this
app uses them, so every caller -- the GUI, the CLI and the tests -- gets the same User-Agent and the Discogs token
from this app's own credential entry (app "mp3redactor", name "discogs_token": permanent).
"""

from redactor_common.core.lookups import discogs as _discogs
from redactor_common.core.lookups import musicbrainz as _musicbrainz

_musicbrainz.configure("mp3redactor/1.0 ( https://github.com/Erlbon/mp3redactor )")
_discogs.configure("mp3redactor/1.0 +https://github.com/Erlbon/mp3redactor", secret_app="mp3redactor")
