"""相册图 → 分享素材：这座桥必须与生成时发布的引用**逐字一致**。

为什么值得单独钉一条用例：``album_refs`` 是"重建"而不是"记录"——它按
``(父来源, 位置, 内容哈希)`` 重新算出三个引用，因为生成图在落库时就已登记为受治理的
source 与 asset。重建逻辑一旦与真正的生成路径漂移，分享域就会在**读取**时才发现
（``SharePlanner`` 会去源仓库核对），那时错误已经很难定位。所以这里直接把两边算出来
对比，用真实生成时用的同一个助手（``ConversationImages.references``）。
"""

import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from companion_v2.conversation.media.generated import GeneratedAlbumImage, GeneratedImages, _AlbumView
from companion_v2.conversation.media.models import ImageDescriptor
from companion_v2.conversation.media.service import ConversationImages
from companion_v2.execution.models import digest
from companion_v2.foundation.refs import RoleOwnerRef, ScopeKind, ScopeRef, SourceRef, VersionRef
from companion_v2.sharing.models import ShareMaterial, ShareMaterialKind


NOW = datetime(2026, 10, 2, 8, 0, tzinfo=timezone.utc)
INSTALLATION = "installation-album-share"
SCOPE = ScopeRef(INSTALLATION, "onebot", ScopeKind.PRIVATE, "private-a", bot_id="bot-a",
                 account_id="account-a", conversation_id="private-a", session_id="private-a")


def context():
    principal = SimpleNamespace(native_account_id="account-a")
    return SimpleNamespace(scope=SCOPE, key="private-a", actor=SimpleNamespace(principal=principal),
                           role_binding=SimpleNamespace(role=SimpleNamespace(subject_id="role-a")))


def album_item(*, invocation_id="inv-1", position=0, created_at=None, content_hash="1" * 64):
    return GeneratedAlbumImage(
        image_id=f"generated-image:{invocation_id}:{position}",
        created_at=(created_at or NOW).isoformat(),
        action="image.generate",
        descriptor=ImageDescriptor(position=position, mime_type="image/png", width=512, height=512,
                                   byte_count=1024, content_hash=content_hash),
        content=b"bytes")


class ReferenceTests(unittest.TestCase):
    def test_the_rebuilt_refs_equal_the_ones_the_generation_path_publishes(self):
        item = album_item()
        parent, source, asset = GeneratedImages.album_refs(item, context())

        # 生成时的做法：先由 invocation 推出父来源，再用同一个助手算图片引用。
        identifier = "generated-images-" + digest("inv-1")
        expected_parent = SourceRef(identifier, "conversation.generated_images", SCOPE,
                                    VersionRef(identifier, 1), NOW)
        self.assertEqual(parent, expected_parent)
        expected_source, expected_asset = ConversationImages.references(
            _AlbumView(expected_parent, context().actor.principal, SCOPE), item.descriptor)
        self.assertEqual(source, expected_source)
        self.assertEqual(asset, expected_asset)

    def test_the_refs_carry_the_scope_and_the_content_hash(self):
        _, source, asset = GeneratedImages.album_refs(album_item(content_hash="9" * 64), context())
        self.assertEqual(source.scope, SCOPE)
        self.assertEqual(asset.scope, SCOPE)
        self.assertEqual(asset.content_hash, "9" * 64)
        self.assertEqual(source.source_id, asset.asset_id, "同一次生成的图片，来源与素材共用一个标识")

    def test_a_different_position_is_a_different_image(self):
        _, first, first_asset = GeneratedImages.album_refs(album_item(position=0), context())
        _, second, second_asset = GeneratedImages.album_refs(album_item(position=1), context())
        self.assertNotEqual(first, second)
        self.assertNotEqual(first_asset.asset_id, second_asset.asset_id)

    def test_an_unusable_image_id_is_refused(self):
        from companion_v2.execution.models import TaskError
        for image_id in ("", "not-an-album-image", "generated-image:", "generated-image:inv-1"):
            with self.assertRaises(TaskError):
                GeneratedImages.album_refs(SimpleNamespace(image_id=image_id, created_at=NOW.isoformat(),
                                                           descriptor=album_item().descriptor), context())


class MaterialTests(unittest.TestCase):
    def test_the_material_points_at_the_published_source_and_asset(self):
        item = album_item()
        material = GeneratedImages.album_share_material(item, context())
        _, source, asset = GeneratedImages.album_refs(item, context())

        self.assertIsInstance(material, ShareMaterial)
        self.assertIs(material.kind, ShareMaterialKind.ROLE_ARTIFACT)
        self.assertEqual(material.source, source)
        self.assertEqual(material.asset, asset)
        self.assertEqual(material.created_at, NOW)
        self.assertEqual(material.scope, SCOPE)

    def test_the_material_declares_the_bot_that_owns_the_image(self):
        # 分享域要求角色自有素材声明它的 bot/人格，否则读取方无法判断这是谁的作品。
        material = GeneratedImages.album_share_material(album_item(), context())
        self.assertEqual(material.role_owner, RoleOwnerRef(INSTALLATION, "bot-a", "role-a"))

    def test_the_material_id_is_stable_for_the_same_image_and_differs_across_images(self):
        first = GeneratedImages.album_share_material(album_item(), context())
        again = GeneratedImages.album_share_material(album_item(), context())
        other = GeneratedImages.album_share_material(album_item(position=1), context())
        self.assertEqual(first.material_id, again.material_id)
        self.assertNotEqual(first.material_id, other.material_id)

    def test_a_blank_bot_id_never_produces_a_material_that_claims_an_owner(self):
        # 没有 bot 就没有"这是谁的作品"，素材模型会拒绝——这里确认我们不静默兜底。
        from companion_v2.foundation.refs import ReferenceValidationError
        bare = ScopeRef(INSTALLATION, "onebot", ScopeKind.PRIVATE, "private-a")
        with self.assertRaises(ReferenceValidationError):
            GeneratedImages.album_share_material(album_item(), SimpleNamespace(
                scope=bare, key="private-a",
                actor=SimpleNamespace(principal=SimpleNamespace(native_account_id="account-a")),
                role_binding=SimpleNamespace(role=SimpleNamespace(subject_id="role-a"))))


class CandidateTests(unittest.TestCase):
    """候选必须经 SharePlanner 复检：它真的去读源仓库与素材仓库。"""

    def planner(self, *, sources=True, assets=True, calls=None):
        from companion_v2.foundation.outcome import Outcome
        from companion_v2.sharing.planner import ShareCatalog, SharePlanner

        class Repo:
            def __init__(self, readable):
                self.readable = readable
                self.seen = []

            def read(self, reference, **kwargs):
                self.seen.append((reference, kwargs))
                if not self.readable:
                    return Outcome.failed("source_revoked", "material is not readable")
                return Outcome.success(reference)

        recorded = calls if calls is not None else {}
        source_repo, asset_repo = Repo(sources), Repo(assets)
        planner = SharePlanner(sources=source_repo, assets=asset_repo, catalog=ShareCatalog())
        recorded["sources"], recorded["assets"] = source_repo, asset_repo
        return planner, recorded

    def test_the_candidate_is_revalidated_against_the_source_and_asset_repositories(self):
        planner, recorded = self.planner()
        item = album_item()
        result = GeneratedImages.album_share_candidate(
            item, context(), planner=planner, capability="qzone.publish", at=NOW,
            authorizer=SimpleNamespace())

        candidate = result.unwrap()
        material = GeneratedImages.album_share_material(item, context())
        self.assertEqual(candidate.material, material)
        self.assertEqual(candidate.target_scope, SCOPE)
        self.assertEqual([reference for reference, _ in recorded["sources"].seen], [material.source])
        self.assertEqual([reference for reference, _ in recorded["assets"].seen], [material.asset])
        # 复检带着身份与能力，不是"看一眼就算过"。
        _, kwargs = recorded["sources"].seen[0]
        self.assertEqual(kwargs["capability"], "qzone.publish")
        self.assertEqual(kwargs["observed_at"], NOW)

    def test_a_material_that_cannot_be_read_never_becomes_a_candidate(self):
        planner, _ = self.planner(sources=False)
        result = GeneratedImages.album_share_candidate(
            album_item(), context(), planner=planner, capability="qzone.publish", at=NOW,
            authorizer=SimpleNamespace())
        self.assertFalse(result.is_success)
        self.assertEqual(result.failure.code, "source_revoked")

    def test_an_unreadable_asset_also_refuses_the_candidate(self):
        planner, recorded = self.planner(assets=False)
        result = GeneratedImages.album_share_candidate(
            album_item(), context(), planner=planner, capability="qzone.publish", at=NOW,
            authorizer=SimpleNamespace())
        self.assertFalse(result.is_success, "素材读不到就不该给出候选")
        self.assertEqual(len(recorded["assets"].seen), 1)

    def test_the_same_image_always_yields_the_same_candidate_id(self):
        first, _ = self.planner()
        second, _ = self.planner()
        item = album_item()
        one = GeneratedImages.album_share_candidate(item, context(), planner=first,
                                                    capability="qzone.publish", at=NOW,
                                                    authorizer=SimpleNamespace()).unwrap()
        two = GeneratedImages.album_share_candidate(item, context(), planner=second,
                                                    capability="qzone.publish", at=NOW,
                                                    authorizer=SimpleNamespace()).unwrap()
        self.assertEqual(one.candidate_id, two.candidate_id)
        self.assertEqual(one.material.source.source_id, two.material.source.source_id)

    def test_preparing_the_same_image_later_reuses_the_candidate_after_rechecking_access(self):
        planner, recorded = self.planner()
        item = album_item()
        first = GeneratedImages.album_share_candidate(item, context(), planner=planner,
            capability="qzone.post.publish", at=NOW, authorizer=SimpleNamespace()).unwrap()
        later = NOW + timedelta(minutes=1)
        again = GeneratedImages.album_share_candidate(item, context(), planner=planner,
            capability="qzone.post.publish", at=later, authorizer=SimpleNamespace()).unwrap()

        self.assertIs(again, first)
        self.assertEqual(again.prepared_at, NOW)
        for repository in (recorded["sources"], recorded["assets"]):
            self.assertEqual([kwargs["observed_at"] for _, kwargs in repository.seen], [NOW, later])

    def test_preparing_a_known_image_does_not_bypass_newly_revoked_access(self):
        planner, recorded = self.planner()
        item = album_item()
        GeneratedImages.album_share_candidate(item, context(), planner=planner,
            capability="qzone.post.publish", at=NOW, authorizer=SimpleNamespace()).unwrap()
        recorded["assets"].readable = False

        result = GeneratedImages.album_share_candidate(item, context(), planner=planner,
            capability="qzone.post.publish", at=NOW + timedelta(minutes=1),
            authorizer=SimpleNamespace())
        self.assertFalse(result.is_success)
        self.assertEqual(result.failure.code, "source_revoked")

    def test_a_known_candidate_cannot_be_rebound_to_changed_material(self):
        planner, _ = self.planner()
        material = GeneratedImages.album_share_material(album_item(), context())
        arguments = {"resolved": context().actor, "capability": "qzone.post.publish",
                     "authorizer": SimpleNamespace(), "role_owner": material.role_owner}
        first = planner.prepare(material, observed_at=NOW, **arguments).unwrap()
        changed = replace(material, created_at=NOW + timedelta(seconds=1))

        with self.assertRaisesRegex(ValueError, "different material"):
            planner.prepare(changed, observed_at=NOW + timedelta(minutes=1), **arguments)
        self.assertEqual(planner.follow_up_material(first.candidate_id,
            observed_at=NOW + timedelta(minutes=2), **arguments).unwrap(), material)


    def test_a_material_revoked_after_the_candidate_was_prepared_is_refused_at_follow_up(self):
        """候选是"稍后发布"的凭据，不是通行证：发布前复检要能当场拒绝。"""
        planner, recorded = self.planner()
        item = album_item()
        candidate = GeneratedImages.album_share_candidate(
            item, context(), planner=planner, capability="qzone.post.publish", at=NOW,
            authorizer=SimpleNamespace()).unwrap()

        recorded["sources"].readable = False          # 页面停留期间这张图被撤销
        follow_up = planner.follow_up_material(candidate.candidate_id, resolved=context().actor,
                                               capability="qzone.post.publish", observed_at=NOW,
                                               authorizer=SimpleNamespace(),
                                               role_owner=RoleOwnerRef(INSTALLATION, "bot-a", "role-a"))
        self.assertFalse(follow_up.is_success)
        self.assertEqual(follow_up.failure.code, "source_revoked")

    def test_an_unknown_candidate_is_refused(self):
        planner, _ = self.planner()
        follow_up = planner.follow_up_material("share-candidate-does-not-exist", resolved=context().actor,
                                               capability="qzone.post.publish", observed_at=NOW,
                                               authorizer=SimpleNamespace(),
                                               role_owner=RoleOwnerRef(INSTALLATION, "bot-a", "role-a"))
        self.assertFalse(follow_up.is_success)
        self.assertEqual(follow_up.failure.code, "share_candidate_not_found")


if __name__ == "__main__":
    unittest.main()
