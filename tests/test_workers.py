from __future__ import annotations

import unittest
from unittest.mock import patch

from mit_sender.commands import SingleMotorDebugCommand, TransportSettings
from mit_sender.damiao import (
    CONTROL_REPEAT, DISABLE_CMD, DM_Motor_Type, ENABLE_CMD, MotorSpec,
    build_control_cmd_frame, build_mit_frame, build_zero_position_frame, default_motor_specs,
)
from mit_sender.workers import CanSetupWorker, FeedbackMonitorWorker, MotorActionWorker, ensure_or_configure_interface


class FakeRecvTransport:
    def __init__(self, packets):
        self.packets = list(packets)
        self.sent: list[tuple[int, bytes]] = []
        self.closed = False

    def send(self, can_id: int, payload: bytes) -> None:
        self.sent.append((int(can_id), bytes(payload)))

    def recv(self, timeout=0.0):  # noqa: ANN001 - mimics SocketCanTransport.
        if self.packets:
            return self.packets.pop(0)
        raise StopIteration

    def close(self) -> None:
        self.closed = True


class FakeFeedbackLogger:
    def __init__(self) -> None:
        self.feedback_ids: list[int] = []
        self.closed = False

    def log_feedback(self, feedback, elapsed_seconds):  # noqa: ANN001 - worker accepts logger protocol.
        self.feedback_ids.append(feedback.can_id)
        return len(self.feedback_ids)

    def close(self) -> None:
        self.closed = True


class WorkerTests(unittest.TestCase):
    def test_can_setup_worker_accepts_already_up_interface_without_reconfiguring(self) -> None:
        worker = CanSetupWorker(TransportSettings("can0", 1_000_000, 5_000_000, False))
        results = []
        worker.finished.connect(lambda ok, message: results.append((ok, message)))

        with (
            patch("mit_sender.workers.get_can_interface_state", return_value="up"),
            patch("mit_sender.workers.configure_can_interface") as configure,
            patch("mit_sender.workers.ensure_interface_ready") as ensure_ready,
        ):
            worker.run()

        self.assertEqual(results, [(True, "can0 已经启动。")])
        configure.assert_not_called()
        ensure_ready.assert_not_called()

    def test_can_setup_worker_reports_missing_interface_with_available_interfaces(self) -> None:
        worker = CanSetupWorker(TransportSettings("can2", 1_000_000, 5_000_000, False))
        results = []
        worker.finished.connect(lambda ok, message: results.append((ok, message)))
        error = RuntimeError(
            "CAN interface can2 does not exist\n"
            "检测到: can0, can1\n"
            "可手动执行以下三行命令:\n"
            "sudo ip link set can2 down\n"
            "sudo ip link set can2 type can bitrate 1000000 dbitrate 5000000 fd on\n"
            "sudo ip link set can2 up"
        )

        with (
            patch("mit_sender.workers.get_can_interface_state", return_value=None),
            patch("mit_sender.workers.ensure_interface_ready", side_effect=error),
        ):
            worker.run()

        self.assertEqual(results[0][0], False)
        self.assertIn("检测到: can0, can1", results[0][1])
        self.assertIn("sudo ip link set can2 down", results[0][1])

    def test_ensure_or_configure_interface_skips_config_when_interface_is_up(self) -> None:
        settings = TransportSettings("can0", 1_000_000, 5_000_000, True)

        with (
            patch("mit_sender.workers.get_can_interface_state", return_value="up"),
            patch("mit_sender.workers.configure_can_interface") as configure,
            patch("mit_sender.workers.ensure_interface_ready") as ensure_ready,
        ):
            ensure_or_configure_interface(settings)

        configure.assert_not_called()
        ensure_ready.assert_not_called()

    def test_feedback_worker_locks_first_feedback_id_when_requested(self) -> None:
        motor_specs = [
            MotorSpec(motor_id=3, can_id=0x03, mst_id=0x13, motor_type=DM_Motor_Type.DM8009),
            MotorSpec(motor_id=4, can_id=0x04, mst_id=0x14, motor_type=DM_Motor_Type.DM8009),
        ]
        packets = [
            (0x13, bytes([0x13, 0x7F, 0xFF, 0x7F, 0xF7, 0xFF, 40, 50])),
            (0x14, bytes([0x14, 0x7F, 0xFF, 0x7F, 0xF7, 0xFF, 41, 51])),
        ]
        transport = FakeRecvTransport(packets)
        logger = FakeFeedbackLogger()
        worker = FeedbackMonitorWorker(
            TransportSettings("can0", 1_000_000, 5_000_000, False),
            motor_specs,
            logger,
            lock_first_feedback_id=True,
        )

        with (
            patch("mit_sender.workers.ensure_or_configure_interface"),
            patch("mit_sender.workers.SocketCanTransport", return_value=transport),
        ):
            worker.run()

        self.assertEqual(logger.feedback_ids, [0x13])
        self.assertTrue(logger.closed)
        self.assertTrue(transport.closed)

    def test_feedback_worker_actively_probes_and_keeps_zero_mit_after_lock(self) -> None:
        motor_specs = [
            MotorSpec(motor_id=1, can_id=0x01, mst_id=0x11, motor_type=DM_Motor_Type.DM8009),
            MotorSpec(motor_id=2, can_id=0x02, mst_id=0x12, motor_type=DM_Motor_Type.DM8009),
        ]
        packets = [
            None,
            (0x12, bytes([0x12, 0x7F, 0xFF, 0x7F, 0xF7, 0xFF, 40, 50])),
            None,
        ]
        transport = FakeRecvTransport(packets)
        logger = FakeFeedbackLogger()
        worker = FeedbackMonitorWorker(
            TransportSettings("can0", 1_000_000, 5_000_000, False),
            motor_specs,
            logger,
            lock_first_feedback_id=True,
            active_probe=True,
            probe_interval_seconds=0.0,
        )

        with (
            patch("mit_sender.workers.ensure_or_configure_interface"),
            patch("mit_sender.workers.SocketCanTransport", return_value=transport),
        ):
            worker.run()

        self.assertIn(build_control_cmd_frame(0x01, ENABLE_CMD), transport.sent)
        self.assertIn(build_control_cmd_frame(0x02, ENABLE_CMD), transport.sent)
        self.assertIn(build_mit_frame(0x01, DM_Motor_Type.DM8009, 0.0, 0.0, 0.0, 0.0, 0.0), transport.sent)
        self.assertGreaterEqual(
            transport.sent.count(build_mit_frame(0x02, DM_Motor_Type.DM8009, 0.0, 0.0, 0.0, 0.0, 0.0)),
            2,
        )
        self.assertEqual(logger.feedback_ids, [0x12])

    def test_feedback_worker_keeps_probing_full_can_id_after_high_id_lock(self) -> None:
        motor_specs = [
            MotorSpec(motor_id=1, can_id=0x20, mst_id=0x30, motor_type=DM_Motor_Type.DM8009),
        ]
        packets = [
            None,
            (0x30, bytes([0x10, 0x7F, 0xFF, 0x7F, 0xF7, 0xFF, 40, 50])),
            None,
        ]
        transport = FakeRecvTransport(packets)
        logger = FakeFeedbackLogger()
        worker = FeedbackMonitorWorker(
            TransportSettings("can0", 1_000_000, 5_000_000, False),
            motor_specs,
            logger,
            lock_first_feedback_id=True,
            active_probe=True,
            probe_interval_seconds=0.0,
        )

        with (
            patch("mit_sender.workers.ensure_or_configure_interface"),
            patch("mit_sender.workers.SocketCanTransport", return_value=transport),
        ):
            worker.run()

        zero_mit_frame = build_mit_frame(0x20, DM_Motor_Type.DM8009, 0.0, 0.0, 0.0, 0.0, 0.0)
        self.assertGreaterEqual(transport.sent.count(zero_mit_frame), 3)
        self.assertEqual(logger.feedback_ids, [0x30])

    def test_feedback_worker_creates_rerun_logger_inside_worker_thread(self) -> None:
        motor_specs = [
            MotorSpec(motor_id=3, can_id=0x03, mst_id=0x13, motor_type=DM_Motor_Type.DM8009),
        ]
        packets = [
            (0x13, bytes([0x13, 0x7F, 0xFF, 0x7F, 0xF7, 0xFF, 40, 50])),
        ]
        transport = FakeRecvTransport(packets)
        created_specs = []

        def logger_factory(specs):  # noqa: ANN001 - worker accepts logger factory protocol.
            created_specs.append(list(specs))
            return FakeFeedbackLogger()

        worker = FeedbackMonitorWorker(
            TransportSettings("can0", 1_000_000, 5_000_000, False),
            motor_specs,
            logger_factory=logger_factory,
        )

        with (
            patch("mit_sender.workers.ensure_or_configure_interface"),
            patch("mit_sender.workers.SocketCanTransport", return_value=transport),
        ):
            worker.run()

        self.assertEqual([[spec.mst_id for spec in specs] for specs in created_specs], [[0x13]])
        self.assertIsNotNone(worker.logger)

    def test_selected_motor_receives_all_probes_while_other_bus_feedback_is_ignored(self) -> None:
        for selected in default_motor_specs():
            with self.subTest(motor=selected.motor_id):
                packets = [
                    (spec.mst_id, bytes([0x10 | spec.motor_id, 0x7F, 0xFF, 0x7F, 0xF7, 0xFF, 40, 50]))
                    for spec in default_motor_specs()
                ]
                transport = FakeRecvTransport(packets)
                logger = FakeFeedbackLogger()
                worker = FeedbackMonitorWorker(
                    TransportSettings("can5", 1_000_000, 5_000_000, False),
                    [selected], logger, active_probe=True, lock_first_feedback_id=True,
                    probe_interval_seconds=0.0,
                )
                with (
                    patch("mit_sender.workers.ensure_or_configure_interface"),
                    patch("mit_sender.workers.SocketCanTransport", return_value=transport),
                ):
                    worker.run()
                self.assertEqual(logger.feedback_ids, [selected.mst_id])
                self.assertEqual({can_id for can_id, _payload in transport.sent}, {selected.can_id})

    def test_zero_and_disable_only_address_the_selected_motor_on_the_selected_bus(self) -> None:
        for spec in default_motor_specs():
            for action in ("debug_zero", "debug_disable"):
                with self.subTest(motor=spec.motor_id, action=action):
                    transport = FakeRecvTransport([])
                    # Deliberately leave a different target ID: it must never select the motor for these actions.
                    command = SingleMotorDebugCommand(spec.can_id, 0, 0x20, 0x30, spec.mst_id)
                    worker = MotorActionWorker(
                        action, TransportSettings("can5", 1_000_000, 5_000_000, False), [], command,
                    )
                    results = []
                    worker.finished.connect(lambda ok, message: results.append(ok))
                    with (
                        patch("mit_sender.workers.ensure_or_configure_interface"),
                        patch("mit_sender.workers.SocketCanTransport", return_value=transport) as transport_factory,
                        patch("mit_sender.damiao.time.sleep"),
                    ):
                        worker.run()
                    transport_factory.assert_called_once_with("can5")
                    expected = (
                        [build_zero_position_frame(spec.can_id)] if action == "debug_zero"
                        else [build_control_cmd_frame(spec.can_id, DISABLE_CMD)] * CONTROL_REPEAT
                    )
                    self.assertEqual(transport.sent, expected)
                    self.assertEqual(results, [True])
                    self.assertTrue(transport.closed)


if __name__ == "__main__":
    unittest.main()
