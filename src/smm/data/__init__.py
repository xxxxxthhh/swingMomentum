"""Market data providers."""

from smm.data.fake import FakeProvider
from smm.data.protocol import DataProvider

__all__ = ["DataProvider", "FakeProvider"]
