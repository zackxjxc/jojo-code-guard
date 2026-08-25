"""插件目录同步事务的并发、恢复与清理安全回归测试。"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import sync_claude_plugin  # noqa: E402
import sync_codex_plugin  # noqa: E402


SYNCHRONIZERS = (sync_claude_plugin, sync_codex_plugin)


class SyncTransactionSafetyTests(unittest.TestCase):
    """验证同步事务绝不删除或覆盖身份不明的并发目录项。"""

    def test_macos_system_aliases_are_distinguished_from_user_links(self) -> None:
        """同步器只能白名单 macOS 固定根目录别名，不能放行 /var 下的用户链接。"""
        for synchronizer in SYNCHRONIZERS:
            with self.subTest(synchronizer=synchronizer.__name__):
                with mock.patch.object(synchronizer.sys, "platform", "darwin"):
                    self.assertTrue(synchronizer._is_macos_system_path_alias(Path("/var")))
                    self.assertTrue(synchronizer._is_macos_system_path_alias(Path("/tmp")))
                    self.assertTrue(synchronizer._is_macos_system_path_alias(Path("/etc")))
                    self.assertFalse(
                        synchronizer._is_macos_system_path_alias(Path("/var/folders"))
                    )

                with mock.patch.object(
                    synchronizer,
                    "_path_is_link_like",
                    return_value=True,
                ), mock.patch.object(
                    synchronizer,
                    "_is_macos_system_path_alias",
                    return_value=True,
                ), mock.patch.object(
                    synchronizer,
                    "_same_identity",
                    return_value=True,
                ):
                    synchronizer._assert_identity(Path("system-alias"), {}, "测试路径")

                with mock.patch.object(
                    synchronizer,
                    "_path_is_link_like",
                    return_value=True,
                ), mock.patch.object(
                    synchronizer,
                    "_is_macos_system_path_alias",
                    return_value=False,
                ):
                    with self.assertRaisesRegex(RuntimeError, "链接/reparse"):
                        synchronizer._assert_identity(Path("user-link"), {}, "测试路径")

    def test_macos_system_alias_survives_ancestor_capture_and_recheck(self) -> None:
        """事务祖先快照与复核都必须放行固定 macOS 系统别名。"""
        for synchronizer in SYNCHRONIZERS:
            with self.subTest(synchronizer=synchronizer.__name__):
                alias = Path("/var")

                def is_var_alias(path: Path) -> bool:
                    return path.as_posix() == "/var"

                with mock.patch.object(synchronizer.sys, "platform", "darwin"), mock.patch.object(
                    synchronizer,
                    "_absolute",
                    return_value=alias,
                ), mock.patch.object(
                    synchronizer,
                    "_path_is_link_like",
                    side_effect=is_var_alias,
                ), mock.patch.object(
                    synchronizer,
                    "_path_exists_without_following",
                    return_value=True,
                ), mock.patch.object(
                    synchronizer,
                    "_path_identity",
                    return_value={"device": 1, "inode": 2, "type": 3},
                ):
                    snapshot = synchronizer._capture_ancestor_snapshot(alias)

                with mock.patch.object(synchronizer.sys, "platform", "darwin"), mock.patch.object(
                    synchronizer,
                    "_path_is_link_like",
                    side_effect=is_var_alias,
                ), mock.patch.object(
                    synchronizer,
                    "_same_identity",
                    return_value=True,
                ):
                    synchronizer._assert_ancestor_snapshot(snapshot)

    def test_owned_tree_cleanup_never_deletes_a_racing_replacement(self) -> None:
        """rmtree 前路径被替换时，外部目录必须继续存在。"""
        for synchronizer in SYNCHRONIZERS:
            with self.subTest(synchronizer=synchronizer.__name__):
                with tempfile.TemporaryDirectory() as directory:
                    root = Path(directory)
                    owned = root / "owned"
                    external = root / "external"
                    preserved = root / "preserved"
                    owned.mkdir()
                    external.mkdir()
                    (owned / "marker.txt").write_text("owned\n", encoding="utf-8")
                    (external / "marker.txt").write_text("external\n", encoding="utf-8")
                    identity = synchronizer._path_identity(owned)
                    parent_snapshot = synchronizer._capture_ancestor_snapshot(root)
                    real_move = synchronizer._move_entry_no_replace
                    raced = False

                    def racing_move(source: Path, destination: Path) -> None:
                        nonlocal raced
                        if source == owned and not raced:
                            os.replace(owned, preserved)
                            os.replace(external, owned)
                            raced = True
                        real_move(source, destination)

                    with mock.patch.object(
                        synchronizer,
                        "_move_entry_no_replace",
                        side_effect=racing_move,
                    ):
                        with self.assertRaises(RuntimeError):
                            synchronizer._remove_owned_tree(
                                owned,
                                identity,
                                parent_snapshot,
                            )

                    self.assertTrue(raced)
                    payloads = [
                        path.read_text(encoding="utf-8")
                        for path in root.rglob("marker.txt")
                    ]
                    self.assertIn("owned\n", payloads)
                    self.assertIn("external\n", payloads)

    def test_owned_tree_double_race_reports_quarantine_path(self) -> None:
        """外部目录无法原位恢复时，异常必须给出其私有隔离位置。"""
        for synchronizer in SYNCHRONIZERS:
            with self.subTest(synchronizer=synchronizer.__name__):
                with tempfile.TemporaryDirectory() as directory:
                    root = Path(directory)
                    owned = root / "owned"
                    external = root / "external"
                    preserved = root / "preserved"
                    owned.mkdir()
                    external.mkdir()
                    (owned / "marker.txt").write_text("owned\n", encoding="utf-8")
                    (external / "marker.txt").write_text("external\n", encoding="utf-8")
                    identity = synchronizer._path_identity(owned)
                    parent_snapshot = synchronizer._capture_ancestor_snapshot(root)
                    real_move = synchronizer._move_entry_no_replace
                    moves = 0

                    def double_racing_move(source: Path, destination: Path) -> None:
                        nonlocal moves
                        moves += 1
                        if moves == 1:
                            os.replace(owned, preserved)
                            os.replace(external, owned)
                        elif moves == 2:
                            owned.mkdir()
                            (owned / "marker.txt").write_text("second\n", encoding="utf-8")
                        real_move(source, destination)

                    with mock.patch.object(
                        synchronizer,
                        "_move_entry_no_replace",
                        side_effect=double_racing_move,
                    ):
                        with self.assertRaises(RuntimeError) as caught:
                            synchronizer._remove_owned_tree(
                                owned,
                                identity,
                                parent_snapshot,
                            )

                    quarantined = list(root.glob(".owned.discard-*/owned"))
                    self.assertEqual(len(quarantined), 1)
                    self.assertEqual(
                        (quarantined[0] / "marker.txt").read_text(encoding="utf-8"),
                        "external\n",
                    )
                    self.assertEqual((owned / "marker.txt").read_text(encoding="utf-8"), "second\n")
                    self.assertIn(str(quarantined[0]), str(caught.exception))

    def test_journal_cleanup_never_deletes_a_racing_replacement(self) -> None:
        """journal 在 unlink 窗口被替换时，外部文件必须继续存在。"""
        for synchronizer in SYNCHRONIZERS:
            with self.subTest(synchronizer=synchronizer.__name__):
                with tempfile.TemporaryDirectory() as directory:
                    root = Path(directory)
                    destination = root / "adapter"
                    journal = synchronizer._journal_path(destination)
                    external = root / "external.txt"
                    preserved = root / "preserved-journal"
                    journal.write_text("{}\n", encoding="utf-8")
                    external.write_text("external\n", encoding="utf-8")
                    record = {
                        "parent_snapshot": synchronizer._capture_ancestor_snapshot(root),
                    }
                    journal_identity = synchronizer._journal_identity(journal)
                    real_move = synchronizer._move_entry_no_replace
                    raced = False

                    def racing_move(source: Path, destination_path: Path) -> None:
                        nonlocal raced
                        if source == journal and not raced:
                            os.replace(journal, preserved)
                            os.replace(external, journal)
                            raced = True
                        real_move(source, destination_path)

                    with mock.patch.object(
                        synchronizer,
                        "_move_entry_no_replace",
                        side_effect=racing_move,
                    ):
                        with self.assertRaises(RuntimeError):
                            synchronizer._remove_journal(
                                destination,
                                record,
                                journal_identity,
                            )

                    self.assertTrue(raced)
                    payloads = [
                        path.read_text(encoding="utf-8")
                        for path in root.iterdir()
                        if path.is_file()
                    ]
                    self.assertIn("{}\n", payloads)
                    self.assertIn("external\n", payloads)

    def test_journal_double_race_reports_quarantine_path(self) -> None:
        """外部 journal 无法原位恢复时，异常必须给出其私有隔离位置。"""
        for synchronizer in SYNCHRONIZERS:
            with self.subTest(synchronizer=synchronizer.__name__):
                with tempfile.TemporaryDirectory() as directory:
                    root = Path(directory)
                    destination = root / "adapter"
                    journal = synchronizer._journal_path(destination)
                    external = root / "external.txt"
                    preserved = root / "preserved-journal"
                    journal.write_text("{}\n", encoding="utf-8")
                    external.write_text("external\n", encoding="utf-8")
                    record = {
                        "parent_snapshot": synchronizer._capture_ancestor_snapshot(root),
                    }
                    journal_identity = synchronizer._journal_identity(journal)
                    real_move = synchronizer._move_entry_no_replace
                    moves = 0

                    def double_racing_move(source: Path, destination_path: Path) -> None:
                        nonlocal moves
                        moves += 1
                        if moves == 1:
                            os.replace(journal, preserved)
                            os.replace(external, journal)
                        elif moves == 2:
                            journal.write_text("second\n", encoding="utf-8")
                        real_move(source, destination_path)

                    with mock.patch.object(
                        synchronizer,
                        "_move_entry_no_replace",
                        side_effect=double_racing_move,
                    ):
                        with self.assertRaises(RuntimeError) as caught:
                            synchronizer._remove_journal(
                                destination,
                                record,
                                journal_identity,
                            )

                    quarantined = list(root.glob(f".{journal.name}.discard-*/owned"))
                    self.assertEqual(len(quarantined), 1)
                    self.assertEqual(quarantined[0].read_text(encoding="utf-8"), "external\n")
                    self.assertEqual(journal.read_text(encoding="utf-8"), "second\n")
                    self.assertIn(str(quarantined[0]), str(caught.exception))

    def test_journal_append_does_not_modify_a_swapped_hardlink_target(self) -> None:
        """append 打开前 journal 被换成硬链接时，不得先写坏外部文件再报错。"""
        for synchronizer in SYNCHRONIZERS:
            with self.subTest(synchronizer=synchronizer.__name__):
                with tempfile.TemporaryDirectory() as directory:
                    root = Path(directory)
                    destination = root / "adapter"
                    journal = synchronizer._journal_path(destination)
                    external = root / "external.txt"
                    external.write_text("external\n", encoding="utf-8")
                    record = {
                        "phase": "prepared",
                        "parent_snapshot": synchronizer._capture_ancestor_snapshot(root),
                    }
                    journal_identity = synchronizer._write_journal_record(
                        journal,
                        record,
                        None,
                        create=True,
                    )
                    preserved = root / "preserved-journal"
                    real_open = synchronizer.os.open
                    raced = False

                    def racing_open(
                        path: object,
                        flags: int,
                        mode: int = 0o777,
                        *args: object,
                        **kwargs: object,
                    ) -> int:
                        nonlocal raced
                        if Path(path) == journal and flags & os.O_APPEND and not raced:
                            raced = True
                            os.replace(journal, preserved)
                            os.link(external, journal)
                        return real_open(path, flags, mode, *args, **kwargs)

                    with mock.patch.object(
                        synchronizer.os,
                        "open",
                        side_effect=racing_open,
                    ):
                        with self.assertRaises(RuntimeError):
                            synchronizer._write_journal_record(
                                journal,
                                dict(record, phase="committed"),
                                journal_identity,
                            )

                    self.assertTrue(raced)
                    self.assertEqual(external.read_text(encoding="utf-8"), "external\n")

    def test_recovery_rejects_a_forged_unmanaged_staging_directory(self) -> None:
        """不可信 journal 不能授权删除仅凭名称和 inode 匹配的用户目录。"""
        for synchronizer in SYNCHRONIZERS:
            with self.subTest(synchronizer=synchronizer.__name__):
                with tempfile.TemporaryDirectory() as directory:
                    root = Path(directory)
                    destination = root / "adapter"
                    staging = root / ".adapter.sync-forged"
                    staging.mkdir()
                    marker = staging / "keep.txt"
                    marker.write_text("keep\n", encoding="utf-8")
                    record = {
                        "format": synchronizer.TRANSACTION_FORMAT,
                        "phase": "prepared",
                        "destination": str(destination),
                        "staging": str(staging),
                        "backup": None,
                        "parent_snapshot": synchronizer._capture_ancestor_snapshot(root),
                        "staging_identity": synchronizer._path_identity(staging),
                        "destination_identity": None,
                    }
                    synchronizer._write_journal_record(
                        synchronizer._journal_path(destination),
                        record,
                        None,
                        create=True,
                    )

                    with self.assertRaises((RuntimeError, FileNotFoundError)):
                        synchronizer._recover_transaction(destination)

                    self.assertEqual(marker.read_text(encoding="utf-8"), "keep\n")
                    self.assertTrue(synchronizer._journal_path(destination).is_file())

    def test_recovery_rejects_a_valid_adapter_with_extra_user_data(self) -> None:
        """看似合法的适配包也不能夹带额外用户文件后取得删除授权。"""
        for synchronizer in SYNCHRONIZERS:
            with self.subTest(synchronizer=synchronizer.__name__):
                with tempfile.TemporaryDirectory() as directory:
                    root = Path(directory)
                    destination = root / "adapter"
                    staging = root / ".adapter.sync-forged"
                    staging.mkdir()
                    synchronizer._build_adapter(ROOT, staging)
                    marker = staging / "USER-DATA.txt"
                    marker.write_text("keep\n", encoding="utf-8")
                    record = {
                        "format": synchronizer.TRANSACTION_FORMAT,
                        "phase": "prepared",
                        "destination": str(destination),
                        "staging": str(staging),
                        "backup": None,
                        "parent_snapshot": synchronizer._capture_ancestor_snapshot(root),
                        "staging_identity": synchronizer._path_identity(staging),
                        "destination_identity": None,
                    }
                    synchronizer._write_journal_record(
                        synchronizer._journal_path(destination),
                        record,
                        None,
                        create=True,
                    )

                    with self.assertRaises(RuntimeError):
                        synchronizer._recover_transaction(destination)

                    self.assertEqual(marker.read_text(encoding="utf-8"), "keep\n")
                    self.assertTrue(synchronizer._journal_path(destination).is_file())

    def test_manifest_name_alone_does_not_authorize_user_data_replacement(self) -> None:
        """同名 manifest 加任意用户文件不能被误认成可覆盖的受管安装。"""
        manifest_paths = {
            sync_claude_plugin: Path(".claude-plugin/plugin.json"),
            sync_codex_plugin: Path(".codex-plugin/plugin.json"),
        }
        for synchronizer in SYNCHRONIZERS:
            with self.subTest(synchronizer=synchronizer.__name__):
                with tempfile.TemporaryDirectory() as directory:
                    destination = Path(directory) / "adapter"
                    relative_manifest = manifest_paths[synchronizer]
                    manifest = destination / relative_manifest
                    manifest.parent.mkdir(parents=True)
                    manifest.write_bytes((ROOT / relative_manifest).read_bytes())
                    user_data = destination / "keep.txt"
                    user_data.write_text("keep\n", encoding="utf-8")

                    with self.assertRaisesRegex(RuntimeError, "受管|用户|额外|摘要|历史|契约"):
                        synchronizer._validate_managed_destination(destination)

                    self.assertEqual(user_data.read_text(encoding="utf-8"), "keep\n")

    def test_legacy_filename_does_not_authorize_arbitrary_contents(self) -> None:
        """一个历史文件名不能让任意内容取得旧包迁移和递归删除权限。"""
        manifest_paths = {
            sync_claude_plugin: Path(".claude-plugin/plugin.json"),
            sync_codex_plugin: Path(".codex-plugin/plugin.json"),
        }
        for synchronizer in SYNCHRONIZERS:
            with self.subTest(synchronizer=synchronizer.__name__):
                with tempfile.TemporaryDirectory() as directory:
                    destination = Path(directory) / "adapter"
                    relative_manifest = manifest_paths[synchronizer]
                    manifest = destination / relative_manifest
                    manifest.parent.mkdir(parents=True)
                    manifest.write_bytes((ROOT / relative_manifest).read_bytes())
                    user_data = destination / "commands" / "commit.md"
                    user_data.parent.mkdir(parents=True)
                    user_data.write_text("USER DATA\n", encoding="utf-8")

                    with self.assertRaisesRegex(RuntimeError, "内容|历史|受管"):
                        synchronizer._validate_managed_destination(destination)

                    self.assertEqual(user_data.read_text(encoding="utf-8"), "USER DATA\n")

    def test_markerless_current_adapter_requires_source_content_contract(self) -> None:
        """去掉 marker 的当前形状若已改写已知文件，也不能取得旧包迁移授权。"""
        for synchronizer in SYNCHRONIZERS:
            with self.subTest(synchronizer=synchronizer.__name__):
                with tempfile.TemporaryDirectory() as directory:
                    destination = Path(directory) / "adapter"
                    synchronizer._build_adapter(ROOT, destination)
                    (destination / synchronizer.OWNERSHIP_MARKER_NAME).unlink()
                    changed = destination / "hooks" / "hooks.json"
                    original = changed.read_bytes()
                    changed.write_bytes(
                        (b"[" if original[:1] != b"[" else b"{") + original[1:]
                    )

                    with self.assertRaisesRegex(RuntimeError, "内容|发布|受管"):
                        synchronizer._validate_managed_destination(destination)

                    self.assertTrue(changed.is_file())

    def test_builder_rejects_file_injected_before_ownership_marker(self) -> None:
        """builder 不能把 marker 写入窗口夹带的未知文件一起签成受管内容。"""
        for synchronizer in SYNCHRONIZERS:
            with self.subTest(synchronizer=synchronizer.__name__):
                with tempfile.TemporaryDirectory() as directory:
                    staging = Path(directory) / "staging"
                    staging.mkdir()
                    real_write_marker = synchronizer._write_ownership_marker
                    injected = staging / "USER-DATA.txt"

                    def inject_before_marker(
                        root: Path,
                        *args: object,
                        **kwargs: object,
                    ) -> None:
                        injected.write_text("keep\n", encoding="utf-8")
                        real_write_marker(root, *args, **kwargs)

                    with mock.patch.object(
                        synchronizer,
                        "_write_ownership_marker",
                        side_effect=inject_before_marker,
                    ):
                        with self.assertRaisesRegex(RuntimeError, "内容|ownership|目录树"):
                            synchronizer._build_adapter(ROOT, staging)

                    self.assertEqual(injected.read_text(encoding="utf-8"), "keep\n")

    def test_builder_never_overwrites_racing_expected_file(self) -> None:
        """空树检查后抢占白名单路径的用户文件必须原样保留。"""
        manifest_paths = {
            sync_claude_plugin: Path(".claude-plugin/plugin.json"),
            sync_codex_plugin: Path(".codex-plugin/plugin.json"),
        }
        for synchronizer in SYNCHRONIZERS:
            with self.subTest(synchronizer=synchronizer.__name__):
                with tempfile.TemporaryDirectory() as directory:
                    staging = Path(directory) / "staging"
                    staging.mkdir()
                    raced_path = staging / manifest_paths[synchronizer]
                    real_open = synchronizer.os.open
                    injected = False

                    def racing_open(
                        path: object,
                        flags: int,
                        mode: int = 0o777,
                        *args: object,
                        **kwargs: object,
                    ) -> int:
                        nonlocal injected
                        if (
                            Path(path) == raced_path
                            and flags & os.O_CREAT
                            and not injected
                        ):
                            injected = True
                            raced_path.write_text("USER DATA\n", encoding="utf-8")
                        return real_open(path, flags, mode, *args, **kwargs)

                    with mock.patch.object(
                        synchronizer.os,
                        "open",
                        side_effect=racing_open,
                    ):
                        with self.assertRaises((FileExistsError, RuntimeError)):
                            synchronizer._build_adapter(ROOT, staging)

                    self.assertTrue(injected)
                    self.assertEqual(raced_path.read_text(encoding="utf-8"), "USER DATA\n")

    def test_copy_tree_never_adopts_destination_created_during_mkdir(self) -> None:
        """根目录 mkdir 竞态失败后不能采信并删除并发创建的外部目录。"""
        for synchronizer in SYNCHRONIZERS:
            with self.subTest(synchronizer=synchronizer.__name__):
                with tempfile.TemporaryDirectory() as directory:
                    root = Path(directory)
                    source = root / "source"
                    destination = root / "destination"
                    source.mkdir()
                    (source / "source.txt").write_text("source\n", encoding="utf-8")
                    real_mkdir = Path.mkdir
                    injected = False

                    def racing_mkdir(
                        path: Path,
                        *args: object,
                        **kwargs: object,
                    ) -> None:
                        nonlocal injected
                        if path == destination and not injected:
                            injected = True
                            real_mkdir(path)
                            (path / "keep.txt").write_text(
                                "external\n",
                                encoding="utf-8",
                            )
                        real_mkdir(path, *args, **kwargs)

                    with mock.patch.object(Path, "mkdir", new=racing_mkdir):
                        with self.assertRaises(FileExistsError):
                            synchronizer._copy_tree(source, destination)

                    self.assertTrue(injected)
                    self.assertEqual(
                        (destination / "keep.txt").read_text(encoding="utf-8"),
                        "external\n",
                    )

    def test_copy_tree_failure_preserves_partial_copy_without_cleanup(self) -> None:
        """源树复核失败时保留 partial staging，不能递归删除并发插入项。"""
        for synchronizer in SYNCHRONIZERS:
            with self.subTest(synchronizer=synchronizer.__name__):
                with tempfile.TemporaryDirectory() as directory:
                    root = Path(directory)
                    source = root / "source"
                    destination = root / "destination"
                    source.mkdir()
                    (source / "source.txt").write_text("source\n", encoding="utf-8")
                    real_snapshot = synchronizer._safe_tree_snapshot
                    source_snapshots = 0
                    injected = False

                    def fail_final_source_snapshot(path: Path) -> dict[str, dict[str, object]]:
                        nonlocal injected, source_snapshots
                        if synchronizer._absolute(path) == synchronizer._absolute(source):
                            source_snapshots += 1
                            if source_snapshots == 2:
                                injected = True
                                (destination / "keep.txt").write_text(
                                    "external\n",
                                    encoding="utf-8",
                                )
                                raise RuntimeError("simulated source verification failure")
                        return real_snapshot(path)

                    with mock.patch.object(
                        synchronizer,
                        "_safe_tree_snapshot",
                        side_effect=fail_final_source_snapshot,
                    ):
                        with self.assertRaisesRegex(RuntimeError, "source verification failure"):
                            synchronizer._copy_tree(source, destination)

                    self.assertTrue(injected)
                    self.assertEqual(
                        (destination / "source.txt").read_text(encoding="utf-8"),
                        "source\n",
                    )
                    self.assertEqual(
                        (destination / "keep.txt").read_text(encoding="utf-8"),
                        "external\n",
                    )

    def test_main_reports_preserved_partial_staging_path(self) -> None:
        """顶层构建失败必须保留无 marker 的 partial staging 并报告精确路径。"""
        environment_names = {
            sync_claude_plugin: "JOJO_CLAUDE_PLUGIN_DIR",
            sync_codex_plugin: "JOJO_CODEX_PLUGIN_DIR",
        }
        for synchronizer in SYNCHRONIZERS:
            with self.subTest(synchronizer=synchronizer.__name__):
                with tempfile.TemporaryDirectory() as directory:
                    root = Path(directory)
                    destination = root / "adapter"

                    def leave_partial_staging(
                        _source_root: Path,
                        staging: Path,
                    ) -> None:
                        (staging / "partial.txt").write_text(
                            "preserve\n",
                            encoding="utf-8",
                        )
                        raise RuntimeError("simulated adapter build failure")

                    with mock.patch.dict(
                        os.environ,
                        {environment_names[synchronizer]: str(destination)},
                    ), mock.patch.object(
                        synchronizer,
                        "_build_adapter",
                        side_effect=leave_partial_staging,
                    ):
                        with self.assertRaisesRegex(
                            RuntimeError,
                            "ownership marker.*保留供人工检查",
                        ) as raised:
                            synchronizer.main()

                    preserved = list(root.glob(".adapter.sync-*"))
                    self.assertEqual(len(preserved), 1)
                    self.assertIn(str(preserved[0]), str(raised.exception))
                    self.assertEqual(
                        (preserved[0] / "partial.txt").read_text(encoding="utf-8"),
                        "preserve\n",
                    )

    def test_builder_fsyncs_generated_tree_before_ownership_marker(self) -> None:
        """所有 staging 文件和目录都必须在 marker/事务提交前完成持久化。"""
        for synchronizer in SYNCHRONIZERS:
            with self.subTest(synchronizer=synchronizer.__name__):
                with tempfile.TemporaryDirectory() as directory:
                    staging = Path(directory) / "staging"
                    staging.mkdir()
                    synced_files: set[Path] = set()
                    synced_directories: set[Path] = set()
                    real_sync_file = getattr(
                        synchronizer,
                        "_fsync_regular_file",
                        lambda _path, _record: None,
                    )
                    real_sync_directory = synchronizer._fsync_directory
                    real_write_marker = synchronizer._write_ownership_marker

                    def record_file(path: Path, record: dict[str, object]) -> None:
                        synced_files.add(path)
                        real_sync_file(path, record)

                    def record_directory(path: Path) -> None:
                        synced_directories.add(path)
                        real_sync_directory(path)

                    def assert_synced_before_marker(
                        root: Path,
                        *args: object,
                        **kwargs: object,
                    ) -> None:
                        snapshot = synchronizer._safe_tree_snapshot(root)
                        expected_files = {
                            root / Path(relative)
                            for relative, record in snapshot.items()
                            if relative != "." and "sha256" in record
                        }
                        expected_directories = {
                            root if relative == "." else root / Path(relative)
                            for relative, record in snapshot.items()
                            if "sha256" not in record
                        }
                        self.assertEqual(synced_files, expected_files)
                        self.assertEqual(synced_directories, expected_directories)
                        real_write_marker(root, *args, **kwargs)

                    with mock.patch.object(
                        synchronizer,
                        "_fsync_regular_file",
                        side_effect=record_file,
                        create=True,
                    ), mock.patch.object(
                        synchronizer,
                        "_fsync_directory",
                        side_effect=record_directory,
                    ), mock.patch.object(
                        synchronizer,
                        "_write_ownership_marker",
                        side_effect=assert_synced_before_marker,
                    ):
                        synchronizer._build_adapter(ROOT, staging)

    def test_replace_rejects_content_change_after_managed_validation(self) -> None:
        """校验后的同 inode、同大小、同 mtime 内容变化也必须阻断目录切换。"""
        for synchronizer in SYNCHRONIZERS:
            with self.subTest(synchronizer=synchronizer.__name__):
                with tempfile.TemporaryDirectory() as directory:
                    root = Path(directory)
                    destination = root / "adapter"
                    staging = root / "staging"
                    synchronizer._build_adapter(ROOT, destination)
                    synchronizer._build_adapter(ROOT, staging)
                    expected_destination = synchronizer._validate_managed_destination(destination)
                    changed = destination / "hooks" / "hooks.json"
                    original = changed.read_bytes()
                    original_stat = changed.stat()
                    replacement = (b"[" if original[:1] != b"[" else b"{") + original[1:]
                    changed.write_bytes(replacement)
                    os.utime(
                        changed,
                        ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns),
                    )

                    with self.assertRaisesRegex(RuntimeError, "内容|目录树|校验后"):
                        synchronizer._replace_directory(
                            staging,
                            destination,
                            expected_destination_identity=expected_destination,
                        )

                    self.assertEqual(changed.read_bytes(), replacement)
                    self.assertTrue(staging.is_dir())
                    self.assertEqual(list(root.glob(".adapter.backup-*")), [])
                    self.assertFalse(synchronizer._journal_path(destination).exists())

    def test_vulnerable_windows_python_rejects_private_directory_creation(self) -> None:
        """Windows 旧补丁版 Python 不能创建声称 owner-only 的事务私有目录。"""
        vulnerable_versions = ((3, 9, 19), (3, 10, 14), (3, 11, 9), (3, 12, 3))
        for synchronizer in SYNCHRONIZERS:
            for version in vulnerable_versions:
                with self.subTest(synchronizer=synchronizer.__name__, version=version):
                    with (
                        mock.patch.object(synchronizer.os, "name", "nt"),
                        mock.patch.object(synchronizer.sys, "version_info", version),
                    ):
                        with self.assertRaisesRegex(RuntimeError, "Python|私有目录|安全"):
                            synchronizer._require_secure_private_directory_support()

    def test_incomplete_first_journal_record_preserves_all_directories(self) -> None:
        """首次 journal 创建后崩溃留下空或截断内容时只报恢复错误，不删除目录。"""
        for synchronizer in SYNCHRONIZERS:
            for payload in (b"", b'{"format":1'):
                with self.subTest(synchronizer=synchronizer.__name__, payload=payload):
                    with tempfile.TemporaryDirectory() as directory:
                        root = Path(directory)
                        destination = root / "adapter"
                        staging = root / ".adapter.sync-preserve"
                        destination.mkdir()
                        staging.mkdir()
                        (destination / "keep.txt").write_text("destination\n", encoding="utf-8")
                        (staging / "keep.txt").write_text("staging\n", encoding="utf-8")
                        journal = synchronizer._journal_path(destination)
                        journal.write_bytes(payload)

                        with self.assertRaisesRegex(RuntimeError, "没有完整阶段"):
                            synchronizer._recover_transaction(destination)

                        self.assertEqual(
                            (destination / "keep.txt").read_text(encoding="utf-8"),
                            "destination\n",
                        )
                        self.assertEqual(
                            (staging / "keep.txt").read_text(encoding="utf-8"),
                            "staging\n",
                        )
                        self.assertEqual(journal.read_bytes(), payload)

    def test_concurrent_empty_destination_is_not_overwritten(self) -> None:
        """最后一次检查后出现的空目录也必须由 no-clobber rename 保留。"""
        for synchronizer in SYNCHRONIZERS:
            with self.subTest(synchronizer=synchronizer.__name__):
                with tempfile.TemporaryDirectory() as directory:
                    root = Path(directory)
                    destination = root / "adapter"
                    staging = root / "staging"
                    destination.mkdir()
                    staging.mkdir()
                    (destination / "version.txt").write_text("old\n", encoding="utf-8")
                    (staging / "version.txt").write_text("new\n", encoding="utf-8")
                    real_guard = synchronizer._transaction_guard
                    real_replace = os.replace
                    backed_up_guard_calls = 0
                    injected = False

                    def racing_guard(
                        guarded_destination: Path,
                        record: dict[str, object],
                        journal_identity: dict[str, int],
                    ) -> None:
                        nonlocal backed_up_guard_calls, injected
                        real_guard(guarded_destination, record, journal_identity)
                        if record.get("phase") == "backed_up" and not destination.exists():
                            backed_up_guard_calls += 1
                            if backed_up_guard_calls == 2:
                                destination.mkdir()
                                injected = True

                    def emulate_replace_of_empty_directory(source: object, target: object) -> None:
                        source_path = Path(source)
                        target_path = Path(target)
                        if source_path == staging and target_path == destination and target_path.is_dir():
                            os.rmdir(target_path)
                        real_replace(source_path, target_path)

                    with (
                        mock.patch.object(
                            synchronizer,
                            "_transaction_guard",
                            side_effect=racing_guard,
                        ),
                        mock.patch.object(
                            synchronizer.os,
                            "replace",
                            side_effect=emulate_replace_of_empty_directory,
                        ),
                    ):
                        with self.assertRaises(RuntimeError):
                            synchronizer._replace_directory(staging, destination)

                    self.assertTrue(injected)
                    self.assertTrue(destination.is_dir())
                    self.assertEqual(list(destination.iterdir()), [])
                    self.assertTrue(staging.is_dir())
                    backups = list(root.glob(".adapter.backup-*"))
                    self.assertEqual(len(backups), 1)
                    self.assertEqual(
                        (backups[0] / "version.txt").read_text(encoding="utf-8"),
                        "old\n",
                    )


if __name__ == "__main__":
    unittest.main()
