#!/bin/bash
set -e

PROJECT_NAME="ftp2s3"
PROJECT_SLUG="ftp2s3"
GIT_REPO_URL="https://github.com/tonyliuzj/ftp2s3.git"
INSTALL_DIR="/opt/ftp2s3"
RUNTIME="python"
SERVICE_TYPE="systemd"
START_COMMAND="venv/bin/python -m uvicorn app.main:app"
BUILD_COMMAND="none"
DEPENDENCY_INSTALL_COMMAND="venv/bin/pip install -r requirements.txt"
ENV_FILE_NAME=".env"
DEFAULT_PORT="8000"

APP_USER="ftp2s3"
APP_GROUP="ftp2s3"
SYSTEMD_SERVICE_NAME="ftp2s3"
SYSTEMD_SERVICE_FILE="/etc/systemd/system/${SYSTEMD_SERVICE_NAME}.service"
INSTALL_STATE_FILE="${INSTALL_DIR}/.installer-state"
VENV_DIR="${INSTALL_DIR}/venv"
DOCKER_APP_IMAGE="${PROJECT_SLUG}-app:latest"
DOCKER_LEGACY_IMAGE="${PROJECT_SLUG}:latest"
DOCKER_LEGACY_CONTAINER="${PROJECT_SLUG}"
DOCKER_COMPOSE_APP_FILE="${INSTALL_DIR}/docker-compose.yml"
DOCKER_COMPOSE_POSTGRES_FILE="${INSTALL_DIR}/postgres/docker-compose.yml"
DOCKER_COMPOSE_PROJECT="${PROJECT_SLUG}"
DOCKER_APP_SERVICE="app"
DOCKER_POSTGRES_SERVICE="postgres"
DOCKER_INTERNAL_PORT="8000"

APP_PORT="${DEFAULT_PORT}"
PUBLIC_BASE_URL=""
DATABASE_URL=""
POSTGRES_MODE="existing"
POSTGRES_SERVICE_NAME=""
POSTGRES_HOST="localhost"
POSTGRES_DB="${PROJECT_SLUG}"
POSTGRES_USER="${PROJECT_SLUG}"
POSTGRES_PASSWORD=""
CREATED_APP_USER="false"
CREATED_APP_GROUP="false"
DOCKER_POSTGRES_VOLUME="${PROJECT_SLUG}-postgres-data"

log() {
  echo "[${PROJECT_SLUG}] $1"
}

fail() {
  echo "[${PROJECT_SLUG}] $1" >&2
  exit 1
}

require_root() {
  if [ "$(id -u)" -ne 0 ]; then
    fail "Run this installer as root or with sudo."
  fi
}

ensure_linux() {
  if [ "$(uname -s)" != "Linux" ]; then
    fail "This installer currently supports Linux hosts with systemd."
  fi

  if ! command -v systemctl >/dev/null 2>&1; then
    fail "systemd is required but systemctl is not available."
  fi
}

command_exists() {
  command -v "$1" >/dev/null 2>&1
}

prompt_with_default() {
  local prompt="$1"
  local default_value="$2"
  local response

  read -r -p "${prompt} [${default_value}]: " response
  if [ -z "$response" ]; then
    response="$default_value"
  fi

  printf '%s\n' "$response"
}

confirm_default_no() {
  local prompt="$1"
  local response

  read -r -p "${prompt} [y/N]: " response
  case "$response" in
    y|Y|yes|YES) return 0 ;;
    *) return 1 ;;
  esac
}

confirm_default_yes() {
  local prompt="$1"
  local response

  read -r -p "${prompt} [Y/n]: " response
  case "$response" in
    n|N|no|NO) return 1 ;;
    *) return 0 ;;
  esac
}

generate_random_string() {
  if command_exists openssl; then
    openssl rand -hex 24
    return
  fi

  tr -dc 'A-Za-z0-9' </dev/urandom | head -c 32
}

detect_package_manager() {
  if command_exists apt-get; then
    echo "apt"
  elif command_exists dnf; then
    echo "dnf"
  elif command_exists yum; then
    echo "yum"
  else
    fail "Unsupported package manager. Expected apt, dnf, or yum."
  fi
}

install_packages() {
  local package_manager="$1"
  shift

  case "$package_manager" in
    apt)
      export DEBIAN_FRONTEND=noninteractive
      apt-get update
      apt-get install -y "$@"
      ;;
    dnf)
      dnf install -y "$@"
      ;;
    yum)
      yum install -y "$@"
      ;;
    *)
      fail "Unsupported package manager: ${package_manager}"
      ;;
  esac
}

install_common_dependencies() {
  local package_manager
  package_manager="$(detect_package_manager)"

  case "$package_manager" in
    apt)
      install_packages "$package_manager" git curl
      ;;
    dnf)
      install_packages "$package_manager" git curl
      ;;
    yum)
      install_packages "$package_manager" git curl
      ;;
  esac
}

install_direct_dependencies() {
  local package_manager
  package_manager="$(detect_package_manager)"

  case "$package_manager" in
    apt)
      install_packages "$package_manager" git python3 python3-venv python3-pip curl
      ;;
    dnf)
      install_packages "$package_manager" git python3 python3-pip python3-virtualenv curl
      ;;
    yum)
      install_packages "$package_manager" git python3 python3-pip python3-virtualenv curl
      ;;
  esac
}

ensure_docker_installed() {
  if command_exists docker; then
    systemctl enable --now docker >/dev/null 2>&1 || true
    ensure_docker_compose_available
    return
  fi

  local package_manager
  package_manager="$(detect_package_manager)"

  log "Docker not found. Installing Docker because Docker mode was selected."
  case "$package_manager" in
    apt)
      install_packages "$package_manager" git docker.io
      ;;
    dnf)
      install_packages "$package_manager" git docker
      ;;
    yum)
      install_packages "$package_manager" git docker
      ;;
  esac

  systemctl enable --now docker
  ensure_docker_compose_available
}

ensure_docker_compose_available() {
  local package_manager

  if docker compose version >/dev/null 2>&1; then
    return
  fi

  if command_exists docker-compose; then
    return
  fi

  package_manager="$(detect_package_manager)"
  log "Docker Compose not found. Installing it because Docker mode was selected."

  case "$package_manager" in
    apt)
      if apt-cache show docker-compose-plugin >/dev/null 2>&1; then
        install_packages "$package_manager" docker-compose-plugin
      else
        install_packages "$package_manager" docker-compose
      fi
      ;;
    dnf)
      install_packages "$package_manager" docker-compose-plugin || install_packages "$package_manager" docker-compose
      ;;
    yum)
      install_packages "$package_manager" docker-compose-plugin || install_packages "$package_manager" docker-compose
      ;;
  esac

  if ! docker compose version >/dev/null 2>&1 && ! command_exists docker-compose; then
    fail "Docker Compose is required for Docker installs."
  fi
}

docker_compose_with_files() {
  local include_postgres="$1"
  shift
  local compose_args=(-p "${DOCKER_COMPOSE_PROJECT}" -f "${DOCKER_COMPOSE_APP_FILE}")

  if [ "${include_postgres}" = "true" ] && [ -f "${DOCKER_COMPOSE_POSTGRES_FILE}" ]; then
    compose_args+=(-f "${DOCKER_COMPOSE_POSTGRES_FILE}")
  fi

  if docker compose version >/dev/null 2>&1; then
    docker compose "${compose_args[@]}" "$@"
    return
  fi

  if command_exists docker-compose; then
    docker-compose "${compose_args[@]}" "$@"
    return
  fi

  fail "Docker Compose is not available."
}

docker_compose() {
  if [ "${POSTGRES_MODE}" = "compose" ]; then
    docker_compose_with_files "true" "$@"
    return
  fi

  docker_compose_with_files "false" "$@"
}

docker_compose_all() {
  docker_compose_with_files "true" "$@"
}

detect_nologin_shell() {
  if [ -x "/usr/sbin/nologin" ]; then
    echo "/usr/sbin/nologin"
  elif [ -x "/sbin/nologin" ]; then
    echo "/sbin/nologin"
  else
    echo "/bin/false"
  fi
}

ensure_app_user() {
  if getent group "${APP_GROUP}" >/dev/null 2>&1; then
    CREATED_APP_GROUP="false"
  else
    groupadd --system "${APP_GROUP}"
    CREATED_APP_GROUP="true"
  fi

  if id -u "${APP_USER}" >/dev/null 2>&1; then
    CREATED_APP_USER="false"
    return
  fi

  useradd \
    --system \
    --gid "${APP_GROUP}" \
    --home-dir "${INSTALL_DIR}" \
    --shell "$(detect_nologin_shell)" \
    "${APP_USER}"
  CREATED_APP_USER="true"
}

sync_repo() {
  mkdir -p "$(dirname "${INSTALL_DIR}")"

  if [ -d "${INSTALL_DIR}/.git" ]; then
    log "Updating repository in ${INSTALL_DIR}"
    git -C "${INSTALL_DIR}" pull --ff-only
    return
  fi

  if [ -d "${INSTALL_DIR}" ] && [ -n "$(ls -A "${INSTALL_DIR}" 2>/dev/null)" ]; then
    fail "${INSTALL_DIR} exists and is not an existing git checkout."
  fi

  rm -rf "${INSTALL_DIR}"
  log "Cloning ${GIT_REPO_URL} into ${INSTALL_DIR}"
  git clone "${GIT_REPO_URL}" "${INSTALL_DIR}"
}

ensure_env_file() {
  local env_path="${INSTALL_DIR}/${ENV_FILE_NAME}"

  if [ -f "${env_path}" ]; then
    return
  fi

  if [ -f "${INSTALL_DIR}/.env.example" ]; then
    cp "${INSTALL_DIR}/.env.example" "${env_path}"
    return
  fi

  touch "${env_path}"
}

set_env_value() {
  local file_path="$1"
  local key="$2"
  local value="$3"
  local temp_file

  touch "${file_path}"
  temp_file="$(mktemp)"

  awk -v key="${key}" -v value="${value}" '
    BEGIN { updated = 0 }
    index($0, key "=") == 1 {
      print key "=" value
      updated = 1
      next
    }
    { print }
    END {
      if (!updated) {
        print key "=" value
      }
    }
  ' "${file_path}" >"${temp_file}"

  mv "${temp_file}" "${file_path}"
}

get_env_value() {
  local file_path="$1"
  local key="$2"

  if [ ! -f "${file_path}" ]; then
    return
  fi

  awk -F= -v key="${key}" '$1 == key { sub(/^[^=]*=/, "", $0); print; exit }' "${file_path}"
}

validate_port() {
  local port="$1"

  if ! [[ "${port}" =~ ^[0-9]+$ ]]; then
    fail "Port must be a number."
  fi

  if [ "${port}" -lt 1 ] || [ "${port}" -gt 65535 ]; then
    fail "Port must be between 1 and 65535."
  fi
}

prompt_app_settings() {
  APP_PORT="$(prompt_with_default "Enter the host port for ${PROJECT_NAME}" "${DEFAULT_PORT}")"
  validate_port "${APP_PORT}"
}

load_existing_install_defaults() {
  local env_path="${INSTALL_DIR}/${ENV_FILE_NAME}"
  local current_postgres_host current_postgres_db current_postgres_user current_postgres_password current_secret_key
  local existing_database_url

  current_postgres_host="$(get_env_value "${env_path}" "POSTGRES_HOST")"
  current_postgres_db="$(get_env_value "${env_path}" "POSTGRES_DB")"
  current_postgres_user="$(get_env_value "${env_path}" "POSTGRES_USER")"
  current_postgres_password="$(get_env_value "${env_path}" "POSTGRES_PASSWORD")"
  current_secret_key="$(get_env_value "${env_path}" "SECRET_KEY")"
  existing_database_url="$(get_env_value "${env_path}" "OBJECT_DATABASE_URL")"
  if [ -z "${existing_database_url}" ]; then
    existing_database_url="$(get_env_value "${env_path}" "DATABASE_URL")"
  fi

  if [ -n "${current_postgres_host}" ]; then
    POSTGRES_HOST="${current_postgres_host}"
  fi
  if [ -n "${current_postgres_db}" ]; then
    POSTGRES_DB="${current_postgres_db}"
  fi
  if [ -n "${current_postgres_user}" ]; then
    POSTGRES_USER="${current_postgres_user}"
  fi
  if [ -n "${current_postgres_password}" ]; then
    POSTGRES_PASSWORD="${current_postgres_password}"
  fi
  if [ -n "${existing_database_url}" ]; then
    DATABASE_URL="${existing_database_url}"
  fi

  if [ -z "${current_secret_key}" ] || [ "${current_secret_key}" = "change-this-in-production" ] || [ "${current_secret_key}" = "dev-secret-key-change-me" ]; then
    current_secret_key="$(generate_random_string)"
  fi

  SECRET_KEY_VALUE="${current_secret_key}"
}

detect_postgresql_service() {
  local candidate

  for candidate in postgresql postgresql.service; do
    if systemctl list-unit-files "${candidate}" --no-legend 2>/dev/null | awk '{print $1}' | grep -qx "${candidate}"; then
      echo "${candidate}"
      return
    fi
  done

  candidate="$(systemctl list-unit-files --type=service --no-legend 'postgresql*.service' 2>/dev/null | awk 'NR==1 { print $1 }')"
  if [ -n "${candidate}" ]; then
    echo "${candidate}"
    return
  fi

  echo "postgresql"
}

install_postgresql_packages() {
  local package_manager
  package_manager="$(detect_package_manager)"

  case "${package_manager}" in
    apt)
      install_packages "${package_manager}" postgresql postgresql-contrib
      ;;
    dnf)
      install_packages "${package_manager}" postgresql-server postgresql
      ;;
    yum)
      install_packages "${package_manager}" postgresql-server postgresql
      ;;
  esac
}

ensure_postgresql_initialized() {
  if command_exists postgresql-setup; then
    postgresql-setup --initdb >/dev/null 2>&1 || true
  fi
}

ensure_postgresql_running() {
  POSTGRES_SERVICE_NAME="$(detect_postgresql_service)"
  systemctl enable --now "${POSTGRES_SERVICE_NAME}"
}

validate_pg_identifier() {
  local value="$1"
  if ! [[ "${value}" =~ ^[A-Za-z0-9_]+$ ]]; then
    fail "Only letters, numbers, and underscores are allowed for PostgreSQL database and username values."
  fi
}

extract_host_from_database_url() {
  local database_url="$1"
  local authority
  local host

  if [[ "${database_url}" != *"://"* ]]; then
    return
  fi

  authority="${database_url#*://}"
  authority="${authority%%/*}"
  authority="${authority##*@}"

  if [ -z "${authority}" ]; then
    return
  fi

  if [[ "${authority}" == \[* ]]; then
    host="${authority#\[}"
    host="${host%%]*}"
    if [ -n "${host}" ]; then
      printf '[%s]\n' "${host}"
    fi
    return
  fi

  host="${authority%%:*}"
  if [ -n "${host}" ]; then
    printf '%s\n' "${host}"
  fi
}

prepare_local_postgresql_defaults() {
  if [ -z "${POSTGRES_DB}" ]; then
    POSTGRES_DB="${PROJECT_SLUG}"
  fi

  if [ -z "${POSTGRES_USER}" ] || [ "${POSTGRES_USER}" = "postgres" ]; then
    POSTGRES_USER="${PROJECT_SLUG}"
  fi

  if [ -z "${POSTGRES_PASSWORD}" ] || [ "${POSTGRES_PASSWORD}" = "postgres" ]; then
    POSTGRES_PASSWORD="$(generate_random_string)"
  fi
}

configure_local_postgresql() {
  local install_mode="$1"
  local db_host sql_password

  prepare_local_postgresql_defaults
  install_postgresql_packages
  ensure_postgresql_initialized
  ensure_postgresql_running

  validate_pg_identifier "${POSTGRES_DB}"
  validate_pg_identifier "${POSTGRES_USER}"
  if ! [[ "${POSTGRES_PASSWORD}" =~ ^[A-Za-z0-9_]+$ ]]; then
    fail "Use letters, numbers, or underscores for the PostgreSQL password."
  fi

  sql_password="${POSTGRES_PASSWORD//\'/\'\'}"

  su - postgres -s /bin/bash -c "psql -tAc \"SELECT 1 FROM pg_roles WHERE rolname='${POSTGRES_USER}'\" | grep -q 1 || psql -c \"CREATE ROLE ${POSTGRES_USER} LOGIN PASSWORD '${sql_password}';\""
  su - postgres -s /bin/bash -c "psql -tAc \"SELECT 1 FROM pg_database WHERE datname='${POSTGRES_DB}'\" | grep -q 1 || psql -c \"CREATE DATABASE ${POSTGRES_DB} OWNER ${POSTGRES_USER};\""

  if [ "${install_mode}" = "docker" ]; then
    db_host="host.docker.internal"
  else
    db_host="localhost"
  fi

  POSTGRES_HOST="${db_host}"
  DATABASE_URL="postgresql+psycopg://${POSTGRES_USER}:${POSTGRES_PASSWORD}@${db_host}:5432/${POSTGRES_DB}"
  POSTGRES_MODE="host"
}

configure_compose_postgresql() {
  prepare_local_postgresql_defaults

  validate_pg_identifier "${POSTGRES_DB}"
  validate_pg_identifier "${POSTGRES_USER}"
  if ! [[ "${POSTGRES_PASSWORD}" =~ ^[A-Za-z0-9_]+$ ]]; then
    fail "Use letters, numbers, or underscores for the PostgreSQL password."
  fi

  POSTGRES_HOST="${DOCKER_POSTGRES_SERVICE}"
  DATABASE_URL="postgresql+psycopg://${POSTGRES_USER}:${POSTGRES_PASSWORD}@${DOCKER_POSTGRES_SERVICE}:5432/${POSTGRES_DB}"
  POSTGRES_SERVICE_NAME=""
  POSTGRES_MODE="compose"
}

normalize_docker_database_url() {
  if [[ "${DATABASE_URL}" == *"@localhost:"* ]]; then
    DATABASE_URL="${DATABASE_URL/@localhost:/@host.docker.internal:}"
    return
  fi

  if [[ "${DATABASE_URL}" == *"@127.0.0.1:"* ]]; then
    DATABASE_URL="${DATABASE_URL/@127.0.0.1:/@host.docker.internal:}"
  fi
}

configure_existing_postgresql() {
  local install_mode="$1"
  local existing_postgres_host

  POSTGRES_MODE="existing"
  POSTGRES_SERVICE_NAME=""

  if [ -z "${POSTGRES_DB}" ]; then
    POSTGRES_DB="${PROJECT_SLUG}"
  fi
  if [ -z "${POSTGRES_USER}" ]; then
    POSTGRES_USER="postgres"
  fi
  if [ -z "${POSTGRES_PASSWORD}" ]; then
    POSTGRES_PASSWORD="postgres"
  fi
  if [ -z "${POSTGRES_HOST}" ]; then
    POSTGRES_HOST="localhost"
  fi

  if [ -z "${DATABASE_URL}" ]; then
    if [ "${install_mode}" = "docker" ] && { [ "${POSTGRES_HOST}" = "localhost" ] || [ "${POSTGRES_HOST}" = "127.0.0.1" ]; }; then
      POSTGRES_HOST="host.docker.internal"
    fi
    DATABASE_URL="postgresql+psycopg://${POSTGRES_USER}:${POSTGRES_PASSWORD}@${POSTGRES_HOST}:5432/${POSTGRES_DB}"
  fi

  if [ "${install_mode}" = "docker" ]; then
    normalize_docker_database_url
  fi

  existing_postgres_host="$(extract_host_from_database_url "${DATABASE_URL}")"
  if [ -n "${existing_postgres_host}" ]; then
    POSTGRES_HOST="${existing_postgres_host}"
  fi
}

prompt_postgresql_mode() {
  local install_mode="$1"

  echo
  echo "PostgreSQL setup:"
  if [ "${install_mode}" = "docker" ]; then
    if confirm_default_no "Set up local PostgreSQL with Docker Compose? Choose No to skip PostgreSQL questions here and finish them on the setup screen."; then
      configure_compose_postgresql
    else
      configure_existing_postgresql "${install_mode}"
    fi
    return
  fi

  if confirm_default_no "Set up local PostgreSQL on this host? Choose No to skip PostgreSQL questions here and finish them on the setup screen."; then
    configure_local_postgresql "${install_mode}"
  else
    configure_existing_postgresql "${install_mode}"
  fi
}

postgres_setup_summary() {
  case "${POSTGRES_MODE}" in
    compose)
      echo "Local Docker Compose PostgreSQL"
      ;;
    host)
      echo "Local PostgreSQL on this host"
      ;;
    existing)
      echo "Configure from the setup screen"
      ;;
    *)
      echo "${POSTGRES_MODE}"
      ;;
  esac
}

print_install_summary() {
  local install_mode="$1"
  local setup_url="http://<server-host>:${APP_PORT}/panel/pages/setup.html"

  echo
  echo "========== Install Summary =========="
  echo "Mode: ${install_mode}"
  echo "Install directory: ${INSTALL_DIR}"
  echo "Port: ${APP_PORT}"
  echo "Setup page: ${setup_url}"
  echo "PostgreSQL: $(postgres_setup_summary)"

  if [ "${install_mode}" = "direct" ]; then
    echo "Service: ${SYSTEMD_SERVICE_NAME}"
  else
    echo "Docker Compose project: ${DOCKER_COMPOSE_PROJECT}"
  fi

  echo "Environment file: ${INSTALL_DIR}/${ENV_FILE_NAME}"
  echo "====================================="
}

write_env_file() {
  local install_mode="$1"
  local env_path="${INSTALL_DIR}/${ENV_FILE_NAME}"
  local runtime_port
  local app_database_url

  if [ "${install_mode}" = "docker" ]; then
    runtime_port="${DOCKER_INTERNAL_PORT}"
    app_database_url="sqlite:////app/data/app.db"
  else
    runtime_port="${APP_PORT}"
    app_database_url="sqlite:///${INSTALL_DIR}/data/app.db"
  fi

  ensure_env_file
  set_env_value "${env_path}" "APP_NAME" "${PROJECT_NAME}"
  set_env_value "${env_path}" "APP_HOST_PORT" "${APP_PORT}"
  set_env_value "${env_path}" "APP_DATABASE_URL" "${app_database_url}"
  set_env_value "${env_path}" "HOST" "0.0.0.0"
  set_env_value "${env_path}" "PORT" "${runtime_port}"
  set_env_value "${env_path}" "OBJECT_DATABASE_URL" "${DATABASE_URL}"
  set_env_value "${env_path}" "DATABASE_URL" "${DATABASE_URL}"
  set_env_value "${env_path}" "POSTGRES_HOST" "${POSTGRES_HOST}"
  set_env_value "${env_path}" "POSTGRES_DB" "${POSTGRES_DB}"
  set_env_value "${env_path}" "POSTGRES_USER" "${POSTGRES_USER}"
  set_env_value "${env_path}" "POSTGRES_PASSWORD" "${POSTGRES_PASSWORD}"
  set_env_value "${env_path}" "SECRET_KEY" "${SECRET_KEY_VALUE}"
}

setup_python_environment() {
  log "Installing Python dependencies"
  python3 -m venv "${VENV_DIR}"
  "${VENV_DIR}/bin/pip" install --upgrade pip
  "${VENV_DIR}/bin/pip" install -r "${INSTALL_DIR}/requirements.txt"
}

write_systemd_service() {
  cat >"${SYSTEMD_SERVICE_FILE}" <<EOF
[Unit]
Description=${PROJECT_NAME} service
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=${APP_USER}
Group=${APP_GROUP}
WorkingDirectory=${INSTALL_DIR}
EnvironmentFile=${INSTALL_DIR}/${ENV_FILE_NAME}
Environment=PYTHONUNBUFFERED=1
ExecStart=/bin/bash -lc 'cd ${INSTALL_DIR} && exec ${VENV_DIR}/bin/python -m uvicorn app.main:app --host \${HOST:-0.0.0.0} --port \${PORT:-${DEFAULT_PORT}}'
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

  systemctl daemon-reload
  systemctl enable --now "${SYSTEMD_SERVICE_NAME}"
}

write_state_file() {
  mkdir -p "${INSTALL_DIR}"
  {
    printf 'INSTALL_MODE=%q\n' "${INSTALL_MODE}"
    printf 'APP_PORT=%q\n' "${APP_PORT}"
    printf 'PUBLIC_BASE_URL=%q\n' "${PUBLIC_BASE_URL}"
    printf 'POSTGRES_MODE=%q\n' "${POSTGRES_MODE}"
    printf 'POSTGRES_SERVICE_NAME=%q\n' "${POSTGRES_SERVICE_NAME}"
    printf 'POSTGRES_DB=%q\n' "${POSTGRES_DB}"
    printf 'POSTGRES_USER=%q\n' "${POSTGRES_USER}"
    printf 'CREATED_APP_USER=%q\n' "${CREATED_APP_USER}"
    printf 'CREATED_APP_GROUP=%q\n' "${CREATED_APP_GROUP}"
  } >"${INSTALL_STATE_FILE}"
}

load_state() {
  if [ -f "${INSTALL_STATE_FILE}" ]; then
    # shellcheck disable=SC1090
    . "${INSTALL_STATE_FILE}"
    if [ -z "${APP_PORT}" ]; then
      APP_PORT="${DEFAULT_PORT}"
    fi
    if [ -z "${POSTGRES_MODE}" ]; then
      if [ "${MANAGED_POSTGRES}" = "true" ]; then
        POSTGRES_MODE="host"
      else
        POSTGRES_MODE="existing"
      fi
    fi
    return
  fi

  INSTALL_MODE=""
}

start_or_restart_systemd_service() {
  systemctl restart "${SYSTEMD_SERVICE_NAME}"
}

wait_for_compose_postgresql() {
  local container_id
  local health_status
  local attempt=0

  container_id="$(docker_compose ps -q "${DOCKER_POSTGRES_SERVICE}")"
  if [ -z "${container_id}" ]; then
    fail "Could not find the PostgreSQL container in the Docker Compose project."
  fi

  while [ "${attempt}" -lt 30 ]; do
    health_status="$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "${container_id}" 2>/dev/null || true)"
    if [ "${health_status}" = "healthy" ]; then
      return
    fi

    sleep 2
    attempt=$((attempt + 1))
  done

  fail "Docker Compose PostgreSQL did not become healthy in time."
}

run_docker_stack() {
  docker_compose_all down --remove-orphans >/dev/null 2>&1 || true
  docker rm -f "${DOCKER_LEGACY_CONTAINER}" >/dev/null 2>&1 || true
  docker image rm "${DOCKER_LEGACY_IMAGE}" >/dev/null 2>&1 || true

  if [ "${POSTGRES_MODE}" = "compose" ]; then
    docker_compose up -d "${DOCKER_POSTGRES_SERVICE}"
    wait_for_compose_postgresql
  fi

  docker_compose up -d --no-deps --build "${DOCKER_APP_SERVICE}"
}

stop_and_remove_docker() {
  if [ -f "${DOCKER_COMPOSE_APP_FILE}" ]; then
    docker_compose_all down --remove-orphans >/dev/null 2>&1 || true
  fi

  docker rm -f "${DOCKER_LEGACY_CONTAINER}" >/dev/null 2>&1 || true
  docker image rm "${DOCKER_LEGACY_IMAGE}" >/dev/null 2>&1 || true
  docker image rm "${DOCKER_APP_IMAGE}" >/dev/null 2>&1 || true
}

drop_managed_postgresql() {
  if [ "${POSTGRES_MODE}" = "compose" ]; then
    if ! confirm_default_yes "Remove the Docker Compose PostgreSQL volume for ${PROJECT_NAME}?"; then
      return
    fi

    if command_exists docker; then
      docker volume rm -f "${DOCKER_POSTGRES_VOLUME}" >/dev/null 2>&1 || true
    fi
    return
  fi

  if [ "${POSTGRES_MODE}" != "host" ]; then
    return
  fi

  if ! confirm_default_yes "Drop the managed PostgreSQL database and user for ${PROJECT_NAME}?"; then
    return
  fi

  if [ -n "${POSTGRES_SERVICE_NAME}" ]; then
    systemctl enable --now "${POSTGRES_SERVICE_NAME}" >/dev/null 2>&1 || true
  fi

  if [ -n "${POSTGRES_DB}" ]; then
    su - postgres -s /bin/bash -c "psql -d postgres -c \"SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = '${POSTGRES_DB}' AND pid <> pg_backend_pid();\"" >/dev/null 2>&1 || true
    su - postgres -s /bin/bash -c "psql -d postgres -c \"DROP DATABASE IF EXISTS ${POSTGRES_DB};\""
  fi

  if [ -n "${POSTGRES_USER}" ]; then
    su - postgres -s /bin/bash -c "psql -d postgres -c \"DROP ROLE IF EXISTS ${POSTGRES_USER};\""
  fi
}

remove_app_user_if_created() {
  if [ "${CREATED_APP_USER}" = "true" ] && id -u "${APP_USER}" >/dev/null 2>&1; then
    userdel "${APP_USER}" >/dev/null 2>&1 || true
  fi

  if [ "${CREATED_APP_GROUP}" = "true" ] && getent group "${APP_GROUP}" >/dev/null 2>&1; then
    groupdel "${APP_GROUP}" >/dev/null 2>&1 || true
  fi
}

install_direct_ftp2s3() {
  INSTALL_MODE="direct"
  install_direct_dependencies
  ensure_app_user
  sync_repo
  ensure_env_file
  load_existing_install_defaults
  prompt_app_settings
  prompt_postgresql_mode "direct"
  write_env_file "direct"
  setup_python_environment
  chown -R "${APP_USER}:${APP_GROUP}" "${INSTALL_DIR}"
  write_systemd_service
  write_state_file

  log "Direct install complete."
  print_install_summary "direct"
}

install_docker_ftp2s3() {
  INSTALL_MODE="docker"
  install_common_dependencies
  ensure_docker_installed
  sync_repo
  ensure_env_file
  load_existing_install_defaults
  prompt_app_settings
  prompt_postgresql_mode "docker"
  write_env_file "docker"
  run_docker_stack
  write_state_file

  log "Docker install complete."
  print_install_summary "docker"
}

install_ftp2s3() {
  local install_choice

  require_root
  ensure_linux

  echo
  echo "Install options:"
  echo "1) Direct install (Python + systemd)"
  echo "2) Docker install"
  read -r -p "Select an install option [1-2]: " install_choice

  case "${install_choice}" in
    1) install_direct_ftp2s3 ;;
    2) install_docker_ftp2s3 ;;
    *) fail "Invalid install option." ;;
  esac
}

update_direct_install() {
  install_direct_dependencies
  ensure_app_user
  sync_repo
  setup_python_environment
  chown -R "${APP_USER}:${APP_GROUP}" "${INSTALL_DIR}"
  if [ -f "${SYSTEMD_SERVICE_FILE}" ]; then
    systemctl daemon-reload
    start_or_restart_systemd_service
  else
    write_systemd_service
  fi
  log "Direct install updated."
}

update_docker_install() {
  install_common_dependencies
  ensure_docker_installed
  sync_repo
  run_docker_stack
  log "Docker install updated."
}

update_ftp2s3() {
  require_root
  ensure_linux
  load_state

  if [ ! -d "${INSTALL_DIR}" ]; then
    fail "${INSTALL_DIR} was not found. Install ${PROJECT_NAME} first."
  fi

  if [ ! -f "${INSTALL_DIR}/${ENV_FILE_NAME}" ]; then
    fail "Missing ${INSTALL_DIR}/${ENV_FILE_NAME}. Update aborted."
  fi

  case "${INSTALL_MODE}" in
    direct) update_direct_install ;;
    docker) update_docker_install ;;
    *)
      if [ -f "${SYSTEMD_SERVICE_FILE}" ]; then
        INSTALL_MODE="direct"
        update_direct_install
      else
        fail "Unable to determine install mode. Expected ${INSTALL_STATE_FILE}."
      fi
      ;;
  esac
}

uninstall_ftp2s3() {
  require_root
  ensure_linux
  load_state

  if [ -f "${SYSTEMD_SERVICE_FILE}" ]; then
    systemctl stop "${SYSTEMD_SERVICE_NAME}" >/dev/null 2>&1 || true
    systemctl disable "${SYSTEMD_SERVICE_NAME}" >/dev/null 2>&1 || true
    rm -f "${SYSTEMD_SERVICE_FILE}"
    systemctl daemon-reload
  fi

  if command_exists docker; then
    stop_and_remove_docker
  fi

  drop_managed_postgresql

  if [ -d "${INSTALL_DIR}" ]; then
    rm -rf "${INSTALL_DIR}"
  fi

  remove_app_user_if_created

  log "Uninstall complete. System packages were left in place."
}

show_menu() {
  echo "========== ftp2s3 Installer =========="
  echo "1) Install"
  echo "2) Update"
  echo "3) Uninstall"
  echo "======================================="
  read -p "Select an option [1-3]: " CHOICE
  case $CHOICE in
    1) install_ftp2s3 ;;
    2) update_ftp2s3 ;;
    3) uninstall_ftp2s3 ;;
    *) echo "Invalid choice. Exiting." ; exit 1 ;;
  esac
}

show_menu
