"""Verified identity, application permissions, CSRF, and mutation attribution."""
import hmac
import secrets
from dataclasses import dataclass
from functools import wraps

import jwt
from flask import abort, g, has_request_context, request, session

from .database import connect

CAPABILITIES = {
    "reviewer": frozenset({"audits.read", "audits.result", "email.retry", "reports.read"}),
    "manager": frozenset({"audits.read", "audits.result", "email.retry", "reports.read", "audits.assign", "settings.manage", "templates.manage", "reviewers.manage"}),
    "admin": frozenset({"audits.read", "audits.result", "email.retry", "reports.read", "audits.assign", "settings.manage", "templates.manage", "reviewers.manage", "users.manage", "activity.read"}),
}


@dataclass(frozen=True)
class Principal:
    subject: str
    email: str
    name: str
    role: str


def allowed(capability):
    # Rendering helpers are also used outside HTTP by isolated business tests.
    return not has_request_context() or capability in CAPABILITIES.get(getattr(g, "principal", None).role if getattr(g, "principal", None) else "", ())


def require(capability):
    def decorate(function):
        @wraps(function)
        def wrapped(*args, **kwargs):
            if not allowed(capability):
                abort(403, "Your application role does not permit this action.")
            return function(*args, **kwargs)
        return wrapped
    return decorate


def event(db, action, target, detail=""):
    principal = getattr(g, "principal", None) if has_request_context() else None
    cursor = db.execute("INSERT INTO activity_events(actor,action,target,detail) VALUES(?,?,?,?)",
               (principal.subject if principal else "system", action, str(target), str(detail)))
    if has_request_context() and hasattr(g, "expected_revision"):
        g.expected_revision = cursor.lastrowid
        g.mutation_started = True


def seed_admins(config):
    with connect(config.database_path) as db:
        for email in config.bootstrap_admins:
            if "@" not in email or len(email) > 254:
                raise ValueError("Invalid bootstrap administrator email")
            db.execute("""INSERT INTO app_users(email,role) VALUES(?,'admin')
                ON CONFLICT(email) DO UPDATE SET role='admin',active=1,
                version=version+CASE WHEN role!='admin' OR active!=1 THEN 1 ELSE 0 END""", (email,))
        db.commit()


class AccessVerifier:
    def __init__(self, config):
        self.config = config
        self.keys = jwt.PyJWKClient(config.access_issuer + "/cdn-cgi/access/certs", lifespan=300, timeout=5)

    def verify(self, token):
        if not token or len(token) > 16384:
            raise ValueError("Missing or invalid assertion")
        key = self.keys.get_signing_key_from_jwt(token)
        claims = jwt.decode(token, key.key, algorithms=["RS256"], audience=self.config.access_audience,
                            issuer=self.config.access_issuer,
                            options={"require": ["exp", "iat", "iss", "aud", "sub", "email"]})
        if any(not isinstance(claims.get(k), str) or not claims[k].strip() for k in ("sub", "email")):
            raise ValueError("Invalid identity claims")
        if "@" not in claims["email"] or len(claims["email"]) > 254 or len(claims["sub"]) > 255:
            raise ValueError("Invalid identity claims")
        return claims


def authenticate(config, verifier):
    if config.env == "development":
        principal = Principal("development-sandbox", "developer@example.invalid", "Demo Developer", "admin")
        return establish_session(principal)
    try:
        claims = verifier.verify(request.headers.get("Cf-Access-Jwt-Assertion", ""))
    except (jwt.PyJWTError, ValueError, TypeError):
        abort(401, "A valid Cloudflare Access identity is required.")
    subject, email = claims["sub"], claims["email"].strip().lower()
    with connect(config.database_path) as db:
        if not db.in_transaction:
            db.execute("BEGIN IMMEDIATE")
        user = db.execute("SELECT * FROM app_users WHERE subject=?", (subject,)).fetchone()
        if not user:
            user = db.execute("SELECT * FROM app_users WHERE email=? AND subject IS NULL", (email,)).fetchone()
        if not user or not user["active"]:
            abort(403, "Your identity is verified, but application access has not been granted. Contact IT.")
        if user["email"].lower() != email:
            abort(403, "Your identity email has changed. Contact IT to update access.")
        if user["subject"] is None:
            db.execute("UPDATE app_users SET subject=? WHERE id=? AND subject IS NULL", (subject, user["id"]))
            event(db, "identity.bound", user["id"])
        db.commit()
    principal = Principal(subject, email, user["display_name"] or email.split("@")[0].replace(".", " ").title(), user["role"])
    return establish_session(principal)


def establish_session(principal):
    g.principal = principal
    if session.get("subject") != principal.subject:
        session.clear()
        session["subject"] = principal.subject
        session["csrf"] = secrets.token_urlsafe(32)
    return principal


def csrf_token(config, action):
    if has_request_context():
        return session.get("csrf", "")
    return ""  # Non-HTTP previews never submit forms.


def check_csrf(config):
    if request.headers.get("Origin") != config.public_url:
        abort(403, "Request origin is not permitted.")
    token = request.form.get("token", "")
    if not session.get("csrf") or not hmac.compare_digest(token, session["csrf"]):
        abort(403, "The form expired. Reload the page and try again.")


def save_user(config, form):
    email = form.get("email", "").strip().lower()
    role = form.get("role", "")
    name = form.get("display_name", "").strip()
    if role not in CAPABILITIES or "@" not in email or len(email) > 254 or len(name) > 100:
        raise ValueError("Enter a valid email, name, and role.")
    active = form.get("active", "") == "1"
    if email in config.bootstrap_admins and (role != "admin" or not active):
        raise ValueError("Bootstrap administrators must remain active administrators.")
    with connect(config.database_path) as db:
        if not db.in_transaction:
            db.execute("BEGIN IMMEDIATE")
        previous = db.execute("SELECT * FROM app_users WHERE email=?", (email,)).fetchone()
        version = form.get("version", "0")
        if not version.isdigit() or int(version) != (previous["version"] if previous else 0):
            raise ValueError("Access changed since this page loaded. Refresh and try again.")
        if previous and previous["role"] == "admin" and previous["active"] and (not active or role != "admin"):
            if db.execute("SELECT count(*) FROM app_users WHERE role='admin' AND active=1").fetchone()[0] <= 1:
                raise ValueError("The last active administrator cannot be removed.")
        db.execute("""INSERT INTO app_users(email,display_name,role,active) VALUES(?,?,?,?)
            ON CONFLICT(email) DO UPDATE SET display_name=excluded.display_name,role=excluded.role,
            active=excluded.active,version=app_users.version+1""", (email, name, role, int(active)))
        event(db, "access.updated", email, f"role={role}; active={active}")
        db.commit()
