#!/bin/sh

export BASEUSR=$USER

cd

# Make Triton startup script
echo "#!/bin/sh
/home/$BASEUSR/python-wifi-connect/scripts/install.sh
python3 /home/$BASEUSR/Drip/Drip_Hub/main.py" > triton

chmod +x ./triton

echo 'Created hub start up script'

# Install Prequisites

# MQTT needs to be root to not throw errors
# sudo -H pip3 install paho-mqtt


# Make system start up file

sudo -E sh -c 'echo "[Unit]
Description=Starts the triton system

[Service]
WorkingDirectory=/home/$BASEUSR

Type=simple
ExecStart=/home/$BASEUSR/triton

[Install]
WantedBy=multi-user.target" > /etc/systemd/system/triton.service'

#sudo systemctl enable triton

echo 'Created and enabled Triton start up service'

echo 'Run sudo systemctl start triton to start the hub without restarting'



