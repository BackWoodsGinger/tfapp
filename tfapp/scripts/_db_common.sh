# Shared helpers for backup_db.sh and restore_db.sh

resolve_python() {
    local project_dir="$1"
    if [ -n "${VIRTUAL_ENV:-}" ] && [ -x "$VIRTUAL_ENV/bin/python" ]; then
        echo "$VIRTUAL_ENV/bin/python"
        return 0
    fi

    local candidates=(
        "$project_dir/../venv/bin/python"
        "$project_dir/../.venv/bin/python"
        "$project_dir/../env/bin/python"
        "$project_dir/.venv/bin/python"
        "$project_dir/venv/bin/python"
        "$project_dir/env/bin/python"
    )
    local candidate
    for candidate in "${candidates[@]}"; do
        if [ -x "$candidate" ]; then
            echo "$candidate"
            return 0
        fi
    done
    if command -v python3 >/dev/null 2>&1; then
        command -v python3
        return 0
    fi
    if command -v python >/dev/null 2>&1; then
        command -v python
        return 0
    fi
    return 1
}

load_db_env() {
    local project_dir="$1"
    local python
    python="$(resolve_python "$project_dir")" || {
        echo "Error: Python not found. Activate your venv or install python3." >&2
        echo "Looked for: ${project_dir}/../venv, ${project_dir}/../env, ${project_dir}/../.venv, ${project_dir}/venv, ${project_dir}/env" >&2
        exit 1
    }

    local db_exports
    if ! db_exports="$(
        cd "$project_dir" || exit 1
        "$python" manage.py shell -c "
from django.conf import settings
db = settings.DATABASES['default']
host = db.get('HOST') or 'localhost'
port = db.get('PORT') or '5432'
print('export DB_NAME=' + repr(db['NAME']))
print('export DB_USER=' + repr(db['USER']))
print('export DB_PASSWORD=' + repr(db['PASSWORD']))
print('export DB_HOST=' + repr(host))
print('export DB_PORT=' + repr(port))
"
    )"; then
        echo "Error: Could not load database settings from Django (.env / settings)." >&2
        echo "Python used: $python" >&2
        echo "If this is cron, make sure the virtualenv exists at ${project_dir}/../venv or ${project_dir}/venv." >&2
        exit 1
    fi

    eval "$db_exports"

    if [ -z "${DB_NAME:-}" ] || [ -z "${DB_USER:-}" ]; then
        echo "Error: Could not load database settings from Django (.env / settings)." >&2
        echo "Python used: $python" >&2
        exit 1
    fi
}
