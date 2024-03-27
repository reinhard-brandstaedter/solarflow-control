import random, time, logging, sys, getopt, os
from datetime import datetime, timedelta
from functools import reduce
from paho.mqtt import client as mqtt_client
from astral import LocationInfo
from astral.sun import sun
import requests
#import geoip2.database
#from ip2geotools.databases.noncommercial import DbIpCity
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

# how frequently we allow triggering the limit function (seconds)
TRIGGER_RATE_LIMIT = 10

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

# this controls the internal calculation of limited growth for setting inverter limits
INVERTER_START_LIMIT = 5

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

lastTriggerTS:datetime = None

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
        lat = lon = 0.0
        try:
            result = requests.get(f'http://ip-api.com/json/{self.ip}')
            response = result.json()
            log.info(response)
            log.info(f'IP Address: {self.ip}')
            log.info(f'Location: {response["city"]}, {response["regionName"]}, {response["country"]}')
            log.info(f'Coordinates: (Lat: {response["lat"]}, Lng: {response["lon"]}')
            lat = response["lat"]
            lon = response["lon"]
        except:
            log.error(f'Can\'t determine location from my IP {self.ip}. Location detection failed, no accurate sunrise/sunset detection possible')

        return (lat,lon)

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

def limitedRise(x) -> int:
    rise = MAX_INVERTER_LIMIT-(MAX_INVERTER_LIMIT-INVERTER_START_LIMIT)*math.exp(-0.0025*x)
    log.info(f'Adjusting inverter limit from {x:.1f}W to {rise:.1f}W')
    return int(rise)


# calculate the safe inverter limit for direct panels, to avoid output over legal limits
def getDirectPanelLimit(inv, hub, smt) -> int:
    # if hub is in bypass mode we can treat it just like a direct panel
    direct_panel_power = inv.getDirectDCPower() + inv.getHubDCPower() if hub.getBypass() else 0
    if direct_panel_power < MAX_INVERTER_LIMIT:
        dc_values = inv.getDirectDCPowerValues() + inv.getHubDCPowerValues() if hub.getBypass() else inv.getDirectDCPowerValues()
        return math.ceil(max(dc_values)) if smt.getPower() < 0 else limitedRise(max(dc_values))
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

    if hub_solarpower - demand > MIN_CHARGE_POWER:
        path += "1." 
        if hub_solarpower - MIN_CHARGE_POWER < MAX_DISCHARGE_POWER:
            path += "1."
            limit = min(demand,MAX_DISCHARGE_POWER)
        else:
            path += "2."
            limit = min(demand,hub_solarpower - MIN_CHARGE_POWER)
    if hub_solarpower - demand <= MIN_CHARGE_POWER:  
        path += "2."
        sunrise_off = timedelta(minutes = SUNRISE_OFFSET)
        sunset_off = timedelta(minutes = SUNSET_OFFSET)
        if (now < (sunrise + sunrise_off) or now > sunset - sunset_off): 
            path += "1."                
            limit = min(demand,MAX_DISCHARGE_POWER)
        else:
            path += "2."                                     
            limit = 0 if hub_solarpower - MIN_CHARGE_POWER < 0 else hub_solarpower - MIN_CHARGE_POWER
            # slower charging at the end, as it often happens to jump, waiting for bypass
            # Issue #140 as the hubs SoC reporting is somewhat inconsistent at the top end, remove slow charging
            # limit = int(hub_solarpower/2) if hub_electricLevel > 95 else limit
    if demand < 0:
        limit = 0

    # if the hub is currently in bypass mode, we do not want to limit the output in any way
    # Note: this seems to have changed with FW 2.0.33 as before in bypass mode the limit was ignored, now it isn't
    if hub.bypass:
        #limit = MAX_INVERTER_LIMIT
        limit = limitedRise(hub.getSolarInputPower())

    # get battery Soc at sunset/sunrise
    td = timedelta(minutes = 1)
    if now > sunset and now < sunset + td:
        hub.setSunsetSoC(hub_electricLevel)
    if now > sunrise and now < sunrise + td:
        hub.setSunriseSoC(hub_electricLevel)
        log.info(f'Good morning! We have consumed {hub.getNightConsumption()}% of the battery tonight!')

    log.info(f'Based on time, solarpower ({hub_solarpower:4.1f}W) minimum charge power ({MIN_CHARGE_POWER}W) and bypass state ({hub.bypass}), hub could contribute {limit:4.1f}W - Decision path: {path}')
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

    inv_limit = 0
    hub_limit = 0

    direct_panel_power = inv.getDirectDCPower()
    # consider DC power of panels below 10W as 0 to avoid fluctuation in very low light.
    direct_panel_power = 0 if direct_panel_power < 10 else direct_panel_power

    grid_power = smt.getPredictedPower()
    inv_acpower = inv.getPredictedACPower()
    # if direct panels are producing more than what is needed we are ok to feed in
    if direct_panel_power > 0:
        demand = grid_power + inv_acpower if (grid_power > 0) else 0 
    # if direct panels are not producing (night), we ensure not to feed into the grid from battery
    else:
        demand = grid_power + inv_acpower
    
    if demand < direct_panel_power and direct_panel_power > 0:
        # we can conver demand with direct panel power, just use all of it
        inv_limit = inv.setLimit(getDirectPanelLimit(inv,hub,smt))
        hub_limit = hub.setOutputLimit(0)
    if demand >= direct_panel_power or demand < 0:
        # the remainder should come from SFHub, in case the remainder is greater than direct panels power
        # we need to make sure the inverter limit is set accordingly high
        
        if demand > 0:
            remainder = demand-direct_panel_power
            log.info(f'Direct connected panels ({direct_panel_power:.1f}W) can\'t cover demand ({demand:.1f}W), trying to get rest from hub.')
        else:
            remainder = demand + inv.getACPower()
            log.info(f'Grid feed in: {demand:.1f}W from {"battery, lowering limit to avoid it." if direct_panel_power == 0 and inv.getHubDCPower() > 0 else "direct panels or other source."}')
        
        log.info(f'Checking if Solarflow is willing to contribute {remainder:.1f}W ...')
        sf_contribution = getSFPowerLimit(hub,remainder)

        # if the hub's contribution (per channel) is larger than what the direct panels max is delivering (night, low light)
        # then we can open the hub to max limit and use the inverter to limit it's output (more precise)
        if sf_contribution/inv.getNrHubChannels() >= max(inv.getDirectDCPowerValues()):
            log.info(f'Hub should contribute more ({sf_contribution:.1f}W) than what we currently get from panels ({direct_panel_power:.1f}W), we will use the inverter for fast/precise limiting!')
            hub_limit = hub.setOutputLimit(hub.getInverseMaxPower())
            direct_limit = sf_contribution/inv.getNrHubChannels()
        else:
            hub_limit = hub.setOutputLimit(sf_contribution)
            log.info(f'Solarflow is willing to contribute {hub_limit:.1f}W!')
            direct_limit = getDirectPanelLimit(inv,hub,smt)
            log.info(f'Direct connected panel limit is {direct_limit}W.')

        limit = direct_limit

        if hub_limit > direct_limit > hub_limit - 10:
            limit = hub_limit - 10
        if direct_limit < hub_limit - 10 and hub_limit < hub.getInverseMaxPower():
            limit = hub_limit - 10
  
        inv_limit = inv.setLimit(limit)

        #lmt = max(remainder,getDirectPanelLimit(inv,hub,smt))
        #inv_limit = inv.setLimit(lmt)
        #log.info(f'Setting hub limit ({remainder}W) bigger than inverter (channel) limit ({direct_limit}W) to avoid MPPT challenges.')
        #hub_limit = hub.setOutputLimit(lmt+10)        # set SF limit higher than inverter limit to avoid MPPT challenges

    panels_dc = "|".join([f'{v:>2}' for v in inv.getDirectDCPowerValues()])
    hub_dc = "|".join([f'{v:>2}' for v in inv.getHubDCPowerValues()])

    now = datetime.now(tz=location.tzinfo)   
    s = sun(location.observer, date=now, tzinfo=location.timezone)
    sunrise = s['sunrise']
    sunset = s['sunset']

    log.info(' '.join(f'Sun: {sunrise.strftime("%H:%M")} - {sunset.strftime("%H:%M")} \
             Demand: {demand:.1f}W, \
             Panel DC: ({panels_dc}), \
             Hub DC: ({hub_dc}), \
             Inverter Limit: {inv_limit:.1f}W, \
             Hub Limit: {hub_limit:.1f}W'.split()))

def getOpts(configtype) -> dict:
    global config
    opts = {}
    for opt,opt_type in configtype.opts.items():
        t = opt_type.__name__
        converter = getattr(config,f'get{t}')
        opts.update({opt:opt_type(converter(configtype.__name__.lower(),opt))})
    return opts

def limit_callback(client: mqtt_client):
    global lastTriggerTS
    #log.info("Smartmeter Callback!")
    now = datetime.now()
    if lastTriggerTS:
        elapsed = now - lastTriggerTS
        # ensure the limit function is not called too often (avoid flooding DTUs)
        if elapsed.total_seconds() >= TRIGGER_RATE_LIMIT:
            lastTriggerTS = now
            limitHomeInput(client)
        else:
            log.info(f'Rate limit on trigger function, last call was only {elapsed.total_seconds():.1f}s ago!')
    else:
        lastTriggerTS = now
        limitHomeInput(client)


def run():
    client = connect_mqtt()
    hub_opts = getOpts(Solarflow)
    hub = Solarflow(client=client,**hub_opts)

    dtuType = getattr(dtus, DTU_TYPE)
    dtu_opts = getOpts(dtuType)
    dtu = dtuType(client=client,ac_limit=MAX_INVERTER_LIMIT,callback=limit_callback,**dtu_opts)

    smtType = getattr(smartmeters, SMT_TYPE)
    smt_opts = getOpts(smtType)
    smt = smtType(client=client,callback=limit_callback, **smt_opts)

    client.user_data_set({"hub":hub, "dtu":dtu, "smartmeter":smt})
    client.on_message = on_message

    #client.loop_start()
    client.loop_forever()

    #while True:
    #    time.sleep(steering_interval)
    #    limitHomeInput(client)
        
    #client.loop_stop()

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
