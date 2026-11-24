"""Adapter: AWS Secrets Manager (production)."""

from __future__ import annotations

import os
from typing import Any

from labeler.infra.secrets.cached import CachedSecrets
from labeler.infra.secrets.port import check_known, hint


class AwsSecrets(CachedSecrets):
    backend = "aws"

    def __init__(self, prefix: str, region: str, endpoint_url: str | None = None):
        super().__init__()
        import boto3

        self.client = boto3.client("secretsmanager", region_name=region, endpoint_url=endpoint_url)
        self.prefix = prefix

    def _id(self, name: str) -> str:
        return f"{self.prefix}{name}"

    def get(self, name: str) -> str | None:
        def load() -> str | None:
            try:
                return self.client.get_secret_value(SecretId=self._id(name))["SecretString"]
            except self.client.exceptions.ResourceNotFoundException:
                return os.environ.get(name) or None

        return self._cached(name, load)

    def set(self, name: str, value: str) -> None:
        check_known(name)
        try:
            self.client.put_secret_value(SecretId=self._id(name), SecretString=value.strip())
        except self.client.exceptions.ResourceNotFoundException:
            self.client.create_secret(Name=self._id(name), SecretString=value.strip())
        self._forget(name)

    def delete(self, name: str) -> bool:
        check_known(name)
        self._forget(name)
        try:
            self.client.delete_secret(SecretId=self._id(name), RecoveryWindowInDays=7)
            return True
        except self.client.exceptions.ResourceNotFoundException:
            return False

    def describe(self, name: str) -> dict[str, Any]:
        v = self.get(name)
        src = None
        if v:
            try:
                self.client.describe_secret(SecretId=self._id(name))
                src = "AWS Secrets Manager"
            except self.client.exceptions.ResourceNotFoundException:
                src = "environment"
        return {"name": name, "set": bool(v), "source": src, "hint": hint(v) if v else None}
