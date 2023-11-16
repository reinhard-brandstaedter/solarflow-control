import random, time, logging, sys, getopt, os
from datetime import datetime, timedelta
from functools import reduce
from paho.mqtt import client as mqtt_client
from astral import LocationInfo
from astral.sun import sun
import requests
from ip2geotools.databases.noncommercial import DbIpCity
import configparser
import math
from solarflow import Solarflow
import dtus
import smartmeters

FORMAT = '%(asctime)s:%(levelname)s: %(message)s'
logging.basicConfig(stream=sys.stdout, level="INFO", format=FORMAT)
log = logging.getLogger("")


'''
Customizing ConfigParser to allow dynamic conversion of array options
'''
config: configparser.ConfigParser
def listoption(option):
    return [int(x) for x in list(filter(lambda x: x.isdigit(), list(option)))]

def stroption(option):
    return option

def load_config():
    config = configparser.ConfigParser(converters={"str":stroption, "list":listoption})
    try:
        with open("config.ini","r") as cf:
            config.read_file(cf)
    except:
        log.error("No configuration file (config.ini) found in execution directory! Using environment variables.")
    return config

config = load_config()



'''
Configuration Options
'''
sf_device_id = config.get('solarflow', 'device_id', fallback=None) or os.environ.get('SF_DEVICE_ID',None)
sf_product_id = config.get('solarflow', 'product_id', fallback="73bkTV") or os.environ.get('SF_PRODUCT_ID',"73bkTV")
mqtt_user = config.get('mqtt', 'mqtt_user', fallback=None) or os.environ.get('MQTT_USER',None)
mqtt_pwd = config.get('mqtt', 'mqtt_pwd', fallback=None) or os.environ.get('MQTT_PWD',None)
mqtt_host = config.get('mqtt', 'mqtt_host', fallback=None) or os.environ.get('MQTT_HOST',None)
mqtt_port = config.getint('mqtt', 'mqtt_port', fallback=None) or os.environ.get('MQTT_PORT',1883)


DTU_TYPE =              config.get('global', 'dtu_type', fallback=None) \
                        or os.environ.get('DTU_TYPE',"OpenDTU")

SMT_TYPE =              config.get('global', 'smartmeter_type', fallback=None) \
                        or os.environ.get('SMARTMETER_TYPE',"Smartmeter")

# The amount of power that should be always reserved for charging, if available. Nothing will be fed to the house if less is produced
MIN_CHARGE_POWER =      config.getint('control', 'min_charge_power', fallback=None) \
                        or int(os.environ.get('MIN_CHARGE_POWER',125))          

# The maximum discharge level of the packSoc. Even if there is more demand it will not go beyond that
MAX_DISCHARGE_POWER =   config.getint('control', 'max_discharge_power', fallback=None) \
                        or int(os.environ.get('MAX_DISCHARGE_POWER',145))   

# battery SoC levels to consider the battry full or empty                                            
BATTERY_LOW =           config.getint('control', 'battery_low', fallback=None) \
                        or int(os.environ.get('BATTERY_LOW',10)) 
BATTERY_HIGH =          config.getint('control', 'battery_high', fallback=None) \
                        or int(os.environ.get('BATTERY_HIGH',98))

# the maximum allowed inverter output
MAX_INVERTER_LIMIT =    config.getint('control', 'max_inverter_limit', fallback=None) \
                        or int(os.environ.get('MAX_INVERTER_LIMIT',800))                                               
MAX_INVERTER_INPUT = MAX_INVERTER_LIMIT - MIN_CHARGE_POWER

# wether to limit the inverter or the solarflow hub
limit_inverter =        config.getboolean('control', 'limit_inverter', fallback=None) \
                        or bool(os.environ.get('LIMIT_INVERTER',False))

# interval for performing control steps
steering_interval =     config.getint('control', 'steering_interval', fallback=None) \
                        or int(os.environ.get('STEERING_INTERVAL',15))

#Adjustments possible to sunrise and sunset offset
SUNRISE_OFFSET =    config.getint('global', 'sunrise_offset', fallback=60) \
                        or int(os.environ.get('SUNRISE_OFFSET',60))                                               
SUNSET_OFFSET =    config.getint('global', 'sunset_offset', fallback=60) \
                        or int(os.environ.get('SUNSET_OFFSET',60))                                                                                             

# Location Info
LAT = config.getfloat('global', 'latitude', fallback=None) or float(os.environ.get('LATITUDE',0))
LNG = config.getfloat('global', 'longitude', fallback=None) or float(os.environ.get('LONGITUDE',0))
location: LocationInfo

# topic for the current household consumption (e.g. from smartmeter): int Watts
# if there is no single topic wich aggregates multiple phases (e.g. shelly 3EM) you can specify the topic in an array like this
# topic_house = shellies/shellyem3/emeter/1/power, shellies/shellyem3/emeter/2/power, shellies/shellyem3/emeter/3/power
#topic_house =       config.get('mqtt_telemetry_topics', 'topic_house', fallback=None) \
#                    or os.environ.get('TOPIC_HOUSE',None)
#topics_house =      [ t.strip() for t in topic_house.split(',')] if topic_house else []

client_id = f'solarflow-ctrl-{random.randint(0, 100)}'


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

def on_message(client, userdata, msg):
    #delegate message handling to hub,smartmeter, dtu
    smartmeter = userdata["smartmeter"]
    smartmeter.handleMsg(msg)
    hub = userdata["hub"]
    hub.handleMsg(msg)
    dtu = userdata["dtu"]
    dtu.handleMsg(msg)

    # handle own messages

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
    topics = [
            f'solarflow-hub/+/control/#'
    ]
    for t in topics:
        client.subscribe(t)
        log.info(f'SFControl subscribing: {t}')

# calculate the safe inverter limit for direct panels, to avoid output over legal limits
def getDirectPanelLimit(inv, hub, smt) -> int:
    direct_panel_power = inv.getDirectDCPower()
    if direct_panel_power < MAX_INVERTER_LIMIT:
        rise_factor = 1.2 if smt.getPower() > 0 else 1
        return math.ceil(max(inv.getDirectDCPowerValues())*rise_factor)
        #return math.ceil(max( max(inv.getHubDCPowerValues()), max(inv.getDirectDCPowerValues()) ))
    else:
        return int(MAX_INVERTER_LIMIT*(inv.getNrHubChannels()/inv.getNrTotalChannels()))

def getSFPowerLimit(hub, demand) -> int:
    hub_electricLevel = hub.getElectricLevel()
    hub_solarpower = hub.getSolarInputPower()
    now = datetime.now(tz=location.tzinfo)   
    s = sun(location.observer, date=now, tzinfo=location.timezone)
    sunrise = s['sunrise']
    sunset = s['sunset']
    path = ""

    if hub_solarpower > MIN_CHARGE_POWER:
        path += "1." 
        if hub_solarpower - MIN_CHARGE_POWER < MAX_DISCHARGE_POWER:
            path += "1."
            limit = min(demand,MAX_DISCHARGE_POWER)
        else:
            path += "2."
            limit = min(demand,hub_solarpower - MIN_CHARGE_POWER)
    if hub_solarpower <= MIN_CHARGE_POWER:  
        path += "2."
        sunrise_off = timedelta(minutes = SUNRISE_OFFSET)
        sunset_off = timedelta(minutes = SUNSET_OFFSET)
        if (now < (sunrise + sunrise_off) or now > sunset - sunset_off): 
            path += "1."                
            limit = min(demand,MAX_DISCHARGE_POWER)
        else:
            path += "2."                                     
            limit = 0
    log.info(f'Sun: {sunrise.strftime("%H:%M")} - {sunset.strftime("%H:%M")} - Solarflow limit: {limit:4.1f}W - Decision path: {path}')

    # get battery Soc at sunset/sunrise
    td = timedelta(minutes = 1)
    if now > sunset and now < sunset + td:
        hub.setSunsetSoC(hub_electricLevel)
    if now > sunrise and now < sunrise + td:
        hub.setSunriseSoC(hub_electricLevel)
        log.info(f'Good morning! We have consumed {hub.getNightConsumption()}% of the battery tonight!')

    return int(limit)


def limitHomeInput(client: mqtt_client):
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
    inv_acpower = inv.getACPower()
    demand = grid_power + inv_acpower
    limit = 0
    
    '''
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
    '''
    inv_limit = 0
    hub_limit = 0

    if limit_inverter:
        direct_panel_power = inv.getDirectDCPower()
        if demand <= direct_panel_power:
            # we can conver demand with direct panel power, just use all of it
            inv_limit = inv.setLimit(getDirectPanelLimit(inv,hub,smt))
            hub_limit = hub.setOutputLimit(0)
        if demand > direct_panel_power:
            # the remainder should come from SFHub, in case the remainder is greater than direct panels power
            # we need to make sure the inverter limit is set accordingly high

            remainder = demand-direct_panel_power
            log.info(f'Direct connected panels can\'t cover demand {direct_panel_power:4.1f}W/{demand:4.1f}W, trying to get rest ({remainder:4.1f}W) from SF.')

            # TODO: here we need to do all the calculation of how much we want to drain from solarflow
            # remainder must be calculated according to preferences of charging power, battery state,
            # day/nighttime input limites etc.
            log.info(f'Checking if Solarflow is willing to contribute {remainder:4.1f}W ...')
            remainder = getSFPowerLimit(hub,remainder)

            lmt = max(remainder,getDirectPanelLimit(inv,hub,smt))
            inv_limit = inv.setLimit(lmt)
            log.info(f'Setting hub limit ({lmt+10}W) bigger than inverter (channel) limit ({lmt}W) to avoid MPPT challenges.')
            hub_limit = hub.setOutputLimit(lmt+10)        # set SF limit higher than inverter limit to avoid MPPT challenges
    else:
        hub_limit = hub.setOutputLimit(limit)

    panels_dc = "|".join([f'{v:>2}' for v in inv.getDirectDCPowerValues()])
    hub_dc = "|".join([f'{v:>2}' for v in inv.getHubDCPowerValues()])
    log.info(' '.join(f'Demand: {demand:4.1f}W, \
             Panel DC: ({panels_dc}), \
             Hub DC: ({hub_dc}), \
             Inverter Limit: {inv_limit:4.1f}W, \
             Hub Limit: {hub_limit:4.1f}W'.split()))

def getOpts(configtype) -> dict:
    global config
    opts = {}
    for opt,opt_type in configtype.opts.items():
        t = opt_type.__name__
        converter = getattr(config,f'get{t}')
        opts.update({opt:opt_type(converter(configtype.__name__.lower(),opt))})
    return opts


def run():
    client = connect_mqtt()
    hub_opts = getOpts(Solarflow)
    hub = Solarflow(client=client,**hub_opts)

    dtuType = getattr(dtus, DTU_TYPE)
    dtu_opts = getOpts(dtuType)
    dtu = dtuType(client=client,**dtu_opts)

    smtType = getattr(smartmeters, SMT_TYPE)
    smt_opts = getOpts(smtType)
    smt = smtType(client=client,**smt_opts)

    client.user_data_set({"hub":hub, "dtu":dtu, "smartmeter":smt})
    client.on_message = on_message

    client.loop_start()

    while True:
        time.sleep(steering_interval)
        limitHomeInput(client)
        
    client.loop_stop()

def main(argv):
    global mqtt_host, mqtt_port, mqtt_user, mqtt_pwd
    global sf_device_id
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

    log.info(f'Limit via inverter: {limit_inverter}')

    log.info("Control Parameters:")
    log.info(f'  MIN_CHARGE_POWER = {MIN_CHARGE_POWER}')
    log.info(f'  MAX_DISCHARGE_LEVEL = {MAX_DISCHARGE_POWER}')
    log.info(f'  MAX_INVERTER_LIMIT = {MAX_INVERTER_LIMIT}')
    log.info(f'  MAX_INVERTER_INPUT = {MAX_INVERTER_INPUT}')
    log.info(f'  SUNRISE_OFFSET = {SUNRISE_OFFSET}')
    log.info(f'  SUNSET_OFFSET = {SUNSET_OFFSET}')


    loc = MyLocation()
    if not LNG and not LAT:
        coordinates = loc.getCoordinates()
        if loc is None:
            coordinates = (LAT,LNG)
            log.info(f'Geocoordinates: {coordinates}')
    else:
        coordinates = (LAT,LNG)

    # location info for determining sunrise/sunset
    location = LocationInfo(timezone='Europe/Berlin',latitude=coordinates[0], longitude=coordinates[1])

    run()

if __name__ == '__main__':
    main(sys.argv[1:])
