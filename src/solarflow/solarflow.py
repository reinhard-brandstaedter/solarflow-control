from paho.mqtt import client as mqtt_client
from datetime import datetime
import logging
import json
import sys
import pathlib
from jinja2 import Environment, FileSystemLoader, DebugUndefined
from utils import TimewindowBuffer, RepeatedTimer

red = "\x1b[31;20m"
reset = "\x1b[0m"
FORMAT = '%(asctime)s:%(levelname)s: %(message)s'
logging.basicConfig(stream=sys.stdout, level="INFO", format=FORMAT)
log = logging.getLogger("")

class Solarflow:
    opts = {"device_id":str ,"full_charge_interval":int}
    SF_PRODUCT_ID = "73bkTV"

    def __init__(self, client: mqtt_client, device_id: str, full_charge_interval:int):
        self.client = client
        self.deviceId = device_id
        self.fullChargeInterval= full_charge_interval
        self.fwVersion = "unknown"
        self.solarInputValues = TimewindowBuffer(minutes=1)
        self.solarInputPower = -1       # solar input power of connected panels
        self.outputPackPower = 0        # charging power of battery pack 
        self.packInputPower = 0         # discharging power of battery pack
        self.outputHomePower = -1       # power sent to home
        self.bypass = False             # Power Bypass Active/Inactive

        self.electricLevel = -1         # state of charge of battery pack
        self.batteriesSoC = {"none":-1}    # state of charge for individual batteries
        self.batteriesVol = {"none":-1}    # voltage for individual batteries
        self.outputLimit = -1           # power limit for home output
        self.inverseMaxPower = 300      # maximum power sent to inverter from hub (read and updated from hub)
        self.outputLimitBuffer = TimewindowBuffer(minutes=1)
        self.lastFullTS = None          # keep track of last time the battery pack was full (100%)
        self.lastEmptyTS = None         # keep track of last time the battery pack was empty (0%)
        self.lastSolarInputTS = None    # time of the last received solar input value
        self.batteryTarget = None

        self.property_topic = f'iot/{self.SF_PRODUCT_ID}/{self.deviceId}/properties/write'
        self.chargeThrough = True
        self.dryrun = False
        self.sunriseSoC = None
        self.sunsetSoC = None
        self.nightConsumption = 100

        self.lastLimitTS = None

    def __str__(self):
        batteries_soc = "|".join([f'{v:>2}' for v in self.batteriesSoC.values()])
        batteries_vol = "|".join([f'{v:2.1f}' for v in self.batteriesVol.values()])
        return ' '.join(f'{red}HUB: \
                        S:{self.solarInputPower:>3.1f}W {self.solarInputValues}, \
                        B:{self.electricLevel:>3}% ({batteries_soc}), \
                        V:{(sum(self.batteriesVol.values()) / len(self.batteriesVol)):2.1f}V ({batteries_vol}), \
                        C:{self.outputPackPower-self.packInputPower:>4}W, \
                        P:{self.bypass}, \
                        F:{self.getLastFullBattery():3.1f}h, \
                        E:{self.getLastEmptyBattery():3.1f}h, \
                        H:{self.outputHomePower:>3}W, \
                        L:{self.outputLimit:>3}W{reset}'.split())

    def update(self): 
        log.info(f'Triggering telemetry update: iot/{self.SF_PRODUCT_ID}/{self.deviceId}/properties/read')
        self.client.publish(f'iot/{self.SF_PRODUCT_ID}/{self.deviceId}/properties/read','{"properties": ["getAll"]}')

    def subscribe(self):
        topics = [
            f'/{self.SF_PRODUCT_ID}/{self.deviceId}/properties/report',
            f'solarflow-hub/{self.deviceId}/telemetry/solarInputPower',
            f'solarflow-hub/{self.deviceId}/telemetry/electricLevel',
            f'solarflow-hub/{self.deviceId}/telemetry/outputPackPower',
            f'solarflow-hub/{self.deviceId}/telemetry/packInputPower',
            f'solarflow-hub/{self.deviceId}/telemetry/outputHomePower',
            f'solarflow-hub/{self.deviceId}/telemetry/outputLimit',
            f'solarflow-hub/{self.deviceId}/telemetry/inverseMaxPower',
            f'solarflow-hub/{self.deviceId}/telemetry/masterSoftVersion',
            f'solarflow-hub/{self.deviceId}/telemetry/pass',
            f'solarflow-hub/{self.deviceId}/telemetry/batteries/+/socLevel',
            f'solarflow-hub/{self.deviceId}/telemetry/batteries/+/totalVol',
            f'solarflow-hub/{self.deviceId}/control/#'
        ]
        for t in topics:
            self.client.subscribe(t)
            log.info(f'Hub subscribing: {t}')
        
        updater = RepeatedTimer(60, self.update)


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
            log.info(f'Battery is full: {self.electricLevel}')
            self.lastFullTS = datetime.now()
            self.client.publish(f'solarflow-hub/{self.deviceId}/control/lastFullTimestamp',int(datetime.timestamp(self.lastFullTS)),retain=True)
            self.client.publish(f'solarflow-hub/{self.deviceId}/control/batteryTarget',"discharging",retain=True)
        if value == 0:
            log.info(f'Battery is empty: {self.electricLevel}')
            self.lastEmptyTS = datetime.now()
            self.client.publish(f'solarflow-hub/{self.deviceId}/control/lastEmptyTimestamp',int(datetime.timestamp(self.lastEmptyTS)),retain=True)
            self.client.publish(f'solarflow-hub/{self.deviceId}/control/batteryTarget',"charging",retain=True)
        self.electricLevel = value
    
    def updOutputPack(self, value:int):
        self.outputPackPower = value

    def updPackInput(self, value:int):
        self.packInputPower = value

    def updOutputHome(self, value:int):
        self.outputHomePower = value
    
    def updOutputLimit(self, value:int):
        self.outputLimit = value
    
    def updInverseMaxPower(self, value:int):
        self.inverseMaxPower = value
    
    def updBatterySoC(self, sn:str, value:int):
        self.batteriesSoC.pop("none",None)
        self.batteriesSoC.update({sn:value})

    def updBatteryVol(self, sn:str, value:int):
        self.batteriesVol.pop("none",None)
        self.batteriesVol.update({sn:value/100})
    
    def updMasterSoftVersion(self, value:int):
        major = (value & 0xf000) >> 12
        minor = (value & 0x0f00) >> 8
        build = (value & 0x00ff)
        self.fwVersion = f'{major}.{minor}.{build}'

        self.pushHomeassistantConfig()
    
    def updByPass(self, value:int):
        self.bypass = bool(value)

    def setChargeThrough(self, value):
        if type(value) == str:
            self.chargeThrough = value.upper() == 'ON'
        if type(value) == int:
            self.chargeThrough = bool(value)
        log.info(f'Set ChargeThrough: {self.chargeThrough}')

    def setDryRun(self,value):
        if type(value) == str:
            self.dryrun = value.upper() == 'ON'
        if type(value) == int:
            self.dryrun = bool(value)
        log.info(f'{self.__class__.__name__} set DryRun: {self.dryrun}')

    def setLastFullTimestamp(self, value):
        self.lastFullTS = datetime.fromtimestamp(value)
        log.info(f'Reading last full time: {datetime.fromtimestamp(value)}')

    def setLastEmptyTimestamp(self, value):
        self.lastEmptyTS = datetime.fromtimestamp(value)
        log.info(f'Reading last empty time: {datetime.fromtimestamp(value)}')

    def setBatteryTarget(self, value):
        self.batteryTarget = value
        log.info(f'Reading battery target mode: {value}')

    def setSunriseSoC(self, soc:int):
        self.sunriseSoC = soc
        if self.sunsetSoC:
            self.nightConsumption = self.sunsetSoC - self.sunriseSoC

    def setSunsetSoC(self, soc:int):
        self.sunsetSoC = soc

    def getNightConsumption(self):
        return self.nightConsumption

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
                case "inverseMaxPower":
                    self.updInverseMaxPower(int(value))
                case "socLevel":
                    sn = msg.topic.split('/')[-2]
                    self.updBatterySoC(sn=sn, value=int(value))
                case "totalVol":
                    sn = msg.topic.split('/')[-2]
                    self.updBatteryVol(sn=sn, value=int(value))
                case "masterSoftVersion":
                    self.updMasterSoftVersion(value=int(value))
                case "chargeThrough":
                    self.setChargeThrough(value)
                case "dryRun":
                    self.setDryRun(value)
                case "lastFullTimestamp":
                    self.setLastFullTimestamp(float(value))
                case "lastEmptyTimestamp":
                    self.setLastEmptyTimestamp(float(value))
                case "batteryTarget":
                    self.setBatteryTarget(value)
                case "pass":
                    self.updByPass(int(value))
                case _:
                    log.warning(f'Ignoring solarflow-hub metric: {metric}')

    def setOutputLimit(self, limit:int):
        # since the hub is slow in adoption we should not try to set the limit too frequently
        # 30-45s seems ok
        now = datetime.now()
        if self.lastLimitTS:
            elapsed = now - self.lastLimitTS
            if elapsed.total_seconds() < 45:
                log.info(f'Hub has just recently adjusted limit, need to wait until it is set again! Current limit: {self.outputLimit}, new limit: {limit}')
                return self.outputLimit
            else:
                self.lastLimitTS = now
        else:
            self.lastLimitTS = now

        if limit < 0:
            limit = 0
        
        # If battery SoC reaches 0% during night, it has been observed that in the morning with first light, residual energy in the batteries gets released
        # Hub goes then into error and no charging occurs (probably deep discharge assumed by the battery).
        # Hence setting the output limit 0 if SoC 0%
        if self.electricLevel == 0:
            limit = 0
            log.info(f'Battery is empty! Disabling solaraflow output, setting limit to {limit}')
            

        # Charge-Through:
        # If charge-through is enabled the hub will not provide any power if the last full state is to long ago
        # this ensures regular loading to 100% to avoid battery-drift
        fullage = self.getLastFullBattery()
        emptyage = self.getLastEmptyBattery()
        can_discharge = (self.batteryTarget == "discharging") or (self.batteryTarget == "charging" and fullage < self.fullChargeInterval)
        if  self.chargeThrough and (limit > 0 and (not can_discharge or fullage < 0)):
            log.info(f'Battery hasn\'t fully charged for {fullage:.1f} hours! To ensure it is fully charged at least every {self.fullChargeInterval}hrs not discharging now!')
            # either limit to 0 or only give away what is higher than min_charge_level
            limit = 0

        # SF takes ~1 minute to apply the limit to actual output, so better smoothen the limit to avoid output spikes on short demand spikes
        self.outputLimitBuffer.add(limit)
        limit = int(self.outputLimitBuffer.qwavg())

        # currently the hub doesn't support single steps for limits below 100
        # to get a fine granular steering at this level we need to fall back to the inverter limit
        # if controlling the inverter is not possible we should stick to either 0 or 100W
        if limit <= 100:
            #limitInverter(client,limit)
            #log.info(f'The output limit would be below 100W ({limit}W). Would need to limit the inverter to match it precisely')
            m = divmod(limit,30)[0]
            r = divmod(limit,30)[1]
            limit = 30 * m + 30 * (r // 15)

        outputlimit = {"properties": { "outputLimit": limit }}
        if self.outputLimit != limit:
            (not self.dryrun) and self.client.publish(self.property_topic,json.dumps(outputlimit))
            log.info(f'{"[DRYRUN] " if self.dryrun else ""}Setting solarflow output limit to {limit:.1f}W')
        else:
            log.info(f'{"[DRYRUN] " if self.dryrun else ""}Not setting solarflow output limit to {limit:.1f}W as it is identical to current limit!')
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
    
    def getInverseMaxPower(self):
        return self.inverseMaxPower
    
    def getBypass(self):
        return self.bypass


