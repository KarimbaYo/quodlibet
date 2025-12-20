# Copyright 2013 Christoph Reiter
#           2023 Nick Boultbee
#           2025 Yoann Guerin
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.

import operator

from gi.repository import Gtk, Pango, Gdk, GdkPixbuf, Gio, GLib
import cairo

from quodlibet import qltk, app
from quodlibet.qltk.views import AllTreeView, TreeViewColumnButton
from quodlibet.qltk.songsmenu import SongsMenu
from quodlibet.qltk.properties import SongProperties
from quodlibet.qltk.information import Information
from quodlibet.qltk.cover import get_no_cover_pixbuf
from quodlibet.qltk.image import add_border_widget, get_surface_for_pixbuf
from quodlibet.qltk import is_accel
from quodlibet.util import connect_obj, DeferredSignal, copool, connect_destroy

from .models import PaneModel, get_album_key_from_entry
from .util import PaneConfig


class Pane(AllTreeView):
    """Pane of the paned browser"""

    TARGET_INFO_QL = 1
    TARGET_INFO_URI_LIST = 2

    # PRELOAD_COUNT: how many rows should be updated
    # beyond the visible area in both directions
    PRELOAD_COUNT = 35

    def __init__(self, library, prefs, next_=None, title=None):
        super().__init__()
        self.set_fixed_height_mode(True)

        self.config = PaneConfig(prefs)
        self.__next = next_
        self.__restore_values = None

        self.__no_fill = 0

        # Cover scanning management
        self._cover_cancel = Gio.Cancellable()
        self.__pending_paths = []
        self.__update_deferred = None
        self.__first_expose = True

        column_title = title if title else self.config.title
        column = TreeViewColumnButton(title=column_title)

        def on_column_header_clicked(column, event):
            # In case the column header gets clicked select the "All" entry
            if (
                event.button != Gdk.BUTTON_PRIMARY
                or event.type != Gdk.EventType.BUTTON_PRESS
            ):
                return Gdk.EVENT_PROPAGATE
            self.set_selected([])
            return Gdk.EVENT_STOP

        column.set_clickable(True)
        column.connect("button-press-event", on_column_header_clicked)
        column.set_use_markup(True)
        column.set_sizing(Gtk.TreeViewColumnSizing.FIXED)

        fixed_width = 60
        if self.config.wants_cover:
            fixed_width = max(fixed_width, self.config.cover_size + 12)
        column.set_fixed_width(fixed_width)

        if self.config.wants_cover:
            cover_size = self.config.cover_size
            self._default_pixbuf = get_no_cover_pixbuf(cover_size, cover_size)

            if self._default_pixbuf is None:
                self._default_pixbuf = GdkPixbuf.Pixbuf.new(
                    GdkPixbuf.Colorspace.RGB, True, 8, cover_size, cover_size
                )
                self._default_pixbuf.fill(0xCCCCCCFF)

            render_icon = Gtk.CellRendererPixbuf()
            render_icon.set_property("width", cover_size + 8)
            render_icon.set_property("height", cover_size + 8)
            column.pack_start(render_icon, False)

            def icon_cdf(column, cell, model, iter_, data):
                entry = model.get_value(iter_)

                surface = self._no_cover

                # If we have a cover, process it
                if entry.cover:
                    pixbuf = entry.cover
                    pixbuf = add_border_widget(pixbuf, self)
                    surface = get_surface_for_pixbuf(self, pixbuf) or surface

                cell.set_property("surface", surface)
                cell.set_property("visible", True)

            column.set_cell_data_func(render_icon, icon_cdf)

        render = Gtk.CellRendererText()
        render.set_property("ellipsize", Pango.EllipsizeMode.END)
        render.set_property("wrap-mode", Pango.WrapMode.WORD_CHAR)

        column.pack_start(render, True)

        def text_cdf(column, cell, model, iter_, data):
            entry = model.get_value(iter_)
            markup = entry.get_markup(self.config)
            cell.set_property("markup", markup)

        column.set_cell_data_func(render, text_cdf)

        render_count = Gtk.CellRendererText()
        render_count.set_property("xalign", 1.0)
        render_count.set_property("max-width-chars", 5)
        column.pack_end(render_count, True)
        # Tiny columns break too much rendering
        column.set_min_width(150)

        def count_cdf(column, cell, model, iter_, data):
            entry = model.get_value(iter_)
            markup = entry.get_count_markup(self.config)
            cell.markup = markup
            cell.set_property("markup", markup)

        column.set_cell_data_func(render_count, count_cdf)
        self.append_column(column)

        model = PaneModel(self.config)
        self.set_model(model)

        self.set_search_equal_func(self.__search_func, None)
        self.set_search_column(0)
        self.set_enable_search(True)

        selection = self.get_selection()
        selection.set_mode(Gtk.SelectionMode.MULTIPLE)
        self.__sig = self.connect("selection-changed", self.__selection_changed)
        s = self.connect("popup-menu", self.__popup_menu, library)
        connect_obj(self, "destroy", self.disconnect, s)

        targets = [
            ("text/x-quodlibet-songs", Gtk.TargetFlags.SAME_APP, self.TARGET_INFO_QL),
            ("text/uri-list", 0, self.TARGET_INFO_URI_LIST),
        ]
        targets = [Gtk.TargetEntry.new(*t) for t in targets]

        self.drag_source_set(
            Gdk.ModifierType.BUTTON1_MASK, targets, Gdk.DragAction.COPY
        )
        self.connect("drag-data-get", self.__drag_data_get)
        self.connect("destroy", self.__destroy)

        librarian = library.librarian or library
        self.connect("key-press-event", self.__key_pressed, librarian)

        # If covers are enabled, activate the scanning logic
        if self.config.wants_cover:
            self._enable_row_update()
            if app.cover_manager:
                connect_destroy(app.cover_manager, "cover-changed", self._on_cover_changed)

    @property
    def _no_cover(self) -> cairo.Surface | None:
        """Returns a cached cairo surface representing a missing cover"""
        if not hasattr(self, "_cached_no_cover"):
            cover_size = self.config.cover_size
            scale_factor = self.get_scale_factor()
            pb = get_no_cover_pixbuf(cover_size, cover_size, scale_factor)
            if pb:
                self._cached_no_cover = get_surface_for_pixbuf(self, pb)
            else:
                self._cached_no_cover = None
        return self._cached_no_cover

    # --- VisibleUpdate Logic (Adapted from browsers/albums/main.py) ---

    def _enable_row_update(self):
        # We need to know when the view is drawn to check visibility
        connect_obj(self, "draw", self.__update_visibility, self)

        # NOTE: self is an AllTreeView which is scrollable, so it has adjustment
        adj = self.get_vadjustment()
        if adj:
            connect_destroy(adj, "value-changed", self.__stop_update, self)

        self.__pending_paths = []
        self.__update_deferred = DeferredSignal(
            self.__update_visible_rows, timeout=50, priority=GLib.PRIORITY_LOW
        )
        self.__first_expose = True

    def _disable_row_update(self):
        if self.__update_deferred:
            self.__update_deferred.abort()
            self.__update_deferred = None

        if self.__pending_paths:
            copool.remove(self.__scan_paths)

        self.__pending_paths = []

    def _row_needs_update(self, model, iter_):
        """Check if row needs scanning. Returns True if not scanned yet."""
        entry = model.get_value(iter_)
        # Only scan if not already scanned and has songs
        return not entry.scanned and entry.songs

    def _update_row(self, model, iter_):
        """Start the scan for the row."""
        entry = model.get_value(iter_)

        # Create a row reference to ensure validity during callback
        path = model.get_path(iter_)
        tref = Gtk.TreeRowReference.new(model, path)

        def callback():
            path = tref.get_path()
            if path is not None:
                # Notify view that row changed so it redraws
                model.row_changed(path, model.get_iter(path))

        scale_factor = self.get_scale_factor()
        entry.scan_cover(
            size=self.config.cover_size,
            scale_factor=scale_factor,
            callback=callback,
            cancel=self._cover_cancel
        )

    def __stop_update(self, adj, view):
        # Stop scanning when scrolling
        if self.__pending_paths:
            copool.remove(self.__scan_paths)
            self.__pending_paths = []
            self.__update_visibility(view)

    def __update_visibility(self, view, *args):
        # update all visible rows on first expose event
        if self.__first_expose:
            self.__first_expose = False
            self.__update_visible_rows(view, 0)
            for _i in self.__scan_paths():
                pass

        if self.__update_deferred:
            self.__update_deferred(view, self.PRELOAD_COUNT)

    def __scan_paths(self):
        # Worker for copool
        while self.__pending_paths:
            model, path = self.__pending_paths.pop()
            try:
                iter_ = model.get_iter(path)
            except ValueError:
                continue
            self._update_row(model, iter_)
            yield True

    def __update_visible_rows(self, view, preload):
        vrange = view.get_visible_range()
        if vrange is None:
            return

        model = view.get_model()
        start, end = vrange

        if not start or not end:
            return

        start = start.get_indices()[0] - preload - 1
        end = end.get_indices()[0] + preload

        # Prioritize center rows then outwards
        vlist = list(range(end, start, -1))
        top = vlist[: len(vlist) // 2]
        bottom = vlist[len(vlist) // 2 :]
        top.reverse()

        vlist_new = []
        for _i in vlist:
            if top:
                vlist_new.append(top.pop())
            if bottom:
                vlist_new.append(bottom.pop())
        vlist_new = filter(lambda s: s >= 0, vlist_new)
        vlist_new = map(Gtk.TreePath, vlist_new)

        visible_paths = []
        for path in vlist_new:
            try:
                iter_ = model.get_iter(path)
            except ValueError:
                continue
            if self._row_needs_update(model, iter_):
                visible_paths.append((model, path))

        if not self.__pending_paths and visible_paths:
            copool.add(self.__scan_paths)
        self.__pending_paths = visible_paths

    def _on_cover_changed(self, manager, songs):
        """Called when a cover is downloaded or changed externally."""
        model = self.get_model()
        if not model:
            return

        songs = set(songs) if songs else None

        # Iterate over model to find entries containing affected songs
        for iter_, entry in model.iterrows():
            if songs is None or (entry.songs and not entry.songs.isdisjoint(songs)):
                # Reset scanned status so it gets picked up by update visibility
                entry.scanned = False
                model.row_changed(model.get_path(iter_), iter_)

    # ----------------------------------------------------------------------

    def __key_pressed(self, view, event, librarian):
        # if ctrl+a is pressed, intercept and select the All entry instead
        if is_accel(event, "<Primary>a"):
            self.set_selected([])
            return True
        if is_accel(event, "<Primary>Return", "<Primary>KP_Enter"):
            qltk.enqueue(self.__get_selected_songs(sort=True))
            return True
        if is_accel(event, "<alt>Return"):
            songs = self.__get_selected_songs(sort=True)
            if songs:
                window = SongProperties(librarian, songs, parent=self)
                window.show()
            return True
        if is_accel(event, "<Primary>I"):
            songs = self.__get_selected_songs(sort=True)
            if songs:
                window = Information(librarian, songs, self)
                window.show()
            return True
        return False

    def __repr__(self):
        return f"<{type(self).__name__} config={self.config!r}>"

    def parse_restore_string(self, config_value):
        assert isinstance(config_value, str)

        values = config_value.split("\t")[:-1]

        try:
            if int(values[0]):
                values[0] = None
            else:
                del values[0]
        except (ValueError, IndexError):
            pass

        self.__restore_values = values

    def get_restore_string(self):
        values = self.get_selected()

        # The first value tells us if All was selected
        all_ = None in values
        if all_:
            values.remove(None)
        all_ = str(int(bool(all_)))
        values = list(values)
        values.insert(0, all_)

        # The config lib strips all whitespace,
        # so add a bogus . at the end
        values.append(".")

        return "\t".join(values)

    @property
    def tags(self):
        """Tags this pane displays"""

        return self.config.tags

    def __destroy(self, *args):
        # Cancel any pending cover scans
        self._cover_cancel.cancel()
        self._disable_row_update()

        # needed for gc
        self.__next = None

    def __search_func(self, model, column, key, iter_, data):
        entry = model.get_value(iter_)
        return not entry.contains_text(key)

    def __drag_data_get(self, view, ctx, sel, tid, etime):
        songs = self.__get_selected_songs(sort=True)

        if tid == self.TARGET_INFO_QL:
            qltk.selection_set_songs(sel, songs)
        else:
            sel.set_uris([song("~uri") for song in songs])

    def __popup_menu(self, view, library):
        songs = self.__get_selected_songs(sort=True)
        menu = SongsMenu(library, songs)
        menu.show_all()
        return view.popup_menu(menu, 0, Gtk.get_current_event_time())

    def __selection_changed(self, *args):
        if self.__next:
            self.__next.fill(self.__get_selected_songs())

    def add(self, songs):
        self.get_model().add_songs(songs)

    def remove(self, songs, remove_if_empty=True):
        self.inhibit()
        self.get_model().remove_songs(songs, remove_if_empty)
        self.uninhibit()

    def matches(self, song):
        model, paths = self.get_selection().get_selected_rows()

        if not paths:
            return True

        return model.matches(paths, song)

    def inhibit(self):
        """Inhibit selection change events and song propagation"""

        self.__no_fill += 1
        self.handler_block(self.__sig)

    def uninhibit(self):
        """Uninhibit selection change events and song propagation"""

        self.handler_unblock(self.__sig)
        self.__no_fill -= 1

    def fill(self, songs):
        # Restore the selection
        if self.__restore_values is not None:
            selected = self.__restore_values
            self.__restore_values = None
        else:
            selected = self.get_selected()

        model = self.get_model()
        # If previously all entries were selected or None: select All
        if not selected or len(model) == len(selected):
            selected = [None]

        self.inhibit()
        with self.without_model():
            model.clear()
            model.add_songs(songs)

        self.set_selected(selected, jump=True)
        self.uninhibit()

        if self.__next and self.__no_fill == 0:
            self.__next.fill(self.__get_selected_songs())

    def scroll(self, song):
        """Select and scroll to entry which contains song"""

        def select_func(row):
            entry = row[0]
            return entry.contains_song(song)

        self.select_by_func(select_func, one=True)

    def list(self, tag):
        return self.get_model().list(tag)

    def get_selected(self):
        """A list of keys for selected entries"""

        model, paths = self.get_selection().get_selected_rows()
        return model.get_keys(paths)

    def set_selected(self, values, jump=False, force_any=True):
        """Select entries with key in values

        jump -- scroll the the first selected entry
        any -- if nothing gets selected, select the first entry
        """

        if self.get_model().is_empty():
            return

        values = values or []

        # If the selection is the same, change nothing
        if values != self.get_selected():
            self.inhibit()
            self.get_selection().unselect_all()

            def select_func(row):
                entry = row[0]
                return entry.key in values

            self.select_by_func(select_func, scroll=jump)
            self.uninhibit()

            self.get_selection().emit("changed")

        if force_any and self.get_selection().count_selected_rows() == 0:
            self.set_cursor((0,))

    def set_selected_by_tag(self, tag, values, *args, **kwargs):
        """Select the entries which songs all have one of
        the values for the given tag.
        """

        pattern_values = self.get_model().get_keys_by_tag(tag, values)
        self.set_selected(pattern_values, *args, **kwargs)

    def __get_selected_songs(self, sort=False):
        model, paths = self.get_selection().get_selected_rows()
        songs = model.get_songs(paths)
        if sort:
            return sorted(songs, key=operator.attrgetter("sort_key"))
        return songs
