# -*- coding: utf-8 -*-
# Copyright 2016 0x1777
#           2016 Nick Boultbee
#           2017 Didier Villevalois
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License version 2 as
# published by the Free Software Foundation

from gi.repository import Gtk, Gdk, Gst
import cairo
from math import ceil, floor

from quodlibet import _, app
from quodlibet import print_w
from quodlibet.plugins import PluginConfig, IntConfProp, \
    ConfProp
from quodlibet.plugins.events import EventPlugin
from quodlibet.qltk import Align
from quodlibet.qltk import Icons
from quodlibet.qltk.seekbutton import TimeLabel
from quodlibet.qltk.tracker import TimeTracker
from quodlibet.qltk import get_fg_highlight_color
from quodlibet.util import connect_destroy, print_d


class WaveformSeekBar(Gtk.Box):
    """A widget containing labels and the seekbar."""

    def __init__(self, player, library):
        super(WaveformSeekBar, self).__init__()

        self._player = player
        self._rms_vals = []

        self._elapsed_label = TimeLabel()
        self._remaining_label = TimeLabel()
        self._waveform_scale = WaveformScale(player)

        self.pack_start(Align(self._elapsed_label, border=6), False, True, 0)
        self.pack_start(self._waveform_scale, True, True, 0)
        self.pack_start(Align(self._remaining_label, border=6), False, True, 0)

        for child in self.get_children():
            child.show_all()

        self._waveform_scale.connect('size-allocate',
                                     self._update_redraw_interval)

        self._label_tracker = TimeTracker(player)
        self._label_tracker.connect('tick', self._on_tick_label, player)

        self._redraw_tracker = TimeTracker(player)
        self._redraw_tracker.connect('tick', self._on_tick_waveform, player)

        connect_destroy(player, 'seek', self._on_player_seek)
        connect_destroy(player, 'song-started', self._on_song_started)
        connect_destroy(player, 'song-ended', self._on_song_ended)
        connect_destroy(player, 'notify::seekable', self._on_seekable_changed)
        connect_destroy(player, 'cue-change', self._on_cue_change)
        connect_destroy(library, 'changed', self._on_song_changed, player)

        self.connect('destroy', self._on_destroy)
        self._update(player)

        if player.info:
            self._create_waveform(player.info, CONFIG.max_data_points)

    def _create_waveform(self, song, points):
        # Close any existing pipeline to avoid leaks
        self._clean_pipeline()

        command_template = """
        filesrc name=fs
        ! decodebin ! audioconvert
        ! level name=audiolevel interval={} post-messages=true
        ! fakesink sync=false"""
        interval = int(song("~#length") * 1E9 / points)
        print_d("Computing data for each %.3f seconds" % (interval / 1E9))

        command = command_template.format(interval)
        pipeline = Gst.parse_launch(command)
        pipeline.get_by_name("fs").set_property("location", song("~filename"))

        bus = pipeline.get_bus()
        self._bus_id = bus.connect("message", self._on_bus_message)
        bus.add_signal_watch()

        pipeline.set_state(Gst.State.PLAYING)

        self._pipeline = pipeline
        self._new_rms_vals = []

    def _on_bus_message(self, bus, message):
        if message.type == Gst.MessageType.ERROR:
            error, debug = message.parse_error()
            print_d("Error received from element {name}: {error}".format(
                name=message.src.get_name(), error=error))
            print_d("Debugging information: {}".format(debug))
        elif message.type == Gst.MessageType.ELEMENT:
            structure = message.get_structure()
            if structure.get_name() == "level":
                rms_db = structure.get_value("rms")
                # Calculate average of all channels (usually 2)
                rms_db_avg = sum(rms_db) / len(rms_db)
                # Normalize dB value to value between 0 and 1
                rms = pow(10, (rms_db_avg / 20))
                self._new_rms_vals.append(rms)
            else:
                print_w("Got unexpected message of type {}"
                        .format(message.type))
        elif message.type == Gst.MessageType.EOS:
            self._clean_pipeline()

            # Update the waveform with the new data
            self._rms_vals = self._new_rms_vals
            self._waveform_scale.reset(self._rms_vals)
            self._waveform_scale.set_placeholder(False)
            self._update_redraw_interval()

            # Clear temporary reference to the waveform data
            del self._new_rms_vals

    def _clean_pipeline(self):
        if hasattr(self, "_pipeline") and self._pipeline:
            self._pipeline.set_state(Gst.State.NULL)
            if self._bus_id:
                bus = self._pipeline.get_bus()
                bus.remove_signal_watch()
                bus.disconnect(self._bus_id)
                self._bus_id = None
            if self._pipeline:
                self._pipeline = None

    def _update_redraw_interval(self, *args):
        if self._player.info and self.is_visible():
            # Must be recomputed when size is changed
            interval = self._waveform_scale.compute_redraw_interval()
            self._redraw_tracker.set_interval(interval)

    def _on_destroy(self, *args):
        self._clean_pipeline()
        self._label_tracker.destroy()
        self._redraw_tracker.destroy()

    def _on_tick_label(self, tracker, player):
        self._update_label(player)

    def _on_tick_waveform(self, tracker, player):
        self._update_waveform(player)

    def _on_seekable_changed(self, player, *args):
        self._update_label(player)

    def _on_player_seek(self, player, song, ms):
        self._update(player)

    def _on_song_changed(self, library, songs, player):
        # Check that the currently playing song has changed
        if player.info and player.info in songs:
            # Trigger a re-computation of the waveform
            self._create_waveform(player.info, CONFIG.max_data_points)
            # Only update the label if some tag value changed
            self._update_label(player)

    def _on_song_started(self, player, song):
        if player.info:
            # Trigger a re-computation of the waveform
            self._create_waveform(player.info, CONFIG.max_data_points)

        self._waveform_scale.set_placeholder(True)
        self._update(player, True)

    def _on_song_ended(self, player, song, ended):
        self._update(player)

    def _on_cue_change(self, player):
        self._update_waveform(player, True)

    def _update(self, player, full_redraw=False):
        self._update_label(player)
        self._update_waveform(player, full_redraw)

    def _update_label(self, player):
        if player.info:
            # Position in ms, length in seconds
            position = player.get_position() / 1000.0
            length = player.info("~#length")
            remaining = length - position

            self._elapsed_label.set_time(position)
            self._remaining_label.set_time(remaining)

            self._elapsed_label.set_disabled(not player.seekable)
            self._remaining_label.set_disabled(not player.seekable)
            self.set_sensitive(player.seekable)
        else:
            self._remaining_label.set_disabled(True)
            self._elapsed_label.set_disabled(True)
            self.set_sensitive(False)

    def _update_waveform(self, player, full_redraw=False):
        if player.info:
            # Position in ms, length in seconds
            position = player.get_position() / 1000.0
            length = player.info("~#length")
            cue_in = player.info("~#cue_in") / 1000.0
            cue_out = player.info("~#cue_out", length * 1000.0) / 1000.0

            if length != 0:
                self._waveform_scale.set_position(position / length)
                self._waveform_scale.set_cue(cue_in / length, cue_out / length)
            else:
                print_d("Length reported as zero for %s" % player.info)
                self._waveform_scale.set_position(0)
                self._waveform_scale.set_cue(0, 1)

            if position == 0 or full_redraw:
                self._waveform_scale.queue_draw()
            else:
                (x, y, w, h) = self._waveform_scale.compute_redraw_area()
                self._waveform_scale.queue_draw_area(x, y, w, h)
        else:
            self._waveform_scale.set_placeholder(True)
            self._waveform_scale.queue_draw()


class WaveformScale(Gtk.EventBox):
    """The waveform widget."""

    _rms_vals = []
    _player = None
    _placeholder = True

    def __init__(self, player):
        super(WaveformScale, self).__init__()
        self._player = player
        self.set_size_request(40, 40)
        self.position = 0
        self.cue_in = 0
        self.cue_out = 1
        self._last_drawn_position = 0
        self.override_background_color(
            Gtk.StateFlags.NORMAL, Gdk.RGBA(alpha=0))

    @property
    def width(self):
        return self.get_allocation().width

    def set_placeholder(self, placeholder):
        self._placeholder = placeholder

    def reset(self, rms_vals):
        self._rms_vals = rms_vals
        self.queue_draw()

    def compute_redraw_interval(self):
        allocation = self.get_allocation()
        width = allocation.width

        scale_factor = self.get_scale_factor()
        pixel_ratio = float(scale_factor)

        # Compute the coarsest time interval for redraws
        length = self._player.info("~#length")
        return length * 1000 / max(width * pixel_ratio, 1)

    def compute_redraw_area(self):
        allocation = self.get_allocation()
        width = allocation.width
        height = allocation.height

        scale_factor = self.get_scale_factor()
        pixel_ratio = float(scale_factor)
        line_width = 1.0 / pixel_ratio

        # Compute the thinnest rectangle to redraw
        last_position_x = self._last_drawn_position * width
        position_x = self.position * width
        x = max(0.0, min(last_position_x, position_x) - line_width * 5)
        w = min(width, abs(position_x - last_position_x) + line_width * 10)
        return x, 0.0, w, height

    def draw_waveform(self, cr, width, height, elapsed_color, remaining_color):
        if width == 0 or height == 0:
            return
        scale_factor = self.get_scale_factor()
        pixel_ratio = float(scale_factor)
        line_width = 1.0 / pixel_ratio

        half_height = self.compute_half_height(height, pixel_ratio)

        value_count = len(self._rms_vals)
        max_value = max(self._rms_vals)
        ratio_width = value_count / (float(width) * pixel_ratio)
        ratio_height = max_value / half_height

        cr.set_line_width(line_width)
        cr.set_line_cap(cairo.LINE_CAP_ROUND)
        cr.set_line_join(cairo.LINE_JOIN_ROUND)

        position_width = self.position * width * pixel_ratio
        cue_in_width = self.cue_in * width * pixel_ratio
        cue_out_width = self.cue_out * width * pixel_ratio
        cue_color = Gdk.RGBA(1 - elapsed_color.red,
                             1 - elapsed_color.green,
                             1 - elapsed_color.blue)
        hw = line_width / 2.0
        # Avoiding object lookups is slightly faster
        data = self._rms_vals

        # There can't be more than one clip rectangle, due to the draws queued
        # But handle the other case anyway
        rectangle_list = cr.copy_clip_rectangle_list()
        (cx, cy, cw, ch) = rectangle_list[0]
        if len(rectangle_list) > 1:
            for i in range(1, len(rectangle_list), 1):
                (ox, oy, ow, oh) = rectangle_list[i]
                (cx, cy, cw, ch) = \
                    (min(cx, ox), min(cy, oy), max(cw, ow), max(ch, oh))

        # Use that clip rectangle to redraw only what is necessary
        for x in range(int(floor(cx * pixel_ratio)),
                       int(ceil((cx + cw) * pixel_ratio)), 1):

            fg_color = (cue_color if x < cue_in_width or x > cue_out_width else
                        elapsed_color if x < position_width
                        else remaining_color)
            cr.set_source_rgba(*list(fg_color))

            # Basic anti-aliasing / oversampling
            u1 = max(0, int(floor((x - hw) * ratio_width)))
            u2 = min(int(ceil((x + hw) * ratio_width)), len(data))
            val = (sum(data[u1:u2]) / (ratio_height * (u2 - u1))
                   if u1 != u2 else 0.0)

            hx = x / pixel_ratio + hw
            cr.move_to(hx, half_height - val)
            cr.line_to(hx, half_height + val)
            cr.stroke()

        self._last_drawn_position = self.position

    def draw_placeholder(self, cr, width, height, color):
        if width == 0 or height == 0:
            return
        scale_factor = self.get_scale_factor()
        pixel_ratio = float(scale_factor)
        line_width = 1.0 / pixel_ratio

        half_height = self.compute_half_height(height, pixel_ratio)
        hw = line_width / 2.0

        cr.set_line_width(line_width)
        cr.set_line_cap(cairo.LINE_CAP_ROUND)
        cr.set_line_join(cairo.LINE_JOIN_ROUND)
        cr.set_source_rgba(*list(color))
        cr.move_to(hw, half_height)
        cr.line_to(width - hw, half_height)
        cr.stroke()

    @staticmethod
    def compute_half_height(height, pixel_ratio):
        # Ensure half_height is in the middle of a pixel (c.f. Cairo's FAQ)
        height_px = int(height * pixel_ratio)
        half_height = \
            (height_px if height_px % 2 else height_px - 1) / pixel_ratio / 2
        return half_height

    def do_draw(self, cr):
        context = self.get_style_context()

        # Get colors
        context.save()
        context.set_state(Gtk.StateFlags.NORMAL)
        bg_color = context.get_background_color(context.get_state())
        fg_color = context.get_color(context.get_state())
        context.restore()

        elapsed_color = get_fg_highlight_color(self)

        # Check if the user set a different color in the config
        elapsed_color_config = CONFIG.elapsed_color
        if elapsed_color_config and Gdk.RGBA().parse(elapsed_color_config):
            elapsed_color = Gdk.RGBA()
            elapsed_color.parse(elapsed_color_config)

        # Paint the background
        cr.set_source_rgba(*list(bg_color))
        cr.paint()

        allocation = self.get_allocation()
        width = allocation.width
        height = allocation.height

        if not self._placeholder and len(self._rms_vals) > 0:
            self.draw_waveform(cr, width, height, elapsed_color, fg_color)
        else:
            self.draw_placeholder(cr, width, height, fg_color)

    def do_button_press_event(self, event):
        # Left mouse button
        if event.button == 1 and self._player:
            ratio = event.x / self.get_allocation().width
            length = self._player.info("~#length")
            self._player.seek(ratio * length * 1000)
            return True

    def set_position(self, position):
        self.position = position

    def set_cue(self, cue_in, cue_out):
        self.cue_in = cue_in
        self.cue_out = cue_out


class Config(object):
    _config = PluginConfig(__name__)

    elapsed_color = ConfProp(_config, "elapsed_color", "")
    max_data_points = IntConfProp(_config, "max_data_points", 3000)

CONFIG = Config()


class WaveformSeekBarPlugin(EventPlugin):
    """The plugin class."""

    PLUGIN_ID = "WaveformSeekBar"
    PLUGIN_NAME = _("Waveform Seek Bar")
    PLUGIN_ICON = Icons.GO_JUMP
    PLUGIN_CONFIG_SECTION = __name__
    PLUGIN_DESC = _(
        "A seekbar in the shape of the waveform of the current song.")

    def enabled(self):
        self._bar = WaveformSeekBar(app.player, app.librarian)
        self._bar.show()
        app.window.set_seekbar_widget(self._bar)

    def disabled(self):
        app.window.set_seekbar_widget(None)
        self._bar.destroy()
        del self._bar

    def PluginPreferences(self, parent):
        red = Gdk.RGBA()
        red.parse("#ff0000")

        def changed(entry):
            text = entry.get_text()

            if not Gdk.RGBA().parse(text):
                # Invalid color, make text red
                entry.override_color(Gtk.StateFlags.NORMAL, red)
            else:
                # Reset text color
                entry.override_color(Gtk.StateFlags.NORMAL, None)

            CONFIG.elapsed_color = text

        vbox = Gtk.VBox(spacing=6)

        def create_color():
            hbox = Gtk.HBox(spacing=6)
            hbox.set_border_width(6)
            label = Gtk.Label(label=_("Override foreground color:"))
            hbox.pack_start(label, False, True, 0)
            entry = Gtk.Entry()
            if CONFIG.elapsed_color:
                entry.set_text(CONFIG.elapsed_color)
            entry.connect('changed', changed)
            hbox.pack_start(entry, True, True, 0)
            return hbox

        vbox.pack_start(create_color(), True, True, 0)

        return vbox
