## What is Solarflow Control

A tool to automatically control Zendure's Solarflow hub with more flexibility to match home power demand and without the official mobile app.

My intention was to use my existing telemetry from my smartmeter (using an Hichi IR reader) and my requirement to control charging and discharging in a better way than what is possible with the app.
Solarflow-Control is currently steering my Hub 24/7 with these capabilities:

- when there is enough solar power it charges the battery with at least 125W. If there is less solar power that goes to the battery first (battery priority) before feeding to home.
- if there is less demand from home than available solarpower the "over-production" goes to the battery.
- generally the output to home is always adjusted to what is needed. This guarantees that no solarpower is "wasted" and fed to the grid, but rather used to charge the battery.
- during night time it discharges the battery with a maximum of 145W but also adapts to the current demand

Originally the script used the Zendure developer MQTT telemetry data (bridged to a local MQTT broker) to make decisions. But meanwhile I have also figured out other ways to get the needed telemetry data into my local broker and now this is the preferred way.
For mor information please see my other projects:

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

