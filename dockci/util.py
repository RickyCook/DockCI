"""
Generic DockCI utils
"""
import hashlib
import hmac
import logging
import os
import re
import socket
import struct
import subprocess
import json
import datetime

from contextlib import contextmanager
from ipaddress import ip_address

import docker.errors

from flask import flash, request
from yaml_model import ValidationError


def is_yaml_file(filename):
    """
    Check if the filename provided points to a file, and ends in .yaml
    """
    return os.path.isfile(filename) and filename.endswith('.yaml')


def request_fill(model_obj, fill_atts, save=True):
    """
    Fill given model attrs from a POST request (and ignore other requests).
    Will save only if the save flag is True
    """
    print(request.method)
    if request.method == 'POST':
        for att in fill_atts:
            if att in request.form:
                setattr(model_obj, att, request.form[att])
            else:
                setattr(model_obj, att, None)

        # TODO move the flash to views
        if save:
            try:
                model_obj.save()
                flash(u"%s saved" % model_obj.__class__.__name__.title(),
                      'success')
                return True

            except ValidationError as ex:
                flash(ex.messages, 'danger')
                return False


def default_gateway():
    """
    Gets the IP address of the default gateway
    """
    with open('/proc/net/route') as handle:
        for line in handle:
            fields = line.strip().split()
            if fields[1] != '00000000' or not int(fields[3], 16) & 2:
                continue

            return ip_address(socket.inet_ntoa(
                struct.pack("<L", int(fields[2], 16))
            ))


def bytes_human_readable(num, suffix='B'):
    """
    Gets byte size in human readable format
    """
    for unit in ('', 'K', 'M', 'G', 'T', 'P', 'E', 'Z'):
        if abs(num) < 1000.0:
            return "%3.1f%s%s" % (num, unit, suffix)
        num /= 1000.0

    return "%.1f%s%s" % (num, 'Y', suffix)


def is_valid_github(secret):
    """
    Validates a GitHub hook payload
    """
    if 'X-Hub-Signature' not in request.headers:
        return False

    hash_type, signature = request.headers['X-Hub-Signature'].split('=')
    if hash_type.lower() != 'sha1':
        logging.warn("Unknown GitHub hash type: '%s'", hash_type)
        return False

    computed_signature = hmac.new(secret.encode(),
                                  request.data,
                                  hashlib.sha1).hexdigest()

    return signature == computed_signature


@contextmanager
def stream_write_status(handle, status, success, fail):
    """
    Context manager to write a status, followed by success message, or fail
    message if yield raises an exception
    """
    handle.write(status.encode())
    try:
        yield
        handle.write((" %s\n" % success).encode())
    except Exception:  # pylint:disable=broad-except
        handle.write((" %s\n" % fail).encode())
        raise


# pylint:disable=too-few-public-methods
class DateTimeEncoder(json.JSONEncoder):
    """
    Encode a date/time for JSON dump
    """
    def default(self, obj):  # pylint:disable=method-hidden
        if isinstance(obj, datetime.datetime):
            encoded_object = list(obj.timetuple())[0:6]

        else:
            encoded_object = super(DateTimeEncoder, self).default(obj)

        return encoded_object


def is_semantic(version):
    """
    Returns True if tag contains a semantic version number prefixed with a
    lowercase v.  e.g. v1.2.3 returns True
    """
    # TODO maybe this could be a configuable regex for different
    # versioning schemes?  (yyyymmdd for example)
    return re.match(r'^v\d+\.\d+\.\d+$', version) is not None


def is_hex_string(value, max_len=None):
    """
    Is the value a hex string (only characters 0-f)
    """
    if max_len:
        regex = r'^[a-fA-F0-9]{1,%d}$' % max_len
    else:
        regex = r'^[a-fA-F0-9]+$'

    return re.match(regex, value) is not None


def is_git_hash(value):
    """
    Validate a git commit hash for validity
    """
    return is_hex_string(value, 40)


def is_docker_id(value):
    """
    Validate a Docker Id (image, container) for validity
    """
    return is_hex_string(value, 64)


def is_git_ancestor(workdir, parent_check, child_check):
    """
    Figures out if the second is a child of the first.

    See git merge-base --is-ancestor
    """
    if parent_check == child_check:
        return False

    proc = subprocess.Popen(
        ['git', 'merge-base', '--is-ancestor', parent_check, child_check],
        cwd=workdir,
    )
    proc.wait()

    return proc.returncode == 0


def setup_templates(app):
    """
    Add util filters/tests/etc to the app's Jinja context
    """
    # pylint:disable=unused-variable
    @app.template_test('an_array')
    def an_array(val):
        """
        Jinja test to see if the value is array-like (tuple, list)
        """
        return isinstance(val, (tuple, list))


def docker_ensure_image(client,
                        image_id,
                        pull_repo,
                        pull_tag,
                        insecure_registry=False,
                        handle=None):
    """
    Ensure that an image id exists, pulling from repo/tag if not available. If
    handle is given (a handle to write to), the pull output will be streamed
    through.

    Returns the image id (might be different, if repo/tag is used and doesn't
    match the ID pulled down... This is bad, but no way around it)
    """
    try:
        return client.inspect_image(image_id)['Id']

    except docker.errors.APIError:
        if handle:
            docker_data = client.pull(pull_repo,
                                      pull_tag,
                                      insecure_registry=insecure_registry,
                                      stream=True,
                                      )

        else:
            docker_data = client.pull(pull_repo,
                                      pull_tag,
                                      insecure_registry=insecure_registry,
                                      ).split('\n')

        latest_id = None
        for line in docker_data:
            if handle:
                handle.write(line.encode())

            data = json.loads(line)
            if 'id' in data:
                latest_id = data['id']

        return latest_id


class FauxDockerLog(object):
    """
    A contextual logger to output JSON lines to a handle
    """
    def __init__(self, handle):
        self.handle = handle
        self.defaults = {}

    @contextmanager
    def more_defaults(self, **kwargs):
        """
        Set some defaults to write to the JSON
        """
        if not kwargs:
            yield
            return

        pre_defaults = self.defaults
        self.defaults = dict(tuple(self.defaults.items()) +
                             tuple(kwargs.items()))
        yield
        self.defaults = pre_defaults

    def update(self, **kwargs):
        """
        Write a JSON line with kwargs, and defaults combined
        """
        with self.more_defaults(**kwargs):
            self.handle.write(json.dumps(self.defaults).encode())
            self.handle.write('\n'.encode())
            self.handle.flush()


def guess_multi_value(value):
    """
    Make the best kind of list from `value`. If it's already a list, or tuple,
    do nothing. If it's a value with new lines, split. If it's a single value
    without new lines, wrap in a list
    """
    if isinstance(value, (tuple, list)):
        return value

    if isinstance(value, str) and '\n' in value:
        return [line.strip() for line in value.split('\n')]

    return [value]
