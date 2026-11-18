"""Offline, fixed-node Desktop lifecycle demonstration; no LLM or tool execution."""

import argparse
import hashlib
import json
import uuid

from .contracts import ExtensionVersion, NodeDescriptor, RequestContext
from .desktop import SqliteDesktop
from .nodes import NodeRegistry


def run_demo(database):
    context = RequestContext("demo-tenant", "demo-user", uuid.uuid4().hex, uuid.uuid4().hex)
    body = b"Reference material for the offline node demonstration."
    extension = ExtensionVersion("demo-document", "1.0", "document",
                                 hashlib.sha256(body).hexdigest(), "Apache-2.0")
    desktop = SqliteDesktop(database)
    try:
        desktop.publish(context, extension, body)
        registry = NodeRegistry()
        for name in ("prepare", "review"):
            registry.register(context, NodeDescriptor(name, "reference-domain", "1.0",
                                                      (("demo-document", "1.0"),)))
        session = registry.activate(context, uuid.uuid4().hex,
                                    (("prepare", "1.0"), ("review", "1.0")),
                                    (("prepare", "review"), ("review", "prepare")), desktop)
        try:
            prepare, review = session.handle("prepare"), session.handle("review")
            prepare.put("draft", "private draft")
            prepare.send(review, "Review the shared reference material")
            request = review.receive()
            review.send(prepare, "Reference received; review complete")
            response = prepare.receive()
            result = {"graph_id": session.graph_id, "run_id": session.run_id,
                      "shared_material_verified": prepare.material("demo-document", "1.0") ==
                          review.material("demo-document", "1.0") == body,
                      "workspaces_isolated": review.get("draft") is None,
                      "request": request.text, "response": response.text,
                      "active_leases_before_close": desktop.stats(context, "demo-document", "1.0")["active_leases"]}
        finally:
            session.close()
        result["active_leases_after_close"] = desktop.stats(context, "demo-document", "1.0")["active_leases"]
        desktop.collect(context, grace_seconds=0)
        result["caches_collected"] = desktop.collect(context, grace_seconds=0)
        return result
    finally:
        desktop.close()


def main():
    parser = argparse.ArgumentParser(description="duomit offline node and mirror-lease demonstration")
    parser.add_argument("--database", default="desktop-demo.sqlite3")
    args = parser.parse_args()
    print(json.dumps(run_demo(args.database), indent=2))


if __name__ == "__main__":
    main()
