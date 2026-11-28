"""Focused host-route contract tests for role-scoped image uploads."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


class ImageMaterialUploadRouteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory(prefix="companion-v2-image-api-")
        cls.repository_root = Path(__file__).resolve().parents[1]
        cls.environment = os.environ.copy()
        cls.environment["ASTRBOT_ROOT"] = cls.temporary.name
        backend_app = Path(sys.executable).resolve().parents[1] / "app"
        paths = [str(cls.repository_root), str(backend_app)]
        paths.extend(value for value in cls.environment.get("PYTHONPATH", "").split(os.pathsep) if value)
        cls.environment["PYTHONPATH"] = os.pathsep.join(paths)

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def test_upload_decode_and_route_contract(self):
        source = r'''
from astrbot_host.image_material_api import ImageMaterialsWebAPI
from companion_v2.execution.models import TaskError

payload, mime = ImageMaterialsWebAPI._decode_upload("data:image/png;base64,aGVsbG8=")
assert (payload, mime) == (b"hello", "image/png")
for data_url, code in (
    ("data:image/gif;base64,aGVsbG8=", "reference_image_type_invalid"),
    ("data:image/png;base64,%%%", "reference_image_invalid"),
    ("data:image/png;base64," + "A" * (16 * 1024 * 1024 + 4), "reference_upload_too_large"),
):
    try:
        ImageMaterialsWebAPI._decode_upload(data_url)
    except TaskError as error:
        assert error.code == code
    else:
        raise AssertionError("invalid upload data was accepted")
routes = {(path.removeprefix("/astrbot_plugin_private_companion/image-materials"), tuple(methods))
          for path, _handler, methods, _description in ImageMaterialsWebAPI(None).routes()}
assert ("/references/upload", ("POST",)) in routes
assert ("/references/update", ("POST",)) in routes
assert ("/references/order", ("POST",)) in routes
assert ("/album/export", ("POST",)) in routes
assert ("/album/hide", ("POST",)) in routes
assert ("/album/open-folder", ("POST",)) in routes
print("upload route contract passed")
'''
        result = subprocess.run([sys.executable, "-c", source], cwd=self.repository_root,
                                env=self.environment, capture_output=True, text=True, check=False)

        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        self.assertIn("upload route contract passed", result.stdout)

    def test_host_routes_persist_usage_gallery_and_daily_outfit_for_one_role(self):
        source = r'''
import asyncio, base64, json, unittest
from datetime import timedelta
from io import BytesIO
from threading import get_ident
from types import SimpleNamespace as NS
from unittest.mock import patch
from PIL import Image
from astrbot.api.web import PluginMultiDict
from tests.multi_host_support import MultiFixture
from tests.test_configuration_api import call as config_call
from companion_v2.references.repositories import ReferenceAssetRepository
from companion_v2.references.service import owner_key
from companion_v2.references.usage import default_usage
from companion_v2.storage.runtime import StorageRuntime

class MaterialRoutes(MultiFixture, unittest.IsolatedAsyncioTestCase):
    async def test_materials(self):
        app = await self.start_host()
        app.daily_outfit.next_check = app.now() + timedelta(days=1)
        self.controller.register_routes()
        handlers = {route: handler for route, handler, _, _ in self.native.registered_web_apis}
        paths = [route for route in handlers if route.endswith('/image-materials')]
        first = next(route for route in paths if '/targets/first/roles/' in route)
        other = next(route for route in paths if '/targets/first/roles/' in route and route != first)
        config = first.removesuffix('/image-materials') + '/config'
        async def call(path, body=None, username='materials-admin', query=()):
            async def read():
                return json.dumps(body, ensure_ascii=False).encode('utf-8')
            with patch('astrbot_host.image_material_api.request',
                       NS(username=username, query=PluginMultiDict(list(query)), body=read)):
                return await handlers[path]()
        def data(response):
            self.assertEqual(response['status'], 'ok', response)
            return response['data']
        try:
            self.assertEqual((await call(first, username=None))['data']['code'], 'dashboard_identity_required')
            self.assertEqual((await call(first, query=[('unexpected', '1')]))['data']['code'],
                             'image_material_request_invalid')
            self.assertEqual((await call(first+'/album', query=[('cursor', 'abcdefgh'),
                                ('cursor', 'ijklmnop')]))['data']['code'], 'image_material_request_invalid')
            for kind, changes in [('photo.reference', {'enabled': True, 'daily_outfit_enabled': True}),
                                  ('wardrobe', {'enabled': True, 'prompt_enabled': True})]:
                snapshot = data(await config_call(handlers[config], query={'sections': kind}))['sections'][kind]
                data(await config_call(handlers[config+'/configure'], {'section': kind,
                    'operation_key': 'materials-config-'+kind, 'edit_token': snapshot['edit_token'], 'changes': changes}))
            image = BytesIO()
            Image.new('RGB', (24, 24), '#438f70').save(image, format='PNG')
            url = 'data:image/png;base64,' + base64.b64encode(image.getvalue()).decode('ascii')
            assets = []
            for index in range(2):
                request = {'data_url': url, 'label': '素材'+str(index), 'roles': ['identity', 'outfit'],
                           'operation_key': 'materials-upload-'+str(index)}
                asset = data(await call(first+'/references/upload', request))
                self.assertEqual(data(await call(first+'/references/upload', request))['asset_id'], asset['asset_id'])
                assets.append(asset)
            original = [item['asset_id'] for item in assets]
            usage = {**default_usage(), 'base_identity': True, 'outfit_category': 'home',
                     'outfit_lock': 'reference', 'preferred_scene': 'home-night', 'scenes': ['卧室'], 'times': ['睡前']}
            updated = data(await call(first+'/references/update', {'asset_id': original[0], 'label': '基础身份',
                'roles': ['identity', 'outfit', 'continuity'], 'expected_revision': 1, 'usage': usage}))
            self.assertEqual(updated['usage'], usage)
            stale = await call(first+'/references/update', {'asset_id': original[0], 'label': '过期编辑',
                'roles': ['identity'], 'expected_revision': 1, 'usage': usage})
            self.assertEqual(stale['data']['code'], 'reference_revision_conflict')
            order = {'expected_asset_ids': original, 'asset_ids': original[::-1]}
            self.assertEqual(data(await call(first+'/references/order', order))['asset_ids'], original[::-1])
            self.assertEqual((await call(first+'/references/order', order))['data']['code'], 'reference_order_conflict')
            self.assertEqual((await call(other+'/references/update', {'asset_id': original[0], 'label': '其他角色',
                'roles': ['identity'], 'expected_revision': 2, 'usage': usage}))['data']['code'], 'reference_asset_missing')
            wardrobe = data(await call(first+'/wardrobe', {'operation_key': 'materials-wardrobe-create',
                'name': '白色针织衫', 'description': '居家', 'tags': ['针织'], 'asset_id': original[0]}))
            outfit = data(await call(first+'/daily-outfit', {'item_id': wardrobe['item_id']}))
            inventory = data(await call(first))
            album = data(await call(first+'/album'))
            self.assertEqual([item['asset_id'] for item in inventory['references']], original[::-1])
            self.assertEqual(album['items'], [])
            self.assertEqual(inventory['references'][1]['usage'], usage)
            self.assertEqual(inventory['daily_outfit']['item_id'], wardrobe['item_id'])
            self.assertEqual(data(await call(other))['references'], [])
            loop = asyncio.get_running_loop()
            main_thread = get_ident()
            launched = loop.create_future()
            prepare_request = app.daily_outfit.prepare_request

            def prepare_outfit(context, **kwargs):
                self.assertNotEqual(get_ident(), main_thread)
                return prepare_request(context, **kwargs)

            async def run_outfit(key, spec):
                launched.set_result(asyncio.get_running_loop())

            with patch.object(app.daily_outfit, 'port', NS(outfit_image_route=lambda *_: True)), patch.object(
                app.daily_outfit, 'prepare_request', side_effect=prepare_outfit
            ), patch.object(app.daily_outfit, '_run', side_effect=run_outfit):
                queued = data(await call(first+'/daily-outfit/generate', {'operation_key': 'manual-regenerate'}))
                self.assertEqual(queued['state'], 'created')
                self.assertIs(await asyncio.wait_for(launched, timeout=2), loop)
            reopened = StorageRuntime.open(data_root=app.runtime.data_root, installation_id=app.runtime.installation_id)
            try:
                repo = ReferenceAssetRepository(reopened)
                owner = owner_key(self.controller.authority.context('first', 'role-a'))
                self.assertEqual([item.asset_id for item in repo.list(owner)], original[::-1])
                self.assertEqual(repo.usage_of(owner, original[0]), usage)
                self.assertEqual(repo.daily_outfit_item(owner, outfit['local_date']).item_id, wardrobe['item_id'])
            finally:
                reopened.close()
            data(await call(first+'/wardrobe/withdraw', {'item_id': wardrobe['item_id']}))
            self.assertIsNone(data(await call(first))['daily_outfit'])
        finally:
            self.controller.unregister_routes()
            self.assertFalse(self.native.registered_web_apis)
            await self.controller.terminate()

unittest.main()
'''
        result = subprocess.run([sys.executable, '-X', 'utf8', '-c', source], cwd=self.repository_root,
                                env=self.environment, capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)


if __name__ == "__main__":
    unittest.main()
