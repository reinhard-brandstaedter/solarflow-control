import random, time, logging, sys, getopt, os
from datetime import datetime, timedelta
from functools import reduce
from paho.mqtt import client as mqtt_client
from astral import LocationInfo
from astral.sun import sun
import requests
import configparser
import math
import solarflow
import dtus
import smartmeters
from utils import RepeatedTimer, str2bool

FORMAT = "%(asctime)s:%(levelname)s: %(message)s"
logging.basicConfig(stream=sys.stdout, level="INFO", format=FORMAT)
log = logging.getLogger("")


"""
Customizing ConfigParser to allow dynamic conversion of array options
"""
config: configparser.ConfigParser


def listoption(option):
    return [int(x) for x in list(filter(lambda x: x.isdigit(), list(option)))]


def stroption(option):
    return option


def load_config():
    config = configparser.ConfigParser(
        converters={"str": stroption, "list": listoption}
    )
    try:
        with open("config.ini", "r") as cf:
            config.read_file(cf)
    except:
        log.error(
            "No configuration file (config.ini) found in execution directory! Using environment variables."
        )
    return config


config = load_config()


"""
Configuration Options
"""
sf_device_id = config.get("solarflow", "device_id", fallback=None) or os.environ.get(
    "SF_DEVICE_ID", None
)
sf_product_id = config.get(
    "solarflow", "product_id", fallback="73bkTV"
) or os.environ.get("SF_PRODUCT_ID", "73bkTV")
mqtt_user = config.get("mqtt", "mqtt_user", fallback=None) or os.environ.get(
    "MQTT_USER", None
)
mqtt_pwd = config.get("mqtt", "mqtt_pwd", fallback=None) or os.environ.get(
    "MQTT_PWD", None
)
mqtt_host = config.get("mqtt", "mqtt_host", fallback=None) or os.environ.get(
    "MQTT_HOST", None
)
mqtt_port = config.getint("mqtt", "mqtt_port", fallback=None) or os.environ.get(
    "MQTT_PORT", 1883
)


DTU_TYPE = config.get("global", "dtu_type", fallback=None) or os.environ.get(
    "DTU_TYPE", "OpenDTU"
)
SMT_TYPE = config.get("global", "smartmeter_type", fallback=None) or os.environ.get(
    "SMARTMETER_TYPE", "Smartmeter"
)

# The amount of power that should be always reserved for charging, if available. Nothing will be fed to the house if less is produced
# MQTT config topic: solarflow-hub/control/minChargePower
# config.ini [control] min_charge_power
MIN_CHARGE_POWER = None

# The maximum discharge level of the packSoc. Even if there is more demand it will not go beyond that
# MQTT config topic: solarflow-hub/control/maxDischargePower
# config.ini [control] max_discharge_power
MAX_DISCHARGE_POWER = None

# battery SoC levels for normal operation cycles (when not in charge through mode)
# MQTT config topic: solarflow-hub/control/batteryTargetSoCMin
# config.ini [control] battery_low
BATTERY_LOW = None
# MQTT config topic: solarflow-hub/control/batteryTargetSoCMax
# config.ini [control] battery_high
BATTERY_HIGH = None

# the SoC that is required before discharging of the battery would start. To allow a bit of charging first in the morning.
BATTERY_DISCHARGE_START = config.getint(
    "control", "battery_discharge_start", fallback=None
) or int(os.environ.get("BATTERY_DISCHARGE_START", 10))

# the maximum allowed inverter output
MAX_INVERTER_LIMIT = config.getint("control", "max_inverter_limit", fallback=None) or int(os.environ.get("MAX_INVERTER_LIMIT", 800))
MAX_INVERTER_INPUT = config.getint("control", "max_inverter_input", fallback=None) or int(os.environ.get("MAX_INVERTER_INPUT", 400))

# this controls the internal calculation of limited growth for setting inverter limits
INVERTER_START_LIMIT = 5

# interval/rate limit for performing control steps
steering_interval = config.getint("control", "steering_interval", fallback=None) or int(
    os.environ.get("STEERING_INTERVAL", 15)
)

# flag, which can be set to allow discharging the battery during daytime
# MQTT config topic: solarflow-hub/control/dischargeDuringDaytime
# config.ini [control] discharge_during_daytime
DISCHARGE_DURING_DAYTIME = None

# Adjustments possible to sunrise and sunset offset
# MQTT config topic: solarflow-hub/control/sunriseOffset
# config.ini [control] sunrise_offset
SUNRISE_OFFSET = None
# MQTT config topic: solarflow-hub/control/sunsetOffset
# config.ini [control] sunset_offset
SUNSET_OFFSET = None

# Location Info
LAT = config.getfloat("global", "latitude", fallback=None) or float(
    os.environ.get("LATITUDE", 0)
)
LNG = config.getfloat("global", "longitude", fallback=None) or float(
    os.environ.get("LONGITUDE", 0)
)
location: LocationInfo

lastTriggerTS: datetime = None


class MyLocation:
    def getCoordinates(self) -> tuple:
        lat = lon = 0.0
        try:
            result = requests.get(
                "http://ip-api.com/json/"
            )  # call without IP uses my IP
            response = result.json()
            log.info(f"IP Address: {response['query']}")
            log.info(
                f"Location: {response['city']}, {response['regionName']}, {response['country']}"
            )
            log.info(f"Coordinates: (Lat: {response['lat']}, Lng: {response['lon']}")
            lat = response["lat"]
            lon = response["lon"]
        except Exception as e:
            log.error(
                f"Can't determine location from my IP. Location detection failed, no accurate sunrise/sunset detection possible",
                e.args,
            )

        return (lat, lon)


def on_config_message(client, userdata, msg):
    """The MQTT client callback function for intial connects - mainly retained messages, where we are not yet fully up and running but still read potential config parameters from MQTT"""

    global \
        SUNRISE_OFFSET, \
        SUNSET_OFFSET, \
        MIN_CHARGE_POWER, \
        MAX_DISCHARGE_POWER, \
        DISCHARGE_DURING_DAYTIME, \
        BATTERY_LOW, \
        BATTERY_HIGH
    # handle own messages (control parameters)
    if (
        msg.topic.startswith("solarflow-hub")
        and "/control/" in msg.topic
        and msg.payload
    ):
        parameter = msg.topic.split("/")[-1]
        value = msg.payload.decode()
        match parameter:
            case "sunriseOffset":
                SUNRISE_OFFSET = int(value)
                log.info(
                    f"Found control/sunriseOffset, set SUNRISE_OFFSET to {SUNRISE_OFFSET} minutes"
                )
            case "sunsetOffset":
                SUNSET_OFFSET = int(value)
                log.info(
                    f"Found control/sunsetOffset, set SUNSET_OFFSET to {SUNSET_OFFSET} minutes"
                )
            case "minChargePower":
                MIN_CHARGE_POWER = int(value)
                log.info(
                    f"Found control/minChargePower, set MIN_CHARGE_POWER to {MIN_CHARGE_POWER}W"
                )
            case "maxDischargePower":
                MAX_DISCHARGE_POWER = int(value)
                log.info(
                    f"Found control/maxDischargePower, set MAX_DISCHARGE_POWER to {MAX_DISCHARGE_POWER}W"
                )
            case "dischargeDuringDaytime":
                DISCHARGE_DURING_DAYTIME = str2bool(value)
                log.info(
                    f"Found control/dischargeDuringDaytime, set DISCHARGE_DURING_DAYTIME to {DISCHARGE_DURING_DAYTIME}"
                )
            case "batteryTargetSoCMin":
                BATTERY_LOW = int(value)
                log.info(
                    f"Found control/batteryTargetSoCMin, set BATTERY_LOW to {BATTERY_LOW}%"
                )
            case "batteryTargetSoCMax":
                BATTERY_HIGH = int(value)
                log.info(
                    f"Found control/batteryTargetSoCMax, set BATTERY_HIGH to {BATTERY_HIGH}%"
                )


def on_message(client, userdata, msg):
    """The MQTT client callback function for continous oepration, messages are delegated to hub, dtu and smartmeter handlers as well as own control parameter updates"""
    global \
        SUNRISE_OFFSET, \
        SUNSET_OFFSET, \
        MIN_CHARGE_POWER, \
        MAX_DISCHARGE_POWER, \
        DISCHARGE_DURING_DAYTIME, \
        BATTERY_LOW, \
        BATTERY_HIGH
    # delegate message handling to hub,smartmeter, dtu
    smartmeter = userdata["smartmeter"]
    smartmeter.handleMsg(msg)
    hub = userdata["hub"]
    hub.handleMsg(msg)
    dtu = userdata["dtu"]
    dtu.handleMsg(msg)

    # handle own messages (control parameters)
    if msg.topic.startswith("solarflow-hub") and "control" in msg.topic and msg.payload:
        parameter = msg.topic.split("/")[-1]
        value = msg.payload.decode()
        match parameter:
            case "sunriseOffset":
                log.info(f'Updating SUNRISE_OFFSET to {int(value)} minutes') if SUNRISE_OFFSET != int(value) else None
                SUNRISE_OFFSET = int(value)
            case "sunsetOffset":
                log.info(f'Updating SUNSET_OFFSET to {int(value)} minutes') if SUNSET_OFFSET != int(value) else None
                SUNSET_OFFSET = int(value)
            case "minChargePower":
                log.info(f'Updating MIN_CHARGE_POWER to {int(value)}W') if MIN_CHARGE_POWER != int(value) else None
                MIN_CHARGE_POWER = int(value)
            case "maxDischargePower":
                log.info(f'Updating MAX_DISCHARGE_POWER to {int(value)}W') if MAX_DISCHARGE_POWER != int(value) else None
                MAX_DISCHARGE_POWER = int(value) 
            case "controlBypass":
                log.info(f"Updating control bypass to {value}")
                hub.setControlBypass(value)
            case "fullChargeInterval":
                log.info(f"Updating full charge interval to {int(value)}hrs")
                hub.updFullChargeInterval(int(value))
            case "dischargeDuringDaytime":
                log.info(f'Updating DISCHARGE_DURING_DAYTIME to {str2bool(value)}') if DISCHARGE_DURING_DAYTIME != str2bool(value) else None
                DISCHARGE_DURING_DAYTIME = str2bool(value)
            case "batteryTargetSoCMin":
                log.info(f'Updating BATTERY_LOW to {int(value)}%') if BATTERY_LOW != int(value) else None
                BATTERY_LOW = int(value)
                hub.updBatteryTargetSoCMin(BATTERY_LOW)
            case "batteryTargetSoCMax":
                log.info(f'Updating BATTERY_HIGH to {int(value)}%') if BATTERY_HIGH != int(value) else None
                BATTERY_HIGH = int(value)
                hub.updBatteryTargetSoCMax(BATTERY_HIGH)


def on_connect(client, userdata, flags, rc):
    if rc == 0:
        log.info("Connected to MQTT Broker!")
    else:
        log.error("Failed to connect, return code %d\n", rc)


def on_disconnect(client, userdata, rc):
    if rc == 0:
        log.info("Disconnected from MQTT Broker on purpose!")
    else:
        log.error("Disconnected from MQTT broker!")


def connect_mqtt() -> mqtt_client:
    client_id = f"solarflow-ctrl-{random.randint(0, 100)}"
    client = mqtt_client.Client(client_id=client_id, clean_session=False)
    if mqtt_user is not None and mqtt_pwd is not None:
        client.username_pw_set(mqtt_user, mqtt_pwd)
    client.on_connect = on_connect
    client.on_disconnect = on_disconnect
    client.on_config_message = on_config_message
    client.connect(mqtt_host, mqtt_port)
    return client


def subscribe(client: mqtt_client):
    topics = [f"solarflow-hub/{sf_device_id}/control/#"]
    for t in topics:
        client.subscribe(t)
        log.info(f"SF Control subscribing: {t}")


def limitedRise(x) -> int:
    rise = MAX_INVERTER_LIMIT - (MAX_INVERTER_LIMIT - INVERTER_START_LIMIT) * math.exp(
        -MAX_INVERTER_LIMIT / 100000 * x
    )
    log.info(f"Adjusting inverter limit from {x:.1f}W to {rise:.1f}W")
    return int(rise)


# calculate the safe inverter limit for direct panels, to avoid output over legal limits
def getDirectPanelLimit(inv, hub, smt) -> int:
    # if hub is in bypass mode we can treat it just like a direct panel
    direct_panel_power = inv.getDirectACPower() + (
        inv.getHubACPower() if hub.getBypass() else 0
    )
    if direct_panel_power < MAX_INVERTER_LIMIT:
        dc_values = (
            (inv.getDirectDCPowerValues() + inv.getHubDCPowerValues())
            if hub.getBypass()
            else inv.getDirectDCPowerValues()
        )
        return (
            math.ceil(max(dc_values) * (inv.getEfficiency() / 100))
            if smt.getPower() - smt.zero_offset < 0
            else limitedRise(max(dc_values) * (inv.getEfficiency() / 100))
        )
    else:
        return int(
            MAX_INVERTER_LIMIT * (inv.getNrHubChannels() / inv.getNrProducingChannels())
        )


def getSFPowerLimit(hub, demand) -> int:
    hub_electricLevel = hub.getElectricLevel()
    hub_solarpower = hub.getSolarInputPower()
    now = datetime.now(tz=location.tzinfo)
    s = sun(location.observer, date=now, tzinfo=location.timezone)
    sunrise = s["sunrise"]
    sunset = s["sunset"]
    path = ""

    sunrise_off = timedelta(minutes=SUNRISE_OFFSET)
    sunset_off = timedelta(minutes=SUNSET_OFFSET)

    # fallback in case byPass is not yet identifieable after a change (HUB2k)
    limit = hub.getLimit()

    # if the hub is currently in bypass mode we don't really worry about any limit
    if hub.getBypass():
        path += "0."
        # leave bypass after sunset/offset
        if (
            (now < (sunrise + sunrise_off) or now > sunset - sunset_off)
            and hub.control_bypass
            and demand > hub_solarpower
        ):
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
                limit = min(demand, MAX_DISCHARGE_POWER)
            else:
                path += "2."
                limit = min(demand, hub_solarpower - MIN_CHARGE_POWER)
        if hub_solarpower - demand <= MIN_CHARGE_POWER:
            path += "2."
            if (
                now < (sunrise + sunrise_off) or now > sunset - sunset_off
            ) or DISCHARGE_DURING_DAYTIME:
                path += "1."
                # FEAT: we should not allow discharging in the sunrise window if battery is still below a certain threshold
                # e.g. if the battery has just started charging do not discharge it again immediately
                if (
                    (sunrise < now < (sunrise + sunrise_off))
                    and hub_electricLevel <= BATTERY_DISCHARGE_START
                    and hub.batteryTarget != solarflow.BATTERY_TARGET_DISCHARGING
                ):
                    path += "1."
                    limit = 0
                else:
                    path += "2."
                    limit = min(demand, MAX_DISCHARGE_POWER)
            else:
                path += "2."
                # limit = 0 if hub_solarpower - MIN_CHARGE_POWER < 0 and hub.getElectricLevel() < 100 else hub_solarpower - MIN_CHARGE_POWER
                limit = (
                    0
                    if hub_solarpower - MIN_CHARGE_POWER < 0
                    else hub_solarpower - MIN_CHARGE_POWER
                )
        if demand < 0:
            limit = 0

    # get battery Soc at sunset/sunrise
    td = timedelta(minutes=3)
    if now > sunset and now < sunset + td:
        hub.setSunsetSoC(hub_electricLevel)
    if now > sunrise and now < sunrise + td:
        hub.setSunriseSoC(hub_electricLevel)
        log.info(
            f"Good morning! We have consumed {hub.getNightConsumption()}% of the battery tonight!"
        )
        ts = int(time.time())
        log.info(
            f"Syncing time of solarflow hub (UTC): {datetime.fromtimestamp(ts).strftime('%Y-%m-%d, %H:%M:%S')}"
        )
        hub.timesync(ts)
        hub.publishBatteryTarget(solarflow.BATTERY_TARGET_CHARGING)

        # sometimes bypass resets to default (auto)
        if hub.control_bypass:
            hub.allowBypass(True)
            hub.setBypass(False)
            hub.setAutorecover(False)

        # calculate expected daylight in hours
        diff = sunset - sunrise
        daylight = diff.total_seconds() / 3600

        # check if we should run a full charge cycle today
        hub.checkChargeThrough(daylight)

    log.info(
        f"Based on time, solarpower ({hub_solarpower:4.1f}W) minimum charge power ({MIN_CHARGE_POWER}W) and bypass state ({hub.getBypass()}), hub could contribute {limit:4.1f}W - Decision path: {path}"
    )
    return int(limit)


def limitHomeInput(client: mqtt_client):
    global location

    hub = client._userdata["hub"]
    log.info(f"{hub}")
    inv = client._userdata["dtu"]
    log.info(f"{inv}")
    smt = client._userdata["smartmeter"]
    log.info(f"{smt}")

    # ensure we have data to work on
    if not (hub.ready() and inv.ready() and smt.ready()):
        return
    
    inv_limit = inv.getLimit()
    hub_limit = hub.getLimit()
    direct_limit = None

    # convert DC Power into AC power by applying current efficiency for more precise calculations
    direct_panel_power = inv.getDirectDCPower() * (inv.getEfficiency() / 100)
    # consider DC power of panels below 10W as 0 to avoid fluctuation in very low light.
    direct_panel_power = 0 if direct_panel_power < 10 else direct_panel_power

    hub_power = inv.getHubDCPower() * (inv.getEfficiency() / 100)

    grid_power = smt.getPower() - smt.zero_offset
    inv_acpower = inv.getCurrentACPower()

    demand = grid_power + direct_panel_power + hub_power

    remainder = demand - direct_panel_power - hub_power  # eq grid_power
    hub_contribution_ask = hub_power + remainder  # the power we need from hub
    hub_contribution_ask = 0 if hub_contribution_ask < 0 else hub_contribution_ask

    # sunny, producing
    if direct_panel_power > 0:
        if demand < direct_panel_power:
            # we can conver demand with direct panel power, just use all of it
            log.info(
                f"Direct connected panels ({direct_panel_power:.1f}W) can cover demand ({demand:.1f}W)"
            )
            # direct_limit = getDirectPanelLimit(inv,hub,smt)
            # keep inverter limit where it is, no need to change
            direct_limit = getDirectPanelLimit(inv, hub, smt)
            hub_limit = hub.setOutputLimit(0)
        else:
            # we need contribution from hub, if possible and/or try to get more from direct panels
            log.info(
                f"Direct connected panels ({direct_panel_power:.1f}W) can't cover demand ({demand:.1f}W), trying to get {hub_contribution_ask:.1f}W from hub."
            )
            if hub_contribution_ask > 5:
                # is there potentially more to get from direct panels?
                # if the direct channel power is below what is theoretically possible, it is worth trying to increase the limit

                # if the max of direct channel power is close to the channel limit we should increase the limit first to eventually get more from direct panels
                if inv.isWithin(
                    max(inv.getDirectDCPowerValues()) * (inv.getEfficiency() / 100),
                    inv.getChannelLimit(),
                    10 * inv.getNrTotalChannels(),
                ):
                    log.info(
                        f"The current max direct channel power {(max(inv.getDirectDCPowerValues()) * (inv.getEfficiency() / 100)):.1f}W is close to the current channel limit {inv.getChannelLimit():.1f}W, trying to get more from direct panels."
                    )

                    sf_contribution = getSFPowerLimit(hub, hub_contribution_ask)
                    hub_limit = hub.getLimit()
                    # in case of hub contribution ask has changed to lower than current value, we should lower it
                    if sf_contribution < hub_limit:
                        hub.setOutputLimit(sf_contribution)
                    direct_limit = getDirectPanelLimit(inv, hub, smt)
                else:
                    # check what hub is currently  willing to contribute
                    sf_contribution = getSFPowerLimit(hub, hub_contribution_ask)

                    # would the hub's contribution plus direct panel power cross the AC limit? If yes only contribute up to the limit
                    if (
                        sf_contribution * (inv.getEfficiency() / 100)
                        + direct_panel_power
                        > inv.acLimit
                    ):
                        log.info(
                            f"Hub could contribute {sf_contribution:.1f}W, but this would exceed the configured AC limit ({inv.acLimit}W), so only asking for {inv.acLimit - direct_panel_power:.1f}W"
                        )
                        sf_contribution = inv.acLimit - direct_panel_power

                    # if the hub's contribution (per channel) is larger than what the direct panels max is delivering (night, low light)
                    # then we can open the hub to max limit and use the inverter to limit it's output (more precise)
                    if sf_contribution / inv.getNrHubChannels() >= max(
                        inv.getDirectDCPowerValues()
                    ) * (inv.getEfficiency() / 100):
                        log.info(
                            f"Hub should contribute more ({sf_contribution:.1f}W) than what we currently get max from panels ({max(inv.getDirectDCPowerValues()) * (inv.getEfficiency() / 100):.1f}W), we will use the inverter for fast/precise limiting!"
                        )
                        hub_limit = (
                            hub.setOutputLimit(0)
                            if hub.getBypass()
                            else hub.setOutputLimit(hub.getInverseMaxPower())
                        )
                        direct_limit = sf_contribution / inv.getNrHubChannels()
                    else:
                        hub_limit = (
                            hub.setOutputLimit(0)
                            if hub.getBypass()
                            else hub.setOutputLimit(sf_contribution)
                        )
                        log.info(
                            f"Hub is willing to contribute {min(hub_limit, hub_contribution_ask):.1f}W of the requested {hub_contribution_ask:.1f}!"
                        )
                        direct_limit = getDirectPanelLimit(inv, hub, smt)
                        log.info(f"Direct connected panel limit is {direct_limit}W.")

    # likely no sun, not producing, eveything comes from hub
    else:
        log.info(
            f"Direct connected panel are producing {direct_panel_power:.1f}W, trying to get {hub_contribution_ask:.1f}W from hub."
        )
        # check what hub is currently  willing to contribute
        sf_contribution = getSFPowerLimit(hub, hub_contribution_ask)
        hub_limit = hub.setOutputLimit(hub.getInverseMaxPower())
        direct_limit = sf_contribution / inv.getNrHubChannels()
        log.info(
            f"Solarflow is willing to contribute {min(hub_limit, direct_limit):.1f}W (per channel) of the requested {hub_contribution_ask:.1f}!"
        )

    if direct_limit != None:
        limit = direct_limit

        if hub_limit > direct_limit > hub_limit - 10:
            limit = hub_limit - 10
        if direct_limit < hub_limit - 10 and hub_limit < hub.getInverseMaxPower():
            limit = hub_limit - 10

        inv_limit = inv.setLimit(limit)

    if remainder < 0:
        source = f"unknown: {-remainder:.1f}"
        if direct_panel_power == 0 and hub_power > 0 and hub.getDischargePower() > 0:
            source = f"battery: {-grid_power:.1f}W"
        # since we usually set the inverter limit not to zero there is always a little bit drawn from the hub (10-15W)
        if (
            direct_panel_power == 0
            and hub_power > 15
            and hub.getDischargePower() == 0
            and not hub.getBypass()
        ):
            source = f"hub solarpower: {-grid_power:.1f}W"
        if (
            direct_panel_power > 0
            and hub_power > 15
            and hub.getDischargePower() == 0
            and hub.getBypass()
        ):
            source = f"hub bypass: {-grid_power:.1f}W"
        if direct_panel_power > 0 and hub_power < 15:
            source = f"panels connected directly to inverter: {-remainder:.1f}"

        log.info(f"Grid feed in from {source}!")

    panels_dc = "|".join([f"{v:>2}" for v in inv.getDirectDCPowerValues()])
    hub_dc = "|".join([f"{v:>2}" for v in inv.getHubDCPowerValues()])

    now = datetime.now(tz=location.tzinfo)
    s = sun(location.observer, date=now, tzinfo=location.timezone)
    sunrise = s["sunrise"]
    sunset = s["sunset"]

    log.info(
        " ".join(
            f"Sun: {sunrise.strftime('%H:%M')} - {sunset.strftime('%H:%M')} \
             Demand: {demand:.1f}W, \
             Panel DC: ({direct_panel_power:.1f}W), \
             Hub DC: ({hub_power:.1f}W), \
             Inverter Limit: {inv_limit:.1f}W, \
             Hub Limit: {hub_limit:.1f}W".split()
        )
    )


def getOpts(configtype) -> dict:
    """Get the configuration options for a specific section from the global config.ini"""
    global config
    opts = {}
    for opt, opt_type in configtype.opts.items():
        t = opt_type.__name__
        try:
            if t == "bool":
                t = "boolean"
            converter = getattr(config, f"get{t}")
            opts.update({opt: opt_type(converter(configtype.__name__.lower(), opt))})
        except configparser.NoOptionError:
            log.info(
                f'No config setting found for option "{opt}" in section {configtype.__name__.lower()}!'
            )
    return opts


def limit_callback(client: mqtt_client, force=False):
    global lastTriggerTS
    dtu = client._userdata['dtu']
    #log.info("Smartmeter Callback!")
    now = datetime.now()
    if lastTriggerTS:
        elapsed = now - lastTriggerTS
        # ensure the limit function is not called too often (avoid flooding DTUs)
        if elapsed.total_seconds() >= steering_interval or force:
            if force and dtu.hasPendingUpdate():
                log.info(f'Force update blocked due to pending DTU update!')
                return False  
            
            lastTriggerTS = now
            limitHomeInput(client)
            return True

        else:
            return False
    else:
        lastTriggerTS = now
        limitHomeInput(client)
        return True


def deviceInfo(client: mqtt_client):
    limitHomeInput(client)


def updateConfigParams(client):
    global \
        config, \
        DISCHARGE_DURING_DAYTIME, \
        SUNRISE_OFFSET, \
        SUNSET_OFFSET, \
        MIN_CHARGE_POWER, \
        MAX_DISCHARGE_POWER, \
        BATTERY_HIGH, \
        BATTERY_LOW

    # only update if configparameters haven't been updated/read from MQTT
    if DISCHARGE_DURING_DAYTIME == None:
        DISCHARGE_DURING_DAYTIME = config.getboolean(
            "control", "discharge_during_daytime", fallback=None
        ) or bool(os.environ.get("DISCHARGE_DURING_DAYTIME", False))
        log.info(
            f"Updating DISCHARGE_DURING_DAYTIME from config file to {DISCHARGE_DURING_DAYTIME}"
        )
        client.publish(
            f"solarflow-hub/{sf_device_id}/control/dischargeDuringDaytime",
            str(DISCHARGE_DURING_DAYTIME),
            retain=True,
        )

    if SUNRISE_OFFSET == None:
        SUNRISE_OFFSET = config.getint("control", "sunrise_offset", fallback=60) or int(
            os.environ.get("SUNRISE_OFFSET", 60)
        )
        log.info(
            f"Updating SUNRISE_OFFSET from config file to {SUNRISE_OFFSET} minutes"
        )
        client.publish(
            f"solarflow-hub/{sf_device_id}/control/sunriseOffset",
            SUNRISE_OFFSET,
            retain=True,
        )

    if SUNSET_OFFSET == None:
        SUNSET_OFFSET = config.getint("control", "sunset_offset", fallback=60) or int(
            os.environ.get("SUNSET_OFFSET", 60)
        )
        log.info(f"Updating SUNSET_OFFSET from config file to {SUNSET_OFFSET} minutes")
        client.publish(
            f"solarflow-hub/{sf_device_id}/control/sunsetOffset",
            SUNSET_OFFSET,
            retain=True,
        )

    if MIN_CHARGE_POWER == None:
        MIN_CHARGE_POWER = config.getint(
            "control", "min_charge_power", fallback=None
        ) or int(os.environ.get("MIN_CHARGE_POWER", 0))
        log.info(f"Updating MIN_CHARGE_POWER from config file to {MIN_CHARGE_POWER}W")
        client.publish(
            f"solarflow-hub/{sf_device_id}/control/minChargePower",
            MIN_CHARGE_POWER,
            retain=True,
        )

    if MAX_DISCHARGE_POWER == None:
        MAX_DISCHARGE_POWER = config.getint(
            "control", "max_discharge_power", fallback=None
        ) or int(os.environ.get("MAX_DISCHARGE_POWER", 145))
        log.info(
            f"Updating MAX_DISCHARGE_POWER from config file to {MAX_DISCHARGE_POWER}W"
        )
        client.publish(
            f"solarflow-hub/{sf_device_id}/control/maxDischargePower",
            MAX_DISCHARGE_POWER,
            retain=True,
        )

    if BATTERY_LOW == None:
        BATTERY_LOW = config.getint("control", "battery_low", fallback=None)
        # or int(os.environ.get('BATTERY_LOW',2))
        log.info(f"Updating BATTERY_LOW from config file to {BATTERY_LOW}%")
        client.publish(
            f"solarflow-hub/{sf_device_id}/control/batteryTargetSoCMin",
            BATTERY_LOW,
            retain=True,
        )

    if BATTERY_HIGH == None:
        BATTERY_HIGH = config.getint("control", "battery_high", fallback=None) or int(
            os.environ.get("BATTERY_HIGH", 98)
        )
        log.info(f"Updating BATTERY_HIGH from config file to {BATTERY_HIGH}%")
        client.publish(
            f"solarflow-hub/{sf_device_id}/control/batteryTargetSoCMax",
            BATTERY_HIGH,
            retain=True,
        )


def run():
    hub_opts = getOpts(solarflow.Solarflow)
    dtuType = getattr(dtus, DTU_TYPE)
    dtu_opts = getOpts(dtuType)
    smtType = getattr(smartmeters, SMT_TYPE)
    smt_opts = getOpts(smtType)

    client = connect_mqtt()
    subscribe(client=client)

    log.info("Reading retained config settings from MQTT...")
    log.info(
        "Note: Solarflow Control persists initial configuration settings in your MQTT broker and will use those first (if found) to allow on-the-fly updates!"
    )
    log.info(
        "If you want to override these values from your config.ini you need to clear those retained topics in your broker first!"
    )
    client.loop_start()
    time.sleep(10)

    # if no config setting were found in MQTT (retained) then update config from config file
    updateConfigParams(client)

    log.info("Control Parameters:")
    log.info(f"  MIN_CHARGE_POWER = {MIN_CHARGE_POWER}")
    log.info(f"  MAX_DISCHARGE_LEVEL = {MAX_DISCHARGE_POWER}")
    log.info(f"  MAX_INVERTER_LIMIT = {MAX_INVERTER_LIMIT}")
    log.info(f"  MAX_INVERTER_INPUT = {MAX_INVERTER_INPUT}")
    log.info(f"  SUNRISE_OFFSET = {SUNRISE_OFFSET}")
    log.info(f"  SUNSET_OFFSET = {SUNSET_OFFSET}")
    log.info(f"  BATTERY_LOW = {BATTERY_LOW}")
    log.info(f"  BATTERY_HIGH = {BATTERY_HIGH}")
    log.info(f"  BATTERY_DISCHARGE_START = {BATTERY_DISCHARGE_START}")
    log.info(f"  DISCHARGE_DURING_DAYTIME = {DISCHARGE_DURING_DAYTIME}")

    hub = solarflow.Solarflow(client=client, callback=limit_callback, **hub_opts)
    dtu = dtuType(
        client=client, ac_limit=MAX_INVERTER_LIMIT, callback=limit_callback, **dtu_opts
    )
    smt = smtType(client=client, callback=limit_callback, **smt_opts)

    client.user_data_set({"hub": hub, "dtu": dtu, "smartmeter": smt})

    # switch the callback function for received MQTT messages to the delegating function
    client.on_message = on_message

    infotimer = RepeatedTimer(120, deviceInfo, client)

    # subscribe Hub, DTU and Smartmeter so that they can react on received messages
    hub.subscribe()
    dtu.subscribe()
    smt.subscribe()

    # ensure that the hubs min/max battery levels are set upon startup according to configuration, adjustments will be done if required by CT mode
    hub.setBatteryHighSoC(BATTERY_HIGH)
    hub.setBatteryLowSoC(BATTERY_LOW)

    # turn off the hub's buzzer (audio feedback for config settings change)
    hub.setBuzzer(False)
    # ensure hub's maximum inverter feed power is set according to configuration
    hub.setInverseMaxPower(MAX_INVERTER_INPUT)
    # ensure hub is in AC output mode
    hub.setACMode()
    # initially turn off bypass and disable auto-recover from bypass
    if hub.control_bypass:
        hub.setBypass(False)
        hub.setAutorecover(False)


def main(argv):
    global mqtt_host, mqtt_port, mqtt_user, mqtt_pwd
    global sf_device_id
    global location
    opts, args = getopt.getopt(
        argv, "hb:p:u:s:d:", ["broker=", "port=", "user=", "password="]
    )
    for opt, arg in opts:
        if opt == "-h":
            log.info("solarflow-control.py -b <MQTT Broker Host> -p <MQTT Broker Port>")
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
        log.error(
            "You need to provide a local MQTT broker (environment variable MQTT_HOST or option --broker)!"
        )
        sys.exit(0)
    else:
        log.info(f"MQTT Host: {mqtt_host}:{mqtt_port}")

    if mqtt_user is None or mqtt_pwd is None:
        log.info(f"MQTT User is not set, assuming authentication not needed")
    else:
        log.info(f"MQTT User: {mqtt_user}/{mqtt_pwd}")

    if sf_device_id is None:
        log.error(
            f"You need to provide a SF_DEVICE_ID (environment variable SF_DEVICE_ID or option --device)!"
        )
        sys.exit()
    else:
        log.info(f"Solarflow Hub: {sf_product_id}/{sf_device_id}")

    loc = MyLocation()
    if not LNG and not LAT:
        coordinates = loc.getCoordinates()
        if loc is None:
            coordinates = (LAT, LNG)
            log.info(f"Geocoordinates: {coordinates}")
    else:
        coordinates = (LAT, LNG)

    # location info for determining sunrise/sunset
    location = LocationInfo(
        timezone="Europe/Berlin", latitude=coordinates[0], longitude=coordinates[1]
    )

    run()


if __name__ == "__main__":
    main(sys.argv[1:])
