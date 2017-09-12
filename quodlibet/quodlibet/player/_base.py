# -*- coding: utf-8 -*-
# Copyright 2007-2008 Joe Wreschnig
#           2009,2010 Steven Robertson
#           2009-2013 Christoph Reiter
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License version 2 as
# published by the Free Software Foundation

from gi.repository import GObject

from quodlibet.formats import AudioFile
from quodlibet.util import print_d
from quodlibet import config
from quodlibet.compat import listfilter


class Equalizer(object):
    _eq_values = []

    @property
    def eq_bands(self):
        """read-only list of equalizer bands (in Hz) supported."""

        return []

    @property
    def eq_values(self):
        """The list of equalizer values, in the range (-24dB, 12dB)."""

        return self._eq_values

    @eq_values.setter
    def eq_values(self, value):
        self._eq_values[:] = value
        self.update_eq_values()

    def update_eq_values(self):
        """Override to apply equalizer values"""

        pass


class BasePlayer(GObject.GObject, Equalizer):
    """Interfaces between a QL PlaylistModel and a GSt playbin.

    Attributes:
    paused -- True or False, set to pause/unpause the player
    volume -- current volume, 0.0 to 1.0

    song -- current song, or None if not playing
    info -- current stream information, or None if not playing. This is
            usually the same as song, unless the user is listening to
            a stream with multiple songs in it.

    If you're going to show things, use .info. If you're going to
    change things, use .song.
    """

    name = ""
    version_info = ""

    paused = None
    song = None
    info = None
    volume = None

    # if the current song couldn't be played because of an error
    # gets reset automatically after 'song-ended' gets emitted
    error = False

    # Replay Gain profiles are a list of values to be tried in order;
    # Four things can set them: rg menu, browser, play order, and a default.
    replaygain_profiles = [None, None, None, ["none"]]
    _paused = True
    _source = None

    __gsignals__ = {
        'song-started':
        (GObject.SignalFlags.RUN_LAST, None, (object,)),
        'song-ended':
        (GObject.SignalFlags.RUN_LAST, None, (object, bool)),
        'seek':
        (GObject.SignalFlags.RUN_LAST, None, (object, int)),
        'paused': (GObject.SignalFlags.RUN_LAST, None, ()),
        'unpaused': (GObject.SignalFlags.RUN_LAST, None, ()),
        # (song, PlayerError)
        'error': (GObject.SignalFlags.RUN_LAST, None, (object, object)),
        'cue-change': (GObject.SignalFlags.RUN_LAST, None, ()),
    }

    __gproperties__ = {
        'volume': (float, 'player volume', 'the volume of the player',
                   0.0, 1.0, 1.0,
                   GObject.ParamFlags.READABLE | GObject.ParamFlags.WRITABLE),
        'seekable': (bool, 'seekable', 'if the stream is seekable', True,
                     GObject.ParamFlags.READABLE),
        'mute': (bool, 'mute', 'if the stream is muted', False,
                 GObject.ParamFlags.READABLE | GObject.ParamFlags.WRITABLE),
    }

    def __init__(self, *args, **kwargs):
        super(BasePlayer, self).__init__()

    def destroy(self):
        """Free resources"""

        if self.song is not self.info:
            self.emit("song-ended", self.info, True)
        self.emit("song-ended", self.song, True)
        self._source = None

        self._destroy()

    def calc_replaygain_volume(self, volume):
        """Returns a new float volume for the given volume.

        Takes into account the global active replaygain profile list,
        the user specified replaygain settings and the tags available
        for that song.

        Args:
            volume (float): 0.0..1.0
        Returns:
            float: adjusted volume, can be outside of 0.0..0.1
        """

        if self.song and config.getboolean("player", "replaygain"):
            profiles = listfilter(None, self.replaygain_profiles)[0]
            fb_gain = config.getfloat("player", "fallback_gain")
            pa_gain = config.getfloat("player", "pre_amp_gain")
            scale = self.song.replay_gain(profiles, pa_gain, fb_gain)
        else:
            scale = 1
        return volume * scale

    def _reset_replaygain(self):
        self.volume = self.volume

    def reset_replaygain(self):
        """Call in case something affecting the replaygain adjustment has
        changed to change the output volume
        """

        self._reset_replaygain()

    @property
    def has_external_volume(self):
        """If setting the volume will affect anything outside of QL and
        if the volume can change without any event in QL.
        """

        return False

    @property
    def volume(self):
        return self.props.volume

    @volume.setter
    def volume(self, v):
        self.props.volume = min(1.0, max(0.0, v))

    @property
    def mute(self):
        return self.props.mute

    @mute.setter
    def mute(self, v):
        self.props.mute = v

    def write_cue(self, which, position=None):
        if not self.song:
            return
        if position is None:
            position = self.get_position()
        tag = 'qldj_cue_%s' % which

        def done():
            self.emit('cue-change')
            if self.song.is_writable():
                self.song.write()

        if not position and tag in self.song:
            del self.song[tag]
            done()
            return

        if position and self.song.get(tag, None) != position:
            self.song[tag] = position
            done()
            return

    @property
    def seekable(self):
        """If the current song can be seeked, in case it's not clear defaults
        to True. See the "seekable" GObject property for notifications.
        """

        return self.props.seekable

    def _destroy(self):
        """Clean up"""

        raise NotImplementedError

    def _end(self, stopped, next_song=None):
        """Start playing the current song from the source or
        next_song if it isn't None.
        """

        raise NotImplementedError

    def setup(self, source, song, seek_pos):
        """Connect to a PlaylistModel, and load a song.

        seek_pos in millisecs
        """

        assert source is not None
        if self._source is None:
            self.emit("song-started", song)
        self._source = source
        self.go_to(song, explicit=True)
        if seek_pos:
            self.seek(seek_pos)

    def seek(self, position):
        """Seek to absolute position in milliseconds.
        If position is larger than the duration start the next song
        """

        raise NotImplementedError

    def get_position(self):
        """The current position in milliseconds"""

        raise NotImplementedError

    def remove(self, song):
        """Make sure the song isn't played anymore"""

        if song and self.song is song:
            self._source.next()
            self._end(True)

    def stop(self):
        """Stop playback and reset the position.
        Might release the audio device
        """

        self.paused = True
        self.seek(0)

    def reset(self):
        """Reset the source and start playing if possible"""

        self._source.reset()
        if self._source.current is not None:
            self._end(True)
            if self.song:
                self.paused = False

    def next(self):
        """Move to the next song"""

        self._source.next()
        self._end(True)
        if self.song:
            self.paused = False

    def previous(self, force=False):
        """Go back if standing at the beginning of the song
        otherwise restart the current song.

        If force is True always go back.
        """

        if force or self.get_position() < 1500 or not self.seekable:
            self._source.previous()
            self._end(True)
        else:
            self.seek(0)
        if self.song:
            self.paused = False

    def go_to(self, song_or_iter, explicit=False, source=None):
        """Activate the song or iter in the playlist if possible and play it.

        Explicit if the action comes from the user.

        Returns True if there is an active song after the call returns.
        """

        print_d("Going to %r" % getattr(song_or_iter, "key", song_or_iter))

        if self._source.go_to(song_or_iter, explicit, source):
            self._end(True)
        else:
            if isinstance(song_or_iter, AudioFile):
                self._end(True, song_or_iter)
            else:
                # FIXME: this is for the queue only plugin. the play order
                # should return if it has handled set() itself instead
                if explicit:
                    return
                self._end(True)

        return self.song is not None

    def can_play_uri(self, uri):
        """Whether the player supports playing te given URI scheme"""

        raise NotImplementedError
