"""Image uploads must reach the post, and verification must observe those images."""
import asyncio
import base64
from io import BytesIO
import json
from types import SimpleNamespace
import unittest
from datetime import timedelta
from unittest.mock import AsyncMock, patch

from PIL import Image

from astrbot_host.qzone_api import QzoneWebAPI
from astrbot_host.qzone_client import QzoneHttpClient
from companion_v2.execution.models import TaskError
from companion_v2.foundation.outcome import Outcome
from companion_v2.qzone import feed as F, protocol as P, policy as Y
from companion_v2.qzone.images import (PublishImage, image_batch, album_images, candidate_image,
                                       list_album_candidates)
from companion_v2.qzone.service import owner_key
from companion_v2.qzone.transport import TransportOutcome
from tests.test_qzone_extension import QzoneServiceFixture, FakeTransport, NOW, PUBLISH, COOKIE


def picture(color="navy"):
    output = BytesIO()
    Image.new("RGB", (24, 16), color).save(output, "PNG")
    return PublishImage.read(output.getvalue(), mime_type="image/png")


def upload_receipt(index=1):
    return {"code": 0, "data": {"albumid": "album", "lloc": f"original-{index}",
        "sloc": f"small-{index}", "type": 1, "width": 24, "height": 16,
        "url": f"https://photo.store.qq.com/a/original-{index}?bo=TEST%3D"}}


class ImageTransport(FakeTransport):
    def __init__(self, *, photos=1, text="带图测试", wrong_id=False, upload_error=False, unknown="", psc=False):
        super().__init__()
        self.photos, self.body = photos, text
        self.wrong_id, self.upload_error, self.unknown = wrong_id, upload_error, unknown
        self.uploads = 0
        self.psc = psc

    async def send(self, request, *, timeout):
        self.sent.append(request)
        if request.description == self.unknown:
            return TransportOutcome(0, delivery_unknown=True, error="synthetic timeout")
        if request.description == "image.upload":
            self.uploads += 1
            payload = {"code": 0, "data": {}} if self.upload_error else upload_receipt(self.uploads)
        elif request.description == "post.publish":
            payload = {"code": 0, "tid": "new-post"}
        else:
            payload = {"code": 0, "msglist": [{"tid": "old-post" if self.wrong_id else "new-post",
                "uin": 123456789, "content": self.body,
                "pic": [{"url1": (f"https://photo.store.qq.com/psc?/Vshared/original-{i+1}/b&bo=TEST" if self.psc
                                    else f"https://photo.store.qq.com/a/original-{i+1}?bo=TEST"),
                         "width": 24, "height": 16} for i in range(self.photos)]}]}
        return TransportOutcome(200, text=json.dumps(payload))


class ImageProtocolTests(unittest.TestCase):
    def test_album_and_prepared_candidates_use_original_bytes_and_recheck_revocation(self):
        from dataclasses import replace
        from companion_v2.conversation.media.generated import GeneratedImages
        from tests.test_generated_album_share import CandidateTests, album_item, context
        image = picture()
        item = album_item(content_hash=image.content_hash)
        item = replace(item, content=image.content, descriptor=replace(item.descriptor,
            width=image.width, height=image.height, byte_count=len(image.content)))
        planner, repositories = CandidateTests().planner()
        runtime = SimpleNamespace(read_one=lambda sql, values: {"content": image.content})
        app = SimpleNamespace(runtime=runtime, tasks=SimpleNamespace(identity=object()),
            messages=SimpleNamespace(generated_images=SimpleNamespace(
                album_image=lambda *args: item, album_share_candidate=GeneratedImages.album_share_candidate)))
        images, candidates = album_images(app, context(), NOW, [item.image_id], planner=planner)
        self.assertEqual(images[0].content, image.content)
        selected = candidate_image(app, context(), NOW, candidates[0], planner)
        self.assertEqual(selected.content_hash, image.content_hash)
        repositories["assets"].readable = False
        with self.assertRaises(TaskError):
            candidate_image(app, context(), NOW, candidates[0], planner)
        with self.assertRaises(TaskError):
            album_images(app, context(), NOW, [item.image_id], planner=planner)

    def test_media_duplicate_keys_track_images_instead_of_shared_caption_words(self):
        state = Y.note_publish(Y.QzoneRuntimeState(), text="带图测试", media_key="first-photo",
                               at=NOW, day="2026-10-02")
        settings = {"enabled": True, "publish_enabled": True, "publish_max_daily": 5,
                    "action_min_interval_minutes": 30}
        later = NOW + timedelta(hours=1)
        self.assertIn("重复", Y.publish_reason(state, settings, text="带图测试", media_key="first-photo",
                                               at=later, day="2026-10-02"))
        self.assertEqual(Y.publish_reason(state, settings, text="带图测试", media_key="second-photo",
                                          at=later, day="2026-10-02"), "")
        self.assertEqual(Y.publish_reason(state, settings, text="", media_key="image-only",
                                          at=later, day="2026-10-02"), "")
        self.assertEqual(Y.QzoneRuntimeState.restore(state.document()).recent_publish_media, ("first-photo",))

    def test_bytes_are_decoded_checked_and_the_upload_receipt_forms_an_attachment(self):
        image = picture()
        data_url = "data:image/png;base64," + base64.b64encode(image.content).decode()
        self.assertEqual(PublishImage.from_data_url(data_url), image)
        credential = P.credential_from_cookies(P.parse_cookie_text(COOKIE))
        upload = P.upload_image_request(credential, image)
        self.assertEqual(base64.b64decode(upload.data["picfile"]), image.content)
        self.assertIs(upload.credential, credential)
        receipt = P.uploaded_image(P.parse_response(json.dumps(upload_receipt())))
        self.assertEqual(receipt.pic_bo, "TEST=")
        request = P.publish_request(credential, "", (receipt,))
        self.assertEqual(request.data["richval"], ",album,original-1,small-1,1,16,24,,16,24")
        self.assertEqual(request.data["con"], " ")
        self.assertEqual(request.data["pic_bo"], "TEST=")

    def test_invalid_content_mime_and_missing_upload_fields_are_not_accepted(self):
        for value in ("https://example.com/photo.png", "data:image/png;base64,broken",
                      "data:image/svg+xml;base64,PHN2Zz4=", "data:image/png;base64,bm90LWltYWdl"):
            with self.assertRaises(TaskError):
                PublishImage.from_data_url(value)
        with self.assertRaises(TaskError):
            PublishImage.read(picture().content, mime_type="image/jpeg")
        with self.assertRaises(TaskError):
            P.uploaded_image({"code": 0})
        with self.assertRaises(TaskError):
            image_batch([picture()] * 10)

    def test_json_and_html_keep_image_only_posts_and_reject_unsafe_urls(self):
        raw = {"code": 0, "msglist": [{"tid": "image", "uin": 1, "content": "",
            "pic": [{"url3": "https://photo.store.qq.com/a/original-1", "width": 10},
                    {"url1": "javascript:alert(1)"}]}]}
        posts = F.posts_from_msglist(raw, my_uin=1, limit=10)
        self.assertEqual(len(posts), 1)
        self.assertEqual(len(posts[0].images), 1)
        self.assertIn("original-1", posts[0].images[0].identities)
        html = '<li data-uin="1" data-key="image"><img data-original="//photo.store.qq.com/a/img?x=1&amp;y=2"></li>'
        posts = F.posts_from_html(html, my_uin=1, limit=10)
        self.assertEqual(posts[0].images[0].url, "https://photo.store.qq.com/a/img?x=1&y=2")
        self.assertEqual(F.image_urls_from_html("<img src='https://q1.qlogo.cn/avatar'><img class='emoji' src='https://photo.store.qq.com/emoji'>"), ())

    def test_psc_photo_identity_lives_in_the_query_and_excludes_shared_album(self):
        url = "http://photo.store.qq.com/psc?/V54sharedAlbum/uniquePhotoToken!!/b&bo=DIMENSIONS&w=392"
        photo = F.photo_from_mapping({"url3": url})
        self.assertEqual(photo.identities, ("uniquePhotoToken!!",))
        self.assertEqual(P.image_location_ids(url), P.image_location_ids("uniquePhotoToken!!"))
        self.assertNotEqual(photo.identities,
            F.photo_from_mapping({"url3": url.replace("uniquePhotoToken", "differentPhoto")}).identities)

    def test_http_client_keeps_the_original_credential_for_upload_and_publish(self):
        async def call():
            credential = P.credential_from_cookies(P.parse_cookie_text(COOKIE))
            client = QzoneHttpClient(bot_provider=lambda: None)
            with patch.object(client, "credential", AsyncMock(side_effect=AssertionError("must not refresh"))), \
                 patch.object(client, "_exchange", AsyncMock(return_value=TransportOutcome(200, "{}"))) as exchange:
                await client.send(P.upload_image_request(credential, picture()), timeout=30)
                self.assertIs(exchange.call_args.args[1], credential)
        asyncio.run(call())


class ImageServiceTests(QzoneServiceFixture):
    def install_transport(self, **kwargs):
        self.transport = ImageTransport(**kwargs)
        self.service.transport = self.transport

    def publish(self, *, text="带图测试", images=None, key="image-test"):
        return asyncio.run(self.service.publish(self.context, text=text, images=images or (picture(),),
                                               operation_key=key, **PUBLISH))

    def test_two_uploads_are_attached_and_verified_against_the_returned_post(self):
        self.install_transport(photos=2, psc=True)
        result = self.publish(images=(picture(), picture("red")))
        self.assertTrue(result["verified"])
        self.assertTrue(result["images_verified"])
        self.assertEqual(result["image_count"], 2)
        self.assertEqual([r.description for r in self.transport.sent],
                         ["image.upload", "image.upload", "post.publish", "feed.list"])
        posted = self.transport.sent[2]
        self.assertEqual(len(posted.data["richval"].split("\t")), 2)
        self.assertIs(posted.credential, self.transport.sent[0].credential)

    def test_text_only_readback_is_pending_and_replay_never_upgrades_it_to_verified(self):
        self.install_transport(photos=0)
        result = self.publish()
        self.assertEqual(result["status"], "accepted")
        self.assertFalse(result["verified"])
        count = len(self.transport.sent)
        self.service.store.write(owner_key(self.context), Y.QzoneRuntimeState(), at=NOW)
        replay = self.publish()
        self.assertEqual(replay["status"], "accepted")
        self.assertFalse(replay["images_verified"])
        self.assertEqual(len(self.transport.sent), count)

    def test_a_different_post_or_different_photo_cannot_verify_the_new_post(self):
        self.install_transport(wrong_id=True)
        self.assertFalse(self.publish()["verified"])
        self.service.store.write(owner_key(self.context), Y.QzoneRuntimeState(), at=NOW)
        self.install_transport()
        with patch.object(P, "uploaded_image", return_value=P.UploadedImage("receipt", "bo", "different", "other")):
            self.assertFalse(self.publish(key="other-image")["verified"])

    def test_image_only_publish_and_scan_keep_the_photo(self):
        self.install_transport(text="")
        result = self.publish(text="")
        self.assertTrue(result["images_verified"])
        scan = self.scan(scope="own", detail=True)
        self.assertEqual(scan["count"], 1)
        self.assertEqual(len(scan["items"][0]["images"]), 1)
        self.assertEqual(scan["items"][0]["excerpt"], "")

    def test_incomplete_upload_receipt_never_sends_a_text_only_post(self):
        self.install_transport(upload_error=True)
        with self.assertRaises(TaskError) as caught:
            self.publish()
        self.assertEqual(caught.exception.code, "qzone_image_upload_failed")
        self.assertEqual([r.description for r in self.transport.sent], ["image.upload"])

    def test_unknown_upload_or_publish_is_not_replayed(self):
        for stage in ("image.upload", "post.publish"):
            self.install_transport(unknown=stage)
            with self.assertRaises(TaskError) as caught:
                self.publish(key=stage)
            self.assertEqual(caught.exception.code, "qzone_delivery_unknown")
            count = len(self.transport.sent)
            with self.assertRaises(TaskError):
                self.publish(key=stage)
            self.assertEqual(len(self.transport.sent), count)

    def test_dashboard_local_and_album_images_reach_the_same_service_in_order(self):
        self.install_transport(photos=2)
        api = QzoneWebAPI(SimpleNamespace(session=SimpleNamespace(application=SimpleNamespace()), last_session=None))
        album = picture("red")
        local = picture()
        command = {"text": "带图测试", **PUBLISH, "operation_key": "web-image",
            "images": [{"image_id": "generated-image:fixture:0"},
                       {"data_url": "data:image/png;base64," + base64.b64encode(local.content).decode()}]}
        async def run(call):
            return await call(None, self.service, NOW)
        with patch.object(api, "_command", AsyncMock(return_value=command)), \
             patch.object(api, "_context", return_value=self.context), \
             patch.object(api, "_planner", return_value=object()), \
             patch.object(api, "_run", side_effect=run), \
             patch("astrbot_host.qzone_api.album_images", return_value=((album,), ("candidate",))):
            result = asyncio.run(api.publish())
        self.assertTrue(result["receipt"]["images_verified"])
        sent = [r for r in self.transport.sent if r.description == "image.upload"]
        self.assertEqual([base64.b64decode(r.data["picfile"]) for r in sent], [album.content, local.content])

    def test_outfit_album_image_resolution_and_list_album_candidates(self):
        from pathlib import Path
        from tempfile import TemporaryDirectory
        from companion_v2.conversation.media.generated import GeneratedAlbumImage
        from companion_v2.conversation.media.models import ImageDescriptor
        from companion_v2.references.daily_outfit import PREFIX as OUTFIT_PREFIX, daily_outfit_image_id
        from companion_v2.references.service import owner_key as ref_owner_key
        from companion_v2.storage.generated_album import ALBUM_HIDDEN_KEY_PREFIX
        from companion_v2.storage.image_payloads import store_image
        from companion_v2.storage.runtime import StorageRuntime

        outfit_img = picture("green")
        gen_visible = picture("navy")
        gen_hidden = picture("red")
        with TemporaryDirectory(prefix="qzone-album-test-") as tmp:
            runtime = StorageRuntime.open(data_root=Path(tmp), installation_id=self.context.scope.installation_id)
            try:
                owner = ref_owner_key(self.context)
                marker = store_image(runtime.data_root, outfit_img.content, mime_type="image/png").decode("ascii")
                outfit_id = daily_outfit_image_id(owner, "2026-10-02", outfit_img.content_hash)
                hidden_id = "generated-image:inv-hidden:0"
                visible_id = "generated-image:inv-visible:0"
                with runtime.transaction() as conn:
                    conn.execute(
                        "INSERT INTO runtime_metadata(key, value) VALUES (?, ?)",
                        (
                            f"{OUTFIT_PREFIX}{owner}:2026-10-02",
                            json.dumps({
                                "task_id": "outfit-task-1",
                                "photo": {
                                    "marker": marker,
                                    "content_hash": outfit_img.content_hash,
                                    "created_at": "2026-10-02T08:00:00+00:00",
                                    "mime_type": "image/png",
                                    "width": outfit_img.width,
                                    "height": outfit_img.height,
                                    "size_bytes": len(outfit_img.content),
                                    "weather": {"condition": "晴", "temperature_c": 22},
                                    "activities": ["散步", "咖啡店看书"],
                                },
                            }),
                        ),
                    )
                    conn.execute(
                        "INSERT INTO runtime_metadata(key, value) VALUES (?, ?)",
                        (ALBUM_HIDDEN_KEY_PREFIX + self.context.key, json.dumps([hidden_id])),
                    )

                gen_items = [
                    GeneratedAlbumImage(
                        visible_id,
                        "2026-10-02T09:00:00+00:00",
                        "image.generate",
                        ImageDescriptor(0, "image/png", gen_visible.width, gen_visible.height,
                                        len(gen_visible.content), gen_visible.content_hash),
                        gen_visible.content,
                    ),
                    GeneratedAlbumImage(
                        hidden_id,
                        "2026-10-02T10:00:00+00:00",
                        "image.generate",
                        ImageDescriptor(0, "image/png", gen_hidden.width, gen_hidden.height,
                                        len(gen_hidden.content), gen_hidden.content_hash),
                        gen_hidden.content,
                    ),
                ]
                generated_service = SimpleNamespace(
                    album_page=lambda ctx, at, limit=24, cursor=None: {"items": gen_items, "next_cursor": None},
                    album_prompt_excerpt=lambda ctx, at, image_id: "雨后街角咖啡馆窗景" if image_id == visible_id else "",
                )
                identity = SimpleNamespace(authorize=lambda *args, **kwargs: Outcome.success(True))
                app = SimpleNamespace(
                    runtime=runtime,
                    tasks=SimpleNamespace(identity=identity),
                    messages=SimpleNamespace(generated_images=generated_service),
                )

                resolved, candidates = album_images(app, self.context, NOW, [outfit_id])
                self.assertEqual(len(resolved), 1)
                self.assertEqual(resolved[0].content, outfit_img.content)
                self.assertEqual(resolved[0].source_id, outfit_id)
                self.assertEqual(candidates, (outfit_id,))

                listed = list_album_candidates(app, self.context, NOW, limit=10)
                self.assertEqual([item["image_id"] for item in listed], [visible_id, outfit_id])
                self.assertEqual(listed[0]["summary"], "雨后街角咖啡馆窗景")
                self.assertIn("2026-10-02 每日穿搭", listed[1]["summary"])
                self.assertIn("散步", listed[1]["summary"])

                app.tasks.identity = SimpleNamespace(
                    authorize=lambda *args, **kwargs: Outcome.failed("authorization_revoked", "revoked")
                )
                with self.assertRaises(TaskError) as denied:
                    album_images(app, self.context, NOW, [outfit_id])
                self.assertEqual(denied.exception.code, "authorization_revoked")
            finally:
                runtime.close()


if __name__ == "__main__":
    unittest.main()
