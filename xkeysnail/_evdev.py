"""Pure-Python replacement for the `evdev` package.

Only the subset used by xkeysnail is implemented:
  - ecodes (EV_KEY, EV_REL, EV_SYN, BTN set, keys dict)
  - InputDevice (open, grab, ungrab, read, capabilities, name, path, phys)
  - list_devices()
  - UInput (create virtual device, write, syn, write_event)
  - UInputError
"""

import array
import fcntl
import glob
import os
import struct

EV_SYN = 0x00
EV_KEY = 0x01
EV_REL = 0x02
EV_ABS = 0x03
EV_MSC = 0x04
SYN_REPORT = 0x00

_INPUT_EVENT_FORMAT = 'llHHi'
_INPUT_EVENT_SIZE = struct.calcsize(_INPUT_EVENT_FORMAT)

BTN = set(range(0x120, 0x150))
KEY_MAX = 0x2ff

keys = {code: code for code in range(KEY_MAX + 1)}

# ioctl helpers
_IOC_READ = 2
_IOC_WRITE = 1
_IOC_DIRSHIFT = 30
_IOC_TYPESHIFT = 8
_IOC_NRSHIFT = 0
_IOC_SIZESHIFT = 16

def _IOC(d, t, nr, sz):
    return (d << _IOC_DIRSHIFT) | (t << _IOC_TYPESHIFT) | (nr << _IOC_NRSHIFT) | (sz << _IOC_SIZESHIFT)

_EVIOCGNAME = lambda l: _IOC(_IOC_READ, 0x45, 0x06, l)
_EVIOCGPHYS = lambda l: _IOC(_IOC_READ, 0x45, 0x07, l)
_EVIOCGBIT  = lambda ev, l: _IOC(_IOC_READ, 0x45, 0x20 + ev, l)
_EVIOCGRAB  = _IOC(_IOC_WRITE, 0x45, 0x90, 4)

_UI_SET_EVBIT  = 0x40045564
_UI_SET_KEYBIT = 0x40045565
_UI_SET_RELBIT = 0x40045566
_UI_DEV_SETUP  = 0x405c5503
_UI_DEV_CREATE = 0x5501
_UI_DEV_DESTROY = 0x5502

_UINPUT_SETUP_FORMAT = 'HHHh80sI'


class _Ecodes:
    EV_SYN = EV_SYN
    EV_KEY = EV_KEY
    EV_REL = EV_REL
    EV_ABS = EV_ABS
    EV_MSC = EV_MSC
    BTN = BTN
    keys = keys

ecodes = _Ecodes()


class InputEvent:
    __slots__ = ('sec', 'usec', 'type', 'code', 'value')
    def __init__(self, sec, usec, typ, code, value):
        self.sec = sec
        self.usec = usec
        self.type = typ
        self.code = code
        self.value = value


class InputDevice:
    def __init__(self, path):
        self.path = path
        self.fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)

        buf = array.array('B', b'\0' * 256)
        try:
            fcntl.ioctl(self.fd, _EVIOCGNAME(256), buf)
            raw = buf.tobytes()
            self.name = raw[:raw.index(0)].decode('utf-8', errors='replace') if 0 in raw else raw.rstrip(b'\0').decode('utf-8', errors='replace')
        except OSError:
            self.name = "unknown"

        buf = array.array('B', b'\0' * 256)
        try:
            fcntl.ioctl(self.fd, _EVIOCGPHYS(256), buf)
            raw = buf.tobytes()
            self.phys = raw[:raw.index(0)].decode('utf-8', errors='replace') if 0 in raw else raw.rstrip(b'\0').decode('utf-8', errors='replace')
        except OSError:
            self.phys = ""

    def fileno(self):
        return self.fd

    def grab(self):
        fcntl.ioctl(self.fd, _EVIOCGRAB, 1)

    def ungrab(self):
        try:
            fcntl.ioctl(self.fd, _EVIOCGRAB, 0)
        except OSError:
            pass

    def capabilities(self, verbose=False):
        result = {}
        ev_bits = self._get_bits(0, 5)
        for ev_type in range(32):
            if ev_bits[ev_type // 8] & (1 << (ev_type % 8)):
                if ev_type == EV_KEY:
                    nbytes = (KEY_MAX // 8) + 1
                    key_bits = self._get_bits(EV_KEY, nbytes)
                    codes = []
                    for code in range(KEY_MAX + 1):
                        if key_bits[code // 8] & (1 << (code % 8)):
                            codes.append(code)
                    result[ev_type] = codes
                else:
                    result[ev_type] = []
        return result

    def _get_bits(self, ev_type, nbytes):
        buf = array.array('B', b'\0' * nbytes)
        try:
            fcntl.ioctl(self.fd, _EVIOCGBIT(ev_type, nbytes), buf)
        except OSError:
            pass
        return buf.tobytes()

    def read(self):
        events = []
        try:
            while True:
                data = os.read(self.fd, _INPUT_EVENT_SIZE)
                if len(data) < _INPUT_EVENT_SIZE:
                    break
                sec, usec, typ, code, value = struct.unpack(_INPUT_EVENT_FORMAT, data)
                events.append(InputEvent(sec, usec, typ, code, value))
        except BlockingIOError:
            pass
        return events

    def close(self):
        try:
            os.close(self.fd)
        except OSError:
            pass

    def __del__(self):
        try:
            os.close(self.fd)
        except Exception:
            pass


def list_devices():
    return sorted(glob.glob('/dev/input/event*'))


class UInputError(Exception):
    pass


class UInput:
    def __init__(self, events=None, name='py-evdev-uinput'):
        self._fd = None
        try:
            self._fd = os.open('/dev/uinput', os.O_WRONLY | os.O_NONBLOCK)
        except PermissionError as e:
            raise UInputError(str(e)) from e

        try:
            self._setup(events or {}, name)
        except Exception:
            os.close(self._fd)
            self._fd = None
            raise

    def _setup(self, events, name):
        fd = self._fd
        for ev_type, codes in events.items():
            fcntl.ioctl(fd, _UI_SET_EVBIT, ev_type)
            if ev_type == EV_KEY:
                code_iter = codes.keys() if hasattr(codes, 'keys') else codes
                for code in code_iter:
                    fcntl.ioctl(fd, _UI_SET_KEYBIT, code)
            elif ev_type == EV_REL:
                for code in codes:
                    fcntl.ioctl(fd, _UI_SET_RELBIT, code)
        fcntl.ioctl(fd, _UI_SET_EVBIT, EV_SYN)

        name_bytes = name.encode('utf-8')[:79].ljust(80, b'\0')
        setup_data = struct.pack(_UINPUT_SETUP_FORMAT, 0x06, 0x1234, 0x5678, 1, name_bytes, 0)
        fcntl.ioctl(fd, _UI_DEV_SETUP, setup_data)
        fcntl.ioctl(fd, _UI_DEV_CREATE)

    def write(self, ev_type, code, value):
        if self._fd is None:
            return
        data = struct.pack(_INPUT_EVENT_FORMAT, 0, 0, ev_type, code, value)
        os.write(self._fd, data)

    def syn(self):
        self.write(EV_SYN, SYN_REPORT, 0)

    def write_event(self, event):
        self.write(event.type, event.code, event.value)

    def close(self):
        if self._fd is not None:
            try:
                fcntl.ioctl(self._fd, _UI_DEV_DESTROY)
            except OSError:
                pass
            try:
                os.close(self._fd)
            except OSError:
                pass
            self._fd = None

    def __del__(self):
        self.close()
