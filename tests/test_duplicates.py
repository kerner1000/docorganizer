"""Tests for duplicate file detection and archiving."""

import hashlib
from pathlib import Path

import pytest

from docorganizer.cli import (
    DuplicateFile,
    archive_duplicates,
    compute_file_hash,
    find_duplicates,
)


def _write_file(path: Path, content: str = "some content") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    return path


class TestComputeFileHash:
    def test_returns_sha256_hex(self, tmp_path):
        f = _write_file(tmp_path / "test.txt", "hello world")
        result = compute_file_hash(f)
        expected = hashlib.sha256(b"hello world").hexdigest()
        assert result == expected

    def test_identical_content_same_hash(self, tmp_path):
        f1 = _write_file(tmp_path / "a.txt", "same")
        f2 = _write_file(tmp_path / "b.txt", "same")
        assert compute_file_hash(f1) == compute_file_hash(f2)

    def test_different_content_different_hash(self, tmp_path):
        f1 = _write_file(tmp_path / "a.txt", "content A")
        f2 = _write_file(tmp_path / "b.txt", "content B")
        assert compute_file_hash(f1) != compute_file_hash(f2)

    def test_binary_file(self, tmp_path):
        f = tmp_path / "binary.bin"
        data = bytes(range(256))
        f.write_bytes(data)
        expected = hashlib.sha256(data).hexdigest()
        assert compute_file_hash(f) == expected


class TestFindDuplicates:
    def test_no_duplicates(self, test_ctx):
        inbox = test_ctx.inbox
        _write_file(inbox / "new.pdf", "unique content")

        duplicates, remaining = find_duplicates([inbox / "new.pdf"], test_ctx)

        assert duplicates == []
        assert len(remaining) == 1

    def test_duplicate_of_existing_filed_document(self, test_ctx):
        family = test_ctx.root_folder
        inbox = test_ctx.inbox

        content = "identical document content"
        _write_file(family / "Germany" / "Steuer" / "filed.pdf", content)
        _write_file(inbox / "incoming.pdf", content)

        duplicates, remaining = find_duplicates([inbox / "incoming.pdf"], test_ctx)

        assert len(duplicates) == 1
        assert duplicates[0].inbox_path == inbox / "incoming.pdf"
        assert duplicates[0].existing_path == family / "Germany" / "Steuer" / "filed.pdf"
        assert remaining == []

    def test_duplicate_of_archived_document(self, test_ctx):
        archive = test_ctx.archive
        inbox = test_ctx.inbox

        content = "archived doc"
        _write_file(archive / "Family" / "old.pdf", content)
        _write_file(inbox / "rescan.pdf", content)

        duplicates, remaining = find_duplicates([inbox / "rescan.pdf"], test_ctx)

        assert len(duplicates) == 1
        assert duplicates[0].existing_path == archive / "Family" / "old.pdf"
        assert remaining == []

    def test_within_inbox_duplicates(self, test_ctx):
        inbox = test_ctx.inbox
        content = "same file twice"
        _write_file(inbox / "copy1.pdf", content)
        _write_file(inbox / "copy2.pdf", content)

        duplicates, remaining = find_duplicates(
            [inbox / "copy1.pdf", inbox / "copy2.pdf"], test_ctx,
        )

        assert len(duplicates) == 1
        assert duplicates[0].inbox_path == inbox / "copy2.pdf"
        assert duplicates[0].existing_path == inbox / "copy1.pdf"
        assert len(remaining) == 1
        assert remaining[0] == inbox / "copy1.pdf"

    def test_mixed_duplicates_and_unique(self, test_ctx):
        family = test_ctx.root_folder
        inbox = test_ctx.inbox

        _write_file(family / "Germany" / "existing.pdf", "existing content")
        _write_file(inbox / "dup.pdf", "existing content")
        _write_file(inbox / "new.pdf", "brand new content")

        duplicates, remaining = find_duplicates(
            [inbox / "dup.pdf", inbox / "new.pdf"], test_ctx,
        )

        assert len(duplicates) == 1
        assert duplicates[0].inbox_path == inbox / "dup.pdf"
        assert len(remaining) == 1
        assert remaining[0] == inbox / "new.pdf"

    def test_dotfiles_in_family_are_ignored(self, test_ctx):
        family = test_ctx.root_folder
        inbox = test_ctx.inbox

        content = "dotfile content"
        _write_file(family / ".DS_Store", content)
        _write_file(inbox / "test.pdf", content)

        duplicates, remaining = find_duplicates([inbox / "test.pdf"], test_ctx)

        assert duplicates == []
        assert len(remaining) == 1

    def test_empty_inbox_list(self, test_ctx):
        duplicates, remaining = find_duplicates([], test_ctx)

        assert duplicates == []
        assert remaining == []

    def test_nonexistent_archive_is_safe(self, test_ctx):
        """Archive directory doesn't exist yet — should not crash."""
        inbox = test_ctx.inbox
        _write_file(inbox / "test.pdf", "content")

        duplicates, remaining = find_duplicates([inbox / "test.pdf"], test_ctx)

        assert duplicates == []
        assert len(remaining) == 1


class TestFindDuplicatesFlatArchive:
    """Duplicate detection with a flat (no-countries) archive."""

    def test_duplicate_of_existing_in_flat_archive(self, flat_ctx):
        root = flat_ctx.root_folder
        inbox = flat_ctx.inbox

        content = "identical document"
        _write_file(root / "Invoices" / "filed.pdf", content)
        _write_file(inbox / "incoming.pdf", content)

        duplicates, remaining = find_duplicates([inbox / "incoming.pdf"], flat_ctx)

        assert len(duplicates) == 1
        assert remaining == []

    def test_unique_file_in_flat_archive(self, flat_ctx):
        inbox = flat_ctx.inbox
        _write_file(inbox / "new.pdf", "unique content")

        duplicates, remaining = find_duplicates([inbox / "new.pdf"], flat_ctx)

        assert duplicates == []
        assert len(remaining) == 1


class TestFindDuplicatesNoRootFolder:
    """Duplicate detection with root_folder=None (flat layout, filing root == archive root)."""

    def test_duplicate_of_filed_document(self, no_root_ctx):
        """Filed documents are found even when root_folder equals archive root."""
        inbox = no_root_ctx.inbox
        content = "identical document"
        _write_file(no_root_ctx.root / "Germany" / "Invoices" / "filed.pdf", content)
        _write_file(inbox / "incoming.pdf", content)

        duplicates, remaining = find_duplicates([inbox / "incoming.pdf"], no_root_ctx)

        assert len(duplicates) == 1
        assert remaining == []

    def test_inbox_files_not_treated_as_existing(self, no_root_ctx):
        """Files in inbox/ must not be hashed as 'existing' — they'd match themselves."""
        inbox = no_root_ctx.inbox
        _write_file(inbox / "new.pdf", "unique content")

        duplicates, remaining = find_duplicates([inbox / "new.pdf"], no_root_ctx)

        assert duplicates == []
        assert len(remaining) == 1

    def test_archive_dir_files_excluded_from_root_scan(self, no_root_ctx):
        """Files in _archive/ are scanned via the archive search_root, not the root scan."""
        inbox = no_root_ctx.inbox
        content = "archived content"
        _write_file(no_root_ctx.archive / "inbox" / "old.pdf", content)
        _write_file(inbox / "old.pdf", content)

        duplicates, remaining = find_duplicates([inbox / "old.pdf"], no_root_ctx)

        assert len(duplicates) == 1
        assert remaining == []


class TestArchiveDuplicates:
    def test_moves_file_to_archive_inbox(self, test_ctx):
        inbox = test_ctx.inbox
        archive = test_ctx.archive
        _write_file(inbox / "dup.pdf", "content")

        dup = DuplicateFile(
            inbox_path=inbox / "dup.pdf",
            existing_path=test_ctx.root_folder / "filed.pdf",
            file_hash="abc123",
        )
        archive_duplicates([dup], test_ctx)

        assert not (inbox / "dup.pdf").exists()
        assert (archive / "inbox" / "dup.pdf").exists()
        assert dup.archived_to == archive / "inbox" / "dup.pdf"

    def test_creates_archive_inbox_directory(self, test_ctx):
        inbox = test_ctx.inbox
        archive = test_ctx.archive
        _write_file(inbox / "dup.pdf", "content")

        assert not archive.exists()

        dup = DuplicateFile(
            inbox_path=inbox / "dup.pdf",
            existing_path=test_ctx.root_folder / "filed.pdf",
            file_hash="abc123",
        )
        archive_duplicates([dup], test_ctx)

        assert (archive / "inbox").is_dir()

    def test_overwrites_existing_archive_file(self, test_ctx):
        inbox = test_ctx.inbox
        archive = test_ctx.archive
        _write_file(inbox / "dup.pdf", "new scan")
        _write_file(archive / "inbox" / "dup.pdf", "old scan")

        dup = DuplicateFile(
            inbox_path=inbox / "dup.pdf",
            existing_path=test_ctx.root_folder / "filed.pdf",
            file_hash="abc123",
        )
        archive_duplicates([dup], test_ctx)

        assert (archive / "inbox" / "dup.pdf").read_text() == "new scan"

    def test_skips_missing_inbox_file(self, test_ctx):
        """If inbox file was removed between detection and execution, do not crash."""
        inbox = test_ctx.inbox
        archive = test_ctx.archive

        dup = DuplicateFile(
            inbox_path=inbox / "gone.pdf",
            existing_path=test_ctx.root_folder / "filed.pdf",
            file_hash="abc123",
        )
        archive_duplicates([dup], test_ctx)  # must not raise

        assert not (archive / "inbox" / "gone.pdf").exists()
        assert dup.archived_to is None

    def test_empty_list_is_noop(self, test_ctx):
        archive = test_ctx.archive
        archive_duplicates([], test_ctx)
        assert not archive.exists()

    def test_multiple_duplicates(self, test_ctx):
        inbox = test_ctx.inbox
        archive = test_ctx.archive
        _write_file(inbox / "a.pdf", "a")
        _write_file(inbox / "b.pdf", "b")

        dups = [
            DuplicateFile(inbox / "a.pdf", test_ctx.root_folder / "fa.pdf", "h1"),
            DuplicateFile(inbox / "b.pdf", test_ctx.root_folder / "fb.pdf", "h2"),
        ]
        archive_duplicates(dups, test_ctx)

        assert (archive / "inbox" / "a.pdf").exists()
        assert (archive / "inbox" / "b.pdf").exists()
        assert dups[0].archived_to == archive / "inbox" / "a.pdf"
        assert dups[1].archived_to == archive / "inbox" / "b.pdf"
