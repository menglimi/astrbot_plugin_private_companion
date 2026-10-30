"""Dedicated model route for native Content creation."""

from companion_v2.execution.models import Lane
from companion_v2.model_gateway.models import ModelTaskProfile


DRAFT_KIND = "content.authoring.draft"
WRITING_KINDS = frozenset({DRAFT_KIND})
# One ceiling, two derived layers. The writing task declares AUTHORING_BUDGET_SECONDS
# and the model round that fills it must expire first, leaving AUTHORING_MARGIN_SECONDS
# for parsing and validating the returned draft. Without that gap both layers ended at
# the same instant, the supervising task always won the race (it starts first), and a
# slow writing model was reported as the supervisor's `timeout` instead of naming the
# model-level stop it actually hit.
AUTHORING_BUDGET_SECONDS = 180
AUTHORING_MARGIN_SECONDS = 30
AUTHORING_PROVIDER_TIMEOUT_SECONDS = AUTHORING_BUDGET_SECONDS - AUTHORING_MARGIN_SECONDS

PROMPTS = {
    DRAFT_KIND: "以当前角色完成用户明确要求的作品，延续时保持既有人物、主线与人工修订。",
}


def authoring_profiles(provider, *, draft_tokens=4096,
                       timeout_seconds=AUTHORING_PROVIDER_TIMEOUT_SECONDS):
    return (ModelTaskProfile(DRAFT_KIND, Lane.BACKGROUND, provider, PROMPTS[DRAFT_KIND],
                             max_output_tokens=draft_tokens, timeout_seconds=timeout_seconds),)


__all__ = ["AUTHORING_BUDGET_SECONDS", "AUTHORING_MARGIN_SECONDS",
           "AUTHORING_PROVIDER_TIMEOUT_SECONDS", "DRAFT_KIND", "PROMPTS", "WRITING_KINDS",
           "authoring_profiles"]
