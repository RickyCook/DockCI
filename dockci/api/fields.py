"""
Flask RESTful fields, and WTForms input validators for validation and
marshaling
"""

import re

from functools import reduce, wraps

from flask_restful import fields

from dockci.util import gravatar_url


def value_path(obj, path):
    """
    Get a value from the given object by dot-separated path

    Examples:

    >>> class TestClass(object):
    ...     pass

    >>> testobj = TestClass()
    >>> value_path(testobj, 'testval.teststr')
    Traceback (most recent call last):
        ...
    AttributeError: 'TestClass' object has no attribute 'testval'

    >>> testobj.testval = TestClass()
    >>> testobj.testval.teststr = 'Test okay'
    >>> value_path(testobj, 'testval.teststr')
    'Test okay'

    >>> testobj.testval = None
    >>> value_path(testobj, 'testval.teststr')
    """
    return reduce(
        lambda acc, attr_name:
            None if acc is None else getattr(acc, attr_name),
        path.split('.'),
        obj,
    )


class RewriteUrl(fields.Url):
    """
    Extension of the Flask RESTful Url field that allows you to remap object
    fields to different names
    """
    def __init__(self,
                 endpoint=None,
                 absolute=False,
                 scheme=None,
                 rewrites=None):
        super(RewriteUrl, self).__init__(endpoint, absolute, scheme)
        self.rewrites = rewrites or {}

    def output(self, key, obj):
        if obj is None:
            return None

        data = obj.__dict__
        for field_set, field_from in self.rewrites.items():
            data[field_set] = value_path(obj, field_from)

        return super(RewriteUrl, self).output(key, data)


class GravatarUrl(fields.String):
    """
    Automatically turn an email into a Gravatar URL

    >>> from dockci.models.auth import User, UserEmail
    >>> from dockci.models.job import Job

    >>> field = GravatarUrl()
    >>> field.output('git_author_email',
    ...              Job(git_author_email='ricky@spruce.sh'))
    'https://s.gravatar.com/avatar/35866d5d838f7aeb9b51a29eda9878e7'

    >>> field.output('primary_email.email',
    ...              User(primary_email=UserEmail(email='ricky@spruce.sh')))
    'https://s.gravatar.com/avatar/35866d5d838f7aeb9b51a29eda9878e7'

    >>> field = GravatarUrl(attr_name='git_author_email')
    >>> field.output('different_name',
    ...              Job(git_author_email='ricky@spruce.sh'))
    'https://s.gravatar.com/avatar/35866d5d838f7aeb9b51a29eda9878e7'

    >>> field.output('git_author_email',
    ...              Job(git_author_email=None))

    >>> field = GravatarUrl(attr_name='primary_email.email')
    >>> field.output('different_name',
    ...              User(primary_email=UserEmail(email='ricky@spruce.sh')))
    'https://s.gravatar.com/avatar/35866d5d838f7aeb9b51a29eda9878e7'
    """
    def __init__(self, attr_name=None):
        super(GravatarUrl, self).__init__()
        self.attr_name = attr_name

    def output(self, key, obj):
        path = key if self.attr_name is None else self.attr_name
        email = value_path(obj, path)

        if email is None:
            return None

        return gravatar_url(email)


class RegexField(fields.String):
    """ Output a Python compiled regex as string """
    def output(self, key, obj):
        regex = getattr(obj, key, None)
        if regex is None:
            return None

        return regex.pattern


class NonBlankInput(object):
    """ Don't allow a field to be blank, or None """
    def _raise_error(self, name):  # pylint:disable=no-self-use
        """ Central place to handle invalid input """
        raise ValueError("The '%s' parameter can not be blank" % name)

    def __call__(self, value, name):
        if value is None:
            self._raise_error(name)
        try:
            if value.strip() == '':
                self._raise_error(name)
        except AttributeError:
            pass

        return value


class RegexInput(object):
    """ Validate a RegEx """
    def __call__(self, value, name):  # pylint:disable=no-self-use
        try:
            return re.compile(value)
        except re.error as ex:
            raise ValueError(str(ex))


def strip(field_type):
    """ Decorator to strip whitespace on input values before parsing """
    @wraps(field_type)
    def inner(value, name):
        """ Strip whitespace, pass to input field type """
        try:
            value = value.strip()
        except AttributeError:
            pass

        return field_type(value, name)

    return inner
