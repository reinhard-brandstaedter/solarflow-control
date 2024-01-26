## What is Solarflow Control

Solarflow Control originally was meant to automatically control Zendure's Solarflow hub with more flexibility to match home power demand and without the official mobile app.
Since its first use case it has now evolved into a more sophisticated solution to also control micro-inverters (mainly via OpenDTU and AhoyDTU), read current household demand from various smartmeter readers (Hichi, Tasmota, Shelly 3EM, PowerOpti, ...) to realize optimal charging/discharging and zero-feed-in solar generation.

It's main features are:
- Support power generation from solarpanels connected to Solarflow Hub AND directly to the inverter. Control the SF Hub and the inverter so that direct power generation takes precedence for feed in and panels connected to the HUB are used for charging the battery
- Auto-adjust houshold feed-in, via matching to demand read from typicall smartmeter readers. (e.g supporting all three phases of Shelly 3EM)
- Ensure SF battery health and drift regulation by automatically ensuring the battery gets fully charged every couple days even during low light seasons.
- Zero-feed-in: when possible solarflow-control will limit the input so that no input to the grid is performed.
- Auto-detect Inverter Channels from OpenDTU/AhoyDTU
- Integration with HomeAssistant
- Configurable minimum charge power of the battery
- Configurable maximum home output power
- Controlling "offline" Solarflow Hubs, without any dependency to an internet connection or Zendure's Cloud services, LAN only!


This project is tightly related to (and works together with) my other projects related to Solarflow Hub

- [Solarflow Bluetooth Manager](https://github.com/reinhard-brandstaedter/solarflow-bt-manager) - how to disconnect the Solarflow Hub fromt he cloud and make it work "offline"
- [Solarflow Status Page](https://github.com/reinhard-brandstaedter/solarflow-statuspage) - a lean statuspage that displays Solarflow Hub's telemetry data in realtime


![solarflow control](https://github.com/reinhard-brandstaedter/solarflow-control/blob/master/img/schema.png?raw=true)
*solarflow-control leverages telemetry data from all involved devices and controls them accordingly*

### Preamble
To control how much data is fed from the Solarflow Hub to Home (our main goal) we need to (i) find out how much power we need at any given time and (ii) be able to steer the output to home either via the inverter or the solarflow hub (or both), in dependency of any panels connected directly to the same inverter as solarflow.

The first step can be best achieved by reading your home's smartmeter on the fly. There are various options to do that (PowerOpti, Shelly EM devices, Hichi Smartmeter reader, BYO devices, ..). I'm using a Hichi IR head to get the current house demand into a local MQTT broker.
The second step is the controlling of how much power the Solarflow Hub will feed to home. This can generally be achieved in two ways:

 - by limiting the connected microinverter so it only takes what is needed. If there are panels directly connected to the inverter steering might also involve limiting the Solarflow Hub

 - by setting the Solarflow Hub's own "output to home" parameter on the fly. In the mobile app you could do this manually or via schedules, but not very granular (only 100W steps at time of this writing). But the hub can also be controlled via MQTT in a better way. This is what we can use

 As most components involved typically exchange telemetry via MQTT or can be controlled via MQTT I recommend to set up your own MQTT broker (if you haven't already done so for other home automation purposes)

### How to use
A prerequisite is to take your Solarflow Hub "offline", meaning to disconnect it from the Zendure cloud and have it only reporting to your local infrastructure/MQTT.
Solarflow control is best run via Docker (or even docker-compose), this avoids dependency problems and ensures portability. To get the latest version you can run:

```
docker pull rbrandstaedter/solarflow-control:latest
```

The configuration is done via the ```config.ini``` file which must be mounted into the container (read-only).
Example ```config.ini```

```
[global]
# DTY Type: either OpenDTU or AhoyDTU
dtu_type = OpenDTU
# Smartmeter Type: either Smartmeter (generic, Tasmota, Hichi, ...), PowerOpti, Shelly3EM
smartmeter_type = Smartmeter

# Geolocation LAT/LNG (e.g. latitude = 48.234, longitude = 12.534)
# if not set we will try to get it via geo-ip lookup, might be inacurate
#latitude = 
#longitude = 

# Offset in minutes after sunrise/before sunset. Can be used to set the duration of what is considered "night"
#sunrise_offset = 
#sunset_offset = 

[solarflow]
# The device ID of your Solarflow Hub (typically 8 characters), you can get these either with solarflow-bt-manager or the solarflow-statuspage
device_id = 5ak8yGU7

# The time interval in hours that solarflow-control will try to ensure a full battery
# (i.e. no discharging if battry hasn't been at 100% for this long)
full_charge_interval = 32

[mqtt]
# Your local MQTT host configuration
mqtt_host = 192.168.1.245
#mqtt_port = 
#mqtt_user =
#mqtt_pwd =

[opendtu]
# The MQTT base topic your OpenDTU reports to (as configured in OpenDTU UI)
base_topic = solar
# your Inverters serial number
inverter_serial = 116491132532

# List of indices of the inverter channels/ports (as reported in the DTU) that the Solarflow Hub is connected to
# typically the index starts at 1 as 0 is the output channel of the inverter
# e.g. 1,3 or 3 or [1,3]
sf_inverter_channels = [3]

[ahoydtu]
# The MQTT base topic your AhoyDTU reports to (as configured in AhoyDTU UI)
base_topic = solar
# The inverter ID in AhoyDTU: typically 1 for the first inverter
inverter_id = 1
# List of indices of the inverter channels/ports (as reported in the DTU) that the Solarflow Hub is connected to
# typically the index starts at 1 as 0 is the output channel of the inverter
# e.g. 1,3 or 3 or [1,3]
sf_inverter_channels = [3]

# the max output power of your inverter, used to calculate correct absolute values
#inverter_max_power = 2000

# The name of the inverter in AhoyDTU
#inverter_name = AhoyDTU

[smartmeter]
# The MQTT base topic your Hichi, Tasmota, generic smartmeter reader reports to
base_topic = tele/E220/SENSOR
# if the published value at the base_topic is a JSON type then these accessors are used to get the power values
# e.g. if Smartmeter reader posts { "Power": {"Power_curr": 120, "Total_in": 12345.6} }
cur_accessor = Power.Power_curr
total_accessor = Power.Total_in

[poweropti]
# Username and password for you Powerfox API to get readings (internet connection required)
poweropti_user = <PowerFox API user>
poweropti_password = <Powerfox API password>

[shellyem3]
# The MQTT base topic your Shelly 3EM (Pro) is posting it's telemetry data to
# Note: you have to configure your Shelly to use MQTT
base_topic = shellies/shellyem3/

[control]
min_charge_power = 125
max_discharge_power = 150
max_inverter_limit = 800                                                
limit_inverter = true
inverter_min_limit = 10
```

Run the container with this command, mounting the ```config.ini```:

```docker run -v ${PWD}/src/config.ini:/solarflow/config.ini --name solarflow-control rbrandstaedter/solarflow-control:latest```

The initial startup will prompt all the settings applied and also the MQTT topics that are used. Note the lines with "... subscribing:" are the topics that should be present in your MQTT broker. Those are relevant for the tool to work properly. 

```
2023-11-07 12:04:41,610:INFO: MQTT Host: 192.168.1.245:1883
2023-11-07 12:04:41,610:INFO: MQTT User is not set, assuming authentication not needed
2023-11-07 12:04:41,610:INFO: Solarflow Hub: 73bkTV/5ak8yGU7
2023-11-07 12:04:41,610:INFO: Limit via inverter: True
2023-11-07 12:04:41,610:INFO: Control Parameters:
2023-11-07 12:04:41,610:INFO:   MIN_CHARGE_POWER = 125
2023-11-07 12:04:41,610:INFO:   MAX_DISCHARGE_LEVEL = 150
2023-11-07 12:04:41,610:INFO:   MAX_INVERTER_LIMIT = 800
2023-11-07 12:04:41,610:INFO:   MAX_INVERTER_INPUT = 675
2023-11-07 12:04:41,610:INFO:   SUNRISE_OFFSET = 120
2023-11-07 12:04:41,610:INFO:   SUNSET_OFFSET = 120
2023-11-07 12:04:42,388:INFO: Requested https://nominatim.openstreetmap.org/search?q=Munich+%28Moosach%29%2C+Bavaria+DE&format=jsonv2&addressdetails=1&limit=1
2023-11-07 12:04:42,391:INFO: IP Address: 93.104.xxx.xxx
2023-11-07 12:04:42,391:INFO: Location: Munich (Moosach), Bavaria, DE
2023-11-07 12:04:42,391:INFO: Coordinates: (Lat: 48.174002200000004, Lng: 11.534082703428721)
2023-11-07 12:04:42,392:INFO: Using OpenDTU: Base topic: solar/116491132532, Limit topic: solar/116491132532/cmd/limit_nonpersistent_absolute, SF Channels: [3]
2023-11-07 12:04:42,392:INFO: Using Smartmeter: Base topic: tele/E220/SENSOR, Current power accessor: Power.Power_curr, Total power accessor: Power.Total_in
2023-11-07 12:04:42,393:INFO: Connected to MQTT Broker!
2023-11-07 12:04:42,393:INFO: Hub subscribing: /73bkTV/5ak8yGU7/properties/report
2023-11-07 12:04:42,393:INFO: Hub subscribing: solarflow-hub/5ak8yGU7/telemetry/solarInputPower
2023-11-07 12:04:42,393:INFO: Hub subscribing: solarflow-hub/5ak8yGU7/telemetry/electricLevel
2023-11-07 12:04:42,393:INFO: Hub subscribing: solarflow-hub/5ak8yGU7/telemetry/outputPackPower
2023-11-07 12:04:42,393:INFO: Hub subscribing: solarflow-hub/5ak8yGU7/telemetry/packInputPower
2023-11-07 12:04:42,393:INFO: Hub subscribing: solarflow-hub/5ak8yGU7/telemetry/outputHomePower
2023-11-07 12:04:42,393:INFO: Hub subscribing: solarflow-hub/5ak8yGU7/telemetry/outputLimit
2023-11-07 12:04:42,393:INFO: Hub subscribing: solarflow-hub/5ak8yGU7/telemetry/masterSoftVersion
2023-11-07 12:04:42,393:INFO: Hub subscribing: solarflow-hub/5ak8yGU7/telemetry/batteries/+/socLevel
2023-11-07 12:04:42,393:INFO: Hub subscribing: solarflow-hub/5ak8yGU7/control/#
2023-11-07 12:04:42,394:INFO: DTU subscribing: solar/116491132532/0/powerdc
2023-11-07 12:04:42,394:INFO: DTU subscribing: solar/116491132532/+/power
2023-11-07 12:04:42,394:INFO: DTU subscribing: solar/116491132532/status/producing
2023-11-07 12:04:42,394:INFO: DTU subscribing: solar/116491132532/status/reachable
2023-11-07 12:04:42,394:INFO: DTU subscribing: solar/116491132532/status/limit_absolute
2023-11-07 12:04:42,394:INFO: DTU subscribing: solarflow-hub/+/control/dryRun
2023-11-07 12:04:42,394:INFO: Smartmeter subscribing: tele/E220/SENSOR
...
```

After a short time you should see additional log lines for the Solarflow Hub (HUB), the inverter (INV) and the smartmeter (SMT). Here you will see the individual components (HUB,INV,SMT) slowly populating with data from MQTT.
If either of them doesn't update data you likely have a issue with the MQTT subscription topics.

```
...
2023-11-07 12:04:57,402:INFO: HUB: S:-1.0W [ ], B: -1% (-1), C: 0W, F:20.1h, E:194.8h, H: -1W, L: -1W
2023-11-07 12:04:57,402:INFO: INV: AC:27.7W, DC:29.2W (29.2|0.0|0.0|0.0), L:138.0W
2023-11-07 12:04:57,402:INFO: SMT: T:Smartmeter P:246.0W [ 246.0 ]
2023-11-07 12:05:12,414:INFO: HUB: S:-1.0W [ ], B: 8% (-1), C: 0W, F:20.1h, E:194.8h, H: -1W, L: -1W
2023-11-07 12:05:12,414:INFO: INV: AC:27.8W, DC:29.4W (29.4|0.0|0.0|0.0), L:140.0W
2023-11-07 12:05:12,414:INFO: SMT: T:Smartmeter P:247.1W [ 246.0,243.0,249.0 ]
2023-11-07 12:05:27,424:INFO: HUB: S:-1.0W [ ], B: 8% (-1), C: 0W, F:20.1h, E:194.8h, H: -1W, L: -1W
2023-11-07 12:05:27,424:INFO: INV: AC:28.0W, DC:29.7W (29.7|0.0|0.0|0.0), L:140.0W
2023-11-07 12:05:27,424:INFO: SMT: T:Smartmeter P:247.6W [ 246.0,243.0,249.0,248.0 ]
...
```

After some more time you should see a ```Triggering telemetry update ...```. This forces the Solarflow hub every now and then to send all it's telemetry data (like battery state, etc.).
Finally, when enough data is collected, you will see the tool starting it's work:

```
...
2023-11-07 12:05:42,394:INFO: Triggering telemetry update: iot/73bkTV/5ak8yGU7/properties/read
2023-11-07 12:05:42,434:INFO: HUB: S:-1.0W [ ], B: 8% (-1), C: 0W, F:20.1h, E:194.8h, H: -1W, L: -1W
2023-11-07 12:05:42,434:INFO: INV: AC:28.2W, DC:30.0W (30.0|0.0|0.0|0.0), L:140.0W
2023-11-07 12:05:42,434:INFO: SMT: T:Smartmeter P:245.8W [ 246.0,243.0,249.0,248.0,242.0,247.0 ]
2023-11-07 12:05:57,444:INFO: HUB: S:26.0W [ 26.0 ], B: 8% (10| 3|11), C: 24W, F:20.1h, E:194.8h, H: 0W, L: 0W
2023-11-07 12:05:57,445:INFO: INV: AC:28.5W, DC:30.2W (30.2|0.0|0.0|0.0), L:140.0W
2023-11-07 12:05:57,445:INFO: SMT: T:Smartmeter P:236.0W [ 243.0,249.0,248.0,242.0,247.0,221.0 ]
2023-11-07 12:05:57,445:INFO: Direct connected panels can't cover demand 30.2W/264.5W, trying to get rest (234.3W) from SF.
2023-11-07 12:05:57,445:INFO: Checking if Solarflow is willing to contribute 234.3W!
2023-11-07 12:05:57,445:INFO: Sun: 07:08 - 16:46 - Solarflow limit:  0.0W - Decision path: 2.2.
2023-11-07 12:05:57,445:INFO: Setting inverter output limit to 148 W (1 min moving average of 37W x 4)
2023-11-07 12:05:57,446:INFO: Not setting solarflow output limit to  0.0W as it is identical to current limit!
2023-11-07 12:05:57,446:INFO: Demand: 264.5W, Panel DC: (30.2|0.0|0.0), Hub DC: (0.0), Inverter Limit: 148.0W, Hub Limit: 0.0W
...
```

## Examples

See the [examples](/examples/) for in-detail setup instructions and templates to get started with.

## Donations

If you read this far you probably are really interested or maybe a happy user. If you like it and would like to buy me a coffee:
[![Donate](https://img.shields.io/badge/Donate-PayPal-green.svg)](https://www.paypal.com/cgi-bin/webscr?cmd=_s-xclick&hosted_button_id=RUZP3LCKH56CU)
