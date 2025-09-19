import argparse
from datetime import datetime
from enum import Enum
from io import TextIOWrapper
import logging
from queue import Queue, Empty
import random
import sys
import threading
import time
from pyocd.core.helpers import ConnectHelper
from chipshouter import ChipSHOUTER
import moonrakerpy as moonpy

MOONRAKER_URL = 'http://192.168.0.x' # Adjust to your needs
#TARGET_NAME = 'nrf54l'
TARGET_NAME = "nrf52810"
CHIPSHOUTER_PORT = '/dev/ttyUSB0'
PULSE_PERIOD = 0.25 # seconds
PULSE_VMIN = 350
PULSE_VMAX = 450

MOVE_COMMAND_FORMAT = 'G91\nG1 {axis}{move_size} F7800\nG90'


class RiserHandler(logging.Handler):
	"""A logging handler that turns errors into exceptions"""
	def emit(self, record: logging.LogRecord):
		if record.levelno >= logging.ERROR:
			msg = self.format(record)
			raise RuntimeError(msg)

class DebuggerStatus(Enum):
	LOCKED = 1
	UNLOCKED = 2
	AP_ERROR = 3
	ERROR = 40

debugger_queue : Queue[tuple[DebuggerStatus, Exception|None]] = Queue()

class csv_writer:
	f: TextIOWrapper
	str_format = '{time},{x},{y},{voltage},{status}\n'

	def __init__(self, filename: str|None = None):
		if not filename:
			filename = datetime.today().strftime('%Y-%m-%d %H:%M') + '.csv'
		self.f = open(filename, '+a')
		self.f.write(self.str_format)

	def write(self, x: int|str, y: int|str, voltage: int|str, status: DebuggerStatus):
		self.f.write(self.str_format.format(time=time.time(), x=x, y=y, voltage=voltage, status=status))

def debug_worker():
	session = ConnectHelper.session_with_chosen_probe(
		target_override=TARGET_NAME, 
		prefer_cmsisdap=False,
		auto_unlock=False, # Otherwise the default setting is to erase the chip, lol
	)

	handler_rise = RiserHandler()
	handler_rise.setLevel(logging.ERROR)
	handler_rise.setFormatter(logging.Formatter("%(name)s: %(message)s"))
	logger = logging.getLogger("pyocd")
	logger.addHandler(handler_rise)
	logger.setLevel(logging.ERROR)

	while True:
		try:
			session.open()
			session.target.reset()	# NOTE: This is necessary to meaningfully test the
									# next point, otherwise the chilp will always be
									# unlocked from now on.
			debugger_queue.put((DebuggerStatus.UNLOCKED, None))
		except KeyError:
			# The chip is alive, but locked
			pass
			# raise NotImplementedError('TODO: shall I handle this?') # TODO better checking
		except RuntimeError as e:
			if 'Error reading AP' in str(e):
				# Some error that often happens when the debug port is unstable,
				# likely due to the EMFI
				debugger_queue.put((DebuggerStatus.AP_ERROR, None))
			elif 'Transfer error while reading AHB-AP' in str(e):
				pass # Chip is alive but locked
			elif 'Memory transfer fault' in str(e):
				pass # Chip's memory is not readable and it is locked
			elif 'bad CTRL-AP IDR' in str(e):
				pass
			elif 'Not supported by current CPU + target interface combination' in str(e):
				pass #Chip is being glitched too hard
				 
			else:
				# Idk what this is...
				debugger_queue.put((DebuggerStatus.ERROR, e))

def main(x_size: int, y_size: int, x_offset: int, step_size: int, pulses: int) -> int:
	print('[+] Starting threads...')

	#### Init debugger thread ####
	workers = [debug_worker]
	threads = [threading.Thread(target=w, daemon=True) for w in workers]
	[t.start() for t in threads]

	#### Init printer ####
	print('[+] Connecting to Moonraker')
	printer = moonpy.MoonrakerPrinter(MOONRAKER_URL)

	#### Init chipshouter ####
	print('[+] Connecting to the ChipShouter')
	cs = ChipSHOUTER(CHIPSHOUTER_PORT)
	if not cs.armed:
		cs.armed = True
	cs.mute = True
	cs.pulse.repeat = 1

	#### Init file logger ####
	csv = csv_writer()

	#### Main loop over chip x-y positions ####
	print('[+] Starting loop')
	direction = ''
	print('x_size', x_size, 'y_size', y_size)
	for y in range(y_size):
		if y % 2 == 0:
			direction = ''
		else:
			direction = '-'
		x_start = x_offset if y == 0 else 0
		for x in range(x_start, x_size):
			print(direction, x, y)
			printer.send_gcode(MOVE_COMMAND_FORMAT.format(axis='X', move_size=direction + str(step_size)))

			for _ in range(pulses):
				for __ in range(5): # Check for chipshouter faults and try to clear them
					if 'fault' not in cs.state:
						break
					cs.faults_current = 0
					cs.armed = True
					time.sleep(0.5)
				else:
					print('[!] Could not clear ChipShouter fault after 5 tries')
					print(cs.status)
					return(1)
				voltage = random.randint(PULSE_VMIN, PULSE_VMAX)
				cs.voltage = voltage
				cs.pulse = 1
				try:
					while True:
						status, exc = debugger_queue.get(block=False)
						print(status)
						if status == DebuggerStatus.ERROR:
							# Some other error that must be handled accordingly
							raise exc # type: ignore
						elif status == DebuggerStatus.LOCKED:
							raise ValueError("Didn't expect DebuggerStatus.LOCKED to be ever posted")

						csv.write(direction + str(x), y, voltage, status)
				except Empty:
					csv.write(direction + str(x), y, cs.voltage, DebuggerStatus.LOCKED)
				time.sleep(PULSE_PERIOD)
		printer.send_gcode(MOVE_COMMAND_FORMAT.format(axis='Y', move_size=step_size))
		print(y)
	#return(42)

	cs.armed = False

	# Back home
	print('[+] Done, homing the X-Y stage')
	if direction == '':
		direction = '-'
	else:
		direction = ''
	printer.send_gcode(MOVE_COMMAND_FORMAT.format(axis='X', move_size=direction+str(x_size)))
	printer.send_gcode(MOVE_COMMAND_FORMAT.format(axis='Y', move_size='-'+str(y_size)))

	return 0

if __name__ == '__main__':
	argparser = argparse.ArgumentParser('Remote control a 3D printer with Moonraker to do EMFI')
	argparser.add_argument('x', help='Target size on the x axis', type=int)
	argparser.add_argument('y', help='Target size on the y axis', type=int)
	argparser.add_argument('-xo', '--x-offset', help='Offset to apply on the x axis on the first iteration ONLY (default=0)', type=int, default=0)
	argparser.add_argument('-s', '--step', help='Step size for each movement (default=0.1)', type=int, default=0.1)
	argparser.add_argument('-p', '--pulses', help='EM pulses for each position (default=1)', type=int, default=1)
	args = argparser.parse_args()
	sys.exit(main(x_size=args.x, y_size=args.y, x_offset=args.x_offset, step_size=args.step, pulses=args.pulses))
