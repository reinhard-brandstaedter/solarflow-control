### Configuration of SF-Control via MQTT
Solarflow control is reading a few dynamic control parameters from MQTT topic which allow us to change settings on the run (apart from the settings that are defined in the config.ini)

Currently these parameters can be set in your MQTT broker. They should be set as retained topics to ensure the are loaded upon restart of sf-control:

| Topic | Value | Meaning |
|---|---|---|
| solarflow-hub/{deviceId}/control/dryRun | ON/OFF | when set to ON sf-control will not take any action to change settings on the hub or the inverter, it will just show what it would do |
| solarflow-hub/{deviceId}/control/controlBypass | ON/OFF | Wether sf-control will manually switch the hub's bypass on/off instead of letting the hub's firmware decide. Note that this doesn't work as reliable on the Hub2k as on the Hub1200 |
| solarflow-hub/{deviceId}/control/chargeThrough | ON/OFF | Wheter sf-control should take care that the battery is fully charged once in a while (e.g. every 5 days). The interval should be specified in config.ini |
| solarflow-hub/{deviceId}/control/sunriseOffset | int | minutes after sunrise to be considered night (allow battery consumption) |
| solarflow-hub/{deviceId}/control/sunsetOffset | int | minutes before sunset to be considered night (allow battery consumption) |
| solarflow-hub/{deviceId}/control/minChargePower | int |  minimum charge power reserved for battery |
| solarflow-hub/{deviceId}/control/maxDischargePower | int | maximum discharge power drawn from battery |
| solarflow-hub/{deviceId}/control/dischargeDuringDaytime | ON/OFF | allow discharging during the day / outside offset timeframe |

### Manual control of the Solarflow Hub via MQTT
You can also control the SF-Hubs manually directly via MQTT and change parameters that you would normally set in the Zendure App. The hub has a list of properties that are either read-only or read-write. Not all of them are well documented by Zendure and some might be not available on all the different products.
To change properties of the hub you need to publish a valid value to this topic:

```
iot/${productKey}/${deviceKey}/properties/write
```

Where the productKey depends on your model:
| Model | productKey |
|---|---|
| Hub1200 | 73bkTV |
| Hub2000 | A8yh63 |
| Hybper 2000 | ja72U0ha | 

To change a setting you need to write a JSON payload to the above topic in this form (example sets the hub's output limit to 0 (off)):

```
{"properties": {"outputLimit": 0 }}"
```

Known properties (taken from Hub1200 reverse engineering) and their function:

| Property | read/write | Value | Function |
|---|---|---|---|
| solarInputPower | read | [int] W | current solarpower accross all inputs |
| outputPackPower | read | [int] W | current charging power of the battery |
| solarPower1 | read | [int] W | current solarpower of input 1 |
| solarPower2 | read | [int] W | current solarpower of input 2 |
| electricLevel | read | [int] % | current state of charge of battery stack |
| solarPower1Cycle | read | [int] W | current moving average (5min?) solarpower of input 1 |
| solarPower2Cycle| read | [int] W | current moving average (5min?) solarpower of input 1 |
| outputHomePowerCycle | read | [int] W | moving average (5min?) of output to home |
| outputPackPowerCycle | read | [int] W | moving average (5min?) of cahrging power |
| packNum | read | [int] | number of batteries in stack |
| masterSwitch | read/write(?) | [int] | turn hub on/off (?) |
| wifiState | read | [int] 0/1| if WiFi is connected |
| buzzerSwitch | read/write | [int] 0/1 | turn audi confirmation of settings change on/off |
| socSet = 1000 | read/write | [int] 0-1000 | maximum charge level of battery in % x 100 (20 = 2%, 1000 = 100%) |
| packInputPower | read | [int] W | current power of battery feeding to home |
| packInputPowerCycle | read | [int] W | moving average (5min?) of battery feeding to home |
| outputHomePower | read | [int] W | current power output to home |
| outputLimit | read/write | [int] W | limit of hub's output power |
| inputLimit | read/write | [int] W | not used |
| remainOutTime | read | [int] m | estimated discharging time in seconds (?) |
| remainInputTime | read | [int] m | estimated discharging time in seconds (?) |
| packState | read | [int] | wether the battery is charging, discharging or idle |
| hubState | read | [int] | state of the hub (?) |
| masterSoftVersion | read | [int] | encoded hub firmware version |
| masterhaerVersion | read | [int] | ??? |
| inputMode | read | [int] | ??? |
| blueOta | read | [int] | likely if over the air update via bluetooth is possible/active |
| pvBrand | read/write | [int] | which inverter is used/configured in the app (1= hoymiles?) |
| pass | read | [int] | whether bypass is on/or off. Note that this is only reported on HUB1200 via this property |
| minSoc | read/write | [int] 0-1000 | minimum discharge level of battery in % x 100 (20 = 2%, 1000 = 100%) |
| inverseMaxPower | read/write | [int] W | maximum output power to inverter |
| autoModel | read/write | [int] | ? likely if/how the hub is auto matching demand |
| gridPower | read/write | [int] W | ? maybe a way to feed current hub consumption to the hub to let the firmware match demand, not tested | 
| smartMode | read/write | [int] | ? maybe a setting to turn smart matchin on/off or which smart mode is used, not tested |
| smartPower | read/write | [int] W | ? maybe a setting to turn smart matchin on/off or which smart mode is used, not tested |
| passMode | read/write | [int] 1-2 | Which bypass mode is used currenlty 0=auto, 1=off, 2=on |
| autoRecover | read/write | [int] | wether to automatically recover from bypass |
| heatState | read/write | [int] | likely the status of the battery heating for models which support it, not tested |

#### Manual triggering device report
Usually the hub only reports these properties at sporadic times (whent hey change). You can enforce a manual report of the hubs properties by sending this command to the hub:

Topic:
```
iot/${productKey}/${deviceKey}/properties/read
```
Payload:
```
{"properties": ["getAll"]}
```

The hub will confirm the command by reporting all properties immediately.