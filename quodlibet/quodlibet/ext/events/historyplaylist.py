import time
import os.path

from quodlibet import _
from quodlibet.qltk import Icons
from quodlibet.util.collection import FileBackedPlaylist
from quodlibet.browsers.playlists.util import PLAYLISTS
from quodlibet.browsers.playlists import PlaylistsBrowser
from quodlibet.plugins.events import EventPlugin

class HistoryPlaylist(EventPlugin):
    PLUGIN_ID = "HistoryPlaylist"
    PLUGIN_NAME = _("History Playlist")
    PLUGIN_DESC = _("Automatically generates a playlist with all played songs.")
    PLUGIN_ICON = Icons.FORMAT_JUSTIFY_FILL

    __started = None
    __lastplayed = None
    __ended = None
    __playlist = None

    def plugin_on_song_started(self, song):
        if song is None:
            return

        self.__started = song
        self.__lastplayed = song("~#lastplayed")

    def plugin_on_song_ended(self, song, stopped):
        if song is None:
            return

        self.__ended = song

    def plugin_on_changed(self, songs):
        if self.__started is None or self.__ended is None or self.__started != self.__ended:
            return

        for song in songs:
            if song == self.__started:
                if song("~#lastplayed") != self.__lastplayed:
                    self._add_to_playlist(song)
                break

    def _add_to_playlist(self, song):
        if self.__playlist is None:
            for i in range(1, 1000):
                try:
                    self.__playlist = FileBackedPlaylist(
                        PLAYLISTS,
                        time.strftime("History / %Y-%m-%d / %%i", time.localtime()) % i,
                        validate=True
                    )
                    break

                except ValueError:
                    pass

        self.__playlist.append(song)
        PlaylistsBrowser.changed(self.__playlist)
