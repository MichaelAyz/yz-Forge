# TODO Person 3
# create_token(label) -> raw_token  (store hashed in DB)
# verify_token(raw_token) -> identity or None
# require_auth(request) -> identity or raise 401

def create_token(label: str) -> str:
    raise NotImplementedError

def verify_token(raw_token: str):
    raise NotImplementedError