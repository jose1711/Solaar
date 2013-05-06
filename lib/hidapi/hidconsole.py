#
#
#

from __future__ import absolute_import, division, print_function, unicode_literals

import os
import sys
from select import select as _select
import time
from binascii import hexlify, unhexlify
import hidapi

#
#
#

try:
	read_packet = raw_input
except NameError:
	# Python 3 equivalent of raw_input
	read_packet = input

interactive = os.isatty(0)
prompt = '?? Input: ' if interactive else ''
start_time = time.time()

strhex = lambda d: hexlify(d).decode('ascii').upper()
try:
	unicode
	# this is certanly Python 2
	is_string = lambda d: isinstance(d, unicode) or isinstance(d, str)
	# no easy way to distinguish between b'' and '' :(
						# or (isinstance(d, str) \
						# 	and not any((chr(k) in d for k in range(0x00, 0x1F))) \
						# 	and not any((chr(k) in d for k in range(0x80, 0xFF))) \
						# 	)
except:
	# this is certanly Python 3
	# In Py3, unicode and str are equal (the unicode object does not exist)
	is_string = lambda d: isinstance(d, str)

#
#
#

from threading import Lock
print_lock = Lock()
del Lock

def _print(marker, data, scroll=False):
	t = time.time() - start_time
	if is_string(data):
		s = marker + ' ' + data
	else:
		hexs = strhex(data)
		s = '%s (% 8.3f) [%s %s %s %s] %s' % (marker, t, hexs[0:2], hexs[2:4], hexs[4:8], hexs[8:], repr(data))

	with print_lock:
		# allow only one thread at a time to write to the console, otherwise
		# the output gets garbled, especially with ANSI codes.

		if interactive and scroll:
			# scroll the entire screen above the current line up by 1 line
			sys.stdout.write('\033[s'  # save cursor position
							'\033[S'  # scroll up
							'\033[A'   # cursor up
							'\033[L'    # insert 1 line
							'\033[G')   # move cursor to column 1
		sys.stdout.write(s)
		if interactive and scroll:
			# restore cursor position
			sys.stdout.write('\033[u')
		else:
			sys.stdout.write('\n')


def _error(text, scroll=False):
	_print("!!", text, scroll)


def _continuous_read(handle, timeout=2000):
	while True:
		try:
			reply = hidapi.read(handle, 128, timeout)
		except OSError as e:
			_error("Read failed, aborting: " + str(e), True)
			break
		assert reply is not None
		if reply:
			_print(">>", reply, True)


def _validate_input(line, hidpp=False):
	try:
		data = unhexlify(line.encode('ascii'))
	except Exception as e:
		_error("Invalid input: " + str(e))
		return None

	if hidpp:
		if len(data) < 4:
			_error("Invalid HID++ request: need at least 4 bytes")
			return None
		if data[:1] not in b'\x10\x11':
			_error("Invalid HID++ request: first byte must be 0x10 or 0x11")
			return None
		if data[1:2] not in b'\xFF\x01\x02\x03\x04\x05\x06':
			_error("Invalid HID++ request: second byte must be 0xFF or one of 0x01..0x06")
			return None
		if data[:1] == b'\x10':
			if len(data) > 7:
				_error("Invalid HID++ request: maximum length of a 0x10 request is 7 bytes")
				return None
			while len(data) < 7:
				data = (data + b'\x00' * 7)[:7]
		elif data[:1] == b'\x11':
			if len(data) > 20:
				_error("Invalid HID++ request: maximum length of a 0x11 request is 20 bytes")
				return None
			while len(data) < 20:
				data = (data + b'\x00' * 20)[:20]

	return data


def _open(device, hidpp):
	if hidpp and not device:
		for d in hidapi.enumerate(vendor_id=0x046d):
			if d.driver == 'logitech-djreceiver':
				device = d.path
				break
		if not device:
			sys.exit("!! No HID++ receiver found.")
	if not device:
		sys.exit("!! Device path required.")

	print (".. Opening device", device)
	handle = hidapi.open_path(device)
	if not handle:
		sys.exit("!! Failed to open %s, aborting." % device)

	print (".. Opened handle %r, vendor %r product %r serial %r." % (
					handle,
					hidapi.get_manufacturer(handle),
					hidapi.get_product(handle),
					hidapi.get_serial(handle)))
	if hidpp:
		if hidapi.get_manufacturer(handle) != b'Logitech':
			sys.exit("!! Only Logitech devices support the HID++ protocol.")
		print (".. HID++ validation enabled.")

	return handle

#
#
#

def _parse_arguments():
	import argparse
	arg_parser = argparse.ArgumentParser()
	arg_parser.add_argument('--history', help='history file (default ~/.hidconsole-history)')
	arg_parser.add_argument('--hidpp', action='store_true', help='ensure input data is a valid HID++ request')
	arg_parser.add_argument('device', nargs='?', help='linux device to connect to (/dev/hidrawX); '
							'may be omitted if --hidpp is given, in which case it looks for the first Logitech receiver')
	return arg_parser.parse_args()


def main():
	args = _parse_arguments()
	handle = _open(args.device, args.hidpp)

	if interactive:
		print (".. Press ^C/^D to exit, or type hex bytes to write to the device.")

		import readline
		if args.history is None:
			import os.path
			args.history = os.path.join(os.path.expanduser("~"), ".hidconsole-history")
		try:
			readline.read_history_file(args.history)
		except:
			# file may not exist yet
			pass

		# re-open stdout unbuffered
		try:
			sys.stdout = os.fdopen(sys.stdout.fileno(), 'w', 0)
		except:
			# will fail in python3
			pass

	try:
		from threading import Thread
		t = Thread(target=_continuous_read, args=(handle,))
		t.daemon = True
		t.start()

		if interactive:
			# move the cursor at the bottom of the screen
			sys.stdout.write('\033[300B')  # move cusor at most 300 lines down, don't scroll

		while t.is_alive():
			line = read_packet(prompt)
			line = line.strip().replace(' ', '')
			# print ("line", line)
			if not line:
				continue

			data = _validate_input(line, args.hidpp)
			if data is None:
				continue

			_print("<<", data)
			hidapi.write(handle, data)
			# wait for some kind of reply
			if args.hidpp and not interactive:
				rlist, wlist, xlist = _select([handle], [], [], 1)
				if data[1:2] == b'\xFF':
					# the receiver will reply very fast, in a few milliseconds
					time.sleep(0.100)
				else:
					# the devices might reply quite slow
					time.sleep(1)
	except EOFError:
		if interactive:
			print ("")
		else:
			time.sleep(1)
	except Exception as e:
		print ('%s: %s' % (type(e).__name__, e))

	print (".. Closing handle %r" % handle)
	hidapi.close(handle)
	if interactive:
		readline.write_history_file(args.history)


if __name__ == '__main__':
	main()
