""" API relating to JWT authentication """
from datetime import datetime

import jwt

from flask import url_for
from flask_restful import fields, Resource
from flask_security import current_user, login_required

from .base import BaseRequestParser
from .exceptions import OnlyMeError, WrappedTokenError, WrongAuthMethodError
from .fields import NonBlankInput
from .util import DT_FORMATTER
from dockci.server import API, CONFIG
from dockci.util import jwt_token


JWT_ME_DETAIL_PARSER = BaseRequestParser()

JWT_NEW_PARSER = BaseRequestParser()
JWT_NEW_PARSER.add_argument('name',
                            required=True, type=NonBlankInput(),
                            help="Service name for the token")
JWT_NEW_PARSER.add_argument('exp',
                            type=DT_FORMATTER,
                            help="Expiration time of the token")


class JwtNew(Resource):
    """ API resource that handles creating JWT tokens """
    @login_required
    def post(self, user_id):
        """ Create a JWT token for a user """
        if current_user.id != user_id:
            raise OnlyMeError("create JWT tokens")

        args = JWT_NEW_PARSER.parse_args(strict=True)
        return {'token': jwt_token(**args)}, 201


class JwtMeDetail(Resource):
    """
    API resource to handle getting current JWT token details, and creating one
    for the current user
    """
    @login_required
    def get(self):
        """ Get details about the current JWT token """
        args = JWT_ME_DETAIL_PARSER.parse_args()
        api_key = args['x_dockci_api_key'] or args['hx_dockci_api_key']
        if api_key is None:
            raise WrongAuthMethodError("a JWT token")
        else:
            return JwtDetail().get(api_key)

    @login_required
    def post(self):
        """ Create a JWT token for the currently logged in user """
        return JwtNew().post(current_user.id)


class JwtDetail(Resource):
    """ API resource to handle getting job details """
    def get(self, token):
        """ Get details about a JWT token """
        try:
            jwt_data = jwt.decode(token, CONFIG.secret)

        except jwt.exceptions.InvalidTokenError as ex:
            raise WrappedTokenError(ex)

        jwt_data['iat'] = DT_FORMATTER.format(
            datetime.fromtimestamp(jwt_data['iat'])
        )

        try:
            user_id = jwt_data['sub']
            jwt_data['sub_detail'] = url_for('user_detail', user_id=user_id)
        except KeyError:
            pass

        return jwt_data


API.add_resource(JwtNew,
                 '/users/<int:id>/jwt',
                 endpoint='jwt_user_new')
API.add_resource(JwtMeDetail,
                 '/me/jwt',
                 endpoint='jwt_me_detail')
API.add_resource(JwtDetail,
                 '/jwt/<string:token>',
                 endpoint='jwt_detail')
