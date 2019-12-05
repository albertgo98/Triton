#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Author: MIT 2.009 Purple Team
# Date: November 11, 2019
from __future__ import print_function
import paho.mqtt.client as mqtt
import RPi.GPIO as GPIO
import time
import os
import math

import requests
import json
import netifaces as ni

import sys
import ssl
import datetime
import logging, traceback

logger = logging.getLogger(__name__)

class TritonClient():
    '''
    Initialize the MQTT server.
    Params:
        config (dict) - dictionary containing certificate and key paths, and stored location
        client_name (string) - id of Triton system on server
        net_interface (string) - network interface to use (wifi vs ethernet), only tested on wifi now
        level1Temp, level2Temp, level3Temp (int) - the warning temperatures for this Triton install
    '''

    def __init__(self, config, client_name='Triton', net_interface='wlan0', level1Temp=32, level2Temp=10, level3Temp=0, testing=True):
        #constants for server connection
        self.IoT_protocol_name = "Triton"

        self.aws_iot_endpoint = config['aws_endpoint']
        self.url = "https://{}".format(self.aws_iot_endpoint)

        self.ca = config['ca-cert']
        self.private = config['private-key']
        self.cert = config['cert']
        #end aws server constants

        self.config_path = config['path-to-config']

        # define script name for w  hen it appears on server
        self.client_name = client_name

        # define the address of the server
        self.net_interface = net_interface
        self.serverAddress = ni.ifaddresses(self.net_interface)[ni.AF_INET][0]['addr']
        logger.info("Client: %s at address: %s", self.client_name, self.serverAddress)

        # define the client
        self.mqtt_client = mqtt.Client(self.client_name)

        #more aws setup
        self.ssl_context= self.ssl_alpn()
        self.mqtt_client.tls_set_context(context=self.ssl_context)

        # Callback function is called when script is connected to mqtt server
        self.mqtt_client.on_connect = self.on_connect

        # Establishing and calling callback functions for when messages from different topics are received
        self.mqtt_client.message_callback_add(self.client_name + '/Location', self.on_message_location)
        logger.debug("Location topic: %s", self.client_name+'/Location')
        self.mqtt_client.message_callback_add(self.client_name + '/Startup', self.on_message_startup)
        self.mqtt_client.message_callback_add(self.client_name + '/Manual', self.on_message_manual)
        self.mqtt_client.on_message = self.on_message

        #connect to aws iot (DA CLOUD)
        self.mqtt_client.connect(self.aws_iot_endpoint, port=8883)

        # Subscribe to everything that starts with Client name
       # self.mqtt_client.connect(self.serverAddress)
        self.mqtt_client.subscribe(self.client_name+'/#')

        # Defining location variables
        self.latitude  = config['coordinates']['lat']
        self.longitude = config['coordinates']['long']

        logger.debug("Initialized TritonClient with %f long, %f lat", self.longitude, self.latitude)

        # Defining variables that the app subscribes to - THESE ARE DUMMY VARIABLES
        self.temperature = [50,0]
        self.active      = 0
        self.danger      = 'None'

        # Defining whether or not setup has been completed
        self.setup = False

        # Tells if Triton was manually set to be on
        self.manual = False
        self.pump_control_on=False

        #set to true if using conduction with our testing setup
        self.testing=testing

        # Causes mqtt to run continuously
        self.mqtt_client.loop_start()

    def update_config_location(self, long, lat):
        '''
            Updates the configuration file with the given long lat coordinates
        '''
        config_file = open(self.config_path, 'r', encoding='utf-8')
        config = json.load(config_file)
        config_file.close()

        logger.debug("Opened JSON file for update")

        config["coordinates"]['long'] = long
        config["coordinates"]['lat'] = lat

        config_file = open(self.config_path, "w")
        json.dump(config, config_file, ensure_ascii=False, indent=4)
        config_file.close()

        logger.info("Wrote JSON with new long lat coordinates: %f long, %f lat", long, lat)

    def on_connect(self, client, userdata, flags, rc):
        '''
        Callback function for when the script is connected to the server
        Prints connected to verify
        '''
        logger.info("Connected to server")

    def on_message(self, client, userdata, msg):
        '''
        Prints message for any message not received through Triton/ topics
        '''
        message = msg.payload.decode(encoding='UTF-8')
        logger.debug("Message %s", message)

    def on_message_location(self, client, userdata, msg):
        '''
        Callback function for Triton/location topics
        Gets the temperature based on the location that is specified
        '''
        # TODO Make app default (before clicking screen) to GPS coordinates of user

        message = msg.payload.decode(encoding='UTF-8')
        logger.debug("Location message %s: ", message)
        #split on the comma because the message in the form of "latitude, longitude"
        location = message.split(",")

        self.latitude = float(location[0])
        self.longitude = float(location[1])
        # Update config file in case script fails and will restart with old location
        self.update_config_location(self.longitude, self.latitude)
        self.setup=True
        logger.debug("Setup has been completed")
        logger.info("Latitude: %f, Longitude: %f", self.latitude, self.longitude)

        try:
            self.temperature = self.get_weather_data()
        except KeyError:
            logger.error("Could not get weather data for given long/lat. Temp staying the same")
        logger.debug("Temperature: %f", self.temperature[0])

    def on_message_startup(self, client, userdata, msg):
        '''
        Callback function for Triton/startup topic
        Publishes State, Temperature, Battery, and Danger information to the respective topics
        This function is called everythime someone opens the Triton dashboard in the app
        Also is called when someone hits the refresh button on the app
        '''
        client.publish(self.client_name + "/Active", self.active)
        client.publish(self.client_name + "/Temperature",str(self.temperature[0]))
        client.publish(self.client_name + "/Danger", str(self.danger))
        client.publish(self.client_name + "/Wind", self.current_wind_speed)
        logger.debug("startup has been called")

    def on_message_manual(self, client, userdata, msg):
        '''
        Callback function for Triton/manual topic
        Sets the self.manual variabl as true and thus opens the valve
        '''
        message = msg.payload.decode(encoding='UTF-8')
        logger.debug("Manual message: %s", message)
        if message == "on":
            self.manual = True
        else:
            self.manual = False
        logger.debug("Manual Settings: %d", self.manual)

    def on_message_pump(self,client,userdata,msg):
        '''
        Callback function for Triton/Pump topic
        sets self.pump_control_on to be on or off_interval
        '''
        message=msg.payload.decode(encoding='UTF-8')
        if message=="on":
            self.pump_control_on=True
        elif message=="off":
            self.pump_control_on=False
        else:
            logger.debug("Bad /Pump Message: %s", message)
        logger.debug("Pump Control is %d", self.pump_control_on)

    def get_weather_data(self):
        '''
        Gets weather data from the location specified by latittude and longitude
        Returns a list of temperatures fro the next 150 hours
        '''
        logger.debug("Getting weather data!")
        # define url that takes latitiude and longitude variables
        url = 'https://api.weather.gov/points/' + str(self.latitude) + ',' + str(self.longitude)
        dict_one = requests.get(url).json()

        # Get new URL for weather at specific coordinates
        # logger.debug("Dict one: %s",str(dict_one))
        try:
            url = dict_one["properties"]["forecastHourly"]
            logger.debug("Url: %s", url)

        except KeyError:
            logger.error("Location given does produce dict with a 'properties key'")
            err = KeyError("Location given does produce dict with a 'properties key'")
            raise err

        # do again but with new url
        dict_one = requests.get(url).json()

        try:
            props = dict_one["properties"]
            # logger.debug("Props: %s", str(props))
        except KeyError:
            logger.error("Location given does produce dict 2 with a 'properties key'")
            err = KeyError("Location given does produce dict with a 'properties key'")
            raise err

        logger.info("Successfully called weather API")

        #get temp data
        temps = [period["temperature"] for period in props["periods"]]

        #now do wind speed
        speeds = [int(period["windSpeed"].split()[0]) for period in props["periods"]]

        current_temp = temps[0]
        current_wind_speed=speeds[0]

        self.current_temp=current_temp
        self.current_wind_speed=current_wind_speed
        self.check_danger()

        return temps


    def ssl_alpn(self): #aws helper function

        #debug print opnessl version
        #logger.info("open ssl version:{}".format(ssl.OPENSSL_VERSION))
        ssl_context = ssl.create_default_context()
        ssl_context.set_alpn_protocols([self.IoT_protocol_name])
        ssl_context.load_verify_locations(cafile=self.ca)
        ssl_context.load_cert_chain(certfile=self.cert, keyfile=self.private)
        return  ssl_context

    def check_danger(self):
        #different bins.
        #starts at highest temp, goes down.
        #first number is off time, second is on time
        t=self.current_temp #F
        s=self.current_wind_speed #mph
        if t>=30:
            self.danger="None"
            self.active=0
            self.time=1000
            return
        self.active=1

        diameter=.0127
        density_air=1.25
        visc_air=1.81*10**-5
        Re_air=density_air*s*.6/2.237*diameter/visc_air
        cp_air=1.005*10**3
        k_air=25.36*10**-3
        Pr=visc_air*cp_air/k_air
        Nu=0.3+( .62 * Re_air**(1/2) * Pr**(1/3) )/( (1+(0.4/Pr)**(2/3))**(1/4) )
        hair=Nu*k_air/diameter
        water_density=1000

        t_freeze=0
        t_initial=7
        t_celc=(t-32)*5/9
        k_water=0.58
        k_ice=2.18
        k_avg=(k_water+k_ice)/2

        exp_length=0.15 #exposed piping length
        volume=math.pi*(diameter/2)**2 * exp_length
        area=math.pi*diameter*exp_length
        c=4200
        l=334*1000
        density_ice=930

        if self.testing:
            #testing, then do conduction
            tau=water_density*volume*c/(area/2*(k_water/diameter))
            t1=math.log((t_initial-t_celc)/(t_freeze-t_celc))*tau
            t2=l*density_ice*diameter**2 / (k_avg*-t_celc)
        else:
            #not testing, do convection
            tau=water_density*volume*c/(area/2*hair)
            t1=math.log((t_initial-t_celc)/(t_freeze-t_celc))*tau
            t2=l*density_ice*diameter/(hair*-t_celc)
        time_freeze=(t1+t2)/60 #total time to freeze in minutes
        time_freeze=.85*time_freeze #adds safety factor

        self.time=time_freeze
        self.danger="Medium"
