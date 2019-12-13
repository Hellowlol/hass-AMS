#

import logging
import threading
import serial

import han_decode
import paho.mqtt.client as mqtt


### Edit start. ###
MQTT_URL = "mqtt.eclipse.org"
MQTT_PASSWORD = ""
MQTT_PORT = "1883"
MQTT_TIMEOUT = 60
MQTT_TOPIC = "ams"


DEFAULT_SERIAL_PORT = "/dev/ttyUSB0"
DEFAULT_BAUDRATE = 2400
DEFAULT_PARITY = serial.PARITY_NONE
DEFAULT_TIMEOUT = 0
### Edit stop ###

FRAME_FLAG = b'\x7e'
_LOGGER = logging.getLogger(__name__)


class AmsMQTT():
    """AmsMQTT wrapper for all sensors."""

    def __init__(self, client):
        """Initalize the AMS hub."""
        port = DEFAULT_SERIAL_PORT
        parity = DEFAULT_PARITY
        self.sensor_data = {}
        self.mqq = client
        self._running = True
        self._ser = serial.Serial(
            port=port,
            baudrate=DEFAULT_BAUDRATE,
            parity=parity,
            stopbits=serial.STOPBITS_ONE,
            bytesize=serial.EIGHTBITS,
            timeout=DEFAULT_TIMEOUT)
        connection = threading.Thread(target=self.connect, daemon=True)
        connection.start()
        _LOGGER.debug('Finish init of AMS')

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
                    #self.client.publish() # something something
            except serial.serialutil.SerialException:
                pass

    @property
    def data(self):
        """Return sensor data."""
        _LOGGER.debug('sending sensor data')
        return self.sensor_data



# The callback for when the client receives a CONNACK response from the server.
def on_connect(client, userdata, flags, rc):
    print("Connected with result code "+str(rc))

    # Subscribing in on_connect() means that if we lose the connection and
    # reconnect then subscriptions will be renewed.
    client.subscribe("$SYS/#")

# The callback for when a PUBLISH message is received from the server.
def on_message(client, userdata, msg):
    print(msg.topic+" "+str(msg.payload))

client = mqtt.Client()
client.on_connect = on_connect
client.on_message = on_message



# Blocking call that processes network traffic, dispatches callbacks and
# handles reconnecting.
# Other loop*() functions are available that give a threaded interface and a
# manual interface.

# Lets add error handling here
try:
    client.connect("mqtt.eclipse.org", 1883, 60)
    client.loop_start()
except Exception as e:
    _LOGGER.exception('Crap happend with mqtt')

amsmqqt = AmsMQTT(client)

