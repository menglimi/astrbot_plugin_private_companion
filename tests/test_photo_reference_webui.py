from pathlib import Path
import unittest


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
APP_JS = (PLUGIN_ROOT / "pages" / "陪伴面板" / "app.js").read_text(encoding="utf-8")
APP_CSS = (PLUGIN_ROOT / "pages" / "陪伴面板" / "app.css").read_text(encoding="utf-8")
INDEX_HTML = (PLUGIN_ROOT / "pages" / "陪伴面板" / "index.html").read_text(encoding="utf-8")
PAGE_API = (PLUGIN_ROOT / "page_api.py").read_text(encoding="utf-8")
PHOTO_REFERENCE_METADATA = (PLUGIN_ROOT / "photo_reference_metadata.py").read_text(encoding="utf-8")


class PhotoReferenceWebUiTests(unittest.TestCase):
    def test_photo_scope_quotas_use_standard_number_inputs(self) -> None:
        quota_keys = (
            "photo_generation_private_owner_max_daily",
            "photo_generation_private_friend_max_daily",
            "photo_generation_group_max_daily",
            "photo_generation_proactive_max_daily",
        )
        scripts: list[bytes] = []

        for page_name in ("陪伴面板", "companion-panel"):
            with self.subTest(page=page_name):
                script_path = PLUGIN_ROOT / "pages" / page_name / "app.js"
                script = script_path.read_text(encoding="utf-8")
                html = (PLUGIN_ROOT / "pages" / page_name / "index.html").read_text(encoding="utf-8")
                scripts.append(script_path.read_bytes())

                section_start = script.index('title: "生图数量限制"')
                section_end = script.index("\n    },", section_start)
                user_photo_section = script[section_start:section_end]
                for key in quota_keys:
                    self.assertIn(f'"{key}"', user_photo_section)
                    self.assertIn(
                        f'{key}: {{ type: "number", min: -1, max: 100, step: 1 }}',
                        script,
                    )
                    guide_line = next(
                        line for line in script.splitlines() if f'{{ key: "{key}"' in line
                    )
                    self.assertIn('type: "number"', guide_line)
                    self.assertIn("min: -1", guide_line)
                    self.assertIn("max: 100", guide_line)
                    self.assertIn("step: 1", guide_line)

                self.assertNotIn("photo_generation_allowed_scopes", script)
                self.assertNotIn("photo-scopes", script)
                self.assertNotIn("data-photo-scope-value", script)
                self.assertNotIn("photo-scope-choice-list", script)
                self.assertIn("scope=photo-scope-quota-v1", html)

        self.assertEqual(scripts[0], scripts[1])

    def test_catalog_dirty_signature_normalizes_array_and_line_formats(self) -> None:
        self.assertIn('paramKey === "photo_reference_catalog"', APP_JS)
        self.assertIn("photoReferenceCatalogSignature(value)", APP_JS)
        self.assertIn("value.split(/\\r?\\n/)", APP_JS)
        self.assertIn("parsePhotoReferenceCatalog(value, true)", APP_JS)
        self.assertIn('if (canonical.kind === "library") delete canonical.id', APP_JS)

    def test_status_hydration_replaces_the_clean_catalog_baseline(self) -> None:
        self.assertIn('baseline?.key === "enable_photo_text_action"', APP_JS)
        self.assertIn('baseline.formSignature = ""', APP_JS)

    def test_opening_manager_skips_the_managed_catalog_draft(self) -> None:
        self.assertIn('control.dataset.featureParam === "photo_reference_catalog"', APP_JS)

    def test_generic_form_events_cannot_overwrite_the_managed_catalog_draft(self) -> None:
        self.assertIn(
            'function rememberFeatureParamDraft(control, { allowPhotoReferenceCatalog = false } = {})',
            APP_JS,
        )
        self.assertIn(
            'key === "photo_reference_catalog" && !allowPhotoReferenceCatalog',
            APP_JS,
        )
        self.assertIn(
            'rememberFeatureParamDraft(catalogInput, { allowPhotoReferenceCatalog: true })',
            APP_JS,
        )

    def test_catalog_sync_marks_dirty_only_after_a_semantic_change(self) -> None:
        self.assertIn(
            'const previousSignature = photoReferenceCatalogSignature(currentPhotoReferenceCatalogValue())',
            APP_JS,
        )
        self.assertIn(
            'previousSignature === photoReferenceCatalogSignature(serialized)',
            APP_JS,
        )
        self.assertIn('refreshFeatureDetailDirty();', APP_JS)
        self.assertIn('return false;', APP_JS)

    def test_unedited_catalog_is_not_submitted_with_other_photo_settings(self) -> None:
        self.assertIn(
            'key === "photo_reference_catalog" && !Object.prototype.hasOwnProperty.call(parameterDraft, key)',
            APP_JS,
        )

    def test_manager_drops_a_preferred_preset_removed_from_server_options(self) -> None:
        self.assertIn('state.photoReferenceLibraryStatus?.options?.presets', APP_JS)
        self.assertIn(
            'availablePresets && !availablePresets.includes(preferredPreset) ? "" : preferredPreset',
            APP_JS,
        )

    def test_time_categories_round_trip_through_manager_draft(self) -> None:
        self.assertIn('metadata.time_categories = normalizePhotoReferenceMetadataList', APP_JS)
        self.assertIn('time_categories: Array.isArray(item.time_categories)', APP_JS)
        self.assertIn('time_categories: normalizePhotoReferenceMetadataList', APP_JS)
        self.assertIn('data-photo-reference-times', APP_JS)

    def test_role_shortcuts_are_rendered_and_applied(self) -> None:
        self.assertIn('status?.options?.role_shortcuts', APP_JS)
        self.assertIn('data-photo-reference-role-shortcut', APP_JS)
        self.assertIn('input.dataset.photoReferenceRoleShortcut', APP_JS)

    def test_selfie_workflow_help_describes_dynamic_image_count(self) -> None:
        self.assertIn("images=N 自拍/改图工作流", APP_JS)
        self.assertNotIn("优先寻找 images=1 的自拍工作流", APP_JS)

    def test_metadata_editor_uses_localized_select_controls(self) -> None:
        self.assertIn('<select data-photo-reference-outfit-category', APP_JS)
        self.assertIn('photoReferenceMultiSelectHtml("reference_roles"', APP_JS)
        self.assertIn('photoReferenceMultiSelectHtml("scene_categories"', APP_JS)
        self.assertIn('photoReferenceMultiSelectHtml("time_categories"', APP_JS)
        self.assertIn('<select data-photo-reference-preferred-preset', APP_JS)
        self.assertIn('photoReferenceSingleSelectOptions("outfit_categories"', APP_JS)
        self.assertIn('photoReferenceSingleSelectOptions("presets"', APP_JS)
        self.assertNotIn('placeholder="sleepwear / daily_outfit / formal"', APP_JS)
        self.assertNotIn('placeholder="home, bedroom, outdoor"', APP_JS)
        self.assertNotIn('placeholder="morning, evening, bedtime"', APP_JS)

    def test_metadata_editor_explains_each_decision_field(self) -> None:
        expected_help = (
            "展开后可指定这张图在生图时负责保留哪些信息。",
            "决定生成时从这张图保留哪些内容",
            "标记图片中的服装类型",
            "控制是否优先沿用参考图中的服装",
            "选择这张图适合使用的通用场景",
            "选择这张图适合使用的时间段",
            "选择使用这张图时优先套用的生图场景预设",
        )
        for help_text in expected_help:
            with self.subTest(help_text=help_text):
                self.assertIn(help_text, APP_JS)

    def test_metadata_editor_visually_separates_help_from_the_next_field(self) -> None:
        self.assertIn('.photo-reference-metadata-editor[open] > label', APP_CSS)
        self.assertIn('.photo-reference-metadata-editor[open] > .photo-reference-field', APP_CSS)
        self.assertIn('border-top: 1px solid var(--line-soft)', APP_CSS)
        self.assertIn('padding-top: 14px', APP_CSS)

    def test_guided_questions_open_only_from_the_add_reference_dialog(self) -> None:
        self.assertIn('data-photo-reference-add-open', APP_JS)
        self.assertIn('<dialog class="photo-reference-add-dialog" data-photo-reference-add-dialog>', APP_JS)
        self.assertIn('grid-template-columns:minmax(0,1fr);grid-template-rows:auto minmax(0,1fr) auto;align-items:stretch', APP_CSS)
        self.assertIn('.photo-reference-add-form>footer button{flex:0 0 auto;width:auto;white-space:nowrap}', APP_CSS)
        self.assertIn('.photo-reference-guided-tabs button{flex:0 0 auto;width:auto', APP_CSS)
        self.assertIn('.photo-reference-guided-templates button{flex:0 0 auto;width:auto', APP_CSS)
        dialog_start = APP_JS.index('<dialog class="photo-reference-add-dialog"')
        dialog_end = APP_JS.index('</dialog>', dialog_start)
        dialog_markup = APP_JS[dialog_start:dialog_end]
        self.assertIn('data-photo-reference-guided-host', dialog_markup)
        self.assertIn('addDialog.showModal()', APP_JS)
        self.assertIn('addDialog.close()', APP_JS)
        manager_start = APP_JS.index('function bindPhotoReferenceManagerActions()')
        manager_end = APP_JS.index('function bindPhotoApiEndpointEditor', manager_start)
        manager_actions = APP_JS[manager_start:manager_end]
        self.assertNotIn(
            'if (!manager || state.featureDetailSubpage !== "photo_reference_library") return;\n  renderGuidedPhotoReferenceEditor();',
            manager_actions,
        )

    def test_existing_cards_hide_internal_fields_and_reopen_guided_editor(self) -> None:
        self.assertIn('class="photo-reference-metadata-editor" hidden aria-hidden="true"', APP_JS)
        self.assertIn('data-photo-reference-configure data-index="${index}"', APP_JS)
        self.assertIn('openAddDialog(Number(button.dataset.index))', APP_JS)
        self.assertIn('applyGuidedPhotoReferenceDraft(', APP_JS)

    def test_trial_posts_the_unsaved_draft_catalog(self) -> None:
        trial_start = APP_JS.index("function guidedPhotoReferenceTrialCandidates")
        trial_end = APP_JS.index("function applyGuidedPhotoReferenceDraft", trial_start)
        trial_candidates = APP_JS[trial_start:trial_end]
        self.assertIn('metadata_source: "guided_editor_draft"', trial_candidates)
        self.assertIn("state.photoReferenceEditingIndex", trial_candidates)
        self.assertIn("editingExisting", trial_candidates)
        self.assertIn('const trialCandidates = guidedPhotoReferenceTrialCandidates(root, compiled.metadata)', APP_JS)
        self.assertIn('candidates: trialCandidates', APP_JS)

    def test_guided_metadata_answers_use_plain_language_choice_controls(self) -> None:
        for field_name in (
            "outfit_category",
            "prefer_scenes",
            "prefer_times",
            "avoid_scenes",
            "avoid_times",
            "preferred_preset",
        ):
            with self.subTest(field_name=field_name):
                self.assertNotIn(f'<input name="{field_name}"', APP_JS)
        for field_name in (
            "core_anchor",
            "wardrobe_change",
            "location_change",
            "pose_change",
            "outfit_behavior",
            "outfit_category",
            "prefer_none",
            "prefer_scenes",
            "prefer_times",
            "avoid_none",
            "avoid_scenes",
            "avoid_times",
            "preferred_preset",
            "fallback_policy",
        ):
            with self.subTest(field_name=field_name):
                self.assertIn(f'guidedPhotoReferenceChoiceGroup("{field_name}"', APP_JS)
        self.assertIn('type="${type}"', APP_JS)
        self.assertIn('name="${escapeHtml(name)}"', APP_JS)
        self.assertIn('data-photo-guided-answer-label', APP_JS)

    def test_guided_questionnaire_uses_eight_redundant_plain_language_questions(self) -> None:
        questions = (
            "1. 这张图最不能丢的特点是什么？",
            "2. 换一身衣服后，这张图还适合用吗？",
            "3. 换到其他地点后，这张图还适合用吗？",
            "4. 动作改变后，这张图还适合用吗？",
            "5. 哪些情况应该优先用这张图？",
            "6. 哪些情况容易用错这张图？",
            "7. 没有完全匹配的图片时，应该怎么处理？",
            "8. 图中的穿搭应该怎么处理？",
        )
        for question in questions:
            with self.subTest(question=question):
                self.assertIn(f"<legend>{question}</legend>", APP_JS)
        for repeated_identity_answer in (
            'value: "yes_identity", label: "适合，主要看人物长相"',
            'value: "yes_outfit", label: "适合，主要看人物穿搭"',
            'value: "yes_style", label: "适合，主要看画面风格"',
        ):
            self.assertGreaterEqual(APP_JS.count(repeated_identity_answer), 2)

    def test_guided_questions_adapt_from_four_core_questions_to_at_most_eight(self) -> None:
        self.assertIn("function updateGuidedPhotoReferenceQuestionVisibility", APP_JS)
        self.assertIn('const coreQuestionIds = new Set([', APP_JS)
        for question_id in (
            "core_anchor",
            "priority_conditions",
            "exclusion_conditions",
            "fallback_policy",
        ):
            self.assertIn(f'"{question_id}"', APP_JS)
        self.assertIn('questionRow.hidden = !visibleQuestionIds.has(questionId)', APP_JS)
        self.assertIn('data-photo-guided-question="core_anchor"', APP_JS)
        self.assertIn('data-photo-guided-question="outfit_rule"', APP_JS)
        self.assertIn('updateGuidedPhotoReferenceQuestionVisibility(root)', APP_JS)

    def test_editing_passes_saved_metadata_to_every_review_path(self) -> None:
        self.assertIn("function guidedPhotoReferenceSavedMetadata", APP_JS)
        self.assertIn("state.photoReferenceEditingIndex", APP_JS)
        self.assertIn("function guidedPhotoReferenceApprovalPayload", APP_JS)
        self.assertIn("saved: guidedPhotoReferenceSavedMetadata()", APP_JS)

    def test_unchanged_guided_answers_reuse_the_model_approval(self) -> None:
        self.assertIn("async function reviewGuidedPhotoReference", APP_JS)
        self.assertIn("root._photoReferenceApprovalSignature === signature", APP_JS)
        self.assertIn("root._photoReferenceApprovalResult = result", APP_JS)
        self.assertGreaterEqual(APP_JS.count("await reviewGuidedPhotoReference(root)"), 2)

    def test_async_guided_actions_keep_button_reference_after_await(self) -> None:
        compile_start = APP_JS.index('data-photo-guided-compile]')
        trial_start = APP_JS.index('data-photo-guided-trial]')
        compile_handler = APP_JS[compile_start:trial_start]
        self.assertIn("const actionButton = event.currentTarget;", compile_handler)
        self.assertIn("setActionBusy(actionButton, true);", compile_handler)
        self.assertIn("setActionBusy(actionButton, false);", compile_handler)
        self.assertNotIn("setActionBusy(event.currentTarget, false);", compile_handler)

        trial_end = APP_JS.index("updateGuidedPhotoReferenceQuestionVisibility", trial_start)
        trial_handler = APP_JS[trial_start:trial_end]
        self.assertIn("const actionButton = event.currentTarget;", trial_handler)
        self.assertIn("setActionBusy(actionButton, true);", trial_handler)
        self.assertIn("setActionBusy(actionButton, false);", trial_handler)
        self.assertNotIn("setActionBusy(event.currentTarget, false);", trial_handler)

        add_start = APP_JS.index('data-photo-reference-add-form]')
        add_end = APP_JS.index('data-photo-reference-move]', add_start)
        add_handler = APP_JS[add_start:add_end]
        self.assertIn(
            'const submitButton = event.submitter || form.querySelector(\'button[type="submit"]\');',
            add_handler,
        )
        self.assertIn("setActionBusy(submitButton, true);", add_handler)
        self.assertIn("setActionBusy(submitButton, false);", add_handler)

    def test_quick_templates_clear_previous_scene_time_and_exclusion_choices(self) -> None:
        template_start = APP_JS.index('root.querySelectorAll("[data-photo-guided-template]")')
        template_end = APP_JS.index("const bindNoneChoice", template_start)
        template_handler = APP_JS[template_start:template_end]
        for field_name in (
            "prefer_scenes",
            "prefer_times",
            "preferred_preset",
            "avoid_scenes",
            "avoid_times",
        ):
            self.assertIn(f'setQuestionValues("{field_name}", [])', template_handler)
        self.assertIn('setQuestionValues("prefer_none", "none")', template_handler)
        self.assertIn('setQuestionValues("avoid_none", "none")', template_handler)

    def test_restoring_questionnaire_does_not_clear_expert_override(self) -> None:
        restore_start = APP_JS.index("function applyGuidedPhotoReferenceDraft")
        restore_end = APP_JS.index("function guidedPhotoReferenceChoiceGroup", restore_start)
        restore = APP_JS[restore_start:restore_end]
        self.assertIn('root.querySelectorAll("[data-photo-guided-answer-label]")', restore)
        self.assertNotIn('querySelectorAll("input[type=\'checkbox\'], input[type=\'radio\']")', restore)
        self.assertGreater(restore.rindex("manualOverrideToggle.checked"), restore.index("questionnaire.answers.forEach"))

    def test_persona_reference_uses_the_simplified_guided_dialog(self) -> None:
        self.assertIn("data-photo-reference-persona-configure", APP_JS)
        self.assertIn("openAddDialog(-2)", APP_JS)
        self.assertIn("const editingPersona = state.photoReferenceEditingIndex === -2", APP_JS)
        self.assertIn('root.dataset.photoGuidedMode = personaMode ? "persona"', APP_JS)
        self.assertIn("guidedPhotoReferencePersonaMetadata", APP_JS)

    def test_guided_review_declares_and_uses_the_configured_main_model(self) -> None:
        self.assertIn('state.overview?.providers?.LLM_PROVIDER_ID', APP_JS)
        self.assertIn('审批将调用 WebUI“模型配置”中的主模型', APP_JS)
        self.assertIn('postJson("/photo_reference/metadata/review"', APP_JS)
        self.assertIn('questionnaire: guidedPhotoReferenceQuestionnaire(root)', APP_JS)
        self.assertIn('compiled.review?.status === "approved"', APP_JS)
        self.assertIn('主模型 ${compiled.review.provider_id', APP_JS)
        review_start = PAGE_API.index("async def review_photo_reference_metadata")
        review_end = PAGE_API.index("async def run_photo_reference_selection_trial", review_start)
        review_endpoint = PAGE_API[review_start:review_end]
        self.assertIn('getattr(self.plugin, "llm_provider_id", "")', review_endpoint)
        self.assertIn('task="photo_reference_metadata_review"', review_endpoint)
        self.assertIn('strict_provider=True', review_endpoint)
        self.assertIn('必须是 WebUI“模型配置”中的主模型', review_endpoint)
        self.assertNotIn("._task_provider(", review_endpoint)

    def test_selection_trial_uses_the_configured_main_model_without_executing_tools(self) -> None:
        trial_start = PAGE_API.index("async def _photo_reference_selection_trial_model_runner")
        trial_end = PAGE_API.index("async def review_photo_reference_metadata", trial_start)
        trial_runner = PAGE_API[trial_start:trial_end]
        self.assertIn('getattr(self.plugin, "llm_provider_id", "")', trial_runner)
        self.assertIn('getattr(self.plugin, "_llm_tool_call", None)', trial_runner)
        self.assertIn('provider_id=provider_id', trial_runner)
        self.assertIn('task="photo_reference_selection_trial"', trial_runner)
        self.assertIn('timeout_key="LLM_PROVIDER_ID"', trial_runner)
        self.assertIn('FunctionTool(', trial_runner)
        self.assertIn('handler=None', trial_runner)
        self.assertIn('tools=ToolSet([trial_tool])', trial_runner)
        self.assertIn('WebUI“模型配置”中的主模型 plugin.llm_provider_id', trial_runner)
        self.assertIn('不得改用任务/备用模型', trial_runner)
        self.assertNotIn('._task_provider(', trial_runner)
        trial_endpoint_start = PAGE_API.index("async def run_photo_reference_selection_trial")
        trial_endpoint_end = PAGE_API.index("def _reference_asset_records", trial_endpoint_start)
        trial_endpoint = PAGE_API[trial_endpoint_start:trial_endpoint_end]
        self.assertIn('context_snapshot = await self._photo_reference_trial_context_snapshot', trial_endpoint)
        self.assertIn('request_payload["ambient_context"] = context_snapshot', trial_endpoint)

    def test_trial_uses_reviewed_metadata_instead_of_recompiling_one_answer(self) -> None:
        trial_start = APP_JS.index("function guidedPhotoReferenceTrialCandidates")
        trial_end = APP_JS.index("function applyGuidedPhotoReferenceDraft", trial_start)
        trial_candidates = APP_JS[trial_start:trial_end]
        self.assertIn("reviewedMetadata", trial_candidates)
        self.assertNotIn('values("core_anchor")', trial_candidates)
        self.assertIn("guidedPhotoReferenceTrialCandidates(root, compiled.metadata)", APP_JS)
        self.assertIn('expected_reference_id: expectedReference?.id || ""', APP_JS)

    def test_v1_metadata_requires_confirmation_for_unrecoverable_answers(self) -> None:
        self.assertGreaterEqual(APP_JS.count('value: "needs_confirmation"'), 3)
        self.assertIn('setValues("wardrobe_change", root.dataset.photoGuidedMode', APP_JS)
        self.assertIn(': "needs_confirmation")', APP_JS)
        self.assertIn('setValues("location_change", "needs_confirmation")', APP_JS)
        self.assertIn('setValues("pose_change", "needs_confirmation")', APP_JS)

    def test_guided_metadata_has_live_local_preview_and_desktop_comparison(self) -> None:
        self.assertIn("const scheduleLocalPreview", APP_JS)
        self.assertIn("reviewGuidedPhotoReference(root, { useModel: false })", APP_JS)
        self.assertIn("use_model: useModel", APP_JS)
        self.assertIn('data-photo-guided-active-tab="answers"', APP_JS)
        self.assertIn('@media(min-width:900px)', APP_CSS)

    def test_trial_defaults_to_current_user_persona_context_and_supports_expert_override(self) -> None:
        self.assertIn('name="trial_context_mode"', APP_JS)
        self.assertIn('user_id: state.selectedUserId || ""', APP_JS)
        self.assertIn('context_mode: host.querySelector', APP_JS)
        self.assertIn("function guidedPhotoReferenceManualOverride", APP_JS)
        self.assertIn('name="manual_override_enabled"', APP_JS)
        self.assertIn('"metadata_source": "manual_override" if manual_override else "guided_editor"', PHOTO_REFERENCE_METADATA)
        restore_start = APP_JS.index("function applyGuidedPhotoReferenceDraft")
        questionnaire_branch = APP_JS.index("if (Array.isArray(questionnaire?.answers))", restore_start)
        override_restore = APP_JS.index("manualOverrideToggle.checked", restore_start)
        self.assertGreater(override_restore, questionnaire_branch)
        self.assertIn('root.querySelectorAll("[data-photo-guided-answer-label]")', APP_JS)

    def test_file_upload_submission_cannot_be_overwritten_by_status_refresh(self) -> None:
        for marker in (
            "photoReferenceLibraryRequestSeq: 0",
            "photoReferenceSubmissionToken: null",
            "const requestSeq = ++state.photoReferenceLibraryRequestSeq",
            "requestSeq !== state.photoReferenceLibraryRequestSeq || photoReferenceManagerBusy()",
            "state.photoReferenceSubmissionToken !== submissionToken",
            "state.featureDetailSubpage === \"photo_reference_library\"\n    && (state.photoReferenceSubmitting || state.photoReferenceAddDialogOpen)",
        ):
            self.assertIn(marker, APP_JS)

    def test_server_upload_uses_full_image_decode_and_bounded_content_addressed_storage(self) -> None:
        for marker in (
            "PILImage.open(io.BytesIO(raw))",
            "image.verify()",
            "image.load()",
            "hashlib.sha256(raw).hexdigest()",
            'target = target_dir / f"webui_{digest}{suffix}"',
            "PHOTO_REFERENCE_UPLOAD_MAX_COUNT = 256",
            "PHOTO_REFERENCE_UPLOAD_MAX_TOTAL_BYTES = 1024 * 1024 * 1024",
            "PHOTO_REFERENCE_UPLOAD_MAX_REQUEST_BYTES = 20 * 1024 * 1024",
            "content_length = request.content_length",
            "os.replace",
        ):
            self.assertIn(marker, PAGE_API)

    def test_reference_manager_is_locked_while_feature_settings_save(self) -> None:
        for marker in (
            "function photoReferenceManagerBusy()",
            "function setPhotoReferenceManagerBusy(busy)",
            "setPhotoReferenceManagerBusy(true)",
            "setPhotoReferenceManagerBusy(false)",
            "const submitting = photoReferenceManagerBusy()",
            "if (photoReferenceManagerBusy()) return;",
            "const keepManagerLocked = !busy",
        ):
            self.assertIn(marker, APP_JS)

    def test_metadata_editor_assets_are_cache_busted(self) -> None:
        self.assertIn('app.css?v=20260809-external-ability-controls-v3&amp;build=20260810-reference-upload-v1', INDEX_HTML)
        self.assertIn('css/polish.css?v=20260810-responsive-containment-v1', INDEX_HTML)
        self.assertIn(
            'app.js?v=20260809-proactive-tts-external-ability-photo-fixed-v1&amp;build=20260810-reference-upload-v2',
            INDEX_HTML,
        )


if __name__ == "__main__":
    unittest.main()
