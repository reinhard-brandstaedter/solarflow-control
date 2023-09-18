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

Currently most of the parameters for the controlling must be provided via environment variables passed to the container. Before we start running the control process let's take a look at the variables needed for steering:

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
| INVERTER_MPPS       | The number of your inverter's MPPTs. This is used to determine the "limit multiplier" as most inverters split the limit across MPPTs |   | 4 |
| INVERTER_SF_INPUTS_USED | The number of inverter inputs used by Solarflow (1 or 2). This is needed to calculate the correct ratio of the applied limit  |   | 2 |
| LIMIT_INVERTER      | If set to "True" the limiting will be done via the inverter, if "False" the SF Hub will be limited directly |   | False |
| LATITUDE            | Your geolocation latitude. If not set geolocation is determined via geo-lookup by IP |   |   |
| LONGITUDE           | Your geolocation longitude. If not set geolocation is determined via geo-lookup by IP |   |   |

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

