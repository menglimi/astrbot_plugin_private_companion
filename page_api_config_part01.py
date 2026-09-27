# -*- coding: utf-8 -*-
"""PrivateCompanionPageApiConfigPart01Mixin。

由 tools/split_mixin_domain.py 从 page_api_config.py 机械抽取（2 个方法 + 0 个模块级名字 + 0 个类级赋值 / 529 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 PrivateCompanionPageApiConfigMixin）。
"""
from __future__ import annotations

from .page_api_config_shared import logger
from .page_api_config_shared import Any
from .page_api_config_shared import CatalogValidationError
from .page_api_config_shared import StoryAuthorityError
from .page_api_config_shared import asyncio
from .page_api_config_shared import build_route_bindings
from .page_api_config_shared import request
from .page_api_config_shared import story_authority_controller
from .page_api_config_shared import time



class PrivateCompanionPageApiConfigPart01Mixin:
    """PrivateCompanionPageApiConfigPart01Mixin（从 PrivateCompanionPageApiConfigMixin 拆出）。"""


    def route_bindings(self) -> list[tuple[str, Any, list[str], str]]:
        """Return the wrapped page handlers used by every transport."""
        routes = [
            ("/overview", self.get_overview, ["GET"], "Private Companion Page overview"),
            ("/calendar", self.get_calendar, ["GET"], "Private Companion Page long-lived calendar"),
            ("/calendar/conflicts", self.get_calendar_conflicts, ["GET"], "Private Companion Page calendar conflicts"),
            ("/calendar/preview", self.preview_calendar, ["POST"], "Private Companion Page preview calendar record"),
            ("/calendar/upsert", self.upsert_calendar, ["POST"], "Private Companion Page create or update calendar record"),
            ("/calendar/cancel", self.cancel_calendar, ["POST"], "Private Companion Page cancel calendar record"),
            ("/calendar/delete", self.cancel_calendar, ["POST"], "Private Companion Page cancel calendar record alias"),
            ("/calendar/candidates/confirm", self.confirm_calendar_candidate, ["POST"], "Private Companion Page confirm calendar candidate"),
            ("/calendar/candidates/reject", self.reject_calendar_candidate, ["POST"], "Private Companion Page reject calendar candidate"),
            ("/extension-migration-notice", self.get_extension_migration_notice, ["GET"], "Private Companion Page extension migration notice preference"),
            ("/extension-migration-notice/update", self.update_extension_migration_notice, ["POST"], "Private Companion Page update extension migration notice preference"),
            ("/task-prompts", self.get_task_prompts, ["GET"], "Private Companion Page plugin task prompt catalog"),
            ("/task-prompts/update", self.update_task_prompts, ["POST"], "Private Companion Page update plugin task prompt overrides"),
            ("/expression-library", self.get_expression_library, ["GET"], "Private Companion Page expression library"),
            ("/expression-library/update", self.update_expression_library, ["POST"], "Private Companion Page update expression library"),
            ("/expression-library/share", self.share_expression_library, ["POST"], "Private Companion Page share expression library"),
            ("/expression-library/import/preview", self.preview_expression_library_import, ["POST"], "Private Companion Page preview expression library import"),
            ("/expression-library/import/apply", self.apply_expression_library_import, ["POST"], "Private Companion Page apply expression library import"),
            ("/users", self.list_users, ["GET"], "Private Companion Page users"),
            ("/user", self.get_user, ["GET"], "Private Companion Page user detail"),
            ("/user/update", self.update_user, ["POST"], "Private Companion Page update user"),
            ("/user/delete", self.delete_user, ["POST"], "Private Companion Page delete user"),
            ("/user/identity/link", self.link_unified_identity, ["POST"], "Private Companion Page detached identity relink preview/apply"),
            ("/user/identity/unlink", self.unlink_unified_identity, ["POST"], "Private Companion Page unified identity unlink preview/apply"),
            ("/user/identity/archive", self.archive_unified_person, ["POST"], "Private Companion Page unified person archive preview/apply"),
            ("/user/identity/delete", self.delete_unified_person, ["POST"], "Private Companion Page archived person physical purge preview/apply"),
            ("/user/identity/pending", self.update_pending_identity_review, ["POST"], "Private Companion Page defer/restore pending identity review"),
            ("/user/identity/merge-preview", self.preview_unified_identity_merge, ["POST"], "Private Companion Page unified person merge preview"),
            ("/groups", self.list_groups, ["GET"], "Private Companion Page groups"),
            ("/group", self.get_group, ["GET"], "Private Companion Page group detail"),
            ("/group/update", self.update_group, ["POST"], "Private Companion Page update group"),
            ("/group/delete", self.delete_group, ["POST"], "Private Companion Page delete group"),
            ("/group/slang/update", self.update_group_slang, ["POST"], "Private Companion Page update group slang"),
            ("/group/member-safety", self.get_group_member_safety, ["GET"], "Private Companion Page group member safety"),
            ("/group/member-safety/action", self.update_group_member_safety, ["POST"], "Private Companion Page update group member safety"),
            ("/settings/update", self.update_settings, ["POST"], "Private Companion Page update settings"),
            ("/reality-touch", self.get_reality_touch, ["GET"], "Private Companion Page reality touch status"),
            ("/reality-touch/update", self.update_reality_touch, ["POST"], "Private Companion Page update reality touch alarm"),
            ("/settings/swap_image_api", self.swap_image_api_settings, ["POST"], "Private Companion Page swap image API settings"),
            ("/extensions/image/status", self.get_image_extension_status, ["GET"], "Private Companion Page image extension status"),
            ("/image/debug", self.get_image_debug, ["GET"], "Private Companion Page image generation debug trace"),
            ("/image_api/status", self.get_image_api_status, ["GET"], "Private Companion Page image API status"),
            ("/image_api/test", self.test_image_api_endpoint, ["POST"], "Private Companion Page test one image API endpoint"),
            ("/config/export", self.export_migration_config, ["GET"], "Private Companion Page export migration config"),
            ("/config/backups", self.list_migration_backups, ["GET"], "Private Companion Page list migration backups"),
            ("/config/restore", self.restore_migration_backup, ["POST"], "Private Companion Page restore migration backup"),
            ("/config/import/preview", self.preview_migration_config_import, ["POST"], "Private Companion Page preview migration config import"),
            ("/config/import/apply", self.apply_migration_config_import, ["POST"], "Private Companion Page apply migration config import"),
            ("/proactive_only/unlock", self.update_proactive_only_unlock, ["POST"], "Private Companion Page proactive-only temporary unlock"),
            ("/proactive/candidate/delete", self.delete_proactive_candidate, ["POST"], "Private Companion Page delete proactive candidate"),
            ("/proactive/candidate/prune", self.prune_proactive_candidates, ["POST"], "Private Companion Page prune proactive candidates"),
            ("/extensions/status", self.get_extension_control_plane_status, ["GET"], "Private Companion Page extension control-plane status"),
            ("/diagnostics", self.get_diagnostics, ["GET"], "Private Companion Page diagnostics"),
            ("/troubleshooting", self.get_troubleshooting, ["GET"], "Private Companion Page troubleshooting"),
            ("/daily-review", self.get_daily_review, ["GET"], "Private Companion Page daily review"),
            ("/daily-review/run", self.run_daily_review, ["POST"], "Private Companion Page run daily review"),
            ("/daily-review/guidance", self.update_daily_review_guidance, ["POST"], "Private Companion Page update daily review guidance"),
            ("/troubleshooting/warnings/update", self.update_troubleshooting_warning_suppression, ["POST"], "Private Companion Page update troubleshooting warning suppression"),
            ("/troubleshooting/test", self.run_troubleshooting_test, ["POST"], "Private Companion Page troubleshooting test"),
            ("/token/stats", self.get_token_stats, ["GET"], "Private Companion Page token stats"),
            ("/token/reset", self.reset_token_stats, ["POST"], "Private Companion Page reset token stats"),
            ("/image_cache/list", self.list_image_cache, ["GET"], "Private Companion Page image cache list"),
            ("/image_cache/preview", self.get_image_cache_preview, ["GET"], "Private Companion Page image cache preview"),
            ("/image_cache/preview_data", self.get_image_cache_preview_data, ["GET"], "Private Companion Page image cache preview data"),
            ("/image_cache/thumbnail_data", self.get_image_cache_thumbnail_data, ["GET"], "Private Companion Page image cache thumbnail data"),
            ("/image_cache/update", self.update_image_cache_item, ["POST"], "Private Companion Page update image cache item"),
            ("/image_cache/delete", self.delete_image_cache_item, ["POST"], "Private Companion Page delete image cache item"),
            ("/image_cache/bulk_delete", self.bulk_delete_image_cache_items, ["POST"], "Private Companion Page bulk delete image cache items"),
            ("/reaction_library/list", self.list_reaction_library, ["GET"], "Private Companion Page reaction library list"),
            ("/reaction_library/image_data", self.get_reaction_library_image_data, ["GET"], "Private Companion Page reaction library image data"),
            ("/reaction_library/import", self.import_reaction_library, ["POST"], "Private Companion Page reaction library import"),
            ("/reaction_library/analyze", self.analyze_reaction_library, ["POST"], "Private Companion Page reaction library analyze"),
            ("/reaction_library/update", self.update_reaction_library, ["POST"], "Private Companion Page reaction library update"),
            ("/reaction_library/delete", self.delete_reaction_library, ["POST"], "Private Companion Page reaction library delete"),
            ("/reaction_library/rescan", self.rescan_reaction_library, ["POST"], "Private Companion Page reaction library rescan"),
            ("/reaction_assets/list", self.list_owned_reaction_assets, ["GET"], "Private Companion Page owned reaction assets"),
            ("/reaction_assets/image_data", self.get_owned_reaction_asset_image_data, ["GET"], "Private Companion Page owned reaction asset image"),
            ("/photo_reference/list", self.list_photo_references, ["GET"], "Private Companion Page photo reference list"),
            ("/photo_reference/image_data", self.get_photo_reference_image_data, ["GET"], "Private Companion Page photo reference image data"),
            ("/photo_reference/upload", self.upload_photo_reference, ["POST"], "Private Companion Page upload photo reference image"),
            ("/wardrobe/describe", self.describe_wardrobe_image, ["POST"], "Private Companion Page describe wardrobe garment image"),
            ("/wardrobe/outfit-preview", self.preview_wardrobe_outfit, ["POST"], "Private Companion Page preview wardrobe outfit injection"),
            ("/wardrobe/drafts", self.list_wardrobe_drafts, ["POST"], "Private Companion Page list wardrobe drafts"),
            ("/wardrobe/draft-apply", self.confirm_wardrobe_draft, ["POST"], "Private Companion Page apply wardrobe draft"),
            ("/wardrobe/draft-reject", self.reject_wardrobe_draft, ["POST"], "Private Companion Page reject wardrobe draft"),
            ("/wardrobe/asset-image", self.get_wardrobe_asset_image, ["POST"], "Private Companion Page wardrobe asset image data"),
            ("/wardrobe/intent", self.get_wardrobe_intent, ["POST"], "Private Companion Page wardrobe session intent"),
            ("/wardrobe/intent-clear", self.clear_wardrobe_intent, ["POST"], "Private Companion Page clear wardrobe session intent"),
            ("/photo_reference/metadata/compile", self.compile_photo_reference_metadata, ["POST"], "Compile guided photo reference metadata"),
            ("/photo_reference/metadata/review", self.review_photo_reference_metadata, ["POST"], "Review and merge guided photo reference answers"),
            ("/photo_reference/selection_trial", self.run_photo_reference_selection_trial, ["POST"], "Run side-effect-free photo reference selection trial"),
            ("/reference_asset/list", self.list_reference_assets, ["GET"], "Private Companion Page scoped visual reference assets"),
            ("/reference_asset/image_data", self.get_reference_asset_image_data, ["GET"], "Private Companion Page scoped visual reference image data"),
            ("/reference_asset/upload", self.upload_reference_asset, ["POST"], "Private Companion Page upload scoped visual reference"),
            ("/reference_asset/update", self.update_reference_asset, ["POST"], "Private Companion Page update scoped visual reference"),
            ("/reference_asset/delete", self.delete_reference_asset, ["POST"], "Private Companion Page delete scoped visual reference"),
            ("/worldbook/member/reference/list", self.list_reference_assets, ["GET"], "Private Companion Page worldbook member reference list"),
            ("/worldbook/member/reference/upload", self.upload_reference_asset, ["POST"], "Private Companion Page worldbook member reference upload"),
            ("/worldbook/member/reference/update", self.update_reference_asset, ["POST"], "Private Companion Page worldbook member reference update"),
            ("/worldbook/member/reference/delete", self.delete_reference_asset, ["POST"], "Private Companion Page worldbook member reference delete"),
            ("/knowledge/reference/list", self.list_reference_assets, ["GET"], "Private Companion Page knowledge reference list"),
            ("/knowledge/reference/upload", self.upload_reference_asset, ["POST"], "Private Companion Page knowledge reference upload"),
            ("/knowledge/reference/update", self.update_reference_asset, ["POST"], "Private Companion Page knowledge reference update"),
            ("/knowledge/reference/delete", self.delete_reference_asset, ["POST"], "Private Companion Page knowledge reference delete"),
            ("/relationship/role/reference/list", self.list_reference_assets, ["GET"], "Private Companion Page relationship role reference list"),
            ("/relationship/role/reference/image_data", self.get_reference_asset_image_data, ["GET"], "Private Companion Page relationship role reference image data"),
            ("/relationship/role/reference/upload", self.upload_reference_asset, ["POST"], "Private Companion Page relationship role reference upload"),
            ("/relationship/role/reference/update", self.update_reference_asset, ["POST"], "Private Companion Page relationship role reference update"),
            ("/relationship/role/reference/delete", self.delete_reference_asset, ["POST"], "Private Companion Page relationship role reference delete"),
            ("/photo_reference/assets", self.list_photo_reference_assets, ["GET"], "Private Companion Page visual reference assets"),
            ("/photo_reference/assets/list", self.list_photo_reference_assets, ["GET"], "Private Companion Page visual reference asset list"),
            ("/photo_reference/assets/image_data", self.get_photo_reference_asset_image_data, ["GET"], "Private Companion Page visual reference asset image data"),
            ("/photo_reference/assets/upload", self.upload_photo_reference_asset, ["POST"], "Private Companion Page upload visual reference asset"),
            ("/photo_reference/assets/update", self.update_photo_reference_asset, ["POST"], "Private Companion Page update visual reference asset"),
            ("/photo_reference/assets/delete", self.delete_photo_reference_asset, ["POST"], "Private Companion Page delete visual reference asset"),
            ("/daily_outfit/image", self.get_daily_outfit_image, ["GET"], "Private Companion Page daily outfit image"),
            ("/daily_outfit/image_data", self.get_daily_outfit_image_data, ["GET"], "Private Companion Page daily outfit image data"),
            ("/bookshelf/unlock", self.unlock_bookshelf, ["POST"], "Private Companion Page unlock bookshelf"),
            ("/bookshelf/session", self.get_bookshelf_session, ["GET", "POST"], "Private Companion Page restore bookshelf session"),
            ("/bookshelf/image", self.get_bookshelf_image, ["GET"], "Private Companion Page bookshelf image"),
            ("/bookshelf/image_data", self.get_bookshelf_image_data, ["GET"], "Private Companion Page bookshelf image data"),
            ("/bookshelf/delete", self.delete_bookshelf_item, ["POST"], "Private Companion Page delete bookshelf item"),
            ("/bookshelf/rate", self.rate_bookshelf_item, ["POST"], "Private Companion Page rate bookshelf item"),
            ("/bookshelf/tags", self.update_bookshelf_item_tags, ["POST"], "Private Companion Page update bookshelf item tags"),
            ("/bookshelf/comments/update", self.update_bookshelf_item_comments, ["POST"], "Private Companion Page update bookshelf item comments"),
            ("/bookshelf/reading_state", self.update_bookshelf_reading_state, ["POST"], "Private Companion Page update bookshelf reading state"),
            ("/memo/list", self.list_memo_notes, ["GET"], "Private Companion Page list memo notes"),
            ("/memo/update", self.update_memo_note, ["POST"], "Private Companion Page update memo note"),
            ("/qzone/status", self.get_qzone_status, ["GET"], "Private Companion Page qzone status"),
            ("/qzone/health", self.get_qzone_status, ["GET"], "Private Companion Page qzone status alias"),
            ("/qzone/summary", self.get_qzone_status, ["GET"], "Private Companion Page qzone status alias"),
            ("/qzone/state", self.get_qzone_status, ["GET"], "Private Companion Page qzone status alias"),
            ("/qzone/feed", self.get_qzone_feed, ["GET"], "Private Companion Page qzone feed"),
            ("/qzone/feeds", self.get_qzone_feed, ["GET"], "Private Companion Page qzone feed alias"),
            ("/qzone/list", self.get_qzone_feed, ["GET"], "Private Companion Page qzone feed alias"),
            ("/qzone/detail", self.get_qzone_detail, ["GET"], "Private Companion Page qzone detail"),
            ("/qzone/post", self.get_qzone_detail, ["GET"], "Private Companion Page qzone detail alias"),
            ("/qzone/item", self.get_qzone_detail, ["GET"], "Private Companion Page qzone detail alias"),
            ("/qzone/refresh_cookies", self.refresh_qzone_cookies, ["POST"], "Private Companion Page qzone refresh cookies"),
            ("/qzone/refresh-cookies", self.refresh_qzone_cookies, ["POST"], "Private Companion Page qzone refresh cookies alias"),
            ("/qzone/cookies/refresh", self.refresh_qzone_cookies, ["POST"], "Private Companion Page qzone refresh cookies alias"),
            ("/qzone/cookie/refresh", self.refresh_qzone_cookies, ["POST"], "Private Companion Page qzone refresh cookies alias"),
            ("/qzone/refresh", self.refresh_qzone_cookies, ["POST"], "Private Companion Page qzone refresh cookies alias"),
            ("/qzone/publish", self.publish_qzone_post, ["POST"], "Private Companion Page qzone publish"),
            ("/qzone/post/publish", self.publish_qzone_post, ["POST"], "Private Companion Page qzone publish alias"),
            ("/qzone/post", self.publish_qzone_post, ["POST"], "Private Companion Page qzone publish alias"),
            ("/qzone/like", self.like_qzone_post, ["POST"], "Private Companion Page qzone like"),
            ("/qzone/post/like", self.like_qzone_post, ["POST"], "Private Companion Page qzone like alias"),
            ("/qzone/comment", self.comment_qzone_post, ["POST"], "Private Companion Page qzone comment"),
            ("/qzone/post/comment", self.comment_qzone_post, ["POST"], "Private Companion Page qzone comment alias"),
            ("/qzone/delete", self.delete_qzone_post, ["POST"], "Private Companion Page qzone delete"),
            ("/qzone/post/delete", self.delete_qzone_post, ["POST"], "Private Companion Page qzone delete alias"),
            ("/creative/project", self.get_creative_project, ["GET"], "Private Companion Page creative project detail"),
            ("/creative/project/cover", self.get_creative_project_cover, ["GET"], "Private Companion Page creative project cover"),
            ("/creative/project/cover_data", self.get_creative_project_cover_data, ["GET"], "Private Companion Page creative project cover data"),
            ("/creative/project/update", self.update_creative_project, ["POST"], "Private Companion Page update creative project"),
            ("/creative/project/chunk/update", self.update_creative_chunk, ["POST"], "Private Companion Page update creative chunk"),
            ("/creative/project/outline/update", self.update_creative_outline, ["POST"], "Private Companion Page update creative outline"),
            ("/creative/project/characters/update", self.update_creative_characters, ["POST"], "Private Companion Page update creative characters"),
            ("/creative/project/reanalyze", self.reanalyze_creative_project, ["POST"], "Private Companion Page reanalyze creative project"),
            ("/creative/project/rebuild_memory", self.rebuild_creative_memory, ["POST"], "Private Companion Page rebuild creative memory"),
            ("/creative/project/delete", self.delete_creative_project, ["POST"], "Private Companion Page delete creative project"),
            ("/worldbook/import", self.import_worldbook, ["POST"], "Private Companion Page import worldbook"),
            ("/worldbook/member/livingmemory", self.get_worldbook_member_livingmemory, ["GET"], "Private Companion Page worldbook member LivingMemory"),
            ("/worldbook/member/update", self.update_worldbook_member, ["POST"], "Private Companion Page update worldbook member"),
            ("/worldbook/observations/clear", self.clear_worldbook_pending_observations, ["POST"], "Private Companion Page clear worldbook pending observations"),
            ("/worldbook/group/update", self.update_worldbook_group, ["POST"], "Private Companion Page update worldbook group"),
            ("/skill/update", self.update_skill_growth, ["POST"], "Private Companion Page update skill growth"),
            ("/personal_goal/update", self.update_personal_goal, ["POST"], "Private Companion Page update personal goal"),
            ("/food_menu/update", self.update_food_menu, ["POST"], "Private Companion Page update food menu"),
            ("/food_menu/bulk_update", self.bulk_update_food_menu, ["POST"], "Private Companion Page bulk update food menu"),
            ("/food_menu/bulk_delete", self.bulk_delete_food_menu, ["POST"], "Private Companion Page bulk delete food menu"),
            ("/external_ability/update", self.update_external_ability, ["POST"], "Private Companion Page update external proactive ability"),
            ("/setup/apply", self.apply_setup_guide, ["POST"], "Private Companion Page apply first setup guide"),
            ("/setup/daily/run", self.run_setup_daily_generation, ["POST"], "Private Companion Page setup guide daily generation"),
            ("/daily/detail/regenerate", self.regenerate_daily_detail_segment, ["POST"], "Private Companion Page regenerate one daily detail segment"),
            ("/roleplay/personas", self.list_roleplay_personas, ["GET"], "Private Companion Page roleplay personas"),
            ("/persona/migrate", self.migrate_persona_profile, ["POST"], "Private Companion Page migrate persona profile"),
            ("/persona/reset-current", self.reset_current_persona, ["POST"], "Private Companion Page reset current persona"),
            ("/persona/config-state", self.get_persona_config_state, ["GET"], "Private Companion Page persona config state"),
            ("/persona/config/create", self.create_persona_config, ["POST"], "Private Companion Page create persona config"),
            ("/persona/settings/update", self.update_persona_settings, ["POST"], "Private Companion Page update persona settings"),
            ("/persona/config/detach-preview", self.preview_persona_config_detach, ["POST"], "Private Companion Page preview persona config detach"),
            ("/persona/config/detach-apply", self.apply_persona_config_detach, ["POST"], "Private Companion Page apply persona config detach"),
            ("/roleplay/draft_from_persona", self.generate_roleplay_draft_from_persona, ["POST"], "Private Companion Page roleplay draft from persona"),
            ("/roleplay/standardize_persona", self.standardize_persona_from_questionnaire, ["POST"], "Private Companion Page roleplay persona standardization"),
            ("/roleplay/persona_style_scenarios", self.generate_persona_style_scenarios, ["POST"], "Private Companion Page roleplay persona style scenarios"),
            ("/roleplay/persona_style_scenario_retry", self.retry_persona_style_scenario, ["POST"], "Private Companion Page roleplay persona style scenario retry"),
            ("/roleplay/persona_style_summary", self.generate_persona_style_summary, ["POST"], "Private Companion Page roleplay persona style summary"),
            ("/preset/apply", self.apply_preset, ["POST"], "Private Companion Page apply preset"),
            ("/providers/available", self.list_available_providers, ["GET"], "Private Companion Page available providers"),
            ("/provider/test", self.test_provider, ["POST"], "Private Companion Page test provider"),
            ("/tts/providers", self.list_tts_provider_configs, ["GET"], "Private Companion Page TTS provider configs"),
            ("/tts/provider/create", self.create_tts_provider_config, ["POST"], "Private Companion Page create TTS provider"),
            ("/tts/provider/clone", self.clone_tts_provider_config, ["POST"], "Private Companion Page clone TTS provider"),
            ("/tts/provider/update", self.update_tts_provider_config, ["POST"], "Private Companion Page update TTS provider"),
            ("/tts/provider/test", self.test_tts_provider_config, ["POST"], "Private Companion Page test TTS provider"),
        ]
        persona_control_routes = {
            "/extension-migration-notice",
            "/extension-migration-notice/update",
            "/task-prompts",
            "/task-prompts/update",
            "/roleplay/personas",
            "/persona/migrate",
            "/persona/reset-current",
            "/persona/config-state",
            "/persona/config/create",
            "/persona/settings/update",
            "/persona/config/detach-preview",
            "/persona/config/detach-apply",
        }
        return build_route_bindings(
            routes,
            persona_control_routes=persona_control_routes,
            persona_wrapper=self._persona_scoped_route_handler,
            http_wrapper=self._http_status_route_handler,
        )

    async def update_settings(self) -> dict[str, Any]:
        payload = await request.get_json(silent=True) or {}
        mode_transition_snapshot: dict[str, Any] = {}
        mode_transition_committed = False
        story_authority_identity: Any | None = None
        primary_data_warning: dict[str, Any] = {}
        try:
            active_getter = getattr(self.plugin, "_active_persona_scope", None)
            active_persona = str(active_getter() if callable(active_getter) else "").strip()
            primary_getter = getattr(self.plugin, "_primary_persona_id", None)
            primary_persona = str(
                primary_getter()
                if callable(primary_getter)
                else getattr(self.plugin, "plugin_specific_persona_id", "")
            ).strip()
            primary_persona_before = primary_persona
            if (
                active_persona
                and bool(getattr(self.plugin, "enable_multi_persona_mode", False))
                and active_persona != primary_persona
            ):
                # Secondary persona edits must never write AstrBot's shared
                # config.  Reuse the dedicated sparse settings transaction.
                changes: dict[str, Any] = {}
                if "group_access_mode" in payload:
                    mode = str(payload.get("group_access_mode") or "").strip().lower()
                    if mode not in {"whitelist", "blacklist"}:
                        return self._error("group_access_mode 只能是 whitelist 或 blacklist")
                    changes["group_access_mode"] = mode
                if "group_whitelist_ids" in payload:
                    changes["group_whitelist_ids"] = self._normalize_id_list(payload.get("group_whitelist_ids"))
                if "group_blacklist_ids" in payload:
                    changes["group_blacklist_ids"] = self._normalize_id_list(payload.get("group_blacklist_ids"))
                for key, value in (payload.get("features") or {}).items():
                    changes[key] = self._normalize_bool_value(value)
                for key, value in (payload.get("providers") or {}).items():
                    changes[key] = self._single_line(value, 160)
                for key, value in (payload.get("settings") or {}).items():
                    changes[key] = self._normalize_setting_value(key, value)
                manifest_getter = getattr(self.plugin, "_persona_scope_manifest", None)
                manifest = manifest_getter() if callable(manifest_getter) else {}
                persona_changes = {
                    key: value
                    for key, value in changes.items()
                    if isinstance(manifest.get(key), dict) and manifest[key].get("scope") == "persona"
                }
                common_changes = {
                    key: value
                    for key, value in changes.items()
                    if key not in persona_changes
                }
                updater = getattr(self.plugin, "_update_persona_settings_async", None)
                if not callable(updater):
                    return self._error("当前版本不支持人格独立配置", status_code=503)
                persona_result = {"ok": True, "changed": []}
                if persona_changes or payload.get("follow_primary_keys"):
                    persona_result = await updater(
                        active_persona,
                        changes=persona_changes,
                        follow_primary_keys=payload.get("follow_primary_keys") or [],
                        expected_revision=payload.get("expected_revision"),
                    )
                    if not persona_result.get("ok"):
                        return self._error(
                            persona_result.get("message") or "人格配置更新失败",
                            status_code=int(persona_result.get("status_code") or 400),
                        )
                if not common_changes:
                    overview = await self.get_overview()
                    if overview.get("success"):
                        overview["data"]["changed"] = persona_result.get("changed", [])
                        overview["data"]["config_saved"] = True
                    return overview
                # Continue through the established shared-config transaction
                # for common keys while preserving the persona update above.
                payload = {
                    **payload,
                    "features": {key: value for key, value in (payload.get("features") or {}).items() if key in common_changes},
                    "providers": {key: value for key, value in (payload.get("providers") or {}).items() if key in common_changes},
                    "settings": {key: value for key, value in (payload.get("settings") or {}).items() if key in common_changes},
                    "follow_primary_keys": [],
                }
            changed: dict[str, Any] = {}
            if "group_access_mode" in payload:
                mode = str(payload.get("group_access_mode") or "").strip().lower()
                if mode not in {"whitelist", "blacklist"}:
                    return self._error("group_access_mode 只能是 whitelist 或 blacklist")
                changed["group_access_mode"] = mode
            if "group_whitelist_ids" in payload:
                changed["group_whitelist_ids"] = self._normalize_id_list(payload.get("group_whitelist_ids"))
            if "group_blacklist_ids" in payload:
                changed["group_blacklist_ids"] = self._normalize_id_list(payload.get("group_blacklist_ids"))
            for key, value in (payload.get("features") or {}).items():
                if key in self._allowed_feature_keys():
                    changed[key] = self._normalize_bool_value(value)
                elif key in self._schema_bool_keys() and key in self._allowed_setting_keys():
                    changed[key] = self._normalize_bool_value(value)
            provider_payload: dict[str, Any] = {}
            for key, value in (payload.get("providers") or {}).items():
                if key in self._allowed_provider_keys():
                    provider_payload[key] = self._single_line(value, 160)
            for key, value in (payload.get("settings") or {}).items():
                if key in self._allowed_setting_keys():
                    changed[key] = self._normalize_setting_value(key, value)
            if provider_payload:
                if bool(payload.get("overwrite_provider_modes")):
                    mode_value = changed.get("provider_config_mode") or self._config_get("provider_config_mode") or getattr(self.plugin, "provider_config_mode", "quick")
                    provider_payload = self._expand_provider_overwrite_bundle(str(mode_value), provider_payload)
                changed.update(provider_payload)
            # Enabling depends on the single authoritative primary ID being
            # installed before the mode transition.
            if bool(changed.get("enable_multi_persona_mode")):
                ordered_changed: dict[str, Any] = {}
                for dependency_key in ("plugin_specific_persona_id", "multi_persona_ids"):
                    if dependency_key in changed:
                        ordered_changed[dependency_key] = changed[dependency_key]
                ordered_changed.update(changed)
                changed = ordered_changed
            mode_transition_changed = "enable_multi_persona_mode" in changed and (
                bool(changed.get("enable_multi_persona_mode"))
                != bool(getattr(self.plugin, "enable_multi_persona_mode", False))
            )
            storage_changed = bool({"storage_backend", "storage_sqlite_path"} & set(changed))
            if storage_changed or mode_transition_changed:
                story_authority_identity = (
                    story_authority_controller().enter_legacy_operation(
                        "page.settings.store-persona-transaction"
                    )
                )
            req041_config_snapshot = self._req041_config_runtime_snapshot(changed)
            apply_overrides = dict(changed)
            apply_overrides["__defer_relationship_data_save"] = True
            if storage_changed or mode_transition_changed:
                apply_overrides["__defer_storage_rebuild"] = True
                flush_save = getattr(self.plugin, "_flush_scheduled_data_save", None)
                if callable(flush_save):
                    await flush_save()
            if mode_transition_changed:
                mode_transition_snapshot = self._multi_persona_transition_snapshot()
            if self.IMAGE_API_RUNTIME_SETTING_KEYS & set(changed):
                async with self._image_api_runtime_lock():
                    for key, value in changed.items():
                        self._apply_config_value(key, value, apply_overrides)
            else:
                for key, value in changed.items():
                    self._apply_config_value(key, value, apply_overrides)
            if apply_overrides.get("__relationship_profile_batch"):
                try:
                    await self._apply_relationship_profile_config_batch(apply_overrides)
                except Exception:
                    self._restore_relationship_config_values(apply_overrides)
                    raise
            if "enable_body_monitor_integration" in changed:
                runtime_task = getattr(self.plugin, "_body_monitor_integration_toggle_task", None)
                if isinstance(runtime_task, asyncio.Task):
                    await runtime_task
            expression_scope_keys = {
                "expression_private_learning_source_mode",
                "expression_private_learning_source_ids",
                "expression_group_learning_source_mode",
                "expression_group_learning_source_ids",
                "expression_private_application_mode",
                "expression_private_application_user_ids",
                "expression_group_application_mode",
                "expression_group_application_ids",
            }
            relationship_data_changed = bool(apply_overrides.get("__relationship_data_changed"))
            if expression_scope_keys & set(changed) or relationship_data_changed:
                save_sections: set[str] = set()
                if expression_scope_keys & set(changed):
                    save_sections.add("expression_learning_runtime")
                if relationship_data_changed:
                    save_sections.add("users")
                async with self.plugin._data_lock:
                    expression_refresher = getattr(self.plugin, "_refresh_expression_voice_profile", None)
                    if expression_scope_keys & set(changed) and callable(expression_refresher):
                        expression_refresher()
                        save_sections.add("expression_voice_profile")
                    self.plugin._save_data_sync(sections=save_sections)
            if storage_changed:
                rebuild = getattr(self.plugin, "_rebuild_store_manager", None)
                if callable(rebuild):
                    rebuild(reload_data=True)
            await self._record_personality_auto_tune_manual_values(changed)
            personality_restore: dict[str, Any] = {}
            if (
                ("enable_personality_iteration_experiment" in changed and not bool(changed.get("enable_personality_iteration_experiment")))
                or ("enable_personality_iteration_auto_tune" in changed and not bool(changed.get("enable_personality_iteration_auto_tune")))
            ):
                personality_restore = await self._restore_personality_iteration_auto_tune(
                    "角色贴合校准或自主调节已关闭"
                )
                restored_values = personality_restore.get("restored") if isinstance(personality_restore, dict) else {}
                if isinstance(restored_values, dict):
                    changed.update(restored_values)
            if any(key in self._allowed_provider_keys() for key in changed) or "provider_config_mode" in changed:
                apply_quick = getattr(self.plugin, "_apply_quick_provider_defaults", None)
                if callable(apply_quick):
                    apply_quick()
            clearer = getattr(self.plugin, "_clear_proactive_only_temp_unlocks_if_mode_off", None)
            if callable(clearer):
                clearer()
            config_saved = True
            if changed:
                try:
                    config_saved = await self._save_config_if_possible()
                except Exception as save_exc:
                    if req041_config_snapshot:
                        rollback_saved = await self._rollback_req041_config_runtime(req041_config_snapshot)
                        if not rollback_saved:
                            raise RuntimeError(
                                "配置写入失败，REQ-041 运行值已恢复，但旧配置重新持久化失败"
                            ) from save_exc
                    if apply_overrides.get("__relationship_profile_transaction"):
                        await self._rollback_relationship_config_transaction(apply_overrides)
                    raise
                if apply_overrides.get("__relationship_profile_transaction"):
                    if not config_saved:
                        if req041_config_snapshot:
                            rollback_saved = await self._rollback_req041_config_runtime(req041_config_snapshot)
                            if not rollback_saved:
                                raise RuntimeError(
                                    "配置保存失败，REQ-041 运行值已恢复，但旧配置重新持久化失败"
                                )
                        await self._rollback_relationship_config_transaction(apply_overrides)
                        raise RuntimeError("配置保存失败，关系配置、人格资料及 REQ-041 关键运行值已回滚")
                    else:
                        apply_overrides.pop("__relationship_profile_transaction", None)
                if not config_saved and req041_config_snapshot:
                    rollback_saved = await self._rollback_req041_config_runtime(req041_config_snapshot)
                    if not rollback_saved:
                        raise RuntimeError(
                            "配置保存失败，REQ-041 运行值已恢复，但旧配置重新持久化失败"
                        )
                    raise RuntimeError("配置保存失败，REQ-041 关键运行值已回滚")
                if not config_saved and mode_transition_snapshot:
                    raise RuntimeError("配置保存失败，多人格模式切换已回滚")
                if config_saved and mode_transition_snapshot:
                    mode_transition_committed = True
            if (
                config_saved
                and "plugin_specific_persona_id" in changed
                and primary_persona_before != str(changed.get("plugin_specific_persona_id") or "").strip()
            ):
                recorder = getattr(self.plugin, "_record_primary_persona_change", None)
                if callable(recorder):
                    primary_data_warning = recorder(
                        primary_persona_before,
                        changed.get("plugin_specific_persona_id"),
                    ) or {}
            overview = await self.get_overview()
            if self._is_http_error_response(overview):
                return overview
            if overview.get("success"):
                overview["data"]["changed"] = changed
                overview["data"]["config_saved"] = config_saved
                if personality_restore:
                    overview["data"]["personality_auto_tune_restore"] = personality_restore
                if primary_data_warning:
                    overview["data"]["primary_store_ownership_warning"] = primary_data_warning
                data = overview.get("data") if isinstance(overview.get("data"), dict) else {}
                features = data.get("features") if isinstance(data.get("features"), dict) else {}
                settings = data.get("settings") if isinstance(data.get("settings"), dict) else {}
                if config_saved:
                    for key, value in changed.items():
                        if key in self._allowed_feature_keys():
                            features[key] = self._normalize_bool_value(value)
                        if key in self._allowed_setting_keys():
                            settings[key] = value
            return overview
        except StoryAuthorityError:
            raise
        except CatalogValidationError as exc:
            detail = next(
                (
                    f"{field}：{message}"
                    for field, messages in exc.errors.items()
                    for message in messages
                ),
                "",
            )
            logger.warning("参考图目录字段校验失败: %s", self._single_line(exc, 240))
            return {
                "success": False,
                "error": f"参考图目录存在无效字段：{detail}" if detail else "参考图目录存在无效字段",
                "field_errors": exc.errors,
                "ts": int(time.time()),
            }
        except Exception as exc:
            if mode_transition_snapshot and not mode_transition_committed:
                try:
                    await self._rollback_multi_persona_transition(mode_transition_snapshot)
                except Exception as rollback_exc:
                    logger.error(
                        "多人格模式切换回滚失败: %s",
                        rollback_exc,
                        exc_info=True,
                    )
                    return self._exception_error(
                        f"{exc}；多人格模式切换回滚失败: {rollback_exc}"
                    )
            logger.error(f"更新设置失败: {exc}", exc_info=True)
            return self._exception_error(str(exc))
        finally:
            if story_authority_identity is not None:
                story_authority_controller().exit_legacy_operation(
                    story_authority_identity
                )
