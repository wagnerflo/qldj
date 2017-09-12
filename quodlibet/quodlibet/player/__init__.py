# -*- coding: utf-8 -*-
# Copyright 2007 Joe Wreschnig
#           2013 Christoph Reiter
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License version 2 as
# published by the Free Software Foundation

import importlib

from quodlibet import util
from quodlibet.compat import swap_to_string, text_type


@swap_to_string
class PlayerError(Exception):
    """Error raised by player loading/initialization and emitted by the
    error signal during playback.

    Both short_desc and long_desc are meant for displaying in the UI.
    They should be unicode.
    """

    def __init__(self, short_desc, long_desc=None):
        self.short_desc = short_desc
        self.long_desc = long_desc

    def __str__(self):
        return self.short_desc + (
            u"\n" + self.long_desc if self.long_desc else u"")

    def __bytes__(self):
        return text_type(self).encode('utf-8')

    def __repr__(self):
        return "%s(%r, %r)" % (
            type(self).__name__, repr(self.short_desc), repr(self.long_desc))


def init_player(backend_name, librarian):
    """Loads the specified backend and initializes it.

    Returns a BasePlayer implementation instance.

    Raises PlayerError in case of an error.
    """

    backend = init_backend(backend_name)
    return backend.init(librarian)


def init_preview(backend_name):
    backend = init_backend(backend_name)
    return backend.init_preview()


def init_backend(backend_name):
    """Imports the player backend module for the given name.
    Raises PlayerError if the import fails.

    the module provides the following functions:
        init(librarian) -> new player instance
    """

    modulename = "quodlibet.player." + backend_name

    try:
        backend = importlib.import_module(modulename)
    except Exception as e:
        util.print_exc()
        util.reraise(PlayerError, str(e))
    else:
        return backend
