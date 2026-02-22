"""Tests for deployment state management with typed dataclasses."""

from __future__ import annotations

from three_stars.state import (
    AgentCoreState,
    StorageState,
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
        state.storage = StorageState(s3_bucket="my-bucket")
        save_state(tmp_path, state)

        loaded = load_state(tmp_path)
        assert loaded is not None
        assert loaded.project_name == "test-app"
        assert loaded.region == "us-east-1"
        assert loaded.storage is not None
        assert loaded.storage.s3_bucket == "my-bucket"
        assert loaded.version == 1

    def test_save_and_load_with_all_resources(self, tmp_path):
        state = create_initial_state("test-app", "us-east-1")
        state.agentcore = AgentCoreState(
            iam_role_name="role",
            iam_role_arn="arn:role",
            runtime_id="rt-123",
            runtime_arn="arn:runtime",
            endpoint_name="ep",
            endpoint_arn="arn:ep",
        )
        state.storage = StorageState(s3_bucket="bucket")
        save_state(tmp_path, state)

        loaded = load_state(tmp_path)
        assert loaded.agentcore is not None
        assert loaded.agentcore.runtime_id == "rt-123"
        assert loaded.storage.s3_bucket == "bucket"

    def test_delete_state(self, tmp_path):
        state = create_initial_state("test-app", "us-east-1")
        save_state(tmp_path, state)
        assert load_state(tmp_path) is not None

        delete_state(tmp_path)
        assert load_state(tmp_path) is None

    def test_delete_nonexistent_state(self, tmp_path):
        delete_state(tmp_path)

    def test_save_updates_timestamp(self, tmp_path):
        state = create_initial_state("test-app", "us-east-1")
        save_state(tmp_path, state)

        loaded = load_state(tmp_path)
        assert loaded.updated_at is not None

    def test_initial_state_has_no_resources(self, tmp_path):
        state = create_initial_state("test-app", "us-east-1")
        assert state.agentcore is None
        assert state.storage is None
        assert state.edge is None
        assert state.cdn is None
