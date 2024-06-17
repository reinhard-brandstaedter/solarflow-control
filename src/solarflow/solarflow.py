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

TRIGGER_DIFF = 30

HUB1200 = "73bkTV"
HUB2000 = "A8yh63"


class Solarflow:
    opts = {"product_id":str, "device_id":str ,"full_charge_interval":int, "control_bypass":bool}

    def default_calllback(self):
        log.info("default callback")

    def __init__(self, client: mqtt_client, product_id:str, device_id:str, full_charge_interval:int, control_bypass:bool = False, callback = default_calllback):
        self.client = client
        self.productId = product_id
        self.deviceId = device_id
        self.fullChargeInterval= full_charge_interval
        self.fwVersion = "unknown"
        self.solarInputValues = TimewindowBuffer(minutes=1)
        self.solarInputPower = -1       # solar input power of connected panels
        self.outputPackPower = 0        # charging power of battery pack
        self.packInputPower = 0         # discharging power of battery pack
        self.outputHomePower = -1       # power sent to home
        self.bypass = False             # Power Bypass Active/Inactive
        self.control_bypass = control_bypass    # wether we control the bypass switch or the hubs firmware
        self.bypass_mode = -1           # bypassmode the hub is operating in 0=auto, 1=manual off, 2=manual on
        self.allow_bypass = True        # if bypass can be currently enabled or not
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

        self.property_topic = f'iot/{self.productId}/{self.deviceId}/properties/write'
        self.chargeThrough = True
        self.dryrun = False
        self.sunriseSoC = None
        self.sunsetSoC = None
        self.nightConsumption = 100
        self.trigger_callback = callback

        self.lastLimitTS = None

        updater = RepeatedTimer(60, self.update)
        haconfig = RepeatedTimer(600, self.pushHomeassistantConfig)
        self.pushHomeassistantConfig()
        self.update()

    def __str__(self):
        batteries_soc = "|".join([f'{v:>2}' for v in self.batteriesSoC.values()])
        batteries_vol = "|".join([f'{v:2.1f}' for v in self.batteriesVol.values()])
        return ' '.join(f'{red}HUB: \
                        S:{self.solarInputPower:>3.1f}W {self.solarInputValues}, \
                        B:{self.electricLevel:>3}% ({batteries_soc}), \
                        V:{(sum(self.batteriesVol.values()) / len(self.batteriesVol)):2.1f}V ({batteries_vol}), \
                        C:{self.outputPackPower-self.packInputPower:>4}W, \
                        P:{self.getBypass()} ({"auto" if self.bypass_mode == 0 else "manual"}, {"possible" if self.allow_bypass else "not possible"}), \
                        F:{self.getLastFullBattery():3.1f}h, \
                        E:{self.getLastEmptyBattery():3.1f}h, \
                        H:{self.outputHomePower:>3}W, \
                        L:{self.outputLimit:>3}W{reset}'.split())

    def update(self):
        log.info(f'Triggering telemetry update: iot/{self.productId}/{self.deviceId}/properties/read')
        self.client.publish(f'iot/{self.productId}/{self.deviceId}/properties/read','{"properties": ["getAll"]}')

    def subscribe(self):
        topics = [
            f'/{self.productId}/{self.deviceId}/properties/report',
            f'solarflow-hub/{self.deviceId}/telemetry/solarInputPower',
            f'solarflow-hub/{self.deviceId}/telemetry/electricLevel',
            f'solarflow-hub/{self.deviceId}/telemetry/outputPackPower',
            f'solarflow-hub/{self.deviceId}/telemetry/packInputPower',
            f'solarflow-hub/{self.deviceId}/telemetry/outputHomePower',
            f'solarflow-hub/{self.deviceId}/telemetry/outputLimit',
            f'solarflow-hub/{self.deviceId}/telemetry/inverseMaxPower',
            f'solarflow-hub/{self.deviceId}/telemetry/masterSoftVersion',
            f'solarflow-hub/{self.deviceId}/telemetry/pass',
            f'solarflow-hub/{self.deviceId}/telemetry/passMode',
            f'solarflow-hub/{self.deviceId}/telemetry/batteries/+/socLevel',
            f'solarflow-hub/{self.deviceId}/telemetry/batteries/+/totalVol',
            f'solarflow-hub/{self.deviceId}/control/#'
        ]
        for t in topics:
            self.client.subscribe(t)
            log.info(f'Hub subscribing: {t}')

    def ready(self):
        return (self.electricLevel > -1 and self.solarInputPower > -1)

    def timesync(self, ts):
        payload = {
            "zoneOffset": "+00:00", 
            "messageId": 123,
            "timestamp": ts
        }
        self.client.publish(f'iot/{self.productId}/{self.deviceId}/time-sync/reply',json.dumps(payload))

    def pushHomeassistantConfig(self):
        log.info("Publishing Homeassistant templates...")
        hatemplates = [f for f in pathlib.Path().glob("homeassistant/*.json")]
        environment = Environment(loader=FileSystemLoader("homeassistant/"), undefined=DebugUndefined)

        for hatemplate in hatemplates:
            template = environment.get_template(hatemplate.name)
            cfg_type = hatemplate.name.split(".")[0]
            cfg_name = hatemplate.name.split(".")[1]
            if cfg_name == "maxTemp":
                for serial,v in self.batteriesVol.items():
                    hacfg = template.render(product_id=self.productId, device_id=self.deviceId, fw_version=self.fwVersion, battery_serial=serial)
                    if serial != "none":
                        self.client.publish(f'homeassistant/{cfg_type}/solarflow-hub-{self.deviceId}-{serial}-{cfg_name}/config',hacfg)
            else:
                hacfg = template.render(product_id=self.productId, device_id=self.deviceId, fw_version=self.fwVersion)
                self.client.publish(f'homeassistant/{cfg_type}/solarflow-hub-{self.deviceId}-{cfg_name}/config',hacfg)
            #log.info(hacfg)
        log.info(f"Published {len(hatemplates)} Homeassistant templates.")

    def updSolarInput(self, value:int):
        self.solarInputValues.add(value)
        self.solarInputPower = self.getSolarInputPower()
        self.lastSolarInputTS = datetime.now()

        # TODO: experimental, trigger limit calculation only on significant changes of smartmeter
        previous = self.solarInputValues.previous()
        if abs(previous - self.getSolarInputPower()) >= TRIGGER_DIFF:
            log.info(f'HUB triggers limit function: {previous} -> {self.getSolarInputPower()}: {"executed" if self.trigger_callback(self.client) else "skipped"}')
            self.last_trigger_value = self.getSolarInputPower()

    def updElectricLevel(self, value:int):
        if value == 100:
            if self.batteryTarget == "charging":
                log.info(f'Battery is full: {self.electricLevel}')
            
            # only enable bypass on first report of 100%, otherwise it would get enabled again and again
            if self.control_bypass and self.allow_bypass:
                log.info(f'Bypass control, turning on bypass!')
                self.setBypass(True)
                self.allow_bypass = False

            self.lastFullTS = datetime.now()
            self.client.publish(f'solarflow-hub/{self.deviceId}/control/lastFullTimestamp',int(datetime.timestamp(self.lastFullTS)),retain=True)
            self.client.publish(f'solarflow-hub/{self.deviceId}/control/batteryTarget',"discharging",retain=True)
        if value == 0:
            if self.batteryTarget == "discharging":
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

        # put into own timer in init
        # self.pushHomeassistantConfig() # why here? this is not needed every minute

    def updByPass(self, value:int):
        # Hub2000 doesn't report bypass via pass property only when in auto mode?
        # see: https://github.com/reinhard-brandstaedter/solarflow-control/issues/244#issuecomment-2152861536
        if self.productId == HUB2000 and self.bypass_mode != 0:
            return
        self.bypass = bool(value)

    def updByPassMode(self, value: int):
        # it seems when the battery is completely depleted SF resets the bypass to auto, so we enforce it manual off when this happens
        if self.control_bypass and value == 0 and not self.bypass:
            self.setBypass(False)
            value = 1

        if self.productId == HUB2000:
            self.bypass = value==2
        
        self.bypass_mode = value

    def allowBypass(self, allow):
        self.allow_bypass = allow

    def setChargeThrough(self, value):
        if type(value) == str:
            self.chargeThrough = value.upper() == 'ON'
        if type(value) == int:
            self.chargeThrough = bool(value)
        log.info(f'Set ChargeThrough: {self.chargeThrough}')
        # in case of setups with no direct panels connected to inverter it is necessary to turn on the inverter as it is likely offline now
        inv = self.client._userdata['dtu']
        if (not inv.ready()) and self.getOutputHomePower() == 0:
            # this will power on the inverter so that control can resume from an interrupted charge-through
            self.setOutputLimit(30)

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
        if self.productId in msg.topic:
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
                case "passMode":
                    self.updByPassMode(int(value))
                case _:
                    log.warning(f'Ignoring solarflow-hub metric: {metric}')

    def setOutputLimit(self, limit:int):
        # since the hub is slow in adoption we should not try to set the limit too frequently
        # 30-45s seems ok
        now = datetime.now()
        if self.lastLimitTS:
            elapsed = now - self.lastLimitTS
            if elapsed.total_seconds() < 30:
                log.info(f'Hub has recently adjusted limit, need to wait until it is set again! Current limit: {self.outputLimit:.0f}, new limit: {limit:.1f}')
                return self.outputLimit

        if limit < 0:
            limit = 0

        # If battery SoC reaches 0% during night, it has been observed that in the morning with first light, residual energy in the batteries gets released
        # Hub goes then into error and no charging occurs (probably deep discharge assumed by the battery).
        # Hence setting the output limit 0 if SoC 0%
        if self.electricLevel == 0:
            limit = 0
            log.info(f'Battery is empty! Disabling solarflow output, setting limit to {limit}')


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
        #self.outputLimitBuffer.add(limit)
        #limit = int(self.outputLimitBuffer.last())

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
            self.lastLimitTS = now
            log.info(f'{"[DRYRUN] " if self.dryrun else ""}Setting solarflow output limit to {limit:.1f}W')
        else:
            log.info(f'{"[DRYRUN] " if self.dryrun else ""}Not setting solarflow output limit to {limit:.1f}W as it is identical to current limit!')
        return limit

    def setBuzzer(self, state: bool):
        buzzer = {"properties": { "buzzerSwitch": 0 if not state else 1 }}
        self.client.publish(self.property_topic,json.dumps(buzzer))
        log.info(f'Turning hub buzzer {"ON" if state else "OFF"}')
    
    def setAutorecover(self, state: bool):
        autorecover = {"properties": { "autoRecover": 0 if not state else 1 }}
        self.client.publish(self.property_topic,json.dumps(autorecover))
        log.info(f'Turning hub bypass autorecover {"ON" if state else "OFF"}')

    def setBypass(self, state: bool):
        passmode = {"properties": { "passMode": 2 if state else 1 }}
        self.client.publish(self.property_topic,json.dumps(passmode))
        log.info(f'Turning hub bypass {"ON" if state else "OFF"}')
        if not state:
            self.bypass = state         # required for cases where we can't wait on confirmation on turning bypass off

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
    
    def getDischargePower(self):
        return self.packInputPower

    def getPreviousSolarInputPower(self):
        return self.solarInputValues.previous()

    def getSolarInputPower(self):
        return self.solarInputValues.last()

    def getElectricLevel(self):
        return self.electricLevel

    def getInverseMaxPower(self):
        return self.inverseMaxPower

    def getLimit(self):
        return self.outputLimit

    def getBypass(self):
        if self.productId == HUB2000:
            return self.bypass_mode == 2 or self.bypass
        else:
            return self.bypass
        
    def getCanDischarge(self):
        fullage = self.getLastFullBattery()
        can_discharge = (self.batteryTarget == "discharging") or (self.batteryTarget == "charging" and fullage < self.fullChargeInterval)
        return not(self.chargeThrough and (not can_discharge or fullage < 0))


