"""ETA REST API client and XML parsing helpers."""

from __future__ import annotations

import asyncio
from collections import OrderedDict
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
import re
from typing import Any
from urllib.parse import quote
import unicodedata
import xml.etree.ElementTree as ET


VAR_PATH = "/user/var"
MENU_PATH = "/user/menu"
VARINFO_PATH = "/user/varinfo"
VARS_PATH = "/user/vars"

CACHE_SCHEMA_VERSION = 1

DEFAULT_ENABLED_SLUGS = {
    "fbh_eingange_vorlauf",
    "fbh_heizkreis",
    "fwm_eingange_primar_rucklauf",
    "heizk_eingange_vorlauf",
    "heizk_heizkreis",
    "kessel",
    "kessel_2",
    "kessel_zahlerstande_verbrauch_seit_aschebox_leeren",
    "pufferflex_eingange_fuhler_1_oben",
    "pufferflex_eingange_fuhler_2",
    "pufferflex_eingange_fuhler_3",
    "pufferflex_eingange_fuhler_4",
    "pufferflex_puffer_ladezustand",
    "solar_eingange_kollektor",
    "solar_solaranlage_kollektor",
    "solar_sonstiges_zustand",
}

CONTROL_NAME_WORDS = (
    "taste",
    "schalter",
    "aktivieren",
    "reset",
    "rücksetzen",
    "zurück",
    "vor",
    "ein/aus",
    "start",
    "abbrechen",
    "deaktivieren",
)

BINARY_VALUE_WORDS = {
    "aus",
    "ein",
    "off",
    "on",
    "nein",
    "ja",
    "no",
    "yes",
    "false",
    "true",
}

OFF_WORDS = {"aus", "off", "nein", "no", "false", "0", "deaktiviert", "gesperrt"}
ON_WORDS = {"ein", "on", "ja", "yes", "true", "1", "aktiv", "freigegeben"}


class EtaApiError(Exception):
    """Raised when the ETA API returns invalid data or a request fails."""


@dataclass(slots=True)
class EtaMenuItem:
    """Menu endpoint discovered from /user/menu."""

    uri: str
    name: str


@dataclass(slots=True)
class EtaValue:
    """Current ETA variable value."""

    uri: str
    raw: str | None
    str_value: str
    unit: str
    scale_factor: int = 1
    dec_places: int = 0
    adv_text_offset: int = 0

    @property
    def native_value(self) -> int | float | str | None:
        """Return the value in the shape Home Assistant expects."""
        if self.raw is None or self.raw == "":
            return self.str_value or None

        raw_number = _parse_number(self.raw)
        if self.unit and raw_number is not None:
            scaled = raw_number / max(self.scale_factor, 1)
            return _compact_number(scaled)

        if self.str_value:
            str_number = _parse_number(self.str_value)
            if str_number is not None and self.str_value == self.raw:
                return _compact_number(str_number)
            return self.str_value

        if raw_number is not None:
            scaled = raw_number / max(self.scale_factor, 1)
            return _compact_number(scaled)

        return self.raw


@dataclass(slots=True)
class EtaVariableInfo:
    """Metadata returned by /user/varinfo."""

    uri: str
    value_type: str | None = None
    unit: str = ""
    name: str | None = None
    full_name: str | None = None
    is_writable: bool = False
    scale_factor: int = 1
    dec_places: int = 0
    valid_values: OrderedDict[str, int] = field(default_factory=OrderedDict)


@dataclass(slots=True)
class EtaEndpoint:
    """Discovered Home Assistant entity endpoint."""

    uri: str
    name: str
    unit: str = ""
    scale_factor: int = 1
    dec_places: int = 0
    value_type: str | None = None
    kind: str = "sensor"
    valid_values: OrderedDict[str, int] = field(default_factory=OrderedDict)
    enabled_default: bool = False

    @property
    def unique_key(self) -> str:
        """Legacy-compatible unique key based on the generated entity name."""
        return self.name.replace(" ", "_")

    def to_dict(self) -> dict[str, Any]:
        """Serialize endpoint for Home Assistant storage."""
        data = asdict(self)
        data["valid_values"] = dict(self.valid_values)
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "EtaEndpoint":
        """Deserialize endpoint from Home Assistant storage."""
        valid_values = OrderedDict(
            (str(label), int(value))
            for label, value in data.get("valid_values", {}).items()
        )
        name = data["name"]
        return cls(
            uri=normalize_uri(data["uri"]),
            name=name,
            unit=data.get("unit", ""),
            scale_factor=int(data.get("scale_factor", 1) or 1),
            dec_places=int(data.get("dec_places", 0) or 0),
            value_type=data.get("value_type"),
            kind=data.get("kind", "sensor"),
            valid_values=valid_values,
            enabled_default=bool(data.get("enabled_default", False))
            or is_default_enabled_name(name)
            or bool(valid_values),
        )


@dataclass(slots=True)
class EtaDiscovery:
    """Discovery result stored in Home Assistant's storage."""

    device_id: str
    endpoints: list[EtaEndpoint]
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())

    def to_cache(self, host: str, port: int) -> dict[str, Any]:
        """Serialize discovery result."""
        return {
            "version": CACHE_SCHEMA_VERSION,
            "host": host,
            "port": port,
            "device_id": self.device_id,
            "created_at": self.created_at,
            "endpoints": [endpoint.to_dict() for endpoint in self.endpoints],
        }

    @classmethod
    def from_cache(
        cls,
        cache: dict[str, Any] | None,
        host: str,
        port: int,
        device_id: str,
    ) -> "EtaDiscovery | None":
        """Load a cache entry if it matches the current ETA device."""
        if not cache:
            return None
        if cache.get("version") != CACHE_SCHEMA_VERSION:
            return None
        if cache.get("host") != host or int(cache.get("port", 0) or 0) != port:
            return None
        if cache.get("device_id") != device_id:
            return None

        endpoints = [
            EtaEndpoint.from_dict(endpoint)
            for endpoint in cache.get("endpoints", [])
            if endpoint.get("uri") and endpoint.get("name")
        ]
        if not endpoints:
            return None
        return cls(
            device_id=device_id,
            endpoints=endpoints,
            created_at=cache.get("created_at") or datetime.now(UTC).isoformat(),
        )


class EtaApiClient:
    """Small async client for the ETA REST API."""

    def __init__(
        self,
        host: str,
        port: int,
        session: Any,
        *,
        request_timeout: int = 10,
    ) -> None:
        self.host = host
        self.port = port
        self.session = session
        self.request_timeout = request_timeout
        self.base_url = f"http://{host}:{port}"

    async def async_get_menu(self) -> str:
        """Return the raw menu XML."""
        return await self._request("GET", MENU_PATH)

    async def async_get_value(self, uri: str) -> EtaValue:
        """Read one ETA variable."""
        xml = await self._request("GET", f"{VAR_PATH}{normalize_uri(uri)}")
        return parse_value_xml(xml)

    async def async_get_values(
        self,
        uris: list[str],
        *,
        limit: int = 32,
    ) -> dict[str, EtaValue]:
        """Read many ETA variables concurrently."""
        semaphore = asyncio.Semaphore(limit)
        values: dict[str, EtaValue] = {}

        async def _read(uri: str) -> None:
            async with semaphore:
                values[normalize_uri(uri)] = await self.async_get_value(uri)

        await asyncio.gather(*(_read(uri) for uri in sorted(set(uris))))
        return values

    async def async_get_varinfo(self, uri: str) -> EtaVariableInfo:
        """Read ETA variable metadata."""
        xml = await self._request(
            "GET",
            f"{VARINFO_PATH}{normalize_uri(uri)}",
            timeout=min(self.request_timeout, 8),
        )
        return parse_varinfo_xml(xml)

    async def async_write_value(self, uri: str, value: int | str) -> None:
        """Write one ETA variable via POST /user/var<uri>."""
        xml = await self._request(
            "POST",
            f"{VAR_PATH}{normalize_uri(uri)}",
            data={"value": str(value)},
        )
        if not has_success(xml):
            raise EtaApiError(f"ETA did not confirm write for {uri}")

    async def async_ensure_variable_set(
        self,
        name: str,
        uris: list[str],
        *,
        limit: int = 16,
    ) -> None:
        """Create and populate a volatile ETA variable set."""
        safe_name = normalize_varset_name(name)
        await self._request(
            "PUT",
            f"{VARS_PATH}/{safe_name}",
            expected_statuses={200, 201, 204, 409},
        )

        semaphore = asyncio.Semaphore(limit)

        async def _add(uri: str) -> None:
            async with semaphore:
                await self._request(
                    "PUT",
                    f"{VARS_PATH}/{safe_name}{normalize_uri(uri)}",
                    expected_statuses={200, 201, 204, 409},
                )

        await asyncio.gather(*(_add(uri) for uri in sorted(set(uris))))

    async def async_get_variable_set(self, name: str) -> dict[str, EtaValue]:
        """Read all values from an ETA variable set."""
        xml = await self._request("GET", f"{VARS_PATH}/{normalize_varset_name(name)}")
        return parse_variable_set_xml(xml)

    async def async_get_device_id(self) -> str:
        """Build a stable device id from the ETA serial number variables."""
        serial_1, serial_2 = await asyncio.gather(
            self.async_get_value("/40/10021/0/0/12489"),
            self.async_get_value("/40/10021/0/0/12490"),
        )
        first = str(serial_1.native_value or serial_1.str_value or "").strip()
        second = str(serial_2.native_value or serial_2.str_value or "").strip()
        if first or second:
            return f"{first}.{second}".strip(".")
        return f"{self.host}:{self.port}"

    async def _request(
        self,
        method: str,
        path: str,
        *,
        data: dict[str, str] | None = None,
        expected_statuses: set[int] | None = None,
        timeout: int | None = None,
    ) -> str:
        """Make one HTTP request and return response text."""
        expected = expected_statuses or {200}
        url = f"{self.base_url}{path}"
        try:
            async with self.session.request(
                method,
                url,
                data=data,
                timeout=timeout or self.request_timeout,
            ) as response:
                text = await response.text(encoding="utf-8")
                if response.status not in expected:
                    raise EtaApiError(
                        f"ETA {method} {path} returned HTTP {response.status}: {text}"
                    )
                return text
        except EtaApiError:
            raise
        except Exception as err:  # noqa: BLE001 - HA logs this as a setup/update error.
            raise EtaApiError(f"ETA {method} {path} failed: {err}") from err


async def async_discover(
    client: EtaApiClient,
    device_id: str,
    *,
    workers: int = 32,
    full_switch_discovery: bool = False,
) -> EtaDiscovery:
    """Discover ETA endpoints quickly and enrich likely switches with varinfo."""
    menu_xml = await client.async_get_menu()
    menu_items = parse_menu_xml(menu_xml)
    values = await client.async_get_values(
        [item.uri for item in menu_items],
        limit=max(1, workers),
    )

    endpoints: list[EtaEndpoint] = []
    switch_candidates: list[str] = []
    endpoint_by_uri: dict[str, list[EtaEndpoint]] = {}
    for item in menu_items:
        value = values.get(item.uri)
        if value is None or not is_entity_value(value):
            continue

        endpoint = EtaEndpoint(
            uri=item.uri,
            name=item.name,
            unit=value.unit,
            scale_factor=value.scale_factor,
            dec_places=value.dec_places,
            enabled_default=is_default_enabled_name(item.name),
        )
        endpoints.append(endpoint)
        endpoint_by_uri.setdefault(item.uri, []).append(endpoint)

        if value.unit == "" and value.str_value:
            if full_switch_discovery or is_likely_control(item.name, value.str_value):
                switch_candidates.append(item.uri)

    varinfos = await async_probe_varinfos(
        client,
        switch_candidates,
        workers=max(1, workers),
    )
    for uri, info in varinfos.items():
        uri_endpoints = endpoint_by_uri.get(uri)
        if uri_endpoints is None:
            continue
        for endpoint in uri_endpoints:
            endpoint.value_type = info.value_type
            endpoint.unit = endpoint.unit or info.unit
            endpoint.scale_factor = info.scale_factor
            endpoint.dec_places = info.dec_places
            if info.is_writable and len(info.valid_values) == 2:
                endpoint.kind = "switch"
                endpoint.valid_values = info.valid_values
                endpoint.enabled_default = True

    return EtaDiscovery(
        device_id=device_id,
        endpoints=endpoints,
    )


async def async_probe_varinfos(
    client: EtaApiClient,
    uris: list[str],
    *,
    workers: int = 16,
) -> dict[str, EtaVariableInfo]:
    """Probe varinfo endpoints without failing discovery when ETA times out."""
    semaphore = asyncio.Semaphore(workers)
    results: dict[str, EtaVariableInfo] = {}

    async def _read(uri: str) -> None:
        async with semaphore:
            try:
                results[normalize_uri(uri)] = await client.async_get_varinfo(uri)
            except Exception:
                return

    await asyncio.gather(*(_read(uri) for uri in sorted(set(uris))))
    return results


def parse_menu_xml(xml: str) -> list[EtaMenuItem]:
    """Parse /user/menu and return legacy-compatible variable endpoints."""
    root = ET.fromstring(xml)
    raw_items: list[EtaMenuItem] = []

    def _walk(element: ET.Element, previous: str = "") -> None:
        for child in element:
            child_name = clean_name(child.attrib.get("name", ""))
            child_previous = f"{previous} {child_name}".strip()
            _walk(child, child_previous)

            if local_name(child.tag) != "object" or not child.attrib.get("uri"):
                continue

            full_name = f"{previous} {child_name}".strip()
            raw_items.append(
                EtaMenuItem(
                    uri=normalize_uri(child.attrib["uri"]),
                    name=remove_duplicate_words(full_name),
                )
            )

    _walk(root)
    return add_legacy_duplicate_suffixes(raw_items)


def parse_value_xml(xml: str) -> EtaValue:
    """Parse one /user/var response."""
    root = ET.fromstring(xml)
    value = first_child(root, "value")
    if value is None:
        raise EtaApiError("ETA response did not contain a value element")
    return value_from_element(value, default_base_path=VAR_PATH)


def parse_variable_set_xml(xml: str) -> dict[str, EtaValue]:
    """Parse /user/vars/{name} response."""
    root = ET.fromstring(xml)
    values: dict[str, EtaValue] = {}
    for variable in root.iter():
        if local_name(variable.tag) != "variable":
            continue
        value = value_from_element(variable)
        values[value.uri] = value
    return values


def parse_varinfo_xml(xml: str) -> EtaVariableInfo:
    """Parse one /user/varinfo response."""
    root = ET.fromstring(xml)
    variable = first_child(root, "variable")
    if variable is None:
        raise EtaApiError("ETA response did not contain a variable element")

    value_type = None
    valid_values: OrderedDict[str, int] = OrderedDict()
    for child in variable:
        tag = local_name(child.tag)
        if tag == "type":
            value_type = child.text
        elif tag == "validValues":
            for value in child:
                if local_name(value.tag) != "value" or value.text is None:
                    continue
                label = value.attrib.get("strValue") or value.text
                number = _parse_number(value.text)
                if number is not None:
                    valid_values[label] = int(number)

    return EtaVariableInfo(
        uri=normalize_uri(variable.attrib.get("uri", "")),
        value_type=value_type,
        unit=variable.attrib.get("unit", ""),
        name=clean_name(variable.attrib.get("name", "")) or None,
        full_name=clean_name(variable.attrib.get("fullName", "")) or None,
        is_writable=variable.attrib.get("isWritable") == "1",
        scale_factor=parse_int(variable.attrib.get("scaleFactor"), 1),
        dec_places=parse_int(variable.attrib.get("decPlaces"), 0),
        valid_values=valid_values,
    )


def value_from_element(
    element: ET.Element,
    *,
    default_base_path: str | None = None,
) -> EtaValue:
    """Build EtaValue from a <value> or <variable> XML element."""
    uri = element.attrib.get("uri", "")
    if default_base_path and uri.startswith(default_base_path):
        uri = uri[len(default_base_path) :]
    return EtaValue(
        uri=normalize_uri(uri),
        raw=element.text,
        str_value=element.attrib.get("strValue", ""),
        unit=element.attrib.get("unit", ""),
        scale_factor=max(parse_int(element.attrib.get("scaleFactor"), 1), 1),
        dec_places=parse_int(element.attrib.get("decPlaces"), 0),
        adv_text_offset=parse_int(element.attrib.get("advTextOffset"), 0),
    )


def has_success(xml: str) -> bool:
    """Return true if an ETA write response contains <success>."""
    root = ET.fromstring(xml)
    return first_child(root, "success") is not None


def split_switch_values(valid_values: OrderedDict[str, int]) -> tuple[int, int]:
    """Return off/on values for a two-state ETA text variable."""
    items = list(valid_values.items())
    if len(items) != 2:
        raise EtaApiError("Switch endpoint does not have exactly two values")

    off_value: int | None = None
    on_value: int | None = None
    for label, value in items:
        label_key = label.strip().lower()
        if label_key in OFF_WORDS:
            off_value = value
        elif label_key in ON_WORDS:
            on_value = value

    if off_value is not None and on_value is not None:
        return off_value, on_value
    return items[0][1], items[1][1]


def is_switch_on(value: EtaValue | None, valid_values: OrderedDict[str, int]) -> bool | None:
    """Evaluate the state of a two-state ETA text variable."""
    if value is None:
        return None
    off_value, on_value = split_switch_values(valid_values)
    raw_number = _parse_number(value.raw or "")
    if raw_number is not None:
        if int(raw_number) == on_value:
            return True
        if int(raw_number) == off_value:
            return False

    label = value.str_value.strip().lower()
    labels = {key.strip().lower(): val for key, val in valid_values.items()}
    if label in labels:
        return labels[label] == on_value
    return None


def is_entity_value(value: EtaValue) -> bool:
    """Filter out menu grouping nodes that do not represent useful values."""
    return bool(value.unit or value.str_value or (value.raw not in (None, "")))


def is_likely_control(name: str, str_value: str) -> bool:
    """Return true when varinfo probing is likely to find a writable control."""
    lower_name = name.lower()
    if str_value.strip().lower() in BINARY_VALUE_WORDS:
        return True
    return any(word in lower_name for word in CONTROL_NAME_WORDS)


def is_default_enabled_name(name: str) -> bool:
    """Return true for the legacy dashboard's high-value ETA sensors."""
    return legacy_slug(name) in DEFAULT_ENABLED_SLUGS


def legacy_slug(value: str) -> str:
    """Approximate Home Assistant's legacy entity-id slug for ETA names."""
    normalized = unicodedata.normalize("NFKD", value)
    ascii_value = normalized.encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-zA-Z0-9_]+", "_", ascii_value.lower())
    return re.sub(r"_+", "_", slug).strip("_")


def normalize_uri(uri: str) -> str:
    """Normalize ETA variable URI to /node/fub/fkt/io/var."""
    if not uri:
        return "/"
    value = uri.strip()
    for prefix in (VAR_PATH, VARINFO_PATH):
        if value.startswith(prefix):
            value = value[len(prefix) :]
    if not value.startswith("/"):
        value = f"/{value}"
    return value


def normalize_varset_name(name: str) -> str:
    """Normalize ETA varset names to the API's alphanumeric constraint."""
    normalized = re.sub(r"[^a-zA-Z0-9]", "", name)
    return normalized or "haeta"


def clean_name(name: str) -> str:
    """Collapse whitespace in ETA names."""
    return " ".join(name.split())


def remove_duplicate_words(name: str) -> str:
    """Match the legacy integration's generated friendly names."""
    words = name.split()
    return " ".join(OrderedDict.fromkeys(words))


def add_legacy_duplicate_suffixes(items: list[EtaMenuItem]) -> list[EtaMenuItem]:
    """Add _2, _3, ... suffixes the same way the legacy sensor platform did."""
    names: set[str] = set()
    result: list[EtaMenuItem] = []
    for item in items:
        name = item.name
        entity_name = name
        count = 2
        while entity_name in names:
            entity_name = f"{name}_{count}"
            count += 1
        names.add(entity_name)
        result.append(EtaMenuItem(uri=item.uri, name=entity_name))
    return result


def local_name(tag: str) -> str:
    """Return an XML tag name without namespace."""
    return tag.rsplit("}", 1)[-1]


def first_child(root: ET.Element, tag_name: str) -> ET.Element | None:
    """Find the first XML element by local name."""
    for element in root.iter():
        if local_name(element.tag) == tag_name:
            return element
    return None


def parse_int(value: str | None, default: int) -> int:
    """Parse an integer with a fallback."""
    if value in (None, ""):
        return default
    try:
        return int(float(value.replace(",", ".")))
    except (TypeError, ValueError):
        return default


def _parse_number(value: str) -> float | None:
    """Parse ETA numeric strings, accepting comma decimals."""
    try:
        return float(value.strip().replace(",", "."))
    except (AttributeError, ValueError):
        return None


def _compact_number(value: float) -> int | float:
    """Return ints without a trailing .0."""
    if value.is_integer():
        return int(value)
    return value


def quote_uri(uri: str) -> str:
    """Quote an ETA URI for logging or future URL composition."""
    return quote(normalize_uri(uri), safe="/")
