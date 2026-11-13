<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="docs/images/rsvp-nano-send-dark.svg">
    <img src="docs/images/rsvp-nano-send-light.svg" alt="RSVP Nano Send" width="360">
  </picture>
</p>

<p align="center"><strong>A Calibre -> RSVP Nano brige.</strong><br></p>

RSVP Nano Send is a small Calibre plugin that allows you to send books via Wi-Fi to an RSVP Nano. 

> [!CAUTION]
> The plugin has only been tested on RSVP Nano v0.2.0 rev2

## How to install

* Put the .zip file in the `/plugins` folder of Calibre
* Install the plugin via the Calibre interface
* Restart calibre

## How to use

### Prepare the RSVP Nano

* From your RSVP Nano go to `Settings > Connections > Companion sync`
* The RSVP Nano will give you its own IP address (default `192.168.4.1`)

### Configure the plugin

<p>
  <picture>
    <img src="docs/images/rsvp-nano-send-config.jpg" alt="RSVP Nano Send config">
  </picture>
</p>

* In the plugin COnfiguration panel, insert the RSVP Nano IP address. You can also test the connection to verify that it works. 
* Select the type of conversion. Either the plugin takes the EPUB and converts it to the RSVP Nano's own `.rsvp` format, or it sendsit as TXT and lets the RSVP Nano handle the conversion automatically. 
