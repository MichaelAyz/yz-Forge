# TODO Person 1
# save_blob(file_bytes) -> sha256_hex
# get_blob(sha256_hex) -> file_bytes or path
# blob_exists(sha256_hex) -> bool

def save_blob(file_bytes: bytes) -> str:
    raise NotImplementedError

def get_blob(sha256_hex: str):
    raise NotImplementedError