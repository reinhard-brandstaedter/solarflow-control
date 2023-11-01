## What is Solarflow Control

A tool to automatically control Zendure's Solarflow hub with more flexibility to match home power demand and without the official mobile app.

My intention was to use my existing telemetry from my smartmeter (using an Hichi IR reader) and my requirement to control charging and discharging in a better way than what is possible with the app.
Solarflow-Control is currently steering my Hub 24/7 with these capabilities:

- when there is enough solar power it charges the battery with at least 125W. If there is less solar power that goes to the battery first (battery priority) before feeding to home.
- if there is less demand from home than available solarpower the "over-production" goes to the battery.
- generally the output to home is always adjusted to what is needed. This guarantees that no solarpower is "wasted" and fed to the grid, but rather used to charge the battery.
- during night time it discharges the battery with a maximum of 145W but also adapts to the current demand

Originally the script used the Zendure developer MQTT telemetry data (bridged to a local MQTT broker) to make decisions. But meanwhile I have also figured out other ways to get the needed telemetry data into my local broker and now this is the preferred way.
For more information please see my other projects:

- [Solarflow Bluetooth Manager](https://github.com/reinhard-brandstaedter/solarflow-bt-manager) - how to get data from the Solarflow Hub via Bluetooth into a local MQTT
- [Solarflow Status Page](https://github.com/reinhard-brandstaedter/solarflow-statuspage) - a lean statuspage that displays Solarflow Hub's telemetry data

### Preamble
To control how much data is fed from the Solarflow Hub to Home (our main goal) we need to (i) find out how much power we need at any given time and (ii) be able to steer the output to home.
The first step can be best achieved by reading your home's smartmeter on the fly. There are various options to do that (PowerOpti, Shelly EM devices, Hichi Smartmeter reader, BYO devices, ..). I'm using a Hichi IR head to get the current house demand into a local MQTT broker.
The second step is the controlling of how much power the Solarflow Hub will feed to home. This can generally be achieved in two ways:

 - by limiting the connected microinverter so it only takes what is needed. The Solarflow "output to home" setting is set to maximum and no schedules or other profiles are active. As this requires a microinverter that can be controlled it might not work for every setup.
 What works well are microinverters that can be controlled via AhoyDTU or OpenDTU.

 - by setting the Solarflow Hub's own "output to home" parameter on the fly. In the mobile app you could do this manually or via schedules, but not very granular (only 100W steps at time of this writing). But the hub can also be controlled via MQTT in a better way. This is what we can use

 As you can see a lot of the controlling will involve a MQTT broker to exchange commands and telemetry. While some of that can also be achieved by talking to Zendure's Cloud MQTT we will be using a local MQTT for this and ultimately disconnect the SF Hub from Zendure's Cloud.

### How to use
Solarflow control is best run as a Docker container, to make any dependency problems easier. To get the latest version you can run:

```
docker pull rbrandstaedter/solarflow-control:latest
```

The parameters for the control script can bei either provided via environment variables or via config file ```config.ini``` located in the execution directory of the script, or the root directory of the container. Before we start running the control process let's take a look at the variables needed for steering:

*General Configuration*

| Variable            | Description         | Example             |
| ------------------- | ------------------- | ------------------- |
| SF_DEVICE_ID        | Your Solarflow's device ID. You can get this e.g. via the solarflow BT manager |   |
| SF_PRODUCT_ID       | The "product category" ID. No need to set |   |
| MQTT_HOST           | Your local MQTT broker IP/DNS-Name |   |
| MQTT_PORT           | The port your MQTT broker listens on (default:1883) |   |
| MQTT_USER           | The user to login to your MQTT broker (if needed) |   |
| MQTT_PWD            | Password for your local MQTT user account |   |

*Control/Steering Parameters*
| Variable            | Description         | Example             | Default             |
| ------------------- | ------------------- | ------------------- | ------------------- |
| BATTERY_LOW         | The SoC of the battery when it is considered empty. This is where discharging will stop. |   | 10 %|
| BATTERY_HIGH        | The Soc of the battery when it is considered full. |   | 98 %|
| MIN_CHARGE_LEVEL    | This is the minimum power that is "reserved" for charging the battery. If solarproduction is less than this it will be used for charging only |   | 125 W |
| MAX_DISCHARGE_LEVEL | The maximum discharging level that is used when running on battery power (e.g. during the night). The output will be between current demand (if lower than this) or the max discharge level. |   | 145 W |
| DAY_DISCHARGE_SOC   | The minimum state of charge of individual batteries in the stack that is required to also discharge throughout the day. I.e. all batteries must be above it before discharging starts. |   | 50 % |
| CHARGE_THROUGH_THRESHOLD | The minimum state of charge of the battery to allow discharging over night. If the battery has not reached this SoC the control will try to continue charging the next day without any discharging. This ensures that batteries get to 100% now and then |  | 60 % |
| INVERTER_MPPS       | The number of your inverter's MPPTs. This is used to determine the "limit multiplier" as most inverters split the limit across MPPTs |   | 4 |
| INVERTER_SF_INPUTS_USED | The number of inverter inputs used by Solarflow (1 or 2). This is needed to calculate the correct ratio of the applied limit  |   | 2 |
| LIMIT_INVERTER      | If set to "True" the limiting will be done via the inverter, if "False" the SF Hub will be limited directly |   | False |
| LATITUDE            | Your geolocation latitude. If not set geolocation is determined via geo-lookup by IP |   |   |
| LONGITUDE           | Your geolocation longitude. If not set geolocation is determined via geo-lookup by IP |   |   |
| SUNRISE_OFFSET           | Once the sunrises the idea is that energy goes to the battery first until day discharge is reached and more importantly the direct panels are used if available. However with sunrise, sun does not reach panels usualy, so an offset in minutes can be configured. This offset is by default 60 minutes; however this offset highly depends on your location, orientation and surroundings. |   | 60 |
| SUNSET_OFFSET           | Once the sunsets the idea is that energy comes from the battery, however with actual sunset that might be too late, hence the time before the sunset can be set. This offset is by default 60 minutes (i.e. 60 minutes before sunset batteries are being used+direct panels if applicable); however this offset highly depends on your location, orientation and surroundings. |   | 60 |

*Telemetry Input Sources - Mandatory*

All these telemetry data is needed for proper function. Those are specified as MQTT topics and must be available in your local MQTT broker.

| Variable            | Description         | Example             | Default             |
| ------------------- | ------------------- | ------------------- | ------------------- |
| TOPIC_HOUSE         | The current home power usage. E.g. provided by smartmeter reader as number. Multiple topics separated via "," are possible to support individual phases. | ```shellies/shellyem3/emeter/1/power,shellies/shellyem3/emeter/2/power,shellies/shellyem3/emeter/3/power``` |   |
| TOPIC_ACINPUT       | The current AC Input delivered by the inverter | ```solar/ac/power``` |   |
| TOPIC_DIRECT_PANEL  | The DC input power of solar panels connected directly to the inverter. Multiple topics separated via "," are possible | ```solar/123456789/1/power, solar/123456789/2/power``` |   |
| TOPIC_LIMIT_OPENDTU | The command topic for limiting an inverter via OpenDTU. Limits are published in absolut (W) | ```solar/116491132532/cmd/limit_nonpersistent_absolute``` |   |

*Solarflow Hub Telemetry Topics - Mandatory*

These telemetry data from the Solarflow Hub must be present in your MQTT. You can get them either by running the (Solarflow Statuspage), the (Solarflow BT Manager) or if your hub already reports to your local MQTT directly by running the (solarflow topic mapper script).

| Topic               | Description         |
| ------------------- | ------------------- |
| solarflow-hub/telemetry/solarInputPower | current solar inputpower to the hub |
| solarflow-hub/telemetry/electricLevel | average battery SoC |
| solarflow-hub/telemetry/outputPackPower | current energy going into the battery (charging) |
| solarflow-hub/telemetry/packInputPower | current energy going out of the battery into the house (discharging) |
| solarflow-hub/telemetry/outputHomePower | current enegy going into the inverter |
| solarflow-hub/telemetry/batteries/+/maxTemp | maximum temperature reported by individual batteries in the stack |
| solarflow-hub/telemetry/batteries/+/socLevel | SoC level of individual batteries in the stack |

### Examples
Below examples assume you have docker installed on your system (supported architectures are x86 and ARMv6 (Raspi)). You will also need a MQTT broker which has the above described topics/telemetry present. The examples are run on Linux/MacOS, if you are using Windows ther might be slight changes in the docker commands.

#### Offline Solarflow Hub reporting to your MQTT broker directly
This is the preferred an most efficient, reliable way as it doesn't depend on any cloud service. I'm using this 24/7.
I'm assuming your SF hub is already reporting to your local MQTT already. If not please see (how to do so)[https://github.com/reinhard-brandstaedter/solarflow-bt-manager#disconnecting-the-hub-from-the-cloud]

Launch the topic mapper, which is simply used to create beautified MQTT topic of the various state topics of the solarflow hub:

```
docker run -d -e  SF_DEVICE_ID=<your device id> \
              -e MQTT_HOST=<your mqtt host> \
              -e MQTT_USER=<your mqtt user> \
              -e MQTT_PWD=<your mqtt password> \
              --name solarflow-topicmapper rbrandstaedter/solarflow-topic-mapper:master
```

After the topic mapper is started you should see a ```solarflow-hub``` topic with various sub-topics and telemetry data from your hub. This data is needed to continue.

Create a ```config.ini``` file with your parameters:

```
### your local MQTT broker, the hub and other required data is reporting to
[local]
mqtt_host = 192.168.1.245
#mqtt_port = 
#mqtt_user =
#mqtt_pwd =

### in offline mode none of the below Zendure is needed
[zendure]
#login = 
#password = 
# since Zendure introduces regional brokers please set the one where you have registered your device
# Global: mq.zen-iot.com
# EU: mqtteu.zen-iot.com
#zen_mqtt = mq.zen-iot.com
# likewise for the API endpoint please select the correct one
# Global: https://app.zendure.tech
# EU: https://app.zendure.tech/eu
#zen_api = https://app.zendure.tech

```

Launch the statuspage in offline mode, providing the above config file:

```docker run -d -v ${PWD}/config.ini:/config.ini -p 0.0.0.0:5000:5000 --name solarflow-statuspage rbrandstaedter/solarflow-statuspage:master --offline```

Point your browser to http://<your docker host>:50000 and you should see the statuspage with updating telemetry data (some data might take a bit).

You can now start the control script, but first you will need to create a configuration file with your parameters:

Example ```config.ini```:
```
[solarflow]
sf_device_id = <your Device ID - you should see it in MQTT>
#sf_product_id =

[local]
mqtt_host = < IP of your MQTT>
#mqtt_port = 
#mqtt_user =
#mqtt_pwd =
#latitude =
#longitude =

[control]
battery_low = 2
battery_high = 98
min_charge_level = 125
max_discharge_level = 150
day_discharge_soc = 50
charge_through_threshold = 60
overage_limit = 15                                                      
max_inverter_limit = 800                                                
inverter_mppts = 4
inverter_sf_inputs_used = 1 
fast_change_offset = 200
limit_inverter = true

# window sizes to calculate moving averages of values to avoid overreacting to short spikes/drops
# use average of last X measurements of Solarflow solarinput 
sf_window = 5
# use average of last X measurements of house smartmeter/consumption
sm_window = 5
# use average of last X measurements of inverter output
inv_window = 5
# use average of last X measurements of inverter limit
limit_window = 5

# MQTT telemetry topics specify where solarflow control can read data for it's operation
# all topics must provide integer or float values (no json message format)
[mqtt_telemetry_topics]
# the topic that provides the current household power consumption, read from a smartmeter or equivalent
# you can also provide multiple topics (which will be added up), e.g for Shelly 3-Phase measurement devices
# by separating them with ","
# topic_house = shellies/shellyem3/emeter/1/power, shellies/shellyem3/emeter/2/power, shellies/shellyem3/emeter/3/power
topic_house = 

# topic for the microinverter input to home (e.g. from OpenDTU, AhouyDTU)
topic_acinput = 

# topics for panels power which are directly connected to the microinverter (optional)
# typically you would also get this from OpenDTU, AhouDTU or your inverter
# you can provide multiple topics by separating them with ","
# topic_direct_panel = solar/116491132532/1/power, solar/116491132532/2/power
#topic_direct_panel = 

# topics for telemetry read from Solarflow Hub
# Note: Solarflow doesn't directly write to these topics when publishing to your local MQTT broker
#       it rather writes to it's predefined topic.
#       Therefor it's recommended to either run the solarflow statuspage or the little topic mapper script to
#       "clean up" the topics and provide them at these locations
# See: https://github.com/reinhard-brandstaedter/solarflow-bt-manager/blob/master/src/solarflow-topic-mapper.py                                        
#topic_solarflow_solarinput = solarflow-hub/telemetry/solarInputPower
#topic_solarflow_electriclevel = solarflow-hub/telemetry/electricLevel
#topic_solarflow_outputpack = solarflow-hub/telemetry/outputPackPower
#topic_solarflow_packinput = solarflow-hub/telemetry/packInputPower
#topic_solarflow_outputhome = solarflow-hub/telemetry/outputHomePower
#topic_solarflow_maxtemp = solarflow-hub/telemetry/batteries/+/maxTemp
#topic_solarflow_battery_soclevel = solarflow-hub/telemetry/batteries/+/socLevel

# topic to steer your microinverter
# for OpenDTU and AhoyDTU please use the command topic that sets the ABSOLUTE limit (in watts) not the RELATIVE limit in percent
topic_limit_non_persistent = solar/116491132532/cmd/limit_nonpersistent_absolute
```

Launch the control script, providing the above config file:

```docker run -d -v ${PWD}/config.ini:/config.ini --name solarflow-control rbrandstaedter/solarflow-control:master```

#### Online Solarflow Hub using the statuspage as a Telemetry relay
In this setup you use the statuspage as a relay between Zendure's cloud MQTT and you own local MQTT. Your hub is still connected and reports data to the cloud (with all drawbacks). The statuspage logs in with your Zendure credentials and subscribes to their MQTT and pushes data to your local MQTT. The control script works with that data.

t.b.c