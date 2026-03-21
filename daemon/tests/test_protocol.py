"""Tests for JSON-RPC protocol serialization/deserialization."""

from __future__ import annotations

import json

from cloud_drive_sync.ipc.protocol import (
    JSONRPC_VERSION,
    JsonRpcNotification,
    JsonRpcRequest,
    JsonRpcResponse,
    parse_message,
    serialize_message,
)


class TestJsonRpcRequest:
    def test_to_json(self):
        req = JsonRpcRequest(method="get_status", params={"key": "val"}, id=1)
        data = json.loads(req.to_json())
        assert data["jsonrpc"] == JSONRPC_VERSION
        assert data["method"] == "get_status"
        assert data["params"] == {"key": "val"}
        assert data["id"] == 1

    def test_from_dict(self):
        d = {"jsonrpc": "2.0", "method": "force_sync", "params": {"pair_id": "p0"}, "id": 42}
        req = JsonRpcRequest.from_dict(d)
        assert req.method == "force_sync"
        assert req.params == {"pair_id": "p0"}
        assert req.id == 42

    def test_from_dict_defaults(self):
        d = {"method": "get_status"}
        req = JsonRpcRequest.from_dict(d)
        assert req.params == {}
        assert req.id is None


class TestJsonRpcResponse:
    def test_success_response(self):
        resp = JsonRpcResponse.success(1, {"status": "ok"})
        data = json.loads(resp.to_json())
        assert data["id"] == 1
        assert data["result"] == {"status": "ok"}
        assert "error" not in data

    def test_error_response(self):
        resp = JsonRpcResponse.fail(1, -32601, "Method not found")
        data = json.loads(resp.to_json())
        assert data["id"] == 1
        assert data["error"]["code"] == -32601
        assert data["error"]["message"] == "Method not found"
        assert "result" not in data

    def test_error_with_data(self):
        resp = JsonRpcResponse.fail(2, -32602, "Invalid params", data={"field": "pair_id"})
        data = json.loads(resp.to_json())
        assert data["error"]["data"] == {"field": "pair_id"}


class TestJsonRpcNotification:
    def test_to_json(self):
        notif = JsonRpcNotification(method="conflict_detected", params={"path": "file.txt"})
        data = json.loads(notif.to_json())
        assert data["jsonrpc"] == JSONRPC_VERSION
        assert data["method"] == "conflict_detected"
        assert data["params"] == {"path": "file.txt"}
        assert "id" not in data or data.get("id") is None


class TestParseMessage:
    def test_valid_request(self):
        msg = json.dumps({"jsonrpc": "2.0", "method": "get_status", "id": 1}).encode()
        req = parse_message(msg)
        assert req is not None
        assert req.method == "get_status"
        assert req.id == 1

    def test_invalid_json(self):
        assert parse_message(b"not json") is None

    def test_missing_method(self):
        msg = json.dumps({"jsonrpc": "2.0", "id": 1}).encode()
        assert parse_message(msg) is None

    def test_not_a_dict(self):
        msg = json.dumps([1, 2, 3]).encode()
        assert parse_message(msg) is None

    def test_notification_no_id(self):
        msg = json.dumps({"jsonrpc": "2.0", "method": "heartbeat"}).encode()
        req = parse_message(msg)
        assert req is not None
        assert req.method == "heartbeat"
        assert req.id is None


class TestSerializeMessage:
    def test_response_serialization(self):
        resp = JsonRpcResponse.success(1, "ok")
        data = serialize_message(resp)
        assert data.endswith(b"\n")
        parsed = json.loads(data)
        assert parsed["result"] == "ok"

    def test_notification_serialization(self):
        notif = JsonRpcNotification(method="sync_complete", params={})
        data = serialize_message(notif)
        assert data.endswith(b"\n")
        parsed = json.loads(data)
        assert parsed["method"] == "sync_complete"
