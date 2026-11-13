"""Native shares only an explicitly mapped role's existing QQ image materials."""
from dataclasses import replace
from hashlib import sha256
from types import SimpleNamespace as NS
from unittest.mock import patch

from astrbot_host.role_reference_owner import RoleReferenceOwnerResolver
from companion_v2.configuration.management import ReferenceManagement, WardrobeManagement
from companion_v2.configuration.models import ConfigLayer, ConfigTarget
from companion_v2.conversation.media.models import ImageDescriptor
from companion_v2.execution.models import TaskError, digest
from companion_v2.foundation.refs import AudienceRef, RoleOwnerRef, ScopeKind
from companion_v2.references import configuration as ref_config
from companion_v2.references import wardrobe as wardrobe_config
from companion_v2.references.management import ReferenceMaterialManagement
from companion_v2.references.models import ReferenceKind, ReferenceRole
from companion_v2.references.service import ReferenceAssetService, OWNED_REFERENCE_PREFIX
from tests import test_reference_assets as fixtures


class WeChatRoleReferenceTests(fixtures.MessageFixture):
    def setUp(self):
        super().setUp()
        self.qq = self.context
        scope = replace(self.context.scope, platform='native-instance', bot_id='native-bot')
        self.native = replace(self.context,
            role_binding=replace(self.context.role_binding, scope=scope),
            audience=AudienceRef(scope, self.context.audience.recipients))
        role = self.qq.role_binding.role.subject_id
        self.qq_mapping = NS(bot_id=self.qq.scope.bot_id, persona_id='actual-persona', role_id=role)
        self.native_mapping = NS(bot_id='native-bot', persona_id='actual-persona', role_id=role)
        self.bridge = NS(settings=NS(
            personas=(self.qq_mapping, self.native_mapping),
            accounts=(NS(platform_id='qq-instance', bot_id=self.qq.scope.bot_id),
                      NS(platform_id='native-instance', bot_id='native-bot'))),
            platform_name=lambda item: 'wechat_native' if
                (getattr(item, 'platform_id', None) or getattr(item, 'platform', None)) ==
                'native-instance' else 'aiocqhttp')
        self.resolver = RoleReferenceOwnerResolver(self.runtime, self.bridge)
        self.config_owner = ref_config.ReferenceConfigurationOwner(self.runtime)
        self.settings = ref_config.ReferenceSettings(self.config_owner, owner_resolver=self.resolver)
        self.allowed = True
        self.service = ReferenceAssetService(self.runtime, self.tasks, settings=self.settings,
            authorize=lambda context, **kw: self.allowed)
        self.target = ConfigTarget(self.qq.scope.installation_id, ConfigLayer.ROLE,
                                  self.qq.role_binding.role, bot_id=self.qq.scope.bot_id)
        self.configure(enabled=True, group_enabled=True)

    def configure(self, **changes):
        revision = self.config_owner.read(self.target, ref_config.configuration_fields()).revision
        self.config_owner.write(self.target, ref_config.configuration_fields(),
            changes={'photo.reference.' + key: value for key, value in changes.items()},
            reset=(), expected_revision=revision, observed_at=self.tasks.now())

    def group(self):
        scope = replace(self.native.scope, kind=ScopeKind.GROUP, native_scope_id='wx-group',
                        conversation_id='native-group', session_id='native-group-original')
        return replace(self.native, role_binding=replace(self.native.role_binding, scope=scope),
                       audience=AudienceRef(scope, self.native.audience.recipients))

    def register(self, context=None):
        payload = b'owned-reference-bytes'
        return self.service.register_asset(context or self.qq, asset_id='portrait',
            kind=ReferenceKind.REFERENCE_IMAGE, role=ReferenceRole.IDENTITY,
            content_hash=sha256(payload).hexdigest(), size_bytes=len(payload),
            mime_type='image/png', media_ref=OWNED_REFERENCE_PREFIX + 'test:portrait',
            payload=payload).unwrap()

    def test_native_default_mismatch_and_shared_group_switch(self):
        local = ref_config.ReferenceSettings(self.config_owner)
        self.assertFalse(local.read(self.native, self.tasks.now())['group_enabled'])
        self.assertTrue(self.settings.read(self.group(), self.tasks.now())['group_enabled'])
        self.assertEqual(self.settings.target(self.native), self.target)

    def test_group_catalog_and_actual_fetch_use_qq_material_without_mutating_context(self):
        self.register()
        group = self.group()
        original = group.scope
        material = self.service.prompt_material(group, local_date='2026-11-12')
        self.assertTrue(material['references'])
        asset, payload, roles = self.service.read_asset(group, 'portrait')
        self.assertEqual(payload, b'owned-reference-bytes')
        self.assertEqual(asset.asset_id, 'portrait')
        self.assertIn('identity', roles)
        self.assertIs(group.scope, original)
        self.assertEqual(group.scope.bot_id, 'native-bot')
        self.assertEqual(group.scope.session_id, 'native-group-original')
        self.assertEqual(self.service.owner(group), self.service.owner(self.qq))

    def test_shared_group_switch_is_respected_on_fetch(self):
        self.register()
        self.configure(group_enabled=False)
        with self.assertRaises(TaskError) as error:
            self.service.read_asset(self.group(), 'portrait')
        self.assertEqual(error.exception.code, 'photo_reference_group_disabled')
        self.assertTrue(self.service.read_asset(self.native, 'portrait')[1])

    def test_disabling_shared_reference_switch_blocks_native(self):
        self.register()
        self.configure(enabled=False)
        with self.assertRaises(TaskError) as error:
            self.service.read_asset(self.native, 'portrait')
        self.assertEqual(error.exception.code, 'photo_reference_disabled')

    def test_native_authority_is_not_borrowed_from_qq(self):
        self.register()
        self.allowed = False
        with self.assertRaises(TaskError) as error:
            self.service.read_asset(self.native, 'portrait')
        self.assertEqual(error.exception.code, 'reference_authority_revoked')

    def test_withdrawal_is_rechecked_at_fetch(self):
        self.register()
        self.service.prompt_material(self.group(), local_date='2026-11-12')
        self.service.withdraw_asset(self.qq, 'portrait').unwrap()
        with self.assertRaises(TaskError) as error:
            self.service.read_asset(self.group(), 'portrait')
        self.assertEqual(error.exception.code, 'reference_asset_withdrawn')

    def test_rebinding_stops_old_material_reads(self):
        self.register()
        self.service.prompt_material(self.group(), local_date='2026-11-12')
        self.bridge.settings.personas = (self.qq_mapping,
            NS(bot_id='native-bot', persona_id='another-persona',
               role_id=self.native_mapping.role_id))
        self.assertNotEqual(self.service.owner(self.native), self.service.owner(self.qq))
        with self.assertRaises(TaskError):
            self.service.read_asset(self.group(), 'portrait')

    def test_same_role_name_with_different_actual_persona_does_not_share(self):
        self.native_mapping.persona_id = 'different-persona'
        self.assertEqual(self.resolver(self.native).bot_id, 'native-bot')

    def test_same_persona_with_different_role_does_not_share(self):
        self.qq_mapping.role_id = 'another-role'
        self.assertEqual(self.resolver(self.native).bot_id, 'native-bot')

    def test_legacy_unspecified_persona_does_not_share(self):
        self.qq_mapping.persona_id = self.native_mapping.persona_id = '[%None]'
        self.assertEqual(self.resolver(self.native).bot_id, 'native-bot')

    def add_second_qq(self):
        self.bridge.settings.personas += (NS(bot_id='second-qq',
            persona_id=self.qq_mapping.persona_id, role_id=self.qq_mapping.role_id),)
        self.bridge.settings.accounts += (NS(platform_id='second-qq-instance', bot_id='second-qq'),)

    def test_multiple_qq_accounts_select_unique_established_owner(self):
        self.add_second_qq()
        self.assertEqual(self.resolver(self.native).bot_id, self.qq.scope.bot_id)

    def test_multiple_established_qq_owners_are_not_silently_merged(self):
        self.add_second_qq()
        target = replace(self.target, bot_id='second-qq')
        self.config_owner.write(target, ref_config.configuration_fields(),
            changes={'photo.reference.enabled': True}, reset=(), expected_revision=0,
            observed_at=self.tasks.now())
        with self.assertRaises(TaskError) as error:
            self.resolver(self.native)
        self.assertEqual(error.exception.code, 'reference_owner_ambiguous')

    def test_existing_native_settings_are_preserved_and_reported_as_conflict(self):
        target = replace(self.target, bot_id='native-bot')
        self.config_owner.write(target, ref_config.configuration_fields(),
            changes={'photo.reference.group_enabled': False}, reset=(), expected_revision=0,
            observed_at=self.tasks.now())
        with self.assertRaises(TaskError) as error:
            self.resolver(self.native)
        self.assertEqual(error.exception.code, 'reference_owner_conflict')
        self.assertFalse(self.config_owner.read(target, ref_config.configuration_fields())
                         .fields['photo.reference.group_enabled'].value)

    def test_qq_owner_and_non_native_behavior_remain_unchanged(self):
        self.assertEqual(self.resolver(self.qq), RoleOwnerRef(self.qq.scope.installation_id,
            self.qq.scope.bot_id, self.qq.role_binding.role.subject_id))

    def test_management_editors_and_inventory_resolve_the_same_material_target(self):
        wardrobe = wardrobe_config.WardrobeSettings(self.configuration,
                                                   target_resolver=self.settings.target)
        app = NS(runtime=self.runtime, reference_settings=self.settings,
                 wardrobe_settings=wardrobe, references=self.service)
        self.assertEqual(ReferenceManagement(app, self.native)._target(), self.target)
        self.assertEqual(WardrobeManagement(app, self.native)._target(), self.target)
        options, _ = ReferenceMaterialManagement(app)._settings(self.native)
        self.assertTrue(options['enabled'])
        self.assertTrue(options['group_enabled'])

    def test_native_registration_writes_shared_owner(self):
        self.register(self.native)
        self.assertEqual(len(self.service.list_assets(self.qq)), 1)
        native_key = digest(['role-references.v1', RoleOwnerRef(self.native.scope.installation_id,
                            'native-bot', self.native.role_binding.role.subject_id)])
        self.assertIsNone(self.runtime.read_one(
            'SELECT 1 FROM reference_assets WHERE owner_key = ?', (native_key,)))

    def test_wardrobe_shares_options_after_authorizing_original_native_target(self):
        owner = wardrobe_config.WardrobeConfigurationOwner(self.runtime)
        owner.write(self.target, wardrobe_config.configuration_fields(),
            changes={'wardrobe.enabled': True, 'wardrobe.tendency': 'existing QQ wardrobe'},
            reset=(), expected_revision=self.config_owner.read(
                self.target, ref_config.configuration_fields()).revision, observed_at=self.tasks.now())
        from unittest.mock import Mock
        read = Mock(return_value=NS(failure=None, unwrap=lambda: NS(values={})))
        configuration = NS(runtime=self.runtime, read=read)
        wardrobe = wardrobe_config.WardrobeSettings(configuration, target_resolver=self.settings.target)
        options = wardrobe.read(self.native, self.tasks.now())
        self.assertTrue(options['enabled'])
        self.assertEqual(options['tendency'], 'existing QQ wardrobe')
        self.assertEqual(read.call_args.kwargs['target'].bot_id, 'native-bot')
        self.assertIs(read.call_args.args[0], self.native.actor)

    def test_wardrobe_denial_is_not_bypassed_by_material_sharing(self):
        def denied(*args, **kw):
            raise TaskError('configuration_access_denied', 'original actor denied')
        wardrobe = wardrobe_config.WardrobeSettings(NS(runtime=self.runtime, read=denied),
                                                    target_resolver=self.settings.target)
        with self.assertRaises(TaskError) as error:
            wardrobe.read(self.native, self.tasks.now())
        self.assertEqual(error.exception.code, 'configuration_access_denied')

    def test_native_album_registration_keeps_original_fetch_context(self):
        payload = b'album-image-bytes'
        descriptor = ImageDescriptor(0, 'image/png', 1, 1, len(payload), sha256(payload).hexdigest())
        fetched = []
        def album_image(context, at, image_id):
            fetched.append((context, image_id))
            return NS(content=payload, descriptor=descriptor)
        app = NS(runtime=self.runtime, references=self.service, reference_settings=self.settings,
                 wardrobe_settings=NS(target=self.settings.target),
                 messages=NS(generated_images=NS(album_image=album_image)))
        manager = ReferenceMaterialManagement(app)
        # Management admission is covered separately by existing material API tests.
        with patch.object(manager, '_admit', side_effect=lambda op, ctx, at: self.service.owner(ctx)):
            result = manager.register_from_album(self.native.actor, self.native,
                at=self.tasks.now(), image_id='native-album-image', label='Native portrait',
                roles=['identity'], operation_key='native-album-register-01')
        self.assertIs(fetched[0][0], self.native)
        self.assertEqual(fetched[0][1], 'native-album-image')
        self.assertEqual(self.service.read_asset(self.qq, result['asset_id'])[1], payload)

    def test_provider_lease_rechecks_shared_asset_using_original_group_context(self):
        from astrbot_host.native_extensions.images import ImageReferenceLease
        asset = self.register()
        group = self.group()
        ticket = {'registered_asset': asset, 'image': NS(content=b'owned-reference-bytes'),
                  'role': 'identity'}
        lease = ImageReferenceLease.__new__(ImageReferenceLease)
        lease.messages = self.messages
        lease.invocation = NS(context=group)
        lease.registry = NS(application=NS(references=self.service))
        lease._recheck(ticket, self.tasks.now())
        self.configure(group_enabled=False)
        with self.assertRaises(TaskError) as error:
            lease._recheck(ticket, self.tasks.now())
        self.assertEqual(error.exception.code, 'photo_reference_group_disabled')
        self.assertIs(lease.invocation.context, group)

    def test_no_qq_mapping_uses_native_original_owner(self):
        self.bridge.settings.personas = (self.native_mapping,)
        self.assertEqual(self.resolver(self.native).bot_id, 'native-bot')

    def test_multiple_empty_qq_owners_do_not_choose_an_arbitrary_account(self):
        self.add_second_qq()
        self.config_owner.write(self.target, ref_config.configuration_fields(),
            changes={}, reset=('photo.reference.enabled', 'photo.reference.group_enabled'),
            expected_revision=self.config_owner.read(self.target,
                ref_config.configuration_fields()).revision, observed_at=self.tasks.now())
        with self.assertRaises(TaskError) as error:
            self.resolver(self.native)
        self.assertEqual(error.exception.code, 'reference_owner_ambiguous')

    def management_app(self):
        from companion_v2.configuration.registry import FieldRegistry
        from companion_v2.configuration.service import ConfigurationService
        configuration = ConfigurationService(self.runtime,
            FieldRegistry(version='reference-sharing-test.v1', fields=(
                *ref_config.configuration_fields(), *wardrobe_config.configuration_fields())),
            owners=(self.config_owner, wardrobe_config.WardrobeConfigurationOwner(self.runtime)))
        return NS(runtime=self.runtime, reference_settings=self.settings,
                  wardrobe_settings=wardrobe_config.WardrobeSettings(configuration,
                      target_resolver=self.settings.target), configuration=configuration,
                  tasks=self.tasks, now=self.tasks.now, notify=lambda: None)

    def test_native_editor_saves_existing_qq_settings_with_revision_checks(self):
        app = self.management_app()
        editor = ReferenceManagement(app, self.native)
        snapshot = editor.status(self.core).unwrap()
        self.assertTrue(snapshot['settings']['group_enabled'])
        data = {'operation_key': 'native-shared-reference-save',
                'edit_token': snapshot['edit_token'], 'changes': {'group_enabled': False}}
        result = editor.configure(self.core, data)
        self.assertTrue(result.is_success, result.failure)
        self.assertFalse(self.settings.read(self.qq, self.tasks.now())['group_enabled'])
        self.assertFalse(self.settings.read(self.native, self.tasks.now())['group_enabled'])
        stale = editor.configure(self.core, {**data, 'operation_key': 'native-stale-save',
                                            'changes': {'group_enabled': True}})
        self.assertFalse(stale.is_success)
        self.assertFalse(self.settings.read(self.qq, self.tasks.now())['group_enabled'])

    def test_native_reference_editor_does_not_grant_standard_user_management(self):
        editor = ReferenceManagement(self.management_app(), self.native)
        result = editor.status(self.standard)
        self.assertFalse(result.is_success)

    def test_existing_native_assets_are_not_hidden_or_migrated(self):
        original = self.service.settings
        self.service.settings = fixtures.SettingsStub(enabled=True)
        self.register(self.native)
        self.service.settings = original
        with self.assertRaises(TaskError) as error:
            self.resolver(self.native)
        self.assertEqual(error.exception.code, 'reference_owner_conflict')
        self.assertEqual(len(self.runtime.read_all('SELECT 1 FROM reference_assets', ())), 1)

    def test_existing_native_wardrobe_without_picture_is_not_hidden(self):
        original = self.service.settings
        self.service.settings = fixtures.SettingsStub(enabled=True)
        self.service.register_item(self.native, item_id='legacy-outfit', name='existing outfit').unwrap()
        self.service.settings = original
        with self.assertRaises(TaskError) as error:
            self.resolver(self.native)
        self.assertEqual(error.exception.code, 'reference_owner_conflict')
        self.assertEqual(len(self.runtime.read_all('SELECT 1 FROM wardrobe_items', ())), 1)

    def test_native_group_core_request_receives_existing_qq_reference_catalog(self):
        from tests import astrbot_data_root
        from astrbot.core.provider.entities import ProviderRequest
        from astrbot_host.core_reply_image_prompt import inject_core_image_prompt
        from companion_v2.conversation.media.output_settings import DEFAULTS
        self.register()
        group = self.group()
        session = NS(application=NS(tasks=self.tasks, messages=NS(
                         reference_materials=self.service, timezone_name='Asia/Shanghai')),
                     context=group, prepared={'native_input': NS(text='来张自拍',
                         at=self.tasks.now())}, state={})
        offered = tuple(NS(spec=NS(action=action)) for action in ('image.generate', 'image.edit'))
        request = ProviderRequest(prompt='来张自拍', system_prompt='Original persona')
        with patch('astrbot_host.core_reply_image_prompt.logger.warning') as warning:
            inject_core_image_prompt(session, request, offered, DEFAULTS)
        warning.assert_not_called()
        material = session.state['_image_reference_material']
        self.assertEqual(material['references'][0]['id'], 'portrait')
        self.assertIsNone(material['wardrobe'])
        self.assertIsNone(material['daily_outfit'])
        self.assertIs(session.context, group)

    def test_withdrawn_native_material_is_still_preserved_as_a_conflict(self):
        original = self.service.settings
        self.service.settings = fixtures.SettingsStub(enabled=True)
        self.register(self.native)
        self.service.withdraw_asset(self.native, 'portrait').unwrap()
        self.service.settings = original
        with self.assertRaises(TaskError) as error:
            self.resolver(self.native)
        self.assertEqual(error.exception.code, 'reference_owner_conflict')
