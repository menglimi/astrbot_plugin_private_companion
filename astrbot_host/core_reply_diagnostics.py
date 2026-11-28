"""Keep existing local diagnostics available through the native tool runner."""

from companion_v2.conversation.diagnostic_tools import diagnostic_query, proposal_document
from companion_v2.diagnostics.models import DiagnosticContext, DiagnosticError
from companion_v2.diagnostics.repairs import RepairProposalService
from companion_v2.diagnostics.service import report_document
from companion_v2.execution.models import TaskError, digest, require

from .core_reply_tool_execution import executor_for


async def execute_core_diagnostic(session, call_id, action, arguments):
    supported = {"diagnostics.inspect", "diagnostics.explain", "diagnostics.propose", "diagnostics.proposal.read",
                 "diagnostics.proposal.confirm", "diagnostics.proposal.cancel"}
    if action not in supported:
        raise TaskError("invalid_tool_arguments", "the diagnostic action requires its declared arguments")
    app, context = session.application, session.context
    diagnostics = app.tools.diagnostics
    original = DiagnosticContext(context.actor, context.role_binding, context.audience)
    repairs = RepairProposalService(diagnostics)
    if action in ("diagnostics.proposal.confirm", "diagnostics.proposal.cancel"):
        command = session.state.get("functional_command")
        expected_intent = "diagnostics_confirm" if action.endswith("confirm") else "diagnostics_cancel"
        if (getattr(command, "surface", None) != "private" or getattr(command, "intent", None) != expected_intent
                or getattr(command, "argument", None) != arguments.get("proposal_id")
                or context.scope.kind.value != "private"):
            raise TaskError("explicit_confirmation_required", "repair confirmation requires the exact original private command and proposal ID")
    if action == "diagnostics.inspect":
        try:
            query = diagnostic_query(original, arguments, diagnostics, diagnostics.now())
        except (DiagnosticError, KeyError, TypeError, ValueError) as error:
            raise TaskError(getattr(error, "code", "invalid_tool_arguments"), "the bounded diagnostic query is invalid") from error
    else:
        query = None

    def command(identifier):
        if action == "diagnostics.inspect":
            report = require(diagnostics.inspect(original, query))
            return {"state": "succeeded", "effect": "none", "report": report_document(report)}
        if action == "diagnostics.explain":
            report = require(diagnostics.explain(original, arguments["topic"]))
            return {"state": "succeeded", "effect": "none", "report": report_document(report)}
        if action == "diagnostics.propose":
            proposal_id = "repair-" + digest([session.turn_id, identifier, arguments])[:32]
            proposal = require(repairs.propose(original, proposal_id=proposal_id, report_id=arguments["report_id"],
                changes=arguments["changes"], rationale=arguments["rationale"]))
            return {"state": "succeeded", "effect": "committed", "proposal": proposal_document(proposal)}
        if action == "diagnostics.proposal.read":
            resolved_id = require(repairs.resolve_proposal_id(original, arguments["proposal_id"]))
            proposal = require(repairs.read(original, proposal_id=resolved_id))
            return {"state": "succeeded", "effect": "none", "proposal": proposal_document(proposal)}
        if action in ("diagnostics.proposal.confirm", "diagnostics.proposal.cancel"):
            resolved_id = require(repairs.resolve_proposal_id(original, arguments["proposal_id"]))
            proposal = require(repairs.read(original, proposal_id=resolved_id))
            finish = repairs.confirm if action.endswith("confirm") else repairs.cancel
            result = require(finish(original, proposal_id=proposal.proposal_id,
                expected_revision=proposal.revision, review_token=proposal.review_token))
            effect = "committed" if result.state in ("confirmed", "cancelled") else "none"
            return {"state": "succeeded", "effect": effect, "proposal": proposal_document(result)}
        raise TaskError("core_tool_unavailable", "the diagnostic action is unavailable")

    result = executor_for(app).local(session, call_id, action, arguments, command)
    if result.get("state") == "succeeded":
        if action in ("diagnostics.inspect", "diagnostics.explain"):
            require(diagnostics.validate_snapshot(original, result["report"], query=query,
                topic=arguments.get("topic")))
        elif action in ("diagnostics.propose", "diagnostics.proposal.read"):
            current = require(repairs.read(original, proposal_id=result["proposal"]["proposal_id"]))
            if proposal_document(current) != result["proposal"]:
                raise TaskError("repair_proposal_changed", "proposal changed before returning the review")
    return result
