"""A mock Digital License Manager store for the tests: records release publishes and answers them.

Standalone, it serves on 127.0.0.1:<port> and answers every publish with 201 (the CI job that runs
the action itself uses this): python3 tests/mock_store.py 8765
"""

import email.parser
import email.policy
import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class Store(BaseHTTPRequestHandler):
    """Records every request; answers from the queue of (status, headers, body) the test sets."""

    requests = []
    responses = []

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)
        message = email.parser.BytesParser(policy=email.policy.HTTP).parsebytes(
            b"Content-Type: " + self.headers["Content-Type"].encode() + b"\r\n\r\n" + body
        )
        fields, files = {}, {}
        for part in message.iter_parts():
            name = part.get_param("name", header="content-disposition")
            payload = part.get_payload(decode=True)
            if part.get_filename() is not None:
                files[name] = {"filename": part.get_filename(), "data": payload}
            else:
                fields[name] = payload.decode()
        Store.requests.append(
            {"path": self.path, "auth": self.headers.get("Authorization"), "fields": fields, "files": files}
        )
        status, headers, response = Store.responses.pop(0) if Store.responses else created_response(fields)
        self.send_response(status)
        for name, value in headers.items():
            self.send_header(name, value)
        data = response if isinstance(response, bytes) else json.dumps(response).encode()
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):
        pass


def created_response(fields, created=True):
    return (
        201 if created else 200,
        {"Content-Type": "application/json"},
        {
            "success": True,
            "data": {
                "id": 42,
                "software_id": 12,
                "version": fields.get("version"),
                "channel": fields.get("channel", "stable"),
                "sha256": fields.get("sha256"),
                "created": created,
                "current": fields.get("make_current") == "true",
                "notification": None,
            },
        },
    )


def error_response(status, code, message):
    return status, {"Content-Type": "application/json"}, {"code": code, "message": message, "data": {"status": status}}


if __name__ == "__main__":
    ThreadingHTTPServer(("127.0.0.1", int(sys.argv[1])), Store).serve_forever()
