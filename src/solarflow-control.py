import random, json, time, logging, sys, getopt, os
from datetime import datetime, date
from functools import reduce
from paho.mqtt import client as mqtt_client
from astral import LocationInfo
from astral.sun import sun
import requests
from ip2geotools.databases.noncommercial import DbIpCity
import click

FORMAT = '%(asctime)s:%(levelname)s: %(message)s'
logging.basicConfig(stream=sys.stdout, level="INFO", format=FORMAT)
log = logging.getLogger("")

sf_device_id = os.environ.get('SF_DEVICE_ID',None)
sf_product_id = os.environ.get('SF_PRODUCT_ID',"73bkTV")
mqtt_user = os.environ.get('MQTT_USER',None)
mqtt_pwd = os.environ.get('MQTT_PWD',None)
mqtt_host = os.environ.get('MQTT_HOST',None)
mqtt_port = os.environ.get('MQTT_PORT',1883)
MIN_CHARGE_LEVEL = int(os.environ.get('MIN_CHARGE_LEVEL',125))          # The amount of power that should be always reserved for charging, if available. Nothing will be fed to the house if less is produced
MAX_DISCHARGE_LEVEL = int(os.environ.get('MAX_DISCHARGE_LEVEL',145))    # The maximum discharge level of the packSoc. Even if there is more demand it will not go beyond that
DAY_DISCHARGE_SOC = int(os.environ.get('DAY_DISCHARGE_SOC',50))         # The minimum state of charge of the battery to start discharging also throughout the day
OVERAGE_LIMIT = 15                                                      # if we produce more than what we need we can feed that much to the grid
BATTERY_LOW = int(os.environ.get('BATTERY_LOW',10)) 
BATTERY_HIGH = int(os.environ.get('BATTERY_HIGH',98))
MAX_INVERTER_LIMIT = 800                                                 # the maximum allowed inverter output
MAX_INVERTER_INPUT = MAX_INVERTER_LIMIT - MIN_CHARGE_LEVEL
INVERTER_MPPTS = int(os.environ.get('INVERTER_MPPTS',4))                 # the number of inverter inputs or mppts. SF only uses 2 so when limiting we need to adjust for that
INVERTER_SF_INPUTS_USED = int(os.environ.get('INVERTER_SF_INPUTS_USED',2))   # how many Inverter input channels are used by Solarflow   
FAST_CHANGE_OFFSET = 200
limit_inverter = bool(os.environ.get('LIMIT_INVERTER',False))

# Location Info
LAT=float(os.environ.get('LATITUDE',48.147381))
LNG=float(os.environ.get('LONGITUDE',11.730140))

# topic for the current household consumption (e.g. from smartmeter): int Watts
# if there is no single topic wich aggregates multiple phases (e.g. shelly 3EM) you can specify the topic in an array like this
# topic_house = [shellies/shellyem3/emeter/1/power, shellies/shellyem3/emeter/2/power, shellies/shellyem3/emeter/3/power]
topic_house = os.environ.get('TOPIC_HOUSE',"tele/E220/SENSOR")
topics_house = [ t.strip() for t in topic_house.split(',')]

# topic for the microinverter input to home (e.g. from OpenDTU, AhouyDTU)
topic_acinput = os.environ.get('TOPIC_ACINPUT',"solar/ac/power")
# topics for telemetry read from Solarflow Hub                                                       
topic_solarflow_solarinput = "solarflow-hub/telemetry/solarInputPower"
topic_solarflow_electriclevel = "solarflow-hub/telemetry/electricLevel"
topic_solarflow_outputpack = "solarflow-hub/telemetry/outputPackPower"
topic_solarflow_packinput = "solarflow-hub/telemetry/packInputPower"
topic_solarflow_outputhome = "solarflow-hub/telemetry/outputHomePower"
topic_solarflow_maxtemp = "solarflow-hub/telemetry/batteries/+/maxTemp"
topic_solarflow_battery_soclevel = "solarflow-hub/telemetry/batteries/+/socLevel"

# topic to control the Solarflow Hub (used to set output limit)
topic_limit_solarflow = f'iot/{sf_product_id}/{sf_device_id}/properties/write'

# optional topic for controlling the inverter limit
#topic_ahoylimit = "inverter/ctrl/limit/0"                                              #AhoyDTU
topic_limit_non_persistent = "solar/116491132532/cmd/limit_nonpersistent_absolute"      #OpenDTU

# location info for determining sunrise/sunset
loc = LocationInfo(timezone='Europe/Berlin',latitude=LAT, longitude=LNG)

client_id = f'solarflow-control-{random.randint(0, 100)}'

# sliding average windows for telemetry data, to remove spikes and drops
sf_window = int(os.environ.get('SF_WINDOW',5))
solarflow_values = [0]*sf_window
sm_window = int(os.environ.get('SM_WINDOW',5))
smartmeter_values = [0]*sm_window
inv_window = int(os.environ.get('INV_WINDOW',5))
inverter_values = [0]*inv_window
limit_window = int(os.environ.get('LIMIT_WINDOW',5))
limit_values =  [0]*limit_window

packSoc = -1
charging = 0
home = 0
maxtemp = 1000
batterySocs = {"dummy": -1}
phase_values = {}
last_solar_input_update = datetime.now()


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


def on_solarflow_solarinput(msg):
    #log.info(f'Received solarInput: {msg}')
    global last_solar_input_update    
    if len(solarflow_values) >= sf_window:
        solarflow_values.pop(0)
        solarflow_values.append(int(msg))
        last_solar_input_update = datetime.now()

def on_solarflow_electriclevel(msg):
    #log.info(f'Received electricLevel: {msg}')
    global packSoc
    packSoc = int(msg)

def on_solarflow_outputpack(msg):
    #log.info(f'Received outputPack: {msg}')
    global charging
    charging = int(msg)

def on_solarflow_packinput(msg):
    #log.info(f'Received packInput: {msg}')
    global charging
    charging = -int(msg)

def on_solarflow_outputhome(msg):
    global home
    home = int(msg)

def on_solarflow_maxtemp(msg):
    global maxtemp
    maxtemp = int(msg)

def on_solarflow_battery_soclevel(sn, msg):
    global batterySocs
    batterySocs.pop("dummy",None)
    batterySocs.update({sn:int(msg)})

def on_inverter_update(msg):
    global inverter_values
    if len(inverter_values) >= inv_window:
        inverter_values.pop(0)
    inverter_values.append(float(msg))

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
    global last_solar_input_update
    if msg.topic.startswith("solarflow-hub"):
        now = datetime.now()
        diff = now - last_solar_input_update
        seconds = diff.total_seconds()
        if seconds > 120:
            #if we haven't received any update on solarInputPower we assume it's not producing
            #log.info(f'No solarInputPower measurement received for {seconds}s')
            solarflow_values.pop(0)
            solarflow_values.append(0)

    if msg.topic == topic_acinput:
        on_inverter_update(msg.payload.decode())
    if msg.topic == topic_solarflow_solarinput:
        on_solarflow_solarinput(msg.payload.decode())  
    if msg.topic == topic_solarflow_electriclevel:
        on_solarflow_electriclevel(msg.payload.decode()) 
    if msg.topic == topic_solarflow_outputpack:
        on_solarflow_outputpack(msg.payload.decode()) 
    if msg.topic == topic_solarflow_packinput:
        on_solarflow_packinput(msg.payload.decode()) 
    if msg.topic == topic_solarflow_outputhome:
        on_solarflow_outputhome(msg.payload.decode()) 
    if "maxTemp" in msg.topic and "batteries" in msg.topic:
        on_solarflow_maxtemp(msg.payload.decode()) 
    if "socLevel" in msg.topic and "batteries" in msg.topic:
        sn = msg.topic.split('/')[-2]
        on_solarflow_battery_soclevel(sn, msg.payload.decode())
    if msg.topic in topics_house:
        on_smartmeter_update(client,msg)
    

def on_connect(client, userdata, flags, rc):
    if rc == 0:
        log.info("Connected to MQTT Broker!")
    else:
        log.error("Failed to connect, return code %d\n", rc)

def connect_mqtt() -> mqtt_client:
    client = mqtt_client.Client(client_id)
    if mqtt_user is not None and mqtt_pwd is not None:
        client.username_pw_set(mqtt_user, mqtt_pwd)
    client.on_connect = on_connect
    client.connect(mqtt_host, mqtt_port)
    return client

def subscribe(client: mqtt_client):
    for th in topics_house:
        client.subscribe(th)

    client.subscribe(topic_acinput)
    client.subscribe(topic_solarflow_solarinput)
    client.subscribe(topic_solarflow_electriclevel)
    client.subscribe(topic_solarflow_outputpack)
    client.subscribe(topic_solarflow_packinput)
    client.subscribe(topic_solarflow_outputhome)
    client.subscribe(topic_solarflow_battery_soclevel)
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
        log.warning(f'The maximum measured battery temperature is {maxtemp/100}. Disabling charging to avoid damage! Please reset manually one temperature is high enough!')
        client.publish(topic_limit_solarflow,json.dumps(socset))

# limit the output to home setting on the Solarflow hub
def limitSolarflow(client: mqtt_client, limit):
    # currently the hub doesn't support single steps for limits below 100
    # to get a fine granular steering at this level we need to fall back to the inverter limit
    # if controlling the inverter is not possible we should stick to either 0 or 100W
    if limit <= 100:
        limitInverter(client,limit)
        log.info(f'The output limit would be below 100W ({limit}W). Would need to limit the inverter to match it precisely')
        m = divmod(limit,30)[0]
        r = divmod(limit,30)[1]
        limit = 30 * m + 30 * (r // 15)
        log.info(f'Setting solarflow output limit to {limit}')
    else:
        limitInverter(client,MAX_INVERTER_LIMIT)

    outputlimit = {"properties": { "outputLimit": limit }}
    client.publish(topic_limit_solarflow,json.dumps(outputlimit))
    return limit

# set the limit on the inverter (when using inverter only mode)
def limitInverter(client: mqtt_client, limit):
    # make sure that the inverter limit (which is applied to all MPPTs output equally) matches globally for what we need
    inv_limit = limit*(1/(INVERTER_SF_INPUTS_USED/INVERTER_MPPTS))
    client.publish(topic_limit_non_persistent,f'{inv_limit}')
    return inv_limit


def limitHomeInput(client: mqtt_client):
    global home
    global packSoc, batterySocs
    global smartmeter_values, solarflow_values, inverter_values
    # ensure we have data to work on
    if len(smartmeter_values) == 0:
        log.info(f'Waiting for smartmeter data to make decisions...')
        return
    if len(solarflow_values) == 0:
        log.info(f'Waiting for solarflow input data to make decisions...')
        return
    if len(inverter_values) == 0:
        log.info(f'Waiting for inverter data to make decisions...')
        return
    if packSoc < 0:
        log.info(f'Waiting for state of charge to make decisions...')
        return
        
    smartmeter = reduce(lambda a,b: a+b, smartmeter_values)/len(smartmeter_values)
    solarinput = int(round(reduce(lambda a,b: a+b, solarflow_values)/len(solarflow_values)))
    inverterinput = round(reduce(lambda a,b: a+b, inverter_values)/len(inverter_values),1)
    demand = int(round((smartmeter + inverterinput)))
    limit = 0

    now = datetime.now(tz=loc.tzinfo)   
    s = sun(loc.observer, date=now, tzinfo=loc.timezone)
    sunrise = s['sunrise']
    sunset = s['sunset']

    # now all the logic when/how to set limit
    path = ""
    if packSoc > BATTERY_HIGH:
        path = "1."
        if solarinput > 0 and solarinput > MIN_CHARGE_LEVEL:    # producing more than what is needed => only take what is needed and charge, giving a bit extra to demand
            path += "1."
            limit = min(demand + OVERAGE_LIMIT,solarinput + OVERAGE_LIMIT)
        if solarinput > 0 and solarinput <= MIN_CHARGE_LEVEL:   # producing less than the minimum charge level 
            path += "2."
            if now <= sunrise or now > sunset:
                path += "1"                         # in the morning keep using packSoc
                limit = MAX_DISCHARGE_LEVEL
            else:         
                path += "2"                                      
                limit = solarinput + OVERAGE_LIMIT              # everything goes to the house throughout the day, in case SF regulated solarinput down we need to demand a bit more stepwise
        if solarinput <= 0:
            path += "3"                                     
            limit = min(demand,MAX_DISCHARGE_LEVEL)             # not producing and demand is less than discharge limit => discharge with what is needed but limit to MAX
    elif packSoc <= BATTERY_LOW:
        path = "2."                                         
        limit = 0                                               # battery is at low stage, stop discharging
    else:
        path = "3."
        if solarinput > 0 and solarinput > MIN_CHARGE_LEVEL:
            path += "1." 
            limit = min(demand,solarinput - MIN_CHARGE_LEVEL - 10)      # give charging precedence
        if solarinput <= MIN_CHARGE_LEVEL:  
            path += "2."                                                # producing less than the minimum charge level 
            if (now < sunrise or now > sunset) or min(batterySocs.values()) > DAY_DISCHARGE_SOC: 
                path += "1"                        
                limit = min(demand,MAX_DISCHARGE_LEVEL)                 # in the morning keep using battery, in the evening start using battery
            else:
                path += "2"                                     
                limit = 0                                               # throughout the day use everything to charge

    if len(limit_values) >= limit_window:
        limit_values.pop(0)
    limit_values.append(0 if limit<0 else limit)                # to recover faster from negative demands
    limit = int(reduce(lambda a,b: a+b, limit_values)/len(limit_values))

    sm = ",".join([f'{v:>4}' for v in smartmeter_values])
    lm = ",".join([f'{v:>4}' for v in limit_values])
    batSoc = "|".join("{}%".format(v) for k, v in batterySocs.items())

    log.info(' '.join(f'Sun: {sunrise.strftime("%H:%M")} - {sunset.strftime("%H:%M")}, \
             Smartmeter: [{sm}], \
             Demand: {demand}W, \
             Solar: {solarinput}W, \
             Inverter: {inverterinput}W, \
             Home: {home}W, \
             Battery: {packSoc}% ({batSoc}), \
             {"dis" if charging<0 else ""}charging: {charging}W \
             => Limit: {limit}W - [{lm}] - decisionpath: {path}'.split()))

    if limit_inverter:
        limitInverter(client,limit)
    else:
        limitSolarflow(client,limit)

def run():
    client = connect_mqtt()
    subscribe(client)
    turnOffBuzzer(client)
    client.loop_start()

    while True:
        time.sleep(15)
        checkCharging(client)
        limitHomeInput(client)
        

    client.loop_stop()

#@click.command
#@click.option("--limit-via", type=click.Choice(['inverter','hub'], case_sensitive=False))
#@click.option("--broker","-b",help="IP/Hostname of the local MQTT broker to use")
#@click.option("--port","-p",help="Port of the local MQTT broker, if different from default (1883)")
#@click.option("--user","-u", help="Login name for local MQTT broker")
#@click.option("--secret","-s", help="Password for the local MQTT broker user")
#@click.option("--offline/--online", default=True, help="Offline/Online mode: either connect to the Zendure API/MQTT or not (requires local MQTT with hub data present)")
def main(argv):
    global mqtt_host, mqtt_port, mqtt_user, mqtt_pwd
    global sf_device_id
    global topic_limit_solarflow
    global limit_inverter
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

    loc = MyLocation()
    coordinates = loc.getCoordinates()
    if loc is None:
        coordinates = (LAT,LNG)
        log.info(f'Geocoordinates: {coordinates}')

    run()

if __name__ == '__main__':
    main(sys.argv[1:])
