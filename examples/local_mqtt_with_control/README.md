## Lokal Mosquitto MQTT and Solarflow-Control in Docker
This example configuration starts a Mosquitto MQTT broker and solarflow-control with a docker-compose stack.
If you haven't got your own MQTT broker already deployed this is probably the quickest way to get started.
However you will need to make sure that your Solarflow Hub, your inverter and your smartmeter reader are all reporting to this MQTT broker.

### Preparation
Before getting started a few preparation steps are needed:

- Get (Docker)[https://www.docker.com/get-started/] on the device you want to run this on (RasPi, NAS, PC, ...) 
- Install (docker-compose)[https://docs.docker.com/compose/] (if it's not already included)
- take your (Solarflow Hub offline)[https://github.com/reinhard-brandstaedter/solarflow-bt-manager#disconnecting-the-hub-from-the-cloud] and have it report to the MQTT we will startup (<IP of the device>:1883).
- have your smartmeter reader device report to this MQTT
- have your inverter DTU report to this MQTT
- get (MQTT explorer)[https://mqtt-explorer.com/] for debugging purposes (install on any machine in your network)
- download all the files in this directory to your device


### Launch MQTT and Pre-flight Checks
Before we start with solarflow-control we need to make sure we have all required data. Start the included MQTT service, which creates a open MQTT Broker that listens on port 1883:

```
# docker-compose up -d mqtt
```

Use MQTT Explorer and check if you can connect to the service and check relevant data.

If you haven't configured your DTU, Smartmeter reader and Solarflow Hub yet. Do so now and have them report to this MQTT broker!
Take a note of the ```device id``` of your hub (part of the topic the hub reports to), the ```DTU base topic``` and the ```Smartmeter base topic```.
These need to go to the configuration file below

### Configuration and Startup of Solarflow-Control
Take a look at the ```config.ini``` in this directory and adjust the required settings: Device ID, DTU Type, Smartmeter Type, MQTT topics (from above).
Once you have adjusted the configuration file you can start the solarflow-control script with:

```# docker-compose up control```

If everything was configured correctly you should see an output like this:

```
sf-control | 2023-11-08 13:59:53,584:INFO: MQTT Host: sf-mqtt:1883
sf-control | 2023-11-08 13:59:53,584:INFO: MQTT User is not set, assuming authentication not needed
sf-control | 2023-11-08 13:59:53,584:INFO: Solarflow Hub: 73bkTV/abasdad
sf-control | 2023-11-08 13:59:53,584:INFO: Limit via inverter: True
sf-control | 2023-11-08 13:59:53,584:INFO: Control Parameters:
sf-control | 2023-11-08 13:59:53,584:INFO:   MIN_CHARGE_POWER = 125
sf-control | 2023-11-08 13:59:53,584:INFO:   MAX_DISCHARGE_LEVEL = 150
sf-control | 2023-11-08 13:59:53,584:INFO:   MAX_INVERTER_LIMIT = 800
sf-control | 2023-11-08 13:59:53,585:INFO:   MAX_INVERTER_INPUT = 675
sf-control | 2023-11-08 13:59:53,585:INFO:   SUNRISE_OFFSET = 60
sf-control | 2023-11-08 13:59:53,585:INFO:   SUNSET_OFFSET = 60
sf-control | 2023-11-08 13:59:54,157:INFO: Requested https://nominatim.openstreetmap.org/search?q=Munich+%28Moosach%29%2C+Bavaria+DE&format=jsonv2&addressdetails=1&limit=1
sf-control | 2023-11-08 13:59:54,159:INFO: IP Address: 93.104.114.xxx
sf-control | 2023-11-08 13:59:54,160:INFO: Location: Munich (Moosach), Bavaria, DE
sf-control | 2023-11-08 13:59:54,160:INFO: Coordinates: (Lat: xxx, Lng: xxx)
sf-control | 2023-11-08 13:59:54,162:INFO: Using OpenDTU: Base topic: solar/116491132532, Limit topic: solar/116491132532/cmd/limit_nonpersistent_absolute, SF Channels: [3]
sf-control | 2023-11-08 13:59:54,162:INFO: Using Smartmeter: Base topic: tele/E220/SENSOR, Current power accessor: Power.Power_curr, Total power accessor: Power.Total_in
sf-control | 2023-11-08 13:59:54,163:INFO: Connected to MQTT Broker!
sf-control | 2023-11-08 13:59:54,164:INFO: Hub subscribing: /73bkTV/abasdad/properties/report
sf-control | 2023-11-08 13:59:54,164:INFO: Hub subscribing: solarflow-hub/abasdad/telemetry/solarInputPower
sf-control | 2023-11-08 13:59:54,164:INFO: Hub subscribing: solarflow-hub/abasdad/telemetry/electricLevel
sf-control | 2023-11-08 13:59:54,164:INFO: Hub subscribing: solarflow-hub/abasdad/telemetry/outputPackPower
sf-control | 2023-11-08 13:59:54,164:INFO: Hub subscribing: solarflow-hub/abasdad/telemetry/packInputPower
sf-control | 2023-11-08 13:59:54,165:INFO: Hub subscribing: solarflow-hub/abasdad/telemetry/outputHomePower
sf-control | 2023-11-08 13:59:54,165:INFO: Hub subscribing: solarflow-hub/abasdad/telemetry/outputLimit
sf-control | 2023-11-08 13:59:54,165:INFO: Hub subscribing: solarflow-hub/abasdad/telemetry/masterSoftVersion
sf-control | 2023-11-08 13:59:54,165:INFO: Hub subscribing: solarflow-hub/abasdad/telemetry/batteries/+/socLevel
sf-control | 2023-11-08 13:59:54,165:INFO: Hub subscribing: solarflow-hub/abasdad/control/#
sf-control | 2023-11-08 13:59:54,166:INFO: DTU subscribing: solar/116491132532/0/powerdc
sf-control | 2023-11-08 13:59:54,166:INFO: DTU subscribing: solar/116491132532/+/power
sf-control | 2023-11-08 13:59:54,166:INFO: DTU subscribing: solar/116491132532/status/producing
sf-control | 2023-11-08 13:59:54,166:INFO: DTU subscribing: solar/116491132532/status/reachable
sf-control | 2023-11-08 13:59:54,167:INFO: DTU subscribing: solar/116491132532/status/limit_absolute
sf-control | 2023-11-08 13:59:54,167:INFO: DTU subscribing: solarflow-hub/+/control/dryRun
sf-control | 2023-11-08 13:59:54,167:INFO: Smartmeter subscribing: tele/E220/SENSOR
sf-control | 2023-11-08 14:00:04,174:INFO: HUB: S:-1.0W [ ], B: -1% (-1), C: 0W, F:-1.0h, E:-1.0h, H: -1W, L: -1W
sf-control | 2023-11-08 14:00:04,175:INFO: INV: AC:0.0W, DC:0.0W (), L: 0W
sf-control | 2023-11-08 14:00:04,175:INFO: SMT: T:Smartmeter P:0.0W [ ]
```

After some time you should see more log lines with updated data and solarflow-control will start working.