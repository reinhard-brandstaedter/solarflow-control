from paho.mqtt import client as mqtt_client
from datetime import datetime, timedelta
from functools import reduce
import logging
import json
import sys
import pathlib
from jinja2 import Environment, FileSystemLoader, DebugUndefined
from utils import TimewindowBuffer

red = "\x1b[31;20m"
reset = "\x1b[0m"
FORMAT = '%(asctime)s:%(levelname)s: %(message)s'
logging.basicConfig(stream=sys.stdout, level="INFO", format=FORMAT)
log = logging.getLogger("")

class SolarflowHub:
    SF_PRODUCT_ID = "73bkTV"
    FULL_CHARGE_AGE = 72

    def __init__(self, device_id: str, client: mqtt_client):
        self.client = client
        self.deviceId = device_id
        self.fwVersion = "unknown"
        self.solarInputValues = TimewindowBuffer(minutes=1)
        self.solarInputPower = -1       # solar input power of connected panels
        self.outputPackPower = 0        # charging power of battery pack 
        self.packInputPower = 0         # discharging power of battery pack
        self.outputHomePower = -1       # power sent to home

        self.electricLevel = -1         # state of charge of battery pack
        self.batteries = {"none":-1}    # state of charge for individual batteries
        self.outputLimit = -1           # power limit for home output
        self.lastFullTS = None          # keep track of last time the battery pack was full (100%)
        self.lastEmptyTS = None         # keep track of last time the battery pack was empty (0%)
        self.lastSolarInputTS = None    # time of the last received solar input value

        self.property_topic = f'iot/73bkTV/{self.deviceId}/properties/write'
        self.chargeThrough = True

    def __str__(self):
        batteries = "|".join([f'{v:>2}' for v in self.batteries.values()])
        return ' '.join(f'{red}HUB: \
                        S:{self.solarInputPower:>3.1f}W {self.solarInputValues}, \
                        B:{self.electricLevel:>3}% ({batteries}), \
                        C:{self.outputPackPower-self.packInputPower:>4}W, \
                        F:{self.getLastFullBattery():3.1f}h, \
                        E:{self.getLastEmptyBattery():3.1f}h, \
                        H:{self.outputHomePower:>3}W, \
                        L:{self.outputLimit:>3}W{reset}'.split())

    def subscribe(self):
        topics = [
            f'/{self.SF_PRODUCT_ID}/{self.deviceId}/properties/report',
            f'solarflow-hub/{self.deviceId}/telemetry/solarInputPower',
            f'solarflow-hub/{self.deviceId}/telemetry/electricLevel',
            f'solarflow-hub/{self.deviceId}/telemetry/outputPackPower',
            f'solarflow-hub/{self.deviceId}/telemetry/packInputPower',
            f'solarflow-hub/{self.deviceId}/telemetry/outputHomePower',
            f'solarflow-hub/{self.deviceId}/telemetry/outputLimit',
            f'solarflow-hub/{self.deviceId}/telemetry/masterSoftVersion',
            f'solarflow-hub/{self.deviceId}/telemetry/batteries/+/socLevel',
            "solarflow-hub/control/chargeThrough"
        ]
        for t in topics:
            self.client.subscribe(t)
            log.info(f'Subscribing: {t}')

    def ready(self):
        return (self.electricLevel > -1 and self.solarInputPower > -1)

    def pushHomeassistantConfig(self):
        hatemplates = [f for f in pathlib.Path().glob("homeassistant/*.json")]
        environment = Environment(loader=FileSystemLoader("homeassistant/"), undefined=DebugUndefined)

        for hatemplate in hatemplates:
            template = environment.get_template(hatemplate.name)
            hacfg = template.render(device_id=self.deviceId, fw_version=self.fwVersion)
            cfg_type = hatemplate.name.split(".")[0]
            cfg_name = hatemplate.name.split(".")[1]
            self.client.publish(f'homeassistant/{cfg_type}/solarflow-hub-{self.deviceId}-{cfg_name}/config',hacfg)
            #log.info(hacfg)

    def updSolarInput(self, value:int):
        self.solarInputValues.add(value)
        self.solarInputPower = self.solarInputValues.wavg()
        self.lastSolarInputTS = datetime.now()
    
    def updElectricLevel(self, value:int):
        if value == 100:
            self.lastFullTS = datetime.now()
        if value == 0:
            self.lastEmptyTS = datetime.now()
        self.electricLevel = value
    
    def updOutputPack(self, value:int):
        self.outputPackPower = value

    def updPackInput(self, value:int):
        self.packInputPower = value

    def updOutputHome(self, value:int):
        self.outputHomePower = value
    
    def updOutputLimit(self, value:int):
        self.outputLimit = value
    
    def updBatterySoC(self, sn:str, value:int):
        self.batteries.pop("none",None)
        self.batteries.update({sn:value})
    
    def updMasterSoftVersion(self, value:int):
        major = (value & 0xf000) >> 12
        minor = (value & 0x0f00) >> 8
        build = (value & 0x00ff)
        self.fwVersion = f'{major}.{minor}.{build}'

        self.pushHomeassistantConfig()

    def setChargeThrough(self, value):
        if type(value) == str:
            self.chargeThrough = value.upper() == 'ON'
        if type(value) == int:
            self.chargeThrough = bool(value)
        log.info(f'Set ChargeThrough: {self.chargeThrough}')

    # handle content of mqtt message and update properties accordingly
    def handleMsg(self, msg):
        # transform the original messages sent by the SF hub into a better readable format
        if self.SF_PRODUCT_ID in msg.topic:
            device_id = msg.topic.split('/')[2]
            payload = json.loads(msg.payload.decode())
            if "properties" in payload:
                props = payload["properties"]
                for prop, val in props.items():
                    self.client.publish(f'solarflow-hub/{device_id}/telemetry/{prop}',val)
            
            if "packData" in payload:
                packdata = payload["packData"]
                if len(packdata) > 0:
                    for pack in packdata:
                        sn = pack.pop('sn')
                        for prop, val in pack.items():
                            self.client.publish(f'solarflow-hub/{device_id}/telemetry/batteries/{sn}/{prop}',val)

        if msg.topic.startswith('solarflow-hub') and msg.payload:
            # check if we got regular updates on solarInputPower
            # if we haven't received any update on solarInputPower for 120s
            # we assume it's not producing and inject 0
            now = datetime.now()
            if self.lastSolarInputTS:
                diff = now - self.lastSolarInputTS
                seconds = diff.total_seconds()
                if seconds > 120:
                    self.updSolarInput(0)

            metric = msg.topic.split('/')[-1]
            value = msg.payload.decode()
            match metric:
                case "electricLevel":
                    self.updElectricLevel(int(value))
                case "solarInputPower":
                    self.updSolarInput(int(value))
                case "outputPackPower":
                    self.updOutputPack(int(value))
                case "packInputPower":
                    self.updPackInput(int(value))
                case "outputHomePower":
                    self.updOutputHome(int(value))
                case "outputLimit":
                    self.updOutputLimit(int(value))
                case "socLevel":
                    sn = msg.topic.split('/')[-2]
                    self.updBatterySoC(sn=sn, value=int(value))
                case "masterSoftVersion":
                    self.updMasterSoftVersion(value=int(value))
                case "chargeThrough":
                    self.setChargeThrough(value)
                case _:
                    log.warning(f'Ignoring solarflow-hub metric: {metric}')

    def setOutputLimit(self, limit:int):
        if limit < 0:
            limit = 0
        # currently the hub doesn't support single steps for limits below 100
        # to get a fine granular steering at this level we need to fall back to the inverter limit
        # if controlling the inverter is not possible we should stick to either 0 or 100W
        if limit <= 100:
            #limitInverter(client,limit)
            #log.info(f'The output limit would be below 100W ({limit}W). Would need to limit the inverter to match it precisely')
            m = divmod(limit,30)[0]
            r = divmod(limit,30)[1]
            limit = 30 * m + 30 * (r // 15)

        fullage = self.getLastFullBattery()
        emptyage = self.getLastEmptyBattery()
        if  self.chargeThrough and (limit > 0 and (fullage > self.FULL_CHARGE_AGE or fullage < 0 or  0 < emptyage < 1)):
            log.info(f'Battery hasn\'t fully charged for {fullage} hours or is empty, not discharging')
            limit = 0

        outputlimit = {"properties": { "outputLimit": limit }}
        if self.outputLimit != limit:
            self.client.publish(self.property_topic,json.dumps(outputlimit))
            log.info(f'Setting solarflow output limit to {limit} W')
        else:
            log.info(f'Not setting solarflow output limit as it is identical to current limit!')
        return limit

    def setBuzzer(self, state: bool):
        buzzer = {"properties": { "buzzerSwitch": 0 if not state else 1 }}
        self.client.publish(self.property_topic,json.dumps(buzzer))

    # return how much time has passed since last full charge (in hours)
    def getLastFullBattery(self) -> int:
        if self.lastFullTS:
            diff = datetime.now() - self.lastFullTS
            return diff.total_seconds()/3600
        else:
            return -1

    # return how much time has passed since last full charge (in hours)
    def getLastEmptyBattery(self) -> int:
        if self.lastEmptyTS:
            diff = datetime.now() - self.lastEmptyTS
            return diff.total_seconds()/3600
        else:
            return -1
        
    def getOutputHomePower(self):
        return self.outputHomePower
    
    def getSolarInputPower(self):
        return self.solarInputPower
    
    def getElectricLevel(self):
        return self.electricLevel


