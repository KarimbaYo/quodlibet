# Copyright 2013 Christoph Reiter
#        2020-23 Nick Boultbee
#           2021 Jej@github
#           2025 Yoann Guerin
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.

import re

from quodlibet.formats import TIME_TAGS
from quodlibet import config
from quodlibet import util
from quodlibet.formats import AudioFile
from quodlibet.pattern import XMLFromMarkupPattern as XMLFromPattern
from quodlibet.util.string.date import format_date

ALBUM_COVER_TAG = "<albumcover>"


class PaneConfig:
    """Row pattern format: 'categorize_pattern:display_pattern'

    * display_pattern is optional (fallback: ~#tracks)
    * patterns, tied and normal tags.
    * display patterns can have function prefixes for numerical tags.
    * ':' has to be escaped ('\\:')

    TODO: sort pattern, filter query
    """

    def __init__(self, row_pattern):

        self.wants_cover = ALBUM_COVER_TAG in row_pattern
        if self.wants_cover:
            # We strip whitespace but keep internal newlines for multi-line support
            row_pattern = row_pattern.replace(ALBUM_COVER_TAG, "").strip()

        # Changed from paned_icon_size to paned_cover_size as requested
        self.cover_size = config.getint("browsers", "paned_cover_size", 96)

        parts = [p.replace(r"\:", ":") for p in (re.split(r"(?<!\\):", row_pattern))]

        def is_numeric(s):
            return s[:2] == "~#" and "~" not in s[2:]

        def is_pattern(s):
            return "<" in s or "\n" in s

        def f_round(s):
            return isinstance(s, float) and f"{s:.2f}" or s

        def is_date(s):
            return s in TIME_TAGS

        disp = (
            parts[1]
            if len(parts) >= 2
            else "[i][span alpha='40%']<~#tracks>[/span][/i]"
        )
        cat = parts[0]

        if is_pattern(cat):
            title = util.pattern(cat, esc=True, markup=True)
            try:
                # Use Markup pattern to support Pango markup in multi-line text
                pc = XMLFromPattern(cat)
            except ValueError:
                pc = XMLFromPattern("")
            tags = pc.tags
            format = pc.format_list
            has_markup = True
        else:
            title = util.tag(cat)
            tags = util.tagsplit(cat)
            has_markup = False
            if is_date(cat):

                def format(song: AudioFile) -> list[tuple[str, str]]:
                    fmt = config.gettext("settings", "datecolumn_timestamp_format")
                    date_str = format_date(song(cat), fmt)
                    return [(date_str, date_str)]
            elif is_numeric(cat):

                def format(song: AudioFile) -> list[tuple[str, str]]:
                    v = str(f_round(song(cat)))
                    return [(v, v)]
            else:

                def format(song: AudioFile) -> list[tuple[str, str]]:
                    return song.list_separate(cat)

        if is_pattern(disp):
            try:
                pd = XMLFromPattern(disp)
            except ValueError:
                pd = XMLFromPattern("")
            format_display = pd.format
        else:
            if is_numeric(disp):

                def format_display(coll):
                    return str(f_round(coll(disp)))
            else:

                def format_display(coll):
                    return util.escape(coll.comma(disp))

        self.title = title
        self.tags = set(tags)
        self.format = format
        self.format_display = format_display
        self.has_markup = has_markup

    def __repr__(self):
        return f"<{self.__class__.__name__} title={self.title!r} tags={self.tags!r}>"


def get_headers():
    # QL <= 2.1 saved the headers tab-separated, but had a space-separated
    # default value, so check for that.
    headers = config.get("browsers", "panes")
    if headers == "~people album":
        return headers.split()
    return headers.split("\t")


def save_headers(headers):
    headers = "\t".join(headers)
    config.set("browsers", "panes", headers)

def get_titles():
    titles = config.get("browsers", "panes_titles", "")

    if not titles:
        return []

    if titles.startswith("|"):
        titles = titles[1:]

    return titles.split("\t")

def save_titles(titles):
    titles_str = "|" + "\t".join(titles)
    config.set("browsers", "panes_titles", titles_str)
