"""Shared helpers for resource modules."""

from __future__ import annotations

import boto3


class AWSContext:
    """Lightweight wrapper around a boto3 session with lazy account ID resolution.

    Resource modules accept an ``AWSContext`` instead of a raw ``boto3.Session``
    so that session creation and account identity travel together.
    """

    def __init__(self, session: boto3.Session) -> None:
        self._session = session
        self._account_id: str | None = None

    @classmethod
    def create(
        cls,
        region: str | None = None,
        profile: str | None = None,
    ) -> AWSContext:
        """Create an AWSContext from optional region / profile."""
        kwargs: dict = {}
        if region:
            kwargs["region_name"] = region
        if profile:
            kwargs["profile_name"] = profile
        return cls(boto3.Session(**kwargs))

    @property
    def session(self) -> boto3.Session:
        """The underlying boto3 session."""
        return self._session

    @property
    def account_id(self) -> str:
        """AWS account ID, resolved lazily on first access."""
        if self._account_id is None:
            sts = self._session.client("sts")
            self._account_id = sts.get_caller_identity()["Account"]
        return self._account_id

    def client(self, service: str, **kwargs) -> boto3.client:
        """Create a boto3 service client."""
        return self._session.client(service, **kwargs)
