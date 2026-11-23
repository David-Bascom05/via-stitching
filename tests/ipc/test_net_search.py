# The filter-as-you-type net picker (plugins/_net_search.py), in the real IPC
# dialog. tests/legacy/test_net_search_legacy.py runs the same picker in the
# SWIG dialog against a real pcbnew board.
#
# Keys and clicks go through wx.UIActionSimulator, so they take the same path
# through the toolkit as a user's: an Enter the platform hands to the OK
# button, or a click on an already selected row, behave here as they would
# for real.
#
# Run with the plugin's own venv interpreter, which already has wx, kipy and
# shapely:
#   "$LOCALAPPDATA/KiCad/10.0/python-environments/com.github.jOaSbA.via-stitching/Scripts/python" tests/ipc/test_net_search.py
#
# License: GPL-3.0-or-later

import os
import random
import sys
import time
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "plugins"))

import wx  # noqa: E402
from kipy.board_types import BoardLayer  # noqa: E402

import via_stitching_action as vs  # noqa: E402
from _net_search import NetIndex  # noqa: E402
from via_stitching_action import ViaStitchingDialog, _resolve_net_name  # noqa: E402

_APP = wx.App()

# Never touch the user's real settings file: Reset settings deletes it.
vs._load_settings = lambda: {}
vs._clear_settings = lambda: None

NETS = sorted([
    "GND", "GNDA", "VCC", "+3V3", "/Battery Management/STACK+",
    "/Battery Management/STACK-", "/Sheet A/GND", "Net-(R1-Pad1)", "/MCU/SWDIO",
])

LAYERS = {
    BoardLayer.BL_F_Cu: "F.Cu",
    BoardLayer.BL_In1_Cu: "In1.Cu",
    BoardLayer.BL_In2_Cu: "In2.Cu",
    BoardLayer.BL_B_Cu: "B.Cu",
}


def _dialog(nets=NETS):
    board = SimpleNamespace(
        get_enabled_layers=lambda: list(LAYERS.keys()),
        get_layer_name=lambda l: LAYERS[l],
        get_selection=lambda kind: [],
        get_items=lambda types: [],
    )
    return ViaStitchingDialog(None, nets, board)


def _many_nets():
    nets = {"GND", "+3V3", "+5V"}
    for sheet in ("Power", "MCU", "Motor Driver", "CAN", "LEDs", "Sensors"):
        nets |= {f"/{sheet}/{('SIG', 'LED', 'EN', 'PWM', 'ADC')[i % 5]}{i}" for i in range(70)}
    return sorted(nets)


def _pump(seconds=0.3):
    end = time.time() + seconds
    while time.time() < end:
        wx.GetApp().Yield(True)
        time.sleep(0.01)


class _User:
    """Real keys and clicks into a shown dialog. Records OK presses instead of
    letting them close it."""

    def __init__(self, dlg):
        self.dlg = dlg
        self.sim = wx.UIActionSimulator()
        self.oks = 0
        dlg.Bind(wx.EVT_BUTTON, self._on_button)
        dlg.Show()
        dlg.Raise()
        _pump(0.5)

    def _on_button(self, evt):
        if evt.GetId() == wx.ID_OK:
            self.oks += 1
        else:
            evt.Skip()

    def type(self, text, clear=True):
        # Unshifted characters only: the simulator sends "+" as "=".
        self.dlg.net.SetFocus()
        _pump(0.1)
        if clear:
            self.dlg.net.SelectAll()
        else:
            self.dlg.net.SetInsertionPointEnd()  # GTK selects it all on focus
        for ch in text:
            self.sim.Char(ord(ch))
            _pump(0.03)
        _pump()

    def key(self, code):
        self.sim.KeyDown(code)
        self.sim.KeyUp(code)
        _pump()

    def click_row(self, row):
        rect = self.dlg.net_list.GetItemRect(row)
        self.sim.MouseMove(self.dlg.net_list.ClientToScreen(
            wx.Point(rect.x + 30, rect.y + rect.height // 2)))
        _pump(0.1)
        self.sim.MouseClick()
        _pump()


def _highlighted(dlg):
    row = dlg.net_list.GetFirstSelected()
    return dlg.net_list.rows[row] if row >= 0 else None


def _ink(ctrl):
    """(dark, light) pixel counts of what the list has actually painted, read
    back off the screen: the list's own state can be right while nothing is
    drawn. A capture of nothing at all comes back black, so a real list has to
    show both its background and some text."""
    w, h = ctrl.GetClientSize()
    bmp = wx.Bitmap(w, h)
    mem = wx.MemoryDC(bmp)
    mem.Blit(0, 0, w, h, wx.ClientDC(ctrl), 0, 0)
    mem.SelectObject(wx.NullBitmap)
    data = bmp.ConvertToImage().GetData()
    pixels = [data[i:i + 3] for i in range(0, len(data), 3)]
    return sum(1 for p in pixels if max(p) < 90), sum(1 for p in pixels if min(p) > 200)


# ---- NetIndex ---------------------------------------------------------------

def test_empty_query_lists_every_net_in_order():
    index = NetIndex(NETS)
    assert index.filter("") == NETS
    assert index.filter("   ") == NETS


def test_matches_ignore_case_and_any_part_of_the_name():
    index = NetIndex(NETS)
    assert index.filter("stack") == ["/Battery Management/STACK+", "/Battery Management/STACK-"]
    assert index.filter("swd") == ["/MCU/SWDIO"]
    assert index.filter("r1") == ["Net-(R1-Pad1)"]
    assert index.filter(" gnda ") == ["GNDA"]
    assert index.filter("nothing like it") == []


def test_best_matches_come_first_in_the_order_ok_prefers_them():
    index = NetIndex(NETS)
    assert index.filter("gnd") == ["GND", "/Sheet A/GND", "GNDA"]
    assert index.filter("GND") == ["GND", "/Sheet A/GND", "GNDA"]
    assert index.filter("stack+") == ["/Battery Management/STACK+"]
    names = sorted(["/A/xVCC", "/VCC_IO/x", "VCC_3V3", "VCC", "vcc"])
    assert NetIndex(names).filter("vcc") == ["vcc", "VCC", "VCC_3V3", "/A/xVCC", "/VCC_IO/x"]


def test_the_highlight_is_what_ok_resolves():
    """NetIndex.match, the highlighted net, is the net OK stitches for what's
    typed, or None wherever OK refuses it. It is always the list's first row,
    so the list never suggests one net while OK picks another."""
    names = NETS + ["/Sheet B/GND", "gnd", "/Power/VCC", "/Power/VCC_IO"]
    index = NetIndex(names)
    for name in names:
        for start in range(len(name)):
            for end in range(start + 1, len(name) + 1):
                for typed in {name[start:end], name[start:end].lower(), name[start:end].upper()}:
                    typed = typed.strip()
                    if not typed:
                        continue
                    try:
                        resolved = _resolve_net_name(typed, names)
                    except ValueError:
                        resolved = None
                    rows = index.filter(typed)
                    assert index.match == resolved, (typed, index.match, resolved)
                    if resolved is not None:
                        assert rows[0] == resolved, (typed, rows[0], resolved)


def test_narrowing_the_query_gives_what_a_fresh_search_would():
    """filter() reuses the previous query's matches when the new query extends
    it. Typing, deleting and retyping in any order must never leave a net out
    or let one in."""
    rng = random.Random(1)
    alphabet = "abcd/+-_ "
    names = sorted({
        "/" + "".join(rng.choice(alphabet) for _ in range(rng.randint(1, 8)))
        + "/" + "".join(rng.choice(alphabet) for _ in range(rng.randint(1, 6)))
        for _ in range(3000)
    })
    index = NetIndex(names)
    typed = ""
    for _ in range(3000):
        if typed and rng.random() < 0.35:
            typed = typed[:-1]
        elif rng.random() < 0.05:
            typed = ""
        else:
            typed += rng.choice(alphabet.upper() + alphabet)
        assert index.filter(typed) == NetIndex(names).filter(typed), repr(typed)


# ---- the dialog -------------------------------------------------------------

def test_the_dialog_opens_on_every_net_with_its_net_highlighted():
    dlg = _dialog()
    try:
        assert dlg.net.GetValue() == "GND"
        assert dlg.net_list.rows == NETS
        assert dlg.net_list.GetItemCount() == len(NETS)
        assert _highlighted(dlg) == "GND"
    finally:
        dlg.Destroy()


def test_typing_filters_the_list_and_leaves_the_text_alone():
    dlg = _dialog()
    try:
        user = _User(dlg)
        user.type("stack")
        assert dlg.net.GetValue() == "stack"
        assert dlg.net_list.rows == ["/Battery Management/STACK+", "/Battery Management/STACK-"]
        assert _highlighted(dlg) is None, "two nets match, so OK would refuse: none highlighted"
        assert dlg.net_list.GetFocusedItem() == -1, "no row may look half picked either"
        user.type("-", clear=False)
        assert dlg.net_list.rows == ["/Battery Management/STACK-"]
        assert _highlighted(dlg) == "/Battery Management/STACK-"
        user.type("gnd")
        assert _highlighted(dlg) == "GND", "the net OK stitches for 'gnd'"
        assert dlg.values()["net_name"] == "GND"
        user.type("zzz")
        assert dlg.net_list.rows == [] and dlg.net_list.GetFirstSelected() == -1
    finally:
        dlg.Destroy()


def test_arrow_keys_walk_the_matches_and_fill_the_field():
    dlg = _dialog()
    try:
        user = _User(dlg)
        user.type("stack")
        user.key(wx.WXK_DOWN)
        assert dlg.net.GetValue() == "/Battery Management/STACK+"
        assert len(dlg.net_list.rows) == 2, "the list keeps showing what was searched for"
        user.key(wx.WXK_DOWN)
        assert dlg.net.GetValue() == "/Battery Management/STACK-"
        user.key(wx.WXK_DOWN)  # already on the last match
        assert dlg.net.GetValue() == "/Battery Management/STACK-"
        user.key(wx.WXK_UP)
        assert dlg.net.GetValue() == "/Battery Management/STACK+"
        assert wx.Window.FindFocus() is dlg.net
        assert dlg.values()["net_name"] == "/Battery Management/STACK+"
    finally:
        dlg.Destroy()


def test_enter_is_still_ok():
    dlg = _dialog()
    try:
        user = _User(dlg)
        user.type("stack-")
        user.key(wx.WXK_RETURN)
        assert user.oks == 1
        assert dlg.net.GetValue() == "stack-", "Enter must not rewrite the field"
    finally:
        dlg.Destroy()


def test_clicking_a_net_fills_the_field_and_enter_is_ok_after():
    dlg = _dialog()
    try:
        user = _User(dlg)
        user.type("gnd")
        user.click_row(0)  # the top match: once this click did nothing
        assert dlg.net.GetValue() == "GND"
        user.type("stack")
        user.click_row(1)
        assert dlg.net.GetValue() == "/Battery Management/STACK-"
        assert len(dlg.net_list.rows) == 2
        assert wx.Window.FindFocus() is dlg.net, "focus goes back to the field"
        user.key(wx.WXK_RETURN)
        assert user.oks == 1
    finally:
        dlg.Destroy()


def test_picking_a_net_updates_the_preview():
    dlg = _dialog()
    try:
        user = _User(dlg)
        user.type("stack")
        dlg.preview._timer.Stop()
        user.key(wx.WXK_DOWN)
        assert dlg.preview._timer.IsRunning(), "a picked net never reached the preview"
    finally:
        dlg.Destroy()


def test_reset_lists_every_net_again():
    dlg = _dialog()
    try:
        _User(dlg).type("stack")
        dlg._on_reset()
        assert dlg.net.GetValue() == "GND"
        assert dlg.net_list.rows == NETS
        assert _highlighted(dlg) == "GND"
    finally:
        dlg.Destroy()


def test_the_list_does_not_widen_the_dialog():
    dlg = _dialog()
    try:
        # It takes the field's width: on its own it asks for 400 px or so,
        # which once widened the whole dialog by a sixth.
        assert dlg.net_list.GetEffectiveMinSize().width <= dlg.net.GetEffectiveMinSize().width
        assert abs(dlg.net_list.GetSize().width - dlg.net.GetSize().width) <= 2
    finally:
        dlg.Destroy()


def test_the_net_it_opens_on_is_in_view_on_a_big_board():
    dlg = _dialog(_many_nets())
    try:
        _User(dlg)
        row, top = dlg.net_list.GetFirstSelected(), dlg.net_list.GetTopItem()
        assert dlg.net_list.rows[row] == "GND"
        assert top <= row < top + dlg.net_list.GetCountPerPage(), (top, row)
    finally:
        dlg.Destroy()


def test_filtering_from_far_down_the_list_still_draws_the_matches():
    """GND sits far down a big board's list, so the list opens scrolled there.
    Filtering from that point once left the right rows in the list but none
    of them painted."""
    dlg = _dialog(_many_nets())
    try:
        user = _User(dlg)
        assert dlg.net_list.GetTopItem() > 100
        user.type("led")
        assert len(dlg.net_list.rows) > 100 and dlg.net_list.GetTopItem() == 0
        dark, light = _ink(dlg.net_list)
        assert light > 0, "read nothing back off the screen"
        assert dark > 0, "the matches are in the list but none were drawn"
    finally:
        dlg.Destroy()


def test_resizing_the_dialog_leaves_the_list_where_it_was_scrolled():
    dlg = _dialog(_many_nets())
    try:
        _User(dlg)
        dlg.net_list.EnsureVisible(0)
        _pump()
        assert dlg.net_list.GetTopItem() == 0
        w, h = dlg.GetSize()
        dlg.SetSize(w + 40, h + 40)
        _pump()
        assert dlg.net_list.GetTopItem() == 0
    finally:
        dlg.Destroy()


def test_typing_stays_fast_on_a_huge_board():
    """20 000 nets, typed one character at a time. Each keystroke is the field's
    whole EVT_TEXT handling: filter, list update, highlight."""
    nets = sorted({f"/Sheet {s}/NET_{i}" for s in range(200) for i in range(100)})
    dlg = _dialog(nets)
    try:
        worst = 0.0
        for typed in ("s", "sh", "she", "sheet 1", "sheet 12", "sheet 12/", "sheet 12/net_4"):
            t0 = time.perf_counter()
            dlg.net.SetValue(typed)
            worst = max(worst, time.perf_counter() - t0)
        assert dlg.net_list.rows[0] == "/Sheet 12/NET_4"
        print(f"   slowest keystroke over {len(nets)} nets: {worst * 1000:.1f} ms")
        assert worst < 0.1, f"a keystroke took {worst * 1000:.0f} ms"
    finally:
        dlg.Destroy()


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
    print("all passed")
