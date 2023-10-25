import random, json, time, logging, sys, getopt, os
from datetime import datetime, timedelta
from functools import reduce
from paho.mqtt import client as mqtt_client
from astral import LocationInfo
from astral.sun import sun
import requests
from ip2geotools.databases.noncommercial import DbIpCity
import configparser
import click
import math
from solarflow import SolarflowHub
import dtus
from smartmeters import Smartmeter

FORMAT = '%(asctime)s:%(levelname)s: %(message)s'
logging.basicConfig(stream=sys.stdout, level="INFO", format=FORMAT)
log = logging.getLogger("")

def isOpenDTU(ctrl_topic) -> bool:
    return "ctrl/limit" not in ctrl_topic

config: configparser.ConfigParser
def load_config():
    config = configparser.ConfigParser()
    try:
        with open("config.ini","r") as cf:
            config.read_file(cf)
    except:
        log.error("No configuration file (config.ini) found in execution directory! Using environment variables.")
    return config

config = load_config()

sf_device_id = config.get('solarflow', 'sf_device_id', fallback=None) or os.environ.get('SF_DEVICE_ID',None)
sf_product_id = config.get('solarflow', 'sf_product_id', fallback="73bkTV") or os.environ.get('SF_PRODUCT_ID',"73bkTV")
mqtt_user = config.get('local', 'mqtt_user', fallback=None) or os.environ.get('MQTT_USER',None)
mqtt_pwd = config.get('local', 'mqtt_pwd', fallback=None) or os.environ.get('MQTT_PWD',None)
mqtt_host = config.get('local', 'mqtt_host', fallback=None) or os.environ.get('MQTT_HOST',None)
mqtt_port = config.getint('local', 'mqtt_port', fallback=None) or os.environ.get('MQTT_PORT',1883)


DTU_TYPE =              config.get('control', 'dtu_type', fallback=None) \
                        or os.environ.get('DTU_TYPE',"OpenDTU")   

# The amount of power that should be always reserved for charging, if available. Nothing will be fed to the house if less is produced
MIN_CHARGE_LEVEL =      config.getint('control', 'min_charge_level', fallback=None) \
                        or int(os.environ.get('MIN_CHARGE_LEVEL',125))          

# The maximum discharge level of the packSoc. Even if there is more demand it will not go beyond that
MAX_DISCHARGE_LEVEL =   config.getint('control', 'max_discharge_level', fallback=None) \
                        or int(os.environ.get('MAX_DISCHARGE_LEVEL',145))   

# The minimum state of charge of the battery to start discharging also throughout the day
DAY_DISCHARGE_SOC =     config.getint('control', 'day_discharge_soc', fallback=None) \
                        or int(os.environ.get('DAY_DISCHARGE_SOC',50))    

CHARGE_THROUGH_THRESHOLD =  config.getint('control', 'charge_through_threshold', fallback=None) \
                        or int(os.environ.get('CHARGE_THROUGH_THRESHOLD',60))      

# if we produce more than what we need we can feed that much to the grid
OVERAGE_LIMIT =         config.getint('control', 'overage_limit', fallback=None) \
                        or int(os.environ.get('OVERAGE_LIMIT',15))  

# battery SoC levels to consider the battry full or empty                                            
BATTERY_LOW =           config.getint('control', 'battery_low', fallback=None) \
                        or int(os.environ.get('BATTERY_LOW',10)) 
BATTERY_HIGH =          config.getint('control', 'battery_high', fallback=None) \
                        or int(os.environ.get('BATTERY_HIGH',98))

# the maximum allowed inverter output
MAX_INVERTER_LIMIT =    config.getint('control', 'max_inverter_limit', fallback=None) \
                        or int(os.environ.get('MAX_INVERTER_LIMIT',800))                                               
MAX_INVERTER_INPUT = MAX_INVERTER_LIMIT - MIN_CHARGE_LEVEL

 # the number of inverter inputs or mppts. SF only uses 1 or 2 so when limiting we need to adjust for that
INVERTER_MPPTS =        config.getint('control', 'inverter_mppts', fallback=None) \
                        or int(os.environ.get('INVERTER_MPPTS',4))

# how many Inverter input channels are used by Solarflow              
INVERTER_INPUTS_USED =  config.getint('control', 'inverter_sf_inputs_used', fallback=None) \
                        or int(os.environ.get('INVERTER_SF_INPUTS_USED',2))

# the delta between two consecutive measurements on houshold usage to consider it a fast rise or drop   
FAST_CHANGE_OFFSET =    config.getint('control', 'fast_change_offset', fallback=None) \
                        or int(os.environ.get('FAST_CHANGE_OFFSET',200))

# wether to limit the inverter or the solarflow hub
limit_inverter =        config.getboolean('control', 'limit_inverter', fallback=None) \
                        or bool(os.environ.get('LIMIT_INVERTER',False))

# Location Info
LAT = config.getfloat('local', 'latitude', fallback=None) or float(os.environ.get('LATITUDE',0))
LNG = config.getfloat('local', 'longitude', fallback=None) or float(os.environ.get('LONGITUDE',0))
location: LocationInfo

# topic for the current household consumption (e.g. from smartmeter): int Watts
# if there is no single topic wich aggregates multiple phases (e.g. shelly 3EM) you can specify the topic in an array like this
# topic_house = shellies/shellyem3/emeter/1/power, shellies/shellyem3/emeter/2/power, shellies/shellyem3/emeter/3/power
topic_house =       config.get('mqtt_telemetry_topics', 'topic_house', fallback=None) \
                    or os.environ.get('TOPIC_HOUSE',None)
topics_house =      [ t.strip() for t in topic_house.split(',')] if topic_house else []

# topic for the microinverter input to home (e.g. from OpenDTU, AhouyDTU)
topic_acinput =     config.get('mqtt_telemetry_topics', 'topic_acinput', fallback=None) \
                    or os.environ.get('TOPIC_ACINPUT',"solar/ac/power")

# topics for panels power which feed directly to inverter
topic_direct_panel =    config.get('mqtt_telemetry_topics', 'topic_direct_panel', fallback=None) \
                        or os.environ.get('TOPIC_DIRECT_PANEL',None)
topics_direct_panel =   [ t.strip() for t in topic_direct_panel.split(',') ] if topic_direct_panel else []


# topics for telemetry read from Solarflow Hub                                                       
topic_solarflow_solarinput = config.get('mqtt_telemetry_topics', 'topic_solarflow_solarinput', fallback="solarflow-hub/telemetry/solarInputPower")
topic_solarflow_electriclevel = config.get('mqtt_telemetry_topics', 'topic_solarflow_electriclevel', fallback="solarflow-hub/telemetry/electricLevel")
topic_solarflow_outputpack = config.get('mqtt_telemetry_topics', 'topic_solarflow_outputpack', fallback="solarflow-hub/telemetry/outputPackPower")
topic_solarflow_packinput = config.get('mqtt_telemetry_topics', 'topic_solarflow_packinput', fallback="solarflow-hub/telemetry/packInputPower")
topic_solarflow_outputhome = config.get('mqtt_telemetry_topics', 'topic_solarflow_outputhome', fallback="solarflow-hub/telemetry/outputHomePower")
topic_solarflow_maxtemp = config.get('mqtt_telemetry_topics', 'topic_solarflow_maxtemp', fallback="solarflow-hub/telemetry/batteries/+/maxTemp")
topic_solarflow_battery_soclevel = config.get('mqtt_telemetry_topics', 'topic_solarflow_battery_soclevel', fallback="solarflow-hub/telemetry/batteries/+/socLevel")

# topic to control the Solarflow Hub (used to set output limit)
topic_limit_solarflow = f'iot/{sf_product_id}/{sf_device_id}/properties/write'

# topic for controlling the inverter limit
topic_limit_non_persistent =    config.get('mqtt_telemetry_topics', 'topic_limit_non_persistent', fallback=None) \
                                or os.environ.get('TOPIC_LIMIT_OPENDTU',"solar/116491132532/cmd/limit_nonpersistent_absolute")

client_id = f'solarflow-ctrl-{random.randint(0, 100)}'

# sliding average windows for telemetry data, to remove spikes and drops
sf_window =     config.getint('control', 'sf_window', fallback=None) \
                or int(os.environ.get('SF_WINDOW',5))
solarflow_values = [0]*sf_window
sm_window =     config.getint('control', 'sm_window', fallback=None) \
                or int(os.environ.get('SM_WINDOW',5))
smartmeter_values = [0]*sm_window
inv_window =    config.getint('control', 'inv_window', fallback=None) \
                or int(os.environ.get('INV_WINDOW',5))
inverter_values = [0]*inv_window
limit_window =  config.getint('control', 'limit_window', fallback=None) \
                or int(os.environ.get('LIMIT_WINDOW',5))
limit_values =  [0]*limit_window

packSoc = -1
charging = 0
home = 0
maxtemp = 1000
batterySocs = {"dummy": -1}
phase_values = {}
direct_panel_values = {}
direct_panel_power = -1
last_solar_input_update = datetime.now()
charge_through = False


class MyLocation:
    ip = ""

    def __init__(self) -> None:
        try:
            result = requests.get("https://ifconfig.me")
            self.ip = result.text
        except:
            log.error(f'Can\'t determine my IP. Auto-location detection failed')
            return None
        
        
    def getCoordinates(self) -> tuple:
        res = DbIpCity.get(self.ip, api_key="free")
        log.info(f"IP Address: {res.ip_address}")
        log.info(f"Location: {res.city}, {res.region}, {res.country}")
        log.info(f"Coordinates: (Lat: {res.latitude}, Lng: {res.longitude})")
        return (res.latitude,res.longitude)

def on_inverter_update(msg):
    global inverter_values
    if len(inverter_values) >= inv_window:
        inverter_values.pop(0)
    inverter_values.append(float(msg))

def on_direct_panel(msg):
    global direct_panel_values
    global direct_panel_power
    payload = json.loads(msg.payload.decode())
    topic = msg.topic

    if type(payload) is float or type(payload) is int:
        value = payload
        direct_panel_values.update({topic:value})
        direct_panel_power = sum(direct_panel_values.values())


# this needs to be configured for different smartmeter readers (Hichi, PowerOpti, Shelly)
# Shelly 3EM reports one metric per phase and doesn't aggregate, so we need to do this by ourselves
def on_smartmeter_update(client,msg):
    global smartmeter_values
    global limit_values
    global phase_values
    payload = json.loads(msg.payload.decode())
    topic = msg.topic

    if len(smartmeter_values) >= sm_window:
        smartmeter_values.pop(0)

    if type(payload) is float or type(payload) is int:
        value = payload
        phase_values.update({topic:value})
        value = int(sum(phase_values.values()))
    else:
        # special case if current power is json format  (Hichi reader) 
        value = int(payload["Power"]["Power_curr"])
        
    smartmeter_values.append(value)
    # also report value to MQTT (for statuspage)
    client.publish("solarflow-hub/control/homeUsage",value)

    if len(smartmeter_values) >= sm_window:    
        tail = reduce(lambda a,b: a+b, smartmeter_values[:-2])/(len(smartmeter_values)-2)
        head = reduce(lambda a,b: a+b, smartmeter_values[-2:])/(len(smartmeter_values)-2)
        # detect fast drop in demand
        if tail > head + FAST_CHANGE_OFFSET:
            log.info(f'Detected a fast drop in demand, enabling accelerated adjustment!')
            smartmeter_values = smartmeter_values[-2:]
            limit_values = []

        # detect fast rise in demand
        if tail + FAST_CHANGE_OFFSET < head:
            log.info(f'Detected a fast rise in demand, enabling accelerated adjustment!')
            smartmeter_values = smartmeter_values[-2:]
            limit_values = []

def on_message(client, userdata, msg):
    smartmeter = userdata["smartmeter"]
    smartmeter.handleMsg(msg)
    hub = userdata["hub"]
    hub.handleMsg(msg)
    dtu = userdata["dtu"]
    dtu.handleMsg(msg)


def on_connect(client, userdata, flags, rc):
    if rc == 0:
        log.info("Connected to MQTT Broker!")
        hub = client._userdata['hub']
        hub.subscribe()
        hub.setBuzzer(False)
        inv = client._userdata['dtu']
        inv.subscribe()
        smt = client._userdata['smartmeter']
        smt.subscribe()
    else:
        log.error("Failed to connect, return code %d\n", rc)

def on_disconnect(client, userdata, rc):
    if rc == 0:
        log.info("Disconnected from MQTT Broker on porpose!")
    else:
        log.error("Disconnected from MQTT broker!")

def connect_mqtt() -> mqtt_client:
    client = mqtt_client.Client(client_id)
    if mqtt_user is not None and mqtt_pwd is not None:
        client.username_pw_set(mqtt_user, mqtt_pwd)
    client.on_connect = on_connect
    client.on_disconnect = on_disconnect
    client.connect(mqtt_host, mqtt_port)
    return client

def subscribe(client: mqtt_client):
    client.on_message = on_message

# this ensures that the buzzerSwitch (audio confirmation upon commands) is off
def turnOffBuzzer(client: mqtt_client):
    buzzer = {"properties": { "buzzerSwitch": 0 }}
    client.publish(topic_limit_solarflow,json.dumps(buzzer))

# this can be used to completely disable charging (e.g. on low packSoc temperature)
def checkCharging(client: mqtt_client):
    global maxtemp
    socset = {"properties": { "socSet": 0 }}
    if maxtemp < 1000:
        log.warning(f'The maximum measured battery temperature is {maxtemp/100}. Disabling charging to avoid damage! Please reset manually once temperature is high enough!')
        client.publish(topic_limit_solarflow,json.dumps(socset))

# limit the output to home setting on the Solarflow hub
def limitSolarflow(client: mqtt_client, limit):
    # currently the hub doesn't support single steps for limits below 100
    # to get a fine granular steering at this level we need to fall back to the inverter limit
    # if controlling the inverter is not possible we should stick to either 0 or 100W
    if limit <= 100:
        #limitInverter(client,limit)
        #log.info(f'The output limit would be below 100W ({limit}W). Would need to limit the inverter to match it precisely')
        m = divmod(limit,30)[0]
        r = divmod(limit,30)[1]
        limit = 30 * m + 30 * (r // 15)
    else:
        pass
        #limitInverter(client,MAX_INVERTER_LIMIT)

    outputlimit = {"properties": { "outputLimit": limit }}
    client.publish(topic_limit_solarflow,json.dumps(outputlimit))
    log.info(f'Setting solarflow output limit to {limit} W')
    return limit

# set the limit on the inverter (when using inverter only mode)
def limitInverter(client: mqtt_client, limit):
    # make sure that the inverter limit (which is applied to all MPPTs output equally) matches globally for what we need
    inv_limit = limit*(1/(INVERTER_INPUTS_USED/INVERTER_MPPTS))
    unit = "" if isOpenDTU(topic_limit_non_persistent) else "W"
    client.publish(topic_limit_non_persistent,f'{inv_limit}{unit}')
    log.info(f'Setting inverter output limit to {inv_limit} W ({limit} x 1 / ({INVERTER_INPUTS_USED}/{INVERTER_MPPTS})')
    return inv_limit

# calculate the safe inverter limit for direct panels, to avoid output over legal limits
def getDirectPanelLimit(inv, hub) -> int:
    direct_panel_power = inv.getDirectDCPower()
    if direct_panel_power < MAX_INVERTER_LIMIT:
        return math.ceil(max( max(inv.getHubDCPowerValues()), max(inv.getDirectDCPowerValues()) ))
    else:
        return int(MAX_INVERTER_LIMIT*(INVERTER_INPUTS_USED/INVERTER_MPPTS))

def limitHomeInput(client: mqtt_client):
    global home
    global packSoc, batterySocs
    global smartmeter_values, solarflow_values, inverter_values
    global charge_through
    global location

    hub = client._userdata['hub']
    log.info(f'{hub}')
    inv = client._userdata['dtu']
    log.info(f'{inv}')
    smt = client._userdata['smartmeter']
    log.info(f'{smt}')

    # ensure we have data to work on
    if not(hub.ready() and inv.ready() and smt.ready()):
        return
        
    grid_power = smt.getPower()
    hub_solarpower = hub.getSolarInputPower()
    hub_homepower = hub.getOutputHomePower()
    inv_acpower = inv.getACPower()
    demand = grid_power + inv_acpower
    limit = 0

    now = datetime.now(tz=location.tzinfo)   
    s = sun(location.observer, date=now, tzinfo=location.timezone)
    sunrise = s['sunrise']
    sunset = s['sunset']

    hub_electricLevel = hub.getElectricLevel()
    hub_solarpower = hub.getSolarInputPower()

    # now all the logic when/how to set limit
    path = ""
    if  hub_electricLevel > BATTERY_HIGH:
        path = "1."
        if hub_solarpower > 0 and hub_solarpower > MIN_CHARGE_LEVEL:    # producing more than what is needed => only take what is needed and charge, giving a bit extra to demand
            path += "1."
            limit = min(demand + OVERAGE_LIMIT,hub_solarpower + OVERAGE_LIMIT)
        if hub_solarpower > 0 and hub_solarpower <= MIN_CHARGE_LEVEL:   # producing less than the minimum charge level 
            path += "2."
            if now <= sunrise or now > sunset:
                path += "1"                         # in the morning keep using packSoc
                limit = MAX_DISCHARGE_LEVEL
            else:         
                path += "2"                                      
                limit = hub_solarpower + OVERAGE_LIMIT              # everything goes to the house throughout the day, in case SF regulated solarinput down we need to demand a bit more stepwise
        if hub_solarpower <= 0:
            path += "3"                                     
            limit = min(demand,MAX_DISCHARGE_LEVEL)             # not producing and demand is less than discharge limit => discharge with what is needed but limit to MAX
    elif hub_electricLevel <= BATTERY_LOW:
        path = "2."                                         
        limit = 0                                               # battery is at low stage, stop discharging
    else:
        path = "3."
        if hub_solarpower > MIN_CHARGE_LEVEL:
            path += "1." 
            if hub_solarpower - MIN_CHARGE_LEVEL < MAX_DISCHARGE_LEVEL and hub_electricLevel > DAY_DISCHARGE_SOC:
                path += "1."
                limit = min(demand,MAX_DISCHARGE_LEVEL)
            else:
                path += "2."
                limit = min(demand,hub_solarpower - MIN_CHARGE_LEVEL)      # give charging precedence
        if hub_solarpower <= MIN_CHARGE_LEVEL:  
            path += "2."                                                # producing less than the minimum charge level 
            sun_offset = timedelta(minutes = 60)
            if (now < (sunrise + sun_offset) or now > sunset - sun_offset) or hub_electricLevel > DAY_DISCHARGE_SOC: 
                path += "1"                
                limit = min(demand,MAX_DISCHARGE_LEVEL)                 # in the morning keep using battery, in the evening start using battery
                td = timedelta(minutes = 5)
                if charge_through or (now > sunset and now < sunset + td and hub_electricLevel < CHARGE_THROUGH_THRESHOLD):      # charge through mode, do not discharge when battery is low at sunset
                    not charge_through and log.info(f'Entering charge-through mode (Threshold: {CHARGE_THROUGH_THRESHOLD}, SoC: {hub_electricLevel}): no discharging')
                    charge_through = True
                    limit = 0
            else:
                path += "2"                                     
                limit = 0
                charge_through and log.info(f'Leaving charge-through mode (Threshold: {CHARGE_THROUGH_THRESHOLD}, SoC: {hub_electricLevel})')
                charge_through = False
                    

    if len(limit_values) >= limit_window:
        limit_values.pop(0)
    limit_values.append(0 if limit<0 else limit)                # to recover faster from negative demands
    limit = int(reduce(lambda a,b: a+b, limit_values)/len(limit_values))

    lm = ",".join([f'{v:>4.1f}' for v in limit_values])

    panels_dc = "|".join([f'{v:>2}' for v in inv.getDirectDCPowerValues()])
    hub_dc = "|".join([f'{v:>2}' for v in inv.getHubDCPowerValues()])
    log.info(' '.join(f'Sun: {sunrise.strftime("%H:%M")} - {sunset.strftime("%H:%M")}, \
             Demand: {demand:4.1f}W, \
             Panel DC: ({panels_dc}), \
             Hub DC: ({hub_dc}), \
             => Limit: {limit}W - [{lm}] - decisionpath: {path}'.split()))

    if limit_inverter:
        # if we get more from the direct connected panels than what we need, we limit the SF hub
        direct_panel_power = inv.getDirectDCPower()
        if direct_panel_power*0.9 <= limit <= direct_panel_power*1.1 or (limit == 0 and direct_panel_power > 10):
            hub.setOutputLimit(0)
            inv.setLimit(getDirectPanelLimit(inv,hub))
        # get the difference from SF if we need more than what the direct connected panels can deliver
        else:
            if direct_panel_power > 10:
                hub.setOutputLimit(limit-direct_panel_power)
            else:
                hub.setOutputLimit(MAX_INVERTER_INPUT)
            inv.setLimit(limit)
    else:
        hub.setOutputLimit(limit)

def run():
    client = connect_mqtt()
    hub = SolarflowHub(device_id=sf_device_id,client=client)
    #hub.subscribe()
    #hub.setBuzzer(False)
    opendtu_opts = {
        "base_topic":"solar",
        "inverter_no":116491132532,
        "sfchannels": [3]
    }
    ahoydtu_opts = {
        "base_topic":"solar",
        "inverter_id": 1,
        "inverter_name": "HM-800",
        "inverter_max_power": 800,
        "sfchannels": [3]
    }
    dtuType = getattr(dtus, DTU_TYPE)
    #dtu = dtuType(client=client,base_topic="solar", inverter_no=116491132532, sfchannels=[3])
    dtu = dtuType(client=client,**opendtu_opts)
    smt = Smartmeter(client=client,base_topic="tele/E220/SENSOR")
    #smt.subscribe()
    client.user_data_set({"hub":hub, "dtu":dtu, "smartmeter":smt})
    client.on_message = on_message

    client.loop_start()

    while True:
        time.sleep(5)
        limitHomeInput(client)
        
    client.loop_stop()

def main(argv):
    global mqtt_host, mqtt_port, mqtt_user, mqtt_pwd
    global sf_device_id
    global topic_limit_solarflow
    global limit_inverter
    global location
    opts, args = getopt.getopt(argv,"hb:p:u:s:d:",["broker=","port=","user=","password="])
    for opt, arg in opts:
        if opt == '-h':
            log.info('solarflow-control.py -b <MQTT Broker Host> -p <MQTT Broker Port>')
            sys.exit()
        elif opt in ("-b", "--broker"):
            mqtt_host = arg
        elif opt in ("-p", "--port"):
            mqtt_port = arg
        elif opt in ("-u", "--user"):
            mqtt_user = arg
        elif opt in ("-s", "--password"):
            mqtt_pwd = arg
        elif opt in ("-d", "--device"):
            sf_device_id = arg

    if mqtt_host is None:
        log.error("You need to provide a local MQTT broker (environment variable MQTT_HOST or option --broker)!")
        sys.exit(0)
    else:
        log.info(f'MQTT Host: {mqtt_host}:{mqtt_port}')

    if mqtt_user is None or mqtt_pwd is None:
        log.info(f'MQTT User is not set, assuming authentication not needed')
    else:
        log.info(f'MQTT User: {mqtt_user}/{mqtt_pwd}')

    if sf_device_id is None:
        log.error(f'You need to provide a SF_DEVICE_ID (environment variable SF_DEVICE_ID or option --device)!')
        sys.exit()
    else:
        log.info(f'Solarflow Hub: {sf_product_id}/{sf_device_id}')
        topic_limit_solarflow = f'iot/{sf_product_id}/{sf_device_id}/properties/write'

    log.info("MQTT telemetry topics used (make sure they are populated)!:")
    log.info(f'  House Consumption: {topic_house}')
    log.info(f'  Inverter AC input (TOPIC_HOUSE): {topic_acinput}')
    log.info(f'  Solarflow Solar Input (TOPIC_ACINPUT): {topic_solarflow_solarinput}')
    log.info(f'  Solarflow Output to home: {topic_solarflow_outputhome}')
    log.info(f'  Solarflow Battery Level: {topic_solarflow_electriclevel}')
    log.info(f'  Solarflow Battery Charging: {topic_solarflow_outputpack}')
    log.info(f'Topic to limit Solarflow Output: {topic_limit_solarflow}')
    log.info(f'Topic to limit Inverter Output: {topic_limit_non_persistent}')
    log.info(f'Limit via inverter: {limit_inverter}')

    log.info("Control Parameters:")
    log.info(f'  MIN_CHARGE_LEVEL = {MIN_CHARGE_LEVEL}')
    log.info(f'  MAX_DISCHARGE_LEVEL = {MAX_DISCHARGE_LEVEL}')
    log.info(f'  BATTERY_LOW = {BATTERY_LOW}')
    log.info(f'  BATTERY_HIGH = {BATTERY_HIGH}')
    log.info(f'  DAY_DISCHARGE_SOC = {DAY_DISCHARGE_SOC}')
    log.info(f'  CHARGE_THROUGH_THRESHOLD = {CHARGE_THROUGH_THRESHOLD}')
    log.info(f'  OVERAGE_LIMIT = {OVERAGE_LIMIT}')
    log.info(f'  MAX_INVERTER_LIMIT = {MAX_INVERTER_LIMIT}')
    log.info(f'  MAX_INVERTER_INPUT = {MAX_INVERTER_INPUT}')
    log.info(f'  INVERTER_MPPTS = {INVERTER_MPPTS}')
    log.info(f'  INVERTER_INPUTS_USED = {INVERTER_INPUTS_USED}')
    log.info(f'  FAST_CHANGE_OFFSET = {FAST_CHANGE_OFFSET}')
    

    loc = MyLocation()
    coordinates = loc.getCoordinates()
    if loc is None:
        coordinates = (LAT,LNG)
        log.info(f'Geocoordinates: {coordinates}')


    # location info for determining sunrise/sunset
    location = LocationInfo(timezone='Europe/Berlin',latitude=coordinates[0], longitude=coordinates[1])

    run()

if __name__ == '__main__':
    main(sys.argv[1:])
