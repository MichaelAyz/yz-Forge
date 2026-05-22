import os
import yaml
import asyncio
import secrets
import bcrypt
import aiosqlite
from fastapi import HTTPException, Security, Request
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

with open(os.environ.get("CONFIG_PATH", "config.yaml")) as f:
    CONFIG = yaml.safe_load(f)

DB_PATH = CONFIG["registry"]["db_path"]
# If running locally from root, paths should work. If in docker, DB_PATH is internal.
TOKENS_TABLE = CONFIG["auth"].get("tokens_table", "tokens")

security = HTTPBearer()

async def init_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(f"""
            CREATE TABLE IF NOT EXISTS {TOKENS_TABLE} (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                publisher TEXT UNIQUE NOT NULL,
                token_hash TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await db.commit()

async def create_token(publisher: str):
    await init_db()
    
    # Generate a secure random token
    raw_token = f"forge_{secrets.token_urlsafe(32)}"
    token_hash = bcrypt.hashpw(raw_token.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')
    
    async with aiosqlite.connect(DB_PATH) as db:
        try:
            await db.execute(
                f"INSERT INTO {TOKENS_TABLE} (publisher, token_hash) VALUES (?, ?)",
                (publisher, token_hash)
            )
            await db.commit()
            return raw_token
        except aiosqlite.IntegrityError:
            print(f"Error: Publisher '{publisher}' already exists. Please choose a different name.")
            return None

async def validate_token(credentials: HTTPAuthorizationCredentials = Security(security)) -> str:
    token = credentials.credentials
    if not token:
        raise HTTPException(status_code=401, detail="Missing token")

    await init_db()
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(f"SELECT publisher, token_hash FROM {TOKENS_TABLE}") as cursor:
            async for row in cursor:
                publisher, token_hash = row
                if bcrypt.checkpw(token.encode('utf-8'), token_hash.encode('utf-8')):
                    return publisher
                    
    raise HTTPException(status_code=401, detail="Invalid token")

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Forge Token Admin CLI")
    parser.add_argument("action", choices=["create-token"], help="Action to perform")
    parser.add_argument("--publisher", required=True, help="Name of the token owner/publisher")
    args = parser.parse_args()

    if args.action == "create-token":
        raw_token = asyncio.run(create_token(args.publisher))
        if raw_token:
            print("\nToken created successfully!")
            print(f"Publisher: {args.publisher}")
            print(f"Token:     {raw_token}")
            print("\nSAVE THIS TOKEN. It is hashed in the database and cannot be recovered.\n")