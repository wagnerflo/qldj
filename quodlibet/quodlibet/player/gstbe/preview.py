import gi
try:
    gi.require_version("Gst", "1.0")
    gi.require_version("GstPbutils", "1.0")
except ValueError as e:
    raise ImportError(e)

from gi.repository import Gst, GLib, GstPbutils
from quodlibet.qltk.songmodel import PlaylistModel

from .player import GStreamerPlayer

class GStreamerPreviewPlayer(GStreamerPlayer):
    def __init__(self, librarian=None):
        GStreamerPlayer.__init__(self, librarian=librarian)
        self._config_name = "gst_pipeline_preview"
        self.setup(PlaylistModel(), None, 0)

    def _get_plugin_elements(self):
        return []

    def preview(self, song):
        if song is None:
            self.paused = True
        elif song == self.song:
            self.paused = not self.paused
        else:
            self._source.set([song])
            self.next()

    def jump(self, sec):
        self.seek(max(0, self.get_position() + sec * 1000))

class MockLibrarian(object):
    def connect(self, *args, **kwargs):     pass
    def disconnect(self, *args, **kwargs):  pass
    def changed(self, *args, **kwargs):     pass

def init_preview():
    # Enable error messages by default
    if Gst.debug_get_default_threshold() == Gst.DebugLevel.NONE:
        Gst.debug_set_default_threshold(Gst.DebugLevel.ERROR)

    return GStreamerPreviewPlayer(MockLibrarian())
