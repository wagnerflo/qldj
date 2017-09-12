# -*- coding: utf-8 -*-
# Copyright 2004-2013 Joe Wreschnig, Michael Urman, Iñigo Serna,
#                     Christoph Reiter, Steven Robertson
#           2011-2016 Nick Boultbee
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License version 2 as
# published by the Free Software Foundation

from __future__ import absolute_import

import os
import random

from senf import fsnative, fsn2bytes, bytes2fsn

from quodlibet import ngettext, _
from quodlibet import util
from quodlibet import config
from quodlibet.formats._audio import TAG_TO_SORT, NUMERIC_ZERO_DEFAULT
from quodlibet.formats._audio import PEOPLE as _PEOPLE
from quodlibet.compat import xrange, text_type, number_types, string_types, \
    swap_to_string, listmap
from collections import Iterable
from quodlibet.util.path import escape_filename, unescape_filename
from quodlibet.util.dprint import print_d
from quodlibet.util.misc import total_ordering, hashable
from .collections import HashedList


PEOPLE = list(_PEOPLE)
# Collections value albumartist more than song artist (Issue 1034)
PEOPLE.remove("albumartist")
PEOPLE.insert(0, "albumartist")

ELPOEP = list(reversed(PEOPLE))
PEOPLE_SCORE = [100 ** i for i in xrange(len(PEOPLE))]


def avg(nums):
    """Returns the average (arithmetic mean) of a list of numbers"""
    return float(sum(nums)) / len(nums)


def bayesian_average(nums, c=None, m=None):
    """Returns the Bayesian average of an iterable of numbers,
    with parameters defaulting to config specific to ~#rating."""
    m = m or config.RATINGS.default
    c = c or config.getfloat("settings", "bayesian_rating_factor", 0.0)
    ret = float(m * c + sum(nums)) / (c + len(nums))
    return ret

NUM_DEFAULT_FUNCS = {
    "length": "sum",
    "length:real": "sum",
    "playcount": "sum",
    "added": "max",
    "lastplayed": "max",
    "laststarted": "max",
    "mtime": "max",
    "rating": "bav",
    "skipcount": "sum",
    "year": "min",
    "originalyear": "min",
    "filesize": "sum"
}

NUM_FUNCS = {
    "max": max,
    "min": min,
    "sum": sum,
    "avg": avg,
    "bav": bayesian_average
}


class Collection(object):
    """A collection of songs which implements some methods similar to the
    AudioFile class.

    The content of the collection can be changed by changing the content of
    the songs attribute.
    """

    _cache_size = 6
    songs = ()

    def __init__(self):
        """Cache in _cache, LRU key order in _used, keys that return default
        are in _default"""
        self.__cache = {}
        self.__default = set()
        self.__used = []

    def finalize(self):
        """Finalize the collection.
        Call this after songs get added or removed"""
        self.__cache.clear()
        self.__default.clear()
        self.__used = []

    def get(self, key, default=u"", connector=u" - "):
        if not self.songs:
            return default
        if key[:1] == "~" and "~" in key[1:]:
            if not isinstance(default, string_types):
                return default
            keys = util.tagsplit(key)
            v = map(self.__get_cached_value, keys)

            def default_funct(x):
                if x is None:
                    return default
                return x
            v = map(default_funct, v)
            v = map(lambda x: (isinstance(x, float) and "%.2f" % x) or x, v)
            v = map(
                lambda x: isinstance(x, string_types) and x or text_type(x), v)
            return connector.join(filter(None, v)) or default
        else:
            value = self.__get_cached_value(key)
            if value is None:
                return default
            return value

    __call__ = get

    def comma(self, key):
        value = self.get(key)
        return (value if isinstance(value, number_types)
                else value.replace("\n", ", "))

    def list(self, key):
        v = self.get(key, connector=u"\n") if "~" in key[1:] else self.get(key)
        return [] if v == "" else v.split("\n")

    def __get_cached_value(self, key):
        if key in self.__cache:
            self.__used.remove(key)
            self.__used.insert(0, key)
            return self.__cache[key]
        elif key in self.__default:
            return None
        else:
            val = self.__get_value(key)
            if val is None:
                self.__default.add(key)
            else:
                self.__used.insert(0, key)
                self.__cache[key] = val
            # Remove the oldest if the cache is full
            if len(self.__used) > self._cache_size:
                self.__cache.pop(self.__used.pop(-1))
        return val

    def __get_value(self, key):
        """This is similar to __call__ in the AudioFile class.
        All internal tags are changed to represent a collection of songs.
        """

        # Using key:<func> runs the resulting list of values
        # through the function before returning it.
        # Numeric keys without a func will default to a reasonable function
        if key.startswith("~#"):
            key = key[2:]

            if key[-4:-3] == ":":
                func = key[-3:]
                key = key[:-4]
            elif key == "tracks":
                return len(self.songs)
            elif key == "discs":
                return len({song("~#disc", 1) for song in self.songs})
            elif key == "bitrate":
                length = self.__get_value("~#length")
                if not length:
                    return 0
                w = lambda s: s("~#bitrate", 0) * s("~#length", 0)
                return sum(w(song) for song in self.songs) / length
            else:
                # Standard or unknown numeric key.
                # AudioFile will try to cast the values to int,
                # default to avg
                func = NUM_DEFAULT_FUNCS.get(key, "avg")

            key = "~#" + key
            func = NUM_FUNCS.get(func)
            if func:
                # If none of the songs can return a numeric key,
                # the album returns default
                values = (song(key) for song in self.songs)
                values = [v for v in values if v != ""]
                return func(values) if values else None
            elif key in NUMERIC_ZERO_DEFAULT:
                return 0
            return None
        elif key[:1] == "~":
            key = key[1:]
            numkey = key.split(":")[0]
            keys = {"people": {}, "peoplesort": {}}
            if key in keys:
                people = keys["people"]
                peoplesort = keys["peoplesort"]
                for song in self.songs:
                    # Rank people by "relevance" -- artists before composers
                    # before performers, then by number of appearances.
                    for w, k in enumerate(ELPOEP):
                        persons = song.list(k)
                        for person in persons:
                            people[person] = (people.get(person, 0) -
                                              PEOPLE_SCORE[w])
                        if k in TAG_TO_SORT:
                            persons = song.list(TAG_TO_SORT[k]) or persons
                        for person in persons:
                            peoplesort[person] = (peoplesort.get(person, 0) -
                                                  PEOPLE_SCORE[w])
                # It's cheaper to get people and peoplesort in one go
                keys["people"] = sorted(people.keys(),
                                        key=people.__getitem__)[:100]
                keys["peoplesort"] = sorted(peoplesort.keys(),
                                            key=peoplesort.__getitem__)[:100]

                ret = keys.pop(key)
                ret = (ret and "\n".join(ret)) or None

                other, values = keys.popitem()
                other = "~" + other
                if not values:
                    self.__default.add(other)
                else:
                    if other in self.__used:
                        self.__used.remove(other)
                    self.__used.append(other)
                    self.__cache[other] = "\n".join(values)
                return ret
            elif numkey == "length":
                length = self.__get_value("~#" + key)
                return None if length is None else util.format_time(length)
            elif numkey == "long-length":
                length = self.__get_value("~#" + key[5:])
                return (None if length is None
                        else util.format_time_long(length))
            elif numkey == "tracks":
                tracks = self.__get_value("~#" + key)
                return (None if tracks is None else
                        ngettext("%d track", "%d tracks", tracks) % tracks)
            elif numkey == "discs":
                discs = self.__get_value("~#" + key)
                if discs > 1:
                    return ngettext("%d disc", "%d discs", discs) % discs
                else:
                    # TODO: check this is correct for discs == 1
                    return None
            elif numkey == "rating":
                rating = self.__get_value("~#" + key)
                if rating is None:
                    return None
                return util.format_rating(rating)
            elif numkey == "filesize":
                size = self.__get_value("~#" + key)
                return None if size is None else util.format_size(size)
            key = "~" + key

        # Nothing special was found, so just take all values of the songs
        # and sort them by their number of appearance
        result = {}
        for song in self.songs:
            for value in song.list(key):
                result[value] = result.get(value, 0) - 1

        values = listmap(lambda x: x[0],
                     sorted(result.items(), key=lambda x: (x[1], x[0])))
        return "\n".join(values) if values else None


class Album(Collection):
    """Like a `Collection` but adds cover scanning, some attributes for sorting
    and uses a set for the songs."""

    @util.cached_property
    def peoplesort(self):
        return util.human_sort_key(self.get("~peoplesort").split("\n")[0])

    @util.cached_property
    def genre(self):
        return util.human_sort_key(self.get("genre").split("\n")[0])

    @property
    def date(self):
        return self.get("date")

    @property
    def title(self):
        return self.get("album")

    def __init__(self, song):
        super(Album, self).__init__()
        self.songs = set()
        # albumsort is part of the album_key, so every song has the same
        self.sort = util.human_sort_key(song("albumsort"))
        self.key = song.album_key

    @property
    def str_key(self):
        return str(self.key)

    def finalize(self):
        """Finalize this album. Call after songs get added or removed"""
        super(Album, self).finalize()
        self.__dict__.pop("peoplesort", None)
        self.__dict__.pop("genre", None)

    def __repr__(self):
        return "Album(%s)" % repr(self.key)


@hashable
@swap_to_string
@total_ordering
class Playlist(Collection, Iterable):
    """A Playlist is a `Collection` that has list-like features
    Songs can appear more than once.
    """

    __instances = []

    @classmethod
    def playlists_featuring(cls, song):
        """Returns the list of playlists in which this song appears"""

        playlists = []
        for instance in cls.__instances:
            if song in instance._list:
                playlists.append(instance)
        return playlists

    def get(self, key, default=u"", connector=u" - "):
        if key == "~name":
            return self.name
        return super(Playlist, self).get(key, default, connector)

    __call__ = get

    # List-like methods, for compatibilty with original Playlist class.
    def extend(self, songs):
        self._list.extend(songs)
        self.finalize()
        self._emit_changed(songs, msg="extend")

    def append(self, song):
        ret = self._list.append(song)
        self._emit_changed([song], msg="append")
        self.finalize()
        return ret

    def clear(self):
        self._emit_changed(self._list, msg="clear")
        del self._list[:]
        self.finalize()

    def __iter__(self):
        return iter(self._list)

    def __len__(self):
        return len(self._list)

    def __getitem__(self, index):
        return self._list[index]

    def index(self, value):
        return self._list.index(value)

    def __setitem__(self, key, value):
        self._list[key] = value
        self.finalize()

    @property
    def songs(self):
        return [s for s in self._list if not isinstance(s, string_types)]

    def __init__(self, name, library=None):
        super(Playlist, self).__init__()
        self.__inhibit_library_signals = False
        self.__instances.append(self)

        name = text_type(name)
        if not name:
            raise ValueError("Playlists must have a name")

        # we require a file library here with masking
        assert library is None or hasattr(library, "masked")

        self.name = name
        self.library = library
        self._list = HashedList()

    @classmethod
    def suggested_name_for(cls, songs):
        if len(songs) == 1:
            title = songs[0].comma("title")
        else:
            title = ngettext(
                    "%(title)s and %(count)d more",
                    "%(title)s and %(count)d more",
                    len(songs) - 1) % (
                        {'title': songs[0].comma("title"),
                         'count': len(songs) - 1})
        return title

    def rename(self, new_name):
        """Changes this playlist's name and re-saves, or raises an `ValueError`
        if the name is not allowed"""
        if new_name == self.name:
            return
        self.name = self._validated_name(new_name)
        self.write()

    def _validated_name(self, new_name):
        """Returns a transformed (or not) name, or raises a `ValueError`
        if the name is not allowed
        """

        new_name = text_type(new_name)
        if not new_name:
            raise ValueError(_("Playlists must have a name"))
        return new_name

    def add_songs(self, filenames, library):
        changed = []
        for i in range(len(self)):
            if isinstance(self[i], string_types) \
                    and self._list[i] in filenames:
                song = library[self._list[i]]
                self._list[i] = song
                changed.append(song)
        if changed:
            self._emit_changed(changed, msg="add")
        return bool(changed)

    def remove_songs(self, songs, leave_dupes=False):
        """Removes `songs` from this playlist if they are there,
         removing only the first reference if `leave_dupes` is True
        """
        print_d("Remove %d song(s) from %s?" % (len(songs), self.name))
        changed = False
        for song in songs:
            # TODO: document the "library.masked" business
            if self.library is not None and self.library.masked(song):
                while True:
                    try:
                        self._list[self.index(song)] = song("~filename")
                    except ValueError:
                        break
                    else:
                        changed = True
            else:
                while song in self._list:
                    self._list.remove(song)
                    changed = True
                    if leave_dupes:
                        break

        def songs_gone():
            return set(songs) - set(self._list)

        if changed:
            self.finalize()
            # Short-circuit logic will avoid the calculation
            if not leave_dupes or songs_gone():
                self._emit_changed(songs, "remove_songs")
        return changed

    @property
    def inhibit(self):
        return self.__inhibit_library_signals

    @inhibit.setter
    def inhibit(self, value):
        self.__inhibit_library_signals = value

    def _emit_changed(self, songs, msg=""):
        if self.library and not self.inhibit and songs:
            print_d("Emitting changed (%s) for %d song(s) from playlist %s "
                    % (msg, len(songs), self))
            self.library.emit('changed', songs)

    def has_songs(self, songs):
        # TODO(rm): consider the "library.masked" business
        some, all = False, True
        for song in songs:
            found = song in self._list
            some = some or found
            all = all and found
            if some and not all:
                break
        return some, all

    def delete(self):
        self.clear()
        if self in self.__instances:
            self.__instances.remove(self)

    def write(self):
        pass

    @property
    def has_duplicates(self):
        """Returns True if there are any duplicated files in this playlist"""
        return self._list.has_duplicates()

    def shuffle(self):
        """Randomly shuffles this playlist, without weighting"""
        random.shuffle(self._list)
        self.write()

    def __eq__(self, other):
        try:
            return self.name == other.name
        except AttributeError:
            return False

    def __lt__(self, other):
        try:
            return self.name < other.name
        except AttributeError:
            return False

    def __hash__(self):
        return id(self)

    def __str__(self):
        songs_text = (ngettext("%d song", "%d songs", len(self.songs))
                      % len(self.songs))
        return u"\"%s\" (%s)" % (self.name, songs_text)


class FileBackedPlaylist(Playlist):
    """A `Playlist` that is stored as a file on disk"""

    quote = staticmethod(escape_filename)
    unquote = staticmethod(unescape_filename)

    def __init__(self, dir, name, library=None, validate=False):
        assert isinstance(dir, fsnative)
        super(FileBackedPlaylist, self).__init__(name, library)

        self.dir = dir
        if validate:
            self.name = self._validated_name(name)
        self._last_fn = self.filename
        self.__populate_from_file()

    def __populate_from_file(self):
        library = self.library
        try:
            with open(self.filename, "rb") as h:
                for line in h:
                    assert library is not None
                    try:
                        line = bytes2fsn(line.rstrip(), "utf-8")
                    except ValueError:
                        # decoding failed
                        continue
                    if line in library:
                        self._list.append(library[line])
                    elif library and library.masked(line):
                        self._list.append(line)
        except IOError:
            if self.name:
                util.print_d(
                    "Playlist '%s' not found, creating new." % self.name)
                self.write()

    @classmethod
    def new(cls, dir_, base=_("New Playlist"), library=None):
        assert isinstance(dir_, fsnative)

        if not (dir_ and os.path.realpath(dir_)):
            raise ValueError("Invalid playlist directory %r" % (dir_,))

        for i in range(1000):
            try:
                name = "%s %d" % (base, i) if i else base
                return FileBackedPlaylist(dir_, name, library, validate=True)
            except ValueError:
                pass
        raise ValueError("Couldn't create playlist of name '%s'" % base)

    @classmethod
    def from_songs(cls, dir_, songs, library=None):
        assert isinstance(dir_, fsnative)
        title = cls.suggested_name_for(songs)
        playlist = cls.new(dir_, title, library)
        playlist.extend(songs)
        return playlist

    @property
    def filename(self):
        basename = self.quote(self.name)
        return os.path.join(self.dir, basename)

    def _validated_name(self, new_name):
        new_name = super(FileBackedPlaylist, self)._validated_name(new_name)
        basename = self.quote(new_name)
        path = os.path.join(self.dir, basename)
        if os.path.exists(path):
            raise ValueError(
                    _("A playlist named %s already exists.") % new_name)
        return new_name

    def delete(self):
        super(FileBackedPlaylist, self).delete()
        self.__delete_file(self.filename)

    @classmethod
    def __delete_file(cls, fn):
        try:
            os.unlink(fn)
        except EnvironmentError:
            pass

    def write(self):
        fn = self.filename
        with open(fn, "wb") as f:
            for song in self._list:
                if isinstance(song, string_types):
                    f.write(fsn2bytes(song, "utf-8") + b"\n")
                else:
                    f.write(fsn2bytes(song("~filename"), "utf-8") + b"\n")
        if self._last_fn != fn:
            self.__delete_file(self._last_fn)
            self._last_fn = fn
