import base64
import hmac
import json
import logging
import struct
import time
import os

from hashlib import sha1
import sys

from requests import Session

time_delta = sys.maxsize


def get_steam_server_time(session: Session) -> int:
    try:
        url = 'https://api.steampowered.com/ITwoFactorService/QueryTime/v1/'
        resp = session.post(url, timeout=20)
        return int(resp.json()['response']['server_time'])
    except Exception as e:
        return -1


def try_to_get_time_delta_from_steam(session: Session) -> int:
    global time_delta
    if time_delta == sys.maxsize:
        for _ in range(3):
            server_time = get_steam_server_time(session)
            if server_time != -1:
                time_delta = server_time - int(time.time())
                logging.debug(f'Time delta from steam: {time_delta}')
                return time_delta
        logging.debug('Failed to get time delta from steam, use system time instead')
        time_delta = 0
    return time_delta


def load_steam_guard(steam_guard) -> dict:
    if isinstance(steam_guard, dict):
        return steam_guard
    if isinstance(steam_guard, str):
        if os.path.isfile(steam_guard):
            with open(steam_guard, 'r') as f:
                return json.loads(f.read())
        else:
            return json.loads(steam_guard)
    raise ValueError('steam_guard must be a dict or a file path or a json string')


def generate_one_time_code(shared_secret: str, timestamp: int = None) -> str:
    if timestamp is None:
        timestamp = int(time.time())
        timestamp += try_to_get_time_delta_from_steam(Session())
    time_buffer = struct.pack('>Q', timestamp // 30)  # pack as Big endian, uint64
    time_hmac = hmac.new(base64.b64decode(shared_secret), time_buffer, digestmod=sha1).digest()
    begin = ord(time_hmac[19:20]) & 0xf
    full_code = struct.unpack('>I', time_hmac[begin:begin + 4])[0] & 0x7fffffff  # unpack as Big endian uint32
    chars = '23456789BCDFGHJKMNPQRTVWXY'
    code = ''

    for _ in range(5):
        full_code, i = divmod(full_code, len(chars))
        code += chars[i]

    return code


def generate_confirmation_key(identity_secret: str, tag: str, timestamp: int = None) -> bytes:
    if timestamp is None:
        timestamp = int(time.time())
        timestamp += try_to_get_time_delta_from_steam(Session())
    buffer = struct.pack('>Q', timestamp) + tag.encode('ascii')
    return base64.b64encode(hmac.new(base64.b64decode(identity_secret), buffer, digestmod=sha1).digest())


# It works, however it's different that one generated from mobile app
def generate_device_id(steam_id: str) -> str:
    hexed_steam_id = sha1(steam_id.encode('ascii')).hexdigest()
    return 'android:' + '-'.join([hexed_steam_id[:8],
                                  hexed_steam_id[8:12],
                                  hexed_steam_id[12:16],
                                  hexed_steam_id[16:20],
                                  hexed_steam_id[20:32]])
