from paho.mqtt import client as mqtt_client
from functools import reduce
import logging
import math
import sys
from utils import TimewindowBuffer

yellow = "\x1b[33;20m"
reset = "\x1b[0m"
FORMAT = '%(asctime)s:%(levelname)s: %(message)s'
logging.basicConfig(stream=sys.stdout, level="INFO", format=FORMAT)
log = logging.getLogger("")

AC_LEGAL_LIMIT = 1000

class DTU:
    opts = {"base_topic":str, "sf_inverter_channels":list}
    limit_topic = ""
    limit_unit = ""

    def __init__(self, client: mqtt_client, base_topic:str, sf_inverter_channels:[]=[]):
        self.client = client
        self.base_topic = base_topic
        self.acPower = TimewindowBuffer(minutes=1)
        self.dcPower = 0
        self.channelsDCPower = []
        self.sf_inverter_channels = sf_inverter_channels
        self.limitAbsolute = 0
        self.limitAbsoluteBuffer = TimewindowBuffer(minutes=1)
        self.producing = True
        self.reachable = True
        self.dryrun = False
        self.limit_nonpersistent_absolute = f'{base_topic}/{self.limit_topic}'
    
    def __str__(self):
        chPower = "|".join([f'{v:>3.1f}' for v in self.channelsDCPower][1:])
        return ' '.join(f'{yellow}INV: \
                        AC:{self.acPower.qwavg():>3.1f}W, \
                        DC:{self.dcPower:>3.1f}W ({chPower}), \
                        L:{self.limitAbsolute:>3}W{reset}'.split())

    def subscribe(self, topics):
        topics.append(f'solarflow-hub/+/control/dryRun')
        for t in topics:
            self.client.subscribe(t)
            log.info(f'DTU subscribing: {t}')
    
    def ready(self):
        return len(self.channelsDCPower) > 0
    
    def updChannelPowerDC(self,channel:int, value:float):
        log.debug(f'Channel Power: {len(self.channelsDCPower)}/{channel} : {value}')
        if len(self.channelsDCPower) == channel:
            self.channelsDCPower.append(value)
        if len(self.channelsDCPower) > channel:
            if channel == 0:
                self.acPower.add(value)
            self.channelsDCPower[channel] = value
    
    def updTotalPowerDC(self, value:float):
        self.dcPower = value

    def updLimitAbsolute(self, value:float):
        self.limitAbsolute = value
    
    def updProducing(self, value):
        self.producing = bool(value)

    def updReachable(self, value):
        self.reachable = bool(value)

    def handleMsg(self, msg):
        if msg.topic.startswith(f'solarflow-hub') and msg.topic and msg.payload:
            metric = msg.topic.split('/')[-1]
            value = msg.payload.decode()
            match metric:
                case "dryRun":
                    self.setDryRun(value)

    def getACPower(self):
        return self.acPower.qwavg()
    
    def getDirectDCPowerValues(self) -> []:
        direct = []
        for idx,v in enumerate(self.channelsDCPower):
            if idx not in self.sf_inverter_channels and idx > 0:
                direct.append(v)
        return direct

    def getNrDirectChannels(self) -> int:
        return len(self.channelsDCPower)-1-len(self.sf_inverter_channels)
    
    def getDirectDCPower(self) -> float:
        return sum(self.getDirectDCPowerValues())

    def getNrTotalChannels(self) -> int:
        return len(self.channelsDCPower)-1
    
    def getHubDCPowerValues(self) -> []:
        hub = []
        for idx,v in enumerate(self.channelsDCPower):
            if idx in self.sf_inverter_channels and idx > 0:
                hub.append(v)
        return hub

    def getNrHubChannels(self) -> int:
        return len(self.sf_inverter_channels)
    
    def setDryRun(self,value):
        if type(value) == str:
            self.dryrun = value.upper() == 'ON'
        if type(value) == int:
            self.dryrun = bool(value)
        log.info(f'{self.__class__.__name__} set DryRun: {self.dryrun}')

    def setLimit(self, limit:int):
        # failsafe, never set the inverter limit to 0, keep a minimum
        # see: https://github.com/lumapu/ahoy/issues/1079
        limit = 10 if limit < 10 else int(limit)

        # make sure that the inverter limit (which is applied to all MPPTs output equally) matches globally for what we need
        #inv_limit = limit*(1/(len(self.sf_inverter_channels)/(len(self.channelsDCPower)-1)))
        inv_limit = limit*(len(self.channelsDCPower)-1)

        self.limitAbsoluteBuffer.add(inv_limit)
        # OpenDTU and AhoysDTU expect even limits?
        inv_limit = int(math.ceil(self.limitAbsoluteBuffer.wavg() / 2.) * 2)

        # failsafe: ensure that the inverter's AC output doesn't exceed acceptable legal limits
        # note this could mean that the inverter limit is still higher but it ensures that not too much power is generated
        if self.getACPower() > AC_LEGAL_LIMIT:
            # decrease inverter limit slowly
            inv_limit -= 2
        
        if self.limitAbsolute != inv_limit and self.reachable:
            (not self.dryrun) and self.client.publish(self.limit_nonpersistent_absolute,f'{inv_limit}{self.limit_unit}')
            #log.info(f'Setting inverter output limit to {inv_limit} W ({limit} x 1 / ({len(self.sf_inverter_channels)}/{len(self.channelsDCPower)-1})')
            log.info(f'{"[DRYRUN] " if self.dryrun else ""}Setting inverter output limit to {inv_limit}W (1 min moving average of {limit}W x {len(self.channelsDCPower)-1})')
        else:
            not self.reachable and log.info(f'{"[DRYRUN] " if self.dryrun else ""}Inverter is not reachable/down. Can\'t set limit')
            self.reachable and log.info(f'Not setting inverter output limit as it is identical to current limit!')

        return inv_limit
    

class OpenDTU(DTU):
    opts = {"base_topic":str ,"inverter_serial":int,"sf_inverter_channels":list}
    limit_topic = "cmd/limit_nonpersistent_absolute"
    limit_unit = ""

    def __init__(self, client: mqtt_client, base_topic:str, inverter_serial:int, sf_inverter_channels:[]=[]):
        super().__init__(client=client,base_topic=base_topic, sf_inverter_channels=sf_inverter_channels)
        self.base_topic = f'{base_topic}/{inverter_serial}'
        self.limit_nonpersistent_absolute = f'{self.base_topic}/{self.limit_topic}'
        log.info(f'Using {type(self).__name__}: Base topic: {self.base_topic}, Limit topic: {self.limit_nonpersistent_absolute}, SF Channels: {self.sf_inverter_channels}')

    def subscribe(self):
        topics = [
            f'{self.base_topic}/0/powerdc',
            f'{self.base_topic}/+/power',
            f'{self.base_topic}/status/producing',
            f'{self.base_topic}/status/reachable',
            f'{self.base_topic}/status/limit_absolute'
        ]
        super().subscribe(topics)

    def handleMsg(self, msg):
        if msg.topic.startswith(self.base_topic) and msg.payload:
            metric = msg.topic.split('/')[-1]
            value = float(msg.payload.decode())
            log.debug(f'DTU received {metric}:{value}')
            match metric:
                case "powerdc":
                    self.updTotalPowerDC(value)
                case "limit_absolute":
                    self.updLimitAbsolute(value)
                case "producing":
                    self.updProducing(value)
                case "reachable":
                    self.updReachable(value)
                case "power":
                    channel = int(msg.topic.split('/')[-2])
                    self.updChannelPowerDC(channel, value)
                case _:
                    log.warning(f'Ignoring inverter metric: {metric}')
        
        super().handleMsg(msg)

class AhoyDTU(DTU):
    opts = {"base_topic":str, "inverter_id":int, "inverter_name":str, "inverter_max_power":int, "sf_inverter_channels":list}
    limit_topic = "ctrl/limit"
    limit_unit = "W"

    def __init__(self, client: mqtt_client, base_topic:str, inverter_name:str, inverter_id:int, inverter_max_power:int, sf_inverter_channels:[]=[]):
        super().__init__(client=client,base_topic=base_topic, sf_inverter_channels=sf_inverter_channels)
        self.base_topic = f'{base_topic}'
        self.inverter_name = inverter_name
        self.inverter_max_power = inverter_max_power
        self.limit_nonpersistent_absolute = f'{self.base_topic}/{self.limit_topic}/{inverter_id}'
        log.info(f'Using {type(self).__name__}: Base topic: {self.base_topic}, Limit topic: {self.limit_nonpersistent_absolute}, SF Channels: {self.sf_inverter_channels}')

    def subscribe(self):
        topics = [
            f'{self.base_topic}/{self.inverter_name}/+/P_DC',
            f'{self.base_topic}/{self.inverter_name}/ch0/P_AC',
            f'{self.base_topic}/{self.inverter_name}/ch0/active_PowerLimit',
            f'{self.base_topic}/status'
        ]
        super().subscribe(topics)
    
    def handleMsg(self, msg):
        if msg.topic.startswith(self.base_topic) and msg.payload:
            metric = msg.topic.split('/')[-1]
            value = float(msg.payload.decode())
            log.debug(f'DTU received {metric}:{value}')
            match metric:
                case "P_AC":
                    self.updChannelPowerDC(0, value)
                case "status":
                    self.updProducing(value)
                case "active_PowerLimit":
                    self.updLimitAbsolute(self.inverter_max_power*value/100)
                case "P_DC":
                    channel = int(msg.topic.split('/')[-2][-1])
                    if channel == 0:
                        self.updTotalPowerDC(value)
                    else:
                        self.updChannelPowerDC(channel, value)
                case _:
                    log.warning(f'Ignoring inverter metric: {metric}')

        super().handleMsg(msg)