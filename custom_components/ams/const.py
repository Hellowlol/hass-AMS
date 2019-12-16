""" Const """

DOMAIN = 'ams'
AMS_SENSORS = 'ams_sensors'
AMS_DEVICES = []
SIGNAL_UPDATE_AMS = 'update'
SIGNAL_NEW_AMS_SENSOR = 'ams_new_sensor'


CONF_SERIAL_PORT = "serial_port"
CONF_BAUDRATE = "baudrate"
CONF_PARITY = "parity"
CONF_TIMEOUT = "timeout"
CONF_SLEEP = "sleep"

DEFAULT_NAME = "AMS Sensor"
DEFAULT_SERIAL_PORT = "/dev/ttyUSB0"
DEFAULT_BAUDRATE = 2400
DEFAULT_PARITY = serial.PARITY_NONE
DEFAULT_TIMEOUT = 0
DEFAULT_SLEEP = 0.2

FRAME_FLAG = b'\x7e'
DATA_FLAG = b"\xe6\xe7\x00\x0f"


DATA_FLAG_LIST = [230, 231, 0, 15]
FRAME_FLAG = b"\x7e"
LIST_TYPE_SHORT_1PH = 17
LIST_TYPE_LONG_1PH = 27
LIST_TYPE_SHORT_3PH = 25
LIST_TYPE_LONG_3PH = 35

WEEKDAY_MAPPING = {
    1: "Monday",
    2: "Tuesday",
    3: "Wednesday",
    4: "Thursday",
    5: "Friday",
    6: "Saturday",
    7: "Sunday"
}
