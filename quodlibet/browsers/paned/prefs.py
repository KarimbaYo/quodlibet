# Copyright 2013 Christoph Reiter
#           2015 Nick Boultbee
#           2017 Fredrik Strupe
#           2025 Yoann Guerin
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.

from gi.repository import Gtk, Gdk, Pango, GObject

from quodlibet import config
from quodlibet import util
from quodlibet import qltk
from quodlibet import _
from quodlibet.qltk.views import BaseView
from quodlibet.qltk.textedit import PatternEditBox
from quodlibet.qltk.x import SymbolicIconImage, MenuItem, Button
from quodlibet.qltk import Icons
from quodlibet.qltk.menubutton import MenuButton
from quodlibet.qltk.ccb import ConfigCheckButton
from quodlibet.util import connect_obj
from .util import get_headers, save_headers, get_titles, save_titles


@util.enum
class ColumnMode(int):
    SMALL = 0
    WIDE = 1
    COLUMNAR = 2


class ColumnModeSelection(Gtk.VBox):
    def __init__(self, browser):
        super().__init__(spacing=6)
        self.browser = browser
        self.buttons = []

        group = None
        mode_label = {
            ColumnMode.SMALL: _("Small"),
            ColumnMode.WIDE: _("Wide"),
            ColumnMode.COLUMNAR: _("Columnar"),
        }
        for mode in ColumnMode.values:
            lbl = mode_label[ColumnMode.value_of(mode)]
            group = Gtk.RadioButton(group=group, label=lbl)
            if mode == config.getint("browsers", "pane_mode", ColumnMode.SMALL):
                group.set_active(True)
            self.pack_start(group, False, True, 0)
            self.buttons.append(group)

        for button in self.buttons:
            button.connect("toggled", self.toggled)

    def toggled(self, button):
        if not button.get_active():
            return
        selected_mode = ColumnMode.SMALL
        if self.buttons[1].get_active():
            selected_mode = ColumnMode.WIDE
        if self.buttons[2].get_active():
            selected_mode = ColumnMode.COLUMNAR
        config.set("browsers", "pane_mode", int(selected_mode))
        self.browser.set_all_column_mode(selected_mode)


class PatternEditor(Gtk.VBox):
    PRESETS = [
        ["genre", "~people", "album"],
        ["~people", "album"],
    ]

    # Columns for the ListStore: Pattern, Title, IsPlaceholder
    (
        COL_PATTERN,
        COL_TITLE,
        COL_IS_PLACEHOLDER
    ) = range(3)

    def __init__(self):
        super().__init__(spacing=6)

        self.__presets_patterns = {}
        buttons = []

        group = None
        for patterns in self.PRESETS:
            tied = "~" + "~".join(patterns)
            group = Gtk.RadioButton(
                group=group, label="_" + util.tag(tied), use_underline=True
            )
            self.__presets_patterns[group] = patterns
            buttons.append(group)

        group = Gtk.RadioButton(group=group, label=_("_Custom"), use_underline=True)
        self.__custom = group
        buttons.append(group)

        # Store: Pattern (str), Title (str), IsPlaceholder (bool)
        self.__model = model = Gtk.ListStore(str, str, bool)

        radio_box = Gtk.VBox(spacing=6)
        for button in buttons:
            radio_box.pack_start(button, False, True, 0)
            button.connect("toggled", self.__toggled, model)

        self.pack_start(radio_box, False, True, 0)

        # List View of columns
        self.view = view = BaseView(model=model)
        view.set_reorderable(True)
        view.set_headers_visible(True)
        view.get_selection().set_mode(Gtk.SelectionMode.BROWSE)

        self.__add_columns(view)

        # Pattern Editor
        self.editor = PatternEditBox()
        self.editor.set_size_request(-1, 100)
        for child in self.editor.get_children():
            if isinstance(child, Gtk.VBox):
                child.hide()
                child.set_no_show_all(True)
        self.editor.buffer.connect("changed", self.__text_changed, view)

        self.title_entry = Gtk.Entry()
        self.title_entry.connect("changed", self.__title_changed, view)

        selection = view.get_selection()
        selection.connect("changed", self.__selection_changed)

        sw = Gtk.ScrolledWindow()
        sw.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        sw.set_shadow_type(Gtk.ShadowType.IN)
        sw.set_min_content_height(120)
        sw.add(view)

        edit_box = Gtk.VBox(spacing=6)
        edit_box.pack_start(sw, True, True, 0)

        lbl_pat = Gtk.Label(label=_("Pattern content:"))
        lbl_pat.set_alignment(0, 0.5)
        edit_box.pack_start(lbl_pat, False, True, 0)
        edit_box.pack_start(self.editor, True, True, 0)

        lbl_title = Gtk.Label(label=_("Column title:"))
        lbl_title.set_alignment(0, 0.5)
        edit_box.pack_start(lbl_title, False, True, 0)
        edit_box.pack_start(self.title_entry, False, True, 0)

        self.pack_start(edit_box, True, True, 0)

    def __add_columns(self, view):
        # 1. Number Column
        render_num = Gtk.CellRendererText()
        render_num.set_property("scale", 0.8)
        render_num.set_property("foreground", "grey")
        col_num = Gtk.TreeViewColumn("#", render_num)
        col_num.set_resizable(False)
        col_num.set_fixed_width(30)
        col_num.set_alignment(0.5)

        def index_data_func(column, cell, model, iter_, data):
            path = model.get_path(iter_)
            if path:
                cell.set_property("text", str(path.get_indices()[0] + 1))

        col_num.set_cell_data_func(render_num, index_data_func)
        view.append_column(col_num)

        # 2. Pattern Column
        render_pat = Gtk.CellRendererText()
        render_pat.set_property("ellipsize", Pango.EllipsizeMode.END)
        col_pat = Gtk.TreeViewColumn(_("Pattern"), render_pat)
        col_pat.set_expand(True)
        col_pat.set_resizable(True)

        def pattern_data_func(column, cell, model, iter_, data):
            text = model.get_value(iter_, self.COL_PATTERN)
            is_placeholder = model.get_value(iter_, self.COL_IS_PLACEHOLDER)

            if is_placeholder:
                cell.set_property("text", _("Add new..."))
                cell.set_property("foreground", "grey")
                cell.set_property("style", Pango.Style.ITALIC)
            else:
                cell.set_property("text", text.replace("\n", " ↵ "))
                cell.set_property("foreground-set", False)
                cell.set_property("style", Pango.Style.NORMAL)

        col_pat.set_cell_data_func(render_pat, pattern_data_func)
        view.append_column(col_pat)

        # 3. Title Column
        render_title = Gtk.CellRendererText()
        render_title.set_property("ellipsize", Pango.EllipsizeMode.END)
        render_title.set_property("foreground", "grey")
        col_title = Gtk.TreeViewColumn(_("Title"), render_title)
        col_title.set_visible(True)
        col_title.set_min_width(80)
        col_title.set_resizable(True)

        def title_data_func(column, cell, model, iter_, data):
            title = model.get_value(iter_, self.COL_TITLE)
            is_placeholder = model.get_value(iter_, self.COL_IS_PLACEHOLDER)
            if is_placeholder:
                cell.set_property("text", "")
            else:
                cell.set_property("text", title)

        col_title.set_cell_data_func(render_title, title_data_func)
        view.append_column(col_title)

        # 4. Delete Column
        render_del = Gtk.CellRendererPixbuf()
        render_del.set_property("icon-name", "edit-delete-symbolic")
        render_del.set_property("mode", Gtk.CellRendererMode.ACTIVATABLE)

        col_del = Gtk.TreeViewColumn("", render_del)
        col_del.set_fixed_width(40)
        col_del.set_alignment(0.5)

        def delete_visible_func(column, cell, model, iter_, data):
            is_placeholder = model.get_value(iter_, self.COL_IS_PLACEHOLDER)
            cell.set_property("visible", not is_placeholder)

        col_del.set_cell_data_func(render_del, delete_visible_func)
        view.append_column(col_del)

        view.connect("button-press-event", self.__delete_clicked, col_del)

    def __delete_clicked(self, view, event, col_del):
        if event.button != Gdk.BUTTON_PRIMARY:
            return
        try:
            path, col, _x, _y = view.get_path_at_pos(int(event.x), int(event.y))
        except TypeError:
            return

        if col == col_del:
            model = view.get_model()
            iter_ = model.get_iter(path)
            is_placeholder = model.get_value(iter_, self.COL_IS_PLACEHOLDER)
            if not is_placeholder:
                model.remove(iter_)
                return True
        return False

    def set_data(self, patterns, titles):
        if len(titles) < len(patterns):
            titles.extend([""] * (len(patterns) - len(titles)))
        titles = titles[:len(patterns)]

        # Check if matches a preset (patterns match and no titles)
        matched_preset = None
        has_titles = any(t for t in titles)

        if not has_titles:
            for button, preset_patterns in self.__presets_patterns.items():
                if patterns == preset_patterns:
                    matched_preset = button
                    break

        if matched_preset:
            matched_preset.set_active(True)
            self.__toggled(matched_preset, self.__model)
        else:
            self.__custom.set_active(True)
            self.__fill_model(self.__model, patterns, titles)

    def get_data(self):
        patterns = []
        titles = []
        for row in self.__model:
            if not row[self.COL_IS_PLACEHOLDER]:
                patterns.append(row[self.COL_PATTERN])
                titles.append(row[self.COL_TITLE])
        return patterns, titles

    def __fill_model(self, model, patterns, titles):
        model.clear()
        for p, t in zip(patterns, titles):
            model.append(row=[p, t, False])

        model.append(row=["", "", True])

    def __selection_changed(self, selection):
        model, iter_ = selection.get_selected()
        has_selection = bool(iter_)
        self.editor.set_sensitive(has_selection)
        self.title_entry.set_sensitive(has_selection)

        if iter_:
            pattern = model.get_value(iter_, self.COL_PATTERN)
            title = model.get_value(iter_, self.COL_TITLE)
            is_placeholder = model.get_value(iter_, self.COL_IS_PLACEHOLDER)

            # Block signals
            self.editor.buffer.handler_block_by_func(self.__text_changed)
            self.title_entry.handler_block_by_func(self.__title_changed)

            if is_placeholder:
                self.editor.text = ""
                self.title_entry.set_text("")
            else:
                self.editor.text = pattern
                self.title_entry.set_text(title)

            self.editor.buffer.handler_unblock_by_func(self.__text_changed)
            self.title_entry.handler_unblock_by_func(self.__title_changed)
        else:
            self.editor.text = ""
            self.title_entry.set_text("")

    def __text_changed(self, buffer, view):
        selection = view.get_selection()
        model, iter_ = selection.get_selected()
        if iter_:
            is_placeholder = model.get_value(iter_, self.COL_IS_PLACEHOLDER)
            new_text = self.editor.text

            if is_placeholder:
                if new_text.strip():
                    model.set_value(iter_, self.COL_PATTERN, new_text)
                    model.set_value(iter_, self.COL_IS_PLACEHOLDER, False)
                    model.append(row=["", "", True])
            else:
                model.set_value(iter_, self.COL_PATTERN, new_text)

    def __title_changed(self, entry, view):
        selection = view.get_selection()
        model, iter_ = selection.get_selected()
        if iter_:
            new_text = entry.get_text()
            model.set_value(iter_, self.COL_TITLE, new_text)

    def __toggled(self, button, model):
        if not button.get_active():
            return

        is_custom = (button == self.__custom)

        if not is_custom:
            patterns = self.__presets_patterns[button]
            titles = [""] * len(patterns)
            self.__fill_model(model, patterns, titles)

        # Only custom is editable
        self.view.set_sensitive(is_custom)
        self.editor.set_sensitive(is_custom)
        self.title_entry.set_sensitive(is_custom)


class PreferencesButton(Gtk.HBox):
    def __init__(self, browser):
        super().__init__()

        self._menu = menu = Gtk.Menu()

        pref_item = MenuItem(_("_Preferences"), Icons.PREFERENCES_SYSTEM)

        def preferences_cb(menu_item):
            window = Preferences(browser)
            window.show()

        pref_item.connect("activate", preferences_cb)
        menu.append(pref_item)

        menu.show_all()

        button = MenuButton(
            SymbolicIconImage(Icons.OPEN_MENU, Gtk.IconSize.MENU), arrow=True
        )
        button.set_menu(menu)
        button.show()
        self.pack_start(button, True, True, 0)


class Preferences(qltk.UniqueWindow):
    def __init__(self, browser):
        if self.is_not_unique():
            return
        super().__init__()

        self.set_transient_for(qltk.get_top_parent(browser))
        self.set_default_size(500, 600)
        self.set_border_width(12)
        self.set_title(_("Paned Browser Preferences"))

        vbox = Gtk.VBox(spacing=12)

        column_modes = ColumnModeSelection(browser)
        column_mode_frame = qltk.Frame(_("Column layout"), child=column_modes)

        editor = PatternEditor()
        editor.set_data(get_headers(), get_titles())
        editor_frame = qltk.Frame(_("Column content"), child=editor)

        # -- Options Section --
        options_box = Gtk.VBox(spacing=6)

        equal_width = ConfigCheckButton(
            _("Equal pane width"), "browsers", "equal_pane_width", populate=True
        )
        options_box.pack_start(equal_width, False, True, 0)

        # Cover Size SpinButton
        size_box = Gtk.HBox(spacing=12)
        size_label = Gtk.Label(label=_("Cover size:"))
        size_label.set_alignment(0, 0.5)

        current_size = config.getint("browsers", "paned_cover_size", 96)
        adj = Gtk.Adjustment(value=current_size, lower=16, upper=512, step_increment=8)
        spin = Gtk.SpinButton(adjustment=adj)

        def on_size_changed(spin):
            config.set("browsers", "paned_cover_size", str(int(spin.get_value())))

        spin.connect("value-changed", on_size_changed)

        size_box.pack_start(size_label, False, False, 0)
        size_box.pack_start(spin, False, False, 0)
        options_box.pack_start(size_box, False, True, 0)

        options_frame = qltk.Frame(_("Options"), child=options_box)

        apply_ = Button(_("_Apply"))
        connect_obj(
            apply_, "clicked", self.__apply, editor, browser, False, equal_width
        )

        cancel = Button(_("_Cancel"))
        cancel.connect("clicked", lambda x: self.destroy())

        box = Gtk.HButtonBox()
        box.set_spacing(6)
        box.set_layout(Gtk.ButtonBoxStyle.EDGE)

        box.pack_start(apply_, False, False, 0)
        self.use_header_bar()
        if not self.has_close_button():
            box.pack_start(cancel, True, True, 0)

        vbox.pack_start(column_mode_frame, False, False, 0)
        vbox.pack_start(editor_frame, True, True, 0)
        vbox.pack_start(options_frame, False, False, 0)
        vbox.pack_start(box, False, True, 0)

        self.add(vbox)

        cancel.grab_focus()
        self.get_child().show_all()

    def __apply(self, editor, browser, close, equal_width):
        new_patterns, new_titles = editor.get_data()

        if new_patterns != get_headers() or new_titles != get_titles():
            save_headers(new_patterns)
            save_titles(new_titles)
            browser.set_all_panes()

        if equal_width.get_active():
            browser.make_pane_widths_equal()

        if close:
            self.destroy()
