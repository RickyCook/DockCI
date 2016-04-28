""" API relating to User model objects """
from flask import abort
from flask_restful import fields, inputs, marshal_with, Resource
from flask_security import current_user, login_required

from .base import BaseDetailResource, BaseRequestParser
from .fields import GravatarUrl, NonBlankInput, RewriteUrl
from .util import DT_FORMATTER, new_edit_parsers
from dockci.models.auth import User
from dockci.server import API, DB


BASIC_FIELDS = {
    'id': fields.Integer(),
    'email': fields.String(),
    'active': fields.Boolean(),
}

LIST_FIELDS = {
    'detail': RewriteUrl('user_detail', rewrites=dict(user_id='id')),
}
LIST_FIELDS.update(BASIC_FIELDS)


DETAIL_FIELDS = {
    'avatar': GravatarUrl(attr_name='email'),
    'confirmed_at': DT_FORMATTER,
    'emails': fields.List(fields.String()),
}
DETAIL_FIELDS.update(BASIC_FIELDS)


SHARED_PARSER_ARGS = {
    'email': dict(
        help="Contact email address",
        required=None, type=NonBlankInput(),
    ),
    'password': dict(
        help="Password for user to authenticate",
        required=None, type=NonBlankInput(),
    )
}

USER_NEW_PARSER = BaseRequestParser()
USER_EDIT_PARSER = BaseRequestParser()
new_edit_parsers(USER_NEW_PARSER, USER_EDIT_PARSER, SHARED_PARSER_ARGS)

USER_EDIT_PARSER.add_argument('active',
                              help="Whether or not the user can login",
                              type=inputs.boolean)


SECURITY_STATE = APP.extensions['security']


# pylint:disable=no-self-use

class UserList(BaseDetailResource):
    """ API resource that handles listing users, and creating new users """
    @login_required
    @marshal_with(LIST_FIELDS)
    def get(self):
        """ List all users """
        return User.query.all()

    @marshal_with(DETAIL_FIELDS)
    def post(self):
        """ Create a new user """
        user = User()
        return self.handle_write(user, USER_NEW_PARSER)


class UserDetail(BaseDetailResource):
    """ API resource that handles getting user details, and updating users """
    @classmethod
    def user_or_404(cls, user_id):
        """ Return a user from the security store, or 404 """
        user = SECURITY_STATE.datastore.get_user(user_id)
        if user is None:
            return abort(404)

        return user

    @login_required
    @marshal_with(DETAIL_FIELDS)
    def get(self, user_id):
        """ Get a user's details """
        return self.user_or_404(user_id)

    @login_required
    @marshal_with(DETAIL_FIELDS)
    def post(self, user_id, user=None):
        """ Update a user """
        if user is None:
            user = self.user_or_404(user_id)

        return self.handle_write(user, USER_EDIT_PARSER)


class MeDetail(Resource):
    """ Wrapper around ``UserDetail`` to user the current user """
    @login_required
    @marshal_with(DETAIL_FIELDS)
    def get(self):
        """ Get details of the current user """
        return current_user

    @login_required
    def post(self):
        """ Update the current user """
        return UserDetail().post(None, current_user)


class MeEmailDetail(Resource):
    """ Deletion of user email addresses """
    @login_required
    def delete(self, email):
        """ Delete an email from the current user """
        email = current_user.emails.filter(
            UserEmail.email.ilike(email),
        ).first_or_404()
        DB.session.delete(email)
        DB.session.commit()
        return {'message': '%s deleted' % email.email}


API.add_resource(UserList,
                 '/users',
                 endpoint='user_list')
API.add_resource(UserDetail,
                 '/users/<int:user_id>',
                 endpoint='user_detail')
API.add_resource(MeDetail,
                 '/me',
                 endpoint='me_detail')
API.add_resource(MeEmailDetail,
                 '/me/<string:email>',
                 endpoint='me_email_detail')
