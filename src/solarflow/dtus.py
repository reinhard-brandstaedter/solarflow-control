from paho.mqtt import client as mqtt_client
from functools import reduce
from datetime import datetime
import logging
import sys
from utils import TimewindowBuffer

yellow = "\x1b[33;20m"
reset = "\x1b[0m"
FORMAT = "%(asctime)s:%(levelname)s: %(message)s"
logging.basicConfig(stream=sys.stdout, level="INFO", format=FORMAT)
log = logging.getLogger("")

AC_LEGAL_LIMIT = 1000
TRIGGER_DIFF = 30


class DTU:
    opts = {"base_topic": str, "sf_inverter_channels": list}
    limit_topic = ""
    limit_unit = ""

    def default_calllback(self):
        log.info("default callback")

    def __init__(
        self,
        client: mqtt_client,
        base_topic: str,
        sf_inverter_channels: [] = [],
        ac_limit: int = 800,
        callback=default_calllback,
    ):
        self.client = client
        self.base_topic = base_topic
        self.acPower = TimewindowBuffer(minutes=1)
        self.acLimit = ac_limit
        self.dcPower = TimewindowBuffer(minutes=1)
        self.channelsDCPower = []
        self.sf_inverter_channels = sf_inverter_channels
        self.limitAbsolute = 0
        self.limitRelative = -1
        self.maxPowerValues = []
        self.maxPower = -1
        self.limitAbsoluteBuffer = TimewindowBuffer(minutes=1)
        self.producing = True
        self.reachable = True
        self.dryrun = False
        self.limit_nonpersistent_absolute = f"{base_topic}/{self.limit_topic}"
        self.trigger_callback = callback
        self.last_trigger_value = 0
        self.efficiency = 95.0
        self.acUpdateTS = datetime.min
        self.lastLimitTimestamp = datetime.min

    def __str__(self):
        chPower = "|".join([f"{v:>3.1f}" for v in self.channelsDCPower][1:])
        return " ".join(
            f"{yellow}INV: \
                        AC:{self.getCurrentACPower():>3.1f}W, \
                        DC:{self.getCurrentDCPower():>3.1f}W ({chPower}), \
                        L:{self.limitAbsolute:>3.0f}W ({self.getChannelLimit():.1f}W/channel) [{self.maxPower:>3.0f}W]{reset}".split()
        )

    def subscribe(self, topics):
        topics.append(f"solarflow-hub/+/control/dryRun")
        for t in topics:
            self.client.subscribe(t)
            log.info(f"DTU subscribing: {t}")

    def ready(self):
        return len(self.channelsDCPower) > 0

    def updChannelPowerDC(self, channel: int, value: float):
        log.debug(f"Channel Power: {len(self.channelsDCPower)}/{channel} : {value}")
        if len(self.channelsDCPower) == channel:
            self.channelsDCPower.append(value)
        if len(self.channelsDCPower) > channel:
            if channel == 0:
                self.acUpdateTS = datetime.now()
                self.acPower.add(value)
            self.channelsDCPower[channel] = value

        previous = self.getPreviousACPower()

        if abs(previous - self.getCurrentACPower()) >= TRIGGER_DIFF:
            log.info(
                f"DTU triggers limit function: {previous} -> {self.getCurrentACPower()}: {'executed' if self.trigger_callback(self.client) else 'skipped'}"
            )
            self.last_trigger_value = self.getCurrentACPower()

    def updTotalPowerDC(self, value: float):
        self.dcPower.add(value)
        # self.dcPower = value

    def updEfficiency(self, value: float):
        self.efficiency = value

    def updLimitAbsolute(self, value: float):
        self.limitAbsolute = value

    def updLimitRelative(self, value: float):
        self.limitRelative = value
        # use 5 updates to determine maxPower of inverter
        if self.limitRelative > 0 and self.limitAbsolute > 0:
            power = int(round(self.limitAbsolute / self.limitRelative * 100, -2))
            if len(self.maxPowerValues) < 5:
                self.maxPowerValues.append(power)

            avg = (reduce(lambda x, y: x + y, self.maxPowerValues)) / len(self.maxPowerValues)
            if len(self.maxPowerValues) >= 5:
                if avg != self.maxPowerValues[0]:  # not stable yet, remove one
                    self.maxPowerValues.pop(0)
                if avg == self.maxPowerValues[0] and avg > 100 and self.maxPower != avg:
                    # we found the max power, no more searching
                    self.maxPower = avg
                    log.info(f"Determined inverter's max capacity: {self.maxPower}")

    def updProducing(self, value):
        self.producing = bool(value)

    def updReachable(self, value):
        self.reachable = bool(value)

    def handleMsg(self, msg):
        if msg.topic.startswith(f"solarflow-hub") and msg.topic and msg.payload:
            metric = msg.topic.split("/")[-1]
            value = msg.payload.decode()
            match metric:
                case "dryRun":
                    self.setDryRun(value)

    def getLimit(self):
        return self.limitAbsolute

    def getEfficiency(self):
        return self.efficiency

    def getACPower(self):
        return self.acPower.qwavg()

    def getCurrentACPower(self):
        return self.acPower.last()

    def getPreviousACPower(self):
        return self.acPower.previous()

    def getCurrentDCPower(self):
        return self.dcPower.last()

    def getDirectDCPowerValues(self) -> []:
        direct = []
        for idx, v in enumerate(self.channelsDCPower):
            if idx not in self.sf_inverter_channels and idx > 0:
                direct.append(v)
        # in case the inverter is not reachable or not producing, return 0
        if len(direct) == 0:
            return [0]
        return direct

    def getNrDirectChannels(self) -> int:
        return len(self.channelsDCPower) - 1 - len(self.sf_inverter_channels)

    def getDirectDCPower(self) -> float:
        return sum(self.getDirectDCPowerValues())

    def getDirectACPower(self) -> float:
        return self.getDirectDCPower() * (self.getEfficiency() / 100)

    def getNrTotalChannels(self) -> int:
        return len(self.channelsDCPower) - 1

    def getNrProducingChannels(self) -> int:
        return len(list(filter(lambda x: x > 0, self.channelsDCPower))) - 1

    def getHubDCPowerValues(self) -> []:
        hub = []
        for idx, v in enumerate(self.channelsDCPower):
            if idx in self.sf_inverter_channels and idx > 0:
                hub.append(v)
        return hub

    def getHubDCPower(self) -> float:
        return sum(self.getHubDCPowerValues())

    def getHubACPower(self) -> float:
        return self.getHubDCPower() * (self.getEfficiency() / 100)

    def getNrHubChannels(self) -> int:
        return len(self.sf_inverter_channels)

    def setDryRun(self, value):
        if type(value) == str:
            self.dryrun = value.upper() == "ON"
        if type(value) == int:
            self.dryrun = bool(value)
        log.info(f"{self.__class__.__name__} set DryRun: {self.dryrun}")

    def isWithin(self, a, b, range: int):
        return b - range < a < b + range

    def getChannelLimit(self) -> int:
        if len(self.channelsDCPower) > 1:
            return self.getLimit() / (len(self.channelsDCPower) - 1)
        else:
            return 0

    def getACLimit(self) -> int:
        # if hub is not contributing to AC output, we can calculate the AC limit based on the max direct channels
        log.info(
            f"Over limit: {self.getCurrentACPower():.0f}W, {self.getNrProducingChannels()} producing channels: {self.getDirectACPower():.0f}W, from hub channels: {self.getHubACPower():.0f}W"
        )

        if self.getHubACPower() == 0:
            return int((self.acLimit / self.getNrDirectChannels()) * self.getNrTotalChannels())
        else:
            return int((self.acLimit / self.getNrProducingChannels()) * self.getNrTotalChannels())

    def hasPendingUpdate(self) -> bool:
        log.info(
            f"Pending Update: {self.lastLimitTimestamp > self.acUpdateTS} - Last limit update: {self.lastLimitTimestamp}, AC update: {self.acUpdateTS}"
        )
        return self.lastLimitTimestamp > self.acUpdateTS

    def setLimit(self, limit: int):
        # failsafe, never set the inverter limit to 0, keep a minimum
        # see: https://github.com/lumapu/ahoy/issues/1079
        limit = 10 if limit < 10 else int(limit)

        # make sure that the inverter limit (which is applied to all MPPTs output equally) matches globally for what we need
        # inv_limit = limit*(1/(len(self.sf_inverter_channels)/(len(self.channelsDCPower)-1)))
        inv_limit = limit * (len(self.channelsDCPower) - 1)

        self.limitAbsoluteBuffer.add(inv_limit)
        # OpenDTU and AhoysDTU expect even limits?
        #### inv_limit = int(math.ceil(self.limitAbsoluteBuffer.qwavg() / 2.) * 2)

        # Avoid setting limit higher than 150% of inverter capacity
        inv_limit = self.maxPower * 1.125 if (inv_limit > self.maxPower * 1.125 and self.maxPower > 0) else inv_limit

        # it could be that maxPower has not yet been detected resulting in a zero limit
        inv_limit = 10 if inv_limit < 10 else int(inv_limit)

        withinRange = 6
        # failsafe: ensure that the inverter's AC output doesn't exceed acceptable legal limits
        # note this could mean that the inverter limit is still higher but it ensures that not too much power is generated

        # acceptable overage on AC power, keep limit where it is
        if self.getCurrentACPower() > self.acLimit and self.isWithin(self.getCurrentACPower(), self.acLimit, 20):
            smt = self.client._userdata["smartmeter"]
            # hub = self.client._userdata['hub']
            smt_power = smt.getPower() - smt.zero_offset
            if smt_power > 0:
                inv_limit = self.limitAbsolute
            else:
                inv_limit = self.getACLimit()
            withinRange = 0
            log.info(
                f"Current inverter AC output ({self.getCurrentACPower():.0f}W) is within acceptable overage ({self.acLimit:.0f}W +/- 20W), {'keeping limit at' if smt_power > 0 else 'but less demand, setting limit to'} {inv_limit:.0f}W"
            )

        if self.getCurrentACPower() > self.acLimit and not self.isWithin(self.getCurrentACPower(), self.acLimit, 20):
            # decrease inverter limit slowly
            # inv_limit = self.limitAbsolute - 8
            inv_limit = self.getACLimit()
            withinRange = 0
            log.info(
                f"Current inverter AC output ({self.getCurrentACPower():.0f}W) is higher than configured limit ({self.acLimit:.0f}W), reducing limit to {inv_limit:.0f}W"
            )

        # failsafe: if the current AC output is close to the AC limit do not increase the invert limit too much
        if self.getCurrentACPower() < self.acLimit and self.isWithin(self.getCurrentACPower(), self.acLimit, 10):
            # only increase inverter limit a little bit
            inv_limit = self.limitAbsolute + 2
            withinRange = 0
            log.info(
                f"Current inverter AC output ({self.getCurrentACPower():.0f}W) is close to the configured AC output limit ({self.acLimit:.0f}W), slow limit increase to {inv_limit:.0f}W"
            )

        # if self.limitAbsolute != inv_limit and self.reachable:
        if not self.isWithin(inv_limit, self.limitAbsolute, withinRange) and self.reachable:
            self.lastLimitTimestamp = datetime.now()
            (not self.dryrun) and self.client.publish(
                self.limit_nonpersistent_absolute, f"{inv_limit}{self.limit_unit}"
            )
            # log.info(f'Setting inverter output limit to {inv_limit} W ({limit} x 1 / ({len(self.sf_inverter_channels)}/{len(self.channelsDCPower)-1})')
            log.info(
                f"{'[DRYRUN] ' if self.dryrun else ''}Setting inverter output limit to {inv_limit}W (1 min moving average of {limit}W x {len(self.channelsDCPower) - 1})"
            )
        else:
            not self.reachable and log.info(
                f"{'[DRYRUN] ' if self.dryrun else ''}Inverter is not reachable/down. Can't set limit"
            )
            self.reachable and log.info(f"Not setting inverter output limit as it is identical to current limit!")

        return inv_limit


class OpenDTU(DTU):
    opts = {"base_topic": str, "inverter_serial": str, "sf_inverter_channels": list}
    limit_topic = "cmd/limit_nonpersistent_absolute"
    limit_unit = ""

    def __init__(
        self,
        client: mqtt_client,
        base_topic: str,
        inverter_serial: str,
        sf_inverter_channels: [] = [],
        ac_limit: int = 800,
        callback=DTU.default_calllback,
    ):
        super().__init__(
            client=client,
            base_topic=base_topic,
            sf_inverter_channels=sf_inverter_channels,
            ac_limit=ac_limit,
            callback=callback,
        )
        self.base_topic = f"{base_topic}/{inverter_serial}"
        self.limit_nonpersistent_absolute = f"{self.base_topic}/{self.limit_topic}"
        log.info(
            f"Using {type(self).__name__}: Base topic: {self.base_topic}, Limit topic: {self.limit_nonpersistent_absolute}, SF Channels: {self.sf_inverter_channels}, AC Limit: {self.acLimit}"
        )

    def subscribe(self):
        topics = [
            f"{self.base_topic}/0/powerdc",
            f"{self.base_topic}/0/efficiency",
            f"{self.base_topic}/+/power",
            f"{self.base_topic}/status/producing",
            f"{self.base_topic}/status/reachable",
            f"{self.base_topic}/status/limit_absolute",
            f"{self.base_topic}/status/limit_relative",
        ]
        super().subscribe(topics)

    def handleMsg(self, msg):
        if msg.topic.startswith(self.base_topic) and msg.payload:
            metric = msg.topic.split("/")[-1]
            value = float(msg.payload.decode())
            log.debug(f"DTU received {metric}:{value}")
            match metric:
                case "powerdc":
                    self.updTotalPowerDC(value)
                case "efficiency":
                    self.updEfficiency(value)
                case "limit_absolute":
                    self.updLimitAbsolute(value)
                case "limit_relative":
                    self.updLimitRelative(value)
                case "producing":
                    self.updProducing(value)
                case "reachable":
                    self.updReachable(value)
                case "power":
                    channel = int(msg.topic.split("/")[-2])
                    self.updChannelPowerDC(channel, value)
                case _:
                    log.warning(f"Ignoring inverter metric: {metric}")

        super().handleMsg(msg)


class AhoyDTU(DTU):
    opts = {
        "base_topic": str,
        "inverter_id": str,
        "inverter_name": str,
        "inverter_max_power": int,
        "sf_inverter_channels": list,
    }
    limit_topic = "ctrl/limit"
    limit_unit = "W"

    def __init__(
        self,
        client: mqtt_client,
        base_topic: str,
        inverter_name: str,
        inverter_id: str,
        inverter_max_power: int,
        sf_inverter_channels: [] = [],
        ac_limit: int = 800,
        callback=DTU.default_calllback,
    ):
        super().__init__(
            client=client,
            base_topic=base_topic,
            sf_inverter_channels=sf_inverter_channels,
            ac_limit=ac_limit,
            callback=callback,
        )
        self.base_topic = f"{base_topic}"
        self.inverter_name = inverter_name
        self.inverter_max_power = self.maxPower = inverter_max_power
        self.limit_nonpersistent_absolute = f"{self.base_topic}/{self.limit_topic}/{inverter_id}"
        log.info(
            f"Using {type(self).__name__}: Base topic: {self.base_topic}, Limit topic: {self.limit_nonpersistent_absolute}, SF Channels: {self.sf_inverter_channels}"
        )

    def subscribe(self):
        topics = [
            f"{self.base_topic}/{self.inverter_name}/+/P_DC",
            f"{self.base_topic}/{self.inverter_name}/ch0/P_AC",
            f"{self.base_topic}/{self.inverter_name}/ch0/active_PowerLimit",
            f"{self.base_topic}/{self.inverter_name}/ch0/Efficiency",
            f"{self.base_topic}/status",
        ]
        super().subscribe(topics)

    def handleMsg(self, msg):
        if msg.topic.startswith(self.base_topic) and msg.payload:
            metric = msg.topic.split("/")[-1]
            value = float(msg.payload.decode())
            log.debug(f"DTU received {metric}:{value}")
            match metric:
                case "P_AC":
                    self.updChannelPowerDC(0, value)
                case "Efficiency":
                    self.updEfficiency(value)
                case "status":
                    self.updProducing(value)
                case "active_PowerLimit":
                    self.updLimitRelative(value)
                    self.updLimitAbsolute(self.inverter_max_power * value / 100)
                case "P_DC":
                    channel = int(msg.topic.split("/")[-2][-1])
                    if channel == 0:
                        self.updTotalPowerDC(value)
                    else:
                        self.updChannelPowerDC(channel, value)
                case _:
                    log.warning(f"Ignoring inverter metric: {metric}")

        super().handleMsg(msg)
