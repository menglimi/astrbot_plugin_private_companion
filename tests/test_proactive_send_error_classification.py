from astrbot_plugin_private_companion.proactive_message import ProactiveMessageMixin


def test_ntqq_event_checker_send_rejection_is_identified():
    error = (
        "ActionFailed: <ActionFailed status='failed', retcode=1200, data=None, "
        "message='EventChecker Failed: NTEvent serviceAndMethod:NodeIKernelMsgService/sendMsg'>"
    )

    assert ProactiveMessageMixin._is_onebot_event_checker_send_rejection(error) is True


def test_unrelated_onebot_failure_is_not_identified_as_event_checker_rejection():
    assert ProactiveMessageMixin._is_onebot_event_checker_send_rejection("ActionFailed: retcode=1404") is False
    assert ProactiveMessageMixin._is_onebot_event_checker_send_rejection("TimeoutError: sendMsg") is False
