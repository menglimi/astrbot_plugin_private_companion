"""Run separately: real SDK group memory, local WebSocket ACKs and full teardown."""

import asyncio, importlib.util, json, logging, os, sys, tempfile, types
from pathlib import Path
from unittest.mock import patch
repo = Path(__file__).resolve().parents[1]
with tempfile.TemporaryDirectory(prefix='v2-group-memory-native-') as temporary:
    os.environ['ASTRBOT_ROOT'] = temporary
    from tests.group_host_support import GroupContext, group_config
    from tests.native_adapter_support import FakeProvider, completion, final_text
    from companion_v2.adapters.astrbot.model_wire import tool_alias
    from tests.test_astrbot_group_native_delivery import LocalSocket
    from tests.staging_bootstrap_support import wait_for_batch
    from tests.test_management_access import NOW
    from companion_v2.adapters.astrbot.staging import NativeAdapterTypes
    from companion_v2.execution.models import TaskContext
    from companion_v2.foundation.clock import DeterministicClock
    from aiocqhttp import Event
    from aiocqhttp.api_impl import WebSocketReverseApi, ResultStore
    from astrbot.api.message_components import Plain
    from astrbot.core.platform.astrbot_message import AstrBotMessage, MessageMember
    from astrbot.core.platform.message_type import MessageType
    from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_message_event import AiocqhttpMessageEvent
    from astrbot.core.utils.astrbot_path import get_astrbot_data_path
    logging.disable(logging.WARNING)
    assert Path(get_astrbot_data_path()).resolve() == Path(temporary).resolve() / 'data'
    package = types.ModuleType('v2_group_memory_fixture')
    package.__path__ = [str(repo)]
    sys.modules[package.__name__] = package
    spec = importlib.util.spec_from_file_location(package.__name__ + '.main', repo / 'main.py')
    plugin_module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = plugin_module
    spec.loader.exec_module(plugin_module)
    native, clock = GroupContext(), DeterministicClock.at(NOW)
    async def memory_model(payload, tools):
        materials = json.loads(next(m['content'] for m in payload['messages'] if m['role'] == 'user'))['materials']
        speaker = json.loads(next(m['text'] for m in materials if m['purpose'] == 'current_group_speaker'))['speaker_id']
        if speaker == '777' and not any(m['role'] == 'tool' for m in payload['messages']):
            return completion(tools=[types.SimpleNamespace(id='remember', type='function', function=types.SimpleNamespace(
                name=tool_alias('group.memory.remember'), arguments=json.dumps(
                    {'input_handle': 'group-input-1', 'statement': '周六我去公园'}, ensure_ascii=False)))])
        if speaker == '778':
            note = next(m for m in materials if m['purpose'] == 'group_memory')
            assert json.loads(note['text'])['speaker_id'] == '777'
            return completion(final_text('777 说周六去公园。', evidence=note['evidence']))
        return completion(final_text('保存了你的群内发言。'))
    native.provider.client.state.behavior = memory_model
    socket = LocalSocket(-456)
    platform = native.platform
    platform.client._wsr_api_clients = {'100': socket}
    platform.client._api._wsr_api = WebSocketReverseApi(platform.client._wsr_api_clients, set(), 0.02)
    sdk = NativeAdapterTypes('4.27.2', AiocqhttpMessageEvent, FakeProvider,
                             types.SimpleNamespace, types.SimpleNamespace, WebSocketReverseApi)
    controller_type = plugin_module.AstrBotMultiController
    def controller(context, config, root):
        return controller_type(context, config, root, native_types=sdk, now=clock.now)
    def event_for(sender, identifier, text):
        message = AstrBotMessage()
        message.type, message.self_id = MessageType.GROUP_MESSAGE, '100'
        message.group_id = message.session_id = '300'
        message.sender, message.message_id = MessageMember(sender, '合成群成员'), identifier
        message.message, message.message_str = [Plain(text=text)], text
        message.raw_message = Event(post_type='message', message_type='group', sub_type='normal', anonymous=None,
            self_id=100, user_id=int(sender), sender={'user_id': int(sender)}, group_id=300, message_id=int(identifier))
        event = AiocqhttpMessageEvent(text, message, platform.meta(), '300', platform.client)
        event.created_at = clock.now().timestamp()
        return event
    async def verify():
        with patch.object(plugin_module.StarTools, 'get_data_dir', return_value=Path(temporary) / 'v2'), \
                patch.object(plugin_module, 'AstrBotMultiController', controller):
            plugin = plugin_module.PrivateCompanionV2StagingPlugin(native, group_config(group_delivery_mode='onebot_group'))
        try:
            await plugin.initialize()
            ctrl = plugin.staging
            (await ctrl.initialize_profile()).unwrap()
            (await ctrl.start()).unwrap()
            app = ctrl.session.application
            batches = []
            for sender, identifier, text in [('777', '4501', '周六我去公园'), ('778', '4502', '我周日才有空')]:
                event = event_for(sender, identifier, text)
                await plugin.receive_companion_message(event)
                assert event.is_stopped()
                row = app.runtime.read_all('SELECT * FROM conversation_batches ORDER BY rowid', ())[-1]
                batches.append(row['batch_id'])
                await wait_for_batch(ctrl.session, row['batch_id'])
            row = app.messages.batch(batches[-1])
            context = TaskContext.restore(json.loads(row['context_json']))
            request = app.messages._request(row, context, clock.now())
            history = [json.loads(m.payload) for m in request.context.materials if m.purpose == 'group_conversation_history']
            assert [m['type'] for m in history] == ['group_member_message', 'confirmed_group_reply'], 'member and confirmed reply'
            assert history[0]['speaker_id'] == '777' and history[0]['text'] == '周六我去公园'
            assert history[1]['in_reply_to_speaker_id'] == '777' and history[1]['recipient_read'] == 'unknown'
            speaker = [json.loads(m.payload) for m in request.context.materials if m.purpose == 'current_group_speaker']
            assert speaker[0]['speaker_id'] == '778'
            payload = native.provider.client.state.calls[-1][0]
            assert 'group_conversation_history' in json.dumps(payload, ensure_ascii=False)
            assert len(socket.frames) == 2 and all(f['action'] == 'send_group_msg' for f in socket.frames)
            assert len(app.messages.history(context).unwrap()['items']) == 1
            assert app.runtime.read_one("SELECT COUNT(*) FROM memory_views WHERE kind = 'group.public.statement.v1'", ())[0] == 1
            assert app.runtime.read_one("SELECT COUNT(*) FROM memory_atoms", ())[0] == 0
            assert ResultStore._futures == {}
        finally:
            await plugin.terminate()
        assert plugin.staging._terminated and plugin.staging.runtime is None and not native.registered_web_apis
        assert not any(name == 'companion_v2' or name.startswith('companion_v2.') for name in sys.modules)
        print('PASS: real SDK plugin import, two speakers, group memory tool write, attributed recall and grounded model response, two local WS ACKs, stop and route cleanup; temporary root; no real provider or QQ traffic.')
    asyncio.run(verify())
