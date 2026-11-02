"""Image and video captions stay inside one declared wait.

Both narration sidecars run before the reply model answers: `summarize` submits a
supervised task, waits for it, then falls back to a caption-less reply. Four
layers used to be unrelated literals - a provider default (15 s image, 60 s
video), a task definition (35 s / 65 s), a deadline of `wait + 1` that sat
*outside* the waiting reply, and the configured wait itself (1-30 s / 1-60 s).
With the default wait of 8 s or 20 s the round could therefore only ever be cut
by the outermost layer: the provider budget could not expire, the task deadline
could not expire, and the image sidecar returned an empty caption without
recording anything. These tests pin the ordering, the recorded cause, the
unchanged fast path, and the round the previous arithmetic made impossible to
attribute. All providers, sources and conversations are synthetic.
"""

import asyncio
from dataclasses import dataclass
from datetime import datetime
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from companion_v2.conversation.media import image_narration as image_module
from companion_v2.conversation.media.image_narration import (
    IMAGE_NARRATION_BUDGET_SECONDS,
    IMAGE_NARRATION_PROVIDER_TIMEOUT_SECONDS,
    IMAGE_NARRATION_WAIT_SECONDS,
    PrivateImageNarrationService,
    image_narration_layers,
    image_narration_profile,
)
from companion_v2.conversation.media.models import OWNER as IMAGE_OWNER
from companion_v2.conversation.media.understanding import DEFAULTS as UNDERSTANDING_DEFAULTS
from companion_v2.conversation.media.video import narration as video_module
from companion_v2.conversation.media.video.narration import (
    VIDEO_NARRATION_BUDGET_SECONDS,
    VIDEO_NARRATION_PROVIDER_TIMEOUT_SECONDS,
    VIDEO_NARRATION_WAIT_SECONDS,
    NativeVideoNarrationService,
    video_narration_layers,
    video_narration_profile,
)
from companion_v2.conversation.foreground import PromptMaterial
from companion_v2.conversation.reply_settings import IMAGE_NARRATION_DEFAULTS
from companion_v2.execution.models import TaskError, TaskResult
from companion_v2.execution.runner import ExactTaskRunner
from companion_v2.foundation.outcome import Outcome
from companion_v2.foundation.refs import SourceRef, VersionRef
from tests.test_execution import ExecutionFixture
from tests.test_management_access import NOW, SCOPE


@dataclass(frozen=True)
class _Descriptor:
    """Only the fields the caption manifest hashes."""

    position: int
    content_hash: str


def _source(identifier, owner):
    return SourceRef(identifier, owner, SCOPE, VersionRef(identifier, 1), NOW)


class NarrationLayerTests(unittest.TestCase):
    def test_the_configured_default_wait_is_the_one_ceiling(self):
        self.assertEqual(IMAGE_NARRATION_WAIT_SECONDS,
                         IMAGE_NARRATION_DEFAULTS["private_image_vision_wait_seconds"])
        self.assertEqual(VIDEO_NARRATION_WAIT_SECONDS,
                         UNDERSTANDING_DEFAULTS["video_understanding_wait_seconds"])

    def test_every_layer_inside_the_configured_wait_is_ordered(self):
        # These are the values one wait implies. What this plugin budgets per call
        # is the task layer; the model round adds its own profile default, so the
        # round tests below cover both sides of the 7 s / 19 s boundary.
        for waits, layers in ((range(3, 31), image_narration_layers),
                              (range(3, 61), video_narration_layers)):
            for wait in waits:
                provider, task, parent = layers(wait)
                with self.subTest(layers=layers.__name__, wait=wait):
                    self.assertLess(provider, task,
                                    "the model round must expire before the task that owns it")
                    self.assertLess(task, parent,
                                    "the supervised task must expire before the reply stops waiting")
                    self.assertEqual(parent, wait)

    def test_the_shortest_configured_wait_still_yields_usable_floors(self):
        for wait in (1, 2):
            for layers in (image_narration_layers, video_narration_layers):
                provider, task, parent = layers(wait)
                with self.subTest(layers=layers.__name__, wait=wait):
                    self.assertGreaterEqual(provider, 1)
                    self.assertGreaterEqual(task, 2)
                    self.assertEqual(parent, wait)

    def test_profiles_and_task_definitions_use_the_derived_values(self):
        self.assertEqual(image_narration_profile("primary").timeout_seconds,
                         IMAGE_NARRATION_PROVIDER_TIMEOUT_SECONDS)
        self.assertEqual(video_narration_profile("primary").timeout_seconds,
                         VIDEO_NARRATION_PROVIDER_TIMEOUT_SECONDS)
        self.assertEqual({registration.definition.timeout_seconds
                          for registration in PrivateImageNarrationService(None).registrations},
                         {IMAGE_NARRATION_BUDGET_SECONDS})
        self.assertEqual({registration.definition.timeout_seconds
                          for registration in NativeVideoNarrationService(None).registrations},
                         {VIDEO_NARRATION_BUDGET_SECONDS})
        self.assertEqual(image_narration_layers(IMAGE_NARRATION_WAIT_SECONDS),
                         (IMAGE_NARRATION_PROVIDER_TIMEOUT_SECONDS, IMAGE_NARRATION_BUDGET_SECONDS,
                          IMAGE_NARRATION_WAIT_SECONDS))
        self.assertEqual(video_narration_layers(VIDEO_NARRATION_WAIT_SECONDS),
                         (VIDEO_NARRATION_PROVIDER_TIMEOUT_SECONDS, VIDEO_NARRATION_BUDGET_SECONDS,
                          VIDEO_NARRATION_WAIT_SECONDS))


class _NarrationFixture(ExecutionFixture):
    """A real supervisor and runner behind the real owner, with a bounded model round."""

    wait_seconds = 8
    kind = ""
    logger_name = ""

    def setUp(self):
        super().setUp()
        self.slow_round = True
        self.rounds = 0
        self.round_text = "图片类型：表情包；可见内容：一只猫。"
        self.service = self.make_service()
        self.registrations = self.service.registrations
        self.tasks = self.supervisor()
        self.tasks.attach(expected_generation=1).unwrap()
        self.service.tasks = self.tasks
        self.service.gateway = self.fake_gateway()
        self.service.app = SimpleNamespace()
        self.service.runner = ExactTaskRunner(self.tasks, frozenset({self.kind}), self.service.generate)
        self.addAsyncCleanup(self.service.runner.close)

    def make_service(self):
        raise NotImplementedError

    def settings(self):
        raise NotImplementedError

    def payload(self):
        raise NotImplementedError

    async def submit_caption(self):
        raise NotImplementedError

    def fake_gateway(self):
        class Router:
            profiles = {}

            @staticmethod
            def selected_route(_context, task, _request, *, at):
                return (("primary", "model-a", ()) if task != "conversation.reply"
                        else ("other", "model-b", ()))

            @staticmethod
            def read(*_args, **_kwargs):
                return Outcome.success(SimpleNamespace(fields={"prompt": SimpleNamespace(value="fixture")}))

        router = Router()
        router.profiles[self.kind] = object()
        router.configuration = SimpleNamespace(read=Router.read)

        async def generate(_run, _request):
            self.rounds += 1
            if not self.slow_round:
                return SimpleNamespace(draft=SimpleNamespace(text=self.round_text), call_id="call-fast")
            # The gateway's own contract: it owns the innermost budget, settles the
            # call and raises the stable code the owner reports.
            try:
                await asyncio.wait_for(asyncio.sleep(30), timeout=self.provider_seconds)
            except TimeoutError as error:
                raise TaskError("model_timeout", "provider wait timed out; original result is unknown") from error
            return SimpleNamespace(draft=SimpleNamespace(text="never returned"), call_id="call-late")

        return SimpleNamespace(router=router, generate=generate,
                               ledger=SimpleNamespace(discard=lambda _call_id: None))

    async def caption(self):
        return await asyncio.wait_for(self.submit_caption(), timeout=self.wait_seconds + 5)

    def task_row(self):
        row = self.runtime.read_one(
            "SELECT task_id, created_at, deadline_at FROM execution_tasks WHERE kind = ?", (self.kind,))
        self.assertIsNotNone(row)
        return row

    def deadline_seconds(self):
        row = self.task_row()
        return (datetime.fromisoformat(row["deadline_at"])
                - datetime.fromisoformat(row["created_at"])).total_seconds()

    def events(self):
        return tuple(self.runtime.read_all(
            "SELECT state, reason FROM execution_events WHERE task_id = ? ORDER BY revision",
            (self.task_row()["task_id"],)))


class ImageNarrationRoundTests(_NarrationFixture, unittest.IsolatedAsyncioTestCase):
    wait_seconds = IMAGE_NARRATION_WAIT_SECONDS
    kind = "conversation.image_narration"
    logger_name = "companion_v2.conversation.media.image_narration"

    def setUp(self):
        # The fake gateway cuts the round where a real one does: at the profile
        # default, not at a number a different wait would imply.
        self.provider_seconds = IMAGE_NARRATION_PROVIDER_TIMEOUT_SECONDS
        _, self.budget_seconds, self.wait = image_narration_layers(self.wait_seconds)
        super().setUp()

    def make_service(self):
        return PrivateImageNarrationService(self.runtime)

    def settings(self):
        return {"enable_private_image_self_recognition": True,
                "private_image_vision_wait_seconds": self.wait_seconds,
                "image_understanding_private_enabled": True,
                "enable_private_image_vision_cache": False}

    def payload(self):
        return SimpleNamespace(images=(SimpleNamespace(
            source=_source("image-source", IMAGE_OWNER),
            parent=_source("image-parent", "conversation.host_input"),
            descriptor=_Descriptor(0, "a" * 64)),), event=None)

    async def submit_caption(self):
        parent_run = SimpleNamespace(task=SimpleNamespace(spec=SimpleNamespace(context=self.context)))
        request = self.payload()
        self.service.app.messages = SimpleNamespace(
            request=lambda _run: request,
            batch=lambda _identifier: {"role_instruction": "原角色人格提示"})
        with patch.object(image_module, "PromptRequest", lambda *args, **kwargs: SimpleNamespace(**kwargs)):
            return await self.service.summarize(parent_run, "batch-1", self.settings())

    async def test_a_round_that_outlives_its_provider_budget_is_reported_by_the_model_layer(self):
        with self.assertLogs(self.logger_name, level="INFO") as captured:
            caption = await self.caption()
        self.assertEqual(caption, "")
        logged = "\n".join(captured.output)
        self.assertIn("code=model_timeout", logged,
                      "the layer that stopped the round must name itself")
        self.assertNotIn("image_narration_wait_exhausted", logged)
        self.assertIn("provider_seconds_default=%s" % self.provider_seconds, logged)
        self.assertAlmostEqual(self.deadline_seconds(), self.budget_seconds, delta=1)
        self.assertEqual(dict(self.events()[-1]), {"state": "failed", "reason": "worker_error"})
        self.assertEqual(self.rounds, 1)

    async def test_a_wait_below_the_provider_default_stops_at_the_task_layer(self):
        # The wait is configurable down to 1 s while the round keeps the profile
        # default, so below a 7 s wait the supervised task expires first. That is
        # still a named stop -- it is never the silent outer cancellation this
        # change removes.
        self.wait_seconds = 3
        _, self.budget_seconds, self.wait = image_narration_layers(self.wait_seconds)
        with self.assertLogs(self.logger_name, level="INFO") as captured:
            caption = await self.caption()
        self.assertEqual(caption, "")
        logged = "\n".join(captured.output)
        self.assertIn("code=timeout", logged,
                      "the task layer must name itself when it expires before the model round")
        self.assertNotIn("model_timeout", logged)
        self.assertNotIn("image_narration_wait_exhausted", logged)
        self.assertAlmostEqual(self.deadline_seconds(), self.budget_seconds, delta=1)
        self.assertEqual(dict(self.events()[-1]), {"state": "expired", "reason": "timeout"})

    async def test_the_previous_numbers_stopped_the_round_at_the_outer_layer(self):
        # A wait the old arithmetic really overran: deadline ``wait + 1`` sat
        # outside the waiting reply, so the reply cancelled first and recorded
        # nothing the owner could read.
        self.wait_seconds = 3
        with patch.object(image_module, "image_narration_layers",
                          lambda wait: (15, max(2, wait + 1), wait)):
            _, self.budget_seconds, self.wait = image_narration_layers(self.wait_seconds)
            self.provider_seconds = 15
            self.round_text = "never reached"
            with self.assertLogs(self.logger_name, level="INFO") as captured:
                caption = await self.caption()
        self.assertEqual(caption, "")
        logged = "\n".join(captured.output)
        self.assertIn("code=image_narration_wait_exhausted", logged)
        self.assertNotIn("model_timeout", logged,
                         "with the old numbers the model layer could never speak")

    async def test_a_fast_round_keeps_its_caption_and_the_declared_deadline(self):
        self.slow_round = False
        self.assertIn("一只猫", await self.caption())
        self.assertAlmostEqual(self.deadline_seconds(), self.budget_seconds, delta=1)
        self.assertEqual(dict(self.events()[-1]), {"state": "succeeded", "reason": "owner_result"})


class VideoNarrationRoundTests(_NarrationFixture, unittest.IsolatedAsyncioTestCase):
    wait_seconds = VIDEO_NARRATION_WAIT_SECONDS
    kind = "conversation.video_narration"
    logger_name = "companion_v2.conversation.media.video.narration"

    def setUp(self):
        # Same rule as the image sidecar: the cut point is the profile default.
        self.provider_seconds = VIDEO_NARRATION_PROVIDER_TIMEOUT_SECONDS
        _, self.budget_seconds, self.wait = video_narration_layers(self.wait_seconds)
        super().setUp()

    def make_service(self):
        return NativeVideoNarrationService(self.runtime)

    def settings(self):
        return {"video_understanding_enabled": True,
                "video_understanding_wait_seconds": self.wait_seconds,
                "video_understanding_private_enabled": True,
                "video_understanding_max_chars": 2400}

    def payload(self):
        source = _source("video-source", "conversation.video")
        parent = _source("video-parent", "conversation.host_input")
        materials = tuple(PromptMaterial("conversation.video", "current_video_evidence", "原视频", reference)
                          for reference in (source, parent))
        return SimpleNamespace(
            videos=(SimpleNamespace(source=source, parent=parent,
                                    descriptor=_Descriptor(0, "b" * 64), frames=()),),
            event=SimpleNamespace(scope=SCOPE),
            context=SimpleNamespace(materials=materials), role_instruction="原角色人格提示")

    async def submit_caption(self):
        parent_run = SimpleNamespace(task=SimpleNamespace(spec=SimpleNamespace(context=self.context)))
        request = self.payload()
        self.service.app.messages = SimpleNamespace(request=lambda _run: request)
        with patch.object(video_module, "PromptRequest", lambda *args, **kwargs: SimpleNamespace(**kwargs)):
            narrations = await self.service.summarize(parent_run, "batch-1", self.settings())
        return narrations[0]

    async def test_a_round_that_outlives_its_provider_budget_is_reported_by_the_model_layer(self):
        with self.assertLogs(self.logger_name, level="WARNING") as captured:
            narration = await self.caption()
        self.assertEqual((narration.text, narration.available), ("视频转述失败，尚未读到视频内容。", False))
        logged = "\n".join(captured.output)
        self.assertIn("code=model_timeout", logged)
        self.assertNotIn("video_narration_wait_exhausted", logged)
        self.assertAlmostEqual(self.deadline_seconds(), self.budget_seconds, delta=1)
        self.assertEqual(dict(self.events()[-1]), {"state": "failed", "reason": "worker_error"})

    async def test_the_previous_numbers_stopped_the_round_at_the_outer_layer(self):
        self.wait_seconds = 3
        with patch.object(video_module, "video_narration_layers", lambda wait: (60, max(2, wait + 1), wait)):
            _, self.budget_seconds, self.wait = video_narration_layers(self.wait_seconds)
            self.provider_seconds = 60
            self.round_text = "never reached"
            with self.assertLogs(self.logger_name, level="WARNING") as captured:
                narration = await self.caption()
        self.assertEqual(narration.text, "视频转述超时，尚未读到视频内容。")
        logged = "\n".join(captured.output)
        self.assertIn("code=video_narration_wait_exhausted", logged)
        self.assertNotIn("model_timeout", logged)

    async def test_a_wait_below_the_provider_default_stops_at_the_task_layer(self):
        self.wait_seconds = 3
        _, self.budget_seconds, self.wait = video_narration_layers(self.wait_seconds)
        with self.assertLogs(self.logger_name, level="WARNING") as captured:
            narration = await self.caption()
        self.assertEqual((narration.text, narration.available), ("视频转述失败，尚未读到视频内容。", False))
        logged = "\n".join(captured.output)
        self.assertIn("code=timeout", logged,
                      "the task layer must name itself when it expires before the model round")
        self.assertNotIn("model_timeout", logged)
        self.assertNotIn("video_narration_wait_exhausted", logged)
        self.assertAlmostEqual(self.deadline_seconds(), self.budget_seconds, delta=1)
        self.assertEqual(dict(self.events()[-1]), {"state": "expired", "reason": "timeout"})

    async def test_a_fast_round_keeps_its_caption_and_the_declared_deadline(self):
        self.slow_round = False
        self.round_text = "画面里有一只猫跳上窗台。"
        narration = await self.caption()
        self.assertEqual((narration.text, narration.available), ("画面里有一只猫跳上窗台。", True))
        self.assertAlmostEqual(self.deadline_seconds(), self.budget_seconds, delta=1)


if __name__ == "__main__":
    unittest.main()
