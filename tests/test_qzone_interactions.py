"""Interaction regression tests using QQ's separate like-list response shape."""

import asyncio
import json
import unittest

from companion_v2.qzone import protocol as P
from companion_v2.qzone.references import QzonePost
from companion_v2.qzone.transport import TransportOutcome
from tests.test_qzone_extension import COOKIE, MSGLIST, FakeTransport, QzoneServiceFixture


class InteractionProtocolTests(unittest.TestCase):
    def test_fallback_resource_and_referer_name_the_author_once(self):
        credential = P.credential_from_cookies(P.parse_cookie_text(COOKIE))
        post = QzonePost(post_id="test-tid", author_uin="123456789", author_name="me", excerpt="test")
        like, read = P.like_request(credential, post), P.like_list_request(credential, post)
        self.assertEqual(like.data["unikey"], "http://user.qzone.qq.com/123456789/mood/test-tid.1")
        self.assertEqual(read.params["unikey"], like.data["unikey"])
        self.assertEqual(like.headers["Referer"], "https://user.qzone.qq.com/123456789/mood/test-tid")
        self.assertIs(like.credential, read.credential)
        self.assertIs(P.comment_request(credential, post, "test").credential, credential)

    def test_a_total_count_or_someone_elses_like_does_not_prove_our_like(self):
        self.assertFalse(P.like_list_has_self({"like_num": 10, "like_uin_info": [{"uin": 9}]}, 1))
        self.assertFalse(P.like_list_has_self({"isliked": "0"}, 1))
        self.assertFalse(P.like_list_has_self({"total_number": 0, "is_dolike": 0, "like_uin_info": []}, 1))
        self.assertTrue(P.like_list_has_self({"total_number": 80, "is_dolike": 1, "like_uin_info": []}, 1))
        self.assertTrue(P.like_list_has_self({"data": {"like_uin_info": [{"uin": 1}]}}, 1))


class InteractionTransport(FakeTransport):
    def __init__(self, *, delayed=False):
        super().__init__()
        self.reads = 0
        self.delayed = delayed

    async def send(self, request, *, timeout):
        self.sent.append(request)
        if request.description == "post.likes":
            self.reads += 1
            liked = not (self.delayed and self.reads == 1)
            # Own-post likes may be omitted from the public count and roster.
            return TransportOutcome(200, json.dumps({"code": 0, "data": {
                "total_number": 0, "is_dolike": int(liked), "like_uin_info": []}}))
        return TransportOutcome(200, MSGLIST)


class InteractionServiceTests(QzoneServiceFixture):
    def test_like_is_verified_even_when_msglist_omits_all_like_fields(self):
        self.transport = InteractionTransport(delayed=True)
        self.service.transport = self.transport
        self.service.verify_delays = (0, 0)
        item = self.scan(scope="own")["items"][0]
        result = asyncio.run(self.service.like(self.context, post_ref=item["post_ref"], operation_key="like-list"))
        self.assertTrue(result["verified"])
        self.assertEqual(self.transport.reads, 2)
        self.assertEqual(sum(r.description == "post.like" for r in self.transport.sent), 1)

    def test_detail_scan_reads_like_roster_and_shows_our_like(self):
        self.transport = InteractionTransport()
        self.service.transport = self.transport
        result = self.scan(scope="own", detail=True)
        self.assertTrue(result["items"][0]["liked"])
        self.assertTrue(any(r.description == "post.likes" for r in self.transport.sent))


if __name__ == "__main__":
    unittest.main()
