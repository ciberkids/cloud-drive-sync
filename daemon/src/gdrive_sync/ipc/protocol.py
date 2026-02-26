"""JSON-RPC 2.0 protocol definitions for IPC."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Any

JSONRPC_VERSION = "2.0"

# Method names
METHOD_GET_STATUS = "get_status"
METHOD_GET_SYNC_PAIRS = "get_sync_pairs"
METHOD_ADD_SYNC_PAIR = "add_sync_pair"
METHOD_REMOVE_SYNC_PAIR = "remove_sync_pair"
METHOD_SET_CONFLICT_STRATEGY = "set_conflict_strategy"
METHOD_RESOLVE_CONFLICT = "resolve_conflict"
METHOD_FORCE_SYNC = "force_sync"
METHOD_PAUSE_SYNC = "pause_sync"
METHOD_RESUME_SYNC = "resume_sync"
METHOD_GET_ACTIVITY_LOG = "get_activity_log"
METHOD_GET_CONFLICTS = "get_conflicts"
METHOD_START_AUTH = "start_auth"
METHOD_LOGOUT = "logout"

ALL_METHODS = [
    METHOD_GET_STATUS,
    METHOD_GET_SYNC_PAIRS,
    METHOD_ADD_SYNC_PAIR,
    METHOD_REMOVE_SYNC_PAIR,
    METHOD_SET_CONFLICT_STRATEGY,
    METHOD_RESOLVE_CONFLICT,
    METHOD_FORCE_SYNC,
    METHOD_PAUSE_SYNC,
    METHOD_RESUME_SYNC,
    METHOD_GET_ACTIVITY_LOG,
    METHOD_GET_CONFLICTS,
    METHOD_START_AUTH,
    METHOD_LOGOUT,
]


@dataclass
class JsonRpcRequest:
    """A JSON-RPC 2.0 request."""

    method: str
    params: dict[str, Any] = field(default_factory=dict)
    id: int | str | None = None
    jsonrpc: str = JSONRPC_VERSION

    def to_json(self) -> str:
        return json.dumps(asdict(self))

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> JsonRpcRequest:
        return cls(
            method=data["method"],
            params=data.get("params", {}),
            id=data.get("id"),
            jsonrpc=data.get("jsonrpc", JSONRPC_VERSION),
        )


@dataclass
class JsonRpcResponse:
    """A JSON-RPC 2.0 response."""

    id: int | str | None = None
    result: Any = None
    error: JsonRpcError | None = None
    jsonrpc: str = JSONRPC_VERSION

    def to_json(self) -> str:
        d: dict[str, Any] = {"jsonrpc": self.jsonrpc, "id": self.id}
        if self.error is not None:
            d["error"] = asdict(self.error)
        else:
            d["result"] = self.result
        return json.dumps(d)

    @classmethod
    def success(cls, id: int | str | None, result: Any) -> JsonRpcResponse:
        return cls(id=id, result=result)

    @classmethod
    def fail(cls, id: int | str | None, code: int, message: str, data: Any = None) -> JsonRpcResponse:
        return cls(id=id, error=JsonRpcError(code=code, message=message, data=data))


@dataclass
class JsonRpcError:
    """A JSON-RPC 2.0 error object."""

    code: int
    message: str
    data: Any = None


@dataclass
class JsonRpcNotification:
    """A JSON-RPC 2.0 notification (no id)."""

    method: str
    params: dict[str, Any] = field(default_factory=dict)
    jsonrpc: str = JSONRPC_VERSION

    def to_json(self) -> str:
        return json.dumps(asdict(self))


# Standard error codes
PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603


def parse_message(data: bytes) -> JsonRpcRequest | None:
    """Parse a raw message into a JsonRpcRequest.

    Returns None if the message is malformed.
    """
    try:
        obj = json.loads(data)
    except json.JSONDecodeError:
        return None

    if not isinstance(obj, dict):
        return None

    if "method" not in obj:
        return None

    return JsonRpcRequest.from_dict(obj)


def serialize_message(msg: JsonRpcResponse | JsonRpcNotification) -> bytes:
    """Serialize a response or notification to newline-delimited bytes."""
    return (msg.to_json() + "\n").encode()
