from paho.mqtt import client as mqtt_client
from datetime import datetime
import logging
import json
import sys
import pathlib
from jinja2 import Environment, FileSystemLoader, DebugUndefined
from utils import TimewindowBuffer, RepeatedTimer, str2bool

red = "\x1b[31;20m"
reset = "\x1b[0m"
FORMAT = '%(asctime)s:%(levelname)s: %(message)s'
logging.basicConfig(stream=sys.stdout, level="INFO", format=FORMAT)
log = logging.getLogger("")

TRIGGER_DIFF = 30

HUB1200 = "73bkTV"
HUB2000 = "A8yh63"

BATTERY_TARGET_IDLE        = "idle"
BATTERY_TARGET_CHARGING    = "charging"
BATTERY_TARGET_DISCHARGING = "discharging"

# according to https://github.com/epicRE/zendure_ble
INVERTER_BRAND = {0: 'Other', 1: 'Hoymiles', 2: 'Enphase', 3: 'APsystems', 4: 'Anker', 5: 'Deye', 6: 'BossWerk', 7: 'Tsun'}

BATTERY_TARGET_IDLE        = "idle"
BATTERY_TARGET_CHARGING    = "charging"
BATTERY_TARGET_DISCHARGING = "discharging"

# according to https://github.com/epicRE/zendure_ble
INVERTER_BRAND = {0: 'Other', 1: 'Hoymiles', 2: 'Enphase', 3: 'APsystems', 4: 'Anker', 5: 'Deye', 6: 'BossWerk', 7: 'Tsun'}


class Solarflow:
    opts = {"product_id":str, "device_id":str ,"full_charge_interval":int, "control_bypass":bool, "control_soc":bool, "disable_full_discharge":bool}

    def default_calllback(self):
        log.info("default callback")

    def __init__(self, client: mqtt_client, product_id:str, device_id:str, full_charge_interval:int, control_bypass:bool = False, control_soc:bool = False, disable_full_discharge:bool = False, callback = default_calllback):
        self.client = client
        self.productId = product_id
        self.deviceId = device_id
        self.fullChargeInterval = full_charge_interval
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
        self.allowFullCycle = not disable_full_discharge

        self.batteryTargetSoCMax = -1
        self.batteryTargetSoCMin = -1
        self.batteryLow = -1
        self.batteryHigh = -1
        self.control_soc = control_soc    # wether we control the soc levels
        self.chargeThroughRequested = False

        self.property_topic = f'iot/{self.productId}/{self.deviceId}/properties/write'
        self.chargeThrough = False
        self.chargeThroughStage = BATTERY_TARGET_IDLE
        self.dryrun = False
        self.sunriseSoC = None
        self.sunsetSoC = None
        self.nightConsumption = 100
        self.trigger_callback = callback

        self.lastLimitTS = None

        self.updater = RepeatedTimer(60, self.update)
        self.haconfig = RepeatedTimer(180, self.pushHomeassistantConfig)
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
                for index, (serial,v) in enumerate(self.batteriesVol.items()):
                    hacfg = template.render(product_id=self.productId, device_id=self.deviceId, fw_version=self.fwVersion, battery_serial=serial, battery_index=index+1)
                    if serial != "none":
                        self.client.publish(f'homeassistant/{cfg_type}/solarflow-hub-{self.deviceId}-{serial}-{cfg_name}/config',hacfg,retain=True)
            else:
                hacfg = template.render(product_id=self.productId, device_id=self.deviceId, fw_version=self.fwVersion)
                self.client.publish(f'homeassistant/{cfg_type}/solarflow-hub-{self.deviceId}-{cfg_name}/config',hacfg,retain=True)
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
        batteryTarget = self.batteryTarget

        # handle full battery
        if value == 100:
            batteryTarget = BATTERY_TARGET_DISCHARGING

            if self.batteryTarget == BATTERY_TARGET_CHARGING:
                log.info(f'Battery is full: {self.electricLevel} => {value}')

            if self.chargeThrough:
                # if allowed to run full cycle, change to discharge,now
                if self.allowFullCycle:
                    self.setChargeThroughStage(BATTERY_TARGET_DISCHARGING)
                # otherwise, we are done
                else:
                    self.setChargeThrough(False)

            self.lastFullTS = datetime.now()
            self.client.publish(f'solarflow-hub/{self.deviceId}/control/lastFullTimestamp',int(datetime.timestamp(self.lastFullTS)),retain=True)
        # handle user given max SoC
        elif value >= self.batteryHigh and not self.chargeThrough:
            batteryTarget = BATTERY_TARGET_DISCHARGING

            if self.batteryTarget == BATTERY_TARGET_CHARGING:
                log.info(f'Battery maximum charge level reached: {self.electricLevel} => {value}')

        # handle empty battery
        if value == 0:
            batteryTarget = BATTERY_TARGET_CHARGING

            if self.batteryTarget == BATTERY_TARGET_DISCHARGING:
                log.info(f'Battery is empty: {self.electricLevel} => {value}')

            if self.chargeThrough:
                self.setChargeThrough(False)

            self.lastEmptyTS = datetime.now()
            self.client.publish(f'solarflow-hub/{self.deviceId}/control/lastEmptyTimestamp',int(datetime.timestamp(self.lastEmptyTS)),retain=True)
        # handle user given min SoC
        elif value <= self.batteryLow and not self.chargeThrough:
            batteryTarget = BATTERY_TARGET_CHARGING

            if self.batteryTarget == BATTERY_TARGET_DISCHARGING:
                log.info(f'Battery minimum charge level reached: {self.electricLevel} => {value}')

        # process changes
        if batteryTarget != self.batteryTarget:
            # only enable bypass once, otherwise it would get enabled again and again
            if self.control_bypass and self.allow_bypass and batteryTarget == BATTERY_TARGET_DISCHARGING and self.batteryTarget == BATTERY_TARGET_CHARGING:
                log.info(f'Bypass control, turning on bypass!')
                self.setBypass(True)
                self.allow_bypass = False

            self.client.publish(f'solarflow-hub/{self.deviceId}/control/batteryTarget',batteryTarget,retain=True)

        self.electricLevel = value

    def processRequestedChargeThrough(self) -> bool:
        if self.chargeThroughRequested and self.batteryTargetSoCMax >= 0 and self.batteryTargetSoCMin >= 0:
            self.chargeThroughRequested = False
            self.setChargeThrough(True)
            return True

        return False

    def updBatteryTargetSoCMax(self, value: int):
        self.batteryTargetSoCMax = value / 10
        self.processRequestedChargeThrough()

    def updBatteryTargetSoCMin(self, value: int):
        self.batteryTargetSoCMin = value / 10
        self.processRequestedChargeThrough()

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
        newfwVersion = f'{major}.{minor}.{build}'
        if self.fwVersion != newfwVersion: # publish ha templates on new version
            self.fwVersion = newfwVersion
            self.pushHomeassistantConfig()

    def updByPass(self, value:int):
        self.bypass = bool(value)

    def updByPassMode(self, value: int):
        # it seems when the battery is completely depleted SF resets the bypass to auto, so we enforce it manual off when this happens
        if self.control_bypass and value == 0 and not self.bypass:
            self.setBypass(False)
            value = 1

        self.bypass_mode = value

    def allowBypass(self, allow):
        self.allow_bypass = allow

    def setChargeThrough(self, value):
        chargeThrough = str2bool(value)

        # chargeThrough can only be used if control_soc is enabled via configuration
        # **OR**
        # if SoC levels configured in battery are correct
        if chargeThrough and not self.control_soc:
            # if no levels have not been read, wait for then and redo evaluation
            if self.batteryTargetSoCMax < 0 or self.batteryTargetSoCMin < 0:
                log.info(f'We are not allowed to control SoC levels and the values read from battery are not available, yet. Waiting for update to re-check conditions')
                self.chargeThroughRequested = True
                return

            # batteryTargetSoCMax has to be setup correctly
            if self.batteryTargetSoCMax < 100:
                log.info(f'Impossible to set charge through! We are not permitted to change maximum target SoC and solarflow has limit configured to {self.batteryTargetSoCMax}% but we expected 100%!')
                return

            # if we shall do a full cycle, batteryTargetSoCMin has to be setup correctly
            if self.allowFullCycle and self.batteryTargetSoCMin > 0:
                log.info(f'Impossible to do full charge through cycle! We are not permitted to change minimum target SoC and solarflow has limit configured to {self.batteryTargetSoCMin}% but we expect 0%!')
                return

        # in case of setups with no direct panels connected to inverter it is necessary to turn on the inverter as it is likely offline now
        inv = self.client._userdata['dtu']
        if (not inv.ready()) and self.getOutputHomePower() == 0:
            # this will power on the inverter so that control can resume from an interrupted charge-through
            self.setOutputLimit(30)

        if self.chargeThrough != chargeThrough:
            log.info(f'Set ChargeThrough: {self.chargeThrough} => {chargeThrough}')
            self.setChargeThroughStage(BATTERY_TARGET_CHARGING if chargeThrough else BATTERY_TARGET_IDLE)
            self.client.publish(f'solarflow-hub/{self.deviceId}/control/chargeThrough','ON' if chargeThrough else 'OFF')

        self.chargeThrough = chargeThrough

    def setChargeThroughStage(self,stage):
        if self.chargeThroughStage == stage:
            return

        log.info(f'Updating charge through stage: {self.chargeThroughStage} => {stage}')
        batteryHigh = 100 if stage in [BATTERY_TARGET_CHARGING, BATTERY_TARGET_DISCHARGING] else self.batteryHigh
        batteryLow = 0 if stage == BATTERY_TARGET_DISCHARGING and self.allowFullCycle else self.batteryLow
        self.client.publish(f'solarflow-hub/{self.deviceId}/control/chargeThroughState', stage)
        self.setBatteryHighSoC(batteryHigh, True)
        self.setBatteryLowSoC(batteryLow, True)
        self.chargeThroughStage = stage

    def setControlBypass(self, value):
        self.control_bypass = str2bool(value)
        log.info(f'Taking over bypass control: {self.control_bypass}')

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
                case "controlBypass":
                    self.setControlBypass(value)
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
                case "socSet":
                    self.updBatteryTargetSoCMax(int(value))
                case "minSoc":
                    self.updBatteryTargetSoCMin(int(value))
                case "chargeThroughState":
                    pass
                case _:
                    if not "control" in msg.topic:
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
        if self.chargeThrough and limit > 0 and self.batteryTarget == BATTERY_TARGET_CHARGING:
            log.info(f'Charge-Through is active! To ensure it is fully charged at least every {self.fullChargeInterval}hrs not discharging now!')
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
        return self.bypass

    def getCanDischarge(self):
        fullage = self.getLastFullBattery()
        can_discharge = (self.batteryTarget == BATTERY_TARGET_DISCHARGING) or (self.batteryTarget == BATTERY_TARGET_CHARGING and fullage < self.fullChargeInterval)
        return not(self.chargeThrough and (not can_discharge or fullage < 0))

    def setBatteryHighSoC(self, level:int, temporary:bool=False) -> int:
        level = min(max(level, 40), 100)
        if not temporary:
            self.batteryHigh = level

        if not self.control_soc:
            return self.batteryHigh

        payload = {"properties": { "socSet": level * 10 }}
        self.client.publish(self.property_topic,json.dumps(payload))
        log.info(f'Setting maximum charge level to {level}%')
        return level

    def setBatteryLowSoC(self, level:int, temporary:bool=False) -> int:
        level = min(max(level, 0), 60)
        if not temporary:
            self.batteryLow = level

        if not self.control_soc:
            return self.batteryLow

        payload = {"properties": { "minSoc": level * 10 }}
        self.client.publish(self.property_topic,json.dumps(payload))
        log.info(f'Setting minimum charge level to {level}%')
        return level

    def checkChargeThrough(self, daylight:float = 0.0) -> bool:
        log.info(f'Checking conditions for charge through with expexted daylight of {daylight:.1f} hours')
        fullage = self.getLastFullBattery()
        fullage_today = fullage + daylight
        # check if we should enable charge through
        if fullage < 0 or fullage > self.fullChargeInterval or fullage_today > self.fullChargeInterval:
            log.info(f'Battery hasn\'t fully charged for {fullage:.1f} hours! To ensure it is fully charged at least every {self.fullChargeInterval} hours chargeing through now!')
            self.setChargeThrough(True)

        return self.chargeThrough

    def setInverseMaxPower(self, value:int) -> int:
        if value <= 100:
            value = 100
        payload = {"properties": { "inverseMaxPower": value }}
        self.client.publish(self.property_topic,json.dumps(payload))
        self.inverseMaxPower = value
        return value

    def setPvBrand(self, brand:int = 1):
        brand_str = INVERTER_BRAND.get(brand,f'Unkown [{brand}]')
        payload = {"properties": { "pvBrand": brand }}
        self.client.publish(self.property_topic,json.dumps(payload))
        log.info(f'Setting inverter brand to {brand_str}')
