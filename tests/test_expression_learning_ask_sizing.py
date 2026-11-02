"""表达学习的申报条数按「本次真实输出上限」算，而不是注册档默认值。

``router.profiles["role.expression.learn"].max_output_tokens`` 只是**没有保存过
本任务上限的安装**所继承的注册档默认值。一次真实执行的上限由路由决定：窗口可以
为这个任务单独保存更小的值，provider 也可能声明自己生成不了注册档那么多。按注册
档估算申报条数，在这些路由上就会多报：模型在第一轮就被 provider 上限或窗口上限
截断，而且这次调用已经付过一次账（``model_output_truncated``）。

因此申报条数必须跟随 ``gateway.output_ceiling`` 解析出的真实上限；拿不到路由
信息时退化为 min(注册档, provider 能力上限)，只少报不多报，绝不让一批学习因为
这条估算失败。所有 provider、用户与平台回执都是合成的。
"""

from types import SimpleNamespace
import json
import unittest

from companion_v2.configuration.models import ConfigLayer, ConfigTarget
from companion_v2.configuration.registry import FieldRegistry
from companion_v2.execution.models import ProviderBinding
from companion_v2.expression import learning
from companion_v2.expression.review import expression_review_profile
from companion_v2.expression.style_service import KIND, expression_learning_profile
from companion_v2.expression.style_management import ExpressionStyleManagement
from companion_v2.model_gateway.models import Amount, LocalProvider
from companion_v2.model_gateway.routing import configuration_fields
from tests import test_expression_v1_pipeline as pipeline
from tests.test_expression_v1_pipeline import ExpressionFixture
from tests.test_host_application import HostFixture


# 注册档默认值：没保存过本任务上限的窗口继承它。按它估算，balanced 会申报 6 条、
# aggressive 会申报 12 条，正好是本次要修的「多报」。
REGISTERED_DEFAULT = 8192
# provider 声明的单项调用能力上限，比注册档默认值小。
PROVIDER_CEILING = 4096
# provider 能力充足时的声明值。
AMPLE_CEILING = 65536


class UnreadableGateway:
    """An older/broken gateway wiring that cannot answer the route peek.

    ``output_ceiling`` is the new read-only seam; a gateway that predates it (or
    that fails to answer) must only cost accuracy in the ask, never the batch.
    """

    def __init__(self, gateway, *, error=None):
        object.__setattr__(self, "_gateway", gateway)
        object.__setattr__(self, "_error", error)

    def __getattr__(self, name):
        if name == "output_ceiling":
            if self._error is None:
                raise AttributeError(name)
            raise self._error
        return getattr(self._gateway, name)


class LearningAskSizingTests(ExpressionFixture, HostFixture, unittest.IsolatedAsyncioTestCase):
    """One real learning batch per declared ceiling."""

    def setUp(self):
        super().setUp()
        self.model_behavior = self.expression_model

    def register_expression_profiles(self, registered=REGISTERED_DEFAULT):
        # The registered default is the reply-sized budget this fixture's
        # installation inherits: high enough that an ask sized from it would
        # promise more candidates than a smaller route can pay for.
        self.profiles += (expression_learning_profile("primary", max_output_tokens=registered),
                          expression_review_profile("primary", max_output_tokens=100),
                          expression_review_profile("primary", max_output_tokens=100, contact=True))
        self.registry = FieldRegistry(version="expression-ask.v1", fields=configuration_fields(self.profiles))

    def declare_provider(self, ceiling):
        """Declare the output capability one dispatch of this provider accepts."""
        self.providers = (LocalProvider(ProviderBinding("model.text", "primary", "1", "model-1"),
            ("text",), ceiling, lambda envelope: Amount(envelope.max_output_tokens + 1024, 1000),
            self.generate),)

    def save_task_ceiling(self, app, context, value):
        """Save the ceiling this window wants for this task, the way the page does."""
        profile = app.router.profiles[KIND]
        target = ConfigTarget(context.scope.installation_id, ConfigLayer.WINDOW,
                              context.role_binding.role, context.scope)
        snapshot = app.configuration.read(self.core, target=target, group_id=profile.group,
                                          observed_at=self.clock.now()).unwrap()
        return app.configuration.patch(self.core, target=target, group_id=profile.group,
            expected_edit_token=snapshot.edit_token,
            changes={profile.group + ".max_output_tokens": value},
            operation=self.operation(target.authorization_scope, owner="configuration"),
            observed_at=self.clock.now()).unwrap()

    async def run_batch(self, *, provider_ceiling, saved=None, mode="balanced", unreadable=None,
                        registered=REGISTERED_DEFAULT):
        """Seed one conversation, learn once and return the sealed learning ask.

        ``unreadable`` selects the degraded gateway: ``"missing"`` for a wiring
        that has no route peek at all, or an exception instance for one that
        fails to answer.
        """
        if not any(profile.kind == KIND for profile in self.profiles):
            self.register_expression_profiles(registered)
        self.declare_provider(provider_ceiling)
        host = await self.start()
        app = host.application
        context = await pipeline.ExpressionReplyPipelineTests.seed(self, host)
        self.configure_expression(app, {"private_learning_enabled": True, "learning_mode": mode})
        if saved is not None:
            self.save_task_ceiling(app, context, saved)
        original = app.expression_style.gateway
        if unreadable is not None:
            app.expression_style.gateway = UnreadableGateway(original,
                error=None if unreadable == "missing" else unreadable)
        try:
            await self.learn_now(app, context)
        finally:
            app.expression_style.gateway = original
        return self.learning_ask()

    def learning_ask(self):
        envelope = next(item for item in reversed(self.called)
                        if item.original.output_format == "role_expression_learning")
        material = json.loads(next(item for item in envelope.original.context.materials
                                   if item.purpose == "role_expression_samples").payload)
        return SimpleNamespace(ask=material["output_schema"]["properties"]["signals"]["maxItems"],
                               instruction=envelope.original.instruction,
                               ceiling=envelope.max_output_tokens)

    async def test_a_capped_provider_asks_six_instead_of_the_registry_twelve(self):
        # aggressive 的注册档默认值 8192 会申报 12 条；这条路由只执行 4096，
        # 按实测口径只够 6 条。多报的 6 条正是第一轮被截断的那次调用。
        sealed = await self.run_batch(provider_ceiling=PROVIDER_CEILING, saved=PROVIDER_CEILING,
                                      mode="aggressive")
        limit = learning.signal_limit("aggressive", PROVIDER_CEILING)

        self.assertEqual(sealed.ceiling, PROVIDER_CEILING, "the dispatch carries the routed ceiling")
        self.assertLess(limit, learning.signal_limit("aggressive", REGISTERED_DEFAULT),
                        "the case only discriminates while the registry default asks for more")
        self.assertEqual(sealed.ask, limit, "the ask has to follow the ceiling the run executes under")
        self.assertIn(learning.mode_instruction("aggressive", limit), sealed.instruction)

    async def test_a_smaller_routed_ceiling_asks_far_fewer_than_six(self):
        # provider 声明 4096，窗口只为这个任务保存 2048：申报 3 条，而不是注册档
        # 8192 的 6 条。多报的 3 条会在 2048 的窗口上限处被截断。
        sealed = await self.run_batch(provider_ceiling=PROVIDER_CEILING, saved=2048, mode="balanced")
        limit = learning.signal_limit("balanced", 2048)

        self.assertEqual(sealed.ceiling, 2048)
        self.assertLess(limit, 6, "a 2048 ceiling cannot pay for six candidates")
        self.assertLess(limit, learning.signal_limit("balanced", REGISTERED_DEFAULT))
        self.assertEqual(sealed.ask, limit)
        self.assertIn(learning.mode_instruction("balanced", limit), sealed.instruction)

    async def test_an_ample_provider_keeps_the_ask_this_route_always_sent(self):
        # provider 能力充足、窗口没有保存本任务上限：真实上限仍是注册档的 8192，
        # 申报条数必须与今天一致（balanced 6 条），不能被这次改动顺手改小。
        sealed = await self.run_batch(provider_ceiling=AMPLE_CEILING, mode="balanced")

        self.assertEqual(sealed.ceiling, REGISTERED_DEFAULT)
        self.assertEqual(sealed.ask, learning.signal_limit("balanced", REGISTERED_DEFAULT))
        self.assertEqual(sealed.ask, 6)
        self.assertIn(learning.mode_instruction("balanced", 6), sealed.instruction)

    async def test_a_raised_route_ceiling_buys_the_candidates_it_can_pay_for(self):
        # 反方向同理：注册档默认值是 2048（只买得起 3 条），窗口把它抬到 8192。
        # 这条路由买得起 6 条，就不该继续只申报 3 条、把一半上限留空。
        sealed = await self.run_batch(provider_ceiling=AMPLE_CEILING, saved=8192, mode="balanced",
                                      registered=2048)
        limit = learning.signal_limit("balanced", 8192)

        self.assertEqual(sealed.ceiling, 8192)
        self.assertGreater(sealed.ask, learning.signal_limit("balanced", 2048),
                           "the raised ceiling affords more than the registry default did")
        self.assertEqual(sealed.ask, limit)
        self.assertIn(learning.mode_instruction("balanced", limit), sealed.instruction)

    async def test_no_route_value_degrades_to_the_stricter_provider_ceiling(self):
        # 网关给不出路由上限（旧接线）：退化为 min(注册档, provider 能力上限)=4096，
        # 申报 6 条而不是注册档的 12 条，并且这一批照常成功、不抛错。
        sealed = await self.run_batch(provider_ceiling=PROVIDER_CEILING, saved=PROVIDER_CEILING,
                                      mode="aggressive", unreadable="missing")
        limit = learning.signal_limit("aggressive", min(REGISTERED_DEFAULT, PROVIDER_CEILING))

        self.assertEqual(sealed.ceiling, PROVIDER_CEILING)
        self.assertEqual(sealed.ask, limit)
        self.assertLess(sealed.ask, learning.signal_limit("aggressive", REGISTERED_DEFAULT))

    async def test_a_broken_route_reader_only_costs_accuracy_in_the_ask(self):
        # 路由读取本身出错也一样：这条估算不许成为一批学习的新失败点。
        sealed = await self.run_batch(provider_ceiling=PROVIDER_CEILING, saved=PROVIDER_CEILING,
                                      mode="aggressive",
                                      unreadable=TypeError("reader does not take a run"))
        limit = learning.signal_limit("aggressive", min(REGISTERED_DEFAULT, PROVIDER_CEILING))

        self.assertEqual(sealed.ceiling, PROVIDER_CEILING)
        self.assertEqual(sealed.ask, limit)

    async def test_a_plan_above_the_provider_ceiling_is_still_refused(self):
        # 「申报几条」与「这份上限是否合法」是两件事：窗口保存的上限大于 provider
        # 能力时，路由照旧以 model_output_limit 拒绝这份计划。本改动的退化估算只
        # 影响条数，不会把不可发送的计划变成可发送，也不会把拒绝换成截断。
        # 用「保存值」而不是「注册默认值」表达这份合同：注册默认值只是产品预算，
        # 结构化任务的默认值会按 provider 能力收敛（role.expression.learn 有
        # thinking 余量），只有用户保存过的值才必须原样被拒绝。
        # 这个用例不走 run_batch，档位要先自己注册。
        self.register_expression_profiles()
        self.declare_provider(PROVIDER_CEILING)
        host = await self.start()
        app = host.application
        context = await pipeline.ExpressionReplyPipelineTests.seed(self, host)
        self.configure_expression(app, {"private_learning_enabled": True, "learning_mode": "balanced"})
        self.save_task_ceiling(app, context, REGISTERED_DEFAULT)
        ExpressionStyleManagement(app).learn(operator=self.core, context=context).unwrap()
        await self.until(lambda: not app.expression_style.status(context)["running"])
        last = app.expression_style.status(context)["last_result"]

        self.assertEqual(last["state"], "failed", last)
        self.assertEqual(last["code"], "model_output_limit")
        self.assertFalse(any(item.original.output_format == "role_expression_learning"
                             for item in self.called), "an invalid plan must never be sent")


if __name__ == "__main__":
    unittest.main()
