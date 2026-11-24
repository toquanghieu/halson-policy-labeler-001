"""Adapter: Amazon SQS, and ElasticMQ locally (same API, another endpoint)."""

from __future__ import annotations

import asyncio
import json
from typing import Any

from labeler.infra.queue.port import Message, QueueName


class SQSQueue:
    def __init__(self, names: dict[QueueName, str], endpoint_url: str | None, region: str):
        import boto3

        self.client = boto3.client("sqs", endpoint_url=endpoint_url, region_name=region)
        self.names = names
        self._urls: dict[QueueName, str] = {}

    def _url(self, queue: QueueName) -> str:
        if queue not in self._urls:
            self._urls[queue] = self.client.get_queue_url(QueueName=self.names[queue])["QueueUrl"]
        return self._urls[queue]

    def ensure(self, visibility_timeout_s: int = 300, max_receives: int = 3) -> None:
        """Create missing queues, each with a DLQ and redrive policy. Existing queues (pre-declared in
        ElasticMQ's config, or by infrastructure-as-code on AWS) are used as they are."""
        from botocore.exceptions import ClientError

        def existing(name: str) -> str | None:
            try:
                return self.client.get_queue_url(QueueName=name)["QueueUrl"]
            except ClientError:
                return None

        for q, name in self.names.items():
            url = existing(name)
            if url is None:
                dlq_url = existing(f"{name}-dlq") or self.client.create_queue(QueueName=f"{name}-dlq")["QueueUrl"]
                dlq_arn = self.client.get_queue_attributes(QueueUrl=dlq_url, AttributeNames=["QueueArn"])["Attributes"][
                    "QueueArn"
                ]
                redrive = {"deadLetterTargetArn": dlq_arn, "maxReceiveCount": str(max_receives)}
                url = self.client.create_queue(
                    QueueName=name,
                    Attributes={"VisibilityTimeout": str(visibility_timeout_s), "RedrivePolicy": json.dumps(redrive)},
                )["QueueUrl"]
            self._urls[q] = url

    async def send(self, queue: QueueName, bodies: list[dict[str, Any]]) -> None:
        url = self._url(queue)
        for i in range(0, len(bodies), 10):  # SQS batch limit
            entries = [{"Id": str(n), "MessageBody": json.dumps(b)} for n, b in enumerate(bodies[i : i + 10])]
            resp = await asyncio.to_thread(self.client.send_message_batch, QueueUrl=url, Entries=entries)
            if resp.get("Failed"):
                raise RuntimeError(f"SQS rejected {len(resp['Failed'])} messages: {resp['Failed'][0]}")

    async def receive(self, queue: QueueName, max_messages: int, wait_s: float) -> list[Message]:
        resp = await asyncio.to_thread(
            self.client.receive_message,
            QueueUrl=self._url(queue),
            MaxNumberOfMessages=max(1, min(10, max_messages)),
            WaitTimeSeconds=int(wait_s),
            AttributeNames=["ApproximateReceiveCount"],
        )
        return [
            Message(
                body=json.loads(m["Body"]),
                receipt=m["ReceiptHandle"],
                receive_count=int(m.get("Attributes", {}).get("ApproximateReceiveCount", 1)),
            )
            for m in resp.get("Messages", [])
        ]

    async def ack(self, queue: QueueName, msg: Message) -> None:
        await asyncio.to_thread(self.client.delete_message, QueueUrl=self._url(queue), ReceiptHandle=msg.receipt)

    async def depth(self, queue: QueueName) -> dict[str, int]:
        attrs = (
            await asyncio.to_thread(
                self.client.get_queue_attributes,
                QueueUrl=self._url(queue),
                AttributeNames=["ApproximateNumberOfMessages", "ApproximateNumberOfMessagesNotVisible"],
            )
        )["Attributes"]
        return {
            "visible": int(attrs.get("ApproximateNumberOfMessages", 0)),
            "in_flight": int(attrs.get("ApproximateNumberOfMessagesNotVisible", 0)),
        }
