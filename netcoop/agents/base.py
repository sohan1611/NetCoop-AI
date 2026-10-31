"""Common interface for every NetCoop agent."""

from abc import ABC, abstractmethod

from netcoop.protocol import Envelope


class Agent(ABC):
    """An asynchronous participant in the NetCoop message protocol."""

    name: str

    @abstractmethod
    async def handle(self, envelope: Envelope) -> list[Envelope]:
        """Handle one envelope and return zero or more response envelopes."""
