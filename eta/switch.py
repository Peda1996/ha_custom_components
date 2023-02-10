"""
Platform for ETA sensor integration in Home Assistant

Help Links:
 Entity Source: https://github.com/home-assistant/core/blob/dev/homeassistant/helpers/entity.py
 SensorEntity derives from Entity https://github.com/home-assistant/core/blob/dev/homeassistant/components/sensor/__init__.py


author hubtub2

"""

from __future__ import annotations

import asyncio
import logging
import xml.etree.ElementTree as ET
from typing import Callable, Any

import requests
import voluptuous as vol
import xmltodict
from homeassistant.components.switch import SwitchEntity

_LOGGER = logging.getLogger(__name__)

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
    PLATFORM_SCHEMA,
    ENTITY_ID_FORMAT
)

from homeassistant.const import FREQUENCY_HERTZ, PRESSURE_BAR, ELECTRIC_POTENTIAL_VOLT, TIME_SECONDS, POWER_WATT, \
    VOLUME_LITERS, ELECTRIC_POTENTIAL_MILLIVOLT, IRRADIATION_WATTS_PER_SQUARE_METER, ELECTRIC_CURRENT_MILLIAMPERE, \
    PRESSURE_PA, PERCENTAGE, AREA_SQUARE_METERS, CONF_PREFIX

from homeassistant.core import HomeAssistant, callback, _T
from homeassistant.helpers.typing import ConfigType, DiscoveryInfoType
import homeassistant.helpers.config_validation as cv

from homeassistant.helpers.entity import generate_entity_id

from homeassistant.const import (CONF_HOST, CONF_PORT, TEMP_CELSIUS, POWER_KILO_WATT,
                                 MASS_KILOGRAMS)

PLATFORM_SCHEMA = PLATFORM_SCHEMA.extend({
    vol.Required(CONF_HOST): cv.string,
    vol.Required(CONF_PORT): cv.positive_int,
    vol.Required(CONF_PREFIX): cv.string,
})


def get_base_url(
        config: ConfigType,
        context: str = ""
) -> str:
    return "".join(["http://", config.get(CONF_HOST), ":", str(config.get(CONF_PORT)), context])


VAR_PATH = "/user/var"
MENU_PATH = "/user/menu"
VARINFO_PATH = "/user/varinfo"


class Setup:

    def __init__(self, config, hass):
        # TODO add cache, using hass.data !!

        self.config = config
        self.hass = hass

        self.sensors = {}
        self.switches = {}

        self.allowed_types = ["DEFAULT", "TEXT"]
        self.serial1 = None
        self.serial2 = None

    async def init(self):
        # read serial
        serial1 = await self.hass.async_add_executor_job(
            requests.get, get_base_url(self.config, VAR_PATH) + "/40/10021/0/0/12489"
        )
        serial2 = await self.hass.async_add_executor_job(
            requests.get, get_base_url(self.config, VAR_PATH) + "/40/10021/0/0/12490"
        )

        # Parse Unique ID
        serial1 = xmltodict.parse(serial1.text)
        self.serial1 = serial1['eta']['value']['@strValue']
        serial2 = xmltodict.parse(serial2.text)
        self.serial2 = serial2['eta']['value']['@strValue']

        # init switches and sensors
        res = await self.hass.async_add_executor_job(
            requests.get, get_base_url(self.config, MENU_PATH)
        )
        res = res.content.decode("utf8")
        await self._find_useful_entities(ET.fromstring(res))

    async def get_sensors(self) -> list:
        return list(self.sensors.values())

    async def get_switches(self) -> list:
        return list(self.switches.values())

    @staticmethod
    def _remove_duplicates_from_name(name):
        words = name.split(" ")
        return " ".join(sorted(set(words), key=words.index))

    async def _get_varinfo(self, uri):
        # TODO --> make things writeable, as we now might find out the possible varinfo states!
        # val = requests.get(get_base_url(self.config, VARINFO_PATH) + uri)

        val = await self.hass.async_add_executor_job(requests.get, get_base_url(self.config, VARINFO_PATH) + uri)
        info = xmltodict.parse(val.text)['eta']
        # print(info)
        if 'varInfo' in info:
            # type, unit, what_states_are_possible (only if writeable)
            write_vals = None
            try:
                if info['varInfo']['variable']['@isWritable'] == "1" and \
                        info['varInfo']['variable']["validValues"] is not None and \
                        "value" in info['varInfo']['variable']["validValues"]:
                    vals = info['varInfo']['variable']["validValues"]["value"]
                    write_vals = dict(zip(
                        [k['@strValue'] for k in vals],
                        [int(v['#text']) for v in vals],
                    ))
            except:
                # TODO better error handling, on startup..
                pass
            return info['varInfo']['variable']['type'], info['varInfo']['variable'].get('@unit', ''), write_vals
        return None, None, None

    async def _create_entities_list(self, root, raw_entities, prev=""):
        for child in root:
            await self._create_entities_list(child, raw_entities, prev=prev + " " + child.attrib.get("name", ""))
            raw_entities.append((child, prev))

    async def _find_useful_entities(self, root):
        raw_entities = []
        await self._create_entities_list(root, raw_entities)

        for child, prev in raw_entities:

            new_name = prev + " " + child.attrib.get("name", "")
            new_name = self._remove_duplicates_from_name(new_name)
            entity_name = new_name

            count = 2
            while entity_name in self.sensors:
                entity_name = new_name + "_" + str(count)
                count += 1
            new_name = entity_name

            # TODO async
            measure = await self._get_varinfo(child.attrib['uri'])
            if measure:
                _type, unit, write_vals = measure
                uri = child.attrib['uri']
                if _type in self.allowed_types:
                    unique_id = self.config.get(
                        CONF_PREFIX) + "_" + self.serial1 + "." + self.serial2 + "." + new_name.replace(" ", "_")

                    # only allow where Yes and No, therefore 1802 and 1803 is no and yes for switch
                    if write_vals and len(write_vals) == 2 and 1802 in list(write_vals.values()):
                        switch_name = new_name + " >"
                        unique_id_switch = unique_id + "_switch"
                        self.switches[switch_name] = EtaSwitch(self.config, self.hass, switch_name, uri, write_vals,
                                                               unique_id_switch)
                    # self.sensors[new_name] = EtaSensor(self.config, self.hass, new_name, uri,
                    #                                   unique_id, unit=unit)
                    # TODO check if writeable
                    # TODO check if it only has two states and therefore is a switch


def unit_mapper(unit):
    return {
        "Hz": (FREQUENCY_HERTZ, SensorDeviceClass.FREQUENCY),
        "kW": (POWER_KILO_WATT, SensorDeviceClass.POWER),
        "°C": (TEMP_CELSIUS, SensorDeviceClass.TEMPERATURE),
        "kg": (MASS_KILOGRAMS, SensorDeviceClass.WEIGHT),
        "bar": (PRESSURE_BAR, SensorDeviceClass.PRESSURE),
        "A": (ELECTRIC_CURRENT_MILLIAMPERE, SensorDeviceClass.CURRENT),
        "s": (TIME_SECONDS, SensorDeviceClass.TIMESTAMP),
        "V": (ELECTRIC_POTENTIAL_VOLT, SensorDeviceClass.VOLTAGE),
        "m²": (AREA_SQUARE_METERS, SensorDeviceClass.DATA_SIZE),
        "%": (PERCENTAGE, SensorDeviceClass.POWER_FACTOR),
        "W": (POWER_WATT, SensorDeviceClass.ENERGY),
        "l": (VOLUME_LITERS, SensorDeviceClass.WATER),
        "mV": (ELECTRIC_POTENTIAL_MILLIVOLT, SensorDeviceClass.VOLTAGE),
        "W/m²": (IRRADIATION_WATTS_PER_SQUARE_METER, SensorDeviceClass.POWER),
        "Pa": (PRESSURE_PA, SensorDeviceClass.PRESSURE),
        "str": (None, SensorDeviceClass.REACTIVE_POWER)
    }.get(unit, (None, None))


async def get_measure(config, hass, uri):
    # TODO use xmltodict instead
    val = await hass.async_add_executor_job(requests.get, get_base_url(config, VAR_PATH) + uri)
    val = val.content.decode("utf8")
    root = ET.fromstring(val)[0]

    # div = pow(0.1, (int(root.attrib.get("decPlaces", "0"))))
    scale = (int(root.attrib.get("scaleFactor", "1")))

    if root.attrib.get('unit', '') != "":
        return float(str(root.text)) / scale, root.attrib.get('unit', '')

    else:
        # check_bool_mapper
        return root.attrib.get('strValue', ''), 'str'


class EtaSwitch(SwitchEntity):

    def __init__(self, config, hass, name, uri, states, unique_id):
        """
        Initialize EtaSwitch.

        To show all values: http://192.168.178.75:8080/user/menu

        There are:
          - entity_id - used to reference id, english, e.g. "eta_outside_temperature"
          - name - Friendly name, e.g "Außentemperatur" in local language
          - unique_id - globally unique id of sensor, e.g. "eta_11.123488_outside_temp", based on serial number
        """
        _LOGGER.info(f"ETA Integration - Init Switch: {name}")
        self._attr_entity_registry_enabled_default = False
        self._is_on = False  # start with off state
        self._attr_unique_id = unique_id

        self.uri = uri
        self.config = config
        self.hass = hass

        _id = name.lower().replace(' ', '_')
        self._attr_name = name  # friendly name - local language
        self.entity_id = generate_entity_id(ENTITY_ID_FORMAT, config.get(CONF_PREFIX) + "_" + _id, hass=hass)

        # {'Aus': 1802, 'Ein': 1803}
        self.states = states
        self.is_available = False

    @property
    def available(self):
        return self.is_available

    @property
    def unique_id(self):
        return self._attr_unique_id

    @staticmethod
    def post_request_wrapped(url, headers, value):
        _LOGGER.info(f"ETA Post: {url}, headers:{headers}, value:{value}")
        val = requests.post(
            url,
            headers=headers,
            data={"value": value}
        )
        return val

    async def async_turn_on(self, **kwargs):
        """Turn the entity on."""

        headers = {'Content-type': 'application/x-www-form-urlencoded'}

        val = await self.hass.async_add_executor_job(self.post_request_wrapped,
                                                     get_base_url(self.config, VAR_PATH) + self.uri,
                                                     headers,
                                                     list(self.states.values())[1])

        val = val.content.decode("utf8")

        if "success" in xmltodict.parse(val)["eta"]:
            self._is_on = True
            self.is_available = True
        else:
            _LOGGER.error(f"Operation failed: unable to turn switch {self._attr_name} on!")

    async def async_turn_off(self, **kwargs):
        """Turn the entity off."""
        headers = {'Content-type': 'application/x-www-form-urlencoded'}
        val = await self.hass.async_add_executor_job(self.post_request_wrapped,
                                                     get_base_url(self.config, VAR_PATH) + self.uri,
                                                     headers,
                                                     0)
        val = val.content.decode("utf8")

        if "success" in xmltodict.parse(val)["eta"]:
            self._is_on = False
            self.is_available = True
        else:
            _LOGGER.error(f"Operation failed: unable to turn switch {self._attr_name} off!")

    @property
    def is_on(self):
        """If the switch is currently on or off."""
        return self._is_on

    async def async_update(self) -> None:
        """Fetch new state data for the sensor.
        This is the only method that should fetch new data for Home Assistant.
        activate first: https://www.meineta.at/javax.faces.resource/downloads/ETA-RESTful-v1.2.pdf.xhtml?ln=default&v=0
        """
        # state_class
        value, unit = await get_measure(self.config, self.hass, self.uri)
        if not value:
            return

        if unit == "str":
            self._attr_state = value

        self._is_on = (list(self.states.keys()).index(value) == 1)
        self.is_available = True

    @property
    def should_poll(self):
        return True


async def async_setup_platform(
        hass: HomeAssistant,
        config: ConfigType,
        async_add_entities,
        discovery_info: DiscoveryInfoType | None = None
) -> None:
    """Set up the sensor platform."""

    _LOGGER.info("ETA Integration - setup platform")
    s = Setup(config, hass)
    await s.init()
    entires = await s.get_switches()
    async_add_entities(entires)
    _LOGGER.info("ETA Integration - setup complete")


class EtaSensor(SensorEntity):
    """Representation of a Sensor."""

    # _attr_device_class = SensorDeviceClass.TEMPERATURE
    # _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, config, hass, name, uri, unique_id, unit=None,
                 state_class=SensorStateClass.MEASUREMENT,
                 factor=1.0):
        """
        Initialize sensor.

        To show all values: http://192.168.178.75:8080/user/menu

        There are:
          - entity_id - used to reference id, english, e.g. "eta_outside_temperature"
          - name - Friendly name, e.g "Außentemperatur" in local language
          - unique_id - globally unique id of sensor, e.g. "eta_11.123488_outside_temp", based on serial number

        """
        _LOGGER.info(f"ETA Integration - Init Sensor: {name}")

        # disable sensor by default
        self._attr_entity_registry_enabled_default = False

        _id = name.lower().replace(' ', '_')
        self._attr_name = name  # friendly name - local language
        self.entity_id = generate_entity_id(ENTITY_ID_FORMAT, config.get(CONF_PREFIX) + "_" + _id, hass=hass)

        hassio_unit, device_class = unit_mapper(unit)

        if unit:
            self._attr_state_class = state_class

        if device_class is not None:
            self._attr_device_class = device_class

        if hassio_unit is not None:
            self._attr_native_unit_of_measurement = hassio_unit

        self.uri = uri
        self.factor = factor
        self.config = config
        self.host = config.get(CONF_HOST)
        self.port = config.get(CONF_PORT)

        self._attr_unique_id = unique_id

    async def async_update(self) -> None:
        """Fetch new state data for the sensor.
        This is the only method that should fetch new data for Home Assistant.
        TODO: readme: activate first: https://www.meineta.at/javax.faces.resource/downloads/ETA-RESTful-v1.2.pdf.xhtml?ln=default&v=0
        """
        # state_class
        value, unit = await get_measure(self.config, self.hass, self.uri)
        if not value:
            return

        if unit == "str":
            self._attr_state = value

        self._attr_native_value = value
