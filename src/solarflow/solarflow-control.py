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
from utils import RepeatedTimer

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
                        or int(os.environ.get('MIN_CHARGE_POWER',0))          

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
    def getCoordinates(self) -> tuple:
        lat = lon = 0.0
        try:
            result = requests.get('http://ip-api.com/json/') # call without IP uses my IP
            response = result.json()
            log.info(f'IP Address: {response["query"]}')
            log.info(f'Location: {response["city"]}, {response["regionName"]}, {response["country"]}')
            log.info(f'Coordinates: (Lat: {response["lat"]}, Lng: {response["lon"]}')
            lat = response["lat"]
            lon = response["lon"]
        except Exception as e:
            log.error(f'Can\'t determine location from my IP. Location detection failed, no accurate sunrise/sunset detection possible',e.args)

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
        if hub.control_bypass:
            hub.setBypass(False)
            hub.setAutorecover(False)
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
    rise = MAX_INVERTER_LIMIT-(MAX_INVERTER_LIMIT-INVERTER_START_LIMIT)*math.exp(-MAX_INVERTER_LIMIT/100000*x)
    log.info(f'Adjusting inverter limit from {x:.1f}W to {rise:.1f}W')
    return int(rise)


# calculate the safe inverter limit for direct panels, to avoid output over legal limits
def getDirectPanelLimit(inv, hub, smt) -> int:
    # if hub is in bypass mode we can treat it just like a direct panel
    direct_panel_power = inv.getDirectACPower() + (inv.getHubACPower() if hub.getBypass() else 0)
    if direct_panel_power < MAX_INVERTER_LIMIT:
        dc_values = (inv.getDirectDCPowerValues() + inv.getHubDCPowerValues()) if hub.getBypass() else inv.getDirectDCPowerValues()
        return math.ceil(max(dc_values) * (inv.getEfficiency()/100)) if smt.getPower() - smt.zero_offset < 0 else limitedRise(max(dc_values) * (inv.getEfficiency()/100))
    else:
        return int(MAX_INVERTER_LIMIT*(inv.getNrHubChannels()/inv.getNrProducingChannels()))

def getSFPowerLimit(hub, demand) -> int:
    hub_electricLevel = hub.getElectricLevel()
    hub_solarpower = hub.getSolarInputPower()
    now = datetime.now(tz=location.tzinfo)   
    s = sun(location.observer, date=now, tzinfo=location.timezone)
    sunrise = s['sunrise']
    sunset = s['sunset']
    path = ""

    sunrise_off = timedelta(minutes = SUNRISE_OFFSET)
    sunset_off = timedelta(minutes = SUNSET_OFFSET)

    # if the hub is currently in bypass mode we don't really worry about any limit
    if hub.getBypass():
        path += "0."
        # leave bypass after sunset/offset
        if (now < (sunrise + sunrise_off) or now > sunset - sunset_off) and hub.control_bypass and demand > hub_solarpower:
            hub.allowBypass(False)
            hub.setBypass(False)
            path += "1."
        else:
            path += "2."
            limit = hub.getInverseMaxPower()

    if not hub.getBypass():
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
            if (now < (sunrise + sunrise_off) or now > sunset - sunset_off): 
                path += "1."                
                limit = min(demand,MAX_DISCHARGE_POWER)
            else:
                path += "2."  
                #limit = 0 if hub_solarpower - MIN_CHARGE_POWER < 0 and hub.getElectricLevel() < 100 else hub_solarpower - MIN_CHARGE_POWER                                   
                limit = 0 if hub_solarpower - MIN_CHARGE_POWER < 0 else hub_solarpower - MIN_CHARGE_POWER
        if demand < 0:
            limit = 0

    # get battery Soc at sunset/sunrise
    td = timedelta(minutes = 3)
    if now > sunset and now < sunset + td:
        hub.setSunsetSoC(hub_electricLevel)
    if now > sunrise and now < sunrise + td:
        hub.setSunriseSoC(hub_electricLevel)
        log.info(f'Good morning! We have consumed {hub.getNightConsumption()}% of the battery tonight!')
        ts = int(time.time())
        log.info(f'Syncing time of solarflow hub (UTC): {datetime.fromtimestamp(ts).strftime("%Y-%m-%d, %H:%M:%S")}')
        hub.timesync(ts)

        # sometimes bypass resets to default (auto)
        if hub.control_bypass:
            hub.allowBypass(True)
            hub.setBypass(False)
            hub.setAutorecover(False)

    log.info(f'Based on time, solarpower ({hub_solarpower:4.1f}W) minimum charge power ({MIN_CHARGE_POWER}W) and bypass state ({hub.getBypass()}), hub could contribute {limit:4.1f}W - Decision path: {path}')
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

    inv_limit = inv.getLimit()
    hub_limit = hub.getLimit()
    direct_limit = None

    # convert DC Power into AC power by applying current efficiency for more precise calculations
    direct_panel_power = inv.getDirectDCPower() * (inv.getEfficiency()/100)
    # consider DC power of panels below 10W as 0 to avoid fluctuation in very low light.
    direct_panel_power = 0 if direct_panel_power < 10 else direct_panel_power

    hub_power = inv.getHubDCPower() * (inv.getEfficiency()/100)

    grid_power = smt.getPower() - smt.zero_offset
    inv_acpower = inv.getCurrentACPower()

    demand = grid_power + direct_panel_power + hub_power

    remainder = demand - direct_panel_power - hub_power        # eq grid_power
    hub_contribution_ask = hub_power+remainder     # the power we need from hub
    hub_contribution_ask = 0 if hub_contribution_ask < 0 else hub_contribution_ask


    # sunny, producing
    if direct_panel_power > 0:
        if demand < direct_panel_power:
            # we can conver demand with direct panel power, just use all of it
            log.info(f'Direct connected panels ({direct_panel_power:.1f}W) can cover demand ({demand:.1f}W)')
            #direct_limit = getDirectPanelLimit(inv,hub,smt)
            # keep inverter limit where it is, no need to change
            direct_limit = getDirectPanelLimit(inv,hub,smt)
            hub_limit = hub.setOutputLimit(0)
        else:
            # we need contribution from hub, if possible and/or try to get more from direct panels
            log.info(f'Direct connected panels ({direct_panel_power:.1f}W) can\'t cover demand ({demand:.1f}W), trying to get {hub_contribution_ask:.1f}W from hub.')
            if hub_contribution_ask > 5:
                # is there potentially more to get from direct panels?
                # if the direct channel power is below what is theoretically possible, it is worth trying to increase the limit

                # if the max of direct channel power is close to the channel limit we should increase the limit first to eventually get more from direct panels 
                if inv.isWithin(max(inv.getDirectDCPowerValues()) * (inv.getEfficiency()/100),inv.getChannelLimit(),10*inv.getNrTotalChannels()):
                    log.info(f'The current max direct channel power {(max(inv.getDirectDCPowerValues()) * (inv.getEfficiency()/100)):.1f}W is close to the current channel limit {inv.getChannelLimit():.1f}W, trying to get more from direct panels.')
                    hub_limit = hub.getLimit()
                    direct_limit = getDirectPanelLimit(inv,hub,smt)
                else:
                    # check what hub is currently  willing to contribute
                    sf_contribution = getSFPowerLimit(hub,hub_contribution_ask)

                    # would the hub's contribution plus direct panel power cross the AC limit? If yes only contribute up to the limit
                    if sf_contribution * (inv.getEfficiency()/100) + direct_panel_power  > inv.acLimit:
                        log.info(f'Hub could contribute {sf_contribution:.1f}W, but this would exceed the configured AC limit ({inv.acLimit}W), so only asking for {inv.acLimit - direct_panel_power:.1f}W')
                        sf_contribution = inv.acLimit - direct_panel_power

                    # if the hub's contribution (per channel) is larger than what the direct panels max is delivering (night, low light)
                    # then we can open the hub to max limit and use the inverter to limit it's output (more precise)
                    if sf_contribution/inv.getNrHubChannels() >= max(inv.getDirectDCPowerValues()) * (inv.getEfficiency()/100):
                        log.info(f'Hub should contribute more ({sf_contribution:.1f}W) than what we currently get max from panels ({max(inv.getDirectDCPowerValues()) * (inv.getEfficiency()/100):.1f}W), we will use the inverter for fast/precise limiting!')
                        hub_limit = hub.setOutputLimit(0) if hub.getBypass() else hub.setOutputLimit(hub.getInverseMaxPower())
                        direct_limit = sf_contribution/inv.getNrHubChannels()
                    else:
                        hub_limit = hub.setOutputLimit(0) if hub.getBypass() else hub.setOutputLimit(sf_contribution)
                        log.info(f'Hub is willing to contribute {min(hub_limit,hub_contribution_ask):.1f}W of the requested {hub_contribution_ask:.1f}!')
                        direct_limit = getDirectPanelLimit(inv,hub,smt)
                        log.info(f'Direct connected panel limit is {direct_limit}W.')

    # likely no sun, not producing, eveything comes from hub
    else:
        log.info(f'Direct connected panel are producing {direct_panel_power:.1f}W, trying to get {hub_contribution_ask:.1f}W from hub.')
        # check what hub is currently  willing to contribute
        sf_contribution = getSFPowerLimit(hub,hub_contribution_ask)
        hub_limit = hub.setOutputLimit(hub.getInverseMaxPower())
        direct_limit = sf_contribution/inv.getNrHubChannels()
        log.info(f'Solarflow is willing to contribute {direct_limit:.1f}W (per channel) of the requested {hub_contribution_ask:.1f}!')


    if direct_limit != None:

        limit = direct_limit

        if hub_limit > direct_limit > hub_limit - 10:
            limit = hub_limit - 10
        if direct_limit < hub_limit - 10 and hub_limit < hub.getInverseMaxPower():
            limit = hub_limit - 10

        inv_limit = inv.setLimit(limit)

    if remainder < 0:
        source = f'unknown: {-remainder:.1f}'
        if direct_panel_power == 0 and hub_power > 0 and hub.getDischargePower() > 0:
            source = f'battery: {-grid_power:.1f}W'
        # since we usually set the inverter limit not to zero there is always a little bit drawn from the hub (10-15W)
        if direct_panel_power == 0 and hub_power > 15 and hub.getDischargePower() == 0 and not hub.getBypass():
            source = f'hub solarpower: {-grid_power:.1f}W'
        if direct_panel_power > 0 and hub_power > 15  and hub.getDischargePower() == 0 and hub.getBypass():
            source = f'hub bypass: {-grid_power:.1f}W'
        if direct_panel_power > 0 and hub_power < 15:
            source = f'panels connected directly to inverter: {-remainder:.1f}'

        log.info(f'Grid feed in from {source}!')

    panels_dc = "|".join([f'{v:>2}' for v in inv.getDirectDCPowerValues()])
    hub_dc = "|".join([f'{v:>2}' for v in inv.getHubDCPowerValues()])

    now = datetime.now(tz=location.tzinfo)   
    s = sun(location.observer, date=now, tzinfo=location.timezone)
    sunrise = s['sunrise']
    sunset = s['sunset']

    log.info(' '.join(f'Sun: {sunrise.strftime("%H:%M")} - {sunset.strftime("%H:%M")} \
             Demand: {demand:.1f}W, \
             Panel DC: ({direct_panel_power:.1f}W), \
             Hub DC: ({hub_power:.1f}W), \
             Inverter Limit: {inv_limit:.1f}W, \
             Hub Limit: {hub_limit:.1f}W'.split()))

def getOpts(configtype) -> dict:
    global config
    opts = {}
    for opt,opt_type in configtype.opts.items():
        t = opt_type.__name__
        try: 
            if t == "bool": t = "boolean"
            converter = getattr(config,f'get{t}')
            opts.update({opt:opt_type(converter(configtype.__name__.lower(),opt))})
        except configparser.NoOptionError:
            log.info(f'No config setting found for option "{opt}" in section {configtype.__name__.lower()}!')
    return opts

def limit_callback(client: mqtt_client,force=False):
    global lastTriggerTS
    #log.info("Smartmeter Callback!")
    now = datetime.now()
    if lastTriggerTS:
        elapsed = now - lastTriggerTS
        # ensure the limit function is not called too often (avoid flooding DTUs)
        if elapsed.total_seconds() >= steering_interval or force:
            lastTriggerTS = now
            limitHomeInput(client)
            return True
        else:
            return False
    else:
        lastTriggerTS = now
        limitHomeInput(client)
        return True

def deviceInfo(client:mqtt_client):
    limitHomeInput(client)
    '''
    hub = client._userdata['hub']
    log.info(f'{hub}')
    inv = client._userdata['dtu']
    log.info(f'{inv}')
    smt = client._userdata['smartmeter']
    log.info(f'{smt}')
    '''


def run():
    client = connect_mqtt()
    hub_opts = getOpts(Solarflow)
    hub = Solarflow(client=client,callback=limit_callback,**hub_opts)

    dtuType = getattr(dtus, DTU_TYPE)
    dtu_opts = getOpts(dtuType)
    dtu = dtuType(client=client,ac_limit=MAX_INVERTER_LIMIT,callback=limit_callback,**dtu_opts)

    smtType = getattr(smartmeters, SMT_TYPE)
    smt_opts = getOpts(smtType)
    smt = smtType(client=client,callback=limit_callback, **smt_opts)

    client.user_data_set({"hub":hub, "dtu":dtu, "smartmeter":smt})
    client.on_message = on_message

    infotimer = RepeatedTimer(120, deviceInfo, client)

    #client.loop_start()
    client.loop_forever()

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
