-- Core tables for Acme Shop
CREATE TABLE users (
    id INTEGER PRIMARY KEY,
    email TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'customer'
);

CREATE TABLE orders (
    id INTEGER PRIMARY KEY,
    owner_id INTEGER NOT NULL REFERENCES users(id),
    total_cents INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'open'
);

CREATE TABLE refresh_tokens (
    user_id INTEGER NOT NULL REFERENCES users(id),
    token TEXT PRIMARY KEY,
    expires_at REAL NOT NULL
);
