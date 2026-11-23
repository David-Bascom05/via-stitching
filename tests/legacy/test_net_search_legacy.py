# The filter-as-you-type net picker in the SWIG dialog, on a real pcbnew board.
# tests/ipc/test_net_search.py covers the matching itself (NetIndex), its
# speed, and real keys and clicks; this checks the same picker is wired into
# this build's dialog the same way.
#
# Typing goes through the field's own SetValue and event loop, which is where
# wx 3.0 (KiCad 6 on Linux) differs. Keys and clicks call the picker's
# handlers: wx.UIActionSimulator needs libXtst on wx 3.0, which the KiCad 6 CI
# image doesn't have.
#
# Run with KiCad's own interpreter:
#   "C:/Program Files/KiCad/9.0/bin/python.exe" tests/legacy/test_net_search_legacy.py

import sys
import time
from types import SimpleNamespace

sys.path.insert(0, __file__.rsplit("tests", 1)[0] + "plugins_legacy")

import pcbnew
import wx

wx.DisableAsserts()  # see test_edge_cases_legacy.py for why

import via_stitching_action_legacy as vsl

_APP = wx.App()

# Never touch the user's real settings file: Reset settings deletes it.
vsl._load_settings = lambda: {}
vsl._clear_settings = lambda: None

NETS = ["GND", "GNDA", "VCC", "/Battery Management/STACK+",
        "/Battery Management/STACK-", "/MCU/SWDIO"]


def _dialog():
    board = pcbnew.BOARD()
    board.SetCopperLayerCount(4)
    for name in NETS:
        board.Add(pcbnew.NETINFO_ITEM(board, name))
    return vsl.ViaStitchingDialogLegacy(None, board)


def _pump(seconds=0.3):
    end = time.time() + seconds
    while time.time() < end:
        wx.GetApp().Yield(True)
        time.sleep(0.01)


def _type(dlg, text):
    dlg.net.SetValue(text)
    _pump()  # wx 3.0 can report the old text first and correct it after


def _key(dlg, code):
    skipped = []
    dlg.net_search._on_key(SimpleNamespace(
        GetKeyCode=lambda: code, HasModifiers=lambda: False,
        Skip=lambda: skipped.append(True)))
    _pump()
    return bool(skipped)


def _click(dlg, row):
    dlg.net_list.Select(row)  # what the list does with the click itself
    dlg.net_search._after_click(row)
    _pump()


def _highlighted(dlg):
    row = dlg.net_list.GetFirstSelected()
    return dlg.net_list.rows[row] if row >= 0 else None


def test_opens_on_every_net_with_gnd_highlighted():
    dlg = _dialog()
    try:
        assert dlg.net.GetValue() == "GND"
        assert dlg.net_list.rows == sorted(NETS)
        assert _highlighted(dlg) == "GND"
    finally:
        dlg.Destroy()


def test_typing_filters_and_the_arrow_keys_pick():
    dlg = _dialog()
    try:
        _type(dlg, "stack")
        assert dlg.net_list.rows == ["/Battery Management/STACK+", "/Battery Management/STACK-"]
        assert _highlighted(dlg) is None
        assert not _key(dlg, wx.WXK_DOWN)
        assert not _key(dlg, wx.WXK_DOWN)
        assert dlg.net.GetValue() == "/Battery Management/STACK-"
        assert len(dlg.net_list.rows) == 2
        assert dlg.values()["net_name"] == "/Battery Management/STACK-"
        _type(dlg, "gnd")
        assert _highlighted(dlg) == "GND"
        assert dlg.values()["net_name"] == "GND"
    finally:
        dlg.Destroy()


def test_enter_is_left_to_the_dialog_and_a_click_picks():
    dlg = _dialog()
    try:
        _type(dlg, "swd")
        assert _key(dlg, wx.WXK_RETURN), "Enter goes on to the OK button"
        assert dlg.net.GetValue() == "swd"
        _type(dlg, "stack")
        _click(dlg, 1)
        assert dlg.net.GetValue() == "/Battery Management/STACK-"
        assert len(dlg.net_list.rows) == 2
    finally:
        dlg.Destroy()


def test_reset_lists_every_net_again():
    dlg = _dialog()
    try:
        _type(dlg, "stack")
        dlg._on_reset()
        assert dlg.net.GetValue() == "GND"
        assert dlg.net_list.rows == sorted(NETS)
        assert _highlighted(dlg) == "GND"
    finally:
        dlg.Destroy()


def test_the_list_sits_under_the_field_in_the_net_group():
    dlg = _dialog()
    try:
        field, lst = dlg.net.GetRect(), dlg.net_list.GetRect()
        assert lst.GetTop() >= field.GetBottom(), "the list must be below the field"
        assert lst.GetBottom() < dlg.via_type.GetRect().GetTop(), "and above the via settings"
        assert abs(lst.GetLeft() - field.GetLeft()) <= 2, "lined up with the field"
        assert abs(lst.GetWidth() - field.GetWidth()) <= 2, "and as wide as it"
    finally:
        dlg.Destroy()


def run():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failures = []
    for test in tests:
        try:
            test()
            print(f"PASS: {test.__name__}")
        except Exception as e:
            failures.append((test.__name__, e))
            print(f"FAIL: {test.__name__}: {e!r}")
    print()
    print(f"{len(tests) - len(failures)}/{len(tests)} PASSED")
    if failures:
        raise SystemExit(1)


if __name__ == "__main__":
    run()
