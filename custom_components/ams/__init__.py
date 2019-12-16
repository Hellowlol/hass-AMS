"""AMS hub platform."""
import logging
import threading
import time
import serial
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.dispatcher import async_dispatcher_send
import homeassistant.helpers.config_validation as cv
import voluptuous as vol
from . import han_decode


DOMAIN = 'ams'
AMS_SENSORS = 'ams_sensors'
AMS_DEVICES = []
SIGNAL_UPDATE_AMS = 'update'
SIGNAL_NEW_AMS_SENSOR = 'ams_new_sensor'

_LOGGER = logging.getLogger(__name__)

CONF_SERIAL_PORT = "serial_port"
CONF_BAUDRATE = "baudrate"
CONF_PARITY = "parity"

DEFAULT_NAME = "AMS Sensor"
DEFAULT_SERIAL_PORT = "/dev/ttyUSB0"
DEFAULT_BAUDRATE = 2400
DEFAULT_PARITY = serial.PARITY_NONE
DEFAULT_TIMEOUT = 0
FRAME_FLAG = b'\x7e'
DATA_FLAG = b"\xe6\xe7\x00\x0f"

CONFIG_SCHEMA = vol.Schema(
    {
        DOMAIN: vol.Schema(
            {
                vol.Required(CONF_SERIAL_PORT,
                             default=DEFAULT_SERIAL_PORT): cv.string,
                vol.Optional(CONF_PARITY, default=DEFAULT_PARITY): cv.string,
            }
        )
    },
    extra=vol.ALLOW_EXTRA,
)


async def async_setup(hass, config) -> bool:
    """AMS hub."""
    if config.get(DOMAIN) is None:
        _LOGGER.debug("No YAML config available, using config_entries")
        return True

    hub = AmsHub(hass, config)
    hass.data[DOMAIN] = hub

    hass.async_create_task(
        hass.config_entries.flow.async_init(
            DOMAIN, context={"source":
                             hass.config_entries.SOURCE_IMPORT}, data={}
        )
    )
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up AMS as config entry."""
    hub = AmsHub(hass, entry)
    hass.data[DOMAIN] = hub
    hass.async_add_job(
        hass.config_entries.async_forward_entry_setup(entry, 'sensor')
    )
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    hass.data[DOMAIN].stop_serial_read()
    await hass.config_entries.async_forward_entry_unload(entry, 'sensor')
    return True


class AmsHub():
    """AmsHub wrapper for all sensors."""

    def __init__(self, hass, entry):
        """Initalize the AMS hub."""
        self._hass = hass
        port = entry.data[CONF_SERIAL_PORT]
        parity = entry.data[CONF_PARITY]
        self.sensor_data = {}
        self._hass.data[AMS_SENSORS] = self.data
        self._running = True
        self._buffer = bytearray()
        self._ser = serial.Serial(
            port=port,
            baudrate=DEFAULT_BAUDRATE,
            parity=parity,
            stopbits=serial.STOPBITS_ONE,
            bytesize=serial.EIGHTBITS,
            timeout=DEFAULT_TIMEOUT)
        self._runner = threading.Thread(target=self.run, daemon=True)
        self._runner.start()
        _LOGGER.info('Finish init of AMS')

    def stop_serial_read(self):
        """Close resources."""
        self._running = False
        self._ser.close()

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
        while self._running:
            try:
                data = self.read_bytes()
                if han_decode.test_valid_data(data):
                    self.sensor_data = han_decode.parse_data(
                        self.sensor_data, data)
                    self._hass.data[AMS_SENSORS] = self.sensor_data
                    self._check_for_new_sensors_and_update(self.sensor_data)
            except serial.serialutil.SerialException:
                pass

    @property
    def data(self):
        """Return sensor data."""
        _LOGGER.debug('sending sensor data')
        return self.sensor_data

    def _check_for_new_sensors_and_update(self, sensor_data):
        """Compare sensor list and update."""
        sensor_list = []
        new_devices = []
        for sensor_name in sensor_data.keys():
            sensor_list.append(sensor_name)
        _LOGGER.debug('sensor_list= %s', sensor_list)
        _LOGGER.debug('AMS_DEVICES= %s', AMS_DEVICES)
        if len(AMS_DEVICES) < len(sensor_list):
            new_devices = list(set(sensor_list) ^ set(AMS_DEVICES))
            for device in new_devices:
                AMS_DEVICES.append(device)
            async_dispatcher_send(self._hass, SIGNAL_NEW_AMS_SENSOR)
            _LOGGER.debug('new_devices= %s', new_devices)
        else:
            _LOGGER.debug('sensors are the same, updating states')
            _LOGGER.debug('hass.data[AMS_SENSORS] = %s',
                          self._hass.data[AMS_SENSORS])
            async_dispatcher_send(self._hass, SIGNAL_UPDATE_AMS)

    def _read_more(self):
        read_nr = max(1, min(2048, self._ser.in_waiting))
        data = self._ser.read(read_nr)
        _LOGGER.debug("Fetching more data wanted %s, got %s", read_nr, len(data))
        if data:
            self._buffer.extend(data)
        else:
            time.sleep(0.2)

    def run(self):
        _LOGGER.debug("Doing run!")

        while self._running:
            # If we have buff, check it
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
                        if len(frame) < 179:
                            _LOGGER.debug("The frame is too small! %s", len(frame))
                            self._buffer = self._buffer[end:]
                            self._read_more()
                            continue

                        # Check if its a data frame.
                        if self._buffer[start + 8:start + 12] == DATA_FLAG:
                            # the verifier expects a list..
                            l_frame = list(frame)
                            # We can make s simple version that just does the crc etc.
                            if han_decode.test_valid_data(l_frame):
                                self.sensor_data = han_decode.parse_data(self.sensor_data, frame)
                                self._hass.data[AMS_SENSORS] = self.sensor_data
                                self._check_for_new_sensors_and_update(self.sensor_data)
                                self._buffer = self._buffer[end:]
                                _LOGGER.debug("reset the buffer to %s", self._buffer[end:])
                                continue
                        else:
                            # remove the frame from the self._buffer as its junk
                            _LOGGER.debug("Deleted the old frame from the buffer it dont have data %s", self._buffer[end:])
                            self._buffer = self._buffer[end:]

                    else:
                        _LOGGER.debug("Didn't find the second DATA_FLAG")
                        self._read_more()
                else:
                    _LOGGER.debug("Didn't find the first DATA_FLAG")
                    self._read_more()
            else:
                _LOGGER.debug("Buffer is to small %s", len(self._buffer))
                self._read_more()
