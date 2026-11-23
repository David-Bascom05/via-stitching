# Filter-as-you-type net picker, shared by both builds.
#
# The net field stays a text box, and a list under it shows the nets matching
# what is typed, best match first. Clicking a net, or moving to it with the
# arrow keys in the field, puts its full name in the field. The field is still
# what the dialog reads and resolves with _resolve_net_name, so typing a name
# by hand, and Enter for OK, work as they always did.
#
# The list is virtual (wx asks only for the rows it draws), and a query that
# extends the previous one only searches its matches, so it keeps up with a
# keystroke on a board with tens of thousands of nets.
#
# Plain wx, no board API calls, so the IPC and SWIG builds use this file
# unchanged. build.py ships it in both packages.
#
# License: GPL-3.0-or-later

import wx

LIST_ROWS = 6

_NAV_KEYS = {
    wx.WXK_DOWN: 1, wx.WXK_NUMPAD_DOWN: 1,
    wx.WXK_UP: -1, wx.WXK_NUMPAD_UP: -1,
    wx.WXK_PAGEDOWN: LIST_ROWS, wx.WXK_NUMPAD_PAGEDOWN: LIST_ROWS,
    wx.WXK_PAGEUP: -LIST_ROWS, wx.WXK_NUMPAD_PAGEUP: -LIST_ROWS,
}


class NetIndex:
    """The board's net names, prepared once for repeated filtering."""

    def __init__(self, names):
        self.names = list(names)
        self._folded = [n.casefold() for n in self.names]
        self._leaf = [f.rsplit("/", 1)[-1] for f in self._folded]  # last sheet segment
        self.longest = max((len(n) for n in self.names), default=0)
        self.match = None  # the net OK would stitch for the last query, if any
        self._last_key = ""
        self._last_hits = range(len(self.names))

    def filter(self, typed):
        """The names containing `typed`, ignoring case, best match first.

        In the order _resolve_net_name prefers them: the exact name, the name
        ignoring case, the last sheet segment, then names whose last segment
        or whole name starts with it, then the rest. Each tier keeps the
        order the names were given in.

        Also sets `match` to the net _resolve_net_name picks for `typed`, or
        None where it would refuse: no net, or more than one equally good.
        """
        text = typed.strip()
        key = text.casefold()
        self.match = None
        if not key:
            self._last_key, self._last_hits = "", range(len(self.names))
            return list(self.names)
        # A name containing "stack+" also contains "stack".
        if self._last_key and key.startswith(self._last_key):
            pool = self._last_hits
        else:
            pool = range(len(self.names))
        names, folded, leaf = self.names, self._folded, self._leaf
        hits = [i for i in pool if key in folded[i]]
        self._last_key, self._last_hits = key, hits

        tiers = ([], [], [], [], [], [])
        for i in hits:
            if names[i] == text:
                tiers[0].append(i)
            elif folded[i] == key:
                tiers[1].append(i)
            elif leaf[i] == key:
                tiers[2].append(i)
            elif leaf[i].startswith(key):
                tiers[3].append(i)
            elif folded[i].startswith(key):
                tiers[4].append(i)
            else:
                tiers[5].append(i)
        exact, folded_whole, whole_leaf = tiers[0], tiers[1], tiers[2]
        if exact:
            self.match = names[exact[0]]
        elif folded_whole or whole_leaf:
            first = folded_whole or whole_leaf
            self.match = names[first[0]] if len(first) == 1 else None
        elif len(hits) == 1:
            self.match = names[hits[0]]
        return [names[i] for tier in tiers for i in tier]


class _NetList(wx.ListCtrl):
    """A one-column virtual list: wx asks for each row's text as it draws it."""

    def __init__(self, parent):
        super().__init__(
            parent,
            style=wx.LC_REPORT | wx.LC_VIRTUAL | wx.LC_NO_HEADER | wx.LC_SINGLE_SEL,
        )
        self.rows = []
        self.InsertColumn(0, "")

    def OnGetItemText(self, item, column):
        return self.rows[item] if 0 <= item < len(self.rows) else ""

    def set_rows(self, rows):
        # Drop the selection and go back to the top while every index is
        # still valid. The generic list (GTK, macOS) only redoes its layout at
        # idle time, and shrinking it while scrolled far down leaves it
        # painted blank.
        selected = self.GetFirstSelected()
        if selected >= 0:
            self.Select(selected, False)
        # The focus outline too, or it stays on whatever row now sits there.
        focused = self.GetFocusedItem()
        if focused >= 0:
            self.SetItemState(focused, 0, wx.LIST_STATE_FOCUSED)
        if self.GetItemCount():
            self.EnsureVisible(0)
        self.rows = rows
        self.SetItemCount(len(rows))
        self.Refresh()


class NetSearch:
    """The net field and the list of matching nets under it.

    The dialog lays out `field` and `list` as two rows and reads `field`.
    The field's EVT_TEXT reaches the dialog as before, picked or typed, so
    the preview follows it.
    """

    def __init__(self, parent, names, value, _):
        self.index = NetIndex(names)
        self.field = wx.TextCtrl(parent, value=value)
        self.field.SetHint(_("Type to search nets"))
        self.list = _NetList(parent)
        self.list.SetToolTip(_(
            "Nets matching what is typed above, best match first. Click one, "
            "or use the arrow keys in the field, to pick it."
        ))
        # A small minimum width, so the list takes the field's width instead
        # of widening the dialog.
        self.list.SetMinSize((80, LIST_ROWS * (self.list.GetCharHeight() + 6) + 6))
        # Wide enough to scroll to the end of the longest name, the part that
        # tells two hierarchical nets apart. Estimated: measuring every name
        # would cost more than everything else here.
        self._text_width = int(self.index.longest * self.list.GetCharWidth() * 1.15) + 12

        self._quiet = False      # set while this class changes the field or list itself
        self._generation = 0     # bumped on every change to the field
        self._query = None       # the field text the rows were filtered by
        self._opened = False     # the opening scroll to the net has been done

        self.field.Bind(wx.EVT_TEXT, self._on_text)
        self.field.Bind(wx.EVT_KEY_DOWN, self._on_key)
        self.list.Bind(wx.EVT_LIST_ITEM_SELECTED, self._on_list_selected)
        self.list.Bind(wx.EVT_SIZE, self._on_list_size)
        # The generic list (GTK, macOS) takes its clicks in a child window.
        main = getattr(self.list, "GetMainWindow", lambda: None)() or self.list
        main.Bind(wx.EVT_LEFT_DOWN, self._on_list_click)

        self._show_all()

    @property
    def names(self):
        return self.index.names

    def set_value(self, value):
        """Put `value` in the field and list every net, for the dialog's
        Reset. The field still sends EVT_TEXT, so the preview follows."""
        self._set_field(value)
        self._show_all()

    # ---- internals ---------------------------------------------------------

    def _show_all(self):
        self._query = None
        self.list.set_rows(self.index.filter(""))
        value = self.field.GetValue()
        self._highlight(self.list.rows.index(value) if value in self.list.rows else -1)

    def _filter(self):
        """Show the nets matching the field, with the one OK would stitch
        highlighted: always the first row, when there is one."""
        self._query = self.field.GetValue()
        self.list.set_rows(self.index.filter(self._query))
        self._highlight(0 if self.index.match is not None else -1)

    def _on_text(self, evt):
        evt.Skip()  # the dialog schedules the preview off the same event
        self._generation += 1
        if self._quiet:
            return
        self._filter()
        # wx 3.0 (KiCad 6 on Linux) can send this event while the field
        # still holds its old text, so check again once it has settled.
        wx.CallAfter(self._recheck, self._generation)

    def _recheck(self, generation):
        if self.field and generation == self._generation and self.field.GetValue() != self._query:
            self._filter()

    def _on_key(self, evt):
        step = _NAV_KEYS.get(evt.GetKeyCode())
        if step is None or evt.HasModifiers():
            evt.Skip()
            return
        rows = self.list.rows
        if rows:
            current = self.list.GetFirstSelected()
            if current < 0:
                self._pick(0)
            else:
                self._pick(max(0, min(len(rows) - 1, current + step)))
        # Never skipped: the caret stays put, and focus stays in the field.

    def _on_list_selected(self, evt):
        if self._quiet:
            return
        row = evt.GetIndex()
        if 0 <= row < len(self.list.rows):
            self._pick(row, from_list=True)

    def _on_list_click(self, evt):
        evt.Skip()  # the list still selects the row itself
        where = self.list.ScreenToClient(evt.GetEventObject().ClientToScreen(evt.GetPosition()))
        row, _flags = self.list.HitTest(where)
        wx.CallAfter(self._after_click, row)

    def _after_click(self, row):
        if not self.field:
            return
        # A click on the row that is already highlighted selects nothing new,
        # so no EVT_LIST_ITEM_SELECTED: pick it here too.
        if 0 <= row < len(self.list.rows) and self.field.GetValue() != self.list.rows[row]:
            self._pick(row)
        # Back to the field: left in the list, Enter would activate the row
        # instead of pressing OK.
        self._focus_field()

    def _on_list_size(self, evt):
        evt.Skip()
        width = self.list.GetClientSize().width
        self.list.SetColumnWidth(0, max(width, self._text_width))
        if not self._opened:
            # The first scroll to the net happens before the dialog has given
            # the list its real height, which can leave the net out of view.
            wx.CallAfter(self._scroll_to_highlight)

    def _scroll_to_highlight(self):
        if not self.list:
            return
        row = self.list.GetFirstSelected()
        if row >= 0:
            self.list.EnsureVisible(row)
        if self.list.IsShownOnScreen():
            self._opened = True

    def _focus_field(self):
        if self.field:
            self.field.SetFocus()
            self.field.SetInsertionPointEnd()

    def _pick(self, row, from_list=False):
        """Put the net on `row` in the field. The list keeps showing the
        matches it was picked from, so the arrow keys carry on through them."""
        self._set_field(self.list.rows[row])
        if not from_list:
            self._highlight(row)

    def _set_field(self, value):
        self._quiet = True
        try:
            self.field.SetValue(value)  # sends EVT_TEXT, which the dialog needs
            self.field.SetInsertionPointEnd()
        finally:
            self._quiet = False

    def _highlight(self, row):
        self._quiet = True
        try:
            current = self.list.GetFirstSelected()
            if current >= 0 and current != row:
                self.list.Select(current, False)
            if row >= 0:
                self.list.Select(row)
                self.list.Focus(row)
        finally:
            self._quiet = False
