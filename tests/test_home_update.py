from __future__ import annotations

import hashlib
import os
from pathlib import Path
import shutil
import zipfile

import pytest
import yaml

import proofline.home_writer as home_writer
from proofline.home_writer import (
    HomeInitError,
    build_home_payload,
    payload_from_wheel,
    prepare_home_update,
    reconcile_existing_home,
)


OPERATIONS = {
    "legacy-nonterminal-history-migration.md": b"migration operation\n",
    "official-wheel-release.md": b"release operation\n",
    "proofline-tool-environment.md": b"environment operation\n",
}


def snapshot(root: Path) -> dict[str, tuple[str, bytes | None]]:
    if not root.exists() and not root.is_symlink():
        return {}
    result: dict[str, tuple[str, bytes | None]] = {}
    for path in [root, *sorted(root.rglob("*"))]:
        relative = "." if path == root else path.relative_to(root).as_posix()
        if path.is_symlink():
            result[relative] = ("symlink", os.readlink(path).encode())
        elif path.is_dir():
            result[relative] = ("dir", None)
        else:
            result[relative] = ("file", path.read_bytes())
    return result


def resource_files(marker: str) -> dict[str, bytes]:
    return {
        "agent-context.md": f"agent-{marker}\n".encode(),
        "contracts/storage.md": f"contract-{marker}\n".encode(),
        **{
            f"operations/{name}": content.replace(
                b"operation", f"operation-{marker}".encode()
            )
            for name, content in OPERATIONS.items()
        },
        "templates/schema-v1/artifacts/line.md": f"template-{marker}\n".encode(),
        "skills/proofline-start-line/SKILL.md": f"skill-{marker}\n".encode(),
    }


def write_payload(root: Path, payload: dict[str, bytes]) -> None:
    for relative, content in payload.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)


def test_target_wheel_payload_is_path_safe_and_manifest_bound(tmp_path: Path) -> None:
    wheel = tmp_path / "proofline-0.4.0-py3-none-any.whl"
    resources = resource_files("new")
    with zipfile.ZipFile(wheel, "w") as archive:
        for relative, content in resources.items():
            archive.writestr(f"proofline_home/{relative}", content)
        archive.writestr("proofline_home/__init__.py", b"")

    payload = payload_from_wheel(wheel, "0.4.0")

    assert {path for path in payload if path != "manifest.yaml"} == set(resources)
    manifest = yaml.safe_load(payload["manifest.yaml"])
    assert manifest["proofline_version"] == "0.4.0"
    assert manifest["managed_files"] == [
        {"path": path, "sha256": hashlib.sha256(resources[path]).hexdigest()}
        for path in sorted(resources)
    ]


@pytest.mark.parametrize(
    "entry",
    [
        "proofline_home/operations/../../escape",
        "proofline_home/unexpected.txt",
    ],
)
def test_target_wheel_rejects_unsafe_or_unexpected_resource(tmp_path: Path, entry: str) -> None:
    wheel = tmp_path / "proofline-0.4.0-py3-none-any.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        for relative, content in resource_files("new").items():
            archive.writestr(f"proofline_home/{relative}", content)
        archive.writestr(entry, b"bad")
    with pytest.raises(HomeInitError, match="unsafe|unexpected"):
        payload_from_wheel(wheel, "0.4.0")


def test_target_wheel_rejects_resource_symlink(tmp_path: Path) -> None:
    wheel = tmp_path / "proofline-0.4.0-py3-none-any.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        for relative, content in resource_files("new").items():
            archive.writestr(f"proofline_home/{relative}", content)
        link = zipfile.ZipInfo("proofline_home/operations/link.md")
        link.create_system = 3
        link.external_attr = 0o120777 << 16
        archive.writestr(link, b"../../outside")
    with pytest.raises(HomeInitError, match="symlink"):
        payload_from_wheel(wheel, "0.4.0")


def test_target_wheel_rejects_symlink_mode_directory_entry(tmp_path: Path) -> None:
    wheel = tmp_path / "proofline-0.4.0-py3-none-any.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        for relative, content in resource_files("new").items():
            archive.writestr(f"proofline_home/{relative}", content)
        link = zipfile.ZipInfo("proofline_home/operations/")
        link.create_system = 3
        link.external_attr = 0o120777 << 16
        archive.writestr(link, b"")

    with pytest.raises(HomeInitError, match="symlink"):
        payload_from_wheel(wheel, "0.4.0")


def test_target_wheel_rejects_duplicate_resource_name(tmp_path: Path) -> None:
    wheel = tmp_path / "proofline-0.4.0-py3-none-any.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        for relative, content in resource_files("new").items():
            archive.writestr(f"proofline_home/{relative}", content)
        with pytest.warns(UserWarning, match="Duplicate name"):
            archive.writestr(
                "proofline_home/operations/official-wheel-release.md", b"duplicate"
            )
    with pytest.raises(HomeInitError, match="duplicate"):
        payload_from_wheel(wheel, "0.4.0")


@pytest.mark.parametrize("kind", ["missing", "empty", "incomplete"])
def test_target_wheel_rejects_incomplete_operations_group(
    tmp_path: Path, kind: str
) -> None:
    wheel = tmp_path / "proofline-0.4.0-py3-none-any.whl"
    resources = resource_files("new")
    if kind in {"missing", "empty"}:
        resources = {
            relative: content
            for relative, content in resources.items()
            if not relative.startswith("operations/")
        }
    else:
        resources.pop("operations/proofline-tool-environment.md")
    with zipfile.ZipFile(wheel, "w") as archive:
        for relative, content in resources.items():
            archive.writestr(f"proofline_home/{relative}", content)
        if kind == "empty":
            archive.writestr("proofline_home/operations/", b"")

    with pytest.raises(HomeInitError, match="incomplete home resource payload"):
        payload_from_wheel(wheel, "0.4.0")


def test_existing_clean_harness_can_commit_and_atomic_rollback(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    target = home / ".proofline"
    old = build_home_payload("0.3.0", resource_files("old"))
    new = build_home_payload("0.4.0", resource_files("new"))
    write_payload(target, old)
    before = snapshot(target)

    transaction = prepare_home_update(new, current_payload=old, home=home)
    assert snapshot(target) == before
    transaction.commit()
    assert (target / "manifest.yaml").read_bytes() == new["manifest.yaml"]
    assert (target / "contracts/storage.md").read_bytes() == resource_files("new")["contracts/storage.md"]

    transaction.rollback()
    assert snapshot(target) == before


def test_existing_clean_harness_can_commit_and_finalize(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    target = home / ".proofline"
    old = build_home_payload("0.3.0", resource_files("old"))
    new = build_home_payload("0.4.0", resource_files("new"))
    write_payload(target, old)

    transaction = prepare_home_update(new, current_payload=old, home=home)
    transaction.commit()
    transaction.finalize()

    assert (target / "manifest.yaml").read_bytes() == new["manifest.yaml"]
    assert not list(home.glob(".proofline-update-*"))


@pytest.mark.parametrize("existing", [False, True])
def test_staging_identity_swap_is_rejected_before_target_mutation(tmp_path: Path, existing: bool) -> None:
    home = tmp_path / "home"
    home.mkdir()
    target = home / ".proofline"
    old = build_home_payload("0.3.0", resource_files("old"))
    new = build_home_payload("0.4.0", resource_files("new"))
    if existing:
        write_payload(target, old)
    transaction = prepare_home_update(
        new,
        current_payload=old if existing else None,
        home=home,
    )
    assert transaction.stage is not None
    original_target = snapshot(target)
    shutil.rmtree(transaction.stage)
    transaction.stage.symlink_to(tmp_path, target_is_directory=True)

    with pytest.raises(HomeInitError, match="staging identity changed|expected directory"):
        transaction.commit()

    assert snapshot(target) == original_target
    assert not target.is_symlink()
    transaction.stage.unlink()


def test_rollback_rejects_swapped_old_stage_before_reverse_exchange(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    target = home / ".proofline"
    old = build_home_payload("0.3.0", resource_files("old"))
    new = build_home_payload("0.4.0", resource_files("new"))
    write_payload(target, old)
    transaction = prepare_home_update(new, current_payload=old, home=home)
    transaction.commit()
    assert transaction.stage is not None
    saved_old = home / "saved-old"
    transaction.stage.rename(saved_old)
    transaction.stage.mkdir()
    (transaction.stage / "agent-context.md").write_text("attacker\n")

    with pytest.raises(HomeInitError, match="rollback stage identity mismatch"):
        transaction.rollback()

    assert (target / "agent-context.md").read_bytes() == new["agent-context.md"]
    assert (saved_old / "agent-context.md").read_bytes() == old["agent-context.md"]


def test_partial_finalize_never_restores_damaged_old_harness(tmp_path: Path, monkeypatch) -> None:
    home = tmp_path / "home"
    home.mkdir()
    target = home / ".proofline"
    old = build_home_payload("0.3.0", resource_files("old"))
    new = build_home_payload("0.4.0", resource_files("new"))
    write_payload(target, old)
    transaction = prepare_home_update(new, current_payload=old, home=home)
    transaction.commit()
    assert transaction.stage is not None

    def partial_cleanup(path: Path) -> None:
        (Path(path) / "contracts/storage.md").unlink()
        raise OSError("injected cleanup failure")

    monkeypatch.setattr("proofline.home_writer.shutil.rmtree", partial_cleanup)
    with pytest.raises(HomeInitError, match="finalize failed"):
        transaction.finalize()
    with pytest.raises(HomeInitError, match="rollback is unavailable"):
        transaction.rollback()
    assert all((target / relative).read_bytes() == content for relative, content in new.items())


def test_target_swap_inside_exchange_is_reversed_without_deleting_concurrent_target(
    tmp_path: Path, monkeypatch
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    target = home / ".proofline"
    old = build_home_payload("0.3.0", resource_files("old"))
    new = build_home_payload("0.4.0", resource_files("new"))
    write_payload(target, old)
    transaction = prepare_home_update(new, current_payload=old, home=home)
    assert transaction.stage is not None
    saved_old = home / "saved-old"
    original_exchange = home_writer._exchange_directories
    exchange_calls = 0

    def swap_then_exchange(left: Path, right: Path) -> None:
        nonlocal exchange_calls
        exchange_calls += 1
        if exchange_calls == 1:
            right.rename(saved_old)
            right.mkdir()
            (right / "agent-context.md").write_text("attacker\n")
        original_exchange(left, right)

    monkeypatch.setattr("proofline.home_writer._exchange_directories", swap_then_exchange)
    with pytest.raises(HomeInitError, match="concurrent target"):
        transaction.commit()
    assert (target / "agent-context.md").read_text() == "attacker\n"
    assert (saved_old / "agent-context.md").read_bytes() == old["agent-context.md"]
    with pytest.raises(HomeInitError, match="target changed before rollback"):
        transaction.rollback()
    assert (target / "agent-context.md").read_text() == "attacker\n"


def test_absent_harness_commit_and_rollback_restores_absence(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    new = build_home_payload("0.4.0", resource_files("new"))

    transaction = prepare_home_update(new, current_payload=None, home=home)
    transaction.commit()
    assert (home / ".proofline/manifest.yaml").is_file()
    transaction.rollback()

    assert not (home / ".proofline").exists()
    assert not list(home.glob(".proofline-update-*"))


@pytest.mark.parametrize("kind", ["modified", "unexpected", "symlink"])
def test_existing_harness_conflict_fails_before_staging(tmp_path: Path, kind: str) -> None:
    home = tmp_path / "home"
    home.mkdir()
    target = home / ".proofline"
    old = build_home_payload("0.3.0", resource_files("old"))
    new = build_home_payload("0.4.0", resource_files("new"))
    write_payload(target, old)
    if kind == "modified":
        (target / "agent-context.md").write_text("user edit\n")
    elif kind == "unexpected":
        (target / "notes.txt").write_text("user file\n")
    else:
        victim = target / "contracts/storage.md"
        victim.unlink()
        victim.symlink_to(tmp_path / "outside")
    before = snapshot(home)

    with pytest.raises(HomeInitError, match="conflict|symlink"):
        prepare_home_update(new, current_payload=old, home=home)

    assert snapshot(home) == before
    assert not list(home.glob(".proofline-update-*"))


def test_version_compatibility_bridge_reconciles_existing_manifest(tmp_path: Path, monkeypatch) -> None:
    home = tmp_path / "home"
    home.mkdir()
    target = home / ".proofline"
    old = build_home_payload("0.3.0", resource_files("old"))
    legacy_manifest = yaml.safe_load(old["manifest.yaml"])
    legacy_manifest.pop("source")
    old["manifest.yaml"] = yaml.safe_dump(legacy_manifest, sort_keys=False).encode()
    new = build_home_payload("0.4.0", resource_files("new"))
    write_payload(target, old)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr("proofline.home_writer._payload", lambda: new)

    assert reconcile_existing_home() == "updated"
    assert (target / "manifest.yaml").read_bytes() == new["manifest.yaml"]


def test_version_compatibility_bridge_adds_operations_to_pre_operations_home(
    tmp_path: Path, monkeypatch
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    target = home / ".proofline"
    old_resources = {
        relative: content
        for relative, content in resource_files("old").items()
        if not relative.startswith("operations/")
    }
    old_manifest = {
        "schema_version": 1,
        "proofline_version": "0.6.0",
        "source": {"type": "packaged-resource"},
        "managed_files": [
            {
                "path": relative,
                "sha256": hashlib.sha256(old_resources[relative]).hexdigest(),
            }
            for relative in sorted(old_resources)
        ],
    }
    old = {
        **old_resources,
        "manifest.yaml": yaml.safe_dump(
            old_manifest, sort_keys=False, allow_unicode=True
        ).encode("utf-8"),
    }
    assert not any(
        record["path"].startswith("operations/")
        for record in old_manifest["managed_files"]
    )
    new_resources = resource_files("new")
    new = build_home_payload("0.7.0", new_resources)
    write_payload(target, old)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr("proofline.home_writer._payload", lambda: new)

    assert reconcile_existing_home() == "updated"
    assert {
        path.relative_to(target).as_posix(): path.read_bytes()
        for path in sorted((target / "operations").iterdir())
    } == {
        relative: content
        for relative, content in new_resources.items()
        if relative.startswith("operations/")
    }
    assert (target / "manifest.yaml").read_bytes() == new["manifest.yaml"]


def test_version_compatibility_bridge_does_not_initialize_absent_home(tmp_path: Path, monkeypatch) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))

    assert reconcile_existing_home() == "absent"
    assert not (home / ".proofline").exists()
