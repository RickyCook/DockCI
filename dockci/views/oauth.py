"""
OAuth related API views and routes
"""

import json
import logging
import re

from functools import wraps
from urllib.parse import urlparse

from flask import abort, flash, redirect, request, Response, url_for
from flask_login import login_user
from flask_security import current_user, login_required
from flask_security.utils import url_for_security
from py.path import local
from sqlalchemy.orm.exc import MultipleResultsFound, NoResultFound

from dockci.models.auth import OAuthToken, UserEmail
from dockci.server import (APP,
                           CONFIG,
                           DB,
                           OAUTH_APPS,
                           OAUTH_APPS_SCOPE_SERIALIZERS,
                           )
from dockci.util import ext_url_for, get_token_for, jwt_token, path_contained


RE_VALID_OAUTH = re.compile(r'^[a-z]+$')

USER_API_PATHS = {
    'github': '/user',
    'gitlab': '/api/v3/user',
}

SECURITY_STATE = APP.extensions['security']

JWT_URL_RE = re.compile(r'\{jwt:(?P<name>[a-zA-Z0-9]+)\}')


class OAuthRegError(Exception):
    """ Exception for when OAuth registration fails for some reason """
    def __init__(self, reason):
        super(OAuthRegError, self).__init__()
        self.reason = reason


def check_oauth_enabled(name):
    """
    Check config to see if a given OAuth service is configured, and enabled for
    register or login actions
    """
    if not RE_VALID_OAUTH.match(name):
        return (False,) * 3

    return (
        getattr(CONFIG, '%s_enabled' % name),
        getattr(CONFIG, 'security_registerable_%s' % name),
        getattr(CONFIG, 'security_login_%s' % name),
    )


def check_redirect_url(url):
    """
    Ensure a redirect URL is authorized

    Examples:

    >>> CONFIG.oauth_authorized_redirects = []
    >>> check_redirect_url('http://fail')
    False

    >>> CONFIG.oauth_authorized_redirects = ['okay']
    >>> check_redirect_url('http://okay')
    True

    >>> CONFIG.oauth_authorized_redirects = ['^https']
    >>> check_redirect_url('http://test')
    False
    >>> check_redirect_url('https://test')
    True

    >>> CONFIG.oauth_authorized_redirects = ['^https', '^http://localhost']
    >>> check_redirect_url('http://localhost/test')
    True
    >>> check_redirect_url('https://other/test')
    True
    >>> check_redirect_url('http://other/test')
    False

    >>> CONFIG.oauth_authorized_redirects = []
    >>> CONFIG.external_url = 'http://localhost:5000/testdockci/'
    >>> check_redirect_url('http://localhost:5000/testdockci/test')
    True
    >>> check_redirect_url('http://localhost:5000/testdockci')
    True
    >>> check_redirect_url('http://localhost:5000/testdockci/')
    True
    >>> check_redirect_url('http://a:b@localhost:5000/testdockci/')
    True
    >>> check_redirect_url('http://localhost:5000/test')
    False
    >>> check_redirect_url('http://localhost:5000/testdockci/../other')
    False
    >>> check_redirect_url('http://localhost:3000/testdockci/test')
    False
    >>> check_redirect_url('https://localhost:5000/testdockci/test')
    False
    >>> check_redirect_url('http://localhost/testdockci/test')
    False
    """
    url_parse = urlparse(url)
    ext_parse = urlparse(CONFIG.external_url)
    if (
        url_parse.scheme == ext_parse.scheme and
        url_parse.hostname == ext_parse.hostname and
        url_parse.port == ext_parse.port and
        path_contained(local(ext_parse.path), local(url_parse.path))
    ):
        return True

    return any(
        re.search(re_string, url)
        for re_string in CONFIG.oauth_authorized_redirects
    )


def oauth_redir(next_url=None, user_id=None):
    """ Get the OAuth redirection URL """
    if next_url is None:
        next_url = request.args.get('next')

    if next_url is None:
        next_url = url_for('index_view')

    next_authorized = check_redirect_url(next_url)
    if not next_authorized:
        logging.error("Unauthorized OAuth URL: %s", next_url)

    elif (
        next_authorized and
        'jwt' in next_url and
        user_id is not None
    ):
            match = JWT_URL_RE.search(next_url)
            if match is not None:
                token = jwt_token(sub=user_id, **match.groupdict())
                next_url = JWT_URL_RE.sub(token, next_url, 1)

    return redirect(next_url)


@APP.route('/login/<name>')
def oauth_login(name):
    """ Entry point for OAuth logins """
    try:
        if name not in OAUTH_APPS:
            raise OAuthRegError("%s auth not available" % name.title())

        else:
            configured, _, login = check_oauth_enabled(name)

            if not (configured and login):
                raise OAuthRegError("%s login not enabled" % name.title())

            return oauth_response(OAUTH_APPS[name])

    except OAuthRegError as ex:
        flash(ex.reason, 'danger')

    return oauth_redir(url_for_security('login'))


def get_oauth_app(name):
    """
    Wrapper to raise exception if oauth app doesn't exist

    Examples:

    >>> get_oauth_app('fake')
    Traceback (most recent call last):
        ...
    dockci.views.oauth.OAuthRegError

    >>> OAUTH_APPS['real'] = 'a real app'
    >>> get_oauth_app('real')
    'a real app'
    """
    try:
        return OAUTH_APPS[name]

    except KeyError:
        raise OAuthRegError("%s auth not available" % name.title())


@APP.route('/oauth-authorized/<name>')
def oauth_authorized(name):
    """
    Callback for oauth authorizations. Handles adding the token to the logged
    in user, errors, as well as redirecting back to the original page
    """
    try:
        oauth_app = get_oauth_app(name)
        configured, register, login = check_oauth_enabled(name)

        if not configured:
            raise OAuthRegError("%s auth not enabled" % name.title())

        resp = oauth_app.authorized_response()

        if isinstance(resp, Exception):
            try:
                logging.error('OAuth exception data: %s', resp.data)
            except AttributeError:
                pass

            raise resp

        if resp is None or 'error' in resp:
            flash("{name}: {message}".format(
                name=name.title(),
                message=request.args['error_description'],
            ), 'danger')
            return oauth_redir()

        if current_user.is_authenticated():
            oauth_token = associate_oauth_current_user(name, resp)
            user = current_user
            user_id = current_user.id

        elif not (register or login):
            raise OAuthRegError(
                "Registration and login disabled for %s" % name.title())

        else:
            user, oauth_token = user_from_oauth(name, resp)
            user_id = user.id

        if user.id is None and not register:
            raise OAuthRegError(
                "Registration disabled for %s" % name.title())
        elif user.id is not None and not login:
            raise OAuthRegError(
                "Login disabled for %s" % name.title())

        associate_user(name, user, oauth_token)
        return oauth_redir(user_id=user_id)

    except OAuthRegError as ex:
        flash(ex.reason, 'danger')

    return oauth_redir()


def associate_user(name, user, oauth_token):
    """
    Given a user, and an OAuth Token, associate the 2. This will ensure that
    the token isn't in use by a different user, erase old tokens of the same
    service, associate the token with the user, then log the user in.

    The DB session will be committed, and a flash message displayed
    """
    if user is None:
        raise OAuthRegError("Couldn't retrieve user "
                            "details from %s" % name.title())

    # Delete other tokens if the user exists, and token is new
    if user.id is not None and oauth_token.id is None:
        user.oauth_tokens.filter_by(service=name).delete(
            synchronize_session=False,
        )

    if oauth_token.user is None:
        user.oauth_tokens.append(oauth_token)

    elif oauth_token.user != user:
        raise OAuthRegError("An existing user is already associated "
                            "with that %s account" % name.title())

    DB.session.add(oauth_token)
    DB.session.add(user)
    DB.session.commit()

    flash(u"Connected to %s" % name.title(), 'success')
    login_user(user)


def associate_oauth_current_user(name, resp):
    """ Associate the OAuth token in response with the current user """
    existing_user, user_email, oauth_token = \
        existing_user_from_oauth(name, resp)
    if (
        existing_user is not None and
        existing_user.id != current_user.id
    ):
        raise OAuthRegError("A user is already registered "
                            "with the email '%s'" % user_email)

    # Add a new email to the user if necessary
    if current_user.emails.filter(
        UserEmail.email.ilike(user_email),
    ).count() == 0:
        DB.session.add(UserEmail(
            email=user_email,
            user=current_user,
        ))

    return oauth_token


def get_oauth_token(name, response):
    """
    Retrieve an ``OAuthToken`` for the response. If a token exists with the
    same service name, and key then we update it with the new details
    """
    try:
        oauth_token = OAuthToken.query.filter_by(
            service=name,
            key=response['access_token'],
        ).one()

    except NoResultFound:
        return create_oauth_token(name, response)

    except MultipleResultsFound:
        raise OAuthRegError(
            "Multiple accounts associated with this "
            "%s account. This shouldn't happen" % name.title()
        )

    else:
        oauth_token.update_details_from(
            create_oauth_token(name, response)
        )
        return oauth_token


def create_oauth_token(name, response):
    """ Create a new ``OAuthToken`` from an OAuth response """
    return OAuthToken(
        service=name,
        key=response['access_token'],
        secret='',
        scope=OAUTH_APPS_SCOPE_SERIALIZERS[name](response['scope'])
    )


def existing_user_from_oauth(name, response):
    """
    Query the OAuth provider API for user email, and get a user from that
    """
    oauth_app = OAUTH_APPS[name]
    oauth_token = get_oauth_token(name, response)
    oauth_token_tuple = (oauth_token.key, oauth_token.secret)

    if oauth_token.id is not None:
        return oauth_token.user, oauth_token.user.email, oauth_token

    user_data = oauth_app.get(
        USER_API_PATHS[name],
        token=oauth_token_tuple,
    ).data
    user_email = user_data['email']

    if user_email is None:
        raise OAuthRegError(
            "Couldn't get email address from %s" % name.title())

    return (
        SECURITY_STATE.datastore.get_user(user_email),
        user_email,
        oauth_token,
    )


def user_from_oauth(name, response):
    """
    Given an OAuth response, extrapolate a ``User`` and an ``OAuthToken``.

    First, if a token exists, we get it's user. Otherwise, we look up the email
    address from the service and look for a matching user. If there is no user
    registered ith the email, we create a new user and return it (uncommitted)

    If the email can't be retrieved from the service, ``OAuthRegError`` raises

    If a user exists with the email from the service, ``OAuthRegError`` raises
    """
    existing_user, user_email, oauth_token = \
        existing_user_from_oauth(name, response)

    if existing_user is not None:
        if existing_user.oauth_tokens.filter_by(service=name).count() > 0:
            return existing_user, oauth_token

        else:
            raise OAuthRegError("A user is already registered "
                                "with the email '%s'" % user_email)

    user_obj = SECURITY_STATE.datastore.create_user(
        active=True,
        email=user_email,
    )

    return user_obj, oauth_token


def oauth_response(oauth_app):
    """
    Build an authorization for the given OAuth service, and return the response
    """
    ext_next = request.args.get('next') or request.referrer
    if ext_next is not None and not check_redirect_url(ext_next):
        logging.error("Unauthorized OAuth URL: %s", ext_next)
        return redirect(ext_next)

    callback_uri = ext_url_for(
        'oauth_authorized',
        name=oauth_app.name,
        next=ext_next or url_for('index_view'),
    )

    return oauth_app.authorize(callback=callback_uri)


def oauth_required(acceptable=None, force_name=None):
    """
    Wrap the view in oauth functionality to make sure there's an acceptable
    token, before moving on to the view
    """
    if force_name is not None and acceptable is not None:
        if force_name not in acceptable:
            raise ValueError(
                "Name has been forced to '%s', but this is never acceptable "
                "for set %s",
                force_name,
                acceptable,
            )

    def outer(func):
        """
        Wrap func in the inner check, and possibly force_name helper
        """
        @login_required
        @wraps(func)
        def inner_with_name(name, *args, **kwargs):
            """
            Wrap a view function to provide the redirect/next
            functionality on current user having no oauth
            """
            if name not in acceptable:
                return abort(404)

            oauth_app = OAUTH_APPS[name]
            if not get_token_for(oauth_app):
                resp = oauth_response(oauth_app)
                return Response(json.dumps({'redirect': resp.location}),
                                mimetype='application/json')

            else:
                if force_name is not None:
                    return func(*args, **kwargs)

                else:
                    return func(name, *args, **kwargs)

        @wraps(inner_with_name)
        def inner_forced_name(*args, **kwargs):
            """
            Wrap inner_with_name to add the name as the value given in the
            force_name param earlier
            """
            return inner_with_name(force_name, *args, **kwargs)

        if force_name is None:
            return inner_with_name

        else:
            return inner_forced_name

    return outer
