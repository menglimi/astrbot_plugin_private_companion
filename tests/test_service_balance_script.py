"""Synthetic scripts and real loopback HTTPS/CONNECT/SOCKS5 transports."""

import asyncio
import base64
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import ipaddress
import json
import re
from pathlib import Path
import ssl
from tempfile import TemporaryDirectory
import time
import unittest
from unittest.mock import patch

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from astrbot_host.service_balance import configured_balance_port, balance_observation_data
from astrbot_host.service_balance_script import BalanceQueryError, ScriptServiceBalancePort, _execute
from companion_v2.proactive.balance_events import BalanceObservation
from tests.contact_schedule_support import ContactScheduleFixture

SCRIPT = """({request:{url:"{{baseUrl}}/v1/user/balance",method:"POST",
headers:{Authorization:"Bearer {{apiKey}}", "Content-Type":"application/json"},
body:JSON.stringify({key:"{{apiKey}}"})},extractor:function(response){
return {...response.data.display,isValid:response.success !== false};}})"""


def settings(**changes):
    return {"advanced_enabled": True, "advanced_base_url": "https://balance.example.test",
            "advanced_api_key": "synthetic-token", "advanced_script": SCRIPT, **changes}


class ScriptBalanceTests(unittest.TestCase):
    def test_issue_response_enters_the_existing_observation_contract(self):
        port = ScriptServiceBalancePort(settings())
        result = _execute(SCRIPT, json.dumps({"success": True, "data": {"display": {
            "remaining": 12.3, "used": 4.5, "total": 16.8, "unit": "CNY",
            "planName": "Synthetic plan", "extra": "For management only"}}}))
        observation = port.parse(result, datetime.now(timezone.utc))
        self.assertIs(type(observation), BalanceObservation)
        self.assertEqual((observation.balance, observation.total, observation.used),
                         (Decimal("12.3"), Decimal("16.8"), Decimal("4.5")))
        self.assertEqual(balance_observation_data(port, observation)["currency_label"], "CNY")
        self.assertEqual(observation.plan_name, "Synthetic plan")

    def test_zero_total_minus_used_and_final_units(self):
        port = ScriptServiceBalancePort(settings())
        at = datetime.now(timezone.utc)
        self.assertEqual(port.parse({"remaining": 0}, at).balance, Decimal(0))
        self.assertEqual(port.parse({"total": 10, "used": 3}, at).balance, Decimal(7))
        self.assertEqual(port.parse({"total": 1, "used": 3}, at).balance, Decimal(0))
        self.assertEqual(port.parse({"remaining": 500000}, at).balance, Decimal(500000))
        for result in ({}, {"remaining": None}, {"remaining": True}, {"remaining": -1},
                       {"remaining": "1"}, {"remaining": float("inf")}, {"isValid": "yes"}):
            with self.subTest(result=result), self.assertRaises(ValueError):
                port.parse(result, at)

    def test_invalid_account_and_metadata_are_redacted(self):
        port = ScriptServiceBalancePort(settings(advanced_proxy_url="socks5://name:proxy-secret@localhost:1234"))
        with self.assertRaises(BalanceQueryError) as raised:
            port.parse({"isValid": False, "remaining": 0,
                        "invalidMessage": "synthetic-token proxy-secret rejected"}, datetime.now(timezone.utc))
        self.assertEqual(raised.exception.state, "account_invalid")
        self.assertNotIn("synthetic-token", str(raised.exception))
        self.assertNotIn("proxy-secret", str(raised.exception))
        result = port.parse({"remaining": 1, "extra": "synthetic-token"}, datetime.now(timezone.utc))
        self.assertEqual(result.extra, "[redacted]")

    def test_variables_are_data_and_never_injected_as_javascript(self):
        key = 'quote"\\ token'
        port = ScriptServiceBalancePort(settings(advanced_api_key=key))
        url, method, headers, body = port._request(_execute(SCRIPT, variables=port.variables))
        self.assertEqual(url, "https://balance.example.test/v1/user/balance")
        self.assertEqual(method, "POST")
        self.assertEqual(headers["Authorization"], "Bearer " + key)
        self.assertEqual(json.loads(body), {"key": key})
        for request in ({"url": "http://example.test"}, {"url": "https://u:p@example.test"},
                        {"url": "https://example.test", "method": "BOGUS"},
                        {"url": "https://example.test", "body": "x" * 16385}):
            with self.subTest(request=request), self.assertRaises(ValueError):
                port._request(request)

    def test_sandbox_errors_async_arrays_and_resource_limits(self):
        started = time.monotonic()
        for script in ("(() => {while(true){}})()", "new Array(100000000).fill('x')",
                       "({request:{},extractor:42})", "(() => {throw Error('DO-NOT-ECHO')})()"):
            with self.subTest(script=script), self.assertRaises(BalanceQueryError) as raised:
                _execute(script)
            self.assertNotIn("DO-NOT-ECHO", str(raised.exception))
        self.assertLess(time.monotonic() - started, 3)
        for extractor in ("async function(r){return {remaining:1}}", "function(r){return [{remaining:1}]}" ,
                          "function(r){return {remaining:Infinity}}"):
            with self.subTest(extractor=extractor), self.assertRaises(BalanceQueryError):
                _execute("({request:{},extractor:" + extractor + "})", "{}")
        sandbox = "({request:{},extractor:function(r){return {extra:[typeof fetch,typeof require,typeof process,typeof std,typeof os].join(',')}}})"
        self.assertEqual(_execute(sandbox, "{}")["extra"], ",".join(["undefined"] * 5))

    def test_mode_selection_has_no_fallback_and_fingerprints_all_active_inputs(self):
        old = {"url": "https://newapi.example.test", "api_key": "old-token", "value_divisor": "500000"}
        self.assertEqual(configured_balance_port({"service_balance": old}).provider, "newapi")
        enabled = {**old, **settings()}
        port = configured_balance_port({"service_balance": enabled})
        self.assertEqual(port.provider, "script")
        with self.assertRaises(ValueError):
            configured_balance_port({"service_balance": {**enabled, "advanced_script": ""}})
        disabled = {**enabled, "advanced_enabled": False, "advanced_script": "bad ignored script"}
        self.assertEqual(configured_balance_port({"service_balance": disabled}).provider, "newapi")
        for field, value in (("advanced_script", SCRIPT + " "), ("advanced_api_key", "new-token"),
                             ("advanced_access_token", "access-secret"), ("advanced_user_id", "456"),
                             ("advanced_base_url", "https://different.example.test"),
                             ("advanced_proxy_url", "http://localhost:1234")):
            self.assertNotEqual(port.source_fingerprint,
                ScriptServiceBalancePort(settings(**{field: value})).source_fingerprint)


class BuiltinBalancePresetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        source = (Path(__file__).resolve().parents[1] / "pages" / "陪伴小屋" / "perception-overview.js").read_text(encoding="utf-8")
        cls.presets = json.loads(re.search(r"const balanceScriptExamples = (\{.*?\n\});", source, re.S).group(1))

    def port(self, name, **changes):
        return ScriptServiceBalancePort(settings(advanced_script=self.presets[name], **changes))

    def extract(self, name, response):
        port = self.port(name)
        result = _execute(port.script, json.dumps(response), port.variables)
        return port.parse(result, datetime.now(timezone.utc))

    def test_generic_default_valid_invalid_and_zero(self):
        port = self.port("generic")
        request = _execute(port.script, variables=port.variables)
        self.assertEqual(request["url"], "https://balance.example.test/user/balance")
        self.assertEqual(request["method"], "GET")
        self.assertEqual(request["headers"]["User-Agent"], "astrbot_plugin_private_companion")
        for active in (True, None):
            self.assertEqual(self.extract("generic", {"balance": 0, "is_active": active}).balance, Decimal(0))
        with self.assertRaises(BalanceQueryError) as raised:
            self.extract("generic", {"balance": 12, "is_active": False})
        self.assertEqual(raised.exception.state, "account_invalid")

    def test_newapi_access_token_user_id_and_all_amounts(self):
        port = self.port("newapi", advanced_access_token="access-secret", advanced_user_id="1234")
        request = _execute(port.script, variables=port.variables)
        self.assertEqual(request["url"], "https://balance.example.test/api/user/self")
        self.assertEqual(request["headers"]["Authorization"], "Bearer access-secret")
        self.assertEqual(request["headers"]["New-Api-User"], "1234")
        observation = self.extract("newapi", {"success": True, "data": {
            "quota": 6250000, "used_quota": 2250000, "group": "test-group"}})
        self.assertEqual((observation.balance, observation.used, observation.total, observation.plan_name),
                         (Decimal("12.5"), Decimal("4.5"), Decimal("17"), "test-group"))
        fallback = self.extract("newapi", {"success": True, "data": {"quota": 0, "used_quota": 0}})
        self.assertEqual((fallback.balance, fallback.plan_name), (Decimal(0), "默认套餐"))
        with self.assertRaises(BalanceQueryError) as raised:
            self.extract("newapi", {"success": False, "message": "Synthetic query failed"})
        self.assertEqual(str(raised.exception), "Synthetic query failed")

    def test_sub2api_normalizes_base_url_before_string_operations(self):
        for base in ("https://balance.example.test", "https://balance.example.test/",
                     "https://balance.example.test/v1", "https://balance.example.test/v1///"):
            port = self.port("sub2api", advanced_base_url=base)
            request = _execute(port.script, variables=port.variables)
            self.assertEqual(request["url"], "https://balance.example.test/v1/usage")
        result = self.extract("sub2api", {"remaining": 12.3, "planName": "test-plan", "quota": {
            "limit": 20, "used": 7.7, "unit": "CNY"}})
        self.assertEqual((result.balance, result.total, result.used, result.unit),
                         (Decimal("12.3"), Decimal(20), Decimal("7.7"), "CNY"))
        fallback = self.extract("sub2api", {"quota": {"limit": 20, "used": 7.7}})
        self.assertEqual((fallback.balance, fallback.unit), (Decimal("12.3"), "USD"))
        with self.assertRaises(BalanceQueryError) as raised:
            self.extract("sub2api", {"isValid": False, "status": "Synthetic expiration"})
        self.assertEqual(str(raised.exception), "Synthetic expiration")
        with self.assertRaises(BalanceQueryError):
            self.extract("sub2api", {})

    def test_variables_cannot_escape_double_single_or_template_literals(self):
        value = "\"';globalThis.injected=true;//\\${globalThis.injected=true} emoji-\U0001f600"
        for quote in ('"', "'", "`"):
            script = "({request:{value:" + quote + "{{apiKey}}" + quote + ",injected:globalThis.injected===true},extractor:r=>({remaining:1})})"
            result = _execute(script, variables={"apiKey": value})
            self.assertEqual(result["value"], value)
            self.assertFalse(result["injected"])
        with self.assertRaises(BalanceQueryError):
            _execute("({request:{url:'{{unknown}}'},extractor:r=>({remaining:1})})")

    def test_all_presets_use_plugin_user_agent(self):
        self.assertEqual(set(self.presets), {"generic", "newapi", "sub2api"})
        for script in self.presets.values():
            self.assertNotIn("cc-switch", script)
            request = _execute(script)
            self.assertEqual(request["headers"]["User-Agent"], "astrbot_plugin_private_companion")


class ScriptBalanceContactTests(ContactScheduleFixture, unittest.IsolatedAsyncioTestCase):
    async def test_extracted_amount_uses_original_threshold_material_and_contact_pipeline(self):
        clock = self.clock
        class ExtractedPort(ScriptServiceBalancePort):
            async def fetch(self):
                # Isolate transport here; the socket tests below cover that half.
                raw = json.dumps({"success": True, "data": {"display": {
                    "remaining": 2.25, "total": 100, "unit": "CNY",
                    "extra": "MANAGEMENT-ONLY", "planName": "PRIVATE-PLAN"}}})
                return self.parse(await self._run(raw), clock.now())
        host, batch = await self.scheduled(balance_port=ExtractedPort(settings(), now=clock.now))
        app = host.application
        changes = {"balance_awareness_enabled": True, "balance_include_amount_in_message": True,
                   "chat.enabled": False}
        changes.update({name + ".enabled": False for name in ("affection", "care", "play", "repair", "invitation")})
        self.settings(app, changes).unwrap()
        self.advance(app, 60)
        await self.automatic_delivery(app)
        from companion_v2.proactive.balance_events import SOURCE_OWNER
        material = next(item for item in self.contact_calls()[0].original.context.materials if item.owner == SOURCE_OWNER)
        body = json.loads(material.payload)
        self.assertEqual((body["level"], body["amount"], body["currency"]), ("critical", "2.25", "CNY"))
        self.assertNotIn("MANAGEMENT-ONLY", material.payload)
        self.assertNotIn("PRIVATE-PLAN", material.payload)


class ScriptBalanceKeywordTests(unittest.IsolatedAsyncioTestCase):
    async def test_script_unit_and_amount_enter_the_existing_keyword_material(self):
        from types import SimpleNamespace
        from astrbot_host.service_balance import balance_keyword_material
        from companion_v2.foundation.refs import ScopeKind
        class ExtractedPort(ScriptServiceBalancePort):
            async def fetch(self):
                raw = json.dumps({"success": True, "data": {"display": {
                    "remaining": 12.3, "total": 16.8, "unit": "CNY",
                    "extra": "MANAGEMENT-ONLY", "planName": "PRIVATE-PLAN"}}})
                return self.parse(await self._run(raw), datetime.now(timezone.utc))
        app = SimpleNamespace(balance_port=ExtractedPort(settings()), runtime=object())
        context = SimpleNamespace(scope=SimpleNamespace(kind=ScopeKind.PRIVATE))
        with patch("companion_v2.expression.configuration.source_subject_allowed_for_context", return_value=True):
            spec = balance_keyword_material(app)
            self.assertTrue(spec.usable(context))
        result = await spec.prepare(context, "余额", datetime.now(timezone.utc))
        self.assertEqual((result["state"], result["balance"], result["currency"]), ("ok", "12.30", "CNY"))
        self.assertNotIn("MANAGEMENT-ONLY", json.dumps(result))
        self.assertNotIn("PRIVATE-PLAN", json.dumps(result))

    async def test_source_change_discards_an_inflight_keyword_amount(self):
        from types import SimpleNamespace
        from astrbot_host.service_balance import balance_keyword_material
        started, release = asyncio.Event(), asyncio.Event()
        class Port:
            async def fetch(self):
                started.set()
                await release.wait()
                return BalanceObservation(Decimal("123"), None, datetime.now(timezone.utc))
        app = SimpleNamespace(balance_port=Port())
        pending = asyncio.create_task(balance_keyword_material(app).prepare(None, "余额", None))
        await started.wait()
        app.balance_port = object()
        release.set()
        self.assertEqual(await pending, {"state": "unavailable", "code": "balance_source_changed"})


class ScriptBalanceTransportTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temporary = TemporaryDirectory(prefix="balance-synthetic-tls-")
        self.addCleanup(self.temporary.cleanup)
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "balance.example.test")])
        now = datetime.now(timezone.utc)
        cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(x509.random_serial_number()).not_valid_before(now - timedelta(minutes=1))
            .not_valid_after(now + timedelta(days=1))
            .add_extension(x509.SubjectAlternativeName([x509.DNSName("balance.example.test"),
                x509.DNSName("localhost"), x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]), critical=False)
            .sign(key, hashes.SHA256()))
        root = Path(self.temporary.name)
        (root / "cert.pem").write_bytes(cert.public_bytes(serialization.Encoding.PEM))
        (root / "key.pem").write_bytes(key.private_bytes(serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
        server_ssl = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        server_ssl.load_cert_chain(root / "cert.pem", root / "key.pem")
        self.client_ssl = ssl.create_default_context(cafile=str(root / "cert.pem"))
        self.requests, self.proxy_requests, self.tasks = [], [], set()
        self.body = json.dumps({"success": True, "data": {"display": {
            "remaining": 12.3, "used": 4.5, "total": 16.8, "unit": "CNY"}}}).encode()
        self.status, self.delay = 200, 0
        self.origin = await asyncio.start_server(self._origin, "127.0.0.1", 0, ssl=server_ssl)
        self.port = self.origin.sockets[0].getsockname()[1]
        self.servers = [self.origin]

    async def asyncTearDown(self):
        for server in self.servers:
            server.close()
            await server.wait_closed()
        for task in list(self.tasks):
            task.cancel()
        if self.tasks:
            await asyncio.gather(*self.tasks, return_exceptions=True)

    async def _origin(self, reader, writer):
        self.tasks.add(asyncio.current_task())
        try:
            head = await reader.readuntil(b"\r\n\r\n")
            length = next((int(line.split(b":", 1)[1]) for line in head.split(b"\r\n")
                           if line.lower().startswith(b"content-length:")), 0)
            body = await reader.readexactly(length) if length else b""
            self.requests.append((head, body))
            await asyncio.sleep(self.delay)
            writer.write(f"HTTP/1.1 {self.status} Test\r\nContent-Length: {len(self.body)}\r\nConnection: close\r\n\r\n".encode() + self.body)
            await writer.drain()
        except (ConnectionError, asyncio.IncompleteReadError):
            pass
        finally:
            writer.close()
            self.tasks.discard(asyncio.current_task())

    async def _proxy(self, scheme, auth=False):
        async def handler(reader, writer):
            self.tasks.add(asyncio.current_task())
            upstream_writer = None
            try:
                if scheme == "http":
                    head = await reader.readuntil(b"\r\n\r\n")
                    self.proxy_requests.append(head)
                    if auth and b"Proxy-Authorization: Basic " + base64.b64encode(b"name:proxy-pass") not in head:
                        writer.write(b"HTTP/1.1 407 Authentication required\r\nContent-Length: 0\r\n\r\n")
                        await writer.drain()
                        return
                    writer.write(b"HTTP/1.1 200 Connection established\r\n\r\n")
                else:
                    version, count = await reader.readexactly(2)
                    self.assertEqual(version, 5)
                    methods = await reader.readexactly(count)
                    writer.write(bytes([5, 2 if auth else 0]));await writer.drain()
                    if auth:
                        version, count = await reader.readexactly(2)
                        username = await reader.readexactly(count)
                        count = (await reader.readexactly(1))[0]
                        password = await reader.readexactly(count)
                        self.assertEqual((username, password), (b"name", b"proxy-pass"))
                        writer.write(bytes([1, 0]));await writer.drain()
                    version, command, reserved, address_type = await reader.readexactly(4)
                    self.assertEqual((version, command, address_type), (5, 1, 3))
                    count = (await reader.readexactly(1))[0]
                    hostname = (await reader.readexactly(count)).decode()
                    port = int.from_bytes(await reader.readexactly(2), "big")
                    self.proxy_requests.append((hostname, port))
                    writer.write(bytes([5, 0, 0, 1, 127, 0, 0, 1, 0, 0]))
                await writer.drain()
                upstream_reader, upstream_writer = await asyncio.open_connection("127.0.0.1", self.port)
                async def relay(source, target):
                    while chunk := await source.read(8192):
                        target.write(chunk);await target.drain()
                    target.close()
                await asyncio.gather(relay(reader, upstream_writer), relay(upstream_reader, writer))
            except (ConnectionError, asyncio.IncompleteReadError):
                pass
            finally:
                if upstream_writer:
                    upstream_writer.close()
                writer.close()
                self.tasks.discard(asyncio.current_task())
        server = await asyncio.start_server(handler, "127.0.0.1", 0)
        self.servers.append(server)
        credentials = "name:proxy-pass@" if auth else ""
        return f"{scheme}://{credentials}127.0.0.1:{server.sockets[0].getsockname()[1]}"

    async def _fetch(self, proxy=""):
        from aiohttp_socks import ProxyConnector
        original = ProxyConnector.from_url
        port = ScriptServiceBalancePort(settings(advanced_base_url=f"https://balance.example.test:{self.port}",
                                                 advanced_proxy_url=proxy))
        # Trust only this ephemeral test certificate; all proxy and TLS bytes
        # still traverse real sockets. Production certificate policy is unchanged.
        def connector(url, **kwargs):
            return original(url, ssl=self.client_ssl, **kwargs)
        with patch("aiohttp_socks.ProxyConnector.from_url", side_effect=connector):
            return await port.fetch()

    async def test_http_connect_with_and_without_authentication(self):
        for auth in (False, True):
            proxy = await self._proxy("http", auth)
            observation = await self._fetch(proxy)
            self.assertEqual(observation.balance, Decimal("12.3"))
        self.assertEqual(len(self.requests), 2)
        head, body = self.requests[0]
        self.assertIn(b"POST /v1/user/balance HTTP/1.1", head)
        self.assertIn(b"Authorization: Bearer synthetic-token", head)
        self.assertEqual(json.loads(body), {"key": "synthetic-token"})

    async def test_socks5_with_and_without_authentication_and_remote_dns(self):
        for auth in (False, True):
            observation = await self._fetch(await self._proxy("socks5", auth))
            self.assertEqual(observation.unit, "CNY")
        self.assertEqual(self.proxy_requests, [("balance.example.test", self.port)] * 2)

    async def test_proxy_failure_does_not_fall_back_to_direct(self):
        proxy = await self._proxy("http", True)
        with self.assertRaises(BalanceQueryError) as raised:
            await self._fetch(proxy.replace("proxy-pass", "wrong-password"))
        self.assertNotIn("wrong-password", str(raised.exception))
        self.assertEqual(self.requests, [])

    async def test_http_error_redirect_invalid_json_and_oversized_response(self):
        proxy = await self._proxy("http")
        for status, body in ((302, b"redirect"), (401, b"secret-token"), (200, b"not json"),
                             (200, b"x" * 65537)):
            self.status, self.body = status, body
            with self.subTest(status=status, length=len(body)), self.assertRaises(BalanceQueryError):
                await self._fetch(proxy)
        self.assertEqual(len(self.requests), 4)

    async def test_deadline_cancels_a_slow_transport(self):
        proxy = await self._proxy("http")
        self.delay = 1
        with self.assertRaises(asyncio.TimeoutError):
            await asyncio.wait_for(self._fetch(proxy), .15)
