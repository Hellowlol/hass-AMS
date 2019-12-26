#
import os
import logging
import serial
import time
import random
import sys
from pprint import pprint

p = os.path.dirname(__file__)
sys.path.insert(1, p)

import han_decode
import paho.mqtt.client as mqtt


### Edit start. ###
MQTT_URL = "mqtt.eclipse.org"
MQTT_PASSWORD = ""
MQTT_PORT = "1883"
MQTT_TIMEOUT = 60
MQTT_TOPIC = "ams"


DEFAULT_SERIAL_PORT = "/dev/ttyUSB0"
DEFAULT_SERIAL_PORT = "COM7"  # <-- windows
DEFAULT_BAUDRATE = 2400
DEFAULT_PARITY = serial.PARITY_NONE
DEFAULT_TIMEOUT = 0
### Edit stop ###


FRAME_FLAG = b"\x7e"
DATA_FLAG = b"\xe6\xe7\x00\x0f"

RAW_FILE_2 = "sample_data_kamstrup.txt"



_LOGGER = logging.getLogger(__name__)
logging.basicConfig(level=logging.DEBUG)

# https://www.kode24.no/guider/smart-meter-part-1-getting-the-meter-data/71287300


class FakeSer():
    def __init__(self):
        self.file = RAW_FILE_2
        self.buf = bytearray()
        self.bytes = b''
        self.loaded = False

    @property
    def in_waiting(self):
        return random.randrange(0, 300)

    def _load_bytes(self):
        with open(self.file, "rb") as f:
            self.bytes = f.read()
            self.loaded = True

    def read(self, n = 0):
        if not self.loaded:
            self._load_bytes()

        to_read = random.randrange(0, 350)

        try:
            c = self.bytes[:to_read]
            self.bytes = self.bytes[to_read:]
            return c
        except IndexError:
            print("idx err")
            # Add something usefull here.
            return ''

class FrameReader:
    def __init__(self, s):
        self._ser = s
        self._buffer = bytearray()
        self.sensor_data = {}
        self._running = True

    def close(self):
        self._running = False

    def _read_more(self):
        read_nr = max(1, min(2048, self._ser.in_waiting))
        data = self._ser.read(read_nr)
        _LOGGER.debug("Fetching more data wanted %s, got %s", read_nr, len(data))
        if data:
            self._buffer.extend(data)
        else:
            _LOGGER.debug("Sleeping..")
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


class AmsMQTT:
    """AmsMQTT wrapper for all sensors."""

    def __init__(self, client, faked=True):
        """Initalize the AMS hub."""
        port = DEFAULT_SERIAL_PORT
        parity = DEFAULT_PARITY
        self.sensor_data = {}
        self.mqq = client
        self._running = True
        if faked is True:
            self._ser = FakeSer()
        else:
            try:
                self._ser = serial.Serial(
                    port=port,
                    baudrate=DEFAULT_BAUDRATE,
                    parity=parity,
                    stopbits=serial.STOPBITS_ONE,
                    bytesize=serial.EIGHTBITS,
                    timeout=DEFAULT_TIMEOUT)
            except:
                self._ser = None
                print("failed to setup serial")
        self.reader = FrameReader(self._ser)

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
                    self.sensor_data = han_decode.parse_data(self.sensor_data, data)
                    #self._check_for_new_sensors_and_update(self.sensor_data)
            except serial.serialutil.SerialException:
                pass


    def faked(self):
        while self._running:
            self.reader.run()

    def write_output_to_file(self, to_file=None, sec=60):

        end = time.time() + sec
        s = self._ser
        try:
            with open(to_file, "wb") as f:

                while time.time() < end:
                    read_nr = max(1, min(2048, s.in_waiting))
                    data = self._ser.read(read_nr)
                    if len(data):
                        print(data)
                        f.write(data)
                print("done")
        except KeyboardInterrupt:
            pass

    def test_decode(self):
        buf = b''
        byte_counter = 0
        bytelist = []
        with open(RAW_FILE_2, 'rb') as f:
            while True:
                data = f.read(1)
                if data:
                    bytelist.extend(data)
                if data == FRAME_FLAG and byte_counter > 1:
                    if han_decode.test_valid_data(bytelist):
                        print("valid", bytelist)
                        t = han_decode.parse_data(self.sensor_data, bytelist)
                        print(pprint.pprint(t, indent=4))
                        bytelist = []
                    byte_counter = byte_counter + 1
                else:
                    continue

    @property
    def data(self):
        """Return sensor data."""
        _LOGGER.debug("sending sensor data")
        return self.sensor_data


# The callback for when the client receives a CONNACK response from the server.
def on_connect(client, userdata, flags, rc):
    print("Connected with result code " + str(rc))

    # Subscribing in on_connect() means that if we lose the connection and
    # reconnect then subscriptions will be renewed.
    client.subscribe("$SYS/#")


# The callback for when a PUBLISH message is received from the server.
def on_message(client, userdata, msg):
    print(msg.topic + " " + str(msg.payload))


client = mqtt.Client()
client.on_connect = on_connect
client.on_message = on_message


# Blocking call that processes network traffic, dispatches callbacks and
# handles reconnecting.
# Other loop*() functions are available that give a threaded interface and a
# manual interface.

# Lets add error handling here
# try:
#    client.connect("mqtt.eclipse.org", 1883, 60)
#    client.loop_start()
# except Exception as e:
#    _LOGGER.exception('Crap happend with mqtt')

try:
    amsmqqt = AmsMQTT(client, faked=True)

    #amsmqqt.write_output_to_file(RAW_FILE,60*10)

    amsmqqt.faked()
    #f = FakeSer()._load_bytes()
    #print(f)
    #r = amsmqqt.test_decode()
    #print(r)
except KeyboardInterrupt:
    amsmqqt._running = False

print("bye")
