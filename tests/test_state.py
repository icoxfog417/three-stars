"""Tests for deployment state management."""

from __future__ import annotations

from three_stars.state import (
    create_initial_state,
    delete_state,
    load_state,
    save_state,
)


class TestState:
    def test_no_state_file(self, tmp_path):
        assert load_state(tmp_path) is None

    def test_save_and_load(self, tmp_path):
        state = create_initial_state("test-app", "us-east-1")
        state["resources"]["s3_bucket"] = "my-bucket"
        save_state(tmp_path, state)

        loaded = load_state(tmp_path)
        assert loaded is not None
        assert loaded["project_name"] == "test-app"
        assert loaded["region"] == "us-east-1"
        assert loaded["resources"]["s3_bucket"] == "my-bucket"
        assert loaded["version"] == 1

    def test_delete_state(self, tmp_path):
        state = create_initial_state("test-app", "us-east-1")
        save_state(tmp_path, state)
        assert load_state(tmp_path) is not None

        delete_state(tmp_path)
        assert load_state(tmp_path) is None

    def test_delete_nonexistent_state(self, tmp_path):
        # Should not raise
        delete_state(tmp_path)

    def test_save_updates_timestamp(self, tmp_path):
        state = create_initial_state("test-app", "us-east-1")
        save_state(tmp_path, state)

        loaded = load_state(tmp_path)
        assert "updated_at" in loaded
