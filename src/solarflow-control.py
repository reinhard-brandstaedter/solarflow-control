import random, json, time, logging, sys, requests, os
from datetime import datetime
from functools import reduce
from paho.mqtt import client as mqtt_client

FORMAT = '%(asctime)s:%(levelname)s: %(message)s'
logging.basicConfig(stream=sys.stdout, level="INFO", format=FORMAT)
log = logging.getLogger("")
logging.getLogger("urllib3").setLevel(logging.WARNING)
logging.getLogger("requests").setLevel(logging.WARNING)

SF_DEVICE_ID = os.environ.get('SF_DEVICE_ID',None)
SF_PRODUCT_ID = os.environ.get('SF_PRODUCT_ID',"73bkTV")
MQTT_USER = os.environ.get('MQTT_USER',None)
MQTT_PW = os.environ.get('MQTT_PW',None)
MQTT_HOST = os.environ.get('MQTT_HOST',None)
MQTT_PORT = os.environ.get('MQTT_PORT',1883)

if SF_DEVICE_ID is None:
    log.error(f'SF_DEVICE_ID environment variables! Exiting!')
    sys.exit()

if MQTT_HOST is None:
    log.error("You need a local MQTT broker set (environment variable MQTT_HOST)!")
    sys.exit(0)

if MQTT_USER is None or MQTT_PW is None:
    log.info(f'MQTT_USER or MQTT_PW is not set, assuming authentication not needed')

# our MQTT broker where we subscribe to all the telemetry data we need to steer
# could also be an external one, e.g. fetching SolarFlow data directly from their dv-server
broker = MQTT_HOST
port = MQTT_PORT

topic_house = "tele/E220/SENSOR"
#topic_acinput = "inverter/HM-600/ch0/P_AC"
topic_acinput = "solar/ac/power"
topic_solarflow_solarinput = "solarflow-hub/telemetry/solarInputPower"
topic_solarflow_electriclevel = "solarflow-hub/telemetry/electricLevel"
topic_solarflow_outputpack = "solarflow-hub/telemetry/outputPackPower"
topic_solarflow_outputhome = "solarflow-hub/telemetry/outputHomePower"
#topic_ahoylimit = "inverter/ctrl/limit/0"
topic_limit_non_persistent = "solar/116491132532/cmd/limit_nonpersistent_absolute"
topic_limit_solarflow = f'iot/{SF_PRODUCT_ID}/{SF_DEVICE_ID}/properties/write'
client_id = f'subscribe-{random.randint(0, 100)}'

# sliding average windows for telemetry data, to remove spikes and drops
sf_window = int(os.environ.get('SF_WINDOW',5))
solarflow_values = [0]*sf_window
sm_window = int(os.environ.get('SM_WINDOW',10))
smartmeter_values = [0]*sm_window
inv_window = int(os.environ.get('INV_WINDOW',5))
inverter_values = [0]*inv_window
limit_values =  [0]*10


battery = -1
charging = 0
home = 0
MIN_CHARGE_LEVEL = int(os.environ.get('MIN_CHARGE_LEVEL',125))          # The amount of power that should be always reserved for charging, if available. Nothing will be fed to the house if less is produced
MAX_DISCHARGE_LEVEL = int(os.environ.get('MAX_DISCHARGE_LEVEL',145))    # The maximum discharge level of the battery. Even if there is more demand it will not go beyond that
OVERAGE_LIMIT = 30              # if we produce more than what we need we can feed that much to the grid
BATTERY_LOW = int(os.environ.get('BATTERY_LOW',10)) 
BATTERY_HIGH = int(os.environ.get('BATTERY_HIGH',98))

last_limit = -1                 # just record the last limit to avoid too many calls to inverter API
last_solar_input_update = datetime.now()

# know properties that are reported as reference
property_set = {'electricLevel', 'outputPackPower', 'outputLimit', 'packInputPower', 'buzzerSwitch', 'inputLimit', 'masterSwitch', 'packNum', 'wifiState', 'socSet', 'hubState', 'remainOutTime', 'remainInputTime', 'solarInputPower', 'inverseMaxPower', 'outputHomePower', 'packState'}

def on_solarflow_solarinput(msg):
    #log.info(f'Received solarInput: {msg}')
    global last_solar_input_update    
    if len(solarflow_values) >= sf_window:
        solarflow_values.pop(0)
        solarflow_values.append(int(msg))
        last_solar_input_update = datetime.now()

def on_solarflow_electriclevel(msg):
    #log.info(f'Received electricLevel: {msg}')
    global battery
    battery = int(msg)

def on_solarflow_outputpack(msg):
    #log.info(f'Received outputPack: {msg}')
    global charging
    charging = int(msg)

def on_solarflow_outputhome(msg):
    global home
    home = int(msg)

def on_solarflow_update(msg):
    global battery, charging
    global last_solar_input_update
    
    now = datetime.now()
    diff = now - last_solar_input_update
    seconds = diff.total_seconds()
    if seconds > 120:
        #if we haven't received any update on solarInputPower we assume it's not producing
        log.info(f'No solarInputPower measurement received for {seconds}s')
        solarflow_values.pop(0)
        solarflow_values.append(0)

    payload = json.loads(msg)
    #for p in payload:
    #    property_set.add(p)

    if "solarInputPower" in payload:
        if len(solarflow_values) >= sf_window:
            solarflow_values.pop(0)
        solarflow_values.append(payload["solarInputPower"])
        last_solar_input_update = now
    if "electricLevel" in payload:
        battery = int(payload["electricLevel"])
    if "outputPackPower" in payload:
        charging = int((payload["outputPackPower"]))

def on_inverter_update(msg):
    if len(inverter_values) >= inv_window:
        inverter_values.pop(0)
    inverter_values.append(float(msg))

def on_smartmeter_update(msg):
    payload = json.loads(msg)
    if len(smartmeter_values) >= sm_window:
        smartmeter_values.pop(0)
    smartmeter_values.append(int(payload["Power"]["Power_curr"]))

def on_message(client, userdata, msg):
    global last_solar_input_update
    if msg.topic.startswith("solarflow-status"):
        now = datetime.now()
        diff = now - last_solar_input_update
        seconds = diff.total_seconds()
        if seconds > 120:
            #if we haven't received any update on solarInputPower we assume it's not producing
            log.info(f'No solarInputPower measurement received for {seconds}s')
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
    if msg.topic == topic_solarflow_outputhome:
        on_solarflow_outputhome(msg.payload.decode()) 
    if msg.topic == topic_house:
        on_smartmeter_update(msg.payload.decode())

def on_connect(client, userdata, flags, rc):
    if rc == 0:
        print("Connected to MQTT Broker!")
    else:
        print("Failed to connect, return code %d\n", rc)

def connect_mqtt() -> mqtt_client:
    client = mqtt_client.Client(client_id)
    if MQTT_USER is not None and MQTT_PW is not None:
        client.username_pw_set(MQTT_USER, MQTT_PW)
    client.on_connect = on_connect
    client.connect(broker, port)
    return client

def subscribe(client: mqtt_client):
    client.subscribe(topic_house)
    client.subscribe(topic_acinput)
    client.subscribe(topic_solarflow_solarinput)
    client.subscribe(topic_solarflow_electriclevel)
    client.subscribe(topic_solarflow_outputpack)
    client.subscribe(topic_solarflow_outputhome)
    client.on_message = on_message

def limitSolarflow(client: mqtt_client, limit):
    outputlimit = {"properties": { "outputLimit": limit }}
    client.publish(topic_limit_solarflow,json.dumps(outputlimit))

def limitInverter(client: mqtt_client, limit):
    client.publish(topic_limit_non_persistent,f'{limit}W')


def steerInverter(client: mqtt_client):
    global home
    global battery
    # ensure we have data to work on
    if len(smartmeter_values) == 0:
        log.warning(f'Waiting for smartmeter data to make decisions...')
        return
    if len(solarflow_values) == 0:
        log.warning(f'Waiting for solarflow input data to make decisions...')
        return
    if len(inverter_values) == 0:
        log.warning(f'Waiting for inverter data to make decisions...')
        return
    if battery < 0:
        log.warning(f'Waiting for battery state to make decisions...')
        return
        
    smartmeter = reduce(lambda a,b: a+b, smartmeter_values)/len(smartmeter_values)
    solarinput = int(round(reduce(lambda a,b: a+b, solarflow_values)/len(solarflow_values)))
    inverterinput = round(reduce(lambda a,b: a+b, inverter_values)/len(inverter_values),1)
    demand = int(round((smartmeter + inverterinput)))
    limit = 0

    hour = datetime.now().hour

    #now all the logic when/how to set limit
    if battery > BATTERY_HIGH:
        if solarinput > 0 and solarinput > MIN_CHARGE_LEVEL:    # producing more than what is needed => only take what is needed and charge, giving a bit extra to demand
            limit = min(demand + OVERAGE_LIMIT,solarinput + OVERAGE_LIMIT)
        if solarinput > 0 and solarinput <= MIN_CHARGE_LEVEL:   # producing less than the minimum charge level 
            if hour <= 6 or hour >= 16:                         # in the morning keep using battery
                limit = MAX_DISCHARGE_LEVEL
            else:                                               
                limit = solarinput + OVERAGE_LIMIT              # everything goes to the house throughout the day, in case SF regulated solarinput down we need to demand a bit more stepwise
        if solarinput <= 0:                                     
            limit = min(demand,MAX_DISCHARGE_LEVEL)             # not producing and demand is less than discharge limit => discharge with what is needed but limit to MAX
    elif battery <= BATTERY_LOW:                                         
        limit = 0                                               # battery is at low stage, stop discharging
    else:
        if solarinput > 0 and solarinput > MIN_CHARGE_LEVEL:
            limit = min(demand,solarinput - MIN_CHARGE_LEVEL - 10)   # give charging precedence
        if solarinput <= MIN_CHARGE_LEVEL:                      # producing less than the minimum charge level 
            if hour <= 6 or hour >= 16:                         
                limit = min(demand,MAX_DISCHARGE_LEVEL)         # in the morning keep using battery, in the evening start using battery
            else:                                               
                limit = 0                                       # throughout the day use everything to charge

    limit_values.pop(0)
    limit_values.append(0 if limit<0 else limit)                # to recover faster from negative demands
    limit = int(reduce(lambda a,b: a+b, limit_values)/len(limit_values))

    #log.info(f'History: Demand: {smartmeter_values}, Inverter: {inverter_values}, Solar: {solarflow_values}')
    log.info(f'Demand: {demand}W, Solar: {solarinput}W, Inverter: {inverterinput}W, Home: {home}W, Battery: {battery}% charging: {charging}W => Limit: {limit}W - {limit_values}')
    # only set the limit if the value has changed
    #if limit != limit_values[-2]:
    #limitInverter(client,limit)
    limitSolarflow(client,limit)

def run():
    client = connect_mqtt()
    subscribe(client)
    client.loop_start()

    while True:
        time.sleep(15)
        steerInverter(client)

    client.loop_stop()

if __name__ == '__main__':
    run()
