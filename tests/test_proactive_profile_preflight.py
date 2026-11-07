"""Contact preflight keeps safe numerical diagnostics for model limits."""

from types import SimpleNamespace as NS
import unittest

from companion_v2.execution.models import TaskError
from companion_v2.proactive.service import ProactiveContacts


class ContactProfilePreflightTests(unittest.TestCase):
    def preflight(self, error):
        def reserve(_envelope):
            raise error

        provider = NS(binding=NS(provider="provider-a"), maximum_input_estimate=2048,
                      maximum_output_tokens=1024, reserve=reserve)
        envelope = NS(max_output_tokens=2048)
        plan = NS(providers=(provider,), envelope=envelope)
        service = NS(gateway=NS(router=NS(resolve=lambda _run, _request: plan)))
        run = NS(task=NS(task_id="contact-a"))
        return ProactiveContacts._contact_request_fits(service, run, object()), run

    def test_output_limit_names_budget_and_provider_capacity(self):
        result, run = self.preflight(TaskError("host_model_limit_output", "output exceeded",
            details={"requested_output_tokens": 2048, "provider_max_output_tokens": 1024}))
        fits, detail = result
        self.assertFalse(fits)
        self.assertEqual(detail["limit_code"], "host_model_limit_output")
        self.assertEqual((detail["requested_output_tokens"], detail["provider_max_output_tokens"]),
                         (2048, 1024))
        with self.assertLogs("companion_v2.proactive.service", level="WARNING") as logs:
            with self.assertRaises(TaskError) as error:
                ProactiveContacts._contact_profile_failure(run, detail)
        self.assertEqual(error.exception.code, "contact_model_profile_exceeded")
        self.assertEqual(error.exception.details, detail)
        self.assertIn("limit_code=host_model_limit_output", logs.output[0])

    def test_input_limit_keeps_estimate_and_unrelated_failures_propagate(self):
        (fits, detail), _ = self.preflight(TaskError("host_model_limit_input", "input exceeded",
            details={"estimated_input_tokens": 4096, "provider_max_input_tokens": 2048}))
        self.assertFalse(fits)
        self.assertEqual((detail["estimated_input_tokens"], detail["provider_max_input_tokens"]),
                         (4096, 2048))
        with self.assertRaises(TaskError) as error:
            self.preflight(TaskError("provider_unavailable", "unrelated failure"))
        self.assertEqual(error.exception.code, "provider_unavailable")


if __name__ == "__main__":
    unittest.main()
