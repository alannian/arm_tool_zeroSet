from __future__ import annotations

import os
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QObject, QSettings, Signal  # noqa: E402

from mit_sender.app import _try_lock_once, main  # noqa: E402
from mit_sender.damiao import MotorFeedback, MotorSpec  # noqa: E402
from mit_sender.settings_store import SettingsStore  # noqa: E402
from mit_sender.ui import MitSenderWindow, create_app  # noqa: E402


class _FakeSignal:
    def connect(self, callback):  # noqa: ANN001 - mimics Qt signal.
        return None


class _FakeApp:
    def __init__(self, result: int | BaseException = 0) -> None:
        self.result = result

    def exec(self) -> int:
        if isinstance(self.result, BaseException):
            raise self.result
        return int(self.result)


class _FakeWindow:
    def __init__(self) -> None:
        self.shown = False
        self.closed = False

    def show(self) -> None:
        self.shown = True

    def close(self) -> None:
        self.closed = True


class UiSmokeTests(unittest.TestCase):
    def test_main_window_builds_with_default_motor_rows(self) -> None:
        app = create_app()
        with tempfile.TemporaryDirectory() as directory:
            settings = SettingsStore(QSettings(str(Path(directory) / "settings.ini"), QSettings.IniFormat))
            window = MitSenderWindow(settings)

            self.assertEqual(len(window.motor_specs), 7)
            self.assertEqual(window.tabs.count(), 2)
            self.assertEqual(window.tabs.tabText(0), "批量发送")
            self.assertEqual(window.tabs.tabText(1), "单电机调试")
            self.assertEqual(window.send_button.text(), "一键发送")
            self.assertEqual(window.uniform_send_button.text(), "统一发送")
            self.assertEqual(window.interface_input.currentText(), window._default_interface_choice())  # noqa: SLF001
            self.assertEqual(
                [window.interface_input.itemText(index) for index in range(window.interface_input.count())],
                list(window.available_interfaces),
            )
            self.assertEqual(window.start_can_button.text(), "启动 CAN")
            self.assertEqual(window.feedback_button.text(), "开启读取数据")
            self.assertEqual(window.selected_count_label.text(), "已选 7/7")
            self.assertEqual(window.select_all_button.text(), "全选")
            self.assertEqual(window.select_none_button.text(), "全不选")
            self.assertEqual(window.status_label.text(), "就绪")
            self.assertEqual(set(window.checkboxes), {1, 2, 3, 4, 5, 6, 7})
            self.assertEqual(window.debug_current_can_input.text(), "0x001")
            self.assertEqual(window.debug_current_mst_input.text(), "0x011")
            self.assertEqual(window.debug_motor_combo.currentData(), 1)
            self.assertEqual(window.debug_new_can_input.text(), "0x001")
            self.assertEqual(window.debug_new_mst_input.text(), "0x011")
            self.assertEqual(window.debug_effective_can_label.text(), "0x001")
            self.assertEqual(window.debug_effective_feedback_label.text(), "0x011")
            self.assertEqual(window.debug_detected_can_label.text(), "-")
            self.assertEqual(window.debug_detected_feedback_label.text(), "-")
            self.assertEqual(window.debug_feedback_position_label.text(), "-")
            self.assertEqual(window.debug_connection_group.title(), "连接与 ID")
            self.assertEqual(window.debug_feedback_group.title(), "实时反馈")
            self.assertEqual(window.debug_read_button.text(), "开启读取数据")
            self.assertEqual(window.debug_actions_group.title(), "调试动作")
            self.assertEqual(window.debug_configure_button.text(), "失能后配置单电机")
            self.assertEqual(window.open_manual_button.text(), "打开说明书")
            self.assertEqual(window.clear_log_button.text(), "清空日志")
            window._append_log("hello")  # noqa: SLF001
            self.assertIn("hello", window.log_view.toPlainText())
            window.clear_log_button.click()
            self.assertEqual(window.log_view.toPlainText(), "")

            window.close()
        app.processEvents()

    def test_app_main_exits_when_single_instance_lock_is_held(self) -> None:
        class FakeLock:
            def __init__(self, path) -> None:  # noqa: ANN001 - mimics QLockFile.
                self.path = path
                self.remove_stale_calls = 0

            def setStaleLockTime(self, milliseconds: int) -> None:
                self.milliseconds = milliseconds

            def tryLock(self, timeout: int) -> bool:
                self.timeout = timeout
                return False

            def removeStaleLockFile(self) -> bool:
                self.remove_stale_calls += 1
                return False

            def unlock(self) -> None:
                return None

        with (
            patch("mit_sender.app.QLockFile", FakeLock),
            patch("mit_sender.app.MitSenderWindow") as window_class,
            patch("mit_sender.app.QMessageBox.warning") as warning,
        ):
            result = main()

        self.assertEqual(result, 1)
        window_class.assert_not_called()
        warning.assert_called_once()

    def test_app_lock_retries_after_removing_stale_lock(self) -> None:
        class FakeLock:
            def __init__(self) -> None:
                self.attempts = 0
                self.remove_stale_calls = 0

            def tryLock(self, timeout: int) -> bool:
                self.attempts += 1
                return self.attempts >= 2

            def removeStaleLockFile(self) -> bool:
                self.remove_stale_calls += 1
                return True

        lock = FakeLock()

        self.assertTrue(_try_lock_once(lock))
        self.assertEqual(lock.attempts, 2)
        self.assertEqual(lock.remove_stale_calls, 1)

    def test_app_main_handles_keyboard_interrupt(self) -> None:
        window = _FakeWindow()

        class FakeLock:
            def __init__(self, path) -> None:  # noqa: ANN001 - mimics QLockFile.
                self.path = path
                self.unlocked = False

            def setStaleLockTime(self, milliseconds: int) -> None:
                self.milliseconds = milliseconds

            def tryLock(self, timeout: int) -> bool:
                self.timeout = timeout
                return True

            def removeStaleLockFile(self) -> bool:
                return False

            def unlock(self) -> None:
                self.unlocked = True

        with (
            patch("mit_sender.app.QLockFile", FakeLock),
            patch("mit_sender.app.create_app", return_value=_FakeApp(KeyboardInterrupt())),
            patch("mit_sender.app.MitSenderWindow", return_value=window),
        ):
            result = main()

        self.assertEqual(result, 130)
        self.assertTrue(window.shown)
        self.assertTrue(window.closed)

    def test_feedback_specs_follow_current_tab(self) -> None:
        app = create_app()
        with tempfile.TemporaryDirectory() as directory:
            settings = SettingsStore(QSettings(str(Path(directory) / "settings.ini"), QSettings.IniFormat))
            window = MitSenderWindow(settings)
            captured_specs = []
            window._start_feedback_monitor = lambda specs=None: captured_specs.append(specs)  # noqa: SLF001
            window._start_single_motor_discovery = lambda specs=None: captured_specs.append(specs)  # noqa: SLF001

            for checkbox in window.checkboxes.values():
                checkbox.setChecked(False)
            window.checkboxes[1].setChecked(True)
            window.checkboxes[3].setChecked(True)
            window.tabs.setCurrentIndex(0)
            window._start_current_tab_feedback_monitor()  # noqa: SLF001
            self.assertEqual([spec.mst_id for spec in captured_specs[-1]], [0x11, 0x13])

            window.tabs.setCurrentIndex(1)
            window.debug_motor_combo.setCurrentIndex(window.debug_motor_combo.findData(4))
            window.debug_new_can_input.setText("0x03")
            window.debug_new_mst_input.setText("0x21")
            window._start_current_tab_feedback_monitor()  # noqa: SLF001
            self.assertEqual([(spec.can_id, spec.mst_id) for spec in captured_specs[-1]], [(0x04, 0x14)])

            window.close()
        app.processEvents()

    def test_single_motor_read_button_starts_discovery_without_feedback_dialog(self) -> None:
        app = create_app()
        with tempfile.TemporaryDirectory() as directory:
            settings = SettingsStore(QSettings(str(Path(directory) / "settings.ini"), QSettings.IniFormat))
            window = MitSenderWindow(settings)
            captured_specs = []
            window._start_single_motor_discovery = lambda specs=None: captured_specs.append(specs)  # noqa: SLF001

            window.debug_motor_combo.setCurrentIndex(window.debug_motor_combo.findData(4))
            window.debug_new_can_input.setText("0x05")
            window.debug_new_mst_input.setText("0x25")
            window.debug_read_button.click()

            self.assertEqual(len(captured_specs), 1)
            self.assertEqual([(spec.can_id, spec.mst_id) for spec in captured_specs[0]], [(0x04, 0x14)])
            self.assertIsNone(window.feedback_dialog)

            window.close()
        app.processEvents()

    def test_single_motor_read_ignores_targets_until_id_change_succeeds(self) -> None:
        app = create_app()
        with tempfile.TemporaryDirectory() as directory:
            settings = SettingsStore(QSettings(str(Path(directory) / "settings.ini"), QSettings.IniFormat))
            window = MitSenderWindow(settings)

            window.debug_current_can_input.setText("0x001")
            window.debug_new_can_input.setText("0x020")
            window.debug_new_mst_input.setText("0x030")

            specs = window._debug_discovery_specs()  # noqa: SLF001

            self.assertEqual([(spec.can_id, spec.mst_id) for spec in specs], [(0x01, 0x11)])
            window._apply_successful_id_change()  # noqa: SLF001
            self.assertEqual(
                [(spec.can_id, spec.mst_id) for spec in window._debug_discovery_specs()],  # noqa: SLF001
                [(0x20, 0x30)],
            )

            window.close()
        app.processEvents()

    def test_set_id_completion_updates_current_id_and_stops_stale_feedback(self) -> None:
        app = create_app()
        with tempfile.TemporaryDirectory() as directory:
            settings = SettingsStore(QSettings(str(Path(directory) / "settings.ini"), QSettings.IniFormat))
            window = MitSenderWindow(settings)
            stop_calls = []

            class FakeFeedbackWorker:
                def stop(self) -> None:
                    stop_calls.append(True)

            window.feedback_worker = FakeFeedbackWorker()  # type: ignore[assignment]
            window.feedback_thread = object()  # type: ignore[assignment]
            window.debug_current_can_input.setText("0x001")
            window.debug_new_can_input.setText("0x020")
            window.debug_new_mst_input.setText("0x030")

            window._finish_action("debug_set_ids", [], True, "ok")  # noqa: SLF001

            self.assertEqual(stop_calls, [True])
            self.assertEqual(window.debug_current_can_input.text(), "0x020")
            self.assertEqual(window.debug_feedback_status_label.text(), "操作完成，点击开启读取数据")

            window.feedback_thread = None
            window.feedback_worker = None
            window.close()
        app.processEvents()

    def test_single_motor_discovery_worker_uses_active_probe(self) -> None:
        app = create_app()
        with tempfile.TemporaryDirectory() as directory:
            settings = SettingsStore(QSettings(str(Path(directory) / "settings.ini"), QSettings.IniFormat))
            window = MitSenderWindow(settings)
            worker_calls = []

            class FakeThread:
                def __init__(self, parent=None) -> None:  # noqa: ANN001 - mimics QThread constructor.
                    self.parent = parent
                    self.started = _FakeSignal()
                    self.finished = _FakeSignal()

                def start(self) -> None:
                    return None

                def quit(self) -> None:
                    return None

                def wait(self, timeout=None) -> bool:  # noqa: ANN001 - mimics QThread.
                    return True

                def deleteLater(self) -> None:
                    return None

            class FakeWorker:
                def __init__(self, *args, **kwargs) -> None:
                    worker_calls.append((args, kwargs))
                    self.frame_received = _FakeSignal()
                    self.status_changed = _FakeSignal()
                    self.failed = _FakeSignal()
                    self.finished = _FakeSignal()

                def moveToThread(self, thread) -> None:  # noqa: ANN001 - mimics QObject.
                    return None

                def run(self) -> None:
                    return None

                def stop(self) -> None:
                    return None

                def deleteLater(self) -> None:
                    return None

            with (
                patch("mit_sender.ui.QThread", FakeThread),
                patch("mit_sender.ui.FeedbackMonitorWorker", FakeWorker),
            ):
                window._start_single_motor_discovery()  # noqa: SLF001

            self.assertEqual(len(worker_calls), 1)
            self.assertTrue(worker_calls[0][1]["lock_first_feedback_id"])
            self.assertTrue(worker_calls[0][1]["active_probe"])
            self.assertEqual([spec.mst_id for spec in worker_calls[0][0][1]], [0x11])
            self.assertIsNone(window.feedback_dialog)

            window.close()
        app.processEvents()

    def test_single_motor_feedback_locks_detected_motor_and_preserves_targets(self) -> None:
        app = create_app()
        with tempfile.TemporaryDirectory() as directory:
            settings = SettingsStore(QSettings(str(Path(directory) / "settings.ini"), QSettings.IniFormat))
            window = MitSenderWindow(settings)
            window.debug_motor_combo.setCurrentIndex(window.debug_motor_combo.findData(3))
            window.debug_new_can_input.setText("0x05")
            window.debug_new_mst_input.setText("0x25")
            feedback = MotorFeedback(
                motor_id=3,
                can_id=0x13,
                state=1,
                controller_id=3,
                position=1.25,
                velocity=-0.5,
                torque=0.75,
                mos_temperature=38.0,
                rotor_temperature=41.0,
            )

            window._single_motor_discovery_active = True  # noqa: SLF001
            window._handle_feedback_frame(feedback, 0.125, 1)  # noqa: SLF001

            self.assertEqual(window.debug_current_can_input.text(), "0x003")
            self.assertEqual(window.debug_detected_can_label.text(), "0x003")
            self.assertEqual(window.debug_detected_feedback_label.text(), "0x013")
            self.assertEqual(window.debug_detected_state_label.text(), "1")
            self.assertEqual(window.debug_new_can_input.text(), "0x05")
            self.assertEqual(window.debug_new_mst_input.text(), "0x25")
            self.assertIn("+1.250000", window.debug_feedback_position_label.text())

            window.close()
        app.processEvents()

    def test_single_motor_feedback_uses_mapped_can_id_for_high_target_id(self) -> None:
        app = create_app()
        with tempfile.TemporaryDirectory() as directory:
            settings = SettingsStore(QSettings(str(Path(directory) / "settings.ini"), QSettings.IniFormat))
            window = MitSenderWindow(settings)
            window.debug_current_can_input.setText("0x020")
            window.debug_current_mst_input.setText("0x030")
            window.feedback_motor_specs = [
                MotorSpec(
                    motor_id=1,
                    can_id=0x20,
                    mst_id=0x30,
                    motor_type=window.motor_specs[0].motor_type,
                )
            ]
            feedback = MotorFeedback(
                motor_id=1,
                can_id=0x30,
                state=1,
                controller_id=0,
                position=0.0,
                velocity=0.0,
                torque=0.0,
                mos_temperature=38.0,
                rotor_temperature=41.0,
            )

            window._single_motor_discovery_active = True  # noqa: SLF001
            window._handle_feedback_frame(feedback, 0.125, 1)  # noqa: SLF001

            self.assertEqual(window.debug_current_can_input.text(), "0x020")
            self.assertEqual(window.debug_detected_can_label.text(), "0x020")
            self.assertEqual(window.debug_detected_feedback_label.text(), "0x030")
            self.assertIn("CAN 0x020 / 反馈 0x030", window.debug_feedback_status_label.text())

            window.close()
        app.processEvents()

    def test_feedback_specs_require_selected_motor_on_batch_tab(self) -> None:
        app = create_app()
        with tempfile.TemporaryDirectory() as directory:
            settings = SettingsStore(QSettings(str(Path(directory) / "settings.ini"), QSettings.IniFormat))
            window = MitSenderWindow(settings)
            for checkbox in window.checkboxes.values():
                checkbox.setChecked(False)
            window.tabs.setCurrentIndex(0)

            with self.assertRaises(ValueError):
                window._feedback_specs_for_current_tab()  # noqa: SLF001

            window.close()
        app.processEvents()

    def test_debug_action_completion_does_not_auto_start_feedback(self) -> None:
        app = create_app()
        with tempfile.TemporaryDirectory() as directory:
            settings = SettingsStore(QSettings(str(Path(directory) / "settings.ini"), QSettings.IniFormat))
            window = MitSenderWindow(settings)
            captured_specs = []
            window._start_feedback_monitor = lambda specs=None: captured_specs.append(specs)  # noqa: SLF001

            window.debug_new_can_input.setText("0x03")
            window.debug_new_mst_input.setText("0x21")
            window._finish_action("debug_configure", [], True, "ok")

            self.assertEqual(captured_specs, [])
            self.assertEqual(window.debug_feedback_status_label.text(), "操作完成，点击开启读取数据")

            window.close()
        app.processEvents()

    def test_action_completion_runs_on_gui_thread(self) -> None:
        app = create_app()
        with tempfile.TemporaryDirectory() as directory:
            settings = SettingsStore(QSettings(str(Path(directory) / "settings.ini"), QSettings.IniFormat))
            window = MitSenderWindow(settings)
            main_thread_id = threading.get_ident()
            worker_thread_ids = []
            finish_thread_ids = []

            class FakeActionWorker(QObject):
                finished = Signal(bool, str)

                def __init__(self, *args, **kwargs) -> None:
                    super().__init__()

                def run(self) -> None:
                    worker_thread_ids.append(threading.get_ident())
                    self.finished.emit(True, "ok")

            def record_finish(action, motor_ids, ok, message):  # noqa: ANN001, PLR0913 - mirrors slot under test.
                finish_thread_ids.append(threading.get_ident())

            window._finish_action = record_finish  # noqa: SLF001

            with patch("mit_sender.ui.MotorActionWorker", FakeActionWorker):
                window._start_action("debug_zero")  # noqa: SLF001
                for _ in range(200):
                    app.processEvents()
                    if finish_thread_ids and window.worker_thread is None:
                        break

            self.assertEqual(finish_thread_ids, [main_thread_id])
            self.assertEqual(len(worker_thread_ids), 1)
            self.assertNotEqual(worker_thread_ids[0], main_thread_id)

            window.close()
        app.processEvents()

    def test_debug_feedback_id_auto_updates_until_user_overrides(self) -> None:
        app = create_app()
        with tempfile.TemporaryDirectory() as directory:
            settings = SettingsStore(QSettings(str(Path(directory) / "settings.ini"), QSettings.IniFormat))
            window = MitSenderWindow(settings)

            window.debug_new_can_input.setText("0x03")
            self.assertEqual(window.debug_new_mst_input.text(), "0x013")
            self.assertEqual(window.debug_effective_feedback_label.text(), "0x013")

            window.debug_new_mst_input.setText("0x21")
            window.debug_new_can_input.setText("0x04")
            self.assertEqual(window.debug_new_mst_input.text(), "0x21")
            self.assertEqual(window.debug_effective_feedback_label.text(), "0x021")

            window.close()
        app.processEvents()

    def test_all_seven_presets_select_their_own_address_feedback_and_motor_type(self) -> None:
        app = create_app()
        with tempfile.TemporaryDirectory() as directory:
            store = SettingsStore(QSettings(str(Path(directory) / "settings.ini"), QSettings.IniFormat))
            window = MitSenderWindow(store)
            for expected in window.motor_specs:
                with self.subTest(motor=expected.motor_id):
                    window.debug_motor_combo.setCurrentIndex(window.debug_motor_combo.findData(expected.can_id))
                    self.assertEqual(window._debug_discovery_specs(), [expected])  # noqa: SLF001
                    self.assertEqual(window._debug_command().current_can_id, expected.can_id)  # noqa: SLF001
                    self.assertEqual(window._debug_command().feedback_id, expected.mst_id)  # noqa: SLF001
            window.close()
        app.processEvents()

    def test_other_motor_feedback_cannot_replace_the_selected_motor(self) -> None:
        app = create_app()
        with tempfile.TemporaryDirectory() as directory:
            store = SettingsStore(QSettings(str(Path(directory) / "settings.ini"), QSettings.IniFormat))
            window = MitSenderWindow(store)
            window.debug_motor_combo.setCurrentIndex(window.debug_motor_combo.findData(4))
            window.feedback_motor_specs = window._debug_discovery_specs()  # noqa: SLF001
            window._single_motor_discovery_active = True  # noqa: SLF001
            other = MotorFeedback(1, 0x11, 1, 1, 9.0, 0.0, 0.0, 30.0, 30.0)
            selected = MotorFeedback(4, 0x14, 1, 4, 0.25, 0.0, 0.0, 30.0, 30.0)
            window._handle_feedback_frame(other, 0.1, 1)  # noqa: SLF001
            self.assertEqual(window.debug_detected_can_label.text(), "-")
            self.assertEqual(window.debug_feedback_position_label.text(), "-")
            window._handle_feedback_frame(selected, 0.2, 2)  # noqa: SLF001
            window._handle_feedback_frame(other, 0.3, 3)  # noqa: SLF001
            self.assertEqual(window.debug_current_can_input.text(), "0x004")
            self.assertEqual(window.debug_detected_can_label.text(), "0x004")
            self.assertEqual(window.debug_feedback_position_label.text(), "+0.250000")
            window.close()
        app.processEvents()

    def test_changing_motor_stops_reading_and_discards_queued_old_feedback(self) -> None:
        app = create_app()
        with tempfile.TemporaryDirectory() as directory:
            store = SettingsStore(QSettings(str(Path(directory) / "settings.ini"), QSettings.IniFormat))
            window = MitSenderWindow(store)
            stops = []

            class FakeWorker:
                def stop(self):
                    stops.append(True)

            window.feedback_worker = FakeWorker()
            window.feedback_thread = object()
            window._single_motor_discovery_active = True  # noqa: SLF001
            window.debug_motor_combo.setCurrentIndex(window.debug_motor_combo.findData(4))
            self.assertEqual(stops, [True])
            self.assertTrue(window._feedback_stopping)  # noqa: SLF001
            stale = MotorFeedback(1, 0x11, 1, 1, 9.0, 0.0, 0.0, 30.0, 30.0)
            window._handle_feedback_frame(stale, 0.2, 1)  # noqa: SLF001
            self.assertEqual(window.debug_feedback_position_label.text(), "-")
            self.assertEqual(window.debug_current_can_input.text(), "0x004")
            window._clear_feedback_worker()  # noqa: SLF001
            self.assertTrue(window.debug_read_button.isEnabled())
            self.assertFalse(window.debug_stop_button.isEnabled())
            window.close()
        app.processEvents()

    def test_zero_and_disable_wait_for_reader_to_stop_before_sending(self) -> None:
        app = create_app()
        with tempfile.TemporaryDirectory() as directory:
            for action in ("debug_zero", "debug_disable"):
                with self.subTest(action=action):
                    store = SettingsStore(QSettings(str(Path(directory) / f"{action}.ini"), QSettings.IniFormat))
                    window = MitSenderWindow(store)
                    window.debug_motor_combo.setCurrentIndex(window.debug_motor_combo.findData(4))
                    stops = []

                    class FakeWorker:
                        def stop(self):
                            stops.append(True)

                    window.feedback_worker = FakeWorker()
                    window.feedback_thread = object()
                    with patch.object(window, "_launch_action") as launch:
                        window._start_action(action)  # noqa: SLF001
                        launch.assert_not_called()
                        self.assertEqual(stops, [True])
                        self.assertFalse(window.debug_motor_combo.isEnabled())
                        self.assertFalse(window.debug_zero_button.isEnabled())
                        window._clear_feedback_worker()  # noqa: SLF001
                        launch.assert_called_once()
                        self.assertEqual(launch.call_args.args[0], action)
                        self.assertEqual(launch.call_args.args[3].current_can_id, 4)
                    window.close()
        app.processEvents()

    def test_current_feedback_id_is_independent_of_target_and_survives_restart(self) -> None:
        app = create_app()
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / "settings.ini")
            window = MitSenderWindow(SettingsStore(QSettings(path, QSettings.IniFormat)))
            window.debug_current_can_input.setText("0x004")
            window.debug_current_mst_input.setText("0x024")
            window.debug_new_can_input.setText("0x005")
            window.debug_new_mst_input.setText("0x035")
            self.assertEqual([(s.can_id, s.mst_id) for s in window._debug_discovery_specs()], [(4, 0x24)])  # noqa: SLF001
            window.close()
            restored = MitSenderWindow(SettingsStore(QSettings(path, QSettings.IniFormat)))
            self.assertEqual(restored.debug_motor_combo.currentData(), 4)
            self.assertEqual(restored.debug_current_mst_input.text(), "0x024")
            self.assertEqual(restored.debug_new_mst_input.text(), "0x035")
            restored.close()
        app.processEvents()

    def test_real_reader_thread_finishes_before_selected_disable_command_starts(self) -> None:
        app = create_app()
        events = []

        class FakeLogger:
            def log_feedback(self, feedback, elapsed):
                return 1

            def close(self):
                events.append("logger_closed")

        class ReaderTransport:
            def __init__(self):
                self.first = True

            def send(self, can_id, payload):
                events.append(("probe", can_id))

            def recv(self, timeout):
                if self.first:
                    self.first = False
                    return 0x14, bytes([0x14, 0x7F, 0xFF, 0x7F, 0xF7, 0xFF, 30, 30])
                time.sleep(0.001)
                return None

            def close(self):
                events.append("reader_closed")

        class ActionTransport:
            def send(self, can_id, payload):
                events.append(("disable", can_id, payload[-1]))

            def close(self):
                events.append("action_closed")

        def wait_until(predicate):
            deadline = time.monotonic() + 5.0
            while not predicate() and time.monotonic() < deadline:
                app.processEvents()
                time.sleep(0.001)
            self.assertTrue(predicate(), events)

        with (
            tempfile.TemporaryDirectory() as directory,
            patch("mit_sender.workers.ensure_or_configure_interface"),
            patch("mit_sender.workers.SocketCanTransport", side_effect=[ReaderTransport(), ActionTransport()]),
            patch("mit_sender.workers.FeedbackRerunLogger", return_value=FakeLogger()),
        ):
            store = SettingsStore(QSettings(str(Path(directory) / "settings.ini"), QSettings.IniFormat))
            window = MitSenderWindow(store)
            try:
                window.debug_motor_combo.setCurrentIndex(window.debug_motor_combo.findData(4))
                window.debug_read_button.click()
                wait_until(lambda: window.debug_detected_can_label.text() == "0x004")
                window.debug_disable_button.click()
                wait_until(lambda: window.feedback_thread is None and window.worker_thread is None)
                disables = [event for event in events if isinstance(event, tuple) and event[0] == "disable"]
                self.assertTrue(disables)
                self.assertTrue(all(event[1:] == (4, 0xFD) for event in disables))
                self.assertLess(events.index("reader_closed"), events.index(disables[0]))
                self.assertLess(events.index("logger_closed"), events.index(disables[0]))
                self.assertTrue(window.debug_read_button.isEnabled())
            finally:
                window.close()
                app.processEvents()


if __name__ == "__main__":
    unittest.main()
