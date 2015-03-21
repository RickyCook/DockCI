"""
Application configuration models
"""

import os.path
import socket

from urllib.parse import urlparse
from uuid import uuid4

from yaml_model import LoadOnAccess, SingletonModel, ValidationError

from dockci.util import default_gateway, guess_multi_value


def default_docker_host(format_string, local_default=None):
    """
    Get a default value for the docker_host variable. This will work out
    if DockCI is running in Docker, and try and guess the Docker IP address
    to use for a TCP connection. Otherwise, defaults to the default
    unix socket.
    """
    docker_files = ('/.dockerenv', '/.dockerinit')
    if any(os.path.isfile(filename) for filename in docker_files):
        return format_string.format(ip=default_gateway())

    return local_default


class Config(SingletonModel):  # pylint:disable=too-few-public-methods
    """
    Global application configuration
    """
    restart_needed = False

    # TODO docker_hosts
    secret = LoadOnAccess(generate=lambda _: uuid4().hex)

    docker_use_env_vars = LoadOnAccess(default=lambda _: False,
                                       input_transform=bool)
    docker_hosts = LoadOnAccess(
        input_transform=guess_multi_value,
        default=lambda _: [default_docker_host(
            "tcp://{ip}:2375", "unix:///var/run/docker.sock"
        )]
    )
    docker_use_registry = LoadOnAccess(default=lambda _: False,
                                       input_transform=bool)
    docker_registry = LoadOnAccess(default=lambda _: default_docker_host(
        "http://{ip}:5000", "http://127.0.0.1:5000"
    ))

    mail_server = LoadOnAccess(default=lambda _: "localhost")
    mail_port = LoadOnAccess(default=lambda _: 25, input_transform=int)
    mail_use_tls = LoadOnAccess(default=lambda _: False, input_transform=bool)
    mail_use_ssl = LoadOnAccess(default=lambda _: False, input_transform=bool)
    mail_username = LoadOnAccess(default=lambda _: None)
    mail_password = LoadOnAccess(default=lambda _: None)
    mail_default_sender = LoadOnAccess(default=lambda _:
                                       "dockci@%s" % socket.gethostname())

    github_client_id = LoadOnAccess(default=lambda _: None)
    github_client_secret = LoadOnAccess(default=lambda _: None)

    @property
    def docker_registry_host(self):
        """
        Get the hostname portion of the Docker registry
        """
        url = urlparse(self.docker_registry)
        return url.netloc

    @property
    def docker_registry_insecure(self):
        """
        Tell if the Docker registry URL is secure, or insecure
        """
        url = urlparse(self.docker_registry)
        return url.scheme.lower() == 'http'

    @property
    def mail_host_string(self):
        """
        Get the host/port as a h:p string
        """
        return "{host}:{port}".format(host=self.mail_server,
                                      port=self.mail_port)

    @mail_host_string.setter
    def mail_host_string(self, value):
        """
        Parse a URL string into host/port/user/pass and set the relevant attrs
        """
        url = urlparse('smtp://%s' % value)
        if url.hostname:
            self.mail_server = url.hostname
        if url.port:
            self.mail_port = url.port
        if url.username:
            self.mail_username = url.username
        if url.password:
            self.mail_password = url.password

    def validate(self):
        with self.parent_validation(Config):
            errors = []

            import docker
            for docker_host in self.docker_hosts:
                try:
                    # pylint:disable=unused-variable
                    client = docker.Client(docker_host)
                except docker.errors.DockerException as ex:
                    message, = ex.args  # pylint:disable=unpacking-non-sequence
                    errors.append(message)

            registry_url = urlparse(self.docker_registry)
            if registry_url.scheme.lower() not in ('http', 'https'):
                errors.append("Registry URL must be HTTP, or HTTPS")

            invalid_url_parts = (
                bool(getattr(registry_url, url_part))
                for url_part
                in ('path', 'params', 'query', 'fragment')
            )
            if any(invalid_url_parts):
                errors.append("Registry URL can only include scheme, host, "
                              "and port")

            if errors:
                raise ValidationError(errors)

        return True
