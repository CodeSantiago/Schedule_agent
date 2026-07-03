"""Messaging transport seam.

Re-exports the seam types so callers can write
`from packages.application.messaging import MessageTransport, EchoTransport`.
"""

from packages.application.messaging.transport import (
    EchoTransport,
    KapsoTransport,
    MessageTransport,
    SendResult,
    TransportFactory,
)

__all__ = [
    "EchoTransport",
    "KapsoTransport",
    "MessageTransport",
    "SendResult",
    "TransportFactory",
]
