from paho.mqtt import client as mqtt_client
from datetime import datetime, timedelta
import logging
import json
import sys
from utils import TimewindowBuffer, RepeatedTimer, deep_get
import requests

TRIGGER_DIFF = 10

green = "\x1b[33;32m"
reset = "\x1b[0m"
FORMAT = '%(asctime)s:%(levelname)s: %(message)s'
logging.basicConfig(stream=sys.stdout, level="INFO", format=FORMAT)
log = logging.getLogger("")

class Smartmeter:
    opts = {"base_topic":str, "cur_accessor":str, "total_accessor":str, "rapid_change_diff":int}

    def default_calllback(self):
        log.info("default callback")

    def __init__(self, client: mqtt_client, base_topic:str, cur_accessor:str = "Power.Power_curr", total_accessor:str = "Power.Total_in", rapid_change_diff:int = 500, callback = default_calllback):
        self.client = client
        self.base_topic = base_topic
        self.power = TimewindowBuffer(minutes=1)
        self.phase_values = {}
        self.cur_accessor = cur_accessor
        self.total_accessor = total_accessor
        self.rapid_change_diff = rapid_change_diff
        self.last_trigger_value = 0
        self.trigger_callback = callback
        log.info(f'Using {type(self).__name__}: Base topic: {self.base_topic}, Current power accessor: {self.cur_accessor}, Total power accessor: {self.total_accessor}')

    
    def __str__(self):
        return ' '.join(f'{green}SMT: \
                        T:{self.__class__.__name__} \
                        P:{sum(self.phase_values.values()):>3.1f}W {self.power} Predict: {self.getPredictedPower():>3.1f}W{reset}'.split())
                        
    def subscribe(self):
        topics = [f'{self.base_topic}']
        for t in topics:
            self.client.subscribe(t)
            log.info(f'Smartmeter subscribing: {t}')
    
    def ready(self):
        return len(self.phase_values) > 0

    def updPower(self):
        phase_sum = sum(self.phase_values.values())
        # rapid change detection
        diff = (phase_sum if phase_sum < 1000 else 1000) - self.getPower()

        if diff > self.rapid_change_diff:
            log.info("Rapid rise in demand detected, clearing buffer!")
            self.power.clear()
        if diff < 0 and abs(diff) > self.rapid_change_diff:
            log.info("Rapid drop in demand detected, clearing buffer!")
            self.power.clear()
        # by recording smartmeter usage only up to a certain max power we can ensure that
        # demand drops from short high-consumption spikes are faster settled
        self.power.add(phase_sum if phase_sum < 1000 else 1000)
        self.client.publish("solarflow-hub/smartmeter/homeUsage",phase_sum)
        self.client.publish("solarflow-hub/smartmeter/homeUsagePredicted",int(round(self.getPredictedPower())))

        # TODO: experimental, trigger limit calculation only on significant changes of smartmeter
        predicted = self.getPredictedPower()
        if abs(predicted - self.power.last()) >= TRIGGER_DIFF:
            log.info(f'SMT triggers limit function: {self.power.last()} -> {predicted}')
            self.last_trigger_value = predicted
            self.trigger_callback(self.client)

        # in case of a rapid change detected we only have one value and should trigger the limit function
        if self.power.len() == 1:
            self.last_trigger_value = self.power.last()
            self.trigger_callback(self.client)

    def handleMsg(self, msg):
        if msg.topic.startswith(self.base_topic) and msg.payload:
            payload = json.loads(msg.payload.decode())

            if type(payload) is float or type(payload) is int:
                self.phase_values.update({msg.topic:payload})
                self.updPower()
            if type(payload) is dict:
                try: 
                    value = deep_get(payload,self.cur_accessor)
                except:
                    log.error(f'Could not get value from topic payload: {sys.exc_info()}')

                if value:
                    self.phase_values.update({msg.topic:value})
                    self.updPower()

    def getPower(self):
        return self.power.qwavg()
    
    def getPredictedPower(self):
        return self.power.predict()[0]


class Poweropti(Smartmeter):
    POWEROPTI_API = "https://backend.powerfox.energy/api/2.0/my/main/current"
    opts = {"poweropti_user":str, "poweropti_password":str, "rapid_change_diff":int}

    def __init__(self, client: mqtt_client, poweropti_user:str, poweropti_password:str, rapid_change_diff:int = 500, callback = Smartmeter.default_calllback):
        self.client = client
        self.user = poweropti_user
        self.password = poweropti_password
        self.power = TimewindowBuffer(minutes=1)
        self.phase_values = {}
        self.rapid_change_diff = rapid_change_diff
        self.last_trigger_value = 0
        self.trigger_callback = callback
        self.session = None

    def pollPowerfoxAPI(self):
        if self.session == None:
            self.session = requests.Session()
            self.session.auth = (self.user, self.password)

        with self.session as s:
            resp = s.get(self.POWEROPTI_API)
            try:
                current = resp.json()
                watt = int(current['Watt'])
                outdated = bool(current['Outdated'])
                self.phase_values.update({"poweropti":watt})
                self.updPower()
                #self.client.publish(f'poweropti/power',watt)
            except:
                log.exception()

    def subscribe(self):
        updater = RepeatedTimer(5, self.pollPowerfoxAPI)

    def handleMsg(self, msg):
        pass

class ShellyEM3(Smartmeter):
    opts = {"base_topic":str, "rapid_change_diff":int}

    def __init__(self, client: mqtt_client, base_topic:str, rapid_change_diff:int = 500, callback = Smartmeter.default_calllback):
        self.client = client
        self.base_topic = base_topic
        self.power = TimewindowBuffer(minutes=1)
        self.phase_values = {}
        self.rapid_change_diff = rapid_change_diff
        self.last_trigger_value = 0
        self.trigger_callback = callback
        log.info(f'Using {type(self).__name__}: Base topic: {self.base_topic}')

    def subscribe(self):
        topics = [f'{self.base_topic}/emeter/0/power',
                  f'{self.base_topic}/emeter/1/power',
                  f'{self.base_topic}/emeter/2/power'
                 ]
        for t in topics:
            self.client.subscribe(t)
            log.info(f'Shelly3EM subscribing: {t}')

class VZLogger(Smartmeter):
    opts = {"cur_usage_topic":str, "rapid_change_diff":int}

    def __init__(self, client: mqtt_client, cur_usage_topic:str, rapid_change_diff:int = 500, callback = Smartmeter.default_calllback):
        self.client = client
        self.base_topic = cur_usage_topic
        self.power = TimewindowBuffer(minutes=1)
        self.phase_values = {}
        self.rapid_change_diff = rapid_change_diff
        self.last_trigger_value = 0
        self.trigger_callback = callback
        log.info(f'Using {type(self).__name__}: Current Usage Topic: {self.base_topic}')

    def subscribe(self):
        topics = [f'{self.base_topic}',
                 ]
        for t in topics:
            self.client.subscribe(t)
            log.info(f'VZLogger subscribing: {t}')
