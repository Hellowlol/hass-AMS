"""AMS hub platform."""
import asyncio
import logging
import threading
import time

from homeassistant.helpers import discovery
from homeassistant import config_entries
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.const import EVENT_HOMEASSISTANT_STOP
import voluptuous as vol


import serial
import serial_asyncio

from .const import (
    DOMAIN,
    DOMAIN_DATA,
    AMS_DEVICES,
    SIGNAL_UPDATE_AMS,
    SIGNAL_NEW_AMS_SENSOR,
    CONF_SERIAL_PORT,
    CONF_BAUDRATE,
    CONF_PARITY,
    CONF_SLEEP,
    CONF_TIMEOUT,
    FRAME_FLAG,
    DATA_FLAG,
    DOMAIN_SCH,
    STARTUP,
)

from . import han_decode


_LOGGER = logging.getLogger(__name__)
CONFIG_SCHEMA = vol.Schema({DOMAIN: vol.Schema(DOMAIN_SCH)}, extra=vol.ALLOW_EXTRA)


async def async_setup(hass, config) -> bool:
    """Setup the integration using using yaml."""
    _LOGGER.debug("start of async_setup")

    if config.get(DOMAIN) is None:
        # So there is no ams stuff in the configure,yaml so we try using the config entry.
        _LOGGER.debug("This integration is set up using config flow.")
        return True

    _LOGGER.debug(STARTUP)

    hub = AmsHub(hass, config[DOMAIN])
    hass.data[DOMAIN_DATA] = {}
    hass.data[DOMAIN_DATA]["client"] = hub

    # Add callback to cancel the task
    # when HA is stopped.
    if config[DOMAIN]["mode"] == "aio":
        hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, hub.stop_serial_read_aio())

    # We just pass junk to this. the the entire point os to enable the callback.
    hass.async_create_task(
        discovery.async_load_platform(hass, "sensor", DOMAIN, {}, config)
    )

    # hass.async_create_task(
    #     hass.config_entries.flow.async_init(
    #         DOMAIN, context={"source": config_entries.SOURCE_IMPORT}, data=config[DOMAIN]
    #     )
    # )
    _LOGGER.debug("end of async_setup")
    return True


async def async_setup_entry(hass: HomeAssistant, config_entry: ConfigEntry):
    """Set up AMS as config entry."""
    _LOGGER.debug("Inside async_setup_entry")
    conf = hass.data.get(DOMAIN)
    if config_entry.source == config_entries.SOURCE_IMPORT:
        config_entry.data = config_entry
        _LOGGER.debug("was source import, conf %s", conf)
        if conf is None:
            hass.async_create_task(
                hass.config_entries.async_remove(config_entry.entry_id)
            )
        return False

    _LOGGER.info(STARTUP)

    # Check if the client is already setup or one as this can
    # be called using the callback.
    if not hass.data.get(DOMAIN_DATA, {}).get("client"):
        hass.data[DOMAIN_DATA] = {}
        hub = AmsHub(hass, config_entry.data)
        hass.data[DOMAIN_DATA]["client"] = hub

    if config_entry.data.get("mode") == "aio":
        hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, hub.stop_serial_read_aio())

    hass.async_add_job(
        hass.config_entries.async_forward_entry_setup(config_entry, "sensor")
    )
    _LOGGER.debug("End async_setup_entry")
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    # isnt this blocking?
    _LOGGER.debug("called async_unload_entry")
    hass.data[DOMAIN_DATA]["client"].stop_serial_read()
    await hass.config_entries.async_forward_entry_unload(entry, "sensor")
    _LOGGER.debug("end async_unload_entry")
    return True


class AmsHub:
    """AmsHub wrapper for all sensors."""

    def __init__(self, hass, entry):
        """Initalize the AMS hub."""
        self._hass = hass
        self._settings = entry
        self.sensor_data = {}
        self._running = True
        self._buffer = bytearray()
        self._sleep = entry[CONF_SLEEP]
        self._aio_reader = None
        if self._settings["mode"] != "aio":
            self._ser = serial.Serial(
                port=entry[CONF_SERIAL_PORT],
                baudrate=entry[CONF_BAUDRATE],
                parity=entry[CONF_PARITY],
                stopbits=serial.STOPBITS_ONE,
                bytesize=serial.EIGHTBITS,
                timeout=entry[CONF_TIMEOUT],
            )
            func = self.run if self._settings["mode"] == "thread" else self.connect
            self._runner = threading.Thread(target=func, daemon=True)
            self._runner.start()
        else:
            # Add the serial connection to the event loop.
            self._runner = hass.loop.create_task(self.aio_run())

    async def create_aio_serial(self):
        """Create the streamreader."""
        if self._aio_reader is None:
            self._aio_reader, _ = await serial_asyncio.open_serial_connection(
                url=self._settings[CONF_SERIAL_PORT],
                baudrate=self._settings[CONF_BAUDRATE],
            )
            return self._aio_reader

    def stop_serial_read(self):
        """Close resources."""
        self._running = False
        self._ser.close()

    async def stop_serial_read_aio(self):
        """Stop the task that reads the serial."""
        self._running = False
        self._runner.cancel()

    def read_bytes(self):
        """Read the raw data from serial port."""
        byte_counter = 0
        bytelist = []
        while self._running:
            data = self._ser.read()
            if data:
                bytelist.extend(data)
                if data == FRAME_FLAG and byte_counter > 1:
                    return bytelist
                byte_counter = byte_counter + 1
            else:
                continue

    def connect(self):
        """Read the data from the port."""
        _LOGGER.debug("Using connect")
        while self._running:
            try:
                data = self.read_bytes()
                if han_decode.test_valid_data(data):
                    self.sensor_data = han_decode.parse_data(self.sensor_data, data)
                    self._hass.data[DOMAIN_DATA]["data"] = self.sensor_data
                    self._check_for_new_sensors_and_update(self.sensor_data)
            except serial.serialutil.SerialException:
                pass

    @property
    def data(self):
        """Return sensor data."""
        _LOGGER.debug("sending sensor data")
        return self.sensor_data

    def _check_for_new_sensors_and_update(self, sensor_data):
        """Compare sensor list and update."""
        new_devices = []
        sensor_list = list(sensor_data.keys())
        if len(AMS_DEVICES) < len(sensor_list):
            new_devices = list(set(sensor_list) ^ set(AMS_DEVICES))
            AMS_DEVICES.extend(new_devices)

            _LOGGER.debug("new_devices= %s", new_devices)
            async_dispatcher_send(self._hass, SIGNAL_NEW_AMS_SENSOR)
        else:
            _LOGGER.info("sensors are the same, updating states")
            async_dispatcher_send(self._hass, SIGNAL_UPDATE_AMS)

    def _read_more(self):
        """Read more data from the serial connection"""
        read_nr = max(1, min(1024, self._ser.in_waiting))
        data = self._ser.read(read_nr)
        if data:
            self._buffer.extend(data)
        else:
            time.sleep(self._sleep)

    async def _aio_read_more(self):
        """Acually read the data from the serial"""
        data = await self._aio_reader.readexactly(180)
        if data:
            self._buffer.extend(data)
        await asyncio.sleep(self._sleep)

    async def aio_run(self):
        """Read the serial async"""
        _LOGGER.debug("Using aio_run")
        await self.create_aio_serial()

        # a[start:stop]  # items start through stop-1
        # a[start:]      # items start through the rest of the array
        # a[:stop]       # items from the beginning through stop-1
        # a[:]           # a copy of the whole array

        try:
            while self._running:
                # Remove this later
                # its just for testing.
                await asyncio.sleep(0)

                if len(self._buffer) > 1024:
                    _LOGGER.warning(
                        "Memory leak detected, the buffer is out of bounds, %s, deleted it",
                        self._buffer,
                    )
                    del self._buffer[:]
                    await self.stop_serial_read_aio()

                if len(self._buffer) > 180:
                    # We start by looking in the buffer for first and second frame flag
                    # check that the frame is big enough for the data that we want and
                    # thats is a data flag.
                    start = self._buffer.find(FRAME_FLAG)
                    if start > -1:
                        # _LOGGER.debug("buffer: %s", self._buffer)
                        # slice the self._buffer so we search for the second FRAME_FLAG
                        end = self._buffer[start + 1:].find(FRAME_FLAG)
                        if end > -1:
                            end = start + 1 + end + 1
                            # So we have found what we think is a frame
                            frame = self._buffer[start:end]
                            if len(frame) < 178:
                                _LOGGER.debug(
                                    "The frame is too small! %s %s", len(frame), frame
                                )
                                if len(frame) == 2:
                                    # So we have found a frame but this is probable the last and the first of a "new" msg.
                                    # So lets delete the first one.
                                    _LOGGER.debug(
                                        "Deleted %s from the buffer, have %s left",
                                        self._buffer[: end - 1],
                                        self._buffer[start:],
                                    )
                                    del self._buffer[: end - 1]
                                    await self._aio_read_more()
                                    continue
                                else:
                                    _LOGGER.debug(
                                        "Less then 178 Deleted %s from the buffer, have %s left",
                                        self._buffer[:end],
                                        self._buffer[end:],
                                    )
                                    del self._buffer[:end]
                                    await self._aio_read_more()
                                    continue

                                # Check if its a data frame.
                            if self._buffer[start + 8: start + 12] == DATA_FLAG:
                                # the verifier expects a list..
                                l_frame = list(frame)
                                # We can make s simple version that just does the crc etc.
                                if han_decode.test_valid_data(l_frame):
                                    self.sensor_data = han_decode.parse_data(
                                        self.sensor_data, frame
                                    )
                                    self._hass.data[DOMAIN_DATA][
                                        "data"
                                    ] = self.sensor_data
                                    self._check_for_new_sensors_and_update(
                                        self.sensor_data
                                    )
                                    _LOGGER.debug(
                                        "Valid frame so remove it from the buffer %s full buffer are %s",
                                        self._buffer[:end],
                                        self._buffer,
                                    )
                                    del self._buffer[:end]
                                    continue
                                else:
                                    _LOGGER.warning(
                                        "The frame wasn't valid, removing it from the buffer. Removed data was %s",
                                        self._buffer[:end],
                                    )
                                    del self._buffer[:end]
                            else:
                                # remove the frame from the self._buffer as its junk
                                _LOGGER.debug(
                                    "Deleted the old frame from the buffer as it dont start with a DATA_FLAG %s",
                                    self._buffer[end:],
                                )
                                del self._buffer[:end]

                        else:
                            _LOGGER.debug("Didn't find the second DATA_FLAG")
                            await self._aio_read_more()
                    else:
                        _LOGGER.debug("Didn't find the first DATA_FLAG")
                        await self._aio_read_more()
                else:
                    _LOGGER.debug("Buffer is too small got %s", len(self._buffer))
                    await self._aio_read_more()

        except Exception:
            _LOGGER.exception("some crap happend..")

    def run(self):
        _LOGGER.info("Using run")

        while self._running:
            try:
                # If we have buff, check it
                # 180 seems to be the minimum lenght for a valid frame
                # so lets check that first.
                if len(self._buffer) > 180:
                    # We start by looking in the buffer for first and second frame flag
                    # check that the frame is big enough for the data that we want and
                    # thats is a data flag.
                    start = self._buffer.find(FRAME_FLAG)
                    if start > -1:
                        # slice the self._buffer so we search for the second FRAME_FLAG
                        end = self._buffer[start + 1:].find(FRAME_FLAG)
                        if end > -1:
                            end = start + 1 + end + 1
                            # So we have found what we think is a frame
                            frame = self._buffer[start:end]
                            if len(frame) < 178:
                                if len(frame) == 2:
                                    # So we have found a frame but this is probable the last and the first of a "new" msg.
                                    _LOGGER.debug(
                                        "Deleted %s from the buffer, have %s left",
                                        self._buffer[: end - 1],
                                        self._buffer[start:],
                                    )
                                    del self._buffer[: end - 1]
                                    self._read_more()
                                    continue
                                else:
                                    _LOGGER.debug(
                                        "Deleted %s from the buffer, have %s left",
                                        self._buffer[:end],
                                        self._buffer[end:],
                                    )
                                    del self._buffer[:end]
                                    self._read_more()
                                    continue

                            # Check if its a data frame.
                            if self._buffer[start + 8: start + 12] == DATA_FLAG:
                                # the verifier expects a list..
                                l_frame = list(frame)
                                # We can make s simple version that just does the crc etc.
                                if han_decode.test_valid_data(l_frame):
                                    self.sensor_data = han_decode.parse_data(
                                        self.sensor_data, frame
                                    )
                                    self._hass.data[DOMAIN_DATA][
                                        "data"
                                    ] = self.sensor_data
                                    self._check_for_new_sensors_and_update(
                                        self.sensor_data
                                    )
                                    _LOGGER.debug(
                                        "Valid frame so remove it from the buffer %s full buffer are %s",
                                        self._buffer[:end],
                                        self._buffer,
                                    )
                                    del self._buffer[:end]

                                    continue
                                else:
                                    _LOGGER.warning(
                                        "The frame wasn't valid, removing it from the buffer. Removed data was %s",
                                        self._buffer[:end],
                                    )
                                    del self._buffer[:end]
                            else:
                                _LOGGER.debug(
                                    "Deleted the old frame from the buffer as it dont start with a DATA_FLAG %s",
                                    self._buffer[end:],
                                )
                                del self._buffer[:end]

                        else:
                            _LOGGER.debug("Didn't find the second DATA_FLAG")
                            self._read_more()
                    else:
                        _LOGGER.debug("Didn't find the first DATA_FLAG")
                        self._read_more()
                else:
                    _LOGGER.debug("Buffer is to small got %s", len(self._buffer))
                    self._read_more()

            except serial.serialutil.SerialException:
                pass
