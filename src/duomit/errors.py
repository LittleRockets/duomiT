"""Stable, safe errors for adapters and application boundaries."""

from dataclasses import dataclass


@dataclass(eq=False)
class DuomitError(Exception):
    code: str
    message: str
    retryable: bool = False

    def __str__(self) -> str:
        return f"{self.code}: {self.message}"

    def envelope(self, request_id: str) -> dict:
        return {"code": self.code, "message": self.message,
                "request_id": request_id, "retryable": self.retryable}


def require(condition: bool, message: str) -> None:
    if not condition:
        raise DuomitError("INVALID_CONTRACT", message)
