"""
DockCI exceptions
"""

from requests.exceptions import ConnectionError
from requests.packages.urllib3.exceptions import ProtocolError


class HumanOutputException(object):
    """
    Mixin for an Exception that can be handled by user output of the string
    repr
    """
    human_str = True


class InvalidOperationError(Exception):
    """
    Raised when a call is not valid at the current time
    """
    pass


class AlreadyBuiltError(Exception):
    """
    Raised when a versioned job already exists in the repository
    """
    pass


class AlreadyRunError(InvalidOperationError):
    """
    Raised when a job or stage is attempted to be run that has already been
    started/completed
    """
    runnable = None

    def __init__(self, runnable):
        super(AlreadyRunError, self).__init__()
        self.runnable = runnable


class DockerUnreachableError(Exception, HumanOutputException):
    """ Raised when Docker is unreachable for some reason """
    def __init__(self, client, exception, message=None):
        self.client = client
        self.exception = exception
        self.message = message

    def __str__(self):
        if self.message is not None:
            message = self.message
        else:
            message = str(self.root_exception())

        return "Error with the Docker server '{host}': {message}".format(
            host=self.client.base_url,
            message=message,
        )

    def root_exception(self, exception=None):
        """
        Unwrap the tangled mess of requests exceptions to get the root cause
        of the given exception
        """
        if exception is None:
            exception = self.exception

        if exception is None:
            raise ValueError("No exception given")

        if isinstance(exception, ConnectionError):
            return self.root_exception(exception.args[0])
        if isinstance(exception, ProtocolError):
            return self.root_exception(exception.args[1])

        return exception
