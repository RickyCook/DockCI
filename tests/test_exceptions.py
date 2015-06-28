import pytest

from requests.exceptions import ConnectionError
from requests.packages.urllib3.exceptions import ProtocolError

from dockci.exceptions import *


CONN_REFUSED = ConnectionRefusedError(61, 'Connection refused')


class TestDockerUnreachableError(object):
    """ Test ``DockerUnreachableError`` class """
    @pytest.mark.parametrize('exception,expected', [
        (ValueError('test error'), ValueError),
        (CONN_REFUSED, ConnectionRefusedError),
        (
            ProtocolError('Connection aborted.', CONN_REFUSED),
            ConnectionRefusedError
        ),
        (
            ConnectionError(ProtocolError(
                'Connection aborted.', CONN_REFUSED,
            )),
            ConnectionRefusedError
        ),
    ])
    def test_root_exception(self, exception, expected):
        """
        Ensure that the ``root_exception`` method returns the correct
        exceptions as the root
        """
        test_ex = DockerUnreachableError(None, exception)
        assert test_ex.root_exception().__class__ == expected

    def test_root_exception_no_exception(self):
        """
        Ensure that ``root_exception`` method raises ``ValueError`` when no
        exception is given to init, or param
        """
        test_ex = DockerUnreachableError(None, None)
        ex_info = pytest.raises(ValueError, test_ex.root_exception)

    @pytest.mark.parametrize('args,expected', [
        ((None, None, 'testing'), 'testing'),
        ((None, ValueError('different'), 'testing'), 'testing'),
        ((None, CONN_REFUSED), '[Errno 61] Connection refused'),
        ((None, ValueError('testing')), 'testing'),
        (
            (None, ConnectionError(ProtocolError(
                'Connection aborted.', CONN_REFUSED,
            ))),
            '[Errno 61] Connection refused',
        ),
    ])
    def test_str(self, args, expected):
        """
        Ensure that messages are retrieved from root exceptions, and overridden
        by init message as expected
        """
        test_ex = DockerUnreachableError(*args)
        assert str(test_ex) == expected
