from paho.mqtt import client as mqtt_client
from datetime import datetime, timedelta
from functools import reduce
import logging
import json
import sys
from utils import TimewindowBuffer, deep_get

green = "\x1b[33;32m"
reset = "\x1b[0m"
FORMAT = '%(asctime)s:%(levelname)s: %(message)s'
logging.basicConfig(stream=sys.stdout, level="INFO", format=FORMAT)
log = logging.getLogger("")

class Smartmeter:

    def __init__(self, client: mqtt_client, base_topic:str, cur_accessor:str = "Power.Power_curr", total_accessor:str = "Power.Total_in"):
        self.client = client
        self.base_topic = base_topic
        self.power = TimewindowBuffer(minutes=1)
        self.phase_values = {}
        self.cur_accessor = cur_accessor
        self.total_accessor = total_accessor
    
    def __str__(self):
        return ' '.join(f'{green}SMT: \
                        P:{self.power.wavg():>3.1f}W {self.power}{reset}'.split())
                        
    def subscribe(self):
        topics = [f'{self.base_topic}']
        for t in topics:
            self.client.subscribe(t)

    def updPower(self):
        phase_sum = sum(self.phase_values.values())
        self.power.add(phase_sum)

    def handleMsg(self, msg):
        if msg.topic.startswith(self.base_topic) and msg.payload:
            payload = json.loads(msg.payload.decode())

            if type(payload) is float or type(payload) is int:
                self.phase_values.update({msg.topic:payload})
            if type(payload) is dict:
                value = float(deep_get(payload,self.cur_accessor))
                self.phase_values.update({msg.topic:value})

            self.updPower()

            
