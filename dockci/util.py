"""
Generic DockCI utils
"""
import hashlib
import hmac
import logging
import pickle
import re
import shlex
import socket
import ssl
import struct
import subprocess
import sys
import json
import datetime

from base64 import b64encode
from contextlib import contextmanager
from functools import wraps
from ipaddress import ip_address
from urllib.parse import urlencode, urlparse, urlunparse

import docker.errors
import jwt
import py.error  # pylint:disable=import-error
import yaml_model

from flask import current_app, flash, request
from flask_security import current_user, login_required
from py.path import local  # pylint:disable=import-error
from yaml_model import ValidationError


AUTH_TOKEN_EXPIRY = 36000  # 10 hours


def request_fill(model_obj, fill_atts, accept_blank=(), data=None, save=True):
    """
    Fill given model attrs from a POST request (and ignore other requests).
    Will save only if the save flag is True
    """
    if data is None:
        data = request.form

    if request.method == 'POST':
        for att in fill_atts:
            if att in data and (data[att] != '' or att in accept_blank):
                setattr(model_obj, att, data[att])
            elif att not in data:  # For check boxes
                setattr(model_obj, att, None)

    if save:
        return model_flash(model_obj)

    return True


def model_flash(model_obj, save=True):
    """
    Save a model object, displaying appropriate flash messages
    """
    # TODO move the flash to views
    try:
        if save:
            if isinstance(model_obj, yaml_model.Model):
                model_obj.save()

            else:
                model_obj.validate()
                from dockci.server import DB
                DB.session.add(model_obj)
                DB.session.commit()

            flash(u"%s saved" % model_obj.__class__.__name__.title(),
                  'success')

        else:
            model_obj.validate()

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


def login_or_github_required(func):
    """
    Decorator to either check for GitHub headers, or require a login
    """
    login_required_func = login_required(func)

    @wraps(func)
    def inner(*args, **kwargs):
        """
        Check headers, pass to func or login_required decorator on outcome
        """
        if request.method == 'POST' and 'X-Github-Event' in request.headers:
            return func(*args, **kwargs)

        else:
            return login_required_func(*args, **kwargs)

    return inner


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


def is_git_ancestor(workdir, parent_check, child_check):
    """
    Figures out if the second is a child of the first.

    See git merge-base --is-ancestor
    """
    if parent_check == child_check:
        return False

    proc = subprocess.Popen(
        ['git', 'merge-base', '--is-ancestor', parent_check, child_check],
        cwd=workdir.strpath,
    )
    proc.wait()

    return proc.returncode == 0


def git_head_ref_name(workdir, stderr=None):
    """ Gets the full git ref name of the HEAD ref """
    proc = subprocess.Popen([
            'git', 'name-rev',
            '--name-only', '--no-undefined',
            '--ref', 'refs/heads/*',
            'HEAD',
        ],
        stdout=subprocess.PIPE,
        stderr=stderr,
        cwd=workdir.strpath,
    )
    proc.wait()
    if proc.returncode == 0:
        return parse_branch_from_ref(
            proc.stdout.read().decode().strip(), strict=False,
        )

    return None


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


def bytes_str(value):
    """
    Turn bytes, or string value into both bytes and string

    Examples:

    >>> bytes_str('myval')
    (b'myval', 'myval')

    >>> bytes_str(b'myval')
    (b'myval', 'myval')
    """
    v_bytes = v_str = value
    try:
        v_bytes = value.encode()
    except AttributeError:
        pass

    try:
        v_str = value.decode()
    except AttributeError:
        pass

    return v_bytes, v_str


def docker_ensure_image(client,
                        service,
                        insecure_registry=False,
                        handle=None):
    """
    Ensure that an image for the service exists, pulling from repo/tag if not
    available. If handle is given (a handle to write to), the pull output will
    be streamed through.

    Returns the image id (might be different, if repo/tag is used and doesn't
    match the ID pulled down... This is bad, but no way around it)
    """
    try:
        return client.inspect_image(service.image)['Id']

    except docker.errors.APIError:
        if handle:
            docker_data = client.pull(service.repo_full,
                                      service.tag,
                                      insecure_registry=insecure_registry,
                                      stream=True,
                                      )

        else:
            docker_data = client.pull(service.repo_full,
                                      service.tag,
                                      insecure_registry=insecure_registry,
                                      ).split('\n')

        latest_id = None
        for line in docker_data:
            line_bytes, line_str = bytes_str(line)

            if handle:
                handle.write(line_bytes)

            data = json.loads(line_str)
            if 'id' in data:
                latest_id = data['id']

        return latest_id


class FauxDockerLogBase(object):
    """
    A contextual logger to output JSON lines to a handle
    """
    def __init__(self):
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
            return self.handle_line(
                ('%s\n' % json.dumps(self.defaults)).encode()
            )

    def handle_line(self, line):
        """ Handle outputting a line to the user """
        raise NotImplementedError("Must override handle_line method")


class GenFauxDockerLog(FauxDockerLogBase):
    """ Generator for the faux Docker lines """
    def handle_line(self, line):
        yield line


class IOFauxDockerLog(FauxDockerLogBase):
    """ Writes faux Docker lines to a handle """
    def __init__(self, handle):
        super(IOFauxDockerLog, self).__init__()
        self.handle = handle

    def handle_line(self, line):
        self.handle.write(line)
        self.handle.flush()


def tokengetter_for(oauth_app):
    """
    Flask security tokengetter for an endpoint
    """
    def inner():
        """
        Create a tokengetter for the current_user model
        """
        return get_token_for(oauth_app)

    return inner


def get_token_for(oauth_app):
    """
    Get a token for the currently logged in user
    """
    if current_user.is_authenticated():
        token = current_user.oauth_tokens.filter_by(
            service=oauth_app.name,
        ).first()

        if token:
            from dockci.server import OAUTH_APPS_SCOPES
            if token.scope == OAUTH_APPS_SCOPES[oauth_app.name]:
                return (token.key, token.secret)

    return None


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


def fq_object_class_name(obj):
    """ Fully qualified name for an object's class """
    return '%s.%s' % (obj.__class__.__module__,
                      obj.__class__.__name__)


def full_model_slug(model):
    """ Get a compound slug if possible, otherwise just the slug is fine """
    if hasattr(model, 'compound_slug'):
        return model.compound_slug

    return model.slug


def auth_token_data_from_form(form_data, user, model):
    """
    Give the data dict necessary for an auth token, filling values from the
    form where possible
    """
    return {
        'user_id': user.id,
        'model_class': fq_object_class_name(model),
        'model_id': model.id,
        'operation': form_data.get('operation', None),
        'expiry': int(form_data['expiry']),  # Should be checked previously
    }


def auth_token_data(user, model, operation, expiry):
    """ Give the data dict necessary for an auth token """
    return {
        'user_id': user.id,
        'model_class': fq_object_class_name(model),
        'model_id': model.id,
        'operation': operation,
        'expiry': expiry,
    }


def auth_token_expiry():
    """ Expiry date for a new auth token """
    return int(datetime.datetime.now().timestamp()) + AUTH_TOKEN_EXPIRY


def create_auth_token(secret, token_data):
    """
    Create an auth token for a user to do a destructive operation on a model
    """
    # TODO HMAC default here is MD5. This needs to NOT be MD5
    return b64encode(hmac.new(
        secret.encode(),
        pickle.dumps(token_data),
        'sha256',
    ).digest()).decode()


def validate_auth_token(secret, form_data, user, model):
    """ Validate that auth token data in the form is valid """
    try:
        expiry = int(form_data.get('expiry', ''))
    except ValueError:
        return False  # TODO better logging

    now = int(datetime.datetime.now().timestamp())
    if expiry < now:
        return False

    req_auth_token = create_auth_token(
        secret, auth_token_data_from_form(form_data, user, model),
    )

    return hmac.compare_digest(req_auth_token,
                               form_data.get('auth_token', None))


def write_all(handle, lines, flush=True):
    """ Encode, write, then flush the line """
    if isinstance(lines, (tuple, list)):
        for line in lines:
            write_all(handle, line, False)
    else:
        if isinstance(lines, bytes):
            handle.write(lines)
        else:
            handle.write(str(lines).encode())

        if flush:
            handle.flush()


def str2bool(value):
    """ Convert a string to a boolean, accounting for english-like terms """
    value = value.lower()

    try:
        return bool(int(value))
    except ValueError:
        pass

    return value in ('yes', 'true', 'y', 't')


BUILT_RE = re.compile(r'Successfully built ([0-9a-f]+)')


def built_docker_image_id(data):
    """ Get an image ID out of the Docker stream data """
    re_match = BUILT_RE.search(data.get('stream', ''))
    if re_match:
        return re_match.group(1)

    return None


def path_contained(outer_path, inner_path):
    """ Ensure that ``inner_path`` is contained within ``outer_path`` """
    common = inner_path.common(outer_path)
    try:
        # Account for symlinks
        return common.samefile(outer_path)

    except py.error.ENOENT:
        return common == outer_path


def client_kwargs_from_config(host_str):
    """ Generate Docker Client kwargs from the host string """
    docker_host, *arg_strings = shlex.split(host_str)
    docker_client_args = {
        'base_url': docker_host,
    }

    tls_args = {}
    for arg_string in arg_strings:
        arg_name, arg_val = arg_string.split('=', 1)
        if arg_name == 'cert_path':
            cert_path = py.path.local(arg_val)
            tls_args['client_cert'] = (
                cert_path.join('cert.pem').strpath,
                cert_path.join('key.pem').strpath,
            )

            # Assign the CA if it exists, and verify isn't overridden
            ca_path = cert_path.join('ca.pem')
            if ca_path.check() and tls_args.get('verify', None) != False:
                tls_args['verify'] = ca_path.strpath

        elif arg_name == 'assert_hostname':
            tls_args[arg_name] = str2bool(arg_val)

        elif arg_name == 'verify':
            tls_args[arg_name] = str2bool(arg_val)

        elif arg_name == 'ssl_version':
            tls_args['ssl_version'] = getattr(ssl, 'PROTOCOL_%s' % arg_val)

    if tls_args:
        docker_client_args['tls'] = docker.tls.TLSConfig(**tls_args)

    return docker_client_args


GIT_NAME_REV_BRANCH = re.compile(r'^(remotes/origin/|refs/heads/)([^~]+)')
GIT_NAME_REV_TAG = re.compile(r'^(refs/tags/)([^~]+)')


def parse_ref(ref):
    """
    Discover what type of ref is passed in, and convert to a short ref name. It
    is assumed that a ref with no full name is a branch.

    Returns:
      tuple(str, str): Ref type, and ref short name
      tuple(None, str): Ref type was unknown. Ref name is the full ref
    """
    for ref_type, func in (
        ('branch', parse_branch_from_ref),
        ('tag', parse_tag_from_ref),
        ('branch', lambda ref: parse_branch_from_ref(ref, relax=True))
    ):
        parsed = func(ref)
        if parsed is not None:
            return ref_type, parsed

    return None, ref


def parse_branch_from_ref(ref, strict=True, relax=False):
    """ Get a branch name from a git symbolic name """
    parsed = _parse_from_ref(ref, GIT_NAME_REV_BRANCH, strict)
    if parsed is not None:
        return parsed
    elif relax and '/' not in ref:
        return ref

    return None


def parse_tag_from_ref(ref, strict=True):
    """ Get a tag name from a git symbolic name """
    return _parse_from_ref(ref, GIT_NAME_REV_TAG, strict)


def _parse_from_ref(ref, regex, strict):
    """ Logic for the tag/branch ref parsers """
    ref_match = regex.search(ref)

    if ref_match:
        return ref_match.groups()[1]
    elif not strict:
        return ref

    return None


def project_root():
    """ Get the DockCI project root """
    return local(__file__).dirpath().join('..')


def bin_root():
    """ Get the bin directory of the execution env """
    return local(sys.prefix).join('bin')


def ext_url_for(endpoint, **values):
    """ Use ``external_url`` from config to build a full URL """
    from dockci.server import CONFIG

    if not CONFIG.external_url:
        return None

    ext_url = urlparse(CONFIG.external_url)
    method = values.pop('_method', None)
    return current_app.url_map.bind(
        ext_url.netloc,
        script_name=ext_url.path,
        url_scheme=values.pop('_scheme', ext_url.scheme),
    ).build(
        endpoint,
        values,
        method=method,
        force_external=True,
    )


def add_to_url_path(url, more_path):
    """ Appends ``more_path`` to ``url`` path, and normalizes the output """
    url = list(urlparse(url))
    url[2] = re.sub('//+', '/', ('%s/%s' % (url[2], more_path)))
    return urlunparse(url)


def unique_model_conflicts(klass, **fields):
    """ Find any models that have values in fields """
    queries = {
        field_name: klass.query.filter_by(**{field_name: field_value})
        for field_name, field_value in fields.items()
    }
    return {
        field_name: query
        for field_name, query in queries.items()
        if query.count() > 0
    }


def rabbit_stage_key(stage, mtype):
    """ RabbitMQ routing key for messages """
    return 'dockci.{project_slug}.{job_slug}.{stage_slug}.{mtype}'.format(
        project_slug=stage.job.project.slug,
        job_slug=stage.job.slug,
        stage_slug=stage.slug,
        mtype=mtype,
    )


def gravatar_url(email, size=None):
    """
    Get a Gravatar URL from an email address

    >>> gravatar_url('ricky@spruce.sh')
    'https://s.gravatar.com/avatar/35866d5d838f7aeb9b51a29eda9878e7'

    >>> gravatar_url('hello@spruce.sh')
    'https://s.gravatar.com/avatar/6f54fe761ebc48100522fc2bdf958848'

    >>> gravatar_url('ricky@spruce.sh', size=100)
    'https://s.gravatar.com/avatar/35866d5d838f7aeb9b51a29eda9878e7?s=100'

    >>> gravatar_url('hello@spruce.sh', size=120)
    'https://s.gravatar.com/avatar/6f54fe761ebc48100522fc2bdf958848?s=120'
    """
    email_digest = hashlib.md5(email.lower().encode()).hexdigest()
    url = "https://s.gravatar.com/avatar/" + email_digest
    if size is not None:
        url += '?' + urlencode({'s': str(size)})

    return url


API_RE = re.compile(r'/api/.*')


def is_api_request(check_request=None):
    """
    Checks if the request is for the API

    Examples:

    >>> from dockci.server import APP

    >>> with APP.test_request_context('/api/v1/projects'):
    ...     is_api_request(request)
    True

    >>> with APP.test_request_context('/projects/dockci'):
    ...     is_api_request(request)
    False
    """
    if check_request is None:
        check_request = request

    return API_RE.match(check_request.url_rule.rule) is not None


def jwt_token(**kwargs):
    """
    Create a new JWT token with the given args

    Examples:

    >>> from .server import CONFIG

    >>> token = jwt_token()
    >>> jwt.decode(token, CONFIG.secret)
    {'iat': ...}

    >>> token = jwt_token(name='test')
    >>> data = jwt.decode(token, CONFIG.secret)
    >>> sorted(list(data.items()))
    [('iat', ...), ('name', 'test')]

    >>> token = jwt_token(iat=1111)
    >>> jwt.decode(token, CONFIG.secret)
    {'iat': 1111}
    """
    from .server import CONFIG

    jwt_kwargs = kwargs.copy()
    if 'sub' not in jwt_kwargs and current_user.is_authenticated():
        jwt_kwargs['sub'] = current_user.id
    if 'iat' not in jwt_kwargs:
        jwt_kwargs['iat'] = datetime.datetime.utcnow()

    jwt_kwargs = {
        key: value
        for key, value in jwt_kwargs.items()
        if value is not None
    }

    return jwt.encode(jwt_kwargs, CONFIG.secret).decode()
