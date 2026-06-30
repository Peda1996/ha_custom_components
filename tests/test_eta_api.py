"""Tests for the ETA REST API helpers."""

from __future__ import annotations

from collections import OrderedDict
import importlib.util
from pathlib import Path
import sys
import unittest

_API_SPEC = importlib.util.spec_from_file_location(
    "eta_api",
    Path(__file__).resolve().parents[1] / "eta" / "api.py",
)
assert _API_SPEC is not None
eta_api = importlib.util.module_from_spec(_API_SPEC)
assert _API_SPEC.loader is not None
sys.modules[_API_SPEC.name] = eta_api
_API_SPEC.loader.exec_module(eta_api)

EtaApiClient = eta_api.EtaApiClient
EtaDiscovery = eta_api.EtaDiscovery
EtaEndpoint = eta_api.EtaEndpoint
EtaValue = eta_api.EtaValue
async_discover = eta_api.async_discover
parse_menu_xml = eta_api.parse_menu_xml
parse_value_xml = eta_api.parse_value_xml
parse_varinfo_xml = eta_api.parse_varinfo_xml
split_switch_values = eta_api.split_switch_values
is_default_enabled_name = eta_api.is_default_enabled_name
legacy_slug = eta_api.legacy_slug


MENU_XML = """<?xml version="1.0" encoding="utf-8"?>
<eta version="1.0" xmlns="http://www.eta.co.at/rest/v1">
 <menu uri="/user/menu">
  <fub uri="/40/10021" name="Kessel">
   <object uri="/40/10021/0/0/12182" name="Sonstiges">
    <object uri="/40/10021/0/0/12000" name="Kessel"/>
    <object uri="/40/10021/0/0/12080" name="Ein/Aus Taste"/>
    <object uri="/40/10021/0/0/12081" name="Ein/Aus Taste"/>
   </object>
  </fub>
 </menu>
</eta>
"""


class EtaApiTests(unittest.IsolatedAsyncioTestCase):
    """ETA API unit tests."""

    def test_parse_menu_uses_legacy_names_and_suffixes(self) -> None:
        items = parse_menu_xml(MENU_XML)

        self.assertEqual(items[0].name, "Kessel Sonstiges")
        self.assertEqual(items[1].name, "Kessel Sonstiges Ein/Aus Taste")
        self.assertEqual(items[2].name, "Kessel Sonstiges Ein/Aus Taste_2")

    def test_dashboard_entities_are_enabled_by_default(self) -> None:
        self.assertEqual(
            legacy_slug("PufferFlex Eingänge Fühler 1 oben"),
            "pufferflex_eingange_fuhler_1_oben",
        )
        self.assertTrue(is_default_enabled_name("PufferFlex Eingänge Fühler 1 oben"))
        self.assertTrue(is_default_enabled_name("FBH Heizkreis"))
        self.assertFalse(is_default_enabled_name("LagerUm. Sonstiges Position 8"))

    def test_parse_value_scales_numeric_unit(self) -> None:
        value = parse_value_xml(
            """<?xml version="1.0" encoding="utf-8"?>
            <eta xmlns="http://www.eta.co.at/rest/v1">
             <value unit="°C" uri="/user/var/40/10021/0/0/12001"
                    strValue="57" scaleFactor="10" decPlaces="0">570</value>
            </eta>"""
        )

        self.assertEqual(value.uri, "/40/10021/0/0/12001")
        self.assertEqual(value.native_value, 57)

    def test_parse_value_returns_text_state(self) -> None:
        value = parse_value_xml(
            """<?xml version="1.0" encoding="utf-8"?>
            <eta xmlns="http://www.eta.co.at/rest/v1">
             <value unit="" uri="/user/var/40/10021/0/0/12000"
                    strValue="Ausgeschaltet" scaleFactor="1">2000</value>
            </eta>"""
        )

        self.assertEqual(value.native_value, "Ausgeschaltet")

    def test_parse_varinfo_and_switch_values(self) -> None:
        info = parse_varinfo_xml(
            """<?xml version="1.0" encoding="utf-8"?>
            <eta xmlns="http://www.eta.co.at/rest/v1">
             <varInfo uri="/user/varinfo/40/10021/0/0/12112">
              <variable unit="" uri="40/10021/0/0/12112" isWritable="1"
                        scaleFactor="1" name="Entaschentaste" decPlaces="0">
               <type>TEXT</type>
               <validValues>
                <value strValue="Aus">1802</value>
                <value strValue="Ein">1803</value>
               </validValues>
              </variable>
             </varInfo>
            </eta>"""
        )

        self.assertTrue(info.is_writable)
        self.assertEqual(info.valid_values, OrderedDict([("Aus", 1802), ("Ein", 1803)]))
        self.assertEqual(split_switch_values(info.valid_values), (1802, 1803))

    async def test_discovery_keeps_sensor_and_marks_switchable_endpoint(self) -> None:
        client = _FakeClient()
        discovery = await async_discover(client, "11.12345", workers=4)

        names = [endpoint.name for endpoint in discovery.endpoints]
        self.assertIn("Kessel Sonstiges", names)
        switch_endpoint = next(
            endpoint
            for endpoint in discovery.endpoints
            if endpoint.uri == "/40/10021/0/0/12080"
        )
        self.assertEqual(switch_endpoint.name, "Kessel Sonstiges Ein/Aus Taste")
        self.assertEqual(switch_endpoint.valid_values, OrderedDict([("Aus", 1802), ("Ein", 1803)]))

    async def test_discovery_creates_switch_from_varinfo_when_value_read_fails(self) -> None:
        client = _MissingSwitchValueClient()
        discovery = await async_discover(client, "11.12345", workers=4)

        switch_endpoint = next(
            endpoint
            for endpoint in discovery.endpoints
            if endpoint.uri == "/40/10021/0/0/12080"
        )

        self.assertEqual(switch_endpoint.name, "Kessel Sonstiges Ein/Aus Taste")
        self.assertEqual(switch_endpoint.valid_values, OrderedDict([("Aus", 1802), ("Ein", 1803)]))
        self.assertTrue(switch_endpoint.enabled_default)

    def test_discovery_adds_legacy_keys_from_previous_cache_by_uri(self) -> None:
        current = EtaDiscovery(
            device_id="11.12345",
            endpoints=[
                EtaEndpoint(
                    uri="/120/10102/0/0/12080",
                    name="Heizk Sonstiges Ein/Aus Taste",
                )
            ],
        )
        previous = EtaDiscovery(
            device_id="11.12345",
            endpoints=[
                EtaEndpoint(
                    uri="/120/10102/0/0/12080",
                    name="Heizk Sonstiges Ein/Aus Taste_2",
                )
            ],
        )

        current.add_legacy_keys_from(previous)

        self.assertEqual(
            current.endpoints[0].legacy_keys,
            ("Heizk_Sonstiges_Ein/Aus_Taste_2",),
        )

    async def test_get_values_skips_one_broken_endpoint(self) -> None:
        session = _OneBrokenGetSession()
        client = EtaApiClient("192.0.2.10", 8080, session)

        values = await client.async_get_values(
            ["/40/10021/0/0/12000", "/40/10021/0/0/99999"],
            limit=2,
        )

        self.assertIn("/40/10021/0/0/12000", values)
        self.assertNotIn("/40/10021/0/0/99999", values)

    async def test_write_posts_to_user_var_endpoint(self) -> None:
        session = _FakeSession()
        client = EtaApiClient("192.0.2.10", 8080, session)

        await client.async_write_value("/40/10021/0/0/12112", 1803)

        self.assertEqual(session.calls[0]["method"], "POST")
        self.assertEqual(
            session.calls[0]["url"],
            "http://192.0.2.10:8080/user/var/40/10021/0/0/12112",
        )
        self.assertEqual(session.calls[0]["data"], {"value": "1803"})


class _FakeClient:
    async def async_get_menu(self) -> str:
        return MENU_XML

    async def async_get_values(self, uris: list[str], *, limit: int = 32):
        return {
            "/40/10021/0/0/12000": EtaValue(
                uri="/40/10021/0/0/12000",
                raw="2000",
                str_value="Ausgeschaltet",
                unit="",
            ),
            "/40/10021/0/0/12080": EtaValue(
                uri="/40/10021/0/0/12080",
                raw="1802",
                str_value="Aus",
                unit="",
            ),
            "/40/10021/0/0/12081": EtaValue(
                uri="/40/10021/0/0/12081",
                raw="0",
                str_value="",
                unit="",
            ),
        }

    async def async_get_varinfo(self, uri: str, *, attempts=None, timeout=None):
        return parse_varinfo_xml(
            """<?xml version="1.0" encoding="utf-8"?>
            <eta xmlns="http://www.eta.co.at/rest/v1">
             <varInfo uri="/user/varinfo/40/10021/0/0/12080">
              <variable unit="" uri="40/10021/0/0/12080" isWritable="1"
                        scaleFactor="1" name="Ein/Aus Taste" decPlaces="0">
               <type>TEXT</type>
               <validValues>
                <value strValue="Aus">1802</value>
                <value strValue="Ein">1803</value>
               </validValues>
              </variable>
             </varInfo>
            </eta>"""
        )


class _MissingSwitchValueClient(_FakeClient):
    async def async_get_values(self, uris: list[str], *, limit: int = 32):
        values = await super().async_get_values(uris, limit=limit)
        values.pop("/40/10021/0/0/12080", None)
        return values


class _FakeSession:
    def __init__(self) -> None:
        self.calls = []

    def request(self, method, url, data=None, timeout=None):
        self.calls.append(
            {
                "method": method,
                "url": url,
                "data": data,
                "timeout": timeout,
            }
        )
        return _FakeResponse()


class _FakeResponse:
    status = 200

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def text(self, encoding="utf-8"):
        return """<?xml version="1.0" encoding="utf-8"?>
        <eta xmlns="http://www.eta.co.at/rest/v1">
         <success uri="/user/var/40/10021/0/0/12112"/>
        </eta>"""


class _OneBrokenGetSession:
    def request(self, method, url, data=None, timeout=None):
        if url.endswith("/40/10021/0/0/99999"):
            raise RuntimeError("broken endpoint")
        return _ValueResponse()


class _ValueResponse:
    status = 200

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def text(self, encoding="utf-8"):
        return """<?xml version="1.0" encoding="utf-8"?>
        <eta xmlns="http://www.eta.co.at/rest/v1">
         <value unit="" uri="/user/var/40/10021/0/0/12000"
                strValue="Ausgeschaltet" scaleFactor="1">2000</value>
        </eta>"""


if __name__ == "__main__":
    unittest.main()
