#!/bin/bash
set -e

# Wait for PostgreSQL to be ready (only if using a PostgreSQL URL)
if [[ "$PURE_DATABASE_URL" == postgresql* ]]; then
    echo "Waiting for PostgreSQL..."
    until python -c "from sqlalchemy import create_engine; engine = create_engine('$PURE_DATABASE_URL'); engine.connect().close()" 2>/dev/null; do
        sleep 1
    done
    echo "PostgreSQL is ready."
fi

# Run Alembic migrations; fall back to create_all for SQLite dev
if python -c "import alembic" 2>/dev/null; then
    echo "Running database migrations..."
    alembic upgrade head
else
    echo "Alembic not found; initializing database with create_all..."
    python -m pure.db.init_db
fi

# Start the API server
exec uvicorn pure.server.main:app --host 0.0.0.0 --port 8000
