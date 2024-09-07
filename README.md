## What is Solarflow Control

Solarflow Control originally was meant to automatically control Zendure's Solarflow hub with more flexibility to match home power demand and without the official mobile app.
Since its first use case it has now evolved into a more sophisticated solution to also control micro-inverters (mainly via OpenDTU and AhoyDTU), read current household demand from various smartmeter readers (Hichi, Tasmota, Shelly 3EM, PowerOpti, ...) to realize optimal charging/discharging and auto-adaptive limitation based on current demand..

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

A good starting point is the [template config.ini](./src/config.ini)

Run the container with this command, mounting the ```config.ini```:

```docker run -v ${PWD}/src/config.ini:/solarflow/config.ini --name solarflow-control rbrandstaedter/solarflow-control:latest```

The initial startup will prompt all the settings applied and also the MQTT topics that are used. Note the lines with "... subscribing:" are the topics that should be present in your MQTT broker. Those are relevant for the tool to work properly. 

```
2024-04-09 22:39:01,600:INFO: MQTT Host: 192.168.1.245:1883
2024-04-09 22:39:01,600:INFO: MQTT User is not set, assuming authentication not needed
2024-04-09 22:39:01,600:INFO: Solarflow Hub: 73bkTV/5ak8yGU7
2024-04-09 22:39:01,600:INFO: Limit via inverter: True
2024-04-09 22:39:01,600:INFO: Control Parameters:
2024-04-09 22:39:01,600:INFO:   MIN_CHARGE_POWER = 225
2024-04-09 22:39:01,600:INFO:   MAX_DISCHARGE_LEVEL = 350
2024-04-09 22:39:01,600:INFO:   MAX_INVERTER_LIMIT = 800
2024-04-09 22:39:01,600:INFO:   MAX_INVERTER_INPUT = 575
2024-04-09 22:39:01,600:INFO:   SUNRISE_OFFSET = 120
2024-04-09 22:39:01,600:INFO:   SUNSET_OFFSET = 120
2024-04-09 22:39:01,921:INFO: IP Address: xxx.xxx.xxx.xxx
2024-04-09 22:39:01,921:INFO: Location: Munich, Bavaria, Germany
2024-04-09 22:39:01,922:INFO: Coordinates: (Lat: xx.xx, Lng: xx.xx)
2024-04-09 22:39:01,923:INFO: Using OpenDTU: Base topic: solar/116491132532, Limit topic: solar/116491132532/cmd/limit_nonpersistent_absolute, SF Channels: [3], AC Limit: 800
2024-04-09 22:39:01,923:INFO: Using Smartmeter: Base topic: tele/E220/SENSOR, Current power accessor: Power.Power_curr, Total power accessor: Power.Total_in
2024-04-09 22:39:01,923:INFO: Connected to MQTT Broker!
2024-04-09 22:39:01,924:INFO: Hub subscribing: /73bkTV/5ak8yGU7/properties/report
2024-04-09 22:39:01,924:INFO: Hub subscribing: solarflow-hub/5ak8yGU7/telemetry/solarInputPower
2024-04-09 22:39:01,924:INFO: Hub subscribing: solarflow-hub/5ak8yGU7/telemetry/electricLevel
2024-04-09 22:39:01,924:INFO: Hub subscribing: solarflow-hub/5ak8yGU7/telemetry/outputPackPower
2024-04-09 22:39:01,924:INFO: Hub subscribing: solarflow-hub/5ak8yGU7/telemetry/packInputPower
2024-04-09 22:39:01,924:INFO: Hub subscribing: solarflow-hub/5ak8yGU7/telemetry/outputHomePower
2024-04-09 22:39:01,924:INFO: Hub subscribing: solarflow-hub/5ak8yGU7/telemetry/outputLimit
2024-04-09 22:39:01,924:INFO: Hub subscribing: solarflow-hub/5ak8yGU7/telemetry/inverseMaxPower
2024-04-09 22:39:01,924:INFO: Hub subscribing: solarflow-hub/5ak8yGU7/telemetry/masterSoftVersion
2024-04-09 22:39:01,924:INFO: Hub subscribing: solarflow-hub/5ak8yGU7/telemetry/pass
2024-04-09 22:39:01,924:INFO: Hub subscribing: solarflow-hub/5ak8yGU7/telemetry/batteries/+/socLevel
2024-04-09 22:39:01,924:INFO: Hub subscribing: solarflow-hub/5ak8yGU7/telemetry/batteries/+/totalVol
2024-04-09 22:39:01,924:INFO: Hub subscribing: solarflow-hub/5ak8yGU7/control/#
2024-04-09 22:39:01,924:INFO: DTU subscribing: solar/116491132532/0/powerdc
2024-04-09 22:39:01,925:INFO: DTU subscribing: solar/116491132532/+/power
2024-04-09 22:39:01,925:INFO: DTU subscribing: solar/116491132532/status/producing
2024-04-09 22:39:01,925:INFO: DTU subscribing: solar/116491132532/status/reachable
2024-04-09 22:39:01,925:INFO: DTU subscribing: solar/116491132532/status/limit_absolute
2024-04-09 22:39:01,925:INFO: DTU subscribing: solar/116491132532/status/limit_relative
2024-04-09 22:39:01,925:INFO: DTU subscribing: solarflow-hub/+/control/dryRun
2024-04-09 22:39:01,925:INFO: Smartmeter subscribing: tele/E220/SENSOR
2024-04-09 22:39:01,966:INFO: Set ChargeThrough: False
2024-04-09 22:39:01,966:INFO: Reading last full time: 2024-04-08 18:41:38
2024-04-09 22:39:01,966:INFO: Reading last empty time: 2024-04-09 22:29:59
2024-04-09 22:39:01,966:INFO: Reading battery target mode: charging
2024-04-09 22:39:02,091:INFO: Rapid rise in demand detected, clearing buffer!
2024-04-09 22:39:02,092:INFO: HUB: S:-1.0W [ ], B: -1% (-1), V:-1.0V (-1.0), C: 0W, P:False, F:28.0h, E:0.2h, H: -1W, L: -1W
2024-04-09 22:39:02,092:INFO: INV: AC:359.5W, AC_Prediction: 359.5W, DC:378.4W, DC_prediction: 378.4W (0.0|0.0|378.4|0.0), L:1400.0W [ -1W]
2024-04-09 22:39:02,092:INFO: SMT: T:Smartmeter P:2194.0W [ 2194.0,2194.0 ] Predict: 2194.0W
2024-04-09 22:39:02,092:INFO: SMT triggers limit function: 2194.0 -> 2194.0: executed
2024-04-09 22:39:17,396:INFO: Determined inverter's max capacity: 2000.0
...
```

After a short time you should see additional log lines for the Solarflow Hub (HUB), the inverter (INV) and the smartmeter (SMT). Here you will see the individual components (HUB,INV,SMT) slowly populating with data from MQTT.
If either of them doesn't update data you likely have a issue with the MQTT subscription topics.

```
...
2024-04-10 09:22:02,203:INFO: Triggering telemetry update: iot/73bkTV/5ak8yGU7/properties/read
2024-04-10 09:22:20,593:INFO: HUB: S:63.3W [ 65.6,65.6,63.3 ], B: 1% ( 2| 1| 1), V:46.8V (46.7|46.9|46.8), C: 52W, P:False, F:38.7h, E:0.6h, H: 8W, L:700W
2024-04-10 09:22:20,595:INFO: INV: AC:11.4W, AC_Prediction: 11.0W, DC:12.0W, DC_prediction: 11.6W (0.0|0.0|12.1|0.0), L:40.0W [2000.0W]
2024-04-10 09:22:20,597:INFO: SMT: T:Smartmeter P:203.0W [ 196.7,196.7,201.0,198.8,198.2,208.5 ] Predict: 188.3W
2024-04-10 09:22:20,597:INFO: Direct connected panels (0.0W) can't cover demand (219.9W), trying to get rest from hub.
2024-04-10 09:22:20,597:INFO: Checking if Solarflow is willing to contribute 219.9W ...
2024-04-10 09:22:20,597:INFO: Based on time, solarpower (63.3W) minimum charge power (225W) and bypass state (False), hub could contribute  0.0W - Decision path: 2.2.
2024-04-10 09:22:20,597:INFO: Hub should contribute more (0.0W) than what we currently get from panels (0.0W), we will use the inverter for fast/precise limiting!
2024-04-10 09:22:20,597:INFO: Not setting solarflow output limit to 700.0W as it is identical to current limit!
2024-04-10 09:22:20,597:INFO: Not setting inverter output limit as it is identical to current limit!
2024-04-10 09:22:20,597:INFO: Sun: 06:33 - 19:57 Demand: 219.9W, Panel DC: (0.0|0.0|0.0), Hub DC: (12.1), Inverter Limit: 40.0W, Hub Limit: 700.0W
2024-04-10 09:22:20,597:INFO: SMT triggers limit function: 198.2 -> 208.5: executed
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


## Q&A
#### How can I enable/disable the charge-thorough feature?
By default solarflow-control has a charge-through feature which ensures that the batteries of the hub are fully charged once in a while. This interval can be set via the configuration setting:

```
[solarflow]
# The time interval in hours that solarflow-control will try to ensure a full battery
# (i.e. no discharging if battry hasn't been at 100% for this long)
full_charge_interval = 120
```

This is mostly intended for periods of low-light (winter) when otherwise the battery would barely reach a full cycle (ensure battry health). In case the charge through mode is active you would then see a log message like this, and the hub will not be discharged untill it reaches 100% state of charge again.

```
2024-04-05 10:09:08,159:INFO: Battery hasn't fully charged for 137.2 hours! To ensure it is fully charged at least every 120hrs not discharging now!
```

It is completely up to you how frequently you want to ensure a full charge. During Summer times you likely will never run into charge through mode anyway. I'm currently using 120 hours for the interval.
To switch this behavior on the fly (e.g. if you want to enforce or stop a discharge manually) you can post a retained topic to your MQTT broker:

| topic | content | 
| ----- | ------- |
| solarflow-hub/5ak8yGU7/control/chargeThrough | ON/OFF |

#### How do I use the Bypass control feature?
Usually the hub's firmware controls the Bypass feature (direct solarinput to home output when battery is full). However this sometimes works unreliable, or switches too often. You can let solarflow-control control of the Bypass by setting the option in your ```config.ini```:

```
[solarflow]
control_bypass = true
```

This will turn the bypass on/off with the following logic, trying to perform as few unnecessary switches as possible:
- after sunrise it is potentially allowed to switch the bypass on
- when the battery reaches 100% the bypass is turned on
- the bypass is kept on until sunset - ```sunset_offset``` (this might change still, depending on feedback)
- after turning the bypass of it is not allowed to turn it on until the next sunrise

In the logs you will find some bypass information of the hub:

```
INFO: HUB: S:0.0W [ 0.0 ], B: 80% (80|82|80), V:49.4V (49.3|49.4|49.4), C:-360W, P:False (manual, not possible), F:2.5h, E:109.3h, H:343W, L:700W
```

The ```P:False (manual, not possible)``` part tells you that the Bypass is on/off (P:False|True), that the hub reported mode is via firmware (=auto) or done by solarflow-control (=manual) and if a change/enabling of the bypass is currently possible.

#### What does the ``` zero_offset``` configuration parameter of smartmeters do?
If you see a lot of feed-in to the grid (especially when discharging the battery during rather constant demand), you can use to "shift" the "zero-point" of your smartmeter readings. In solarflow-controls logic this will then use the adjusted point for calculating the output from the hub to the house.
Note that short feed-in situations are OK, depending if your household demand changes quickly there will be always a little bit of lag in adjusting limits, so at the end of a high-usage contribution you will always see a little bit of overcontribution.