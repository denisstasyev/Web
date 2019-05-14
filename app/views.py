import re
from flask import (
    jsonify,
    request,
    abort,
    make_response,
    url_for,
    render_template,
)
import wtforms_json
from flask_login import current_user, login_user, logout_user, login_required
from sqlalchemy import or_, any_, and_

from app import app, db, cache

from .tasks import send_email
from .models import (
    User,
    Member,
    Chat,
    Message,
    Attachment,
    model_as_dict,
)
from .forms import (
    UserForm,
    MemberForm,
    ChatForm,
    MessageForm,
    AttachmentForm,
    LoginForm,
    RegistrationForm,
)


wtforms_json.init()
default_chatname_pattern = re.compile(r"chat\d+")


def clean_correct_html(correct_html):
    correct_html_pattern = re.compile(r"(\<(/?[^>]+)>)")
    clean_text = re.sub(correct_html_pattern, '', correct_html)
    return clean_text


@app.errorhandler(400)
def bad_request(error):
    return make_response(jsonify({"error": "Bad request", "description": clean_correct_html(error.get_description(request.environ))}), 400)


@app.errorhandler(403)
def forbidden(error):
    return make_response(jsonify({"error": "Forbidden", "description": clean_correct_html(error.get_description(request.environ))}), 403)


@app.errorhandler(404)
def not_found(error):
    return make_response(jsonify({"error": "Not found", "description": clean_correct_html(error.get_description(request.environ))}), 404)


# Authorization API


@app.route("/api/login/", methods=["POST"])
def login():
    """Login User"""
    if not request.json:
        abort(400)
    if current_user.is_authenticated:
        return jsonify({"result": True}), 200

    form = LoginForm.from_  # , load_userjson(request.json)  ??????

    if not form.validate():
        print(form.errors)
        abort(400)

    user = User.query.filter(User.username == form.username.data).first_or_404()
    if user is None or (user.password != form.password.data):
        return jsonify({"result": False}), 401

    if not login_user(user, remember=form.remember_me.data):
        return jsonify({"result": False}), 401
    return jsonify({"result": True}), 202


# TODO: clean cookies after logout
@app.route("/api/logout/", methods=["POST"])
@login_required
def logout():
    """Logout User"""
    logout_user()
    return jsonify({"result": True}), 200


@app.route("/api/register/", methods=["POST"])
def register():
    """Register User"""
    if not request.json:
        abort(400)
    if current_user.is_authenticated:
        return jsonify({"result": True}), 200

    form = RegistrationForm.from_json(request.json)

    if not form.validate():
        print(form.errors)
        abort(400)

    user = User()
    form.populate_obj(user)

    db.session.add(user)
    db.session.commit()

    if user.email is not None:
        send_email.apply_async(
            (
                "Registration",
                [user.email],
                "Successful registration in the STD-messenger!",
                render_template(
                    "email_registration.html", user=user, password=form.password.data
                ),
            )
        )

    users = User.query.all()
    users = [model_as_dict(user) for user in users]
    rv = jsonify({"users": list(map(make_public_uri_user, users))}), 200
    cache.set("users", rv, timeout=5 * 60)

    return jsonify({"user": make_public_uri_user(model_as_dict(user))}), 201


# API for User


def make_public_uri_user(user):
    """Create URI for User"""
    new_user = {}
    for field in user:
        if field == "user_id":
            new_user["uri"] = url_for(
                "get_user", username=user["username"], _external=True
            )
        elif field == "password":
            pass
        else:
            new_user[field] = user[field]
    return new_user


# curl -X GET http://std-messenger.com/api/users/ --cookie "remember_token=...; session=..."
#TODO: limit query to 10 replies
@app.route("/api/get_users/", methods=["GET"])
@login_required
def get_users():
    """Get Users"""
    rv = cache.get("users")
    if rv is None:
        users = User.query.all()
        users = [model_as_dict(user) for user in users]
        rv = jsonify({"users": list(map(make_public_uri_user, users))}), 200
        cache.set("users", rv, timeout=5 * 60)
    return rv


# curl -X GET http://std-messenger.com/api/users/denis/ --cookie "remember_token=...; session=..."
@app.route("/api/get_user/<string:username>/", methods=["GET"])
@login_required
def get_user(username):
    """Get User"""
    user = User.query.filter(User.username == username).first_or_404()
    return jsonify({"user": make_public_uri_user(model_as_dict(user))}), 200


# curl -i -H "Content-Type: application/json" -X PUT -d '{"username": "denis",
#  "first_name": "Denis", "last_name": "Stasyev"}' http://std-messenger.com/api/users/ddenis/
#  --cookie "remember_token=...; session=..."
@app.route("/api/update_current_user/", methods=["PUT"])
@login_required
def update_current_user():
    """Update current User"""
    if not request.json:
        abort(400)

    username = current_user.username

    form = UserForm.from_json(request.json)

    if not form.validate():
        print(form.errors)
        abort(400)

    logout()

    user = User.query.filter(User.username == username).first_or_404()

    form.populate_obj(user)

    db.session.query(User).filter(User.user_id == user.user_id).update(
        model_as_dict(user)
    )
    db.session.commit()

    users = User.query.all()
    users = [model_as_dict(user) for user in users]
    rv = jsonify({"users": list(map(make_public_uri_user, users))}), 200
    cache.set("users", rv, timeout=5 * 60)

    return jsonify({"user": make_public_uri_user(model_as_dict(user))}), 202


# curl -X DELETE  http://std-messenger.com/api/users/denis/
#  --cookie "remember_token=...; session=..."
@app.route("/api/delete_current_user/", methods=["DELETE"])
@login_required
def delete_current_user():
    """Delete current User and all his data"""
    username = current_user.username

    logout()

    user = User.query.filter(User.username == username).first_or_404()

    db.session.query(Attachment).filter(Attachment.user_id == user.user_id).delete()
    db.session.query(Member).filter(
        Member.user_id == user.user_id
    ).delete()  # TODO: как удалять файл из облака?
    db.session.query(Message).filter(Message.user_id == user.user_id).delete()

    db.session.execute(
        'DELETE FROM members USING chats\
         WHERE chats.chat_id = members.chat_id AND chats.creator_id = :param',
        {"param": user.user_id}
    )
    db.session.execute(
        'DELETE FROM messages USING chats\
         WHERE chats.chat_id = messages.chat_id AND chats.creator_id = :param',
        {"param": user.user_id}
    )
    db.session.query(Chat).filter(Chat.creator_id == user.user_id).delete()

    db.session.query(Chat).filter(Chat.members is not None).delete()

    db.session.query(Attachment).filter(
        and_(
            Attachment.user_id.is_(None),
            and_(Attachment.chat_id.is_(None), Attachment.message_id.is_(None)),
        )
    ).delete()  # TODO: как удалять файл из облака?

    db.session.delete(user)
    db.session.commit()

    users = User.query.all()
    users = [model_as_dict(user) for user in users]
    rv = jsonify({"users": list(map(make_public_uri_user, users))}), 200
    cache.set("users", rv, timeout=5 * 60)

    return jsonify({"result": True}), 200


# API for Member


def make_public_member(member):
    """Create public data of Member"""
    user = User.query.filter(User.user_id == member["user_id"]).first_or_404()
    chat = Chat.query.filter(Chat.chat_id == member["chat_id"]).first_or_404()

    new_member = {}
    new_member["username"] = user.username
    new_member["chatname"] = chat.chatname
    return new_member


#TODO: limit query to 10 replies
@app.route("/api/get_members/<string:chatname>/", methods=["GET"])
@login_required
def get_members(chatname):
    """Get Members in Chat by Chat.chatname"""
    chat = Chat.query.filter(Chat.chatname == chatname).first_or_404()
    members = Member.query.filter(Member.chat_id == chat.chat_id).all()

    user_ids = [member.user_id for member in members]
    # Current User in this Chat
    if not current_user.user_id in user_ids:
        abort(403)

    members = [model_as_dict(member) for member in members]
    return jsonify({"members": list(map(make_public_member, members))}), 200


# Send json with username and chatname
@app.route("/api/create_member/", methods=["POST"])
@login_required
def create_member():
    """Create Member (Join Chat)"""
    if not request.json:
        abort(400)

    form = MemberForm.from_json(request.json)

    if not form.validate():
        print(form.errors)
        abort(400)

    user = User.query.filter(User.username == form.username.data).first_or_404()
    chat = Chat.query.filter(Chat.chatname == form.chatname.data).first_or_404()

    member = Member(user.user_id, chat.chat_id)
    form.populate_obj(member)

    chat = Chat.query.filter(Chat.chat_id == member.chat_id).first_or_404()

    if chat.is_public:
        # only User themselves can join public Chat
        if member.user_id != current_user.user_id:
            abort(400)
    else:
        # only creator of private Chat can add User in private Chat
        if current_user.user_id != chat.creator_id:
            abort(400)

    db.session.add(member)
    db.session.commit()

    return jsonify({"member": make_public_member(model_as_dict(member))}), 201


@app.route("/api/delete_member/", methods=["DELETE"])
@login_required
def delete_member():
    """Delete Member (Leave Chat)"""
    if not request.json:
        abort(400)

    form = MemberForm.from_json(request.json)

    if not form.validate():
        print(form.errors)
        abort(400)

    user = User.query.filter(User.username == form.username.data).first_or_404()
    chat = Chat.query.filter(Chat.chatname == form.chatname.data).first_or_404()

    member = Member.query.filter(and_(Member.user_id == user.user_id, Member.chat_id == chat.chat_id)).first_or_404()

    # only User themselves and Chat.creator_id can remove User from Chat
    # if Chat.creator_id leaves Chat then this Chat is deleted
    if not (current_user.user_id in [member.user_id, chat.creator_id]):
        abort(400)

    db.session.delete(member)
    if chat.creator_id == member.user_id:
        delete_chat(chat.chatname)

    db.session.commit()

    return jsonify({"result": True}), 200


# API for Chat


def make_public_uri_chat(chat):
    """Create URI for Chat"""
    new_chat = {}
    for field in chat:
        if field == "chat_id":
            new_chat["uri"] = url_for(
                "get_my_chat", chatname=chat["chatname"], _external=True
            )
        else:
            new_chat[field] = chat[field]
    return new_chat


#TODO: limit query to 10 replies
@app.route("/api/get_all_chats/", methods=["GET"])
@login_required
def get_all_chats():
    """Get all available for current User Chats"""
    chats = db.session.execute(
        """SELECT * FROM chats
           WHERE chats.is_public OR chats.chat_id = ANY (
               SELECT members.chat_id FROM members
               WHERE members.user_id = :param)""",
        {"param": current_user.user_id}
    )

    chats = [{column: value for column, value in rowproxy.items()} for rowproxy in chats]
    return jsonify({"chats": list(map(make_public_uri_chat, chats))}), 200


#TODO: limit query to 10 replies
@app.route("/api/get_my_chats/", methods=["GET"])
@login_required
def get_my_chats():
    """Get Chats in which current User participates"""
    chats = db.session.execute(
        """SELECT * FROM chats
           WHERE chats.chat_id = ANY (
               SELECT members.chat_id FROM members
               WHERE members.user_id = :param)""",
        {"param": current_user.user_id}
    )

    chats = [{column: value for column, value in rowproxy.items()} for rowproxy in chats]
    return jsonify({"chats": list(map(make_public_uri_chat, chats))}), 200


@app.route("/api/get_my_chat/<string:chatname>/", methods=["GET"])
@login_required
def get_my_chat(chatname):
    """Get Chat"""
    chats = db.session.execute(
        """SELECT * FROM chats\
           WHERE chats.chatname = :param1 AND
               chats.chat_id = ANY (
               SELECT members.chat_id FROM members
               WHERE members.user_id = :param2)""",
        {"param1": chatname, "param2": current_user.user_id}
    )

    chats = [{column: value for column, value in rowproxy.items()} for rowproxy in chats]

    if not len(chats):
        abort(404)

    return jsonify({"chat": make_public_uri_chat(chats[0])}), 200


@app.route("/api/create_chat/", methods=["POST"])
@login_required
def create_chat():
    """Create Chat"""
    if not request.json:
        abort(400)

    form = ChatForm.from_json(request.json)

    if not form.validate():
        print(form.errors)
        abort(400)

    chat = Chat(current_user.user_id)
    form.populate_obj(chat)

    if default_chatname_pattern.match(chat.chatname):
        abort(400)

    db.session.add(chat)
    db.session.flush()

    db.session.refresh(chat)
    member = Member(current_user.user_id, chat.chat_id)

    db.session.add(member)
    db.session.commit()

    return jsonify({"chat": make_public_uri_chat(model_as_dict(chat))}), 201


# curl -i -H "Content-Type: application/json" -X PUT -d '{"chat_title": "topic"}'
# http://std-messenger.com/api/update_chat/chat3/ --cookie "session=..."
@app.route("/api/update_chat/<string:chatname>/", methods=["PUT"])
@login_required
def update_chat(chatname):
    """Update Chat"""
    if not request.json:
        abort(400)

    # Each Member can update Chat
    chat_ids = db.session.execute(
        """SELECT chats.chat_id FROM chats
           WHERE chats.chatname = :param1 AND
               chats.chat_id = ANY (
               SELECT members.chat_id FROM members
               WHERE members.user_id = :param2)""",
        {"param1": chatname, "param2": current_user.user_id}
    )

    chat_ids = [{column: value for column, value in rowproxy.items()} for rowproxy in chat_ids]

    if not len(chat_ids):
        abort(400)

    chat = Chat.query.filter(Chat.chat_id == chat_ids[0]["chat_id"]).first_or_404()
    chat_title = chat.chat_title

    form = ChatForm.from_json(request.json)

    if not form.validate():
        print(form.errors)
        abort(400)

    form.populate_obj(chat)

    if chat.chatname is None:
        chat.chatname = chatname

    if chat.chat_title is None:
        chat.chat_title = chat_title

    if default_chatname_pattern.match(chat.chatname) and (chat.chatname != "chat" + chat.chat_id):
        abort(400)

    db.session.query(Chat).filter(Chat.chat_id == chat.chat_id).update(
        model_as_dict(chat)
    )
    db.session.commit()

    return jsonify({"chat": make_public_uri_chat(model_as_dict(chat))}), 202


@app.route("/api/delete_chat/<string:chatname>/", methods=["DELETE"])
@login_required
def delete_chat(chatname):
    """Delete Chat"""
    chat = Chat.query.filter(Chat.chatname == chatname).first_or_404()

    if chat.creator_id != current_user.user_id:
        abort(403)

    db.session.query(Message).filter(Message.chat_id == chat.chat_id).delete()
    db.session.query(Member).filter(Member.chat_id == chat.chat_id).delete()

    db.session.query(Attachment).filter(
        and_(
            Attachment.user_id.is_(None),
            and_(Attachment.chat_id.is_(None), Attachment.message_id.is_(None)),
        )
    ).delete()  # TODO: как удалять файл из облака?

    db.session.delete(chat)
    db.session.commit()

    return jsonify({"result": True}), 200


# TODO: API for Message


#TODO: limit query to 10 replies
@app.route("/api/get_messages/", methods=["GET"])
@login_required
def get_messages():
    """Get all Messages created by current_user"""
    messages = Message.query.filter(Message.user_id == current_user.user_id)
    messages = [model_as_dict(message) for message in messages]
    return jsonify({"messages": list(messages)}), 200


@app.route("/api/get_message/<int:message_id>/", methods=["GET"])
@login_required
def get_message(message_id):
    """Get Message created by current_user"""
    message = Message.query.filter(
        and_(Message.message_id == message_id, Message.user_id == current_user.user_id)
    ).first_or_404()
    return jsonify({"message": model_as_dict(message)}), 200


@app.route("/api/create_message/", methods=["POST"])
@login_required
def create_message():
    """Create Message"""
    if not request.json:
        abort(400)

    form = MessageForm.from_json(request.json)

    if not form.validate():
        print(form.errors)
        abort(400)

    message = Message()
    form.populate_obj(message)

    if (
        message.user_id != current_user.user_id
        or Member.query.filter(
            and_(
                Member.chat_id == message.chat_id,
                Member.user_id == current_user.user_id,
            )
        ).first()
        is None
    ):
        abort(400)

    db.session.add(message)
    db.session.commit()

    return jsonify({"message": model_as_dict(message)}), 201


@app.route("/api/update_message/<int:message_id>/", methods=["PUT"])
@login_required
def update_message(message_id):
    """Update Message"""
    if not request.json:
        abort(400)

    message = Message.query.filter(
        and_(Message.message_id == message_id, Message.user_id == current_user.user_id)
    ).first_or_404()
    form = MessageForm.from_json(request.json)

    if not form.validate():
        print(form.errors)
        abort(400)

    form.populate_obj(message)

    db.session.query(Message).filter(Message.message_id == message.message_id).update(
        model_as_dict(message)
    )
    db.session.commit()

    return jsonify({"message": model_as_dict(message)}), 202


@app.route("/api/delete_message/<int:message_id>/", methods=["DELETE"])
@login_required
def delete_message(message_id):
    """Delete Message"""
    message = Message.query.filter(
        and_(Message.message_id == message_id, Message.user_id == current_user.user_id)
    ).first_or_404()

    db.session.delete(message)
    db.session.commit()

    return jsonify({"result": True}), 200


# API for Attachment


@app.route("/api/get_attachments/", methods=["GET"])
@login_required
def get_attachments():
    """Get all Attachments created by current_user"""
    attachments = Attachment.query.filter(Attachment.user_id == current_user.user_id)
    attachments = [model_as_dict(attachment) for attachment in attachments]
    return jsonify({"attachments": list(attachments)}), 200


@app.route("/api/get_attachment/<int:attachment_id>/", methods=["GET"])
@login_required
def get_attachment(attachment_id):
    """Get Attachment"""
    attachment = Attachment.query.filter(
        and_(
            Attachment.attachment_id == attachment_id,
            or_(
                Attachment.user_id == current_user.user_id,
                Attachment.chat_id
                == any_(
                    User.query.filter(User.user_id == current_user.user_id).memberships
                ).chat_id,
            ),
        )
    ).first_or_404()
    return jsonify({"attachment": model_as_dict(attachment)}), 200


@app.route("/api/create_attachment/", methods=["POST"])
@login_required
def create_attachment():
    """Create Attachment"""
    if not request.json:
        abort(400)

    form = AttachmentForm.from_json(request.json)

    if not form.validate():
        print(form.errors)
        abort(400)

    attachment = Attachment()
    form.populate_obj(attachment)

    if attachment.user_id != current_user.user_id:
        abort(400)

    db.session.add(attachment)
    db.session.commit()

    return jsonify({"attachment": model_as_dict(attachment)}), 201


@app.route("/api/update_attachment/<int:attachment_id>/", methods=["PUT"])
@login_required
def update_attachment(attachment_id):
    """Update Attachment"""
    if not request.json:
        abort(400)

    attachment = Attachment.query.filter(
        and_(
            Attachment.attachment_id == attachment_id,
            Attachment.user_id == current_user.user_id,
        )
    ).first_or_404()
    form = AttachmentForm.from_json(request.json)

    if not form.validate():
        print(form.errors)
        abort(400)

    form.populate_obj(attachment)

    db.session.query(Attachment).filter(
        Attachment.attachment_id == attachment.attachment_id
    ).update(model_as_dict(attachment))
    db.session.commit()

    return jsonify({"attachment": model_as_dict(attachment)}), 202


@app.route("/api/attachments/<int:attachment_id>/", methods=["DELETE"])
@login_required
def delete_attachment(attachment_id):
    """Delete Attachment"""
    # TODO: как удалять файл из облака?
    attachment = Attachment.query.filter(
        and_(
            Attachment.attachment_id == attachment_id,
            Attachment.user_id == current_user.user_id,
        )
    ).first_or_404()

    db.session.delete(attachment)
    db.session.commit()

    return jsonify({"result": True}), 200


# Some other API methods


#################

# @app.route("/search_users/", methods=["GET"])
# def search_users(query=None, limit=None):
#     """Поиск пользователей"""
#     return jsonify(users=["User1", "User2"])


# @app.route("/search_chats/", methods=["GET"])
# def search_chats(query=None, limit=None):
#     """Поиск среди чатов пользователя"""
#     return jsonify(chats=["Chat1", "Chat2"])
