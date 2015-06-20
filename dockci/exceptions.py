"""
DockCI exceptions
"""


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
