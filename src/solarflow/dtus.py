from paho.mqtt import client as mqtt_client
from datetime import datetime, timedelta
from functools import reduce
import logging
import json
import sys
from utils import TimewindowBuffer

yellow = "\x1b[33;20m"
reset = "\x1b[0m"
FORMAT = '%(asctime)s:%(levelname)s: %(message)s'
logging.basicConfig(stream=sys.stdout, level="INFO", format=FORMAT)
log = logging.getLogger("")


class Inverter:

    def __init__(self, client: mqtt_client, base_topic:str, sfinputs:int, mppts:int, window:int = 5):
        self.client = client
        self.base_topic = base_topic
        self.acPower = TimewindowBuffer()
        self.dcPower = 0
        self.channelsDCPower = []
        self.limitAbsolute = 0
        self.producing = True
        pass
    
    def __str__(self):
        chPower = "|".join([f'{v:>3.1f}' for v in self.channelsDCPower][1:])
        return ' '.join(f'{yellow}INV: \
                        AC:{self.acPower.avg():>3.1f}W, \
                        DC:{self.dcPower:>3.1f}W ({chPower}), \
                        L:{self.limitAbsolute:>3}W{reset}'.split())

    def subscribe(self):
        topics = [
            f'{self.base_topic}/0/powerdc',
            f'{self.base_topic}/+/power',
            f'{self.base_topic}/status/producing',
            f'{self.base_topic}/status/limit_absolute'
        ]
        for t in topics:
            self.client.subscribe(t)
    
    def updChannelPowerDC(self,channel:int, value:float):
        log.info(f'Channel Power: {len(self.channelsDCPower)}/{channel} : {value}')
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
                case "power":
                    channel = int(msg.topic.split('/')[-2])
                    self.updChannelPowerDC(channel, value)
                case _:
                    log.warning(f'Ignoring inverter metric: {metric}')

    def setLimit(self, value:int):
        # make sure that the inverter limit (which is applied to all MPPTs output equally) matches globally for what we need
        inv_limit = limit*(1/(INVERTER_INPUTS_USED/INVERTER_MPPTS))
        unit = "" if isOpenDTU(topic_limit_non_persistent) else "W"
        client.publish(topic_limit_non_persistent,f'{inv_limit}{unit}')
        log.info(f'Setting inverter output limit to {inv_limit} W ({limit} x 1 / ({INVERTER_INPUTS_USED}/{INVERTER_MPPTS})')
        return inv_limit