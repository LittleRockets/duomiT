"""Assistant text interface. It has no workflow executor or implicit memory access."""

from .contracts import AssistantBinding, ChatMessage, ChatRequest, ModelResponse, RequestContext
from .ports import AssistantAccessPort


class AssistantService:
    def __init__(self, access: AssistantAccessPort, binding: AssistantBinding):
        self._access = access
        self._binding = binding

    def respond(self, context: RequestContext, messages: tuple[ChatMessage, ...]) -> ModelResponse:
        request = ChatRequest(messages,
                              "Help the user understand, summarize or review the supplied text. "
                              "Your answer is advice only. Do not claim to execute tools, "
                              "approve operations or change workflow state.",
                              self._binding.max_output_tokens)
        return self._access.complete(context, self._binding, request)
