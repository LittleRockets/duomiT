"""Trusted, in-process node reference runtime; no plugin loading or arbitrary code.

Node handles are local capability objects, not distributed authentication tokens.
Workspaces and message queues are isolated in-memory objects, not OS sandboxes.
The graph holds membership and communication scope, never workflow step states.
"""

from collections import deque
import uuid

from .contracts import NodeDescriptor, NodeMessage, RequestContext, digest, identifier
from .errors import DuomitError, require
from .ports import DesktopPort


class NodeRegistry:
    def __init__(self):
        self._nodes = {}
        self._draining = set()

    @staticmethod
    def _key(context, node_id, version):
        return context.tenant_id, context.principal_id, node_id, version

    def register(self, context: RequestContext, descriptor: NodeDescriptor):
        require(isinstance(descriptor, NodeDescriptor), "Invalid node descriptor")
        key = self._key(context, descriptor.node_id, descriptor.version)
        previous = self._nodes.get(key)
        if previous is not None and previous != descriptor:
            raise DuomitError("CONFLICT", "Registered node version is immutable")
        self._nodes[key] = descriptor

    def drain(self, context: RequestContext, node_id: str, version: str):
        key = self._key(context, node_id, version)
        if key not in self._nodes:
            raise DuomitError("NOT_FOUND", "Node is not available")
        self._draining.add(key)

    def activate(self, context: RequestContext, run_id: str,
                 node_versions: tuple[tuple[str, str], ...], edges: tuple[tuple[str, str], ...],
                 desktop: DesktopPort, *, message_budget: int = 100):
        identifier(run_id)
        require(type(node_versions) is tuple and 0 < len(node_versions) <= 64, "Invalid graph nodes")
        descriptors = []
        for item in node_versions:
            require(type(item) is tuple and len(item) == 2, "Invalid node version")
            key = self._key(context, item[0], item[1])
            if key not in self._nodes:
                raise DuomitError("NOT_FOUND", "Node is not available")
            if key in self._draining:
                raise DuomitError("NODE_DRAINING", "Node is not accepting new activations")
            descriptors.append(self._nodes[key])
        return NodeSession(context, run_id, tuple(descriptors), edges, desktop, message_budget)


class NodeSession:
    def __init__(self, context, run_id, descriptors, edges, desktop, message_budget):
        names = {item.node_id for item in descriptors}
        require(len(names) == len(descriptors), "Duplicate graph node")
        require(type(edges) is tuple and len(edges) <= 256, "Invalid graph edges")
        for edge in edges:
            require(type(edge) is tuple and len(edge) == 2 and edge[0] in names and edge[1] in names,
                    "Graph edge references a non-member")
        require(len(set(edges)) == len(edges), "Duplicate graph edge")
        require(type(message_budget) is int and 0 < message_budget <= 10000, "Invalid message budget")
        self.graph_id = uuid.uuid4().hex
        self.run_id = run_id
        self._context = context
        self._desktop = desktop
        self._edges = frozenset(edges)
        self._remaining = message_budget
        self._sequence = 0
        self._closed = False
        self._handles = {item.node_id: NodeHandle(self, item) for item in descriptors}

    def _active(self):
        if self._closed:
            raise DuomitError("GRAPH_CLOSED", "Node activation has ended")

    def handle(self, node_id: str):
        self._active()
        if node_id not in self._handles:
            raise DuomitError("NOT_FOUND", "Node is not a graph member")
        return self._handles[node_id]

    def close(self):
        self._closed = True
        failed = False
        for handle in self._handles.values():
            for requirement, lease in tuple(handle._leases.items()):
                try:
                    self._desktop.release(self._context, lease.lease_id, lease.fence)
                except Exception:
                    failed = True
                else:
                    del handle._leases[requirement]
            handle._workspace.clear()
            handle._messages.clear()
        if failed:
            raise DuomitError("LEASE_RELEASE_FAILED", "Some leases still require cleanup", True)


class NodeHandle:
    def __init__(self, session: NodeSession, descriptor: NodeDescriptor):
        self._session = session
        self._descriptor = descriptor
        self._leases = {}
        self._workspace = {}
        self._messages = deque()

    @property
    def descriptor(self):
        return self._descriptor

    def _active(self):
        self._session._active()
        if self._session._handles.get(self.descriptor.node_id) is not self:
            raise DuomitError("CAPABILITY_DENIED", "Node handle was not issued for this graph")

    def material(self, extension_id: str, version: str) -> bytes:
        session = self._session
        self._active()
        requirement = (extension_id, version)
        if requirement not in self.descriptor.required_extensions:
            raise DuomitError("CAPABILITY_DENIED", "Node did not declare this extension")
        if requirement not in self._leases:
            key = digest({"graph": session.graph_id, "node": self.descriptor.node_id,
                          "extension": extension_id, "version": version})
            self._leases[requirement] = session._desktop.acquire(
                session._context, extension_id, version, session.run_id, self.descriptor.node_id, key)
        lease = self._leases[requirement]
        return session._desktop.read(session._context, lease.lease_id, lease.fence,
                                     session.run_id, self.descriptor.node_id)

    def renew_material(self, extension_id: str, version: str):
        self._active()
        requirement = (extension_id, version)
        if requirement not in self._leases:
            raise DuomitError("NOT_FOUND", "Node has not acquired this material")
        lease = self._leases[requirement]
        self._leases[requirement] = self._session._desktop.renew(
            self._session._context, lease.lease_id, lease.fence)

    def put(self, key: str, value: str):
        self._active()
        identifier(key)
        require(isinstance(value, str) and len(value) <= 65536, "Invalid workspace text")
        require(key in self._workspace or len(self._workspace) < 64, "Workspace item limit reached")
        self._workspace[key] = value

    def get(self, key: str) -> str | None:
        self._active()
        return self._workspace.get(key)

    def send(self, target, text: str) -> NodeMessage:
        session = self._session
        self._active()
        if not isinstance(target, NodeHandle) or target._session is not session:
            raise DuomitError("CAPABILITY_DENIED", "Target is outside this graph")
        target._active()
        if (self.descriptor.node_id, target.descriptor.node_id) not in session._edges:
            raise DuomitError("CAPABILITY_DENIED", "Graph edge is not authorized")
        require(isinstance(text, str) and 0 < len(text.strip()) <= 16384, "Invalid node message")
        if session._remaining == 0:
            raise DuomitError("BUDGET_EXCEEDED", "Graph message budget is exhausted")
        session._remaining -= 1
        session._sequence += 1
        message = NodeMessage(session.graph_id, session.run_id, self.descriptor.node_id,
                              target.descriptor.node_id, session._sequence, text)
        target._messages.append(message)
        return message

    def receive(self) -> NodeMessage | None:
        self._active()
        return self._messages.popleft() if self._messages else None
