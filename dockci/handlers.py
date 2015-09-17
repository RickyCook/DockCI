""" Handlers for Flask, and Flask plugins """
import json
import re

import jwt

from flask import abort, flash, redirect, Response, request
from flask_login import login_url
from flask_security.utils import verify_and_update_password

from dockci.api.util import clean_attrs, DefaultRequestParser
from dockci.models.auth import User
from dockci.server import APP, CONFIG


LOGIN_MANAGER = APP.extensions['security'].login_manager
LOGIN_FORM = DefaultRequestParser()

API_RE = re.compile(r'/api/.*')


@LOGIN_MANAGER.unauthorized_handler
def unauthorized_handler():
    """
    Handler for unauthorized user requests. If API request, handle with a basic
    auth dialog (for users) and a JSON response (for APIs). Otherwise, treat
    the login like ``flask-login`` treats them. In most cases (all cases for
    DockCI; extra code left for completeness), this redirects to the login
    form
    """
    message = None
    if LOGIN_MANAGER.login_message:
        message = LOGIN_MANAGER.login_message
        if LOGIN_MANAGER.localize_callback is not None:
            message = LOGIN_MANAGER.localize_callback(message)

    if API_RE.match(request.url_rule.rule):
        args = clean_attrs(LOGIN_FORM.parse_args())
        if 'username' in args or 'password' in args or 'api_key' in args:
            message = "Invalid credentials"

        return Response(
            {'message': message or "Unauthorized"},
            401,
            {'WWW-Authenticate': 'Basic realm="DockCI API"'},
        )

    else:
        if not LOGIN_MANAGER.login_view:
            abort(401)

        if message:
            flash(message, category=LOGIN_MANAGER.login_message_category)

        return redirect(login_url(LOGIN_MANAGER.login_view, request.url))


@LOGIN_MANAGER.request_loader
def request_loader(request):
    """
    Request loader that first tries the ``LOGIN_FORM`` request parser (see
    ``try_reqparser``), then basic auth (see ``try_basic_auth``)
    """
    return try_reqparser() or try_basic_auth()


def try_jwt(token):
    """ Check a JWT token """
    if token is None:
        return None

    try:
        jwt_data = jwt.decode(token, CONFIG.secret)
    except jwt.exceptions.InvalidTokenError as ex:
        return None

    else:
        return User.query.get(jwt_data['sub'])


def try_user_pass(password, lookup):
    """
    Try to authenticate a user based on first a user ID, if ``lookup`` can be
    parsed into an ``int``, othewise it's treated as a user email. Uses
    ``verify_and_update_password`` to check the password
    """
    if password is None or lookup is None:
        return None

    try:
        user = User.query.get(int(lookup))

    except ValueError:
        user = User.query.filter_by(email=lookup).first()

    if not user:
        return None

    if verify_and_update_password(password, user):
        return user

    return None


def try_all_auth(api_key, password, username):
    """ Attempt auth with the API key, then username/password """
    user = try_jwt(api_key)
    if user is not None:
        return user

    user = try_user_pass(password, username)
    if user is not None:
        return user

    return None


def try_reqparser():
    """
    Use ``try_all_auth`` to attempt authorization from the ``LOGIN_FORM``
    ``RequestParser``. Will take JWT keys from ``api_key``, and
    ``username``/``password`` combinations
    """
    args = LOGIN_FORM.parse_args()
    return try_all_auth(
        args.get('api_key', None),
        args.get('password', None),
        args.get('username', None),
    )


def try_basic_auth():
    """
    Use ``try_all_auth`` to attempt authorization from HTTP basic auth. Only
    the password is used for API key
    """
    auth = request.authorization
    if not auth:
        return None

    return try_all_auth(
        auth.password,
        auth.password,
        auth.username,
    )
