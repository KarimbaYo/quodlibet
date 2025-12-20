# Copyright 2013 Christoph Reiter
#           2015 Nick Boultbee
#           2017 Fredrik Strupe
#           2025 Yoann Guerin
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.

from gi.repository import Gtk

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
from .util import get_headers, save_headers


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

    def __init__(self):
        super().__init__(spacing=6)

        self.__headers = headers = {}
        buttons = []

        group = None
        for tags in self.PRESETS:
            tied = "~" + "~".join(tags)
            group = Gtk.RadioButton(
                group=group, label="_" + util.tag(tied), use_underline=True
            )
            headers[group] = tags
            buttons.append(group)

        group = Gtk.RadioButton(group=group, label=_("_Custom"), use_underline=True)
        self.__custom = group
        headers[group] = []
        buttons.append(group)

        button_box = Gtk.HBox(spacing=6)
        self.__model = model = Gtk.ListStore(str)

        radio_box = Gtk.VBox(spacing=6)
        for button in buttons:
            radio_box.pack_start(button, False, True, 0)
            button.connect("toggled", self.__toggled, button_box, model)

        self.pack_start(radio_box, False, True, 0)

        # List View of columns
        view = BaseView(model=model)
        view.set_reorderable(True)
        view.set_headers_visible(False)
        view.get_selection().set_mode(Gtk.SelectionMode.BROWSE)

        # Use PatternEditBox directly
        self.editor = PatternEditBox()
        self.editor.set_size_request(-1, 100)

        # HACK: Remove the button box (Revert/Apply) from PatternEditBox/TextEditBox
        # TextEditBox structure is HBox containing [ScrolledWindow, VBox(Buttons)]
        # We iterate over children to find the VBox and hide it.
        for child in self.editor.get_children():
            if isinstance(child, Gtk.VBox):
                child.hide()
                child.set_no_show_all(True)

        # When text changes in editor, update the selected row in the list
        self.editor.buffer.connect("changed", self.__text_changed, view)

        ctrl_box = Gtk.VBox(spacing=6)

        add = Button(_("_Add"), Icons.LIST_ADD)
        ctrl_box.pack_start(add, False, True, 0)
        add.connect("clicked", self.__add, model)

        remove = Button(_("_Remove"), Icons.LIST_REMOVE)
        ctrl_box.pack_start(remove, False, True, 0)
        remove.connect("clicked", self.__remove, view)

        selection = view.get_selection()
        selection.connect("changed", self.__selection_changed, remove)

        sw = Gtk.ScrolledWindow()
        sw.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        sw.set_shadow_type(Gtk.ShadowType.IN)
        sw.set_min_content_height(120)
        sw.add(view)

        edit_box = Gtk.VBox(spacing=6)
        edit_box.pack_start(sw, True, True, 0)

        # Add label for the editor
        lbl = Gtk.Label(label=_("Column Pattern:"))
        lbl.set_alignment(0, 0.5)
        edit_box.pack_start(lbl, False, True, 0)
        # Pack with expand=True fill=True to take full width
        edit_box.pack_start(self.editor, True, True, 0)

        button_box.pack_start(edit_box, True, True, 0)
        button_box.pack_start(ctrl_box, False, True, 0)
        self.pack_start(button_box, True, True, 0)

        render = Gtk.CellRendererText()
        def display_func(column, cell, model, iter_, data):
            text = model.get_value(iter_, 0)
            # Flatten newlines for list display
            cell.set_property("text", text.replace("\n", " ↵ "))

        column = Gtk.TreeViewColumn(None, render)
        column.set_cell_data_func(render, display_func)
        view.append_column(column)

    @property
    def headers(self):
        for button in self.__headers.keys():
            if button.get_active():
                if button == self.__custom:
                    model_headers = [row[0] for row in self.__model]
                    self.__headers[self.__custom] = model_headers
                return self.__headers[button]
        return None

    @headers.setter
    def headers(self, new_headers):
        for button, headers in self.__headers.items():
            if headers == new_headers:
                button.set_active(True)
                button.emit("toggled")
                break
        else:
            self.__headers[self.__custom] = new_headers
            self.__custom.set_active(True)

    def __selection_changed(self, selection, remove):
        model, iter_ = selection.get_selected()
        has_selection = bool(iter_)
        remove.set_sensitive(has_selection)
        self.editor.set_sensitive(has_selection)

        if iter_:
            text = model[iter_][0]
            self.editor.buffer.handler_block_by_func(self.__text_changed)
            self.editor.text = text
            self.editor.buffer.handler_unblock_by_func(self.__text_changed)
        else:
            self.editor.text = ""

    def __text_changed(self, buffer, view):
        selection = view.get_selection()
        model, iter_ = selection.get_selected()
        if iter_:
            model[iter_][0] = self.editor.text

    def __add(self, button, model):
        model.append(row=["<artist>"])

    def __remove(self, button, view):
        view.remove_selection()

    def __toggled(self, button, edit_widget, model):
        tags = self.__headers[button]

        if tags:
            model.clear()
            for h in tags:
                model.append(row=[h])

        is_custom = button.get_active() and button is self.__custom
        edit_widget.set_sensitive(is_custom)


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
        self.set_default_size(500, 550)
        self.set_border_width(12)
        self.set_title(_("Paned Browser Preferences"))

        vbox = Gtk.VBox(spacing=12)

        column_modes = ColumnModeSelection(browser)
        column_mode_frame = qltk.Frame(_("Column layout"), child=column_modes)

        editor = PatternEditor()
        editor.headers = get_headers()
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
        if editor.headers != get_headers():
            save_headers(editor.headers)
            browser.set_all_panes()

        if equal_width.get_active():
            browser.make_pane_widths_equal()

        if close:
            self.destroy()
