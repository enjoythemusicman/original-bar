"""Offline behavior checks for the isolated CC42 routing demo; opens no MIDI ports."""

import importlib.util
import pathlib
import threading
import time
import unittest
from unittest.mock import Mock


DEMO = pathlib.Path(__file__).with_name("FloBar_v99_CC42_routing_demo.py")
spec = importlib.util.spec_from_file_location("cc42_demo", DEMO)
cc42_demo = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cc42_demo)


class FakeOutput:
    def __init__(self):
        self.messages = []

    def send(self, message):
        self.messages.append(message.copy())


class CC42RoutingDemoTests(unittest.TestCase):
    def test_standalone_message_keeps_channel_cc_and_value(self):
        controller = object.__new__(cc42_demo.Controller)
        controller.out_port = FakeOutput()
        controller._record_midi_out = Mock()
        source = cc42_demo.mido.Message(
            "control_change", channel=15, control=42, value=79
        )

        sent = controller.send_original_cc42(source)

        self.assertEqual(sent, source)
        self.assertEqual(controller.out_port.messages, [source])
        self.assertEqual(controller.out_port.messages[0].channel, 15)
        self.assertEqual(controller.out_port.messages[0].control, 42)
        self.assertEqual(controller.out_port.messages[0].value, 79)

    def test_standalone_handler_passes_cc42_once(self):
        controller = object.__new__(cc42_demo.Controller)
        controller.lock = threading.RLock()
        controller.lockout_until = 0.0
        controller.awaiting_tt = True
        controller.g2m_repeat_active = False
        controller.tb_repeat_active = False
        controller.send_original_cc42 = Mock()
        controller.handle_midi(
            cc42_demo.mido.Message(
                "control_change", channel=15, control=42, value=127
            )
        )
        controller.send_original_cc42.assert_called_once()

    def test_tt1_handler_consumes_cc42_into_hid_branch(self):
        controller = object.__new__(cc42_demo.Controller)
        controller.lock = threading.RLock()
        controller.lockout_until = 0.0
        controller.awaiting_tt = False
        controller.scenario_busy = True
        controller.g2m_repeat_active = False
        controller.tb_repeat_active = False
        controller.fade = cc42_demo.FadeState(
            active=True,
            special_window_until=time.monotonic() + 5.0,
        )
        controller.send_original_cc42 = Mock()
        controller.cancel_fade = Mock()
        controller._clear_fade = Mock()
        controller.guitar_swap_first_and_start_repeat_window = Mock()

        controller.handle_midi(
            cc42_demo.mido.Message(
                "control_change", channel=15, control=42, value=127
            )
        )

        controller.cancel_fade.assert_called_once_with(start_lockout=False)
        controller.guitar_swap_first_and_start_repeat_window.assert_called_once_with(127)
        controller.send_original_cc42.assert_not_called()

    def test_tt1_guitar_actions_only_send_hid(self):
        controller = object.__new__(cc42_demo.Controller)
        controller.lock = threading.RLock()
        controller.option_tab = Mock()
        controller._refresh_g2m_repeat_timeout = Mock()
        controller.send_cc = Mock()

        controller.guitar_swap_first_and_start_repeat_window(127)
        controller.guitar_repeat_cmd_tab_only(127)

        self.assertEqual(controller.option_tab.call_count, 2)
        controller.send_cc.assert_not_called()
        self.assertEqual(controller._refresh_g2m_repeat_timeout.call_count, 2)


if __name__ == "__main__":
    unittest.main()
