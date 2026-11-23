"""The knowledge collaborator the conversation injection point talks to.

``ConversationMessages`` used to hold a bare
:class:`~companion_v2.knowledge.retrieval.KnowledgeRetriever`; it now holds this
object, which adds two things without moving any boundary:

* the turn's bounds come from the registered ``knowledge`` configuration group
  instead of the four constants that used to sit in the call site;
* member entries offer bounded, attributed background for natural conversation,
  each with its own governed source reference and no identity or access authority.

A failure inside the optional clue path drops the clues and never the turn.  The
background-material path keeps the existing behaviour: a source that cannot be
published stops the turn instead of silently using unbacked text.
"""

from __future__ import annotations

from companion_v2.conversation.foreground import ContextContribution, ContributionState
from companion_v2.execution.models import digest
from companion_v2.foundation.refs import ScopeKind
from companion_v2.sources.repositories import SourceRepository

from .catalog import CatalogKnowledgeProvider, KnowledgeCatalog
from .configuration import DEFAULTS, KnowledgeSettings, read_settings
from .members import MEMBER_CLUE_INSTRUCTION, MEMBER_CLUE_PURPOSE, select_member_clues
from .retrieval import KnowledgeHit, KnowledgeRetriever, KnowledgeSelection, authorized, publish_material, query_for_context


KNOWLEDGE_OWNER = "knowledge.v2"
# Query-side bound only: the same pre-existing cap on the turn text handed to the
# provider.  Injected material is bounded by the registered turn budget instead.
MAX_QUERY_CHARS = 12000


class KnowledgeContext:
    """Per-installation knowledge collaborator for the conversation turn."""

    def __init__(self, retriever=None, *, catalog=None, runtime=None, settings_reader=None,
                 installation_id=None, official_candidate_reader=None,
                 official_semantic_retriever=None) -> None:
        if retriever is not None and not isinstance(retriever, KnowledgeRetriever):
            retriever = KnowledgeRetriever(retriever)
        self.retriever = retriever
        self.catalog = catalog
        self.runtime = runtime
        self.settings_reader = settings_reader
        self.official_candidate_reader = official_candidate_reader
        self.official_semantic_retriever = official_semantic_retriever
        self.installation_id = installation_id if installation_id is not None else getattr(
            runtime, "installation_id", None)

    # -- settings ------------------------------------------------------------

    def settings(self, context) -> KnowledgeSettings:
        """This turn's bounds; a damaged configuration read falls back to defaults."""
        if self.settings_reader is not None:
            try:
                return self.settings_reader(context)
            except Exception:
                return KnowledgeSettings.from_values(DEFAULTS)
        if self.runtime is None:
            return KnowledgeSettings.from_values(DEFAULTS)
        try:
            return read_settings(self.runtime, context)
        except Exception:
            return KnowledgeSettings.from_values(DEFAULTS)

    def _apply_provider_limits(self, settings: KnowledgeSettings) -> None:
        provider = getattr(self.retriever, "provider", None)
        if isinstance(provider, CatalogKnowledgeProvider):
            provider.apply_limits(source_limit=settings.source_limit,
                                  char_budget=settings.candidate_char_budget)

    # -- turn material -------------------------------------------------------

    def contributions(self, context, *, text, at, batch_id, sources: SourceRepository, audience,
                      official_hits=None, query_text=None):
        """Bounded background material plus bounded member clues for one sealed turn."""
        settings = self.settings(context)
        materials: list[ContextContribution] = []
        materials.extend(self._background(context, settings, text=text, at=at, batch_id=batch_id,
                                         sources=sources, audience=audience,
                                         official_hits=official_hits, query_text=query_text))
        clue = self._clues(context, settings, text=text, at=at, batch_id=batch_id, sources=sources,
                           audience=audience)
        if clue is not None:
            materials.append(clue)
        return tuple(materials)

    def background_contributions(self, context, *, text, at, batch_id, sources, audience,
                                 include_official=True, official_hits=None, query_text=None):
        """Select knowledge separately from members; native turns keep official KB in AstrBot."""
        return self._background(context, self.settings(context), text=text, at=at, batch_id=batch_id,
                                sources=sources, audience=audience, include_official=include_official,
                                official_hits=official_hits, query_text=query_text)

    def core_background_contributions(self, context, *, text, at, batch_id, sources, audience,
                                      official_hits=None, query_text=None):
        """Core replies delegate official KBs to AstrBot unless ``official_in_core`` is on.

        When it is on, V2 queries the selected official knowledge bases with the
        user's actual message instead of relying on AstrBot's retrieval query, so
        a host-side query mismatch cannot silently remove the knowledge reference.
        """
        settings = self.settings(context)
        return self._background(context, settings, text=text, at=at, batch_id=batch_id,
                                sources=sources, audience=audience,
                                include_official=settings.official_in_core,
                                official_hits=official_hits, query_text=query_text)

    def member_contribution(self, context, *, text, at, batch_id, sources, audience):
        """Select attributed member background independently of optional knowledge retrieval."""
        return self._clues(context, self.settings(context), text=text, at=at, batch_id=batch_id,
                           sources=sources, audience=audience)

    def _background(self, context, settings, *, text, at, batch_id, sources, audience,
                    include_official=True, official_hits=None, query_text=None):
        if not settings.inject_enabled:
            return ()
        self._apply_provider_limits(settings)
        retriever = self.retriever
        query = query_for_context(
            str(text or "")[:MAX_QUERY_CHARS],
            scope=context.scope,
            role_id=context.role_binding.role.subject_id,
            persona_id=context.role_binding.role.subject_id,
            subject=context.actor.subject,
            audience=context.audience.recipients,
            max_hits=settings.inject_max_entries,
            max_chars=settings.inject_max_chars,
            max_per_document=2,
        )
        semantic_hits = None
        if include_official and settings.source_ids:
            if official_hits is not None:
                semantic_hits = tuple(official_hits)
            elif self.official_semantic_retriever is not None:
                semantic_query = str(text if query_text is None else query_text or "")[:MAX_QUERY_CHARS]
                try:
                    semantic_hits = self.official_semantic_retriever(
                        semantic_query,
                        settings.knowledge_source_ids(),
                        source_limit=settings.source_limit,
                        max_hits=settings.inject_max_entries,
                        entry_char_limit=settings.entry_char_limit,
                        char_budget=settings.inject_max_chars,
                    )
                except Exception:
                    semantic_hits = None
            if semantic_hits is None and self.official_candidate_reader is not None:
                try:
                    base_candidates = retriever.candidates() if retriever is not None else ()
                    used_chars = sum(len(item.text) for item in base_candidates)
                    official_candidates = self.official_candidate_reader(
                        settings.knowledge_source_ids(),
                        source_limit=settings.source_limit,
                        entry_limit=settings.entry_limit,
                        entry_char_limit=settings.entry_char_limit,
                        char_budget=max(0, settings.candidate_char_budget - used_chars),
                    )
                    retriever = KnowledgeRetriever((*base_candidates, *official_candidates))
                except Exception:
                    # Official knowledge is optional context. A missing or busy host
                    # index must not prevent the underlying conversation from sealing.
                    official_candidates = ()
        if semantic_hits is not None:
            selection = self._merge_semantic_selection(retriever, query, semantic_hits)
            if sources is not None and selection.hits:
                selection = (retriever or KnowledgeRetriever(())).publish_selection(
                    selection, sources=sources, audience=audience, observed_at=at,
                    source_prefix="knowledge:" + batch_id)
        else:
            if retriever is None:
                return ()
            selection = retriever.retrieve(query, sources=sources, audience=audience, observed_at=at,
                                           source_prefix="knowledge:" + batch_id)
        return tuple(
            ContextContribution(
                "knowledge:" + batch_id + ":" + hit.candidate.candidate_id,
                KNOWLEDGE_OWNER,
                "knowledge_reference:" + hit.candidate.candidate_id,
                ContributionState.READY,
                hit.text,
                hit.source,
                False,
            )
            for hit in selection.hits
        )

    @staticmethod
    def _merge_semantic_selection(retriever, query, semantic_hits) -> KnowledgeSelection:
        """Preserve host-ranked semantic hits while keeping local worldbook matches."""
        local_selection = retriever.select(query) if retriever is not None else KnowledgeSelection(query, ())
        authorized_semantic = [candidate for candidate in semantic_hits if authorized(candidate, query)]
        # Do not let unsampled lexical fallback hide actual semantic KB matches.
        local_hits = () if (authorized_semantic and local_selection.sampled) else local_selection.hits
        selected: list[KnowledgeHit] = []
        seen_text: set[str] = set()
        characters = 0
        for hit in local_hits:
            normalized = " ".join(hit.candidate.text.casefold().split())
            if normalized in seen_text:
                continue
            separator = 2 if selected else 0
            if len(selected) >= query.max_hits or characters + separator + len(hit.candidate.text) > query.max_chars:
                continue
            selected.append(hit)
            seen_text.add(normalized)
            characters += separator + len(hit.candidate.text)
        for rank, candidate in enumerate(authorized_semantic):
            if len(selected) >= query.max_hits:
                break
            normalized = " ".join(candidate.text.casefold().split())
            if not normalized or normalized in seen_text:
                continue
            separator = 2 if selected else 0
            if characters + separator + len(candidate.text) > query.max_chars:
                continue
            selected.append(KnowledgeHit(candidate, max(1, len(authorized_semantic) - rank), sampled=False))
            seen_text.add(normalized)
            characters += separator + len(candidate.text)
        sampled = local_selection.sampled and not authorized_semantic and bool(selected)
        return KnowledgeSelection(query, tuple(selected), sampled=sampled, characters=characters)

    def _clues(self, context, settings, *, text, at, batch_id, sources, audience):
        """Optional member clues: any failure drops them instead of the turn."""
        if self.catalog is None or not settings.member_recognition_enabled:
            return None
        try:
            entries = self.catalog.member_entries(
                installation_id=self.installation_id, source_limit=settings.source_limit,
                role_id=context.role_binding.role.subject_id,
                scope_ids=(context.scope.native_scope_id,), scope_kind=context.scope.kind,
                scope=context.scope)
            if not entries:
                return None
            selection = select_member_clues(
                entries,
                text=str(text or ""),
                native_account_id=getattr(context.actor.principal, "native_account_id", ""),
                limit=settings.member_inject_limit,
                match_aliases=settings.member_match_aliases,
            )
            if selection.empty:
                return None
            payload = selection.prompt(char_limit=settings.member_clue_chars,
                                       private_chat=context.scope.kind is ScopeKind.PRIVATE)
            if not payload:
                return None
            reference = publish_material(
                sources,
                text=payload,
                audience=audience,
                observed_at=at,
                key="member-clues:" + digest([clue.entry.entry_id for clue in selection.clues]),
                owner=KNOWLEDGE_OWNER,
                source_prefix="knowledge-clue:" + batch_id,
            )
        except Exception:
            return None
        return ContextContribution("knowledge-clues:" + batch_id, KNOWLEDGE_OWNER, MEMBER_CLUE_PURPOSE,
                                   ContributionState.READY, payload, reference, False)


def as_knowledge_context(value, *, runtime=None, catalog=None, settings_reader=None):
    """Accept an existing retriever, an iterable of candidates, or a full context."""
    if isinstance(value, KnowledgeContext):
        return value
    if runtime is not None and catalog is None:
        catalog = KnowledgeCatalog(runtime)
    return KnowledgeContext(value, catalog=catalog, runtime=runtime, settings_reader=settings_reader)


def knowledge_context(runtime, *, settings_reader=None, catalog=None, official_candidate_reader=None,
                      official_semantic_retriever=None):
    """Catalogue-backed context: imported sources plus member clues."""
    catalog = catalog if catalog is not None else KnowledgeCatalog(runtime)
    settings = KnowledgeSettings.from_values(DEFAULTS)
    provider = CatalogKnowledgeProvider(catalog, installation_id=runtime.installation_id,
                                        source_limit=settings.source_limit,
                                        char_budget=settings.candidate_char_budget)
    return KnowledgeContext(KnowledgeRetriever(provider), catalog=catalog, runtime=runtime,
                            settings_reader=settings_reader,
                            official_candidate_reader=official_candidate_reader,
                            official_semantic_retriever=official_semantic_retriever)


__all__ = [
    "KNOWLEDGE_OWNER", "KnowledgeContext", "MEMBER_CLUE_INSTRUCTION", "MEMBER_CLUE_PURPOSE",
    "as_knowledge_context", "knowledge_context",
]
