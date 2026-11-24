"""Notification interface intentionally has no configured provider."""
from typing import Protocol

class NotificationProvider(Protocol):
    def send(self, *, edition_day: str, edition_url: str, count: int, partial: bool) -> None: ...
