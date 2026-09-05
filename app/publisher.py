"""Event transport for the outbox relay.

Two transports, selected by the PUBSUB_TOPIC configuration. With a topic set,
events publish to Pub/Sub. Without one, they publish to the structured log, so
the exact relay path runs in local and test environments without cloud
credentials. Either way the relay marks a row published only after the
transport has accepted the event, so a failure leaves the row pending for
retry and delivery is at-least-once.
"""

import json
import logging

from app import config

logger = logging.getLogger("orchestrator.publisher")


class LogTransport:
    name = "log"

    def publish(self, envelope: dict) -> str:
        logger.info(
            "event published to log transport",
            extra={
                "event_type": envelope["event_type"],
                "correlation_id": envelope["correlation_id"],
            },
        )
        return f"log:{envelope['event_id']}"


class PubSubTransport:
    name = "pubsub"

    def __init__(self, topic: str):
        from google.cloud import pubsub_v1

        self._client = pubsub_v1.PublisherClient()
        self._topic = topic

    def publish(self, envelope: dict) -> str:
        data = json.dumps(envelope).encode("utf-8")
        future = self._client.publish(
            self._topic,
            data,
            event_id=envelope["event_id"],
            event_type=envelope["event_type"],
            correlation_id=envelope["correlation_id"],
        )
        return future.result(timeout=30)


_transport = None


def get_transport():
    global _transport
    if _transport is None:
        _transport = (
            PubSubTransport(config.PUBSUB_TOPIC) if config.PUBSUB_TOPIC else LogTransport()
        )
        logger.info(f"outbox transport: {_transport.name}")
    return _transport


def reset_transport() -> None:
    global _transport
    _transport = None
