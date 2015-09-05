from flask import request
from flask_restful import fields, marshal_with, Resource, reqparse

from .base import BaseDetailResource
from .util import new_edit_parsers
from dockci.models.auth import User
from dockci.server import API, DB


BASIC_FIELDS = {
    'id': fields.Integer(),
    'email': fields.String(),
    'active': fields.Boolean(),
}

LIST_FIELDS = {
    'detail': fields.Url('user_detail'),
}
LIST_FIELDS.update(BASIC_FIELDS)


DETAIL_FIELDS = {
    'confirmed_at': fields.DateTime(),
    #'roles': fields.Nested(),
}
DETAIL_FIELDS.update(BASIC_FIELDS)


SHARED_PARSER_ARGS = {
    'email': dict(
        help="Contact email address",
        required=None,
    ),
    'password': dict(
        help="Password for user to authenticate",
        required=None,
    ),
    'active': dict(
        help="Whether or not the user can login",
        required=False,
    ),
}

USER_NEW_PARSER = reqparse.RequestParser(bundle_errors=True)
USER_EDIT_PARSER = reqparse.RequestParser(bundle_errors=True)
new_edit_parsers(USER_NEW_PARSER, USER_EDIT_PARSER, SHARED_PARSER_ARGS)


class UserList(Resource):
    @marshal_with(LIST_FIELDS)
    def get(self):
        return User.query.all()


class UserDetail(BaseDetailResource):
    @marshal_with(DETAIL_FIELDS)
    def get(self, id):
        return User.query.get_or_404(id)

    @marshal_with(DETAIL_FIELDS)
    def put(self, id):
        user = User()
        return self.handle_write(user, USER_NEW_PARSER)

    @marshal_with(DETAIL_FIELDS)
    def post(self, id):
        user = User.query.get_or_404(id)
        return self.handle_write(user, USER_EDIT_PARSER)


API.add_resource(UserList,
                 '/users',
                 endpoint='user_list')
API.add_resource(UserDetail,
                 '/users/<int:id>',
                 endpoint='user_detail')
