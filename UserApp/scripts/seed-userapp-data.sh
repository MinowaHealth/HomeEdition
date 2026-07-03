#!/bin/bash
# Seed script for UserApp healthv10 database.
# Creates one deterministic user (by tenant_id + email) and sample health data.
# Idempotent: rerunning updates/keeps the same seeded records.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
USERAPP_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

load_env_file() {
  local env_file="$1"
  if [[ ! -f "${env_file}" ]]; then
    return
  fi

  while IFS= read -r raw_line || [[ -n "${raw_line}" ]]; do
    local line="${raw_line%$'\r'}"
    [[ -z "${line}" ]] && continue
    [[ "${line}" =~ ^[[:space:]]*# ]] && continue
    if [[ "${line}" =~ ^[A-Za-z_][A-Za-z0-9_]*= ]]; then
      export "${line}"
    fi
  done < "${env_file}"
}

load_env_file "${USERAPP_DIR}/.env"

TENANT_ID="${DEFAULT_TENANT_ID:-1}"
SEED_EMAIL="seed.user@minowa.local"
SEED_NAME="Seed User"
SEED_HASH='$argon2id$v=19$m=65536,t=3,p=4$OU5iYWEtkC7kROYlkpsM5g$IiDiazkVxuHnhEKAFJBnBQlMujfrx2crgTVOTABLXM0'
DB_NAME="${DB_NAME:-healthv10}"
DB_HOST="${DB_HOST:-localhost}"
DB_PORT="${DB_PORT:-5432}"
DB_USER="${POSTGRES_USER:-postgres}"
DB_PASSWORD="${POSTGRES_PASSWORD:-Password2026}"
MODE="docker"
CONTAINER_NAME="pgvector"

show_help() {
  cat <<EOF
Usage:
  ./scripts/seed-userapp-data.sh [options]

Options:
  --tenant-id <id>        Tenant ID (default: ${TENANT_ID})
  --email <email>         Seed user email (default: ${SEED_EMAIL})
  --display-name <name>   Seed user display name (default: ${SEED_NAME})
  --docker                Use docker exec psql (default)
  --direct                Use direct psql connection
  --container <name>      Docker postgres container name (default: ${CONTAINER_NAME})
  --db-name <name>        Database name (default: ${DB_NAME})
  --db-host <host>        DB host for --direct (default: ${DB_HOST})
  --db-port <port>        DB port for --direct (default: ${DB_PORT})
  --db-user <user>        DB user (default: ${DB_USER})
  --db-password <pass>    DB password (default from .env)
  --help                  Show this help

Notes:
  - Seeded password for the user is: password
  - This script targets shared healthv10 with tenant scoping (RLS model).
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --tenant-id)
      TENANT_ID="$2"; shift 2 ;;
    --email)
      SEED_EMAIL="$2"; shift 2 ;;
    --display-name)
      SEED_NAME="$2"; shift 2 ;;
    --docker)
      MODE="docker"; shift ;;
    --direct)
      MODE="direct"; shift ;;
    --container)
      CONTAINER_NAME="$2"; shift 2 ;;
    --db-name)
      DB_NAME="$2"; shift 2 ;;
    --db-host)
      DB_HOST="$2"; shift 2 ;;
    --db-port)
      DB_PORT="$2"; shift 2 ;;
    --db-user)
      DB_USER="$2"; shift 2 ;;
    --db-password)
      DB_PASSWORD="$2"; shift 2 ;;
    --help|-h)
      show_help; exit 0 ;;
    *)
      echo "Unknown argument: $1"
      show_help
      exit 1 ;;
  esac
done

if ! [[ "${TENANT_ID}" =~ ^[0-9]+$ ]]; then
  echo "Error: --tenant-id must be a positive integer"
  exit 1
fi

if [[ "${MODE}" == "docker" ]]; then
  if ! command -v docker >/dev/null 2>&1; then
    echo "Error: docker is required for --docker mode"
    exit 1
  fi
  if ! docker ps --format '{{.Names}}' | grep -q "^${CONTAINER_NAME}\$"; then
    echo "Error: container '${CONTAINER_NAME}' is not running"
    exit 1
  fi
fi

echo "Seeding UserApp data"
echo "  tenant_id:    ${TENANT_ID}"
echo "  email:        ${SEED_EMAIL}"
echo "  display_name: ${SEED_NAME}"
echo "  mode:         ${MODE}"
echo ""

run_psql() {
  if [[ "${MODE}" == "docker" ]]; then
    docker exec -i "${CONTAINER_NAME}" psql \
      -v ON_ERROR_STOP=1 \
      -U "${DB_USER}" \
      -d "${DB_NAME}" \
      -v tenant_id="${TENANT_ID}" \
      -v seed_email="${SEED_EMAIL}" \
      -v seed_name="${SEED_NAME}" \
      -v seed_hash="${SEED_HASH}"
  else
    if ! command -v psql >/dev/null 2>&1; then
      echo "Error: psql is required for --direct mode"
      exit 1
    fi
    PGPASSWORD="${DB_PASSWORD}" psql \
      -v ON_ERROR_STOP=1 \
      -h "${DB_HOST}" \
      -p "${DB_PORT}" \
      -U "${DB_USER}" \
      -d "${DB_NAME}" \
      -v tenant_id="${TENANT_ID}" \
      -v seed_email="${SEED_EMAIL}" \
      -v seed_name="${SEED_NAME}" \
      -v seed_hash="${SEED_HASH}"
  fi
}

run_psql <<'SQL'
SELECT set_config('seed.tenant_id', :'tenant_id', false);
SELECT set_config('seed.seed_email', lower(:'seed_email'), false);
SELECT set_config('seed.seed_name', :'seed_name', false);
SELECT set_config('seed.seed_hash', :'seed_hash', false);

DO $$
DECLARE
    v_tenant_id SMALLINT := current_setting('seed.tenant_id')::SMALLINT;
    v_email TEXT := current_setting('seed.seed_email');
    v_display_name TEXT := current_setting('seed.seed_name');
    v_password_hash TEXT := current_setting('seed.seed_hash');
    v_ns UUID := 'f5b7965c-3a50-4e39-b812-aed2f6a4f5dd';
    v_user_id UUID;
    v_pref_id UUID;
    v_timeframe_morning UUID;
    v_timeframe_lunch UUID;
    v_timeframe_dinner UUID;
    v_input_med UUID;
    v_input_supp UUID;
    v_food_oatmeal UUID;
    v_food_salad UUID;
BEGIN
    IF to_regclass('public.users') IS NULL OR to_regclass('public.user_preferences') IS NULL THEN
        RAISE EXCEPTION 'Required tables missing. Run UserApp setup/migrations before seeding.';
    END IF;

    IF to_regprocedure('uuid_generate_v5(uuid,text)') IS NULL THEN
        RAISE EXCEPTION 'uuid_generate_v5(uuid,text) is unavailable. Ensure extension "uuid-ossp" is installed.';
    END IF;

    v_user_id := uuid_generate_v5(v_ns, format('user:%s:%s', v_tenant_id, v_email));
    v_pref_id := uuid_generate_v5(v_ns, format('user-preferences:%s:%s', v_tenant_id, v_email));
    v_timeframe_morning := uuid_generate_v5(v_ns, format('timeframe:morning:%s:%s', v_tenant_id, v_email));
    v_timeframe_lunch := uuid_generate_v5(v_ns, format('timeframe:lunch:%s:%s', v_tenant_id, v_email));
    v_timeframe_dinner := uuid_generate_v5(v_ns, format('timeframe:dinner:%s:%s', v_tenant_id, v_email));
    v_input_med := uuid_generate_v5(v_ns, format('input:medication:%s:%s', v_tenant_id, v_email));
    v_input_supp := uuid_generate_v5(v_ns, format('input:supplement:%s:%s', v_tenant_id, v_email));
    v_food_oatmeal := uuid_generate_v5(v_ns, format('food:oatmeal:%s:%s', v_tenant_id, v_email));
    v_food_salad := uuid_generate_v5(v_ns, format('food:salad:%s:%s', v_tenant_id, v_email));

    INSERT INTO public.users (
        tenant_id, id, email, display_name, password_hash,
        biological_sex, birth_year, home_timezone, is_active, created_at, updated_at
    )
    VALUES (
        v_tenant_id, v_user_id, v_email, v_display_name, v_password_hash,
        'not_specified', 1990, 'America/Los_Angeles', true, NOW(), NOW()
    )
    ON CONFLICT (tenant_id, email) DO UPDATE
    SET display_name = EXCLUDED.display_name,
        updated_at = NOW();

    SELECT id
      INTO v_user_id
      FROM public.users
     WHERE tenant_id = v_tenant_id
       AND email = v_email;

    INSERT INTO public.user_preferences (
        tenant_id, id, user_id, color_scheme, units_weight, units_height,
        units_temperature, created_at, updated_at
    )
    VALUES (
        v_tenant_id, v_pref_id, v_user_id, 'system', 'lbs', 'ft_in',
        'fahrenheit', NOW(), NOW()
    )
    ON CONFLICT (tenant_id, user_id) DO UPDATE
    SET updated_at = NOW();

    IF to_regclass('public.timeframes') IS NOT NULL THEN
        INSERT INTO public.timeframes (tenant_id, id, user_id, name, time_of_day, sort_order, created_at, updated_at)
        VALUES
            (v_tenant_id, v_timeframe_morning, v_user_id, 'Morning', '08:00', 1, NOW(), NOW()),
            (v_tenant_id, v_timeframe_lunch, v_user_id, 'Lunch', '12:30', 2, NOW(), NOW()),
            (v_tenant_id, v_timeframe_dinner, v_user_id, 'Dinner', '18:30', 3, NOW(), NOW())
        ON CONFLICT (tenant_id, id) DO UPDATE
        SET name = EXCLUDED.name,
            time_of_day = EXCLUDED.time_of_day,
            sort_order = EXCLUDED.sort_order,
            updated_at = NOW();
    END IF;

    IF to_regclass('public.health_inputs') IS NOT NULL THEN
        INSERT INTO public.health_inputs (
            tenant_id, id, user_id, name, input_type, dosage, frequency, notes, created_at, updated_at
        )
        VALUES
            (v_tenant_id, v_input_med, v_user_id, 'Lisinopril', 'medication', '10 mg', 'daily', 'Seed script medication', NOW(), NOW()),
            (v_tenant_id, v_input_supp, v_user_id, 'Vitamin D3', 'supplement', '2000 IU', 'daily', 'Seed script supplement', NOW(), NOW())
        ON CONFLICT (tenant_id, id) DO UPDATE
        SET name = EXCLUDED.name,
            dosage = EXCLUDED.dosage,
            frequency = EXCLUDED.frequency,
            updated_at = NOW();
    END IF;

    IF to_regclass('public.health_input_log') IS NOT NULL THEN
        INSERT INTO public.health_input_log (
            tenant_id, id, user_id, logged_at, input_id, dosage_taken, notes, created_at
        )
        VALUES
            (v_tenant_id, uuid_generate_v5(v_ns, format('input-log:med:%s:%s:1', v_tenant_id, v_email)), v_user_id, NOW() - INTERVAL '6 hours', v_input_med, '10 mg', 'Taken with breakfast', NOW() - INTERVAL '6 hours'),
            (v_tenant_id, uuid_generate_v5(v_ns, format('input-log:supp:%s:%s:1', v_tenant_id, v_email)), v_user_id, NOW() - INTERVAL '5 hours', v_input_supp, '2000 IU', 'Morning supplement', NOW() - INTERVAL '5 hours')
        ON CONFLICT (tenant_id, id) DO UPDATE
        SET dosage_taken = EXCLUDED.dosage_taken,
            notes = EXCLUDED.notes;
    END IF;

    IF to_regclass('public.health_food_itemsv2') IS NOT NULL THEN
        INSERT INTO public.health_food_itemsv2 (
            tenant_id, id, user_id, name, brand, serving_size, serving_unit,
            calories, protein_g, carbs_g, fat_g, source, created_at, updated_at
        )
        VALUES
            (v_tenant_id, v_food_oatmeal, v_user_id, 'Rolled Oats Bowl', 'Seed Kitchen', '1 bowl', 'serving', 320, 12, 54, 8, 'seed-script', NOW(), NOW()),
            (v_tenant_id, v_food_salad, v_user_id, 'Chicken Salad', 'Seed Kitchen', '1 plate', 'serving', 410, 35, 18, 19, 'seed-script', NOW(), NOW())
        ON CONFLICT (tenant_id, id) DO UPDATE
        SET name = EXCLUDED.name,
            calories = EXCLUDED.calories,
            protein_g = EXCLUDED.protein_g,
            carbs_g = EXCLUDED.carbs_g,
            fat_g = EXCLUDED.fat_g,
            updated_at = NOW();
    END IF;

    IF to_regclass('public.health_food_logv2') IS NOT NULL THEN
        INSERT INTO public.health_food_logv2 (
            tenant_id, id, user_id, logged_at, food_item_id, servings, meal_type, timeframe_id, notes, created_at
        )
        VALUES
            (v_tenant_id, uuid_generate_v5(v_ns, format('food-log:oatmeal:%s:%s:1', v_tenant_id, v_email)), v_user_id, NOW() - INTERVAL '6 hours', v_food_oatmeal, 1, 'breakfast', v_timeframe_morning, 'Pre-work meal', NOW() - INTERVAL '6 hours'),
            (v_tenant_id, uuid_generate_v5(v_ns, format('food-log:salad:%s:%s:1', v_tenant_id, v_email)), v_user_id, NOW() - INTERVAL '2 hours', v_food_salad, 1, 'lunch', v_timeframe_lunch, 'High protein lunch', NOW() - INTERVAL '2 hours')
        ON CONFLICT (tenant_id, id) DO UPDATE
        SET servings = EXCLUDED.servings,
            notes = EXCLUDED.notes;
    END IF;

    IF to_regclass('public.health_metrics') IS NOT NULL THEN
        INSERT INTO public.health_metrics (
            tenant_id, id, user_id, metric_type, recorded_at, value, unit, source, notes, created_at, updated_at
        )
        VALUES
            (v_tenant_id, uuid_generate_v5(v_ns, format('metric:steps:%s:%s:1', v_tenant_id, v_email)), v_user_id, 'steps', NOW() - INTERVAL '1 hour', 8421, 'count', 'seed_script', 'Today''s step count', NOW() - INTERVAL '1 hour', NOW() - INTERVAL '1 hour'),
            (v_tenant_id, uuid_generate_v5(v_ns, format('metric:heart_rate:%s:%s:1', v_tenant_id, v_email)), v_user_id, 'heart_rate', NOW() - INTERVAL '55 minutes', 72, 'bpm', 'seed_script', 'Resting reading', NOW() - INTERVAL '55 minutes', NOW() - INTERVAL '55 minutes'),
            (v_tenant_id, uuid_generate_v5(v_ns, format('metric:resting_hr:%s:%s:1', v_tenant_id, v_email)), v_user_id, 'resting_heart_rate', NOW() - INTERVAL '54 minutes', 61, 'bpm', 'seed_script', 'Daily baseline', NOW() - INTERVAL '54 minutes', NOW() - INTERVAL '54 minutes'),
            (v_tenant_id, uuid_generate_v5(v_ns, format('metric:weight:%s:%s:1', v_tenant_id, v_email)), v_user_id, 'weight', NOW() - INTERVAL '8 hours', 176.4, 'lb', 'seed_script', 'Morning weigh-in', NOW() - INTERVAL '8 hours', NOW() - INTERVAL '8 hours'),
            (v_tenant_id, uuid_generate_v5(v_ns, format('metric:sleep:%s:%s:1', v_tenant_id, v_email)), v_user_id, 'sleep', NOW() - INTERVAL '10 hours', 7.6, 'hours', 'seed_script', 'Last night sleep duration', NOW() - INTERVAL '10 hours', NOW() - INTERVAL '10 hours'),
            (v_tenant_id, uuid_generate_v5(v_ns, format('metric:hydration:%s:%s:1', v_tenant_id, v_email)), v_user_id, 'hydration', NOW() - INTERVAL '3 hours', 24, 'oz', 'seed_script', 'Water intake sample', NOW() - INTERVAL '3 hours', NOW() - INTERVAL '3 hours')
        ON CONFLICT (tenant_id, id) DO UPDATE
        SET value = EXCLUDED.value,
            notes = EXCLUDED.notes,
            updated_at = NOW();
    END IF;

    IF to_regclass('public.health_blood_pressure_readings') IS NOT NULL THEN
        INSERT INTO public.health_blood_pressure_readings (
            tenant_id, id, user_id, measured_at, systolic, diastolic, pulse, position, arm, notes, created_at
        )
        VALUES
            (v_tenant_id, uuid_generate_v5(v_ns, format('bp:%s:%s:1', v_tenant_id, v_email)), v_user_id, NOW() - INTERVAL '4 hours', 118, 76, 68, 'sitting', 'left', 'Morning check', NOW() - INTERVAL '4 hours'),
            (v_tenant_id, uuid_generate_v5(v_ns, format('bp:%s:%s:2', v_tenant_id, v_email)), v_user_id, NOW() - INTERVAL '1 day', 121, 79, 70, 'sitting', 'left', 'Yesterday check', NOW() - INTERVAL '1 day')
        ON CONFLICT (tenant_id, id) DO UPDATE
        SET systolic = EXCLUDED.systolic,
            diastolic = EXCLUDED.diastolic,
            pulse = EXCLUDED.pulse,
            notes = EXCLUDED.notes;
    END IF;

    IF to_regclass('public.health_observations') IS NOT NULL THEN
        INSERT INTO public.health_observations (
            tenant_id, id, user_id, observed_at, category, content, severity, mental_health_flag, created_at, updated_at
        )
        VALUES
            (v_tenant_id, uuid_generate_v5(v_ns, format('observation:%s:%s:energy', v_tenant_id, v_email)), v_user_id, NOW() - INTERVAL '2 hours', 'energy', 'Felt focused and energetic after lunch walk.', 2, false, NOW() - INTERVAL '2 hours', NOW() - INTERVAL '2 hours'),
            (v_tenant_id, uuid_generate_v5(v_ns, format('observation:%s:%s:sleep', v_tenant_id, v_email)), v_user_id, NOW() - INTERVAL '9 hours', 'sleep', 'Woke once overnight but returned to sleep quickly.', 1, false, NOW() - INTERVAL '9 hours', NOW() - INTERVAL '9 hours')
        ON CONFLICT (tenant_id, id) DO UPDATE
        SET content = EXCLUDED.content,
            severity = EXCLUDED.severity,
            updated_at = NOW();
    END IF;

    IF to_regclass('public.audit_log') IS NOT NULL THEN
        IF NOT EXISTS (
            SELECT 1
              FROM public.audit_log
             WHERE tenant_id = v_tenant_id
               AND user_id = v_user_id
               AND action = 'seed_data_loaded'
        ) THEN
            INSERT INTO public.audit_log (
                tenant_id, user_id, action, target_type, target_id, details, created_at
            )
            VALUES (
                v_tenant_id,
                v_user_id,
                'seed_data_loaded',
                'user',
                v_user_id::TEXT,
                jsonb_build_object(
                    'source', 'seed-userapp-data.sh',
                    'email', v_email
                ),
                NOW()
            );
        END IF;
    END IF;

    RAISE NOTICE 'Seed complete for tenant_id=%, email=%, user_id=%', v_tenant_id, v_email, v_user_id;
END $$;

WITH seed_user AS (
    SELECT tenant_id, id, email, display_name
    FROM public.users
    WHERE tenant_id = CAST(:'tenant_id' AS SMALLINT)
      AND email = lower(:'seed_email')
)
SELECT
    su.tenant_id,
    su.id AS user_id,
    su.email,
    su.display_name,
    (SELECT COUNT(*) FROM public.health_metrics m WHERE m.tenant_id = su.tenant_id AND m.user_id = su.id) AS metric_rows,
    (SELECT COUNT(*) FROM public.health_food_logv2 f WHERE f.tenant_id = su.tenant_id AND f.user_id = su.id) AS food_log_rows,
    (SELECT COUNT(*) FROM public.health_input_log i WHERE i.tenant_id = su.tenant_id AND i.user_id = su.id) AS input_log_rows,
    (SELECT COUNT(*) FROM public.health_observations o WHERE o.tenant_id = su.tenant_id AND o.user_id = su.id) AS observation_rows
FROM seed_user su;
SQL

echo ""
echo "Seed script completed."
echo "Login:"
echo "  Email:    ${SEED_EMAIL}"
echo "  Password: Password2026"
